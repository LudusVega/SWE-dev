# -*- coding: utf-8 -*-
"""
Associate Note Block Schedules with Drafting Views, then in one transaction:
  1. Write Drafting View name -> ID_ViewName_TXT on each Note Block Schedule
  2. Write Schedule name      -> ID_ViewName_TXT on each Drafting View
  3. Write Drafting View name -> CT_ScheduleTag_TXT on all "M - BAS, IO Type"
     annotation instances owned by each associated Drafting View
  4. Apply/update a Note Block Schedule filter:
     CT_ScheduleTag_TXT == ID_ViewName_TXT value written to that schedule
"""

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

import System
import System.Windows
import System.Windows.Input as Input
from System.Windows.Markup import XamlReader
from System.Collections.ObjectModel import ObservableCollection

from pyrevit import revit, DB, forms


doc = revit.doc

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
VIEW_NAME_PARAM = "ID_ViewName_TXT"
ANNOT_TYPE_NAME = "M - BAS, IO Type"
ANNOT_PARAM = "CT_ScheduleTag_TXT"

GENERIC_ANNOTATION_CAT = DB.BuiltInCategory.OST_GenericAnnotation
GENERIC_ANNOTATION_CAT_ID = DB.ElementId(GENERIC_ANNOTATION_CAT)
BULLET = u"\u25CF"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def get_elem_name(elem):
    return DB.Element.Name.__get__(elem)


def get_symbol_name(elem):
    param = elem.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    return param.AsString() if param else ""


def collect_note_block_schedules(document):
    schedules = [
        schedule for schedule in
        DB.FilteredElementCollector(document).OfClass(DB.ViewSchedule).ToElements()
        if schedule.Definition.CategoryId == GENERIC_ANNOTATION_CAT_ID
        and not schedule.IsTemplate
        and not schedule.Definition.IsKeySchedule
        and "bas" in get_elem_name(schedule).lower()
    ]
    return sorted(schedules, key=get_elem_name)


def collect_drafting_views(document):
    views = [
        view for view in
        DB.FilteredElementCollector(document).OfClass(DB.View).ToElements()
        if isinstance(view, DB.ViewDrafting) and not view.IsTemplate
    ]
    return sorted(views, key=get_elem_name)


def collect_target_annotation_instances_by_view(document, target_type_name):
    target_type_ids = {
        symbol.Id for symbol in
        DB.FilteredElementCollector(document)
        .OfClass(DB.FamilySymbol)
        .OfCategory(GENERIC_ANNOTATION_CAT)
        if get_symbol_name(symbol) == target_type_name
    }

    if not target_type_ids:
        forms.alert(
            "No Generic Annotation type named '{}' found in this document.".format(
                target_type_name
            ),
            title="Type Not Found",
            exitscript=True
        )

    instances = [
        elem for elem in
        DB.FilteredElementCollector(document)
        .OfCategory(GENERIC_ANNOTATION_CAT)
        .WhereElementIsNotElementType()
        if elem.GetTypeId() in target_type_ids
    ]

    if not instances:
        forms.alert(
            "No instances of '{}' found in the document.".format(target_type_name),
            title="No Instances Found",
            exitscript=True
        )

    by_view_id = {}
    for elem in instances:
        owner_view_id = elem.OwnerViewId
        if owner_view_id == DB.ElementId.InvalidElementId:
            continue

        owner_view = document.GetElement(owner_view_id)
        if owner_view is None or not isinstance(owner_view, DB.ViewDrafting):
            continue

        by_view_id.setdefault(owner_view_id.IntegerValue, []).append(elem)

    if not by_view_id:
        forms.alert(
            "No instances of '{}' found on any Drafting View.".format(target_type_name),
            title="No Drafting View Instances",
            exitscript=True
        )

    return by_view_id


def ensure_parameter_writable(elem, param_name, elem_label):
    param = elem.LookupParameter(param_name)
    if param is None:
        forms.alert(
            "Parameter '{}' not found on {}.".format(param_name, elem_label),
            title="Parameter Not Found",
            exitscript=True
        )
    if param.IsReadOnly:
        forms.alert(
            "Parameter '{}' is read-only on {}.".format(param_name, elem_label),
            title="Read-Only Parameter",
            exitscript=True
        )
    return param


def ensure_annotation_param_is_writable(annotation_by_view, param_name, type_name):
    first_elem = None
    for elems in annotation_by_view.values():
        if elems:
            first_elem = elems[0]
            break

    if first_elem is None:
        forms.alert(
            "No instances of '{}' found on any Drafting View.".format(type_name),
            title="No Drafting View Instances",
            exitscript=True
        )

    param = first_elem.LookupParameter(param_name)
    if param is None:
        forms.alert(
            "Parameter '{}' not found on family type '{}'.\n\n"
            "Verify the parameter name is correct and that it is an instance parameter.".format(
                param_name, type_name
            ),
            title="Parameter Not Found",
            exitscript=True
        )

    if param.IsReadOnly:
        forms.alert(
            "Parameter '{}' is read-only and cannot be written.".format(param_name),
            title="Read-Only Parameter",
            exitscript=True
        )


def get_schedule_field_id_by_param_name(schedule, param_name):
    definition = schedule.Definition

    for field_id in definition.GetFieldOrder():
        field = definition.GetField(field_id)
        param_id = field.ParameterId
        if param_id == DB.ElementId.InvalidElementId:
            continue

        if param_id.IntegerValue < 0:
            try:
                bip = System.Enum.ToObject(DB.BuiltInParameter, param_id.IntegerValue)
                if DB.LabelUtils.GetLabelFor(bip) == param_name:
                    return field_id
            except:
                pass
            continue

        param_elem = doc.GetElement(param_id)
        if param_elem is not None and get_elem_name(param_elem) == param_name:
            return field_id

    return None


def upsert_schedule_equals_filter(schedule, field_id, value, hide_field=True):
    definition = schedule.Definition
    remove_indexes = []

    for index in range(definition.GetFilterCount()):
        existing_filter = definition.GetFilter(index)
        if existing_filter.FieldId == field_id:
            remove_indexes.append(index)

    for index in reversed(remove_indexes):
        definition.RemoveFilter(index)

    definition.AddFilter(DB.ScheduleFilter(field_id, DB.ScheduleFilterType.Equal, value))

    if hide_field:
        field = definition.GetField(field_id)
        field.IsHidden = True


def filter_rows(source_rows, query_text, target_collection):
    query = (query_text or "").strip().lower()
    target_collection.Clear()
    for row in source_rows:
        if query in row.Name.lower():
            target_collection.Add(row)


def pluralize(count, singular, plural=None):
    return singular if count == 1 else (plural or singular + "s")


# -----------------------------------------------------------------------------
# Collect model data
# -----------------------------------------------------------------------------
note_blocks = collect_note_block_schedules(doc)
if not note_blocks:
    forms.alert(
        "No Note Block Schedules containing 'BAS' were found in this document.",
        title="None Found",
        exitscript=True
    )

drafting_views = collect_drafting_views(doc)
if not drafting_views:
    forms.alert(
        "No Drafting Views found in this document.",
        title="None Found",
        exitscript=True
    )

annotation_by_view_id = collect_target_annotation_instances_by_view(doc, ANNOT_TYPE_NAME)
ensure_annotation_param_is_writable(annotation_by_view_id, ANNOT_PARAM, ANNOT_TYPE_NAME)


# -----------------------------------------------------------------------------
# Row classes
# -----------------------------------------------------------------------------
class ScheduleRow(object):
    def __init__(self, schedule):
        self.Name = get_elem_name(schedule)
        self.ElementId = schedule.Id.IntegerValue
        self.Marker = ""
        self._schedule = schedule

    @property
    def Schedule(self):
        return self._schedule


class DraftingViewRow(object):
    def __init__(self, view):
        self.Name = get_elem_name(view)
        self.ElementId = view.Id.IntegerValue
        self.Marker = ""
        self._view = view

    @property
    def View(self):
        return self._view


class AssociationRow(object):
    def __init__(self, schedule, view):
        schedule_name = get_elem_name(schedule)
        view_name = get_elem_name(view)

        self.ScheduleName = schedule_name
        self.ViewName = view_name
        self.ScheduleWriteValue = view_name
        self.ViewWriteValue = schedule_name
        self._schedule = schedule
        self._view = view
        self.key = (schedule.Id.IntegerValue, view.Id.IntegerValue)

    @property
    def Schedule(self):
        return self._schedule

    @property
    def View(self):
        return self._view


ALL_SCHEDULE_ROWS = [ScheduleRow(s) for s in note_blocks]
ALL_VIEW_ROWS = [DraftingViewRow(v) for v in drafting_views]


# -----------------------------------------------------------------------------
# XAML
# -----------------------------------------------------------------------------
XAML = """
<Window
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="Associate Schedules and Drafting Views"
    Width="860" Height="680"
    MinWidth="620" MinHeight="520"
    WindowStartupLocation="CenterScreen"
    Background="#1C1B19"
    FontFamily="Segoe UI"
    ResizeMode="CanResizeWithGrip">

    <Window.Resources>

        <Style TargetType="DataGrid">
            <Setter Property="Background"               Value="#201F1D"/>
            <Setter Property="Foreground"               Value="#CDCCCA"/>
            <Setter Property="BorderThickness"          Value="0"/>
            <Setter Property="RowBackground"            Value="#201F1D"/>
            <Setter Property="AlternatingRowBackground" Value="#22211F"/>
            <Setter Property="GridLinesVisibility"      Value="Horizontal"/>
            <Setter Property="HorizontalGridLinesBrush" Value="#262523"/>
            <Setter Property="SelectionMode"            Value="Single"/>
            <Setter Property="SelectionUnit"            Value="FullRow"/>
            <Setter Property="CanUserAddRows"           Value="False"/>
            <Setter Property="CanUserDeleteRows"        Value="False"/>
            <Setter Property="CanUserReorderColumns"    Value="False"/>
            <Setter Property="CanUserSortColumns"       Value="True"/>
            <Setter Property="AutoGenerateColumns"      Value="False"/>
            <Setter Property="HeadersVisibility"        Value="Column"/>
            <Setter Property="FontSize"                 Value="13"/>
            <Setter Property="FocusVisualStyle"         Value="{x:Null}"/>
        </Style>

        <Style TargetType="DataGridColumnHeader">
            <Setter Property="Background"      Value="#171614"/>
            <Setter Property="Foreground"      Value="#797876"/>
            <Setter Property="Padding"         Value="12,7"/>
            <Setter Property="FontSize"        Value="11"/>
            <Setter Property="FontWeight"      Value="SemiBold"/>
            <Setter Property="BorderBrush"     Value="#393836"/>
            <Setter Property="BorderThickness" Value="0,0,1,1"/>
        </Style>

        <Style TargetType="DataGridRow">
            <Setter Property="Cursor" Value="Hand"/>
            <Style.Triggers>
                <Trigger Property="IsSelected" Value="True">
                    <Setter Property="Background" Value="#313B3B"/>
                    <Setter Property="Foreground" Value="#4F98A3"/>
                </Trigger>
                <Trigger Property="IsMouseOver" Value="True">
                    <Setter Property="Background" Value="#2D2C2A"/>
                </Trigger>
            </Style.Triggers>
        </Style>

        <Style TargetType="DataGridCell">
            <Setter Property="BorderThickness"  Value="0"/>
            <Setter Property="FocusVisualStyle" Value="{x:Null}"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="DataGridCell">
                        <Border Padding="12,7"
                                Background="{TemplateBinding Background}">
                            <ContentPresenter/>
                        </Border>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>

        <Style TargetType="TextBox">
            <Setter Property="Background"      Value="#22211F"/>
            <Setter Property="Foreground"      Value="#CDCCCA"/>
            <Setter Property="CaretBrush"      Value="#4F98A3"/>
            <Setter Property="BorderBrush"     Value="#393836"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding"         Value="10,7"/>
            <Setter Property="FontSize"        Value="13"/>
        </Style>

        <Style x:Key="BtnPrimary" TargetType="Button">
            <Setter Property="Background"      Value="#01696F"/>
            <Setter Property="Foreground"      Value="#F9F8F5"/>
            <Setter Property="BorderThickness" Value="0"/>
            <Setter Property="Padding"         Value="24,8"/>
            <Setter Property="FontSize"        Value="13"/>
            <Setter Property="Cursor"          Value="Hand"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border Background="{TemplateBinding Background}"
                                CornerRadius="4" Padding="{TemplateBinding Padding}">
                            <ContentPresenter HorizontalAlignment="Center"
                                              VerticalAlignment="Center"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter Property="Background" Value="#0C4E54"/>
                            </Trigger>
                            <Trigger Property="IsPressed" Value="True">
                                <Setter Property="Background" Value="#0F3638"/>
                            </Trigger>
                            <Trigger Property="IsEnabled" Value="False">
                                <Setter Property="Background" Value="#262523"/>
                                <Setter Property="Foreground"  Value="#5A5957"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>

        <Style x:Key="BtnAssociate" TargetType="Button">
            <Setter Property="Background"      Value="Transparent"/>
            <Setter Property="Foreground"      Value="#4F98A3"/>
            <Setter Property="BorderBrush"     Value="#4F98A3"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding"         Value="24,7"/>
            <Setter Property="FontSize"        Value="13"/>
            <Setter Property="Cursor"          Value="Hand"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border Background="{TemplateBinding Background}"
                                BorderBrush="{TemplateBinding BorderBrush}"
                                BorderThickness="{TemplateBinding BorderThickness}"
                                CornerRadius="4" Padding="{TemplateBinding Padding}">
                            <ContentPresenter HorizontalAlignment="Center"
                                              VerticalAlignment="Center"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter Property="Background" Value="#313B3B"/>
                            </Trigger>
                            <Trigger Property="IsPressed" Value="True">
                                <Setter Property="Background" Value="#1A3535"/>
                            </Trigger>
                            <Trigger Property="IsEnabled" Value="False">
                                <Setter Property="BorderBrush" Value="#393836"/>
                                <Setter Property="Foreground"  Value="#5A5957"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>

        <Style x:Key="BtnGhost" TargetType="Button">
            <Setter Property="Background"      Value="Transparent"/>
            <Setter Property="Foreground"      Value="#797876"/>
            <Setter Property="BorderBrush"     Value="#393836"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding"         Value="24,8"/>
            <Setter Property="FontSize"        Value="13"/>
            <Setter Property="Cursor"          Value="Hand"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border Background="{TemplateBinding Background}"
                                BorderBrush="{TemplateBinding BorderBrush}"
                                BorderThickness="{TemplateBinding BorderThickness}"
                                CornerRadius="4" Padding="{TemplateBinding Padding}">
                            <ContentPresenter HorizontalAlignment="Center"
                                              VerticalAlignment="Center"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter Property="Background" Value="#2D2C2A"/>
                                <Setter Property="Foreground"  Value="#CDCCCA"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>

        <Style x:Key="BtnDanger" TargetType="Button">
            <Setter Property="Background"      Value="Transparent"/>
            <Setter Property="Foreground"      Value="#5A5957"/>
            <Setter Property="BorderBrush"     Value="#393836"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding"         Value="12,4"/>
            <Setter Property="FontSize"        Value="12"/>
            <Setter Property="Cursor"          Value="Hand"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border Background="{TemplateBinding Background}"
                                BorderBrush="{TemplateBinding BorderBrush}"
                                BorderThickness="{TemplateBinding BorderThickness}"
                                CornerRadius="3" Padding="{TemplateBinding Padding}">
                            <ContentPresenter HorizontalAlignment="Center"
                                              VerticalAlignment="Center"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter Property="Background"  Value="#3A1A1A"/>
                                <Setter Property="BorderBrush" Value="#A13544"/>
                                <Setter Property="Foreground"  Value="#DD6974"/>
                            </Trigger>
                            <Trigger Property="IsEnabled" Value="False">
                                <Setter Property="Foreground" Value="#393836"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>

    </Window.Resources>

    <Grid Margin="16,14,16,16">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="150"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0" Margin="0,0,0,14">
            <TextBlock Text="Associate Schedules and Drafting Views"
                       FontSize="17" FontWeight="SemiBold" Foreground="#CDCCCA"/>
            <StackPanel Orientation="Horizontal" Margin="0,3,0,0">
                <TextBlock FontSize="12" Foreground="#5A5957">
                    <Run Text="Writes "/>
                    <Run Text="ID_ViewName_TXT" Foreground="#4F98A3" FontWeight="SemiBold"/>
                    <Run Text=" on schedules and views  |  Writes "/>
                    <Run Text="CT_ScheduleTag_TXT" Foreground="#4F98A3" FontWeight="SemiBold"/>
                    <Run Text=" on M - BAS, IO Type annotations | Filters schedules by that value"/>
                </TextBlock>
            </StackPanel>
        </StackPanel>

        <Grid Grid.Row="1">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="12"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>

            <Grid Grid.Column="0">
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="*"/>
                    <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>
                <TextBlock Grid.Row="0" Text="BAS SCHEDULE"
                           FontSize="11" FontWeight="SemiBold"
                           Foreground="#4F98A3" Margin="0,0,0,8"/>
                <Grid Grid.Row="1" Margin="0,0,0,6">
                    <TextBox x:Name="ScheduleSearchBox"/>
                    <TextBlock x:Name="SchedulePlaceholder"
                               Text="Filter schedules..." Foreground="#5A5957"
                               FontSize="13" Margin="11,7,0,0"
                               IsHitTestVisible="False"/>
                </Grid>
                <Border Grid.Row="2" BorderBrush="#393836" BorderThickness="1"
                        CornerRadius="4" ClipToBounds="True">
                    <DataGrid x:Name="ScheduleGrid">
                        <DataGrid.Columns>
                            <DataGridTextColumn Width="28"
                                                Binding="{Binding Marker}"
                                                IsReadOnly="True"
                                                CanUserResize="False"
                                                CanUserSort="False">
                                <DataGridTextColumn.ElementStyle>
                                    <Style TargetType="TextBlock">
                                        <Setter Property="Foreground"          Value="#4F98A3"/>
                                        <Setter Property="HorizontalAlignment" Value="Center"/>
                                        <Setter Property="FontSize"            Value="10"/>
                                    </Style>
                                </DataGridTextColumn.ElementStyle>
                            </DataGridTextColumn>
                            <DataGridTextColumn Header="SCHEDULE NAME"
                                                Binding="{Binding Name}"
                                                Width="*" IsReadOnly="True"/>
                            <DataGridTextColumn Header="ID"
                                                Binding="{Binding ElementId}"
                                                Width="72" IsReadOnly="True"/>
                        </DataGrid.Columns>
                    </DataGrid>
                </Border>
                <TextBlock x:Name="ScheduleCountLabel" Grid.Row="3"
                           FontSize="11" Foreground="#5A5957" Margin="0,5,0,0"/>
            </Grid>

            <Rectangle Grid.Column="1" Width="1" Fill="#393836"
                       HorizontalAlignment="Center" Margin="0,24,0,0"/>

            <Grid Grid.Column="2">
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="*"/>
                    <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>
                <TextBlock Grid.Row="0" Text="DRAFTING VIEW"
                           FontSize="11" FontWeight="SemiBold"
                           Foreground="#4F98A3" Margin="0,0,0,8"/>
                <Grid Grid.Row="1" Margin="0,0,0,6">
                    <TextBox x:Name="ViewSearchBox"/>
                    <TextBlock x:Name="ViewPlaceholder"
                               Text="Filter views..." Foreground="#5A5957"
                               FontSize="13" Margin="11,7,0,0"
                               IsHitTestVisible="False"/>
                </Grid>
                <Border Grid.Row="2" BorderBrush="#393836" BorderThickness="1"
                        CornerRadius="4" ClipToBounds="True">
                    <DataGrid x:Name="ViewGrid">
                        <DataGrid.Columns>
                            <DataGridTextColumn Width="28"
                                                Binding="{Binding Marker}"
                                                IsReadOnly="True"
                                                CanUserResize="False"
                                                CanUserSort="False">
                                <DataGridTextColumn.ElementStyle>
                                    <Style TargetType="TextBlock">
                                        <Setter Property="Foreground"          Value="#4F98A3"/>
                                        <Setter Property="HorizontalAlignment" Value="Center"/>
                                        <Setter Property="FontSize"            Value="10"/>
                                    </Style>
                                </DataGridTextColumn.ElementStyle>
                            </DataGridTextColumn>
                            <DataGridTextColumn Header="VIEW NAME"
                                                Binding="{Binding Name}"
                                                Width="*" IsReadOnly="True"/>
                            <DataGridTextColumn Header="ID"
                                                Binding="{Binding ElementId}"
                                                Width="72" IsReadOnly="True"/>
                        </DataGrid.Columns>
                    </DataGrid>
                </Border>
                <TextBlock x:Name="ViewCountLabel" Grid.Row="3"
                           FontSize="11" Foreground="#5A5957" Margin="0,5,0,0"/>
            </Grid>
        </Grid>

        <Grid Grid.Row="2" Margin="0,10,0,0">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>
            <Rectangle Grid.Column="0" Height="1" Fill="#393836" VerticalAlignment="Center"/>
            <Button x:Name="AssociateBtn" Grid.Column="1"
                    Content="+ Associate" IsEnabled="False"
                    Margin="12,0" Style="{StaticResource BtnAssociate}"/>
            <Rectangle Grid.Column="2" Height="1" Fill="#393836" VerticalAlignment="Center"/>
        </Grid>

        <Grid Grid.Row="3" Margin="0,10,0,6">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <StackPanel Grid.Column="0" Orientation="Horizontal">
                <TextBlock Text="GROUP" FontSize="11" FontWeight="SemiBold"
                           Foreground="#4F98A3" VerticalAlignment="Center"/>
                <TextBlock x:Name="GroupCountLabel" FontSize="11"
                           Foreground="#5A5957" VerticalAlignment="Center"
                           Margin="8,0,0,0"/>
            </StackPanel>
            <Button x:Name="RemoveBtn" Grid.Column="1"
                    Content="Remove Selected" IsEnabled="False"
                    Style="{StaticResource BtnDanger}"/>
        </Grid>

        <Border Grid.Row="4" BorderBrush="#393836" BorderThickness="1"
                CornerRadius="4" ClipToBounds="True">
            <DataGrid x:Name="GroupGrid">
                <DataGrid.Columns>
                    <DataGridTextColumn Header="NOTE BLOCK SCHEDULE"
                                        Binding="{Binding ScheduleName}"
                                        Width="*" IsReadOnly="True"/>
                    <DataGridTextColumn Header="DRAFTING VIEW"
                                        Binding="{Binding ViewName}"
                                        Width="*" IsReadOnly="True"/>
                    <DataGridTextColumn Header="SCHEDULE &lt;- VIEW NAME"
                                        Binding="{Binding ScheduleWriteValue}"
                                        Width="180" IsReadOnly="True"/>
                    <DataGridTextColumn Header="VIEW &lt;- SCHEDULE NAME"
                                        Binding="{Binding ViewWriteValue}"
                                        Width="180" IsReadOnly="True"/>
                </DataGrid.Columns>
            </DataGrid>
        </Border>

        <Grid Grid.Row="5" Margin="0,12,0,0">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="8"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <TextBlock x:Name="FooterLabel" Grid.Column="0"
                       VerticalAlignment="Center" FontSize="12"
                       Foreground="#5A5957"/>
            <Button x:Name="CancelBtn" Grid.Column="1"
                    Content="Cancel" Style="{StaticResource BtnGhost}"/>
            <Button x:Name="RunBtn" Grid.Column="3"
                    Content="Run" IsEnabled="False"
                    Style="{StaticResource BtnPrimary}"/>
        </Grid>
    </Grid>
</Window>
"""


# -----------------------------------------------------------------------------
# Window controller
# -----------------------------------------------------------------------------
class DualSelectorWindow(object):
    def __init__(self, schedule_rows, view_rows):
        self._all_schedule_rows = schedule_rows
        self._all_view_rows = view_rows
        self.associations = []
        self._confirmed = False
        self._win = XamlReader.Parse(XAML)

        self._sched_grid = self._win.FindName("ScheduleGrid")
        self._view_grid = self._win.FindName("ViewGrid")
        self._sched_srch = self._win.FindName("ScheduleSearchBox")
        self._view_srch = self._win.FindName("ViewSearchBox")
        self._sched_ph = self._win.FindName("SchedulePlaceholder")
        self._view_ph = self._win.FindName("ViewPlaceholder")
        self._sched_count = self._win.FindName("ScheduleCountLabel")
        self._view_count = self._win.FindName("ViewCountLabel")
        self._assoc_btn = self._win.FindName("AssociateBtn")
        self._group_grid = self._win.FindName("GroupGrid")
        self._group_count = self._win.FindName("GroupCountLabel")
        self._remove_btn = self._win.FindName("RemoveBtn")
        self._footer_lbl = self._win.FindName("FooterLabel")
        self._run_btn = self._win.FindName("RunBtn")
        self._cancel_btn = self._win.FindName("CancelBtn")

        self._sched_rows = ObservableCollection[object]()
        self._view_rows = ObservableCollection[object]()
        self._group_rows = ObservableCollection[object]()

        for row in self._all_schedule_rows:
            self._sched_rows.Add(row)
        for row in self._all_view_rows:
            self._view_rows.Add(row)

        self._sched_grid.ItemsSource = self._sched_rows
        self._view_grid.ItemsSource = self._view_rows
        self._group_grid.ItemsSource = self._group_rows

        self._wire_events()
        self._refresh_all()

    def _wire_events(self):
        self._sched_srch.TextChanged += self._on_sched_search
        self._sched_srch.GotFocus += lambda s, e: self._hide_placeholder(self._sched_ph)
        self._sched_srch.LostFocus += lambda s, e: self._show_placeholder_if_empty(self._sched_srch, self._sched_ph)

        self._view_srch.TextChanged += self._on_view_search
        self._view_srch.GotFocus += lambda s, e: self._hide_placeholder(self._view_ph)
        self._view_srch.LostFocus += lambda s, e: self._show_placeholder_if_empty(self._view_srch, self._view_ph)

        self._sched_grid.SelectionChanged += self._on_panel_selection_changed
        self._view_grid.SelectionChanged += self._on_panel_selection_changed
        self._group_grid.SelectionChanged += self._on_group_selection_changed

        self._assoc_btn.Click += self._on_associate
        self._remove_btn.Click += self._on_remove
        self._run_btn.Click += self._on_run
        self._cancel_btn.Click += self._on_cancel
        self._win.KeyDown += self._on_key_down

    def _hide_placeholder(self, placeholder):
        placeholder.Visibility = System.Windows.Visibility.Collapsed

    def _show_placeholder_if_empty(self, textbox, placeholder):
        if not textbox.Text:
            placeholder.Visibility = System.Windows.Visibility.Visible

    def _set_placeholder_visibility(self, textbox, placeholder):
        placeholder.Visibility = (
            System.Windows.Visibility.Collapsed
            if textbox.Text else
            System.Windows.Visibility.Visible
        )

    def _existing_keys(self):
        return {row.key for row in self._group_rows}

    def _refresh_schedule_count(self):
        shown = self._sched_rows.Count
        total = len(self._all_schedule_rows)
        self._sched_count.Text = (
            "{} {}".format(shown, pluralize(shown, "schedule"))
            if shown == total else
            "{} of {} schedules".format(shown, total)
        )

    def _refresh_view_count(self):
        shown = self._view_rows.Count
        total = len(self._all_view_rows)
        self._view_count.Text = (
            "{} {}".format(shown, pluralize(shown, "view"))
            if shown == total else
            "{} of {} views".format(shown, total)
        )

    def _refresh_group_count(self):
        count = self._group_rows.Count
        self._group_count.Text = (
            u"\u2014 empty" if count == 0 else
            "{} {}".format(count, pluralize(count, "association"))
        )

    def _refresh_associate_button(self):
        schedule_row = self._sched_grid.SelectedItem
        view_row = self._view_grid.SelectedItem
        self._assoc_btn.IsEnabled = bool(
            schedule_row is not None
            and view_row is not None
            and (schedule_row.ElementId, view_row.ElementId) not in self._existing_keys()
        )

    def _refresh_footer(self):
        count = self._group_rows.Count
        self._run_btn.IsEnabled = count > 0
        self._footer_lbl.Text = (
            "{} {} ready to run".format(count, pluralize(count, "association"))
            if count > 0 else
            "Build associations in the group to continue"
        )

    def _refresh_markers(self):
        associated_schedule_ids = {row.key[0] for row in self._group_rows}
        associated_view_ids = {row.key[1] for row in self._group_rows}

        for row in self._all_schedule_rows:
            row.Marker = BULLET if row.ElementId in associated_schedule_ids else ""
        for row in self._all_view_rows:
            row.Marker = BULLET if row.ElementId in associated_view_ids else ""

    def _restore_selection(self, grid, rows, selected_id):
        if selected_id is None:
            return
        for row in rows:
            if row.ElementId == selected_id:
                grid.SelectedItem = row
                break

    def _apply_filters(self):
        selected_schedule = self._sched_grid.SelectedItem
        selected_view = self._view_grid.SelectedItem
        selected_schedule_id = selected_schedule.ElementId if selected_schedule else None
        selected_view_id = selected_view.ElementId if selected_view else None

        filter_rows(self._all_schedule_rows, self._sched_srch.Text, self._sched_rows)
        filter_rows(self._all_view_rows, self._view_srch.Text, self._view_rows)

        self._restore_selection(self._sched_grid, self._sched_rows, selected_schedule_id)
        self._restore_selection(self._view_grid, self._view_rows, selected_view_id)

    def _refresh_all(self):
        self._refresh_markers()
        self._apply_filters()
        self._set_placeholder_visibility(self._sched_srch, self._sched_ph)
        self._set_placeholder_visibility(self._view_srch, self._view_ph)
        self._refresh_schedule_count()
        self._refresh_view_count()
        self._refresh_group_count()
        self._refresh_associate_button()
        self._refresh_footer()
        self._remove_btn.IsEnabled = self._group_grid.SelectedItem is not None

    def _on_sched_search(self, sender, e):
        self._apply_filters()
        self._set_placeholder_visibility(self._sched_srch, self._sched_ph)
        self._refresh_schedule_count()
        self._refresh_associate_button()

    def _on_view_search(self, sender, e):
        self._apply_filters()
        self._set_placeholder_visibility(self._view_srch, self._view_ph)
        self._refresh_view_count()
        self._refresh_associate_button()

    def _on_panel_selection_changed(self, sender, e):
        self._refresh_associate_button()

    def _on_group_selection_changed(self, sender, e):
        self._remove_btn.IsEnabled = self._group_grid.SelectedItem is not None

    def _on_associate(self, sender, e):
        schedule_row = self._sched_grid.SelectedItem
        view_row = self._view_grid.SelectedItem
        if schedule_row is None or view_row is None:
            return

        association = AssociationRow(schedule_row.Schedule, view_row.View)
        if association.key in self._existing_keys():
            return

        self._group_rows.Add(association)
        self._group_grid.ScrollIntoView(association)
        self._refresh_all()

    def _on_remove(self, sender, e):
        selected_row = self._group_grid.SelectedItem
        if selected_row is None:
            return

        self._group_rows.Remove(selected_row)
        self._refresh_all()

    def _on_run(self, sender, e):
        if self._group_rows.Count <= 0:
            return

        self.associations = list(self._group_rows)
        self._confirmed = True
        self._win.Close()

    def _on_cancel(self, sender, e):
        self._win.Close()

    def _on_key_down(self, sender, e):
        if e.Key == Input.Key.Delete and self._group_grid.IsKeyboardFocusWithin:
            self._on_remove(None, None)
        elif e.Key == Input.Key.Return:
            self._on_run(None, None)
        elif e.Key == Input.Key.Escape:
            self._win.Close()

    def show(self):
        self._win.ShowDialog()
        if self._confirmed:
            return [(row.Schedule, row.View) for row in self.associations]
        return []


# -----------------------------------------------------------------------------
# Launch UI
# -----------------------------------------------------------------------------
selector = DualSelectorWindow(ALL_SCHEDULE_ROWS, ALL_VIEW_ROWS)
pairs = selector.show()

if not pairs:
    forms.alert("No associations were made.", exitscript=True)


# -----------------------------------------------------------------------------
# Preflight validation
# -----------------------------------------------------------------------------
schedule_filter_field_ids = {}
validation_errors = []
seen_annotation_ids = set()

for schedule, view in pairs:
    schedule_name = get_elem_name(schedule)
    view_name = get_elem_name(view)

    schedule_label = "schedule '{}'".format(schedule_name)
    view_label = "view '{}'".format(view_name)

    try:
        ensure_parameter_writable(schedule, VIEW_NAME_PARAM, schedule_label)
        ensure_parameter_writable(view, VIEW_NAME_PARAM, view_label)
    except:
        raise

    field_id = get_schedule_field_id_by_param_name(schedule, ANNOT_PARAM)
    if field_id is None:
        validation_errors.append(
            "Schedule '{}' does not contain a schedulable field for '{}'.".format(
                schedule_name, ANNOT_PARAM
            )
        )
    else:
        schedule_filter_field_ids[schedule.Id.IntegerValue] = field_id

    for annotation in annotation_by_view_id.get(view.Id.IntegerValue, []):
        annotation_id = annotation.Id.IntegerValue
        if annotation_id in seen_annotation_ids:
            continue

        annotation_param = annotation.LookupParameter(ANNOT_PARAM)
        if annotation_param is None:
            validation_errors.append(
                "Annotation ID {} on drafting view '{}' does not have parameter '{}'.".format(
                    annotation_id, view_name, ANNOT_PARAM
                )
            )
        elif annotation_param.IsReadOnly:
            validation_errors.append(
                "Annotation ID {} on drafting view '{}' has read-only parameter '{}'.".format(
                    annotation_id, view_name, ANNOT_PARAM
                )
            )

        seen_annotation_ids.add(annotation_id)

if validation_errors:
    forms.alert(
        "Validation failed:\n\n- " + "\n- ".join(validation_errors),
        title="Preflight Failed",
        exitscript=True
    )


# -----------------------------------------------------------------------------
# Write values
# -----------------------------------------------------------------------------
sched_updated = 0
view_updated = 0
annot_updated = 0
filter_updated = 0

processed_annotation_ids = set()

with revit.Transaction("Associate schedules/views and update filters"):
    for schedule, view in pairs:
        view_name = get_elem_name(view)
        schedule_name = get_elem_name(schedule)

        schedule_param = schedule.LookupParameter(VIEW_NAME_PARAM)
        schedule_param.Set(view_name)
        sched_updated += 1

        view_param = view.LookupParameter(VIEW_NAME_PARAM)
        view_param.Set(schedule_name)
        view_updated += 1

        filter_field_id = schedule_filter_field_ids[schedule.Id.IntegerValue]
        upsert_schedule_equals_filter(schedule, filter_field_id, view_name)
        filter_updated += 1

        for annotation in annotation_by_view_id.get(view.Id.IntegerValue, []):
            annotation_id = annotation.Id.IntegerValue
            if annotation_id in processed_annotation_ids:
                continue

            annotation_param = annotation.LookupParameter(ANNOT_PARAM)
            annotation_param.Set(view_name)
            annot_updated += 1
            processed_annotation_ids.add(annotation_id)


# -----------------------------------------------------------------------------
# Report
# -----------------------------------------------------------------------------
lines = [
    "SCHEDULES ({})".format(VIEW_NAME_PARAM),
    "  Written   : {}".format(sched_updated),
    "",
    "VIEWS ({})".format(VIEW_NAME_PARAM),
    "  Written   : {}".format(view_updated),
    "",
    "SCHEDULE FILTERS ({})".format(ANNOT_PARAM),
    "  Updated   : {}".format(filter_updated),
    "",
    "ANNOTATIONS ({} -> {})".format(ANNOT_TYPE_NAME, ANNOT_PARAM),
    "  Written   : {}".format(annot_updated),
]
