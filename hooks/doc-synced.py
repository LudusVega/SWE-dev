# -*- coding: utf-8 -*-
import sys
import os

lib_path = os.path.join(os.path.dirname(__file__), "..", "lib")
sys.path.append(os.path.abspath(lib_path))

from pyrevit import EXEC_PARAMS
import sync_queue as sq

event_args = EXEC_PARAMS.event_args
doc = event_args.Document

if not doc or not doc.IsWorkshared:
    raise SystemExit

username = ""
try:
    username = doc.Application.Username
except Exception:
    username = "Unknown"

model = doc.Title

try:
    sq.leave_queue(doc, username, model)
except Exception:
    pass