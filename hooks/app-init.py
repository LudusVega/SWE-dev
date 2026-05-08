# -*- coding: utf-8 -*-
import os
import sys
import traceback

debug_dir = os.path.expanduser(r"~\pyRevitSyncLogs")
if not os.path.exists(debug_dir):
    os.makedirs(debug_dir)
debug_file = os.path.join(debug_dir, "sync_queue_startup_debug.txt")

def log(msg):
    try:
        with open(debug_file, "a") as f:
            f.write(msg + "\n")
    except:
        pass

try:
    lib_path = os.path.join(os.path.dirname(__file__), "..", "lib")
    lib_path = os.path.abspath(lib_path)
    if lib_path not in sys.path:
        sys.path.append(lib_path)

    log("app-init started")
    log("lib_path = {}".format(lib_path))

    import sync_queue_panel as panel
    log("Imported sync_queue_panel")

    from Autodesk.Revit.UI import UIApplication, ExternalEvent
    log("Imported UIApplication and ExternalEvent")

    uiapp = UIApplication(__revit__)
    log("Created UIApplication")

    panel.PANE_INSTANCE.set_uiapp(uiapp)
    log("Set uiapp on pane instance")

    try:
        uiapp.RegisterDockablePane(
            panel.PANE_ID,
            "Sync Queue",
            panel.PANE_INSTANCE
        )
        log("RegisterDockablePane succeeded")
    except Exception as reg_ex:
        log("RegisterDockablePane failed: {}".format(str(reg_ex)))
        log(traceback.format_exc())

    try:
        panel.SYNC_EXTERNAL_EVENT = ExternalEvent.Create(panel.SYNC_HANDLER)
        log("ExternalEvent.Create succeeded")
    except Exception as ex_event:
        log("ExternalEvent.Create failed: {}".format(str(ex_event)))
        log(traceback.format_exc())

except Exception as ex:
    log("app-init fatal error: {}".format(str(ex)))
    log(traceback.format_exc())