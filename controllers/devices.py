"""
Device registry and rig-wide settings.

One place that knows which drivers exist, so app.py can offer them in the
Configuration tab and the controllers can pick one up without hard-coding a
magnet. The selection lives in params.ini under [Devices]; drivers are imported
lazily, so an unselected (or half-finished) driver can never break a run.

Anything else the Configuration tab exposes about the rig as a whole — the
settle time before a VNA read, for now — is read from [Settings] here too, so
the GUI and the controllers agree on the defaults.

Adding a magnet:
    1. drop the driver in this directory, exposing a MagnetController class
       with the EM3000S API,
    2. add one line to MAGNETS below, saying whether it can be driven in mT.
"""
import configparser
import importlib
import os
from typing import NamedTuple

CONFIG_FILE = 'params.ini'

class MagnetSpec(NamedTuple):
    """
    How to import a magnet driver, and what the rig may ask of it.

    `supports_field` is False for a magnet with no trustworthy mT <-> A curve.
    Commanding such a magnet in mT would mean fitting a calibration that does
    not exist, so the GUI hides field mode and the Calibration tab entirely
    rather than offering controls that fail once a run has started.
    """
    module: str
    cls: str
    supports_field: bool = True


#: display name -> MagnetSpec. The display name is what the GUI shows and what
#: gets written to params.ini.
MAGNETS = {
    'EM3000S': MagnetSpec('EM3000S', 'MagnetController'),
    # Control signals captured and verified, but its gaussmeter is broken, so
    # no field calibration can be taken: Amps only (see EM7000S.py).
    'EM7000S': MagnetSpec('EM7000S', 'MagnetController', supports_field=False),
    # No hardware: prints what it would do. For working on the app itself
    # without the rig, and for exercising a full sweep before committing the
    # magnet to one. Supports mT because its set_field needs no calibration.
    'Emulated': MagnetSpec('lab_emulator', 'MagnetController'),
}

VNAS = {
    'ZNLE': ('VNA', 'VNAController'),
    # Replays a saved ZNLE18 sweep from dev/s_parameters.npz. The same data at
    # every field point, so maps come out flat along the field axis -- it
    # exercises the plumbing, not the physics.
    'Emulated': ('lab_emulator', 'VNAController'),
}

#: Nanovoltmeters. Unlike the magnet and VNA this instrument is optional --
#: plenty of runs are VNA-only -- so selection is gated by [DVM]/enabled
#: rather than by a "none" entry here, and the driver is never imported when
#: that flag is off.
DVMS = {
    '2182A': ('K2182A', 'NanovoltmeterController'),
}

DEFAULT_MAGNET = 'EM3000S'
DEFAULT_VNA = 'ZNLE'
DEFAULT_DVM = '2182A'

#: VISA backend for every controller. Empty means the system VISA library,
#: which on this rig is NI-VISA.
#:
#: NI-VISA is not optional here: the electromagnet's serial link does not work
#: under pyvisa-py (see the top-level README). The VNA used to ask for '@py'
#: while the magnets used the system library, so one process could be talking
#: through two different VISA implementations at once. Everything now goes
#: through the same one. Pass backend='@py' explicitly to override.
VISA_BACKEND = ''

#: Seconds to let the rig settle before each VNA read.
DEFAULT_STABILIZE_TIME = 10

#: Degaussing defaults. The routine drives the magnet through an alternating,
#: decaying current sequence to clear remanent magnetisation from the core:
#: `steps` alternations starting at `start` amps, each `decay` times the last,
#: dwelling `dwell` seconds.
#:
#: `start` is deliberately mild rather than the magnet's full range. Textbook
#: degaussing begins at saturation, which does the most complete job but puts
#: the magnet through its hardest duty every time; starting at 1 A treats the
#: remanence left by ordinary sweeps without that. The trade is real: what a
#: mild pass cannot reach is remanence from having been driven harder than
#: `start`, so raise it here after a run that went near the limit.
#:
#: The floor exists because the drive is not trustworthy at very small
#: currents (see EM3000S._current_map), so continuing below it adds time
#: without adding effect.
DEFAULT_DEGAUSS_START_A = 1.0
DEFAULT_DEGAUSS_STEPS = 12
DEFAULT_DEGAUSS_DECAY = 0.75
DEFAULT_DEGAUSS_DWELL = 2
DEGAUSS_CURRENT_FLOOR_A = 0.1

#: Nanovoltmeter defaults. Channel is which LEMO input the sample is wired
#: into; NPLC is integration time in power line cycles.
DEFAULT_DVM_CHANNEL = 1
DEFAULT_DVM_NPLC = 5.0

#: The EM7000S energises between 1 and 4 of its coils; the field per amp
#: depends on the choice, so it is rig configuration, not a run parameter.
EM7000S_COILS_MIN = 1
EM7000S_COILS_MAX = 4
DEFAULT_EM7000S_COILS = 4


def _read_config():
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        config.read(CONFIG_FILE)
    return config


def stabilize_time():
    """
    Seconds the routines wait for the equipment to settle before each VNA read
    ([Settings]/stabilize_time). Blank or malformed values fall back to the
    default rather than killing a sweep that is already underway.
    """
    raw = _read_config().get('Settings', 'stabilize_time',
                             fallback=str(DEFAULT_STABILIZE_TIME))
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        print(f"Bad stabilize_time '{raw}' in {CONFIG_FILE}; "
              f"using {DEFAULT_STABILIZE_TIME} s.")
        return DEFAULT_STABILIZE_TIME


def em7000s_coils():
    """
    Number of EM7000S coils energised (1-4), from [EM7000S]/coils.

    Same fallback stance as stabilize_time(): a blank or malformed value logs
    a line and uses the default rather than killing a run. An out-of-range
    integer is treated the same way -- there is no sensible clamp, because
    "you asked for 7 coils, you got 4" is exactly the kind of silent
    reinterpretation this file exists to avoid.
    """
    raw = _read_config().get('EM7000S', 'coils',
                             fallback=str(DEFAULT_EM7000S_COILS))
    try:
        coils = int(raw)
        if not EM7000S_COILS_MIN <= coils <= EM7000S_COILS_MAX:
            raise ValueError
        return coils
    except (TypeError, ValueError):
        print(f"Bad EM7000S coils '{raw}' in {CONFIG_FILE} (must be "
              f"{EM7000S_COILS_MIN}-{EM7000S_COILS_MAX}); "
              f"using {DEFAULT_EM7000S_COILS}.")
        return DEFAULT_EM7000S_COILS


def degauss_settings():
    """
    ([Degauss] start, steps, decay, dwell), each falling back to its default.

    Same forgiving stance as stabilize_time(): a malformed value logs a line
    and uses the default rather than refusing to degauss, since the routine is
    most often reached for when the rig is already misbehaving.
    """
    config = _read_config()

    def _read(option, default, cast, valid):
        raw = config.get('Degauss', option, fallback=str(default))
        try:
            value = cast(raw)
            if not valid(value):
                raise ValueError
            return value
        except (TypeError, ValueError):
            print(f"Bad Degauss {option} '{raw}' in {CONFIG_FILE}; "
                  f"using {default}.")
            return default

    return (
        _read('start', DEFAULT_DEGAUSS_START_A, float, lambda v: 0 < v <= 10.0),
        _read('steps', DEFAULT_DEGAUSS_STEPS, int, lambda v: 1 <= v <= 100),
        _read('decay', DEFAULT_DEGAUSS_DECAY, float, lambda v: 0.1 < v < 1.0),
        _read('dwell', DEFAULT_DEGAUSS_DWELL, int, lambda v: 0 <= v <= 600),
    )


def dvm_enabled():
    """
    Whether a nanovoltmeter is wired in for this run ([DVM]/enabled).

    This one flag gates everything downstream: detection skips its GPIB pass,
    the experiment loop never imports the driver, and the plotter draws no
    voltage axis. It is a per-run fact about how the rig is currently wired,
    which is why it lives on the Experiment tab rather than Configuration.
    """
    raw = _read_config().get('DVM', 'enabled', fallback='0').strip()
    return raw.lower() in ('1', 'true', 'yes', 'on')


def dvm_channel():
    """Which LEMO input the sample is wired into (1 or 2)."""
    raw = _read_config().get('DVM', 'channel', fallback=str(DEFAULT_DVM_CHANNEL))
    try:
        channel = int(raw)
        if channel not in (1, 2):
            raise ValueError
        return channel
    except (TypeError, ValueError):
        print(f"Bad DVM channel '{raw}' in {CONFIG_FILE} (must be 1 or 2); "
              f"using {DEFAULT_DVM_CHANNEL}.")
        return DEFAULT_DVM_CHANNEL


def dvm_nplc():
    """Integration time in power line cycles."""
    raw = _read_config().get('DVM', 'nplc', fallback=str(DEFAULT_DVM_NPLC))
    try:
        nplc = float(raw)
        if not 0.01 <= nplc <= 60.0:
            raise ValueError
        return nplc
    except (TypeError, ValueError):
        print(f"Bad DVM nplc '{raw}' in {CONFIG_FILE} (must be 0.01-60); "
              f"using {DEFAULT_DVM_NPLC}.")
        return DEFAULT_DVM_NPLC


def selected_dvm():
    """Name of the nanovoltmeter chosen in the Configuration tab."""
    return _selected('dvm', DVMS, DEFAULT_DVM)


def get_dvm_controller(announce=True):
    """
    Returns the NanovoltmeterController class for the selected DVM, or None
    when no voltmeter is enabled for this run.

    Returning None rather than raising is deliberate: a VNA-only run is a
    normal, supported case, and callers read better as `if dvm_class:` than
    wrapped in a try.
    """
    if not dvm_enabled():
        return None
    name = selected_dvm()
    module_name, class_name = DVMS[name]
    if announce:
        print(f"Nanovoltmeter driver: {name}")
    return getattr(importlib.import_module(module_name), class_name)


def _selected(option, table, default):
    """Reads [Devices]/<option> from params.ini, falling back to `default`."""
    name = _read_config().get('Devices', option, fallback=default)
    if name not in table:
        raise ValueError(
            f"Unknown {option} '{name}' in {CONFIG_FILE}. "
            f"Known: {', '.join(table)}."
        )
    return name


def selected_magnet():
    """Name of the magnet chosen in the Configuration tab."""
    return _selected('magnet', MAGNETS, DEFAULT_MAGNET)


def selected_vna():
    """Name of the VNA chosen in the Configuration tab."""
    return _selected('vna', VNAS, DEFAULT_VNA)


def magnet_supports_field(name=None):
    """
    Whether the magnet can be commanded in mT as well as Amps.

    Answered from the registry table, not from the driver class, so the GUI can
    ask the question without importing a driver (and dragging pyvisa into the
    Tk process just to grey out a radio button).
    """
    if name is None:
        name = selected_magnet()
    if name not in MAGNETS:
        raise ValueError(
            f"Unknown magnet '{name}'. Known: {', '.join(MAGNETS)}."
        )
    return MAGNETS[name].supports_field


def require_field_support(unit):
    """
    Guard for the controllers: refuses a mT run on a magnet without a curve.

    The GUI already hides field mode for such a magnet, but params.ini is a
    plain text file that can be edited (or left over from another magnet), and
    the failure this prevents is a whole sweep taken at the wrong field.
    """
    if unit != 'mT':
        return
    name = selected_magnet()
    if not magnet_supports_field(name):
        raise ValueError(
            f"The {name} cannot be commanded in mT: it has no verified field "
            f"calibration. Set the unit to 'A' in the Experiment/Magnet tab, "
            f"or select a magnet that has been calibrated."
        )


def get_magnet_controller(announce=True):
    """Returns the MagnetController class for the selected magnet."""
    name = selected_magnet()
    spec = MAGNETS[name]
    if announce:
        print(f"Magnet driver: {name}")
    return getattr(importlib.import_module(spec.module), spec.cls)


def get_vna_controller(announce=True):
    """Returns the VNAController class for the selected VNA."""
    name = selected_vna()
    module_name, class_name = VNAS[name]
    if announce:
        print(f"VNA driver: {name}")
    return getattr(importlib.import_module(module_name), class_name)


if __name__ == "__main__":
    print(f"Magnets: {', '.join(MAGNETS)}")
    print(f"VNAs:    {', '.join(VNAS)}")
    print(f"Selected magnet: {selected_magnet()} "
          f"({'A and mT' if magnet_supports_field() else 'A only'})")
    print(f"Selected VNA:    {selected_vna()}")
    print(f"Stabilize time:  {stabilize_time()} s")
