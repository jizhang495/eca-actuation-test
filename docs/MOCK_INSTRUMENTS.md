# Mock Instruments Guide

The ECA Testing Webapp includes **mock instruments** that simulate real hardware for testing without physical connections.

## Auto-Detection

The system automatically detects if real VISA instruments are available:

- ✅ **Real instruments found** → Uses actual hardware
- ⚠️ **No instruments found** → Automatically switches to MOCK mode

## Mock Instruments

### Available Mock Devices

| Instrument | Mock Address | Purpose |
|------------|--------------|---------|
| DMM1 | `MOCK::DMM::DMM1::INSTR` | Voltage measurement (simulated) |
| DMM2 | `MOCK::DMM::DMM2::INSTR` | Voltage measurement (simulated) |
| Power Supply | `MOCK::POWER::IT6412::INSTR` | Voltage output (simulated) |
| Relay Board | `MOCK_COM3` | Relay switching (simulated) |
| Camera | Mock mode (automatic) | Video recording (simulated) |

## Features

### Mock DMM (Digital Multimeter)
- **Realistic voltage readings** with Gaussian noise (1mV standard deviation)
- **Small drift** to simulate real instrument behavior
- **Configurable base voltage** for testing
- **Instant connection** without hardware

### Mock Power Supply
- **Voltage control** simulation
- **Current measurement** based on simple Ohm's law
- **Output on/off** state tracking
- **Current limiting** simulation

### Mock Relay Board
- **8-channel relay** simulation
- **State tracking** for all relays
- **Instant switching** response
- **Individual channel control**

### Mock Camera
- **Start/stop recording** simulation
- **Status tracking**
- **No hardware required**

## Using Mock Mode

### 1. Start the App

The app will automatically use mock instruments if no real hardware is detected:

```bash
cd eca-actuation-test
uv run python run_backend.py
```

You'll see in the logs:
```
WARNING - No VISA devices detected - using MOCK instruments
INFO - Initializing MOCK instruments for testing
```

### 2. In the Web Interface

When using mock mode, you'll see:

1. **Instrument dropdowns** show mock addresses:
   - `MOCK::DMM::DMM1::INSTR`
   - `MOCK::DMM::DMM2::INSTR`
   - `MOCK::POWER::IT6412::INSTR`
   - `MOCK_COM3`

2. **Status display** shows `(MOCK)` next to instrument names

3. **Mock indicator** in the UI (if added)

### 3. Run a Test Measurement

You can run a full measurement with mock instruments:

1. Click **"Start Measurement"**
2. Mock instruments auto-connect
3. Live graphs show simulated voltage readings with noise
4. Voltage stages are simulated
5. Relay stages are logged
6. Data is saved to CSV just like with real instruments

### 4. Check the Data

Mock measurements produce real data files:

```
data/2025-10-13_15-30-00_test/
├── readings.csv      # Simulated DMM readings with noise
├── config.json       # Your test configuration
└── log.txt          # Measurement log
```

The CSV contains realistic data:
```csv
time,dmm1_voltage,dmm2_voltage
0.0000,0.000123,0.000045
0.1000,0.000089,0.000067
0.2000,0.000145,0.000023
...
```

## Benefits of Mock Mode

### ✅ Development Without Hardware
- Test the entire application flow
- No need for physical instruments
- Work from anywhere

### ✅ Reproducible Testing
- Consistent behavior for debugging
- Fast iteration cycles
- No hardware failures

### ✅ Safe Experimentation
- Try different configurations
- Test edge cases
- No risk to expensive equipment

### ✅ Full Feature Testing
- Test voltage stages
- Test relay switching
- Test data logging
- Test WebSocket streaming

## Checking Mock Status

### Via API

```bash
curl http://localhost:8000/api/status
```

Response includes:
```json
{
  "mock_mode": true,
  "instruments": [
    {"name": "DMM1 (MOCK)", "connected": true, ...},
    {"name": "DMM2 (MOCK)", "connected": true, ...}
  ]
}
```

### Via Logs

Backend logs will show:
```
INFO - Initializing MOCK instruments for testing
INFO - Mock DMM1 connected
INFO - Mock DMM2 connected
INFO - Mock Power Supply connected
INFO - Mock Relay Board connected
```

## Mock vs Real Instruments

| Feature | Mock | Real |
|---------|------|------|
| Connection | Instant | Requires VISA drivers |
| Data | Simulated with noise | Actual measurements |
| Speed | Very fast | Hardware-limited |
| Setup | None | Physical connections |
| Cost | Free | Expensive equipment |
| Reliability | Always available | Can fail |

## Customizing Mock Behavior

You can modify mock instrument behavior in `eca-actuation-test/instruments/mock.py`:

### Adjust Noise Level

```python
class MockKeithleyDMM:
    def __init__(self, ...):
        self._noise_level = 0.005  # Increase to 5mV noise
```

### Change Base Voltage

```python
# In measurement_controller.py
controller.dmm1.set_base_voltage(1.5)  # Set base to 1.5V
```

### Simulate Failures

```python
class MockKeithleyDMM:
    def read_voltage(self):
        if random.random() < 0.01:  # 1% failure rate
            return None
        return self._base_voltage + noise
```

## Troubleshooting

### Mock Mode Not Activating

If real instruments are connected but you want to force mock mode:

```python
# In app.py
controller = MeasurementController(use_mock=True)
```

### No Mock Devices in Dropdown

Refresh the instruments list:
1. Click "Refresh" (if available)
2. Restart backend
3. Reload webpage

### Mock Data Looks Wrong

Check the logs for errors:
```bash
# In backend terminal
tail -f logs/backend.log
```

## Example Test Scenarios

### Scenario 1: Basic Voltage Measurement

1. Start measurement (no instrument selection needed)
2. Mock DMMs automatically connect
3. Observe live graphs with simulated readings
4. Stop measurement
5. Check CSV for data

### Scenario 2: Voltage Stages

1. Add voltage stages:
   - 0-5s: 0.5V
   - 5-10s: 1.0V
   - 10-15s: 1.5V
2. Start measurement
3. Mock power supply "applies" voltages
4. DMMs show corresponding readings (simulated)
5. Check log for voltage changes

### Scenario 3: Relay Control

1. Add relay stages for CH1:
   - 0-5s: closed
   - 5-10s: open
2. Start measurement
3. Mock relay switches states
4. Check log for relay events

## Transition to Real Hardware

When you're ready to use real instruments:

1. **Connect hardware** via USB
2. **Install VISA drivers** (NI-VISA)
3. **Restart backend** → Auto-detects real devices
4. **Select real VISA IDs** from dropdowns
5. **Run measurement** with actual hardware

The app will automatically switch from mock to real mode!

## Advanced: Mixed Mode

You can mix mock and real instruments:

```python
# Use real DMMs but mock power supply
controller.dmm1 = KeithleyDMM()
controller.dmm2 = KeithleyDMM()
controller.power_supply = MockIT6412PowerSupply()  # Still mock
```

## Summary

✅ **Mock instruments let you:**
- Test the app without hardware
- Develop and debug faster
- Learn the system safely
- Generate realistic test data

✅ **Auto-detection makes it easy:**
- No configuration needed
- Seamless transition to real hardware
- Clear indication of mock mode

🚀 **Start testing immediately** - no hardware required!

---

For more information, see:
- [README.md](../README.md) - Main documentation
- [QUICKSTART.md](../QUICKSTART.md) - Getting started
- [SETUP.md](../SETUP.md) - Hardware setup guide

