import sys
import time

print("Detecting instruments...")
time.sleep(1)
print("Oh no, something went wrong!")
print("Could not find Keithley 2400.")

# Exit with a non-zero status code to signal an error
sys.exit(1)