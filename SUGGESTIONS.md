# Suggestions — Coukab LAN

Improvement ideas for the web UI/UX, `web_server.py` performance, and the rest
of the project. Grouped by area and ordered by value-for-effort inside each
group. Items marked **[quick win]** are small, low-risk changes.

---

## 1. Web UI / UX — ✅ implemented

All of section 1 has been implemented:

- **1.1 Live updates (SSE)** — `/api/events` streams status snapshots; the
  server pushes after every action (keypad presses included, via
  `Controller.on_action`) with a 0.3 s debounce and 15 s heartbeats. The UI
  uses `EventSource` and falls back to 5 s polling (with auto-reconnect) if
  the stream drops.
- **1.2 Pending + optimistic UI** — every action button dims while its request
  is in flight (`act()` handles it centrally); purifier toggles flip
  optimistically and self-correct on the next push.
- **1.3 Hidden backend features exposed** — dance-pattern picker (pill row,
  shown while party mode runs; new `set_party_pattern()` +
  `party_pattern` action), per-room cleaning (`/api/vacuum/rooms` with a
  firmware-tolerant parser; room chips + "Clean selected rooms"), consumables
  in a collapsible Maintenance panel (`/api/vacuum/consumables`, bars with
  warn <30% / bad <10%), and an undo-depth badge on the Undo button
  (disabled at 0).
- **1.4 Live brightness drag** — `input` events send a trailing-edge-throttled
  preview (≥350 ms apart, respecting the bulbs' LAN rate limit); `change`
  sends the final value.
- **1.5 D-pad press-and-hold** — hold a direction to drive (repeats every
  400 ms), release sends remote-stop; `touch-action: none` stops page scroll.
- **1.6 PWA** — `manifest.webmanifest` + `icon.svg` + `sw.js`
  (stale-while-revalidate for the shell, network-only for API/moments);
  "Add to Home Screen" now opens full-screen.
- **1.7 Safety** — "All off" shows an **Undo?** action toast; vacuum Stop
  requires a 600 ms hold (with progress fill; a quick tap shows a hint);
  haptic tick via `navigator.vibrate` where supported.
- **1.8 Chat** — the server keeps a rolling 6-turn conversation history
  (`brain.run_prompt(history=…)`), so follow-ups work; the pending bubble
  shows elapsed seconds and a "loading the model" notice on the first message.
- **1.9 A11y + i18n** — `aria-label`s on icon-only controls,
  `:focus-visible` outlines, the failing card flashes red on action errors,
  and a full **English/Farsi toggle** (FA/EN button, RTL layout, translated
  labels and device-state names, persisted in `localStorage`).
- **Camera image management** — every gallery image and the latest shot have a
  **download** button (`/moments/<name>?download=1` sends `Content-Disposition:
  attachment`) and a **hold-to-delete** button (`POST /api/camera/delete`).
  Deletes are guarded by `_resolve_moment()` (path-traversal + non-image
  rejection) and the API strips to the basename, so a request can't escape the
  `moments/` directory. Deleting pushes a fresh status to all clients via SSE.

Small follow-ups deliberately left out (worth doing later):
- Remote-mode **Exit (10)** is never sent after D-pad driving — the vacuum may
  stay in remote mode until something else commands it. Send Exit after ~5 s
  of no driving.
- Farsi covers UI labels and common device states; rarely-seen vacuum
  statuses/faults still render in English.
- The SSE initial snapshot does a full live device read per new connection —
  the TTL status cache (2.1) would make opening the page instant.

---

## 2. `web_server.py` performance

### 2.1 Cache device status server-side (biggest win)
Every `/api/status` hits all six bulbs + vacuum + purifier live. Two open
browser tabs double the device traffic; the bulbs' LAN interface is
rate-limited (~60 cmd/min) and `miio` calls take seconds.
- Add a small TTL cache (2–4 s) around `_build_status()`: concurrent and
  rapid-fire requests get the cached snapshot, a background refresh fills it.
  This also makes the UI poll feel faster (cached responses return in ~0 ms).
- Invalidate (or refresh) the cache after any successful action so the
  post-action 250 ms re-poll sees fresh data.

### 2.2 Static files: cache headers + in-memory **[quick win]**
- `index.html`/`app.js`/`style.css` are read from disk on every request and
  sent with no caching headers. Read them once at startup (or stat-check
  mtime) and send `Cache-Control: max-age=300` + `ETag`; return `304` on
  `If-None-Match`. On a Pi-class host this removes most request latency.
- Gzip them (`gzip.compress` at load time) when the client sends
  `Accept-Encoding: gzip` — `app.js` is ~22 KB → ~6 KB.

### 2.3 Thumbnails for the moments gallery
Gallery `<img>` tags load **full RTSP frames** (often 1–3 MB each, 8 at a
time) scaled down to 80 px squares — the single heaviest thing the page does
on mobile.
- Generate a ~256 px JPEG thumbnail on capture (cv2 is already a dependency)
  into `moments/.thumbs/`, serve it for the gallery, and keep the full image
  for the lightbox click-through. Backfill thumbnails lazily on first request.
- **[quick win]** meanwhile: add `loading="lazy"` + `decoding="async"` to
  gallery images and `Cache-Control: max-age=86400` to `/moments/*` responses
  (filenames are timestamped, hence immutable).

### 2.4 Don't let slow jobs block unrelated web actions
All web light-actions and the flash-capture share the single keypad worker
queue. A 90 s flash capture stalls every queued light action behind it
(their `run_action` may even time out while still queued).
- Split into two lanes: keep the serialized lane for state-shared light
  actions, but run captures on their own thread while still snapshotting via
  the lane (only the save/apply state steps need serialization).
- **[quick win]**: in `run_action`, fail fast if the queue already holds more
  than N jobs ("controller busy") instead of letting requests pile up for
  60 s each.

### 2.5 Misc server hygiene
- `_moments_summary()` stats every file in `moments/` on each status poll —
  fine at 9 files, slow at 5 000. Cache the listing and invalidate on capture.
- Add a `timeout=` to the `socket` level (`httpd.timeout`) and
  `SO_REUSEADDR` (ThreadingHTTPServer sets it, but verify on restart-loops
  under systemd).
- Log one structured line per action (`action`, `source=web|keypad`,
  `duration_ms`, `ok`) — makes "why was Tuesday slow" answerable.

---

## 3. Other parts

### 3.1 Security (worth doing even on a trusted LAN)
- Optional shared-secret: a `WEB_TOKEN` env var; when set, the UI asks once
  and stores it in `localStorage`, sent as a header. Keeps the "no login"
  feel (one-time paste) while stopping houseguests' phones from driving the
  vacuum. Off by default, so current behavior is unchanged.
- Bind suggestion in docs is good; also consider mDNS (`avahi`) so the page is
  reachable at `http://coukab.local:8080` — nicer than remembering an IP, and
  it survives DHCP changes.

### 3.2 Reliability
- `Controller.run_action`'s timeout covers *queue wait + execution*; a fast
  action queued behind a slow one reports a misleading timeout. Start the
  timer when the job begins, or report "queued behind N jobs" in the error.
- systemd: add `Restart=always` plus `WatchdogSec` with a tiny `sd_notify`
  ping from the main loop, so a hung evdev read or wedged worker gets
  restarted automatically.
- The status pool (`_STATUS_POOL`, 6 workers) can be exhausted by stacked
  slow polls → `device did not respond in time` even though devices are fine.
  The TTL cache (2.1) mostly fixes this; otherwise raise the timeout only for
  the first poll after startup (cold miio handshakes are the slow ones).

### 3.3 Code structure
- `web_server.py` reaches into `Controller._do_*` private methods. Rename
  them to public (`apply_mode`, `party_toggle`, …) or expose an explicit
  `ACTIONS` registry dict on the controller — same behavior, but the
  contract between the two files becomes visible and testable.
- The `_lights_action` if/elif ladder and its test would both shrink if the
  action→method map were data (`{"party_toggle": (c.run_action, c._do_party_toggle), ...}`).

### 3.4 Testing
- Add one integration test that boots the real server on a random port with a
  stub controller and asserts the static files + `/api/capabilities` +
  unknown-route 404 behave (the manual end-to-end script from development,
  but checked in).
- A tiny JS sanity check in CI: `node --check web/app.js` plus a grep that
  every `data-icon` name exists in `ICONS` (this exact mismatch is easy to
  reintroduce).

### 3.5 Feature ideas (later)
- **Schedules**: "purifier auto at 22:00", "all off at 01:00" — a small
  `schedules.json` + a checker thread; surfaced in the web UI.
- **Status history**: append PM2.5/temp/humidity to a CSV ring buffer and
  draw a 24 h sparkline in the Air card — the purifier is already polled.
- **Camera live glimpse**: a "refresh frame" button that grabs one RTSP frame
  on demand (cheaper than streaming, much fresher than the last capture).
- **Keypad parity page**: a printable cheat-sheet route (`/keys`) generated
  from the `main.py` docstring, so the key map is always in sync.
