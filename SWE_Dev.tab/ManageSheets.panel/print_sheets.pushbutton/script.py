# -*- coding: utf-8 -*-
# pylint: disable=unused-argument,too-many-lines
# pylint: disable=missing-function-docstring,missing-class-docstring

"""Print sheets in order from a sheet index.

Note:
When using the `Combine into one file` option
in Revit 2022 and earlier,
the tool adds non-printable character u'\u200e'
(Left-To-Right Mark) at the start of the sheet names
to push Revit's internal printing engine to sort
the sheets correctly per the drawing index order.

Make sure your drawings indices consider this
when filtering for sheet numbers.

Shift-Click:
Shift-Clicking the tool will remove all
non-printable characters from the sheet numbers,
in case an error in the tool causes these characters
to remain.
"""
# pylint: disable=import-error,invalid-name,broad-except,superfluous-parens
import re
import os.path as op
import codecs
import os
import datetime
import locale
from collections import namedtuple

from pyrevit import HOST_APP
from pyrevit import framework
from pyrevit.framework import Windows, Drawing, ObjectModel, Forms, List
from pyrevit import coreutils
from pyrevit import forms
from pyrevit import revit, DB
from pyrevit import script
from pyrevit.compat import get_elementid_value_func

#__beta__ = True

get_elementid_value = get_elementid_value_func()

logger = script.get_logger()
config = script.get_config()


# Non Printable Char
NPC = u'\u200e'
INDEX_FORMAT = '{{:0{digits}}}'


EXPORT_ENCODING = 'utf_16_le'
if HOST_APP.is_newer_than(2020):
    EXPORT_ENCODING = 'utf_8'

IS_REVIT_2022_OR_NEWER = HOST_APP.is_newer_than(2021)


AvailableDoc = namedtuple('AvailableDoc', ['name', 'hash', 'linked'])

NamingFormatter = namedtuple('NamingFormatter', ['template', 'desc'])

SheetRevision = namedtuple('SheetRevision', ['number', 'desc', 'date', 'is_set'])
UNSET_REVISION = SheetRevision(number=None, desc=None, date=None, is_set=False)

TitleBlockPrintSettings = \
    namedtuple('TitleBlockPrintSettings', ['psettings', 'set_by_param'])


# ─────────────────────────────────────────────────────────────────────────────
# BLACK LINE OVERRIDES
# Temporarily forces all vector elements in a sheet's viewport views and the
# sheet itself to black, excluding title blocks. Used to simulate BlackLine
# export while keeping raster images in color via ColorDepth=Color.
# ─────────────────────────────────────────────────────────────────────────────
_BLACK = DB.Color(0, 0, 0)

_BLACK_OGS = DB.OverrideGraphicSettings()
_BLACK_OGS.SetProjectionLineColor(_BLACK)
_BLACK_OGS.SetCutLineColor(_BLACK)
_BLACK_OGS.SetSurfaceForegroundPatternColor(_BLACK)
_BLACK_OGS.SetSurfaceBackgroundPatternColor(_BLACK)
_BLACK_OGS.SetCutForegroundPatternColor(_BLACK)
_BLACK_OGS.SetCutBackgroundPatternColor(_BLACK)

# Full B&W: same as BlackLine but also halftones surfaces so colored fills
# render as gray rather than their original hue
_BW_OGS = DB.OverrideGraphicSettings()
_BW_OGS.SetProjectionLineColor(_BLACK)
_BW_OGS.SetCutLineColor(_BLACK)
_BW_OGS.SetSurfaceForegroundPatternColor(_BLACK)
_BW_OGS.SetSurfaceBackgroundPatternColor(_BLACK)
_BW_OGS.SetCutForegroundPatternColor(_BLACK)
_BW_OGS.SetCutBackgroundPatternColor(_BLACK)
_BW_OGS.SetHalftone(False)

# Kernel-side exclusion filter — title blocks excluded without Python iteration
_excl_cats = List[DB.BuiltInCategory]()
_excl_cats.Add(DB.BuiltInCategory.OST_TitleBlocks)
_excl_cats.Add(DB.BuiltInCategory.OST_ScheduleGraphics)
_TITLEBLOCK_EXCLUSION_FILTER = DB.ElementMulticategoryFilter(_excl_cats, True)


class BlackLineOverrides(object):
    """Manages temporary per-element black overrides across a set of sheets.

    mode='blackline'  → black lines only, raster color preserved
    mode='bw'         → black lines + halftone surfaces (full B&W)
    """

    def __init__(self, doc, mode='blackline'):
        self._doc = doc
        self._ogs = _BW_OGS if mode == 'bw' else _BLACK_OGS
        self._view_cache = {}
        self._originals = {}

    def build_cache(self, revit_sheets):
        self._view_cache = {}
        for sheet in revit_sheets:
            all_views = self._get_all_views_for_sheet(sheet)
            for view in all_views:
                vid = view.Id.IntegerValue
                if vid not in self._view_cache:
                    eids = self._get_element_ids(view)
                    self._view_cache[vid] = (view, eids)

    def build_cache_for_sheet(self, revit_sheet):
        for view in self._get_all_views_for_sheet(revit_sheet):
            vid = view.Id.IntegerValue
            if vid not in self._view_cache:
                eids = self._get_element_ids(view)
                self._view_cache[vid] = (view, eids)

    def apply(self):
        self._originals = {}
        with DB.Transaction(self._doc, "Apply Black Element Overrides") as t:
            t.Start()
            for vid, (view, eids) in self._view_cache.items():
                originals = {}
                get_ov = view.GetElementOverrides
                set_ov = view.SetElementOverrides
                for eid in eids:
                    try:
                        originals[eid] = get_ov(eid)
                        set_ov(eid, self._ogs)
                    except Exception:
                        pass
                self._originals[vid] = originals
            t.Commit()

    def restore(self):
        try:
            with DB.Transaction(self._doc, "Restore Element Overrides") as t:
                t.Start()
                for vid, (view, _) in self._view_cache.items():
                    originals = self._originals.get(vid, {})
                    set_ov = view.SetElementOverrides
                    for eid, ogs in originals.items():
                        try:
                            set_ov(eid, ogs)
                        except Exception:
                            pass
                t.Commit()
        except Exception as ex:
            logger.error("Could not restore black overrides: %s", ex)

    def clear(self):
        self._view_cache = {}
        self._originals = {}

    def _get_all_views_for_sheet(self, sheet):
        views = []
        for vp_id in sheet.GetAllViewports():
            vp = self._doc.GetElement(vp_id)
            if isinstance(vp, DB.Viewport):
                view = self._doc.GetElement(vp.ViewId)
                if view and isinstance(view, DB.View) and not view.IsTemplate:
                    views.append(view)
        views.append(sheet)
        return views

    def _get_element_ids(self, view):
        return list(
            DB.FilteredElementCollector(self._doc, view.Id)
            .WhereElementIsNotElementType()
            .WherePasses(_TITLEBLOCK_EXCLUSION_FILTER)
            .ToElementIds()
        )

class PrintUtils:
    """Utility functions for printing and exporting sheets."""

    @staticmethod
    def get_doc():
        return revit.doc

    @staticmethod
    def get_project_number(doc):
        """Return the SWE Project Number from Project Information, or None."""
        try:
            for param in doc.ProjectInformation.Parameters:
                if param.Definition.Name == 'SWE Project Number':
                    return param.AsString()
        except Exception:
            pass
        return None

    # @staticmethod
    # def get_dir():
    #     return os.path.join(os.path.expanduser("~"), "Desktop", "pyRevit Print Folder")

    @staticmethod
    def get_dir(doc=None):
        """Return the base output directory.
        Resolves to //SPR-NAS/Company/Projects/<SWE Project Number>/
        If the project number cannot be found, falls back to Desktop.
        """
        if doc is not None:
            proj_num = PrintUtils.get_project_number(doc)
            if proj_num:
                base = os.path.realpath(
                    '//SPR-NAS/Company/Projects/{}'.format(proj_num)
                )
                if os.path.exists(base):
                    return base
                else:
                    logger.warning(
                        'Project folder not found at %s — falling back to Desktop.',
                        base
                    )
        return os.path.join(os.path.expanduser("~"), "Desktop", "pyRevit Print Folder")

    @staticmethod
    def get_folder(task="_PDF"):
        dateStamp = datetime.datetime.today().strftime("%y%m%d")
        timeStamp = datetime.datetime.today().strftime("%H%M%S")
        return dateStamp + "_" + timeStamp + task

    @staticmethod
    def ensure_dir(dp):
        if not os.path.exists(dp):
            os.makedirs(dp)
        return dp

    @staticmethod
    def open_dir(dp):
        try:
            os.startfile(dp)
        except Exception:
            pass
        return dp


    @staticmethod
    def pdf_opts(color_mode='blackline', hcb=True, hsb=True, hrp=True, hvt=True, mcl=True):
        opts = DB.PDFExportOptions()
        opts.AlwaysUseRaster = False
        opts.ExportQuality = DB.PDFExportQualityType.DPI300
        opts.PaperFormat = DB.ExportPaperFormat.Default
        opts.ZoomType = DB.ZoomType.FitToPage
        opts.PaperPlacement = DB.PaperPlacementType.Center
        opts.HideCropBoundaries = hcb
        opts.HideScopeBoxes = hsb
        opts.HideReferencePlane = hrp
        opts.HideUnreferencedViewTags = hvt
        opts.MaskCoincidentLines = mcl
        opts.ReplaceHalftoneWithThinLines = False
        opts.ViewLinksInBlue = False
        opts.StopOnError = False

        if color_mode == 'bw':
            opts.ColorDepth = DB.ColorDepthType.GrayScale
        else:
            opts.ColorDepth = DB.ColorDepthType.Color

        return opts

    @staticmethod
    def dwg_opts(sc=False, mv=True):
        opts = DB.DWGExportOptions()
        opts.SharedCoords = sc
        opts.MergedViews = mv
        return opts

    @staticmethod
    def export_sheet_pdf(dir_path, sheet, opt, doc, filename):
        pdf_doc_name = op.splitext(filename)[0]
        opt.FileName = pdf_doc_name
        export_sheet = List[DB.ElementId]()
        export_sheet.Add(sheet.Id)
        doc.Export(dir_path, export_sheet, opt)
        return True

    @staticmethod
    def export_sheet_dwg(dir_path, sheet, opt, doc, filename):
        base_name = op.splitext(filename)[0]
        dwg_doc_name = base_name + ".dwg"
        export_sheet = List[DB.ElementId]()
        export_sheet.Add(sheet.Id)
        doc.Export(dir_path, dwg_doc_name, export_sheet, opt)
        return True


class NamingFormat(forms.Reactive):
    """Print File Naming Format"""
    def __init__(self, name, template, builtin=False):
        self._name = name
        self._template = self.verify_template(template)
        self.builtin = builtin

    @staticmethod
    def verify_template(value):
        """Verify template is valid"""
        if not value.lower().endswith('.pdf'):
            value += '.pdf'
        return value

    @forms.reactive
    def name(self):
        """Format name"""
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @forms.reactive
    def template(self):
        """Format template string"""
        return self._template

    @template.setter
    def template(self, value):
        self._template = self.verify_template(value)


class ViewSheetListItem(forms.Reactive):
    """Revit Sheet show in Print Window"""

    def __init__(self, view_sheet, view_tblock,
                 print_settings=None, rev_settings=None):
        self._sheet = view_sheet
        self._tblock = view_tblock
        if self._tblock:
            self._tblock_type = \
                view_sheet.Document.GetElement(view_tblock.GetTypeId())
        else:
            self._tblock_type = None
        self.name = self._sheet.Name
        self.number = self._sheet.SheetNumber if hasattr(self._sheet, 'SheetNumber') else ''
        self.issue_date = \
            self._sheet.Parameter[
                DB.BuiltInParameter.SHEET_ISSUE_DATE].AsString() if self._sheet.Parameter[
                DB.BuiltInParameter.SHEET_ISSUE_DATE] else ''
        self.printable = self._sheet.CanBePrinted
        self.revision_date_sortable = ""
        self._print_index = 0
        self._print_filename = ''

        self._tblock_psettings = print_settings
        self._print_settings = self._tblock_psettings.psettings
        self.all_print_settings = self._tblock_psettings.psettings
        if self.all_print_settings:
            self._print_settings = self.all_print_settings[0]
        self.read_only = self._tblock_psettings.set_by_param

        per_sheet_revisions = \
            rev_settings.RevisionNumbering == DB.RevisionNumbering.PerSheet \
            if rev_settings else False
        cur_rev = revit.query.get_current_sheet_revision(self._sheet) if hasattr(self._sheet, 'GetCurrentRevision') else ''
        self.revision = UNSET_REVISION
        if cur_rev:
            on_sheet = self._sheet if per_sheet_revisions else None
            self.revision = SheetRevision(
                number=revit.query.get_rev_number(cur_rev, sheet=on_sheet),
                desc=cur_rev.Description,
                date=cur_rev.RevisionDate,
                is_set=True
            )

    @property
    def revit_sheet(self):
        """Revit sheet instance"""
        return self._sheet

    @property
    def revit_tblock(self):
        """Revit titleblock instance"""
        return self._tblock

    @property
    def revit_tblock_type(self):
        """Revit titleblock type"""
        return self._tblock_type

    @forms.reactive
    def print_settings(self):
        """Sheet pring settings"""
        return self._print_settings

    @print_settings.setter
    def print_settings(self, value):
        self._print_settings = value

    @forms.reactive
    def print_index(self):
        """Sheet print index"""
        return self._print_index

    @print_index.setter
    def print_index(self, value):
        self._print_index = value

    @forms.reactive
    def print_filename(self):
        """Sheet print output filename"""
        return self._print_filename

    @print_filename.setter
    def print_filename(self, value):
        self._print_filename = \
            coreutils.cleanup_filename(value, windows_safe=True)


class PrintSettingListItem(forms.TemplateListItem):
    """Print Setting shown in Print Window"""

    def __init__(self, print_settings=None):
        super(PrintSettingListItem, self).__init__(print_settings)
        self.is_compatible = isinstance(self.item, DB.InSessionPrintSetting)

    @property
    def name(self):
        if isinstance(self.item, DB.InSessionPrintSetting):
            return "<In Session>"
        else:
            return self.item.Name

    @property
    def print_settings(self):
        return self.item

    @property
    def print_params(self):
        if self.print_settings:
            return self.print_settings.PrintParameters

    @property
    def paper_size(self):
        try:
            if self.print_params:
                return self.print_params.PaperSize
        except Exception:
            pass

    @property
    def allows_variable_paper(self):
        return False

    @property
    def is_user_defined(self):
        return not self.name.startswith('<')


class VariablePaperPrintSettingListItem(PrintSettingListItem):
    def __init__(self):
        PrintSettingListItem.__init__(self, None)
        self.is_compatible = True

    @property
    def name(self):
        return "<Variable Paper Size>"

    @property
    def allows_variable_paper(self):
        return True


class EditNamingFormatsWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, start_with=None):
        forms.WPFWindow.__init__(self, xaml_file_name)

        self._drop_pos = 0
        self._starting_item = start_with
        self._saved = False
        self.reset_naming_formats()
        self.reset_formatters()

    @staticmethod
    def get_default_formatters():
        return [
            NamingFormatter(
                template='{index}',
                desc='Print Index Number e.g. "0001"'
            ),
            NamingFormatter(
                template='{number}',
                desc='Sheet Number e.g. "A1.00"'
            ),
            NamingFormatter(
                template='{name}',
                desc='Sheet Name e.g. "1ST FLOOR PLAN"'
            ),
            NamingFormatter(
                template='{name_dash}',
                desc='Sheet Name (with - for space) e.g. "1ST-FLOOR-PLAN"'
            ),
            NamingFormatter(
                template='{name_underline}',
                desc='Sheet Name (with _ for space) e.g. "1ST_FLOOR_PLAN"'
            ),
            NamingFormatter(
                template='{current_date}',
                desc='Today\'s Date e.g. "2019-10-12"'
            ),
            NamingFormatter(
                template='{issue_date}',
                desc='Sheet Issue Date e.g. "2019-10-12"'
            ),
            NamingFormatter(
                template='{rev_number}',
                desc='Revision Number e.g. "01"'
            ),
            NamingFormatter(
                template='{rev_desc}',
                desc='Revision Description e.g. "ASI01"'
            ),
            NamingFormatter(
                template='{rev_date}',
                desc='Revision Date e.g. "2019-10-12"'
            ),
            NamingFormatter(
                template='{proj_name}',
                desc='Project Name e.g. "MY_PROJECT"'
            ),
            NamingFormatter(
                template='{proj_number}',
                desc='Project Number e.g. "PR2019.12"'
            ),
            NamingFormatter(
                template='{proj_building_name}',
                desc='Project Building Name e.g. "BLDG01"'
            ),
            NamingFormatter(
                template='{proj_issue_date}',
                desc='Project Issue Date e.g. "2019-10-12"'
            ),
            NamingFormatter(
                template='{proj_org_name}',
                desc='Project Organization Name e.g. "MYCOMP"'
            ),
            NamingFormatter(
                template='{proj_status}',
                desc='Project Status e.g. "CD100"'
            ),
            NamingFormatter(
                template='{username}',
                desc='Active User e.g. "eirannejad"'
            ),
            NamingFormatter(
                template='{revit_version}',
                desc='Active Revit Version e.g. "2019"'
            ),
            NamingFormatter(
                template='{sheet_param:PARAM_NAME}',
                desc='Value of Given Sheet Parameter e.g. '
                     'Replace PARAM_NAME with target parameter name'
            ),
            NamingFormatter(
                template='{tblock_param:PARAM_NAME}',
                desc='Value of Given TitleBlock Parameter e.g. '
                     'Replace PARAM_NAME with target parameter name'
            ),
            NamingFormatter(
                template='{proj_param:PARAM_NAME}',
                desc='Value of Given Project Information Parameter e.g. '
                     'Replace PARAM_NAME with target parameter name'
            ),
            NamingFormatter(
                template='{glob_param:PARAM_NAME}',
                desc='Value of Given Global Parameter. '
                     'Replace PARAM_NAME with target parameter name'
            ),
        ]

    @staticmethod
    def get_default_naming_formats():
        return [
            NamingFormat(
                name='0001 A1.00 1ST FLOOR PLAN.pdf',
                template='{index} {number} {name}.pdf',
                builtin=True
            ),
            NamingFormat(
                name='0001_A1.00_1ST FLOOR PLAN.pdf',
                template='{index}_{number}_{name}.pdf',
                builtin=True
            ),
            NamingFormat(
                name='0001-A1.00-1ST FLOOR PLAN.pdf',
                template='{index}-{number}-{name}.pdf',
                builtin=True
            ),
        ]

    @staticmethod
    def get_naming_formats():
        naming_formats = EditNamingFormatsWindow.get_default_naming_formats()
        naming_formats_dict = config.get_option('namingformats', {})
        for name, template in naming_formats_dict.items():
            naming_formats.append(NamingFormat(name=name, template=template))
        return naming_formats

    @staticmethod
    def set_naming_formats(naming_formats):
        naming_formats_dict = {
            x.name: x.template for x in naming_formats if not x.builtin
        }
        config.namingformats = naming_formats_dict
        script.save_config()

    @property
    def naming_formats(self):
        return self.formats_lb.ItemsSource

    @property
    def selected_naming_format(self):
        return self.formats_lb.SelectedItem

    @selected_naming_format.setter
    def selected_naming_format(self, value):
        self.formats_lb.SelectedItem = value
        self.namingformat_edit.DataContext = value

    def reset_formatters(self):
        self.formatters_wp.ItemsSource = \
            EditNamingFormatsWindow.get_default_formatters()

    def reset_naming_formats(self):
        self.formats_lb.ItemsSource = \
                ObjectModel.ObservableCollection[object](
                    EditNamingFormatsWindow.get_naming_formats()
                )
        if isinstance(self._starting_item, NamingFormat):
            for item in self.formats_lb.ItemsSource:
                if item.name == self._starting_item.name:
                    self.selected_naming_format = item
                    break

    def start_drag(self, sender, args):
        name_formatter = args.OriginalSource.DataContext
        Windows.DragDrop.DoDragDrop(
            self.formatters_wp,
            Windows.DataObject("name_formatter", name_formatter),
            Windows.DragDropEffects.Copy
            )

    def preview_drag(self, sender, args):
        mouse_pos = Forms.Cursor.Position
        mouse_po_pt = Windows.Point(mouse_pos.X, mouse_pos.Y)
        self._drop_pos = \
            self.template_tb.GetCharacterIndexFromPoint(
                point=self.template_tb.PointFromScreen(mouse_po_pt),
                snapToText=True
                )
        self.template_tb.SelectionStart = self._drop_pos
        self.template_tb.SelectionLength = 0
        self.template_tb.Focus()
        args.Effects = Windows.DragDropEffects.Copy
        args.Handled = True

    def stop_drag(self, sender, args):
        name_formatter = args.Data.GetData("name_formatter")
        if name_formatter:
            new_template = \
                str(self.template_tb.Text)[:self._drop_pos] \
                + name_formatter.template \
                + str(self.template_tb.Text)[self._drop_pos:]
            self.template_tb.Text = new_template
            self.template_tb.Focus()

    def namingformat_changed(self, sender, args):
        naming_format = self.selected_naming_format
        self.namingformat_edit.DataContext = naming_format

    def duplicate_namingformat(self, sender, args):
        naming_format = self.selected_naming_format
        new_naming_format = NamingFormat(
            name='<unnamed>',
            template=naming_format.template
            )
        self.naming_formats.Add(new_naming_format)
        self.selected_naming_format = new_naming_format

    def delete_namingformat(self, sender, args):
        naming_format = self.selected_naming_format
        if naming_format.builtin:
            return
        item_index = self.naming_formats.IndexOf(naming_format)
        self.naming_formats.Remove(naming_format)
        next_index = min([item_index, self.naming_formats.Count-1])
        self.selected_naming_format = self.naming_formats[next_index]

    def save_formats(self, sender, args):
        EditNamingFormatsWindow.set_naming_formats(self.naming_formats)
        self._saved = True
        self.Close()

    def cancelled(self, sender, args):
        if not self._saved:
            self.reset_naming_formats()

    def show_dialog(self):
        self.ShowDialog()


class SheetSetList(object):
    """List of sheets from a named Revit Sheet Set."""
    def __init__(self, view_sheetset):
        self.doc = view_sheetset.Document
        self.name = view_sheetset.Name
        self.sheetset = view_sheetset

    def get_sheets(self, doc):
        if doc == self.doc:
            return list(self.sheetset.Views)
        return []


class ScheduleSheetList(object):
    def __init__(self, view_shedule):
        self.doc = view_shedule.Document
        self.name = view_shedule.Name
        self.schedule = view_shedule

    def get_sheets(self, doc):
        return self._get_ordered_schedule_sheets(doc)

    def _get_schedule_text_data(self, view_shedule):
        schedule_data_file = \
            script.get_instance_data_file(str(get_elementid_value(view_shedule.Id)))
        vseop = DB.ViewScheduleExportOptions()
        vseop.TextQualifier = coreutils.get_enum_none(DB.ExportTextQualifier)
        view_shedule.Export(op.dirname(schedule_data_file),
                            op.basename(schedule_data_file),
                            vseop)

        sched_data = []
        try:
            with codecs.open(schedule_data_file, 'r', EXPORT_ENCODING) \
                    as sched_data_file:
                return [x.strip() for x in sched_data_file.readlines()]
        except Exception as open_err:
            logger.error('Error opening sheet index export: %s | %s',
                         schedule_data_file, open_err)
            return sched_data

    def _order_sheets_by_schedule_data(self, view_shedule, sheet_list):
        sched_data = self._get_schedule_text_data(view_shedule)

        if not sched_data:
            return sheet_list

        ordered_sheets_dict = {}
        for sheet in sheet_list:
            logger.debug('finding index for: %s', sheet.SheetNumber)
            for line_no, data_line in enumerate(sched_data):
                match_pattern = r'(^|.*\t){}(\t.*|$)'.format(sheet.SheetNumber)
                matches_sheet = re.match(match_pattern, data_line)
                logger.debug('match: %s', matches_sheet)
                try:
                    if matches_sheet:
                        ordered_sheets_dict[line_no] = sheet
                        break
                    if not sheet.CanBePrinted:
                        logger.debug('Sheet %s is not printable.',
                                     sheet.SheetNumber)
                except Exception:
                    continue

        sorted_keys = sorted(ordered_sheets_dict.keys())
        return [ordered_sheets_dict[x] for x in sorted_keys]

    def _get_ordered_schedule_sheets(self, doc):
        if doc == self.doc:
            sheets = DB.FilteredElementCollector(self.doc,
                                                 self.schedule.Id)\
                    .OfClass(framework.get_type(DB.ViewSheet))\
                    .WhereElementIsNotElementType()\
                    .ToElements()

            return self._order_sheets_by_schedule_data(
                self.schedule,
                sheets
                )
        return []


class AllSheetsList(object):
    @property
    def name(self):
        return "<All Sheets>"

    def get_sheets(self, doc):
        return DB.FilteredElementCollector(doc)\
                 .OfClass(framework.get_type(DB.ViewSheet))\
                 .WhereElementIsNotElementType()\
                 .ToElements()


class UnlistedSheetsList(object):
    @property
    def name(self):
        return "<Unlisted Sheets>"

    def get_sheets(self, doc):
        scheduled_param_id = DB.ElementId(DB.BuiltInParameter.SHEET_SCHEDULED)
        param_prov = DB.ParameterValueProvider(scheduled_param_id)
        param_equality = DB.FilterNumericEquals()
        value_rule = DB.FilterIntegerRule(param_prov, param_equality, 0)
        param_filter = DB.ElementParameterFilter(value_rule)
        return DB.FilteredElementCollector(doc)\
                 .OfClass(framework.get_type(DB.ViewSheet))\
                 .WherePasses(param_filter) \
                 .WhereElementIsNotElementType()\
                 .ToElements()


class PrintSheetsWindow(forms.WPFWindow):

    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)

        self._init_psettings = None
        self._scheduled_sheets = []
        self._cached_workset_name = None  # ← ADD
        self._cached_workset_doc_hash = None  # ← ADD

        self.project_info = revit.query.get_project_info(doc=revit.doc)
        self.sheet_cat_id = \
            revit.query.get_category(DB.BuiltInCategory.OST_Sheets).Id

        # Set export_dir BEFORE _setup_docs_list so doclist_changed
        # can safely reference it if SelectionChanged fires during init.
        # self.export_dir = os.path.join(
        #     PrintUtils.get_dir(revit.doc),
        #     PrintUtils.get_folder("_PRINT")
        # )
        self.export_dir = self._get_export_dir(revit.doc)

        self._setup_docs_list()
        self._setup_naming_formats()
        self._apply_projectinfo_naming_format_default()
        self._all_sheets_list = list(self.sheets_lb.ItemsSource) \
            if self.sheets_lb.ItemsSource else []

        # Refresh path now that selected_doc is fully resolved
        doc = self.selected_doc
        # self.export_dir = os.path.join(
        #     PrintUtils.get_dir(doc),
        #     PrintUtils.get_folder("_PRINT")
        # )
        self.export_dir = self._get_export_dir(revit.doc)
        self.exportfolder_tb.Text = self.export_dir
        self.combined_pdf_name_tb.Text = self._default_combined_pdf_name()

    def copy_naming_format(self, sender, args):
        try:
            naming_format = sender.DataContext
            if not naming_format:
                return
            script.clipboard_copy(naming_format.name)
        except Exception as e:
            logger.error("Failed to copy naming format: %s", e)

    def _apply_projectinfo_naming_format_default(self):
        pi = self.selected_doc.ProjectInformation
        param = pi.LookupParameter("Naming Format") if pi else None
        param_value = param.AsString() if param else None

        selected_item = next(
            (nf for nf in self.namingformat_cb.ItemsSource if nf.name == param_value),
            None
        )

        if not selected_item and self.namingformat_cb.ItemsSource:
            selected_item = self.namingformat_cb.ItemsSource[0]

        self.namingformat_cb.SelectedItem = selected_item

    def _get_user_workset_name(self):
        """Return the active workset name, cached per document to avoid repeated API calls."""
        try:
            doc = self.selected_doc or revit.doc
            doc_hash = doc.GetHashCode()
            if self._cached_workset_doc_hash == doc_hash:
                return self._cached_workset_name
            if not doc.IsWorkshared:
                self._cached_workset_name = None
            else:
                workset_table = doc.GetWorksetTable()
                active_id = workset_table.GetActiveWorksetId()
                self._cached_workset_name = workset_table.GetWorkset(active_id).Name
            self._cached_workset_doc_hash = doc_hash
            return self._cached_workset_name
        except Exception:
            return None

    def _get_export_dir(self, doc):
        """Return the export directory, with the active workset name appended as a subfolder."""
        base = os.path.join(
            PrintUtils.get_dir(doc),
            #PrintUtils.get_folder("_PRINT")
        )
        workset = self._get_user_workset_name()
        if not workset:
            return base

        if "Pl" in workset:
            folder_name = "15 Plumbing"
        elif "Mech" in workset:
            folder_name = "16 Mechanical"
        elif "Elec" in workset:
            folder_name = "17 Electrical"
        else:
            folder_name = ""

        if folder_name:
            workset_folder = coreutils.cleanup_filename(folder_name, windows_safe=True)
            return os.path.join(base, workset_folder)
        return base

    def _get_proj_info_param(self, param_name):
        """Return the string value of a named parameter from Project Information, or None."""
        try:
            pi = self.selected_doc.ProjectInformation
            param = pi.LookupParameter(param_name)
            if param:
                return param.AsString() or param.AsValueString()
        except Exception:
            pass
        return None

    def _default_combined_pdf_name(self):
        """Return a default combined PDF name: YYYY-MM-DD ProjectNumber ProjectName.pdf"""
        date_str = datetime.datetime.today().strftime("%Y-%m-%d")
        proj_number = self._get_proj_info_param("SWE Project Number") \
                      or (self.project_info.number if self.project_info else "")
        proj_name   = self._get_proj_info_param("Building Name") \
                      or (self.project_info.name if self.project_info else "")
        parts = [p for p in [date_str, proj_number, proj_name] if p]
        raw = "_".join(parts)
        return coreutils.cleanup_filename(raw, windows_safe=True) + ".pdf"

    def sheet_search_changed(self, sender, args):
        search_text = self.sheetsearch_tb.Text.strip().lower()
        if not self._all_sheets_list:
            return

        if not search_text:
            self.sheets_lb.ItemsSource = self._all_sheets_list
        else:
            filtered = []
            for sheet in self._all_sheets_list:
                number = sheet.number.lower() if sheet.number else ''
                name = sheet.name.lower() if sheet.name else ''
                if search_text in number or search_text in name:
                    filtered.append(sheet)
            self.sheets_lb.ItemsSource = filtered

    @property
    def selected_doc(self):
        selected_doc = self.documents_cb.SelectedItem
        for opened_doc in revit.docs:
            if opened_doc.GetHashCode() == selected_doc.hash:
                return opened_doc

    @property
    def selected_sheetlist(self):
        return self.schedules_cb.SelectedItem

    @property
    def has_errors(self):
        return self.errormsg_tb.Text != ''

    @property
    def reverse_print(self):
        return self.reverse_cb.IsChecked

    @property
    def combine_print(self):
        return self.combine_cb.IsChecked

    @property
    def combined_pdf_name(self):
        name = self.combined_pdf_name_tb.Text.strip() if self.combined_pdf_name_tb.Text else ""
        return name if name else None

    @property
    def show_placeholders(self):
        return self.placeholder_cb.IsChecked

    @property
    def index_digits(self):
        return int(self.index_slider.Value)

    @property
    def index_start(self):
        return int(self.indexstart_tb.Text or 0)

    @property
    def include_placeholders(self):
        return self.indexspace_cb.IsChecked

    @property
    def selected_naming_format(self):
        return self.namingformat_cb.SelectedItem

    @property
    def selected_printer(self):
        return self.printers_cb.SelectedItem

    @property
    def selected_print_setting(self):
        return self.printsettings_cb.SelectedItem

    @property
    def has_print_settings(self):
        return self.selected_print_setting is not None

    @property
    def print_settings(self):
        return self.printsettings_cb.ItemsSource

    @property
    def sheet_list(self):
        return self.sheets_lb.ItemsSource

    @sheet_list.setter
    def sheet_list(self, value):
        self.sheets_lb.ItemsSource = value

    @property
    def selected_sheets(self):
        return self.sheets_lb.SelectedItems

    @property
    def printable_sheets(self):
        return [x for x in self.sheet_list if x.printable]

    @property
    def selected_printable_sheets(self):
        return [x for x in self.selected_sheets if x.printable]

    # ── Add this property to PrintSheetsWindow ───────────────────────────────────
    @property
    def color_mode(self):
        """Returns 'blackline', 'color', or 'bw' based on selected radio button."""
        if self.colormode_bw.IsChecked:
            return 'bw'
        if self.colormode_color.IsChecked:
            return 'color'
        return 'blackline'  # default

    def _is_sheet_index(self, schedule_view):
        return self.sheet_cat_id == schedule_view.Definition.CategoryId \
               and not schedule_view.IsTemplate

    def _get_sheet_index_list(self):
        schedules = DB.FilteredElementCollector(self.selected_doc)\
                      .OfClass(framework.get_type(DB.ViewSchedule))\
                      .WhereElementIsNotElementType()\
                      .ToElements()

        return [
            ScheduleSheetList(s) for s in schedules
            if self._is_sheet_index(s)
            ]

    def _get_printmanager(self):
        try:
            return self.selected_doc.PrintManager
        except Exception as printerr:
            logger.critical('Error getting printer manager from document. '
                            'Most probably there is not a printer defined '
                            'on your system. | %s', printerr)
            script.exit()

    def _setup_docs_list(self):
        if not revit.doc.IsFamilyDocument:
            docs = [AvailableDoc(name=revit.doc.Title,
                                 hash=revit.doc.GetHashCode(),
                                 linked=False)]
            docs.extend([
                AvailableDoc(name=x.Title, hash=x.GetHashCode(), linked=True)
                for x in revit.query.get_all_linkeddocs(doc=revit.doc)
            ])
            self.documents_cb.ItemsSource = docs
            self.documents_cb.SelectedIndex = 0

    def _setup_naming_formats(self):
        self.namingformat_cb.ItemsSource = \
            EditNamingFormatsWindow.get_naming_formats()
        self.namingformat_cb.SelectedIndex = 0

    def _setup_printers(self):
        printers = list(Drawing.Printing.PrinterSettings.InstalledPrinters)

        if IS_REVIT_2022_OR_NEWER:
            printers.insert(0, "Revit Internal Printer")

        self.printers_cb.ItemsSource = printers
        if IS_REVIT_2022_OR_NEWER and "Revit Internal Printer" in printers:
            self.printers_cb.SelectedItem = "Revit Internal Printer"
        else:
            print_mgr = self._get_printmanager()
            self.printers_cb.SelectedItem = print_mgr.PrinterName

    def _get_psetting_items(self, doc,
                            psettings=None, include_varsettings=False):
        if include_varsettings:
            psetting_items = [VariablePaperPrintSettingListItem()]
        else:
            psetting_items = []

        psettings = psettings or revit.query.get_all_print_settings(doc=doc)
        psetting_items.extend([PrintSettingListItem(x) for x in psettings])

        print_mgr = self._get_printmanager()
        compatible_sizes = {x.Name for x in print_mgr.PaperSizes}
        for psetting_item in psetting_items:
            if isinstance(psetting_item, PrintSettingListItem):
                if psetting_item.paper_size \
                        and psetting_item.paper_size.Name in compatible_sizes:
                    psetting_item.is_compatible = True
        return psetting_items

    def _setup_print_settings(self):
        psetting_items = \
            self._get_psetting_items(
                doc=self.selected_doc,
                include_varsettings=not self.selected_doc.IsLinked
                )
        self.printsettings_cb.ItemsSource = psetting_items

        print_mgr = self._get_printmanager()
        if isinstance(print_mgr.PrintSetup.CurrentPrintSetting,
                      DB.InSessionPrintSetting):
            in_session = PrintSettingListItem(
                print_mgr.PrintSetup.CurrentPrintSetting
                )
            psetting_items.append(in_session)
            self.printsettings_cb.SelectedItem = in_session
        else:
            self._init_psettings = print_mgr.PrintSetup.CurrentPrintSetting
            cur_psetting_name = print_mgr.PrintSetup.CurrentPrintSetting.Name
            for psetting_item in psetting_items:
                if psetting_item.name == cur_psetting_name:
                    self.printsettings_cb.SelectedItem = psetting_item

        if self.selected_doc.IsLinked:
            self.disable_element(self.printsettings_cb)
        else:
            self.enable_element(self.printsettings_cb)

        self._update_combine_option()

    def _update_combine_option(self):
        self.enable_element(self.combine_cb)
        if self.selected_doc.IsLinked \
                or ((self.selected_sheetlist and self.has_print_settings)
                    and self.selected_print_setting.allows_variable_paper):
            self.disable_element(self.combine_cb)
            self.combine_cb.IsChecked = False

    def _setup_sheet_list(self):
        sheet_indices = self._get_sheet_index_list()
        try:
            cl = DB.FilteredElementCollector(self.selected_doc)
            sheetsets = cl.OfClass(framework.get_type(DB.ViewSheetSet)).WhereElementIsNotElementType().ToElements()
            for ss in sheetsets:
                sheet_indices.append(SheetSetList(ss))
        except Exception as e:
            logger.warning("Could not load sheet sets: {}".format(e))
        sheet_indices.append(AllSheetsList())
        sheet_indices.append(UnlistedSheetsList())

        self.schedules_cb.ItemsSource = sheet_indices
        self.schedules_cb.SelectedIndex = 0
        if self.schedules_cb.ItemsSource:
            self.enable_element(self.schedules_cb)
        else:
            self.disable_element(self.schedules_cb)

    def _verify_print_filename(self, sheet_name, sheet_print_filepath):
        if op.exists(sheet_print_filepath):
            logger.warning(
                "Skipping sheet \"%s\" "
                "File already exist at %s.",
                sheet_name, sheet_print_filepath
                )
            return False
        return True


    def _print_combined_sheets_in_order(self, target_sheets):
        print_mgr = self._get_printmanager()
        if not print_mgr:
            forms.alert(
                "Error getting print manager for this document",
                exitscript=True
            )

        doc = self.selected_doc
        mode = self.color_mode
        using_internal_printer = (
                IS_REVIT_2022_OR_NEWER
                and self.selected_printer == "Revit Internal Printer"
        )

        if using_internal_printer:
            dirPath = self.export_dir

            printable_sheets = [s for s in target_sheets if s.printable]
            if not printable_sheets:
                forms.alert("No printable sheets selected.")
                return

            # ── Only create folder once we know we have sheets to print ──────
            PrintUtils.ensure_dir(dirPath)
            PrintUtils.open_dir(dirPath)

            blo = BlackLineOverrides(doc, mode=mode)
            if mode in ('blackline', 'bw'):
                blo.build_cache([s.revit_sheet for s in printable_sheets])
                blo.apply()

            try:
                opts = PrintUtils.pdf_opts(color_mode=mode)
                opts.Combine = True
                custom_name = self.combined_pdf_name
                opts.FileName = coreutils.cleanup_filename(custom_name, windows_safe=True) if custom_name else doc.Title

                sheet_ids = List[DB.ElementId]()
                for sheet in printable_sheets:
                    sheet_ids.Add(sheet.revit_sheet.Id)

                try:
                    doc.Export(dirPath, sheet_ids, opts)
                except Exception as ex:
                    forms.alert(
                        "Combined PDF export failed.",
                        expanded=str(ex)
                    )
            finally:
                if mode in ('blackline', 'bw'):
                    blo.restore()
            return

        # ── Legacy print driver pipeline (non-internal printer) ──────────────
        with revit.TransactionGroup('Print Sheets in Order', doc=doc):
            with revit.Transaction('Set Printer Settings', doc=doc, log_errors=False):
                try:
                    print_mgr.PrintSetup.CurrentPrintSetting = \
                        self.selected_print_setting.print_settings
                    print_mgr.SelectNewPrintDriver(self.selected_printer)
                    print_mgr.PrintRange = DB.PrintRange.Select
                except Exception as cpSetEx:
                    forms.alert(
                        "Print setting is incompatible with printer.",
                        expanded=str(cpSetEx)
                    )
                    return

            supports_OrderedViewList = HOST_APP.is_newer_than(2022)
            if supports_OrderedViewList:
                sheet_list = List[DB.View]()
                for sheet in target_sheets:
                    if sheet.printable:
                        sheet_list.Add(sheet.revit_sheet)
            else:
                sheet_set = DB.ViewSet()
                original_sheetnums = []
                with revit.Transaction('Fix Sheet Numbers', doc=doc):
                    for idx, sheet in enumerate(target_sheets):
                        rvtsheet = sheet.revit_sheet
                        if NPC in rvtsheet.SheetNumber:
                            rvtsheet.SheetNumber = \
                                rvtsheet.SheetNumber.replace(NPC, '')
                        original_sheetnums.append(rvtsheet.SheetNumber)
                        rvtsheet.SheetNumber = \
                            NPC * (idx + 1) + rvtsheet.SheetNumber
                        if sheet.printable:
                            sheet_set.Insert(rvtsheet)

            cl = DB.FilteredElementCollector(doc)
            viewsheetsets = cl.OfClass(framework.get_type(DB.ViewSheetSet)) \
                .WhereElementIsNotElementType() \
                .ToElements()
            all_viewsheetsets = {vss.Name: vss for vss in viewsheetsets}
            sheetsetname = 'OrderedPrintSet'

            with revit.Transaction('Remove Previous Print Set', doc=doc):
                if sheetsetname in all_viewsheetsets:
                    print_mgr.ViewSheetSetting.CurrentViewSheetSet = \
                        all_viewsheetsets[sheetsetname]
                    print_mgr.ViewSheetSetting.Delete()

            with revit.Transaction('Update Ordered Print Set', doc=doc):
                try:
                    viewsheet_settings = print_mgr.ViewSheetSetting
                    if supports_OrderedViewList:
                        viewsheet_settings.CurrentViewSheetSet.IsAutomatic = False
                        viewsheet_settings.CurrentViewSheetSet.OrderedViewList = \
                            sheet_list
                    else:
                        viewsheet_settings.CurrentViewSheetSet.Views = sheet_set
                    viewsheet_settings.SaveAs(sheetsetname)
                except Exception as viewset_err:
                    sheet_report = ''
                    for sheet in sheet_set:
                        sheet_report += '{} {}\n'.format(
                            sheet.SheetNumber if isinstance(sheet, DB.ViewSheet)
                            else '---',
                            type(sheet)
                        )
                    logger.critical(
                        'Error setting sheet set on print mechanism. '
                        'These items are included in the viewset object:\n%s',
                        sheet_report
                    )
                    raise viewset_err

            print_mgr.PrintOrderReverse = self.reverse_print
            try:
                print_mgr.CombinedFile = True
            except Exception as e:
                forms.alert(str(e) + '\nSet printer correctly in Print settings.')
                script.exit()

            #print_filepath = op.join('C:', 'Ordered Sheet Set.pdf')
            _combined_name = self.combined_pdf_name or "Ordered Sheet Set"
            _combined_name = coreutils.cleanup_filename(_combined_name, windows_safe=True)
            if not _combined_name.lower().endswith(".pdf"):
                _combined_name += ".pdf"
            print_filepath = op.join('C:', _combined_name)
            print_mgr.PrintToFile = True
            print_mgr.PrintToFileName = print_filepath

            with revit.Transaction('Reload Keynote File', doc=doc):
                DB.KeynoteTable.GetKeynoteTable(revit.doc).Reload(None)
            print_mgr.Apply()
            print_mgr.SubmitPrint()

            if not supports_OrderedViewList:
                with revit.Transaction('Restore Sheet Numbers', doc=doc):
                    for sheet, sheetnum in zip(target_sheets, original_sheetnums):
                        sheet.revit_sheet.SheetNumber = sheetnum

        self._reset_psettings()

    def _print_sheets_in_order(self, target_sheets):
        doc = self.selected_doc
        print_mgr = self._get_printmanager()
        print_mgr.PrintToFile = True
        per_sheet_psettings = self.selected_print_setting.allows_variable_paper
        dirPath = self.export_dir

        with revit.Transaction('Reload Keynote File', doc=doc):
            DB.KeynoteTable.GetKeynoteTable(doc).Reload(None)

        with revit.DryTransaction('Set Printer Settings', doc=doc):
            try:
                if not per_sheet_psettings:
                    print_mgr.PrintSetup.CurrentPrintSetting = \
                        self.selected_print_setting.print_settings
                if not (IS_REVIT_2022_OR_NEWER
                        and self.selected_printer == "Revit Internal Printer"):
                    print_mgr.SelectNewPrintDriver(self.selected_printer)
                print_mgr.PrintRange = DB.PrintRange.Current
            except Exception as cpSetEx:
                forms.alert(
                    "Print setting is incompatible with printer.",
                    expanded=str(cpSetEx)
                )
                return

        if not target_sheets:
            return

        # ── Only create the folder once we know we are actually printing ──────
        PrintUtils.ensure_dir(dirPath)
        if self.selected_printer == "Revit Internal Printer" or self.export_dwg.IsChecked:
            PrintUtils.open_dir(dirPath)

        using_internal_printer = (
                IS_REVIT_2022_OR_NEWER
                and self.selected_printer == "Revit Internal Printer"
        )
        mode = self.color_mode

        blo = BlackLineOverrides(doc, mode=mode)
        if using_internal_printer and mode in ('blackline', 'bw'):
            printable_revit_sheets = [
                s.revit_sheet for s in target_sheets if s.printable
            ]
            blo.build_cache(printable_revit_sheets)
            blo.apply()

        try:
            if self.export_dwg.IsChecked:
                with forms.ProgressBar(
                        step=1,
                        title='Exporting PDF & DWGs... {value} of {max_value}',
                        cancellable=True
                ) as pb1:
                    pbTotal1 = len(target_sheets) * 2
                    pbCount1 = 1
                    for sheet in target_sheets:
                        if pb1.cancelled:
                            break
                        if sheet.printable:
                            if sheet.print_filename:
                                print_filepath = op.join(dirPath, sheet.print_filename)
                                print_mgr.PrintToFileName = print_filepath

                                if per_sheet_psettings:
                                    print_mgr.PrintSetup.CurrentPrintSetting = \
                                        sheet.print_settings

                                if self._verify_print_filename(sheet.name, print_filepath):
                                    try:
                                        pb1.update_progress(pbCount1, pbTotal1)
                                        pbCount1 += 1
                                        if using_internal_printer:
                                            optspdf = PrintUtils.pdf_opts(color_mode=mode)
                                            PrintUtils.export_sheet_pdf(
                                                dirPath, sheet.revit_sheet,
                                                optspdf, doc, sheet.print_filename
                                            )
                                        else:
                                            print_mgr.SubmitPrint(sheet.revit_sheet)
                                    except Exception as e:
                                        logger.error(
                                            'Failed to export PDF for sheet %s: %s',
                                            sheet.number, e
                                        )
                                    try:
                                        pb1.update_progress(pbCount1, pbTotal1)
                                        pbCount1 += 1
                                        optsdwg = PrintUtils.dwg_opts()
                                        PrintUtils.export_sheet_dwg(
                                            dirPath, sheet.revit_sheet,
                                            optsdwg, doc, sheet.print_filename
                                        )
                                    except Exception as e:
                                        logger.error(
                                            'Failed to export DWG for sheet %s: %s',
                                            sheet.number, e
                                        )
                            else:
                                pbCount1 += 2
                                logger.debug(
                                    'Sheet %s does not have a valid file name.',
                                    sheet.number
                                )
                        else:
                            pbCount1 += 2
                            logger.debug(
                                'Sheet %s is not printable. Skipping print.',
                                sheet.number
                            )
            else:
                with forms.ProgressBar(
                        step=1,
                        title='Exporting PDFs... {value} of {max_value}',
                        cancellable=True
                ) as pb1:
                    pbTotal1 = len(target_sheets)
                    pbCount1 = 1
                    for sheet in target_sheets:
                        if pb1.cancelled:
                            break
                        if sheet.printable:
                            if sheet.print_filename:
                                print_filepath = op.join(dirPath, sheet.print_filename)
                                print_mgr.PrintToFileName = print_filepath

                                if per_sheet_psettings:
                                    print_mgr.PrintSetup.CurrentPrintSetting = \
                                        sheet.print_settings

                                if self._verify_print_filename(sheet.name, print_filepath):
                                    try:
                                        pb1.update_progress(pbCount1, pbTotal1)
                                        pbCount1 += 1
                                        if using_internal_printer:
                                            optspdf = PrintUtils.pdf_opts(color_mode=mode)
                                            PrintUtils.export_sheet_pdf(
                                                dirPath, sheet.revit_sheet,
                                                optspdf, doc, sheet.print_filename
                                            )
                                        else:
                                            print_mgr.SubmitPrint(sheet.revit_sheet)
                                    except Exception as e:
                                        logger.error(
                                            'Failed to export PDF for sheet %s: %s',
                                            sheet.number, e
                                        )
                            else:
                                pbCount1 += 1
                                logger.debug(
                                    'Sheet %s does not have a valid file name.',
                                    sheet.number
                                )
                        else:
                            pbCount1 += 1
                            logger.debug(
                                'Sheet %s is not printable. Skipping print.',
                                sheet.number
                            )
        finally:
            if using_internal_printer and mode in ('blackline', 'bw'):
                blo.restore()


    def _print_linked_sheets_in_order(self, target_sheets, target_doc):
        print_mgr = self._get_printmanager()
        print_mgr.PrintToFile = True
        if not (IS_REVIT_2022_OR_NEWER
                and self.selected_printer == "Revit Internal Printer"):
            print_mgr.SelectNewPrintDriver(self.selected_printer)
        print_mgr.PrintRange = DB.PrintRange.Current

        dirPath = self.export_dir
        doc = target_doc

        if not target_sheets:
            return

        # ── Only create folder once we know we have sheets to print ──────────
        PrintUtils.ensure_dir(dirPath)
        if self.selected_printer == "Revit Internal Printer":
            PrintUtils.open_dir(dirPath)

        using_internal_printer = (
                IS_REVIT_2022_OR_NEWER
                and self.selected_printer == "Revit Internal Printer"
        )
        mode = self.color_mode

        blo = BlackLineOverrides(doc, mode=mode)
        if using_internal_printer and mode in ('blackline', 'bw'):
            printable_revit_sheets = [
                s.revit_sheet for s in target_sheets if s.printable
            ]
            blo.build_cache(printable_revit_sheets)
            blo.apply()

        try:
            with forms.ProgressBar(
                    step=1,
                    title='Exporting Linked PDFs... {value} of {max_value}',
                    cancellable=True
            ) as pb1:
                pbTotal1 = len(target_sheets)
                pbCount1 = 1
                for sheet in target_sheets:
                    if pb1.cancelled:
                        break
                    if sheet.printable:
                        if sheet.print_filename:
                            print_filepath = op.join(dirPath, sheet.print_filename)
                            print_mgr.PrintToFileName = print_filepath

                            if self._verify_print_filename(sheet.name, print_filepath):
                                try:
                                    pb1.update_progress(pbCount1, pbTotal1)
                                    pbCount1 += 1
                                    if using_internal_printer:
                                        optspdf = PrintUtils.pdf_opts(color_mode=mode)
                                        PrintUtils.export_sheet_pdf(
                                            dirPath, sheet.revit_sheet,
                                            optspdf, doc, sheet.print_filename
                                        )
                                    else:
                                        print_mgr.SubmitPrint(sheet.revit_sheet)
                                except Exception as e:
                                    logger.error(
                                        'Failed to export PDF for sheet %s: %s',
                                        sheet.number, e
                                    )
                        else:
                            pbCount1 += 1
                            logger.debug(
                                'Sheet %s does not have a valid file name.',
                                sheet.number
                            )
                    else:
                        pbCount1 += 1
                        logger.debug(
                            'Sheet %s is not printable. Skipping print.',
                            sheet.number
                        )
        finally:
            if using_internal_printer and mode in ('blackline', 'bw'):
                blo.restore()

    def _reset_error(self):
        self.enable_element(self.print_b)
        self.hide_element(self.errormsg_block)
        self.errormsg_tb.Text = ''

    def _set_error(self, err_msg):
        if self.errormsg_tb.Text != err_msg:
            self.disable_element(self.print_b)
            self.show_element(self.errormsg_block)
            self.errormsg_tb.Text = err_msg

    def _update_print_indices(self, sheet_list):
        start_idx = self.index_start
        for idx, sheet in enumerate(sheet_list):
            sheet.print_index = INDEX_FORMAT\
                .format(digits=self.index_digits)\
                .format(idx + start_idx)

    def _update_filename_template(self, template, value_type, value_getter):
        finder_pattern = r'{' + value_type + r':(.*?)}'
        for param_name in re.findall(finder_pattern, template):
            param_value = value_getter(param_name)
            repl_pattern = r'{' + value_type + ':' + param_name + r'}'
            if param_value:
                template = re.sub(repl_pattern, str(param_value), template)
            template = re.sub(repl_pattern, '', template)
        return template

    def _update_print_filename(self, template, sheet):
        template = self._update_filename_template(
            template=template,
            value_type='tblock_param',
            value_getter=lambda x: revit.query.get_param_value(
                    revit.query.get_param(sheet.revit_tblock, x)
                ) or revit.query.get_param_value(
                    revit.query.get_param(sheet.revit_tblock_type, x)
                )
        )

        template = self._update_filename_template(
            template=template,
            value_type='sheet_param',
            value_getter=lambda x: revit.query.get_param_value(
                revit.query.get_param(sheet.revit_sheet, x)
                )
        )

        rev_date_str = sheet.revision.date or ""
        sortable_date = ""

        locale_tuple = locale.getdefaultlocale()
        user_locale = (locale_tuple[0] if locale_tuple and locale_tuple[0] else "en_GB")
        dayfirst = not user_locale.startswith("en_US")

        date_formats = ["%d.%m.%y", "%m.%d.%y", "%d/%m/%y", "%m/%d/%y"]
        if not dayfirst:
            date_formats = ["%m.%d.%y", "%m/%d/%y", "%d.%m.%y", "%d/%m/%y"]

        for fmt in date_formats:
            try:
                parsed = datetime.datetime.strptime(rev_date_str, fmt)
                sortable_date = parsed.strftime("%Y%m%d")
                break
            except (ValueError, TypeError):
                continue

        sheet.revision_date_sortable = sortable_date

        try:
            output_fname = \
                template.format(
                    index=sheet.print_index,
                    number=sheet.number,
                    name=sheet.name,
                    name_dash=sheet.name.replace(' ', '-'),
                    name_underline=sheet.name.replace(' ', '_'),
                    current_date=coreutils.current_date(),
                    issue_date=sheet.issue_date,
                    rev_number=sheet.revision.number if sheet.revision else '',
                    rev_desc=sheet.revision.desc if sheet.revision else '',
                    rev_date=sheet.revision.date if sheet.revision else '',
                    proj_name=self.project_info.name,
                    proj_number=self.project_info.number,
                    proj_building_name=self.project_info.building_name,
                    proj_issue_date=self.project_info.issue_date,
                    proj_org_name=self.project_info.org_name,
                    proj_status=self.project_info.status,
                    username=HOST_APP.username,
                    revit_version=HOST_APP.version,
                )

        except Exception as ferr:
            output_fname = ''
            if isinstance(ferr, KeyError):
                self._set_error('Unknown key in selected naming format')
        sheet.print_filename = output_fname

    def _update_print_filenames(self, sheet_list):
        doc = self.selected_doc
        naming_fmt = self.selected_naming_format
        if naming_fmt:
            template = naming_fmt.template
            template = self._update_filename_template(
                template=template,
                value_type='proj_param',
                value_getter=lambda x: revit.query.get_param_value(
                    doc.ProjectInformation.LookupParameter(x)
                    )
            )

            template = self._update_filename_template(
                template=template,
                value_type='glob_param',
                value_getter=lambda x: revit.query.get_param_value(
                    revit.query.get_global_parameter(x, doc=doc)
                    )
            )

            for sheet in sheet_list:
                self._update_print_filename(template, sheet)

    def _find_sheet_tblock(self, revit_sheet, tblocks):
        for tblock in tblocks:
            view_sheet = revit_sheet.Document.GetElement(tblock.OwnerViewId)
            if view_sheet.Id == revit_sheet.Id:
                return tblock

    def _get_sheet_printsettings(self, tblocks, psettings):
        tblock_printsettings = {}
        sheet_printsettings = {}
        for tblock in tblocks:
            tblock_psetting = None
            sheet = self.selected_doc.GetElement(tblock.OwnerViewId)
            tblock_tform = tblock.GetTotalTransform()
            tblock_tid = get_elementid_value(tblock.GetTypeId())
            tblock_tid = tblock_tid * 100 + tblock_tform.BasisX.X * 10 + tblock_tform.BasisX.Y
            tblock_psetting = tblock_printsettings.get(tblock_tid, None)
            if tblock_psetting:
                sheet_printsettings[sheet.SheetNumber] = tblock_psetting
            else:
                tblock_type = tblock.Document.GetElement(tblock.GetTypeId())
                if tblock_type:
                    psparam = tblock_type.LookupParameter("Print Setting")
                    if psparam:
                        psetting_name = psparam.AsString()
                        psparam_psetting = \
                            next(
                                (x for x in psettings
                                    if x.Name == psetting_name),
                                None
                            )
                        if psparam_psetting:
                            tblock_psetting = \
                                TitleBlockPrintSettings(
                                    psettings=[psparam_psetting],
                                    set_by_param=True
                                )
                if not tblock_psetting:
                    tblock_psetting = \
                        TitleBlockPrintSettings(
                            psettings=revit.query.get_titleblock_print_settings(
                                tblock,
                                self.selected_printer,
                                psettings
                                ),
                            set_by_param=False
                        )
                tblock_printsettings[tblock_tid] = tblock_psetting
                sheet_printsettings[sheet.SheetNumber] = tblock_psetting
        return sheet_printsettings

    def _reset_psettings(self):
        if self._init_psettings:
            print_mgr = self._get_printmanager()
            with revit.Transaction("Revert to Original Print Settings"):
                print_mgr.PrintSetup.CurrentPrintSetting = self._init_psettings

    def _update_index_slider(self):
        index_digits = \
            coreutils.get_integer_length(
                len(self._scheduled_sheets) + self.index_start
                )
        self.index_slider.Minimum = max([index_digits, 2])
        self.index_slider.Maximum = self.index_slider.Minimum + 3

    def doclist_changed(self, sender, args):
        self._cached_workset_doc_hash = None  # ← invalidate cache on doc switch
        doc = self.selected_doc or revit.doc  # ← safe fallback
        self.project_info = revit.query.get_project_info(doc=doc)
        self._setup_printers()
        self._setup_print_settings()
        self._setup_sheet_list()

        if hasattr(self, 'exportfolder_tb') and self.exportfolder_tb is not None:
            self.export_dir = self._get_export_dir(doc)
            self.exportfolder_tb.Text = self.export_dir

        if hasattr(self, 'combined_pdf_name_tb') and self.combined_pdf_name_tb is not None:
            self.combined_pdf_name_tb.Text = self._default_combined_pdf_name()

    def choose_export_folder(self, sender, args):
        dialog = Windows.Forms.OpenFileDialog()
        dialog.Title = "Select Export Folder"
        dialog.Filter = "Folder|*.none"
        dialog.FileName = "Select Folder"
        dialog.CheckFileExists = False
        dialog.CheckPathExists = True
        dialog.ValidateNames = False

        start_dir = getattr(self, 'export_dir', None)
        if start_dir and os.path.exists(start_dir):
            dialog.InitialDirectory = start_dir
        elif start_dir:
            parent_dir = os.path.dirname(start_dir)
            if parent_dir and os.path.exists(parent_dir):
                dialog.InitialDirectory = parent_dir

        if dialog.ShowDialog() == Windows.Forms.DialogResult.OK:
            folder = os.path.dirname(dialog.FileName)
            self.export_dir = folder
            self.exportfolder_tb.Text = folder

    def sheetlist_changed(self, sender, args):
        print_settings = None
        tblocks = revit.query.get_elements_by_categories(
            [DB.BuiltInCategory.OST_TitleBlocks],
            doc=self.selected_doc
        )
        if self.selected_sheetlist and self.has_print_settings:
            rev_cfg = DB.RevisionSettings.GetRevisionSettings(revit.doc)
            if self.selected_print_setting.allows_variable_paper:
                sheet_printsettings = \
                    self._get_sheet_printsettings(
                        tblocks,
                        revit.query.get_all_print_settings(
                            doc=self.selected_doc
                            )
                        )
                self.show_element(self.varsizeguide)
                self.show_element(self.psettingcol)
                self._scheduled_sheets = [
                    ViewSheetListItem(
                        view_sheet=x,
                        view_tblock=self._find_sheet_tblock(x, tblocks),
                        print_settings=sheet_printsettings.get(
                            x.SheetNumber,
                            None),
                        rev_settings=rev_cfg)
                    for x in self.selected_sheetlist.get_sheets(
                        doc=self.selected_doc
                        )
                    ]
            else:
                print_settings = self.selected_print_setting.print_settings
                self.hide_element(self.varsizeguide)
                self.hide_element(self.psettingcol)
                self._scheduled_sheets = [
                    ViewSheetListItem(
                        view_sheet=x,
                        view_tblock=self._find_sheet_tblock(x, tblocks),
                        print_settings=TitleBlockPrintSettings(
                            psettings=[print_settings],
                            set_by_param=False
                        ),
                        rev_settings=rev_cfg)
                    for x in self.selected_sheetlist.get_sheets(
                        doc=self.selected_doc
                        )
                    ]
        self._update_combine_option()
        self.options_changed(None, None)

    def printers_changed(self, sender, args):
        print_mgr = self._get_printmanager()

        if self.selected_printer == "Revit Internal Printer":
            return
        print_mgr.SelectNewPrintDriver(self.selected_printer)
        self._setup_print_settings()

    def options_changed(self, sender, args):
        self._reset_error()

        self._update_index_slider()

        sheet_list = [x for x in self._scheduled_sheets]
        if self.reverse_print:
            sheet_list.reverse()

        # AFTER:
        if self.combine_cb.IsChecked:
            self.hide_element(self.order_sp)
            self.hide_element(self.namingformat_dp)
            self.hide_element(self.pfilename)
            self.export_dwg.IsChecked = False
            self.export_dwg.IsEnabled = False
            self.show_element(self.combined_pdf_name_dp)
        else:
            self.show_element(self.order_sp)
            self.show_element(self.namingformat_dp)
            self.show_element(self.pfilename)
            self.export_dwg.IsEnabled = True

        if self.selected_doc.IsLinked:
            self.export_dwg.IsChecked = False
            self.export_dwg.IsEnabled = False

        if not self.show_placeholders:
            self.indexspace_cb.IsEnabled = True
            self._update_print_indices(sheet_list)
            printable_sheets = []
            for sheet in sheet_list:
                if sheet.printable:
                    printable_sheets.append(sheet)
            if not self.include_placeholders:
                self._update_print_indices(printable_sheets)
            self.sheet_list = printable_sheets
        else:
            self.indexspace_cb.IsChecked = True
            self.indexspace_cb.IsEnabled = False
            self._update_print_indices(sheet_list)
            self.sheet_list = sheet_list

        self._update_print_filenames(sheet_list)

    def set_sheet_printsettings(self, sender, args):
        if self.selected_printable_sheets:
            if any(x.read_only for x in self.selected_printable_sheets):
                forms.alert("Print settings has been set by titleblock "
                            "for one or more sheets and can only be changed "
                            "by modifying the titleblock print setting")
                return

            all_psettings = \
                [x for x in self.print_settings if x.is_user_defined]
            sheet_psettings = \
                self.selected_printable_sheets[0].all_print_settings
            if sheet_psettings:
                options = {
                    'Matching Print Settings':
                        self._get_psetting_items(
                            doc=self.selected_doc,
                            psettings=sheet_psettings
                            ),
                    'All Print Settings':
                        all_psettings
                }
            else:
                options = all_psettings or []

            if options:
                psetting_item = forms.SelectFromList.show(
                    options,
                    name_attr='name',
                    group_selector_title='Print Settings:',
                    default_group='Matching Print Settings',
                    title='Select Print Setting',
                    item_container_template=self.Resources["printSettingsItem"],
                    width=450, height=400
                    )
                if psetting_item:
                    for sheet in self.selected_printable_sheets:
                        sheet.print_settings = psetting_item
            else:
                forms.alert('There are no print settings in this model.')

    def sheet_selection_changed(self, sender, args):
        if self.selected_printable_sheets:
            return self.enable_element(self.sheetopts_wp)
        self.disable_element(self.sheetopts_wp)

    def validate_index_start(self, sender, args):
        args.Handled = re.match(r'[^0-9]+', args.Text)

    def rest_index(self, sender, args):
        self.indexstart_tb.Text = '0'

    def edit_formats(self, sender, args):
        editfmt_wnd = \
            EditNamingFormatsWindow(
                'EditNamingFormats.xaml',
                start_with=self.selected_naming_format
                )
        editfmt_wnd.show_dialog()
        self.namingformat_cb.ItemsSource = editfmt_wnd.naming_formats
        self.namingformat_cb.SelectedItem = editfmt_wnd.selected_naming_format

    def copy_filenames(self, sender, args):
        if self.selected_sheets:
            filenames = [x.print_filename for x in self.selected_sheets]
            script.clipboard_copy('\n'.join(filenames))

    def print_sheets(self, sender, args):
        if self.sheet_list:
            selected_only = False
            if self.selected_sheets:
                opts = forms.alert(
                    "You have a series of sheets selected. Do you want to "
                    "print the selected sheets or all sheets?",
                    options=["Only Selected Sheets", "All Scheduled Sheets"]
                    )
                selected_only = opts == "Only Selected Sheets"

            target_sheets = \
                self.selected_sheets if selected_only else self.sheet_list

            if not self.combine_print:
                if (self.selected_print_setting.allows_variable_paper
                        and not all(x.print_settings for x in target_sheets)):
                    forms.alert(
                        'Not all sheets have a print setting assigned to them. '
                        'Select sheets and assign print settings.')
                    return
                printable_count = len([x for x in target_sheets if x.printable])
                if printable_count > 5:
                    sheet_count = len(target_sheets)
                    message = str(printable_count)
                    if printable_count != sheet_count:
                        message += ' (out of {} total)'.format(sheet_count)

                    if not forms.alert('Are you sure you want to print {} '
                                       'sheets individually? The process can '
                                       'not be cancelled.'.format(message),
                                       ok=False, yes=True, no=True):
                        return
            self.Close()
            if self.combine_print:
                self._print_combined_sheets_in_order(target_sheets)
            else:
                if self.selected_doc.IsLinked:
                    self._print_linked_sheets_in_order(target_sheets, self.selected_doc)
                else:
                    self._print_sheets_in_order(target_sheets)


def cleanup_sheetnumbers(doc):
    sheets = revit.query.get_sheets(doc=doc)
    with revit.Transaction('Cleanup Sheet Numbers', doc=doc):
        for sheet in sheets:
            sheet.SheetNumber = sheet.SheetNumber.replace(NPC, '')


forms.check_modeldoc(exitscript=True)
revit.selection.get_selection().clear()

if __shiftclick__:  # pylint: disable=E0602
    open_docs = forms.select_open_docs(check_more_than_one=False)
    if open_docs:
        for open_doc in open_docs:
            cleanup_sheetnumbers(open_doc)
else:
    PrintSheetsWindow('PrintSheets.xaml').ShowDialog()
