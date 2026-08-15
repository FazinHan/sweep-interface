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

| Nanovoltmeter | Status |
| --- | --- |
| Keithley 2182A | optional; **SCPI strings not yet confirmed on hardware** |

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

### Degaussing
An iron core keeps a remanent magnetisation, so the field at "0 A" is whatever the last
run left behind, and an upward sweep does not retrace a downward one. **Degauss** (on
both the Experiment and Magnet tabs) walks that out by alternating the current's polarity
while decaying its amplitude:

```
+1.00, -0.75, +0.56, -0.42, +0.32, -0.24, +0.18, -0.13, +0.10 A, then off
```

It starts mild — 1 A, not the magnet's full range — so a routine degauss is not the
magnet's hardest duty of the day. The trade is that it cannot clear remanence left by
being driven *harder* than the starting amplitude. Tune `start`, `steps`, `decay` and
`dwell` under `[Degauss]` in `params.ini`; the start is clamped to the selected magnet's
own current limit.

For that harder case, **Degauss (full strength)** on the Configuration tab starts from the
selected magnet's limit instead — 4.0 A on the EM3000S, 4.2 A on the EM7000S, taken from
the driver so neither is named in the routine. Use it after a run that drove the magnet
hard. **Keep magnetic material — tools, watches, phones, cards, storage media — clear of
the magnet before running it**; hover the `?` beside the button for the full warning.

### Nanovoltmeter (optional)
Tick **Nanovoltmeter connected** on the Experiment tab to record a DC voltage at every
field point alongside the S-parameters — the electrically-detected FMR channel. That one
checkbox gates everything: detection skips its GPIB pass without it, the sweep never
imports the driver, and the plotter draws no voltage axis. Model, input channel and NPLC
sit on the Configuration tab, greyed out until the box is ticked.

The voltage is written as a comment-prefixed metadata block above each CSV's table rather
than as a column, because it is one number for the whole field point, not something that
varies with frequency. A run without the voltmeter produces a file identical to before.

> The 2182A's SCPI strings were written from the documented command structure, not copied
> from the manual (its text would not extract), so bench-test the driver standalone —
> `python controllers/K2182A.py` — before trusting a long run.

### Plotting
**Plotter** opens a window primed with the sweep entered on the main window. Choose a fit
shape (Lorentzian or Gaussian), how many peaks to find per trace, and how many traces to
draw, then pick a figure:

- **Full spectrum** — the four S-parameter maps and the S21 gradient figure, as before,
  with DC voltage overlaid on a right-hand axis where a run recorded it.
- **P vs H** — traces along the field axis at evenly spaced frequencies, with detected
  peak positions in the legend.
- **dP/dH vs H** — the same, differentiated along field.

The two peak figures stay disabled until their settings are valid. Peaks are found with
`scipy.signal.find_peaks` and then refined by a fit, because the nearest sampled field is
quantised to the sweep step; on a 5 mT step the fit recovers a known resonance to 0.03 mT.
Both maxima and minima count, since a resonance in |S21| is an absorption dip.

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