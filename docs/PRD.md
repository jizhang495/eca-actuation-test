# Electrochemical Actuator Testing Webapp

It should be a webapp coordinating the following instruments:
- 2 digital multimeters (DMMs)
- 1 oscilloscope (to be added later as a replacement of the DMMs)
- 1 IT6412 bipolar DC power supply
- 1 RS Pro RSDG805 function generator (to be added later as a replacement of the DC supply)
- 1 Devantech USB-RLY08C relay board
- 1 Canon 2000D DSLR camera

 The data is recorded as csv (time, DMM1 reading, DMM2 reading) and saved into the data\ folder. 

---

## 1. Vision

The **Electrochemical Actuator Testing Webapp** (*ECA-TestBench*) provides a unified interface for controlling, monitoring, and recording experiments involving electrochemical actuators (ECAs).
It serves two purposes:

1. **Standalone Mode:** A browser-based test control system for researchers to run and log experiments manually.
2. **Integrated Mode:** A backend module callable via REST API or Python SDK by higher-level AI agents (e.g., the “Operation Agent”) for automated testing.

The app coordinates multiple laboratory instruments—digital multimeters, DC power supplies, relay boards, cameras—and synchronizes their operation for precise, reproducible actuator measurements.

---

## 2. Objectives

1. Enable **synchronized data acquisition and control** of multiple instruments.
2. Provide an **intuitive web interface** for human-supervised operation.
3. Ensure **modular architecture**, allowing headless API use by automation agents.
4. Record all experimental data, configurations, and logs for reproducibility.

---

## 3. Supported Instruments (MVP + Future)

| Category     | Model                      | Communication       | Function                               |
| ------------ | -------------------------- | ------------------- | -------------------------------------- |
| DMM ×2       | Keithley 2110              | VISA / USB          | Voltage measurement                    |
| Power Supply | IT6412                     | VISA / USB          | Apply programmed voltage waveforms     |
| Relay Board  | Devantech USB-RLY08C       | USB (serial)        | Channel switching control              |
| Camera       | Canon 2000D DSLR           | C++ backend service | Synchronized video recording           |
| *(Future)*   | Oscilloscope               | VISA / USB          | Replace DMMs for AC/frequency tests    |
| *(Future)*   | RSDG805 Function Generator | VISA / USB          | Replace DC supply for waveform driving |

---

## 4. System Overview

### 4.1 Operation Flow

```
[User/Agent clicks START]
      ↓
[Camera → Start Recording]
      ↓
[DMMs → Start Live Reading + CSV Logging]
      ↓
[DC Supply + Relay → Apply Programmed Functions]
      ↓
[All data synchronized and logged]
      ↓
[User/Agent clicks STOP]
      ↓
[Camera Stop + DMM Stop + Save CSV]
```

### 4.2 Core Components

* **Frontend:** Interactive dashboard for control and visualization.
* **Backend:** FastAPI (Python) managing instrument drivers, WebSocket streams, and CSV logging.
* **Instrument Drivers:** Python classes for DMMs, DC supply, relays, and camera service bridge.
* **Camera Service:** Background C++ daemon exposing start/stop endpoints.
* **Data Layer:** Local CSV files + metadata JSON (for session records).

---

## 5. Functional Requirements

### 5.1 User Interface

**Layout Overview**

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ [ Start Measurement ] [ Stop Measurement ]                                   │  ← Header
├───────────────────────────────────────────────────────────────────────────────┤
│ [ Graph DMM1 ]                                                               │
│   Dropdown: Instrument / VISA ID                                             │
│ [ Graph DMM2 ]                                                               │
│   Dropdown: Instrument / VISA ID                                             │  ← Left Column
├───────────────────────────────────────────────────────────────────────────────┤
│ DC Power Supply Settings                                                     │
│   + Add Stage (max 10): [Start s] [End s] [Voltage V]                        │
│ Relay Board Channels                                                         │
│   CH1 stages (max 10): [Start s] [End s] [Open/Closed]                       │
│   CH2 stages (max 10): [Start s] [End s] [Open/Closed]                       │
│ [ Camera Status: ● Recording / ○ Idle ]                                      │
│ [ Log / Output Folder: data\ ]                                               │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Functional Elements**

| Feature                      | Description                                                                           |
| ---------------------------- | ------------------------------------------------------------------------------------- |
| **Start Measurement Button** | Initiates synchronized start of camera, DMMs, and programmed DC/relay functions.      |
| **Stop Measurement Button**  | Stops all devices, finalizes CSV and video saving.                                    |
| **Live Graphs (2)**          | Realtime voltage (V) vs. time (s) plots from each DMM. Adjustable timebase and scale. |
| **Graph Dropdowns**          | Select which instrument and VISA ID to use for each graph.                            |
| **DC Power Supply Control**  | Add up to 10 time-voltage stages. Backend sends commands at scheduled intervals.      |
| **Relay Control**            | Configure up to 10 open/close stages per channel.                                     |
| **Camera Indicator**         | Shows live status (Idle / Recording).                                                 |
| **Data Logging**             | CSV: `time, DMM1, DMM2` saved to `user-data/sessions/`. Video stored with session artifacts. |

---

### 5.2 Backend Services

| Module                   | Functionality                                                        | Implementation                  |
| ------------------------ | -------------------------------------------------------------------- | ------------------------------- |
| **Instrument Manager**   | Manages connected VISA/serial devices; dynamic enumeration.          | Python (PyVISA, pyserial)       |
| **Scheduler**            | Executes staged actions for DC supply and relays with ms precision.  | Async tasks (FastAPI + asyncio) |
| **Data Streamer**        | Collects DMM readings, timestamps, streams via WebSocket.            | Async loop, thread-safe queue   |
| **Camera Controller**    | REST bridge to C++ camera service (`/start_record`, `/stop_record`). | HTTP calls                      |
| **CSV Logger**           | Writes timestamped DMM readings.                                     | Pandas / native CSV writer      |
| **API Layer**            | Exposes endpoints for external calls (start/stop, status, logs).     | FastAPI                         |
| **WebSocket Layer**      | Real-time data push to frontend graphs.                              | FastAPI WebSocket               |
| **Config & State Store** | Stores session config in JSON.                                       | `user-data/sessions/`           |

---

### 5.3 External API (for integration with AI agents)

| Endpoint                 | Method | Description                                                               |
| ------------------------ | ------ | ------------------------------------------------------------------------- |
| `/api/start_measurement` | `POST` | Starts full test sequence; accepts JSON config for voltage & relay stages |
| `/api/stop_measurement`  | `POST` | Stops measurement and returns file paths                                  |
| `/api/status`            | `GET`  | Returns live instrument status                                            |
| `/api/live`              | `WS`   | WebSocket streaming DMM readings                                          |
| `/api/list_instruments`  | `GET`  | Returns available VISA/serial devices                                     |
| `/api/session/{id}/data` | `GET`  | Retrieves stored CSV and metadata                                         |

This allows the **Operation Agent** to:

* Trigger measurements autonomously
* Retrieve data for analysis
* Query system health

---

## 6. Non-Functional Requirements

| Category          | Requirement                                                                  |
| ----------------- | ---------------------------------------------------------------------------- |
| **Performance**   | Data logging at ≥10 Hz per DMM.                                              |
| **Reliability**   | Auto-reconnect for instrument communication errors.                          |
| **Usability**     | Simple visual layout; one-click start/stop.                                  |
| **Extensibility** | Modular driver layer for new instruments (oscilloscope, waveform generator). |
| **Compatibility** | Runs cross-platform (Windows primary target; Linux optional).                |
| **Data Safety**   | Each run saved in timestamped folder; CSV and logs auto-backed-up.           |

---

## 7. Data & File Structure

```
user-data/sessions/
  /2025-10-12_15-30-02_test1/
    readings.csv          # time, DMM1, DMM2
    config.json           # voltage + relay stages
    video.mp4             # recorded by camera, to be transferred later from camera SD card to computer
    log.txt               # instrument messages
```

---

## 8. Technology Stack

| Layer              | Tech                                               | Notes                               |
| ------------------ | -------------------------------------------------- | ----------------------------------- |
| **Frontend**       | React + Next.js + shadcn/ui + Tailwind             | Modern, modular dashboard           |
| **Backend**        | Python 3.11 + FastAPI + asyncio                    | High-concurrency instrument control |
| **Drivers**        | PyVISA, pyserial, pyusb                            | Instrument communication            |
| **Camera Service** | C++ daemon + HTTP bridge                           | Canon SDK wrapper                   |
| **Data Streaming** | FastAPI WebSockets                                 | Live visualization                  |
| **Storage**        | Local FS (CSV + video), SQLite (optional metadata) | Versioned runs                      |
| **Deployment**     | Docker Compose (backend, frontend, camera-service) | `.dev/` setup scripts               |

---

## 9. Success Metrics

| Metric                  | Target                                         |
| ----------------------- | ---------------------------------------------- |
| Instrument sync latency | <100 ms drift between DMMs and camera          |
| UI responsiveness       | <200 ms update latency                         |
| Data loss rate          | 0% during normal operation                     |
| User setup time         | <5 min from launch to measurement              |
| Integration readiness   | REST endpoints functional for AI orchestration |

---

## 10. Risks & Mitigation

| Risk                          | Impact | Mitigation                                             |
| ----------------------------- | ------ | ------------------------------------------------------ |
| VISA connection instability   | Medium | Implement auto-retry & watchdog                        |
| Camera driver issues          | Medium | Keep C++ service modular, fallback to manual record    |
| Relay timing drift            | Low    | Use async scheduler with ms timestamp alignment        |
| File I/O lag at high sampling | Low    | Buffered writes or in-memory queue                     |
| Multiple device IDs confusion | Medium | Clear dropdown display of VISA IDs, config persistence |

---

## 11. Deliverables (MVP)

* Web frontend with:

  * Start/Stop buttons
  * Two adjustable live voltage graphs
  * Dropdowns for VISA instrument selection
  * DC & relay stage configurators
  * Camera indicator
* Backend with:

  * Device detection & control APIs
  * CSV data logger
  * WebSocket live stream
  * C++ camera bridge service
* `.dev/` environment:

  * Docker Compose setup
  * Mock DMM and relay simulators
  * Example dataset

---

## 12. Timeline (Estimated)

| Phase | Deliverable                            | Duration |
| ----- | -------------------------------------- | -------- |
| M1    | DMM + Power Supply + Relay integration | 3 weeks  |
| M2    | Live graph UI + camera service         | 2 weeks  |
| M3    | REST + WebSocket API                   | 1 week   |
| M4    | Data logging + replay mode             | 1 week   |
| M5    | Integration test + docs                | 1 week   |

---

## 13. Future Extensions

1. Oscilloscope and Function Generator integration.
2. Real-time impedance or frequency response tests.
3. Cloud-based experiment logging and dashboard.
4. Agentic API plugin for full automation (Operation Agent).
5. Cross-session comparison and analytics (capacitance, hysteresis).

