"""
Draws the figures for a completed sweep.

Three modes, selected by the first command-line argument and matching the
buttons in the app's Plotter window:

    full   the four S-parameter colour maps, plus the S21 gradient figure
    pvh    P vs H: 1-D traces along the field axis with detected peaks
    dpdh   dP/dH vs H: the same, differentiated along field

The peak-detected modes read their settings from [Plotter] in params.ini.
Where a run recorded DC voltage, the colour maps also carry it on a
right-hand twin axis.
"""
import numpy as np
import matplotlib.pyplot as plt
import os, sys
import configparser
from dotenv import load_dotenv
import pandas as pd

import peaks as peak_tools

load_dotenv()

MODES = ('full', 'pvh', 'dpdh')

dir = "data"

CONFIG_FILE = 'params.ini'

if not os.path.exists(CONFIG_FILE):
    raise FileNotFoundError("params.ini not found!")
    
config = configparser.ConfigParser()
config.read(CONFIG_FILE)
    
# Which data to plot comes from [Plotter], not [Experiment]: the Plotter
# window owns its own sweep boxes so that setting up the next measurement does
# not silently repoint it at different data.
if not config.has_section('Plotter'):
    raise ValueError(
        "No [Plotter] section in params.ini. Open the Plotter window from the "
        "Experiment tab and enter the sweep to plot."
    )

try:
    UNIT = config.get('Plotter', 'unit', fallback='A')
    CURRENT_LOW = float(config.get('Plotter', 'low'))
    CURRENT_HIGH = float(config.get('Plotter', 'high'))
    STEP = float(config.get('Plotter', 'step'))
    DOWN = config.getboolean('Plotter', 'sweep_down', fallback=False)

    print(UNIT, CURRENT_LOW, CURRENT_HIGH, STEP)
    print("Config loaded successfully.")
except Exception as e:
    raise ValueError(
        f"Could not read the sweep to plot from [Plotter] in {CONFIG_FILE}: "
        f"{e}. Fill in Low, High and Step in the Plotter window."
    )

# Plotter window settings. The GUI validates these before enabling its
# buttons; the fallbacks here only matter when plotter.py is run by hand.
FIT_SHAPE = config.get('Plotter', 'fit_shape',
                       fallback=peak_tools.LORENTZIAN).strip().lower()
N_PEAKS = int(config.get('Plotter', 'n_peaks', fallback='1'))
N_TRACES = int(config.get('Plotter', 'n_traces', fallback='3'))
S_PARAM = config.get('Plotter', 's_param', fallback='S21').strip().upper()

# if len(sys.argv)>1:
    # subdir = sys.argv[1]
# else:
subdir = f"s_params_{CURRENT_LOW}{UNIT}_to_{CURRENT_HIGH}{UNIT}_step_{STEP}{UNIT}{'_DOWN' if DOWN else ''}"

dir = os.path.join("data",subdir)

if not os.path.isdir(dir):
    raise FileNotFoundError(
        f"No data at '{dir}'. Recheck the values entered in the Experiment tab "
        f"- the folder name is built from them, so a different low/high/step "
        f"(or the sweep-down setting) points at a different folder."
    )


def latest_run(sweep_dir):
    """
    The highest-numbered run directory inside a sweep directory.

    Counting up from 1 until a miss lands on run 0 when the sweep folder exists
    but holds no runs yet, and stops early if an intermediate run was deleted.
    """
    runs = [int(name) for name in os.listdir(sweep_dir)
            if name.isdigit() and os.path.isdir(os.path.join(sweep_dir, name))]
    if not runs:
        raise FileNotFoundError(
            f"'{sweep_dir}' has no run directories in it. Run the experiment "
            f"for these parameters before plotting."
        )
    return os.path.join(sweep_dir, str(max(runs)))


dir = latest_run(dir)

AXIS_LABEL = f"{'Field' if UNIT == 'mT' else 'Current'} ({UNIT})"

def _parse_value(filename):
    """'-1.50mT.csv' -> -1.5. The unit suffix comes from params.ini."""
    stem = os.path.splitext(filename)[0]
    if stem.endswith(UNIT):
        stem = stem[:-len(UNIT)]
    return float(stem)

def _data_files(dirname):
    """CSV files for the run, sorted by their swept value (os.listdir is not)."""
    files = [i for i in os.listdir(dirname) if i.lower().endswith('.csv')]
    return sorted(files, key=_parse_value)

def read_metadata(path):
    """
    The comment-prefixed scalar block above the table, as {label: value}.

    Written by experiment.py for runs that recorded DC voltage. Absent on
    VNA-only runs, which is not an error -- it just means there is no voltage
    to plot.
    """
    metadata = {}
    with open(path) as handle:
        for line in handle:
            if not line.startswith('#'):
                break                       # table starts; stop reading
            label, _, value = line[1:].partition(':')
            try:
                metadata[label.strip()] = float(value.strip())
            except ValueError:
                metadata[label.strip()] = value.strip()
    return metadata


def import_data(dirname=dir):
    files = _data_files(dirname)
    if not files:
        raise FileNotFoundError(
            f"'{dirname}' holds no CSV files - the run was probably aborted "
            f"before its first point finished."
        )
    currs = np.array([_parse_value(f) for f in files])
    s_param_dict = {'S11 (db)': [], 'S12 (db)': [], 'S21 (db)': [], 'S22 (db)': []}
    voltages = []
    for file in files:
        path = os.path.join(dirname, file)
        # comment='#' skips the metadata block, so this reads old VNA-only
        # files and new ones with a voltage line identically.
        frame = pd.read_csv(path, comment='#')
        for key in s_param_dict.keys():
            s_param_dict[key].append(frame[key].to_numpy(dtype=float))
        voltages.append(read_metadata(path).get('DC Voltage (V)'))
    frame = pd.read_csv(os.path.join(dirname, files[0]), comment='#')
    freq = frame['Frequency (Hz)'].to_numpy(dtype=float)
    # All-or-nothing: a partial voltage column would plot a broken line, so
    # unless every point has one, treat the run as VNA-only.
    volts = (np.array(voltages, dtype=float)
             if voltages and all(v is not None for v in voltages) else None)
    return currs, freq, s_param_dict, volts

def matrixize(dirname=dir):
    currs, freq, s_param_dict, volts = import_data(dirname)
    for key in s_param_dict.keys():
        s_param_array = np.zeros_like(s_param_dict[key])
        for idx, array in enumerate(s_param_dict[key]):
            s_param_array[idx,:] = array
        s_param_dict[key] = s_param_array
    return currs, freq, s_param_dict, volts


def _overlay_voltage(ax, currs, volts):
    """
    Draws DC voltage against field on a right-hand twin axis.

    White line with a dark outline: the maps underneath are 'jet' and
    'RdBu_r', and no single colour stays legible over either, so the line is
    drawn twice at different widths to give it a contrasting edge.
    """
    twin = ax.twinx()
    twin.plot(currs, volts, color='black', linewidth=2.6, solid_capstyle='round')
    twin.plot(currs, volts, color='white', linewidth=1.3, solid_capstyle='round')
    twin.set_ylabel("DC voltage (V)")
    return twin


def _share_voltage_limits(twins, volts):
    """One set of limits across a figure's twins - it is the same data."""
    if not twins:
        return
    low, high = float(np.nanmin(volts)), float(np.nanmax(volts))
    if not np.isfinite(low) or not np.isfinite(high):
        return
    margin = 0.05 * (high - low) if high > low else 1.0
    for twin in twins:
        twin.set_ylim(low - margin, high + margin)

def plotter(currs, freq, s_params, dirname=dir, volts=None):
    print(currs)
    fig, axs = plt.subplots(2,2, figsize=(6,6), sharex=False, sharey=True)
    axs = axs.ravel()
    twins = []
    for idx, key in enumerate(s_params.keys()):
        im=axs[idx].pcolormesh(currs, freq*1e-9, s_params[key].T, cmap='jet')
        axs[idx].set_xlabel(AXIS_LABEL)
        if idx % 2 == 0:
            axs[idx].set_ylabel("Frequency (GHz)")
        axs[idx].set_title(key)
        plt.colorbar(im, ax=axs[idx], label='Magnitude (dB)')
        if volts is not None:
            twins.append(_overlay_voltage(axs[idx], currs, volts))
    _share_voltage_limits(twins, volts if volts is not None else [])
    plt.tight_layout()
    plt.savefig(os.path.join(dirname, "s_params_plot.png"), dpi=150)
    return fig

def gradient_plotter(currs, freq, s_params, dirname=dir, volts=None):
    """
    Second figure: the first derivative of |S21| (dB), taken along each axis.
    Left  = d|S21|/d(field or current), the usual FMR-style lineshape.
    Right = d|S21|/d(frequency).
    np.gradient is given the real coordinate vectors, so non-uniform steps
    and descending (sweep-down) axes are handled correctly.
    """
    s21 = s_params['S21 (db)']          # shape (n_values, n_freq)
    freq_ghz = freq * 1e-9

    if currs.size < 2:
        print("Need at least 2 sweep points for a gradient plot; skipping.")
        return None

    grads = [
        (np.gradient(s21, currs, axis=0),
         f"d|S21| / d{'Field' if UNIT == 'mT' else 'Current'}", f"dB/{UNIT}"),
        (np.gradient(s21, freq_ghz, axis=1), "d|S21| / dFrequency", "dB/GHz"),
    ]

    fig, axs = plt.subplots(1, 2, figsize=(10, 4.5), sharey=True)
    twins = []
    for ax, (grad, title, unit) in zip(axs, grads):
        # symmetric limits about zero, clipped at the 99th percentile so a
        # single spike does not wash the map out
        lim = np.nanpercentile(np.abs(grad), 99)
        if not np.isfinite(lim) or lim == 0:
            lim = None
        im = ax.pcolormesh(currs, freq_ghz, grad.T, cmap='RdBu_r',
                           vmin=None if lim is None else -lim,
                           vmax=lim)
        ax.set_xlabel(AXIS_LABEL)
        ax.set_title(title)
        plt.colorbar(im, ax=ax, label=unit)
        if volts is not None:
            twins.append(_overlay_voltage(ax, currs, volts))
    _share_voltage_limits(twins, volts if volts is not None else [])
    axs[0].set_ylabel("Frequency (GHz)")
    plt.tight_layout()
    plt.savefig(os.path.join(dirname, "s21_gradient_plot.png"), dpi=150)
    return fig


def _traces_along_field(currs, freq, s_params, differentiate):
    """
    (n_field, n_freq) -> a list of (frequency, trace) running along field.

    This is the reorientation the peak plots are built on: the stored array
    is indexed by field first, but a resonance moves along field at a fixed
    frequency, so each trace has to be one frequency row.
    """
    matrix = peak_tools.orient_by_field(s_params[f"{S_PARAM} (db)"])
    if differentiate and currs.size > 1:
        matrix = np.gradient(matrix, currs, axis=1)
    return matrix


def peak_plotter(currs, freq, s_params, dirname=dir, differentiate=False):
    """
    1-D traces along the field axis, with detected peaks marked.

    `N_TRACES` frequencies are chosen evenly across the span (first, last and
    evenly between), each drawn as one line, with its peak positions in the
    legend. `N_PEAKS` peaks are sought per trace and refined by a `FIT_SHAPE`
    fit -- see peaks.py for why the fitted position rather than the sampled
    one is what gets reported.
    """
    if currs.size < 2:
        print("Need at least 2 sweep points to plot along field; skipping.")
        return None

    matrix = _traces_along_field(currs, freq, s_params, differentiate)
    chosen = peak_tools.select_evenly(matrix.shape[0], N_TRACES)
    quantity = (f"d|{S_PARAM}| / d{'Field' if UNIT == 'mT' else 'Current'}"
                if differentiate else f"|{S_PARAM}|")
    ylabel = f"{quantity} (dB/{UNIT})" if differentiate else f"{quantity} (dB)"

    print(f"{'dP/dH' if differentiate else 'P'} vs H: {len(chosen)} trace(s) "
          f"of {matrix.shape[0]}, {N_PEAKS} peak(s) each, {FIT_SHAPE} fit")

    fig, ax = plt.subplots(figsize=(9, 5.5))
    colours = plt.cm.viridis(np.linspace(0, 0.9, max(len(chosen), 1)))

    for colour, row in zip(colours, chosen):
        trace = matrix[row]
        result = peak_tools.fit_trace(currs, trace, N_PEAKS, FIT_SHAPE)
        label = peak_tools.format_peak_label(
            result, UNIT, prefix=f"{freq[row]*1e-9:.3f} GHz - ")
        ax.plot(currs, trace, color=colour, linewidth=1.4, label=label)
        # Mark each fitted peak on the trace it belongs to, so a legend entry
        # can be tied back to a feature by eye.
        for centre, ok in zip(result['centres'], result['converged']):
            ax.axvline(centre, color=colour, linestyle='--' if ok else ':',
                       linewidth=0.9, alpha=0.7)
        if result['found'] < result['requested']:
            print(f"  {freq[row]*1e-9:.3f} GHz: found {result['found']} of "
                  f"{result['requested']} requested peaks")

    ax.set_xlabel(AXIS_LABEL)
    ax.set_ylabel(ylabel)
    ax.set_title(f"{quantity} vs {'field' if UNIT == 'mT' else 'current'}, "
                 f"{FIT_SHAPE} fit")
    ax.legend(fontsize='small', loc='best', framealpha=0.9)
    plt.tight_layout()
    name = "dpdh_vs_h_peaks.png" if differentiate else "p_vs_h_peaks.png"
    plt.savefig(os.path.join(dirname, name), dpi=150)
    return fig

if __name__ == "__main__":
    mode = sys.argv[1].strip().lower() if len(sys.argv) > 1 else 'full'
    if mode not in MODES:
        raise SystemExit(f"Unknown plot mode '{mode}'. Expected one of: "
                         f"{', '.join(MODES)}.")

    currs, freq, s_params, volts = matrixize()
    if volts is not None:
        print(f"DC voltage found for all {volts.size} points; overlaying it.")
    else:
        print("No DC voltage in this run; drawing S-parameters only.")

    if mode == 'full':
        plotter(currs, freq, s_params, volts=volts)
        gradient_plotter(currs, freq, s_params, volts=volts)
    elif mode == 'pvh':
        peak_plotter(currs, freq, s_params, differentiate=False)
    elif mode == 'dpdh':
        peak_plotter(currs, freq, s_params, differentiate=True)

    plt.show()
