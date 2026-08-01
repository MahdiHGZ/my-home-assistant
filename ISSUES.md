# Coukab LAN code-review issues

Reviewed: 2026-08-01

Scope: current working tree, including the existing uncommitted touch-controller and panel-status changes

Review lenses: performance, reliability, bugs, cleanup, missing features, and test quality

## Executive summary

The project has a clear architecture, defensive error handling around optional devices, a useful host renderer for the ESP32 UI, and a fast unit suite. The largest risks are not syntax errors; they are concurrency and failure-semantics problems at the boundaries between HTTP requests, device I/O, the serialized light controller, and the ESP32 queue.

The recommended implementation order is:

1. Make vacuum stop ordering and controller timeouts safe (`CL-001`, `CL-002`).
2. Stop reporting failed light commands as successful (`CL-003`).
3. Serialize access to cached device clients and harden the status pipeline (`CL-004`, `CL-005`).
4. Fix camera durability and cross-client chat history (`CL-006`, `CL-007`).
5. Make controller alerts non-blocking and non-droppable (`CL-008`, `CL-009`).
6. Enforce typed API inputs before expanding the API (`CL-017`).
7. Add the concurrency/API/firmware test matrix before larger refactors (`CL-014`).

### Priority definitions

| Priority | Meaning |
| --- | --- |
| P0 | Physical-safety or severe correctness risk; fix first. |
| P1 | High-impact reliability, data-integrity, privacy, or availability defect. |
| P2 | Important hardening, maintainability, performance, or product gap. |
| P3 | Low-risk cleanup or developer-experience improvement. |

### Issue index

| ID | Type | Priority | Confidence | Part | Summary |
| --- | --- | --- | --- | --- | --- |
| CL-001 | Bug / safety | P0 | High | web_server, tools | Vacuum drive commands can execute after the stop command. |
| CL-002 | Bug / reliability | P1 | controller | A timed-out queued action is not cancelled and executes later. |
| CL-003 | Bug / reliability | P1 | controller, tools | Failed Yeelight actions are logged but reported as successful; discovery retry is bypassed. |
| CL-004 | Performance / reliability | P1 | web_server | Status polling can stampede, exhaust the shared pool, and serve stale panel state indefinitely. |
| CL-005 | Bug / reliability | P1 | tools, brain, controller | Cached device clients are used concurrently without per-device serialization. |
| CL-006 | Bug / data integrity | P1 | tools, web_server | Camera writes are not verified and same-second captures can overwrite each other. |
| CL-007 | Bug / privacy | P1 | brain, web_server | Chat history is process-global despite the documented stateless design. |
| CL-008 | Performance / reliability | P1 | controller | Panel image downloads block touch, OTA, and SSE processing. |
| CL-009 | Bug / reliability | P1 | controller | Panel alert fetches are silently lost when the queue is full. |
| CL-010 | Security / reliability | P1 | web_server | Request bodies, connection duration, and request-thread growth are unbounded. |
| CL-011 | Security / feature gap | P2 | web_server, controller | Physical-control APIs and OTA lack a secure, opt-in access-control path. |
| CL-012 | Security / bug | P2 | web_server | Device-derived strings are inserted with `innerHTML`, enabling DOM injection. |
| CL-013 | Performance / UX | P2 | web_server | Browser requests have no timeout or cancellation policy. |
| CL-017 | Bug / API contract | P1 | web_server | String values such as `"false"` are treated as true for physical actions. |
| CL-014 | Tests / necessary feature | P1 | tools, web_server, brain, controller | Critical concurrency, HTTP, device-adapter, and firmware behavior is untested. |
| CL-015 | Cleanup / reliability | P2 | tools, brain, controller | Deployments are not reproducible and production code imports a test harness. |
| CL-016 | Feature gap / operations | P2 | web_server, controller | Health checks do not detect degraded or wedged subsystems. |

---

## CL-001 — Make vacuum remote control ordered and stop-dominant

- **Type:** Bug / physical safety / reliability
- **Priority:** P0
- **Confidence:** High
- **Status:** Fixed (`149e410`)
- **Affected part:** `web_server`, `tools`
- **Effort:** Medium

### Description

The browser sends a new remote-drive request every 400 ms without waiting for the prior request. On release it sends `remote=5` (stop), but all requests are independent HTTP requests. `ThreadingHTTPServer` may run them concurrently, and `_vacuum_action()` calls the shared vacuum client directly without an ordering lock. A delayed direction request can therefore finish after the stop request and make the vacuum continue moving.

### Evidence

- [`web/app.js`](web/app.js#L858) starts overlapping drive requests and sends stop on release at line 884.
- [`web_server.py`](web_server.py#L599) executes vacuum actions directly on request threads.
- [`miot_base.py`](miot_base.py#L49) protects only creation/invalidation of the cached device, not commands sent through it.

### Impact

- The UI can show that driving stopped while a late request restarts motion.
- Repeated commands can race on the cached MIoT transport.
- Poor Wi-Fi makes the unsafe ordering more likely.

### Suggested fix

1. Create one serialized executor/actor per physical device; all vacuum commands must pass through it.
2. Give remote-control sessions a monotonically increasing session/sequence ID. Reject direction commands older than the latest stop.
3. Make stop high priority and idempotent. Clear queued direction commands before executing it.
4. In the browser, keep at most one direction request in flight; coalesce repeated direction intent instead of issuing overlapping fetches.

### Required tests

- Delay direction request N, complete stop N+1 first, then release N; assert the late direction is discarded.
- Simulate packet loss and 2-second latency; assert release always leaves the last executed command as stop.
- Run two browser sessions; assert commands remain serialized and stop dominates both.

### Acceptance criteria

- No direction command from an older control session can execute after stop.
- The server records and exposes the most recent remote sequence for diagnostics.

### Resources

- Project references: [`web/app.js`](web/app.js#L858), [`web_server.py`](web_server.py#L599), [`miot_base.py`](miot_base.py#L99)
- [Python `ThreadingHTTPServer`](https://docs.python.org/3/library/http.server.html#http.server.ThreadingHTTPServer)

---

## CL-002 — Cancel or explicitly track actions after an HTTP timeout

- **Type:** Bug / reliability
- **Priority:** P1
- **Confidence:** High (reproduced locally)
- **Status:** Fixed (`e5f2ace`)
- **Affected part:** `controller`
- **Effort:** Medium

### Description

`Controller.run_action()` waits on an event and raises `TimeoutError` to the caller, but the queued closure remains in the queue. If it had not started yet, it executes later. If the client retries after seeing the error, the same physical action may execute twice.

The queue is also unbounded. The `qsize() >= 5` check is approximate and separate from `put()`, so concurrent callers can all pass the check and enqueue more than five jobs.

### Evidence

- [`main.py`](main.py#L196) constructs an unbounded queue.
- [`main.py`](main.py#L237) checks `qsize()` before a later `put()` at line 251.
- [`main.py`](main.py#L252) times out without marking or removing the job.
- Local reproduction: a second action returned `TimeoutError`, then executed after the blocking first action completed.

### Impact

- A reported failure can mutate real devices later.
- Retries can duplicate toggles, captures, or scene changes.
- Queue backpressure is racy under concurrent web requests.

### Suggested fix

1. Use `queue.Queue(maxsize=5)` and `put_nowait()`; map `queue.Full` directly to HTTP 429.
2. Represent jobs with states (`queued`, `running`, `cancelled`, `done`) and a cancellation token.
3. On timeout, cancel jobs that have not started. For already-running device I/O, return `202 Accepted` plus a job ID instead of claiming failure.
4. Add idempotency keys for mutating HTTP actions so a client retry cannot duplicate a completed action.
5. Map actual deadline expiry to HTTP 504 and include whether the operation is still running.

### Required tests

- Queue a blocked job followed by a short-deadline job; assert the latter never executes after timeout.
- Launch ten simultaneous callers; assert at most the configured queue capacity is accepted.
- Retry with the same idempotency key; assert exactly one device call.

### Acceptance criteria

- A request reported as cancelled can never execute later.
- A running request has an observable job state and cannot be duplicated by retry.

### Resources

- Project reference: [`main.py`](main.py#L229)
- [Python queue size guarantees](https://docs.python.org/3/library/queue.html#queue.Queue.qsize)
- [Python future cancellation semantics](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel)

---

## CL-003 — Propagate partial Yeelight failures instead of returning success

- **Type:** Bug / reliability
- **Priority:** P1
- **Confidence:** High (reproduced locally)
- **Status:** Fixed (`6a13faa`)
- **Affected part:** `controller`, `tools`
- **Effort:** Medium

### Description

Most multi-bulb helpers catch exceptions inside each worker, log them, and return normally. The controller's discovery retry only runs when the outer function raises, so common bulb failures never trigger rediscovery. `_lights_action()` then hard-codes `{"ok": true}` even if every bulb failed.

### Evidence

- [`yeelight_bulb_utils.py`](yeelight_bulb_utils.py#L680) catches per-bulb mode failures without propagating them.
- The same pattern exists in `full_off`, brightness adjustment, random color, and state restore.
- [`main.py`](main.py#L258) retries only raised exceptions.
- [`web_server.py`](web_server.py#L543) returns success after `run_action()` without checking per-bulb results.
- Local reproduction: a fake bulb whose `turn_on()` raises caused `apply_mode()` to return without error.

### Impact

- Browser and panel show success while lights remain unchanged.
- DHCP/IP changes do not activate the advertised auto-discovery path.
- Undo snapshots may be partial yet appear valid.

### Suggested fix

1. Return a standard batch result from every bulb operation: `ok`, `succeeded`, `failed`, and per-bulb errors.
2. Raise a typed `PartialDeviceError` when any failure is retryable, or make `_with_discovery_retry()` inspect the result's `ok` field.
3. Rediscover once, reload the new config, and retry only failed bulbs.
4. Return HTTP 207 or a 502 with structured partial results; never hard-code success.
5. Do not push an empty/partial state onto undo history unless explicitly marked.

### Required tests

- One of six bulbs fails, discovery updates its IP, and only that bulb is retried.
- All bulbs fail; API returns non-2xx/partial failure and panel does not show a success toast.
- Undo with a partial snapshot does not silently lose state.

### Acceptance criteria

- Any failed bulb is visible in the API result.
- Rediscovery is exercised by a failing bulb command and verified by a test.

### Resources

- Project references: [`yeelight_bulb_utils.py`](yeelight_bulb_utils.py#L658), [`main.py`](main.py#L258), [`web_server.py`](web_server.py#L543)

---

## CL-004 — Add single-flight status refreshes, real deadlines, and cache freshness

- **Type:** Performance / reliability
- **Priority:** P1
- **Confidence:** High
- **Status:** Fixed (`2cd9b7b`)
- **Affected part:** `web_server`
- **Effort:** Medium

### Description

Each status build submits three jobs to one six-thread global pool. Concurrent cold requests and new SSE connections can create multiple builds because there is no single-flight guard; the SSE initial snapshot bypasses the cache. Timed-out futures are neither cancelled nor removed, and running I/O cannot be cancelled by `Future.result(timeout=...)`. Enough hung device calls can occupy all workers and leave later status jobs queued.

Under pool saturation, the loop can spend up to one timeout per queued future. The panel cache then returns any existing snapshot indefinitely and only requests a background refresh, without returning age or a stale flag.

### Evidence

- Shared six-thread pool: [`web_server.py`](web_server.py#L56).
- Per-build submission/waits: [`web_server.py`](web_server.py#L384).
- Cache miss has no in-progress refresh sharing: [`web_server.py`](web_server.py#L407).
- Panel returns an arbitrarily old snapshot: [`web_server.py`](web_server.py#L417).
- Every SSE subscriber performs a fresh build at connection time: [`web_server.py`](web_server.py#L856).

### Impact

- One offline/hung device can degrade every browser and panel.
- Reconnect storms amplify LAN traffic and executor queue depth.
- The panel can display stale state as current indefinitely.

### Suggested fix

1. Use one shared in-progress status future; all callers await or reuse it.
2. Apply one absolute deadline to the whole snapshot, not a fresh timeout per future.
3. Configure timeouts in the underlying Yeelight/MIoT calls; threads cannot safely stop already-running socket I/O.
4. Cancel futures that are still queued after the deadline and track pool saturation.
5. Serve the cached snapshot immediately to new SSE clients, then trigger one refresh.
6. Store cache time with `time.monotonic()`, include `generated_at`, `age_ms`, and `stale` in responses, and define a maximum stale age.

### Required tests

- Twenty simultaneous cold status requests result in exactly one poll per device.
- One permanently blocking reader does not exhaust the pool after repeated requests.
- Wall-clock jumps do not change cache TTL behavior.
- Panel response marks stale data and eventually transitions to unavailable.

### Acceptance criteria

- Status latency is bounded by one configured end-to-end deadline.
- Concurrent callers share one refresh and cache age is observable.

### Resources

- Project references: [`web_server.py`](web_server.py#L384), [`web_server.py`](web_server.py#L417)
- [Python executor and cancellation behavior](https://docs.python.org/3/library/concurrent.futures.html)
- [Python monotonic clock](https://docs.python.org/3/library/time.html#time.monotonic)

---

## CL-005 — Serialize access to each cached physical-device connection

- **Type:** Bug / reliability / architecture
- **Priority:** P1
- **Confidence:** High for the race; Medium for library-specific failure mode
- **Status:** Fixed (`9606ce3`)
- **Affected part:** `tools`, `brain`, `controller`
- **Effort:** Large

### Description

The project caches one `yeelight.Bulb` per IP and one MIoT client per device, but does not serialize commands sent through those objects. Web status, direct device actions, party mode, keypad jobs, and LLM tools can run on different threads at the same time.

The controller docstring promises serialization, but brain tools call the utility functions directly and status reads bypass `Controller.run_action()`.

### Evidence

- Shared Yeelight object cache: [`yeelight_bulb_utils.py`](yeelight_bulb_utils.py#L32).
- Shared MIoT client with a lock only around construction/invalidation: [`miot_base.py`](miot_base.py#L48).
- Web status runs from a separate executor: [`web_server.py`](web_server.py#L384).
- Vacuum and purifier actions run on request threads: [`web_server.py`](web_server.py#L599).
- Brain registers raw utility functions as handlers: [`brain_test.py`](brain_test.py#L139).
- Party mode has its own background thread: [`yeelight_bulb_utils.py`](yeelight_bulb_utils.py#L456).

### Impact

- Persistent socket request/response streams may interleave.
- A status failure can invalidate a client while another action is using it.
- The LLM can race keypad/web operations despite the stated controller guarantee.

### Suggested fix

1. Introduce a device gateway layer with one lock/actor per physical device (and per bulb IP).
2. Route browser actions, status reads, keypad calls, and brain tools through that layer.
3. Give writes priority over non-critical status reads and allow status to use a recent cache.
4. Keep state transitions (party start/stop, undo, current mode) inside the same serialized boundary.

### Required tests

- Instrument fake clients to fail if entered concurrently; run status, brain, and web actions in parallel.
- Invalidate during a failed call and assert a concurrent successful call keeps a valid client.
- Start/stop party while polling status and changing a scene; assert deterministic final state.

### Acceptance criteria

- No cached transport instance is entered concurrently.
- Every real-device access path uses the same gateway.

### Resources

- Project references: [`miot_base.py`](miot_base.py#L23), [`yeelight_bulb_utils.py`](yeelight_bulb_utils.py#L39), [`brain_test.py`](brain_test.py#L114)

---

## CL-006 — Make camera capture durable, unique, and atomic

- **Type:** Bug / data integrity / cleanup
- **Priority:** P1
- **Confidence:** High
- **Status:** Fixed (`b7c3005`)
- **Affected part:** `tools`, `web_server`
- **Effort:** Medium

### Description

`capture_moment()` ignores the boolean returned by both `cv2.imwrite()` calls. It reports a saved path even when disk permissions, disk-full conditions, or codec failure prevent the write. Filenames have one-second precision, so concurrent or rapid captures overwrite the same image. The server then chooses the newest existing file, which can make a failed capture appear successful by returning an older image.

Retention is also performed as a side effect of `_moment_files()`, a read helper called by status and listing endpoints. Concurrent reads can race while deleting files.

### Evidence

- Unchecked image and thumbnail writes: [`tapo_camera_utils.py`](tapo_camera_utils.py#L151).
- One-second filename: [`tapo_camera_utils.py`](tapo_camera_utils.py#L164).
- Server checks only whether *any* moment exists after capture: [`web_server.py`](web_server.py#L673).
- GET/status path prunes files: [`web_server.py`](web_server.py#L313).

### Impact

- False capture success and stale-image display.
- Silent overwrite of distinct moments.
- Partially written files may be served while another thread reads them.

### Suggested fix

1. Use UTC timestamp with microseconds plus a random suffix/UUID.
2. Encode/write to a temporary file, verify the return value and non-zero size, then `os.replace()` atomically.
3. Treat thumbnail failure separately; the full capture may succeed with a lazy thumbnail fallback.
4. Return the exact created path from the capture call through the API instead of rescanning for the newest file.
5. Run retention once after a successful capture under a moments lock; listing endpoints must be read-only.

### Required tests

- Mock `cv2.imwrite()` returning `False`; assert API returns an error and no stale image URL.
- Freeze time and capture twice; assert two unique files.
- Read/list while capture and retention run; assert no partial file is served and no uncaught race.

### Acceptance criteria

- A successful response points to the exact newly and atomically written image.
- Read endpoints never delete files.

### Resources

- Project references: [`tapo_camera_utils.py`](tapo_camera_utils.py#L151), [`web_server.py`](web_server.py#L313)
- [OpenCV `imwrite()` return value](https://docs.opencv.org/4.9.0/d4/da8/group__imgcodecs.html)

---

## CL-007 — Remove global cross-client chat memory or scope it to a session

- **Type:** Bug / privacy / product behavior
- **Priority:** P1
- **Confidence:** High
- **Status:** Fixed (`c458dbb`)
- **Affected part:** `brain`, `web_server`
- **Effort:** Small for stateless behavior; Medium for sessions

### Description

The brain, README, and browser all describe chat as single-shot with no memory. The web server instead keeps the latest six messages in one process-global deque and injects them into every user's next system prompt. There is no browser/session identity, clear-history action, or isolation.

The streaming path appends the user message before generation completes, so disconnects/errors can leave unmatched history that contaminates the next request.

### Evidence

- Global history: [`web_server.py`](web_server.py#L169).
- Blocking and streaming paths inject it: [`web_server.py`](web_server.py#L172).
- Brain explicitly promises no persisted conversation: [`brain.py`](brain.py#L349).
- Browser says each command replaces the previous one: [`web/app.js`](web/app.js#L950).

### Impact

- One household user's conversation can affect or leak into another user's reply.
- Prompt-cache assumptions and documentation are no longer true.
- Broken/disconnected streams leave hidden state.

### Suggested fix

Preferred: restore the documented stateless design by removing `_chat_history` from the web layer. If memory is a required feature, add an explicit session ID, per-session bounded storage, expiration, a clear button, locking, and a privacy notice.

### Required tests

- Two simulated clients send distinct prompts; assert neither prompt appears in the other's model messages.
- Disconnect mid-stream; assert no partial history remains.
- Stateless mode produces an identical system/tool prefix for repeated requests.

### Acceptance criteria

- Runtime behavior, README, brain docstring, and UI describe the same memory model.
- No process-global conversation content crosses clients.

### Resources

- Project references: [`web_server.py`](web_server.py#L137), [`brain.py`](brain.py#L339), [`README.md`](README.md)

---

## CL-008 — Move panel image downloads off the UI loop

- **Type:** Performance / reliability
- **Priority:** P1
- **Confidence:** High
- **Status:** Fixed (`cf5e3d8`)
- **Affected part:** `controller` (ESP32 touch controller)
- **Effort:** Large

### Description

Although normal API calls use the FreeRTOS worker, alert and moment RGB565 downloads run synchronously in the main `loop()` task. `HTTPClient.GET()` can wait up to 15 seconds, followed by a seven-second streaming deadline. During that time the controller does not process touch input, `ArduinoOTA.handle()`, SSE, backlight timing, or completed worker results.

Alerts are server-triggered, so this freeze can happen without a user choosing to open a preview.

### Evidence

- Blocking alert download: [`tuch_controller/tuch_controller.ino`](tuch_controller/tuch_controller.ino#L2232).
- Blocking moment download: [`tuch_controller/tuch_controller.ino`](tuch_controller/tuch_controller.ino#L2310).
- OTA and SSE are serviced only from `loop()`: [`tuch_controller/tuch_controller.ino`](tuch_controller/tuch_controller.ino#L3162).

### Impact

- Frozen touch UI and delayed close/wake behavior on weak Wi-Fi.
- OTA servicing pauses during downloads.
- SSE and status updates back up, making the panel appear disconnected.

### Suggested fix

1. Download/validate RGB565 data in the network worker into a bounded PSRAM buffer or temporary store.
2. Send a completed image descriptor to the UI task; only the UI task should touch SPI/TFT.
3. Add a cancel flag and visible progress for user-requested previews.
4. Keep a strict content-length check (`w*h*2`) and total deadline.
5. If memory is tight, stream scanline chunks through a bounded queue while the UI continues servicing touch between chunks.

### Required tests

- Host shim simulates a 10-second/stalled image stream; UI loop ticks and cancel input still execute.
- Truncated, oversized, and wrong-content-type image responses are rejected.
- OTA handler and SSE pump receive regular calls during transfer.

### Acceptance criteria

- No network wait longer than one UI frame occurs on the main controller task.
- A stalled transfer is cancellable and leaves the prior page intact.

### Resources

- Project references: [`tuch_controller/tuch_controller.ino`](tuch_controller/tuch_controller.ino#L2232), [`tuch_controller/tuch_controller.ino`](tuch_controller/tuch_controller.ino#L2310)

---

## CL-009 — Preserve and prioritize panel alerts when the FreeRTOS queue is full

- **Type:** Bug / reliability
- **Priority:** P1
- **Confidence:** High
- **Status:** Fixed (`14a723e`)
- **Affected part:** `controller` (ESP32 touch controller)
- **Effort:** Small

### Description

`enqueueAlertFetch()` ignores the result of non-blocking `xQueueSend()`. The main loop clears `alertPokePending` before calling it. If the eight-item queue is full, the alert event is lost with no retry. The deep-sleep alert poll has a similar enqueue path and can wait until its general Wi-Fi deadline before sleeping again.

### Evidence

- Queue send exposes success/failure: [`tuch_controller/tuch_controller.ino`](tuch_controller/tuch_controller.ino#L2171).
- Alert enqueue ignores it: [`tuch_controller/tuch_controller.ino`](tuch_controller/tuch_controller.ino#L2220).
- Pending flag is cleared before enqueue: [`tuch_controller/tuch_controller.ino`](tuch_controller/tuch_controller.ino#L3237).

### Impact

- Important/urgent alerts may never appear.
- There is no telemetry distinguishing “no alert” from “alert fetch dropped.”

### Suggested fix

1. Return `bool` from `enqueueAlertFetch()` and clear `alertPokePending` only on success.
2. Use `xQueueSendToFront()` for alerts or reserve a queue slot for alert/control traffic.
3. Deduplicate by alert ID after fetching, not by dropping event intent.
4. Track queue-full counters in the diagnostics page.

### Required tests

- Fill the queue, raise an alert event, free one slot, and assert the alert is retried.
- Burst multiple alert SSE events; assert the newest alert is eventually shown once.

### Acceptance criteria

- A queue-full condition delays an alert but cannot silently discard it.

### Resources

- Project reference: [`tuch_controller/tuch_controller.ino`](tuch_controller/tuch_controller.ino#L2171)
- [Espressif FreeRTOS queue API](https://docs.espressif.com/projects/esp-idf/en/v4.4.5/esp32/api-reference/system/freertos.html#queue-api)

---

## CL-010 — Bound HTTP body size, socket time, and request concurrency

- **Type:** Security / reliability / performance
- **Priority:** P1
- **Confidence:** High
- **Status:** Fixed (`916ebcf`)
- **Affected part:** `web_server`
- **Effort:** Medium

### Description

The HTTP handler trusts arbitrary `Content-Length`, reads that amount with no configured socket read timeout, and runs in one thread per connection. A broken or hostile LAN client can hold threads with partial bodies, allocate excessive memory, or create enough connections to exhaust process resources.

Internal exception text is also returned directly in 500 responses, leaking device/library details unnecessarily.

### Evidence

- Unbounded body read: [`web_server.py`](web_server.py#L743).
- Unbounded per-connection thread model: [`web_server.py`](web_server.py#L990).
- Raw exception responses: [`web_server.py`](web_server.py#L939) and [`web_server.py`](web_server.py#L969).

### Impact

- Dashboard, controller, and SSE can become unavailable from one faulty client.
- Memory pressure is especially risky on the low-power server.

### Suggested fix

1. Reject missing/invalid/oversized lengths (for example, 16 KiB maximum) with 411/413.
2. Require `application/json` for JSON endpoints.
3. Set connection/header/body timeouts and close incomplete requests.
4. Bound active handler threads or place a small production server/reverse proxy in front.
5. Return stable public error codes; keep detailed exceptions only in logs.

### Required tests

- Oversized and negative `Content-Length` receive 413/400 without allocating the declared size.
- Slow partial body is closed after the configured deadline.
- More than N simultaneous connections receive controlled backpressure.

### Acceptance criteria

- Memory and threads per client are bounded.
- Public 5xx responses contain an error ID, not raw library/device exceptions.

### Resources

- Project reference: [`web_server.py`](web_server.py#L715)
- [Python `http.server` security considerations](https://docs.python.org/3/library/http.server.html#security-considerations)

---

## CL-011 — Add optional authentication and secure OTA defaults

- **Type:** Security / necessary feature gap
- **Priority:** P2
- **Confidence:** High
- **Affected part:** `web_server`, `controller`
- **Effort:** Large

### Description

The server intentionally exposes light, vacuum, purifier, camera, panel-alert, and LLM-tool actions without authentication on `0.0.0.0`. Delete protection covers only photos. The ESP32 OTA password is optional. A trusted LAN reduces exposure but does not provide user/device identity, revocation, or least privilege.

### Evidence

- Explicit no-auth design: [`web_server.py`](web_server.py#L7).
- Default all-interface bind: [`main.py`](main.py#L517).
- Only camera deletion checks a password: [`web_server.py`](web_server.py#L689).
- OTA password is optional: [`tuch_controller/tuch_controller.ino`](tuch_controller/tuch_controller.ino#L3073).

### Impact

- Any device or guest on the LAN can operate physical devices, capture images, invoke the LLM tools, and send panel alerts.
- An unprotected OTA endpoint can replace panel firmware.

### Suggested fix

1. Preserve no-login mode only as an explicit `trusted-lan` configuration.
2. Add an API token/session option with separate credentials for browsers and the wall panel.
3. Require authorization for camera, vacuum remote drive, LLM tools, deletion, and alerts; allow read-only status separately.
4. Validate `Origin`/`Host`, rate-limit sensitive actions, and document network segmentation.
5. Require `OTA_PASSWORD` for release firmware and fail the release build if absent.

### Required tests

- Anonymous requests are rejected when auth mode is enabled.
- Panel token cannot call browser/admin-only endpoints.
- Revoked/rotated credentials stop working without reflashing unrelated devices.
- Release firmware build fails without OTA credentials.

### Acceptance criteria

- A documented secure mode protects every mutating endpoint and OTA.
- Trusted-LAN mode remains available only by explicit configuration.

### Resources

- Project references: [`README.md`](README.md), [`web_server.py`](web_server.py#L40)
- [OWASP IoT user-space authentication and authorization requirements](https://owasp.org/IoT-Security-Verification-Standard-ISVS/en/V2-User_Space_Application_Requirements.html)

---

## CL-012 — Remove device-derived HTML interpolation

- **Type:** Security / bug
- **Priority:** P2
- **Confidence:** High for injection sinks; Medium for remote exploitability
- **Affected part:** `web_server` (browser UI)
- **Effort:** Small

### Description

Several strings originating in config, device responses, or server error messages are interpolated into `innerHTML`. Examples include vacuum room names, status metrics, consumable errors, bulb names, and image URLs. A crafted device/config value containing markup can execute in the dashboard origin.

### Evidence

- Room names: [`web/app.js`](web/app.js#L366).
- Status metrics: [`web/app.js`](web/app.js#L413), consumed at lines 474 and 498.
- Server/device error in maintenance HTML: [`web/app.js`](web/app.js#L892).
- Bulb/config names: [`web/app.js`](web/app.js#L309).

### Impact

- Script running in the dashboard origin can operate every unauthenticated API.
- Future auth tokens stored in the page would make the impact larger.

### Suggested fix

1. Build elements with `createElement()` and assign all dynamic values through `textContent`, `dataset`, and property setters.
2. Keep `innerHTML` only for audited constant icon templates.
3. Add a restrictive Content Security Policy and avoid inline script/style requirements.

### Required tests

- Render a room/bulb/error value such as `<img src=x onerror=...>` and assert it appears as text with no element/event creation.
- Run the test for both English and Farsi rendering paths.

### Acceptance criteria

- No device/config/error string reaches an HTML parsing sink.

### Resources

- Project reference: [`web/app.js`](web/app.js)
- [OWASP DOM-based XSS prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/DOM_based_XSS_Prevention_Cheat_Sheet.html)

---

## CL-013 — Add browser-side deadlines, cancellation, and stale-response protection

- **Type:** Performance / UX / reliability
- **Priority:** P2
- **Confidence:** High
- **Affected part:** `web_server` (browser UI)
- **Effort:** Medium

### Description

The common `api()` helper calls `fetch()` without an abort signal. A hung request can leave `polling=true`, a control disabled, or a chat/capture spinner active indefinitely. Slider and remote-drive requests also lack stale-response suppression.

### Evidence

- Common fetch wrapper: [`web/app.js`](web/app.js#L192).
- Polling flag remains set until fetch settles: [`web/app.js`](web/app.js#L646).
- Chat streaming read has no overall/idle deadline: [`web/app.js`](web/app.js#L992).

### Impact

- The UI may never recover without reloading after a half-open connection.
- Late responses can overwrite newer optimistic state.

### Suggested fix

1. Add endpoint-specific deadlines with `AbortController`/`AbortSignal.timeout()` and a compatibility fallback.
2. Cancel obsolete status/slider requests when a newer request starts.
3. Attach sequence numbers and ignore stale responses.
4. Use longer explicit deadlines for capture/chat and an idle-token timeout for streaming.
5. Do not automatically retry mutating actions unless the server supports idempotency (`CL-002`).

### Required tests

- A never-resolving fetch re-enables controls and shows a timeout.
- Response N arrives after N+1; only N+1 updates the UI.
- A chat stream that stops producing bytes is cancelled without leaving the form stuck.

### Acceptance criteria

- Every finite request has an explicit timeout and recoverable UI state.

### Resources

- Project reference: [`web/app.js`](web/app.js#L192)
- [MDN `AbortSignal.timeout()`](https://developer.mozilla.org/en-US/docs/Web/API/AbortSignal/timeout_static)

---

## CL-014 — Add concurrency, HTTP, adapter, browser, and firmware tests in CI

- **Type:** Tests / necessary feature
- **Priority:** P1
- **Confidence:** High
- **Status:** Fixed (CI and integration coverage added with this issue)
- **Affected part:** `tools`, `web_server`, `brain`, `controller`
- **Effort:** Large

### Description

The current suite passes quickly but primarily tests pure helpers and handler mapping with fakes. It does not import/test `main.Controller`, start a real HTTP server, exercise concurrent requests, test MIoT plumbing, validate camera writes, run browser behavior, or assert firmware queue/timing semantics. There is no CI, lint, type-check, or coverage configuration.

`main.py` imports Linux-only `evdev` at module import time; in the checked environment this prevents importing the controller for tests even though the rest of the application is testable.

### Evidence

- 105 tests passed with `python -m unittest discover -s tests -v` in about 0.09 seconds.
- Tests do not import `main.py`; web tests use `RecordingController`: [`tests/test_web_server.py`](tests/test_web_server.py#L21).
- `tests/test.py` is a manual device smoke script, not an automated `TestCase` suite.
- Host preview compiles with `-w`, suppressing all C++ warnings: [`tuch_controller/render_preview.py`](tuch_controller/render_preview.py#L37).
- No `pyproject.toml`, CI workflow, pre-commit, lint, type-check, or coverage config exists.

### Suggested fix

Establish a layered CI test strategy: fast deterministic unit tests on every change, socket/browser/host-firmware integration tests on every change, and explicitly gated hardware smoke tests for release candidates. Make device dependencies injectable and move the `evdev` import behind the keypad boundary so `Controller` can be tested on non-Linux development hosts.

### Required tests

#### Controller and concurrency

- Queue capacity, cancellation, idempotency, callback ordering, and graceful shutdown.
- Parallel status/action/brain/party access with fakes that detect concurrent entry.
- Deterministic clock/fault injection for timeouts and cache aging.

#### HTTP integration

- Start the server on `127.0.0.1:0`; exercise JSON, malformed/large/slow bodies, status codes, SSE connect/disconnect, and graceful shutdown through real sockets.
- Reconnect storm and slow-client tests with bounded resource assertions.

#### Device adapters

- MIoT batch response permutations, missing keys, non-zero result codes, retry/invalidation, and enum handling.
- Camera read/write failure, file collision, atomic visibility, retention, and disk-full behavior.
- Yeelight partial failure and rediscovery.

#### Brain

- Cross-client isolation/statelessness, disconnect cleanup, tool timeout, malicious tool-result text, and schema validation.

#### Browser

- Use a small DOM test setup or Playwright for timeout recovery, no HTML injection, SSE fallback, hold-to-stop ordering, RTL, keyboard access, and service-worker updates.

#### Firmware

- Keep the host preview build, enable `-Wall -Wextra`, and selectively suppress shim-only warnings.
- Extract queue policy, wrap-safe timer helpers, JSON parsing, and touch state transitions into host-testable units.
- Add an Arduino/PlatformIO compile job for the real ESP32-S3 target in addition to the shim build.

### Acceptance criteria

- CI runs unit, integration, browser, Python lint/type checks, host-preview warnings, and real firmware compilation.
- Each P0/P1 issue in this report has a failing regression test before its fix.
- Coverage thresholds focus on controller/server branches rather than only a global percentage.

### Resources

- Project references: [`tests/`](tests/), [`tuch_controller/render_preview.py`](tuch_controller/render_preview.py)
- [Python `unittest.mock`](https://docs.python.org/3/library/unittest.mock.html)
- [GitHub Actions Python workflow guide](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)

---

## CL-015 — Make runtime dependencies reproducible and separate production tool registration

- **Type:** Cleanup / reliability / developer experience
- **Priority:** P2
- **Confidence:** High
- **Affected part:** `tools`, `brain`, `controller`
- **Effort:** Medium

### Description

All runtime dependencies are unversioned, there is no declared Python version, and optional LLM dependencies are comments rather than an installable extra/lock. A fresh deployment can therefore resolve materially different libraries. The code already requires modern Python syntax (`X | None`, `str.removeprefix`) but setup docs do not state the minimum.

Production chat initialization also imports `brain_test.py` to register tools. Production behavior should not depend on a module named and structured as a CLI/test harness.

The README contains machine-specific `file:///Users/divar/...` links, which do not work for other clones.

### Evidence

- Unpinned packages: [`requirements.txt`](requirements.txt).
- Production import of test harness: [`web_server.py`](web_server.py#L156).
- Tool registration implementation: [`brain_test.py`](brain_test.py#L139).
- Machine-local links: [`README.md`](README.md).

### Suggested fix

1. Declare a supported Python version and package metadata in `pyproject.toml`.
2. Separate top-level constraints from a fully resolved, hashed deployment lock file; document a controlled upgrade process.
3. Define optional groups for `llm`, `dev`, and firmware tooling.
4. Move registration/schema code to `brain_tools.py`; keep `brain_test.py` as a thin CLI only.
5. Replace `file://` README links with repository-relative links.

### Required tests

- Build a clean environment from the lock file in CI.
- Import production modules without dev/test dependencies.
- Validate README links with a link checker.

### Acceptance criteria

- Two clean deployments from the same commit resolve the same dependency set.
- No production module imports a test/harness module.

### Resources

- Project references: [`requirements.txt`](requirements.txt), [`web_server.py`](web_server.py#L156)
- [pip repeatable installs](https://pip.pypa.io/en/stable/topics/repeatable-installs/)
- [pip secure/hash-checked installs](https://pip.pypa.io/en/stable/topics/secure-installs/)

---

## CL-016 — Expose readiness, queue health, freshness, and disk/device diagnostics

- **Type:** Necessary feature / operations / reliability
- **Priority:** P2
- **Confidence:** High
- **Affected part:** `web_server`, `controller`
- **Effort:** Medium

### Description

`/api/health` always returns `running` if a request thread can answer. It does not detect a saturated status pool, a blocked controller worker, a growing queue, stale status, low disk space, failed camera storage, unavailable devices, or a stuck LLM. The systemd service restarts only when the process exits, so a live-but-wedged process can remain unhealthy indefinitely.

### Evidence

- Constant health response: [`web_server.py`](web_server.py#L902).
- Service restarts only on process failure: [`coukab-lan.service`](coukab-lan.service).
- Queue/status/cache internals are not exposed: [`main.py`](main.py#L196), [`web_server.py`](web_server.py#L376).

### Suggested fix

1. Split liveness (`process event loop answers`) from readiness (`controller/status pipeline accepts work`).
2. Report controller queue depth/oldest age, worker heartbeat, status refresh duration/age, per-device last success, SSE client count/drop count, chat busy age, and moments disk free space.
3. Add structured duration/error logs with request IDs and action IDs; never log credentials.
4. Add a watchdog/health monitor that restarts only after repeated readiness failure, with graceful shutdown of server, executors, party mode, and controller worker.
5. Surface a compact subset on the panel diagnostics page.

### Required tests

- Block the controller worker and status readers; liveness remains 200 while readiness becomes 503 with reasons.
- Fill the action queue and assert metrics/health expose depth and age.
- Simulate low disk space and stale status.

### Acceptance criteria

- Operators can distinguish process alive, degraded device, and wedged subsystem states.
- Every long-lived worker has a heartbeat and a bounded shutdown path.

### Resources

- Project references: [`web_server.py`](web_server.py#L902), [`coukab-lan.service`](coukab-lan.service)

---

## CL-017 — Validate request types instead of relying on Python truthiness

- **Type:** Bug / API contract / reliability
- **Priority:** P1
- **Confidence:** High
- **Status:** Fixed (`97e46e9`)
- **Affected part:** `web_server`
- **Effort:** Small

### Description

Several physical-action fields are accepted without schema/type validation. Non-empty strings are truthy in Python, so a request containing `"false"` can perform the opposite action from its apparent meaning:

- `{"action":"power","value":"false"}` turns the purifier **on**.
- `{"flash":"false"}` performs a flash capture.
- `{"power":"false"}` is passed to the bulb layer and turns selected bulbs **on**.

Numeric fields are similarly converted with `int()` at scattered call sites, producing inconsistent 400/500/502 errors and accepting booleans as integers.

### Evidence

- Purifier power uses raw truthiness: [`web_server.py`](web_server.py#L638).
- Camera flash uses `bool(body.get("flash"))`: [`web_server.py`](web_server.py#L673).
- Light power is passed through unvalidated: [`web_server.py`](web_server.py#L580).
- Error handling often converts validation mistakes into device/gateway failures: [`web_server.py`](web_server.py#L599).

### Impact

- Third-party clients, older panel firmware, or hand-written API calls can trigger the opposite physical action.
- Clients cannot reliably distinguish bad input from an offline device.

### Suggested fix

1. Define one explicit schema for every endpoint/action, using a small validation layer or dataclasses with strict parsing.
2. Require JSON booleans for boolean fields; never coerce strings with `bool()`.
3. Reject booleans for integer fields, validate enum membership/ranges, and reject unknown fields.
4. Return a stable 400 response with field-level errors before any device call.
5. Publish the request/response schemas for the browser, panel, and external callers from one source of truth.

### Required tests

- Parameterize `false`, `"false"`, `0`, `"0"`, `null`, missing, and valid booleans for every boolean field.
- Assert invalid requests make zero calls to device fakes.
- Fuzz endpoint bodies with wrong JSON types and assert only stable 4xx responses, never 5xx.

### Acceptance criteria

- The same strict validation rules apply to browser, panel, and external API calls.
- No malformed boolean can be interpreted as `True`.

### Resources

- Project references: [`web_server.py`](web_server.py#L537), [`web_server.py`](web_server.py#L580), [`web_server.py`](web_server.py#L638)
- [JSON data-interchange grammar (RFC 8259)](https://www.rfc-editor.org/rfc/rfc8259)

---

## Verification performed during review

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | 105 tests passed. |
| `python -m compileall` | Passed for project Python files. |
| `node --check web/app.js` | Passed. |
| `python tuch_controller/render_preview.py` | Host firmware build passed; nine screens rendered. |
| Ruff / mypy | Not installed and no project configuration found. |
| CI / coverage configuration | Not found. |

## Notes on scope

- No real bulbs, vacuum, purifier, camera, or ESP32 hardware were exercised; all hardware-specific findings are based on code paths, documented APIs, host compilation, and focused local reproductions.
- Existing uncommitted edits were reviewed in place and were not modified by this report.
- Generated preview/build artifacts are ignored by Git and are not part of this report change.
