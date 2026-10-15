# SPDX-License-Identifier: MIT
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import NexusModelLinks as links


def fixture(path, model="pyCGM2-FullBody_CGM23", names=("A", "B", "C"), edges=(), extra=""):
    text = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<KinematicModel MODEL="%s" VERSION="3.2">\n'
            '<!-- synthetic calibration, intentionally retain formatting -->\n'
            '<Parameters><Parameter NAME="Length" VALUE="321.123456789"/></Parameters>\n'
            '<Skeleton><Segment NAME="Synthetic" POSITION="1 2 3"/></Skeleton>\n'
            '<MarkerSet><Markers>%s</Markers>\n<Sticks>%s</Sticks>\n</MarkerSet>\n'
            '<TargetSet><Target MARKER="A" POSITION="private_parameter 2 3"/></TargetSet>%s\n'
            '</KinematicModel>') % (model, ''.join('<Marker NAME="%s"/>' % n for n in names),
                ''.join('<Stick MARKER1="%s" MARKER2="%s" RGB1="255 0 0" RGB2="0 255 0"/>' % e for e in edges), extra)
    path.write_text(text, encoding="utf-8")
    return path


class ModelLinksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nml_test_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = links.registry()

    def item(self, name="subject.vsk", **kwargs):
        return links.definition(fixture(self.root / name, **kwargs))

    def plan(self, target, candidates, **kwargs):
        return links.make_plan(target, candidates, self.catalog, **kwargs)

    def test_declared_version_not_xml_format_version(self):
        target = self.item(model="Custom", edges=(("A", "B"),))
        result = links.identify(target, [], self.catalog)
        self.assertEqual(result["status"], "unknown_model")
        self.assertEqual(result["profile_ids"], [])
        self.assertFalse(result["calibration_verified"])

    def test_explicit_cgm_version_and_all_catalog_identity_patterns(self):
        samples = {"plug-in-gait": "PlugInGait_Ai", "cgm1.0": "CGM1.0",
                   "cgm1.1": "CGM11", "cgm1-medial": "pyCGM2-LowerLimb_CGM1_medial",
                   "cgm1-kad-medial": "pyCGM2-FullBody_CGM1_KADmed",
                   "oxford-foot": "OxfordFootModel LowerBody Ai",
                   "hybrid-cast-visual3d": "Hybrid CAST Visual3D Ai"}
        samples.update({"cgm2.%d" % i: "pyCGM2-FullBody_CGM2%d" % i for i in range(1, 7)})
        for expected, text in samples.items():
            with self.subTest(expected=expected):
                self.assertEqual(links.profile_ids(text, self.catalog), [expected])
        self.assertEqual(links.profile_ids("CGM2.30", self.catalog), [])

    def test_common_marker_set_does_not_identify_computational_version(self):
        target = self.item(model="Unspecified")
        candidates = [self.item("a.vst", model="CGM2.1", edges=(("A", "B"),)),
                      self.item("b.vst", model="CGM2.2", edges=(("A", "B"),))]
        report = self.plan(target, candidates)
        self.assertEqual(report["status"], "needs_template")
        self.assertEqual(report["detection"]["profile_ids"], [])
        self.assertEqual(report["detection"]["marker_layout_candidate_profiles"], ["cgm2.1", "cgm2.2"])
        self.assertEqual(report["additions"], [])

    def test_nexus_model_conflict_blocks_even_an_explicit_template(self):
        target = self.item()
        source = self.item("model.vst", edges=(("A", "B"),))
        report = self.plan(target, [source], nexus_template="CGM2.4", explicit=source)
        self.assertEqual(report["status"], "metadata_conflict")
        self.assertEqual(report["additions"], [])

    def test_same_model_different_layout_not_chosen_from_metadata_alone(self):
        target = self.item(model="OxfordFootModel LowerBody Ai")
        source = self.item("left.vst", model=target["model"], names=("A", "B"), edges=(("A", "B"),))
        self.assertEqual(self.plan(target, [source])["status"], "needs_template")

    def test_custom_self_describing_template_supported_without_registry_entry(self):
        target = self.item(model="LabCustom42")
        source = self.item("custom.vst", model="LabCustom42", edges=(("A", "B"),))
        report = self.plan(target, [source])
        self.assertEqual(report["detection"]["status"], "unknown_model")
        self.assertEqual(report["status"], "ready")

    def test_conflicting_local_template_versions_require_selection(self):
        target = self.item()
        a = self.item("v1.vst", edges=(("A", "B"),))
        b = self.item("v2.vst", edges=(("A", "C"),))
        self.assertEqual(self.plan(target, [a, b])["status"], "ambiguous_display_definitions")
        selected = self.plan(target, [a, b], explicit=b)
        self.assertEqual(selected["selection_basis"], "user_selected_template")
        self.assertEqual(selected["additions"][0]["MARKER2"], "C")

    def test_equivalent_template_copies_do_not_create_false_ambiguity(self):
        target = self.item()
        a = self.item("a.vst", edges=(("A", "B"),))
        b = self.item("b.vst", edges=(("A", "B"),))
        self.assertEqual(self.plan(target, [a, b])["status"], "ready")

    def test_existing_colors_and_edges_kept_adds_only_missing(self):
        target = self.item(edges=(("B", "A"),))
        source = self.item("model.vst", edges=(("A", "B"), ("B", "C")))
        report = self.plan(target, [source])
        self.assertEqual(len(report["additions"]), 1)
        result, verification = links.patch_sticks(target, report["additions"])
        parsed = ET.fromstring(result)
        first = parsed.find("./MarkerSet/Sticks/Stick")
        self.assertEqual(first.get("MARKER1"), "B")
        self.assertEqual(first.get("RGB1"), "255 0 0")
        self.assertTrue(verification["non_display_structure_unchanged"])
        old = target["raw"]
        prefix, suffix = old.split(b"<Sticks>", 1)[0], old.split(b"</Sticks>", 1)[1]
        self.assertTrue(result.startswith(prefix))
        self.assertTrue(result.endswith(suffix))

    def test_no_sticks_or_empty_sticks_can_be_populated_without_other_edits(self):
        for replacement in (b"", b"<Sticks/>"):
            target = self.item()
            raw = target["raw"].replace(b"<Sticks></Sticks>", replacement)
            Path(target["path"]).write_bytes(raw)
            target = links.definition(target["path"])
            output, _ = links.patch_sticks(target, [{"MARKER1": "A", "MARKER2": "C"}])
            self.assertEqual(ET.fromstring(output).find("./MarkerSet/Sticks/Stick").get("MARKER2"), "C")

    def test_explicit_template_skips_absent_markers_never_creates_points(self):
        target = self.item()
        source = self.item("source.vst", names=("A", "B", "C", "D"), edges=(("A", "B"), ("C", "D")))
        report = self.plan(target, [], explicit=source)
        self.assertEqual(len(report["additions"]), 1)
        self.assertEqual(report["skipped_edges_missing_target_markers"], [["C", "D"]])
        output, _ = links.patch_sticks(target, report["additions"])
        self.assertEqual(len(ET.fromstring(output).findall("./MarkerSet/Markers/Marker")), 3)

    def test_known_other_family_explicit_template_refused(self):
        target = self.item()
        other = self.item("pig.vst", model="PlugInGait_Ai", edges=(("A", "B"),))
        self.assertEqual(self.plan(target, [], explicit=other)["status"], "explicit_template_model_conflict")

    def test_existing_custom_links_are_reported_without_invented_extras(self):
        target = self.item(model="Custom", edges=(("A", "C"),))
        result = self.plan(target, [])
        self.assertEqual(result["status"], "existing_links_only_no_matching_template")
        self.assertFalse(result["applied_in_nexus"])
        self.assertEqual(result["additions"], [])

    def test_write_protects_source_and_uses_same_basename_in_new_folders(self):
        target = self.item(edges=(("A", "B"),))
        source = self.item("model.vst", edges=(("A", "B"), ("B", "C")))
        plan = self.plan(target, [source])
        first = links.write_result(target, plan, self.root / "generated")
        second = links.write_result(target, plan, self.root / "generated")
        self.assertNotEqual(first["output_vsk"], second["output_vsk"])
        self.assertEqual(Path(first["output_vsk"]).name, Path(target["path"]).name)
        self.assertEqual(Path(target["path"]).read_bytes(), target["raw"])
        self.assertEqual(first["status"], "vsk_copy_verified")
        self.assertFalse(first["applied_in_nexus"])
        saved = json.loads(Path(first["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["output_sha256"], first["output_sha256"])

    def test_source_changed_after_planning_refuses_write(self):
        target = self.item()
        source = self.item("model.vst", edges=(("A", "B"),))
        plan = self.plan(target, [source])
        Path(target["path"]).write_bytes(target["raw"] + b"\n")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            links.write_result(target, plan, self.root / "generated")
        self.assertFalse((self.root / "generated").exists())

    def test_ambiguous_plan_cannot_write_an_importable_vsk(self):
        target = self.item()
        plan = self.plan(target, [])
        result = links.write_result(target, plan, self.root / "generated")
        self.assertNotIn("output_vsk", result)
        self.assertFalse((self.root / "generated").exists())

    def test_only_template_display_sections_can_be_read_when_unrelated_xml_invalid(self):
        path = fixture(self.root / "vendor.vst", edges=(("A", "B"),), extra='<Bad VALUE="1"BROKEN="2"/>')
        item = links.definition(path, display_template=True)
        self.assertEqual(len(item["sticks"]), 1)
        self.assertIn("template_full_xml_invalid_read_only_markerset_extracted", item["warnings"])
        path_vsk = self.root / "subject.vsk"
        path_vsk.write_bytes(path.read_bytes())
        with self.assertRaises(ET.ParseError):
            links.definition(path_vsk, display_template=True)

    def test_invalid_endpoint_and_entity_refused(self):
        path = fixture(self.root / "invalid.vsk", edges=(("A", "D"),))
        with self.assertRaisesRegex(ValueError, "endpoint"):
            links.definition(path)
        path.write_bytes(b'<!DOCTYPE KinematicModel [<!ENTITY xx "A">]><KinematicModel/>')
        with self.assertRaisesRegex(ValueError, "DTD"):
            links.definition(path)

    def test_discovery_reads_vst_not_other_subject_vsk(self):
        fixture(self.root / "a.vst", edges=(("A", "B"),))
        fixture(self.root / "private.vsk", edges=(("A", "C"),))
        (self.root / "bad.vst").write_bytes(b"invalid")
        items, errors = links.discover([self.root])
        self.assertEqual(len(items), 1)
        self.assertEqual(len(errors), 1)
        self.assertTrue(items[0]["path"].endswith("a.vst"))

    def test_ascii_guard_includes_parent_path_before_writing(self):
        target = self.item()
        source = self.item("a.vst", edges=(("A", "B"),))
        with self.assertRaisesRegex(ValueError, "ASCII_PATH_REQUIRED"):
            links.write_result(target, self.plan(target, [source]), self.root / "\u4e2d\u6587")

    def test_offline_analyze_default_is_read_only(self):
        fixture(self.root / "subject.vsk")
        fixture(self.root / "template.vst", edges=(("A", "B"),))
        before = sorted(p.name for p in self.root.iterdir())
        result = links.analyze(self.root / "subject.vsk", roots=[self.root])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(before, sorted(p.name for p in self.root.iterdir()))

    def test_nexus_static_marker_not_in_trajectory_list_is_valid(self):
        fixture(self.root / "Subject.vsk")
        fixture(self.root / "template.vst", edges=(("A", "B"),))
        class ReadOnlyNexus:
            def GetTrialName(inner): return str(self.root), "Trial"
            def GetSubjectInfo(inner): return ["Subject"], ["pyCGM2-FullBody_CGM23"], [True]
            def GetMarkerNames(inner, subject): return ["A", "B"]
            def GetSegmentNames(inner, subject): return ["Body"]
            def GetSegmentDetails(inner, subject, segment): return "", [], ["A", "B", "C"]
        report = links.prepare_nexus(ReadOnlyNexus(), roots=[self.root], output=self.root / "output")
        self.assertEqual(report["status"], "vsk_copy_verified")
        self.assertFalse(report["applied_in_nexus"])

    def test_nexus_unsaved_marker_edits_refuse_preparation(self):
        fixture(self.root / "Subject.vsk")
        class ReadOnlyNexus:
            def GetTrialName(inner): return str(self.root), "Trial"
            def GetSubjectInfo(inner): return ["Subject"], ["CGM2.3"], [True]
            def GetMarkerNames(inner, subject): return ["A", "B", "C", "NewMarker"]
            def GetSegmentNames(inner, subject): return []
        with self.assertRaisesRegex(ValueError, "SAVED_VSK_MARKERS_DIFFER_FROM_NEXUS"):
            links.prepare_nexus(ReadOnlyNexus(), roots=[], output=self.root / "output")
        self.assertFalse((self.root / "output").exists())

    def test_vst_is_not_accepted_as_a_calibrated_target(self):
        path = fixture(self.root / "template.vst", edges=(("A", "B"),))
        with self.assertRaisesRegex(ValueError, "calibrated VSK"):
            links.analyze(path, roots=[])

    def test_unicode_point_labels_round_trip_in_utf8_without_renaming(self):
        names = ("A", "B", "\u5b9e\u9a8c\u70b9")
        target = self.item(names=names)
        source = self.item("model.vst", names=names, edges=(("A", names[2]),))
        report = self.plan(target, [source])
        result = links.write_result(target, report, self.root / "output")
        saved = links.definition(result["output_vsk"])
        self.assertEqual(saved["markers"], sorted(names))
        self.assertEqual(saved["sticks"][0].get("MARKER2"), names[2])

    def test_alternative_declared_encoding_is_refused_without_conversion(self):
        target = self.item()
        path = Path(target["path"])
        path.write_bytes(target["raw"].replace(b'encoding="UTF-8"', b'encoding="ISO-8859-1"'))
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            links.definition(path)


if __name__ == "__main__":
    unittest.main()
