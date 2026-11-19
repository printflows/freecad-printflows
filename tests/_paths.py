# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows.
"""Make ``freecad.PrintFlows`` importable from this checkout, in plain Python and in FreeCAD.

FreeCAD ships its own ``freecad`` package; namespace add-ons live inside it through
``freecad.__path__``. Plain Python has no ``freecad`` at all, so the checkout's ``freecad/``
folder becomes a PEP 420 namespace package.
"""

import os
import sys

ADDON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ADDON)  # the PrintFlows CAD repo, when the add-on sits in it


def setup() -> None:
    if ADDON not in sys.path:
        sys.path.insert(0, ADDON)
    try:
        import freecad
    except ImportError:
        return
    ours = os.path.join(ADDON, "freecad")
    paths = list(getattr(freecad, "__path__", []))
    if ours not in paths:
        try:
            freecad.__path__.insert(0, ours)
        except (
            AttributeError
        ):  # a _NamespacePath: rebuilt from sys.path, which has ADDON
            freecad.__path__.append(ours)


setup()
