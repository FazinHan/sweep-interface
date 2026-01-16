# Electromagnet + VNA Device Analysis

This project was built to interface a HO-EM3000S electromagnet with a VNA to do field sweeps.

### Prerequisites
- VISA Backend: [NI-VISA](https://www.ni.com/en-us/support/downloads/drivers/download.ni-visa.html) sadly the electromagnet does not work with the `pyvisa-py` backend.

### Installation
Recommended use with anaconda
```powershell
conda env create -f environment.yaml -n <env-name>
conda activate <env-name>
```
### Usage
Simply run `app.py`.

### Data Output
All data is saved in `\data`. `experiment.py` polls the VNA for its data three times, separated by two seconds. What is saved in the final data file is the mean of the three measurements.

### Issues
Magnetic field sweep will be restricted by the calibration resolution due to the lookup function, current sweep does not suffer from this.

Control signals are inaccurate for $|\text{current}|<1$.