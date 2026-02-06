Here is a comprehensive `README.md` designed for developers maintaining or extending this codebase. It focuses on architecture, data flow, and controller logic.

---

# Instrument Control Sweep Interface

## Overview

This project is a modular instrument control application designed to automate magnetic field sweeps while simultaneously capturing S-parameter data from a Vector Network Analyzer (VNA). It features a **decoupled architecture** where the User Interface (GUI) runs separately from the hardware control logic, communicating via subprocesses and configuration files.

## 1. System Architecture

The application uses a **Producer-Consumer** pattern mediated by Python's `asyncio` and `subprocess` modules.

* **The Frontend (`app.py`)**: A Tkinter-based GUI that manages configuration and process scheduling. It does *not* block on hardware I/O.
* **The Backend (Controllers)**: Standalone Python scripts that perform the actual blocking hardware operations.
* **State Management**: Shared state is maintained via `params.ini` (experiment parameters) and `.env` (hardware addresses).

```mermaid
graph TD
    User([User]) -->|Input Params| GUI[app.py]
    GUI -->|Writes| Config[(params.ini)]
    
    subgraph "Async Execution Loop"
        GUI -- Spawns --> Subprocess((Subprocess))
        Subprocess -- stdout/stderr --> Queue[Msg Queue]
        Queue -- Updates --> GUI
    end

    Subprocess -->|Executes| Scripts[Controllers/*.py]
    
    Scripts -->|Read| Config
    Scripts -->|Read| Env[(.env)]
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


* **`VNA.py` (VNA Driver)**:
* **Protocol**: Standard SCPI commands via VISA (TCPIP).
* **Key Logic**: Wraps `pyvisa` to handle query timeouts and data parsing (interleaved Real/Imaginary data -> Complex Numpy array).



### Logic Scripts

* **`detect.py`**:
* Scans all VISA resources (`@py` backend).
* Heuristically identifies instruments (ASRL = Magnet, TCPIP = VNA).
* **Output**: Writes confirmed Resource IDs to `.env`.


* **`experiment.py`**:
* The main run loop.
* Reads `params.ini` for start/stop/step values.
* Iterates through current steps  Sets Magnet  Waits 2s  Averages 3 VNA sweeps  Saves CSV.


* **`calibration.py`**:
* Sweeps current from -4A to 4A.
* Records internal Gaussmeter readings to map **Amps  mT**.
* **Output**: `field_calibration_data.csv`.


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



## 5. Setup & Installation

### Requirements

* **Python 3.9+**
* **Drivers**: `pyvisa`, `pyvisa-py`, `pyserial`
* **Data/Math**: `numpy`, `pandas`, `matplotlib`
* **GUI**: `tkinter` (usually standard with Python)

### Developer Quick Start

1. **Hardware Emulation**:
* If no hardware is present, enable `lab_emulator.py` inside `experiment.py` by uncommenting the import:
```python
# from EM3000S import MagnetController  <-- Comment this
from lab_emulator import MagnetController <-- Uncomment this

```




2. **Running the App**:
```bash
python app.py

```


3. **Debugging**:
* Check `.env` to see if instruments were detected correctly.
* Run controllers individually to isolate errors:
```bash
python controllers/detect.py
python controllers/experiment.py

```