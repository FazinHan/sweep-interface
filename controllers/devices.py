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
    2. add one line to MAGNETS below.
"""
import configparser
import importlib
import os

CONFIG_FILE = 'params.ini'

#: display name -> (module, class). The display name is what the GUI shows and
#: what gets written to params.ini.
MAGNETS = {
    'EM3000S': ('EM3000S', 'MagnetController'),
    'EM7000S': ('EM7000S', 'MagnetController'),
}

VNAS = {
    'ZNLE': ('VNA', 'VNAController'),
}

DEFAULT_MAGNET = 'EM3000S'
DEFAULT_VNA = 'ZNLE'

#: Seconds to let the rig settle before each VNA read.
DEFAULT_STABILIZE_TIME = 10


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


def get_magnet_controller(announce=True):
    """Returns the MagnetController class for the selected magnet."""
    name = selected_magnet()
    module_name, class_name = MAGNETS[name]
    if announce:
        print(f"Magnet driver: {name}")
    return getattr(importlib.import_module(module_name), class_name)


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
    print(f"Selected magnet: {selected_magnet()}")
    print(f"Selected VNA:    {selected_vna()}")
    print(f"Stabilize time:  {stabilize_time()} s")
