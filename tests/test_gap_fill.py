import csv
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# SPDX-License-Identifier: MIT
import importlib.util
sys.path.insert(0, str(ROOT / "scripts"))
import NexusGapFill_Single as bridge
from NexusGapFill_Single import (array_frame_origin, complete_remaining, eligible_rigid_groups,
                                low_dimensional_kalman, roi_mask)
import stage_session



def moving_clusters(frames=240):
    t = np.arange(frames, dtype=float)
    theta = 0.006*t
    local = np.array([[0, 0, 0], [120, 0, 0], [0, 90, 0], [10, 20, 80],
                      [250, 20, 30], [330, 0, 50], [240, 100, 100]], float)
    xyz = np.empty((frames, len(local), 3))
    for i in range(frames):
        rotation = np.array([[np.cos(theta[i]), -np.sin(theta[i]), 0],
                             [np.sin(theta[i]), np.cos(theta[i]), 0], [0, 0, 1]])
        xyz[i] = local.dot(rotation.T) + [2*t[i], 20*np.sin(t[i]/30), 900+15*np.sin(t[i]/25)]
    names = ["custom_%s" % i for i in range(len(local))]
    return xyz, names, {"arbitrary_segment": names[:4]}


class CoreTests(unittest.TestCase):
    def test_kalman_reconstructs_hidden_correlated_motion(self):
        truth, _, _ = moving_clusters()
        exists = np.ones(truth.shape[:2], bool)
        exists[85:110, 1] = False
        missing = truth.copy()
        missing[~exists] = np.nan
        prediction, variance = low_dimensional_kalman(missing, exists)
        rmse = np.sqrt(np.mean(np.sum((prediction[~exists]-truth[~exists])**2, axis=1)))
        self.assertLess(rmse, 10.0)
        self.assertTrue(np.isfinite(variance).all())
        self.assertTrue((variance >= 0).all())

    def test_native_and_input_are_frozen_even_for_wrong_native_estimate(self):
        truth, names, groups = moving_clusters()
        original_valid = np.ones(truth.shape[:2], bool)
        original_valid[:25, 0] = False
        original_valid[-35:, 1] = False
        original_valid[80:100, 2] = False
        original_valid[130:165, 3:5] = False
        original = truth.copy()
        original[~original_valid] = 0.0
        native, native_valid = original.copy(), original_valid.copy()
        native[80:100, 2] = truth[80:100, 2] + 7.0
        native_valid[80:100, 2] = True
        roi = np.ones(len(truth), bool)
        result = complete_remaining(original, original_valid, native, native_valid,
                                    names, groups, roi)
        self.assertTrue(result["valid"].all())
        np.testing.assert_array_equal(result["xyz"][native_valid], native[native_valid])
        self.assertTrue(result["low"][~native_valid].all())
        self.assertFalse(result["low"][native_valid].any())
        self.assertTrue(np.isfinite(result["xyz"]).all())

    def test_no_complete_frames_falls_back_without_zero_fabrication(self):
        truth, names, _ = moving_clusters(120)
        valid = np.ones(truth.shape[:2], bool)
        for i in range(len(valid)):
            valid[i, i % len(names)] = False
        valid[:30, 0] = False
        valid[-30:, 1] = False
        source = truth.copy()
        source[~valid] = 0
        with self.assertRaises(ValueError):
            low_dimensional_kalman(source, valid)
        result = complete_remaining(source, valid, source, valid, names, {},
                                    np.ones(len(truth), bool))
        self.assertTrue(result["valid"].all())
        self.assertTrue(np.isfinite(result["xyz"]).all())
        self.assertTrue((np.linalg.norm(result["xyz"][~valid], axis=1) > 100).all())
        self.assertTrue(any("insufficient_common" in s for s in result["warnings"]))

    def test_roi_and_non_one_based_frame_mapping(self):
        self.assertEqual(array_frame_origin(200, (501, 700)), 501)
        self.assertEqual(array_frame_origin(700, (501, 700)), 1)
        with self.assertRaises(ValueError):
            array_frame_origin(33, (501, 700))
        truth, names, groups = moving_clusters(200)
        valid = np.ones(truth.shape[:2], bool)
        valid[:30, 0] = False
        valid[50:75, 1] = False
        valid[-20:, 2] = False
        source = truth.copy()
        source[~valid] = 0
        roi = roi_mask(200, 501, (541, 650))
        result = complete_remaining(source, valid, source, valid, names, groups, roi)
        np.testing.assert_array_equal(result["xyz"][~roi], source[~roi])
        np.testing.assert_array_equal(result["valid"][~roi], valid[~roi])
        self.assertTrue(result["valid"][roi].all())

    def test_unobserved_marker_is_explicitly_unresolved(self):
        xyz, names, groups = moving_clusters(120)
        valid = np.ones(xyz.shape[:2], bool)
        valid[:, -1] = False
        xyz[:, -1] = 0
        result = complete_remaining(xyz, valid, xyz, valid, names, groups,
                                    np.ones(len(xyz), bool))
        self.assertFalse(result["valid"][:, -1].any())
        self.assertTrue(any("unidentifiable" in w for w in result["warnings"]))

    def test_relabelled_layout_and_nonrigid_group(self):
        xyz, names, groups = moving_clusters()
        valid = np.ones(xyz.shape[:2], bool)
        accepted, _ = eligible_rigid_groups(xyz, valid, names, groups)
        self.assertIn("arbitrary_segment", accepted)
        xyz[:, 3, 0] += np.arange(len(xyz))*4
        accepted, rejected = eligible_rigid_groups(xyz, valid, names, groups)
        self.assertNotIn("arbitrary_segment", accepted)
        self.assertIn("arbitrary_segment", rejected)


class PipelineTests(unittest.TestCase):
    def test_generated_pipeline_has_native_first_no_hardcoded_marker_names(self):
        profile = dict(settings=bridge.settings(), rigid_groups={"segment_z": ["a", "b", "c", "d"]})
        xml = bridge.generate_pipeline(profile, Path("C:/layout with spaces/profile.json"))
        root = ET.fromstring(xml)
        ops = [entry.attrib["OperationName"] for entry in root]
        self.assertEqual(ops[0], "Python")
        self.assertIn("FillGapsWoltring", ops)
        self.assertIn("ComputeSubjectMotion", ops)
        self.assertIn("FillGapsKinematic", ops)
        finish_index = next(i for i, e in enumerate(root) if "Complete remaining" in e.attrib["DisplayName"])
        self.assertGreater(finish_index, ops.index("FillGapsKinematic"))
        self.assertEqual(ops[finish_index+1], "Exportc3d")
        text = xml.decode("utf-8")
        self.assertIn("a,b,c,d", text)
        self.assertNotIn("LASI", text)
        self.assertNotIn("LFHD", text)
        self.assertIn("_NGF", text)

    def test_protected_source_root_cannot_be_processed(self):
        with self.assertRaises(RuntimeError):
            bridge._check_write_target("D:/RawCapture/session", {"protected_roots": ["D:/RawCapture"]})
        bridge._check_write_target("C:/testcopy", {"protected_roots": ["D:/RawCapture"]})

    def test_csv_labels_every_roi_frame_and_low_estimate(self):
        xyz, names, groups = moving_clusters(120)
        valid = np.ones(xyz.shape[:2], bool)
        valid[10:20, 0] = False
        source = xyz.copy()
        source[~valid] = 0
        roi = roi_mask(120, 501, (506, 530))
        result = complete_remaining(source, valid, source, valid, names, groups, roi)
        meta = dict(array_frame_origin=501, roi=[506, 530], trial_range=[501, 620],
                    frame_rate=120., trial="example", subject="custom_subject",
                    description=dict(target_markers=names))
        test_dir = ROOT / "test_work"
        test_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(test_dir)) as directory:
            path = Path(directory) / "audit.csv"
            bridge._csv_report(path, meta, result)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 25*len(names))
        self.assertEqual(min(int(r["OriginalFrame"]) for r in rows), 506)
        self.assertEqual(max(int(r["OriginalFrame"]) for r in rows), 530)
        estimates = [r for r in rows if r["Source"] == "FALLBACK_ESTIMATE"]
        self.assertEqual(len(estimates), 10)
        self.assertTrue(all(r["LowReliability"] == "1" and r["Reliability"] == "LOW" for r in estimates))


class FakeNexus:
    """SDK boundary simulator; no claim that this executes Vicon algorithms."""
    def __init__(self, directory):
        truth, names, _ = moving_clusters(120)
        self.truth = truth[:, :4].copy()
        self.markers = names[:4]
        self.data = {name: [self.truth[:, i].copy(), np.ones(120, bool)]
                     for i, name in enumerate(self.markers)}
        self.data[self.markers[0]][1][10:18] = False
        self.data[self.markers[0]][0][10:18] = 0
        self.folder = str(directory) + "\\"
        self.roi = (506, 550)
        self.invalidated = []
        self.original = {m: (x.copy(), e.copy()) for m, (x, e) in self.data.items()}

    def GetSubjectInfo(self): return ["S"], ["any_layout"], [True]
    def GetMarkerNames(self, subject): return list(self.data)
    def GetSegmentNames(self, subject): return ["custom_segment"]
    def GetSegmentDetails(self, subject, segment): return "World", [], self.markers + ["optional_marker"]
    def GetTrialName(self): return self.folder, "demo"
    def GetTrialRange(self): return 501, 620
    def GetTrialRegionOfInterest(self): return self.roi
    def GetFrameRate(self): return 120.
    def HasTrajectory(self, subject, name): return name in self.data
    def GetTrajectory(self, subject, name):
        a, e = self.data[name]
        return a[:, 0].tolist(), a[:, 1].tolist(), a[:, 2].tolist(), e.tolist()
    def SetTrajectory(self, subject, marker, x, y, z, e):
        self.data[marker] = [np.column_stack([x, y, z]), np.asarray(e, bool)]
    def GetModelOutputNames(self, subject): return ["old_angle"]
    def GetModelOutput(self, subject, name): return [[1.]*120]*3, [True]*120
    def SetModelOutput(self, subject, name, values, e):
        self.invalidated.append((name, any(e)))


class BoundaryIntegrationTests(unittest.TestCase):
    def test_moved_installation_registers_distinct_pipeline_name(self):
        test_dir = ROOT / "test_work"
        test_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(test_dir)) as directory:
            temp = Path(directory)
            session = temp / "session"
            session.mkdir()
            nexus = FakeNexus(session)
            prepared = []
            for name in ("installation_a", "installation_b"):
                app = temp / name
                app.mkdir()
                (app / "settings.json").write_text(json.dumps({"protected_roots": []}))
                with patch.object(bridge, "ROOT", app):
                    prepared.append(bridge.prepare(nexus))
            self.assertNotEqual(prepared[0]["pipeline"], prepared[1]["pipeline"])

    def test_begin_native_checkpoint_fallback_reports_and_optional_markers(self):
        test_dir = ROOT / "test_work"
        test_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(test_dir)) as directory:
            temp = Path(directory)
            app, session = temp / "app", temp / "session"
            app.mkdir(); session.mkdir()
            (app / "settings.json").write_text(json.dumps({"protected_roots": []}))
            (session / "demo.c3d").write_bytes(b"source-file-must-stay-identical")
            (session / "S.vsk").write_bytes(b"source-vsk-must-stay-identical")
            nexus = FakeNexus(session)
            with patch.object(bridge, "ROOT", app):
                prepared = bridge.prepare(nexus)
                bridge.begin(nexus, prepared["profile"])
                # Simulate a native fill and also an unwanted native edit.
                marker = nexus.markers[0]
                nexus.data[marker][0][10:13] = nexus.truth[10:13, 0]
                nexus.data[marker][1][10:13] = True
                nexus.data[nexus.markers[1]][0][5] += 100
                nexus.data[nexus.markers[1]][0][100] += 100
                # Simulate KinFit making an unused optional marker appear.
                nexus.data["optional_marker"] = [np.ones((120, 3)), np.ones(120, bool)]
                bridge.checkpoint(nexus, prepared["profile"], "nexus_test_simulation")
                self.assertFalse(nexus.data["optional_marker"][1].any())
                np.testing.assert_array_equal(nexus.data[nexus.markers[1]][0],
                                              nexus.original[nexus.markers[1]][0])
                bridge.finish(nexus, prepared["profile"])
                self.assertTrue(nexus.data[marker][1].all())
                np.testing.assert_array_equal(nexus.data[marker][0][10:13], nexus.truth[10:13, 0])
                for name, (values, exists) in nexus.original.items():
                    np.testing.assert_array_equal(nexus.data[name][0][exists], values[exists])
                self.assertEqual(nexus.invalidated, [("old_angle", False)])
                summary_path = next((session / "NGF_reports").glob("*/run.json"))
                state = bridge.read_json(summary_path)
                self.assertEqual(state["summary"]["native_added"], 3)
                self.assertEqual(state["summary"]["fallback_added"], 5)
                self.assertEqual(state["summary"]["remaining"], 0)
                with self.assertRaises(RuntimeError):
                    bridge.finish(nexus, prepared["profile"])
                # Real BTK I/O catches file naming, frame mapping and corrupted exports.
                from pyCGM2 import btk
                acquisition = btk.btkAcquisition()
                acquisition.Init(len(nexus.markers), 45, 0, 0)
                acquisition.SetFirstFrame(506)
                acquisition.SetPointFrequency(120.)
                for j, name in enumerate(nexus.markers):
                    point = acquisition.GetPoint(j)
                    point.SetLabel("S:" + name)
                    point.SetValues(nexus.data[name][0][5:50])
                    point.SetResiduals(np.zeros((45, 1)))
                writer = btk.btkAcquisitionFileWriter()
                writer.SetInput(acquisition)
                writer.SetFilename(str(session / "demo._NGF.c3d"))
                good = acquisition.GetPoint(0).GetValues().copy()
                acquisition.GetPoint(0).SetValues(good + 1.0)
                writer.Update()
                with self.assertRaisesRegex(RuntimeError, "coordinate mismatch"):
                    bridge.verify_export(nexus, prepared["profile"])
                acquisition.GetPoint(0).SetValues(good)
                writer.Update()
                bridge.verify_export(nexus, prepared["profile"])
                self.assertEqual(bridge.read_json(summary_path)["status"], "export_verified")
                self.assertEqual((session / "demo.c3d").read_bytes(), b"source-file-must-stay-identical")
                self.assertEqual((session / "S.vsk").read_bytes(), b"source-vsk-must-stay-identical")

    def test_roi_change_aborts_before_writeback(self):
        test_dir = ROOT / "test_work"
        test_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(test_dir)) as directory:
            temp = Path(directory)
            app, session = temp / "app", temp / "session"
            app.mkdir(); session.mkdir()
            (app / "settings.json").write_text(json.dumps({"protected_roots": []}))
            nexus = FakeNexus(session)
            with patch.object(bridge, "ROOT", app):
                prepared = bridge.prepare(nexus)
                bridge.begin(nexus, prepared["profile"])
                nexus.roi = (507, 550)
                with self.assertRaises(RuntimeError):
                    bridge.checkpoint(nexus, prepared["profile"], "bad")





class AsciiAndCopyTests(unittest.TestCase):
    def temporary(self):
        parent = ROOT / "test_work"
        parent.mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(dir=str(parent))

    def source_session(self, folder):
        folder.mkdir(parents=True)
        for name, contents in (("walk.c3d", b"saved-c3d"), ("walk.Trial.enf", b"saved-enf"), ("Subject.vsk", b"calibrated-vsk")):
            (folder / name).write_bytes(contents)
        return folder

    def test_printable_ascii_spaces_and_digits_are_accepted(self):
        bridge.ascii_path_guard({"sample": "C:/Vicon Work/Session_01/trial-02.c3d"})

    def test_non_ascii_parent_filename_and_control_character_are_rejected(self):
        for value in ("C:/Data/试验/Session01", "C:/Data/Session01/跳跃.c3d", "C:/Data/café", "C:/Data/\tSession01", "C:/Data/🙂"):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "ASCII_PATH_REQUIRED"):
                bridge.ascii_path_guard({"input": value})

    def test_english_alias_resolving_to_unicode_is_rejected(self):
        with patch.object(Path, "resolve", return_value=Path("C:/Data/中文")):
            with self.assertRaisesRegex(RuntimeError, "resolved"):
                bridge.ascii_path_guard({"alias": "C:/Data/EnglishAlias"})

    def test_unicode_trial_directory_stops_before_capture_or_writes(self):
        with self.temporary() as directory:
            folder = Path(directory)
            session = folder / "数据" / "Session01"
            session.mkdir(parents=True)
            nexus = FakeNexus(session)
            cache = folder / "not_created"
            with patch.object(bridge, "ROOT", cache), patch.object(bridge, "capture") as capture:
                with self.assertRaisesRegex(RuntimeError, "ASCII_PATH_REQUIRED"):
                    bridge.prepare(nexus, install=True)
                capture.assert_not_called()
            self.assertFalse(cache.exists())

    def test_unicode_trial_name_and_subject_vsk_are_rejected(self):
        with self.temporary() as directory:
            nexus = FakeNexus(Path(directory))
            with patch.object(nexus, "GetTrialName", return_value=(directory, "walking_中文")):
                with self.assertRaisesRegex(RuntimeError, "ASCII_PATH_REQUIRED"):
                    bridge.path_preflight(nexus)
            with patch.object(nexus, "GetSubjectInfo", return_value=(["受试者"], ["layout"], [True])):
                with self.assertRaisesRegex(RuntimeError, "ASCII_PATH_REQUIRED"):
                    bridge.path_preflight(nexus)

    def test_unicode_cache_or_private_config_stops_before_output(self):
        with self.temporary() as directory:
            folder = Path(directory)
            nexus = FakeNexus(folder)
            with patch.object(bridge, "ROOT", folder / "缓存"):
                with self.assertRaisesRegex(RuntimeError, "ASCII_PATH_REQUIRED"):
                    bridge.prepare(nexus)
            with patch.dict(bridge.os.environ, {"APPDATA": str(folder / "账户")}):
                with self.assertRaisesRegex(RuntimeError, "ASCII_PATH_REQUIRED"):
                    bridge.prepare(nexus, install=True)
            self.assertFalse((folder / "缓存").exists())
            self.assertFalse((folder / "账户").exists())

    def test_copy_unicode_source_parent_to_ascii_target_and_verify(self):
        with self.temporary() as directory:
            folder = Path(directory)
            source = self.source_session(folder / "源父目录" / "Session01")
            (source / "empty").mkdir()
            target = folder / "EnglishCopy"
            plan = stage_session.stage(source, target)
            self.assertEqual(plan["status"], "plan")
            self.assertFalse(target.exists())
            report = stage_session.stage(source, target, execute=True)
            self.assertEqual(report["status"], "verified_copy")
            self.assertTrue(report["source_unchanged"])
            self.assertTrue((target / "empty").is_dir())
            for name, digest in report["sha256"].items():
                self.assertEqual(stage_session.sha256(target / name), digest)
                self.assertEqual(stage_session.sha256(source / name), digest)

    def test_copy_rejects_unicode_relative_name_before_creating_target(self):
        with self.temporary() as directory:
            folder = Path(directory)
            source = self.source_session(folder / "source")
            (source / "静态.mp").write_bytes(b"x")
            target = folder / "destination"
            with self.assertRaisesRegex(RuntimeError, "ASCII_RELATIVE_NAME_REQUIRED"):
                stage_session.stage(source, target, True)
            self.assertFalse(target.exists())

    def test_copy_rejects_existing_overlapping_or_unicode_destination(self):
        with self.temporary() as directory:
            folder = Path(directory)
            source = self.source_session(folder / "source")
            existing = folder / "existing"
            existing.mkdir()
            for target, error in ((existing, "DESTINATION_EXISTS"), (source / "nested", "OVERLAP"), (folder / "副本", "ASCII_PATH_REQUIRED")):
                with self.subTest(target=str(target)), self.assertRaisesRegex(RuntimeError, error):
                    stage_session.stage(source, target, True)

    def test_source_changed_during_copy_is_detected(self):
        with self.temporary() as directory:
            folder = Path(directory)
            source = self.source_session(folder / "source")
            target = folder / "destination"
            real_copy = stage_session.shutil.copy2
            def changing_copy(src, dst):
                result = real_copy(src, dst)
                if Path(src).name == "walk.c3d":
                    Path(src).write_bytes(b"changed-during-copy")
                return result
            with patch.object(stage_session.shutil, "copy2", side_effect=changing_copy):
                with self.assertRaisesRegex(RuntimeError, "Source file changed"):
                    stage_session.stage(source, target, True)
            manifest = json.loads((target / "ngf-copy-manifest.json").read_text())
            self.assertEqual(manifest["status"], "failed_copy_do_not_use")

    def test_standalone_copy_has_no_project_dependency(self):
        import shutil
        import subprocess
        with self.temporary() as directory:
            folder = Path(directory)
            copied = folder / "Standalone Copy.py"
            shutil.copy2(str(Path(bridge.__file__)), str(copied))
            result = subprocess.run([sys.executable, str(copied), "--help"], cwd=str(folder), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 0, result.stderr)
            spec = importlib.util.spec_from_file_location("moved_ngf", str(copied))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.SCRIPT_FILE, copied.resolve())
            self.assertNotEqual(module.ROOT, bridge.ROOT)

    def test_blank_arguments_generate_pipeline_without_changing_coordinates(self):
        with self.temporary() as directory:
            folder = Path(directory)
            session = folder / "session"
            session.mkdir()
            nexus = FakeNexus(session)
            nexus.Disconnect = lambda: None
            with patch.object(bridge, "ROOT", folder / "cache"), patch.object(bridge, "connect", return_value=nexus), patch.dict(bridge.os.environ, {"APPDATA": str(folder / "appdata")}):
                bridge.main([])
                prepared = bridge.read_json(bridge.ROOT / "last_prepared.json")
            self.assertTrue(prepared["installed"])
            self.assertEqual(prepared["path_preflight"]["status"], "ascii_paths_ok")
            xml = ET.parse(prepared["generated_pipeline"]).getroot()
            for entry in xml:
                if entry.attrib["OperationName"] == "Python":
                    params = {p.attrib["name"]: p.attrib for p in entry.find("ParamList")}
                    self.assertEqual(params["Script"]["value"], str(bridge.SCRIPT_FILE))
            for marker, (values, valid) in nexus.original.items():
                np.testing.assert_array_equal(nexus.data[marker][0], values)
                np.testing.assert_array_equal(nexus.data[marker][1], valid)

if __name__ == "__main__":
    unittest.main(verbosity=2)
