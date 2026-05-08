# -*- coding: utf-8 -*-
import os
import glob

PROJECTS_ROOT = os.path.realpath(r"//SPR-NAS/Company/Projects")


def get_swe_project_number(doc):
    if not doc:
        return None

    try:
        project_info = doc.ProjectInformation
        for param in project_info.Parameters:
            try:
                if param.Definition and param.Definition.Name == "SWE Project Number":
                    value = param.AsString()
                    if value:
                        value = value.strip()
                        if value:
                            return value
            except Exception:
                pass
    except Exception:
        pass

    return None


def get_project_root(doc):
    project_number = get_swe_project_number(doc)
    if not project_number:
        return None
    return os.path.join(PROJECTS_ROOT, project_number)


def get_cad_folder(doc):
    project_root = get_project_root(doc)
    if not project_root or not os.path.exists(project_root):
        return None

    matches = [
        p for p in glob.glob(os.path.join(project_root, "10 CAD*"))
        if os.path.isdir(p)
    ]

    if not matches:
        return None

    matches.sort()
    return matches[0]


def get_project_json_path(doc, filename):
    cad_folder = get_cad_folder(doc)
    if not cad_folder:
        return None
    return os.path.join(cad_folder, filename)


def get_dashboard_json_path(doc):
    title = doc.Title if doc else "Project"
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in title)
    return get_project_json_path(doc, safe + "_coordination_dashboard.json")


def get_sync_queue_json_path(doc):
    return get_project_json_path(doc, "sync_queue.json")