# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows. See LICENSE at the top of the add-on.
"""The upload job: runs on a worker thread and never touches FreeCAD or Qt.

It reports through ``emit(payload_dict)``; the dialog puts the events on a queue and reads
them on the GUI thread with a QTimer. Events, in order:

    {"kind": "stage", "stage": "request"}
    {"kind": "stage", "stage": "upload", "total": N}
    {"kind": "progress", "sent": n, "total": N}   (at most every 1 % and 0.15 s)
    {"kind": "done", "openUrl": "..."}           or
    {"kind": "error", "code": "...", "message": "..."}

Nothing connects unless consent was given: ``consent()`` is called first, on the worker,
before the client is even built. It must not call FreeCAD: the caller reads the answer on
the GUI thread and passes ``lambda: allowed``. ``base`` is the server that answer covers. The temp folder holding the export is removed when the
job ends, whatever happens.
"""

from __future__ import annotations

import os
import shutil
import threading
import time

from . import config
from . import printflows_handoff as ph


def progress_percent(sent: int, total: int) -> int:
    """The progress bar's value for ``sent`` of ``total`` bytes: 0 to 99.

    ``sent`` counts bytes handed to the socket, so on a small file it reaches ``total`` before
    the server has read them. 100 waits for the server's answer (the "done" event).
    """
    if not total or total <= 0:
        return 0
    return max(0, min(99, int(sent * 100 / total)))


class ConsentRequired(Exception):
    """The person has not allowed the add-on to connect yet."""


class UploadJob:
    def __init__(
        self,
        file_path: str,
        locale: str,
        emit,
        *,
        consent,
        temp_dir: str | None = None,
        base: str | None = None,
        client: ph.HandoffClient | None = None,
        version: str = config.VERSION,
    ):
        self.file_path = file_path
        self.locale = locale
        self.emit = emit
        self.temp_dir = temp_dir
        self.base = base
        self.version = version
        self._consent = consent
        self._client = client
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_percent = -1
        self._last_time = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self.run, name="PrintFlows-upload", daemon=True
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise ph.HandoffError("cancelled")

    def _progress(self, sent: int, total: int) -> None:
        if self._cancel.is_set():
            raise ph.HandoffError("cancelled")
        percent = int(sent * 100 / total) if total else 100
        now = time.monotonic()
        if sent == total or (
            percent > self._last_percent and now - self._last_time >= 0.15
        ):
            self._last_percent = percent
            self._last_time = now
            self.emit({"kind": "progress", "sent": sent, "total": total})

    def run(self) -> None:
        try:
            if not self._consent():
                raise ConsentRequired()
            self._check_cancel()
            client = self._client or ph.HandoffClient(
                config.SOURCE, self.version, self.base
            )
            size = ph.check_file(self.file_path)
            self.emit({"kind": "stage", "stage": "request"})
            handoff = client.request_handoff(
                os.path.basename(self.file_path), size, self.locale
            )
            self._check_cancel()
            self.emit({"kind": "stage", "stage": "upload", "total": size})
            client.upload(handoff, self.file_path, self._progress)
            # A cancel at any point, even after the last byte went out, means no "done"
            # and so no browser. finish() checks once more before opening it.
            self._check_cancel()
            self.emit({"kind": "done", "openUrl": handoff.open_url})
        except ConsentRequired:
            self.emit(
                {
                    "kind": "error",
                    "code": "consent_required",
                    "message": "",
                    "detail": "",
                }
            )
        except ph.HandoffError as err:
            self.emit(
                {
                    "kind": "error",
                    "code": err.code,
                    "message": err.message(self.locale),
                    "detail": str(err),
                }
            )
        except Exception as exc:  # noqa: BLE001 - every failure must reach the person
            self.emit(
                {
                    "kind": "error",
                    "code": "unexpected",
                    "message": "",
                    "detail": "%s: %s" % (type(exc).__name__, exc),
                }
            )
        finally:
            if self.temp_dir:
                shutil.rmtree(self.temp_dir, ignore_errors=True)


def finish(event: dict, job: UploadJob | None, opener) -> str:
    """What to do with a final event, on the GUI thread. Opens the browser only after a
    successful upload that nobody cancelled.

    Returns "opened", "open_failed" (show the link instead), "cancelled", "error" or "ignored".
    """
    kind = event.get("kind")
    if kind == "done":
        if job is not None and job.cancelled:
            return "cancelled"
        return "opened" if opener(event.get("openUrl", "")) else "open_failed"
    if kind == "error":
        return "cancelled" if event.get("code") == "cancelled" else "error"
    return "ignored"
