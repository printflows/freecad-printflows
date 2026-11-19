# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows. See LICENSE at the top of the add-on.
"""The "Print with PrintFlows" command: dialog, consent, export, background upload.

Threading: the dialog, the export and every FreeCAD call run on the GUI thread. The upload
runs on a worker thread (job.UploadJob) that never calls FreeCAD or Qt (consent is read here
and handed over as a value); its events go on a
queue that a QTimer reads on the GUI thread. The browser opens only after a successful,
uncancelled upload.

Imported from the workbench's Initialize(), never at FreeCAD startup.
"""

from __future__ import annotations

import html
import os
import queue
import shutil

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui  # FreeCAD's Qt wrapper; QtGui also carries the widgets

from . import config, exporter, settings, strings
from . import job as jobmod

_state = {"job": None, "timer": None, "progress": None, "queue": None}
_last_choice = {
    "what": 0,
    "format": 0,
    "quality": exporter.QUALITY_ORDER.index(exporter.DEFAULT_QUALITY),
}  # remembered for this FreeCAD session


def _t(key, locale, **values):
    return strings.text(key, locale, **values)


def _main_window():
    try:
        return FreeCADGui.getMainWindow()
    except Exception:  # noqa: BLE001
        return None


def _message(text, locale, icon=None):
    box = QtGui.QMessageBox(_main_window())
    box.setIcon(icon if icon is not None else QtGui.QMessageBox.Warning)
    box.setWindowTitle(_t("title", locale))
    box.setText(text)
    box.exec_()


def _base_url_override():
    value = (os.environ.get(config.BASE_URL_ENV) or "").strip()
    return value or None


def _host(base: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(base).netloc or base


def _rich(text: str) -> str:
    """Plain text (which may hold a host name) as Qt rich text."""
    return html.escape(text).replace("\n", "<br>")


def sweep_stale_exports() -> list:
    """Remove export folders a FreeCAD that quit mid-upload left in the temp folder."""
    return exporter.sweep_stale_temp(
        prefix=config.TEMP_PREFIX, max_age=config.STALE_TEMP_SECONDS
    )


def open_url(url: str) -> bool:
    """Open the handoff page in the system browser (Qt first, then the webbrowser module)."""
    try:
        if QtGui.QDesktopServices.openUrl(QtCore.QUrl(url)):
            return True
    except Exception:  # noqa: BLE001
        pass
    from . import printflows_handoff as ph

    return ph.open_in_browser(url)


# --------------------------------------------------------------------------
# Dialog
# --------------------------------------------------------------------------


class PrintDialog(QtGui.QDialog):
    """What to send, format, mesh quality, size, privacy note, Send / Cancel."""

    def __init__(self, doc, selection, locale, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.selection = list(selection)
        self.locale = locale
        self.parts = []
        self.skipped = []
        self.setWindowTitle(_t("title", locale))
        self.setMinimumWidth(460)

        layout = QtGui.QVBoxLayout(self)
        form = QtGui.QFormLayout()
        self.what = QtGui.QComboBox()
        self.what.addItem(_t("what_selected", locale), "selected")
        self.what.addItem(_t("what_visible", locale), "visible")
        self.fmt = QtGui.QComboBox()
        self.fmt.addItem(_t("fmt_3mf", locale), "3mf")
        self.fmt.addItem(_t("fmt_stl", locale), "stl")
        self.quality = QtGui.QComboBox()
        for key in exporter.QUALITY_ORDER:
            self.quality.addItem(_t("quality_" + key, locale), key)
        self.size = QtGui.QLabel()
        form.addRow(_t("what_label", locale), self.what)
        form.addRow(_t("fmt_label", locale), self.fmt)
        form.addRow(_t("quality_label", locale), self.quality)
        form.addRow(_t("size_label", locale), self.size)
        layout.addLayout(form)

        self.notice = QtGui.QLabel()  # no selection / objects without a solid
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("color: #b45309;")
        layout.addWidget(self.notice)

        privacy = QtGui.QLabel(_t("privacy", locale))
        privacy.setWordWrap(True)
        privacy.setTextFormat(QtCore.Qt.RichText)
        privacy.setOpenExternalLinks(True)
        layout.addWidget(privacy)
        self.privacy = privacy

        self.test_notice = None
        override = _base_url_override()
        if override:
            self.test_notice = QtGui.QLabel(_t("test_base", locale, base=override))
            self.test_notice.setTextFormat(QtCore.Qt.PlainText)
            self.test_notice.setWordWrap(True)
            self.test_notice.setStyleSheet("color: #b91c1c; font-weight: bold;")
            layout.addWidget(self.test_notice)

        buttons = QtGui.QDialogButtonBox()
        self.send_button = buttons.addButton(
            _t("ok", locale), QtGui.QDialogButtonBox.AcceptRole
        )
        buttons.addButton(_t("cancel", locale), QtGui.QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.what.setCurrentIndex(_last_choice["what"] if self.selection else 0)
        self.fmt.setCurrentIndex(_last_choice["format"])
        self.quality.setCurrentIndex(_last_choice["quality"])
        self.what.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def choice(self) -> dict:
        return {
            "what": self.what.currentData(),
            "format": self.fmt.currentData(),
            "quality": self.quality.currentData(),
        }

    def refresh(self, *_args) -> None:
        loc = self.locale
        notes = []
        if self.what.currentData() == "visible":
            self.parts, self.skipped = exporter.visible_solids(self.doc), []
        elif not self.selection:
            self.parts, self.skipped = [], []
            notes.append(_t("no_selection", loc))
        else:
            self.parts, self.skipped = exporter.from_selection(self.selection)
            if self.skipped and self.parts:
                notes.append(_t("no_solid", loc, names=", ".join(self.skipped)))
            elif self.skipped:
                notes.append(_t("nothing", loc))
        if self.what.currentData() == "visible" and not self.parts:
            notes.append(_t("nothing", loc))
        size = exporter.bounding_box(self.parts)
        if size is None:
            self.size.setText(_t("size_none", loc))
        else:
            x, y, z = exporter.format_size(size)
            self.size.setText(
                "%s  (%s)"
                % (
                    _t("size_value", loc, x=x, y=y, z=z),
                    strings.plural("parts_count", len(self.parts), loc),
                )
            )
        self.notice.setText("\n".join(notes))
        self.notice.setVisible(bool(notes))
        self.send_button.setEnabled(bool(self.parts))


def ask_consent(locale, base, parent=None) -> bool:
    """The first-connection question for the server ``base``. Stores a yes for that server
    only; returns True when allowed."""
    box = QtGui.QMessageBox(parent)
    box.setIcon(QtGui.QMessageBox.Question)
    box.setWindowTitle(_t("consent_title", locale))
    # Rich text so the privacy link is a link. The question itself is escaped, so the host
    # name is shown as text whatever it holds.
    box.setTextFormat(QtCore.Qt.RichText)
    box.setText(_rich(_t("consent_text", locale, host=_host(base))))
    box.setInformativeText(_t("consent_privacy", locale))
    box.setTextFormat(QtCore.Qt.RichText)  # applies to the informative label too
    yes = box.addButton(_t("consent_yes", locale), QtGui.QMessageBox.AcceptRole)
    box.addButton(_t("consent_no", locale), QtGui.QMessageBox.RejectRole)
    box.exec_()
    allowed = box.clickedButton() is yes
    if allowed:
        settings.set_consent(True, base)
    return allowed


# --------------------------------------------------------------------------
# Command
# --------------------------------------------------------------------------


class PrintCommand:
    def GetResources(self):
        locale = settings.current_locale()
        return {
            "Pixmap": config.ICON_PATH,
            "MenuText": _t("cmd_name", locale),
            "ToolTip": _t("cmd_tooltip", locale),
        }

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        try:
            run()
        except Exception:  # noqa: BLE001 - never let a traceback be the only message
            import traceback

            _message(
                _t(
                    "unexpected",
                    settings.current_locale(),
                    detail=traceback.format_exc(),
                ),
                "en",
            )


def run():
    locale = settings.current_locale()
    doc = FreeCAD.ActiveDocument
    if doc is None:
        _message(_t("no_document", locale), locale)
        return
    current = _state["job"]
    if current is not None and current.running:
        _message(_t("busy", locale), locale)
        return

    selection = [o for o in FreeCADGui.Selection.getSelection(doc.Name)]
    dialog = PrintDialog(doc, selection, locale, _main_window())
    if dialog.exec_() != QtGui.QDialog.Accepted:
        return
    choice = dialog.choice()
    _last_choice.update(
        what=dialog.what.currentIndex(),
        format=dialog.fmt.currentIndex(),
        quality=dialog.quality.currentIndex(),
    )
    parts = dialog.parts
    if not parts:
        _message(_t("nothing", locale), locale)
        return

    from . import printflows_handoff as ph

    try:
        base = ph.base_url()
    except ph.HandoffError as err:
        _message(err.message(locale), locale)
        return

    # Consent for this server before anything that could lead to a connection. Declining
    # sends nothing.
    if not settings.has_consent(base) and not ask_consent(locale, base, _main_window()):
        _message(_t("consent_needed", locale), locale, QtGui.QMessageBox.Information)
        return

    QtGui.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
    try:
        meshes = exporter.mesh_parts(parts, choice["quality"])
        triangles = exporter.triangle_count(meshes)
    except Exception as exc:  # noqa: BLE001
        QtGui.QApplication.restoreOverrideCursor()
        _message(
            _t("export_failed", locale, detail=str(exc) or type(exc).__name__), locale
        )
        return
    QtGui.QApplication.restoreOverrideCursor()

    if triangles > exporter.MAX_PREVIEW_TRIANGLES:
        answer = QtGui.QMessageBox.warning(
            _main_window(),
            _t("title", locale),
            _t(
                "too_many_triangles",
                locale,
                count="{:,}".format(triangles),
                limit="{:,}".format(exporter.MAX_PREVIEW_TRIANGLES),
            ),
            QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
            QtGui.QMessageBox.No,
        )
        if answer != QtGui.QMessageBox.Yes:
            return

    QtGui.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
    try:
        stem = (
            doc.Label
            if choice["what"] == "visible" or len(parts) > 1
            else parts[0].label
        )
        temp_dir, path = exporter.write_to_temp(
            meshes, choice["format"], stem, config.TEMP_PREFIX
        )
    except Exception as exc:  # noqa: BLE001
        QtGui.QApplication.restoreOverrideCursor()
        _message(
            _t("export_failed", locale, detail=str(exc) or type(exc).__name__), locale
        )
        return
    QtGui.QApplication.restoreOverrideCursor()

    try:
        start_upload(path, temp_dir, locale, base=base)
    except Exception as exc:  # noqa: BLE001 - never leave the export in the temp folder
        shutil.rmtree(temp_dir, ignore_errors=True)
        _stop_ui()
        _message(
            _t("unexpected", locale, detail=str(exc) or type(exc).__name__), locale
        )


def start_upload(path, temp_dir, locale, opener=open_url, base=None):
    """Start the worker, show a progress dialog with Cancel, poll the events with a QTimer.

    Runs on the GUI thread: the server and the consent answer are read here and handed to
    the worker as values, so the worker never calls FreeCAD.
    """
    from . import printflows_handoff as ph

    base = base or ph.base_url()
    allowed = settings.has_consent(base)
    events = queue.Queue()
    upload = jobmod.UploadJob(
        path,
        locale,
        events.put,
        consent=lambda: allowed,
        temp_dir=temp_dir,
        base=base,
    )

    progress = QtGui.QProgressDialog(
        _t("preparing", locale), _t("cancel", locale), 0, 100, _main_window()
    )
    progress.setWindowTitle(_t("title", locale))
    progress.setWindowModality(QtCore.Qt.NonModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.setValue(0)
    progress.canceled.connect(upload.cancel)

    timer = QtCore.QTimer()
    timer.setInterval(100)
    timer.timeout.connect(lambda: _poll(upload, events, locale, opener))
    _state.update(job=upload, timer=timer, progress=progress, queue=events)
    upload.start()
    timer.start()
    progress.show()
    return upload


def _stop_ui():
    timer, progress = _state["timer"], _state["progress"]
    if timer is not None:
        timer.stop()
    if progress is not None:
        # Closing a QProgressDialog emits canceled(); unhook it first so a finished
        # upload is not marked as cancelled (which would keep the browser closed).
        try:
            progress.canceled.disconnect()
        except (RuntimeError, TypeError):
            pass
        progress.close()
    _state.update(timer=None, progress=None, queue=None)


def _poll(upload, events, locale, opener):
    if _state["job"] is not upload:
        return
    progress = _state["progress"]
    while True:
        try:
            event = events.get_nowait()
        except queue.Empty:
            return
        kind = event.get("kind")
        if kind == "stage" and progress is not None:
            progress.setLabelText(
                _t("sending" if event.get("stage") == "upload" else "preparing", locale)
            )
        elif kind == "progress" and progress is not None:
            # At most 99 %: the bytes are out, but the server has not answered yet.
            progress.setValue(
                jobmod.progress_percent(event.get("sent", 0), event.get("total") or 0)
            )
        elif kind in ("done", "error"):
            if kind == "done" and progress is not None:
                progress.setValue(100)  # the server took the file
            action = jobmod.finish(event, upload, opener)  # before the dialog closes
            _stop_ui()
            _state["job"] = None
            if action == "open_failed":
                _message(
                    _t("browser_failed", locale, url=event.get("openUrl", "")),
                    locale,
                    QtGui.QMessageBox.Information,
                )
            elif action == "error":
                if event.get("code") == "consent_required":
                    _message(
                        _t("consent_needed", locale),
                        locale,
                        QtGui.QMessageBox.Information,
                    )
                else:
                    _message(
                        event.get("message")
                        or _t("unexpected", locale, detail=event.get("detail", "")),
                        locale,
                    )
            return


class WithdrawCommand:
    """Withdraw consent for every server. As easy as giving it: one menu entry."""

    def GetResources(self):
        locale = settings.current_locale()
        return {
            "MenuText": _t("withdraw_name", locale),
            "ToolTip": _t("withdraw_tooltip", locale),
        }

    def IsActive(self):
        return True

    def Activated(self):
        locale = settings.current_locale()
        settings.set_consent(False)
        _message(_t("withdraw_done", locale), locale, QtGui.QMessageBox.Information)


FreeCADGui.addCommand(config.CMD_ID, PrintCommand())
FreeCADGui.addCommand(config.WITHDRAW_CMD_ID, WithdrawCommand())
