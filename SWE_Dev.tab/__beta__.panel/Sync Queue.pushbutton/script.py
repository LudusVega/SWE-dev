# -*- coding: utf-8 -*-
import os
import sys

lib_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib")
lib_path = os.path.abspath(lib_path)
if lib_path not in sys.path:
    sys.path.append(lib_path)

import sync_queue_panel as panel
from pyrevit import forms
from Autodesk.Revit.UI import DockablePane

uiapp = __revit__

try:
    is_registered = DockablePane.PaneIsRegistered(panel.PANE_ID)
except Exception:
    is_registered = False

if not is_registered:
    forms.alert(
        "Sync Queue pane is not registered.\n\n"
        "1. Close Revit completely\n"
        "2. Reopen Revit\n"
        "3. Open the debug log:\n"
        "~\\pyRevitSyncLogs\\sync_queue_startup_debug.txt",
        title="Sync Queue"
    )
else:
    try:
        pane = uiapp.GetDockablePane(panel.PANE_ID)
        pane.Show()
    except Exception as e:
        forms.alert(
            "Pane is registered but could not be shown.\n\n{}".format(str(e)),
            title="Sync Queue"
        )