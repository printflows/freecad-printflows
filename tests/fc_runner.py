# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows.
"""Run the test modules inside FreeCADCmd and write a JSON summary to $PF_RESULT.

FreeCADCmd.exe tests/fc_runner.py      (tests/run_all.py does this with the right env)
"""

import json
import os
import sys
import unittest

sys.dont_write_bytecode = (
    True  # FreeCAD's embedded Python ignores PYTHONDONTWRITEBYTECODE
)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _paths  # noqa: E402,F401


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in ("test_freecad", "test_logic"):
        suite.addTests(loader.loadTestsFromName(name))
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    summary = {
        "ran": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "ok": result.wasSuccessful(),
        "problems": [str(t) + "\n" + tb for t, tb in result.failures + result.errors],
        "skips": [str(t) + ": " + why for t, why in result.skipped],
    }
    out = os.environ.get("PF_RESULT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=1)
    sys.stdout.flush()
    return 0 if result.wasSuccessful() else 1


code = main()
os._exit(code)  # FreeCADCmd would otherwise drop into its interactive console
