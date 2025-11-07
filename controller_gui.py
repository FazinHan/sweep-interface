import tkinter as tk
from tkinter import ttk
import subprocess
import configparser
import os

# --- Configuration ---
CONFIG_FILE = 'params.ini'
DETECT_SCRIPT = os.path.join('dev','detect.py')
EXPERIMENT_SCRIPT = os.path.join('dev','experiment.py')
CALIBRATION_SCRIPT = os.path.join('dev','calibration.py')

# --- Backend Functions ---

def run_script(script_name):
    """Runs a Python script using subprocess and updates the status bar."""
    status_var.set(f"Running {script_name}...")
    
    try:
        print(f"Starting subprocess: python {script_name}")
        process = subprocess.Popen(['python', script_name], 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE, 
                                   text=True)
        
        # Wait for the process to complete, get output
        # This will block the GUI, but only for the duration of the script.
        # A more complex app would use threading.
        stdout, stderr = process.communicate(timeout=30) # 30-second timeout
        
        # --- Print output to console ---
        print("--- Script Output ---")
        if stdout:
            print(f"stdout: {stdout.strip()}")
        if stderr:
            # This is key: errors are printed to the console
            print(f"stderr: {stderr.strip()}")
        print("---------------------")

        # --- Check for errors ---
        if process.returncode != 0:
            # The script failed!
            print(f"Error: {script_name} exited with code {process.returncode}")
            status_var.set(f"{script_name} errored, see console for more details")
        else:
            # Success
            status_var.set(f"{script_name} finished.")

    except subprocess.TimeoutExpired:
        process.kill() # Stop the process if it times out
        print(f"Error: {script_name} timed out after 30 seconds.")
        status_var.set(f"Error: {script_name} timed out!")
        
    except FileNotFoundError:
        print(f"Error: Script '{script_name}' not found.")
        status_var.set(f"Error: {script_name} not found!")

    except Exception as e:
        # Catch other unexpected errors
        status_var.set(f"Error running {script_name}!")
        print(f"An unexpected error occurred: {e}")

def load_config():
    """Loads values from params.ini into the GUI's variables."""
    if not os.path.exists(CONFIG_FILE):
        status_var.set("Error: params.ini not found!")
        return
        
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    
    try:
        # Load Experiment tab values
        exp_low_var.set(config.get('Experiment', 'low', fallback='0'))
        exp_high_var.set(config.get('Experiment', 'high', fallback='1'))
        exp_step_var.set(config.get('Experiment', 'step', fallback='0.1'))
        exp_unit_var.set(config.get('Experiment', 'unit', fallback='A'))
        
        # Load Calibration tab values
        cal_res_var.set(config.get('Calibration', 'cal_res', fallback='800'))
        
        status_var.set("Config loaded successfully.")
    except Exception as e:
        status_var.set("Error reading config file.")
        print(f"Error loading config: {e}")

def save_config():
    """Saves the current values from the GUI back to params.ini."""
    config = configparser.ConfigParser()
    # Read existing file to not overwrite other sections/keys
    config.read(CONFIG_FILE)

    # Ensure sections exist
    if 'Experiment' not in config:
        config['Experiment'] = {}
    if 'Calibration' not in config:
        config['Calibration'] = {}

    try:
        # Save Experiment tab values
        config['Experiment']['current_low'] = exp_low_var.get()
        config['Experiment']['current_high'] = exp_high_var.get()
        config['Experiment']['step'] = exp_step_var.get()
        config['Experiment']['unit'] = exp_unit_var.get()
        
        # Save Calibration tab values
        config['Calibration']['cal_res'] = cal_res_var.get()
        
        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)
            
        status_var.set("Parameters saved.")
    except Exception as e:
        status_var.set("Error saving config!")
        print(f"Error saving config: {e}")


# --- Button Click Handlers ---

def on_detect_click():
    """Handler for the 'Detect Insts!' button."""
    # This function is re-used by both buttons
    run_script(DETECT_SCRIPT)

def on_start_exp_click():
    """Saves config and runs the experiment script."""
    save_config()
    run_script(EXPERIMENT_SCRIPT)

def on_start_cal_click():
    """Saves config and runs the calibration script."""
    save_config()
    run_script(CALIBRATION_SCRIPT)


# --- Main Application Setup ---

# Create the main window
root = tk.Tk()
root.title("Instrument Controller")

# Create string variables to hold the values from our widgets
exp_low_var = tk.StringVar()
exp_high_var = tk.StringVar()
exp_step_var = tk.StringVar()
exp_unit_var = tk.StringVar(value='A') # Default value
cal_res_var = tk.StringVar()
status_var = tk.StringVar(value="Ready. Load config or enter values.")

# --- Create the Tabbed Interface ---
tab_control = ttk.Notebook(root)

# -- Experiment Tab --
tab_exp = ttk.Frame(tab_control, padding=10)
tab_control.add(tab_exp, text='Experiment')

exp_inputs_frame = ttk.Frame(tab_exp)
exp_inputs_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))

exp_buttons_frame = ttk.Frame(tab_exp)
exp_buttons_frame.pack(side=tk.RIGHT, fill=tk.Y)

# Input fields
ttk.Label(exp_inputs_frame, text="Low:").grid(row=0, column=0, sticky='w', pady=5)
ttk.Entry(exp_inputs_frame, textvariable=exp_low_var).grid(row=0, column=1, sticky='ew')

ttk.Label(exp_inputs_frame, text="High:").grid(row=1, column=0, sticky='w', pady=5)
ttk.Entry(exp_inputs_frame, textvariable=exp_high_var).grid(row=1, column=1, sticky='ew')

ttk.Label(exp_inputs_frame, text="Step:").grid(row=2, column=0, sticky='w', pady=5)
ttk.Entry(exp_inputs_frame, textvariable=exp_step_var).grid(row=2, column=1, sticky='ew')

# Radio buttons
radio_frame = ttk.Frame(exp_inputs_frame)
radio_frame.grid(row=3, column=0, columnspan=2, pady=10)
ttk.Radiobutton(radio_frame, text="A", variable=exp_unit_var, value="A").pack(side=tk.LEFT, padx=5)
ttk.Radiobutton(radio_frame, text="mT", variable=exp_unit_var, value="mT").pack(side=tk.LEFT, padx=5)

# Buttons
ttk.Button(exp_buttons_frame, text="Detect Insts!", command=on_detect_click).pack(fill=tk.X, pady=5)
ttk.Button(exp_buttons_frame, text="START Exp", command=on_start_exp_click, style='Accent.TButton').pack(fill=tk.X, pady=5)

# -- Calibration Tab --
tab_cal = ttk.Frame(tab_control, padding=10)
tab_control.add(tab_cal, text='Calibration')

cal_inputs_frame = ttk.Frame(tab_cal)
cal_inputs_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))

cal_buttons_frame = ttk.Frame(tab_cal)
cal_buttons_frame.pack(side=tk.RIGHT, fill=tk.Y)

# Input fields
ttk.Label(cal_inputs_frame, text="Resolution:").grid(row=0, column=0, sticky='w', pady=5)
ttk.Entry(cal_inputs_frame, textvariable=cal_res_var).grid(row=0, column=1, sticky='ew')

# Buttons
ttk.Button(cal_buttons_frame, text="Detect Insts!", command=on_detect_click).pack(fill=tk.X, pady=5)
ttk.Button(cal_buttons_frame, text="START CAL", command=on_start_cal_click, style='Accent.TButton').pack(fill=tk.X, pady=5)

# --- Style and Status Bar ---

# Add a style for the "START" buttons to make them stand out
style = ttk.Style()
style.configure('Accent.TButton', font=('Helvetica', 10, 'bold'), foreground='blue')

# Add the main tab control to the window
tab_control.pack(expand=1, fill='both')

# Add status bar
status_bar = ttk.Label(root, textvariable=status_var, relief=tk.SUNKEN, anchor=tk.W, padding=5)
status_bar.pack(side=tk.BOTTOM, fill=tk.X)

# --- Load initial data and run ---
load_config()
root.mainloop()