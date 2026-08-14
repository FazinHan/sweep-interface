# Electromagnet + VNA Device Analysis

This project was built to interface a Holmarc electromagnet with a VNA to do field sweeps.

### Supported instruments
| Electromagnet | Status |
| --- | --- |
| HO-EM3000S | working, Amps and mT |
| HO-EM7000S | working, **Amps only** (broken gaussmeter, so no field calibration) — see below |

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

### The HO-EM7000S
`controllers/EM7000S.py` was brought up from USBPcap captures of the vendor application
(2026-08-14). Its command set is documented in the header of that file — it differs from
the EM3000S in nearly every constant, most notably: replies are `2×` the command byte
rather than a fixed ACK, each of the four coils has its own enable opcode and value
channel (the coil count is expressed by which channels a set sequence writes, not by any
standalone message), stop is the single byte `0x27`, and the limit is ±4.2 A. The driver
reproduces every captured byte sequence exactly; the 2- and 3-coil sequences are
interpolated from the 1- and 4-coil captures and print a warning until verified.

The number of coils energised (1–4) is set in the **Configuration** tab and stored in
`params.ini` under `[EM7000S]`.

The EM7000S is **Amps only**: its gaussmeter is broken, so no mT ↔ A calibration can be
taken. Selecting it disables the `mT` option in the Experiment and Magnet tabs and hides
the Calibration tab; anything that still asks for mT (a hand-edited `params.ini`, say) is
refused before the magnet is opened.

Each magnet keeps its own field calibration file (`calibration_file` on the driver
class), so calibrating one never overwrites the other's curve. A calibration run writes
each point as it is measured and only replaces the existing curve once it has finished,
keeping the old one under a timestamped `.bak` — an aborted or failed sweep costs you
nothing.

### Data Output
All data is saved in `\data`. `experiment.py` polls the VNA for its data three times, separated by the stabilisation time set in the Configuration tab. What is saved in the final data file is the mean of the three measurements.

### Issues
Magnetic field sweep will be restricted by the calibration resolution due to the lookup function, current sweep does not suffer from this.

Control signals are inaccurate for $|\text{current}|<1$ on the EM3000S. Below roughly
$0.1$ A it is worse than inaccurate: `EM3000S._current_map` builds the payload by slicing
the hex string, so any mapped value under `0x10` silently becomes zero. `EM7000S.py`
avoids this with `divmod`; the EM3000S has been left alone because changing it changes
the bytes on the wire, which needs verifying against the hardware.

A field sweep is only as good as `field_calibration_data.csv`. If the requested field
falls in a large gap between calibration points the run prints a loud warning, and a
field outside the calibrated range is refused outright.

The EM7000S driver cannot talk to hardware until its control signals are captured.

### Codebase readme
See the [README](controllers/README.md) file in the `controllers` directory.