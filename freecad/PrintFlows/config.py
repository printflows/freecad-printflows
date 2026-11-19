# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows. See LICENSE at the top of the add-on.
"""Ids and constants for the PrintFlows workbench. Imports only os, so loading stays cheap."""

import os

# Must equal <version> in package.xml (build.py --check fails otherwise). Sent as sourceVersion.
VERSION = "1.0.0"
SOURCE = "freecad"

WORKBENCH_NAME = "PrintFlows"
CMD_ID = "PrintFlows_Print"
WITHDRAW_CMD_ID = "PrintFlows_Withdraw"

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(PACKAGE_DIR, "resources", "icons", "PrintFlows.svg")

# Where consent lives (Tools > Edit parameters shows it under Preferences/Mod/PrintFlows/Consent):
# one integer per server, named by its address ("https://printflows.org"), holding the consent
# version the person allowed. A yes for one server never covers another.
PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/PrintFlows"
PARAM_CONSENT_GROUP = "Consent"
# The first test build stored one integer here for every server. It no longer counts as
# consent; withdrawing removes it.
PARAM_CONSENT = "ConsentVersion"
# Raise this when what the add-on sends, or where it sends it, changes: everyone is asked again.
CONSENT_VERSION = 1

# Export folders (TEMP_PREFIX*) older than this are left over from a FreeCAD that quit
# mid-upload, and are removed when the workbench starts.
STALE_TEMP_SECONDS = 24 * 3600

BASE_URL_ENV = (
    "PRINTFLOWS_BASE_URL"  # testing only; same name as printflows_handoff.BASE_URL_ENV
)
TEMP_PREFIX = "PrintFlows-"
