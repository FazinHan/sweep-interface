import time
import configparser

# Read the latest params saved by the GUI
config = configparser.ConfigParser()
config.read('params.ini')

low = config.getfloat('Experiment', 'current_low')
high = config.getfloat('Experiment', 'current_high')
step = config.getfloat('Experiment', 'step')
unit = config.get('Experiment', 'unit')
raise ValueError("Simulated error for demonstration purposes.")

print(f"--- STARTING EXPERIMENT ---")
print(f"Running from {low} {unit} to {high} {unit} in {step} {unit} steps.")
time.sleep(3) # Simulate a long experiment
print("--- EXPERIMENT FINISHED ---")