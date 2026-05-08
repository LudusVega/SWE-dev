# -*- coding: utf-8 -*-
import os
import sys
import clr
import datetime
import threading as py_threading

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System.Xaml")

import sync_queue as sq

from System import Action
from System.Windows.Markup import XamlReader
from System.Windows.Threading import DispatcherPriority
from System.Collections.ObjectModel import ObservableCollection
from System.Windows.Media import SolidColorBrush, ColorConverter
from Autodesk.Revit.UI import (
    IDockablePaneProvider,
    DockablePaneId,
    DockablePaneState,
    DockPosition,
    DockablePanes,
    IExternalEventHandler,
    ExternalEvent,
    TaskDialog
)
import System

from pyrevit import DB, revit, HOST_APP, script, forms, coreutils
from pyrevit.compat import get_elementid_value_func

PANE_GUID = System.Guid("A1B2C3D4-E5F6-7890-ABCD-EF1234567890")
PANE_ID = DockablePaneId(PANE_GUID)

logger = script.get_logger()
view_cache = []

XAML = """
<Page
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Background="#2B2B2B"
    FontFamily="Segoe UI">

    <Page.Resources>

        <SolidColorBrush x:Key="PanelBg" Color="#2B2B2B"/>
        <SolidColorBrush x:Key="PanelBg2" Color="#333333"/>
        <SolidColorBrush x:Key="PanelBorder" Color="#3F3F3F"/>
        <SolidColorBrush x:Key="TextPrimary" Color="#F0F0F0"/>
        <SolidColorBrush x:Key="TextMuted" Color="#C8C8C8"/>
        <SolidColorBrush x:Key="TextDim" Color="#9A9A9A"/>

        <Style x:Key="PanelButton" TargetType="Button">
            <Setter Property="Height" Value="28"/>
            <Setter Property="MinWidth" Value="72"/>
            <Setter Property="Margin" Value="0,0,6,0"/>
            <Setter Property="Padding" Value="12,0"/>
            <Setter Property="Foreground" Value="{StaticResource TextPrimary}"/>
            <Setter Property="Background" Value="{StaticResource PanelBg2}"/>
            <Setter Property="BorderBrush" Value="{StaticResource PanelBorder}"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="FontSize" Value="12"/>
            <Setter Property="Template">
                <Setter.Value>
                    <ControlTemplate TargetType="Button">
                        <Border Background="{TemplateBinding Background}"
                                BorderBrush="{TemplateBinding BorderBrush}"
                                BorderThickness="{TemplateBinding BorderThickness}"
                                CornerRadius="2">
                            <ContentPresenter HorizontalAlignment="Center"
                                              VerticalAlignment="Center"
                                              Margin="{TemplateBinding Padding}"/>
                        </Border>
                        <ControlTemplate.Triggers>
                            <Trigger Property="IsMouseOver" Value="True">
                                <Setter Property="Background" Value="#3A3A3A"/>
                                <Setter Property="BorderBrush" Value="#5A5A5A"/>
                            </Trigger>
                            <Trigger Property="IsPressed" Value="True">
                                <Setter Property="Background" Value="#1F1F1F"/>
                            </Trigger>
                            <Trigger Property="IsEnabled" Value="False">
                                <Setter Property="Opacity" Value="0.45"/>
                            </Trigger>
                        </ControlTemplate.Triggers>
                    </ControlTemplate>
                </Setter.Value>
            </Setter>
        </Style>

        <Style x:Key="PrimaryPanelButton" TargetType="Button" BasedOn="{StaticResource PanelButton}">
            <Setter Property="Background" Value="#3B6EA8"/>
            <Setter Property="BorderBrush" Value="#5C93D1"/>
            <Setter Property="Foreground" Value="#FFFFFF"/>
        </Style>

        <Style x:Key="DangerPanelButton" TargetType="Button" BasedOn="{StaticResource PanelButton}">
            <Setter Property="Background" Value="#4A3535"/>
            <Setter Property="BorderBrush" Value="#6A4B4B"/>
            <Setter Property="Foreground" Value="#F2DADA"/>
        </Style>

        <Style TargetType="DataGrid">
            <Setter Property="Background" Value="{StaticResource PanelBg2}"/>
            <Setter Property="Foreground" Value="{StaticResource TextPrimary}"/>
            <Setter Property="BorderBrush" Value="{StaticResource PanelBorder}"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="RowBackground" Value="#2F2F2F"/>
            <Setter Property="AlternatingRowBackground" Value="#313131"/>
            <Setter Property="GridLinesVisibility" Value="Horizontal"/>
            <Setter Property="HorizontalGridLinesBrush" Value="#3D3D3D"/>
            <Setter Property="HeadersVisibility" Value="Column"/>
            <Setter Property="CanUserAddRows" Value="False"/>
            <Setter Property="CanUserDeleteRows" Value="False"/>
            <Setter Property="CanUserResizeRows" Value="False"/>
            <Setter Property="SelectionMode" Value="Single"/>
            <Setter Property="SelectionUnit" Value="FullRow"/>
            <Setter Property="RowHeaderWidth" Value="0"/>
        </Style>

        <Style TargetType="DataGridColumnHeader">
            <Setter Property="Background" Value="#373737"/>
            <Setter Property="Foreground" Value="{StaticResource TextMuted}"/>
            <Setter Property="BorderBrush" Value="{StaticResource PanelBorder}"/>
            <Setter Property="BorderThickness" Value="0,0,0,1"/>
            <Setter Property="Padding" Value="8,6"/>
            <Setter Property="FontSize" Value="11"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
        </Style>

        <Style TargetType="DataGridCell">
            <Setter Property="BorderThickness" Value="0"/>
            <Setter Property="Padding" Value="8,5"/>
            <Setter Property="Foreground" Value="{StaticResource TextPrimary}"/>
        </Style>

        <Style TargetType="DataGridRow">
            <Setter Property="Background" Value="#2F2F2F"/>
            <Setter Property="Foreground" Value="{StaticResource TextPrimary}"/>
            <Setter Property="Height" Value="28"/>
            <Style.Triggers>
                <Trigger Property="IsMouseOver" Value="True">
                    <Setter Property="Background" Value="#383838"/>
                </Trigger>
                <Trigger Property="IsSelected" Value="True">
                    <Setter Property="Background" Value="#2F4F73"/>
                    <Setter Property="Foreground" Value="#FFFFFF"/>
                </Trigger>
            </Style.Triggers>
        </Style>

    </Page.Resources>

    <Grid Margin="10">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <Border x:Name="TurnBanner"
                Grid.Row="0"
                Background="#3A3A3A"
                BorderBrush="#4A4A4A"
                BorderThickness="1"
                Padding="8,6"
                Margin="0,0,0,8">
            <TextBlock x:Name="TurnBannerText"
                       Foreground="#D0D0D0"
                       FontSize="12"
                       FontWeight="SemiBold"
                       Text="Waiting for your turn"/>
        </Border>

        <TextBlock x:Name="StatusText"
                   Grid.Row="1"
                   Margin="0,0,0,8"
                   Foreground="{StaticResource TextDim}"
                   FontSize="11"
                   Text="Ready"/>

        <DataGrid x:Name="QueueGrid"
                  Grid.Row="2"
                  AutoGenerateColumns="False"
                  Margin="0,0,0,8">
            <DataGrid.Columns>
                <DataGridTextColumn Header="User" Binding="{Binding User}" Width="*"/>
                <DataGridTextColumn Header="Joined" Binding="{Binding Joined}" Width="120"/>
            </DataGrid.Columns>
        </DataGrid>

        <Grid Grid.Row="3">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>

            <TextBlock x:Name="FooterText"
                       Grid.Column="0"
                       VerticalAlignment="Center"
                       Foreground="{StaticResource TextDim}"
                       FontSize="10"
                       Text=""/>

            <Button x:Name="JoinBtn"
                    Grid.Column="1"
                    Content="Join"
                    Style="{StaticResource PanelButton}"/>

            <Button x:Name="LeaveBtn"
                    Grid.Column="2"
                    Content="Leave"
                    Style="{StaticResource PanelButton}"
                    IsEnabled="False"/>

            <Button x:Name="RefreshBtn"
                    Grid.Column="3"
                    Content="Refresh"
                    Style="{StaticResource PanelButton}"/>

            <Button x:Name="ClearBtn"
                    Grid.Column="4"
                    Content="Clear"
                    Style="{StaticResource DangerPanelButton}"/>

            <Button x:Name="SyncBtn"
                    Grid.Column="5"
                    Content="Sync Now"
                    Style="{StaticResource PrimaryPanelButton}"
                    Margin="0,0,0,0"
                    IsEnabled="False"/>
        </Grid>
    </Grid>
</Page>
"""


class QueueRow(object):
    def __init__(self, user, joined):
        self.User = user
        self.Joined = joined


def get_view_handling():
    my_config = script.get_config()
    try:
        return getattr(my_config, "view_handling")
    except Exception:
        setattr(my_config, "view_handling", "nothing")
        script.save_config()
        return "nothing"


def close_inactive_views(view_handling="nothing", document=None):
    if view_handling == "nothing" or document is None:
        return

    del view_cache[:]

    starting_view_id = DB.StartingViewSettings.GetStartingViewSettings(document).ViewId
    starting_view = document.GetElement(starting_view_id)

    if starting_view:
        uidoc = HOST_APP.uidoc
        HOST_APP.uidoc.RequestViewChange(starting_view)
        uidoc.ActiveView = starting_view
        all_open_views = uidoc.GetOpenUIViews()
        for ui_view in all_open_views:
            if view_handling == "reopen":
                view_cache.append(ui_view.ViewId)
            doc_view = document.GetElement(ui_view.ViewId)
            if doc_view.Id != starting_view.Id:
                ui_view.Close()
    else:
        forms.show_balloon("No Starting View Set", "No Starting View Set")


def set_active_view(view):
    if not isinstance(view, DB.View):
        raise TypeError('Element [{}] is not a View!'.format(view.Id))
    name = view.Name
    if view.ViewType != DB.ViewType.Internal and \
            view.ViewType != DB.ViewType.ProjectBrowser:
        revit.uidoc.ActiveView = view
        logger.debug('Active View is: {}'.format(view.Name))
        return name
    else:
        logger.info('View {} ({}) cannot be activated.'.format(name, view.ViewType))
        return 'INTERNAL / PB: ' + name


class SyncExternalEventHandler(IExternalEventHandler):
    def __init__(self):
        self.panel = None

    def Execute(self, uiapp):
        doc = None
        try:
            if uiapp.ActiveUIDocument:
                doc = uiapp.ActiveUIDocument.Document
        except Exception:
            doc = None

        if not doc:
            TaskDialog.Show("Sync Queue", "No active document found.")
            return

        if not (doc.IsWorkshared and not doc.IsFamilyDocument and not doc.IsLinked):
            TaskDialog.Show("Sync Queue", "Current document is not workshared and was not synced.")
            return

        try:
            timer = coreutils.Timer()
            view_handling = get_view_handling()

            close_inactive_views(view_handling, doc)

            trans_options = DB.TransactWithCentralOptions()
            sync_options = DB.SynchronizeWithCentralOptions()
            relinquish_all = True
            relinquish_options = DB.RelinquishOptions(relinquish_all)
            reload_latest_options = DB.ReloadLatestOptions()
            save_options = DB.SaveOptions()

            sync_options.SetRelinquishOptions(relinquish_options)
            sync_options.Compact = True
            sync_options.Comment = "Synchronisation from pyRevit Sync Queue"

            doc.Save(save_options)
            doc.ReloadLatest(reload_latest_options)
            doc.Save(save_options)
            doc.SynchronizeWithCentral(trans_options, sync_options)

            if view_handling == "reopen":
                for v_id in view_cache:
                    view = doc.GetElement(v_id)
                    try:
                        set_active_view(view)
                    except Exception:
                        get_elementid_value = get_elementid_value_func()
                        logger.warn(
                            "Failed to reopen view {}".format(get_elementid_value(v_id))
                        )

            endtime = timer.get_time()
            endtime_hms = str(datetime.timedelta(seconds=endtime).seconds)
            forms.show_balloon(
                "Synchronisation took {}s.".format(endtime_hms),
                "{}s. to synchronize".format(endtime_hms)
            )

            try:
                username = doc.Application.Username
            except Exception:
                username = "Unknown"

            model = doc.Title

            try:
                sq.leave_queue(doc, username, model)
            except Exception:
                pass

            if self.panel:
                self.panel.refresh()

        except Exception as ex:
            TaskDialog.Show("Sync Queue", "Sync failed:\n{}".format(str(ex)))

    def GetName(self):
        return "Sync Queue External Event Handler"


class SyncQueueDockablePanel(IDockablePaneProvider):
    def __init__(self):
        self._uiapp = None
        self._page = None
        self._grid = None
        self._join_btn = None
        self._leave_btn = None
        self._refresh_btn = None
        self._clear_btn = None
        self._sync_btn = None
        self._status = None
        self._footer = None
        self._turn_banner = None
        self._turn_banner_text = None
        self._auto_refresh = False

    def set_uiapp(self, uiapp):
        self._uiapp = uiapp

    def SetupDockablePane(self, data):
        self._build_ui()
        data.FrameworkElement = self._page

        state = DockablePaneState()
        state.DockPosition = DockPosition.Tabbed
        state.TabBehind = DockablePanes.BuiltInDockablePanes.ProjectBrowser
        data.InitialState = state

    def _build_ui(self):
        if self._page:
            return

        self._page = XamlReader.Parse(XAML)
        self._grid = self._page.FindName("QueueGrid")
        self._join_btn = self._page.FindName("JoinBtn")
        self._leave_btn = self._page.FindName("LeaveBtn")
        self._refresh_btn = self._page.FindName("RefreshBtn")
        self._clear_btn = self._page.FindName("ClearBtn")
        self._sync_btn = self._page.FindName("SyncBtn")
        self._status = self._page.FindName("StatusText")
        self._footer = self._page.FindName("FooterText")
        self._turn_banner = self._page.FindName("TurnBanner")
        self._turn_banner_text = self._page.FindName("TurnBannerText")

        self._join_btn.Click += self.on_join
        self._leave_btn.Click += self.on_leave
        self._refresh_btn.Click += self.on_refresh
        self._clear_btn.Click += self.on_clear
        self._sync_btn.Click += self.on_sync

        self.refresh()
        self._start_auto_refresh()

    def _get_active_doc(self):
        try:
            if self._uiapp and self._uiapp.ActiveUIDocument:
                return self._uiapp.ActiveUIDocument.Document
        except Exception:
            return None
        return None

    def _get_user_model(self):
        doc = self._get_active_doc()
        if not doc:
            return None, None
        try:
            username = doc.Application.Username
        except Exception:
            username = "Unknown"
        model = doc.Title
        return username, model

    def _is_my_turn(self):
        doc = self._get_active_doc()
        user, model = self._get_user_model()
        if not doc or not user or not model:
            return False

        queue = sq.get_queue(doc)
        if not queue:
            return False

        first = queue[0]
        return first.get("username") == user and first.get("model") == model

    def refresh(self):
        doc = self._get_active_doc()

        if not doc:
            if self._grid:
                self._grid.ItemsSource = ObservableCollection[object]()
            if self._status:
                self._status.Text = "No active document."
            if self._footer:
                self._footer.Text = "Waiting for active model"
            if self._sync_btn:
                self._sync_btn.IsEnabled = False
            if self._leave_btn:
                self._leave_btn.IsEnabled = False
            if self._turn_banner and self._turn_banner_text:
                self._turn_banner.Background = SolidColorBrush(ColorConverter.ConvertFromString("#3A3A3A"))
                self._turn_banner.BorderBrush = SolidColorBrush(ColorConverter.ConvertFromString("#4A4A4A"))
                self._turn_banner_text.Foreground = SolidColorBrush(ColorConverter.ConvertFromString("#D0D0D0"))
                self._turn_banner_text.Text = "Waiting for active model"
            return

        bumped, queue = sq.bump_first_if_needed(doc)

        rows = ObservableCollection[object]()
        for item in queue:
            rows.Add(QueueRow(
                item.get("username", ""),
                item.get("joined", "")
            ))

        if self._grid:
            self._grid.ItemsSource = rows

        myturn = self._is_my_turn()
        user, model = self._get_user_model()
        remaining = sq.get_first_timeout_remaining(doc)

        in_queue = any(
            q.get("username") == user and q.get("model") == model
            for q in queue
        )

        if self._turn_banner and self._turn_banner_text:
            if myturn:
                self._turn_banner.Background = SolidColorBrush(ColorConverter.ConvertFromString("#2F5A2F"))
                self._turn_banner.BorderBrush = SolidColorBrush(ColorConverter.ConvertFromString("#4D8A4D"))
                self._turn_banner_text.Foreground = SolidColorBrush(ColorConverter.ConvertFromString("#FFFFFF"))
                self._turn_banner_text.Text = "It is your turn to Sync with Central"
            else:
                self._turn_banner.Background = SolidColorBrush(ColorConverter.ConvertFromString("#3A3A3A"))
                self._turn_banner.BorderBrush = SolidColorBrush(ColorConverter.ConvertFromString("#4A4A4A"))
                self._turn_banner_text.Foreground = SolidColorBrush(ColorConverter.ConvertFromString("#D0D0D0"))
                self._turn_banner_text.Text = "Waiting for your turn"

        if self._status:
            if bumped:
                self._status.Text = "First user timed out and was moved to the back."
            elif myturn:
                self._status.Text = "It is your turn to sync. Time remaining: {}s".format(remaining)
            elif queue:
                if in_queue:
                    pos = 0
                    for idx, q in enumerate(queue):
                        if q.get("username") == user and q.get("model") == model:
                            pos = idx + 1
                            break
                    self._status.Text = "You are #{} in the queue.".format(pos)
                else:
                    self._status.Text = "Queue active."
            else:
                self._status.Text = "Queue is empty."

        if self._footer:
            if remaining is None:
                self._footer.Text = "Updated {}".format(
                    datetime.datetime.now().strftime("%H:%M:%S")
                )
            else:
                self._footer.Text = "First slot timeout: {}s | Updated {}".format(
                    remaining,
                    datetime.datetime.now().strftime("%H:%M:%S")
                )

        if self._sync_btn:
            self._sync_btn.IsEnabled = myturn

        if self._leave_btn:
            self._leave_btn.IsEnabled = in_queue

    def on_join(self, sender, args):
        doc = self._get_active_doc()
        user, model = self._get_user_model()
        if not doc or not user or not model:
            self.refresh()
            return

        sq.join_queue(doc, user, model)
        self.refresh()

    def on_leave(self, sender, args):
        doc = self._get_active_doc()
        user, model = self._get_user_model()
        if not doc or not user or not model:
            self.refresh()
            return

        sq.leave_queue(doc, user, model)
        self.refresh()

    def on_refresh(self, sender, args):
        self.refresh()

    def on_clear(self, sender, args):
        doc = self._get_active_doc()
        if not doc:
            self.refresh()
            return

        sq.clear_queue(doc)
        self.refresh()

    def on_sync(self, sender, args):
        if not self._is_my_turn():
            TaskDialog.Show("Sync Queue", "It is not your turn to sync.")
            return
        if SYNC_EXTERNAL_EVENT:
            SYNC_EXTERNAL_EVENT.Raise()

    def _start_auto_refresh(self):
        self._auto_refresh = True

        def _tick():
            import time
            while self._auto_refresh:
                time.sleep(10)
                if not self._auto_refresh or not self._page:
                    break
                try:
                    self._page.Dispatcher.Invoke(
                        DispatcherPriority.Background,
                        Action(self.refresh)
                    )
                except Exception:
                    pass

        t = py_threading.Thread(target=_tick)
        t.daemon = True
        t.start()

    def stop(self):
        self._auto_refresh = False


PANE_INSTANCE = SyncQueueDockablePanel()
SYNC_HANDLER = SyncExternalEventHandler()
SYNC_HANDLER.panel = PANE_INSTANCE
SYNC_EXTERNAL_EVENT = None