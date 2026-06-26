# Coukab LAN

Local-network home-automation controller. A numeric keypad (read via evdev)
**and** a beautiful, no-login glassmorphic web dashboard drive Yeelight bulbs,
a Xiaomi S20 vacuum, a Xiaomi Air Purifier 4 Lite, and a TP-Link Tapo camera —
all over the LAN, no cloud required. The web UI is fully optimized for mobile
viewports via bottom-nav tabs, and desktop viewports via responsive multi-column
layouts. An optional local LLM agent ("Khatoon", `brain.py`) exposes the same
device utilities as tool-calling functions.

Run `python main.py` and you get **both** interfaces at once: the keypad and a
live web dashboard you can open from any phone or laptop on the same network.

---

## Quick start

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp example.env .env          # fill in your device IPs / tokens (see below)
# edit bulb.json with your bulbs

venv/bin/python main.py      # starts the keypad + web UI on port 8080
```

Then open the web page (see **[Connecting to the web page](#connecting-to-the-web-page)**).

---

## Setup

### 1. Install

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

The web interface needs **no extra dependencies** — it uses the Python standard
library only.

### 2. `.env` — device credentials

```bash
cp example.env .env
```

| Variable | Purpose |
| --- | --- |
| `TAPO_IP`, `TAPO_USERNAME`, `TAPO_PASSWORD` | Tapo camera account (RTSP + control API) |
| `TAPO_CLOUD_PASSWORD` | Optional cloud-auth fallback |
| `VACUUM_IP`, `VACUUM_TOKEN` | Xiaomi S20 vacuum (32-char hex token) |
| `AIRPURIFIER_IP`, `AIRPURIFIER_TOKEN` | Xiaomi Air Purifier 4 Lite |
| `MOMENTS_DELETE_PASSWORD` | Optional password required to delete captured images from the web UI (unset = no password) |

Xiaomi tokens come from the Xiaomi Cloud Tokens Extractor. Any device you leave
unconfigured simply shows as "offline" in the web UI — the rest still work.

### 3. `bulb.json` — your bulbs

Maps bulb names to their Yeelight device IDs and IPs (the grid layout is
I1/I2/I3 in one column, I4/I5/I6 in the other):

```json
{
    "I1": {"id": "0x0000000012345678", "ip": "192.168.1.21"},
    "I2": {"id": "0x0000000012345679", "ip": "192.168.1.22"}
}
```

IPs are refreshed automatically by discovery when commands start failing (e.g.
after DHCP reassignment). **LAN Control must be enabled for each bulb** in the
Yeelight app.

---

## Running

```bash
# keypad + web (default): web on http://0.0.0.0:8080
venv/bin/python main.py

# pick the keypad device explicitly (find yours with: sudo evtest)
venv/bin/python main.py --device /dev/input/event3 --grab -v

# change the web port
venv/bin/python main.py --web-port 9000

# keypad only (no web)
venv/bin/python main.py --no-web

# web only on this machine (not exposed to the LAN)
venv/bin/python main.py --web-host 127.0.0.1
```

If the keypad device can't be opened, the web interface **keeps running anyway**
— so you can still control everything from your phone.

---

## Connecting to the web page

The web page runs on the machine where `main.py` is running. Open it from any
device on the **same Wi-Fi / LAN** — there is no login.

**1. Make sure both devices are on the same network.** Your phone/laptop and the
machine running `main.py` must be on the same router/Wi-Fi.

**2. Find the address.** When `main.py` starts it prints the URL, e.g.:

```
Web interface: http://0.0.0.0:8080  (open http://192.168.1.42:8080 on your phone) — no login.
```

Use the `192.168.x.x` address (not `0.0.0.0`). If you missed it, find the
machine's LAN IP yourself:

```bash
hostname -I | awk '{print $1}'      # Linux: prints the LAN IP
ip -4 addr show | grep inet         # Linux: list all addresses
```

(Running as a service? Read the log: `journalctl --user -u coukab-lan -n 20`.)

**3. Open it in a browser** on your phone or laptop:

```
http://<that-ip>:8080
```

For example `http://192.168.1.42:8080`. That's it — the dashboard loads
immediately, no password.

**Tip:** on a phone, use the browser's **"Add to Home Screen"** to get an
app-style icon that opens straight to the controls.

### Can't connect?

| Symptom | Fix |
| --- | --- |
| Page won't load at all | Confirm both devices are on the **same** network; confirm `main.py` is running; try the IP from `hostname -I`. |
| "Connection refused" | The app isn't running, or you used the wrong port. Default is `8080`; change with `--web-port`. |
| Loads on the host but not from the phone | A firewall is blocking the port. On Linux: `sudo ufw allow 8080`. Also make sure you did **not** start with `--web-host 127.0.0.1` (that's local-only). |
| Port already in use | Another program holds `8080` — start with `--web-port 9000` (or any free port). |
| Page loads but a device says "offline" | Check that device's IP/token in `.env`, that it's powered on, and that it's on the same LAN. |

> **Security note:** the web interface is intentionally unauthenticated. Only run
> it on a trusted home LAN (the default `--web-host 0.0.0.0` listens on all
> interfaces). Use `--web-host 127.0.0.1` to keep it on the host only.

---

## What's on the web page

A live, high-performance dashboard featuring a modern minimal glassmorphic design, fluid typography, and responsive layouts:

*   **Real-time Synchronization (SSE):** Utilizes Server-Sent Events (`/api/events`) to stream status snapshots instantly to all connected clients when changes occur (including keypad actions), with automatic fallback to 5s polling.
*   **Optimistic UI & Low Latency:** Buttons dim during requests, and purifier toggles transition optimistically for immediate visual feedback.
*   **Mobile Viewport Optimization:** A floating bottom tab bar navigation dynamically partitions controls into *Home*, *Lights*, *Devices* (Vacuum + Purifier), *Camera*, and *Khatoon Chat* views to avoid scrolling fatigue.
*   **Desktop Viewport Adaptation:** Displays a unified dashboard view with a sticky sidebar on the left and a responsive multi-column layout on the right.
*   **Progressive Web App (PWA):** Includes PWA support (`manifest.webmanifest`, `icon.svg`, `sw.js`) with a service worker implementing stale-while-revalidate caching. "Add to Home Screen" launches the UI in full-screen.
*   **A11y & Localization:** Features a full English/Farsi localization toggle (FA/EN button, RTL layout alignment, translated labels and device states) persisted in `localStorage`, along with `aria-label` tags for screen readers.
*   **Lights** — tap individual bulbs (I1–I6) to target a subset, scenes
    (cool/warm white, sunset, sleep, romantic, movie), brightness slider (live-throttled at $\ge$350ms to respect LAN rate limits), full RGB color picker, white-temperature slider, party mode + patterns, color cycle, random colors, targeted on/off, all-off, and undo.
*   **Vacuum** — sweep / mop / both, pause, stop (guarded by a 600ms hold gesture), return-to-dock, find-me, suction and water levels, speaker volume, and a manual-drive D-pad (with press-and-hold repeating). Features per-room cleaning chips ("Clean selected rooms") and a collapsible Maintenance panel for consumables (filters, brushes, mop) warning if wear is under 30%.
*   **Air purifier** — power, modes, fan level, favorite speed, ionizer / child-lock
    / buzzer toggles, screen brightness, and live PM2.5 / temperature / humidity.
*   **Camera** — capture a moment (with or without a flash blink) and browse recent
    shots. Features instant downloading and hold-to-delete (safely confined to the `moments/` directory and optionally password-protected).
*   **Assistant** — a chat box for the local LLM ("Khatoon"); appears only when the
    model is installed (see below).

Web actions that touch shared lighting state (party dance, color cycle) run on
the same serialized worker as the keypad, so the two interfaces never conflict.

---

## Keypad bindings

See the docstring at the top of `main.py` for the full single-key and KP0-combo
map (modes, brightness, vacuum toggle, camera capture, color cycle, party, undo,
etc.).

---

## Run as a service

`coukab-lan.service` is a systemd unit that starts `main.py` at boot (keypad +
web). It documents user-service installation, starting at boot without an
interactive login, and the input-group permission evdev needs. In short:

```bash
mkdir -p ~/.config/systemd/user
cp coukab-lan.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now coukab-lan.service
sudo loginctl enable-linger "$USER"   # start at boot without logging in
sudo usermod -aG input "$USER"        # evdev keypad access (re-login after)
```

The web page is then always available at `http://<device-ip>:8080`.

---

## Touch-controller wall panel (Optional)

An ESP32-S3 wall-mounted touch panel can display real-time status and trigger actions on the LAN. Two firmware versions are available:
*   **v1 (Classic):** Found in [tuch_controller/](file:///Users/divar/Coukab/coukab_lan/tuch_controller), featuring robust layout control and setup diagnostics (see [tuch_controller/HARDWARE.md](file:///Users/divar/Coukab/coukab_lan/tuch_controller/HARDWARE.md)).
*   **v2 (Liquid-Glass):** Found in [tuch_controller_v2/](file:///Users/divar/Coukab/coukab_lan/tuch_controller_v2), featuring Apple-style frosted translucency (Liquid-Glass design), a Dynamic Island notification bar, and an ambient background that responds to active bulb colors (see [tuch_controller_v2/DESIGN.md](file:///Users/divar/Coukab/coukab_lan/tuch_controller_v2/DESIGN.md)).

Both versions sync with the backend server via Server-Sent Events (SSE) and request server-rendered RGB565 images for alert and camera preview rendering.

---

## LLM brain — "Khatoon" (optional)

A local, CPU-only LLM that **chats on the web page and controls the house**
through the same device tools. Model selection for the low-power server is
documented in [brain_model/model_choice_design.md](file:///Users/divar/Coukab/coukab_lan/brain_model/model_choice_design.md); the chosen model is
**Gemma 4 E2B (QAT, `UD-Q4_K_XL` GGUF, ~2.6 GB)**, run text-only.

Requires `llama-cpp-python` and the GGUF at
`brain_model/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf` (or pass `--model-path`).
Download it:

```bash
curl -L -o brain_model/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf \
  https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf
```

When the model file is present, the web page's **Assistant** chat box appears
automatically and **streams** the reply (showing tool activity like
"⚙ using vacuum…" while it acts). The model controls lights, vacuum, air
purifier, camera, and the wall panel via tools.

Since the model is run in text-only mode on the low-power CPU, it uses the OpenCV-powered `look_around` brain tool (which reports lighting levels, face/person presence, and scene change vectors) to "sense" the room context textually, keeping inference light and fast.

It is **single-shot**: one command → one answer, with **no memory** of past
messages. That's intentional — it keeps the prompt prefix constant so the RAM
prompt cache makes each command faster on the low-power CPU.

```bash
# List the device tools that get registered
venv/bin/python brain_test.py --list-only

# Ask the assistant something — it will call device tools as needed
venv/bin/python brain_test.py --prompt "Check purifier PM2.5 and vacuum battery."
```

The system prompt lives in `system.prompt`. The agentic tool protocol and the
streaming chat endpoint (`/api/chat/stream`) are described in
[brain_model/model_choice_design.md](file:///Users/divar/Coukab/coukab_lan/brain_model/model_choice_design.md).

---

## Tests

```bash
venv/bin/python -m unittest discover -s tests -v
```

---

## Project layout

| File / Folder | Role |
| --- | --- |
| [main.py](file:///Users/divar/Coukab/coukab_lan/main.py) | Entry point — keypad event loop, action controller, starts the web server |
| [web_server.py](file:///Users/divar/Coukab/coukab_lan/web_server.py) | Standard-library HTTP server: JSON API + serves the UI |
| [web/](file:///Users/divar/Coukab/coukab_lan/web) | Front-end web application (index.html, style.css, app.js, PWA manifest, service worker) |
| [yeelight_bulb_utils.py](file:///Users/divar/Coukab/coukab_lan/yeelight_bulb_utils.py) | Yeelight bulb control, scenes, party dance, discovery |
| [xiaomi_vacuum_utils.py](file:///Users/divar/Coukab/coukab_lan/xiaomi_vacuum_utils.py) | Xiaomi vacuum (MIoT) control and status |
| [xiaomi_airpurifier_utils.py](file:///Users/divar/Coukab/coukab_lan/xiaomi_airpurifier_utils.py) | Xiaomi air purifier (MIoT) control and status |
| [miot_base.py](file:///Users/divar/Coukab/coukab_lan/miot_base.py) | Shared MIoT connection management and caching base class |
| [tapo_camera_utils.py](file:///Users/divar/Coukab/coukab_lan/tapo_camera_utils.py) | Tapo camera RTSP capture, OpenCV room analysis, and brain tools |
| [brain.py](file:///Users/divar/Coukab/coukab_lan/brain.py), [brain_test.py](file:///Users/divar/Coukab/coukab_lan/brain_test.py) | Optional local LLM agent, tool calling pipeline, and mock tests |
| [brain_model/](file:///Users/divar/Coukab/coukab_lan/brain_model) | Contains the Gemma 4 LLM weights and model choice design doc |
| [tooling.py](file:///Users/divar/Coukab/coukab_lan/tooling.py) | Shared decorators (`@brain_tool`) for registering LLM function tools |
| [tuch_controller/](file:///Users/divar/Coukab/coukab_lan/tuch_controller) | ESP32-S3 wall touch panel firmware (v1: classic design; see `HARDWARE.md`) |
| [tuch_controller_v2/](file:///Users/divar/Coukab/coukab_lan/tuch_controller_v2) | ESP32-S3 wall touch panel firmware (v2: Liquid-Glass design; see `DESIGN.md`) |
| [tuch_controller_utils.py](file:///Users/divar/Coukab/coukab_lan/tuch_controller_utils.py) | Server-side panel helpers: alerts, RGB565 rendering, status flattening, panel discovery |
| [moments/](file:///Users/divar/Coukab/coukab_lan/moments) | Captured camera photos (moments) and thumbnails |
| [SUGGESTIONS.md](file:///Users/divar/Coukab/coukab_lan/SUGGESTIONS.md) | Feature suggestions roadmap (Web, Server, Security, Reliability, etc.) |
| [CAMERA_AI_SUGGESTIONS.md](file:///Users/divar/Coukab/coukab_lan/CAMERA_AI_SUGGESTIONS.md) | Future vision LLM, CV-motion detection, and retention policy suggestions |
| [bulb.json](file:///Users/divar/Coukab/coukab_lan/bulb.json), [.env](file:///Users/divar/Coukab/coukab_lan/.env) | Your device configuration |
| [coukab-lan.service](file:///Users/divar/Coukab/coukab_lan/coukab-lan.service) | systemd unit |
