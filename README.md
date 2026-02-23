# ADS-B Drone Safety Monitor

A real-time airspace awareness tool for drone pilots and FPV operators. Displays nearby aircraft on a vintage-style radar display with voice alerts, threat detection, and orbit/circling aircraft warnings.

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-red)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **Live radar display** with rotating sweep animation showing all ADS-B aircraft within range
- **Threat detection** — identifies aircraft heading toward your position at low altitude
- **Three-tier alert system** — Caution, Warning, and Danger with color-coded display and audio
- **Voice alerts** via espeak-ng with aircraft identity, distance, and ETA
- **Orbit/circling detection** — alerts when any aircraft completes more than 270 degrees of turn
- **Blue non-threat aircraft** displayed on radar so you can see all traffic, not just threats
- **Clickable radar** — tap any aircraft dot to see full details in the Selected panel
- **GPS-locked position** — your location updates in real time via gpsd
- **Registration lookup** — displays tail numbers when available via tar1090 database
- **Adjustable range** — 1 to 10 mile radar range slider
- **Adjustable altitude ceiling** — filter by max altitude AGL
- **Field elevation setting** — accurate AGL calculations at your flying site

---

## Hardware

| Component | Details |
|-----------|---------|
| Computer | ClockworkPi CM4 Lite 8GB |
| SDR Board | HackerGadgets AIO Version 1 |
| GPS | Built-in via HackerGadgets AIO (ttyAMA0) |
| OS | Raspberry Pi OS (Debian Bookworm, aarch64) |

---

## Dependencies

### System packages
```bash
sudo apt install readsb gpsd gpsd-clients espeak-ng python3-tk pulseaudio


