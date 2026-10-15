# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""Nexus display-link preparation, Python 3.7+, standard library only.

No arguments in Nexus: read the saved active-subject VSK, discover local VSTs,
and prepare a separate same-name VSK for File Import > Import VSK.
Offline: --vsk C:\\ViconWork\\Session\\Subject.vsk [--write --output DIR]
Keep models/registry.json alongside this package. No motion data are uploaded.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

VERSION = "0.3.0"
SCRIPT = Path(__file__).resolve()
REGISTRY = SCRIPT.parent.parent / "models" / "registry.json"


def ascii_paths(paths):
    for label, value in paths.items():
        for form in (str(value), os.path.abspath(str(value)), str(Path(value).resolve())):
            if not form or not all(32 <= ord(c) < 127 for c in form):
                raise ValueError("ASCII_PATH_REQUIRED: %s: %s" %
                                 (label, form.encode("unicode_escape").decode("ascii")))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def registry(path=REGISTRY):
    ascii_paths({"script": SCRIPT, "Python": sys.executable, "registry": path})
    result = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if result.get("schema_version") != 1:
        raise ValueError("Unsupported registry schema")
    ids = [p["id"] for p in result["profiles"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate registry profile")
    return result


def profile_ids(text, catalog):
    return sorted(p["id"] for p in catalog["profiles"]
                  if any(re.search(pattern, text or "", re.I) for pattern in p["patterns"]))


def _safe_xml(raw):
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        raise ValueError("DTD and entities are not supported")
    raw.decode("utf-8-sig")  # refuse a lossy or implicit vendor encoding conversion
    declaration = re.search(br"<\?xml\b[^>]*encoding\s*=\s*['\"]([^'\"]+)['\"]", raw, re.I)
    if declaration and declaration.group(1).lower().replace(b"-", b"") != b"utf8":
        raise ValueError("Only UTF-8 XML is supported; do not convert calibrated files implicitly")
    root = ET.fromstring(raw)
    if root.tag != "KinematicModel":
        raise ValueError("Expected unnamespaced KinematicModel XML")
    return root


def definition(path, display_template=False):
    path = Path(path)
    ascii_paths({"VSK/VST": path})
    raw = path.read_bytes()
    warnings = []
    try:
        root = _safe_xml(raw)
    except ET.ParseError:
        # Some installed VSTs contain malformed unrelated TargetSet attributes.
        # Read only a complete strict MarkerSet and root metadata in that case.
        # Never use this path for the calibrated VSK we will write or import.
        if not display_template or path.suffix.lower() != ".vst":
            raise
        if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
            raise ValueError("DTD and entities are not supported")
        raw.decode("utf-8-sig")
        headers = re.findall(br"<KinematicModel\b[^<>]*>", raw)
        sections = re.findall(br"<MarkerSet\b[^<>]*>.*?</MarkerSet\s*>", raw, re.S)
        if len(headers) != 1 or len(sections) != 1:
            raise ValueError("Cannot isolate unique display metadata")
        root = _safe_xml(headers[0] + sections[0] + b"</KinematicModel>")
        warnings.append("template_full_xml_invalid_read_only_markerset_extracted")
    marker_sets = root.findall("./MarkerSet")
    if len(marker_sets) != 1:
        raise ValueError("Expected exactly one MarkerSet")
    sets = marker_sets[0].findall("./Markers")
    if len(sets) != 1:
        raise ValueError("Expected exactly one Markers list")
    markers = [m.get("NAME", "") for m in sets[0].findall("./Marker")]
    if not markers or any(not m for m in markers) or len(markers) != len(set(markers)):
        raise ValueError("Empty or duplicate marker names")
    stick_sets = marker_sets[0].findall("./Sticks")
    if len(stick_sets) > 1:
        raise ValueError("Multiple Sticks sections")
    sticks = list(stick_sets[0]) if stick_sets else []
    keys = []
    for stick in sticks:
        a, b = stick.get("MARKER1"), stick.get("MARKER2")
        if stick.tag != "Stick" or a not in markers or b not in markers or a == b:
            raise ValueError("Invalid display Stick endpoint or element")
        for attr in ("RGB1", "RGB2"):
            if attr in stick.attrib:
                values = stick.get(attr).split()
                if len(values) != 3 or any(not v.isdigit() or not 0 <= int(v) <= 255 for v in values):
                    raise ValueError("Invalid display RGB value")
        keys.append(edge_key(stick))
    if len(keys) != len(set(keys)):
        warnings.append("duplicate_existing_display_edges_preserved")
    return dict(path=str(path.resolve()), raw=raw, root=root,
                model=root.get("MODEL", ""), markers=sorted(markers), sticks=sticks,
                sha256=sha256(raw), warnings=warnings)


def edge_key(stick):
    return tuple(sorted((stick.get("MARKER1"), stick.get("MARKER2"))))


def edge_signature(sticks):
    # Canonicalize direction with endpoint colors, preserving other attributes.
    result = []
    for s in sticks:
        attrs = dict(s.attrib)
        a, b = attrs.pop("MARKER1"), attrs.pop("MARKER2")
        ca, cb = attrs.pop("RGB1", ""), attrs.pop("RGB2", "")
        if a > b:
            a, b, ca, cb = b, a, cb, ca
        result.append((a, b, ca, cb, tuple(sorted(attrs.items()))))
    return sorted(set(result))


def default_roots():
    roots = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        base = Path(os.environ.get(variable, "C:/Program Files")) / "Vicon"
        if base.is_dir():
            roots.extend(base.glob("Nexus*/ModelTemplates"))
            roots.extend(base.glob("Nexus*/Python/Lib/site-packages/pyCGM2/Install/vst"))
    return sorted(set(roots), key=str)


def discover(roots):
    definitions, errors, seen = [], [], set()
    for folder in roots:
        folder = Path(folder)
        ascii_paths({"template root": folder})
        if not folder.is_dir():
            errors.append(dict(path=str(folder), error="template_root_missing"))
            continue
        # Only VSTs are scanned automatically. Subject VSKs require --template.
        for path in sorted(folder.rglob("*")):
            if path.suffix.lower() != ".vst" or not path.is_file():
                continue
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                definitions.append(definition(path, display_template=True))
            except (OSError, ValueError, ET.ParseError) as error:
                errors.append(dict(path=str(path), error=str(error)))
    return definitions, errors


def identify(target, candidates, catalog, nexus_template=""):
    root_ids = profile_ids(target["model"], catalog)
    hint_ids = profile_ids(nexus_template, catalog)
    matches = [c for c in candidates if c["markers"] == target["markers"]]
    compatible_ids = sorted(set(i for c in matches for i in profile_ids(c["model"], catalog)))
    conflict = bool(root_ids and hint_ids and root_ids != hint_ids)
    status = "metadata_conflict" if conflict else (
        "metadata_identified" if len(root_ids) == 1 else
        "metadata_ambiguous" if root_ids else "unknown_model")
    return dict(status=status, profile_ids=root_ids, nexus_template_profile_ids=hint_ids,
                model_metadata=target["model"], nexus_template=nexus_template,
                marker_layout_candidate_profiles=compatible_ids,
                evidence="VSK MODEL and optional Nexus template; XML VERSION is not model version",
                calibration_verified=False,
                caveat="Recognizes declared labeling-template identity, not proof of the dynamic pipeline used.")


def make_plan(target, candidates, catalog, nexus_template="", explicit=None):
    detection = identify(target, candidates, catalog, nexus_template)
    report = dict(version=VERSION, status="needs_template", detection=detection,
                  input_vsk=target["path"], input_sha256=target["sha256"],
                  marker_count=len(target["markers"]), existing_edges=len(target["sticks"]),
                  warnings=list(target["warnings"]), candidates=[], additions=[],
                  changed_scope="MarkerSet/Sticks only", applied_in_nexus=False)
    if detection["status"] in ("metadata_conflict", "metadata_ambiguous"):
        report["status"] = detection["status"]
        return report
    eligible = []
    for candidate in candidates:
        exact_model = bool(target["model"]) and candidate["model"].casefold() == target["model"].casefold()
        exact_layout = candidate["markers"] == target["markers"]
        if exact_model or exact_layout:
            report["candidates"].append(dict(path=candidate["path"], model=candidate["model"],
                exact_model=exact_model, exact_marker_layout=exact_layout,
                display_edges=len(candidate["sticks"])))
        if exact_model and exact_layout and candidate["sticks"]:
            eligible.append(candidate)
    if explicit is not None:
        ids = profile_ids(explicit["model"], catalog)
        if detection["profile_ids"] and ids and ids != detection["profile_ids"]:
            report["status"] = "explicit_template_model_conflict"
            return report
        eligible = [explicit]
    if not eligible:
        if target["sticks"]:
            report["status"] = "existing_links_only_no_matching_template"
        return report
    signatures = [edge_signature(c["sticks"]) for c in eligible]
    if any(s != signatures[0] for s in signatures[1:]):
        report["status"] = "ambiguous_display_definitions"
        return report
    chosen = eligible[0]
    names, seen = set(target["markers"]), {edge_key(s) for s in target["sticks"]}
    additions, skipped = [], []
    for stick in chosen["sticks"]:
        key = edge_key(stick)
        if not set(key).issubset(names):
            skipped.append(list(key))
        elif key not in seen:
            additions.append(dict(stick.attrib))
            seen.add(key)
    report.update(status="ready" if additions else "already_complete",
                  selection_basis="user_selected_template" if explicit is not None else "exact_model_and_marker_layout",
                  template_sources=[dict(path=c["path"], sha256=c["sha256"], warnings=c["warnings"]) for c in eligible],
                  additions=additions, skipped_edges_missing_target_markers=skipped,
                  preserved_existing_edges=True)
    if skipped:
        report["warnings"].append("template_edges_with_absent_target_markers_skipped")
    return report


def _without_sticks(root):
    result = copy.deepcopy(root)
    for marker_set in result.findall("./MarkerSet"):
        for sticks in marker_set.findall("./Sticks"):
            marker_set.remove(sticks)
    def structure(element):
        return (element.tag, sorted(element.attrib.items()), (element.text or "").strip(),
                tuple(structure(c) for c in element))
    return structure(result)


def patch_sticks(target, additions):
    raw, root = target["raw"], target["root"]
    old_sets = root.findall("./MarkerSet/Sticks")
    sticks = copy.deepcopy(old_sets[0]) if old_sets else ET.Element("Sticks")
    for attrs in additions:
        ET.SubElement(sticks, "Stick", attrs)
    replacement = ET.tostring(sticks, encoding="utf-8").rstrip()
    sections = list(re.finditer(br"<MarkerSet\b[^<>]*>.*?</MarkerSet\s*>", raw, re.S))
    if len(sections) != 1:
        raise ValueError("Cannot isolate unique MarkerSet byte range")
    section = sections[0]
    body = section.group()
    spans = list(re.finditer(br"<Sticks\b[^<>]*(?:/>|>.*?</Sticks\s*>)", body, re.S))
    if len(spans) != len(old_sets):
        raise ValueError("Ambiguous Sticks byte ranges (including comments)")
    if spans:
        start, end = section.start() + spans[0].start(), section.start() + spans[0].end()
    else:
        start = end = section.start() + body.rindex(b"</MarkerSet")
        replacement += b"\n  "
    result = raw[:start] + replacement + raw[end:]
    rebuilt = _safe_xml(result)
    if _without_sticks(root) != _without_sticks(rebuilt):
        raise AssertionError("Refusing a non-display XML change")
    expected = list(target["sticks"]) + [ET.Element("Stick", a) for a in additions]
    if edge_signature(rebuilt.findall("./MarkerSet/Sticks/Stick")) != edge_signature(expected):
        raise AssertionError("Display edge write verification failed")
    return result, dict(non_display_structure_unchanged=True,
                        bytes_outside_sticks_unchanged=True,
                        input_sticks=len(target["sticks"]), added_sticks=len(additions))


def write_result(target, report, output_root):
    output_root = Path(output_root)
    ascii_paths({"output": output_root})
    if report["status"] not in ("ready", "already_complete"):
        return report
    # New child folder, same subject filename: no source overwrite or renaming.
    input_path = Path(target["path"])
    if input_path.suffix.lower() != ".vsk":
        raise ValueError("Target must be a saved calibrated VSK, not a VST")
    if sha256(input_path.read_bytes()) != target["sha256"]:
        raise RuntimeError("Input VSK changed since planning")
    output_root.mkdir(parents=True, exist_ok=True)
    destination_dir = Path(tempfile.mkdtemp(prefix="NML_", dir=str(output_root)))
    destination = destination_dir / input_path.name
    result, verification = patch_sticks(target, report["additions"])
    with destination.open("xb") as handle:
        handle.write(result)
    reread = definition(destination)
    if reread["raw"] != result or sha256(input_path.read_bytes()) != target["sha256"]:
        raise RuntimeError("Output verification or source protection failed; do not import")
    report = dict(report, status="vsk_copy_verified", output_vsk=str(destination),
                  output_sha256=sha256(result), verification=verification,
                  next_action="Use only if subject edits were saved BEFORE generation; otherwise save and rerun. File Import > Import VSK on a trial copy, confirm the original subject and 3D display. No live import was performed.")
    report_path = destination_dir / "display-links.report.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def analyze(vsk, roots=None, template=None, nexus_template="", output=None, write=False):
    catalog = registry()
    if Path(vsk).suffix.lower() != ".vsk":
        raise ValueError("Target must be a saved calibrated VSK, not a VST")
    target = definition(vsk)
    candidates, errors = discover(default_roots() if roots is None else roots)
    explicit = definition(template, display_template=True) if template else None
    report = make_plan(target, candidates, catalog, nexus_template, explicit)
    report["template_scan_errors"] = errors
    report["template_definitions_scanned"] = len(candidates)
    available = set(i for c in candidates + ([explicit] if explicit else [])
                    for i in profile_ids(c["model"], catalog))
    report["registry_profiles_with_local_definition"] = sorted(available)
    report["registry_profiles_without_local_definition"] = sorted(
        p["id"] for p in catalog["profiles"] if p["id"] not in available)
    report["saved_vsk_only"] = True
    if write:
        if output is None:
            raise ValueError("An ASCII output directory is required")
        report = write_result(target, report, output)
    return report


def prepare_nexus(nexus, roots=None, template=None, output=None):
    folder, trial = nexus.GetTrialName()
    names, templates, active = nexus.GetSubjectInfo()
    selected = [i for i, enabled in enumerate(active) if enabled]
    if not folder or not trial or len(selected) != 1:
        raise ValueError("Open a saved trial copy with exactly one active subject")
    index = selected[0]
    subject = names[index]
    target = Path(folder) / (subject + ".vsk")
    # The full session and configuration paths remain subject to the gap-fill
    # preflight as well; check every local session file used by this operation.
    paths = {"trial": Path(folder) / (trial + ".c3d"), "subject VSK": target}
    for path in Path(folder).iterdir():
        if path.suffix.lower() in (".c3d", ".vsk", ".vst", ".enf", ".mp", ".xcp", ".x2d", ".x1d"):
            paths["session file " + path.name] = path
    ascii_paths(paths)
    disk = definition(target)
    loaded = set(nexus.GetMarkerNames(subject))
    # SDK GetMarkerNames may omit static-only markers without trajectories.
    # Include physical markers attached to labeling-skeleton segments, as in
    # the gap-fill adapter; never derive a marker from a model-output name.
    for segment in nexus.GetSegmentNames(subject):
        loaded.update(nexus.GetSegmentDetails(subject, segment)[2])
    loaded_markers = sorted(loaded)
    # A mismatch may indicate unsaved labeling-model edits. Never import a stale
    # saved calibration silently. Observability of a marker is irrelevant here.
    if loaded_markers != disk["markers"]:
        raise ValueError("SAVED_VSK_MARKERS_DIFFER_FROM_NEXUS: save subject edits and check the VSK first")
    root = output or Path(os.environ.get("LOCALAPPDATA", str(SCRIPT.parent))) / "NexusModelLinks"
    report = analyze(target, roots, template, templates[index], root, write=True)
    report["subject"] = subject
    report["trial"] = trial
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vsk", help="Saved calibrated VSK, offline read-only unless --write")
    parser.add_argument("--template", help="Explicit display definition VST/VSK, exact marker labels")
    parser.add_argument("--template-root", action="append", help="Additional local VST directory")
    parser.add_argument("--no-default-roots", action="store_true")
    parser.add_argument("--output", help="New generated child folders are placed here")
    parser.add_argument("--write", action="store_true", help="Write a separate verified VSK copy")
    parser.add_argument("--list-models", action="store_true")
    args = parser.parse_args(argv)
    if args.list_models:
        result = registry()
    else:
        roots = ([] if args.no_default_roots else default_roots()) + [Path(p) for p in args.template_root or []]
        if args.vsk:
            result = analyze(args.vsk, roots, args.template, output=args.output, write=args.write)
        else:
            from viconnexusapi import ViconNexus
            nexus = ViconNexus.ViconNexus()
            if not nexus.IsConnected():
                raise RuntimeError("Open Nexus 2.16 Offline first")
            result = prepare_nexus(nexus, roots, args.template, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in (None, "ready", "already_complete", "vsk_copy_verified",
                                         "existing_links_only_no_matching_template") else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("NEXUS_MODEL_LINKS_FAILED: " + str(error))
        sys.exit(1)
