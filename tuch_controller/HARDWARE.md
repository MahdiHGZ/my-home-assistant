# tuch_controller — Hardware & Setup Reference

Everything needed to wire, flash, configure, and debug the Coukab LAN touch
panel. Supersedes the old `esp32s3_ili9341_xpt2046_handoff.md`.

---

## 1. Hardware

| Part | Detail |
|---|---|
| MCU | ESP32-S3 module (dual core; HTTP runs on core 0, UI on core 1) |
| Display | ILI9341 SPI TFT, 320×240, landscape (`tft.setRotation(1)`) |
| Touch | XPT2046 resistive controller, shared SPI bus |
| Logic level | **3.3 V only** — never feed 5 V into GPIO |

Display and touch share `SCK/MOSI/MISO` but **must** have separate
chip-select lines. All grounds common.

## 2. Wiring

### TFT (pin label variants → ESP32-S3)

| Display pin (any label) | GPIO | Define |
|---|---:|---|
| `VCC` | 3V3 | — |
| `GND` | GND | — |
| `SCK` / `CLK` / `SCL` | 12 | `TFT_SCK` |
| `SDI` / `MOSI` / `SDA` / `DIN` | 11 | `TFT_MOSI` |
| `SDO` / `MISO` | 13 | `TFT_MISO` |
| `CS` | 10 | `TFT_CS` |
| `DC` / `D/C` / `RS` / `A0` | 9 | `TFT_DC` |
| `RST` / `RESET` / `RES` | 14 | `TFT_RST` |
| `LED` / `BL` / `BACKLIGHT` | 21 | `TFT_BL` |

> **Backlight must be on GPIO21, not tied to 3V3** — the firmware PWM-drives
> it (full / night-dim / idle-dim / off).

### XPT2046 touch

| Touch pin | GPIO | Define |
|---|---:|---|
| `T_CLK` | 12 | shared `TFT_SCK` |
| `T_DIN` | 11 | shared `TFT_MOSI` |
| `T_DO` | 13 | shared `TFT_MISO` |
| `T_CS` | 8 | `TOUCH_CS` |
| `T_IRQ` | 7 | `TOUCH_IRQ` (used as a cheap wake pre-check) |

SPI bus object: `SPIClass tftSPI(FSPI)`, initialised
`tftSPI.begin(TFT_SCK, TFT_MISO, TFT_MOSI, TFT_CS)`.

## 3. Touch mapping & calibration

Empirically validated on this panel — change with care:

- `tft.setRotation(1)` + `ts.setRotation(0)` + `TOUCH_MAP_MODE 5`
  (screen X tracks raw touch Y, screen Y tracks raw X):
  ```cpp
  mappedX = map(p.y, tsMaxY, tsMinY, 0, 320);
  mappedY = map(p.x, tsMinX, tsMaxX, 0, 240);
  ```
- Defaults `TS_MINX/MAXX/MINY/MAXY = 250/3800/250/3800` live in `config.h`
  but are only the fallback: **SETUP → CALIBRATE** (or hold the COUKAB title
  ~3 s) runs a 4-point calibration and stores the result in NVS namespace
  `tcal`, which wins over the defaults on every boot.
- Anti-ghosting: pressure must exceed `TOUCH_MIN_PRESSURE` (250) and the
  same button must be hit on two consecutive 25 ms polls.
- Hit-slop: touches up to `HIT_SLOP` (8 px) outside a button still land.

## 4. Firmware build (Arduino IDE)

Sketch folder `tuch_controller/` with tabs:

| File | Contents |
|---|---|
| `tuch_controller.ino` | all logic |
| `config.h` | pins, palette, timing, night hours, timezone |
| `secrets.h` | Wi-Fi SSID/password, server IP/port — keep private |

- Board: an ESP32-S3 profile matching the module; ESP32 Arduino core
  **≥ 2.0.5** (needed for `analogWrite` PWM).
- Libraries (Library Manager): `Adafruit ILI9341`, `Adafruit GFX Library`,
  `Adafruit BusIO`, `XPT2046_Touchscreen`. (`WiFi`, `HTTPClient`,
  `ArduinoOTA`, `Preferences` ship with the core.)
- After the first USB flash, the panel appears as **network port
  `coukab-panel`** (Tools → Port) for OTA re-flashing.

Settings stored in NVS: touch calibration (`tcal`), screen timeout and
night-mode toggle (`cfg`). They survive re-flashing.

## 5. Server interface

The panel talks to `web_server.py` (default `:8080`) on the machine running
`main.py`. **`API_HOST` in `secrets.h` must be that machine's LAN IP — never
"localhost"** (on the ESP32 that means the ESP32 itself). Give the server a
static DHCP lease so the IP doesn't drift.

Used endpoints:

| Endpoint | Use |
|---|---|
| `GET /api/panel/status` | flat ~250 B status (bulbs, vacuum, purifier, moments count, server hour) |
| `GET /api/events` | SSE; `status` frames trigger a status refetch, `alert` frames an alert fetch — payloads discarded |
| `GET /api/panel/moment.rgb565?w=&h=[&name=]` | camera photo as raw big-endian RGB565, `w*h*2` bytes, streamed straight to the TFT |
| `GET /api/panel/alert` | current alert metadata (`id` for dedupe, text, level) |
| `GET /api/panel/alert.rgb565?w=&h=` | server-rendered alert popup, same RGB565 format |
| `POST /api/lights/action` `/api/lights/control` | modes, all-off, color (`#rrggbb`), brightness |
| `POST /api/vacuum/action` | sweep / stop / dock / pause / find_me |
| `POST /api/purifier/action` | power / mode / fan |
| `POST /api/camera/capture` | `{"flash": bool}`; flash path can take ~90 s server-side (panel waits 60 s) |

Success = HTTP 200 **and** `"ok": true` in the body. All HTTP runs on a
FreeRTOS worker; the UI never blocks (the only synchronous calls are the
photo preview and the alert popup blit).

**Alerts:** `POST /api/panel/alert {"text": "...", "level": "info"|"alert"}`
(or the web UI's "Panel alert" card, or `python tuch_controller_utils.py
--alert "..."`) pops a popup on the panel: the popup card is rendered by the
*server* (cv2 → RGB565, modern rounded dark-navy card, ASCII text only), the
panel wakes its screen if dark, blits it, and adds a local CLOSE button. Tap
anywhere to dismiss. Alerts dedupe by id; one alert is kept at a time.

The server side of all panel features lives in **`tuch_controller_utils.py`**
(alerts, RGB565 rendering, status flattening, discovery); `web_server`
delegates to it. The panel tags its SSE request with `X-Coukab-Panel: 1` so
the server learns its IP — `find_panel_ip()` resolves `coukab-panel.local`
(mDNS) or falls back to that last-seen IP.

Connection dot (Dynamic Island, left, every page): **green** wifi+stream OK ·
**yellow** wifi OK, server unreachable · **red** no wifi · **grey**
wifi disabled. Offline behavior: pages stay usable, banners/toasts say
exactly what's wrong ("no wifi" / "server offline" / "device not
responding"), and sync retries automatically (SSE every 5 s; first snapshot
every 30 s).

## 6. Power / backlight policy

- Full brightness on activity; dims at half the screen timeout; off at the
  timeout (30/60/120 s, SETUP page). Waking touch lands on MAIN and is
  swallowed.
- Night mode (SETUP toggle): between `NIGHT_START_HOUR`–`NIGHT_END_HOUR`
  (default 22–07, timezone `TZ_OFFSET_SEC` in config.h) "full" is a lower
  PWM level. Clock from NTP, falling back to the server-reported hour.

## 7. Troubleshooting

| Symptom | Check |
|---|---|
| White screen | TFT SPI wiring, `TFT_CS/DC/RST`, power |
| Black screen | `VCC`/`GND`, backlight on GPIO21 actually driven |
| Touch offset / wrong button | run SETUP → CALIBRATE; confirm rotation pair + map mode 5 unchanged |
| One button triggers another | swapped `T_DIN`/`T_DO`, stale calibration |
| Touch dead | `T_CS`→8, `T_IRQ`→7, shared SPI pins, `ts.begin(tftSPI)` |
| Wi-Fi won't connect | 2.4 GHz network, credentials in `secrets.h`, antenna clearance |
| Yellow conn icon | server not running / wrong `API_HOST` / firewall on :8080 |
| Cards say "no server" | start `main.py`; panel recovers automatically |
| OTA port missing | panel and computer on same LAN; re-flash once over USB |
