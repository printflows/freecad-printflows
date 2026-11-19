# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows. See LICENSE at the top of the add-on.
"""Consent and the interface language, both read from FreeCAD's parameters.

Nothing here touches the network. ``has_consent(base)`` is the gate every connection checks
first. Consent is stored per server, so a yes given in test mode (PRINTFLOWS_BASE_URL set)
never covers printflows.org. Call these on FreeCAD's GUI thread only.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from . import config


def _params():
    import FreeCAD

    return FreeCAD.ParamGet(config.PARAM_PATH)


def consent_key(base: str) -> str:
    """The server a consent answer belongs to: "scheme://host[:port]", lower case."""
    parts = urlsplit((base or "").strip())
    if not parts.scheme or not parts.netloc:
        raise ValueError("not a server address: %r" % (base,))
    return "%s://%s" % (parts.scheme.lower(), parts.netloc.lower())


def has_consent(base: str, params=None) -> bool:
    """True once the person has allowed ``base`` at the current consent version."""
    group = params if params is not None else _params()
    stored = group.GetGroup(config.PARAM_CONSENT_GROUP).GetInt(consent_key(base), 0)
    return stored >= config.CONSENT_VERSION


def set_consent(allowed: bool, base: str | None = None, params=None) -> None:
    """Allow ``base``, or withdraw consent for ``base`` (or for every server when None)."""
    group = params if params is not None else _params()
    if allowed:
        if not base:
            raise ValueError("consent is given for one server")
        group.GetGroup(config.PARAM_CONSENT_GROUP).SetInt(
            consent_key(base), config.CONSENT_VERSION
        )
    elif base:
        group.GetGroup(config.PARAM_CONSENT_GROUP).RemInt(consent_key(base))
    else:
        group.RemGroup(config.PARAM_CONSENT_GROUP)
        group.RemInt(config.PARAM_CONSENT)


def locale_from(language: str, supported: dict | None = None, system: str = "") -> str:
    """ "fr" or "en" from FreeCAD's Language parameter.

    FreeCAD stores the language by its English name ("French", "English"); FreeCADGui.supportedLocales()
    maps that name to a code ("fr"). An empty value means "follow the system", so the Qt system
    locale name ("fr_CA") decides.
    """
    value = (language or "").strip()
    if value and supported:
        value = supported.get(value, value)
    if not value:
        value = system or ""
    return "fr" if value.strip().lower()[:2] == "fr" else "en"


def current_locale() -> str:
    """The FreeCAD interface language as "fr" or "en". Never raises."""
    try:
        import FreeCAD

        language = FreeCAD.ParamGet(
            "User parameter:BaseApp/Preferences/General"
        ).GetString("Language", "")
    except Exception:  # noqa: BLE001
        return "en"
    supported = None
    system = ""
    try:
        import FreeCADGui

        supported = dict(FreeCADGui.supportedLocales())
    except Exception:  # noqa: BLE001 - console mode has no FreeCADGui
        pass
    try:
        from PySide import QtCore

        system = QtCore.QLocale.system().name()
    except Exception:  # noqa: BLE001
        pass
    return locale_from(language, supported, system)
