Here is a comprehensive `README.md` designed for developers maintaining or extending this codebase. It focuses on architecture, data flow, and controller logic.

---

# Instrument Control Sweep Interface

## Overview

This project is a modular instrument control application designed to automate magnetic field sweeps while simultaneously capturing S-parameter data from a Vector Network Analyzer (VNA). It features a **decoupled architecture** where the User Interface (GUI) runs separately from the hardware control logic, communicating via subprocesses and configuration files.

## 1. System Architecture

The application uses a **Producer-Consumer** pattern mediated by Python's `asyncio` and `subprocess` modules.

* **The Frontend (`app.py`)**: A Tkinter-based GUI that manages configuration and process scheduling. It does *not* block on hardware I/O.
* **The Backend (Controllers)**: Standalone Python scripts that perform the actual blocking hardware operations.
* **State Management**: Shared state is maintained via `params.ini` (experiment parameters *and* the selected instruments) and `.env` (hardware addresses).
* **Instrument Selection**: `devices.py` turns the `[Devices]` entries in `params.ini` into driver classes, so no logic script names a magnet.

```mermaid
graph TD
    User([User]) -->|Input Params| GUI[app.py]
    GUI -->|Writes| Config[(params.ini)]
    
    subgraph "Async Execution Loop"
        GUI -- Spawns --> Subprocess((Subprocess Wrapper))
        Queue[Msg Queue] -- Updates --> GUI
    end

    Subprocess -->|Executes| Scripts[Controllers/*.py]
    
    Scripts -->|Ask which driver| Registry[devices.py]
    Registry -->|Read| Config
    Registry -->|Imports| Drivers[EM3000S / EM7000S / VNA]
    Scripts -->|Read| Config
    Scripts -->|Read| Env[(.env)]
    Scripts -- stdout/stderr --> Queue
    Scripts -->|Read/Write| Hardware[Magnet & VNA]
    Scripts -->|Write| Data[(/data/ Output)]

```

---

## 2. The Controller Ecosystem (`/controllers`)

The core logic resides in `controllers/`. These scripts are designed to be run as subprocesses but can also be executed manually for debugging.

### Hardware Drivers

* **`EM3000S.py` (Magnet Driver)**:
* **Protocol**: Custom reverse-engineered Hex byte protocol over Serial (19200 Baud, 8-N-1).
* **Key Logic**: Maps current (Amps) to Hex values using a cubic polynomial fit ().
* **Safety**: Implements `stop_and_query_field()` to safely ramp down the magnet before disconnecting.


* **`EM7000S.py` (Magnet Driver)**:
* Same public API as `EM3000S.py`, so every logic script drives it unchanged.
* **Protocol** (captured from the vendor app, 2026-08-14; full notes in the file
header): replies are `2×` the command byte (no fixed ACK); four per-coil enable
opcodes (`0x1E`–`0x21`) and value channels (`0x28`–`0x2B`); a 5-byte payload per
channel `[high, low, 0x00, sign, 0x00]`; commit `0x0A → 0x19`; stop is the single
echoed byte `0x27`. **`0x2B` is a coil channel here, not the EM3000S stop** — never
share constants between the drivers.
* **Coil count** (1–4, Configuration tab, `[EM7000S]` in `params.ini`) is expressed
purely by which channels a set sequence writes; the vendor app sends nothing when the
setting changes. 1- and 4-coil sequences are captured verbatim; 2 and 3 are inferred
and warn until verified.
* **Current mapping**: the captured (amps → counts) pairs are used as an
interpolation table rather than a fitted curve, so captured currents reproduce the
vendor app's bytes exactly. Limit ±4.2 A (from the vendor app's own dialog).
* **Amps only**: registered with `supports_field=False` — the gaussmeter is broken,
so no calibration curve can be measured. The GUI disables **mT** and hides the
**Calibration** tab while this magnet is selected; `set_field()` raises, and the
field queries return `None`.


* **`VNA.py` (VNA Driver)**:
* **Protocol**: Standard SCPI commands via VISA (TCPIP).
* **Key Logic**: Wraps `pyvisa` to handle query timeouts and data parsing (interleaved Real/Imaginary data -> Complex Numpy array).



### Device Registry (`devices.py`)

The single place that knows which drivers exist. `MAGNETS` maps the display name shown
in the app's **Configuration** tab to a `MagnetSpec(module, cls, supports_field)`, and
`VNAS` maps to `(module, class)`; the selection is read from `params.ini` `[Devices]` and
the driver is imported lazily, so an unselected or half-finished driver can never break
a run.

```python
from devices import get_magnet_controller
MagnetController = get_magnet_controller()   # class for the selected magnet
```

Adding a magnet: drop a driver exposing a `MagnetController` with the EM3000S API into
this directory, add one line to `MAGNETS`. The GUI dropdown picks it up automatically.

Per-magnet differences that logic scripts rely on are class attributes: `max_current`
(the |I| limit, and the span `calibration.py` sweeps) and `calibration_file` (that
magnet's own mT ↔ A curve, read back by `set_field()`).

**Field capability.** `supports_field=False` marks a magnet that has no trustworthy
mT ↔ A curve and so can only be commanded in Amps. It lives in the registry rather than
on the driver class so the Tk process can ask without importing pyvisa just to grey out
a radio button. Two consumers:

* `magnet_supports_field(name)` — what `app.py` calls to disable the **mT** radio
buttons and hide the **Calibration** tab (running a calibration is how the curve would
be built, and that needs control signals such a magnet does not have yet).
* `require_field_support(unit)` — the guard `experiment.py` and `set_magnet.py` call
before touching hardware, since `params.ini` is editable text and may be left over from
another magnet.

### Field Calibration (`field_calibration.py`)

Shared loading, validation and use of a magnet's mT ↔ A curve; talks to no device. Both
drivers call `current_for_field(path, field)`, which is the same cubic fit of current
against measured field as before, wrapped in checks:

* rows with unreadable values are dropped (one failed reading would otherwise turn every
`set_field()` into a NaN current);
* fewer than `MIN_POINTS` (4, what a cubic needs) is an error naming the file;
* a field outside the measured range is an error — that is extrapolation, and a cubic
leaving its data does so steeply;
* a field inside a gap wider than `MAX_GAP_FRACTION` of the measured span warns loudly
but proceeds, because a sparse curve over a near-linear magnet is still usable.

The checks exist because a bad curve is otherwise silent: the magnet simply sits at the
wrong field for the whole experiment.

The module also serves rig-wide settings from `[Settings]`. `stabilize_time()` returns
the seconds to wait for the equipment to settle before each VNA read; `experiment.py`
and `calibration.py` take their `STABILIZE_TIME` from it. Blank or malformed values log
a line and fall back to `DEFAULT_STABILIZE_TIME` (10 s) rather than killing a sweep
that is already underway.

### Logic Scripts

* **`detect.py`**:
* Scans all VISA resources (`@py` backend) for the **magnet**, classifying serial
candidates by hardware id so a Bluetooth virtual port can never be chosen.
* The **VNA is not discoverable by enumeration** — NI-VISA lists only TCPIP
resources already registered in NI-MAX, and pyvisa-py needs `zeroconf` plus an
instrument advertising over mDNS. Its address is therefore configuration:
`[Devices]/vna_address` in `params.ini` (bare IP or a full VISA string), which
`detect.py` verifies with `*IDN?` before writing.
* **Output**: Writes confirmed Resource IDs to `.env`. An instrument that is *not*
found leaves its previous address alone rather than writing a placeholder — that is
what produced `VNA_ID=None`, which drivers then passed to `open_resource()` as the
literal string `"None"`. Exits non-zero if nothing at all is found.

### Bring-up and capture tooling

Used to reverse-engineer the EM7000S; kept because they are how the next magnet
(or a firmware change) gets handled. None of them are imported by the app.

* **`replay_em7000s.py`** — the regression test for the control signals. Replays the
driver against the captured byte exchanges using a fake instrument that answers as
the real magnet did, and compares byte-for-byte. Run it after touching any opcode,
the payload layout or the current table. Needs no hardware.
* **`probe_em7000s.py`** — read-only serial probe. Sends only opcodes that cannot
raise the current, and refuses to echo a byte that could begin a set sequence.
* **`decode_capture.py`** — USBPcap `.pcap` → FTDI control requests (baud, framing,
DTR/RTS) plus a chronological byte transcript, stripping the two modem-status bytes
FTDI prepends to every IN transfer.
* **`analyse_capture.py`** — that transcript → labelled sequences, classifying each
byte as command (reply doubled), echo, or device data, split on idle gaps.
* **`find_lxi.py`** — mDNS discovery for LAN instruments, for when the VNA does not
appear. Pure stdlib; no port scanning.


* **`experiment.py`**:
* The main run loop.
* Reads `params.ini` for start/stop/step values.
* Iterates through current steps  Sets Magnet  Waits `stabilize_time`  Averages 3 VNA sweeps  Saves CSV.


* **`calibration.py`**:
* Sweeps current across the selected magnet's full range (`±magnet.max_current`).
* Records internal Gaussmeter readings to map **Amps  mT**.
* **Output**: `magnet.calibration_file` — `field_calibration_data.csv` for the EM3000S,
`field_calibration_data_em7000s.csv` for the EM7000S.
* **Never loses a sweep.** A point takes the stabilisation time, so a full run is
hours of hardware time. A reading that does not come back is written blank and the
sweep carries on (it used to die on `f"{field:.2f}"` when `query_field()` returned
`"Query Failed"`); every point is appended to `<calibration_file>.partial` and flushed
as it is measured, so an ABORT — which on Windows terminates the process outright, with
no chance to run cleanup — still leaves everything collected up to that moment on disk.
* **Never overwrites blindly.** The real file is only replaced once a sweep has
finished with at least `MIN_POINTS` usable points, and the file it replaces is kept
under a timestamped `.bak` name. A sweep that fails leaves the old curve in place and
its own data in the `.partial` file.


* **`abort_all.py`**:
* Emergency script. Attempts to connect to the magnet independent of the main process to issue a Stop command (`0x2B`).



---

## 3. Data I/O & File Structure

### Configuration (`params.ini`)

The GUI and controllers share state via this INI file.

```ini
[Experiment]
low = 0         ; Start Current/Field
high = 1        ; End Current/Field
step = 0.1      ; Step Size
unit = A        ; 'A' or 'mT'

[Calibration]
cal_res = 800   ; Resolution points for calibration sweep

[Magnet]
value = 326     ; One-shot set from the Magnet tab
unit = mT       ; 'A' or 'mT'

[Devices]
magnet = EM3000S  ; Key in devices.MAGNETS - written by the Configuration tab
vna = ZNLE        ; Key in devices.VNAS

[Settings]
stabilize_time = 10  ; Seconds to settle before each VNA read

```

### Output Data Schema

Data is organized hierarchically to prevent overwrites.

* **Root**: `/data`
* **Sweep Directory**: Named by parameters to ensure uniqueness.
* *Format*: `s_params_{START}{UNIT}_to_{END}{UNIT}_step_{STEP}{UNIT}`


* **Run Directory**: Incremented integer (e.g., `/1`, `/2`) to allow multiple runs of the same parameters.
* **Files**: One CSV per step.
* *Format*: `{Current/Field_Value}.csv`
* *Content*: Frequency, S11/S12/S21/S22 (Real, Imag, dB, Phase).



---

## 4. The GUI Engine (`app.py`)

The `app.py` script is the orchestrator. It uses a **Threaded Asyncio** model to keep the UI responsive.

### Key Components:

1. **`msg_queue`**: A thread-safe `queue.Queue()`.
* The background thread pushes `("print", "message")` or `("status", "message")` tuples.
* The Main Thread polls this queue every 100ms via `root.after()`.


2. **`run_script_async(script_name)`**:
* Creates a `subprocess.exec`.
* Pipes `stdout` and `stderr` continuously to the `msg_queue`.
* This allows the GUI to show real-time print statements from the controllers (e.g., "Setting field to 0.5T...").


3. **Abort Logic**:
* Sends `SIGTERM` to the active subprocess.
* Immediately schedules `abort_all.py` to ensure the hardware is not left in a high-energy state.


4. **Configuration Tab**:
* Two read-only comboboxes populated from `devices.MAGNETS` / `devices.VNAS`.
* An integer-only **Stabilisation time (s)** box, with a `?` badge whose hover text explains it (`Tooltip` — a borderless `Toplevel`, since ttk has none).
* Both write to `params.ini` as soon as they change (`on_device_change`, `on_stabilize_change` on `<FocusOut>`/`<Return>`), because the controllers only read the file at process start. A blank settle box is reset to the default rather than saved empty.
* `app.py` imports `devices` directly by putting `controllers/` on `sys.path`; the module is stdlib-only and imports drivers lazily, so the GUI never pulls in `pyvisa`.



## 5. Setup & Installation

### Requirements

* **Python 3.9+**
* **Drivers**: `pyvisa`, `pyvisa-py`, `pyserial`
* **Data/Math**: `numpy`, `pandas`, `matplotlib`
* **GUI**: `tkinter` (usually standard with Python)

### Developer Quick Start

1. **Hardware Emulation**:
* If no hardware is present, enable `lab_emulator.py` inside `experiment.py` by uncommenting the import. It sits *after* the registry lookup, so it shadows whatever the Configuration tab selected:
```python
MagnetController = get_magnet_controller()
VNAController = get_vna_controller()
# Uncomment to run without hardware (overrides the selection above):
from lab_emulator import MagnetController, VNAController   <-- Uncomment this

```




2. **Running the App**:
```bash
python app.py

```


3. **Debugging**:
* Check `.env` to see if instruments were detected correctly.
* Check which drivers are selected: `python controllers/devices.py`.
* Run controllers individually to isolate errors (from the repo root — `params.ini` and the calibration CSVs resolve relative to the CWD):
```bash
python controllers/detect.py
python controllers/experiment.py

```