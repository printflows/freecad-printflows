# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows.
"""Run every FreeCAD test hidden, against the local mock server only.

    python tests/run_all.py --freecad <FreeCAD>/bin --user <scratch folder> [--logs DIR] [--no-gui]

1. Copies the add-on into <user>/data/Mod/PrintFlows (FreeCAD's user Mod folder for this run).
2. Starts the mock server hidden and sets PRINTFLOWS_BASE_URL for the FreeCAD child only.
   The mock is tools/mock_server.py: inside the add-on's folder in its own repo, or in the
   PrintFlows CAD repo next to it.
3. FreeCADCmd runs tests/fc_runner.py (test_freecad + test_logic).
4. FreeCAD.exe runs tests/gui_smoke.py with QT_QPA_PLATFORM=offscreen and a hidden window flag,
   against a fresh mock.
5. FreeCAD's bundled python runs test_logic, and the shared client's own tests (shared/tests)
   when the add-on sits in the PrintFlows CAD repo. The add-on's own repo has no shared/
   folder; the runner then says those tests were skipped and why.

Every FreeCAD run sets FREECAD_USER_HOME / _DATA / _TEMP under <user>, so nothing goes to
%APPDATA%. Each mock is stopped by its own PID. Exit 0 when everything passed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.dirname(HERE)
REPO = os.path.dirname(ADDON)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SHARED_SKIPPED = (
    "skipped: no shared/tests next to the add-on. They are the shared client's own tests "
    "and run in the PrintFlows CAD repo; this copy of the client is checked against its "
    "pinned sha256 by build.py --check instead."
)


def default_mock() -> str:
    """tools/mock_server.py: in the add-on's own repo first, then in the PrintFlows CAD repo."""
    candidates = (
        os.path.join(ADDON, "tools", "mock_server.py"),
        os.path.join(REPO, "tools", "mock_server.py"),
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]


def shared_tests_dir() -> str | None:
    """shared/tests when the add-on sits in the PrintFlows CAD repo, else None."""
    path = os.path.join(REPO, "shared", "tests")
    return path if os.path.isdir(path) else None


def hidden_startupinfo():
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0  # SW_HIDE
    return info


def install(user: str) -> str:
    target = os.path.join(user, "data", "Mod", "PrintFlows")
    os.makedirs(target, exist_ok=True)
    for name in ("package.xml", "LICENSE", "README.md"):
        if os.path.exists(os.path.join(ADDON, name)):
            shutil.copyfile(os.path.join(ADDON, name), os.path.join(target, name))
    shutil.copytree(
        os.path.join(ADDON, "freecad"),
        os.path.join(target, "freecad"),
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return target


def freecad_env(user: str, **extra) -> dict:
    env = dict(os.environ)
    env.pop("PRINTFLOWS_BASE_URL", None)
    env.update(
        FREECAD_USER_HOME=os.path.join(user, "home"),
        FREECAD_USER_DATA=os.path.join(user, "data"),
        FREECAD_USER_TEMP=os.path.join(user, "temp"),
        PYTHONDONTWRITEBYTECODE="1",
        TEMP=os.path.join(user, "temp"),
        TMP=os.path.join(user, "temp"),
    )
    for key in ("home", "data", "temp"):
        os.makedirs(os.path.join(user, key), exist_ok=True)
    env.update(extra)
    return env


class Mock:
    def __init__(self, python: str, script: str, store: str, log_path: str):
        os.makedirs(store, exist_ok=True)
        self.log = open(log_path, "wb")
        self.proc = subprocess.Popen(
            [python, "-I", script, "--store", store],
            stdout=subprocess.PIPE,
            stderr=self.log,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
        self.base_url = self.proc.stdout.readline().decode("ascii").strip()
        if not self.base_url.startswith("http://127.0.0.1:"):
            self.stop()
            raise SystemExit("mock did not start: %r" % self.base_url)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()  # TerminateProcess on this PID only
            self.proc.wait(10)
        self.log.close()


def run(cmd, env, log_path, timeout, cwd=None, startupinfo=None) -> int:
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW,
            startupinfo=startupinfo,
        )
        try:
            return proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.kill()  # this PID only
            proc.wait(10)
            return -999


def forces_windows_platform(exe: str) -> bool:
    """True when this FreeCAD.exe sets QT_QPA_PLATFORM=windows itself (FreeCAD 1.0.x does:
    "QT_QPA_PLATFORM=windows:darkmode=1" is in its FreeCAD.exe). It then ignores our
    offscreen setting and its windows would show on the desktop, so the GUI smoke is skipped.
    """
    try:
        with open(exe, "rb") as fh:
            return b"QT_QPA_PLATFORM=windows" in fh.read()
    except OSError:
        return False


def appdata_snapshot() -> dict:
    """mtime of every file under %APPDATA%\\FreeCAD and %LOCALAPPDATA%\\FreeCAD (read only)."""
    out = {}
    for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        root = os.path.join(base, "FreeCAD")
        for folder, _dirs, files in os.walk(root):
            for name in files:
                full = os.path.join(folder, name)
                try:
                    out[full] = os.path.getmtime(full)
                except OSError:
                    pass
    return out


def tail(path: str, lines: int = 6) -> str:
    with open(path, "rb") as fh:
        return "\n".join(
            fh.read().decode("utf-8", "replace").strip().splitlines()[-lines:]
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freecad", required=True, help="FreeCAD's bin folder")
    parser.add_argument(
        "--user", required=True, help="scratch folder for FreeCAD's user files"
    )
    parser.add_argument("--logs", default=None)
    parser.add_argument("--mock", default=default_mock())
    parser.add_argument("--no-gui", action="store_true")
    args = parser.parse_args(argv)
    user = os.path.abspath(args.user)
    logs = os.path.abspath(args.logs or os.path.join(user, "logs"))
    os.makedirs(logs, exist_ok=True)
    bin_dir = os.path.abspath(args.freecad)
    fc_python = os.path.join(bin_dir, "python.exe" if os.name == "nt" else "python")
    results = {}
    before = appdata_snapshot()

    print("installed:", install(user))

    # 1. FreeCADCmd: export, selection, consent, send.
    mock = Mock(
        sys.executable,
        args.mock,
        os.path.join(user, "mock-store", "cmd"),
        os.path.join(logs, "mock-cmd.log"),
    )
    print("mock (cmd): %s pid %d" % (mock.base_url, mock.proc.pid))
    try:
        result_path = os.path.join(logs, "fc-result.json")
        env = freecad_env(
            user, PRINTFLOWS_BASE_URL=mock.base_url, PF_RESULT=result_path
        )
        start = time.time()
        code = run(
            [
                os.path.join(bin_dir, "freecadcmd.exe"),
                os.path.join(HERE, "fc_runner.py"),
            ],
            env,
            os.path.join(logs, "freecadcmd.log"),
            600,
        )
        with open(result_path, encoding="utf-8") as fh:
            summary = json.load(fh)
        results["freecadcmd"] = dict(
            exit=code, seconds=round(time.time() - start, 1), **summary
        )
    finally:
        mock.stop()
        print("mock (cmd) stopped, exit", mock.proc.returncode)

    # 2. GUI smoke, offscreen and hidden.
    gui_exe = os.path.join(bin_dir, "freecad.exe")
    if not args.no_gui and forces_windows_platform(gui_exe):
        args.no_gui = True
        results["gui_skipped"] = (
            "%s forces QT_QPA_PLATFORM=windows, so it cannot run offscreen; "
            "GUI smoke skipped so no window opens" % gui_exe
        )
        print("GUI smoke skipped:", results["gui_skipped"])
    if not args.no_gui:
        mock = Mock(
            sys.executable,
            args.mock,
            os.path.join(user, "mock-store", "gui"),
            os.path.join(logs, "mock-gui.log"),
        )
        print("mock (gui): %s pid %d" % (mock.base_url, mock.proc.pid))
        try:
            gui_path = os.path.join(logs, "gui-result.json")
            if os.path.exists(gui_path):
                os.replace(gui_path, gui_path + ".prev")
            env = freecad_env(
                user,
                PRINTFLOWS_BASE_URL=mock.base_url,
                PF_GUI_RESULT=gui_path,
                QT_QPA_PLATFORM="offscreen",
            )
            code = run(
                [
                    os.path.join(bin_dir, "freecad.exe"),
                    os.path.join(HERE, "gui_smoke.py"),
                ],
                env,
                os.path.join(logs, "freecad-gui.log"),
                300,
                startupinfo=hidden_startupinfo(),
            )
            gui = {"exit": code}
            if os.path.exists(gui_path):
                with open(gui_path, encoding="utf-8") as fh:
                    gui.update(json.load(fh))
            results["gui"] = gui
        finally:
            mock.stop()
            print("mock (gui) stopped, exit", mock.proc.returncode)

    # 3. FreeCAD's bundled python: the shared client's tests (in the CAD repo) and the logic tests.
    env = freecad_env(user)
    shared_tests = shared_tests_dir()
    if shared_tests is None:
        results["shared_tests"] = {"exit": 0, "skipped": True, "tail": SHARED_SKIPPED}
    for name, cmd, cwd in (
        (
            "shared_tests",
            [fc_python, "-m", "unittest", "discover", "-s", shared_tests or ""],
            REPO,
        ),
        (
            "logic_tests",
            [
                fc_python,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_logic.py",
            ],
            ADDON,
        ),
    ):
        if name in results:  # skipped above
            continue
        log = os.path.join(logs, name + ".log")
        code = run(cmd, env, log, 600, cwd=cwd)
        results[name] = {"exit": code, "tail": tail(log, 3)}

    after = appdata_snapshot()
    changed = sorted(p for p, t in after.items() if before.get(p) != t)
    results["appdata_changed"] = changed

    with open(os.path.join(logs, "run_all.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    ok = (
        results["freecadcmd"]["ok"]
        and results["freecadcmd"]["exit"] == 0
        and (
            args.no_gui
            or (results["gui"]["exit"] == 0 and not results["gui"].get("error"))
        )
        and results["shared_tests"]["exit"] == 0
        and results["logic_tests"]["exit"] == 0
    )
    fc = results["freecadcmd"]
    print(
        "FreeCADCmd: ran %d, failures %d, errors %d, skipped %d (exit %s)"
        % (fc["ran"], fc["failures"], fc["errors"], fc["skipped"], fc["exit"])
    )
    for problem in fc["problems"]:
        print(problem)
    if "gui" in results:
        gui = results["gui"]
        checks = gui.get("checks", {})
        print(
            "GUI smoke: %d/%d checks passed (exit %s)"
            % (sum(checks.values()), len(checks), gui["exit"])
        )
        for name, passed in checks.items():
            if not passed:
                print("  FAILED:", name)
        if gui.get("error"):
            print(gui["error"])
        for name, trace in (gui.get("errors") or {}).items():
            print("  section %s raised: %s" % (name, trace.strip().splitlines()[-1]))
    for name in ("shared_tests", "logic_tests"):
        if results[name].get("skipped"):
            print("%s %s" % (name, results[name]["tail"]))
            continue
        print(
            "%s (FreeCAD python): exit %s | %s"
            % (name, results[name]["exit"], results[name]["tail"].replace("\n", " | "))
        )
    print("%APPDATA%/%LOCALAPPDATA% FreeCAD files changed:", len(changed))
    for path in changed:
        print("  ", path)
    print("ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
