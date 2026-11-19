# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PrintFlows. See LICENSE at the top of the add-on.
"""FreeCAD GUI startup hook: registers the PrintFlows workbench.

Nothing heavy and nothing on the network happens here: the command module (Qt, export,
upload) loads only when the workbench is first activated, which is also when stale export
folders older than a day are removed from the temp folder.
"""

import FreeCADGui

from . import config, settings, strings


class PrintFlowsWorkbench(FreeCADGui.Workbench):
    MenuText = "PrintFlows"
    ToolTip = strings.text("wb_tooltip", settings.current_locale())
    Icon = config.ICON_PATH

    def Initialize(self):
        from . import commands  # registers config.CMD_ID and config.WITHDRAW_CMD_ID

        self.appendToolbar("PrintFlows", [config.CMD_ID])
        self.appendMenu("PrintFlows", [config.CMD_ID, config.WITHDRAW_CMD_ID])
        try:
            commands.sweep_stale_exports()
        except Exception:  # noqa: BLE001 - a cleanup failure must not stop the workbench
            pass

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(PrintFlowsWorkbench())
