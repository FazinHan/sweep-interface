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
DEGAUSS_SCRIPT = os.path.join('controllers', 'degauss.py')

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

async def run_script_async(script_name, *args):
    """
    Async coroutine to run the subprocess.
    It reads output line-by-line and pushes it to the GUI queue.

    Extra positional args are passed through to the script, which is how the
    Plotter window selects which figure to draw.
    """
    global current_process, running, abort_requested

    abort_requested = False
    shown = " ".join([script_name, *args])
    msg_queue.put(("status", f"Running {shown}..."))
    msg_queue.put(("print", f"Starting subprocess: python {shown}"))

    try:
        # Create the subprocess asynchronously
        # We use sys.executable to ensure we use the same python interpreter.
        # -u (plus PYTHONUNBUFFERED for any grandchildren) is what makes the
        # logs live: a piped stdout is block-buffered by default, so without
        # it the controller's prints only arrive once the process exits.
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        current_process = await asyncio.create_subprocess_exec(
            sys.executable, "-u", script_name, *args,
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

def schedule_script(script_name, *args):
    """Helper to schedule the async task from the synchronous GUI."""
    global running
    if running:
        status_var.set("Error: A process is already running!")
        return

    running = True
    # Schedule the coroutine in the background loop
    asyncio.run_coroutine_threadsafe(run_script_async(script_name, *args), loop)


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
        dvm_enabled_var.set(int(config.get('DVM', 'enabled', fallback='0')))
        dvm_model_var.set(config.get('DVM', 'model', fallback=devices.DEFAULT_DVM))
        dvm_channel_var.set(config.get('DVM', 'channel',
                                       fallback=str(devices.DEFAULT_DVM_CHANNEL)))
        dvm_nplc_var.set(config.get('DVM', 'nplc',
                                    fallback=str(devices.DEFAULT_DVM_NPLC)))
        # Plotter's own target sweep. No Experiment fallback: an empty box is
        # the honest state before the user has said what to plot.
        plot_low_var.set(config.get('Plotter', 'low', fallback=''))
        plot_high_var.set(config.get('Plotter', 'high', fallback=''))
        plot_step_var.set(config.get('Plotter', 'step', fallback=''))
        plot_unit_var.set(config.get('Plotter', 'unit', fallback='A'))
        plot_sweep_down_var.set(int(config.get('Plotter', 'sweep_down',
                                               fallback='0')))
        plot_shape_var.set(config.get('Plotter', 'fit_shape', fallback=''))
        plot_npeaks_var.set(config.get('Plotter', 'n_peaks', fallback=''))
        plot_ntraces_var.set(config.get('Plotter', 'n_traces', fallback=''))
        plot_sparam_var.set(config.get('Plotter', 's_param', fallback='S21'))
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
    if 'DVM' not in config: config['DVM'] = {}
    if 'Plotter' not in config: config['Plotter'] = {}
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
        config['DVM']['enabled'] = str(dvm_enabled_var.get())
        config['DVM']['model'] = dvm_model_var.get()
        config['DVM']['channel'] = dvm_channel_var.get() or str(devices.DEFAULT_DVM_CHANNEL)
        config['DVM']['nplc'] = dvm_nplc_var.get() or str(devices.DEFAULT_DVM_NPLC)
        config['Plotter']['low'] = plot_low_var.get()
        config['Plotter']['high'] = plot_high_var.get()
        config['Plotter']['step'] = plot_step_var.get()
        config['Plotter']['unit'] = plot_unit_var.get()
        config['Plotter']['sweep_down'] = str(plot_sweep_down_var.get())
        config['Plotter']['fit_shape'] = plot_shape_var.get()
        config['Plotter']['n_peaks'] = plot_npeaks_var.get() or '1'
        config['Plotter']['n_traces'] = plot_ntraces_var.get() or '3'
        config['Plotter']['s_param'] = plot_sparam_var.get()
        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)
        # status_var.set("Parameters saved.") # Optional: don't overwrite "Running..." status
    except Exception as e:
        status_var.set("Error saving config!")
        print(f"Error saving config: {e}")

# --- Button Click Handlers ---

def on_detect_click():
    schedule_script(DETECT_SCRIPT)

def open_plotter_window():
    """
    Opens the Plotter window, its sweep boxes seeded from the Experiment tab.

    Seeded, not linked: the values are copied in each time the window opens,
    and are then the Plotter's own to edit. That covers the common case -- you
    have just run a sweep and want to look at it -- without the window
    silently tracking the Experiment tab afterwards, which would mean setting
    up the next measurement repointed the Plotter mid-session at data that may
    not exist.

    The consequence to know about: editing these boxes to look at an older run
    is not remembered across a close-and-reopen, because reopening seeds them
    again. The fit settings below are not seeded and do persist.
    """
    plot_low_var.set(exp_low_var.get())
    plot_high_var.set(exp_high_var.get())
    plot_step_var.set(exp_step_var.get())
    plot_unit_var.set(exp_unit_var.get())
    plot_sweep_down_var.set(exp_sweep_down_var.get())

    window = tk.Toplevel(root)
    window.title("Plotter")
    window.transient(root)

    frame = ttk.Frame(window, padding=12)
    frame.pack(fill=tk.BOTH, expand=True)

    # --- which data ---
    ttk.Label(frame, text="Data to plot", font=('Helvetica', 9, 'bold')).grid(
        row=0, column=0, columnspan=3, sticky='w')
    ttk.Label(frame, text="The sweep that names the data folder. Copied from\n"
                          "the Experiment tab; edit to plot a different run.",
              foreground='grey', justify='left').grid(row=1, column=0,
                                                      columnspan=3, sticky='w',
                                                      pady=(0, 6))

    ttk.Label(frame, text="Low:").grid(row=2, column=0, sticky='w', pady=3)
    ttk.Entry(frame, textvariable=plot_low_var, width=12, validate='key',
              validatecommand=vcmd_float).grid(row=2, column=1, sticky='w')

    ttk.Label(frame, text="High:").grid(row=3, column=0, sticky='w', pady=3)
    ttk.Entry(frame, textvariable=plot_high_var, width=12, validate='key',
              validatecommand=vcmd_float).grid(row=3, column=1, sticky='w')

    ttk.Label(frame, text="Step:").grid(row=4, column=0, sticky='w', pady=3)
    ttk.Entry(frame, textvariable=plot_step_var, width=12, validate='key',
              validatecommand=vcmd_float).grid(row=4, column=1, sticky='w')

    plot_unit_frame = ttk.Frame(frame)
    plot_unit_frame.grid(row=5, column=0, columnspan=3, sticky='w', pady=4)
    ttk.Radiobutton(plot_unit_frame, text="A", variable=plot_unit_var,
                    value="A").pack(side=tk.LEFT, padx=(0, 8))
    ttk.Radiobutton(plot_unit_frame, text="mT", variable=plot_unit_var,
                    value="mT").pack(side=tk.LEFT)
    ttk.Checkbutton(plot_unit_frame, text="Sweep down",
                    variable=plot_sweep_down_var).pack(side=tk.LEFT, padx=(16, 0))

    ttk.Separator(frame, orient='horizontal').grid(row=6, column=0, columnspan=3,
                                                   sticky='ew', pady=10)

    # --- peak detection ---
    ttk.Label(frame, text="Peak detection", font=('Helvetica', 9, 'bold')).grid(
        row=7, column=0, columnspan=3, sticky='w', pady=(0, 4))

    ttk.Label(frame, text="Fit shape:").grid(row=8, column=0, sticky='w', pady=4)
    shape_frame = ttk.Frame(frame)
    shape_frame.grid(row=8, column=1, columnspan=2, sticky='w')
    ttk.Radiobutton(shape_frame, text="Lorentzian", value='lorentzian',
                    variable=plot_shape_var,
                    command=_refresh_plot_buttons).pack(side=tk.LEFT, padx=(0, 8))
    ttk.Radiobutton(shape_frame, text="Gaussian", value='gaussian',
                    variable=plot_shape_var,
                    command=_refresh_plot_buttons).pack(side=tk.LEFT)

    ttk.Label(frame, text="Peaks per trace (N):").grid(row=9, column=0,
                                                       sticky='w', pady=4)
    ttk.Entry(frame, textvariable=plot_npeaks_var, width=10, validate='key',
              validatecommand=vcmd_int).grid(row=9, column=1, sticky='w')

    ttk.Label(frame, text="Traces to show:").grid(row=10, column=0,
                                                  sticky='w', pady=4)
    ttk.Entry(frame, textvariable=plot_ntraces_var, width=10, validate='key',
              validatecommand=vcmd_int).grid(row=10, column=1, sticky='w')

    ttk.Label(frame, text="S-parameter:").grid(row=11, column=0, sticky='w', pady=4)
    ttk.Combobox(frame, textvariable=plot_sparam_var,
                 values=['S11', 'S12', 'S21', 'S22'], state='readonly',
                 width=8).grid(row=11, column=1, sticky='w')

    ttk.Separator(frame, orient='horizontal').grid(row=12, column=0, columnspan=3,
                                                   sticky='ew', pady=10)

    # --- plot buttons ---
    global plot_full_button, plot_pvh_button, plot_dpdh_button
    plot_full_button = ttk.Button(frame, text="Full spectrum",
                                  command=lambda: _run_plot('full'))
    plot_full_button.grid(row=13, column=0, sticky='ew', padx=2)
    plot_pvh_button = ttk.Button(frame, text="P vs H",
                                 command=lambda: _run_plot('pvh'))
    plot_pvh_button.grid(row=13, column=1, sticky='ew', padx=2)
    plot_dpdh_button = ttk.Button(frame, text="dP/dH vs H",
                                  command=lambda: _run_plot('dpdh'))
    plot_dpdh_button.grid(row=13, column=2, sticky='ew', padx=2)

    global plot_hint_label
    plot_hint_label = ttk.Label(frame, text="", foreground='grey',
                                wraplength=400, justify='left')
    plot_hint_label.grid(row=14, column=0, columnspan=3, sticky='w', pady=(10, 0))

    # Re-check whenever a field changes, so the buttons track validity live.
    for var in (plot_low_var, plot_high_var, plot_step_var,
                plot_npeaks_var, plot_ntraces_var):
        var.trace_add('write', lambda *_: _refresh_plot_buttons())
    window.protocol("WM_DELETE_WINDOW",
                    lambda: (_forget_plot_buttons(), window.destroy()))
    _refresh_plot_buttons()


def _plot_target_problem():
    """
    Why no plot can be drawn at all, or None when the target is complete.

    The sweep values name the data directory, so without them there is
    nothing to read -- this gates every button, including Full spectrum.
    """
    for label, var in (("Low", plot_low_var), ("High", plot_high_var),
                       ("Step", plot_step_var)):
        text = var.get().strip()
        if not text or text in ('-', '.'):
            return f"{label} is required to identify the data."
        try:
            float(text)
        except ValueError:
            return f"{label} must be a number."
    if float(plot_step_var.get()) == 0:
        return "Step cannot be zero."
    return None


def _plot_settings_problem():
    """Why the peak-based buttons are unusable, or None when they are fine."""
    if plot_shape_var.get() not in ('lorentzian', 'gaussian'):
        return "Choose a fit shape."
    for label, var in (("Peaks per trace (N)", plot_npeaks_var),
                       ("Traces to show", plot_ntraces_var)):
        text = var.get().strip()
        if not text:
            return f"{label} is required."
        if not text.isdigit() or int(text) < 1:
            return f"{label} must be a whole number of at least 1."
    return None


def _refresh_plot_buttons():
    """
    Tracks the two tiers of requirement.

    Every button needs the target sweep, since that is what locates the data.
    Only the peak-based buttons additionally need the fit settings -- 'Full
    spectrum' consumes none of those, so gating it behind a peak count it
    never reads would block a working feature.
    """
    if plot_pvh_button is None:
        return

    target = _plot_target_problem()
    settings = _plot_settings_problem()

    plot_full_button.configure(state='disabled' if target else 'normal')
    peak_state = 'disabled' if (target or settings) else 'normal'
    plot_pvh_button.configure(state=peak_state)
    plot_dpdh_button.configure(state=peak_state)

    if plot_hint_label is not None:
        plot_hint_label.configure(
            text=target or settings or
            "Peak positions appear in the plot legend.")


def _forget_plot_buttons():
    global plot_full_button, plot_pvh_button, plot_dpdh_button, plot_hint_label
    plot_full_button = plot_pvh_button = plot_dpdh_button = None
    plot_hint_label = None


def _run_plot(mode):
    """Saves the Plotter settings, then spawns the plotter in that mode."""
    problem = _plot_target_problem()
    if not problem and mode in ('pvh', 'dpdh'):
        problem = _plot_settings_problem()
    if problem:
        status_var.set(f"Error: {problem}")
        return
    save_config()
    schedule_script(PLOTTER_SCRIPT, mode)

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

def on_degauss_click():
    """
    Runs the mild degauss. Offered on both the Experiment and Magnet tabs
    because it is wanted in two different moments: before starting a series,
    and after a manual set has left the core somewhere awkward.
    """
    save_config()
    schedule_script(DEGAUSS_SCRIPT)

def on_full_degauss_click():
    """
    Runs the degauss from the selected magnet's full current limit.

    Kept on the Configuration tab, away from the per-run controls, because it
    is the occasional deep clean rather than routine housekeeping: it drives
    the magnet to full field in both directions.
    """
    save_config()
    schedule_script(DEGAUSS_SCRIPT, 'full')

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

def on_dvm_toggle():
    """
    The voltmeter checkbox is the single source of truth for whether a DVM is
    in play, so the Configuration tab's DVM controls follow it.
    """
    apply_dvm_state()
    save_config()
    status_var.set("Nanovoltmeter enabled." if dvm_enabled_var.get()
                   else "Nanovoltmeter disabled; runs will be VNA-only.")


def apply_dvm_state():
    """Greys out the DVM settings when no voltmeter is declared."""
    if dvm_model_combo is None:
        return
    enabled = bool(dvm_enabled_var.get())
    dvm_model_combo.configure(state='readonly' if enabled else 'disabled')
    dvm_channel_combo.configure(state='readonly' if enabled else 'disabled')
    dvm_nplc_entry.configure(state='normal' if enabled else 'disabled')


def on_dvm_setting_change(event=None):
    save_config()
    status_var.set(f"Nanovoltmeter: channel {dvm_channel_var.get()}, "
                   f"{dvm_nplc_var.get()} NPLC")


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
dvm_enabled_var = tk.IntVar(value=0)
dvm_model_var = tk.StringVar(value=devices.DEFAULT_DVM)
dvm_channel_var = tk.StringVar(value=str(devices.DEFAULT_DVM_CHANNEL))
dvm_nplc_var = tk.StringVar(value=str(devices.DEFAULT_DVM_NPLC))

# Plotter window state. Held here rather than inside the window so a reopened
# window comes back with the same settings, and so save_config can reach them.
#
# The sweep that identifies the data is the Plotter's own, deliberately not
# read from the Experiment tab. Those boxes describe the run you are about to
# take; plotting is usually about a run you took earlier, and quietly following
# the Experiment tab meant editing it for the next measurement silently
# repointed the Plotter at data that may not exist.
plot_low_var = tk.StringVar(value='')
plot_high_var = tk.StringVar(value='')
plot_step_var = tk.StringVar(value='')
plot_unit_var = tk.StringVar(value='A')
plot_sweep_down_var = tk.IntVar(value=0)
plot_shape_var = tk.StringVar(value='')      # empty until the user chooses
plot_npeaks_var = tk.StringVar(value='')
plot_ntraces_var = tk.StringVar(value='')
plot_sparam_var = tk.StringVar(value='S21')
plot_full_button = None
plot_pvh_button = None
plot_dpdh_button = None
plot_hint_label = None

# Assigned when the Configuration tab is built, below.
dvm_model_combo = None
dvm_channel_combo = None
dvm_nplc_entry = None

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

# Per-run fact about how the rig is wired, which is why it lives here rather
# than in Configuration. It gates detection, the sweep, and the plotter.
ttk.Checkbutton(exp_inputs, text="Nanovoltmeter connected",
                variable=dvm_enabled_var,
                command=lambda: on_dvm_toggle()).grid(row=5, column=0,
                                                      columnspan=2, sticky='w',
                                                      pady=(0, 10))

# Buttons
ttk.Button(exp_buttons, text="Detect Insts!", command=on_detect_click).pack(fill=tk.X, pady=5)
ttk.Button(exp_buttons, text="Plotter", command=open_plotter_window).pack(fill=tk.X, pady=5)
ttk.Button(exp_buttons, text="Degauss", command=on_degauss_click).pack(fill=tk.X, pady=5)
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
ttk.Button(mag_buttons, text="Degauss", command=on_degauss_click).pack(fill=tk.X, pady=5)
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

ttk.Separator(cfg_inputs, orient='horizontal').grid(row=4, column=0, columnspan=3,
                                                    sticky='ew', pady=10)

# Nanovoltmeter settings. Enabled by the Experiment tab's checkbox, which is
# the one place the user declares whether a voltmeter is wired in at all.
ttk.Label(cfg_inputs, text="Nanovoltmeter:").grid(row=5, column=0, sticky='w', pady=5)
dvm_model_combo = ttk.Combobox(cfg_inputs, textvariable=dvm_model_var,
                               values=list(devices.DVMS), state='readonly',
                               width=18)
dvm_model_combo.grid(row=5, column=1, sticky='ew', padx=(10, 0))
dvm_model_combo.bind('<<ComboboxSelected>>', on_dvm_setting_change)

ttk.Label(cfg_inputs, text="DVM channel:").grid(row=6, column=0, sticky='w', pady=5)
dvm_channel_combo = ttk.Combobox(cfg_inputs, textvariable=dvm_channel_var,
                                 values=['1', '2'], state='readonly', width=18)
dvm_channel_combo.grid(row=6, column=1, sticky='ew', padx=(10, 0))
dvm_channel_combo.bind('<<ComboboxSelected>>', on_dvm_setting_change)

ttk.Label(cfg_inputs, text="DVM NPLC:").grid(row=7, column=0, sticky='w', pady=5)
dvm_nplc_entry = ttk.Entry(cfg_inputs, textvariable=dvm_nplc_var, width=18,
                           validate='key', validatecommand=vcmd_float)
dvm_nplc_entry.grid(row=7, column=1, sticky='ew', padx=(10, 0))
dvm_nplc_entry.bind('<FocusOut>', on_dvm_setting_change)
dvm_nplc_entry.bind('<Return>', on_dvm_setting_change)

dvm_hint = ttk.Label(cfg_inputs, text=" ? ", relief='raised',
                     foreground='blue', cursor='question_arrow')
dvm_hint.grid(row=7, column=2, sticky='w', padx=(6, 0))
Tooltip(dvm_hint,
        "Channel is which LEMO input the sample is wired into. NPLC is the "
        "integration time in power line cycles - higher is slower and "
        "quieter. A sweep is gated by the magnet settle time anyway, so "
        "there is rarely a reason to rush it. Enable the voltmeter with the "
        "checkbox on the Experiment tab.")

ttk.Label(cfg_inputs, text="Saved to params.ini; every controller\n"
                          "reads it when it starts.",
          justify='left', foreground='grey').grid(row=8, column=0, columnspan=3,
                                                  sticky='w', pady=(15, 0))

ttk.Button(cfg_buttons, text="Detect Insts!", command=on_detect_click).pack(fill=tk.X, pady=5)

# Full-strength degauss, with its warning attached. The button and its hint
# share a row so the '?' cannot drift away from what it is warning about.
full_degauss_row = ttk.Frame(cfg_buttons)
full_degauss_row.pack(fill=tk.X, pady=5)
ttk.Button(full_degauss_row, text="Degauss (full strength)",
           command=on_full_degauss_click).pack(side=tk.LEFT, fill=tk.X, expand=True)
full_degauss_hint = ttk.Label(full_degauss_row, text=" ? ", relief='raised',
                              foreground='blue', cursor='question_arrow')
full_degauss_hint.pack(side=tk.LEFT, padx=(6, 0))
Tooltip(full_degauss_hint,
        "KEEP MAGNETIC MATERIAL AWAY FROM THE MAGNET BEFORE STARTING.\n\n"
        "Tools, screwdrivers, watches, phones, bank cards and magnetic "
        "storage media should all be clear of the bore and the surrounding "
        "bench: this drives the core to its full field in both directions, "
        "repeatedly.\n\n"
        "Starts from the selected magnet's own current limit (4.0 A on the "
        "EM3000S, 4.2 A on the EM7000S) rather than the mild 1 A pass behind "
        "the Degauss buttons on the Experiment and Magnet tabs. Use it after "
        "a run that drove the magnet hard, since a mild pass cannot undo "
        "remanence left by larger loops than it retraces.")

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
# Calibration tab are available at all, and the saved voltmeter flag decides
# whether the DVM settings are reachable.
apply_magnet_capabilities()
apply_dvm_state()

# 1. Start the asyncio loop in a separate thread
t = threading.Thread(target=start_background_loop, args=(loop,), daemon=True)
t.start()

# 2. Start the Queue checker (this bridges the threads)
check_queue()

root.mainloop()
