# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows.
"""GUI smoke test, run by FreeCAD.exe with QT_QPA_PLATFORM=offscreen (no window on screen).

Uses the add-on as installed in the user Mod folder (the way FreeCAD loads it), activates the
workbench, checks the commands, toolbar and menu, builds the dialog, drives the real modal
dialogs of "Print with PrintFlows" (the consent question included) with a QTimer that clicks
their buttons, and runs uploads through the Qt timer path with the browser opener replaced by
a recorder. Each section is checked on its own, so one failure does not hide the others.
Writes JSON to $PF_GUI_RESULT, then exits FreeCAD hard (no save prompts).
"""

import json
import os
import tempfile
import threading
import time
import traceback
import urllib.request

results = {"checks": {}, "info": {}, "errors": {}, "error": None}
MOCK = os.environ.get("PRINTFLOWS_BASE_URL", "")
PROD = "https://printflows.org"


def check(name, value):
    results["checks"][name] = bool(value)


def section(name, fn):
    try:
        fn()
    except Exception:  # noqa: BLE001 - recorded as a failed check, the next section runs
        results["checks"]["section ran: " + name] = False
        results["errors"][name] = traceback.format_exc()
    else:
        results["checks"]["section ran: " + name] = True


def mock_requests() -> int:
    with urllib.request.urlopen(MOCK + "/__mock__/state", timeout=5) as resp:
        return len(json.loads(resp.read().decode("utf-8"))["log"])


def wait_for_job(commands, seconds=30):
    from PySide import QtGui

    deadline = time.time() + seconds
    while commands._state["job"] is not None and time.time() < deadline:
        QtGui.QApplication.processEvents()
        time.sleep(0.02)


def shown_text(label) -> str:
    """What a person reads in a QLabel: rich text is rendered, plain text is shown as is."""
    from PySide import QtCore, QtGui

    text = label.text()
    if label.textFormat() == QtCore.Qt.PlainText:
        return text
    doc = QtGui.QTextDocument()
    doc.setHtml(text)
    return doc.toPlainText()


def selected_names() -> list:
    import FreeCADGui

    return sorted(
        "%s.%s" % (o.Document.Name, o.Name) for o in FreeCADGui.Selection.getSelection()
    )


def describe_box(box) -> dict:
    from PySide import QtGui

    out = {"title": box.windowTitle(), "buttons": [b.text() for b in box.buttons()]}
    for name, key in (
        ("qt_msgbox_label", "text"),
        ("qt_msgbox_informativelabel", "informative"),
    ):
        label = box.findChild(QtGui.QLabel, name)
        out[key] = shown_text(label) if label is not None else None
        out[key + "_raw"] = label.text() if label is not None else None
        out[key + "_format"] = str(label.textFormat()) if label is not None else None
    return out


class ModalDriver:
    """Answers the modal dialogs that open while ``fn`` runs, in order, with ``answers``.

    Each answer is a function taking the dialog; it records what it needs and closes it.
    A dialog beyond the list is rejected, so a test never hangs on an unexpected modal.
    """

    def __init__(self, answers):
        from PySide import QtCore

        self.answers = list(answers)
        self.seen = []
        self.timer = QtCore.QTimer()
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)

    def _tick(self):
        from PySide import QtGui

        widget = QtGui.QApplication.activeModalWidget()
        if widget is None or widget.property("pf_seen"):
            return
        widget.setProperty("pf_seen", True)
        index = len(self.seen)
        self.seen.append(type(widget).__name__)
        if index < len(self.answers):
            self.answers[index](widget)
        else:
            widget.reject()

    def run(self, fn):
        self.timer.start()
        try:
            return fn()
        finally:
            self.timer.stop()


def click(text):
    def answer(box):
        for button in box.buttons():
            if button.text() == text:
                button.click()
                return
        box.reject()

    return answer


def main():
    import FreeCAD
    import FreeCADGui
    from PySide import QtGui

    results["info"]["qpa"] = QtGui.QGuiApplication.platformName()
    if results["info"]["qpa"] != "offscreen":
        # Never go on with real windows: they would show on the desktop.
        raise RuntimeError("not offscreen: platform %r" % results["info"]["qpa"])
    results["info"]["freecad"] = ".".join(FreeCAD.Version()[:3])
    results["info"]["language_param"] = FreeCAD.ParamGet(
        "User parameter:BaseApp/Preferences/General"
    ).GetString("Language", "")

    # Stale export folders left by a FreeCAD that quit mid-upload: swept at activation.
    temp = tempfile.gettempdir()
    stale = os.path.join(temp, "PrintFlows-smoke-stale")
    fresh = os.path.join(temp, "PrintFlows-smoke-fresh")
    for path in (stale, fresh):
        os.makedirs(path, exist_ok=True)
    old = time.time() - 2 * 86400
    os.utime(stale, (old, old))

    check("workbench listed", "PrintFlowsWorkbench" in FreeCADGui.listWorkbenches())
    FreeCADGui.activateWorkbench("PrintFlowsWorkbench")
    check(
        "workbench active", FreeCADGui.activeWorkbench().name() == "PrintFlowsWorkbench"
    )
    check("activation swept the stale export folder", not os.path.exists(stale))
    check("activation kept the fresh export folder", os.path.exists(fresh))
    if os.path.isdir(fresh):
        os.rmdir(fresh)
    if os.path.isdir(stale):
        os.rmdir(stale)
    check("command registered", "PrintFlows_Print" in FreeCADGui.listCommands())
    check(
        "withdraw command registered",
        "PrintFlows_Withdraw" in FreeCADGui.listCommands(),
    )

    import freecad.PrintFlows as pkg
    from freecad.PrintFlows import commands, exporter, settings, strings

    results["info"]["package_file"] = pkg.__file__
    check(
        "loaded from user Mod",
        os.path.normcase(FreeCAD.getUserAppDataDir()) in os.path.normcase(pkg.__file__),
    )
    mw = FreeCADGui.getMainWindow()
    toolbars = [t.windowTitle() for t in mw.findChildren(QtGui.QToolBar)]
    check("toolbar present", "PrintFlows" in toolbars)
    menus = [a.text().replace("&", "") for a in mw.menuBar().actions()]
    results["info"]["menus"] = menus
    check("menu present", "PrintFlows" in menus)
    menu_items = []
    for action in mw.menuBar().actions():
        if action.text().replace("&", "") == "PrintFlows" and action.menu():
            menu_items = [a.text().replace("&", "") for a in action.menu().actions()]
    results["info"]["menu_items"] = menu_items
    check(
        "menu has the withdraw entry",
        any(
            item
            in (
                strings.text("withdraw_name", "en"),
                strings.text("withdraw_name", "fr"),
            )
            for item in menu_items
        )
        if "withdraw_name" in strings.STRINGS["en"]
        else False,
    )
    res = commands.PrintCommand().GetResources()
    check(
        "command text",
        res["MenuText"] in ("Print with PrintFlows", "Imprimer avec PrintFlows"),
    )
    check("icon file exists", os.path.exists(res["Pixmap"]))

    # Language: FreeCAD stores names ("French"); supportedLocales() maps them to codes.
    supported = dict(FreeCADGui.supportedLocales())
    results["info"]["supported_french"] = supported.get("French")
    general = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/General")
    saved = general.GetString("Language", "")
    general.SetString("Language", "French")
    check("French setting gives fr", settings.current_locale() == "fr")
    general.SetString("Language", "English")
    check("English setting gives en", settings.current_locale() == "en")
    general.SetString("Language", saved)

    # Dialog: fixtures built in code.
    doc = FreeCAD.newDocument("Smoke")
    box = doc.addObject("Part::Box", "Box")
    circle = doc.addObject("Part::Circle", "Circle")
    doc.recompute()

    def dialog_checks():
        dlg = commands.PrintDialog(doc, [box, circle], "en", mw)
        check("dialog built", dlg.windowTitle() == "Print with PrintFlows")
        check("default: selected objects", dlg.what.currentData() == "selected")
        check("default: 3mf", dlg.fmt.currentData() == "3mf")
        check("default: medium", dlg.quality.currentData() == "medium")
        check("size shown", dlg.size.text().startswith("10 x 10 x 10 mm"))
        check(
            "non-solid named",
            "Circle" in dlg.notice.text() and dlg.send_button.isEnabled(),
        )
        check(
            "privacy link",
            "https://printflows.org/en/legal/privacy" in dlg.privacy.text(),
        )
        check(
            "test-mode notice",
            dlg.test_notice is not None and MOCK in dlg.test_notice.text(),
        )
        results["info"]["size_text"] = dlg.size.text()
        dlg.deleteLater()
        empty = commands.PrintDialog(doc, [], "fr", mw)
        check("fr dialog", empty.windowTitle() == "Imprimer avec PrintFlows")
        check(
            "no selection message",
            empty.notice.text() == strings.text("no_selection", "fr")
            and not empty.send_button.isEnabled(),
        )
        empty.what.setCurrentIndex(1)  # all visible solids
        check("visible solids enable Send", empty.send_button.isEnabled())
        empty.deleteLater()
        only_circle = commands.PrintDialog(doc, [circle], "en", mw)
        check(
            "only non-solid blocks Send",
            not only_circle.send_button.isEnabled()
            and only_circle.notice.text() == strings.text("nothing", "en"),
        )
        only_circle.deleteLater()

    section("dialog", dialog_checks)

    # The real "Print with PrintFlows" run, with every modal answered by the driver.
    FreeCAD.setActiveDocument(doc.Name)
    FreeCADGui.Selection.clearSelection()
    FreeCADGui.Selection.addSelection(box)

    def declined_run(locale, language, no_text):
        def body():
            settings.set_consent(False)
            general.SetString("Language", language)
            boxes = []
            before = mock_requests()
            driver = ModalDriver(
                [
                    lambda w: w.accept(),  # the Print dialog: Send
                    lambda w: (boxes.append(describe_box(w)), click(no_text)(w)),
                    lambda w: (boxes.append(describe_box(w)), w.accept()),
                ]
            )
            try:
                driver.run(commands.run)
            finally:
                general.SetString("Language", saved)
            results["info"]["consent_box_" + locale] = boxes[:1]
            results["info"]["modals_" + locale] = driver.seen
            consent = boxes[0] if boxes else {}
            url = strings.PRIVACY_URLS[locale]
            shown = (consent.get("text") or "") + (consent.get("informative") or "")
            check(
                "consent box (%s) is the consent question" % locale,
                consent.get("title") == strings.text("consent_title", locale),
            )
            check(
                "consent box (%s) shows no HTML tags" % locale,
                shown and "<" not in shown and "href" not in shown,
            )
            check(
                "consent box (%s) shows the privacy URL" % locale,
                url in (consent.get("informative") or ""),
            )
            check(
                "consent box (%s) names the test server" % locale,
                MOCK.split("://", 1)[-1] in (consent.get("text") or ""),
            )
            check(
                "declined (%s): consent_needed shown" % locale,
                len(boxes) == 2
                and boxes[1].get("text") == strings.text("consent_needed", locale),
            )
            check(
                "declined (%s): 0 server requests" % locale, mock_requests() == before
            )

        return body

    section("declined run en", declined_run("en", "English", "Don't allow"))
    section("declined run fr", declined_run("fr", "French", "Ne pas autoriser"))

    def allowed_run():
        settings.set_consent(False)
        general.SetString("Language", "English")
        opened = []
        real_start = commands.start_upload

        def start_with_recorder(path, temp_dir, locale, *args, **kwargs):
            kwargs["opener"] = lambda url: opened.append(url) or True
            return real_start(path, temp_dir, locale, *args, **kwargs)

        commands.start_upload = start_with_recorder
        driver = ModalDriver([lambda w: w.accept(), click("Allow")])
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(box)
        before = selected_names()
        try:
            driver.run(commands.run)
            wait_for_job(commands)
        finally:
            commands.start_upload = real_start
            general.SetString("Language", saved)
        after = selected_names()
        results["info"]["modals_allowed"] = driver.seen
        results["info"]["selection_before_after_send"] = [before, after]
        check(
            "allowed: the selection is the same after the send",
            before == ["Smoke.Box"] and after == before,
        )
        check(
            "allowed: upload opened the recorder once",
            len(opened) == 1 and opened[0].startswith(MOCK),
        )
        check("allowed: consent stored for the test server", settings.has_consent(MOCK))
        check(
            "allowed: the test server's yes does not cover printflows.org",
            not settings.has_consent(PROD),
        )

    section("allowed run", allowed_run)

    def selection_kept_by_export():
        # Which step could clear it: each export format on its own, with the box selected.
        for fmt in ("3mf", "stl"):
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(box)
            parts, _ = exporter.from_selection([box])
            folder, _path = exporter.write_to_temp(
                exporter.mesh_parts(parts), fmt, "SmokeSel"
            )
            import shutil

            shutil.rmtree(
                folder, ignore_errors=True
            )  # the folder write_to_temp just made
            after = selected_names()
            results["info"]["selection_after_" + fmt] = after
            check("%s export keeps the selection" % fmt, after == ["Smoke.Box"])
            check(
                "%s export keeps the active document" % fmt,
                FreeCAD.ActiveDocument is not None
                and FreeCAD.ActiveDocument.Name == doc.Name,
            )

    section("selection kept", selection_kept_by_export)

    def upload_paths():
        # One upload through the GUI path: QProgressDialog + QTimer polling, recorder as browser.
        settings.set_consent(True, MOCK)
        parts, _ = exporter.from_selection([box])
        folder, path = exporter.write_to_temp(
            exporter.mesh_parts(parts), "3mf", "SmokeBox"
        )
        opened = []
        values = []
        events_seen = []
        real_poll = commands._poll

        def recording_poll(upload, events, locale, opener):
            # Record each value the bar gets and the event that set it.
            progress = commands._state["progress"]
            if progress is not None and not getattr(progress, "_pf_recorded", False):
                real_set = progress.setValue

                def set_value(value, _real=real_set):
                    values.append((value, list(events_seen)))
                    return _real(value)

                progress.setValue = set_value
                progress._pf_recorded = True
            if not getattr(events, "_pf_recorded", False):
                real_get = events.get_nowait

                def get_nowait(_real=real_get):
                    event = _real()  # raises queue.Empty when there is none
                    events_seen.append(event.get("kind"))
                    return event

                events.get_nowait = get_nowait
                events._pf_recorded = True
            return real_poll(upload, events, locale, opener)

        commands._poll = recording_poll
        try:
            commands.start_upload(
                path, folder, "en", opener=lambda url: opened.append(url) or True
            )
            wait_for_job(commands)
        finally:
            commands._poll = real_poll
        results["info"]["progress_values"] = [v for v, _ in values]
        check(
            "progress stays below 100 until the server answered",
            values
            and all(v < 100 for v, seen in values if "done" not in seen)
            and values[-1][0] == 100,
        )
        check(
            "gui upload opened recorder once",
            len(opened) == 1 and "/en/start?handoff=" in opened[0],
        )
        check("gui upload temp removed", not os.path.exists(folder))
        check("progress dialog closed", commands._state["progress"] is None)
        results["info"]["opened"] = opened
        # Cancel from the progress dialog: whatever the worker got done, no browser.
        folder2, path2 = exporter.write_to_temp(
            exporter.mesh_parts(parts), "stl", "SmokeCancel"
        )
        opened2 = []
        commands.start_upload(
            path2, folder2, "en", opener=lambda url: opened2.append(url) or True
        )
        cancel_button = commands._state["progress"].findChild(QtGui.QPushButton)
        check(
            "progress has a Cancel button",
            cancel_button is not None and cancel_button.text() == "Cancel",
        )
        cancel_button.click()
        wait_for_job(commands)
        check(
            "cancel button: no browser",
            opened2 == [] and commands._state["job"] is None,
        )
        check("cancel button: temp removed", not os.path.exists(folder2))

    section("upload paths", upload_paths)

    def worker_never_reads_params():
        # FreeCAD.ParamGet must only run on the GUI thread, never on the upload worker.
        threads = []
        real_params = settings._params

        def recording_params():
            threads.append(threading.current_thread().name)
            return real_params()

        parts, _ = exporter.from_selection([box])
        folder, path = exporter.write_to_temp(
            exporter.mesh_parts(parts), "3mf", "SmokeThread"
        )
        settings._params = recording_params
        try:
            upload = commands.start_upload(path, folder, "en", opener=lambda url: True)
            upload.join(30)
            wait_for_job(commands)
        finally:
            settings._params = real_params
        results["info"]["param_threads"] = threads
        check(
            "upload worker never calls FreeCAD.ParamGet",
            threads and set(threads) == {threading.main_thread().name},
        )

    section("worker thread", worker_never_reads_params)

    def withdraw():
        settings.set_consent(True, MOCK)
        settings.set_consent(True, PROD)
        top = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/PrintFlows")
        top.SetInt("ConsentVersion", 1)  # what round 1 stored
        general.SetString("Language", "English")
        boxes = []
        driver = ModalDriver([lambda w: (boxes.append(describe_box(w)), w.accept())])
        try:
            driver.run(lambda: FreeCADGui.runCommand("PrintFlows_Withdraw"))
        finally:
            general.SetString("Language", saved)
        results["info"]["withdraw_box"] = boxes
        check(
            "withdraw: confirmation shown",
            len(boxes) == 1 and boxes[0]["text"] == strings.text("withdraw_done", "en"),
        )
        check(
            "withdraw: no host has consent",
            not settings.has_consent(MOCK) and not settings.has_consent(PROD),
        )
        check("withdraw: round 1 flag removed", top.GetInt("ConsentVersion", 0) == 0)

    section("withdraw", withdraw)

    settings.set_consent(False)
    FreeCAD.closeDocument(doc.Name)


try:
    main()
except Exception:  # noqa: BLE001
    results["error"] = traceback.format_exc()
out = os.environ.get("PF_GUI_RESULT")
if out:
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
os._exit(0 if results["error"] is None and all(results["checks"].values()) else 1)
