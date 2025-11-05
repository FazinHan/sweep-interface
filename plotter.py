import numpy as np
import matplotlib.pyplot as plt
import os, sys
from dotenv import load_dotenv

load_dotenv()

if len(sys.argv)>1:
    subdir = sys.argv[1]
else:
    subdir = "s_params_-1A_to_1A_step_0.1A"

dir = os.path.join("data",subdir)

with open("params.txt", 'r') as f:
    lines = f.readlines()
    params = {}
    for line in lines:
        if line.strip() and not line.startswith("---"):
            key, value = line.strip().split('=')
            params[key] = value.strip().strip('"')

min_curr = float(params.get("CURRENT_LOW", -1))
max_curr = float(params.get("CURRENT_HIGH", 1))
step = float(params.get("STEP", 0.1))

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
    currs = np.arange(min_curr, max_curr + step, step)
    fig, axs = plt.subplots(2,2, figsize=(6,6), sharex=False, sharey=True)
    dirs = s_params.keys()
    axs = axs.ravel()
    for idx, dir in enumerate(dirs):
        axs[idx].pcolormesh(currs, freq*1e-9, s_params[dir].real.T)
        axs[idx].set_xlabel("Current (A)")
        if idx % 2 == 0:
            axs[idx].set_ylabel("Frequency (GHz)")
        # axs[idx].set_box_aspect(1)
        axs[idx].set_title(dir.upper())
        # axs[idx].set_yticks()
        # axs[idx].set_ylim(min_curr, max_curr)
    plt.tight_layout()
    plt.savefig(os.path.join(dirname, "s_params_plot.png"), dpi=150)
    plt.show()

if __name__ == "__main__":
    print(matrixize()[1]['s21'].shape)
    plotter()