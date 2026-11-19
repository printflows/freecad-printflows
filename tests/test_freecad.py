# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows.
"""Tests that run inside FreeCAD's console (FreeCADCmd): export, selection, consent, send.

Run through tests/run_all.py, which starts the mock server and sets PRINTFLOWS_BASE_URL for
the FreeCAD process only. Fixtures are built in code; no model files are read.
"""

import hashlib
import json
import os
import re
import struct
import tempfile
import unittest
import urllib.request
import zipfile

import _paths  # noqa: F401
import FreeCAD

from freecad.PrintFlows import config, exporter, job, settings

MOCK = os.environ.get("PRINTFLOWS_BASE_URL", "")


def mock_state() -> dict:
    with urllib.request.urlopen(MOCK + "/__mock__/state", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_fixtures():
    """A Part box, a PartDesign body with a fillet, a circle (no solid), an App::Part with a box."""
    doc = FreeCAD.newDocument("PrintFlowsFixtures")
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 20, 10, 5
    box.Placement.Base = FreeCAD.Vector(5, 6, 7)

    body = doc.addObject("PartDesign::Body", "Body")
    block = body.newObject("PartDesign::AdditiveBox", "Block")
    block.Length, block.Width, block.Height = 30, 20, 10
    doc.recompute()
    fillet = body.newObject("PartDesign::Fillet", "Fillet")
    fillet.Base = (block, ["Edge1", "Edge3", "Edge5", "Edge7"])
    fillet.Radius = 3
    body.Placement.Base = FreeCAD.Vector(100, 0, 0)

    circle = doc.addObject("Part::Circle", "Circle")
    circle.Radius = 4

    assy = doc.addObject("App::Part", "Assembly")
    inner = doc.addObject("Part::Cylinder", "Pin")
    inner.Radius, inner.Height = 2, 12
    assy.addObject(inner)
    assy.Placement.Base = FreeCAD.Vector(0, 50, 0)
    doc.recompute()
    return doc


def model_bbox_3mf(path):
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        model = zf.read("3D/3dmodel.model").decode("utf-8")
    xs, ys, zs = [], [], []
    for x, y, z in re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', model):
        xs.append(float(x))
        ys.append(float(y))
        zs.append(float(z))
    return names, model, (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def stl_bbox(path):
    with open(path, "rb") as fh:
        data = fh.read()
    (count,) = struct.unpack_from("<I", data, 80)
    pts = []
    for i in range(count):
        values = struct.unpack_from("<12f", data, 84 + 50 * i)
        pts.extend([values[3:6], values[6:9], values[9:12]])
    xs, ys, zs = zip(*pts)
    return count, len(data), (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = build_fixtures()
        cls.o = {
            name: cls.doc.getObject(name)
            for name in ("Box", "Body", "Fillet", "Circle", "Assembly", "Pin")
        }

    @classmethod
    def tearDownClass(cls):
        FreeCAD.closeDocument(cls.doc.Name)

    def assertBox(self, actual, expected, tol=0.01):
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(a, e, delta=tol)


class A_ConsentBlocks(Base):
    """Runs first, against a fresh mock: without consent nothing reaches it."""

    def test_no_consent_no_request(self):
        if not MOCK:
            self.skipTest("PRINTFLOWS_BASE_URL not set (run tests/run_all.py)")
        settings.set_consent(False)
        self.assertFalse(settings.has_consent(MOCK))
        parts, _ = exporter.from_selection([self.o["Box"]])
        folder, path = exporter.write_to_temp(exporter.mesh_parts(parts), "3mf", "Box")
        before = len(mock_state()["log"])
        events = []
        upload = job.UploadJob(
            path,
            "en",
            events.append,
            consent=lambda: settings.has_consent(MOCK),
            temp_dir=folder,
        )
        upload.start()
        upload.join(30)
        opened = []
        action = job.finish(events[-1], upload, lambda url: opened.append(url) or True)
        state = mock_state()
        self.assertEqual(before, 0)
        self.assertEqual(len(state["log"]), 0, state["log"])
        self.assertEqual(state["handoffs"], {})
        self.assertEqual(events[-1]["code"], "consent_required")
        self.assertEqual(action, "error")
        self.assertEqual(opened, [])
        self.assertFalse(os.path.exists(folder))

    def test_param_path(self):
        """Real FreeCAD parameters: one entry per server under Mod/PrintFlows/Consent."""
        base = "http://127.0.0.1:49449"
        settings.set_consent(True, base)
        group = FreeCAD.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/PrintFlows/Consent"
        )
        self.assertEqual(group.GetInt(base, 0), config.CONSENT_VERSION)
        self.assertFalse(settings.has_consent("https://printflows.org"))
        settings.set_consent(False)
        self.assertEqual(group.GetInt(base, 0), 0)
        self.assertFalse(settings.has_consent(base))

    def test_round1_flag_is_not_consent(self):
        top = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/PrintFlows")
        top.SetInt("ConsentVersion", config.CONSENT_VERSION)  # what round 1 stored
        try:
            self.assertFalse(settings.has_consent("https://printflows.org"))
            settings.set_consent(False)  # withdrawing removes it too
            self.assertEqual(top.GetInt("ConsentVersion", 0), 0)
        finally:
            top.RemInt("ConsentVersion")


class Selection(Base):
    def test_visible_solids_counts_each_once(self):
        parts = exporter.visible_solids(self.doc)
        self.assertEqual(sorted(p.label for p in parts), ["Body", "Box", "Pin"])

    def test_selected_part_and_non_solid(self):
        parts, skipped = exporter.from_selection([self.o["Box"], self.o["Circle"]])
        self.assertEqual([p.label for p in parts], ["Box"])
        self.assertEqual(skipped, ["Circle"])

    def test_only_non_solid(self):
        parts, skipped = exporter.from_selection([self.o["Circle"]])
        self.assertEqual(parts, [])
        self.assertEqual(skipped, ["Circle"])

    def test_container_sends_its_solids_in_place(self):
        parts, skipped = exporter.from_selection([self.o["Assembly"]])
        self.assertEqual([p.label for p in parts], ["Pin"])
        self.assertEqual(skipped, [])
        self.assertBox(exporter.bounding_box(parts), (4, 4, 12))
        self.assertAlmostEqual(parts[0].shape.BoundBox.YMin, 48, delta=0.01)

    def test_duplicate_selection_once(self):
        parts, _ = exporter.from_selection([self.o["Box"], self.o["Box"]])
        self.assertEqual(len(parts), 1)

    def test_size_in_mm(self):
        parts, _ = exporter.from_selection([self.o["Box"], self.o["Body"]])
        size = exporter.bounding_box(parts)
        self.assertBox(size, (125, 20, 12))
        self.assertEqual(exporter.format_size((20.0, 10.04, 5.0)), ("20", "10", "5"))


class Export(Base):
    def test_3mf_box_in_mm_as_built(self):
        parts, _ = exporter.from_selection([self.o["Box"]])
        meshes = exporter.mesh_parts(parts)
        self.assertEqual(exporter.triangle_count(meshes), 12)
        folder, path = exporter.write_to_temp(meshes, "3mf", "Box")
        names, model, bbox = model_bbox_3mf(path)
        self.assertIn("[Content_Types].xml", names)
        self.assertIn("3D/3dmodel.model", names)
        self.assertIn('unit="millimeter"', model)
        self.assertEqual(model.count("<vertex "), 8)
        self.assertEqual(model.count("<triangle "), 12)
        self.assertBox(bbox, (5, 6, 7, 25, 16, 12))
        self.assertEqual(path, os.path.join(folder, "Box.3mf"))
        self.assertNotIn(
            "PrintFlowsExport", FreeCAD.listDocuments()
        )  # temp document closed

    def test_3mf_body_with_fillet(self):
        parts, _ = exporter.from_selection([self.o["Body"]])
        meshes = exporter.mesh_parts(parts, "medium")
        triangles = exporter.triangle_count(meshes)
        self.assertGreater(triangles, 12)
        _, path = exporter.write_to_temp(meshes, "3mf", "Body")
        names, model, bbox = model_bbox_3mf(path)
        self.assertEqual(model.count("<triangle "), triangles)
        self.assertBox(bbox, (100, 0, 0, 130, 20, 10), tol=0.06)

    def test_3mf_keeps_parts_separate(self):
        parts, _ = exporter.from_selection([self.o["Box"], self.o["Body"]])
        _, path = exporter.write_to_temp(exporter.mesh_parts(parts), "3mf", "Two")
        _, model, _ = model_bbox_3mf(path)
        self.assertEqual(model.count('type="model"'), 2)

    def test_stl_binary_in_mm(self):
        parts, _ = exporter.from_selection([self.o["Box"], self.o["Body"]])
        meshes = exporter.mesh_parts(parts)
        _, path = exporter.write_to_temp(meshes, "stl", "Both")
        count, size, bbox = stl_bbox(path)
        self.assertEqual(count, exporter.triangle_count(meshes))
        self.assertEqual(size, 84 + 50 * count)
        self.assertBox(bbox, (5, 0, 0, 130, 20, 12), tol=0.06)
        self.assertEqual(job.ph.check_file(path), size)

    def test_presets_change_triangle_count(self):
        parts, _ = exporter.from_selection([self.o["Body"]])
        counts = {
            q: exporter.triangle_count(exporter.mesh_parts(parts, q))
            for q in exporter.QUALITY_ORDER
        }
        print("\n    triangles per preset (filleted body):", counts)
        self.assertLess(counts["low"], counts["medium"])
        self.assertLess(counts["medium"], counts["high"])

    def test_file_name_is_safe(self):
        self.assertEqual(exporter.safe_name('a/b:c*?"<>|d'), "a b c d")
        self.assertEqual(exporter.safe_name(""), "design")
        self.assertEqual(len(exporter.safe_name("x" * 300)), 100)

    def test_export_error_removes_temp_folder(self):
        before = set(os.listdir(tempfile.gettempdir()))
        with self.assertRaises(ValueError):
            exporter.write_to_temp([], "obj", "x")
        leftover = [
            n
            for n in set(os.listdir(tempfile.gettempdir())) - before
            if n.startswith("PrintFlows-")
        ]
        self.assertEqual(leftover, [])


class Links(unittest.TestCase):
    """App::Link and friends: what a link points at is still sent, in the right place."""

    @classmethod
    def setUpClass(cls):
        doc = FreeCAD.newDocument("PrintFlowsLinks")
        body = doc.addObject("PartDesign::Body", "Body")
        block = body.newObject("PartDesign::AdditiveBox", "Block")
        block.Length, block.Width, block.Height = 10, 10, 10
        link_body = doc.addObject("App::Link", "LinkBody")
        link_body.LinkedObject = body
        link_body.Placement.Base = FreeCAD.Vector(0, 100, 0)

        box = doc.addObject("Part::Box", "Box")
        box.Placement.Base = FreeCAD.Vector(200, 0, 0)
        link_box = doc.addObject("App::Link", "LinkBox")
        link_box.LinkedObject = box
        link_box.Placement.Base = FreeCAD.Vector(300, 0, 0)

        # A link group owns its members: they are sent once, as the group, at the
        # group's placement (the member's own placement does not include it).
        cyl = doc.addObject("Part::Cylinder", "Cyl")
        cyl.Radius, cyl.Height = 2, 10
        group = doc.addObject("App::LinkGroup", "Grp")
        group.setLink([cyl])
        group.Placement.Base = FreeCAD.Vector(0, 0, 50)

        # A link array: its LinkElements are part of the array, sent once with it.
        base = doc.addObject("Part::Box", "ArrBase")
        base.Placement.Base = FreeCAD.Vector(0, 300, 0)
        array = doc.addObject("App::Link", "Arr")
        array.LinkedObject = base
        array.Placement.Base = FreeCAD.Vector(0, 400, 0)
        array.ElementCount = 2
        doc.recompute()
        cls.doc = doc

    @classmethod
    def tearDownClass(cls):
        FreeCAD.closeDocument(cls.doc.Name)

    def boxes(self, parts):
        return {
            p.label: tuple(
                round(v, 3)
                for v in (
                    p.shape.BoundBox.XMin,
                    p.shape.BoundBox.YMin,
                    p.shape.BoundBox.ZMin,
                    p.shape.BoundBox.XMax,
                    p.shape.BoundBox.YMax,
                    p.shape.BoundBox.ZMax,
                )
            )
            for p in parts
        }

    def test_visible_keeps_what_links_point_at(self):
        parts = exporter.visible_solids(self.doc)
        self.assertEqual(
            sorted(p.label for p in parts),
            ["Arr", "ArrBase", "Body", "Box", "Grp", "LinkBody", "LinkBox"],
        )

    def test_link_placements(self):
        boxes = self.boxes(exporter.visible_solids(self.doc))
        self.assertEqual(boxes["Body"], (0, 0, 0, 10, 10, 10))
        self.assertEqual(boxes["LinkBody"], (0, 100, 0, 10, 110, 10))
        self.assertEqual(boxes["Box"], (200, 0, 0, 210, 10, 10))
        self.assertEqual(boxes["LinkBox"], (300, 0, 0, 310, 10, 10))
        self.assertEqual(boxes["Grp"], (-2, -2, 50, 2, 2, 60))
        self.assertEqual(boxes["ArrBase"], (0, 300, 0, 10, 310, 10))
        self.assertEqual(boxes["Arr"][1], 400)

    def test_linked_meshes_land_in_place(self):
        parts = exporter.visible_solids(self.doc)
        meshes = exporter.mesh_parts(parts)
        _, path = exporter.write_to_temp(meshes, "stl", "Links")
        count, _, bbox = stl_bbox(path)
        self.assertEqual(count, exporter.triangle_count(meshes))
        self.assertEqual(tuple(round(v, 3) for v in bbox), (-2, -2, 0, 310, 410, 60))
        os.remove(path)
        os.rmdir(os.path.dirname(path))

    def test_selecting_a_body_and_its_link_sends_both(self):
        parts, skipped = exporter.from_selection(
            [self.doc.getObject("Body"), self.doc.getObject("LinkBody")]
        )
        self.assertEqual([p.label for p in parts], ["Body", "LinkBody"])
        self.assertEqual(skipped, [])


class Shapes(unittest.TestCase):
    def setUp(self):
        self.doc = FreeCAD.newDocument("PrintFlowsShapes")

    def tearDown(self):
        FreeCAD.closeDocument(self.doc.Name)

    def test_compound_meshes_only_its_solids(self):
        import Part

        mixed = self.doc.addObject("Part::Feature", "Mixed")
        mixed.Shape = Part.makeCompound(
            [
                Part.makeBox(10, 10, 10),
                Part.makePlane(5, 5, FreeCAD.Vector(30, 0, 0)),  # a loose face
            ]
        )
        mixed.Placement.Base = FreeCAD.Vector(0, 0, 100)
        self.doc.recompute()
        parts, skipped = exporter.from_selection([mixed])
        self.assertEqual(skipped, [])
        meshes = exporter.mesh_parts(parts)
        self.assertEqual(exporter.triangle_count(meshes), 12)
        b = meshes[0][1].BoundBox
        self.assertEqual(
            tuple(
                round(v, 3) for v in (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)
            ),
            (0, 0, 100, 10, 10, 110),
        )

    def test_3mf_objects_carry_the_part_names(self):
        import xml.etree.ElementTree as ET

        import Part

        labels = ["Small & <1>", 'Big "2"']
        parts = [
            exporter.Solid(labels[0], Part.makeBox(5, 5, 5)),
            exporter.Solid(labels[1], Part.makeBox(20, 20, 20)),
        ]
        folder, path = exporter.write_to_temp(
            exporter.mesh_parts(parts), "3mf", "Named"
        )
        with zipfile.ZipFile(path) as zf:
            self.assertIsNone(zf.testzip())
            root = ET.fromstring(zf.read("3D/3dmodel.model"))
        objects = [e for e in root.iter() if e.tag.endswith("}object")]
        self.assertEqual([o.get("name") for o in objects], labels)
        sizes = [
            max(float(v.get("x")) for v in o.iter() if v.tag.endswith("}vertex"))
            for o in objects
        ]
        self.assertEqual(sizes, [5.0, 20.0])
        os.remove(path)
        os.rmdir(folder)


class Send(Base):
    """The core send path against the mock: export, POST, PUT, then open (recorded)."""

    def _send(self, objects, fmt, locale):
        settings.set_consent(True, MOCK)
        parts, _ = exporter.from_selection(objects)
        folder, path = exporter.write_to_temp(
            exporter.mesh_parts(parts), fmt, parts[0].label
        )
        with open(path, "rb") as fh:
            sent = fh.read()
        events = []
        upload = job.UploadJob(
            path,
            locale,
            events.append,
            consent=lambda: settings.has_consent(MOCK),
            temp_dir=folder,
        )
        upload.start()
        upload.join(60)
        opened = []
        action = job.finish(events[-1], upload, lambda url: opened.append(url) or True)
        return sent, os.path.basename(path), events, action, opened, folder

    def setUp(self):
        if not MOCK:
            self.skipTest("PRINTFLOWS_BASE_URL not set (run tests/run_all.py)")

    def tearDown(self):
        settings.set_consent(False)

    def _check(self, sent, name, events, action, opened, folder, locale):
        self.assertEqual(events[-1]["kind"], "done", events)
        self.assertEqual(action, "opened")
        self.assertEqual(opened, [events[-1]["openUrl"]])
        start = {"en": "/en/start?handoff=", "fr": "/fr/commencer?handoff="}[locale]
        self.assertTrue(opened[0].startswith(MOCK + start), opened[0])
        self.assertFalse(os.path.exists(folder))
        state = mock_state()
        digest = hashlib.sha256(sent).hexdigest()
        records = [r for r in state["handoffs"].values() if r.get("sha256") == digest]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["fileName"], name)
        self.assertEqual(record["locale"], locale)
        with open(record["storedPath"], "rb") as fh:
            self.assertEqual(fh.read(), sent)  # the exact bytes
        posts = [
            e
            for e in state["log"]
            if e["route"] == "POST /api/handoff"
            and (e.get("body") or {}).get("fileName") == name
        ]
        self.assertTrue(posts)
        self.assertEqual(posts[-1]["body"]["source"], "freecad")
        self.assertEqual(posts[-1]["body"]["sourceVersion"], config.VERSION)
        self.assertEqual(posts[-1]["userAgent"], "PrintFlows-freecad/" + config.VERSION)
        with urllib.request.urlopen(opened[0], timeout=5) as resp:
            self.assertIn(b"Uploaded: True", resp.read())

    def test_send_3mf_en(self):
        result = self._send([self.o["Body"]], "3mf", "en")
        self._check(*result, "en")

    def test_send_stl_fr(self):
        result = self._send([self.o["Box"]], "stl", "fr")
        self._check(*result, "fr")
        self.assertTrue(result[1].endswith(".stl"))

    def test_cancel_means_no_browser(self):
        settings.set_consent(True, MOCK)
        parts, _ = exporter.from_selection([self.o["Box"]])
        folder, path = exporter.write_to_temp(
            exporter.mesh_parts(parts), "3mf", "Cancelled"
        )
        events = []
        upload = job.UploadJob(
            path,
            "en",
            events.append,
            consent=lambda: settings.has_consent(MOCK),
            temp_dir=folder,
        )
        upload.cancel()
        upload.start()
        upload.join(30)
        opened = []
        self.assertEqual(
            job.finish(events[-1], upload, lambda url: opened.append(url) or True),
            "cancelled",
        )
        self.assertEqual(opened, [])
        self.assertFalse(os.path.exists(folder))
        self.assertFalse(
            [
                r
                for r in mock_state()["handoffs"].values()
                if r["fileName"] == "Cancelled.3mf"
            ]
        )


if __name__ == "__main__":
    unittest.main()
