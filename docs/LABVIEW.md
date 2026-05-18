# ECA Actuation Test - LabVIEW Implementation

I used these LabVIEW programmes to control the actuation testing setup for experiments in my PhD thesis and the following publications:

[1] [J. Zhang, Q. Jing, T. Wade, Z. Xu, L. Ives, D. Zhang, J.J. Baumberg, S. Kar-Narayan, Controllable multimodal actuation in fully printed ultrathin micro-patterned electrochemical actuators, *ACS Appl. Mater. Interfaces* 16 (2024) 6485–6494.](https://doi.org/10.1021/acsami.3c19006)

[2] [J. Zhang, J.J. Baumberg, S. Kar-Narayan, The thickness-dependent response of aerosol-jet-printed ultrathin high-aspect-ratio electrochemical microactuators, *Soft Matter* 20 (2024) 9424-9433.](https://doi.org/10.1039/D4SM00886C)

The programme [`actuationIT6412w2110.vi`](../labview/actuationIT6412w2110.vi) controls a Canon EOS 2000D DSLR camera, an IT6412 bipolar DC power supply, and up to two Keithley 2100 digital multimeters. It is used for manual control of output voltage, frequency sweep tests, and cyclic voltammetry (testing under triangular waves).

The programme [`automateRelayShortCircuit_IT6412w2110.vi`](../labview/automateRelayShortCircuit_IT6412w2110.vi) controls a Canon EOS 2000D DSLR camera, an IT6412 bipolar DC power supply, two Keithley 2100 digital multimeters, and a Devantech USB-RLY08C relay board. It is used for DC transient tests.

## Experimental Setup

Basic setup (refer to the papers above for more details):

![`Basic setup`](../labview/pics-labview-setup/basic_actuation_testing_setup.png)

Schematic:

![`Basic schematic`](../labview/pics-labview-setup/schematic_basic_actuation_testing_setup.svg)

The digital multimeter measuring across the $300\ \mathsf{\Omega}$ resistor gives the current in the circuit (electronic resistance across the ECA is much larger ~$\mathsf{M\Omega}$)

## How to Use

To control the IT6412 power supply with the LabVIEW programme, download the LabVIEW Driver & CTRL Softwware from the [ITECH download page](https://www.itechate.com/en/download.html) (an account is required for log in). Put the IT64001 folder into this folder. 

The Canon EOS Digital Software Development Kit (EDSDK) is required to create the executables to start/stop the camera (by compiling [`StartRecord.cpp`](../camera/StartRecord.cpp) and [`StopRecord.cpp`](../camera/StopRecord.cpp) in the [`camera`](../camera/) folder), which are called by the LabVIEW programme.
