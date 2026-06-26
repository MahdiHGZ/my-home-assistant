# Merged Project Suggestions & Priorities

This document synthesizes and categorizes the recommendations from three different sources (`claude_opus_4.6_suggestions.md`, `gemini_3.1_pro_suggestion.md`, and `gemini_3.5_flash.md`). 

The suggestions are categorized by domain and prioritized using the following scale:
*   **P0:** Critical fixes / High-impact quick wins (Do these first).
*   **P1:** High-value architecture, UX, or stability features.
*   **P2:** Medium-value enhancements and quality-of-life improvements.
*   **P3:** Long-term polish and advanced concepts.

---

## 1. Performance & Core Architecture

*   **P0 - Server-Side Device Status Caching (TTL Cache):** Prevent `miio` handshake timeouts and LAN rate limits by serving status polls from a short-lived memory cache. *(Gemini 3.5)*
*   **P1 - Asyncio Migration & WebSockets:** Move from the synchronous `http.server` to an asynchronous framework (`aiohttp` or `FastAPI`), and upgrade unidirectional SSE to bidirectional WebSockets for zero-latency actions. *(Gemini 3.1)*
*   **P1 - Multi-Lane Execution Queue:** Prevent slow tasks (like camera captures) from blocking fast tasks (like turning on a light) by splitting the worker pool. *(Gemini 3.5)*
*   **P1 - Unified Device Registry / Plugin Architecture:** Extract device-specific code into dynamically loaded plugins or a unified registry to make adding new devices trivial. *(Claude 4.6 / Gemini 3.1)*
*   **P1 - Internal Pub/Sub Event Bus:** Decouple actions, the web server, and SSE by routing device state changes through a central event bus. *(Gemini 3.1)*
*   **P2 - Declarative Route Table & Type-Safe Dispatch:** Replace the `web_server.py` and `Controller` if/elif chains with clean, type-safe dictionaries. *(Claude 4.6)*
*   **P2 - Web Asset Caching & Compression:** Read `index.html`/JS/CSS at startup, gzip them, and serve with proper `Cache-Control` headers. *(Gemini 3.5)*

---

## 2. UI/UX & Web Frontend

*   **P1 - Gallery Thumbnail Generation:** Stop downloading 3MB RTSP frames for the gallery. Generate and cache 256px thumbnails. *(Gemini 3.5)*
*   **P1 - Scene Scheduler (Automations):** A lightweight `schedules.json` to handle time-based commands like "all off at 01:00". *(Claude 4.6)*
*   **P1 - Ambient Color Sync:** Tint the dashboard's glassmorphic background dynamically using the current dominant hue of active bulbs. *(Claude 4.6)*
*   **P2 - Time-Contextual Dashboard Adaptation:** Dynamically re-order dashboard cards based on time of day (e.g., Purifier in the morning, Camera at night). *(Gemini 3.1)*
*   **P2 - Notification Toast Queue:** Manage feedback toasts (like Undo) to prevent overlapping messages when clicking rapidly. *(Claude 4.6)*
*   **P2 - Bulb Group Presets:** Allow users to define groups (e.g., "Desk", "Bedroom") to control subsets of lights easily. *(Claude 4.6)*
*   **P2 - "Empty State" Onboarding Wizard:** Guide users through finding device IPs/tokens instead of just showing blank "offline" cards on first run. *(Gemini 3.1)*
*   **P3 - Spatial "Floorplan" View:** Allow uploading a room layout to drag-and-drop interactive device chips. *(Gemini 3.1)*
*   **P3 - Spring-Physics Animations & Theming:** Add Apple-style bouncy sliders, desktop keyboard shortcuts, and a Dark/Light mode toggle. *(Gemini 3.1 / Claude 4.6)*

---

## 3. Reliability, Observability & Security

*   **P0 - Congestion Handling:** If the action queue is overwhelmed, fail fast (`429 Too Many Requests`) instead of allowing UI actions to pile up and timeout slowly. *(Gemini 3.5)*
*   **P1 - Health Endpoint:** Expose `/api/health` for monitoring tools (Uptime Kuma) and systemd watchdogs to track process health. *(Claude 4.6)*
*   **P1 - Optional Shared-Secret (`WEB_TOKEN`):** Add an optional simple authentication token stored in `localStorage` to prevent guests from controlling the house. *(Gemini 3.5)*
*   **P1 - Connection Watchdog:** Ping devices periodically in the background to proactively invalidate dead connections before a user tries to interact with them. *(Claude 4.6)*
*   **P2 - Structured Telemetry (SQLite WAL / JSONL):** Track action history natively (who triggered what and when) using an embedded SQLite database. *(Claude 4.6 / Gemini 3.1)*
*   **P2 - Zero-Touch Config Hot-Reloading:** Watch `bulb.json` and `.env` for changes, or use mDNS to auto-discover devices, applying updates without dropping the web server. *(Gemini 3.1 / Gemini 3.5)*
*   **P2 - Graceful Shutdown:** Trap `SIGTERM` to save active states (e.g., party mode colors) to disk before exiting. *(Claude 4.6)*
*   **P3 - Explicit Garbage Collection:** Periodically invoke `gc.collect()` after heavy tasks (like computer vision) to protect memory on low-end servers. *(Gemini 3.1)*

---

## 4. AI, Camera & ESP32 Hardware Integrations

*   **P1 - Conversation Memory Sliding Window (Khatoon):** Append the last 3-5 turns to the system prompt so the LLM remembers the context of follow-up requests. *(Claude 4.6)*
*   **P1 - Gallery Retention Policy:** Auto-prune the `moments/` directory by age (90 days) or count to prevent disk exhaustion. *(Gemini 3.5)*
*   **P2 - Motion Detection on RTSP Substream:** Hook into the low-res camera stream (`stream2`) to trigger frame-differencing motion alerts. *(Gemini 3.5)*
*   **P2 - Doorbell Push & Bidirectional Panel:** Push motion events to the ESP32 to show the camera feed, and allow the ESP32 to send HTTP actions back to the server. *(Claude 4.6 / Gemini 3.5)*
*   **P2 - Tool Result Summarization:** Compress device status payloads before feeding them to the LLM to save tokens and improve latency. *(Claude 4.6)*
*   **P3 - Hardware OTA & Sleep Modes:** Flash the ESP32 over the network and dim its screen when no presence is detected in the room. *(Claude 4.6)*
*   **P3 - Khatoon CLI Pipe:** Expose the LLM through standard input for external shell scripting. *(Claude 4.6)*

---

## 5. Developer Experience (DX) & Testing

*   **P1 - Mock Device Mode & Single-Command Setup:** Use a `--mock` flag to run the server entirely without physical hardware, coupled with a `Makefile` for instant onboarding. *(Claude 4.6)*
*   **P2 - End-to-End Smoke Test:** A simple bash script to verify API routes before merging PRs. *(Claude 4.6)*
*   **P2 - Hot-Reload for Web Assets:** Send an SSE signal to refresh the browser when modifying frontend CSS/JS files during development. *(Claude 4.6)*
*   **P3 - Fuzzing & Property-Based Tests:** Stress test the MIoT batching and LLM tool-parser edge cases. *(Claude 4.6)*
