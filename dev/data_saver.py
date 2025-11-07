from VNA import Controller
import numpy as np
import matplotlib.pyplot as plt

vna = Controller()
vna.connect()

freq, s11 = vna.read_s11()
_, s21 = vna.read_s21()
_, s12 = vna.read_s12()
_, s22 = vna.read_s22()

vna.close() 

fig, axs  = plt.subplots(2, 2, figsize=(10, 8))
axs[0, 0].plot(freq, 20 * np.log10(np.abs(s21)))
axs[0, 0].set_title("S21 Magnitude (dB)")
axs[0, 1].plot(freq, 20 * np.log10(np.abs(s11)))
axs[0, 1].set_title("S11 Magnitude (dB)")
axs[1, 0].plot(freq, 20 * np.log10(np.abs(s12)))
axs[1, 0].set_title("S12 Magnitude (dB)")
axs[1, 1].plot(freq, 20 * np.log10(np.abs(s22)))
axs[1, 1].set_title("S22 Magnitude (dB)")
plt.tight_layout()
plt.show()

save = input("Save data to 's_parameters.npz'? (y/n): ")
if save.lower() == 'y':
    np.savez('s_parameters.npz', frequency=freq, S11=s11, S21=s21, S12=s12, S22=s22)
    print("Data saved to 's_parameters.npz'")