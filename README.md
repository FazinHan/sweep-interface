# Electromagnet + VNA Device Analysis

This project was built to interface a Holmarc electromagnet with a VNA to do field sweeps.

### Supported instruments
| Electromagnet | Status |
| --- | --- |
| HO-EM3000S | working |
| HO-EM7000S | driver scaffolded, **control signals not captured yet** — see below |

| VNA | Status |
| --- | --- |
| R&S ZNLE | working |

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

Pick your hardware in the **Configuration** tab first: one dropdown for the electromagnet,
one for the VNA. The choice is written to `params.ini` under `[Devices]` and every
controller reads it when it starts, so nothing else needs editing to change magnet.

The same tab holds the **stabilisation time** (whole seconds) — how long the rig is left
to settle before each VNA read. Every routine that reads the VNA uses it, both the
experiment sweep and the calibration sweep. Hover the `?` beside it for what it does.

### Bringing up the HO-EM7000S
`controllers/EM7000S.py` mirrors the EM3000S driver, but the EM7000S command set has not
been reverse-engineered yet. Every byte, the link settings, the current limit and the
current-mapping polynomial sit in one clearly marked block at the top of that file, and
`connect()` refuses to open the port while `SIGNALS_VERIFIED = False`. To bring it up:
capture the vendor application driving the magnet over the serial link, correct the
constants, re-run a calibration, then flip the flag.

Each magnet keeps its own field calibration file (`calibration_file` on the driver
class), so calibrating one never overwrites the other's curve.

### Data Output
All data is saved in `\data`. `experiment.py` polls the VNA for its data three times, separated by the stabilisation time set in the Configuration tab. What is saved in the final data file is the mean of the three measurements.

### Issues
Magnetic field sweep will be restricted by the calibration resolution due to the lookup function, current sweep does not suffer from this.

Control signals are inaccurate for $|\text{current}|<1$ on the EM3000S.

The EM7000S driver cannot talk to hardware until its control signals are captured.

### Codebase readme
See the [README](controllers/README.md) file in the `controllers` directory.