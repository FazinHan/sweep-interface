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
   
    print(UNIT, CURRENT_LOW, CURRENT_HIGH, STEP)
    print("Config loaded successfully.")
except Exception as e:
    raise ValueError("Error reading config file.")

# if len(sys.argv)>1:
    # subdir = sys.argv[1]
# else:
subdir = f"s_params_{CURRENT_LOW}{UNIT}_to_{CURRENT_HIGH}{UNIT}_step_{STEP}{UNIT}"

dir = os.path.join("data",subdir)

assert os.path.isdir(dir), "Data does not exist, recheck values entered in inputs."

ss = 1
subdir = os.path.join(dir, str(ss))
while os.path.isdir(subdir):
    ss += 1
    subdir = os.path.join(dir, str(ss))
else:
    subdir = os.path.join(dir, str(ss-1))

dir = subdir

try:
    currs = [float(i.split('mT.')[0]) for i in os.listdir(dir) if '.png' not in i]
except ValueError:
    currs = [float(i.split('A.')[0]) for i in os.listdir(dir) if '.png' not in i]
currs = np.linspace(currs[0],currs[-1],len(currs))

def import_data(dirname=dir):
    files = [i for i in os.listdir(dirname) if '.png' not in i]
    s_param_dict = {'S11 (db)': [], 'S12 (db)': [], 'S21 (db)': [], 'S22 (db)': []}
    for file in files:
        frame = pd.read_csv(os.path.join(dirname, file))
        for key in s_param_dict.keys():
            s_param_dict[key].append(frame[key].to_numpy(dtype=float))
    frame = pd.read_csv(os.path.join(dirname, files[0]))
    freq = frame['Frequency (Hz)'].to_numpy(dtype=float)
    return freq, s_param_dict

def matrixize(dirname=dir):
    freq, s_param_dict = import_data(dirname)
    for key in s_param_dict.keys():
        s_param_array = np.zeros_like(s_param_dict[key])
        for idx, array in enumerate(s_param_dict[key]):
            s_param_array[idx,:] = array
        s_param_dict[key] = s_param_array
    return freq, s_param_dict

def plotter(dirname=dir):
    freq, s_params = matrixize(dirname)
    print(currs)
    # currs = np.arange(CURRENT_LOW, CURRENT_HIGH + STEP, STEP)
    fig, axs = plt.subplots(2,2, figsize=(6,6), sharex=False, sharey=True)
    dirs = s_params.keys()
    axs = axs.ravel()
    for idx, dir in enumerate(dirs):
        axs[idx].pcolormesh(currs, freq*1e-9, s_params[dir].T, cmap='jet')
        axs[idx].set_xlabel(f"{'Field' if UNIT == 'mT' else 'Current'} ({UNIT})")
        if idx % 2 == 0:
            axs[idx].set_ylabel("Frequency (GHz)")
        axs[idx].set_title(dir)
    plt.tight_layout()
    plt.savefig(os.path.join(dirname, "s_params_plot.png"), dpi=150)
    plt.show()

if __name__ == "__main__":
    # print(matrixize()[1]['s21'].shape)
    plotter()
