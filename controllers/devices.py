"""
Device registry.

One place that knows which drivers exist, so app.py can offer them in the
Configuration tab and the controllers can pick one up without hard-coding a
magnet. The selection lives in params.ini under [Devices]; drivers are imported
lazily, so an unselected (or half-finished) driver can never break a run.

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


def _selected(option, table, default):
    """Reads [Devices]/<option> from params.ini, falling back to `default`."""
    name = default
    if os.path.exists(CONFIG_FILE):
        config = configparser.ConfigParser()
        config.read(CONFIG_FILE)
        name = config.get('Devices', option, fallback=default)
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
