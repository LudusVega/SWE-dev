# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import io
import json
import glob
import clr
import datetime
import sys

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')
clr.AddReference('System.Xaml')

from System import DateTime
from System.Windows.Markup import XamlReader
from System.Windows.Controls import (
    DataGridTextColumn,
    DataGridComboBoxColumn,
    DataGridCheckBoxColumn,
    DataGridLength,
    DataGridTemplateColumn
)
from System.Windows.Data import Binding
from System.Collections.ObjectModel import ObservableCollection

from Autodesk.Revit.DB import FilteredElementCollector, ViewSheet
from pyrevit import revit, forms

script_dir = os.path.dirname(EXEC_PARAMS.command_path)
extension_root = os.path.abspath(
    os.path.join(script_dir, "..", "..", "..")
)
lib_dir = os.path.join(extension_root, "lib")

if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

import project_paths

__title__ = 'Dashboard'
__author__ = 'Evelyn Lutz'
__doc__ = """
Project stage coordination dashboard with revision-level and 
sheet-level marker colors, revision-specific completion tracking, 
narrative tracking, completed-by users, completed timestamps, 
pencils down fields, narrative file paths, browse/open narrative buttons, 
revision highlighting, and inline add-stage support.
"""

DOC = revit.doc
json_path = project_paths.get_dashboard_json_path(doc)

if not json_path:
    forms.alert(
        "Dashboard launch cancelled.\nNo folder was selected.",
        exitscript=True
    )

STATUS_OPTIONS = ['Not Started', 'In Progress', 'On Track', 'Needs Input', 'At Risk', 'Complete']
DISC_KEYS = ['M', 'E', 'P']


class StageRow(object):
    def __init__(self, data):
        self.id = data.get('id', '')
        self.name = data.get('name', '')
        self.target_date = data.get('target_date', '')
        self.actual_date = data.get('actual_date', '')
        self.pencils_down = data.get('pencils_down', '')
        self.status = data.get('status', 'Not Started')
        self.owner = data.get('owner', '')
        self.notes = data.get('notes', '')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'target_date': self.target_date,
            'actual_date': self.actual_date,
            'pencils_down': self.pencils_down,
            'status': self.status,
            'owner': self.owner,
            'notes': self.notes,
        }


class RevisionRow(object):
    def __init__(self, data):
        self.key = data.get('key', '')
        self.sequence = data.get('sequence', '')
        self.number = data.get('number', '')
        self.date = data.get('date', '')
        self.pencils_down = data.get('pencils_down', '')
        self.description = data.get('description', '')
        self.narrative = data.get('narrative', '')
        self.sheet_count = data.get('sheet_count', 0)
        self.marker_state = data.get('marker_state', 'red')

    def to_dict(self):
        return {
            'key': self.key,
            'sequence': self.sequence,
            'number': self.number,
            'date': self.date,
            'pencils_down': self.pencils_down,
            'description': self.description,
            'narrative': self.narrative,
            'sheet_count': self.sheet_count,
            'marker_state': self.marker_state
        }


class SheetRow(object):
    def __init__(self, data):
        self.revision_key = data.get('revision_key', '')
        self.sheet_number = data.get('sheet_number', '')
        self.sheet_name = data.get('sheet_name', '')
        self.current_revision = data.get('current_revision', '')
        self.current_revision_date = data.get('current_revision_date', '')
        self.revisions_complete = bool(data.get('revisions_complete', False))
        self.narrative_complete = bool(data.get('narrative_complete', False))
        self.completed_by = data.get('completed_by', '')
        self.completed_on = data.get('completed_on', '')
        self.marker_state = self._compute_marker_state()

    def _compute_marker_state(self):
        rev = bool(self.revisions_complete)
        nar = bool(self.narrative_complete)
        if rev and nar:
            return 'green'
        elif rev or nar:
            return 'orange'
        return 'red'

    def refresh_marker(self):
        self.marker_state = self._compute_marker_state()

    def to_dict(self):
        return {
            'revision_key': self.revision_key,
            'sheet_number': self.sheet_number,
            'sheet_name': self.sheet_name,
            'current_revision': self.current_revision,
            'current_revision_date': self.current_revision_date,
            'revisions_complete': self.revisions_complete,
            'narrative_complete': self.narrative_complete,
            'completed_by': self.completed_by,
            'completed_on': self.completed_on
        }


def _current_username():
    return os.environ.get('USERNAME', '') or os.environ.get('USER', '') or ''


def _current_timestamp():
    return datetime.datetime.now().isoformat(timespec='seconds')


def _get_project_number_from_info(doc):
    try:
        project_info = doc.ProjectInformation
        for param in project_info.Parameters:
            if param.Definition and param.Definition.Name == 'SWE Project Number':
                value = param.AsString()
                if value:
                    return value.strip()
    except Exception:
        pass
    return ''


def _get_project_folder_from_project_info(doc):
    base_path = os.path.realpath('//SPR-NAS/Company/Projects/')

    if not doc:
        forms.alert('No Revit document open.', exitscript=True)

    swe_project_number = _get_project_number_from_info(doc)

    if not swe_project_number:
        forms.alert(
            'SWE Project Number must be included in Project Information '
            'for this dashboard to save to the project folder.',
            exitscript=True
        )

    project_root = os.path.join(base_path, swe_project_number)

    if not os.path.exists(project_root):
        forms.alert(
            'Project root folder does not exist:\n{}'.format(project_root),
            exitscript=True
        )

    cad_matches = [
        p for p in glob.glob(os.path.join(project_root, '10 CAD*'))
        if os.path.isdir(p)
    ]

    if not cad_matches:
        forms.alert(
            "Could not find a folder matching '10 CAD*' under:\n{}".format(project_root),
            exitscript=True
        )

    cad_matches.sort()
    return cad_matches[0]


def _default_json_path(doc):
    title = doc.Title if doc else 'Project'
    safe = ''.join(c if c.isalnum() or c in ('-', '_') else '_' for c in title)
    project_folder = _get_project_folder_from_project_info(doc)
    return os.path.join(project_folder, safe + '_coordination_dashboard.json')


def _sheet_metrics(doc):
    sheets = list(FilteredElementCollector(doc).OfClass(ViewSheet).ToElements())
    total = len([s for s in sheets if not s.IsPlaceholder])
    complete = 0
    issued = 0

    for s in sheets:
        if s.IsPlaceholder:
            continue

        done = False
        for pname in ['Drawing Complete', 'Sheet Complete', 'Complete']:
            p = s.LookupParameter(pname)
            if p:
                try:
                    if p.StorageType.ToString() == 'Integer' and p.AsInteger() == 1:
                        done = True
                        break
                    sval = p.AsString()
                    if sval and sval.strip().lower() in ['yes', 'true', 'complete', 'completed']:
                        done = True
                        break
                except Exception:
                    pass

        if done:
            complete += 1

        try:
            if s.GetAllRevisionIds().Count > 0:
                issued += 1
        except Exception:
            pass

    return {
        'total_sheets': total,
        'complete_sheets': complete,
        'issued_sheets': issued
    }


def _get_revision_status(data, rev_key, sheet_number):
    status_root = data.get('sheet_revision_status', {})
    rev_bucket = status_root.get(str(rev_key), {})
    return rev_bucket.get(sheet_number, {})


def _compute_revision_marker(sheet_rows):
    if not sheet_rows:
        return 'red'

    total = len(sheet_rows)
    green_count = 0

    for row in sheet_rows:
        try:
            row.refresh_marker()
            if row.marker_state == 'green':
                green_count += 1
        except Exception:
            pass

    if green_count == total and total > 0:
        return 'green'
    elif green_count > 0:
        return 'orange'
    return 'red'


def _collect_revision_data(doc, data):
    rev_map = {}
    rev_sheet_map = {}

    if not doc:
        return [], {}

    revision_metadata = data.get('revision_metadata', {})
    sheets = list(FilteredElementCollector(doc).OfClass(ViewSheet).ToElements())

    for sheet in sheets:
        if sheet.IsPlaceholder:
            continue

        try:
            rev_ids = sheet.GetAllRevisionIds()
        except Exception:
            rev_ids = []

        for rev_id in rev_ids:
            rev = doc.GetElement(rev_id)
            if not rev:
                continue

            key = str(rev.Id.IntegerValue)
            if key not in rev_map:
                saved_meta = revision_metadata.get(key, {})
                rev_map[key] = {
                    'key': key,
                    'sequence': '',
                    'number': '',
                    'date': '',
                    'pencils_down': saved_meta.get('pencils_down', ''),
                    'description': '',
                    'narrative': saved_meta.get('narrative', ''),
                    'sheet_count': 0,
                    'marker_state': 'red'
                }
                rev_sheet_map[key] = []

                try:
                    rev_map[key]['sequence'] = str(rev.SequenceNumber)
                except Exception:
                    pass

                try:
                    rev_map[key]['number'] = rev.RevisionNumber or ''
                except Exception:
                    pass

                try:
                    rev_map[key]['date'] = rev.RevisionDate or ''
                except Exception:
                    pass

                try:
                    rev_map[key]['description'] = rev.Description or ''
                except Exception:
                    pass

            rev_map[key]['sheet_count'] += 1
            sheet_status = _get_revision_status(data, key, sheet.SheetNumber or '')

            rev_sheet_map[key].append(SheetRow({
                'revision_key': key,
                'sheet_number': sheet.SheetNumber or '',
                'sheet_name': sheet.Name or '',
                'current_revision': rev_map[key]['number'],
                'current_revision_date': rev_map[key]['date'],
                'revisions_complete': sheet_status.get('complete', False),
                'narrative_complete': sheet_status.get('narrative_complete', sheet_status.get('narrative', False)),
                'completed_by': sheet_status.get('completed_by', ''),
                'completed_on': sheet_status.get('completed_on', '')
            }))

    for key, sheet_rows in rev_sheet_map.items():
        rev_map[key]['marker_state'] = _compute_revision_marker(sheet_rows)

    rows = [RevisionRow(v) for v in rev_map.values()]

    def _sort_key(r):
        try:
            return int(r.sequence)
        except Exception:
            return 999999

    rows.sort(key=_sort_key)

    for key in rev_sheet_map:
        rev_sheet_map[key].sort(key=lambda x: (x.sheet_number, x.sheet_name))

    return rows, rev_sheet_map


def create_default_dashboard(doc):
    metrics = _sheet_metrics(doc)
    return {
        'project': {
            'name': doc.Title if doc else '',
            'number': _get_project_number_from_info(doc),
            'model_path': '',
            'last_updated': DateTime.Now.ToString('s')
        },
        'summary': metrics,
        'stages': [
            {'id': 'SD', 'name': 'Schematic Design', 'target_date': '', 'actual_date': '', 'pencils_down': '', 'status': 'Not Started', 'owner': '', 'notes': ''},
            {'id': 'DD', 'name': 'Design Development', 'target_date': '', 'actual_date': '', 'pencils_down': '', 'status': 'Not Started', 'owner': '', 'notes': ''},
            {'id': '50CD', 'name': '50% Construction Docs', 'target_date': '', 'actual_date': '', 'pencils_down': '', 'status': 'Not Started', 'owner': '', 'notes': ''},
            {'id': '90CD', 'name': '90% Construction Docs', 'target_date': '', 'actual_date': '', 'pencils_down': '', 'status': 'Not Started', 'owner': '', 'notes': ''},
            {'id': 'PERMIT', 'name': 'Permit', 'target_date': '', 'actual_date': '', 'pencils_down': '', 'status': 'Not Started', 'owner': '', 'notes': ''},
            {'id': 'IFC', 'name': 'Issued For Construction', 'target_date': '', 'actual_date': '', 'pencils_down': '', 'status': 'Not Started', 'owner': '', 'notes': ''}
        ],
        'disciplines': {
            'M': {'percent': 0, 'issues_open': 0, 'status': 'Not Started'},
            'E': {'percent': 0, 'issues_open': 0, 'status': 'Not Started'},
            'P': {'percent': 0, 'issues_open': 0, 'status': 'Not Started'}
        },
        'revision_metadata': {},
        'sheet_revision_status': {},
        'audit_log': []
    }


# def _upgrade_legacy_structure(data):
#     if not isinstance(data, dict):
#         data = {}

#     if 'revision_metadata' not in data or not isinstance(data.get('revision_metadata'), dict):
#         data['revision_metadata'] = {}

#     if 'sheet_revision_status' not in data or not isinstance(data.get('sheet_revision_status'), dict):
#         data['sheet_revision_status'] = {}

#     if 'audit_log' not in data or not isinstance(data.get('audit_log'), list):
#         data['audit_log'] = []

#     if 'stages' in data and isinstance(data.get('stages'), list):
#         for stage in data.get('stages', []):
#             if not isinstance(stage, dict):
#                 continue
#             if 'id' not in stage:
#                 stage['id'] = ''
#             if 'name' not in stage:
#                 stage['name'] = ''
#             if 'target_date' not in stage:
#                 stage['target_date'] = ''
#             if 'actual_date' not in stage:
#                 stage['actual_date'] = ''
#             if 'pencils_down' not in stage:
#                 stage['pencils_down'] = ''
#             if 'status' not in stage:
#                 stage['status'] = 'Not Started'
#             if 'owner' not in stage:
#                 stage['owner'] = ''
#             if 'notes' not in stage:
#                 stage['notes'] = ''

#     for rev_key, meta in data.get('revision_metadata', {}).items():
#         if not isinstance(meta, dict):
#             data['revision_metadata'][rev_key] = {
#                 'key': str(rev_key),
#                 'sequence': '',
#                 'number': '',
#                 'date': '',
#                 'pencils_down': '',
#                 'description': '',
#                 'narrative': '',
#                 'sheet_count': 0,
#                 'marker_state': 'red'
#             }
#             continue

#         if 'key' not in meta:
#             meta['key'] = str(rev_key)
#         if 'sequence' not in meta:
#             meta['sequence'] = ''
#         if 'number' not in meta:
#             meta['number'] = ''
#         if 'date' not in meta:
#             meta['date'] = ''
#         if 'pencils_down' not in meta:
#             meta['pencils_down'] = ''
#         if 'description' not in meta:
#             meta['description'] = ''
#         if 'narrative' not in meta:
#             meta['narrative'] = ''
#         if 'sheet_count' not in meta:
#             meta['sheet_count'] = 0
#         if 'marker_state' not in meta:
#             meta['marker_state'] = 'red'

#     status = data.get('sheet_revision_status', {})
#     if not status:
#         data['sheet_revision_status'] = {}
#         return data

#     sample_value = None
#     for value in status.values():
#         sample_value = value
#         break

#     if isinstance(sample_value, dict):
#         nested_candidate = None
#         for v in sample_value.values():
#             nested_candidate = v
#             break

#         if isinstance(nested_candidate, dict) and (
#             'complete' in nested_candidate or
#             'narrative_complete' in nested_candidate or
#             'completed_by' in nested_candidate or
#             'completed_on' in nested_candidate
#         ):
#             for rev_key, sheet_map in status.items():
#                 if not isinstance(sheet_map, dict):
#                     status[rev_key] = {}
#                     continue

#                 for sheet_number, payload in sheet_map.items():
#                     if isinstance(payload, dict):
#                         if 'complete' not in payload:
#                             payload['complete'] = False
#                         if 'narrative_complete' not in payload:
#                             payload['narrative_complete'] = bool(payload.get('narrative', False))
#                         if 'completed_by' not in payload:
#                             payload['completed_by'] = ''
#                         if 'completed_on' not in payload:
#                             payload['completed_on'] = ''
#                     else:
#                         sheet_map[sheet_number] = {
#                             'complete': bool(payload),
#                             'narrative_complete': False,
#                             'completed_by': '',
#                             'completed_on': ''
#                         }
#             return data

#         if (
#             'complete' in sample_value or
#             'narrative_complete' in sample_value or
#             'narrative' in sample_value or
#             'completed_by' in sample_value or
#             'completed_on' in sample_value
#         ):
#             legacy = status
#             upgraded = {'legacy': {}}
#             for sheet_number, payload in legacy.items():
#                 if isinstance(payload, dict):
#                     upgraded['legacy'][sheet_number] = {
#                         'complete': bool(payload.get('complete', False)),
#                         'narrative_complete': bool(
#                             payload.get('narrative_complete', payload.get('narrative', False))
#                         ),
#                         'completed_by': payload.get('completed_by', ''),
#                         'completed_on': payload.get('completed_on', '')
#                     }
#                 else:
#                     upgraded['legacy'][sheet_number] = {
#                         'complete': bool(payload),
#                         'narrative_complete': False,
#                         'completed_by': '',
#                         'completed_on': ''
#                     }
#             data['sheet_revision_status'] = upgraded
#             return data

    # if isinstance(sample_value, bool):
    #     legacy = status
    #     upgraded = {'legacy': {}}
    #     for sheet_number, payload in legacy.items():
    #         upgraded['legacy'][sheet_number] = {
    #             'complete': bool(payload),
    #             'narrative_complete': False,
    #             'completed_by': '',
    #             'completed_on': ''
    #         }
    #     data['sheet_revision_status'] = upgraded

    # return data

def _upgrade_legacy_structure(data):
    if not isinstance(data, dict):
        data = {}

    if 'revision_metadata' not in data or not isinstance(data.get('revision_metadata'), dict):
        data['revision_metadata'] = {}

    if 'sheet_revision_status' not in data or not isinstance(data.get('sheet_revision_status'), dict):
        data['sheet_revision_status'] = {}

    if 'audit_log' not in data or not isinstance(data.get('audit_log'), list):
        data['audit_log'] = []

    if 'stages' in data and isinstance(data.get('stages'), list):
        for stage in data.get('stages', []):
            if not isinstance(stage, dict):
                continue
            if 'id' not in stage:
                stage['id'] = ''
            if 'name' not in stage:
                stage['name'] = ''
            if 'target_date' not in stage:
                stage['target_date'] = ''
            if 'actual_date' not in stage:
                stage['actual_date'] = ''
            if 'pencils_down' not in stage:
                stage['pencils_down'] = ''
            if 'status' not in stage:
                stage['status'] = 'Not Started'
            if 'owner' not in stage:
                stage['owner'] = ''
            if 'notes' not in stage:
                stage['notes'] = ''

    for rev_key, meta in data.get('revision_metadata', {}).items():
        if not isinstance(meta, dict):
            data['revision_metadata'][rev_key] = {
                'key': str(rev_key),
                'sequence': '',
                'number': '',
                'date': '',
                'pencils_down': '',
                'description': '',
                'narrative': '',
                'sheet_count': 0,
                'marker_state': 'red'
            }
            continue

        if 'key' not in meta:
            meta['key'] = str(rev_key)
        if 'sequence' not in meta:
            meta['sequence'] = ''
        if 'number' not in meta:
            meta['number'] = ''
        if 'date' not in meta:
            meta['date'] = ''
        if 'pencils_down' not in meta:
            meta['pencils_down'] = ''
        if 'description' not in meta:
            meta['description'] = ''
        if 'narrative' not in meta:
            meta['narrative'] = ''
        if 'sheet_count' not in meta:
            meta['sheet_count'] = 0
        if 'marker_state' not in meta:
            meta['marker_state'] = 'red'

    status = data.get('sheet_revision_status', {})
    if not status:
        data['sheet_revision_status'] = {}
        return data

    sample_value = None
    for value in status.values():
        sample_value = value
        break

    if isinstance(sample_value, dict):
        nested_candidate = None
        for v in sample_value.values():
            nested_candidate = v
            break

        if isinstance(nested_candidate, dict) and (
            'complete' in nested_candidate or
            'narrative_complete' in nested_candidate or
            'completed_by' in nested_candidate or
            'completed_on' in nested_candidate
        ):
            for rev_key, sheet_map in status.items():
                if not isinstance(sheet_map, dict):
                    status[rev_key] = {}
                    continue

                for sheet_number, payload in sheet_map.items():
                    if isinstance(payload, dict):
                        if 'revision_key' not in payload:
                            payload['revision_key'] = str(rev_key)
                        if 'sheet_number' not in payload:
                            payload['sheet_number'] = sheet_number or ''
                        if 'sheet_name' not in payload:
                            payload['sheet_name'] = ''
                        if 'current_revision' not in payload:
                            payload['current_revision'] = ''
                        if 'current_revision_date' not in payload:
                            payload['current_revision_date'] = ''
                        if 'complete' not in payload:
                            payload['complete'] = False
                        if 'narrative_complete' not in payload:
                            payload['narrative_complete'] = bool(payload.get('narrative', False))
                        if 'completed_by' not in payload:
                            payload['completed_by'] = ''
                        if 'completed_on' not in payload:
                            payload['completed_on'] = ''
                        if 'marker_state' not in payload:
                            payload['marker_state'] = 'red'
                    else:
                        sheet_map[sheet_number] = {
                            'revision_key': str(rev_key),
                            'sheet_number': sheet_number or '',
                            'sheet_name': '',
                            'current_revision': '',
                            'current_revision_date': '',
                            'complete': bool(payload),
                            'narrative_complete': False,
                            'completed_by': '',
                            'completed_on': '',
                            'marker_state': 'red'
                        }
            return data

        if (
            'complete' in sample_value or
            'narrative_complete' in sample_value or
            'narrative' in sample_value or
            'completed_by' in sample_value or
            'completed_on' in sample_value
        ):
            legacy = status
            upgraded = {'legacy': {}}
            for sheet_number, payload in legacy.items():
                if isinstance(payload, dict):
                    upgraded['legacy'][sheet_number] = {
                        'revision_key': 'legacy',
                        'sheet_number': sheet_number or '',
                        'sheet_name': payload.get('sheet_name', ''),
                        'current_revision': payload.get('current_revision', ''),
                        'current_revision_date': payload.get('current_revision_date', ''),
                        'complete': bool(payload.get('complete', False)),
                        'narrative_complete': bool(
                            payload.get('narrative_complete', payload.get('narrative', False))
                        ),
                        'completed_by': payload.get('completed_by', ''),
                        'completed_on': payload.get('completed_on', ''),
                        'marker_state': payload.get('marker_state', 'red')
                    }
                else:
                    upgraded['legacy'][sheet_number] = {
                        'revision_key': 'legacy',
                        'sheet_number': sheet_number or '',
                        'sheet_name': '',
                        'current_revision': '',
                        'current_revision_date': '',
                        'complete': bool(payload),
                        'narrative_complete': False,
                        'completed_by': '',
                        'completed_on': '',
                        'marker_state': 'red'
                    }
            data['sheet_revision_status'] = upgraded
            return data

    if isinstance(sample_value, bool):
        legacy = status
        upgraded = {'legacy': {}}
        for sheet_number, payload in legacy.items():
            upgraded['legacy'][sheet_number] = {
                'revision_key': 'legacy',
                'sheet_number': sheet_number or '',
                'sheet_name': '',
                'current_revision': '',
                'current_revision_date': '',
                'complete': bool(payload),
                'narrative_complete': False,
                'completed_by': '',
                'completed_on': '',
                'marker_state': 'red'
            }
        data['sheet_revision_status'] = upgraded

    return data

def load_dashboard(path, doc):
    if not os.path.exists(path):
        return create_default_dashboard(doc)
    with io.open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    data = _upgrade_legacy_structure(data)
    if 'sheet_revision_status' not in data:
        data['sheet_revision_status'] = {}
    if 'revision_metadata' not in data:
        data['revision_metadata'] = {}
    return data


def save_dashboard(path, data):
    data['project']['last_updated'] = DateTime.Now.ToString('s')
    with io.open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def add_audit(data, message):
    data.setdefault('audit_log', []).append({
        'timestamp': DateTime.Now.ToString('s'),
        'user': _current_username(),
        'action': message
    })


XAML = r'''
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Coordination Dashboard"
        Height="920" Width="1760"
        WindowStartupLocation="CenterScreen"
        ResizeMode="CanResize"
        Background="#F5F5F5">
    <DockPanel Margin="12">
        <Border DockPanel.Dock="Top" Background="#124130" CornerRadius="10" Padding="14" Margin="0,0,0,10">
            <Grid>
                <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="Auto"/>
                </Grid.ColumnDefinitions>
                <StackPanel>
                    <TextBlock x:Name="ProjectTitleText" FontSize="24" FontWeight="Bold" Foreground="#F1E1C0"/>
                    <TextBlock x:Name="ProjectMetaText" FontSize="12" Foreground="#F5F5F5" Margin="0,4,0,0"/>
                </StackPanel>
                <StackPanel Grid.Column="1" Orientation="Horizontal" VerticalAlignment="Center">
                    <Button x:Name="LoadBtn" Content="Load JSON" Width="90" Height="32" Margin="0,0,6,0"/>
                    <Button x:Name="SaveBtn" Content="Save" Width="72" Height="32" Margin="0,0,6,0"/>
                    <Button x:Name="SaveAsBtn" Content="Save As" Width="80" Height="32" Margin="0,0,6,0"/>
                    <Button x:Name="SyncBtn" Content="Sync from Revit" Width="120" Height="32"/>
                </StackPanel>
            </Grid>
        </Border>

        <Grid>
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="4.6*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="1.35*"/>
            </Grid.ColumnDefinitions>

            <Border Grid.Column="0" Background="White" CornerRadius="10" Padding="10">
                <Grid>
                    <Grid.RowDefinitions>
                        <RowDefinition Height="3*"/>
                        <RowDefinition Height="6"/>
                        <RowDefinition Height="1.7*"/>
                        <RowDefinition Height="6"/>
                        <RowDefinition Height="1.7*"/>
                    </Grid.RowDefinitions>

                    <DockPanel Grid.Row="0">
                        <Grid DockPanel.Dock="Top" Margin="0,0,0,8">
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="*"/>
                                <ColumnDefinition Width="Auto"/>
                            </Grid.ColumnDefinitions>

                            <TextBlock Grid.Column="0"
                                       Text="Project Stages"
                                       FontSize="18"
                                       FontWeight="Bold"
                                       Foreground="#124130"
                                       VerticalAlignment="Center"
                                       Margin="4,0,0,0"/>

                            <Button x:Name="AddStageBtn"
                                    Grid.Column="1"
                                    Content="Add Stage"
                                    Width="92"
                                    Height="30"
                                    Margin="8,0,0,0"/>
                        </Grid>

                        <DataGrid x:Name="StageGrid"
                                  AutoGenerateColumns="False"
                                  CanUserAddRows="False"
                                  HeadersVisibility="Column"
                                  GridLinesVisibility="Horizontal"
                                  RowBackground="White"
                                  AlternatingRowBackground="#FAFAFA"/>
                    </DockPanel>

                    <GridSplitter Grid.Row="1"
                                  Height="6"
                                  HorizontalAlignment="Stretch"
                                  VerticalAlignment="Center"
                                  Background="#D6D6D6"
                                  ResizeDirection="Rows"
                                  ShowsPreview="True"/>

                    <DockPanel Grid.Row="2" Margin="0,8,0,0">
                        <Grid DockPanel.Dock="Top" Margin="0,0,0,8">
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="*"/>
                                <ColumnDefinition Width="Auto"/>
                            </Grid.ColumnDefinitions>

                            <TextBlock Grid.Column="0"
                                       Text="Revisions"
                                       FontSize="18"
                                       FontWeight="Bold"
                                       Foreground="#124130"
                                       VerticalAlignment="Center"
                                       Margin="4,0,0,0"/>

                            <StackPanel Grid.Column="1" Orientation="Horizontal">
                                <Button x:Name="BrowseNarrativeBtn"
                                        Content="Browse Narrative"
                                        Width="120"
                                        Height="30"
                                        Margin="0,0,6,0"/>
                                <Button x:Name="OpenNarrativeBtn"
                                        Content="Open Narrative"
                                        Width="115"
                                        Height="30"/>
                            </StackPanel>
                        </Grid>

                        <DataGrid x:Name="RevisionGrid"
                                  AutoGenerateColumns="False"
                                  CanUserAddRows="False"
                                  IsReadOnly="False"
                                  HeadersVisibility="Column"
                                  GridLinesVisibility="Horizontal"
                                  RowBackground="White"
                                  AlternatingRowBackground="#FAFAFA">
                            <DataGrid.RowStyle>
                                <Style TargetType="DataGridRow">
                                    <Setter Property="Background" Value="White"/>
                                    <Setter Property="Foreground" Value="#1F2937"/>
                                    <Style.Triggers>
                                        <Trigger Property="IsSelected" Value="True">
                                            <Setter Property="Background" Value="#DCEBDD"/>
                                            <Setter Property="Foreground" Value="#124130"/>
                                            <Setter Property="FontWeight" Value="Bold"/>
                                        </Trigger>
                                    </Style.Triggers>
                                </Style>
                            </DataGrid.RowStyle>
                        </DataGrid>
                    </DockPanel>

                    <GridSplitter Grid.Row="3"
                                  Height="6"
                                  HorizontalAlignment="Stretch"
                                  VerticalAlignment="Center"
                                  Background="#D6D6D6"
                                  ResizeDirection="Rows"
                                  ShowsPreview="True"/>

                    <DockPanel Grid.Row="4" Margin="0,8,0,0">
                        <TextBlock DockPanel.Dock="Top"
                                   Text="Affected Sheets"
                                   FontSize="18"
                                   FontWeight="Bold"
                                   Foreground="#124130"
                                   Margin="4,0,0,8"/>
                        <DataGrid x:Name="SheetGrid"
                                  AutoGenerateColumns="False"
                                  CanUserAddRows="False"
                                  IsReadOnly="False"
                                  HeadersVisibility="Column"
                                  GridLinesVisibility="Horizontal"
                                  RowBackground="White"
                                  AlternatingRowBackground="#FAFAFA"/>
                    </DockPanel>
                </Grid>
            </Border>

            <StackPanel Grid.Column="2">
                <Border Background="White" CornerRadius="10" Padding="12" Margin="0,0,0,10">
                    <StackPanel>
                        <TextBlock Text="Summary" FontSize="18" FontWeight="Bold" Foreground="#124130"/>
                        <TextBlock x:Name="SummaryText" Margin="0,8,0,0" TextWrapping="Wrap"/>
                    </StackPanel>
                </Border>

                <Border Background="White" CornerRadius="10" Padding="12" Margin="0,0,0,10">
                    <StackPanel>
                        <TextBlock Text="Mechanical" FontSize="16" FontWeight="Bold" Foreground="#124130"/>
                        <TextBlock Text="Percent Complete" Margin="0,8,0,2"/>
                        <TextBox x:Name="MPercentBox" Height="28"/>
                        <TextBlock Text="Open Issues" Margin="0,8,0,2"/>
                        <TextBox x:Name="MIssuesBox" Height="28"/>
                        <TextBlock Text="Status" Margin="0,8,0,2"/>
                        <ComboBox x:Name="MStatusBox" Height="28"/>
                    </StackPanel>
                </Border>

                <Border Background="White" CornerRadius="10" Padding="12" Margin="0,0,0,10">
                    <StackPanel>
                        <TextBlock Text="Electrical" FontSize="16" FontWeight="Bold" Foreground="#124130"/>
                        <TextBlock Text="Percent Complete" Margin="0,8,0,2"/>
                        <TextBox x:Name="EPercentBox" Height="28"/>
                        <TextBlock Text="Open Issues" Margin="0,8,0,2"/>
                        <TextBox x:Name="EIssuesBox" Height="28"/>
                        <TextBlock Text="Status" Margin="0,8,0,2"/>
                        <ComboBox x:Name="EStatusBox" Height="28"/>
                    </StackPanel>
                </Border>

                <Border Background="White" CornerRadius="10" Padding="12">
                    <StackPanel>
                        <TextBlock Text="Plumbing" FontSize="16" FontWeight="Bold" Foreground="#124130"/>
                        <TextBlock Text="Percent Complete" Margin="0,8,0,2"/>
                        <TextBox x:Name="PPercentBox" Height="28"/>
                        <TextBlock Text="Open Issues" Margin="0,8,0,2"/>
                        <TextBox x:Name="PIssuesBox" Height="28"/>
                        <TextBlock Text="Status" Margin="0,8,0,2"/>
                        <ComboBox x:Name="PStatusBox" Height="28"/>
                    </StackPanel>
                </Border>
            </StackPanel>
        </Grid>

        <Border DockPanel.Dock="Bottom" Background="#E0E0E0" CornerRadius="8" Padding="8" Margin="0,10,0,0">
            <TextBlock x:Name="StatusText" FontSize="12" Foreground="#124130"/>
        </Border>
    </DockPanel>
</Window>
'''


MARKER_TEMPLATE_XAML = r'''
<DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
              xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
    <Grid HorizontalAlignment="Center" VerticalAlignment="Center">
        <Ellipse x:Name="MarkerDot"
                 Width="14"
                 Height="14"
                 Stroke="#666666"
                 StrokeThickness="0.8"
                 Fill="#D64545"/>
    </Grid>
    <DataTemplate.Triggers>
        <DataTrigger Binding="{Binding marker_state}" Value="orange">
            <Setter TargetName="MarkerDot" Property="Fill" Value="#E6A23C"/>
        </DataTrigger>
        <DataTrigger Binding="{Binding marker_state}" Value="green">
            <Setter TargetName="MarkerDot" Property="Fill" Value="#4CAF50"/>
        </DataTrigger>
        <DataTrigger Binding="{Binding marker_state}" Value="red">
            <Setter TargetName="MarkerDot" Property="Fill" Value="#D64545"/>
        </DataTrigger>
    </DataTemplate.Triggers>
</DataTemplate>
'''


class DashboardWindow(object):
    def __init__(self, doc):
        self.doc = doc
        self.json_path = _default_json_path(doc)
        self.data = load_dashboard(self.json_path, doc)

        self.win = XamlReader.Parse(XAML)
        self.marker_template = XamlReader.Parse(MARKER_TEMPLATE_XAML)

        self.project_title = self.win.FindName('ProjectTitleText')
        self.project_meta = self.win.FindName('ProjectMetaText')
        self.stage_grid = self.win.FindName('StageGrid')
        self.revision_grid = self.win.FindName('RevisionGrid')
        self.sheet_grid = self.win.FindName('SheetGrid')
        self.summary_text = self.win.FindName('SummaryText')
        self.status_text = self.win.FindName('StatusText')
        self.load_btn = self.win.FindName('LoadBtn')
        self.save_btn = self.win.FindName('SaveBtn')
        self.save_as_btn = self.win.FindName('SaveAsBtn')
        self.sync_btn = self.win.FindName('SyncBtn')
        self.browse_narrative_btn = self.win.FindName('BrowseNarrativeBtn')
        self.open_narrative_btn = self.win.FindName('OpenNarrativeBtn')
        self.add_stage_btn = self.win.FindName('AddStageBtn')

        self.disc_boxes = {}
        for key in DISC_KEYS:
            self.disc_boxes[key] = {
                'percent': self.win.FindName(key + 'PercentBox'),
                'issues': self.win.FindName(key + 'IssuesBox'),
                'status': self.win.FindName(key + 'StatusBox')
            }
            self.disc_boxes[key]['status'].ItemsSource = STATUS_OPTIONS

        self.stage_rows = ObservableCollection[object]()
        self.revision_rows = ObservableCollection[object]()
        self.sheet_rows = ObservableCollection[object]()
        self.revision_sheet_map = {}
        self.selected_revision_key = None

        self._build_stage_columns()
        self._build_revision_columns()
        self._build_sheet_columns()
        self._bind_data()

        self.load_btn.Click += self.on_load
        self.save_btn.Click += self.on_save
        self.save_as_btn.Click += self.on_save_as
        self.sync_btn.Click += self.on_sync
        self.browse_narrative_btn.Click += self.on_browse_narrative
        self.open_narrative_btn.Click += self.on_open_narrative
        self.add_stage_btn.Click += self.on_add_stage
        self.revision_grid.SelectionChanged += self.on_revision_selected
        self.revision_grid.CurrentCellChanged += self.on_revision_grid_changed
        self.sheet_grid.CurrentCellChanged += self.on_sheet_grid_changed

    def _build_stage_columns(self):
        self.stage_grid.Columns.Clear()

        columns = [
            ('ID', 'id', 70),
            ('Stage', 'name', 180),
            ('Target Date', 'target_date', 100),
            ('Actual Date', 'actual_date', 100),
            ('Pencils Down', 'pencils_down', 100),
            ('Owner', 'owner', 90),
            ('Notes', 'notes', 220),
        ]

        for header, path, width in columns:
            col = DataGridTextColumn()
            col.Header = header
            col.Binding = Binding(path)
            col.Width = DataGridLength(width)
            self.stage_grid.Columns.Add(col)

        combo = DataGridComboBoxColumn()
        combo.Header = 'Status'
        combo.SelectedItemBinding = Binding('status')
        combo.ItemsSource = STATUS_OPTIONS
        combo.Width = DataGridLength(120)
        self.stage_grid.Columns.Insert(5, combo)

    def _build_revision_columns(self):
        self.revision_grid.Columns.Clear()

        marker_col = DataGridTemplateColumn()
        marker_col.Header = 'Marker'
        marker_col.CellTemplate = self.marker_template
        marker_col.Width = DataGridLength(60)
        self.revision_grid.Columns.Add(marker_col)

        columns = [
            ('Seq', 'sequence', 55),
            ('Rev', 'number', 70),
            ('Date', 'date', 95),
            ('Pencils Down', 'pencils_down', 100),
            ('Description', 'description', 210),
            ('Narrative Path', 'narrative', 360),
            ('Sheets', 'sheet_count', 60),
        ]

        for header, path, width in columns:
            col = DataGridTextColumn()
            col.Header = header
            col.Binding = Binding(path)
            col.Width = DataGridLength(width)
            self.revision_grid.Columns.Add(col)

    def _build_sheet_columns(self):
        self.sheet_grid.Columns.Clear()

        marker_col = DataGridTemplateColumn()
        marker_col.Header = 'Marker'
        marker_col.CellTemplate = self.marker_template
        marker_col.Width = DataGridLength(60)
        self.sheet_grid.Columns.Add(marker_col)

        check_col = DataGridCheckBoxColumn()
        check_col.Header = 'Revisions Complete'
        check_col.Binding = Binding('revisions_complete')
        check_col.IsThreeState = False
        check_col.Width = DataGridLength(120)
        self.sheet_grid.Columns.Add(check_col)

        narrative_col = DataGridCheckBoxColumn()
        narrative_col.Header = 'Narrative'
        narrative_col.Binding = Binding('narrative_complete')
        narrative_col.IsThreeState = False
        narrative_col.Width = DataGridLength(85)
        self.sheet_grid.Columns.Add(narrative_col)

        columns = [
            ('Completed By', 'completed_by', 110),
            ('Completed On', 'completed_on', 145),
            ('Sheet No.', 'sheet_number', 90),
            ('Sheet Name', 'sheet_name', 240),
            ('Revision', 'current_revision', 80),
            ('Rev Date', 'current_revision_date', 100),
        ]

        for header, path, width in columns:
            col = DataGridTextColumn()
            col.Header = header
            col.Binding = Binding(path)
            col.Width = DataGridLength(width)
            self.sheet_grid.Columns.Add(col)

    def _bind_data(self):
        self.project_title.Text = self.data.get('project', {}).get('name', 'Project Coordination Dashboard')
        self.project_meta.Text = 'Project No: {0}   |   JSON: {1}'.format(
            self.data.get('project', {}).get('number', ''),
            self.json_path
        )

        self.stage_rows.Clear()
        for stage in self.data.get('stages', []):
            self.stage_rows.Add(StageRow(stage))
        self.stage_grid.ItemsSource = self.stage_rows

        self._refresh_revisions()

        disciplines = self.data.get('disciplines', {})
        for key in DISC_KEYS:
            d = disciplines.get(key, {})
            self.disc_boxes[key]['percent'].Text = str(d.get('percent', 0))
            self.disc_boxes[key]['issues'].Text = str(d.get('issues_open', 0))
            self.disc_boxes[key]['status'].SelectedItem = d.get('status', 'Not Started')

        self._refresh_summary()
        self.status_text.Text = 'Ready. Select a revision to view affected sheets.'

    def _refresh_summary(self):
        s = self.data.get('summary', {})
        complete_count = 0
        narrative_count = 0

        for row in self.sheet_rows:
            try:
                row.refresh_marker()
                if row.revisions_complete:
                    complete_count += 1
                if row.narrative_complete:
                    narrative_count += 1
            except Exception:
                pass

        self.summary_text.Text = (
            'Total Sheets: {0}\n'
            'Complete Sheets: {1}\n'
            'Issued Sheets: {2}\n'
            'Revisions On Sheets: {3}\n'
            'Selected Revision Sheets: {4}\n'
            'Checked Complete: {5}\n'
            'Narratives Checked: {6}\n'
            'Last Updated: {7}'
        ).format(
            s.get('total_sheets', 0),
            s.get('complete_sheets', 0),
            s.get('issued_sheets', 0),
            len(self.revision_rows),
            len(self.sheet_rows),
            complete_count,
            narrative_count,
            self.data.get('project', {}).get('last_updated', '')
        )

    def _update_revision_markers(self):
        rev_lookup = {}
        for rev in self.revision_rows:
            rev_lookup[str(rev.key)] = rev

        for rev_key, sheet_list in self.revision_sheet_map.items():
            marker = _compute_revision_marker(sheet_list)
            rev = rev_lookup.get(str(rev_key))
            if rev:
                rev.marker_state = marker

    def _update_sheet_completion_metadata(self):
        username = _current_username()
        for row in self.sheet_rows:
            try:
                row.refresh_marker()
                if bool(row.revisions_complete):
                    if not row.completed_by:
                        row.completed_by = username
                    if not row.completed_on:
                        row.completed_on = _current_timestamp()
                else:
                    row.completed_by = ''
                    row.completed_on = ''
            except Exception:
                pass

    
    def _store_revision_metadata(self):
        self.data.setdefault('revision_metadata', {})
        for row in self.revision_rows:
            self.data['revision_metadata'][str(row.key)] = {
                'key': row.key or '',
                'sequence': row.sequence or '',
                'number': row.number or '',
                'date': row.date or '',
                'pencils_down': row.pencils_down or '',
                'description': row.description or '',
                'narrative': row.narrative or '',
                'sheet_count': row.sheet_count if row.sheet_count is not None else 0,
                'marker_state': row.marker_state or 'red'
            }

    # def _store_sheet_revision_status(self):
    #     self.data.setdefault('sheet_revision_status', {})
    #     self._update_sheet_completion_metadata()

    #     for row in self.sheet_rows:
    #         try:
    #             row.refresh_marker()
    #             rev_key = str(row.revision_key)
    #             sheet_number = row.sheet_number
    #             if rev_key not in self.data['sheet_revision_status']:
    #                 self.data['sheet_revision_status'][rev_key] = {}
    #             self.data['sheet_revision_status'][rev_key][sheet_number] = {
    #                 'complete': bool(row.revisions_complete),
    #                 'narrative_complete': bool(row.narrative_complete),
    #                 'completed_by': row.completed_by or '',
    #                 'completed_on': row.completed_on or ''
    #             }
    #         except Exception:
    #             pass

    #     self._update_revision_markers()
    def _store_sheet_revision_status(self):
        self.data.setdefault('sheet_revision_status', {})
        self._update_sheet_completion_metadata()

        for row in self.sheet_rows:
            try:
                row.refresh_marker()
                rev_key = str(row.revision_key)
                sheet_number = row.sheet_number or ''

                if rev_key not in self.data['sheet_revision_status']:
                    self.data['sheet_revision_status'][rev_key] = {}

                self.data['sheet_revision_status'][rev_key][sheet_number] = {
                    'revision_key': rev_key,
                    'sheet_number': row.sheet_number or '',
                    'sheet_name': row.sheet_name or '',
                    'current_revision': row.current_revision or '',
                    'current_revision_date': row.current_revision_date or '',
                    'complete': bool(row.revisions_complete),
                    'narrative_complete': bool(row.narrative_complete),
                    'completed_by': row.completed_by or '',
                    'completed_on': row.completed_on or '',
                    'marker_state': row.marker_state or 'red'
                }
            except Exception:
                pass

        self._update_revision_markers()

    def _pull_ui_into_data(self):
        self.data['stages'] = [row.to_dict() for row in self.stage_rows]
        self.data.setdefault('disciplines', {})

        self._store_revision_metadata()
        self._store_sheet_revision_status()

        for key in DISC_KEYS:
            percent_text = self.disc_boxes[key]['percent'].Text or '0'
            issues_text = self.disc_boxes[key]['issues'].Text or '0'

            try:
                percent = int(percent_text)
            except Exception:
                percent = 0

            try:
                issues = int(issues_text)
            except Exception:
                issues = 0

            self.data['disciplines'][key] = {
                'percent': percent,
                'issues_open': issues,
                'status': self.disc_boxes[key]['status'].SelectedItem or 'Not Started'
            }

    def _refresh_revisions(self):
        self.revision_rows.Clear()
        rows, sheet_map = _collect_revision_data(self.doc, self.data)
        self.revision_sheet_map = sheet_map

        for rev_row in rows:
            self.revision_rows.Add(rev_row)
        self.revision_grid.ItemsSource = self.revision_rows

        self.sheet_rows.Clear()
        self.sheet_grid.ItemsSource = self.sheet_rows

    def _show_sheets_for_revision(self, rev_key):
        self.sheet_rows.Clear()

        for sheet_row in self.revision_sheet_map.get(str(rev_key), []):
            sheet_row.refresh_marker()
            self.sheet_rows.Add(sheet_row)

        self.sheet_grid.ItemsSource = self.sheet_rows
        self._refresh_summary()

    def on_add_stage(self, sender, args):
        next_index = len(self.stage_rows) + 1
        new_stage = StageRow({
            'id': 'CUSTOM{}'.format(next_index),
            'name': 'New Stage {}'.format(next_index),
            'target_date': '',
            'actual_date': '',
            'pencils_down': '',
            'status': 'Not Started',
            'owner': '',
            'notes': ''
        })

        self.stage_rows.Add(new_stage)
        self.stage_grid.Items.Refresh()
        self.status_text.Text = 'Added a new project stage.'

    def on_revision_selected(self, sender, args):
        row = self.revision_grid.SelectedItem
        if not row:
            self.selected_revision_key = None
            self.sheet_rows.Clear()
            self.sheet_grid.ItemsSource = self.sheet_rows
            self.status_text.Text = 'No revision selected.'
            self._refresh_summary()
            return

        try:
            self.selected_revision_key = str(row.key)
            self._show_sheets_for_revision(self.selected_revision_key)
            self.status_text.Text = 'Showing affected sheets for revision {}.'.format(row.number or row.sequence)
        except Exception:
            self.status_text.Text = 'Could not load affected sheets for selected revision.'

    def on_revision_grid_changed(self, sender, args):
        try:
            self._store_revision_metadata()
            self._update_revision_markers()
            self.revision_grid.Items.Refresh()
            self._refresh_summary()
        except Exception:
            pass

    def on_sheet_grid_changed(self, sender, args):
        try:
            for row in self.sheet_rows:
                row.refresh_marker()
            self._store_sheet_revision_status()
            self._update_revision_markers()
            self.sheet_grid.Items.Refresh()
            self.revision_grid.Items.Refresh()
            self._refresh_summary()
        except Exception:
            pass

    def on_browse_narrative(self, sender, args):
        rev = self.revision_grid.SelectedItem
        if not rev:
            forms.alert('Select a revision first.')
            return

        init_dir = ''
        current_path = (rev.narrative or '').strip().strip('"')
        if current_path and os.path.exists(os.path.dirname(current_path)):
            init_dir = os.path.dirname(current_path)
        else:
            try:
                init_dir = os.path.dirname(self.json_path)
            except Exception:
                init_dir = ''

        picked = forms.pick_file(
            init_dir=init_dir,
            title='Select narrative document'
        )

        if not picked:
            return

        rev.narrative = picked
        self.revision_grid.Items.Refresh()
        self._store_revision_metadata()
        self.status_text.Text = 'Narrative path updated for selected revision.'

    def on_open_narrative(self, sender, args):
        rev = self.revision_grid.SelectedItem
        if not rev:
            forms.alert('Select a revision first.')
            return

        path = (rev.narrative or '').strip().strip('"')
        if not path:
            forms.alert('No narrative path is set for this revision.')
            return

        if not os.path.exists(path):
            forms.alert('Narrative file not found:\n{}'.format(path))
            return

        try:
            os.startfile(path)
            self.status_text.Text = 'Opened narrative document.'
        except Exception as ex:
            forms.alert('Could not open narrative file:\n{}'.format(ex))

    def on_load(self, sender, args):
        path = forms.pick_file(
            file_ext='json',
            init_dir=os.path.dirname(self.json_path),
            title='Select dashboard JSON'
        )
        if not path:
            return

        self.json_path = path
        self.data = load_dashboard(self.json_path, self.doc)
        self._bind_data()
        self.status_text.Text = 'Loaded JSON.'

    def on_save(self, sender, args):
        self._pull_ui_into_data()
        add_audit(self.data, 'Saved dashboard')
        save_dashboard(self.json_path, self.data)
        self._bind_data()
        self.status_text.Text = 'Saved to JSON.'

    def on_save_as(self, sender, args):
        path = forms.save_file(
            file_ext='json',
            init_dir=os.path.dirname(self.json_path),
            default_name=os.path.basename(self.json_path),
            title='Save dashboard JSON as'
        )
        if not path:
            return

        self.json_path = path
        self.on_save(sender, args)

    def on_sync(self, sender, args):
        self._pull_ui_into_data()
        self.data['summary'] = _sheet_metrics(self.doc)
        self.data['project']['name'] = self.doc.Title if self.doc else self.data['project'].get('name', '')
        self.data['project']['number'] = _get_project_number_from_info(self.doc)
        add_audit(self.data, 'Synced from Revit')

        selected_key = self.selected_revision_key
        self._refresh_revisions()
        self._refresh_summary()

        if selected_key:
            for row in self.revision_rows:
                if str(row.key) == str(selected_key):
                    self.revision_grid.SelectedItem = row
                    self._show_sheets_for_revision(selected_key)
                    break

        self.project_title.Text = self.data['project']['name']
        self.project_meta.Text = 'Project No: {0}   |   JSON: {1}'.format(
            self.data['project']['number'],
            self.json_path
        )
        self.status_text.Text = 'Synced live metrics and revisions from Revit.'

    def show(self):
        self.win.ShowDialog()


def main():
    dashboard = DashboardWindow(DOC)
    dashboard.show()


if __name__ == '__main__':
    main()
