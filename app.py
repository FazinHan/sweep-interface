import tkinter as tk
from tkinter import ttk
import asyncio
import threading
import queue
import configparser
import os
import sys

# --- Configuration ---
CONFIG_FILE = 'params.ini'
DETECT_SCRIPT = os.path.join('controllers', 'detect.py')
EXPERIMENT_SCRIPT = os.path.join('controllers', 'experiment.py')
CALIBRATION_SCRIPT = os.path.join('controllers', 'calibration.py')
PLOTTER_SCRIPT = os.path.join('controllers', 'plotter.py')
ABORT_SCRIPT = os.path.join('controllers', 'abort_all.py')

# --- Global State for Async Control ---
# We need a reference to the current process to kill it later
current_process = None 
# A queue to pass messages from the background thread to the GUI
msg_queue = queue.Queue()
# The asyncio loop that will run in a background thread
loop = asyncio.new_event_loop()


# --- Async Backend Functions ---

def start_background_loop(loop):
    """Runs the asyncio event loop in a separate thread."""
    asyncio.set_event_loop(loop)
    loop.run_forever()

async def run_script_async(script_name):
    """
    Async coroutine to run the subprocess. 
    It reads output line-by-line and pushes it to the GUI queue.
    """
    global current_process
    
    msg_queue.put(("status", f"Running {script_name}..."))
    msg_queue.put(("print", f"Starting subprocess: python {script_name}"))

    try:
        # Create the subprocess asynchronously
        # We use sys.executable to ensure we use the same python interpreter
        current_process = await asyncio.create_subprocess_exec(
            sys.executable, script_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Read stdout line by line
        async for line in current_process.stdout:
            decoded_line = line.decode().strip()
            if decoded_line:
                msg_queue.put(("print", decoded_line))

        # Wait for the process to exit
        await current_process.wait()
        
        # Check for errors in stderr
        stderr_data = await current_process.stderr.read()
        if stderr_data:
            msg_queue.put(("print", f"stderr: {stderr_data.decode().strip()}"))

        rc = current_process.returncode
        if rc != 0:
            # -15 usually means terminated by signal (our Abort button)
            if rc == -15: 
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

def schedule_script(script_name):
    """Helper to schedule the async task from the synchronous GUI."""
    if current_process is not None:
        status_var.set("Error: A process is already running!")
        return
    
    # Schedule the coroutine in the background loop
    asyncio.run_coroutine_threadsafe(run_script_async(script_name), loop)


# --- Abort Functionality ---

def on_abort_click():
    """Kills the running subprocess if it exists."""
    global current_process
    if current_process:
        try:
            current_process.terminate() # or .kill() if it's stubborn
            status_var.set("Aborting...")
            print("Sending terminate signal...")
            schedule_script(ABORT_SCRIPT)
        except ProcessLookupError:
            status_var.set("Process already ended.")
    else:
        status_var.set("No process is running.")

# --- GUI Update Loop ---

def check_queue():
    """
    Periodically checks the queue for messages from the background thread
    and updates the GUI. This runs on the Main Thread.
    """
    try:
        while True:
            # Get messages without blocking
            msg_type, content = msg_queue.get_nowait()
            
            if msg_type == "status":
                status_var.set(content)
            elif msg_type == "print":
                print(content) # Or print to a Text widget if you add one later
                
    except queue.Empty:
        pass
    
    # Schedule this function to run again in 100ms
    root.after(100, check_queue)


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
        cal_res_var.set(config.get('Calibration', 'cal_res', fallback='800'))
        status_var.set("Config loaded successfully.")
    except Exception as e:
        status_var.set("Error reading config file.")
        print(f"Error loading config: {e}")

def save_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    if 'Experiment' not in config: config['Experiment'] = {}
    if 'Calibration' not in config: config['Calibration'] = {}
    try:
        config['Experiment']['low'] = exp_low_var.get()
        config['Experiment']['high'] = exp_high_var.get()
        config['Experiment']['step'] = exp_step_var.get()
        config['Experiment']['unit'] = exp_unit_var.get()
        config['Calibration']['cal_res'] = cal_res_var.get()
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

# --- Main Application Setup ---

root = tk.Tk()
root.title("Instrument Controller")

vcmd_float = (root.register(_validate_float), '%P')

exp_low_var = tk.StringVar()
exp_high_var = tk.StringVar()
exp_step_var = tk.StringVar()
exp_unit_var = tk.StringVar(value='A')
cal_res_var = tk.StringVar()
status_var = tk.StringVar(value="Ready. Load config or enter values.")

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
ttk.Radiobutton(radio_frame, text="mT", variable=exp_unit_var, value="mT").pack(side=tk.LEFT, padx=5)

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


# --- Style ---
style = ttk.Style()
style.configure('Accent.TButton', font=('Helvetica', 10, 'bold'), foreground='blue')
style.configure('Danger.TButton', font=('Helvetica', 10, 'bold'), foreground='red')

tab_control.pack(expand=1, fill='both')

status_bar = ttk.Label(root, textvariable=status_var, relief=tk.SUNKEN, anchor=tk.W, padding=5)
status_bar.pack(side=tk.BOTTOM, fill=tk.X)

# --- Start Up ---
load_config()

# 1. Start the asyncio loop in a separate thread
t = threading.Thread(target=start_background_loop, args=(loop,), daemon=True)
t.start()

# 2. Start the Queue checker (this bridges the threads)
check_queue()

root.mainloop()