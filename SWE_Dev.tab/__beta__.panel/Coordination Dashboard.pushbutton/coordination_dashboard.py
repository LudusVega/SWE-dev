# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import io
import json
import clr

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')
clr.AddReference('System.Xaml')

from System import DateTime
from System.IO import StringReader
from System.Windows import Window
from System.Windows.Markup import XamlReader
from System.Windows.Controls import DataGridTextColumn, DataGridComboBoxColumn
from System.Windows.Data import Binding
from System.Collections.ObjectModel import ObservableCollection

from Autodesk.Revit.DB import FilteredElementCollector, ViewSheet
from pyrevit import revit, forms, script

__title__ = 'Coordination\nDashboard'
__author__ = 'Perplexity'
__doc__ = 'Project stage coordination dashboard with external JSON read/write.'

DOC = revit.doc

STATUS_OPTIONS = ['Not Started', 'In Progress', 'On Track', 'Needs Input', 'At Risk', 'Complete']
DISC_KEYS = ['M', 'E', 'P']


class StageRow(object):
    def __init__(self, data):
        self.id = data.get('id', '')
        self.name = data.get('name', '')
        self.target_date = data.get('target_date', '')
        self.actual_date = data.get('actual_date', '')
        self.status = data.get('status', 'Not Started')
        self.owner = data.get('owner', '')
        self.notes = data.get('notes', '')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'target_date': self.target_date,
            'actual_date': self.actual_date,
            'status': self.status,
            'owner': self.owner,
            'notes': self.notes,
        }


def _script_dir():
    return os.path.dirname(__file__)


def _default_json_path(doc):
    title = doc.Title if doc else 'Project'
    safe = ''.join(c if c.isalnum() or c in ('-', '_') else '_' for c in title)
    return os.path.join(_script_dir(), safe + '_coordination_dashboard.json')


def _get_project_number(doc):
    try:
        pi = doc.ProjectInformation
        p = pi.LookupParameter('Project Number')
        if p and p.HasValue:
            return p.AsString() or ''
    except Exception:
        pass
    return ''


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


def create_default_dashboard(doc):
    metrics = _sheet_metrics(doc)
    return {
        'project': {
            'name': doc.Title if doc else '',
            'number': _get_project_number(doc),
            'model_path': '',
            'last_updated': DateTime.Now.ToString('s')
        },
        'summary': metrics,
        'stages': [
            {'id': 'SD', 'name': 'Schematic Design', 'target_date': '', 'actual_date': '', 'status': 'Not Started', 'owner': '', 'notes': ''},
            {'id': 'DD', 'name': 'Design Development', 'target_date': '', 'actual_date': '', 'status': 'Not Started', 'owner': '', 'notes': ''},
            {'id': '50CD', 'name': '50% Construction Docs', 'target_date': '', 'actual_date': '', 'status': 'Not Started', 'owner': '', 'notes': ''},
            {'id': '90CD', 'name': '90% Construction Docs', 'target_date': '', 'actual_date': '', 'status': 'Not Started', 'owner': '', 'notes': ''},
            {'id': 'PERMIT', 'name': 'Permit', 'target_date': '', 'actual_date': '', 'status': 'Not Started', 'owner': '', 'notes': ''},
            {'id': 'IFC', 'name': 'Issued For Construction', 'target_date': '', 'actual_date': '', 'status': 'Not Started', 'owner': '', 'notes': ''}
        ],
        'disciplines': {
            'M': {'percent': 0, 'issues_open': 0, 'status': 'Not Started'},
            'E': {'percent': 0, 'issues_open': 0, 'status': 'Not Started'},
            'P': {'percent': 0, 'issues_open': 0, 'status': 'Not Started'}
        },
        'audit_log': []
    }


def load_dashboard(path, doc):
    if not os.path.exists(path):
        return create_default_dashboard(doc)
    with io.open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_dashboard(path, data):
    data['project']['last_updated'] = DateTime.Now.ToString('s')
    with io.open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def add_audit(data, message):
    data.setdefault('audit_log', []).append({
        'timestamp': DateTime.Now.ToString('s'),
        'user': os.environ.get('USERNAME', ''),
        'action': message
    })


XAML = r'''
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Coordination Dashboard"
        Height="760" Width="1220"
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
                <ColumnDefinition Width="3*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="1.3*"/>
            </Grid.ColumnDefinitions>

            <Border Grid.Column="0" Background="White" CornerRadius="10" Padding="10">
                <DockPanel>
                    <TextBlock DockPanel.Dock="Top" Text="Project Stages" FontSize="18" FontWeight="Bold" Foreground="#124130" Margin="4,0,0,8"/>
                    <DataGrid x:Name="StageGrid"
                              AutoGenerateColumns="False"
                              CanUserAddRows="False"
                              HeadersVisibility="Column"
                              GridLinesVisibility="Horizontal"
                              RowBackground="White"
                              AlternatingRowBackground="#FAFAFA"/>
                </DockPanel>
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


class DashboardWindow(Window):
    def __init__(self, doc):
        self.doc = doc
        self.json_path = _default_json_path(doc)
        self.data = load_dashboard(self.json_path, doc)

        reader = StringReader(XAML)
        win = XamlReader.Load(reader)

        self.Content = win.Content
        self.Title = win.Title
        self.Width = win.Width
        self.Height = win.Height
        self.Background = win.Background
        self.WindowStartupLocation = win.WindowStartupLocation
        self.ResizeMode = win.ResizeMode

        self.project_title = self.FindName('ProjectTitleText')
        self.project_meta = self.FindName('ProjectMetaText')
        self.stage_grid = self.FindName('StageGrid')
        self.summary_text = self.FindName('SummaryText')
        self.status_text = self.FindName('StatusText')
        self.load_btn = self.FindName('LoadBtn')
        self.save_btn = self.FindName('SaveBtn')
        self.save_as_btn = self.FindName('SaveAsBtn')
        self.sync_btn = self.FindName('SyncBtn')

        self.disc_boxes = {}
        for key in DISC_KEYS:
            self.disc_boxes[key] = {
                'percent': self.FindName(key + 'PercentBox'),
                'issues': self.FindName(key + 'IssuesBox'),
                'status': self.FindName(key + 'StatusBox')
            }
            self.disc_boxes[key]['status'].ItemsSource = STATUS_OPTIONS

        self.stage_rows = ObservableCollection[object]()
        self._build_stage_columns()
        self._bind_data()

        self.load_btn.Click += self.on_load
        self.save_btn.Click += self.on_save
        self.save_as_btn.Click += self.on_save_as
        self.sync_btn.Click += self.on_sync

    def _build_stage_columns(self):
        self.stage_grid.Columns.Clear()

        columns = [
            ('ID', 'id', 70),
            ('Stage', 'name', 180),
            ('Target Date', 'target_date', 100),
            ('Actual Date', 'actual_date', 100),
            ('Owner', 'owner', 90),
            ('Notes', 'notes', 260),
        ]

        for header, path, width in columns:
            col = DataGridTextColumn()
            col.Header = header
            col.Binding = Binding(path)
            col.Width = width
            self.stage_grid.Columns.Add(col)

        combo = DataGridComboBoxColumn()
        combo.Header = 'Status'
        combo.SelectedItemBinding = Binding('status')
        combo.ItemsSource = STATUS_OPTIONS
        combo.Width = 120
        self.stage_grid.Columns.Insert(4, combo)

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

        disciplines = self.data.get('disciplines', {})
        for key in DISC_KEYS:
            d = disciplines.get(key, {})
            self.disc_boxes[key]['percent'].Text = str(d.get('percent', 0))
            self.disc_boxes[key]['issues'].Text = str(d.get('issues_open', 0))
            self.disc_boxes[key]['status'].SelectedItem = d.get('status', 'Not Started')

        self._refresh_summary()
        self.status_text.Text = 'Ready.'

    def _refresh_summary(self):
        s = self.data.get('summary', {})
        self.summary_text.Text = (
            'Total Sheets: {0}\n'
            'Complete Sheets: {1}\n'
            'Issued Sheets: {2}\n'
            'Last Updated: {3}'
        ).format(
            s.get('total_sheets', 0),
            s.get('complete_sheets', 0),
            s.get('issued_sheets', 0),
            self.data.get('project', {}).get('last_updated', '')
        )

    def _pull_ui_into_data(self):
        self.data['stages'] = [row.to_dict() for row in self.stage_rows]
        self.data.setdefault('disciplines', {})

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
        self.data['project']['number'] = _get_project_number(self.doc)
        add_audit(self.data, 'Synced from Revit')

        self._refresh_summary()
        self.project_title.Text = self.data['project']['name']
        self.project_meta.Text = 'Project No: {0}   |   JSON: {1}'.format(
            self.data['project']['number'],
            self.json_path
        )
        self.status_text.Text = 'Synced live metrics from Revit.'


def main():
    win = DashboardWindow(DOC)
    win.ShowDialog()


if __name__ == '__main__':
    main()