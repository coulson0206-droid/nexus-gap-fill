# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""External, single-session Nexus queue. Do not load this file in Nexus Python.

Input: saved labelled C3D trial copies with a shared, already verified calibrated
VSK. The user confirms saved edits and loaded calibration ONCE before starting.
One long-lived Python process calls native-only pipelines, then invokes the same
audited gap-fill checkpoints directly. No SDK nesting, auto-save or auto-retry.
"""
import argparse
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import xml.etree.ElementTree as ET

import NexusGapFill_Single as gap
from stage_session import sha256

VERSION = "0.4.0"
NATIVE_OPERATIONS = {"FillGapsWoltring", "FillGapsRigidBody", "FillGapsPattern",
                     "ComputeSubjectMotion", "FillGapsKinematic", "Exportc3d"}


def _name(name):
    if (not gap._ascii_text(name) or Path(name).name != name or name.endswith((".", " "))
            or any(c in name for c in '/\\:')):
        raise ValueError("Use an exact ASCII trial/subject basename")
    return name


def write_json(path, data):
    gap.write_json(Path(path), data)


def plan(session, subject, jobs, allow_low=False):
    session = Path(session).resolve()
    gap.ascii_path_guard({"working session": session, "batch script": __file__, "Python": sys.executable})
    certificate = gap.read_json(session / "ngf-copy-manifest.json")
    source = Path(certificate["source"]).resolve()
    if (certificate["status"] != "verified_copy" or Path(certificate["destination"]).resolve() != session
            or source == session or source in session.parents or session in source.parents):
        raise ValueError("A verified independent session copy is required")
    subject = _name(subject)
    vsk = session / (subject + ".vsk")
    if not vsk.is_file():
        raise ValueError("Saved calibrated VSK missing")
    entries, seen = [], set()
    for item in jobs:
        trial = _name(item["trial"])
        if trial.lower() in seen:
            raise ValueError("Duplicate trial")
        seen.add(trial.lower())
        roi = item["roi"]
        if (not isinstance(roi, (list, tuple)) or len(roi) != 2 or
                any(type(i) is not int for i in roi) or not 1 <= roi[0] <= roi[1]):
            raise ValueError("Explicit inclusive integer ROI required")
        c3d, enf = session / (trial + ".c3d"), session / (trial + ".Trial.enf")
        if not c3d.is_file() or not enf.is_file():
            raise ValueError("READY_C3D_REQUIRED: reconstruct/label and save the trial copy before queuing: " + trial)
        gap.ascii_path_guard({"C3D": c3d, "ENF": enf, "VSK": vsk})
        entries.append(dict(trial=trial, roi=list(roi), input_c3d_sha256=sha256(c3d)))
    if not entries:
        raise ValueError("Select at least one trial")
    return dict(schema_version=1, version=VERSION, session=str(session), source_session=str(source),
        subject=subject, vsk_sha256=sha256(vsk), jobs=entries,
        allow_low_reliability_completion=bool(allow_low),
        setup_required="Save user edits, load and verify this calibrated VSK in this working session; open the first queued trial. Keep editing paused during the queue.")


def validate(manifest):
    if manifest.get("schema_version") != 1 or type(manifest.get("allow_low_reliability_completion")) is not bool:
        raise ValueError("Unsupported manifest or missing LOW policy")
    # Recreate the declared input identity once per queue, not per phase/trial.
    current = plan(manifest["session"], manifest["subject"], manifest["jobs"],
                   manifest["allow_low_reliability_completion"])
    for key in ("session", "source_session", "subject", "vsk_sha256", "jobs"):
        if current[key] != manifest[key]:
            raise ValueError("Inputs changed since planning: " + key)
    return current


class CountedSDK:
    def __init__(self, nexus):
        self.nexus, self.counts = nexus, {}

    def __getattr__(self, name):
        method = getattr(self.nexus, name)
        if not callable(method):
            return method
        def call(*args, **kwargs):
            self.counts[name] = self.counts.get(name, 0) + 1
            return method(*args, **kwargs)
        return call


class BatchRunner:
    def __init__(self, manifest_path, nexus=None, timeout=60):
        self.path = Path(manifest_path).resolve()
        gap.ascii_path_guard({"batch plan": self.path})
        self.manifest = gap.read_json(self.path)
        self.digest = sha256(self.path)
        self.journal_path = self.path.with_name(self.path.stem + ".state.json")
        self.private = Path(os.environ.get("APPDATA", "")) / "Vicon/Nexus2.x/Configurations/Pipelines"
        self.nexus = CountedSDK(nexus) if nexus is not None else None
        self.template_identity = None
        self.timeout = int(timeout)
        if not 1 <= self.timeout <= 600:
            raise ValueError("Timeout must be between 1 and 600 seconds")

    def persist(self):
        self.state["sdk_calls"] = dict(self.nexus.counts) if self.nexus else self.state.get("sdk_calls", {})
        write_json(self.journal_path, self.state)

    def current(self, job, check_roi=False):
        folder, trial = self.nexus.GetTrialName()
        if Path(folder).resolve() != Path(self.manifest["session"]) or trial != job["trial"]:
            raise RuntimeError("Active trial differs from the approved working-copy job")
        if check_roi and list(self.nexus.GetTrialRegionOfInterest()) != job["roi"]:
            raise RuntimeError("ROI changed during queue")
        names, templates, active = self.nexus.GetSubjectInfo()
        if [name for name, enabled in zip(names, active) if enabled] != [self.manifest["subject"]]:
            raise RuntimeError("Active subject differs; do not change calibration during this queue")
        template = templates[names.index(self.manifest["subject"])]
        if self.template_identity is None:
            self.template_identity = template
        elif self.template_identity != template:
            raise RuntimeError("Loaded template identity changed during queue")
        vsk = Path(self.manifest["session"]) / (self.manifest["subject"] + ".vsk")
        stat = vsk.stat()
        if (stat.st_size, stat.st_mtime_ns) != self.vsk_stat:
            # Cheap metadata check each trial; hash only if it changed.
            if sha256(vsk) != self.manifest["vsk_sha256"]:
                raise RuntimeError("Calibrated VSK changed during queue")
            self.vsk_stat = stat.st_size, stat.st_mtime_ns
        c3d = Path(self.manifest["session"]) / (job["trial"] + ".c3d")
        stat = c3d.stat()
        if (stat.st_size, stat.st_mtime_ns) != self.input_stats[job["trial"]]:
            if sha256(c3d) != job["input_c3d_sha256"]:
                raise RuntimeError("Input C3D changed during queue")
            self.input_stats[job["trial"]] = stat.st_size, stat.st_mtime_ns

    def step(self, row, label, function, *args):
        row.update(phase=label, outcome="running")
        self.persist()  # record intent before a call with possible side effects
        start, errors = time.perf_counter(), io.StringIO()
        try:
            with contextlib.redirect_stderr(errors):
                result = function(*args)
            if errors.getvalue().strip():
                raise RuntimeError(errors.getvalue().strip())
            row["timings_seconds"][label] = time.perf_counter()-start
            self.persist()
            return result
        except Exception:
            row["timings_seconds"][label] = time.perf_counter()-start
            self.persist()
            raise

    def native(self, entry):
        if entry.get("OperationName") not in NATIVE_OPERATIONS:
            raise ValueError("Unapproved native operation")
        root = ET.Element("Pipeline")
        root.append(copy.deepcopy(entry))
        raw = b'<?xml version="1.1" encoding="UTF-8" standalone="no" ?>\n' + ET.tostring(root, encoding="utf-8")
        name = "NGF_NATIVE_" + hashlib.sha256(raw).hexdigest()[:16]
        self.private.mkdir(parents=True, exist_ok=True)
        path = self.private / (name + ".Pipeline")
        if not path.exists():
            with path.open("xb") as handle:
                handle.write(raw)
        elif path.read_bytes() != raw:
            raise RuntimeError("Native pipeline name/content collision")
        self.nexus.RunPipeline(name, "Private", self.timeout)

    def execute(self, job, row):
        self.current(job)
        first, last = self.nexus.GetTrialRange()
        if not first <= job["roi"][0] <= job["roi"][1] <= last:
            raise ValueError("ROI outside loaded trial")
        self.step(row, "set_roi", self.nexus.SetTrialRegionOfInterest, *job["roi"])
        if list(self.nexus.GetTrialRegionOfInterest()) != job["roi"]:
            raise RuntimeError("ROI did not update")
        ready = self.step(row, "prepare", gap.prepare, self.nexus, False)
        profile = ready["profile"]
        row["profile"] = profile
        root = ET.fromstring(Path(ready["generated_pipeline"]).read_bytes())
        for index, entry in enumerate(root):
            self.current(job, check_roi=True)
            if entry.get("OperationName") != "Python":
                self.step(row, "%02d_%s" % (index, entry.get("OperationName")), self.native, entry)
                continue
            params = {p.get("name"):p.get("value") for p in entry.findall("./ParamList/Param")}
            words = shlex.split(params["ScriptArgs"])
            if Path(params["Script"]).resolve() != gap.SCRIPT_FILE or words[words.index("--profile")+1] != profile:
                raise RuntimeError("Unexpected generated Python step")
            command = words[0]
            if command == "checkpoint":
                stage = words[words.index("--stage")+1]
                self.step(row, "%02d_%s" % (index, stage), gap.checkpoint, self.nexus, profile, stage)
            elif command in ("begin", "finish", "verify-export"):
                function = {"begin":gap.begin, "finish":gap.finish, "verify-export":gap.verify_export}[command]
                self.step(row, "%02d_%s" % (index, command), function, self.nexus, profile)
                if command == "begin":
                    meta = dict(subject=self.manifest["subject"], trial_folder=self.manifest["session"], trial=job["trial"])
                    row["run_path"] = gap.read_json(gap._state_pointer(meta))["run_path"]
                    self.persist()
            else:
                raise RuntimeError("Unknown generated NGF command")
        run = gap.read_json(Path(row["run_path"]) / "run.json")
        if run["status"] != "export_verified" or sha256(run["export"]["file"]) != run["export"]["sha256"]:
            raise RuntimeError("New export not verified")
        row.update(outcome="verified", phase="complete", export=run["export"], summary=run["summary"])
        self.persist()

    def run(self, setup_confirmed=False, resume=False):
        if not setup_confirmed:
            raise ValueError("SETUP_REQUIRED: confirm saved edits and correct loaded calibrated VSK once before running")
        gap.ascii_path_guard({"private pipelines": self.private, "journal": self.journal_path,
                              "gap script": gap.SCRIPT_FILE, "cache": gap.ROOT})
        if not os.environ.get("APPDATA"):
            raise ValueError("APPDATA missing")
        start = time.perf_counter()
        validate(self.manifest)
        if self.journal_path.exists():
            if not resume:
                raise ValueError("Journal exists; inspect it and use --resume only for untouched pending jobs")
            self.state = gap.read_json(self.journal_path)
            if self.state["manifest_sha256"] != self.digest:
                raise ValueError("Manifest changed since queue creation")
            for row in self.state["jobs"]:
                if row["outcome"] == "verified":
                    if sha256(row["export"]["file"]) != row["export"]["sha256"]:
                        raise RuntimeError("Previously verified export changed; do not rerun or overwrite")
                elif row["outcome"] != "pending":
                    raise RuntimeError("INTERRUPTED_JOB_REQUIRES_REVIEW: no automatic retry of an uncertain host operation")
        else:
            self.state = dict(version=VERSION, manifest_sha256=self.digest, status="running",
                calibration_verification="user_confirmed_loaded_VSK_not_provable_from_marker_names",
                jobs=[dict(trial=j["trial"], outcome="pending", phase="pending", timings_seconds={})
                      for j in self.manifest["jobs"]])
        self.state["input_validation_seconds"] = time.perf_counter()-start
        pending = [i for i,row in enumerate(self.state["jobs"]) if row["outcome"] == "pending"]
        if not pending:
            self.state["status"] = "complete"
            self.persist()
            return self.state
        from pyCGM2 import btk  # dependency failure must precede any native mutation
        if self.nexus is None:
            self.nexus = CountedSDK(gap.connect())
        if self.nexus.GetServerInfo()[1:3] != (2,16):
            raise RuntimeError("Only Nexus 2.16 is the declared target of this external runner")
        stat = (Path(self.manifest["session"]) / (self.manifest["subject"]+".vsk")).stat()
        self.vsk_stat = stat.st_size, stat.st_mtime_ns
        self.input_stats = {}
        for job in self.manifest["jobs"]:
            stat = (Path(self.manifest["session"]) / (job["trial"] + ".c3d")).stat()
            self.input_stats[job["trial"]] = stat.st_size, stat.st_mtime_ns
        try:
            self.current(self.manifest["jobs"][pending[0]])
            first, last = self.nexus.GetTrialRange()
            roi = self.manifest["jobs"][pending[0]]["roi"]
            if not first <= roi[0] <= roi[1] <= last:
                raise ValueError("ROI outside loaded trial")
        except Exception as error:
            raise RuntimeError("SETUP_REQUIRED: " + str(error))
        lock_path = self.private.parent / "NGF_BATCH.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                json.dump(dict(manifest=str(self.path), manifest_sha256=self.digest,
                               pid=os.getpid()), handle)
        except FileExistsError:
            raise RuntimeError("QUEUE_LOCKED: inspect the prior queue and confirm Nexus is idle before removing " + str(lock_path))
        previous_config = dict(gap.CONFIG)
        try:
            gap.CONFIG.update(allow_low_reliability_completion=self.manifest["allow_low_reliability_completion"],
                              protected_roots=[self.manifest["source_session"]])
            self.state["status"] = "running"
            self.persist()
            # execute() checks that the first job is already open before changing ROI.
            for position,index in enumerate(pending):
                job, row = self.manifest["jobs"][index], self.state["jobs"][index]
                try:
                    if position:
                        # Only close OUR prior job after its new export is verified.
                        # Never auto-save into the baseline C3D/VSK or discard user edits.
                        self.current(self.manifest["jobs"][pending[position-1]], check_roi=True)
                        self.step(row,"close_verified_previous",self.nexus.CloseTrial,self.timeout)
                        self.step(row,"open_trial",self.nexus.OpenTrial,
                                  str(Path(self.manifest["session"])/job["trial"]),self.timeout)
                    self.execute(job,row)
                except Exception as error:
                    row.update(outcome="needs_attention", error=str(error), host_outcome="unknown_do_not_retry")
                    self.state["status"] = "needs_attention"
                    self.persist()
                    return self.state
            self.state["status"] = "complete"
            self.persist()
            return self.state
        finally:
            gap.CONFIG.clear()
            gap.CONFIG.update(previous_config)
            if self.state["status"] == "complete":
                lock_path.unlink()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan","run","status"])
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--session")
    parser.add_argument("--subject")
    parser.add_argument("--trial", action="append", help="Exact basename:first:last; repeat per trial")
    parser.add_argument("--allow-low", action="store_true")
    parser.add_argument("--setup-confirmed", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    args=parser.parse_args(argv)
    path=Path(args.manifest).resolve()
    if args.command=="plan":
        if not args.session or not args.subject or not args.trial:
            parser.error("plan needs --session, --subject and --trial")
        if path.exists():
            raise ValueError("Plan already exists; do not overwrite")
        jobs=[]
        for text in args.trial:
            name,first,last=text.rsplit(":",2)
            jobs.append(dict(trial=name,roi=[int(first),int(last)]))
        result=plan(args.session,args.subject,jobs,args.allow_low)
        write_json(path,result)
    elif args.command=="status":
        result=gap.read_json(path.with_name(path.stem+".state.json"))
    else:
        result=BatchRunner(path,timeout=args.timeout).run(args.setup_confirmed,args.resume)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 2 if result.get("status")=="needs_attention" else 0


if __name__=="__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("NEXUS_BATCH_STOPPED: "+str(error),file=sys.stderr)
        sys.exit(1)
