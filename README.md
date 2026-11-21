# Coukab LAN

Local-network home-automation controller. A numeric keypad (read via evdev)
**and** a web dashboard drive Yeelight bulbs,
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

The deployment lock supports CPython 3.11 and 3.12 on Linux. Install a C compiler, Python development headers and Linux input headers for packages that build native extensions. The exported `requirements.txt` installs the locked runtime. For a `uv` deployment, run `UV_PROJECT_ENVIRONMENT=venv uv sync --locked --no-dev` to match the systemd unit's Python path. Add `--group llm` to install the optional model runtime.

Update dependencies by editing `pyproject.toml`, running `uv lock --upgrade`, and regenerating the pip file with `uv export --locked --no-dev --no-emit-project --no-hashes --no-annotate --no-header -o requirements.txt`. Commit both files after the clean-install CI passes.

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
device on the **same Wi-Fi / LAN**. The default trusted-LAN mode opens directly.

**1. Make sure both devices are on the same network.** Your phone/laptop and the
machine running `main.py` must be on the same router/Wi-Fi.

**2. Find the address.** When `main.py` starts it prints the URL, e.g.:

```
Web interface: http://0.0.0.0:8080  (open http://192.168.1.42:8080 on your phone).
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

For example `http://192.168.1.42:8080`. The dashboard opens in your browser.

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

### Access and firmware updates

On a trusted home LAN, leave the four `COUKAB_*_PASSWORD` settings empty. The server listens on all interfaces by default. Use `--web-host 127.0.0.1` to bind to loopback.

To require authentication, set `COUKAB_BROWSER_PASSWORD` in `.env` and restart the service. Log in as `browser` using the browser's HTTP Basic prompt. Authentication covers the dashboard, API, streams, and camera files. Basic credentials travel with requests, so use an HTTPS reverse proxy on an untrusted network. Direct requests can use the local interface IP, localhost, the system hostname, or the configured mDNS name. For a reverse proxy or custom DNS name, set `COUKAB_ALLOWED_ORIGINS` to the exact comma-separated external origins and restart. These origins authorize additional hostnames and determine which browser origins can submit changes.

The `panel` username and `COUKAB_PANEL_PASSWORD` grant access to panel status, events, panel images, device controls, capture and the panel's own action receipts. Use the `browser` account for administration, camera policy and MQTT sharing. The stock touch firmware uses trusted-LAN mode. Enabling authentication requires an authenticating intermediary for that firmware. To rotate either password, put the old value in its `_PREVIOUS` setting, set the new current password, restart, migrate clients, then clear the previous value and restart again.

New camera images and household databases are created with owner-only access. When upgrading a shared Linux host, check the existing household database (including any SQLite sidecars), `.env` and moments directory. Restrict their permissions to the account running Coukab if they were created under a broader umask.

Firmware OTA starts only when the sketch has a non-empty `OTA_PASSWORD` defined in `secrets.h`. USB flashing works with OTA disabled.

### Readiness, retries and household data

- `GET /api/health` checks HTTP liveness. `GET /api/ready` reports worker progress, queue depth and age, per-device last success, event stream pressure, and free space for snapshots and household state. An unavailable optional device is reported separately. Full queues, stalled workers and low storage return HTTP 503.
- Physical control and routine-run requests accept an `Idempotency-Key` of 1–128 ASCII letters, digits, dots, underscores or hyphens. Reuse one key for retries of the same request. A key reused with a changed payload returns 409. A 202 response includes a status URL to poll. Receipts stay in memory for ten minutes after completion, up to 1,024 keys per service instance. Restarting clears HTTP receipts.
- The web dashboard can save and run up to 32 routines of 1–16 steps. Each routine can have a daily time in the server's local timezone. The scheduler claims a routine once per local date. A repeated clock hour runs once, and a skipped hour or a service outage skips the missed run. An interrupted scheduled run retains its claim, and its last step is marked unconfirmed. Inspect **Recent runs** for per-device results.
- `COUKAB_STATE_DB` sets the SQLite path for routines, recent runs and action history. The default is `coukab.sqlite3` in the service working directory. The service retains the latest 1,000 action records and up to 24 hours of minute-level air readings. Expand **Air quality over 24 hours** for hourly averages. `GET /api/history` also returns the latest 100 action records with source, duration and outcome.

Open **S20 advanced settings** to read the active map's cleaning profiles, mop route, double pass and do-not-disturb settings. **Verify support with current settings** resubmits the settings already on the vacuum and checks the readback. Editing becomes available for that firmware when verification succeeds. DND editing also requires a readable device timezone so Coukab can preserve it. The purifier's filter and motor detail view reports the reading time and uses the detected model's MIoT properties. [S20 MIoT spec](https://home.miot-spec.com/spec/xiaomi.vacuum.d106gl) · [4 Lite MIoT spec](https://home.miot-spec.com/spec/zhimi.airp.rmb1)

In **Vacuum and air purifier routine**, choose a mode and settling period, review the preview, and enable the routine. It observes the S20 locally, including cleanings started from Xiaomi Home. If a purifier is offline during cleaning, Coukab waits for a usable reading before starting. After cleaning, it waits for the configured interval and checks each setting before restoring it. A missed vacuum reading pauses the decision until the device reports again. Settings that were changed manually stay as they are. The routine and any pending restoration use the household database, so disabling the routine or restarting the server retains settings still awaiting restoration.

---

## What's on the web page

A live, high-performance dashboard featuring a modern minimal glassmorphic design, fluid typography, and responsive layouts:

*   **Real-time Synchronization (SSE):** Utilizes Server-Sent Events (`/api/events`) to stream status snapshots instantly to all connected clients when changes occur (including keypad actions), with automatic fallback to 5s polling.
*   **Optimistic UI & Low Latency:** Buttons dim during requests, and purifier toggles transition optimistically for immediate visual feedback.
*   **Mobile Viewport Optimization:** A floating bottom tab bar navigation dynamically partitions controls into *Home*, *Lights*, *Devices* (Vacuum + Purifier), *Camera*, and *Khatoon Chat* views to avoid scrolling fatigue.
*   **Desktop Viewport Adaptation:** Displays a unified dashboard view with a sticky sidebar on the left and a responsive multi-column layout on the right.
*   **Progressive Web App (PWA):** Includes PWA support (`manifest.webmanifest`, `icon.svg`, `sw.js`) with a service worker that revalidates the dashboard on load and caches the shell for offline access. "Add to Home Screen" launches the UI in full-screen.
*   **A11y & Localization:** Features a full English/Farsi localization toggle (FA/EN button, RTL layout alignment, translated labels and device states) persisted in `localStorage`, along with `aria-label` tags for screen readers.
*   **Lights** — tap individual bulbs (I1–I6) to target a subset, scenes
    (cool/warm white, sunset, sleep, romantic, movie), brightness slider (live-throttled at $\ge$350ms to respect LAN rate limits), full RGB color picker, white-temperature slider, party mode + patterns, color cycle, random colors, targeted on/off, all-off, and undo.
*   **Vacuum** — sweep / mop / both, pause, stop (guarded by a 600ms hold gesture), return-to-dock, find-me, suction and water levels, speaker volume, and a manual-drive D-pad (with press-and-hold repeating). Features per-room cleaning chips ("Clean selected rooms") and a collapsible Maintenance panel for consumables (filters, brushes, mop) warning if wear is under 30%.
*   **Air purifier** — power, modes, fan level, favorite speed, ionizer / child-lock
    / buzzer toggles, screen brightness, and live PM2.5 / temperature / humidity.
*   **Camera** — capture a moment (with or without a flash blink) and browse recent
    shots. Add captions to find a retained photo by name or text. Use **Preview** for a fresh frame without adding it to the gallery. Camera settings include local-time privacy intervals, an opt-in motion watcher and an interval recorder. The recorder exports up to 60 ordered JPEG frames in a ZIP. A camera reporting physical privacy mode stays private. A failed privacy check blocks capture. Motor controls appear only when the camera reports support.
*   **Household overview and rooms** — check active cleaning and faults, run a favorite scene, or open a device’s advanced controls. Group bulbs and save their current settings as a scene. Assign devices to rooms and optionally place them on a locally stored floor plan. The dashboard includes English and Farsi labels, keyboard shortcuts, and a printable keypad reference.
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

### Manual vacuum drive clients

For a new directional hold, take `drive_epoch` from the latest `/api/status` snapshot.
Send it as `epoch` together with a unique `session` and increasing integer `seq` on
each `remote` command for that hold. Keep that original epoch throughout the hold.
A response with `ignored: true` ends the hold. Use a fresh snapshot or response epoch
for the next deliberate press. Release sends direction 5 immediately. An unscoped
direction 5 stops all holds, and the server exits remote mode after five idle seconds.

## Touch-controller wall panel (Optional)

An ESP32-S3 wall-mounted touch panel can display real-time status and trigger actions on the LAN. The firmware in [tuch_controller/](tuch_controller) features an Apple-style **Liquid-Glass** design — frosted translucency, a Dynamic Island notification bar, and an ambient background that responds to active bulb colors (see [tuch_controller/DESIGN.md](tuch_controller/DESIGN.md) and [tuch_controller/HARDWARE.md](tuch_controller/HARDWARE.md) for wiring and setup).

It syncs with the backend server via Server-Sent Events (SSE) and requests server-rendered RGB565 images for alert and camera preview rendering.

---

## LLM brain — "Khatoon" (optional)

A local, CPU-only LLM that **chats on the web page and controls the house**
through the same device tools. The configured model for the low-power server is
**Gemma 4 E2B (QAT, `UD-Q4_K_XL` GGUF, ~2.6 GB)**, run text-only.

Requires `llama-cpp-python` and the GGUF at
`brain_model/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf` (or pass `--model-path`).
Download it:

```bash
mkdir -p brain_model
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

The system prompt lives in [system.prompt](system.prompt). The streaming endpoint is `/api/chat/stream`.

---

## Tests

```bash
venv/bin/python -m unittest discover -s tests -v
```

---

## Project layout

| File / Folder | Role |
| --- | --- |
| [main.py](main.py) | Entry point — keypad event loop, action controller, starts the web server |
| [web_server.py](web_server.py) | Standard-library HTTP server: JSON API + serves the UI |
| [web/](web) | Front-end web application (index.html, style.css, app.js, PWA manifest, service worker) |
| [yeelight_bulb_utils.py](yeelight_bulb_utils.py) | Yeelight bulb control, scenes, party dance, discovery |
| [xiaomi_vacuum_utils.py](xiaomi_vacuum_utils.py) | Xiaomi vacuum (MIoT) control and status |
| [xiaomi_airpurifier_utils.py](xiaomi_airpurifier_utils.py) | Xiaomi air purifier (MIoT) control and status |
| [miot_base.py](miot_base.py) | Shared MIoT connection management and caching base class |
| [tapo_camera_utils.py](tapo_camera_utils.py) | Tapo camera RTSP capture, OpenCV room analysis, and brain tools |
| [brain.py](brain.py), [brain_test.py](brain_test.py) | Optional local LLM agent, tool calling pipeline, and mock tests |
| `brain_model/` (created locally) | Contains the Gemma 4 LLM weights and model choice design doc |
| [tooling.py](tooling.py) | Shared decorators (`@brain_tool`) for registering LLM function tools |
| [tuch_controller/](tuch_controller) | ESP32-S3 wall touch panel firmware (Liquid-Glass design; see `DESIGN.md` / `HARDWARE.md`) |
| [tuch_controller_utils.py](tuch_controller_utils.py) | Server-side panel helpers: alerts, RGB565 rendering, status flattening, panel discovery |
| `moments/` (created on capture) | Captured camera photos (moments) and thumbnails |
| [GitHub Issues](https://github.com/MahdiHGZ/my-home-assistant/issues) | Project bugs and feature requests, labeled by component and priority |
| [bulb.json](bulb.json), `.env` (copy [example.env](example.env)) | Your device configuration |
| [coukab-lan.service](coukab-lan.service) | systemd unit |

## First-run setup, demo and integrations

Open **Setup** in the dashboard to see missing configuration keys and the manual address. On LAN interfaces the server also advertises `http://coukab.local:8080/` over mDNS. Adjust `COUKAB_LOCAL_NAME` for multiple installations or set `COUKAB_MDNS=0` to disable the advertisement. After editing device credentials locally in `.env`, use **Reload device settings**. Provide complete groups. Omitted groups keep their settings and a complete group of empty values disables that device. Running device calls finish with their original transport. Change server authentication or broker settings by restarting the service.

For a hardware-free household run `uv run --locked main.py --demo --watch-assets` and open `http://127.0.0.1:8080/`. The demo uses temporary state, simulated device transports, a generated camera frame and loopback networking. The optional asset watcher refreshes open dashboard tabs when local web files change.

To share devices with Home Assistant, configure the same MQTT 5 broker on both systems, restart Coukab, then open **Setup → Home Assistant · MQTT**. Enable sharing and select individual readings and named routines. The dashboard and keypad work independently of the broker. Clearing a selection removes its discovery entry. Disable sharing and let the connected broker acknowledge the removals before removing the broker configuration. Restrict the command topics to trusted publishers using your broker's ACLs and enable TLS for remote connections.

An external door contact can trigger an existing evening routine. Share the routine in Setup, then create a Home Assistant automation with the contact changing to open as its trigger and **Button: Press** on the discovered Coukab routine as its action. Its device steps appear under **Recent runs**. The bridge ignores retained commands and stores command IDs for 24 hours to deduplicate redeliveries. [Home Assistant MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery)

When the routine worker is busy, Coukab stores up to 128 distinct MQTT presses in the household database. A press expires after one minute if it has not started. When the inbox is full, Coukab logs rejected presses. Disabling sharing or removing a routine from sharing discards its queued presses. If you edit the steps before a press starts, press the button again to run the revised routine.
