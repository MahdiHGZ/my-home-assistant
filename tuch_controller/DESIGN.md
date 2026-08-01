# tuch_controller — UI Design Notes

The firmware pairs a proven backend — FreeRTOS HTTP worker, SSE sync, touch +
calibration, sleep ladder, OTA, the whole `/api/panel/*` contract — with a
compact, **flat dark "HTML-inspired" UI**: solid rounded panels with a thin
border and a per-device accent color, a Dynamic Island tab along the top edge,
and big touch targets sized for a low-sensitivity resistive panel. For wiring,
build steps, server API, and troubleshooting see `HARDWARE.md`.

> **Renderer note.** The look is drawn by the `softPanel` / `drawHtmlButton`
> path (the `UI_*` palette). The older `glass*` primitives (`glassPanel`,
> `glassBody`, `glassCircle`, `glassRing`) are **legacy and currently unused** —
> `drawBtn()` calls `drawHtmlButton()` first, which handles every button and
> returns early. See `SUGGESTIONS.md` (§2) for the "revive glass vs. keep flat"
> decision. This document describes what actually renders.

## The look

Flat, modern, dark. Every control is a `softPanel` — a filled rounded rect with
a 1px border (`softPanel(x,y,w,h,r,border,fill)`). Depth and state come from
**color**, not gradients:

- **Resting**: near-black panel fill (`UI_PANEL` / a faint white tint) with a
  subtle `UI_BORDER`.
- **Pressed**: brighter fill (`UI_PANEL_HI`).
- **Selected / active**: accent-tinted fill (`uiTint(accent, 28)`), an
  accent-colored border (`uiTint(accent, 105)`), and accent-colored text.

Each domain has a signature accent so a glance reads the page: **lights → pink**,
**vacuum → sky blue**, **purifier → emerald**, **camera → purple**,
**settings/night → zinc**, plus **amber** for warmth/brightness and **rose** for
stop/capture. All are RGB565 constants (`UI_*`) kept cheap for the ESP32/ILI9341.

## Palette (`UI_*`, RGB565)

| Token | Use |
|---|---|
| `UI_BG` / `UI_BG2` | app background / pure black |
| `UI_PANEL` / `UI_PANEL_HI` | panel fill / pressed fill |
| `UI_BORDER` / `UI_BORDER_2` | resting border / stronger border |
| `UI_TEXT` / `UI_MUTED` / `UI_DIM` | primary / secondary / faint text |
| `UI_SKY` `UI_EMERALD` `UI_AMBER` `UI_ROSE` `UI_PINK` `UI_PURPLE` `UI_ZINC` | accents |

`uiTint(color, a)` alpha-blends an accent over `UI_BG` (via `blend565`) to get
the tinted fills/borders. A separate light theme flips `COL_*` at runtime
(`applyTheme`), NVS-persisted.

## Dashboard tiles (MAIN)

`drawDashboardTile()` renders the six MAIN tiles (LIGHTS / VACUUM / PURIFIER /
CAMERA / SETTINGS / NIGHT). Each tile has:

- a **glyph** top-left in the device accent,
- a **name + live value** at the bottom (`6/6 on`, `Charged`, `12 ug/m3`,
  `3 saved`, `60s sleep`, `Standby`),
- a **glow border + accent status dot** when the device is active (lights on,
  purifier on), otherwise a plain border and a dim dot,
- special badges: the vacuum shows a **battery pill** (`100%`).

**State-reactive bulb**: the LIGHTS glyph glows in the *live* bulb color
(`aliveR/G/B` from `updateAliveTint()` — amber for warm, icy blue for cool,
etc.) when lights are on, and mutes when off/offline.

**Per-bulb color grid** (LIGHTS tile): a 2×3 dot grid (`drawBulbGrid()`) shows
each individual bulb's current color, laid out **column-major** so the positions
map to the physical layout `I1 I4 / I2 I5 / I3 I6` (order from
`yeelight_bulb_utils.py`). Colors arrive in the flat status payload as the
`bulb_rgb` field — one `RRGGBB` per bulb in sorted-name order, dimmed by each
bulb's brightness; an off bulb is `000000` and renders as an empty ring. The grid
replaces the single status dot on this tile and refreshes live on SSE updates.

## Buttons & selection

- `softPanel(...)` — the base for every button, card, and status strip.
- `drawCompactButton(...)` — the small labelled buttons (modes, fans, vacuum
  secondary row); shows the accent fill/border/text when `active`.
- Round "key" glyph buttons (BACK, purifier power) are still rounded panels with
  a centered icon.
- Selection is always the same language: **accent fill + accent border + accent
  text**, so the active mode/fan/state is obvious at a glance.

**Scene icons** (MODES page): each scene tile carries a vector glyph — COOL
snowflake, WARM sun, SUNSET dome-over-horizon, SLEEP moon, LOVE heart, MOVIE film
— drawn in that mode's representative hue (`modeVibe565()`: COOL blue, WARM amber,
SUNSET orange, SLEEP violet, LOVE pink, MOVIE purple), so a scene is recognizable
by shape *and* color, not just its label. All glyphs live in `icons.h`, a shared
vector-icon library (drawn from GFX primitives, centered on `cx/cy`).

## Dynamic Island (top-edge tab)

A matte-black tab that **hangs from the top wall**: its top edge is flush with
`y=0` and only the **bottom corners are rounded**, so it reads as a notch
bulging down — drawn per-row in `islandShell()`. It owns all ambient info
(`drawIsland` / `islandTick`), and there is no separate header bar:

- **Resting content**: the clock on MAIN, the page title on sub-pages (bold).
- **Connection dot** (left): green = wifi + server stream · yellow = wifi only ·
  red = no wifi · grey = wifi off (`connColor`).
- **In-flight dot** (right): accent dot while an HTTP request is queued/running.
- **Toasts/alerts morph the pill**: `drawToast` / `drawToastAlarm` swap the
  centre text (errors in red) without touching the shell or dots, then it
  reverts to the clock/title after `TOAST_HOLD_MS`.

The device IP is available under SETUP → INFO. Geometry knobs: the `ISL_*`
defines.

## Background

`drawBackground()` fills the screen row-by-row with `bgRowColor()` — a subtle
static **dark-navy top glow fading to black** (a low-alpha `blend565` that is
strongest behind the island and vanishes toward the bottom). It's a pure
function of the row, so overlays that need a flat erase use `COL_BG` directly.

## Layout & touch

Gutters are 5 px and every target is enlarged for the low-sensitivity resistive
panel (dashboard tiles ~94×84, action buttons ≥44 tall, brightness/display bars
are full pills). Sub-page content starts at `y≈46`. `TOUCH_MIN_PRESSURE` is 250.

**BACK** is a small rounded button in the top-left, set *above* the content
(with a gap) so a tap on it can't spill into the row below. `HIT_SLOP` is **8 px**:
with big targets and 5 px gutters, a larger slop bled one button's hit-zone into
its neighbour.

## DEVICE info (SETUP → INFO)

`showDeviceInfo()` is a full-screen label/value readout: device/OTA name,
firmware build date, Wi-Fi SSID, IP, gateway, MAC, signal (dBm + quality),
server `host:port`, stream status, uptime, free/total RAM, chip + cores, flash
size, and display resolution. Tap anywhere to close.

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

## Tuning the look

- **Palette**: the `UI_*` constants near the top of the sketch (and the light
  theme in `applyTheme()`).
- **Panels**: `softPanel()` (corner radius, border/fill) and `uiTint()` (accent
  blend strength for selected/active fills and borders).
- **Accents**: `modeAccent()` (per-mode UI accent) and `modeVibe565()` (the
  scene-icon hue / living bulb tint).
- **Dashboard**: `drawDashboardTile()` (per-device accent, glyph, value, glow).
- **Icons**: `icons.h` — the shared vector-icon library (all `icon*()` glyphs).
  Add new controls' glyphs here rather than inline in the sketch.
