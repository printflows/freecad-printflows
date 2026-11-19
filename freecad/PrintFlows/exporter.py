# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows. See LICENSE at the top of the add-on.
"""Pick the solids to send, mesh them and write 3MF or binary STL. FreeCAD App only (no GUI),
so it runs the same in FreeCADCmd and in the tests. The one exception: when the GUI is up,
the 3MF writer puts the person's selection back (see ``_gui_selection``).

FreeCAD models are in millimetres, and both 3MF (unit="millimeter") and STL are written in
millimetres, so nothing is scaled.
"""

from __future__ import annotations

import os
import re

# (LinearDeflection mm, AngularDeflection rad) for MeshPart.meshFromShape, Relative=False.
# The list reads Low, Medium, High; Medium is the default: it keeps ordinary parts well under
# the site's 1.5 million triangle preview limit.
QUALITY_PRESETS = {
    "low": (0.2, 0.5236),  # 30 degrees
    "medium": (0.05, 0.2618),  # 15 degrees
    "high": (0.01, 0.0873),  # 5 degrees
}
QUALITY_ORDER = ("low", "medium", "high")
DEFAULT_QUALITY = "medium"
FORMATS = ("3mf", "stl")

# The site cannot preview parts above this many triangles (MAX_HERO_TRIANGLES on printflows.org).
MAX_PREVIEW_TRIANGLES = 1_500_000

# Grouping containers whose children are exported on their own. Any other parent (a Body, a
# boolean, a Pad...) means the child is an input to something else, so it is skipped.
_GROUP_TYPES = ("App::DocumentObjectGroup", "App::Part")

# Link objects. A link shows the object it points at (LinkedObject) somewhere else; it does
# not consume it, so the original is still sent when it is visible. What a link owns (the
# LinkElements of a link array, the members of a LinkGroup) is part of the link's own shape,
# already at the link's placement, so those stay skipped.
_LINK_TYPES = ("App::Link", "App::LinkElement", "App::LinkGroup", "App::LinkPython")


class Solid:
    """One solid to send: a label for messages, and its shape in global coordinates."""

    __slots__ = ("label", "shape")

    def __init__(self, label: str, shape):
        self.label = label
        self.shape = shape


def _solid_shape(obj):
    """The object's solids in global coordinates, or None when it has none.

    Only solids are kept: loose faces or shells in a compound are not part of a printable
    solid and would otherwise be meshed and sent too.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull() or not shape.Solids:
        return None
    shape = shape.copy()
    try:
        shape.Placement = obj.getGlobalPlacement()
    except Exception:  # noqa: BLE001 - objects without a placement keep their own
        pass
    if shape.ShapeType != "Solid" and len(shape.Faces) != sum(
        len(solid.Faces) for solid in shape.Solids
    ):
        import Part

        # Sub-shapes carry the compound's placement, so the new compound stays in place.
        shape = Part.makeCompound(shape.Solids)
    return shape


def _is_container(obj) -> bool:
    return obj.TypeId in _GROUP_TYPES


def _is_link(obj) -> bool:
    try:
        return any(obj.isDerivedFrom(kind) for kind in _LINK_TYPES)
    except Exception:  # noqa: BLE001
        return obj.TypeId in _LINK_TYPES


def _links_to(parent, obj) -> bool:
    """True when ``parent`` is a link whose LinkedObject is ``obj``."""
    if not _is_link(parent):
        return False
    target = getattr(parent, "LinkedObject", None)
    if isinstance(target, tuple):  # (object, sub-element) form
        target = target[0] if target else None
    return target is not None and target == obj


def _is_input(obj) -> bool:
    """True when the object feeds another feature (a Pad in a Body, a Box in a Cut...).

    Containers and links pointing at the object don't count: a Body with a Link to it is
    still sent, and so is the Link.
    """
    return any(
        not _is_container(parent) and not _links_to(parent, obj)
        for parent in obj.InList
    )


def _visible(obj) -> bool:
    view = getattr(obj, "ViewObject", None)
    if view is not None:
        try:
            return bool(view.Visibility)
        except Exception:  # noqa: BLE001
            pass
    return bool(getattr(obj, "Visibility", True))


def visible_solids(doc) -> list:
    """Every visible solid in the document, each counted once (a Body, not its features)."""
    parts = []
    for obj in doc.Objects:
        if _is_container(obj) or _is_input(obj) or not _visible(obj):
            continue
        shape = _solid_shape(obj)
        if shape is not None:
            parts.append(Solid(obj.Label, shape))
    return parts


def from_selection(objects) -> tuple[list, list]:
    """Solids for the selected objects. Returns (parts, labels of objects with no solid).

    A selected App::Part or group sends its visible solids; anything else sends its own shape.
    """
    parts = []
    skipped = []
    seen = set()
    for obj in objects:
        key = (obj.Document.Name, obj.Name)
        if key in seen:
            continue
        seen.add(key)
        if _is_container(obj):
            found = False
            for child in obj.OutListRecursive:
                if (
                    _is_container(child)
                    or not _visible(child)
                    or (child.Document.Name, child.Name) in seen
                ):
                    continue
                if _is_input(child):
                    continue
                shape = _solid_shape(child)
                if shape is not None:
                    seen.add((child.Document.Name, child.Name))
                    parts.append(Solid(child.Label, shape))
                    found = True
            if not found:
                skipped.append(obj.Label)
            continue
        shape = _solid_shape(obj)
        if shape is None:
            skipped.append(obj.Label)
        else:
            parts.append(Solid(obj.Label, shape))
    return parts, skipped


def bounding_box(parts):
    """(x, y, z) size in mm of everything in ``parts``, or None when empty."""
    box = None
    for part in parts:
        b = part.shape.BoundBox
        if box is None:
            box = b
        else:
            box = box.united(b)
    if box is None:
        return None
    return (box.XLength, box.YLength, box.ZLength)


def format_size(size) -> tuple[str, str, str]:
    return tuple(("%.1f" % v).rstrip("0").rstrip(".") for v in size)


def mesh_parts(parts, quality: str = "medium"):
    """Mesh every part. Returns a list of (label, Mesh.Mesh)."""
    import MeshPart

    linear, angular = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["medium"])
    out = []
    for part in parts:
        mesh = MeshPart.meshFromShape(
            Shape=part.shape,
            LinearDeflection=linear,
            AngularDeflection=angular,
            Relative=False,
        )
        out.append((part.label, mesh))
    return out


def triangle_count(meshes) -> int:
    return sum(mesh.CountFacets for _, mesh in meshes)


def safe_name(name: str) -> str:
    """A file name stem the server accepts: no path characters, at most 100 characters."""
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name or "").strip(" .")
    return (stem or "design")[:100]


def write_to_temp(
    meshes, fmt: str, stem: str, prefix: str = "PrintFlows-"
) -> tuple[str, str]:
    """Write into a new tempfile.mkdtemp() folder. Returns (folder, path).

    The caller removes the folder when the upload ends; on an export error it is removed here.
    """
    import shutil
    import tempfile

    folder = tempfile.mkdtemp(prefix=prefix)
    try:
        return folder, write(meshes, fmt, folder, stem)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def write(meshes, fmt: str, folder: str, stem: str) -> str:
    """Write the meshes to ``folder/stem.<fmt>`` and return the path.

    3MF goes through Mesh.export with one Mesh::Feature per part (in a hidden, temporary
    document that is closed again), so each part stays a separate object. STL is binary.
    """
    import FreeCAD
    import Mesh

    if fmt not in FORMATS:
        raise ValueError("unknown format %r" % (fmt,))
    path = os.path.join(folder, "%s.%s" % (safe_name(stem), fmt))
    if fmt == "stl":
        merged = Mesh.Mesh()
        for _, mesh in meshes:
            merged.addMesh(mesh)
        merged.write(path, "STL")
        return path
    active = FreeCAD.ActiveDocument
    selection = _gui_selection()  # opening and closing the temp document clears it
    temp = FreeCAD.newDocument("PrintFlowsExport", hidden=True, temp=True)
    try:
        objects = []
        for label, mesh in meshes:
            feature = temp.addObject("Mesh::Feature", "Part")
            feature.Label = label
            feature.Mesh = mesh
            objects.append(feature)
        Mesh.export(objects, path)
    finally:
        FreeCAD.closeDocument(temp.Name)
        if active is not None and FreeCAD.ActiveDocument is not active:
            try:
                FreeCAD.setActiveDocument(active.Name)
            except Exception:  # noqa: BLE001
                pass
        _restore_gui_selection(selection)
    name_3mf_objects(path, [label for label, _ in meshes])
    return path


def _gui_selection():
    """The GUI selection as (document, object, sub-element) names; None without a GUI.

    The only GUI call in this module: FreeCAD clears the selection when the hidden export
    document comes and goes, and the person's selection must be the same after a send.
    """
    import FreeCAD

    if not getattr(FreeCAD, "GuiUp", False):
        return None
    try:
        import FreeCADGui

        entries = []
        for item in FreeCADGui.Selection.getSelectionEx("", 0):
            obj = item.Object
            subs = [s for s in (item.SubElementNames or ()) if s] or [""]
            entries.extend((obj.Document.Name, obj.Name, sub) for sub in subs)
        return entries
    except Exception:  # noqa: BLE001 - never fail an export over the selection
        return None


def _restore_gui_selection(entries) -> None:
    if entries is None or _gui_selection() == entries:
        return
    try:
        import FreeCADGui

        FreeCADGui.Selection.clearSelection()
        for doc_name, obj_name, sub in entries:
            FreeCADGui.Selection.addSelection(doc_name, obj_name, sub)
    except Exception:  # noqa: BLE001
        pass


_OBJECT_TAG = re.compile(r"<object(?=[\s>/])(?![^>]*\sname=)")


def name_3mf_objects(path: str, labels) -> bool:
    """Give each <object> in a 3MF the part's label as its ``name`` attribute.

    3MF Core Specification 1.4.0, Chapter 4 (Object Resources), <object> attributes: "name | xs:string |
    Name of object to improve readability." FreeCAD's 3MF writer leaves it out. The objects
    are written in the order of ``labels``; when the count differs nothing is changed.
    Returns True when the names were written.
    """
    import zipfile
    from xml.sax.saxutils import quoteattr

    model_name = "3D/3dmodel.model"
    with zipfile.ZipFile(path) as zf:
        entries = [(info, zf.read(info.filename)) for info in zf.infolist()]
    labels = list(labels)
    for index, (info, data) in enumerate(entries):
        if info.filename != model_name:
            continue
        text = data.decode("utf-8")
        if len(_OBJECT_TAG.findall(text)) != len(labels) or not labels:
            return False
        names = iter(labels)
        text = _OBJECT_TAG.sub(
            lambda _m: "<object name=%s" % quoteattr(next(names)), text
        )
        entries[index] = (info, text.encode("utf-8"))
        break
    else:
        return False
    rewritten = path + ".tmp"
    try:
        with zipfile.ZipFile(rewritten, "w") as zf:
            for info, data in entries:
                zf.writestr(info, data)
        os.replace(rewritten, path)
    finally:
        if os.path.exists(rewritten):
            os.remove(rewritten)
    return True


def _is_reparse_point(path: str) -> bool:
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return True
    return os.path.islink(path) or bool(
        attributes & 0x400
    )  # FILE_ATTRIBUTE_REPARSE_POINT


def sweep_stale_temp(
    folder=None, now=None, prefix="PrintFlows-", max_age=86400
) -> list:
    """Remove ``prefix*`` folders directly in ``folder`` (the temp folder) older than ``max_age``.

    They are export folders left behind when FreeCAD quit during an upload. Younger folders,
    files, links and junctions are never touched. Returns the folders removed.
    """
    import shutil
    import tempfile
    import time

    folder = folder or tempfile.gettempdir()
    now = time.time() if now is None else now
    removed = []
    try:
        names = os.listdir(folder)
    except OSError:
        return removed
    for name in sorted(names):
        if not name.startswith(prefix):
            continue
        path = os.path.join(folder, name)
        try:
            if _is_reparse_point(path) or not os.path.isdir(path):
                continue
            if now - os.path.getmtime(path) < max_age:
                continue
            shutil.rmtree(path)
            removed.append(path)
        except OSError:
            continue
    return removed
