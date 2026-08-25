---
publishDate: 2026-08-25
title: Shipment Sentinel — A Multi-Sensor Tamper-Evident Logger for Shipping & Asset Integrity Monitoring
excerpt: A device built on the MYOSA kit that stays fully offline for the entire journey, logging every shock, drop, tamper, and pressure event a shipment experiences in transit, then hands over a tamper-evident, cryptographically verifiable record once it reaches the receiver.
image: shipment-sentinel/cover-image.png
tags:
- iot
- logistics
- embedded-systems
---

> A fully offline shipment logger that proves exactly how a package was handled in transit, and hands over a tamper-evident record the moment it's delivered.

---

## Acknowledgements

We express our sincere gratitude to our Faculty Mentor, Prof. Job Chunkath, Associate Professor, Department of Electronics and Communication Engineering, Government Engineering College Thrissur, for his continuous guidance, technical insights, and encouragement at every stage of developing Shipment Sentinel.

We thank the Department of Electronics and Communication Engineering and the management of Government Engineering College Thrissur (GEC Thrissur), Keralam, India, for providing the lab access, resources, and encouraging environment that made this project possible.

We are grateful to the IEEE MYOSA Event 6.0 organizers and the IEEE Sensors Council for building a platform that pushes students to move beyond theory and solve real industrial problems — in our case, the everyday challenge of proving what actually happens to a shipment in transit.

Finally, we thank our teammates for their persistence through every sensor calibration issue, every firmware bug, and every late debugging session — for turning an idea born out of a real logistics problem into a working, tamper-evident device we're proud to bring to this stage.

---

## Overview

Shipment Sentinel is a compact, low-cost edge logger built on the MYOSA Mini IoT Kit (ESP32-WROOM-32 core) that protects high-value or sensitive cargo — electronics, pharmaceuticals, aerospace components, and similar goods — during transit. Once placed inside the shipping box and sealed before dispatch, the device runs completely unattended and fully offline for the entire journey: WiFi and Bluetooth remain off throughout transit, and the device operates purely as a self-contained edge sensor and logger, powered end-to-end by a single internal Samsung INR21700-48X 3.6V 4800mAh 2C Li-ion battery.

A fused sensor stack — the MPU6050 accelerometer/gyroscope, the APDS9960 ambient light sensor, the BMP180 barometric sensor, and a battery-backed DS3231 RTC for accurate offline timestamps — continuously watches for four physical excursion vectors: mechanical shocks, free-fall drops, container light breaches (tampering), and barometric pressure anomalies indicating altitude change or depressurization. Every incident is timestamped and written to a microSD card as part of a cryptographically chained, tamper-evident event log.

Only at the receiving end does the device ever go online. The recipient holds a push button on the outside of the device casing for two seconds, which wakes an onboard WiFi access point — the only point in the device's operating life where wireless is active. A companion desktop application then connects to this access point, pulls the full event log and device status, verifies the cryptographic hash chain, and computes a Shipment Integrity Score. If WiFi retrieval isn't convenient, the enclosure can also be opened to remove the microSD card directly, as a secondary retrieval option.

Each event is scored using a tiered, capped scoring model. Mechanical shocks are classified into minor (2.20G–5.00G) and severe (≥5.00G) tiers; free-fall drop height is derived mathematically from the measured fall duration; tamper events accrue a penalty proportional to how long the container stays open; and pressure deviations beyond 5% of a rolling baseline are flagged as anomalies. A shock or drop occurring within a 10-second window of a tamper event is automatically escalated to a Severe/Compromised Incident — a correlation a single-sensor device could not make. Deductions in each category are individually capped (shock ≤30, drop ≤30, tamper ≤25, pressure ≤15 points) so that no single incident type can unfairly dominate the final 0–100 Shipment Integrity Score, which resolves to a clear PASSED, WARNING, or COMPROMISED status.

Every logged event also carries a rolling SHA-256 hash. Each hash incorporates the previous event's hash along with the current timestamp and telemetry, forming an unbroken chain from the first log entry to the last. Altering or deleting any single entry after the fact breaks this mathematical chain, making tampering with the historical record detectable rather than silent.

The device features a sealed enclosure designed to protect internal hardware during transit, with a precise top aperture positioned over the APDS9960 light sensor so it can detect container breaches and box openings from within the housing. For data retrieval and backup, the onboard microSD card can be retrieved directly from the unit when required.

**Key features:**

* Multi-vector sensing: shock, free-fall, tamper, and pressure detection fused in one device
* Tiered event classification with automatic shock+tamper "Severe Incident" escalation
* On-device 0–100 Shipment Integrity Score with PASSED / WARNING / COMPROMISED status
* Rolling SHA-256 hash chain for cryptographically verifiable, tamper-evident logs
* Fully offline, deep-sleep edge operation throughout transit, with instant interrupt-driven wake-up
* Powered by a single Samsung INR21700-48X 3.6V 4800mAh 2C Li-ion battery, sealed internally for the full unattended transit duration
* WiFi activated only at the receiving end for data extraction, with SD card removal as a backup retrieval option
* Custom Tkinter-based desktop ingestion utility with a waveform visualizer
* One-click, printable chain-of-custody certificate
* Reusable design with secure operator-controlled wipe and re-arm

---

## Demo / Examples

### Images



<p align="center">
<img src="components.jpeg" width="400"><br/>
<i>System prototyping stage showing the MYOSA core board connected to the multi-sensor stack, external power bank, and SPI microSD logging module.</i>
</p>

<p align="center">
<img src="enclosure.jpeg" width="400"><br/>
<i>Internal hardware assembly showcasing the MYOSA ESP32 motherboard, stackable sensor module stack, microSD logger, and high-capacity 21700 Li-ion battery.</i>
</p>

<p align="center">
<img src="final-structure.jpeg" width="400"><br/>
<i>The final sealed enclosure houses the full sensing stack in a tamper-resistant unit. A precise top aperture leaves the APDS9960 ambient light sensor exposed to detect unauthorized box openings, while an externally accessible push-button allows the recipient to initiate the 2-second hold sequence to activate the onboard WiFi access point for data extraction.</i>
</p>

<p align="center">
<img src="front-end.jpeg" width="400"><br/>
<i>Shipment Sentinel desktop ingestion utility displaying the cryptographic chain-of-custody audit log, complete with itemized event timestamps, telemetry metrics, and rolling SHA-256 block hashes.</i>
</p>

<p align="center">
<img src="time-graph.jpeg" width="400"><br/>
<i>Shipment Sentinel desktop app displaying real-time telemetry waveforms, Integrity Score breakdown, and verified SHA-256 hash-chain audit log.</i>
</p>

<p align="center">
<img src="coc-transit-certificate.jpeg" width="400"><br/>
<i>Auto-generated Chain-of-Custody Transit Certificate featuring cryptographic verification status, trip passport metadata, incident excursion summary, and printable audit trail.</i>
</p>

<p align="center">
<img src="oled.jpeg" width="400"><br/>
<i>0.96-inch OLED display module used during prototyping and testing to display real-time trip summaries, incident counts, and live integrity scores.</i>
</p>


### Videos

#### 1. Project Explanation Video

<video controls width="100%">
  <source src="shipment-sentinel-explanation.mp4" type="video/mp4">
</video>

[▶ Click here to watch the Project Explanation Video](https://github.com/SajidSKS/shipment-sentinel/raw/main/shipment-sentinel-explanation.mp4)

#### 2. Live Demonstration Video

https://github.com/user-attachments/assets/9a2c8cb4-0ac7-4288-9f29-d50a4d84027f

## Features (Detailed)

### 1. Multi-vector sensing: shock, free-fall, tamper, and pressure detection in one device

The system fuses four independent sensing channels — the MPU6050 accelerometer for kinetic shocks, sustained near-zero-G readings for free-fall detection, the APDS9960 ambient light sensor for container breaches, and the BMP180 barometric sensor for altitude/depressurization tracking — all sampled continuously and correlated on-device rather than read in isolation.

### 2. Tiered event classification with automatic shock+tamper "Severe Incident" escalation

Rather than a single flat threshold, each vector uses graded severity. Shocks are split into Minor (2.20G–5.00G) and Severe (≥5.00G) tiers. A free-fall is registered once acceleration drops below 0.50G and stays there for at least 80ms, and the resulting drop height is derived mathematically from the fall duration (h = ½gt²). Tamper events accrue an escalating penalty the longer the container stays open. When a shock or drop occurs within a 10-second window of a tamper event, the system automatically classifies it as a Severe/Compromised Incident — a correlation a single-sensor device could not make.

### 3. On-device 0–100 Shipment Integrity Score with PASSED/WARNING/COMPROMISED status

The score starts at 100/100 and deducts weighted penalties per category — shocks, drops, tamper, and pressure — with a hard cap on each category (shock ≤30, drop ≤30, tamper ≤25, pressure ≤15 points) so that no single incident type can unfairly dominate the result. The final number resolves to a clear status band (≥85 Passed/Safe, 50–84 Warning/Caution, <50 Compromised/Severe), turning a raw event log into an instantly readable verdict.

### 4. Rolling SHA-256 hash chain for cryptographically verifiable logs

Each logged event computes a hash that incorporates the previous event's hash along with the current timestamp and telemetry, forming an unbroken chain from the first log entry to the last. Altering or deleting any single entry after the fact breaks the mathematical chain, making tampering with the historical record detectable rather than silent.

### 5. Fully offline, deep-sleep edge operation throughout transit

For the entire duration of the shipment, WiFi and Bluetooth remain off. The device spends most of its life in low-power deep sleep, waking on a routine timer or instantly via hardware interrupt the moment a shock or a light-level change is detected, ensuring no event is missed while keeping power draw to a minimum.

### 6. WiFi-based retrieval at the receiving end, with SD card removal as a backup

The device only ever goes online once, at the point of delivery. Holding the push button on the device casing for two seconds wakes an onboard WiFi access point. The recipient connects a laptop to this network using the on-device SSID and password, then browses to the device's IP address (or uses the companion desktop app) to pull the trip log and status. As a secondary option, the case can also be opened and the microSD card removed directly to read the log on any computer.

### 7. Custom Tkinter-based desktop ingestion utility

The receiving-end companion application is a custom Python 3 / Tkinter desktop tool that auto-connects to the device's WiFi access point, streams the raw trip log and compact status JSON, recomputes and verifies the SHA-256 hash chain, and renders an interactive shock/tamper/pressure waveform timeline of the full journey.

### 8. One-click, printable chain-of-custody certificate

After hash-chain verification, the ingestion utility generates a printable inspection certificate summarizing the shipment's integrity record, giving inspectors, insurers, or logistics teams a physical document to support claims or dispute resolution.

### 9. Reusable design with secure operator-controlled wipe and re-arm

Once a shipment is verified and archived, the operator can trigger an erase & re-arm action that wipes the log and resets the device's score to 100/100, making the same hardware fully reusable for the next trip rather than single-use.

### 10. OLED status display — prototyping and demonstration aid only

During prototyping and development, a 0.96" OLED display was connected to the MYOSA stack to visually confirm sensor readings, dashboard WiFi credentials, and trip summaries while building and testing the firmware — this is what is shown in the accompanying development images. In the final, sealed device the OLED is not included: it stays off for most of the device's operating life and serves no purpose once the unit is closed inside its enclosure, so removing it reduces power consumption. It remains a useful bench-testing tool but is not part of the deployed device.

### 11. Powered by a Samsung INR21700-48X Li-ion battery

The device runs on a single Samsung INR21700-48X, 3.6V, 4800mAh, 2C-rated Li-ion cell, sized to cover the ESP32's near-zero deep-sleep draw plus periodic interrupt-driven sensor wake-ups across multi-day or multi-week shipments, while its 2C discharge rating (~9.6A continuous) absorbs the brief 200–300mA current spike when the recipient's button-hold triggers the WiFi access point, preventing brownout at the exact moment the log needs retrieval. Sealed permanently within the casing alongside the wiring and microSD card, the rechargeable cell also enables the device's wipe-and-re-arm cycle, letting the same physical unit be reused shipment after shipment rather than discarded.

---

## Usage Instructions

1. **Seal and arm the device.** Place the Sentinel inside the shipping box before dispatch and close the box. The device enters a short placement buffer window so it isn't triggered by the handling involved in packing it, then samples the MPU6050, APDS9960, and BMP180 to establish a baseline (rest orientation, ambient light with the box closed, and local barometric pressure). Once set, the device arms itself automatically — no button press needed.

2. **Let it run unattended through transit.** WiFi and Bluetooth stay off; the ESP32 spends most of its time in deep sleep, waking instantly on a hardware interrupt whenever a sensor crosses a threshold, or on a routine timer for housekeeping. Every qualifying event is timestamped against the DS3231 RTC, scored, and appended to the microSD log as the next link in the SHA-256 hash chain.

3. **Wake the device on arrival.** A two-second button hold is the only action needed — it's also the only point in the device's life where wireless turns on, waking the onboard WiFi SoftAP and a lightweight HTTP server.

4. **Connect to the device.** Join the broadcast WiFi network using the SSID/password shown during setup, then either browse to the device's IP directly or let the companion app auto-connect.

5. **Retrieve and verify the trip data.** The app recomputes the SHA-256 chain and flags any break, renders the waveform timeline, and shows the final Integrity Score with a category breakdown (shock/drop/tamper/pressure).

6. **Generate the certificate (optional).** One click produces a printable chain-of-custody certificate for insurers or logistics teams.

7. **Fall back to manual retrieval if needed.** Open the case and pull the microSD card — logs are readable on any computer without the access point.

8. **Reset for reuse (optional).** Erase & re-arm wipes the log, resets the score to 100/100, and re-baselines the sensors for the next shipment.

---

## Tech Stack

* **ESP32-WROOM-32** (Arduino / C++ firmware)
* **MPU6050** (accelerometer/gyroscope), **APDS9960** (ambient light), **BMP180** (barometric pressure), **DS3231** (RTC)
* **microSD card storage** (SPI, SdFat)
* **SHA-256 rolling hash chain** (mbedTLS)
* **ESP32 WiFi SoftAP** + lightweight HTTP server (active only at receiver end)
* **Python 3 with Tkinter** (desktop ingestion utility)

---

## Requirements / Installation

**Firmware:**

* Arduino IDE with ESP32 board support
* Libraries: MPU6050, Adafruit APDS9960, Adafruit BMP180, RTClib, SdFat

**Companion desktop app:**

```bash
pip install tk pandas matplotlib
```

---

## File Structure

```
shipment-sentinel
|-shipment-sentinel.md            # Full technical report
├── LICENSE                       # License text file
├─ shipment_sentinel.ino          # ESP32 firmware source (sensors, storage, hash chain, WiFi server)
├─ sentinel_extractor.py          # Desktop ingestion utility & hash verifier (Python 3, Tkinter)
├─ launch_sentinel_extractor.bat  # One-click launcher for the ingestion utility
└─ sentinel_reports               # Extracted chain-of-custody archives
   ├─ Trip_SENTINEL_AC7F_20260825_000152.csv                         # Raw event logs
   ├─ Trip_SENTINEL_AC7F_20260825_000152.json                        # Trip metadata / passport records
   └─ Chain-of-Custody Transit Certificate — CERT-1787654897-SENTIN  # Printable inspection certificates
```

---


## License

This project is licensed under the [MIT License](LICENSE) — see the `LICENSE` file in this directory for complete details. It covers all software and firmware components including `shipment_sentinel.ino` and `sentinel_extractor.py`.



## Contribution Notes 

Contributions, bug reports, and feature requests are welcome! 

* **Reporting Issues:** If you encounter bugs in the firmware logging, cryptographic verification, or UI rendering, please open an issue describing the bug and steps to reproduce it.
* **Feature Requests & Enhancements:** PRs are welcome for expanding sensor support (e.g., GPS integration, temperature monitoring), optimizing power consumption, or improving desktop application analytics.
* **Pull Requests:** 
  1. Fork the repository and create your feature branch (`git checkout -b feature/AmazingFeature`).
  2. Commit your changes (`git commit -m 'Add some AmazingFeature'`).
  3. Push to the branch (`git push origin feature/AmazingFeature`).
  4. Open a Pull Request for review.

<br>
<br>


Team Solvers<br>
Government Engineering College,Thrissur<br>
Keralam,India
