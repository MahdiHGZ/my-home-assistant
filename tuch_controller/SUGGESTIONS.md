# tuch_controller — Roadmap, Open Findings & Product Ideas

Forward-looking only: what's **left to build**, not what's already shipped.
The firmware is `tuch_controller.ino`; hardware / wiring / server-API reference
lives in `HARDWARE.md`.

**Legend** — 🌐 needs `web_server.py` / server work · ⏸ deferred (reason given)
· ⬜ open firmware change · 💡 product idea. Priority **P1 = hurts everyday use
or latent bug · P2 = noticeable · P3 = refinement · P4 = polish.**

**Contents**
1. [Design principles](#1-design-principles)
2. [Feature roadmap](#2-feature-roadmap)
3. [Open engineering findings](#3-open-engineering-findings)
4. [Product ideas (product-engineer lens)](#4-product-ideas-product-engineer-lens)
5. [Priority](#5-priority)

---

## 1. Design principles

**Accessibility first.** The resistive panel has poor sensitivity, so: big
targets (≥56×38, most ≥66 px) with 8 px hit-slop, few elements per screen, 1-D
drag bars instead of 2-D pickers, send-on-release, real FreeSansBold labels,
pressure threshold + two-sample confirm against ghost touches, and on-device
4-point calibration (NVS).

**Offload to the server what isn't UI.** The ESP32 draws and reads touch;
parsing, aggregation, and image work belong on the server (`/api/panel/*`, all
additive). **Rejected** offloads: full server-side screen rendering
(150 KB/frame kills touch latency), touch mapping (needs 40 Hz, zero latency),
toast text composition (nothing to gain).

**Honest offline degradation.** Rendering/input never needs the network.
Missing data and failed actions say exactly why ("no wifi" / "server offline" /
"device not responding"), fail fast when the server is known-down (2.5 s
probes), and recover automatically (SSE retry 5 s, first snapshot retry 30 s).

---

## 2. Feature roadmap

### Lighting
- **More scene modes** (P2): the six modes (`MODE_LABELS` / `MODE_KEYS` /
  `MODE_MATCH` in `pages.h`) are compiled in. Add high-value scenes — **READING**,
  **FOCUS**, **CANDLE**, **DAWN** (a slow wake-up ramp), **PARTY** — and, better,
  serve the list from `/api/capabilities` so modes become data, not code (pairs
  with capability discovery below). The MODES page already scrolls to a 3×2 grid;
  a longer list wants paging or a scrollable column.
- **Mode color swatches** (P3): tint each MODES / quick-mode tile with that
  mode's representative color (reuse the `updateAliveTint()` vibe map) so a mode
  is recognizable at a glance instead of by label alone.
- **Kelvin (white-temperature) strip** (P3): a dedicated warm↔cool CCT bar on
  LIGHTS/COLOR, distinct from hue — most everyday "just make it warmer/cooler"
  adjustments aren't a hue pick. Reuse the gradient-bar widget.
- **Save current as a favorite scene** (P3): long-press a mode slot to store the
  live color + brightness as a user scene (NVS, or server-side once
  `/api/panel/scene` exists), recalled with one tap.
- **Per-bulb targeting page** (P2): I1–I6 grid (names from `/api/capabilities`),
  tap-to-select a subset, then color/brightness apply to `targets`.
- **Party page** (P3): toggle + pattern prev/next (`party_toggle`,
  `party_pattern`), pattern name from status.
- **Color-cycle controls** (P3): start / next / prev mapped to existing actions.
- **Undo button** (P3) — `{"action":"undo"}`, fits the LIGHTS top bar (mind the
  Dynamic Island along the top edge).

### Vacuum
- **Rooms page** (P2): list from `/api/vacuum/rooms`, tap = `room_sweep`.
- **Suction / water / volume** rows (P3): reuse the gradient bar widget.
- **Manual drive pad** (P3, `remote` action) — only if latency feels OK; test
  first.

### Purifier
- **Favorite-speed slider** (P3, `favorite_speed` 200–2300 step 50) — reuse the
  gradient bar.
- **Anion / child-lock / buzzer / screen-brightness** toggles (P3).

### Camera
- **Browse older moments** (P3): the thumbnail endpoint already takes `name`;
  add prev/next paging on the preview overlay.
- **Delete moment** from the panel (P3, `/api/camera/delete`) with a long-press
  confirm; hide the action when `delete_protected` (no way to prompt for a
  password on the panel).

### Main / ambient
- **Date alongside the clock** (P3) — the clock ships; add the date line.
- **Ambient alert** (P3): PM2.5 over a threshold → AIR card sub flashes warm.
- **Ultra-dim screensaver clock** (⏸): instead of backlight-off, a dim clock
  face that shifts position each minute (burn-in). Kept plain backlight-off for
  now; only revisit for an always-on-display use.

### UI, icons & visual polish
- **Richer icon set** (P2): the glyphs (`iconBulb`, `iconVacuum`,
  `iconFanBlades`, `iconGear`, …) are single-color primitives drawn from circles
  and rects — blocky and inconsistent in weight. Move to a compact **1-bit icon
  atlas in PROGMEM** (or multi-tone draws) for crisp, uniform glyphs, and scale
  up the MAIN dashboard badges. Keeps the draw cheap while looking intentional.
- **Delete the dead `glass*` renderer** (P3): the flat `softPanel` (UI_*) theme
  is now the design of record (`DESIGN.md` rewritten to match). The old `glass*`
  primitives (`glassPanel` / `glassBody` / `glassCircle` / `glassRing` /
  `glassErase`) are bypassed — `drawBtn` calls `drawHtmlButton()` first, which
  handles every button and returns early — so the glass branch in `drawBtn`
  (and the `glassErase`-based `drawCardSub` still reached by `refreshDynamic` on
  MAIN) is unreachable/inconsistent code. Remove it (or fully revive it), and
  drop the now-unused `COL_CARD*` plumbing, to cut confusion and flash.
- **State-reactive icons** (P3): the bulb glyph glows in the live bulb color,
  fan blades animate while the purifier runs, the vacuum shows a sweeping spinner
  while cleaning, and the brightness sun's rays scale with level — turns static
  icons into at-a-glance status.
- **Anti-aliased corners & edges** (P3): rounded-rect borders are hard-stepped;
  a 1px edge blend (same per-row budget as `glassBody`) would smooth them and
  reinforce the glass feel.
- **Typography pass** (P3): pair the bold value font with a lighter label
  treatment, right-align numeric readouts, add unit formatting (µg/m³, °C), and
  keep title casing consistent across pages.
- **Honest control affordances** (P2): when a device is offline its controls
  should visibly **dim + lock** instead of silently no-op'ing; pressed/holding
  states need a clearer treatment than today's subtle frost shift.

### Interaction
- **Double-tap-to-wake** (P3): require two taps to wake (guards against pocket /
  pet touches on a flat-lying panel).
- **Hold-to-repeat** on mode buttons for cycling (P4).
- **Piezo click feedback** (⏸ hardware): one GPIO + buzzer; big tactile win on a
  travel-less resistive panel.

### Power
- **Per-tier sleep controls on SETUP** (P3): the sleep *ladder* ships (light →
  deep), but its delays are compile constants — expose per-tier timeouts, a
  **day/night** split, and an **always-on / never-sleep** choice (e.g. a kitchen
  dashboard) in the UI, persisted to NVS.

### System / network
- **mDNS** (`coukab.local`, P3): zeroconf advertise on the server + `ESPmDNS`
  resolve; today's answer is a static DHCP lease.
- **Wi-Fi provisioning portal** (P2, WiFiManager-style) so credentials aren't
  compiled into `secrets.h`.
- **Multi-panel** (P3): per-panel OTA hostnames (`coukab-panel-bedroom`) before
  adding a second unit.
- **Worker watchdog** (⏸): `esp_task_wdt` on the HTTP worker — deferred because
  it conflicts with the legitimate 60 s capture POST; needs a timeout longer
  than capture or a heartbeat scheme first.
- **PlatformIO project** for CI compile checks (P3; keep the folder
  Arduino-IDE-compatible).

### Server-side companions (🌐)
- **`/api/panel/scene`** — execute a named multi-device routine in one atomic
  POST (the panel NIGHT scene currently queues three separate posts).
- **Capability discovery + API-version field** — serve modes/labels from
  `/api/capabilities` (the panel caches them in NVS) instead of compiled-in
  arrays, and add a version field to `/api/panel/status` the firmware can flag
  on mismatch. Kills the silent "highlight stopped matching" coupling.
- **Little-endian RGB565** — let the panel skip its per-pixel byte-swap on photo
  / alert blits.
- **Theme-aware alert popup** — the alert card is server-rendered RGB565 and
  always dark-navy; pass the panel's current theme so it matches light mode.
- **`/api/panel/events`** flat SSE stream — only worth it if the
  trigger-then-fetch pattern ever feels laggy.

---

## 3. Open engineering findings

Condensed from the code / UX / performance / SWE reviews — only items **not yet
done**:

- **Heap & stack hygiene** (P3): `httpGetJson` still allocates an Arduino
  `String` per fetch; watch the DIAG free-heap over days for a slow leak, and
  trim the 8 KB worker stack to its measured high-water mark.
- **Skip the full repaint on unchanged page re-entry** (P2): `drawPage()` does a
  full `fillScreen` + redraw on every nav/wake/auto-return; re-entering the same
  page with unchanged layout could reuse the `refreshDynamic()` path.
- **Lock the firmware↔server contract with a test** (P2): a host-compiled fixture
  test that feeds a captured `/api/panel/status` payload and asserts every parsed
  field — converts the substring JSON parser from "fragile but correct" to
  "guarded."
- **Host unit tests + CI compile** (P2): extract pure logic (JSON parse, touch
  mapping/hit-test, `isNightNow` hour math) for host testing; add
  `arduino-cli compile` / PlatformIO in CI so a broken build is caught before
  flashing.
- **Named layout constants + action table** (P3): replace magic coordinates with
  derived constants, and the long `handlePress` if-ladder with a
  `BtnId → {endpoint, body, okMsg, timeout}` table for the simple POST cases.
- **Split the translation unit** (⏸): break the single `.ino` into `net` / `ui`
  / `touch` / `model` tabs for navigability and to unlock host tests — structural,
  no behavior change.

---

## 4. Product ideas (product-engineer lens)

Stepping back from "what to fix" to "what would make this a product people love."
The panel already sits on a capable base — lights, vacuum, purifier, a Tapo
camera **with server-side people/face detection**, a server-pushed alert popup,
and a local LLM ("Khatoon") with device tools. Several high-value features are
mostly a matter of *connecting things that already exist*.

### Standouts (reuse capabilities already built)

- **💡 Visual doorbell / intercom (P1 flagship).** On-brand for a wall panel: a
  door event raises an alert that pops on **every** panel with a fresh Tapo
  **snapshot** and "Someone's at the door." The alert popup + RGB565 image
  pipeline already exists — this is largely wiring a door trigger →
  `/api/panel/alert` with an image. Full two-way audio needs mic/speaker
  hardware, but the *visual* doorbell ships on today's parts and is the kind of
  feature that sells the whole device. (Server + a door button/PIR.)
- **💡 "Ask Khatoon" quick-commands (P2).** The local LLM streams replies and
  drives every device via tools (`/api/chat/stream`), but the panel never uses
  it. Add an **Assistant** page with a handful of curated one-tap prompts —
  "cozy scene", "is the air OK?", "start cleaning", "goodnight" — that POST to
  the stream endpoint and show Khatoon's short reply + the action it took. No
  keyboard or mic needed; it surfaces a built feature the panel ignores.
- **💡 One-tap routines beyond NIGHT (P2).** Generalize the hard-coded NIGHT
  scene into user-defined routines ("Good morning", "Leaving", "Movie") authored
  in the web UI and surfaced as panel tiles. Builds directly on the proposed
  `/api/panel/scene`. Highest *everyday* value — most home-automation use is a
  few repeated multi-device actions.

### Make it feel personal & contextual

- **💡 Room-aware panel (P2).** Each panel stores its room (NVS, set during
  onboarding). Lights/Vacuum then default to *that room's* bulbs/zones, plus a
  one-tap "this room off." Turns a generic remote into a contextual one. (Pairs
  with capability discovery for the room→device map.)
- **💡 Adaptive home screen by time of day (P3).** Morning → AQI + temp (+ weather
  if a feed exists); daytime → lights / vacuum; evening → scenes; night → a big
  dim clock only. The idle panel always shows the most relevant thing, earning
  its place on the wall. Uses the clock/night logic already present.
- **💡 Presence wake & greeting (P3).** `tapo_camera_utils.look_around()` already
  detects people; a server "presence near panel" event over SSE could wake the
  screen from sleep and show a glanceable greeting/dashboard — hands-free. A real
  "magic moment," gated by camera placement.

### Trust, calm, and onboarding

- **💡 First-run onboarding (P2).** Today setup needs serial + docs (hidden
  title-hold calibration, IPs hand-entered in `secrets.h`). A guided first boot —
  calibrate → confirm the server is reachable → pick theme & brightness → name
  the room — makes the panel self-explanatory and cuts support burden. Pairs with
  the Wi-Fi provisioning portal.
- **💡 Quiet hours / Do-Not-Disturb (P2).** Night is already known. Suppress
  non-critical panel alerts overnight (alert `level` field already exists), so a
  bedroom panel doesn't light up at 3 a.m. for a "vacuum finished" toast. Living-
  with-the-device essential.
- **💡 Trends at a glance (P3).** A small sparkline of PM2.5 / temperature over
  the last few hours on the AIR page turns instantaneous numbers into insight
  ("air's been getting worse"). Needs the server to retain a short history + a
  tiny series endpoint.
- **💡 Reliability as visible UX (P3).** A subtle "synced 2m ago" line and
  self-heal indicator make the panel feel trustworthy at a glance (the connection
  dot is a start). Cheap; compounds confidence in the whole system.

---

## 5. Priority

| Priority | Item | Where |
|---|---|---|
| **P1** | Visual doorbell / intercom (snapshot alert to all panels) 💡 | §4 + 🌐 |
| **P2** | "Ask Khatoon" quick-commands page 💡 | §4 + 🌐 |
| **P2** | One-tap user routines (+ `/api/panel/scene`) 💡 | §4 + 🌐 |
| **P2** | Room-aware panel 💡 | §4 + 🌐 |
| **P2** | First-run onboarding + Wi-Fi provisioning portal 💡 | §4, §2 |
| **P2** | Quiet hours / DND for alerts 💡 | §4 + 🌐 |
| **P2** | Capability discovery + API-version handshake | §2 🌐 |
| **P2** | More scene modes (+ capability-served list) | §2 🌐 |
| **P2** | Richer icon set · honest control affordances | §2 |
| **P3** | Delete the dead `glass*` renderer (flat theme is design of record) | §2 |
| **P2** | Per-bulb targeting page · Rooms page | §2 |
| **P2** | Skip full repaint on unchanged re-entry | §3 |
| **P2** | JSON-contract fixture test · host tests + CI compile | §3 |
| **P3** | State-reactive icons · Kelvin strip · mode swatches · AA corners · typography | §2 |
| **P3** | Adaptive home screen · presence wake · trends sparkline 💡 | §4 + 🌐 |
| **P3** | Date on MAIN · ambient PM2.5 alert · per-tier sleep controls | §2 |
| **P3** | Save-current-as-favorite scene · mDNS · per-panel OTA · named constants | §2, §3 |
| ⏸ | Worker watchdog · TU split · piezo · little-endian RGB565 · screensaver | §2, §3 |

The biggest product leverage is in **§4**: the doorbell, Khatoon quick-commands,
and routines each turn a capability that's *already built* (camera detection,
the LLM, multi-device control) into a feature users would actually show off —
and all three are mostly server-side glue rather than new firmware.
