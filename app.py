#!/bin/python
import tkinter as tk
from tkinter import ttk
import asyncio
import threading
import queue
import configparser
import os
import sys

# The controllers are plain scripts, not a package; put their directory on the
# path the same way the subprocesses get it, so the GUI can read the device
# registry (stdlib only — drivers themselves are imported lazily, in the child).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'controllers'))
import devices

# --- Configuration ---
CONFIG_FILE = 'params.ini'
DETECT_SCRIPT = os.path.join('controllers', 'detect.py')
EXPERIMENT_SCRIPT = os.path.join('controllers', 'experiment.py')
CALIBRATION_SCRIPT = os.path.join('controllers', 'calibration.py')
PLOTTER_SCRIPT = os.path.join('controllers', 'plotter.py')
ABORT_SCRIPT = os.path.join('controllers', 'abort_all.py')
SET_MAGNET_SCRIPT = os.path.join('controllers', 'set_magnet.py')
PROBE_MAGNET_SCRIPT = os.path.join('controllers', 'probe_magnet.py')
STOP_MAGNET_SCRIPT = os.path.join('controllers', 'stop_magnet.py')

# --- Global State for Async Control ---
# We need a reference to the current process to kill it later
current_process = None
# True from the moment a script is scheduled until its coroutine finishes.
# Set from the GUI thread, cleared from the loop thread, so a fast double-click
# cannot slip two subprocesses past the guard while the first is still starting.
running = False
# Set by the Abort button so the exit code is reported as an abort on every
# platform (POSIX terminate gives -15, Windows TerminateProcess gives 1).
abort_requested = False
# A queue to pass messages from the background thread to the GUI
msg_queue = queue.Queue()
# The asyncio loop that will run in a background thread
loop = asyncio.new_event_loop()


# --- Async Backend Functions ---

def start_background_loop(loop):
    """Runs the asyncio event loop in a separate thread."""
    asyncio.set_event_loop(loop)
    loop.run_forever()

async def _pump_stream(stream, prefix=""):
    """Forwards a subprocess pipe to the GUI queue, one line at a time."""
    async for line in stream:
        decoded_line = line.decode(errors="replace").strip()
        if decoded_line:
            msg_queue.put(("print", f"{prefix}{decoded_line}"))

async def run_script_async(script_name):
    """
    Async coroutine to run the subprocess.
    It reads output line-by-line and pushes it to the GUI queue.
    """
    global current_process, running, abort_requested

    abort_requested = False
    msg_queue.put(("status", f"Running {script_name}..."))
    msg_queue.put(("print", f"Starting subprocess: python {script_name}"))

    try:
        # Create the subprocess asynchronously
        # We use sys.executable to ensure we use the same python interpreter.
        # -u (plus PYTHONUNBUFFERED for any grandchildren) is what makes the
        # logs live: a piped stdout is block-buffered by default, so without
        # it the controller's prints only arrive once the process exits.
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        current_process = await asyncio.create_subprocess_exec(
            sys.executable, "-u", script_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # Drain stdout and stderr concurrently. Reading stderr only after the
        # process exits deadlocks as soon as a controller writes more than a
        # pipe buffer's worth of warnings (pyvisa is chatty).
        await asyncio.gather(
            _pump_stream(current_process.stdout),
            _pump_stream(current_process.stderr, prefix="stderr: "),
        )

        # Wait for the process to exit
        rc = await current_process.wait()

        if rc != 0:
            if abort_requested:
                msg_queue.put(("status", "Process Aborted."))
                msg_queue.put(("print", "--- Process Aborted by User ---"))
            else:
                msg_queue.put(("status", f"Error: Exited with code {rc}"))
                msg_queue.put(("print", f"Error: Exited with code {rc}"))
        else:
            msg_queue.put(("status", f"{script_name} finished."))
            msg_queue.put(("print", "--- Script Finished Successfully ---"))

    except asyncio.CancelledError:
        msg_queue.put(("status", "Task Cancelled."))
    except Exception as e:
        msg_queue.put(("status", f"Error running {script_name}"))
        msg_queue.put(("print", f"Unexpected error: {e}"))
    finally:
        current_process = None
        running = False

def schedule_script(script_name):
    """Helper to schedule the async task from the synchronous GUI."""
    global running
    if running:
        status_var.set("Error: A process is already running!")
        return

    running = True
    # Schedule the coroutine in the background loop
    asyncio.run_coroutine_threadsafe(run_script_async(script_name), loop)


# --- Abort Functionality ---

async def abort_and_cleanup():
    """Terminates the running script, then de-energises the magnet."""
    global running, abort_requested

    proc = current_process
    if proc is not None:
        abort_requested = True
        msg_queue.put(("status", "Aborting..."))
        msg_queue.put(("print", "--- Sending terminate signal ---"))
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            msg_queue.put(("print", "--- Did not exit in 5s, killing ---"))
            proc.kill()

    # Let the runner coroutine finish its teardown before reusing the slot.
    while running:
        await asyncio.sleep(0.05)

    running = True
    await run_script_async(ABORT_SCRIPT)

def on_abort_click():
    """Kills the running subprocess, then runs the emergency stop script."""
    if not running:
        status_var.set("No process is running.")
        return

    asyncio.run_coroutine_threadsafe(abort_and_cleanup(), loop)

# --- GUI Update Loop ---

# Progress frames arrive as ordinary lines (a bare '\r' from the child would
# never reach us: the pump reads line-by-line). We do the in-place redraw here.
PROGRESS_PREFIX = "PROGRESS:"
_progress_active = False   # last thing written was an animation frame
_progress_width = 0        # widest frame so far, to erase shorter ones

def _write_progress(frame):
    global _progress_active, _progress_width
    _progress_width = max(_progress_width, len(frame))
    sys.stdout.write("\r" + frame.ljust(_progress_width))
    sys.stdout.flush()
    _progress_active = True

def _end_progress():
    """Close an animated line so the next normal print starts on its own row."""
    global _progress_active, _progress_width
    if _progress_active:
        sys.stdout.write("\n")
        sys.stdout.flush()
        _progress_active = False
        _progress_width = 0

def check_queue():
    try:
        while True:
            msg_type, content = msg_queue.get_nowait()

            if msg_type == "status":
                status_var.set(content)
            elif msg_type == "print":
                # Intercept the probe result specifically
                if content.startswith("PROBE_RESULT:"):
                    val = content.split(":")[1].strip()
                    mag_probe_var.set(f"{val} mT")
                elif content.startswith(PROGRESS_PREFIX):
                    _write_progress(content[len(PROGRESS_PREFIX):].strip())
                else:
                    _end_progress()
                    # flush explicitly: our own stdout is block-buffered too
                    # whenever the app is launched with its output redirected.
                    print(content, flush=True)

    except queue.Empty:
        pass

    root.after(50, check_queue)


# --- Configuration Functions (Unchanged Logic) ---

def load_config():
    if not os.path.exists(CONFIG_FILE):
        status_var.set("Error: params.ini not found!")
        return
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    try:
        exp_low_var.set(config.get('Experiment', 'low', fallback='0'))
        exp_high_var.set(config.get('Experiment', 'high', fallback='1'))
        exp_step_var.set(config.get('Experiment', 'step', fallback='0.1'))
        exp_unit_var.set(config.get('Experiment', 'unit', fallback='A'))
        exp_sweep_down_var.set(int(config.get('Experiment', 'sweep_down', fallback='0'))) # <-- ADD THIS LINE
        cal_res_var.set(config.get('Calibration', 'cal_res', fallback='800'))
        mag_val_var.set(config.get('Magnet', 'value', fallback='0'))
        mag_unit_var.set(config.get('Magnet', 'unit', fallback='A'))
        dev_magnet_var.set(config.get('Devices', 'magnet', fallback=devices.DEFAULT_MAGNET))
        dev_vna_var.set(config.get('Devices', 'vna', fallback=devices.DEFAULT_VNA))
        stabilize_var.set(config.get('Settings', 'stabilize_time',
                                     fallback=str(devices.DEFAULT_STABILIZE_TIME)))
        em7000s_coils_var.set(config.get('EM7000S', 'coils',
                                         fallback=str(devices.DEFAULT_EM7000S_COILS)))
        status_var.set("Config loaded successfully.")
    except Exception as e:
        status_var.set("Error reading config file.")
        print(f"Error loading config: {e}")

def save_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    if 'Experiment' not in config: config['Experiment'] = {}
    if 'Calibration' not in config: config['Calibration'] = {}
    if 'Magnet' not in config: config['Magnet'] = {}
    if 'Devices' not in config: config['Devices'] = {}
    if 'Settings' not in config: config['Settings'] = {}
    if 'EM7000S' not in config: config['EM7000S'] = {}
    try:
        config['Experiment']['low'] = exp_low_var.get()
        config['Experiment']['high'] = exp_high_var.get()
        config['Experiment']['step'] = exp_step_var.get()
        config['Experiment']['unit'] = exp_unit_var.get()
        config['Experiment']['sweep_down'] = str(exp_sweep_down_var.get())
        config['Calibration']['cal_res'] = cal_res_var.get()
        config['Magnet']['value'] = mag_val_var.get()
        config['Magnet']['unit'] = mag_unit_var.get()
        config['Devices']['magnet'] = dev_magnet_var.get()
        config['Devices']['vna'] = dev_vna_var.get()
        # An empty box must not reach the controllers as int('').
        config['Settings']['stabilize_time'] = stabilize_var.get() or str(devices.DEFAULT_STABILIZE_TIME)
        config['EM7000S']['coils'] = em7000s_coils_var.get() or str(devices.DEFAULT_EM7000S_COILS)
        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)
        # status_var.set("Parameters saved.") # Optional: don't overwrite "Running..." status
    except Exception as e:
        status_var.set("Error saving config!")
        print(f"Error saving config: {e}")

# --- Button Click Handlers ---

def on_detect_click():
    schedule_script(DETECT_SCRIPT)

def on_plot_click():
    if not (exp_low_var.get() and exp_high_var.get() and exp_step_var.get()):
        status_var.set("Error: All experiment fields must be filled.")
        return
    save_config()
    schedule_script(PLOTTER_SCRIPT)

def on_start_exp_click():
    if not (exp_low_var.get() and exp_high_var.get() and exp_step_var.get()):
        status_var.set("Error: All experiment fields must be filled.")
        return
    save_config()
    schedule_script(EXPERIMENT_SCRIPT)

def on_start_cal_click():
    if not cal_res_var.get():
        status_var.set("Error: Resolution field must be filled.")
        return
    save_config()
    schedule_script(CALIBRATION_SCRIPT)

def _validate_float(new_value):
    if new_value == "" or new_value == "-" or new_value == ".":
        return True
    try:
        float(new_value)
        return True
    except ValueError:
        return False

def _validate_int(new_value):
    """Whole seconds only; empty is allowed while the box is being edited."""
    return new_value == "" or new_value.isdigit()

def on_set_magnet_click():
    if not mag_val_var.get():
        status_var.set("Error: Magnet value must be filled.")
        return
    save_config()
    schedule_script(SET_MAGNET_SCRIPT)

def on_probe_magnet_click():
    mag_probe_var.set("Reading...")
    schedule_script(PROBE_MAGNET_SCRIPT)

def on_stop_magnet_click():
    schedule_script(STOP_MAGNET_SCRIPT)

def apply_magnet_capabilities():
    """
    Shows only what the selected magnet can actually do.

    Field mode turns a value in mT into a current through a calibration curve,
    so a magnet without a trustworthy curve can only be driven in Amps. For
    one of those, the mT choice is disabled, any mT already selected falls back
    to Amps, and the Calibration tab is hidden outright — running a calibration
    is how you would build the curve, and that needs control signals the magnet
    does not have yet.
    """
    supports_field = devices.magnet_supports_field(dev_magnet_var.get())

    radio_state = 'normal' if supports_field else 'disabled'
    exp_mt_radio.configure(state=radio_state)
    mag_mt_radio.configure(state=radio_state)

    # The coils selector belongs to the EM7000S alone.
    coils_combo.configure(
        state='readonly' if dev_magnet_var.get() == 'EM7000S' else 'disabled')

    if supports_field:
        tab_control.tab(tab_cal, state='normal')
        return

    # Nothing may be left pointing at mT once the option is gone.
    if 'mT' in (exp_unit_var.get(), mag_unit_var.get()):
        exp_unit_var.set('A')
        mag_unit_var.set('A')
        status_var.set(f"{dev_magnet_var.get()}: Amps only — unit reset to A.")
    tab_control.tab(tab_cal, state='hidden')

def on_device_change(event=None):
    """Persists the device selection immediately: the controllers read it from
    params.ini when they start, so it must be on disk before the next run."""
    # Reconcile the unit first: apply_magnet_capabilities may force it to A,
    # and that has to reach the file in this same save.
    apply_magnet_capabilities()
    save_config()
    magnet = dev_magnet_var.get()
    limits = "" if devices.magnet_supports_field(magnet) else " (Amps only)"
    status_var.set(f"Devices: {magnet}{limits} + {dev_vna_var.get()}")

def on_coils_change():
    """Coil count is read by the controllers at process start, like the device
    selection, so it goes to disk the moment it changes."""
    save_config()
    status_var.set(f"EM7000S: {em7000s_coils_var.get()} coil(s) energised.")

def on_stabilize_change(event=None):
    """Same deal for the settle time — write it out as soon as the box loses
    focus, so it is on disk whether or not a run is started from this tab."""
    if not stabilize_var.get():
        stabilize_var.set(str(devices.DEFAULT_STABILIZE_TIME))
    save_config()
    status_var.set(f"Stabilisation time: {stabilize_var.get()} s")

# --- Hover Help ---

class Tooltip:
    """
    Hover text for a widget. ttk has no tooltip, so this is a bare borderless
    Toplevel put next to the widget on <Enter> and destroyed on <Leave>.
    """
    def __init__(self, widget, text, delay_ms=350, wraplength=340):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._after_id = None
        self._window = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self._show_now, add='+')

    def _schedule(self, event=None):
        self._unschedule()
        self._after_id = self.widget.after(self.delay_ms, self.show)

    def _unschedule(self):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show_now(self, event=None):
        """Clicking the hint button shows it without waiting out the delay."""
        self._unschedule()
        self.show()

    def show(self):
        if self._window is not None:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._window = tk.Toplevel(self.widget)
        self._window.wm_overrideredirect(True)
        self._window.wm_geometry(f"+{x}+{y}")
        tk.Label(self._window, text=self.text, justify=tk.LEFT,
                 wraplength=self.wraplength, background="#ffffe0",
                 foreground="black", relief=tk.SOLID, borderwidth=1,
                 padx=6, pady=4).pack()

    def hide(self, event=None):
        self._unschedule()
        if self._window is not None:
            self._window.destroy()
            self._window = None

# --- Main Application Setup ---

root = tk.Tk()
root.title("Instrument Controller")

vcmd_float = (root.register(_validate_float), '%P')
vcmd_int = (root.register(_validate_int), '%P')

exp_low_var = tk.StringVar()
exp_high_var = tk.StringVar()
exp_step_var = tk.StringVar()
exp_unit_var = tk.StringVar(value='A')
exp_sweep_down_var = tk.IntVar(value=0)  # <-- ADD THIS LINE
cal_res_var = tk.StringVar()
status_var = tk.StringVar(value="Ready. Load config or enter values.")
mag_val_var = tk.StringVar()
mag_unit_var = tk.StringVar(value='A')
mag_probe_var = tk.StringVar(value='---')
dev_magnet_var = tk.StringVar(value=devices.DEFAULT_MAGNET)
dev_vna_var = tk.StringVar(value=devices.DEFAULT_VNA)
stabilize_var = tk.StringVar(value=str(devices.DEFAULT_STABILIZE_TIME))
em7000s_coils_var = tk.StringVar(value=str(devices.DEFAULT_EM7000S_COILS))

# --- Tabbed Interface ---
tab_control = ttk.Notebook(root)

# Experiment Tab
tab_exp = ttk.Frame(tab_control, padding=10)
tab_control.add(tab_exp, text='Experiment')

exp_inputs = ttk.Frame(tab_exp)
exp_inputs.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))
exp_buttons = ttk.Frame(tab_exp)
exp_buttons.pack(side=tk.RIGHT, fill=tk.Y)

# Inputs
ttk.Label(exp_inputs, text="Low:").grid(row=0, column=0, sticky='w', pady=5)
ttk.Entry(exp_inputs, textvariable=exp_low_var, validate='key', validatecommand=vcmd_float).grid(row=0, column=1, sticky='ew')

ttk.Label(exp_inputs, text="High:").grid(row=1, column=0, sticky='w', pady=5)
ttk.Entry(exp_inputs, textvariable=exp_high_var, validate='key', validatecommand=vcmd_float).grid(row=1, column=1, sticky='ew')

ttk.Label(exp_inputs, text="Step:").grid(row=2, column=0, sticky='w', pady=5)
ttk.Entry(exp_inputs, textvariable=exp_step_var, validate='key', validatecommand=vcmd_float).grid(row=2, column=1, sticky='ew')

radio_frame = ttk.Frame(exp_inputs)
radio_frame.grid(row=3, column=0, columnspan=2, pady=10)
ttk.Radiobutton(radio_frame, text="A", variable=exp_unit_var, value="A").pack(side=tk.LEFT, padx=5)
# Kept as a name: disabled for magnets with no field calibration.
exp_mt_radio = ttk.Radiobutton(radio_frame, text="mT", variable=exp_unit_var, value="mT")
exp_mt_radio.pack(side=tk.LEFT, padx=5)

ttk.Checkbutton(exp_inputs, text="Sweep down", variable=exp_sweep_down_var).grid(row=4, column=0, columnspan=2, sticky='w', pady=10)

# Buttons
ttk.Button(exp_buttons, text="Detect Insts!", command=on_detect_click).pack(fill=tk.X, pady=5)
ttk.Button(exp_buttons, text="Plot", command=on_plot_click).pack(fill=tk.X, pady=5)
ttk.Button(exp_buttons, text="START", command=on_start_exp_click, style='Accent.TButton').pack(fill=tk.X, pady=5)

# ABORT BUTTON (New)
# Placing it in the Experiment tab, but you might want a global one
ttk.Separator(exp_buttons, orient='horizontal').pack(fill='x', pady=10)
ttk.Button(exp_buttons, text="ABORT", command=on_abort_click, style='Danger.TButton').pack(fill=tk.X, pady=5)


# Calibration Tab
tab_cal = ttk.Frame(tab_control, padding=10)
tab_control.add(tab_cal, text='Calibration')

cal_inputs = ttk.Frame(tab_cal)
cal_inputs.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))
cal_buttons = ttk.Frame(tab_cal)
cal_buttons.pack(side=tk.RIGHT, fill=tk.Y)

ttk.Label(cal_inputs, text="Resolution:").grid(row=0, column=0, sticky='w', pady=5)
ttk.Entry(cal_inputs, textvariable=cal_res_var, validate='key', validatecommand=vcmd_float).grid(row=0, column=1, sticky='ew')

ttk.Button(cal_buttons, text="Detect Insts!", command=on_detect_click).pack(fill=tk.X, pady=5)
ttk.Button(cal_buttons, text="START CAL", command=on_start_cal_click, style='Accent.TButton').pack(fill=tk.X, pady=5)
# Calibration Abort Button
ttk.Separator(cal_buttons, orient='horizontal').pack(fill='x', pady=10)
ttk.Button(cal_buttons, text="ABORT", command=on_abort_click, style='Danger.TButton').pack(fill=tk.X, pady=5)

# Magnet Tab
tab_mag = ttk.Frame(tab_control, padding=10)
tab_control.add(tab_mag, text='Magnet')

mag_inputs = ttk.Frame(tab_mag)
mag_inputs.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))
mag_buttons = ttk.Frame(tab_mag)
mag_buttons.pack(side=tk.RIGHT, fill=tk.Y)

# Inputs
ttk.Label(mag_inputs, text="Value:").grid(row=0, column=0, sticky='w', pady=5)
ttk.Entry(mag_inputs, textvariable=mag_val_var, validate='key', validatecommand=vcmd_float).grid(row=0, column=1, sticky='ew')

mag_radio_frame = ttk.Frame(mag_inputs)
mag_radio_frame.grid(row=1, column=0, columnspan=2, pady=10)
ttk.Radiobutton(mag_radio_frame, text="A", variable=mag_unit_var, value="A").pack(side=tk.LEFT, padx=5)
mag_mt_radio = ttk.Radiobutton(mag_radio_frame, text="mT", variable=mag_unit_var, value="mT")
mag_mt_radio.pack(side=tk.LEFT, padx=5)

# Buttons
ttk.Button(mag_buttons, text="Set Field", command=on_set_magnet_click).pack(fill=tk.X, pady=5)
ttk.Button(mag_buttons, text="Probe Field", command=on_probe_magnet_click).pack(fill=tk.X, pady=5)

# Uneditable Text Box for Probe Result
ttk.Entry(mag_buttons, textvariable=mag_probe_var, state='readonly', justify='center').pack(fill=tk.X, pady=5)

ttk.Separator(mag_buttons, orient='horizontal').pack(fill='x', pady=10)
ttk.Button(mag_buttons, text="Stop Magnet", command=on_stop_magnet_click, style='Danger.TButton').pack(fill=tk.X, pady=5)

# Configuration Tab
tab_cfg = ttk.Frame(tab_control, padding=10)
tab_control.add(tab_cfg, text='Configuration')

cfg_inputs = ttk.Frame(tab_cfg)
cfg_inputs.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))
cfg_buttons = ttk.Frame(tab_cfg)
cfg_buttons.pack(side=tk.RIGHT, fill=tk.Y)

# Which drivers the controllers should load. The lists come from the registry in
# controllers/devices.py, so a new driver shows up here without touching the GUI.
ttk.Label(cfg_inputs, text="Electromagnet:").grid(row=0, column=0, sticky='w', pady=5)
mag_combo = ttk.Combobox(cfg_inputs, textvariable=dev_magnet_var,
                         values=list(devices.MAGNETS), state='readonly', width=18)
mag_combo.grid(row=0, column=1, sticky='ew', padx=(10, 0))
mag_combo.bind('<<ComboboxSelected>>', on_device_change)

ttk.Label(cfg_inputs, text="VNA:").grid(row=1, column=0, sticky='w', pady=5)
vna_combo = ttk.Combobox(cfg_inputs, textvariable=dev_vna_var,
                         values=list(devices.VNAS), state='readonly', width=18)
vna_combo.grid(row=1, column=1, sticky='ew', padx=(10, 0))
vna_combo.bind('<<ComboboxSelected>>', on_device_change)

ttk.Label(cfg_inputs, text="Stabilisation time (s):").grid(row=2, column=0, sticky='w', pady=5)
stabilize_entry = ttk.Entry(cfg_inputs, textvariable=stabilize_var, width=18,
                            validate='key', validatecommand=vcmd_int)
stabilize_entry.grid(row=2, column=1, sticky='ew', padx=(10, 0))
stabilize_entry.bind('<FocusOut>', on_stabilize_change)
stabilize_entry.bind('<Return>', on_stabilize_change)

# Hover help for the setting above.
stabilize_hint = ttk.Label(cfg_inputs, text=" ? ", relief='raised',
                           foreground='blue', cursor='question_arrow')
stabilize_hint.grid(row=2, column=2, sticky='w', padx=(6, 0))
Tooltip(stabilize_hint,
        "This setting is the time the system waits for the equipment to "
        "stabilise between successive readings for the same parameters of "
        "the experiment, which readings are then averaged to eliminate "
        "static noise.")

# EM7000S-specific: how many of its coils to energise. Greyed out while any
# other magnet is selected (apply_magnet_capabilities drives the state).
ttk.Label(cfg_inputs, text="EM7000S coils energised:").grid(row=3, column=0, sticky='w', pady=5)
coils_combo = ttk.Combobox(cfg_inputs, textvariable=em7000s_coils_var,
                           values=[str(n) for n in range(devices.EM7000S_COILS_MIN,
                                                         devices.EM7000S_COILS_MAX + 1)],
                           state='readonly', width=18)
coils_combo.grid(row=3, column=1, sticky='ew', padx=(10, 0))
coils_combo.bind('<<ComboboxSelected>>', lambda e: on_coils_change())

coils_hint = ttk.Label(cfg_inputs, text=" ? ", relief='raised',
                       foreground='blue', cursor='question_arrow')
coils_hint.grid(row=3, column=2, sticky='w', padx=(6, 0))
Tooltip(coils_hint,
        "How many of the EM7000S's four coils are energised. The field "
        "produced per ampere depends on this, so treat it as part of the "
        "rig: change it and any field calibration taken at a different "
        "coil count no longer applies.")

ttk.Label(cfg_inputs, text="Saved to params.ini; every controller\n"
                          "reads it when it starts.",
          justify='left', foreground='grey').grid(row=4, column=0, columnspan=3,
                                                  sticky='w', pady=(15, 0))

ttk.Button(cfg_buttons, text="Detect Insts!", command=on_detect_click).pack(fill=tk.X, pady=5)
ttk.Separator(cfg_buttons, orient='horizontal').pack(fill='x', pady=10)
ttk.Button(cfg_buttons, text="ABORT", command=on_abort_click, style='Danger.TButton').pack(fill=tk.X, pady=5)

# --- Style ---
style = ttk.Style()
style.configure('Accent.TButton', font=('Helvetica', 10, 'bold'), foreground='blue')
style.configure('Danger.TButton', font=('Helvetica', 10, 'bold'), foreground='red')

tab_control.pack(expand=1, fill='both')

status_bar = ttk.Label(root, textvariable=status_var, relief=tk.SUNKEN, anchor=tk.W, padding=5)
status_bar.pack(side=tk.BOTTOM, fill=tk.X)

# --- Start Up ---
load_config()
# After load_config: the saved magnet decides whether field mode and the
# Calibration tab are available at all.
apply_magnet_capabilities()

# 1. Start the asyncio loop in a separate thread
t = threading.Thread(target=start_background_loop, args=(loop,), daemon=True)
t.start()

# 2. Start the Queue checker (this bridges the threads)
check_queue()

root.mainloop()
