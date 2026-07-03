# tuch_controller — Liquid-Glass Design Notes

The firmware pairs a proven backend — FreeRTOS HTTP worker, SSE sync, touch +
calibration, sleep ladder, OTA, the whole `/api/panel/*` contract — with a
"liquid glass" visual layer: it is only the rendering primitives and how
buttons/cards are painted that make this look distinct. For wiring, build steps,
server API, and troubleshooting see `HARDWARE.md`.

## The look

Apple-style **liquid glass**: frosted, translucent panels that float over the
dark background, with **circular borders** as the signature motif — circular
icon badges on the dashboard, frosted *circles* for square buttons, and
glowing accent rings on selection.

## How "glass" works without a GPU

The ILI9341 has no alpha/blur hardware and the ESP32-S3 can't afford to read
back framebuffer pixels. So glass is faked with three cheap tricks:

1. **Software alpha blend** (`blend565`): mix a light *tint* (white on the dark
   theme, ink on light) over the solid background `COL_BG` at low opacity to get
   a frosted body. The background stays a solid fill, so the frost reads as a
   translucent sheet laid on top.
2. **Per-row tint = pure function of the row** (`glassBody(dy, h, style)`): the
   frost gets a subtle top-down *sheen* (brighter at the top edge). Because the
   color depends only on the row within a panel, any partial repaint
   (`glassErase`) can reproduce the exact same pixels — no full-screen redraw
   and no flicker. This is what keeps live values (PM2.5, battery, brightness)
   updating smoothly on top of glass.
3. **Edge highlights**: a bright 1px sheen line just inside the top border plus a
   soft light border (`glassEdge`) sell the "pane of glass" edge. Selection
   swaps the border for a double **accent ring**.

### States

| Style | Meaning | Treatment |
|---|---|---|
| `GS_NORMAL` | resting | low-opacity frost + soft light border |
| `GS_PRESSED` | finger down | denser frost (more opaque) |
| `GS_SELECTED` | active mode/fan/state | accent-tinted frost + double accent ring |

### Shapes

- `glassPanel(x,y,w,h,r,style)` — frosted **rounded** panel (most buttons, cards,
  status strips, the alert CLOSE pill).
- `glassCircle(cx,cy,rad,style)` — frosted **disc**; square-ish small buttons
  (e.g. the brightness OFF/ON keys) render as circles.
- `glassRing(cx,cy,rad)` — decorative circular **icon badge** behind the glyphs on
  the MAIN dashboard tiles (LIGHTS / VACUUM / AIR / CAMERA / SETUP).
- `glassErase(x,y,w,h,panelY,panelH,style)` — repaint a sub-band of an existing
  panel by recomputing its frost (used by card sub-text and live value tiles).

## Dynamic Island (top-edge tab)

A matte-black tab that **hangs from the top wall**: its top edge is flush with
`y=0` (the wall "cuts" the top off) and only the **bottom corners are rounded**,
so it reads as a notch bulging down — drawn per-row in `islandShell()`. It holds
all ambient info (`drawIsland` / `islandTick`):

- **Resting content**: the clock on MAIN, the page title on sub-pages (bold).
- **Connection dot** (left): green = wifi + server stream · yellow = wifi only ·
  red = no wifi · grey = wifi off (`connColor`).
- **In-flight dot** (right): accent dot while an HTTP request is queued/running.
- **Toasts/alerts morph the pill**: `drawToast` / `drawToastAlarm` swap the
  centre text (errors in red) without touching the shell or dots, then it
  reverts to the clock/title after `TOAST_HOLD_MS`.

This replaced v1's separate header (COUKAB title + IP line) and the wifi-arc
connection icon. The IP is still available under SETUP → DIAG. Geometry knobs:
the `ISL_*` defines.

## Living background

`drawBackground()` paints deep navy with a soft, bulb-colored glow at the top
(behind the island) and a fainter one at the bottom; the middle stays pure navy
(and is covered by panels). The glow color comes from `updateAliveTint()`, which
maps the reported light mode to a vibe (warm→amber, cool→icy blue, sunset→orange,
sleep→violet, love→pink, movie→purple) and uses the live hue/sat on the COLOR
page. Lights off → a faint, calm glow. The navy base is unchanged — the color is
only *added* (`bgRowColor`): a hint everywhere, a strong glow from the top
(behind the island) and a softer one from the bottom, capped so navy stays the
base. The glass panels also get a subtle bulb-color **cast** in `glassBody()`, so
the whole UI — not just the gutters — carries the lights' color and feels alive.
It's a function of row only, so the flat-`COL_BG` erases overlays still use are
unaffected; only full page draws use the glow.

## Layout & touch

Gutters are 5 px and every target is enlarged for the low-sensitivity resistive
panel (dashboard tiles 100×95, action buttons ≥56 tall, brightness/display bars
are full pills). Sub-page content starts at `y=46`. `TOUCH_MIN_PRESSURE` is 250.

**BACK** is a **circular** button in the top-left, deliberately small and set
*above* the content (with a gap) so a tap on it can't spill into the row below —
the earlier full-width back bar sat flush against the first row and cross-
triggered it. `HIT_SLOP` was also dialed back to **8 px**: with big targets and
5 px gutters, a large slop bled one button's hit-zone into its neighbour.

## DEVICE info (SETUP → DEVICE)

`showDeviceInfo()` is a full-screen label/value readout: device/OTA name,
firmware build date, Wi-Fi SSID, IP, gateway, MAC, signal (dBm + quality),
server `host:port`, stream status, uptime, free/total RAM, chip + cores, flash
size, and display resolution. Tap anywhere to close. (Replaces v1's terse DIAG.)

## Wake: no white flash

The backlight is forced **off** through TFT init and the *first full page draw*,
then switched on only once a complete frame is on the panel — both in `setup()`
(cold boot / deep-sleep touch wake) and `wakeScreen()` (light-sleep wake). This
removes the white flash; the residual delay on a deep-sleep wake is the ESP32
reboot itself (inherent to deep sleep).

## Desktop preview (render from the real code)

`python3 render_preview.py` renders every screen to `preview/*.png` **on the
computer, from the actual firmware** — no flashing, no Python re-implementation
of the UI. It builds `tools/host_preview/preview.cpp`, which `#include`s the real
`tuch_controller.ino` (and `pages.h`) compiled against desktop shims, then
calls the genuine `drawPage()` for each page and writes `tft`'s framebuffer to
PNG. All geometry/text comes from the vendored genuine Adafruit_GFX, so the
output matches the device. The screen list and layouts live in `pages.h`, shared
verbatim with the firmware. See `tools/host_preview/README.md`.

## What did **not** change

- All page/button layouts, IDs, hit-testing, and actions.
- Networking, optimistic UI, offline messaging, alerts, camera preview.
- Touch calibration, NVS settings, theme toggle, sleep/backlight policy.
- The server contract. v2 talks to the same `web_server.py` / `tuch_controller_utils.py`.

## Tuning the frost

All knobs live in `glassBody()` / the `frost*` / `glassEdge()` helpers near the
top of the sketch:

- `baseA` — resting opacity of the frost (raise for a more solid card).
- the `sheen` term — strength of the top-edge gradient.
- `glassTintColor()` — the color the frost tints toward per theme.
- `glassRing()` ring colors — the dashboard badge accent.

Both light and dark themes are supported; the tint flips automatically.
