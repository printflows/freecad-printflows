# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows.
"""Tests that need no FreeCAD: strings, language, consent flag, job events, build --check.

python -m unittest discover -s tests          (any Python 3.11+, FreeCAD's included)
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402,F401

from freecad.PrintFlows import config, job, settings, strings  # noqa: E402
from freecad.PrintFlows import printflows_handoff as ph  # noqa: E402

BUILD = os.path.join(_paths.ADDON, "build.py")


def python_exe() -> str:
    """A Python interpreter: inside FreeCADCmd sys.executable is FreeCADCmd itself."""
    exe = sys.executable
    if os.path.basename(exe).lower().startswith("python"):
        return exe
    candidate = os.path.join(
        os.path.dirname(exe), "python.exe" if os.name == "nt" else "python"
    )
    return candidate if os.path.exists(candidate) else exe


class FakeParams:
    """Stands in for FreeCAD.ParamGet(...) (Get/Set/RemInt, GetInts, Get/RemGroup)."""

    def __init__(self):
        self.ints = {}
        self.groups = {}

    def GetInt(self, name, default=0):  # noqa: N802
        return self.ints.get(name, default)

    def SetInt(self, name, value):  # noqa: N802
        self.ints[name] = value

    def RemInt(self, name):  # noqa: N802
        self.ints.pop(name, None)

    def GetInts(self):  # noqa: N802
        return list(self.ints)

    def GetGroup(self, name):  # noqa: N802
        return self.groups.setdefault(name, FakeParams())

    def RemGroup(self, name):  # noqa: N802
        self.groups.pop(name, None)


TEST_BASE = "http://127.0.0.1:49449"
PROD_BASE = "https://printflows.org"


class Strings(unittest.TestCase):
    def test_same_keys_in_both_languages(self):
        self.assertEqual(set(strings.STRINGS["en"]), set(strings.STRINGS["fr"]))

    def test_french_differs_and_formats(self):
        for key in strings.STRINGS["en"]:
            en = strings.text(
                key,
                "en",
                names="A",
                count="1",
                limit="2",
                detail="d",
                url="u",
                base="b",
                host="h",
                x=1,
                y=2,
                z=3,
            )
            fr = strings.text(
                key,
                "fr",
                names="A",
                count="1",
                limit="2",
                detail="d",
                url="u",
                base="b",
                host="h",
                x=1,
                y=2,
                z=3,
            )
            self.assertTrue(en and fr, key)
        self.assertEqual(strings.text("cmd_name", "en"), "Print with PrintFlows")
        self.assertEqual(strings.text("cmd_name", "fr"), "Imprimer avec PrintFlows")

    def test_privacy_matches_fusion(self):
        self.assertIn(
            "https://printflows.org/en/legal/privacy", strings.text("privacy", "en")
        )
        self.assertIn(
            "https://printflows.org/fr/legal/confidentialite",
            strings.text("privacy", "fr"),
        )
        fusion = os.path.join(
            _paths.REPO, "fusion", "PrintFlows", "pfaddin", "strings.py"
        )
        if not os.path.exists(fusion):
            self.skipTest("not inside the PrintFlows CAD repo")
        with open(fusion, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and node.targets[0].id in (
                "STRINGS",
                "PRIVACY_URLS",
            ):
                values[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(values["PRIVACY_URLS"], strings.PRIVACY_URLS)
        for loc in ("en", "fr"):
            for key in ("privacy", "test_base", "too_many_triangles", "browser_failed"):
                if key == "too_many_triangles":
                    continue  # ours says "design", not "STL": the count is known for 3MF too
                self.assertEqual(
                    values["STRINGS"][loc][key], strings.STRINGS[loc][key], (loc, key)
                )

    def test_no_em_dash(self):
        for loc in ("en", "fr"):
            for key, value in strings.STRINGS[loc].items():
                self.assertNotIn("—", value, (loc, key))

    def test_client_messages_both_languages(self):
        err = ph.HandoffError("offline")
        self.assertIn("Could not reach PrintFlows", err.message("en"))
        self.assertIn("Impossible de joindre PrintFlows", err.message("fr"))

    def test_french_space_before_double_punctuation(self):
        """French puts a space before ? ! : ; (the same plain space the strings use before ":")."""
        import re

        for key, value in strings.STRINGS["fr"].items():
            self.assertIsNone(re.search(r"\S[?!;]", value), key)
            self.assertIsNone(re.search(r"[^\s:{]:(?![/:\d])", value), key)
        self.assertEqual(
            strings.text("consent_title", "fr"), "Se connecter à PrintFlows ?"
        )

    def test_part_count_plurals(self):
        self.assertEqual(strings.plural("parts_count", 1, "en"), "1 part")
        self.assertEqual(strings.plural("parts_count", 2, "en"), "2 parts")
        self.assertEqual(strings.plural("parts_count", 1, "fr"), "1 pièce")
        self.assertEqual(strings.plural("parts_count", 2, "fr"), "2 pièces")
        self.assertEqual(strings.plural("parts_count", 0, "fr"), "0 pièce")
        for loc in ("en", "fr"):
            for value in strings.STRINGS[loc].values():
                self.assertNotIn("(s)", value)


class Quality(unittest.TestCase):
    def test_order_low_medium_high_with_medium_default(self):
        from freecad.PrintFlows import exporter

        self.assertEqual(exporter.QUALITY_ORDER, ("low", "medium", "high"))
        self.assertEqual(exporter.DEFAULT_QUALITY, "medium")
        self.assertEqual(set(exporter.QUALITY_PRESETS), set(exporter.QUALITY_ORDER))
        # Finer mesh = smaller deflection, so the list really goes coarse to fine.
        linear = [exporter.QUALITY_PRESETS[k][0] for k in exporter.QUALITY_ORDER]
        self.assertEqual(linear, sorted(linear, reverse=True))


class Progress(unittest.TestCase):
    def test_capped_at_99_until_the_server_answers(self):
        self.assertEqual(job.progress_percent(0, 1000), 0)
        self.assertEqual(job.progress_percent(500, 1000), 50)
        self.assertEqual(job.progress_percent(999, 1000), 99)
        self.assertEqual(
            job.progress_percent(1000, 1000), 99
        )  # all bytes out, no answer yet
        self.assertEqual(job.progress_percent(10, 10), 99)  # a small file
        self.assertEqual(job.progress_percent(5, 0), 0)

    def test_dialog_uses_the_cap_and_sets_100_on_done(self):
        path = os.path.join(_paths.ADDON, "freecad", "PrintFlows", "commands.py")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("jobmod.progress_percent(", source)
        self.assertNotIn("* 100 / total", source)
        self.assertIn("progress.setValue(100)", source)


class Language(unittest.TestCase):
    def test_names_and_codes(self):
        supported = {
            "French": "fr",
            "English": "en",
            "German": "de",
            "Portuguese, Brazilian": "pt-BR",
        }
        self.assertEqual(settings.locale_from("French", supported), "fr")
        self.assertEqual(
            settings.locale_from("French"), "fr"
        )  # no map: the name itself starts with fr
        self.assertEqual(settings.locale_from("English", supported), "en")
        self.assertEqual(settings.locale_from("German", supported), "en")
        self.assertEqual(settings.locale_from("", supported, "fr_CA"), "fr")
        self.assertEqual(settings.locale_from("", supported, "en_US"), "en")
        self.assertEqual(settings.locale_from("English", supported, "fr_CA"), "en")


class Consent(unittest.TestCase):
    def test_flag(self):
        params = FakeParams()
        self.assertFalse(settings.has_consent(PROD_BASE, params))
        settings.set_consent(True, PROD_BASE, params)
        self.assertTrue(settings.has_consent(PROD_BASE, params))
        settings.set_consent(False, PROD_BASE, params)
        self.assertFalse(settings.has_consent(PROD_BASE, params))

    def test_older_consent_version_asks_again(self):
        params = FakeParams()
        params.GetGroup(config.PARAM_CONSENT_GROUP).SetInt(
            settings.consent_key(PROD_BASE), config.CONSENT_VERSION - 1
        )
        self.assertFalse(settings.has_consent(PROD_BASE, params))

    def test_test_mode_yes_does_not_cover_production(self):
        params = FakeParams()
        settings.set_consent(True, TEST_BASE, params)
        self.assertTrue(settings.has_consent(TEST_BASE, params))
        self.assertFalse(settings.has_consent(PROD_BASE, params))

    def test_production_yes_does_not_cover_test_mode(self):
        params = FakeParams()
        settings.set_consent(True, PROD_BASE, params)
        self.assertTrue(settings.has_consent(PROD_BASE, params))
        self.assertFalse(settings.has_consent(TEST_BASE, params))
        self.assertFalse(settings.has_consent("http://127.0.0.1:50000", params))

    def test_round1_consent_without_host_is_no_consent(self):
        params = FakeParams()
        params.SetInt(config.PARAM_CONSENT, config.CONSENT_VERSION)  # round 1 layout
        self.assertFalse(settings.has_consent(PROD_BASE, params))
        self.assertFalse(settings.has_consent(TEST_BASE, params))

    def test_key_ignores_case_and_trailing_slash(self):
        params = FakeParams()
        settings.set_consent(True, "https://PrintFlows.org/", params)
        self.assertTrue(settings.has_consent(PROD_BASE, params))
        self.assertEqual(settings.consent_key(PROD_BASE), PROD_BASE)

    def test_allow_needs_a_host(self):
        with self.assertRaises(ValueError):
            settings.set_consent(True, None, FakeParams())

    def test_withdraw_covers_every_host(self):
        params = FakeParams()
        settings.set_consent(True, PROD_BASE, params)
        settings.set_consent(True, TEST_BASE, params)
        params.SetInt(config.PARAM_CONSENT, config.CONSENT_VERSION)
        settings.set_consent(False, params=params)
        self.assertFalse(settings.has_consent(PROD_BASE, params))
        self.assertFalse(settings.has_consent(TEST_BASE, params))
        self.assertNotIn(config.PARAM_CONSENT, params.ints)
        self.assertEqual(params.GetGroup(config.PARAM_CONSENT_GROUP).GetInts(), [])

    def test_withdraw_strings(self):
        for loc in ("en", "fr"):
            for key in ("withdraw_name", "withdraw_tooltip", "withdraw_done"):
                self.assertTrue(strings.STRINGS[loc].get(key), (loc, key))
        self.assertEqual(
            strings.text("withdraw_name", "en"), "Stop PrintFlows connecting"
        )

    def test_job_without_consent_never_builds_a_client(self):
        folder = tempfile.mkdtemp(prefix="pf-test-")
        path = os.path.join(folder, "x.stl")
        with open(path, "wb") as fh:
            fh.write(b"\0" * 84)
        built = []

        class Boom:
            def __getattr__(self, name):
                built.append(name)
                raise AssertionError("client used without consent")

        events = []
        upload = job.UploadJob(
            path,
            "en",
            events.append,
            consent=lambda: False,
            temp_dir=folder,
            client=Boom(),
        )
        upload.run()
        self.assertEqual(
            events,
            [
                {
                    "kind": "error",
                    "code": "consent_required",
                    "message": "",
                    "detail": "",
                }
            ],
        )
        self.assertEqual(built, [])
        self.assertFalse(os.path.exists(folder))


class Finish(unittest.TestCase):
    def test_actions(self):
        opened = []

        def opener(url):
            opened.append(url)
            return True

        upload = job.UploadJob("x.stl", "en", lambda e: None, consent=lambda: True)
        self.assertEqual(
            job.finish({"kind": "done", "openUrl": "u1"}, upload, opener), "opened"
        )
        upload.cancel()
        self.assertEqual(
            job.finish({"kind": "done", "openUrl": "u2"}, upload, opener), "cancelled"
        )
        self.assertEqual(
            job.finish({"kind": "error", "code": "cancelled"}, upload, opener),
            "cancelled",
        )
        self.assertEqual(
            job.finish({"kind": "error", "code": "offline"}, upload, opener), "error"
        )
        self.assertEqual(opened, ["u1"])
        self.assertEqual(
            job.finish({"kind": "done", "openUrl": "u3"}, None, lambda u: False),
            "open_failed",
        )


class Build(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [python_exe(), BUILD, *args],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_check_passes_on_checkout(self):
        result = self._run("--check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_fails_on_stale_copy(self):
        shared = os.path.join(
            tempfile.mkdtemp(prefix="pf-test-"), "printflows_handoff.py"
        )
        vendored = os.path.join(
            _paths.ADDON, "freecad", "PrintFlows", "printflows_handoff.py"
        )
        shutil.copyfile(vendored, shared)
        with open(shared, "ab") as fh:
            fh.write(b"# changed\n")
        result = self._run("--check", "--shared", shared)
        self.assertEqual(result.returncode, 1)
        self.assertIn("differs", result.stderr)
        os.remove(shared)
        os.rmdir(os.path.dirname(shared))

    def test_vendored_copy_is_the_shared_client(self):
        shared = os.path.join(_paths.REPO, "shared", "printflows_handoff.py")
        if not os.path.exists(shared):
            self.skipTest("not inside the PrintFlows CAD repo")
        with (
            open(shared, "rb") as a,
            open(
                os.path.join(
                    _paths.ADDON, "freecad", "PrintFlows", "printflows_handoff.py"
                ),
                "rb",
            ) as b,
        ):
            self.assertEqual(a.read(), b.read())

    def test_icon_check_ignores_crlf(self):
        """git autocrlf checks the SVG out with CRLF on Windows; --check must still pass."""
        sys.path.insert(0, _paths.ADDON)
        import build

        folder = tempfile.mkdtemp(prefix="pf-test-")
        saved = build.ICON
        build.ICON = os.path.join(folder, "PrintFlows.svg")
        try:
            with open(build.ICON, "wb") as fh:
                fh.write(build.ICON_SVG.replace("\n", "\r\n").encode("utf-8"))
            self.assertTrue(build.icon(check_only=True))
            with open(build.ICON, "wb") as fh:
                fh.write(b"<svg/>")
            self.assertFalse(build.icon(check_only=True))
        finally:
            build.ICON = saved
            os.remove(os.path.join(folder, "PrintFlows.svg"))
            os.rmdir(folder)

    def test_version_matches_package_xml(self):
        sys.path.insert(0, _paths.ADDON)
        import build

        self.assertEqual(build.package_version(), config.VERSION)

    def _public_copy(self):
        """The add-on alone, as in its own public repo: no shared/ folder next to it."""
        root = tempfile.mkdtemp(prefix="pf-test-")
        addon = os.path.join(root, "addon")
        os.makedirs(addon)
        for name in ("build.py", "package.xml", "LICENSE", "README.md"):
            shutil.copyfile(os.path.join(_paths.ADDON, name), os.path.join(addon, name))
        shutil.copytree(
            os.path.join(_paths.ADDON, "freecad"),
            os.path.join(addon, "freecad"),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        self.addCleanup(self._remove_copy, root)
        return addon

    @staticmethod
    def _remove_copy(root):
        # Only the folder this test made: a pf-test-* folder directly in the temp folder.
        assert os.path.dirname(root) == tempfile.gettempdir(), root
        assert os.path.basename(root).startswith("pf-test-"), root
        shutil.rmtree(root)

    def _run_in(self, addon, *args):
        return subprocess.run(
            [python_exe(), os.path.join(addon, "build.py"), *args],
            capture_output=True,
            text=True,
            cwd=addon,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_public_repo_check_passes_with_pinned_client(self):
        addon = self._public_copy()
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(addon), "shared")))
        result = self._run_in(addon, "--check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_repo_check_fails_on_changed_client(self):
        addon = self._public_copy()
        client = os.path.join(addon, "freecad", "PrintFlows", "printflows_handoff.py")
        with open(client, "ab") as fh:
            fh.write(b"# changed\n")
        result = self._run_in(addon, "--check")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("sha256", result.stderr)

    def test_public_repo_test_runner_finds_its_own_mock(self):
        """In the public repo the mock sits in tools/ inside the add-on, and shared/ is absent."""
        addon = self._public_copy()
        shutil.copytree(
            os.path.join(_paths.ADDON, "tests"),
            os.path.join(addon, "tests"),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_out"),
        )
        os.makedirs(os.path.join(addon, "tools"))
        with open(os.path.join(addon, "tools", "mock_server.py"), "w") as fh:
            fh.write("# stand-in\n")
        probe = (
            "import sys; sys.path.insert(0, 'tests'); import run_all; "
            "print(run_all.default_mock()); print(run_all.shared_tests_dir())"
        )
        result = subprocess.run(
            [python_exe(), "-c", probe],
            capture_output=True,
            text=True,
            cwd=addon,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        mock, shared = result.stdout.splitlines()
        self.assertEqual(
            os.path.normcase(mock),
            os.path.normcase(os.path.join(addon, "tools", "mock_server.py")),
        )
        self.assertEqual(shared, "None")

    def test_monorepo_test_runner_runs_shared_tests(self):
        if not os.path.isdir(os.path.join(_paths.REPO, "shared", "tests")):
            self.skipTest("not inside the PrintFlows CAD repo")
        import run_all

        self.assertEqual(
            run_all.shared_tests_dir(), os.path.join(_paths.REPO, "shared", "tests")
        )
        self.assertTrue(os.path.isfile(run_all.default_mock()))

    def test_icon_is_the_app_icon(self):
        """The kit's app icon: the white nozzle-tip mark on the teal rounded square."""
        sys.path.insert(0, _paths.ADDON)
        import build

        self.assertIn('fill="#12736b"', build.ICON_SVG)  # the kit's teal
        self.assertIn('fill="#ffffff"', build.ICON_SVG)  # the mark, white
        self.assertNotIn("#1f6feb", build.ICON_SVG)  # the old blue placeholder
        with open(BUILD, encoding="utf-8") as fh:
            self.assertNotIn("PLACEHOLDER", fh.read())
        with open(build.ICON, "rb") as fh:
            self.assertEqual(
                fh.read().replace(b"\r\n", b"\n"), build.ICON_SVG.encode("utf-8")
            )

    def test_explicit_shared_path_must_exist(self):
        missing = os.path.join(tempfile.gettempdir(), "pf-no-such-dir", "typo.py")
        result = self._run("--check", "--shared", missing)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("not found", result.stderr)

    def test_pin_ignores_crlf(self):
        """git autocrlf may check the client out with CRLF; the pin is over LF bytes."""
        sys.path.insert(0, _paths.ADDON)
        import build

        with open(build.VENDORED_CLIENT, "rb") as fh:
            data = fh.read().replace(b"\r\n", b"\n")
        self.assertEqual(build.client_sha256(data), build.CLIENT_SHA256)
        self.assertEqual(
            build.client_sha256(data.replace(b"\n", b"\r\n")), build.CLIENT_SHA256
        )


class Sweep(unittest.TestCase):
    """Stale PrintFlows-* export folders (FreeCAD quit mid-upload) are removed after a day."""

    def test_only_old_printflows_folders_go(self):
        import time

        from freecad.PrintFlows import exporter

        root = tempfile.mkdtemp(prefix="pf-test-")
        now = time.time()
        old = now - 2 * 86400

        def make(name, is_dir=True, mtime=None):
            path = os.path.join(root, name)
            if is_dir:
                os.makedirs(path)
                with open(os.path.join(path, "part.3mf"), "wb") as fh:
                    fh.write(b"x")
            else:
                with open(path, "wb") as fh:
                    fh.write(b"x")
            if mtime is not None:
                os.utime(path, (mtime, mtime))
            return path

        stale = make("PrintFlows-abc", mtime=old)
        fresh = make("PrintFlows-new")
        young = make("PrintFlows-young", mtime=now - 3600)
        other = make("Other-abc", mtime=old)
        loose = make("PrintFlows-file", is_dir=False, mtime=old)
        removed = exporter.sweep_stale_temp(root, now=now)
        self.assertEqual(removed, [stale])
        self.assertFalse(os.path.exists(stale))
        for path in (fresh, young, other, loose):
            self.assertTrue(os.path.exists(path), path)
        # cleanup: exactly what this test made
        for path in (fresh, young, other):
            os.remove(os.path.join(path, "part.3mf"))
            os.rmdir(path)
        os.remove(loose)
        os.rmdir(root)

    def test_missing_folder_is_fine(self):
        from freecad.PrintFlows import exporter

        self.assertEqual(
            exporter.sweep_stale_temp(os.path.join(tempfile.gettempdir(), "pf-none")),
            [],
        )


class Harness(unittest.TestCase):
    def test_detects_a_freecad_that_forces_the_windows_platform(self):
        import run_all

        folder = tempfile.mkdtemp(prefix="pf-test-")
        forced = os.path.join(folder, "forced.exe")
        plain = os.path.join(folder, "plain.exe")
        with open(forced, "wb") as fh:
            fh.write(b"MZ\0\0QT_QPA_PLATFORM=windows:darkmode=1\0rest")
        with open(plain, "wb") as fh:
            fh.write(b"MZ\0\0nothing here")
        try:
            self.assertTrue(run_all.forces_windows_platform(forced))
            self.assertFalse(run_all.forces_windows_platform(plain))
            self.assertFalse(run_all.forces_windows_platform(plain + ".missing"))
        finally:
            os.remove(forced)
            os.remove(plain)
            os.rmdir(folder)


class Licence(unittest.TestCase):
    def test_licence_next_to_the_client(self):
        """The copied client says "See LICENSE next to this file"; that file must exist."""
        here = os.path.join(_paths.ADDON, "freecad", "PrintFlows", "LICENSE")
        with (
            open(os.path.join(_paths.ADDON, "LICENSE"), "rb") as a,
            open(here, "rb") as b,
        ):
            self.assertEqual(
                a.read().replace(b"\r\n", b"\n"), b.read().replace(b"\r\n", b"\n")
            )
        with open(
            os.path.join(
                _paths.ADDON, "freecad", "PrintFlows", "printflows_handoff.py"
            ),
            "rb",
        ) as fh:
            self.assertIn(b"See LICENSE next to this file", fh.readline())


class Layout(unittest.TestCase):
    def test_namespace_layout(self):
        self.assertFalse(
            os.path.exists(os.path.join(_paths.ADDON, "freecad", "__init__.py"))
        )
        for name in ("__init__.py", "init_gui.py"):
            self.assertTrue(
                os.path.exists(
                    os.path.join(_paths.ADDON, "freecad", "PrintFlows", name)
                )
            )

    def test_startup_imports_stay_light(self):
        """init_gui imports only FreeCADGui and our small modules: no Qt, no network, no export."""
        src = open(
            os.path.join(_paths.ADDON, "freecad", "PrintFlows", "init_gui.py"),
            encoding="utf-8",
        ).read()
        tree = ast.parse(src)
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = sorted(
            (n.module or "") + ":" + ",".join(a.name for a in n.names)
            if isinstance(n, ast.ImportFrom)
            else n.names[0].name
            for n in top
        )
        self.assertEqual(names, [":config,settings,strings", "FreeCADGui"])

    def test_no_sys_path_changes_in_package(self):
        folder = os.path.join(_paths.ADDON, "freecad", "PrintFlows")
        for name in os.listdir(folder):
            if name.endswith(".py"):
                self.assertNotIn(
                    "sys.path",
                    open(os.path.join(folder, name), encoding="utf-8").read(),
                    name,
                )


if __name__ == "__main__":
    unittest.main()
