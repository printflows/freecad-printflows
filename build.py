# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows.
"""Build the PrintFlows FreeCAD add-on.

    python build.py            copy the shared client in, write the icon, then build dist/
    python build.py --check    fail if the copied client or the icon is stale, or if the
                               version in config.py differs from package.xml

The shared client lives in the PrintFlows CAD repo at shared/printflows_handoff.py. Its sha256
is pinned below (CLIENT_SHA256, over LF line endings), and the copy in freecad/PrintFlows/ is
always checked against the pin, so the check guards the client in the add-on's own repo too,
where there is no shared/ folder. When shared/ is there, the copy must also equal it byte for
byte. --shared PATH names another source; a PATH that does not exist is an error.

Output (dist/, ignored by git): PrintFlows-<version>.zip, a PrintFlows/ folder to unzip into
FreeCAD's user Mod folder. Zips are deterministic (sorted entries, fixed timestamps).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.join(HERE, "freecad", "PrintFlows")
VENDORED_CLIENT = os.path.join(PACKAGE, "printflows_handoff.py")
DEFAULT_SHARED = os.path.join(os.path.dirname(HERE), "shared", "printflows_handoff.py")
ICON = os.path.join(PACKAGE, "resources", "icons", "PrintFlows.svg")
PACKAGE_XML = os.path.join(HERE, "package.xml")
CONFIG = os.path.join(PACKAGE, "config.py")
DIST = os.path.join(HERE, "dist")
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
NS = "{https://wiki.freecad.org/Package_Metadata}"

# sha256 of printflows_handoff.py with LF line endings (git blob 7f5ccd3d). Update it, in the
# same commit, whenever the shared client changes.
CLIENT_SHA256 = "30a4b2f59c396c122b11ede738d44939457fb470a6a6d6a5281f4c9344bd871d"

# Shipped in the zip, relative to HERE. Everything under freecad/ except caches.
TOP_FILES = ("package.xml", "LICENSE", "README.md")
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = (".pyc", ".pyo")

# The PrintFlows app icon: the white "nozzle tip" mark on the brand's teal rounded square,
# as in the brand kit's 512 px app icon. The mark's paths are the brand mark's own; the
# placement and the corner curve were fitted to the kit's PNG (in the PrintFlows CAD repo,
# tools/icon_check.py compares a render of this SVG with it). Never recolour: white on teal is the kit's app-icon treatment.
ICON_SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 512 512">\n'
    "  <title>PrintFlows</title>\n"
    '  <path fill="#12736b" d="M113 0H399C449.85 0 512 62.15 512 113V399C512 449.85 '
    '449.85 512 399 512H113C62.15 512 0 449.85 0 399V113C0 62.15 62.15 0 113 0Z"/>\n'
    '  <g fill="#ffffff" transform="translate(81.2 79.8) scale(5.462)">\n'
    '    <path fill-rule="evenodd" d="M25 40C22 36 17 30 17 21A15 15 0 0 1 47 21C47 30 '
    '42 36 39 40ZM37 21A5 5 0 1 0 27 21A5 5 0 1 0 37 21Z"/>\n'
    '    <path d="M27 41.6H37L34.2 47H29.8Z"/>\n'
    '    <path d="M16 48.5H31.5A2 2 0 0 1 33.5 50.5V50.5A2 2 0 0 1 31.5 52.5H16A2 2 0 0 '
    '1 14 50.5V50.5A2 2 0 0 1 16 48.5Z"/>\n'
    '    <path d="M14 54.5H50A2 2 0 0 1 52 56.5V56.5A2 2 0 0 1 50 58.5H14A2 2 0 0 1 12 '
    '56.5V56.5A2 2 0 0 1 14 54.5Z"/>\n'
    "  </g>\n"
    "</svg>\n"
)


def package_version() -> str:
    root = ET.parse(PACKAGE_XML).getroot()
    return root.find(NS + "version").text.strip()


def config_version() -> str:
    with open(CONFIG, encoding="utf-8") as fh:
        match = re.search(r'^VERSION = "([^"]+)"', fh.read(), re.M)
    return match.group(1) if match else ""


def _sync(path: str, data: bytes, check_only: bool) -> bool:
    current = None
    if os.path.exists(path):
        with open(path, "rb") as fh:
            current = fh.read()
    if current == data:
        return True
    if check_only:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return True


def client_sha256(data: bytes) -> str:
    """sha256 of the client over LF line endings (git autocrlf may check it out with CRLF)."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def vendor(shared: str | None, check_only: bool = False) -> bool | None:
    """Copy the shared client in. True when they match, None when there is no shared copy."""
    if not shared or not os.path.exists(shared):
        return None
    with open(shared, "rb") as fh:
        return _sync(VENDORED_CLIENT, fh.read(), check_only)


def icon(check_only: bool = False) -> bool:
    # Compared without line endings: git's autocrlf may check the SVG out with CRLF.
    if os.path.exists(ICON):
        with open(ICON, "rb") as fh:
            if fh.read().replace(b"\r\n", b"\n") == ICON_SVG.encode("utf-8"):
                return True
    return _sync(ICON, ICON_SVG.encode("utf-8"), check_only)


def check(shared: str | None, explicit: bool = False) -> list[str]:
    problems = []
    if explicit and not os.path.exists(shared or ""):
        problems.append("--shared %s: file not found" % shared)
    vendored = vendor(shared, check_only=True)
    if vendored is False:
        problems.append(
            "freecad/PrintFlows/printflows_handoff.py differs from %s; run build.py"
            % shared
        )
    if not os.path.exists(VENDORED_CLIENT):
        problems.append(
            "freecad/PrintFlows/printflows_handoff.py is missing; run build.py"
        )
    else:
        with open(VENDORED_CLIENT, "rb") as fh:
            digest = client_sha256(fh.read())
        if digest != CLIENT_SHA256:
            problems.append(
                "freecad/PrintFlows/printflows_handoff.py sha256 %s differs from the pinned "
                "CLIENT_SHA256 %s; copy the shared client in again, or update the pin when "
                "the shared client changed" % (digest, CLIENT_SHA256)
            )
    if not icon(check_only=True):
        problems.append("resources/icons/PrintFlows.svg is stale; run build.py")
    if package_version() != config_version():
        problems.append(
            "package.xml version %s differs from config.VERSION %s"
            % (package_version(), config_version())
        )
    return problems


def shipped_files() -> list[tuple[str, str]]:
    out = [(os.path.join(HERE, name), name) for name in TOP_FILES]
    for folder, dirs, files in os.walk(os.path.join(HERE, "freecad")):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(SKIP_SUFFIXES):
                continue
            full = os.path.join(folder, name)
            out.append((full, os.path.relpath(full, HERE).replace(os.sep, "/")))
    return out


def build(shared: str | None, dist: str = DIST, explicit: bool = False) -> str:
    if explicit and not os.path.exists(shared or ""):
        raise SystemExit("--shared %s: file not found" % shared)
    vendor(shared)
    icon()
    problems = check(shared, explicit)
    if problems:
        raise SystemExit("\n".join(problems))
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, "PrintFlows-%s.zip" % package_version())
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, rel in sorted(shipped_files(), key=lambda item: item[1]):
            with open(full, "rb") as fh:
                data = fh.read()
            info = zipfile.ZipInfo("PrintFlows/" + rel, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the PrintFlows FreeCAD add-on.")
    parser.add_argument(
        "--check", action="store_true", help="only check, change nothing"
    )
    parser.add_argument(
        "--shared",
        default=None,
        help="path to shared/printflows_handoff.py (default: ../shared, when present)",
    )
    parser.add_argument("--dist", default=DIST)
    args = parser.parse_args(argv)
    explicit = args.shared is not None
    if not explicit:
        args.shared = DEFAULT_SHARED
    if args.check:
        problems = check(args.shared, explicit)
        for line in problems:
            print(line, file=sys.stderr)
        if problems:
            return 1
        source = (
            args.shared if os.path.exists(args.shared) else "(no shared copy found)"
        )
        print(
            "ok: client matches the pinned sha256 and %s, icon current, version %s"
            % (source, package_version())
        )
        return 0
    print(build(args.shared, args.dist, explicit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
