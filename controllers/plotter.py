import numpy as np
import matplotlib.pyplot as plt
import os, sys
import configparser
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

dir = "data"

CONFIG_FILE = 'params.ini'

if not os.path.exists(CONFIG_FILE):
    raise FileNotFoundError("params.ini not found!")
    
config = configparser.ConfigParser()
config.read(CONFIG_FILE)
    
try:
    # Load Experiment tab values
    UNIT = config.get('Experiment', 'unit', fallback='A')
    CURRENT_LOW = float(config.get('Experiment', 'low', fallback='0'))
    CURRENT_HIGH = float(config.get('Experiment', 'high', fallback='1'))
    STEP = float(config.get('Experiment', 'step', fallback='0.1'))
    DOWN = config.getboolean('Experiment', 'sweep_down', fallback=False)
   
    print(UNIT, CURRENT_LOW, CURRENT_HIGH, STEP)
    print("Config loaded successfully.")
except Exception as e:
    raise ValueError("Error reading config file.")

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

def import_data(dirname=dir):
    files = _data_files(dirname)
    if not files:
        raise FileNotFoundError(
            f"'{dirname}' holds no CSV files - the run was probably aborted "
            f"before its first point finished."
        )
    currs = np.array([_parse_value(f) for f in files])
    s_param_dict = {'S11 (db)': [], 'S12 (db)': [], 'S21 (db)': [], 'S22 (db)': []}
    for file in files:
        frame = pd.read_csv(os.path.join(dirname, file))
        for key in s_param_dict.keys():
            s_param_dict[key].append(frame[key].to_numpy(dtype=float))
    frame = pd.read_csv(os.path.join(dirname, files[0]))
    freq = frame['Frequency (Hz)'].to_numpy(dtype=float)
    return currs, freq, s_param_dict

def matrixize(dirname=dir):
    currs, freq, s_param_dict = import_data(dirname)
    for key in s_param_dict.keys():
        s_param_array = np.zeros_like(s_param_dict[key])
        for idx, array in enumerate(s_param_dict[key]):
            s_param_array[idx,:] = array
        s_param_dict[key] = s_param_array
    return currs, freq, s_param_dict

def plotter(currs, freq, s_params, dirname=dir):
    print(currs)
    fig, axs = plt.subplots(2,2, figsize=(6,6), sharex=False, sharey=True)
    axs = axs.ravel()
    for idx, key in enumerate(s_params.keys()):
        im=axs[idx].pcolormesh(currs, freq*1e-9, s_params[key].T, cmap='jet')
        axs[idx].set_xlabel(AXIS_LABEL)
        if idx % 2 == 0:
            axs[idx].set_ylabel("Frequency (GHz)")
        axs[idx].set_title(key)
        plt.colorbar(im, ax=axs[idx], label='Magnitude (dB)')
    plt.tight_layout()
    plt.savefig(os.path.join(dirname, "s_params_plot.png"), dpi=150)
    return fig

def gradient_plotter(currs, freq, s_params, dirname=dir):
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
    axs[0].set_ylabel("Frequency (GHz)")
    plt.tight_layout()
    plt.savefig(os.path.join(dirname, "s21_gradient_plot.png"), dpi=150)
    return fig

if __name__ == "__main__":
    currs, freq, s_params = matrixize()
    plotter(currs, freq, s_params)
    gradient_plotter(currs, freq, s_params)
    plt.show()
