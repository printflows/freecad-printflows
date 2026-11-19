# MIT License. Copyright (c) 2026 PrintFlows. See LICENSE next to this file.
"""PrintFlows handoff client, shared by the PrintFlows CAD add-ins.

Flow: ask the site for a handoff (POST /api/handoff), PUT the file bytes to
the signed upload URL it returns, then open the handoff page in the system
browser so the person can pick a maker and pay on printflows.org.

Standard library only. Runs on Python 3.11 to 3.14 (FreeCAD, Fusion and
Blender bundle their own interpreters). Nothing here calls a CAD program's
API, so every function is safe to run on a worker thread.

No telemetry: the only requests made are the two above, to the server that
answered the first one and to the upload URL it named.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

__all__ = [
    "ACCEPTED_EXTENSIONS",
    "BASE_URL_ENV",
    "DEFAULT_BASE_URL",
    "MAX_BYTES",
    "Handoff",
    "HandoffClient",
    "HandoffError",
    "base_url",
    "check_file",
    "message_for",
    "normalize_locale",
    "open_in_browser",
    "request_handoff",
    "send_file",
    "upload",
    "user_agent",
]

__version__ = "1.0.0"

DEFAULT_BASE_URL = "https://printflows.org"
BASE_URL_ENV = "PRINTFLOWS_BASE_URL"  # testing only
MAX_BYTES = 100 * 1024 * 1024
ACCEPTED_EXTENSIONS = (".stl", ".3mf", ".step", ".stp", ".obj")

REQUEST_TIMEOUT = 30.0  # seconds, for the handoff request
UPLOAD_TIMEOUT = 120.0  # seconds without progress before an upload gives up
PROGRESS_STEP = 64 * 1024  # report upload progress at most once per this many bytes

# Hosts whose pages the client may open even when the API base is elsewhere.
_SITE_HOSTS = ("printflows.org", "www.printflows.org")
# Signed upload URLs point at Cloudflare R2 (or at the API base's own host, e.g. the local mock).
_UPLOAD_HOST_SUFFIX = ".r2.cloudflarestorage.com"
# The only hosts an http:// base URL may use (local testing).
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
# Shown when the server asks for an update but names no download link. The add-in sets this
# to its own help page.
HELP_URL = None
# Environment variables that could change how curl checks certificates or logs TLS keys.
_CURL_ENV_DROP = (
    "CURL_HOME",
    "CURL_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "SSLKEYLOGFILE",
    "CURL_SSL_BACKEND",
)

# curl exit codes, from the curl man page.
_CURL_TLS_EXITS = {35, 51, 53, 54, 58, 59, 60, 64, 66, 77, 80, 82, 83, 90, 91}
_CURL_OFFLINE_EXITS = {5, 6, 7}
_CURL_TIMEOUT_EXITS = {28}
_CURL_RESET_EXITS = {52, 55, 56}


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------

_MESSAGES = {
    "en": {
        "invalid": "PrintFlows could not read this request ({field}). Please update the add-in or contact support.",
        "too_large": "This file is {size}. PrintFlows takes files up to {limit}. Try a lower mesh quality or send one part at a time.",
        "unsupported_format": "PrintFlows takes these file types: {accepted}.",
        "update_required": "{server_message}This version of the add-in is too old. Get the new one here: {download_url}",
        "rate_limited": "Too many sends in a short time. Please try again in {retry_after} seconds.",
        "server_error": "PrintFlows had a problem on its side. Please try again in a few minutes.",
        "busy": "PrintFlows is busy right now. Please try again later.",
        "offline": "Could not reach PrintFlows. Check your internet connection and try again.",
        "timeout": "PrintFlows took too long to answer. Check your connection and try again.",
        "tls": "Could not open a secure connection to PrintFlows. Check that your computer's date and time are right, and that no proxy or antivirus is blocking printflows.org.",
        "size_mismatch": "The file changed while it was being sent. Please try again.",
        "upload_failed": "The upload did not finish (status {status}). Please try again.",
        "bad_response": "PrintFlows sent an answer this add-in does not understand. Please update the add-in.",
        "file_error": "Could not read the exported file: {detail}",
        "cancelled": "The upload was cancelled.",
        "bad_base_url": "PRINTFLOWS_BASE_URL is set to {detail}, which is not allowed. It must be an https address, or http on this computer (localhost, 127.0.0.1 or ::1). Remove the variable to send to printflows.org.",
    },
    "fr": {
        "invalid": "PrintFlows n'a pas pu lire cette demande ({field}). Mettez le module à jour ou contactez le support.",
        "too_large": "Ce fichier fait {size}. PrintFlows accepte les fichiers jusqu'à {limit}. Essayez une qualité de maillage plus basse ou envoyez une pièce à la fois.",
        "unsupported_format": "PrintFlows accepte ces types de fichiers : {accepted}.",
        "update_required": "{server_message}Cette version du module est trop ancienne. Obtenez la nouvelle ici : {download_url}",
        "rate_limited": "Trop d'envois en peu de temps. Réessayez dans {retry_after} secondes.",
        "server_error": "PrintFlows a eu un problème de son côté. Réessayez dans quelques minutes.",
        "busy": "PrintFlows est très occupé en ce moment. Réessayez plus tard.",
        "offline": "Impossible de joindre PrintFlows. Vérifiez votre connexion Internet et réessayez.",
        "timeout": "PrintFlows a mis trop de temps à répondre. Vérifiez votre connexion et réessayez.",
        "tls": "Impossible d'ouvrir une connexion sécurisée avec PrintFlows. Vérifiez la date et l'heure de votre ordinateur, et qu'aucun proxy ni antivirus ne bloque printflows.org.",
        "size_mismatch": "Le fichier a changé pendant l'envoi. Réessayez.",
        "upload_failed": "L'envoi ne s'est pas terminé (code {status}). Réessayez.",
        "bad_response": "PrintFlows a envoyé une réponse que ce module ne comprend pas. Mettez le module à jour.",
        "file_error": "Impossible de lire le fichier exporté : {detail}",
        "cancelled": "L'envoi a été annulé.",
        "bad_base_url": "PRINTFLOWS_BASE_URL vaut {detail}, ce qui n'est pas permis. Il faut une adresse https, ou http sur cet ordinateur (localhost, 127.0.0.1 ou ::1). Supprimez la variable pour envoyer vers printflows.org.",
    },
}

ERROR_CODES = tuple(_MESSAGES["en"].keys())

# Variants used when the server leaves out a value the main text needs.
_VARIANTS = {
    "en": {
        "rate_limited": "Too many sends in a short time. Please try again in a few minutes.",
        "update_required": "{server_message}This version of the add-in is too old. The help page explains how to update: {download_url}",
        "update_required_nolink": "{server_message}This version of the add-in is too old. Please install the latest version.",
    },
    "fr": {
        "rate_limited": "Trop d'envois en peu de temps. Réessayez dans quelques minutes.",
        "update_required": "{server_message}Cette version du module est trop ancienne. La page d'aide explique comment la mettre à jour : {download_url}",
        "update_required_nolink": "{server_message}Cette version du module est trop ancienne. Installez la dernière version.",
    },
}

_STATUS_TO_CODE = {
    400: "invalid",
    413: "too_large",
    415: "unsupported_format",
    426: "update_required",
    429: "rate_limited",
}


def normalize_locale(value: object) -> str:
    """Return "fr" for any French locale string ("fr", "fr-CA", "fr_FR"), else "en"."""
    if isinstance(value, str) and value.strip().lower()[:2] == "fr":
        return "fr"
    return "en"


def _human_size(num: int | None, locale: str) -> str:
    if num is None:
        return "?"
    value = float(num)
    units = ("B", "KB", "MB", "GB") if locale == "en" else ("o", "Ko", "Mo", "Go")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    text = ("%d" % value) if unit in ("B", "o") else ("%.1f" % value)
    if locale == "fr":
        text = text.replace(".", ",")
    return "%s %s" % (text, unit)


def message_for(code: str, locale: str = "en", **values: object) -> str:
    """Friendly message for an error code in English or French."""
    table = _MESSAGES[normalize_locale(locale)]
    template = table.get(code) or table["bad_response"]
    safe = _SafeDict(values)
    return template.format_map(safe).strip()


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "?"


# --------------------------------------------------------------------------
# Errors and results
# --------------------------------------------------------------------------


class HandoffError(Exception):
    """A handoff failed. ``code`` is the server's error code or a local one.

    Server codes: invalid, too_large, unsupported_format, update_required,
    rate_limited, busy (503: daily cap reached), server_error. Local codes: offline, timeout, tls,
    size_mismatch, upload_failed, bad_response, file_error, cancelled.
    """

    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        status: int | None = None,
        payload: dict | None = None,
    ):
        self.code = code
        self.detail = detail
        self.status = status
        self.payload = dict(payload or {})
        super().__init__("%s%s" % (code, (": " + detail) if detail else ""))

    @property
    def field(self) -> str | None:
        value = self.payload.get("field")
        return value if isinstance(value, str) else None

    @property
    def max_bytes(self) -> int | None:
        value = self.payload.get("maxBytes")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def accepted(self) -> list[str]:
        value = self.payload.get("accepted")
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return list(value)
        return list(ACCEPTED_EXTENSIONS)

    @property
    def retry_after(self) -> int | None:
        value = self.payload.get("retryAfter")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        ):
            return int(value)
        return None

    @property
    def download_url(self) -> str | None:
        value = self.payload.get("downloadUrl")
        return value if isinstance(value, str) else None

    @property
    def server_message(self) -> str | None:
        value = self.payload.get("message")
        return value if isinstance(value, str) and value.strip() else None

    def message(self, locale: str = "en") -> str:
        """Friendly text for this error, in "en" or "fr"."""
        loc = normalize_locale(locale)
        server_message = ""
        if self.code == "update_required" and self.server_message:
            server_message = self.server_message.rstrip() + "\n\n"
        size = self.payload.get("sizeBytes")
        values = dict(
            field=self.field or "?",
            size=_human_size(size if isinstance(size, int) else None, loc),
            limit=_human_size(self.max_bytes or MAX_BYTES, loc),
            accepted=" ".join(self.accepted),
            server_message=server_message,
            download_url=self.download_url or "",
            retry_after=self.retry_after,
            status=self.status if self.status is not None else "?",
            detail=self.detail or "?",
        )
        variants = _VARIANTS[loc]
        if self.code == "rate_limited" and self.retry_after is None:
            return variants["rate_limited"]
        if self.code == "update_required" and not self.download_url:
            if HELP_URL:
                values["download_url"] = HELP_URL
                return variants["update_required"].format_map(_SafeDict(values)).strip()
            return (
                variants["update_required_nolink"].format_map(_SafeDict(values)).strip()
            )
        return message_for(self.code, loc, **values)


class Handoff:
    """The server's answer to a handoff request."""

    __slots__ = (
        "upload_url",
        "upload_method",
        "upload_headers",
        "open_url",
        "expires_at",
    )

    def __init__(
        self,
        upload_url: str,
        upload_method: str,
        upload_headers: dict,
        open_url: str,
        expires_at: str | None,
    ):
        self.upload_url = upload_url
        self.upload_method = upload_method
        self.upload_headers = dict(upload_headers)
        self.open_url = open_url
        self.expires_at = expires_at

    def content_length(self) -> int | None:
        for key, value in self.upload_headers.items():
            if key.lower() == "content-length":
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None

    def __repr__(self) -> str:
        return "Handoff(open_url=%r, expires_at=%r)" % (self.open_url, self.expires_at)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def base_url(explicit: str | None = None) -> str:
    """The API base: ``explicit``, else $PRINTFLOWS_BASE_URL, else https://printflows.org.

    Only https on any host, or http on localhost / 127.0.0.1 / ::1, is accepted;
    anything else raises HandoffError("bad_base_url").
    """
    value = (
        (explicit or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL)
        .strip()
        .rstrip("/")
    )
    try:
        parts = urllib.parse.urlsplit(value)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        raise HandoffError("bad_base_url", value) from None
    ok = (
        bool(host)
        and not parts.username
        and not parts.password
        and not parts.query
        and not parts.fragment
        and (
            parts.scheme == "https" or (parts.scheme == "http" and host in _LOCAL_HOSTS)
        )
    )
    if not ok:
        raise HandoffError("bad_base_url", value)
    return value


def base_url_override() -> str | None:
    """The PRINTFLOWS_BASE_URL value when one is set (for showing it to the user), else None."""
    value = (os.environ.get(BASE_URL_ENV) or "").strip()
    return value or None


def user_agent(source: str, version: str) -> str:
    return "PrintFlows-%s/%s" % (source, version)


def check_file(path: str) -> int:
    """Check a file before sending: it exists, has an accepted extension and fits the limit.

    Returns its size in bytes. Raises HandoffError("file_error" | "unsupported_format" | "too_large").
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in ACCEPTED_EXTENSIONS:
        raise HandoffError(
            "unsupported_format",
            ext or "(none)",
            payload={"accepted": list(ACCEPTED_EXTENSIONS)},
        )
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise HandoffError("file_error", str(exc)) from exc
    if size <= 0:
        raise HandoffError("file_error", "empty file")
    if size > MAX_BYTES:
        raise HandoffError(
            "too_large", str(size), payload={"maxBytes": MAX_BYTES, "sizeBytes": size}
        )
    return size


def _origin(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme.lower(), parts.netloc.lower()


def _upload_url_allowed(upload_url: str, base: str) -> bool:
    """https on an R2 storage host, or the API base's own scheme and host (the local mock)."""
    try:
        up = urllib.parse.urlsplit(upload_url)
        bs = urllib.parse.urlsplit(base)
        up_host = (up.hostname or "").lower()
        base_host = (bs.hostname or "").lower()
    except ValueError:
        return False
    if not up_host or up.username or up.password:
        return False
    if up.scheme == "https" and up_host.endswith(_UPLOAD_HOST_SUFFIX):
        return True
    return (
        up_host == base_host
        and up.scheme == bs.scheme
        and up.scheme in ("https", "http")
    )


def _is_reset(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            http.client.RemoteDisconnected,
        ),
    ):
        return True
    if isinstance(exc, urllib.error.URLError) and not isinstance(
        exc, urllib.error.HTTPError
    ):
        return _is_reset(exc.reason) if isinstance(exc.reason, BaseException) else False
    return False


def _cert_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    if isinstance(exc, urllib.error.URLError) and not isinstance(
        exc, urllib.error.HTTPError
    ):
        return isinstance(exc.reason, ssl.SSLCertVerificationError)
    return False


def _tls_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, urllib.error.URLError) and not isinstance(
        exc, urllib.error.HTTPError
    ):
        return isinstance(exc.reason, ssl.SSLError)
    return False


def _timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError) and not isinstance(
        exc, urllib.error.HTTPError
    ):
        return isinstance(exc.reason, (TimeoutError, socket.timeout))
    return False


def find_curl() -> str | None:
    """The system curl, which uses the operating system's certificate store."""
    if sys.platform == "darwin":
        return "/usr/bin/curl" if os.path.exists("/usr/bin/curl") else None
    if os.name == "nt":
        system = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe"
        )
        if os.path.exists(system):
            return system
        return shutil.which("curl.exe")
    return shutil.which("curl")


class _Response:
    __slots__ = ("status", "headers", "body")

    def __init__(self, status: int, headers: dict, body: bytes):
        self.status = status
        self.headers = {str(k).lower(): str(v) for k, v in headers.items()}
        self.body = body


class _CountingReader:
    """File reader for http.client: sends exactly ``total`` bytes and reports progress."""

    def __init__(self, handle, total: int, progress):
        self._handle = handle
        self._total = total
        self._sent = 0
        self._reported = 0
        self._progress = progress

    def read(self, amount: int = -1) -> bytes:
        remaining = self._total - self._sent
        if remaining <= 0:
            return b""
        if amount is None or amount < 0 or amount > remaining:
            amount = remaining
        chunk = self._handle.read(amount)
        if not chunk:
            raise HandoffError(
                "size_mismatch",
                "file ended after %d of %d bytes" % (self._sent, self._total),
            )
        self._sent += len(chunk)
        if self._progress is not None and (
            self._sent - self._reported >= PROGRESS_STEP or self._sent == self._total
        ):
            self._reported = self._sent
            self._progress(self._sent, self._total)
        return chunk


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class HandoffClient:
    """Talks to one PrintFlows server.

    ``source`` names the CAD program ("fusion", "freecad", "blender") and
    ``source_version`` the add-in version; both go in the User-Agent.
    ``urlopen`` and ``curl_runner`` exist for tests.
    """

    def __init__(
        self,
        source: str,
        source_version: str,
        base: str | None = None,
        *,
        timeout: float = REQUEST_TIMEOUT,
        upload_timeout: float = UPLOAD_TIMEOUT,
        urlopen=None,
        curl_runner=None,
    ):
        self.source = source
        self.source_version = source_version
        self.base_url = base_url(base)
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self.user_agent = user_agent(source, source_version)
        self._urlopen = urlopen or urllib.request.urlopen
        self._curl_runner = curl_runner or subprocess.run
        self._context = ssl.create_default_context()
        self.using_curl = (
            False  # set after a certificate error, for the rest of this client's life
        )

    # -- transport --------------------------------------------------------

    def _send(
        self,
        method: str,
        url: str,
        headers: dict,
        *,
        body: bytes | None = None,
        file_path: str | None = None,
        total: int = 0,
        progress=None,
        timeout: float,
    ) -> _Response:
        """One HTTP exchange, with one retry on a connection reset and the curl fallback on a certificate error."""
        attempts = 0
        while True:
            attempts += 1
            try:
                if self.using_curl:
                    return self._send_curl(
                        method,
                        url,
                        headers,
                        body=body,
                        file_path=file_path,
                        total=total,
                        progress=progress,
                        timeout=timeout,
                    )
                return self._send_urllib(
                    method,
                    url,
                    headers,
                    body=body,
                    file_path=file_path,
                    total=total,
                    progress=progress,
                    timeout=timeout,
                )
            except HandoffError:
                raise
            except Exception as exc:  # noqa: BLE001 - mapped to HandoffError below
                if _cert_error(exc) and not self.using_curl:
                    self.using_curl = True
                    attempts -= 1  # the curl try is the "retry once", not a reset retry
                    continue
                if _is_reset(exc) and attempts < 2:
                    continue
                raise self._map_exception(exc) from exc

    @staticmethod
    def _map_exception(exc: BaseException) -> HandoffError:
        if _timeout_error(exc):
            return HandoffError("timeout", str(exc))
        if _tls_error(exc):
            return HandoffError("tls", str(exc))
        if isinstance(exc, (urllib.error.URLError, OSError, http.client.HTTPException)):
            return HandoffError("offline", str(exc))
        return HandoffError("bad_response", "%s: %s" % (type(exc).__name__, exc))

    def _send_urllib(
        self, method, url, headers, *, body, file_path, total, progress, timeout
    ) -> _Response:
        handle = None
        try:
            data = body
            if file_path is not None:
                try:
                    handle = open(file_path, "rb")
                except OSError as exc:
                    raise HandoffError("file_error", str(exc)) from exc
                if progress is not None:
                    progress(0, total)
                data = _CountingReader(handle, total, progress)
            request = urllib.request.Request(
                url, data=data, method=method, headers=headers
            )
            kwargs = {"timeout": timeout}
            if url.lower().startswith("https:"):
                kwargs["context"] = self._context
            try:
                resp = self._urlopen(request, **kwargs)
            except urllib.error.HTTPError as err:
                try:
                    payload = err.read()
                except Exception:  # noqa: BLE001
                    payload = b""
                finally:
                    err.close()
                return _Response(err.code, dict(err.headers or {}), payload)
            with resp:
                return _Response(resp.status, dict(resp.headers), resp.read())
        finally:
            if handle is not None:
                handle.close()

    def _send_curl(
        self, method, url, headers, *, body, file_path, total, progress, timeout
    ) -> _Response:
        curl = find_curl()
        if curl is None:
            raise HandoffError(
                "tls", "certificate check failed and no system curl was found"
            )
        work = tempfile.mkdtemp(prefix="printflows-curl-")
        try:
            out_path = os.path.join(work, "body")
            head_path = os.path.join(work, "headers")
            args = [
                curl,
                "--disable",  # must come first: ignore every .curlrc / _curlrc
                "--silent",
                "--show-error",
                "--globoff",
                "--proto",
                "=https,http",
                "--proto-redir",
                "=https",
                "--tlsv1.2",
                "--connect-timeout",
                str(int(max(1, min(timeout, 30)))),
                "--request",
                method,
                "--output",
                out_path,
                "--dump-header",
                head_path,
                "--write-out",
                "%{http_code}",
            ]
            if file_path is None:
                args += ["--max-time", str(int(max(1, timeout)))]
            else:
                # An upload may take long; give up only when it stalls.
                args += [
                    "--speed-limit",
                    "1",
                    "--speed-time",
                    str(int(max(1, timeout))),
                ]
            for key, value in headers.items():
                args += ["--header", "%s: %s" % (key, value)]
            # Send exactly the given headers plus User-Agent: drop curl's own Expect and Accept.
            args += ["--header", "Expect:"]
            if not any(k.lower() == "accept" for k in headers):
                args += ["--header", "Accept:"]
            stdin = None
            if body is not None:
                args += ["--data-binary", "@-"]
                stdin = body
            if file_path is not None:
                args += ["--upload-file", file_path]
                if progress is not None:
                    progress(0, total)
            args.append(url)
            kwargs = {
                "input": stdin if stdin is not None else b"",
                "capture_output": True,
            }
            kwargs["env"] = {
                k: v for k, v in os.environ.items() if k.upper() not in _CURL_ENV_DROP
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = self._curl_runner(args, **kwargs)
            code = result.returncode
            err_text = (result.stderr or b"").decode("utf-8", "replace").strip()
            if code in _CURL_RESET_EXITS:
                raise ConnectionResetError("curl exit %d: %s" % (code, err_text))
            if code in _CURL_TLS_EXITS:
                raise HandoffError("tls", "curl exit %d: %s" % (code, err_text))
            if code in _CURL_TIMEOUT_EXITS:
                raise HandoffError("timeout", "curl exit %d: %s" % (code, err_text))
            if code in _CURL_OFFLINE_EXITS:
                raise HandoffError("offline", "curl exit %d: %s" % (code, err_text))
            if code != 0:
                raise HandoffError("offline", "curl exit %d: %s" % (code, err_text))
            try:
                status = int(
                    (result.stdout or b"").decode("ascii", "replace").strip()[-3:]
                )
            except ValueError as exc:
                raise HandoffError("bad_response", "curl gave no status") from exc
            resp_headers = {}
            if os.path.exists(head_path):
                with open(head_path, "rb") as fh:
                    for line in fh.read().decode("latin-1").splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            resp_headers[k.strip()] = v.strip()
            payload = b""
            if os.path.exists(out_path):
                with open(out_path, "rb") as fh:
                    payload = fh.read()
            if file_path is not None and progress is not None and 200 <= status < 300:
                progress(total, total)
            return _Response(status, resp_headers, payload)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # -- API ----------------------------------------------------------------

    def request_handoff(
        self, file_name: str, size_bytes: int, locale: str = "en"
    ) -> Handoff:
        """POST {base}/api/handoff. Returns a Handoff or raises HandoffError."""
        body = json.dumps(
            {
                "fileName": file_name,
                "sizeBytes": int(size_bytes),
                "source": self.source,
                "sourceVersion": self.source_version,
                "locale": normalize_locale(locale),
            }
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        resp = self._send(
            "POST",
            self.base_url + "/api/handoff",
            headers,
            body=body,
            timeout=self.timeout,
        )
        payload = _json_or_none(resp.body)
        if resp.status in (200, 201):
            return self._parse_handoff(payload)
        raise _error_from_response(resp, payload, size_bytes)

    def _parse_handoff(self, payload) -> Handoff:
        if not isinstance(payload, dict):
            # A proxy or captive portal page, not our API: treat it as the server failing.
            raise HandoffError("server_error", "answer is not a JSON object")
        upload_url = payload.get("uploadUrl")
        method = payload.get("uploadMethod", "PUT")
        headers = payload.get("uploadHeaders")
        open_url = payload.get("openUrl")
        expires = payload.get("expiresAt")
        if not isinstance(upload_url, str) or not isinstance(open_url, str):
            raise HandoffError("bad_response", "uploadUrl or openUrl missing")
        if method != "PUT":
            raise HandoffError("bad_response", "uploadMethod %r" % (method,))
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
        ):
            raise HandoffError(
                "bad_response", "uploadHeaders must map strings to strings"
            )
        lower = {k.lower() for k in headers}
        if "content-type" not in lower or "content-length" not in lower:
            raise HandoffError(
                "bad_response",
                "uploadHeaders must carry Content-Type and Content-Length",
            )
        base_scheme, base_host = _origin(self.base_url)
        if not _upload_url_allowed(upload_url, self.base_url):
            raise HandoffError("bad_response", "upload URL is not on an allowed host")
        open_scheme, open_host = _origin(open_url)
        same_site = (open_scheme, open_host) == (base_scheme, base_host)
        printflows_site = open_scheme == "https" and open_host in _SITE_HOSTS
        if not (same_site or printflows_site):
            raise HandoffError("bad_response", "openUrl points to another site")
        return Handoff(
            upload_url,
            method,
            headers,
            open_url,
            expires if isinstance(expires, str) else None,
        )

    def upload(self, handoff: Handoff, file_path: str, progress=None) -> None:
        """PUT the file to the signed URL, sending exactly the server's uploadHeaders.

        ``progress(sent_bytes, total_bytes)`` is called from this thread as the
        file streams. It may raise to stop the upload.
        """
        expected = handoff.content_length()
        try:
            actual = os.path.getsize(file_path)
        except OSError as exc:
            raise HandoffError("file_error", str(exc)) from exc
        if expected is None:
            raise HandoffError("bad_response", "uploadHeaders has no Content-Length")
        if not any(k.lower() == "content-type" for k in handoff.upload_headers):
            raise HandoffError("bad_response", "uploadHeaders has no Content-Type")
        if actual != expected:
            raise HandoffError(
                "size_mismatch",
                "file is %d bytes, server expects %d" % (actual, expected),
            )
        headers = dict(handoff.upload_headers)
        if not any(k.lower() == "user-agent" for k in headers):
            headers["User-Agent"] = self.user_agent
        resp = self._send(
            handoff.upload_method,
            handoff.upload_url,
            headers,
            file_path=file_path,
            total=expected,
            progress=progress,
            timeout=self.upload_timeout,
        )
        if resp.status == 413:
            raise HandoffError(
                "too_large",
                "storage answered 413",
                status=413,
                payload={"sizeBytes": expected, "maxBytes": MAX_BYTES},
            )
        if not 200 <= resp.status < 300:
            raise HandoffError(
                "upload_failed",
                resp.body[:200].decode("utf-8", "replace"),
                status=resp.status,
            )

    def send_file(
        self,
        file_path: str,
        locale: str = "en",
        progress=None,
        open_browser: bool = True,
        file_name: str | None = None,
    ) -> Handoff:
        """Check, request, upload and (optionally) open the browser. Returns the Handoff."""
        size = check_file(file_path)
        handoff = self.request_handoff(
            file_name or os.path.basename(file_path), size, locale
        )
        self.upload(handoff, file_path, progress)
        if open_browser:
            open_in_browser(handoff.open_url)
        return handoff


def _json_or_none(body: bytes):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _error_from_response(resp: _Response, payload, size_bytes: int) -> HandoffError:
    data = dict(payload) if isinstance(payload, dict) else {}
    code = data.get("error") if isinstance(data.get("error"), str) else None
    if code not in ERROR_CODES:
        code = _STATUS_TO_CODE.get(resp.status) or (
            "server_error" if resp.status >= 500 else "bad_response"
        )
    if code == "rate_limited" and "retryAfter" not in data:
        try:
            data["retryAfter"] = int(resp.headers.get("retry-after", ""))
        except ValueError:
            pass
    if code == "too_large":
        data.setdefault("sizeBytes", int(size_bytes))
    return HandoffError(code, "HTTP %d" % resp.status, status=resp.status, payload=data)


# --------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------


def request_handoff(
    file_name: str,
    size_bytes: int,
    *,
    source: str,
    source_version: str,
    locale: str = "en",
    base: str | None = None,
) -> Handoff:
    return HandoffClient(source, source_version, base).request_handoff(
        file_name, size_bytes, locale
    )


def upload(
    handoff: Handoff, file_path: str, progress=None, *, source: str, source_version: str
) -> None:
    HandoffClient(source, source_version).upload(handoff, file_path, progress)


def open_in_browser(url: str) -> bool:
    """Open ``url`` in the system browser. Safe from a worker thread."""
    try:
        return bool(webbrowser.open(url))
    except webbrowser.Error:
        return False


def send_file(
    file_path: str,
    *,
    source: str,
    source_version: str,
    locale: str = "en",
    progress=None,
    open_browser: bool = True,
    base: str | None = None,
) -> Handoff:
    return HandoffClient(source, source_version, base).send_file(
        file_path, locale, progress, open_browser
    )
