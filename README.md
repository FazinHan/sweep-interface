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
1) Connect only the electromagnet by serial and VNA by an ethernet cable and run `instrument_detection.py` when both instruments are turned on. This will detect them and save their IDs to a `.env` file.
2) With the field probe plugged in and positioned correctly, run `field_calibration.py`.
3) Set desired parameters in `params.txt`. Either use `FIELD_LOW` and `FIELD_HIGH` with `STEP` (as in the example file) or use `CURRENT_LOW` and `CURRENT_HIGH` with `STEP`.
4) Finally, run `main_interface.py` to start the experiment.

### Plotter Usage
1) Discover within the directory `data` the subdirectory that corresponds to the experiment of interest.
2) Run `plotter.py` with the name of this subdirectory.
3) Plot is displayed and also saved within the subdirectory.