# -*- coding: utf-8 -*-
__title__ = 'Add Electrical\nParams'
__author__ = 'EML'
__min_revit_ver__ = 2023
__max_revit_ver__ = 2026
__doc__ = """
V1.4  MVVM refactor with four new features over v1.3:

  * Connector Type Selector
        Pick the Revit ElectricalSystemType used when creating the connector
        (Power - Balanced / Unbalanced, Data, Telephone, Communication,
        Fire Alarm, Nurse Call, Controls, Security).

  * Row-level Exclude checkbox
        Each parameter row in the TO ADD grid has an EXCL column. Excluded
        rows stay visible (greyed out) and are not added during the run.

  * Parameter groups
        Tray rows are grouped by their Revit parameter group with collapsible
        section headers (click anywhere on the header to toggle).

  * Family Load Options
        Toggle to control whether existing parameter values are overwritten
        when the modified family is reloaded into the project.

All v1.3 PERF optimisations are preserved.

Architecture
------------
    Models                pure data
    BaseViewModel         INotifyPropertyChanged plumbing
    RelayCommand          ICommand for buttons
    SharedParameterRowVM  one row in the TO ADD grid
    FamilyItemVM          one row in the family list
    MainViewModel         all state + business logic, no UI references
    AddSharedParamsView   thin WPFWindow code-behind, sets DataContext, wires
                          CollectionViewSource grouping, routes only those
                          events that cannot be expressed as bindings
"""

# ==================== IMPORTS ====================
import clr
import os
import re
import System

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')
clr.AddReference('System.Core')

from io import open as io_open

from System import EventArgs
from System.Collections.ObjectModel import ObservableCollection
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs
from System.Windows.Data import CollectionViewSource, PropertyGroupDescription
from System.Windows.Input import ICommand
from Microsoft.Win32 import OpenFileDialog

from Autodesk.Revit.UI import TaskDialog
from Autodesk.Revit.DB import (
    Transaction, FilteredElementCollector,
    Family, FamilyInstance, ElementId, IFamilyLoadOptions,
    ConnectorElement, Domain, GeometryInstance,
    ReferencePlane, Options, Solid, PlanarFace,
    XYZ, ViewDetailLevel,
)
from Autodesk.Revit.DB.Electrical import ElectricalSystemType

try:
    from Autodesk.Revit.DB import GroupTypeId
    _USE_FORGE = True
except ImportError:
    from Autodesk.Revit.DB import BuiltInParameterGroup
    _USE_FORGE = False

from pyrevit.forms import WPFWindow

# ==================== CONSTANTS ====================

FALLBACK_SP_FILE = (
    r'\\SPR-NAS\Company\Engineering Operations\05 BIM'
    r'\REVIT Library\00 Add-Ins & Shared Parameters'
    r'\Shared Parameter\Systems West Shared Parameter File.txt'
)

MAX_CONNECTIONS = 4

CONNECTOR_ASSOCIATION_MAP = [
    ('1_Voltage [V]',                  ['Voltage', 'Nominal Voltage']),
    ('1_Phase [Ph]',                   ['Number of Poles', 'Poles', 'Phase']),
    ('1_Apparent Load',                ['Apparent Load', 'Apparent Power','Load']),
    ('1_Power Factor',                 ['Power Factor']),
    ('1_Connection Description [txt]', ['Connector Description']),
]

AUTO_ADD_PARAMS = [
    '1_Voltage [V]',
    '1_Phase [Ph]',
    '1_FLA [A]',
    '1_Watt [W]',
    '1_Apparent Load',
    '1_Power Factor',
    '1_Connection Description [txt]',
]

SCHEDULE_PARAMETERS = {
    'MEP Coordination Schedule - Change Indicator [SWE]':  'Electrical Analysis',
    'Division 26':                                          'Identity Data',
    'Division Designator [txt]':                            'Electrical Analysis',
    'Equipment Category [txt]':                             'Electrical Analysis',
    'Tag No.':                                              'Text',
    'Sub Tag No.':                                          'Text',
    '1_Connection Description [txt]':                       'Electrical - Loads',
    '1_Voltage [V]':                                        'Electrical - Loads',
    '1_Phase [Ph]':                                         'Electrical - Loads',
    '1_FLA [A]':                                            'Electrical - Loads',
    '1_Watt [W]':                                           'Electrical - Loads',
    '1_Horsepower [hp]':                                    'Electrical - Loads',
    '1_MCA [A]':                                            'Electrical - Loads',
    '1_MOP [A]':                                            'Electrical - Loads',
    '1_Apparent Load':                                      'Electrical - Loads',
    '1_FLA MFR [A]':                                        'Electrical - Loads',
    '1_Load Classification':                                'Electrical - Loads',
    '1_MCA Factor [Num]':                                   'Electrical - Loads',
    '1_Power Factor':                                       'Electrical - Loads',
    'Power Source [txt]':                                   'Electrical Analysis',
    'Plug Connections [txt]':                               'Electrical Analysis',
    'Disconnect Furnished By [txt]':                        'Electrical Analysis',
    'Disconnect Installed By [txt]':                        'Electrical Analysis',
    'Disconnect Outdoor Location [txt]':                    'Electrical Analysis',
    'MC Type [txt]':                                        'Electrical Analysis',
    'MC Furnished By [txt]':                                'Electrical Analysis',
    'MC Installed By [txt]':                                'Electrical Analysis',
    'MC Outdoor Location [txt]':                            'Electrical Analysis',
    'S/S Control Interlock [txt]':                          'Data',
    'S/S Control Unit Tag [txt]':                           'Data',
    'S/S Control On/Off Switch [txt]':                      'Data',
    'S/S Control Occupancy Sensor [txt]':                   'Data',
    'S/S AQUA-STAT [txt]':                                  'Data',
    'T-STAT Furnished By [txt]':                            'Data',
    'T-STAT Wired By [txt]':                                'Data',
    'T-STAT Line Volt [txt]':                               'Data',
    'T-STAT Low Volt [txt]':                                'Data',
    'OCC. Sensor Furnished By [txt]':                       'Data',
    'OCC. Sensor Installed By [txt]':                       'Data',
    'OCC. Sensor Control Wiring By [txt]':                  'Data',
    'Control Wiring [txt]':                                 'Data',
    'Control Devices [txt]':                                'Data',
    'Control Wiring & Devices Description [txt]':           'Data',
    'FA&S Fire Riser - Wet [txt]':                          'Fire Protection',
    'FA&S Fire Riser - Dry [txt]':                          'Fire Protection',
    'FA&S Air Compressor [txt]':                            'Fire Protection',
    'FA&S FSD [txt]':                                       'Fire Protection',
    'FA&S Duct SD [txt]':                                   'Fire Protection',
    'Data Port [txt]':                                      'Data',
    'Special Requirements/Notes [txt]':                     'Electrical Analysis',
}

SPEC_TYPE_MAP = {
    'Acceleration': 'Acceleration', 'AirFlow': 'Air Flow',
    'AirFlowDensity': 'Air Flow Density',
    'AirFlowDividedByCoolingLoad': 'Air Flow Divided By Cooling Load',
    'AirFlowDividedByVolume': 'Air Flow Divided By Volume',
    'Angle': 'Angle', 'AngularSpeed': 'Angular Speed',
    'ApparentPower': 'Apparent Power', 'ApparentPowerDensity': 'Apparent Power Density',
    'Area': 'Area', 'Bool': 'Yes/No', 'Boolean': 'Yes/No',
    'CableTraySize': 'Cable Tray Size', 'ColorTemperature': 'Color Temperature',
    'ConduitSize': 'Conduit Size', 'CoolingLoad': 'Cooling Load',
    'Currency': 'Currency', 'Current': 'Current', 'Custom': 'Custom',
    'DemandFactor': 'Demand Factor', 'Distance': 'Distance',
    'DuctSize': 'Duct Size', 'Efficacy': 'Efficacy',
    'ElectricalFrequency': 'Frequency', 'ElectricalPotential': 'Voltage',
    'ElectricalPower': 'Power', 'ElectricalPowerDensity': 'Power Density',
    'ElectricalResistivity': 'Electrical Resistivity',
    'ElectricalTemperature': 'Temperature',
    'ElectricalTemperatureDifference': 'Temperature Difference',
    'Energy': 'Energy', 'Factor': 'Factor', 'Flow': 'Flow',
    'Force': 'Force', 'HeatGain': 'Heat Gain', 'HeatingLoad': 'Heating Load',
    'HvacDensity': 'HVAC Density', 'HvacEnergy': 'HVAC Energy',
    'HvacFriction': 'HVAC Friction', 'HvacPower': 'HVAC Power',
    'HvacPressure': 'HVAC Pressure', 'HvacTemperature': 'HVAC Temperature',
    'HvacVelocity': 'HVAC Velocity', 'Illuminance': 'Illuminance',
    'Image': 'Image', 'Integer': 'Integer', 'Int64': 'Integer',
    'Length': 'Length', 'LinearForce': 'Linear Force',
    'Luminance': 'Luminance', 'LuminousFlux': 'Luminous Flux',
    'LuminousIntensity': 'Luminous Intensity', 'Mass': 'Mass',
    'MassDensity': 'Mass Density', 'MassPerUnitArea': 'Mass Per Unit Area',
    'Material': 'Material', 'Moment': 'Moment',
    'MultilineText': 'Multiline Text', 'Number': 'Number',
    'NumberOfPoles': 'Number of Poles', 'Period': 'Period',
    'PipeDimension': 'Pipe Dimension', 'PipeSize': 'Pipe Size',
    'PipingDensity': 'Piping Density', 'PipingPressure': 'Piping Pressure',
    'PipingTemperature': 'Piping Temperature', 'PipingVelocity': 'Piping Velocity',
    'PipingVolume': 'Piping Volume', 'PowerPerFlow': 'Power Per Flow',
    'PowerPerLength': 'Power Per Length', 'Reference': 'Reference',
    'Rotation': 'Rotation', 'SectionArea': 'Section Area',
    'SectionDimension': 'Section Dimension', 'SectionModulus': 'Section Modulus',
    'SheetLength': 'Sheet Length', 'Slope': 'Slope',
    'SpecificHeat': 'Specific Heat', 'Speed': 'Speed', 'Stress': 'Stress',
    'String': 'Text', 'ThermalConductivity': 'Thermal Conductivity',
    'ThermalMass': 'Thermal Mass', 'ThermalResistance': 'Thermal Resistance',
    'Time': 'Time', 'UnitWeight': 'Unit Weight', 'Url': 'URL',
    'Volume': 'Volume', 'Wattage': 'Wattage', 'Weight': 'Weight',
    'WeightPerUnitLength': 'Weight Per Unit Length', 'WireDiameter': 'Wire Diameter',
    'YesNo': 'Yes/No', 'Text': 'Text', 'FamilyType': 'Family Type',
}

if _USE_FORGE:
    DEFAULT_PARAM_GROUP = GroupTypeId.ElectricalAnalysis
    _GROUP_MAP = {
        'Electrical Analysis': GroupTypeId.ElectricalAnalysis,
        'Electrical - Loads':  GroupTypeId.ElectricalLoads,
        'Identity Data':       GroupTypeId.IdentityData,
        'Text':                GroupTypeId.Text,
        'Fire Protection':     GroupTypeId.FireProtection,
        'Data':                GroupTypeId.Data,
    }
    _GROUP_FALLBACK = GroupTypeId.EnergyAnalysis
else:
    DEFAULT_PARAM_GROUP = BuiltInParameterGroup.NONE
    _GROUP_MAP = {
        'Electrical Analysis': BuiltInParameterGroup.PG_ELECTRICAL,
        'Electrical - Loads':  BuiltInParameterGroup.PG_ELECTRICAL_LOADS,
        'Identity Data':       BuiltInParameterGroup.PG_IDENTITY_DATA,
        'Text':                BuiltInParameterGroup.PG_TEXT,
    }
    _GROUP_FALLBACK = BuiltInParameterGroup.PG_TEXT

# Family categories that should be hidden from the family picker. The list is
# substring-matched (case-insensitive) against the FamilyCategory.Name.
_FAMILY_EXCLUDE = ('tag', 'annotation', 'heads', 'view', 'fittings',
                   'marks', 'title', 'structural', 'symbol',
                   'detail', 'profile', 'baluster', 'casework')
_FAMILY_EXCLUDE_RE = re.compile('|'.join(re.escape(k) for k in _FAMILY_EXCLUDE))

_CONN_NUM_RE = re.compile(r'\d+_')

_SCHED_BASE_KEYS         = [k for k in SCHEDULE_PARAMETERS if not k.startswith('1_')]
_SCHED_PER_CONN_SUFFIXES = [k[2:] for k in SCHEDULE_PARAMETERS if k.startswith('1_')]
_ACTIVE_PARAM_LIST_CACHE = {}

# Smart defaults: parameters that should default to TYPE rather than INSTANCE.
_DEFAULT_TYPE_PARAMS = frozenset({
    'Division 26',
    'Division Designator [txt]',
    'Equipment Category [txt]',
})

# Group ordering used to control the visual order of CollectionViewSource
# groups. Lower numbers appear first. Anything not in this map sorts at 50.
_GROUP_ORDER = {
    'Electrical - Loads':   0,
    'Electrical Analysis':  1,
    'Identity Data':        2,
    'Text':                 3,
    'Fire Protection':      4,
    'Data':                 5,
    '(Ungrouped)':         99,
}

# Connector type options. Some entries are conditionally registered at runtime
# because not all ElectricalSystemType members exist in every Revit version.
_CONNECTOR_TYPE_PAIRS_PRIMARY = [
    (u'Power \u2013 Balanced',   'PowerBalanced'),
    (u'Power \u2013 Unbalanced', 'PowerUnBalanced'),
    (u'Data',                    'Data'),
    (u'Telephone',               'Telephone'),
    (u'Communication',           'Communication'),
]
_CONNECTOR_TYPE_PAIRS_OPTIONAL = [
    (u'Fire Alarm', 'FireAlarm'),
    (u'Nurse Call', 'NurseCall'),
    (u'Controls',   'Controls'),
    (u'Security',   'Security'),
]


# ==================== HELPERS (pure functions) ====================

def _get_doc():
    return __revit__.ActiveUIDocument.Document


def _resolve_param_group(label):
    return _GROUP_MAP.get(label, _GROUP_FALLBACK)


def _group_label_for_param(name):
    # Derive the DataGrid group header from SCHEDULE_PARAMETERS. Per-connector
    # multipoint names like '2_Voltage [V]' are normalized back to their
    # canonical '1_...' key before lookup. Anything not present falls into
    # the '(Ungrouped)' bucket.
    if not name:
        return '(Ungrouped)'
    label = SCHEDULE_PARAMETERS.get(name)
    if label is not None:
        return label
    canonical = _CONN_NUM_RE.sub('1_', name, 1)
    label = SCHEDULE_PARAMETERS.get(canonical)
    if label is not None:
        return label
    return '(Ungrouped)'


def _active_param_list(multipoint, connection_count=1):
    # PERF (carried over from v1.3): return cached reference; callers do not mutate.
    key = (bool(multipoint), connection_count)
    cached = _ACTIVE_PARAM_LIST_CACHE.get(key)
    if cached is not None:
        return cached
    if not multipoint:
        result = list(AUTO_ADD_PARAMS)
    else:
        result = list(_SCHED_BASE_KEYS)
        result_append = result.append
        suffixes = _SCHED_PER_CONN_SUFFIXES
        for n in range(1, connection_count + 1):
            n_prefix = '{0}_'.format(n)
            for suffix in suffixes:
                result_append(n_prefix + suffix)
    _ACTIVE_PARAM_LIST_CACHE[key] = result
    return result


# ==================== REVIT SERVICES ====================
# These remain plain module-level functions. They are called by the ViewModel
# layer and have zero UI awareness.

def get_project_shared_parameter_file(app, prompt_user=False):
    try:
        sp_file = app.SharedParametersFilename
        if sp_file and os.path.exists(sp_file):
            return sp_file
    except Exception as ex:
        print("WARNING: Could not read SharedParametersFilename: {0}".format(ex))

    if os.path.exists(FALLBACK_SP_FILE):
        return FALLBACK_SP_FILE

    if prompt_user:
        try:
            dlg = OpenFileDialog()
            dlg.Title = 'Select Shared Parameter File'
            dlg.Filter = 'Text files (*.txt)|*.txt'
            dlg.Multiselect = False

            picked = dlg.ShowDialog()
            if picked:
                file_path = dlg.FileName
                if file_path and os.path.exists(file_path) and file_path.lower().endswith('.txt'):
                    return file_path
        except Exception as ex:
            print("WARNING: Could not show file picker: {0}".format(ex))

    return None


def read_txt_file_combined(filepath):
    group_dict, param_dict = {}, {}
    try:
        with io_open(filepath, 'r', encoding='utf-16-le') as f:
            for line in f:
                fields = line.strip().split('\t', 7)
                if not fields or not fields[0]:
                    continue
                if fields[0] == 'GROUP' and len(fields) >= 3:
                    group_dict[fields[1]] = fields[2]
                elif fields[0] == 'PARAM' and len(fields) >= 6:
                    desc = fields[7].replace('1', '').replace('0', '') if len(fields) > 7 else ''
                    param_dict[fields[2]] = {
                        'guid':        fields[1],
                        'dtype':       fields[3],
                        'dcat':        fields[4],
                        'group':       group_dict.get(fields[5], 'Unknown'),
                        'visible':     fields[6] if len(fields) > 6 else '1',
                        'description': desc,
                    }
    except Exception as e:
        print("Error reading SP file: {0}".format(e))
    return group_dict, param_dict


def get_loaded_families(doc):
    # PERF: precompiled regex (single C-level scan) instead of a Python loop
    # over each excluded keyword.
    result        = []
    result_append = result.append
    excl_search   = _FAMILY_EXCLUDE_RE.search
    for fam in FilteredElementCollector(doc).OfClass(Family):
        try:
            if not fam.IsEditable:
                continue
            try:
                fcat     = fam.FamilyCategory
                cat_name = fcat.Name if fcat else ''
            except Exception:
                cat_name = ''
            if cat_name and excl_search(cat_name.lower()):
                continue
            result_append((fam.Name, fam, cat_name))
        except Exception:
            pass
    result.sort(key=lambda x: x[0].lower())
    return result


def get_family_parameter_names(project_doc, family_elem):
    fam_doc = None
    try:
        fam_doc = project_doc.EditFamily(family_elem)
        names = set()
        for p in fam_doc.FamilyManager.Parameters:
            try:
                names.add(p.Definition.Name)
            except Exception:
                pass
        return names, None
    except Exception as ex:
        return set(), ex
    finally:
        try:
            if fam_doc:
                fam_doc.Close(False)
        except Exception:
            pass


# ---------- Connector helpers (carried from v1.3) ----------

_PREFERRED_REF_PLANE_NAMES = [
    'Center (Left/Right)',
    'Center (Front/Back)',
    'Center (Elevation)',
    'Reference Plane',
    'Origin',
]


def _get_named_reference_plane(fam_doc):
    by_name    = {}
    first_seen = None
    for rp in FilteredElementCollector(fam_doc).OfClass(ReferencePlane):
        if first_seen is None:
            first_seen = rp
        try:
            n = rp.Name
        except Exception:
            continue
        by_name[n] = rp
    for name in _PREFERRED_REF_PLANE_NAMES:
        rp = by_name.get(name)
        if rp is not None:
            return rp
    return first_seen


def _iter_solids(geom_elem):
    for g in geom_elem:
        if isinstance(g, Solid) and g.Volume > 0:
            yield g
        elif isinstance(g, GeometryInstance):
            for ig in g.GetInstanceGeometry():
                if isinstance(ig, Solid) and ig.Volume > 0:
                    yield ig


def _get_first_planar_face_and_edge(fam_doc):
    opts                   = Options()
    opts.ComputeReferences = True
    opts.DetailLevel       = ViewDetailLevel.Fine
    # PERF: lazy iteration of the FilteredElementCollector — short-circuits
    # without materialising every non-type element.
    for elem in FilteredElementCollector(fam_doc).WhereElementIsNotElementType():
        try:
            geom = elem.get_Geometry(opts)
        except Exception:
            geom = None
        if not geom:
            continue
        for solid in _iter_solids(geom):
            faces = solid.Faces
            if faces is None or faces.Size == 0:
                continue
            for face in faces:
                if isinstance(face, PlanarFace) and face.Reference:
                    loops = face.EdgeLoops
                    if loops and loops.Size > 0:
                        loop = loops.get_Item(0)
                        if loop and loop.Size > 0:
                            edge = loop.get_Item(0)
                            return face, edge
    return None, None


def _get_first_planar_face_reference(fam_doc):
    face, _edge = _get_first_planar_face_and_edge(fam_doc)
    if face is not None:
        return face.Reference, face.Origin, face.FaceNormal
    return None, None, None


def _has_electrical_connector(fam_doc):
    try:
        for ce in FilteredElementCollector(fam_doc).OfClass(ConnectorElement):
            try:
                if ce.Domain == Domain.DomainElectrical:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _create_electrical_connector(fam_doc, system_type):
    """Create an electrical connector of *system_type* in *fam_doc*.

    Strategy:
      1. Try a named reference plane (most reliable in MEP families).
      2. Fall back to the first planar face reference found in geometry.

    Returns (ConnectorElement, message_string).
    Raises RuntimeError if no host can be found.
    Must be called inside an active Transaction on *fam_doc*.
    """
    rp = _get_named_reference_plane(fam_doc)
    if rp is not None:
        try:
            conn = ConnectorElement.CreateElectricalConnector(
                fam_doc, system_type, rp.GetReference()
            )
            return conn, "Connector created on reference plane '{0}'.".format(rp.Name)
        except Exception:
            pass

    face_ref, _origin, _normal = _get_first_planar_face_reference(fam_doc)
    if face_ref is not None:
        conn = ConnectorElement.CreateElectricalConnector(
            fam_doc, system_type, face_ref
        )
        return conn, "Connector created on planar face geometry."

    raise RuntimeError(
        "No reference plane or planar face found in the family document. "
        "Add a named reference plane to the family and retry."
    )


def _build_family_param_map(fam_mgr):
    out = {}
    for fp in fam_mgr.Parameters:
        try:
            out[fp.Definition.Name] = fp
        except Exception:
            pass
    return out


def _build_connector_param_lookup(connector):
    exact_map = {}
    lower_map = {}
    for p in connector.GetOrderedParameters():
        try:
            n = p.Definition.Name
        except Exception:
            continue
        if not n:
            continue
        if n not in exact_map:
            exact_map[n] = p
        ln = n.lower()
        if ln not in lower_map:
            lower_map[ln] = p
    return exact_map, lower_map


def _associate_connector_parameters(fam_doc, connector, association_map=None):
    fam_mgr  = fam_doc.FamilyManager
    mapping  = association_map or CONNECTOR_ASSOCIATION_MAP
    results  = []
    missing  = []

    fam_param_map        = _build_family_param_map(fam_mgr)
    exact_map, lower_map = _build_connector_param_lookup(connector)
    connector_names      = list(exact_map.keys())
    associate            = fam_mgr.AssociateElementParameterToFamilyParameter

    for family_param_name, candidates in mapping:
        family_param = fam_param_map.get(family_param_name)
        if family_param is None:
            missing.append("Family parameter '{0}' not found.".format(family_param_name))
            continue

        connector_param = None
        for target in candidates:
            cp = exact_map.get(target)
            if cp is not None:
                connector_param = cp
                break
        if connector_param is None:
            for target in candidates:
                cp = lower_map.get(target.lower())
                if cp is not None:
                    connector_param = cp
                    break

        if connector_param is None:
            missing.append(
                "Connector parameter for '{0}' not found (searched: {1}; available: {2}).".format(
                    family_param_name,
                    ', '.join(candidates),
                    ', '.join(connector_names) if connector_names else 'none'
                )
            )
            continue

        try:
            associate(connector_param, family_param)
            results.append("{0} -> {1}".format(family_param_name, connector_param.Definition.Name))
        except Exception as ex:
            missing.append("Association failed for '{0}' -> '{1}': {2}".format(
                family_param_name, connector_param.Definition.Name, ex))

    return results, missing


# ==================== INFRASTRUCTURE ====================

class BaseViewModel(INotifyPropertyChanged):
    """Minimal INotifyPropertyChanged base class for ViewModels."""

    def __init__(self):
        self._pc_handlers = []

    # IronPython binds the .NET event Add/Remove accessors via these names.
    def add_PropertyChanged(self, handler):
        self._pc_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        try:
            self._pc_handlers.remove(handler)
        except ValueError:
            pass

    def _notify(self, *names):
        if not self._pc_handlers:
            return
        # Snapshot in case a handler registers/unregisters during dispatch.
        handlers = list(self._pc_handlers)
        for name in names:
            args = PropertyChangedEventArgs(name)
            for h in handlers:
                try:
                    h(self, args)
                except Exception as ex:
                    print("PropertyChanged handler error ({0}): {1}".format(name, ex))


class RelayCommand(ICommand):
    """Generic ICommand implementation that delegates to Python callables."""

    def __init__(self, execute, can_execute=None):
        self._execute     = execute
        self._can_execute = can_execute
        self._handlers    = []

    def add_CanExecuteChanged(self, handler):
        self._handlers.append(handler)

    def remove_CanExecuteChanged(self, handler):
        try:
            self._handlers.remove(handler)
        except ValueError:
            pass

    def CanExecute(self, parameter):
        if self._can_execute is None:
            return True
        try:
            return bool(self._can_execute(parameter))
        except Exception:
            return False

    def Execute(self, parameter):
        if self._execute is not None:
            self._execute(parameter)

    def raise_can_execute_changed(self):
        for h in list(self._handlers):
            try:
                h(self, EventArgs.Empty)
            except Exception:
                pass


# ==================== MODELS ====================

class ConnectorTypeOption(object):
    """Plain data wrapper for an ElectricalSystemType + display label."""

    def __init__(self, label, system_type):
        self.Label      = label
        self.SystemType = system_type

    def __str__(self):
        return self.Label

    def __repr__(self):
        return 'ConnectorTypeOption({0!r})'.format(self.Label)


class _AdaptedLoadOptions(IFamilyLoadOptions):
    """IFamilyLoadOptions impl whose overwrite behaviour is configurable."""

    def __init__(self, overwrite_param_values):
        self._overwrite = bool(overwrite_param_values)

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues.Value = self._overwrite
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        overwriteParameterValues.Value = self._overwrite
        return True


# ==================== VIEW MODELS ====================

class SharedParameterRowVM(BaseViewModel):
    """One row in the TO ADD parameter grid."""

    def __init__(self, param_name, data_type, group_name, data_cat,
                 description, guid, not_found=False, parent_vm=None):
        BaseViewModel.__init__(self)
        self._param_name  = param_name
        self._data_type   = data_type
        self._group_name  = group_name or '(Ungrouped)'
        self._data_cat    = data_cat
        self._description = description
        self._guid        = guid
        self._not_found   = not_found
        self._is_type     = (param_name in _DEFAULT_TYPE_PARAMS)
        self._is_excluded = False
        self._parent_vm   = parent_vm

    # ---- read-only properties (OneWay binding) ----
    @property
    def ParamName(self):  return self._param_name
    @property
    def DataType(self):   return self._data_type
    @property
    def GroupName(self):  return self._group_name
    @property
    def DataCat(self):    return self._data_cat
    @property
    def Description(self): return self._description
    @property
    def ParamGUID(self):  return self._guid
    @property
    def NotFound(self):   return self._not_found
    @property
    def RowOpacity(self):
        # Excluded rows are dimmed; not-found rows are also dimmed (but italic
        # via DataTrigger) to make them visually distinct without colour.
        if self._is_excluded:
            return 0.35
        if self._not_found:
            return 0.6
        return 1.0

    # ---- two-way properties (TwoWay binding from DataGrid checkboxes) ----
    @property
    def IsType(self):
        return self._is_type

    @IsType.setter
    def IsType(self, value):
        v = bool(value) if value is not None else False
        if self._is_type != v:
            self._is_type = v
            self._notify('IsType')

    @property
    def IsExcluded(self):
        return self._is_excluded

    @IsExcluded.setter
    def IsExcluded(self, value):
        v = bool(value) if value is not None else False
        if self._is_excluded != v:
            self._is_excluded = v
            self._notify('IsExcluded', 'RowOpacity')
            if self._parent_vm is not None:
                # Tell parent to recompute "(N to add, M excluded)"
                self._parent_vm._update_active_count()


class FamilyItemVM(BaseViewModel):
    """One entry in the LOADED FAMILIES list."""

    def __init__(self, name, elem, category=''):
        BaseViewModel.__init__(self)
        self._name     = name
        self._elem     = elem
        self._category = category

    @property
    def FamilyName(self):
        return self._name

    @property
    def FamilyElem(self):
        return self._elem

    @property
    def CategoryName(self):
        return self._category

    def update_elem(self, new_elem):
        """Mutate the underlying Family reference after LoadFamily reloads it.

        Mutating in place (rather than constructing a new VM) keeps the same
        Python object reference in the bound ListBox, so the user's selection
        is preserved across an Add operation without any code-behind work.
        """
        self._elem = new_elem


class MainViewModel(BaseViewModel):
    """Top-level ViewModel: owns all state and exposes commands.

    The View binds to properties and commands here via DataContext. The View
    layer has no business logic and no Revit imports.
    """

    def __init__(self, app):
        BaseViewModel.__init__(self)
        self.app = app
        self.doc = _get_doc()

        # Domain state
        self._sp_param_dict      = {}
        self._sp_group_dict      = {}
        self._family_param_cache = {}

        # UI-bound state with sensible defaults
        self._is_multipoint        = False
        self._connection_count     = 1
        self._overwrite_values     = True   # Family Load Options default
        self._status_text          = ''
        self._result_text          = ''
        self._result_is_error      = False
        self._active_count_text    = '(0 to add)'
        self._selected_family_item = None
        self._selected_category    = '(All Categories)'
        self._result_details_text = ''


        # Connector type list + initial selection
        self._connector_types = self._build_connector_type_options()
        self._selected_connector_type = (
            self._connector_types[0] if self._connector_types else None
        )

        # Observable collections
        self._family_all   = []                                  # all FamilyItemVMs
        self._family_items = ObservableCollection[System.Object]()
        self._param_rows   = ObservableCollection[System.Object]()
        self._categories   = ['(All Categories)']

        # Commands
        self.auto_add_command  = RelayCommand(
            lambda p: self._execute_auto_add(),
            lambda p: self._selected_family_item is not None,
        )
        self.conn_inc_command = RelayCommand(
            lambda p: self._on_conn_inc(),
            lambda p: self._is_multipoint and self._connection_count < MAX_CONNECTIONS,
        )
        self.conn_dec_command = RelayCommand(
            lambda p: self._on_conn_dec(),
            lambda p: self._is_multipoint and self._connection_count > 1,
        )

        # Initial data load
        self._load_sp_file()
        self._load_families()

    # ---------- startup helpers ----------

    def _build_connector_type_options(self):
        """Resolve ElectricalSystemType members; skip any not on this Revit."""
        result = []
        for label, attr in _CONNECTOR_TYPE_PAIRS_PRIMARY:
            try:
                result.append(ConnectorTypeOption(label, getattr(ElectricalSystemType, attr)))
            except AttributeError:
                pass
        for label, attr in _CONNECTOR_TYPE_PAIRS_OPTIONAL:
            try:
                result.append(ConnectorTypeOption(label, getattr(ElectricalSystemType, attr)))
            except AttributeError:
                pass
        return result

    def _load_sp_file(self):
        #sp_file = get_project_shared_parameter_file(self.app)
        sp_file = get_project_shared_parameter_file(self.app, prompt_user=False)
        if sp_file:
            self._sp_group_dict, self._sp_param_dict = read_txt_file_combined(sp_file)
            self._status_text = '({0} parameters loaded)'.format(len(self._sp_param_dict))
        else:
            self._status_text = '(No shared parameter file found)'
        self._notify('StatusText')

    def _load_families(self):
        families = get_loaded_families(self.doc)
        self._family_all = [FamilyItemVM(n, f, c) for n, f, c in families]
        cats = sorted({fi.CategoryName for fi in self._family_all if fi.CategoryName})
        self._categories = ['(All Categories)'] + cats
        self._notify('Categories')
        self._apply_family_filter()

    # ---------- bindable read-only properties ----------

    @property
    def FamilyItems(self):
        return self._family_items

    @property
    def Categories(self):
        return self._categories

    @property
    def ParameterRows(self):
        return self._param_rows

    @property
    def ConnectorTypes(self):
        return self._connector_types

    @property
    def StatusText(self):
        return self._status_text

    @property
    def ResultText(self):
        return self._result_text

    @property
    def ResultDetailsText(self):
        return self._result_details_text

    @property
    def HasResultDetails(self):
        return bool(self._result_details_text)

    @property
    def ResultBrush(self):
        # Hex string consumed by the inline "result strip" in XAML.
        return '#B91C1C' if self._result_is_error else '#12413C'

    @property
    def ResultVisible(self):
        return bool(self._result_text)

    @property
    def ActiveCountText(self):
        return self._active_count_text

    @property
    def FamilyCountText(self):
        return '({0})'.format(len(self._family_items))

    @property
    def SelectedFamilyName(self):
        return self._selected_family_item.FamilyName if self._selected_family_item else '(none selected)'

    @property
    def SelectedFamilyCategory(self):
        return self._selected_family_item.CategoryName if self._selected_family_item else ''

    @property
    def HasCategoryBadge(self):
        return bool(self._selected_family_item and self._selected_family_item.CategoryName)

    @property
    def IsStepperEnabled(self):
        return self._is_multipoint

    @property
    def ConnectionCountText(self):
        return str(self._connection_count)

    # ---------- bindable two-way properties ----------

    @property
    def IsMultiPoint(self):
        return self._is_multipoint

    @IsMultiPoint.setter
    def IsMultiPoint(self, value):
        v = bool(value) if value is not None else False
        if self._is_multipoint == v:
            return
        self._is_multipoint = v
        if not v:
            self._connection_count = 1
            self._notify('ConnectionCountText')
        self._notify('IsMultiPoint', 'IsStepperEnabled')
        self.conn_inc_command.raise_can_execute_changed()
        self.conn_dec_command.raise_can_execute_changed()
        self._refresh_parameter_tray()

    @property
    def OverwriteParamValues(self):
        return self._overwrite_values

    @OverwriteParamValues.setter
    def OverwriteParamValues(self, value):
        v = bool(value) if value is not None else True
        if self._overwrite_values != v:
            self._overwrite_values = v
            self._notify('OverwriteParamValues')

    # ---------- methods called from the View's event handlers ----------
    # Selection events (ListBox / ComboBox SelectionChanged) are not as robust
    # to TwoWay binding under IronPython as simple bools, so we route them via
    # explicit setter methods rather than property setters.

    @property
    def SelectedFamilyItem(self):
        return self._selected_family_item

    def set_selected_family(self, item):
        if self._selected_family_item is item:
            return
        self._selected_family_item = item
        self._notify('SelectedFamilyItem',
                     'SelectedFamilyName',
                     'SelectedFamilyCategory',
                     'HasCategoryBadge')
        self.auto_add_command.raise_can_execute_changed()
        self._refresh_parameter_tray()

    @property
    def SelectedCategory(self):
        return self._selected_category

    def set_selected_category(self, value):
        if not value or self._selected_category == value:
            return
        self._selected_category = value
        self._apply_family_filter()

    @property
    def SelectedConnectorType(self):
        return self._selected_connector_type

    def set_selected_connector_type(self, value):
        if value is None or self._selected_connector_type is value:
            return
        self._selected_connector_type = value
        self._notify('SelectedConnectorType')

    # ---------- internal updates ----------

    def _apply_family_filter(self):
        cat = self._selected_category
        if not cat or cat == '(All Categories)':
            filtered = self._family_all
        else:
            filtered = [fi for fi in self._family_all if fi.CategoryName == cat]

        # PERF (carried from v1.3): build a fresh ObservableCollection unbound
        # and then replace _family_items wholesale. The {Binding FamilyItems}
        # detects ONE property change and the ListBox refreshes once.
        new_col = ObservableCollection[System.Object]()
        col_add = new_col.Add
        for fi in filtered:
            col_add(fi)
        self._family_items = new_col
        self._notify('FamilyItems', 'FamilyCountText')

    def _update_active_count(self):
        rows = list(self._param_rows)
        total    = len(rows)
        excluded = sum(1 for r in rows if r.IsExcluded)
        active   = total - excluded
        if total == 0:
            self._active_count_text = '(nothing to add)'
        elif excluded:
            self._active_count_text = '({0} to add, {1} excluded)'.format(active, excluded)
        else:
            self._active_count_text = '({0} to add)'.format(active)
        self._notify('ActiveCountText')

    def _set_result(self, text, is_error=False, details=''):
        self._result_text = text or ''
        self._result_details_text = details or ''
        self._result_is_error = bool(is_error)
        self._notify('ResultText', 'ResultDetailsText', 'HasResultDetails',
                     'ResultBrush', 'ResultVisible')

    def _get_family_param_names_cached(self, family_elem):
        fam_id = family_elem.Id.IntegerValue
        cached = self._family_param_cache.get(fam_id)
        if cached is not None:
            return cached, None
        names, err = get_family_parameter_names(self.doc, family_elem)
        if not err:
            self._family_param_cache[fam_id] = names
        return names, err

    # ---------- parameter tray ----------

    def _refresh_parameter_tray(self):
        # Wipe and rebuild. The View's CollectionViewSource is observing
        # _param_rows directly, so groups update automatically.
        self._param_rows.Clear()

        if self._selected_family_item is None or not self._sp_param_dict:
            self._update_active_count()
            return

        family_elem = self._selected_family_item.FamilyElem
        existing, err = self._get_family_param_names_cached(family_elem)
        if err:
            self._status_text = '(Could not inspect family)'
            self._notify('StatusText')
            self._update_active_count()
            return

        param_list   = _active_param_list(self._is_multipoint, self._connection_count)
        sp_dict      = self._sp_param_dict
        existing_chk = existing.__contains__
        rows_add     = self._param_rows.Add

        # Sort by group then name. Items appear in this order inside the
        # CollectionViewSource and the section headers therefore appear in
        # _GROUP_ORDER order rather than insertion order. Group labels come
        # from SCHEDULE_PARAMETERS rather than the shared-parameter file.
        def _sort_key(name):
            grp = _group_label_for_param(name)
            return (_GROUP_ORDER.get(grp, 50), name)

        ordered = sorted(
            (n for n in param_list if not existing_chk(n)),
            key=_sort_key,
        )

        for name in ordered:
            data = sp_dict.get(name)
            if data is None:
                row = SharedParameterRowVM(
                    param_name=name, data_type='(not in file)',
                    group_name=_group_label_for_param(name), data_cat='',
                    description='', guid='', not_found=True,
                    parent_vm=self,
                )
            else:
                row = SharedParameterRowVM(
                    param_name=name,
                    data_type=SPEC_TYPE_MAP.get(data.get('dtype', ''), data.get('dtype', '')),
                    group_name=_group_label_for_param(name),
                    data_cat=data.get('dcat', ''),
                    description=data.get('description', ''),
                    guid=data.get('guid', ''),
                    not_found=False,
                    parent_vm=self,
                )
            rows_add(row)

        self._update_active_count()

    # ---------- stepper command implementations ----------

    def _on_conn_inc(self):
        if self._connection_count < MAX_CONNECTIONS:
            self._connection_count += 1
            self._notify('ConnectionCountText')
            self.conn_inc_command.raise_can_execute_changed()
            self.conn_dec_command.raise_can_execute_changed()
            self._refresh_parameter_tray()

    def _on_conn_dec(self):
        if self._connection_count > 1:
            self._connection_count -= 1
            self._notify('ConnectionCountText')
            self.conn_inc_command.raise_can_execute_changed()
            self.conn_dec_command.raise_can_execute_changed()
            self._refresh_parameter_tray()

    # ---------- the main Add command ----------

    def _execute_auto_add(self):
        fi = self._selected_family_item
        if fi is None:
            self._set_result('No family selected.', is_error=True)
            return

        current_sp_file = get_project_shared_parameter_file(self.app, prompt_user=True)
        if not current_sp_file or not os.path.exists(current_sp_file):
            self._set_result('No shared parameter file found.', is_error=True)
            TaskDialog.Show(
                'No Parameter File',
                'No shared parameter file could be found.\n\nFallback: {0}'.format(FALLBACK_SP_FILE)
            )
            return

        family      = fi.FamilyElem
        family_name = fi.FamilyName
        fam_id_int  = family.Id.IntegerValue

        original_sp = self.app.SharedParametersFilename
        self.app.SharedParametersFilename = current_sp_file
        def_file = self.app.OpenSharedParameterFile()
        if def_file is None:
            self.app.SharedParametersFilename = original_sp
            self._set_result('Could not open SP file.', is_error=True)
            TaskDialog.Show('Error', 'Could not open shared parameter file:\n{0}'.format(current_sp_file))
            return

        # PERF (carried from v1.3): bound-method hoisting around a tight loop.
        name_to_def = {}
        ntd_set     = name_to_def.__setitem__
        for grp in def_file.Groups:
            for defn in grp.Definitions:
                ntd_set(defn.Name, defn)

        existing, err = self._get_family_param_names_cached(family)
        if err:
            self.app.SharedParametersFilename = original_sp
            self._set_result('Could not inspect family.', is_error=True)
            TaskDialog.Show('Error', 'Could not inspect family:\n{0}'.format(err))
            return

        param_list   = _active_param_list(self._is_multipoint, self._connection_count)
        existing_chk = existing.__contains__
        ntd_chk      = name_to_def.__contains__

        # Materialise per-row decisions BEFORE opening the family doc — once
        # the document is open the row VMs may still be edited from the UI
        # thread, but the values we capture here drive a single transaction.
        excl_set = {r.ParamName for r in self._param_rows if r.IsExcluded}
        type_map = {r.ParamName: bool(r.IsType) for r in self._param_rows}

        # PERF (carried from v1.3): single pass for missing / not_found.
        missing   = []
        not_found = []
        for n in param_list:
            if existing_chk(n):
                continue
            missing.append(n)
            if not ntd_chk(n):
                not_found.append(n)

        to_add = [n for n in missing if n in name_to_def and n not in excl_set]

        # Connector type from the user's selection (Connector Type Selector).
        selected_ct   = self._selected_connector_type
        conn_sys_type = selected_ct.SystemType if selected_ct is not None else ElectricalSystemType.PowerBalanced
        conn_label    = selected_ct.Label if selected_ct is not None else 'Power - Balanced'

        fam_doc        = None
        added          = []
        failed         = []
        conn_msg       = ''
        assoc_results  = []
        assoc_failures = []

        try:
            fam_doc = self.doc.EditFamily(family)
            fam_mgr = fam_doc.FamilyManager

            with Transaction(fam_doc, 'Add Electrical Params + Connector') as t:
                t.Start()
                try:
                    # ---- 1. Add missing shared parameters ----
                    add_parameter = fam_mgr.AddParameter
                    conn_sub      = _CONN_NUM_RE.sub
                    sched_get     = SCHEDULE_PARAMETERS.get
                    resolve_group = _resolve_param_group
                    default_grp   = DEFAULT_PARAM_GROUP
                    defs          = name_to_def
                    type_get      = type_map.get
                    added_append  = added.append
                    failed_append = failed.append

                    for name in to_add:
                        is_instance = not type_get(name, False)
                        try:
                            norm   = conn_sub('1_', name, 1)
                            glabel = sched_get(norm) or sched_get(name)
                            pgroup = resolve_group(glabel) if glabel else default_grp
                            add_parameter(defs[name], pgroup, is_instance)
                            added_append('{0} ({1})'.format(
                                name, 'Type' if not is_instance else 'Instance'))
                        except Exception as ex:
                            failed_append('{0}: {1}'.format(name, ex))

                    # ---- 2. Create connector of the user-selected type ----
                    if _has_electrical_connector(fam_doc):
                        conn_msg = 'Connector already present - skipped.'
                    else:
                        try:
                            _conn, conn_msg = _create_electrical_connector(fam_doc, conn_sys_type)
                            assoc_results, assoc_failures = _associate_connector_parameters(fam_doc, _conn)
                        except Exception as ce:
                            conn_msg = 'Connector FAILED: {0}'.format(ce)
                            failed_append(conn_msg)

                    if assoc_failures:
                        failed.extend(assoc_failures)

                    t.Commit()
                except Exception:
                    t.RollBack()
                    raise

            # ---- 3. Reload with user-selected Family Load Options ----
            if added or 'created' in conn_msg.lower():
                load_opts = _AdaptedLoadOptions(self._overwrite_values)
                fam_doc.LoadFamily(self.doc, load_opts)
                self._family_param_cache.pop(fam_id_int, None)

        except Exception as ex:
            import traceback
            self._set_result('Transaction failed - see dialog.', is_error=True)
            TaskDialog.Show(
                'Transaction Error',
                'Exception:\n{0}\n\nTraceback:\n{1}'.format(ex, traceback.format_exc())
            )
            return
        finally:
            try:
                if fam_doc:
                    fam_doc.Close(False)
            except Exception:
                pass
            try:
                self.app.SharedParametersFilename = original_sp
            except Exception:
                pass

        # ---- 4. Refresh the tray against the reloaded family ----
        # PERF (carried from v1.3): direct GetElement-by-id, with the v1.3
        # full-collector fallback if the lookup ever fails.
        refreshed = None
        try:
            refreshed = self.doc.GetElement(ElementId(fam_id_int))
        except Exception:
            refreshed = None
        if refreshed is None:
            try:
                for f in FilteredElementCollector(self.doc).OfClass(Family):
                    try:
                        if f.Name == family_name:
                            refreshed = f
                            break
                    except Exception:
                        pass
            except Exception:
                pass

        if refreshed is not None:
            # Mutate the existing FamilyItemVM in-place so the ListBox keeps
            # the user's selection without code-behind work.
            fi.update_elem(refreshed)
            self._refresh_parameter_tray()

        # ---- 5. Inline result strip + summary dialog ----
        summary_parts = []
        if added:     summary_parts.append('Added {0}'.format(len(added)))
        if failed:    summary_parts.append('Failed {0}'.format(len(failed)))
        if not_found: summary_parts.append('Not in SP file: {0}'.format(len(not_found)))
        summary_parts.append('Connector: {0} ({1})'.format(conn_msg, conn_label))
        is_err = bool(failed)
        self._set_result(' | '.join(p for p in summary_parts if p), is_error=is_err)

        summary_parts = []
        if added:
            summary_parts.append('Added {0} parameter{1}'.format(
                len(added), '' if len(added) == 1 else 's'))
        else:
            summary_parts.append('Added 0 parameters')

        if failed:
            summary_parts.append('Failed {0}'.format(len(failed)))

        if not_found:
            summary_parts.append('Not in SP file: {0}'.format(len(not_found)))

        if assoc_results:
            summary_parts.append('Associated {0}'.format(len(assoc_results)))

        summary_parts.append('Connector: {0} ({1})'.format(conn_msg, conn_label))

        is_err = bool(failed)
        self._set_result(' | '.join(summary_parts), is_error=is_err)


# ==================== VIEW ====================

class AddSharedParamsView(WPFWindow):
    """Thin View. All business logic lives on the ViewModel.

    Code-behind is intentionally limited to:
      * loading XAML and instantiating the ViewModel
      * setting DataContext
      * wiring CollectionViewSource grouping (this can't be done cleanly in
        XAML alone for an IronPython DataContext)
      * routing the few events that don't survive TwoWay binding under
        IronPython (Selection-changed events on ListBox/ComboBox)
    """

    def __init__(self, app):
        xaml_path = self._write_xaml()
        WPFWindow.__init__(self, xaml_path)

        self._vm = MainViewModel(app)
        self.DataContext = self._vm

        # Bind static collections that don't change after load.
        self.cmb_family_category.ItemsSource = self._vm.Categories
        self.cmb_connector_type.ItemsSource  = self._vm.ConnectorTypes

        # Initial selections in their respective ComboBoxes.
        if self._vm.Categories:
            self.cmb_family_category.SelectedIndex = 0
        if self._vm.ConnectorTypes and self._vm.SelectedConnectorType is not None:
            self.cmb_connector_type.SelectedItem = self._vm.SelectedConnectorType

        # Group the parameter rows by GroupName via a CollectionViewSource.
        self._cvs = CollectionViewSource()
        self._cvs.Source = self._vm.ParameterRows
        self._cvs.GroupDescriptions.Add(PropertyGroupDescription('GroupName'))
        self.sp_grid.ItemsSource = self._cvs.View

    # ----- XAML loading -----
    def _write_xaml(self):
        import tempfile
        xaml_path = os.path.join(tempfile.gettempdir(), 'add_shared_params_family_v14.xaml')
        try:
            if os.path.exists(xaml_path):
                os.remove(xaml_path)
        except Exception:
            pass
        with io_open(xaml_path, 'w', encoding='utf-8') as f:
            f.write(_XAML)
        return xaml_path

    # ----- event handlers (only those not expressible as bindings) -----

    def family_selection_changed(self, sender, args):
        self._vm.set_selected_family(self.family_list.SelectedItem)

    def family_category_changed(self, sender, args):
        cat = self.cmb_family_category.SelectedItem
        if cat is not None:
            self._vm.set_selected_category(cat)

    def connector_type_changed(self, sender, args):
        opt = self.cmb_connector_type.SelectedItem
        if opt is not None:
            self._vm.set_selected_connector_type(opt)


# ==================== XAML ====================
# Stored as a module-level string so the View's __init__ can write it to a
# temp file. (pyRevit's WPFWindow loads from a path.)

_XAML = u"""<?xml version="1.0" encoding="utf-8"?>
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Add Electrical Parameters + Connector  (v1.4)"
        Height="980" Width="1140"
        MinHeight="820" MinWidth="900"
        ShowInTaskbar="False"
        ResizeMode="CanResize"
        WindowStartupLocation="CenterScreen"
        Background="White">

    <Window.Resources>
        <SolidColorBrush x:Key="ForestBrush"     Color="#12413C"/>
        <SolidColorBrush x:Key="SandBrush"       Color="#F1E1C0"/>
        <SolidColorBrush x:Key="LightGrayBrush"  Color="#F5F5F5"/>
        <SolidColorBrush x:Key="MediumGrayBrush" Color="#E0E0E0"/>
        <BooleanToVisibilityConverter x:Key="BoolToVis"/>

        <Style x:Key="PrimaryFlatButton" TargetType="Button">
            <Setter Property="Background"      Value="White"/>
            <Setter Property="Foreground"      Value="{StaticResource ForestBrush}"/>
            <Setter Property="BorderBrush"     Value="{StaticResource ForestBrush}"/>
            <Setter Property="BorderThickness" Value="2"/>
            <Setter Property="Padding"         Value="15,8"/>
            <Setter Property="Margin"          Value="5"/>
            <Setter Property="FontSize"        Value="12"/>
            <Setter Property="FontWeight"      Value="Bold"/>
            <Setter Property="Height"          Value="40"/>
            <Setter Property="Cursor"          Value="Hand"/>
            <Style.Triggers>
                <Trigger Property="IsMouseOver" Value="True">
                    <Setter Property="Background" Value="{StaticResource SandBrush}"/>
                </Trigger>
                <Trigger Property="IsEnabled" Value="False">
                    <Setter Property="Opacity" Value="0.4"/>
                </Trigger>
            </Style.Triggers>
        </Style>

        <Style x:Key="AccentButton" TargetType="Button" BasedOn="{StaticResource PrimaryFlatButton}">
            <Setter Property="Background"  Value="{StaticResource ForestBrush}"/>
            <Setter Property="Foreground"  Value="White"/>
            <Setter Property="BorderBrush" Value="{StaticResource ForestBrush}"/>
            <Style.Triggers>
                <Trigger Property="IsMouseOver" Value="True">
                    <Setter Property="Background" Value="#0e3330"/>
                </Trigger>
                <Trigger Property="IsEnabled" Value="False">
                    <Setter Property="Opacity" Value="0.4"/>
                </Trigger>
            </Style.Triggers>
        </Style>

        <Style x:Key="ColHeader" TargetType="DataGridColumnHeader">
            <Setter Property="Background"                 Value="{StaticResource ForestBrush}"/>
            <Setter Property="Foreground"                 Value="{StaticResource SandBrush}"/>
            <Setter Property="FontWeight"                 Value="Bold"/>
            <Setter Property="FontSize"                   Value="10"/>
            <Setter Property="Padding"                    Value="10,0"/>
            <Setter Property="HorizontalContentAlignment" Value="Left"/>
            <Setter Property="Height"                     Value="30"/>
            <Setter Property="BorderBrush"                Value="#0e3330"/>
            <Setter Property="BorderThickness"            Value="0,0,1,0"/>
            <Setter Property="Cursor"                     Value="Arrow"/>
        </Style>

        <Style x:Key="ColHeaderCenter" TargetType="DataGridColumnHeader" BasedOn="{StaticResource ColHeader}">
            <Setter Property="HorizontalContentAlignment" Value="Center"/>
        </Style>

        <Style x:Key="CellText" TargetType="TextBlock">
            <Setter Property="Padding"           Value="10,0"/>
            <Setter Property="VerticalAlignment" Value="Center"/>
            <Setter Property="FontSize"          Value="12"/>
            <Setter Property="TextTrimming"      Value="CharacterEllipsis"/>
        </Style>

        <Style x:Key="CellTextMuted" TargetType="TextBlock" BasedOn="{StaticResource CellText}">
            <Setter Property="Foreground" Value="#6B7280"/>
            <Setter Property="FontSize"   Value="11"/>
        </Style>

        <Style x:Key="SettingsLabel" TargetType="TextBlock">
            <Setter Property="FontSize"          Value="12"/>
            <Setter Property="FontWeight"        Value="SemiBold"/>
            <Setter Property="Foreground"        Value="{StaticResource ForestBrush}"/>
            <Setter Property="VerticalAlignment" Value="Center"/>
            <Setter Property="Margin"            Value="0,0,8,0"/>
        </Style>

        <Style x:Key="FlatComboBox" TargetType="ComboBox">
            <Setter Property="Background"               Value="White"/>
            <Setter Property="Foreground"               Value="{StaticResource ForestBrush}"/>
            <Setter Property="BorderBrush"              Value="{StaticResource ForestBrush}"/>
            <Setter Property="BorderThickness"          Value="2"/>
            <Setter Property="Padding"                  Value="10,8"/>
            <Setter Property="FontSize"                 Value="12"/>
            <Setter Property="Height"                   Value="34"/>
            <Setter Property="VerticalContentAlignment" Value="Center"/>
            <Setter Property="Cursor"                   Value="Hand"/>
            <Style.Resources>
                <SolidColorBrush x:Key="{x:Static SystemColors.HighlightBrushKey}"     Color="#F1E1C0"/>
                <SolidColorBrush x:Key="{x:Static SystemColors.HighlightTextBrushKey}" Color="#12413C"/>
                <SolidColorBrush x:Key="{x:Static SystemColors.ControlBrushKey}"       Color="White"/>
            </Style.Resources>
        </Style>

        <Style x:Key="StepperButton" TargetType="Button">
            <Setter Property="Width"           Value="28"/>
            <Setter Property="Height"          Value="28"/>
            <Setter Property="FontSize"        Value="16"/>
            <Setter Property="FontWeight"      Value="Bold"/>
            <Setter Property="Background"      Value="White"/>
            <Setter Property="Foreground"      Value="{StaticResource ForestBrush}"/>
            <Setter Property="BorderBrush"     Value="{StaticResource ForestBrush}"/>
            <Setter Property="BorderThickness" Value="2"/>
            <Setter Property="Cursor"          Value="Hand"/>
            <Setter Property="Padding"         Value="0"/>
            <Style.Triggers>
                <Trigger Property="IsMouseOver" Value="True">
                    <Setter Property="Background" Value="{StaticResource SandBrush}"/>
                </Trigger>
                <Trigger Property="IsEnabled" Value="False">
                    <Setter Property="Opacity" Value="0.35"/>
                </Trigger>
            </Style.Triggers>
        </Style>

        <ControlTemplate x:Key="ToggleSwitchTemplate" TargetType="ToggleButton">
            <Grid Width="46" Height="24">
                <Border x:Name="Track"
                        CornerRadius="12"
                        Background="{StaticResource MediumGrayBrush}"
                        BorderBrush="{StaticResource MediumGrayBrush}"
                        BorderThickness="1"/>
                <Ellipse x:Name="Thumb"
                         Width="18" Height="18"
                         Fill="White"
                         HorizontalAlignment="Left"
                         Margin="3,0,0,0"
                         VerticalAlignment="Center"/>
            </Grid>
            <ControlTemplate.Triggers>
                <Trigger Property="IsChecked" Value="True">
                    <Setter TargetName="Track" Property="Background"  Value="{StaticResource ForestBrush}"/>
                    <Setter TargetName="Track" Property="BorderBrush" Value="{StaticResource ForestBrush}"/>
                    <Setter TargetName="Thumb" Property="HorizontalAlignment" Value="Right"/>
                    <Setter TargetName="Thumb" Property="Margin"              Value="0,0,3,0"/>
                </Trigger>
                <Trigger Property="IsMouseOver" Value="True">
                    <Setter TargetName="Track" Property="Opacity" Value="0.85"/>
                </Trigger>
                <Trigger Property="IsEnabled" Value="False">
                    <Setter Property="Opacity" Value="0.4"/>
                </Trigger>
            </ControlTemplate.Triggers>
        </ControlTemplate>
    </Window.Resources>

    <Grid Margin="20">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
        </Grid.RowDefinitions>

        <StackPanel Grid.Row="0"
                    Orientation="Horizontal"
                    HorizontalAlignment="Center"
                    Margin="0,0,0,10">
            <TextBlock Text="Add Electrical Parameters + Connector"
                       FontSize="16"
                       FontWeight="Bold"
                       Foreground="{StaticResource ForestBrush}"/>
            <TextBlock Text="{Binding StatusText}"
                       FontSize="11"
                       FontStyle="Italic"
                       Foreground="{StaticResource MediumGrayBrush}"
                       VerticalAlignment="Center"
                       Margin="12,0,0,0"/>
        </StackPanel>

        <Grid Grid.Row="1" Margin="0,8,0,0">
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="240"/>
                <ColumnDefinition Width="5"/>
                <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>

            <Border Grid.Column="0"
                    BorderBrush="{StaticResource ForestBrush}"
                    BorderThickness="2">
                <Grid>
                    <Grid.RowDefinitions>
                        <RowDefinition Height="40"/>
                        <RowDefinition Height="Auto"/>
                        <RowDefinition Height="*"/>
                    </Grid.RowDefinitions>

                    <Border Grid.Row="0"
                            Background="{StaticResource ForestBrush}"
                            Padding="10">
                        <StackPanel Orientation="Horizontal">
                            <TextBlock Text="LOADED FAMILIES"
                                       Foreground="White"
                                       FontWeight="Bold"
                                       FontSize="12"
                                       VerticalAlignment="Center"/>
                            <TextBlock Text="{Binding FamilyCountText}"
                                       Foreground="White"
                                       FontSize="11"
                                       VerticalAlignment="Center"
                                       Margin="10,0,0,0"/>
                        </StackPanel>
                    </Border>

                    <ComboBox Grid.Row="1"
                              x:Name="cmb_family_category"
                              Style="{StaticResource FlatComboBox}"
                              Margin="6,6,6,4"
                              SelectionChanged="family_category_changed"/>

                    <ListBox Grid.Row="2"
                             x:Name="family_list"
                             ItemsSource="{Binding FamilyItems}"
                             Background="White"
                             BorderThickness="0"
                             Padding="4"
                             Margin="0"
                             VirtualizingPanel.IsVirtualizing="True"
                             VirtualizingPanel.VirtualizationMode="Recycling"
                             SelectionChanged="family_selection_changed">
                        <ListBox.Resources>
                            <SolidColorBrush x:Key="{x:Static SystemColors.HighlightBrushKey}" Color="#D6EAE8"/>
                            <SolidColorBrush x:Key="{x:Static SystemColors.HighlightTextBrushKey}" Color="#12413C"/>
                            <SolidColorBrush x:Key="{x:Static SystemColors.InactiveSelectionHighlightBrushKey}" Color="#EDF4F3"/>
                            <SolidColorBrush x:Key="{x:Static SystemColors.InactiveSelectionHighlightTextBrushKey}" Color="#12413C"/>
                        </ListBox.Resources>

                        <ListBox.ItemContainerStyle>
                            <Style TargetType="ListBoxItem">
                                <Setter Property="Padding"                    Value="8,6"/>
                                <Setter Property="Margin"                     Value="0,1,0,0"/>
                                <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
                                <Setter Property="Background"                 Value="Transparent"/>
                                <Setter Property="BorderThickness"            Value="0"/>
                                <Setter Property="Cursor"                     Value="Hand"/>
                                <Setter Property="Template">
                                    <Setter.Value>
                                        <ControlTemplate TargetType="ListBoxItem">
                                            <Border x:Name="ItemBorder"
                                                    Background="{TemplateBinding Background}"
                                                    BorderThickness="0"
                                                    CornerRadius="4"
                                                    Padding="{TemplateBinding Padding}">
                                                <ContentPresenter/>
                                            </Border>
                                            <ControlTemplate.Triggers>
                                                <Trigger Property="IsMouseOver" Value="True">
                                                    <Setter TargetName="ItemBorder" Property="Background" Value="#EDF4F3"/>
                                                </Trigger>
                                                <Trigger Property="IsSelected" Value="True">
                                                    <Setter TargetName="ItemBorder" Property="Background" Value="#D6EAE8"/>
                                                    <Setter TargetName="ItemBorder" Property="BorderThickness" Value="0,0,0,2"/>
                                                    <Setter TargetName="ItemBorder" Property="BorderBrush" Value="{StaticResource ForestBrush}"/>
                                                </Trigger>
                                            </ControlTemplate.Triggers>
                                        </ControlTemplate>
                                    </Setter.Value>
                                </Setter>
                            </Style>
                        </ListBox.ItemContainerStyle>

                        <ListBox.ItemTemplate>
                            <DataTemplate>
                                <TextBlock Text="{Binding FamilyName}"
                                           FontSize="12"
                                           FontWeight="Medium"
                                           Foreground="#1F2937"
                                           TextTrimming="CharacterEllipsis"/>
                            </DataTemplate>
                        </ListBox.ItemTemplate>
                    </ListBox>
                </Grid>
            </Border>

            <GridSplitter Grid.Column="1"
                          Width="5"
                          Background="{StaticResource SandBrush}"
                          HorizontalAlignment="Stretch"
                          VerticalAlignment="Stretch"/>

            <Grid Grid.Column="2">
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="*"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>

                <Border Grid.Row="0"
                        BorderBrush="{StaticResource ForestBrush}"
                        BorderThickness="2"
                        Margin="0,0,0,6">
                    <Grid>
                        <Grid.RowDefinitions>
                            <RowDefinition Height="40"/>
                            <RowDefinition Height="Auto"/>
                        </Grid.RowDefinitions>

                        <Border Grid.Row="0"
                                Background="{StaticResource ForestBrush}"
                                Padding="10">
                            <TextBlock Text="SELECTED FAMILY"
                                       Foreground="White"
                                       FontWeight="Bold"
                                       FontSize="12"
                                       VerticalAlignment="Center"/>
                        </Border>

                        <Grid Grid.Row="1" Margin="12,10,12,12">
                            <Grid.ColumnDefinitions>
                                <ColumnDefinition Width="*"/>
                                <ColumnDefinition Width="Auto"/>
                            </Grid.ColumnDefinitions>

                            <StackPanel Grid.Column="0"
                                        Orientation="Vertical"
                                        VerticalAlignment="Center">
                                <TextBlock Text="{Binding SelectedFamilyName}"
                                           FontSize="13"
                                           FontWeight="SemiBold"
                                           Foreground="{StaticResource ForestBrush}"
                                           TextTrimming="CharacterEllipsis"/>
                                <Border Background="#E8F0EF"
                                        CornerRadius="3"
                                        Padding="6,2"
                                        Margin="0,4,0,0"
                                        HorizontalAlignment="Left"
                                        Visibility="{Binding HasCategoryBadge, Converter={StaticResource BoolToVis}}">
                                    <TextBlock Text="{Binding SelectedFamilyCategory}"
                                               FontSize="10"
                                               FontWeight="SemiBold"
                                               Foreground="{StaticResource ForestBrush}"/>
                                </Border>
                            </StackPanel>

                            <Grid Grid.Column="1" VerticalAlignment="Center" Margin="20,0,0,0">
                                <Grid.RowDefinitions>
                                    <RowDefinition Height="Auto"/>
                                    <RowDefinition Height="6"/>
                                    <RowDefinition Height="Auto"/>
                                    <RowDefinition Height="6"/>
                                    <RowDefinition Height="Auto"/>
                                </Grid.RowDefinitions>
                                <Grid.ColumnDefinitions>
                                    <ColumnDefinition Width="Auto"/>
                                    <ColumnDefinition Width="Auto"/>
                                </Grid.ColumnDefinitions>

                                <StackPanel Grid.Row="0"
                                            Grid.Column="0"
                                            Orientation="Horizontal"
                                            VerticalAlignment="Center">
                                    <TextBlock Text="Multi-point"
                                               Style="{StaticResource SettingsLabel}"/>
                                    <ToggleButton x:Name="toggle_multipoint"
                                                  Width="46"
                                                  Height="24"
                                                  Cursor="Hand"
                                                  IsChecked="{Binding IsMultiPoint, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"
                                                  Template="{StaticResource ToggleSwitchTemplate}"/>
                                </StackPanel>

                                <StackPanel Grid.Row="0"
                                            Grid.Column="1"
                                            Orientation="Horizontal"
                                            VerticalAlignment="Center"
                                            Margin="20,0,0,0"
                                            IsEnabled="{Binding IsStepperEnabled}">
                                    <TextBlock Text="Connections:"
                                               Style="{StaticResource SettingsLabel}"/>
                                    <Button Style="{StaticResource StepperButton}"
                                            Content="-"
                                            Command="{Binding conn_dec_command}"/>
                                    <TextBlock Text="{Binding ConnectionCountText}"
                                               FontSize="14"
                                               FontWeight="Bold"
                                               Foreground="{StaticResource ForestBrush}"
                                               VerticalAlignment="Center"
                                               TextAlignment="Center"
                                               Width="28"/>
                                    <Button Style="{StaticResource StepperButton}"
                                            Content="+"
                                            Command="{Binding conn_inc_command}"/>
                                </StackPanel>

                                <TextBlock Grid.Row="2"
                                           Grid.Column="0"
                                           Text="Connector type"
                                           Style="{StaticResource SettingsLabel}"
                                           VerticalAlignment="Center"/>

                                <ComboBox Grid.Row="2"
                                          Grid.Column="1"
                                          x:Name="cmb_connector_type"
                                          Style="{StaticResource FlatComboBox}"
                                          DisplayMemberPath="Label"
                                          MinWidth="220"
                                          Margin="0,0,0,0"
                                          SelectionChanged="connector_type_changed"/>

                                <TextBlock Grid.Row="4"
                                           Grid.Column="0"
                                           Text="Overwrite existing values"
                                           Style="{StaticResource SettingsLabel}"
                                           VerticalAlignment="Center"/>

                                <ToggleButton Grid.Row="4"
                                              Grid.Column="1"
                                              x:Name="toggle_overwrite"
                                              Width="46"
                                              Height="24"
                                              HorizontalAlignment="Left"
                                              Cursor="Hand"
                                              IsChecked="{Binding OverwriteParamValues, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"
                                              Template="{StaticResource ToggleSwitchTemplate}"/>
                            </Grid>
                        </Grid>
                    </Grid>
                </Border>

                <Border Grid.Row="1"
                        BorderBrush="{StaticResource ForestBrush}"
                        BorderThickness="2">
                    <Grid>
                        <Grid.RowDefinitions>
                            <RowDefinition Height="40"/>
                            <RowDefinition Height="*"/>
                        </Grid.RowDefinitions>

                        <Border Grid.Row="0"
                                Background="{StaticResource ForestBrush}"
                                Padding="10">
                            <StackPanel Orientation="Horizontal">
                                <TextBlock Text="TO ADD"
                                           Foreground="White"
                                           FontWeight="Bold"
                                           FontSize="12"
                                           VerticalAlignment="Center"/>
                                <TextBlock Text="{Binding ActiveCountText}"
                                           Foreground="White"
                                           FontSize="11"
                                           VerticalAlignment="Center"
                                           Margin="10,0,0,0"/>
                            </StackPanel>
                        </Border>

                        <DataGrid Grid.Row="1"
                                  x:Name="sp_grid"
                                  AutoGenerateColumns="False"
                                  CanUserAddRows="False"
                                  CanUserDeleteRows="False"
                                  SelectionMode="Single"
                                  GridLinesVisibility="Horizontal"
                                  HeadersVisibility="Column"
                                  RowHeight="34"
                                  BorderThickness="0"
                                  Background="White"
                                  RowBackground="White"
                                  AlternatingRowBackground="#FAFAF8"
                                  HorizontalGridLinesBrush="#EBEBEB"
                                  VerticalGridLinesBrush="Transparent"
                                  CanUserSortColumns="False"
                                  CanUserResizeRows="False"
                                  SelectionUnit="FullRow"
                                  VirtualizingPanel.IsVirtualizing="True"
                                  VirtualizingPanel.VirtualizationMode="Recycling"
                                  HorizontalScrollBarVisibility="Auto"
                                  VerticalScrollBarVisibility="Auto">

                            <DataGrid.Resources>
                                <SolidColorBrush x:Key="{x:Static SystemColors.HighlightBrushKey}" Color="#D6EAE8"/>
                                <SolidColorBrush x:Key="{x:Static SystemColors.HighlightTextBrushKey}" Color="#12413C"/>
                                <SolidColorBrush x:Key="{x:Static SystemColors.InactiveSelectionHighlightBrushKey}" Color="#EDF4F3"/>
                                <SolidColorBrush x:Key="{x:Static SystemColors.InactiveSelectionHighlightTextBrushKey}" Color="#12413C"/>
                            </DataGrid.Resources>

                            <DataGrid.RowStyle>
                                <Style TargetType="DataGridRow">
                                    <Setter Property="FontSize" Value="12"/>
                                    <Setter Property="Foreground" Value="#1F2937"/>
                                    <Setter Property="BorderThickness" Value="0"/>
                                    <Setter Property="Opacity" Value="{Binding RowOpacity}"/>
                                    <Style.Triggers>
                                        <DataTrigger Binding="{Binding NotFound}" Value="True">
                                            <Setter Property="Foreground" Value="#9CA3AF"/>
                                            <Setter Property="FontStyle" Value="Italic"/>
                                            <Setter Property="ToolTip" Value="Not found in shared parameter file"/>
                                        </DataTrigger>
                                        <DataTrigger Binding="{Binding IsExcluded}" Value="True">
                                            <Setter Property="ToolTip" Value="Excluded from this run - will not be added"/>
                                        </DataTrigger>
                                        <Trigger Property="IsMouseOver" Value="True">
                                            <Setter Property="Background" Value="#EDF4F3"/>
                                        </Trigger>
                                    </Style.Triggers>
                                </Style>
                            </DataGrid.RowStyle>

                            <DataGrid.CellStyle>
                                <Style TargetType="DataGridCell">
                                    <Setter Property="BorderThickness" Value="0"/>
                                    <Setter Property="Padding" Value="0"/>
                                    <Setter Property="FocusVisualStyle" Value="{x:Null}"/>
                                    <Style.Triggers>
                                        <Trigger Property="IsSelected" Value="True">
                                            <Setter Property="Background" Value="Transparent"/>
                                            <Setter Property="BorderBrush" Value="Transparent"/>
                                        </Trigger>
                                    </Style.Triggers>
                                </Style>
                            </DataGrid.CellStyle>

                            <DataGrid.ColumnHeaderStyle>
                                <Style TargetType="DataGridColumnHeader" BasedOn="{StaticResource ColHeader}"/>
                            </DataGrid.ColumnHeaderStyle>

                            <DataGrid.GroupStyle>
                                <GroupStyle>
                                    <GroupStyle.ContainerStyle>
                                        <Style TargetType="GroupItem">
                                            <Setter Property="Template">
                                                <Setter.Value>
                                                    <ControlTemplate TargetType="GroupItem">
                                                        <StackPanel>
                                                            <Border x:Name="groupHeader"
                                                                    Background="#EDF4F3"
                                                                    BorderBrush="#C0D4D5"
                                                                    BorderThickness="0,0,0,1"
                                                                    Cursor="Hand">
                                                                <Grid>
                                                                    <StackPanel Orientation="Horizontal"
                                                                                IsHitTestVisible="False"
                                                                                Margin="10,5,10,5">
                                                                        <TextBlock x:Name="chevron"
                                                                                   Text="&#x25BE;"
                                                                                   Foreground="{StaticResource ForestBrush}"
                                                                                   FontSize="11"
                                                                                   FontWeight="Bold"
                                                                                   VerticalAlignment="Center"
                                                                                   Margin="0,0,8,0"/>
                                                                        <TextBlock Text="{Binding Name}"
                                                                                   FontWeight="SemiBold"
                                                                                   FontSize="11"
                                                                                   Foreground="{StaticResource ForestBrush}"
                                                                                   VerticalAlignment="Center"/>
                                                                        <TextBlock FontSize="11"
                                                                                   Foreground="#6B7280"
                                                                                   VerticalAlignment="Center"
                                                                                   Margin="6,0,0,0">
                                                                            <TextBlock.Text>
                                                                                <Binding Path="ItemCount" StringFormat="({0})"/>
                                                                            </TextBlock.Text>
                                                                        </TextBlock>
                                                                    </StackPanel>
                                                                    <ToggleButton x:Name="expander"
                                                                                  IsChecked="True"
                                                                                  Background="Transparent"
                                                                                  BorderThickness="0"
                                                                                  Focusable="False"
                                                                                  Cursor="Hand"
                                                                                  HorizontalAlignment="Stretch"
                                                                                  VerticalAlignment="Stretch"
                                                                                  Opacity="0"/>
                                                                </Grid>
                                                            </Border>
                                                            <ItemsPresenter x:Name="items"/>
                                                        </StackPanel>
                                                        <ControlTemplate.Triggers>
                                                            <Trigger SourceName="expander" Property="IsChecked" Value="False">
                                                                <Setter TargetName="items" Property="Visibility" Value="Collapsed"/>
                                                                <Setter TargetName="chevron" Property="Text" Value="&#x25B8;"/>
                                                            </Trigger>
                                                        </ControlTemplate.Triggers>
                                                    </ControlTemplate>
                                                </Setter.Value>
                                            </Setter>
                                        </Style>
                                    </GroupStyle.ContainerStyle>
                                </GroupStyle>
                            </DataGrid.GroupStyle>

                            <DataGrid.Columns>
                                <DataGridTemplateColumn Width="68"
                                                        MinWidth="68"
                                                        HeaderStyle="{StaticResource ColHeaderCenter}">
                                    <DataGridTemplateColumn.Header>EXCL</DataGridTemplateColumn.Header>
                                    <DataGridTemplateColumn.CellTemplate>
                                        <DataTemplate>
                                            <CheckBox HorizontalAlignment="Center"
                                                      VerticalAlignment="Center"
                                                      ToolTip="Exclude this parameter from the next add run"
                                                      IsChecked="{Binding IsExcluded, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"/>
                                        </DataTemplate>
                                    </DataGridTemplateColumn.CellTemplate>
                                </DataGridTemplateColumn>

                                <DataGridTemplateColumn Width="68"
                                                        MinWidth="68"
                                                        HeaderStyle="{StaticResource ColHeaderCenter}">
                                    <DataGridTemplateColumn.Header>TYPE</DataGridTemplateColumn.Header>
                                    <DataGridTemplateColumn.CellTemplate>
                                        <DataTemplate>
                                            <CheckBox HorizontalAlignment="Center"
                                                      VerticalAlignment="Center"
                                                      ToolTip="Add as Type parameter (otherwise Instance)"
                                                      IsChecked="{Binding IsType, Mode=TwoWay, UpdateSourceTrigger=PropertyChanged}"/>
                                        </DataTemplate>
                                    </DataGridTemplateColumn.CellTemplate>
                                </DataGridTemplateColumn>

                                <DataGridTextColumn Header="PARAMETER NAME"
                                                    Binding="{Binding ParamName}"
                                                    Width="2*"
                                                    MinWidth="320"
                                                    IsReadOnly="True">
                                    <DataGridTextColumn.ElementStyle>
                                        <Style TargetType="TextBlock" BasedOn="{StaticResource CellText}">
                                            <Setter Property="FontWeight" Value="Medium"/>
                                        </Style>
                                    </DataGridTextColumn.ElementStyle>
                                </DataGridTextColumn>

                                <DataGridTextColumn Header="DATA TYPE"
                                                    Binding="{Binding DataType}"
                                                    Width="1*"
                                                    MinWidth="200"
                                                    IsReadOnly="True">
                                    <DataGridTextColumn.ElementStyle>
                                        <Style TargetType="TextBlock" BasedOn="{StaticResource CellTextMuted}"/>
                                    </DataGridTextColumn.ElementStyle>
                                </DataGridTextColumn>
                            </DataGrid.Columns>
                        </DataGrid>
                    </Grid>
                </Border>

                <Border Grid.Row="2"
                        Margin="0,8,0,0"
                        Padding="12"
                        Background="#F8F8F4"
                        BorderBrush="#D9D2BD"
                        BorderThickness="1"
                        Visibility="{Binding ResultVisible, Converter={StaticResource BoolToVis}}">
                    <StackPanel>
                        <TextBlock Text="{Binding ResultText}"
                                   FontSize="11"
                                   FontWeight="SemiBold"
                                   TextWrapping="Wrap"
                                   Foreground="{Binding ResultBrush}"/>

                        <Border Margin="0,8,0,0"
                                Padding="8"
                                Background="White"
                                BorderBrush="#E5E7EB"
                                BorderThickness="1"
                                Visibility="{Binding HasResultDetails, Converter={StaticResource BoolToVis}}">
                            <ScrollViewer VerticalScrollBarVisibility="Auto"
                                          HorizontalScrollBarVisibility="Auto"
                                          MaxHeight="180">
                                <TextBlock Text="{Binding ResultDetailsText}"
                                           FontFamily="Consolas"
                                           FontSize="11"
                                           Foreground="#374151"
                                           TextWrapping="NoWrap"/>
                            </ScrollViewer>
                        </Border>
                    </StackPanel>
                </Border>

                <StackPanel Grid.Row="3"
                            Orientation="Horizontal"
                            HorizontalAlignment="Right"
                            Margin="0,8,0,0">
                    <Button Content="Add Params + Electrical Connector"
                            Style="{StaticResource AccentButton}"
                            Command="{Binding auto_add_command}"
                            Width="290"/>
                </StackPanel>
            </Grid>
        </Grid>
    </Grid>
</Window>
"""


# ==================== ENTRY POINT ====================

if __name__ == '__main__':
    app  = __revit__.Application
    view = AddSharedParamsView(app)
    view.ShowDialog()
