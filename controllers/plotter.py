import numpy as np
import matplotlib.pyplot as plt
import os, sys
import configparser
from dotenv import load_dotenv

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
   
    print("Config loaded successfully.")
except Exception as e:
    raise ValueError("Error reading config file.")

# if len(sys.argv)>1:
    # subdir = sys.argv[1]
# else:
subdir = f"s_params_{CURRENT_LOW}{UNIT}_to_{CURRENT_HIGH}{UNIT}_step_{STEP}{UNIT}"

dir = os.path.join("data",subdir)

assert os.path.isdir(dir)

def import_data(dirname=dir):
    for root, dirs, files in os.walk(dirname):
        # print(roots)
        if dirs != []:
            keys = dirs
            freq = np.load(os.path.join(root, 'frequency.npy'))
            s_param_dict = {key: [] for key in keys}
            continue
        if root[-3:] in keys:
            for file in files:
                s_param_dict[root[-3:]].append(np.load(os.path.join(root, file)))

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
    currs = np.arange(CURRENT_LOW, CURRENT_HIGH + STEP, STEP)
    fig, axs = plt.subplots(2,2, figsize=(6,6), sharex=False, sharey=True)
    dirs = s_params.keys()
    axs = axs.ravel()
    for idx, dir in enumerate(dirs):
        axs[idx].pcolormesh(currs, freq*1e-9, s_params[dir].real.T)
        axs[idx].set_xlabel(f"Current ({UNIT})")
        if idx % 2 == 0:
            axs[idx].set_ylabel("Frequency (GHz)")
        axs[idx].set_title(dir.upper())
    plt.tight_layout()
    plt.savefig(os.path.join(dirname, "s_params_plot.png"), dpi=150)
    plt.show()

if __name__ == "__main__":
    print(matrixize()[1]['s21'].shape)
    plotter()
