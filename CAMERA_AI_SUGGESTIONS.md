# Camera & Local-AI Suggestions

Ideas for `tapo_camera_utils.py`, tools built on camera pictures, and local
AI — grounded in what the codebase already has. Everything here runs on the
LAN; nothing leaves the house.

**What already exists (build on, don't redo):**
- `tapo_camera_utils.connect()` / `capture_moment()` — RTSP capture to
  `moments/`, privacy-mode auto-disable, flash-blink capture via the
  controller.
- `capture_moment_for_model()` — already a `@brain_tool` that returns a
  `media_input` payload (resized JPEG data URI) for multimodal chat.
- `brain.py` — llama-cpp + Gemma GGUF with **media-injection plumbing
  already written** (`_image_path_to_data_uri`, `_media_entry_to_content_part`,
  `_extract_tool_media_parts`). Vision was designed for, not yet enabled.
- Server-side cv2 + numpy, the `moments/` gallery (web + panel preview via
  `/api/panel/moment.rgb565`), and SSE live-update infrastructure.

**Recommended order:** 1 (vision unlock) → 3 (watch mode) → 14 (retention),
then pick by appetite.

---

## A. Local AI on pictures

### 1. Unlock Khatoon's vision  ⭐ highest leverage
The whole pipeline — capture tool → `media_input` → chat-message content
parts — already exists; the only missing piece is the model side. Gemma 3n
E2B is multimodal, but llama-cpp needs the **mmproj companion GGUF**
(`mmproj-*.gguf` published alongside the model) and a vision-enabled chat
handler passed to `Llama(...)` in `brain.load_model()`. With that, "Khatoon,
look at the camera — is the front door closed?" works end-to-end today.
Fallback if Gemma-vision-in-llama-cpp proves fragile: a small dedicated
VLM (e.g. Moondream ~2B GGUF) loaded lazily just for image questions.

### 2. Scene-description on capture (auto-captions)
After each `capture_moment()`, optionally run the (now-sighted) model with a
fixed prompt ("describe this scene in one line") and store the caption next
to the image (`moments/<name>.txt`). The web gallery and the daily digest
(idea 8) get searchable, human-readable history nearly for free.

### 3. CLIP semantic search over moments
Embed each saved moment once with a small CLIP model (onnxruntime, ~100 MB)
and store vectors in a JSON/np file; a search box in the web UI ("vacuum
stuck", "person at desk") ranks by cosine similarity. No LLM needed at query
time — instant results even on the Pi-class server.

> **Implemented:** `tapo_camera_utils.look_around()` (brain tool
> `camera_look_around`) — captures one low-res frame and returns TEXT facts
> (lighting, people/face presence via built-in OpenCV HOG+Haar, scene-changed)
> so the text-only LLM can sense the room without true vision. True
> model-vision was ruled out: `llama-cpp-python` has no Gemma vision handler
> and image inference is far too slow on the 2-core CPU.

## B. Local CV without an LLM (cheap, always-on)

### 4. Motion detection on the RTSP stream
A `watch` loop using `connect()` + frame differencing
(`cv2.absdiff`/`createBackgroundSubtractorMOG2`): when motion exceeds a
threshold, call `capture_moment()` and fire `notify_status_changed()` so the
web gallery and panel update live. Use **`stream2`** (the low-res substream;
change one character in the RTSP URL) so this costs almost nothing.

### 5. Person detection
OpenCV DNN with a tiny detector (YuNet for faces, or MobileNet-SSD /
YOLOv8-n via onnxruntime) on motion frames only — motion gates the expensive
model. Result: "motion" vs "person" become distinct events, which makes
automations (idea 7) trustworthy instead of jumpy.

### 6. Pet detection variant
Same detector, different class filter (cat/dog) — fun events like
"the cat is on the desk again" in the digest, and a guard against the vacuum
starting while the pet sleeps next to the dock.

## C. Tools on top of pictures

### 7. Presence-aware automations
Wire detector events into the existing serialized controller
(`main.Controller.run_action`): person enters at night → `cool_white` on;
no person for 30 min → `all_off`. The controller already serializes with
keypad/web so nothing races. Start conservative: notify first, act later.

### 8. Daily digest
A cron (or `coukab-lan.service` timer) that has Khatoon summarize the day:
captions of the day's moments + device stats ("7 moments, vacuum cleaned
42 m², PM2.5 peaked at 41"). Post it into the web chat log and keep the
collage image in `moments/`.

### 9. Panel "doorbell" push
On a person-detection event, push an SSE event type the panel understands
and have it auto-show the fresh `/api/panel/moment.rgb565` thumbnail with a
warm border — the wall panel becomes a live peephole. Firmware change is
small (one new trigger → `showMomentPreview()`).

### 10. Timelapse builder
Cron-capture a frame every N minutes into `moments/timelapse/`, then stitch
daily with `cv2.VideoWriter` (mp4) — no new dependencies. Expose the result
in the gallery; great for plants, light changes, vacuum coverage.

## D. Camera control extras (pytapo)

### 11. PTZ / patrol
If the camera model has a motor, `pytapo` exposes `moveMotor`/presets — a
D-pad on the web Camera card (mirroring the vacuum D-pad pattern) plus
"scan the room then capture 3 angles" as a brain tool.

### 12. Privacy-mode schedule
`ensure_camera_on()` already toggles privacy mode; add the inverse and a
schedule ("privacy ON 09:00–18:00 weekdays") so the camera is provably
blind when you're home — a trust feature, surfaced as a toggle in the web
UI and on the panel's SETUP page.

### 13. Native camera events
`pytapo` can read the camera's own detection events (`getEvents`) — cheaper
than running CV when only coarse "something moved" is needed; combine:
native event wakes the CV pipeline.

## E. Engineering hardening

### 14. Moments retention policy
`moments/` grows forever. Add max-count/max-age pruning (e.g. keep 500 or
90 days) applied after each capture, with the digest/timelapse folders
exempt. One small function in `tapo_camera_utils`, called from
`capture_moment()`.

### 15. RTSP capture robustness
Before `cap.read()`, grab-and-discard a few frames — RTSP buffers serve a
stale frame after idle periods, which is why "capture" sometimes shows the
past. Add reconnect-with-backoff in the watch loop, and prefer `stream2`
for anything that isn't a keepsake photo.

### 16. Thumbnail cache
`_panel_moment_rgb565` and the web gallery re-decode full-size JPEGs every
time; cache small thumbnails beside the originals
(`moments/.thumbs/<name>.jpg`) on first request.

### 17. Testability
A `FakeCapture` (returns a numpy test pattern) injected into
`capture_moment()` would let the watch loop, retention, and caption paths be
unit-tested in the existing network-free style of `tests/test_web_server.py`.
