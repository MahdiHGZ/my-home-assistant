// =====================================================================
// tuch_controller — minimal dark-navy LAN remote for Coukab LAN
// =====================================================================
// ESP32-S3 + ILI9341 (320x240, landscape) + XPT2046 resistive touch.
// Arduino IDE sketch; companions: config.h (pins/palette), secrets.h (wifi).
// Hardware, wiring, server API and troubleshooting: HARDWARE.md.
// Roadmap and design rationale: SUGGESTIONS.md.
//
// Pages: MAIN -> LIGHTS (modes/color/brightness), MODES, COLOR (hue+sat),
//        VACUUM (start/stop/dock/pause/find), AIR (purifier).
//
// Responsiveness: all HTTP runs on a FreeRTOS worker task — touch never
// blocks on the network. Results return over a FreeRTOS *result queue*.
//
// Sync: GET /api/status/cached on boot (instant — server-side snapshot),
// then live SSE /api/events. POST success requires "ok": true.
//
// Accessibility: big targets + hit-slop, real GFX font, drag bars,
// hold-to-ramp brightness, on-device touch calibration (hold the COUKAB
// title ~3 s) stored in NVS, auto-return to MAIN, night-dimmed backlight,
// OTA updates (Arduino IDE network port: "coukab-panel").
//
// Offline: rendering/input is fully local; missing data and failed
// actions degrade with honest messages and automatic recovery.
//
// ---------------------------------------------------------------------
// Design notes (full rationale + status: SUGGESTIONS.md §0):
//   * Worker -> loop handoff is a FreeRTOS result queue (explicit
//     cross-core ownership; no volatile slots / spin-waits).
//   * Optimistic UI: power/mode/fan/light-mode commit on the control
//     immediately, before the server confirms.
//   * Persistent in-flight indicator dot + sticky error toasts.
//   * Faster SPI clock; diff-only brightness-bar repaint; whole-image
//     blit deadline instead of per-row.
//   * sseLine is a fixed char[] — no per-event String churn.
//   * Clock on MAIN; offline shown distinctly; brighter sub-text.
//   * Saturation strip on the COLOR page.
//   * Left-edge swipe = back; calibration auto-offered after repeated
//     edge-misses.
//   * SETUP diagnostics view; optional OTA password (#define in secrets.h).
// Known-open (server-side): /api/panel/scene, little-endian RGB565,
//   capability discovery + API-version handshake. Deferred (firmware):
//   worker watchdog (conflicts with the 60 s capture POST), piezo (hardware).
// =====================================================================

#include <Arduino.h>
#include <SPI.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoOTA.h>
#include <Preferences.h>
#include <time.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ILI9341.h>
#include <XPT2046_Touchscreen.h>
#include <Fonts/FreeSansBold9pt7b.h>
#include <esp_sleep.h>
#include <driver/gpio.h>

#include "config.h"
#include "secrets.h"

SPIClass tftSPI(FSPI);

Adafruit_ILI9341 tft(&tftSPI, TFT_DC, TFT_CS, TFT_RST);
XPT2046_Touchscreen ts(TOUCH_CS, TOUCH_IRQ);

// --------------------------------------------------
// Tuning (firmware-only; palette extends config.h without editing it)
// --------------------------------------------------
#ifndef TFT_SPI_HZ
#define TFT_SPI_HZ 40000000      // push the shared SPI bus; lower if unstable
#endif
// --- Runtime theme (dark / light) ------------------------------------------
// config.h's COL_* are the dark palette. To switch themes at runtime without
// touching every call site, we snapshot both palettes, then #undef the macros
// and re-declare COL_* as variables that applyTheme() reassigns. Every
// existing COL_BG / COL_CARD / ... reference then reads the live theme. (No
// COL_* is used in a static initializer, so this is safe.) COL_SUB is a
// brighter sub-text grey for WCAG; COL_OFFLINE marks absence distinctly.
struct ThemePalette {
  uint16_t bg, card, cardHi, edge, text, dim, accent, ok, err, warm, sub, offline;
};
static const ThemePalette THEME_DARK = {
  COL_BG, COL_CARD, COL_CARD_HI, COL_EDGE, COL_TEXT, COL_DIM,
  COL_ACCENT, COL_OK, COL_ERR, COL_WARM,
  0x8D17,   // sub     brighter grey-blue
  0x92CB,   // offline muted red-grey
};
static const ThemePalette THEME_LIGHT = {
  0xF7BF,   // bg      soft off-white
  0xE73D,   // card    light grey
  0xCEBB,   // cardHi  pressed (darker)
  0xB5F9,   // edge    grey border
  0x10C3,   // text    near-black
  0x6BB0,   // dim     mid grey
  0x02D9,   // accent  deep blue (contrast on light)
  0x1467,   // ok      green
  0xC8E3,   // err     red
  0xCC60,   // warm    amber
  0x5B0D,   // sub     dark grey small-text
  0xAA28,   // offline muted red
};
#undef COL_BG
#undef COL_CARD
#undef COL_CARD_HI
#undef COL_EDGE
#undef COL_TEXT
#undef COL_DIM
#undef COL_ACCENT
#undef COL_OK
#undef COL_ERR
#undef COL_WARM
uint16_t COL_BG = THEME_DARK.bg, COL_CARD = THEME_DARK.card,
         COL_CARD_HI = THEME_DARK.cardHi, COL_EDGE = THEME_DARK.edge,
         COL_TEXT = THEME_DARK.text, COL_DIM = THEME_DARK.dim,
         COL_ACCENT = THEME_DARK.accent, COL_OK = THEME_DARK.ok,
         COL_ERR = THEME_DARK.err, COL_WARM = THEME_DARK.warm,
         COL_SUB = THEME_DARK.sub, COL_OFFLINE = THEME_DARK.offline;
bool darkTheme = true;
// applyTheme() is defined *after* the type declarations below — Arduino
// auto-generates prototypes before the first function definition, so no
// function may precede enum Page / struct Btn / etc. (only data may).

// --- Display (backlight) brightness, user-set on SETUP, persisted in NVS ----
#define DISP_BRIGHT_MIN 30       // never let "on" go fully dark
uint8_t dispBrightDuty = BL_DUTY_FULL;

// --- Power management / sleep ----------------------------------------------
// Ladder: full -> dim -> backlight-off -> light-sleep -> (15 min) -> deep-sleep.
// Light sleep keeps RAM + Wi-Fi so SSE/alerts survive (~1-5 mA); after
// DEEP_SLEEP_AFTER_MS of unbroken light sleep it drops to deep sleep (~10 uA),
// where a timer wakes every DEEP_SLEEP_ALERT_POLL_S to poll /api/panel/alert and
// a touch (T_IRQ/GPIO7) wakes straight to MAIN. Set false for a panel that must
// stay fully live (the old always-on behavior).
#ifndef SLEEP_ENABLED
#define SLEEP_ENABLED true
#endif
#define LIGHT_SLEEP_WAKE_MS      3000UL          // timer wake to service SSE
#define DEEP_SLEEP_AFTER_MS      (15UL * 60 * 1000)
#define DEEP_SLEEP_ALERT_POLL_S  (15UL * 60)
#define ALERT_POLL_WIFI_MS       12000UL         // give up the poll if Wi-Fi is slow

// Survives deep sleep (RTC memory) so the alert-poll wake doesn't re-show an
// alert already seen before sleeping.
RTC_DATA_ATTR int rtcLastAlertId = 0;

// Left-edge swipe-to-back: a touch that starts within this band and travels
// right by SWIPE_BACK_DX is treated as "back". (§ roadmap)
#define EDGE_SWIPE_X0   24
#define SWIPE_BACK_DX   60
// After this many consecutive taps that land on *nothing*, offer calibration —
// the usual symptom of a drifted touch panel. (§5.6)
#define MISS_BEFORE_CAL 6

// --------------------------------------------------
// Pages and button ids
// --------------------------------------------------
enum Page { PAGE_MAIN, PAGE_LIGHTS, PAGE_MODES, PAGE_COLOR,
            PAGE_VACUUM, PAGE_AIR, PAGE_CAM, PAGE_SET };

enum BtnId {
  ID_NAV_LIGHTS, ID_NAV_VACUUM, ID_NAV_AIR, ID_NAV_CAM, ID_NAV_SET,
  ID_SCENE_NIGHT,
  ID_BACK,
  ID_MODE0, ID_MODE1, ID_MODE2, ID_MODE3, ID_MODE4, ID_MODE5,
  ID_MORE, ID_COLOR_PAGE,
  ID_BR_OFF, ID_BRIGHT, ID_BR_ON,
  ID_HUE, ID_SAT, ID_WHITE, ID_RANDOM,
  ID_VAC_START, ID_VAC_STOP, ID_VAC_DOCK, ID_VAC_PAUSE, ID_VAC_FIND,
  ID_PUR_POWER,
  ID_PMODE0, ID_PMODE1, ID_PMODE2, ID_PMODE3,
  ID_FAN0, ID_FAN1, ID_FAN2,
  ID_CAP, ID_CAP_FLASH, ID_CAP_VIEW,
  ID_SET_CAL, ID_SET_SYNC, ID_SET_DIAG, ID_SET_TIMEOUT, ID_SET_NIGHT,
  ID_SET_THEME, ID_SET_BRIGHT
};

struct Btn {
  int16_t x, y, w, h;
  const char* label;
  int8_t id;
};

// Main lighting modes — same set the keypad controller binds.
// MODE_MATCH = BulbMode.name reported by the server as lights.state.last_mode.
const char* MODE_LABELS[6] = { "COOL", "WARM", "SUNSET", "SLEEP", "LOVE", "MOVIE" };
const char* MODE_KEYS[6]   = { "cool_white", "warm_white", "sunset",
                               "sleep", "romantic", "movie" };
const char* MODE_MATCH[6]  = { "cool white", "warm white", "sunset",
                               "sleep", "romantic", "movie" };

// Purifier modes/fans: labels, match-names from /api/status, action ints.
const char* PUR_MODE_LABELS[4] = { "AUTO", "SLEEP", "FAV", "MAN" };
const char* PUR_MODE_MATCH[4]  = { "Auto", "Sleep", "Favorite", "Manual" };
const char* PUR_FAN_LABELS[3]  = { "LOW", "MED", "HIGH" };
const char* PUR_FAN_MATCH[3]   = { "Low", "Medium", "High" };

// Layout uses a strict 8 px gutter system:
//   3-col: 8 |96| 8 |96| 8 |96| 8 = 320      4-col: 8 |70| 8 |70| 8 |70| 8 |70| 8
//   2-col: 8 |148| 8 |148| 8                  rows start at y=52, end at y=232
Btn mainBtns[] = {
  {   8,  52,  96, 86, "LIGHTS", ID_NAV_LIGHTS },
  { 112,  52,  96, 86, "VACUUM", ID_NAV_VACUUM },
  { 216,  52,  96, 86, "AIR",    ID_NAV_AIR },
  {   8, 146,  96, 86, "CAMERA", ID_NAV_CAM },
  { 112, 146,  96, 86, "SETUP",  ID_NAV_SET },
  { 216, 146,  96, 86, "NIGHT",  ID_SCENE_NIGHT },
};

Btn lightsBtns[] = {
  {   4,   4,  60,  38, "",      ID_BACK },

  {   8,  52,  96,  64, "",      ID_MODE5 },   // MOVIE
  { 112,  52,  96,  64, "",      ID_MODE4 },   // LOVE
  { 216,  52,  96,  64, "MORE",  ID_MORE },

  {   8, 124, 304,  44, "COLOR", ID_COLOR_PAGE },

  {   8, 176,  56,  56, "",      ID_BR_OFF },
  {  72, 176, 176,  56, "",      ID_BRIGHT },
  { 256, 176,  56,  56, "",      ID_BR_ON },
};

Btn modesBtns[] = {
  {   4,   4,  60,  38, "", ID_BACK },
  {   8,  52,  96,  86, "", ID_MODE0 },
  { 112,  52,  96,  86, "", ID_MODE1 },
  { 216,  52,  96,  86, "", ID_MODE2 },
  {   8, 146,  96,  86, "", ID_MODE3 },
  { 112, 146,  96,  86, "", ID_MODE4 },
  { 216, 146,  96,  86, "", ID_MODE5 },
};

Btn colorBtns[] = {
  {   4,   4,  60,  38, "",       ID_BACK },
  {   8,  52, 304,  64, "",       ID_HUE },   // hue 0..359
  {   8, 124, 304,  36, "",       ID_SAT },   // saturation white->full (§5.4)
  { 112, 168,  96,  64, "WHITE",  ID_WHITE },
  { 216, 168,  96,  64, "RANDOM", ID_RANDOM },
};

Btn vacuumBtns[] = {
  {   4,   4,  60,  38, "",      ID_BACK },
  // Status card occupies y38..104 (drawn by drawVacStatusCard, not a button).
  // Primary pair:
  {   8, 112, 148, 56, "START", ID_VAC_START },
  { 164, 112, 148, 56, "STOP",  ID_VAC_STOP },
  // Secondary row:
  {   8, 176,  96, 56, "DOCK",  ID_VAC_DOCK },
  { 112, 176,  96, 56, "PAUSE", ID_VAC_PAUSE },
  { 216, 176,  96, 56, "FIND",  ID_VAC_FIND },
};

Btn airBtns[] = {
  {   4,   4,  60, 38, "", ID_BACK },
  {  68,   4,  60, 38, "", ID_PUR_POWER },

  {   8, 110,  70, 58, "", ID_PMODE0 },
  {  86, 110,  70, 58, "", ID_PMODE1 },
  { 164, 110,  70, 58, "", ID_PMODE2 },
  { 242, 110,  70, 58, "", ID_PMODE3 },

  {   8, 176,  96, 56, "", ID_FAN0 },
  { 112, 176,  96, 56, "", ID_FAN1 },
  { 216, 176,  96, 56, "", ID_FAN2 },
};

Btn camBtns[] = {
  {   4,   4,  60,  38, "",        ID_BACK },
  {   8,  52, 148, 110, "CAPTURE", ID_CAP },
  { 164,  52, 148, 110, "FLASH",   ID_CAP_FLASH },
  {   8, 170, 304,  62, "VIEW LAST PHOTO", ID_CAP_VIEW },
};

Btn setBtns[] = {
  {   4,   4,  60,  38, "",          ID_BACK },
  // Row 1
  {   8,  48, 100,  52, "CALIBRATE", ID_SET_CAL },
  { 114,  48, 100,  52, "SYNC",      ID_SET_SYNC },
  { 220,  48,  92,  52, "DIAG",      ID_SET_DIAG },
  // Row 2
  {   8, 106, 100,  52, "",          ID_SET_TIMEOUT },
  { 114, 106, 100,  52, "",          ID_SET_NIGHT },
  { 220, 106,  92,  52, "",          ID_SET_THEME },
  // Row 3: display backlight brightness (drag bar)
  {   8, 180, 304,  52, "",          ID_SET_BRIGHT },
};

// --------------------------------------------------
// Synced device state (from /api/status/cached and SSE pushes)
// --------------------------------------------------
struct DeviceState {
  bool valid = false;

  bool lightsAvail = false;
  int bulbsOn = 0;
  int bulbsTotal = 0;
  char lightMode[16] = "";   // lights.state.last_mode (BulbMode.name)

  bool vacAvail = false;
  char vacStatus[16] = "";
  int vacBattery = -1;

  bool purAvail = false;
  bool purOn = false;
  char purMode[12] = "";
  char purFan[10] = "";
  int pm25 = -1;
  int tempC = -999;
  int humidity = -1;

  int moments = -1;          // captured photos on the server
  int serverHour = -1;       // server's local hour (night-mode fallback)
};

DeviceState st;

Page currentPage = PAGE_MAIN;
int lastPressed = -1;            // confirmed-held button id, -1 = none
int lastRawHit = -2;             // for two-sample touch confirmation
bool ignoreUntilRelease = false; // swallow touch after page change / wake
int dragId = -1;                 // ID_BRIGHT / ID_HUE / ID_SAT while dragging
int curBrightness = 80;
int curHue = 0;                  // 0..359 for the color page
int curSat = 100;                // 0..100 saturation (white -> full hue) (§5.4)

// Press-down position, for left-edge swipe-to-back. (§ roadmap)
int16_t pressDownX = -1, pressDownY = -1;
int missStreak = 0;              // consecutive taps that hit nothing (§5.6)

// Hold-to-ramp state for the brightness off/on buttons.
unsigned long holdStartMs = 0;
bool holdRamped = false;

// Long-press on the COUKAB title opens touch calibration.
bool titleHold = false;
unsigned long titleHoldStart = 0;

// Toast region in the top bar (set per page in drawPage).
int16_t toastX0 = 68, toastX1 = 252;
// The clock (MAIN) yields the band while a toast is held this long. (§5.3/5.7)
unsigned long toastHoldUntil = 0;
#define TOAST_HOLD_MS 4000

enum BlState { BL_FULL, BL_DIM, BL_OFF };
BlState bl = BL_FULL;
bool screenOn = true;
unsigned long screenOffSince = 0;   // when the screen last turned off (sleep ladder)

// Deep-sleep alert-poll boot: woke on the 15-min timer to check for a new alert
// without lighting the screen, then re-sleep unless one is waiting.
bool wokeFromTouch = false;         // deep-sleep wake was a touch (ext1)
bool bootForAlertPoll = false;      // deep-sleep wake was the alert-poll timer
bool alertPollRequested = false;    // the one-shot alert fetch was queued
bool alertPollDone = false;         // its result came back (new alert or not)
unsigned long bootMs = 0;           // for the alert-poll Wi-Fi deadline

bool needSync = true;
bool syncToastPending = false;
bool netReady = false;           // one-time init done after Wi-Fi connects
bool previewActive = false;      // a camera photo covers the screen
bool statusPokePending = false;  // SSE said something changed
bool statusFetchQueued = false;  // dedupe: one panel-status fetch at a time

// Server-pushed alerts: SSE `event: alert` -> fetch id -> blit the
// server-rendered popup. All popup graphics come from the server; the
// firmware only adds the CLOSE button.
bool alertActive = false;        // an alert popup covers the screen
bool alertPokePending = false;   // SSE announced a new alert
int lastAlertId = 0;             // dedupe: last popup shown
bool diagActive = false;         // SETUP diagnostics overlay covers the screen (§7.6)

// Sticky errors: an error toast stays until a *successful* action or a page
// change clears it, so low-priority "syncing/synced" pings can't bury it. (§5.3)
bool errorSticky = false;

// Offline resilience: the UI is fully local — only data and actions need
// the server. This flag (written by the worker/SSE, read by the UI) drives
// honest messaging and fast-fail timeouts while the server is away.
volatile bool serverReachable = false;
unsigned long lastSyncRetryMs = 0;
unsigned long lastActivityMs = 0;
unsigned long lastHeaderUpdate = 0;
unsigned long lastActionMs = 0;

WiFiClient sseClient;
// Only the line *prefix* matters ("event: x" / "data:"), so a tiny fixed
// buffer avoids per-event String churn. (§6.5)
char sseLine[10];
uint8_t sseLineLen = 0;
unsigned long lastSseAttempt = 0;

// Runtime touch calibration (defaults from config.h, NVS overrides).
int tsMinX = TS_MINX, tsMaxX = TS_MAXX, tsMinY = TS_MINY, tsMaxY = TS_MAXY;

// Runtime settings (SETUP page, NVS-persisted). Dim kicks in at half
// the off timeout.
unsigned long offAfterMs = OFF_AFTER_MS;
bool nightEnabled = true;

// --------------------------------------------------
// Async HTTP worker — touch never waits on the network.
// Jobs go in on apiQueue; results come back on resultQueue. This makes the
// cross-core handoff an explicit ownership transfer (no volatile slots,
// no spin-waits — §4.2/§7.1). Drawing stays on the main task; SPI is unshared.
// --------------------------------------------------
#define JOB_POST   0
#define JOB_STATUS 1
#define JOB_ALERT  2

struct ApiJob {
  uint8_t type;
  uint32_t timeoutMs;  // 0 = default HTTP_TIMEOUT_MS
  char path[40];
  char body[200];
  char okMsg[40];
};

// One result frame, fully owned by whichever task currently holds it.
#define RES_TOAST  0   // a POST finished: text = message, ok = success
#define RES_STATUS 1   // text = /api/panel/status payload ("{}" on failure)
#define RES_ALERT  2   // text = /api/panel/alert payload
struct ApiResult {
  uint8_t type;
  bool ok;
  char text[384];   // flat panel status is ~250 B; margin against truncation
};

QueueHandle_t apiQueue = nullptr;
QueueHandle_t resultQueue = nullptr;

// True while the worker is mid-request; read by the UI to show the in-flight
// dot. Single writer (worker), single reader (loop) — volatile is enough. (§5.2)
volatile bool workerBusy = false;

// --------------------------------------------------
// Theme application (declared here, after all types, so it is the safe spot
// for Arduino's auto-prototypes; the palette data lives near the top).
// --------------------------------------------------
void applyTheme(bool dark) {
  const ThemePalette& t = dark ? THEME_DARK : THEME_LIGHT;
  COL_BG = t.bg; COL_CARD = t.card; COL_CARD_HI = t.cardHi; COL_EDGE = t.edge;
  COL_TEXT = t.text; COL_DIM = t.dim; COL_ACCENT = t.accent; COL_OK = t.ok;
  COL_ERR = t.err; COL_WARM = t.warm; COL_SUB = t.sub; COL_OFFLINE = t.offline;
  darkTheme = dark;
}

// --------------------------------------------------
// Tiny JSON helpers (server uses json.dumps: `"key": value`)
// --------------------------------------------------
int jsonKeyPos(const String& s, const char* key, int from) {
  String pat = String("\"") + key + "\"";
  int i = s.indexOf(pat, from);
  if (i < 0) return -1;
  i += pat.length();
  while (i < (int)s.length() && (s[i] == ' ' || s[i] == ':')) i++;
  return i;  // index of the value's first char
}

String jsonSection(const String& s, const char* key) {
  int i = jsonKeyPos(s, key, 0);
  if (i < 0 || s[i] != '{') return "";
  int depth = 0;
  for (int j = i; j < (int)s.length(); j++) {
    if (s[j] == '{') depth++;
    else if (s[j] == '}' && --depth == 0) return s.substring(i, j + 1);
  }
  return "";
}

String jsonStr(const String& s, const char* key) {
  int i = jsonKeyPos(s, key, 0);
  if (i < 0 || s[i] != '"') return "";
  int e = s.indexOf('"', i + 1);
  if (e < 0) return "";
  return s.substring(i + 1, e);
}

int jsonInt(const String& s, const char* key, int def) {
  int i = jsonKeyPos(s, key, 0);
  if (i < 0) return def;
  if (s[i] == '"') i++;  // numbers sometimes arrive as strings ("bright")
  if (s[i] != '-' && !isDigit(s[i])) return def;
  return s.substring(i).toInt();
}

bool jsonIsTrue(const String& s, const char* key) {
  int i = jsonKeyPos(s, key, 0);
  return i >= 0 && s.substring(i, i + 4) == "true";
}

// --------------------------------------------------
// Color helpers
// --------------------------------------------------
void hueToRgb(int hue, uint8_t& r, uint8_t& g, uint8_t& b) {
  int h = hue % 360;
  if (h < 0) h += 360;
  int x = 255 * (60 - abs(h % 120 - 60)) / 60;
  if (h < 60)       { r = 255; g = x;   b = 0;   }
  else if (h < 120) { r = x;   g = 255; b = 0;   }
  else if (h < 180) { r = 0;   g = 255; b = x;   }
  else if (h < 240) { r = 0;   g = x;   b = 255; }
  else if (h < 300) { r = x;   g = 0;   b = 255; }
  else              { r = 255; g = 0;   b = x;   }
}

uint16_t hueTo565(int hue) {
  uint8_t r, g, b;
  hueToRgb(hue, r, g, b);
  return tft.color565(r, g, b);
}

// Hue + saturation (0..100) at full value: blend the pure hue toward white as
// saturation drops, so the COLOR page can reach pastels and warm whites. (§5.4)
void hsvToRgb(int hue, int sat, uint8_t& r, uint8_t& g, uint8_t& b) {
  uint8_t hr, hg, hb;
  hueToRgb(hue, hr, hg, hb);
  sat = constrain(sat, 0, 100);
  r = 255 - (uint16_t)(255 - hr) * sat / 100;
  g = 255 - (uint16_t)(255 - hg) * sat / 100;
  b = 255 - (uint16_t)(255 - hb) * sat / 100;
}

uint16_t hsvTo565(int hue, int sat) {
  uint8_t r, g, b;
  hsvToRgb(hue, sat, r, g, b);
  return tft.color565(r, g, b);
}

// --------------------------------------------------
// Page button lookup
// --------------------------------------------------
Btn* pageButtons(int& count) {
  switch (currentPage) {
    case PAGE_LIGHTS: count = sizeof(lightsBtns) / sizeof(Btn); return lightsBtns;
    case PAGE_MODES:  count = sizeof(modesBtns)  / sizeof(Btn); return modesBtns;
    case PAGE_COLOR:  count = sizeof(colorBtns)  / sizeof(Btn); return colorBtns;
    case PAGE_VACUUM: count = sizeof(vacuumBtns) / sizeof(Btn); return vacuumBtns;
    case PAGE_AIR:    count = sizeof(airBtns)    / sizeof(Btn); return airBtns;
    case PAGE_CAM:    count = sizeof(camBtns)    / sizeof(Btn); return camBtns;
    case PAGE_SET:    count = sizeof(setBtns)    / sizeof(Btn); return setBtns;
    default:          count = sizeof(mainBtns)   / sizeof(Btn); return mainBtns;
  }
}

Btn* findBtn(int8_t id) {
  int n;
  Btn* btns = pageButtons(n);
  for (int i = 0; i < n; i++) {
    if (btns[i].id == id) return &btns[i];
  }
  return nullptr;
}

// --------------------------------------------------
// Icons (GFX primitives, centered on cx/cy)
// --------------------------------------------------
void iconBulb(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillCircle(cx, cy - 3, 11, color);
  tft.fillRect(cx - 5, cy + 7, 10, 4, color);
  tft.fillRect(cx - 3, cy + 12, 6, 3, color);
  tft.drawLine(cx - 17, cy - 15, cx - 13, cy - 11, color);
  tft.drawLine(cx + 17, cy - 15, cx + 13, cy - 11, color);
  tft.drawLine(cx, cy - 21, cx, cy - 16, color);
}

void iconVacuum(int16_t cx, int16_t cy, uint16_t color) {
  tft.drawCircle(cx, cy, 14, color);
  tft.drawCircle(cx, cy, 13, color);
  tft.fillCircle(cx, cy, 4, color);
  tft.drawLine(cx - 9, cy - 8, cx + 9, cy - 8, color);
}

void iconFanBlades(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillCircle(cx, cy - 9, 6, color);
  tft.fillCircle(cx - 8, cy + 5, 6, color);
  tft.fillCircle(cx + 8, cy + 5, 6, color);
  tft.fillCircle(cx, cy, 3, bg);
}

void iconPlay(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillTriangle(cx - 7, cy - 10, cx - 7, cy + 10, cx + 11, cy, color);
}

void iconStopSq(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillRect(cx - 9, cy - 9, 18, 18, color);
}

void iconPause(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillRect(cx - 8, cy - 8, 6, 16, color);
  tft.fillRect(cx + 2, cy - 8, 6, 16, color);
}

void iconDock(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillTriangle(cx - 10, cy - 1, cx + 10, cy - 1, cx, cy - 10, color);
  tft.fillRect(cx - 7, cy - 1, 14, 9, color);
}

void iconFind(int16_t cx, int16_t cy, uint16_t color) {
  tft.drawCircle(cx, cy, 9, color);
  tft.fillCircle(cx, cy, 3, color);
}

void iconPower(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.drawCircle(cx, cy + 1, 9, color);
  tft.drawCircle(cx, cy + 1, 8, color);
  tft.fillRect(cx - 2, cy - 11, 5, 10, bg);
  tft.fillRect(cx - 1, cy - 10, 3, 10, color);
}

void iconSun(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillCircle(cx, cy, 7, color);
  for (int i = 0; i < 8; i++) {
    float a = i * PI / 4.0f;
    int16_t x1 = cx + (int16_t)(cosf(a) * 10);
    int16_t y1 = cy + (int16_t)(sinf(a) * 10);
    int16_t x2 = cx + (int16_t)(cosf(a) * 14);
    int16_t y2 = cy + (int16_t)(sinf(a) * 14);
    tft.drawLine(x1, y1, x2, y2, color);
  }
}

void iconBack(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillTriangle(cx + 7, cy - 11, cx + 7, cy + 11, cx - 9, cy, color);
}

void iconHeart(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillCircle(cx - 5, cy - 3, 6, color);
  tft.fillCircle(cx + 5, cy - 3, 6, color);
  tft.fillTriangle(cx - 10, cy, cx + 10, cy, cx, cy + 11, color);
}

void iconMovie(int16_t cx, int16_t cy, uint16_t color) {
  tft.drawRect(cx - 11, cy - 8, 22, 16, color);
  tft.drawRect(cx - 10, cy - 7, 20, 14, color);
  tft.fillTriangle(cx - 3, cy - 4, cx - 3, cy + 4, cx + 5, cy, color);
}

void iconDots(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillCircle(cx - 10, cy, 3, color);
  tft.fillCircle(cx,      cy, 3, color);
  tft.fillCircle(cx + 10, cy, 3, color);
}

void iconCamera(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillRoundRect(cx - 13, cy - 8, 26, 18, 3, color);
  tft.fillRect(cx - 5, cy - 12, 10, 4, color);   // top hump
  tft.fillCircle(cx, cy + 1, 6, bg);             // lens hole
  tft.fillCircle(cx, cy + 1, 4, color);          // lens
}

void iconGear(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  for (int i = 0; i < 8; i++) {
    float a = i * PI / 4.0f;
    int16_t tx = cx + (int16_t)(cosf(a) * 11);
    int16_t ty = cy + (int16_t)(sinf(a) * 11);
    tft.fillCircle(tx, ty, 3, color);            // teeth
  }
  tft.fillCircle(cx, cy, 9, color);
  tft.fillCircle(cx, cy, 4, bg);                 // hub hole
}

void iconRefresh(int16_t cx, int16_t cy, uint16_t color) {
  tft.drawCircle(cx, cy, 9, color);
  tft.drawCircle(cx, cy, 8, color);
  tft.fillTriangle(cx + 5, cy - 13, cx + 5, cy - 3, cx + 13, cy - 8, color);
}

// --------------------------------------------------
// Text helpers — size >= 2 uses the FreeSansBold font (much more legible
// than the scaled 5x7 built-in); the offset math centers either font.
// --------------------------------------------------
void setFontFor(uint8_t size) {
  if (size >= 2) {
    tft.setFont(&FreeSansBold9pt7b);
    tft.setTextSize(size - 1);
  } else {
    tft.setFont(nullptr);
    tft.setTextSize(1);
  }
}

void drawCenteredText(const char* text, int16_t cx, int16_t cy, uint8_t size, uint16_t color) {
  int16_t x1, y1;
  uint16_t w, h;
  setFontFor(size);
  tft.setTextColor(color);
  tft.getTextBounds(text, 0, 0, &x1, &y1, &w, &h);
  tft.setCursor(cx - w / 2 - x1, cy - h / 2 - y1);
  tft.print(text);
  tft.setFont(nullptr);
}

void drawLeftText(const char* text, int16_t x, int16_t y, uint8_t size, uint16_t color) {
  int16_t x1, y1;
  uint16_t w, h;
  setFontFor(size);
  tft.setTextColor(color);
  tft.getTextBounds(text, 0, 0, &x1, &y1, &w, &h);
  tft.setCursor(x - x1, y - y1);
  tft.print(text);
  tft.setFont(nullptr);
}

// Top-bar toast: short status text where the page title sits.
void topbarText(const char* msg, uint16_t color, uint8_t size) {
  tft.fillRect(toastX0, 6, toastX1 - toastX0, 36, COL_BG);
  drawCenteredText(msg, (toastX0 + toastX1) / 2, 24, size, color);
}

void drawToast(const char* msg, uint16_t color) {
  // Don't let a low-priority info toast (e.g. "synced") bury a sticky error
  // until it is cleared by a success or a page change. (§5.3)
  if (errorSticky && color != COL_OK) return;
  if (color == COL_OK) errorSticky = false;  // a success clears the error
  topbarText(msg, color, 1);
  toastHoldUntil = millis() + TOAST_HOLD_MS;
}

// Failure toast: red bar, dark text — errors must be visible at a glance, and
// stay until acknowledged by a success or a page change.
void drawToastAlarm(const char* msg) {
  tft.fillRoundRect(toastX0, 6, toastX1 - toastX0, 36, 6, COL_ERR);
  drawCenteredText(msg, (toastX0 + toastX1) / 2, 24, 1, COL_BG);
  errorSticky = true;
  toastHoldUntil = millis() + TOAST_HOLD_MS;
}

// --------------------------------------------------
// Widgets
// --------------------------------------------------
// One gradient column of the brightness bar: dim blue -> warm bulb-white, so
// the bar visibly "gets brighter" as the level rises.
static void brightCol(Btn& b, int16_t px) {
  int pc = (int32_t)px * 100 / b.w;
  uint8_t r = 45 + (210 * pc) / 100;    //  45 -> 255
  uint8_t g = 60 + (165 * pc) / 100;    //  60 -> 225
  uint8_t bl = 110 + (40 * pc) / 100;   // 110 -> 150
  tft.drawFastVLine(b.x + px, b.y + 2, b.h - 4, tft.color565(r, g, bl));
}

// Repaint inner columns [px0,px1]: gradient where filled, card bg otherwise.
static void brightSpan(Btn& b, int16_t px0, int16_t px1, int16_t fillW) {
  px0 = constrain(px0, (int16_t)2, (int16_t)(b.w - 2));
  px1 = constrain(px1, (int16_t)2, (int16_t)(b.w - 2));
  for (int16_t px = px0; px <= px1; px++) {
    if (px < fillW - 1) brightCol(b, px);
    else tft.drawFastVLine(b.x + px, b.y + 2, b.h - 4, COL_CARD);
  }
}

static void brightLabel(Btn& b, int16_t fillW, int pct) {
  char s[8];
  snprintf(s, sizeof(s), "%d%%", pct);
  uint16_t txt = (fillW > b.w / 2) ? COL_BG : COL_TEXT;
  // Repaint the label band first (gradient/card) so shrinking digits leave no
  // ghost, then draw the text — cheap (~40 px) vs. a whole-bar redraw.
  brightSpan(b, b.w / 2 - 24, b.w / 2 + 24, fillW);
  drawCenteredText(s, b.x + b.w / 2, b.y + b.h / 2, 2, txt);
}

static int16_t prevBrFillW = -1;  // last painted fill width, for diff updates

// Shared gradient bar — bulb brightness AND display backlight use the same
// widget (dim-blue -> warm-white glow, centered "%"). `pct` is 0..100.
void drawGradientBar(Btn& b, int pct, bool pressed) {
  int16_t fillW = (int32_t)b.w * pct / 100;
  tft.fillRoundRect(b.x, b.y, b.w, b.h, 8, pressed ? COL_CARD_HI : COL_CARD);
  brightSpan(b, 2, b.w - 2, fillW);
  tft.drawRoundRect(b.x, b.y, b.w, b.h, 8, COL_EDGE);
  brightLabel(b, fillW, pct);
  prevBrFillW = fillW;
}

// Diff-only update while dragging: repaint just the columns between the old
// and new fill width, plus the label — no full-bar fillRoundRect. (§6.2)
void updateGradientBar(Btn& b, int pct) {
  int16_t fillW = (int32_t)b.w * pct / 100;
  if (prevBrFillW < 0) { drawGradientBar(b, pct, true); return; }
  if (fillW != prevBrFillW) {
    int16_t lo = min(prevBrFillW, fillW) - 1, hi = max(prevBrFillW, fillW) + 1;
    brightSpan(b, lo, hi, fillW);
  }
  brightLabel(b, fillW, pct);
  prevBrFillW = fillW;
}

// Bulb brightness uses the shared bar driven by curBrightness.
void drawBrightnessBar(Btn& b, bool pressed) { drawGradientBar(b, curBrightness, pressed); }
void updateBrightnessBar(Btn& b) { updateGradientBar(b, curBrightness); }

// --- saturation bar (COLOR page): white (left) -> full hue (right) (§5.4) ---
void drawSatColumns(Btn& b, int16_t px0, int16_t px1) {
  px0 = constrain(px0, 0, (int16_t)(b.w - 1));
  px1 = constrain(px1, 0, (int16_t)(b.w - 1));
  for (int16_t px = px0; px <= px1; px++) {
    tft.drawFastVLine(b.x + px, b.y, b.h, hsvTo565(curHue, (int32_t)px * 100 / b.w));
  }
}

void drawSatMarker(Btn& b) {
  int16_t mx = b.x + (int32_t)curSat * b.w / 100;
  mx = constrain(mx, (int16_t)(b.x + 1), (int16_t)(b.x + b.w - 2));
  tft.fillRect(mx - 2, b.y, 5, b.h, COL_TEXT);
  tft.fillRect(mx - 1, b.y + 2, 3, b.h - 4, hsvTo565(curHue, curSat));
}

void drawSatBar(Btn& b) {
  drawSatColumns(b, 0, b.w - 1);
  tft.drawRect(b.x - 1, b.y - 1, b.w + 2, b.h + 2, COL_EDGE);
  drawSatMarker(b);
}

void moveSatMarker(Btn& b, int oldSat) {
  int16_t ox = b.x + (int32_t)oldSat * b.w / 100;
  drawSatColumns(b, ox - b.x - 3, ox - b.x + 3);
  drawSatMarker(b);
}

// --- display backlight brightness bar (SETUP page) --------------------------
// Uses the same gradient widget as the bulb brightness bar; the "DISPLAY" label
// drawn above it (drawPage) says what it controls.
int dispBrightPct() {
  return constrain((dispBrightDuty - DISP_BRIGHT_MIN) * 100 / (255 - DISP_BRIGHT_MIN), 0, 100);
}

void drawDispBrightBar(Btn& b, bool pressed) {
  drawGradientBar(b, dispBrightPct(), pressed);
}

void drawHueColumns(Btn& b, int16_t px0, int16_t px1) {
  px0 = constrain(px0, 0, (int16_t)(b.w - 1));
  px1 = constrain(px1, 0, (int16_t)(b.w - 1));
  for (int16_t px = px0; px <= px1; px++) {
    tft.drawFastVLine(b.x + px, b.y, b.h, hueTo565((int32_t)px * 360 / b.w));
  }
}

void drawHueMarker(Btn& b) {
  int16_t mx = b.x + (int32_t)curHue * b.w / 360;
  mx = constrain(mx, (int16_t)(b.x + 1), (int16_t)(b.x + b.w - 2));
  tft.fillRect(mx - 2, b.y, 5, b.h, COL_TEXT);
  tft.fillRect(mx - 1, b.y + 3, 3, b.h - 6, hueTo565(curHue));
}

void drawHueBar(Btn& b) {
  drawHueColumns(b, 0, b.w - 1);
  tft.drawRect(b.x - 1, b.y - 1, b.w + 2, b.h + 2, COL_EDGE);
  drawHueMarker(b);
}

void moveHueMarker(Btn& b, int oldHue) {
  int16_t ox = b.x + (int32_t)oldHue * b.w / 360;
  drawHueColumns(b, ox - b.x - 3, ox - b.x + 3);
  drawHueMarker(b);
}

void drawColorPreview() {
  uint8_t r, g, b;
  hsvToRgb(curHue, curSat, r, g, b);
  tft.fillRoundRect(8, 168, 96, 64, 8, tft.color565(r, g, b));
  tft.drawRoundRect(8, 168, 96, 64, 8, COL_EDGE);
  char hex[10];
  snprintf(hex, sizeof(hex), "#%02X%02X%02X", r, g, b);
  // Dark text on light swatches, light text on dark — stays legible.
  uint16_t txt = (r + g + b > 384) ? COL_BG : COL_TEXT;
  drawCenteredText(hex, 56, 224, 1, txt);
}

void cardSubText(Btn& b, const char* text, uint16_t color) {
  tft.fillRect(b.x + 3, b.y + 64, b.w - 6, 16, COL_CARD);
  drawCenteredText(text, b.x + b.w / 2, b.y + 72, 1, color);
}

void drawCardSub(Btn& b) {
  char sub[20];
  if (b.id == ID_SCENE_NIGHT) {  // static scene description, no data needed
    cardSubText(b, "off+dock+sleep", COL_DIM);
    return;
  }
  if (!st.valid) {
    // No data yet — say why instead of an eternal "...".
    const char* msg = "...";
    if (!ENABLE_WIFI || WiFi.status() != WL_CONNECTED) msg = "no wifi";
    else if (netReady && !serverReachable) msg = "no server";
    cardSubText(b, msg, COL_SUB);
    return;
  }

  switch (b.id) {
    case ID_NAV_LIGHTS:
      if (!st.lightsAvail) { cardSubText(b, "offline", COL_OFFLINE); return; }
      snprintf(sub, sizeof(sub), "%d/%d on", st.bulbsOn, st.bulbsTotal);
      cardSubText(b, sub, st.bulbsOn > 0 ? COL_OK : COL_SUB);
      return;
    case ID_NAV_VACUUM:
      if (!st.vacAvail) { cardSubText(b, "offline", COL_OFFLINE); return; }
      if (st.vacBattery >= 0) {
        snprintf(sub, sizeof(sub), "%.9s %d%%", st.vacStatus, st.vacBattery);
      } else {
        snprintf(sub, sizeof(sub), "%.12s", st.vacStatus);
      }
      cardSubText(b, sub, COL_SUB);
      return;
    case ID_NAV_AIR:
      if (!st.purAvail) { cardSubText(b, "offline", COL_OFFLINE); return; }
      if (st.purOn && st.pm25 >= 0) {
        snprintf(sub, sizeof(sub), "PM2.5 %d", st.pm25);
        cardSubText(b, sub, COL_OK);
      } else {
        cardSubText(b, st.purOn ? "on" : "off", COL_SUB);
      }
      return;
    case ID_NAV_CAM:
      if (st.moments >= 0) {
        snprintf(sub, sizeof(sub), "%d saved", st.moments);
        cardSubText(b, sub, COL_SUB);
      } else {
        cardSubText(b, "capture", COL_SUB);
      }
      return;
    case ID_NAV_SET:
      snprintf(sub, sizeof(sub), "screen %lus", offAfterMs / 1000);
      cardSubText(b, sub, COL_SUB);
      return;
  }
}

void drawBtn(Btn& b, bool pressed) {
  if (b.id == ID_BRIGHT)     { drawBrightnessBar(b, pressed); return; }
  if (b.id == ID_HUE)        { drawHueBar(b); return; }
  if (b.id == ID_SAT)        { drawSatBar(b); return; }
  if (b.id == ID_SET_BRIGHT) { drawDispBrightBar(b, pressed); return; }

  bool selected = false;
  const char* label = b.label;
  if (b.id >= ID_PMODE0 && b.id <= ID_PMODE3) {
    label = PUR_MODE_LABELS[b.id - ID_PMODE0];
    selected = st.purAvail && strcasecmp(st.purMode, PUR_MODE_MATCH[b.id - ID_PMODE0]) == 0;
  } else if (b.id >= ID_FAN0 && b.id <= ID_FAN2) {
    label = PUR_FAN_LABELS[b.id - ID_FAN0];
    selected = st.purAvail && strcasecmp(st.purFan, PUR_FAN_MATCH[b.id - ID_FAN0]) == 0;
  } else if (b.id >= ID_MODE0 && b.id <= ID_MODE5) {
    label = MODE_LABELS[b.id - ID_MODE0];
    selected = st.lightMode[0] && strcasecmp(st.lightMode, MODE_MATCH[b.id - ID_MODE0]) == 0;
  } else if (b.id >= ID_VAC_START && b.id <= ID_VAC_FIND) {
    selected = (vacActiveId() == b.id);   // accent border on the active state
  }

  uint16_t fill   = pressed ? COL_CARD_HI : COL_CARD;
  uint16_t border = (pressed || selected) ? COL_ACCENT : COL_EDGE;
  uint16_t text   = selected ? COL_ACCENT : COL_TEXT;

  tft.fillRoundRect(b.x, b.y, b.w, b.h, 8, fill);
  tft.drawRoundRect(b.x, b.y, b.w, b.h, 8, border);

  int16_t cx = b.x + b.w / 2;
  int16_t cy = b.y + b.h / 2;

  switch (b.id) {
    case ID_BACK:
      iconBack(cx, cy, COL_ACCENT);
      return;
    case ID_PUR_POWER:
      iconPower(cx, cy, st.purOn ? COL_OK : COL_ERR, fill);
      return;
    case ID_BR_OFF:
      iconPower(cx, cy, COL_ERR, fill);
      return;
    case ID_BR_ON:
      iconSun(cx, cy, COL_WARM);
      return;

    case ID_NAV_LIGHTS:
      iconBulb(cx, b.y + 26, COL_WARM);
      drawCenteredText(b.label, cx, b.y + 52, 1, text);
      drawCardSub(b);
      return;
    case ID_NAV_VACUUM:
      iconVacuum(cx, b.y + 26, COL_ACCENT);
      drawCenteredText(b.label, cx, b.y + 52, 1, text);
      drawCardSub(b);
      return;
    case ID_NAV_AIR:
      iconFanBlades(cx, b.y + 26, COL_OK, fill);
      drawCenteredText(b.label, cx, b.y + 52, 1, text);
      drawCardSub(b);
      return;
    case ID_NAV_CAM:
      iconCamera(cx, b.y + 26, COL_ACCENT, fill);
      drawCenteredText(b.label, cx, b.y + 52, 1, text);
      drawCardSub(b);
      return;
    case ID_NAV_SET:
      iconGear(cx, b.y + 26, COL_DIM, fill);
      drawCenteredText(b.label, cx, b.y + 52, 1, text);
      drawCardSub(b);
      return;
    case ID_SCENE_NIGHT:
      // crescent moon
      tft.fillCircle(cx, b.y + 26, 12, COL_WARM);
      tft.fillCircle(cx + 7, b.y + 21, 11, fill);
      drawCenteredText(b.label, cx, b.y + 52, 1, text);
      drawCardSub(b);
      return;

    case ID_CAP:
      iconCamera(cx, b.y + 38, COL_ACCENT, fill);
      drawCenteredText(b.label, cx, b.y + 72, 2, text);
      drawCenteredText("no flash", cx, b.y + 94, 1, COL_DIM);
      return;
    case ID_CAP_FLASH:
      iconCamera(cx, b.y + 38, COL_WARM, fill);
      iconSun(cx + 34, b.y + 22, COL_WARM);
      drawCenteredText(b.label, cx, b.y + 72, 2, text);
      drawCenteredText("blink bulbs", cx, b.y + 94, 1, COL_DIM);
      return;
    case ID_CAP_VIEW:
      iconFind(b.x + 30, cy, COL_ACCENT);
      drawCenteredText(b.label, cx + 12, cy, 1, text);
      return;

    case ID_SET_CAL:
      calDrawCross(cx, b.y + 18, COL_ACCENT);
      drawCenteredText(b.label, cx, b.y + 40, 1, text);
      return;
    case ID_SET_SYNC:
      iconRefresh(cx, b.y + 18, COL_ACCENT);
      drawCenteredText(b.label, cx, b.y + 40, 1, text);
      return;
    case ID_SET_DIAG:
      // info glyph: circled "i"
      tft.drawCircle(cx, b.y + 18, 10, COL_ACCENT);
      tft.fillCircle(cx, b.y + 13, 2, COL_ACCENT);
      tft.fillRect(cx - 1, b.y + 17, 3, 7, COL_ACCENT);
      drawCenteredText(b.label, cx, b.y + 40, 1, text);
      return;
    case ID_SET_TIMEOUT: {
      char val[12];
      snprintf(val, sizeof(val), "%lus", offAfterMs / 1000);
      drawCenteredText(val, cx, b.y + 18, 2, COL_ACCENT);
      drawCenteredText("TIMEOUT", cx, b.y + 40, 1, text);
      return;
    }
    case ID_SET_NIGHT: {
      // crescent moon
      tft.fillCircle(cx, b.y + 18, 9, nightEnabled ? COL_WARM : COL_DIM);
      tft.fillCircle(cx + 6, b.y + 15, 8, fill);
      drawCenteredText(nightEnabled ? "NIGHT ON" : "NIGHT OFF", cx, b.y + 40, 1,
                       nightEnabled ? COL_WARM : text);
      return;
    }
    case ID_SET_THEME: {
      if (darkTheme) {              // currently dark -> show moon, tap = go light
        tft.fillCircle(cx, b.y + 18, 9, COL_ACCENT);
        tft.fillCircle(cx + 6, b.y + 15, 8, fill);
      } else {                      // currently light -> show sun
        iconSun(cx, b.y + 18, COL_WARM);
      }
      drawCenteredText(darkTheme ? "DARK" : "LIGHT", cx, b.y + 40, 1, text);
      return;
    }

    case ID_VAC_START:
      iconPlay(cx, b.y + 18, COL_OK);
      drawCenteredText(b.label, cx, b.y + 42, 2, COL_OK);
      return;
    case ID_VAC_STOP:
      iconStopSq(cx, b.y + 18, COL_ERR);
      drawCenteredText(b.label, cx, b.y + 42, 2, COL_ERR);
      return;
    case ID_VAC_DOCK:
      iconDock(cx, b.y + 18, COL_ACCENT);
      drawCenteredText(b.label, cx, b.y + 44, 1, text);
      return;
    case ID_VAC_PAUSE:
      iconPause(cx, b.y + 18, COL_TEXT);
      drawCenteredText(b.label, cx, b.y + 44, 1, text);
      return;
    case ID_VAC_FIND:
      iconFind(cx, b.y + 18, COL_ACCENT);
      drawCenteredText(b.label, cx, b.y + 44, 1, text);
      return;
  }

  // Lights-page mode shortcuts get icons above the label.
  if (currentPage == PAGE_LIGHTS && b.id == ID_MODE5) {
    iconMovie(cx, b.y + 20, COL_ACCENT);
    drawCenteredText(label, cx, b.y + 45, 2, text);
    return;
  }
  if (currentPage == PAGE_LIGHTS && b.id == ID_MODE4) {
    iconHeart(cx, b.y + 20, COL_ERR);
    drawCenteredText(label, cx, b.y + 45, 2, text);
    return;
  }
  if (b.id == ID_MORE) {
    iconDots(cx, b.y + 20, COL_DIM);
    drawCenteredText(label, cx, b.y + 45, 2, text);
    return;
  }
  if (b.id == ID_COLOR_PAGE) {
    for (int16_t px = 0; px < 60; px++) {
      tft.drawFastVLine(b.x + 14 + px, b.y + 10, b.h - 20, hueTo565(px * 6));
    }
    drawCenteredText(label, cx + 14, cy, 2, text);
    return;
  }

  drawCenteredText(label, cx, cy, 2, text);
}

void drawBtnById(int8_t id, bool pressed) {
  Btn* b = findBtn(id);
  if (b) drawBtn(*b, pressed);
}

// --------------------------------------------------
// Connection icon (main page, top-right): wifi arcs + dot.
//   green  = wifi + server stream connected
//   yellow = wifi ok, server not connected
//   red    = no wifi   |   grey = wifi disabled
// --------------------------------------------------
void drawConnIcon() {
  tft.fillRect(276, 2, SCREEN_W - 276 - 2, 44, COL_BG);

  int16_t cx = 298, cy = 32;
  uint16_t c;
  if (!ENABLE_WIFI)                        c = COL_DIM;
  else if (WiFi.status() != WL_CONNECTED)  c = COL_ERR;
  else if (!sseClient.connected())         c = COL_WARM;
  else                                     c = COL_OK;

  tft.fillCircle(cx, cy, 3, c);
  tft.drawCircleHelper(cx, cy, 7,  3, c);   // 3 = both top quarters
  tft.drawCircleHelper(cx, cy, 8,  3, c);
  tft.drawCircleHelper(cx, cy, 12, 3, c);
  tft.drawCircleHelper(cx, cy, 13, 3, c);
  tft.drawCircleHelper(cx, cy, 17, 3, c);
  tft.drawCircleHelper(cx, cy, 18, 3, c);

  if (ENABLE_WIFI && WiFi.status() != WL_CONNECTED) {
    tft.drawLine(cx - 14, cy + 6, cx + 12, cy - 16, COL_ERR);
    tft.drawLine(cx - 13, cy + 7, cx + 13, cy - 15, COL_ERR);
  }

  // In-flight indicator: a small accent dot above the arcs while a request is
  // queued or running, so a tap never feels like it vanished. (§5.2)
  bool busy = workerBusy || (apiQueue && uxQueueMessagesWaiting(apiQueue) > 0);
  if (busy) tft.fillCircle(280, 9, 4, COL_ACCENT);
}

// "HH:MM" from NTP, or "HH:--" from the server hour, or false if unknown.
bool currentClock(char* buf, size_t n) {
  time_t now = time(nullptr);
  if (now >= 1600000000) {
    struct tm t;
    localtime_r(&now, &t);
    snprintf(buf, n, "%02d:%02d", t.tm_hour, t.tm_min);
    return true;
  }
  if (st.serverHour >= 0) { snprintf(buf, n, "%02d:--", st.serverHour); return true; }
  return false;
}

void drawMainHeader() {
  drawLeftText("COUKAB", 8, 8, 2, COL_TEXT);

  // Clock shares the MAIN toast band; it yields while a toast is held or an
  // error is sticky, and reclaims the space afterward. (§5.7)
  if (millis() > toastHoldUntil && !errorSticky) {
    char clk[8];
    tft.fillRect(156, 4, 114, 40, COL_BG);   // cover the full toast band
    if (currentClock(clk, sizeof(clk))) drawCenteredText(clk, 213, 22, 2, COL_SUB);
  }

  tft.fillRect(8, 30, 140, 12, COL_BG);
  tft.setTextSize(1);
  tft.setTextColor(COL_DIM);
  tft.setCursor(8, 32);
  if (ENABLE_WIFI && WiFi.status() == WL_CONNECTED) {
    tft.print(WiFi.localIP());
    if (!sseClient.connected()) {
      tft.setTextColor(COL_ERR);
      tft.print("  server?");
    }
  } else {
    tft.print(ENABLE_WIFI ? "no network" : "wifi disabled");
  }

  drawConnIcon();
}

// Case-insensitive substring (avoids the non-portable strcasestr).
static bool ciContains(const char* hay, const char* needle) {
  size_t nl = strlen(needle);
  for (const char* p = hay; *p; p++)
    if (strncasecmp(p, needle, nl) == 0) return true;
  return false;
}

// Which action button reflects the current device state (for the live
// "active" highlight on the VACUUM page).
int vacActiveId() {
  if (!st.vacAvail || !st.vacStatus[0]) return -1;
  if (ciContains(st.vacStatus, "paus")) return ID_VAC_PAUSE;
  if (ciContains(st.vacStatus, "sweep") || ciContains(st.vacStatus, "mop") ||
      ciContains(st.vacStatus, "clean")) return ID_VAC_START;
  if (ciContains(st.vacStatus, "charg") || ciContains(st.vacStatus, "dock")) return ID_VAC_DOCK;
  return -1;
}

// Short, friendly state label into `out`; returns its color.
static uint16_t vacStateLabel(char* out, size_t n) {
  const char* s = st.vacStatus;
  if (ciContains(s, "sweep") && ciContains(s, "mop")) { strlcpy(out, "Sweep+Mop", n); return COL_ACCENT; }
  if (ciContains(s, "sweep"))    { strlcpy(out, "Sweeping", n); return COL_ACCENT; }
  if (ciContains(s, "mop"))      { strlcpy(out, "Mopping", n);  return COL_ACCENT; }
  if (ciContains(s, "paus"))     { strlcpy(out, "Paused", n);   return COL_WARM; }
  if (ciContains(s, "complete")) { strlcpy(out, "Charged", n);  return COL_OK; }
  if (ciContains(s, "charg"))    { strlcpy(out, "Charging", n); return COL_WARM; }
  if (ciContains(s, "dock"))     { strlcpy(out, "Docking", n);  return COL_ACCENT; }
  if (ciContains(s, "idle"))     { strlcpy(out, "Idle", n);     return COL_SUB; }
  if (ciContains(s, "sleep"))    { strlcpy(out, "Asleep", n);   return COL_SUB; }
  if (s[0]) { strlcpy(out, s, n); return COL_TEXT; }   // unknown firmware state: show raw
  strlcpy(out, "—", n); return COL_SUB;
}

// Prominent status strip for the VACUUM page: state on the left, battery
// (number + level bar, color-coded) on the right. Replaces the old cramped
// corner text so the two things that matter are glanceable.
void drawVacStatusCard() {
  const int16_t X = 8, Y = 38, W = 304, H = 66;
  tft.fillRoundRect(X, Y, W, H, 8, COL_CARD);
  tft.drawRoundRect(X, Y, W, H, 8, COL_EDGE);

  if (!st.valid)    { drawCenteredText("connecting...", X + W / 2, Y + H / 2, 2, COL_SUB); return; }
  if (!st.vacAvail) { drawCenteredText("offline",       X + W / 2, Y + H / 2, 2, COL_OFFLINE); return; }

  char label[16];
  uint16_t sc = vacStateLabel(label, sizeof(label));
  drawLeftText("STATUS", X + 16, Y + 12, 1, COL_SUB);
  drawLeftText(label,    X + 16, Y + 28, 2, sc);

  if (st.vacBattery >= 0) {
    int pct = constrain(st.vacBattery, 0, 100);
    uint16_t bc = pct > 50 ? COL_OK : (pct > 20 ? COL_WARM : COL_ERR);
    int16_t rcx = X + W - 70;
    char pb[6];
    snprintf(pb, sizeof(pb), "%d%%", pct);
    drawCenteredText("BATTERY", rcx, Y + 12, 1, COL_SUB);
    drawCenteredText(pb,        rcx, Y + 30, 2, bc);
    int16_t lbx = rcx - 58, lby = Y + 48, lbw = 116, lbh = 7;
    tft.drawRoundRect(lbx, lby, lbw, lbh, 2, COL_EDGE);
    int fw = (lbw - 2) * pct / 100;
    if (fw > 0) tft.fillRect(lbx + 1, lby + 1, fw, lbh - 2, bc);
  }
}

void drawCamInfo() {
  tft.fillRect(198, 4, 78, 40, COL_BG);
  if (st.moments < 0) return;
  tft.setTextSize(1);
  tft.setTextColor(COL_DIM);
  tft.setCursor(206, 18);
  tft.print(st.moments);
  tft.print(" saved");
}

void airTileValue(int idx, const char* value, uint16_t color) {
  int16_t x = 8 + idx * 104;
  tft.fillRect(x + 2, 70, 92, 28, COL_CARD);
  drawCenteredText(value, x + 48, 83, 2, color);
}

void drawAirValues() {
  char buf[12];
  if (!st.valid || !st.purAvail) {
    airTileValue(0, "-", COL_DIM);
    airTileValue(1, "-", COL_DIM);
    airTileValue(2, "-", COL_DIM);
    return;
  }
  if (st.pm25 >= 0) {
    snprintf(buf, sizeof(buf), "%d", st.pm25);
    airTileValue(0, buf, st.pm25 <= 35 ? COL_OK : (st.pm25 <= 75 ? COL_WARM : COL_ERR));
  } else airTileValue(0, "-", COL_DIM);

  if (st.tempC > -999) {
    snprintf(buf, sizeof(buf), "%dC", st.tempC);
    airTileValue(1, buf, COL_TEXT);
  } else airTileValue(1, "-", COL_DIM);

  if (st.humidity >= 0) {
    snprintf(buf, sizeof(buf), "%d%%", st.humidity);
    airTileValue(2, buf, COL_TEXT);
  } else airTileValue(2, "-", COL_DIM);
}

void drawAirTiles() {
  const char* labels[3] = { "PM2.5", "TEMP", "HUMIDITY" };
  for (int i = 0; i < 3; i++) {
    int16_t x = 8 + i * 104;
    tft.fillRoundRect(x, 52, 96, 50, 8, COL_CARD);
    tft.drawRoundRect(x, 52, 96, 50, 8, COL_EDGE);
    drawCenteredText(labels[i], x + 48, 61, 1, COL_DIM);
  }
  drawAirValues();
}

void drawPage() {
  tft.fillScreen(COL_BG);

  switch (currentPage) {
    case PAGE_MAIN:
      // Starts right of the header text zone (title + IP/server line end
      // ~x=150) so the 2 s header refresh can never punch into a toast.
      toastX0 = 156; toastX1 = 270;
      drawMainHeader();
      break;
    case PAGE_LIGHTS:
      toastX0 = 68; toastX1 = 272;
      topbarText("LIGHTS", COL_TEXT, 2);
      break;
    case PAGE_MODES:
      toastX0 = 68; toastX1 = 272;
      topbarText("MODES", COL_TEXT, 2);
      break;
    case PAGE_COLOR:
      toastX0 = 68; toastX1 = 272;
      topbarText("COLOR", COL_TEXT, 2);
      break;
    case PAGE_VACUUM:
      toastX0 = 68; toastX1 = 252;
      topbarText("VACUUM", COL_TEXT, 2);
      drawVacStatusCard();
      break;
    case PAGE_AIR:
      toastX0 = 132; toastX1 = 272;
      topbarText("AIR", COL_TEXT, 2);
      drawAirTiles();
      break;
    case PAGE_CAM:
      toastX0 = 68; toastX1 = 196;
      topbarText("CAMERA", COL_TEXT, 2);
      drawCamInfo();
      break;
    case PAGE_SET:
      toastX0 = 68; toastX1 = 272;
      topbarText("SETUP", COL_TEXT, 2);
      break;
  }

  // Connection icon lives in the top-right corner of every page.
  if (currentPage != PAGE_MAIN) drawConnIcon();

  int n;
  Btn* btns = pageButtons(n);
  for (int i = 0; i < n; i++) {
    drawBtn(btns[i], false);
  }

  if (currentPage == PAGE_COLOR) {
    drawColorPreview();
  }
  if (currentPage == PAGE_SET) {
    drawCenteredText("DISPLAY", 160, 170, 1, COL_DIM);   // label above the brightness bar
  }
}

// Targeted redraw of live values after a status update (no full flicker).
void refreshDynamic() {
  if (!screenOn || previewActive || alertActive || diagActive) return;
  switch (currentPage) {
    case PAGE_MAIN: {
      int n;
      Btn* btns = pageButtons(n);
      for (int i = 0; i < n; i++) drawCardSub(btns[i]);
      break;
    }
    case PAGE_LIGHTS: {
      Btn* b = findBtn(ID_BRIGHT);
      if (b && dragId != ID_BRIGHT && lastPressed != ID_BRIGHT) drawBrightnessBar(*b, false);
      if (lastPressed != ID_MODE4) drawBtnById(ID_MODE4, false);
      if (lastPressed != ID_MODE5) drawBtnById(ID_MODE5, false);
      break;
    }
    case PAGE_MODES:
      for (int8_t id = ID_MODE0; id <= ID_MODE5; id++)
        if (id != lastPressed) drawBtnById(id, false);
      break;
    case PAGE_VACUUM:
      drawVacStatusCard();
      for (int8_t id = ID_VAC_START; id <= ID_VAC_FIND; id++)
        if (id != lastPressed) drawBtnById(id, false);  // refresh active highlight
      break;
    case PAGE_CAM:
      drawCamInfo();
      break;
    case PAGE_AIR:
      drawAirValues();
      if (lastPressed != ID_PUR_POWER) drawBtnById(ID_PUR_POWER, false);
      for (int8_t id = ID_PMODE0; id <= ID_PMODE3; id++)
        if (id != lastPressed) drawBtnById(id, false);
      for (int8_t id = ID_FAN0; id <= ID_FAN2; id++)
        if (id != lastPressed) drawBtnById(id, false);
      break;
    default:
      break;
  }
}

void showPage(Page p) {
  currentPage = p;
  lastPressed = -1;
  lastRawHit = -2;
  dragId = -1;
  previewActive = false;
  alertActive = false;
  diagActive = false;
  errorSticky = false;        // a deliberate page change acknowledges errors (§5.3)
  toastHoldUntil = 0;
  ignoreUntilRelease = true;  // finger is still down from the tap that got us here
  drawPage();

  // Entering a page while disconnected: say so up front instead of letting
  // the first action surprise the user. The UI itself stays fully usable.
  if (ENABLE_WIFI && WiFi.status() != WL_CONNECTED) {
    drawToastAlarm("no wifi");
  } else if (netReady && !serverReachable && !sseClient.connected()) {
    drawToastAlarm("server offline");
  }
}

// --------------------------------------------------
// Backlight: full -> dim (30 s) -> off (60 s); wake on touch.
// Night hours use a lower "full" level (NTP time).
// --------------------------------------------------
bool isNightNow() {
  if (!nightEnabled) return false;
  int hour = -1;
  time_t now = time(nullptr);
  if (now >= 1600000000) {        // NTP synced: authoritative
    struct tm t;
    localtime_r(&now, &t);
    hour = t.tm_hour;
  } else {
    hour = st.serverHour;          // fallback: hour from /api/panel/status
  }
  if (hour < 0) return false;
  return hour >= NIGHT_START_HOUR || hour < NIGHT_END_HOUR;
}

void setBacklight(BlState s) {
  bl = s;
  uint8_t duty = 0;
  if (s == BL_FULL) {
    // User brightness is the ceiling; night mode caps it lower.
    duty = dispBrightDuty;
    if (isNightNow() && duty > BL_DUTY_NIGHT) duty = BL_DUTY_NIGHT;
  } else if (s == BL_DIM) {
    duty = min((uint8_t)BL_DUTY_DIM, dispBrightDuty);
  }
  analogWrite(TFT_BL, duty);
}

// --------------------------------------------------
// Sleep ladder (see "Power management" near the top).
//   light sleep: CPU halts between events, Wi-Fi/RAM/SSE preserved; wakes on a
//                short timer (to service SSE) or a touch on T_IRQ.
//   deep sleep:  full power-down; wakes on a touch (ext1) -> MAIN, or on the
//                alert-poll timer -> fetch /api/panel/alert, then re-sleep.
// --------------------------------------------------
void enterLightSleep() {
  esp_sleep_enable_timer_wakeup((uint64_t)LIGHT_SLEEP_WAKE_MS * 1000ULL);
  gpio_wakeup_enable((gpio_num_t)TOUCH_IRQ, GPIO_INTR_LOW_LEVEL);  // touch is active-low
  esp_sleep_enable_gpio_wakeup();
  esp_light_sleep_start();   // returns after timer or touch wake
}

void enterDeepSleep() {
  rtcLastAlertId = lastAlertId;       // remember what we've already shown
  setBacklight(BL_OFF);
  Serial.println("Entering deep sleep.");
  Serial.flush();
  esp_sleep_enable_timer_wakeup((uint64_t)DEEP_SLEEP_ALERT_POLL_S * 1000000ULL);
  esp_sleep_enable_ext1_wakeup(1ULL << TOUCH_IRQ, ESP_EXT1_WAKEUP_ANY_LOW);
  esp_deep_sleep_start();    // never returns; wake = full reset
}

void wakeScreen() {
  screenOn = true;
  lastActivityMs = millis();
  ignoreUntilRelease = true;  // the waking touch must not press anything
  lastPressed = -1;
  lastRawHit = -2;
  dragId = -1;
  previewActive = false;
  alertActive = false;
  diagActive = false;
  errorSticky = false;
  bootForAlertPoll = false;   // a touch means the user is interacting now
  currentPage = PAGE_MAIN;    // always wake on the main page
  drawPage();
  setBacklight(BL_FULL);
  if (!sseClient.connected()) needSync = true;  // we may have missed pushes
  Serial.println("Screen on.");
}

void updateBacklight() {
  unsigned long idle = millis() - lastActivityMs;
  if (idle >= offAfterMs) {
    if (screenOn) {
      screenOn = false;
      screenOffSince = millis();   // start of the light-sleep window
      setBacklight(BL_OFF);
      Serial.println("Screen off (inactivity).");
    }
  } else if (idle >= offAfterMs / 2) {
    if (bl == BL_FULL) setBacklight(BL_DIM);
  } else {
    if (bl == BL_DIM) setBacklight(BL_FULL);  // touch during dim restores
  }
}

// --------------------------------------------------
// Status parsing + sync
// --------------------------------------------------
// Parses the flat /api/panel/status payload (see offload_design.md) —
// every key is top-level, so plain key lookups are unambiguous.
void applyStatusJson(const String& js) {
  if (js.length() < 2 || js[0] != '{' || !jsonIsTrue(js, "ok")) return;

  st.lightsAvail = jsonIsTrue(js, "lights_avail");
  st.bulbsOn     = jsonInt(js, "bulbs_on", 0);
  st.bulbsTotal  = jsonInt(js, "bulbs_total", 0);
  strlcpy(st.lightMode, jsonStr(js, "last_mode").c_str(), sizeof(st.lightMode));
  if (dragId != ID_BRIGHT) {
    int b = jsonInt(js, "brightness", -1);
    if (b >= 1 && b <= 100) curBrightness = b;
  }

  st.vacAvail = jsonIsTrue(js, "vac_avail");
  strlcpy(st.vacStatus, jsonStr(js, "vac_status").c_str(), sizeof(st.vacStatus));
  st.vacBattery = jsonInt(js, "vac_battery", -1);

  st.purAvail = jsonIsTrue(js, "pur_avail");
  st.purOn    = jsonIsTrue(js, "pur_on");
  strlcpy(st.purMode, jsonStr(js, "pur_mode").c_str(), sizeof(st.purMode));
  strlcpy(st.purFan, jsonStr(js, "pur_fan").c_str(), sizeof(st.purFan));
  st.pm25     = jsonInt(js, "pm25", -1);
  st.tempC    = jsonInt(js, "temp", -999);
  st.humidity = jsonInt(js, "hum", -1);

  st.moments    = jsonInt(js, "moments", -1);
  st.serverHour = jsonInt(js, "hour", -1);

  st.valid = true;
}

// SSE: hold /api/events open; each `data:` line is a full status snapshot.
void pumpSSE() {
  if (!ENABLE_WIFI || WiFi.status() != WL_CONNECTED) return;

  if (!sseClient.connected()) {
    if (millis() - lastSseAttempt < 5000) return;
    lastSseAttempt = millis();
    if (sseClient.connect(API_HOST, API_PORT, 3000)) {
      sseClient.print(String("GET /api/events HTTP/1.1\r\nHost: ") + API_HOST +
                      "\r\nAccept: text/event-stream\r\nX-Coukab-Panel: 1"
                      "\r\nConnection: keep-alive\r\n\r\n");
      sseLineLen = 0;
      serverReachable = true;
      Serial.println("SSE connected.");
      if (screenOn && !previewActive) drawConnIcon();
    }
    return;
  }

  // Trigger-only: we never buffer the (multi-KB) event payload. The
  // `event:` line names the frame ("status" or "alert"); its `data:` line
  // just schedules the matching tiny fetch. Only the 8-char prefix is kept,
  // in a fixed buffer — no per-event String allocation. (§6.5)
  static char sseEvent = 's';
  int guard = 0;
  while (sseClient.available() && guard++ < 16384) {
    char c = (char)sseClient.read();
    if (c == '\n') {
      sseLine[sseLineLen] = '\0';
      if (strncmp(sseLine, "event: ", 7) == 0) {
        sseEvent = sseLineLen > 7 ? sseLine[7] : 's';
      } else if (strncmp(sseLine, "data:", 5) == 0) {
        if (sseEvent == 'a') alertPokePending = true;
        else statusPokePending = true;
        sseEvent = 's';  // frame consumed; default back to status
      }
      sseLineLen = 0;
    } else if (c != '\r') {
      if (sseLineLen < sizeof(sseLine) - 1) sseLine[sseLineLen++] = c;
    }
  }
}

// --------------------------------------------------
// HTTP (worker task only) — success = HTTP 200 AND "ok": true in the body
// (the server can answer 200 with ok:false when a device is offline).
// --------------------------------------------------
bool apiPost(const char* path, const char* jsonBody, int& httpCode, uint32_t timeoutMs) {
  if (ENABLE_WIFI && WiFi.status() != WL_CONNECTED) {
    httpCode = -1;
    return false;
  }

  WiFiClient client;
  HTTPClient http;
  String url = String("http://") + API_HOST + ":" + String(API_PORT) + path;

  if (!http.begin(client, url)) {
    httpCode = -2;
    return false;
  }

  if (timeoutMs == 0) timeoutMs = HTTP_TIMEOUT_MS;
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(timeoutMs);
  // Fast probe while the server is known-down so queued actions fail in
  // ~2.5 s instead of stacking 6 s timeouts.
  http.setConnectTimeout(serverReachable ? HTTP_TIMEOUT_MS : 2500);

  Serial.printf("POST %s  %s\n", url.c_str(), jsonBody);

  httpCode = http.POST((uint8_t*)jsonBody, strlen(jsonBody));
  bool ok = false;
  if (httpCode == 200) {
    ok = jsonIsTrue(http.getString(), "ok");
  }

  Serial.printf("  -> %d %s\n", httpCode, ok ? "ok" : "not-ok");

  http.end();
  return ok;
}

String httpGetJson(const char* path, uint32_t timeoutMs) {
  String out = "";
  if (ENABLE_WIFI && WiFi.status() != WL_CONNECTED) return out;

  WiFiClient client;
  HTTPClient http;
  String url = String("http://") + API_HOST + ":" + String(API_PORT) + path;
  if (http.begin(client, url)) {
    http.setTimeout(timeoutMs);
    http.setConnectTimeout(serverReachable ? HTTP_TIMEOUT_MS : 2500);
    if (http.GET() == 200) out = http.getString();
    http.end();
  }
  return out;
}

// Hand one finished result to loop() over the result queue. Blocks the worker
// only if loop() is briefly behind (queue full) — bounded, and far cleaner
// than the old volatile-slot spin-waits.
static void pushResult(uint8_t type, bool ok, const char* text) {
  ApiResult res;
  res.type = type;
  res.ok = ok;
  strlcpy(res.text, text ? text : "", sizeof(res.text));
  if (resultQueue) xQueueSend(resultQueue, &res, portMAX_DELAY);
}

void apiWorkerTask(void* arg) {
  ApiJob job;
  for (;;) {
    if (xQueueReceive(apiQueue, &job, portMAX_DELAY) != pdTRUE) continue;
    workerBusy = true;

    if (job.type == JOB_POST) {
      int code;
      bool ok = apiPost(job.path, job.body, code, job.timeoutMs);
      // code > 0 means the server answered — reachable even if the device
      // behind it wasn't. code <= 0 is a transport failure.
      if (code > 0) serverReachable = true;
      else if (code != -1) serverReachable = false;  // -1 = wifi down, says nothing about the server
      const char* msg = job.okMsg;
      if (!ok) {
        if (code == 200)      msg = "device offline";
        else if (code == -1)  msg = "no wifi";
        else                  msg = "server unreachable";
      }
      pushResult(RES_TOAST, ok, msg);
    } else if (job.type == JOB_STATUS) {  // always deliver so loop() clears its dedupe flag
      String payload = httpGetJson("/api/panel/status", STATUS_TIMEOUT_MS);
      if (payload.length()) serverReachable = true;
      else if (WiFi.status() == WL_CONNECTED) serverReachable = false;
      pushResult(RES_STATUS, payload.length() > 0, payload.length() ? payload.c_str() : "{}");
    } else {  // JOB_ALERT — fetch the alert metadata (id) for dedupe
      String payload = httpGetJson("/api/panel/alert", HTTP_TIMEOUT_MS);
      if (payload.length()) serverReachable = true;
      pushResult(RES_ALERT, payload.length() > 0, payload.length() ? payload.c_str() : "{}");
    }

    workerBusy = false;
  }
}

bool enqueueJob(ApiJob& job) {
  return apiQueue && xQueueSend(apiQueue, &job, 0) == pdTRUE;
}

void apiAndReportLong(const char* path, const String& body, const char* okMsg,
                      uint32_t timeoutMs) {
  ApiJob job;
  job.type = JOB_POST;
  job.timeoutMs = timeoutMs;
  strlcpy(job.path, path, sizeof(job.path));
  strlcpy(job.body, body.c_str(), sizeof(job.body));
  strlcpy(job.okMsg, okMsg, sizeof(job.okMsg));
  if (enqueueJob(job)) {
    drawToast("sending...", COL_ACCENT);
  } else {
    drawToast("busy - wait", COL_WARM);
  }
  lastActionMs = millis();
}

void apiAndReport(const char* path, const String& body, const char* okMsg) {
  apiAndReportLong(path, body, okMsg, 0);
}

void enqueueStatusFetch() {
  if (statusFetchQueued) return;  // collapse SSE event bursts into one fetch
  ApiJob job;
  job.type = JOB_STATUS;
  job.timeoutMs = 0;
  job.path[0] = job.body[0] = job.okMsg[0] = '\0';
  statusFetchQueued = enqueueJob(job);
}

void enqueueAlertFetch() {
  ApiJob job;
  job.type = JOB_ALERT;
  job.timeoutMs = 0;
  job.path[0] = job.body[0] = job.okMsg[0] = '\0';
  enqueueJob(job);
}

// --------------------------------------------------
// Server-pushed alert popup. The popup body is rendered by the server
// (alert.rgb565) and blitted row-by-row; only the CLOSE button is local.
// --------------------------------------------------
void showAlertOverlay() {
  if (!ENABLE_WIFI || WiFi.status() != WL_CONNECTED) return;

  const int16_t W = 296, H = 160, X = 12, Y = 24;

  // Alerts must be seen: wake a dark screen and reset the idle timer.
  if (!screenOn) {
    screenOn = true;
    setBacklight(BL_FULL);
  }
  lastActivityMs = millis();
  previewActive = false;

  WiFiClient client;
  HTTPClient http;
  String url = String("http://") + API_HOST + ":" + String(API_PORT) +
               "/api/panel/alert.rgb565?w=" + W + "&h=" + H;
  if (!http.begin(client, url)) return;
  http.setTimeout(15000);
  http.setConnectTimeout(HTTP_TIMEOUT_MS);

  int code = http.GET();
  if (code != 200) {
    http.end();
    return;  // nothing to show; stay on the current page
  }

  WiFiClient* s = http.getStreamPtr();
  static uint16_t line[296];
  uint8_t raw[592];

  tft.fillScreen(COL_BG);

  // One deadline for the whole image (not 8 s per row) so a stalled transfer
  // can't freeze the UI for minutes. (§6.4)
  unsigned long blitDeadline = millis() + 7000;
  bool complete = true;
  for (int16_t row = 0; row < H; row++) {
    int need = W * 2, got = 0;
    while (got < need && millis() < blitDeadline) {
      int r = s->read(raw + got, need - got);
      if (r > 0) got += r;
      else delay(2);
    }
    if (got < need) { complete = false; break; }
    for (int16_t px = 0; px < W; px++) {
      line[px] = ((uint16_t)raw[2 * px] << 8) | raw[2 * px + 1];  // big-endian wire
    }
    tft.drawRGBBitmap(X, Y + row, line, W, 1);
  }
  http.end();
  (void)complete;

  // Local CLOSE button (tapping anywhere also dismisses). Rounded pill with
  // an X glyph + label, matching the server-rendered card's accent.
  const int16_t cbW = 150, cbH = 40, cbX = (SCREEN_W - cbW) / 2, cbY = 194;
  tft.fillRoundRect(cbX, cbY, cbW, cbH, 12, COL_CARD);
  tft.drawRoundRect(cbX, cbY, cbW, cbH, 12, COL_ACCENT);
  int16_t ix = cbX + 30, iy = cbY + cbH / 2;
  tft.drawLine(ix - 6, iy - 6, ix + 6, iy + 6, COL_ACCENT);
  tft.drawLine(ix - 5, iy - 6, ix + 7, iy + 6, COL_ACCENT);
  tft.drawLine(ix - 6, iy + 6, ix + 6, iy - 6, COL_ACCENT);
  tft.drawLine(ix - 5, iy + 6, ix + 7, iy - 6, COL_ACCENT);
  drawCenteredText("CLOSE", cbX + cbW / 2 + 14, iy, 2, COL_TEXT);

  alertActive = true;
  ignoreUntilRelease = true;
  lastRawHit = -2;
  lastPressed = -1;
  dragId = -1;
}

// --------------------------------------------------
// Button actions
// --------------------------------------------------
// Streams the server-rendered RGB565 thumbnail of the newest moment
// straight to the TFT, row by row. Deliberately synchronous: it's an
// explicit user action with visible progress (the image painting in).
void showMomentPreview() {
  if (!ENABLE_WIFI || WiFi.status() != WL_CONNECTED) {
    drawToastAlarm("no network");
    return;
  }

  const int16_t W = 296, H = 186, X = 12, Y = 46;

  drawToast("loading photo...", COL_ACCENT);

  WiFiClient client;
  HTTPClient http;
  String url = String("http://") + API_HOST + ":" + String(API_PORT) +
               "/api/panel/moment.rgb565?w=" + W + "&h=" + H;
  if (!http.begin(client, url)) return;
  http.setTimeout(15000);
  http.setConnectTimeout(HTTP_TIMEOUT_MS);

  int code = http.GET();
  if (code != 200) {
    http.end();
    drawToastAlarm(code == 404 ? "no photos yet" : "photo load failed");
    return;
  }

  WiFiClient* s = http.getStreamPtr();
  static uint16_t line[296];
  uint8_t raw[592];

  tft.fillScreen(COL_BG);
  tft.drawRect(X - 2, Y - 2, W + 4, H + 4, COL_EDGE);
  drawCenteredText("tap to close", 160, 18, 1, COL_DIM);

  // One deadline for the whole image (not 8 s per row). (§6.4)
  unsigned long blitDeadline = millis() + 7000;
  bool complete = true;
  for (int16_t row = 0; row < H; row++) {
    int need = W * 2, got = 0;
    while (got < need && millis() < blitDeadline) {
      int r = s->read(raw + got, need - got);
      if (r > 0) got += r;
      else delay(2);
    }
    if (got < need) { complete = false; break; }
    for (int16_t px = 0; px < W; px++) {
      line[px] = ((uint16_t)raw[2 * px] << 8) | raw[2 * px + 1];  // big-endian wire
    }
    tft.drawRGBBitmap(X, Y + row, line, W, 1);
  }
  http.end();

  if (!complete) drawCenteredText("(incomplete)", 160, 236, 1, COL_ERR);

  previewActive = true;
  ignoreUntilRelease = true;
  lastActivityMs = millis();
}

void sendBrightness() {
  String body = String("{\"targets\":\"all\",\"brightness\":") + curBrightness + "}";
  char msg[32];
  snprintf(msg, sizeof(msg), "brightness %d%%", curBrightness);
  apiAndReport("/api/lights/control", body, msg);
}

void sendHueColor() {
  uint8_t r, g, b;
  hsvToRgb(curHue, curSat, r, g, b);   // honor the saturation strip (§5.4)
  char hex[10];
  snprintf(hex, sizeof(hex), "#%02x%02x%02x", r, g, b);
  String body = String("{\"targets\":\"all\",\"power\":true,\"color\":\"") + hex + "\"}";
  char msg[24];
  snprintf(msg, sizeof(msg), "color %s", hex);
  apiAndReport("/api/lights/control", body, msg);
}

// Full-screen diagnostics overlay (SETUP -> DIAG). Tap anywhere to close.
// Surfaces what's otherwise only visible over USB serial. (§7.6)
void showDiagOverlay() {
  tft.fillScreen(COL_BG);
  drawCenteredText("DIAGNOSTICS", 160, 16, 2, COL_TEXT);

  char line[40];
  int16_t y = 50;
  auto row = [&](const char* s, uint16_t c) {
    drawLeftText(s, 16, y, 1, c); y += 22;
  };

  snprintf(line, sizeof(line), "uptime   %lu s", millis() / 1000);
  row(line, COL_SUB);
  snprintf(line, sizeof(line), "free heap %u B", (unsigned)ESP.getFreeHeap());
  row(line, COL_SUB);
  snprintf(line, sizeof(line), "min heap  %u B", (unsigned)ESP.getMinFreeHeap());
  row(line, COL_SUB);
  UBaseType_t hw = uxTaskGetStackHighWaterMark(nullptr);
  snprintf(line, sizeof(line), "loop stack hw %u", (unsigned)hw);
  row(line, COL_SUB);
  snprintf(line, sizeof(line), "wifi %s  sse %s",
           WiFi.status() == WL_CONNECTED ? "up" : "down",
           sseClient.connected() ? "up" : "down");
  row(line, sseClient.connected() ? COL_OK : COL_WARM);
  if (WiFi.status() == WL_CONNECTED) {
    String ip = WiFi.localIP().toString();
    snprintf(line, sizeof(line), "ip %s", ip.c_str());
    row(line, COL_SUB);
    snprintf(line, sizeof(line), "rssi %d dBm", WiFi.RSSI());
    row(line, COL_SUB);
  }

  drawCenteredText("tap to close", 160, 228, 1, COL_DIM);
  diagActive = true;
  ignoreUntilRelease = true;
  lastActivityMs = millis();
}

// Brief reassurance overlay for the one-tap NIGHT scene. (§5.8)
void showNightConfirm() {
  tft.fillScreen(COL_BG);
  int16_t cx = 160, cy = 96;
  tft.fillCircle(cx, cy, 34, COL_WARM);
  tft.fillCircle(cx + 16, cy - 10, 30, COL_BG);   // crescent
  drawCenteredText("GOOD NIGHT", 160, 168, 2, COL_TEXT);
  drawCenteredText("lights off  dock  sleep", 160, 196, 1, COL_SUB);
}

void sendFullOn() {
  curBrightness = 100;
  Btn* b = findBtn(ID_BRIGHT);
  if (b) drawBrightnessBar(*b, false);
  apiAndReport("/api/lights/control",
               "{\"targets\":\"all\",\"power\":true,\"brightness\":100}",
               "full on");
}

void sendPowerOff() {
  // all_off also stops party mode / color cycle — a true "everything off".
  apiAndReport("/api/lights/action", "{\"action\":\"all_off\"}", "lights off");
}

void handlePress(int8_t id) {
  if (millis() - lastActionMs < 250) return;  // double-send debounce

  Serial.printf("Pressed id=%d\n", id);

  if (id == ID_NAV_LIGHTS) { showPage(PAGE_LIGHTS); return; }
  if (id == ID_NAV_VACUUM) { showPage(PAGE_VACUUM); return; }
  if (id == ID_NAV_AIR)    { showPage(PAGE_AIR);    return; }
  if (id == ID_NAV_CAM)    { showPage(PAGE_CAM);    return; }
  if (id == ID_NAV_SET)    { showPage(PAGE_SET);    return; }
  if (id == ID_MORE)       { showPage(PAGE_MODES);  return; }
  if (id == ID_COLOR_PAGE) { showPage(PAGE_COLOR);  return; }

  // Good-night scene: one tap, three queued actions. A full-screen
  // confirmation replaces the transient toast (which the three results would
  // otherwise clobber); per-action toasts are suppressed while it shows. (§5.8)
  if (id == ID_SCENE_NIGHT) {
    apiAndReport("/api/lights/action",   "{\"action\":\"all_off\"}",          "lights off");
    apiAndReport("/api/vacuum/action",   "{\"action\":\"dock\"}",             "vacuum docking");
    apiAndReport("/api/purifier/action", "{\"action\":\"mode\",\"value\":1}", "purifier sleep");
    showNightConfirm();
    previewActive = true;        // tap dismisses; toasts stay suppressed
    ignoreUntilRelease = true;
    return;
  }
  if (id == ID_BACK) {
    showPage((currentPage == PAGE_MODES || currentPage == PAGE_COLOR)
             ? PAGE_LIGHTS : PAGE_MAIN);
    return;
  }

  if (id >= ID_MODE0 && id <= ID_MODE5) {
    const char* key = MODE_KEYS[id - ID_MODE0];
    String body = String("{\"action\":\"mode\",\"mode\":\"") + key + "\"}";
    char msg[32];
    snprintf(msg, sizeof(msg), "mode: %s", MODE_LABELS[id - ID_MODE0]);
    apiAndReport("/api/lights/action", body, msg);
    // Optimistic: highlight the chosen mode now, clear the old one. (§5.1)
    strlcpy(st.lightMode, MODE_MATCH[id - ID_MODE0], sizeof(st.lightMode));
    for (int8_t i = ID_MODE0; i <= ID_MODE5; i++) if (i != id) drawBtnById(i, false);
    return;
  }

  if (id == ID_WHITE) {
    apiAndReport("/api/lights/control",
                 "{\"targets\":\"all\",\"power\":true,\"color\":\"white\"}",
                 "white");
    return;
  }
  if (id == ID_RANDOM) {
    apiAndReport("/api/lights/action", "{\"action\":\"random_color\"}", "random color");
    return;
  }

  // Vacuum: commit a plausible status on the control now; the next status
  // push reconciles it with the real device state. (§5.1)
  if (id == ID_VAC_START) { apiAndReport("/api/vacuum/action", "{\"action\":\"sweep\"}",   "sweeping");
                            strlcpy(st.vacStatus, "Sweeping", sizeof(st.vacStatus)); drawVacInfo(); return; }
  if (id == ID_VAC_STOP)  { apiAndReport("/api/vacuum/action", "{\"action\":\"stop\"}",    "stopped");
                            strlcpy(st.vacStatus, "Idle", sizeof(st.vacStatus)); drawVacInfo(); return; }
  if (id == ID_VAC_DOCK)  { apiAndReport("/api/vacuum/action", "{\"action\":\"dock\"}",    "docking");
                            strlcpy(st.vacStatus, "Docking", sizeof(st.vacStatus)); drawVacInfo(); return; }
  if (id == ID_VAC_PAUSE) { apiAndReport("/api/vacuum/action", "{\"action\":\"pause\"}",   "paused");
                            strlcpy(st.vacStatus, "Paused", sizeof(st.vacStatus)); drawVacInfo(); return; }
  if (id == ID_VAC_FIND)  { apiAndReport("/api/vacuum/action", "{\"action\":\"find_me\"}", "beeping");  return; }

  if (id == ID_PUR_POWER) {
    bool target = !st.purOn;
    String body = String("{\"action\":\"power\",\"value\":") + (target ? "true" : "false") + "}";
    apiAndReport("/api/purifier/action", body, target ? "purifier on" : "purifier off");
    st.purOn = target;                       // optimistic (§5.1)
    drawBtnById(ID_PUR_POWER, false);
    return;
  }

  if (id >= ID_PMODE0 && id <= ID_PMODE3) {
    int value = id - ID_PMODE0;  // AUTO=0 SLEEP=1 FAVORITE=2 MANUAL=3
    String body = String("{\"action\":\"mode\",\"value\":") + value + "}";
    char msg[32];
    snprintf(msg, sizeof(msg), "mode: %s", PUR_MODE_MATCH[id - ID_PMODE0]);
    apiAndReport("/api/purifier/action", body, msg);
    st.purOn = true;                         // a mode implies power on
    strlcpy(st.purMode, PUR_MODE_MATCH[id - ID_PMODE0], sizeof(st.purMode));  // optimistic
    for (int8_t i = ID_PMODE0; i <= ID_PMODE3; i++) if (i != id) drawBtnById(i, false);
    return;
  }

  if (id >= ID_FAN0 && id <= ID_FAN2) {
    int value = id - ID_FAN0 + 1;  // LOW=1 MEDIUM=2 HIGH=3
    String body = String("{\"action\":\"fan\",\"value\":") + value + "}";
    char msg[32];
    snprintf(msg, sizeof(msg), "fan: %s", PUR_FAN_MATCH[id - ID_FAN0]);
    apiAndReport("/api/purifier/action", body, msg);
    strlcpy(st.purFan, PUR_FAN_MATCH[id - ID_FAN0], sizeof(st.purFan));  // optimistic
    for (int8_t i = ID_FAN0; i <= ID_FAN2; i++) if (i != id) drawBtnById(i, false);
    return;
  }

  // Camera: captures can take a long time (flash path blinks the bulbs),
  // so these jobs run with an extended HTTP timeout.
  if (id == ID_CAP) {
    apiAndReportLong("/api/camera/capture", "{\"flash\":false}", "captured", 60000);
    return;
  }
  if (id == ID_CAP_FLASH) {
    apiAndReportLong("/api/camera/capture", "{\"flash\":true}", "captured", 60000);
    return;
  }
  if (id == ID_CAP_VIEW) {
    showMomentPreview();
    return;
  }

  // Settings.
  if (id == ID_SET_CAL)  { runCalibration(); return; }
  if (id == ID_SET_SYNC) { needSync = true; return; }
  if (id == ID_SET_DIAG) { showDiagOverlay(); return; }
  if (id == ID_SET_TIMEOUT) {
    if (offAfterMs >= 120000) offAfterMs = 30000;
    else offAfterMs *= 2;          // 30s -> 60s -> 120s -> 30s
    saveConfig();
    drawBtnById(ID_SET_TIMEOUT, true);
    return;
  }
  if (id == ID_SET_NIGHT) {
    nightEnabled = !nightEnabled;
    saveConfig();
    drawBtnById(ID_SET_NIGHT, true);
    setBacklight(bl);  // apply the new night level immediately
    return;
  }
  if (id == ID_SET_THEME) {
    applyTheme(!darkTheme);
    saveConfig();
    setBacklight(bl);
    drawPage();                 // full repaint in the new palette
    ignoreUntilRelease = true;  // don't latch a press across the redraw
    return;
  }
}

// --------------------------------------------------
// Touch mapping — mode 5 confirmed; min/max are runtime (NVS calibration)
// --------------------------------------------------
bool getTouchPoint(int16_t& x, int16_t& y) {
  if (!ts.touched()) {
    return false;
  }

  TS_Point p = ts.getPoint();
  if (p.z < TOUCH_MIN_PRESSURE) {
    return false;  // anti-ghosting: too light to trust
  }

  long mappedX = 0;
  long mappedY = 0;

#if TOUCH_MAP_MODE == 5
  mappedX = map((long)p.y, (long)tsMaxY, (long)tsMinY, 0L, (long)SCREEN_W);
  mappedY = map((long)p.x, (long)tsMinX, (long)tsMaxX, 0L, (long)SCREEN_H);
#else
  mappedX = map((long)p.x, (long)tsMinX, (long)tsMaxX, 0L, (long)SCREEN_W);
  mappedY = map((long)p.y, (long)tsMinY, (long)tsMaxY, 0L, (long)SCREEN_H);
#endif

  x = constrain(mappedX, 0L, (long)(SCREEN_W - 1));
  y = constrain(mappedY, 0L, (long)(SCREEN_H - 1));

  return true;
}

// Exact hit first; otherwise nearest button within HIT_SLOP px of its edge.
int hitTest(int16_t x, int16_t y) {
  int n;
  Btn* btns = pageButtons(n);

  for (int i = 0; i < n; i++) {
    Btn& b = btns[i];
    if (x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h) {
      return b.id;
    }
  }

  int best = -1;
  int32_t bestD = 0x7FFFFFFF;
  for (int i = 0; i < n; i++) {
    Btn& b = btns[i];
    if (x >= b.x - HIT_SLOP && x <= b.x + b.w + HIT_SLOP &&
        y >= b.y - HIT_SLOP && y <= b.y + b.h + HIT_SLOP) {
      int32_t dx = x - (b.x + b.w / 2);
      int32_t dy = y - (b.y + b.h / 2);
      int32_t d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = b.id; }
    }
  }
  return best;
}

// --------------------------------------------------
// Touch calibration: 4 cross-hair targets -> recompute tsMin/Max -> NVS.
// Enter by holding the COUKAB title (main page) for ~3 s.
// --------------------------------------------------
void loadCalibration() {
  Preferences p;
  if (p.begin("tcal", true)) {
    if (p.isKey("minx")) {
      tsMinX = p.getInt("minx", TS_MINX);
      tsMaxX = p.getInt("maxx", TS_MAXX);
      tsMinY = p.getInt("miny", TS_MINY);
      tsMaxY = p.getInt("maxy", TS_MAXY);
      Serial.printf("Calibration loaded: X %d..%d  Y %d..%d\n",
                    tsMinX, tsMaxX, tsMinY, tsMaxY);
    }
    p.end();
  }
}

void saveCalibration() {
  Preferences p;
  if (p.begin("tcal", false)) {
    p.putInt("minx", tsMinX);
    p.putInt("maxx", tsMaxX);
    p.putInt("miny", tsMinY);
    p.putInt("maxy", tsMaxY);
    p.end();
  }
}

void loadConfig() {
  Preferences p;
  if (p.begin("cfg", true)) {
    offAfterMs = p.getULong("off", OFF_AFTER_MS);
    nightEnabled = p.getBool("night", true);
    dispBrightDuty = p.getUChar("disp", BL_DUTY_FULL);
    darkTheme = p.getBool("dark", true);
    p.end();
  }
  if (offAfterMs < 30000 || offAfterMs > 120000) offAfterMs = OFF_AFTER_MS;
  if (dispBrightDuty < DISP_BRIGHT_MIN) dispBrightDuty = DISP_BRIGHT_MIN;
}

void saveConfig() {
  Preferences p;
  if (p.begin("cfg", false)) {
    p.putULong("off", offAfterMs);
    p.putBool("night", nightEnabled);
    p.putUChar("disp", dispBrightDuty);
    p.putBool("dark", darkTheme);
    p.end();
  }
}

void calDrawCross(int16_t x, int16_t y, uint16_t color) {
  tft.drawFastHLine(x - 10, y, 21, color);
  tft.drawFastVLine(x, y - 10, 21, color);
  tft.drawCircle(x, y, 6, color);
}

void calWaitRelease() {
  while (ts.touched()) delay(20);
  delay(120);
}

bool calReadRaw(int16_t& rx, int16_t& ry) {
  unsigned long start = millis();
  while (millis() - start < 30000) {
    if (ts.touched()) {
      int32_t sx = 0, sy = 0;
      int got = 0;
      for (int i = 0; i < 10; i++) {
        if (ts.touched()) {
          TS_Point p = ts.getPoint();
          if (p.z >= TOUCH_MIN_PRESSURE) { sx += p.x; sy += p.y; got++; }
        }
        delay(20);
      }
      if (got >= 5) {
        rx = sx / got;
        ry = sy / got;
        return true;
      }
    }
    delay(20);
  }
  return false;
}

bool calCollect(int16_t* rx, int16_t* ry, const int16_t* px, const int16_t* py) {
  for (int i = 0; i < 4; i++) {
    calDrawCross(px[i], py[i], COL_ACCENT);
    if (!calReadRaw(rx[i], ry[i])) return false;
    calDrawCross(px[i], py[i], COL_OK);
    calWaitRelease();
  }
  return true;
}

void runCalibration() {
  tft.fillScreen(COL_BG);
  drawCenteredText("TOUCH CALIBRATION", 160, 40, 2, COL_TEXT);
  drawCenteredText("press each target firmly", 160, 70, 1, COL_DIM);

  const int16_t px[4] = { 20, 300, 300, 20 };
  const int16_t py[4] = { 20, 20, 220, 220 };
  int16_t rx[4], ry[4];

  calWaitRelease();

  if (calCollect(rx, ry, px, py)) {
    // Invert the mode-5 mapping from the corner samples:
    // screen X tracks raw Y, screen Y tracks raw X.
    float ryL = (ry[0] + ry[3]) / 2.0f, ryR = (ry[1] + ry[2]) / 2.0f;
    float slopeX = (ryR - ryL) / (float)(px[1] - px[0]);
    float maxY = ryL - px[0] * slopeX;        // raw y at screen x = 0
    float minY = maxY + SCREEN_W * slopeX;    // raw y at screen x = W

    float rxT = (rx[0] + rx[1]) / 2.0f, rxB = (rx[2] + rx[3]) / 2.0f;
    float slopeY = (rxB - rxT) / (float)(py[2] - py[1]);
    float minX = rxT - py[0] * slopeY;        // raw x at screen y = 0
    float maxX = minX + SCREEN_H * slopeY;    // raw x at screen y = H

    if (fabsf(maxY - minY) > 800 && fabsf(maxX - minX) > 800) {
      tsMinX = (int)minX; tsMaxX = (int)maxX;
      tsMinY = (int)minY; tsMaxY = (int)maxY;
      saveCalibration();
      drawCenteredText("SAVED", 160, 130, 2, COL_OK);
      Serial.printf("Calibration saved: X %d..%d  Y %d..%d\n",
                    tsMinX, tsMaxX, tsMinY, tsMaxY);
    } else {
      drawCenteredText("failed - kept old values", 160, 130, 1, COL_ERR);
    }
  } else {
    drawCenteredText("timeout", 160, 130, 2, COL_ERR);
  }
  delay(1200);

  currentPage = PAGE_MAIN;
  lastPressed = -1;
  lastRawHit = -2;
  dragId = -1;
  ignoreUntilRelease = true;
  lastActivityMs = millis();
  drawPage();
}

// --------------------------------------------------
// Touch handling
// --------------------------------------------------
void updateTouch() {
  int16_t x, y;
  bool touching = getTouchPoint(x, y);

  if (touching) {
    lastActivityMs = millis();
  }

  // Rising-edge bookkeeping for swipe-to-back and miss-recovery. prevTouch is
  // updated on every call so it stays correct across the many early returns.
  static bool prevTouch = false;
  bool rising = touching && !prevTouch;
  prevTouch = touching;
  if (rising) { pressDownX = x; pressDownY = y; }

  // An alert popup, photo preview, or diagnostics overlay covers the screen:
  // any tap dismisses.
  if (alertActive || previewActive || diagActive) {
    if (touching && !ignoreUntilRelease) {
      alertActive = false;
      previewActive = false;
      diagActive = false;
      ignoreUntilRelease = true;
      drawPage();
    } else if (!touching) {
      ignoreUntilRelease = false;
    }
    return;
  }

  // Long-press on the COUKAB title (main page) -> calibration.
  if (currentPage == PAGE_MAIN && touching && x < 150 && y < 46 && !ignoreUntilRelease) {
    if (!titleHold) {
      titleHold = true;
      titleHoldStart = millis();
    } else if (millis() - titleHoldStart > 2800) {
      titleHold = false;
      runCalibration();
      return;
    }
  } else {
    titleHold = false;
  }

  if (ignoreUntilRelease) {
    if (!touching) ignoreUntilRelease = false;
    return;
  }

  // Auto-offer calibration when taps keep landing on nothing — the classic
  // sign of a drifted panel that can no longer reach SETUP reliably. (§5.6)
  if (rising) {
    if (hitTest(x, y) < 0) {
      if (++missStreak >= MISS_BEFORE_CAL) { missStreak = 0; runCalibration(); return; }
    } else {
      missStreak = 0;
    }
  }

  // Drag bars: live update while moving, one HTTP call on release.
  if (dragId >= 0) {
    Btn* b = findBtn(dragId);
    if (touching && b) {
      if (dragId == ID_BRIGHT) {
        int pct = constrain((int)((int32_t)(x - b->x) * 100 / b->w), 1, 100);
        if (pct != curBrightness) {
          curBrightness = pct;
          updateBrightnessBar(*b);        // diff-only repaint (§6.2)
        }
      } else if (dragId == ID_HUE) {
        int hue = constrain((int)((int32_t)(x - b->x) * 360 / b->w), 0, 359);
        if (hue != curHue) {
          int old = curHue;
          curHue = hue;
          moveHueMarker(*b, old);
          Btn* sb = findBtn(ID_SAT);
          if (sb) drawSatBar(*sb);          // its gradient depends on hue
          drawColorPreview();
        }
      } else if (dragId == ID_SAT) {
        int sat = constrain((int)((int32_t)(x - b->x) * 100 / b->w), 0, 100);
        if (sat != curSat) {
          int old = curSat;
          curSat = sat;
          moveSatMarker(*b, old);
          drawColorPreview();
        }
      } else {  // ID_SET_BRIGHT — display backlight
        int pct = constrain((int)((int32_t)(x - b->x) * 100 / b->w), 0, 100);
        uint8_t duty = DISP_BRIGHT_MIN + pct * (255 - DISP_BRIGHT_MIN) / 100;
        if (duty != dispBrightDuty) {
          dispBrightDuty = duty;
          setBacklight(BL_FULL);            // live preview of the new level
          updateGradientBar(*b, pct);       // diff-only, same as the bulb bar
        }
      }
    } else {
      int finished = dragId;
      dragId = -1;
      lastPressed = -1;
      lastRawHit = -2;
      if (finished == ID_BRIGHT) {
        if (b) drawBrightnessBar(*b, false);
        sendBrightness();
      } else if (finished == ID_SET_BRIGHT) {
        if (b) drawDispBrightBar(*b, false);
        saveConfig();                       // persist the chosen brightness
      } else {
        sendHueColor();                     // ID_HUE or ID_SAT -> same color post
      }
    }
    return;
  }

  int raw = touching ? hitTest(x, y) : -1;

  // Anti-ghosting: require the same hit on two consecutive polls.
  if (raw != lastRawHit) {
    lastRawHit = raw;
    return;
  }

  int hit = raw;

  // Left-edge swipe -> back (drag bars excluded so the COLOR sliders still
  // work from the edge). (§ roadmap)
  if (currentPage != PAGE_MAIN && dragId < 0 && touching &&
      pressDownX >= 0 && pressDownX < EDGE_SWIPE_X0 &&
      (x - pressDownX) > SWIPE_BACK_DX &&
      hit != ID_HUE && hit != ID_SAT && hit != ID_BRIGHT && hit != ID_SET_BRIGHT) {
    showPage((currentPage == PAGE_MODES || currentPage == PAGE_COLOR)
             ? PAGE_LIGHTS : PAGE_MAIN);
    return;
  }

  // Hold-to-ramp: keeping a finger on the brightness off/on buttons
  // ramps the value (~80%/s); the API call goes out on release.
  if ((lastPressed == ID_BR_OFF || lastPressed == ID_BR_ON) &&
      touching && hit == lastPressed) {
    if (millis() - holdStartMs >= HOLD_RAMP_MS) {
      int delta = (lastPressed == ID_BR_ON) ? 2 : -2;
      int nb = constrain(curBrightness + delta, 1, 100);
      if (nb != curBrightness) {
        curBrightness = nb;
        holdRamped = true;
        Btn* bb = findBtn(ID_BRIGHT);
        if (bb) drawBrightnessBar(*bb, false);
      }
    }
  }

  // Edge-triggered: act once on the press-down transition only.
  if (hit != lastPressed) {
    // Release / slide-away from the hold-capable buttons.
    if (lastPressed == ID_BR_OFF || lastPressed == ID_BR_ON) {
      int8_t finished = lastPressed;
      drawBtnById(finished, false);
      lastPressed = -1;
      if (holdRamped) {
        sendBrightness();           // ramped -> commit the new level
      } else if (hit == -1) {
        if (finished == ID_BR_OFF) sendPowerOff();  // plain tap
        else sendFullOn();
      }
      // sliding onto another button without ramping = cancel
      return;
    }

    if (lastPressed >= 0) drawBtnById(lastPressed, false);

    if (hit == ID_BRIGHT || hit == ID_HUE || hit == ID_SAT || hit == ID_SET_BRIGHT) {
      Btn* b = findBtn(hit);
      if (b) {
        dragId = hit;
        if (hit == ID_BRIGHT) {
          curBrightness = constrain((int)((int32_t)(x - b->x) * 100 / b->w), 1, 100);
          drawBrightnessBar(*b, true);
        } else if (hit == ID_HUE) {
          int old = curHue;
          curHue = constrain((int)((int32_t)(x - b->x) * 360 / b->w), 0, 359);
          moveHueMarker(*b, old);
          Btn* sb = findBtn(ID_SAT);
          if (sb) drawSatBar(*sb);
          drawColorPreview();
        } else if (hit == ID_SAT) {
          int old = curSat;
          curSat = constrain((int)((int32_t)(x - b->x) * 100 / b->w), 0, 100);
          moveSatMarker(*b, old);
          drawColorPreview();
        } else {  // ID_SET_BRIGHT — display backlight
          int pct = constrain((int)((int32_t)(x - b->x) * 100 / b->w), 0, 100);
          dispBrightDuty = DISP_BRIGHT_MIN + pct * (255 - DISP_BRIGHT_MIN) / 100;
          setBacklight(BL_FULL);
          drawDispBrightBar(*b, true);
        }
      }
      lastPressed = hit;
      return;
    }

    if (hit == ID_BR_OFF || hit == ID_BR_ON) {
      drawBtnById(hit, true);
      holdStartMs = millis();
      holdRamped = false;
      lastPressed = hit;
      return;
    }

    if (hit >= 0) {
      drawBtnById(hit, true);
      handlePress(hit);
    }
    if (!ignoreUntilRelease) lastPressed = hit;
  }
}

// --------------------------------------------------
// Wi-Fi / network services
// --------------------------------------------------
void setupWiFi() {
  if (!ENABLE_WIFI) {
    Serial.println("WiFi disabled.");
    return;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to WiFi: ");
  Serial.println(WIFI_SSID);
}

// One-time init once Wi-Fi is up: NTP (night mode) + OTA.
void initNetServices() {
  configTime(TZ_OFFSET_SEC, 0, "pool.ntp.org");
  ArduinoOTA.setHostname(OTA_HOSTNAME);
  // Optional OTA password: define OTA_PASSWORD in secrets.h to require it.
  // Reflashing is higher-privilege than the (intentionally open) web API. (§7.7)
#ifdef OTA_PASSWORD
  ArduinoOTA.setPassword(OTA_PASSWORD);
#endif
  ArduinoOTA.begin();
  Serial.printf("OTA ready as '%s'.\n", OTA_HOSTNAME);
}

void updateHeaderPeriodically() {
  if (previewActive || alertActive || diagActive) return;
  if (millis() - lastHeaderUpdate < 2000) return;
  lastHeaderUpdate = millis();
  if (currentPage == PAGE_MAIN) drawMainHeader();   // also re-ticks the clock
  else drawConnIcon();
}

// --------------------------------------------------
// Setup / loop
// --------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println();
  Serial.println("tuch_controller — Coukab LAN remote");

  bootMs = millis();
  // Why did we boot? After deep sleep, a timer wake means "check alerts quietly"
  // (screen stays dark); a touch (ext1) means wake to MAIN.
  esp_sleep_wakeup_cause_t wake = esp_sleep_get_wakeup_cause();
  bootForAlertPoll = (wake == ESP_SLEEP_WAKEUP_TIMER);
  wokeFromTouch    = (wake == ESP_SLEEP_WAKEUP_EXT1);
  lastAlertId = rtcLastAlertId;   // don't re-show an alert seen before sleeping

  pinMode(TFT_BL, OUTPUT);
  setBacklight(bootForAlertPoll ? BL_OFF : BL_FULL);  // alert poll stays dark

  pinMode(TFT_CS, OUTPUT);
  pinMode(TOUCH_CS, OUTPUT);
  digitalWrite(TFT_CS, HIGH);
  digitalWrite(TOUCH_CS, HIGH);

  tftSPI.begin(TFT_SCK, TFT_MISO, TFT_MOSI, TFT_CS);

  tft.begin(TFT_SPI_HZ);  // push the shared SPI bus (§6.1)
  tft.setRotation(1);     // Landscape 320x240

  ts.begin(tftSPI);
  ts.setRotation(0);      // Keep at 0; manual mapping uses mode 5

  loadCalibration();
  loadConfig();
  applyTheme(darkTheme);          // palette from NVS before the first draw
  if (!bootForAlertPoll) setBacklight(BL_FULL);  // apply saved brightness (poll: stay dark)

  // HTTP worker (core 0; loop/draw stays on core 1). Jobs in on apiQueue,
  // finished results back on resultQueue (explicit cross-core handoff).
  apiQueue = xQueueCreate(8, sizeof(ApiJob));        // deeper: NIGHT posts 3 (§4.1)
  resultQueue = xQueueCreate(8, sizeof(ApiResult));
  xTaskCreatePinnedToCore(apiWorkerTask, "apiWorker", 8192, nullptr, 1, nullptr, 0);

  // Wi-Fi modem sleep lets the radio idle between DTIM beacons during light
  // sleep without dropping the association (so SSE/alerts survive).
  if (SLEEP_ENABLED) WiFi.setSleep(true);
  setupWiFi();

  lastActivityMs = millis();
  if (bootForAlertPoll) {
    // Woke only to poll alerts — keep the screen off; loop() drives the
    // fetch-then-resleep. Don't waste a status fetch.
    screenOn = false;
    screenOffSince = millis();
    needSync = false;
    Serial.println("Woke for alert poll (screen stays off).");
  } else {
    if (wokeFromTouch) ignoreUntilRelease = true;  // swallow the touch that woke us
    drawPage();
  }
}

void loop() {
  // One-time network services once Wi-Fi connects.
  if (!netReady && ENABLE_WIFI && WiFi.status() == WL_CONNECTED) {
    netReady = true;
    initNetServices();
  }
  if (netReady) ArduinoOTA.handle();

  // Deep-sleep alert poll: once Wi-Fi is up, fetch the alert exactly once. The
  // RES_ALERT handler lights the screen iff it's new and sets alertPollDone;
  // the screen-off branch then re-sleeps. Give up (and re-sleep) if Wi-Fi is slow.
  if (bootForAlertPoll && !alertPollDone) {
    if (netReady && !alertPollRequested) {
      alertPollRequested = true;
      enqueueAlertFetch();
    } else if (millis() - bootMs > ALERT_POLL_WIFI_MS) {
      Serial.println("Alert poll: Wi-Fi too slow, re-sleeping.");
      alertPollDone = true;   // screen-off branch will deep-sleep again
    }
  }

  pumpSSE();  // keep live updates flowing even while the screen is dark

  // Drain finished results from the HTTP worker (one ownership transfer per
  // frame — no volatile slots, no spin-waits).
  ApiResult res;
  while (resultQueue && xQueueReceive(resultQueue, &res, 0) == pdTRUE) {
    if (res.type == RES_TOAST) {
      lastActionMs = millis();
      if (screenOn && !previewActive) {
        if (res.ok) drawToast(res.text, COL_OK);
        else drawToastAlarm(res.text);
        // A finished capture on the camera page shows the photo right away.
        if (res.ok && currentPage == PAGE_CAM && strcmp(res.text, "captured") == 0) {
          showMomentPreview();
        }
      }
    } else if (res.type == RES_STATUS) {  // always delivered so the dedupe flag clears
      statusFetchQueued = false;
      bool gotData = strlen(res.text) > 2;
      if (gotData) {
        String payload(res.text);
        applyStatusJson(payload);
        refreshDynamic();
      }
      if (syncToastPending) {
        syncToastPending = false;
        if (screenOn && !previewActive) {
          if (gotData) drawToast("synced", COL_SUB);
          else drawToastAlarm(WiFi.status() != WL_CONNECTED ? "no wifi" : "sync failed");
        }
      }
    } else {  // RES_ALERT — show once per id (dedupes repeated SSE pings/refetches)
      String payload(res.text);
      int id = jsonInt(payload, "id", 0);
      if (id > 0 && id != lastAlertId) {
        lastAlertId = id;
        showAlertOverlay();       // lights the screen
        bootForAlertPoll = false; // a real alert is up; resume normal operation
      }
      alertPollDone = true;       // the deep-sleep alert poll has its answer
    }
  }

  if (needSync && netReady) {
    needSync = false;
    syncToastPending = true;
    if (screenOn && !previewActive) drawToast("syncing...", COL_ACCENT);
    enqueueStatusFetch();
  }
  if (statusPokePending && netReady) {
    statusPokePending = false;
    enqueueStatusFetch();  // silent refresh triggered by an SSE event
  }
  if (alertPokePending && netReady) {
    alertPokePending = false;
    enqueueAlertFetch();   // SSE announced a new alert; fetch its id
  }

  // Until the first snapshot lands, keep retrying quietly every 30 s —
  // covers "panel booted before the server" without any user action.
  if (!st.valid && netReady && !bootForAlertPoll && millis() - lastSyncRetryMs >= 30000) {
    lastSyncRetryMs = millis();
    enqueueStatusFetch();
  }

  if (!screenOn) {
    // IRQ line is cheap to read; confirm over SPI before waking.
    if (digitalRead(TOUCH_IRQ) == LOW && ts.touched()) {
      wakeScreen();
      while (ts.touched()) delay(10);  // swallow the waking touch
      ignoreUntilRelease = false;
      return;
    }

    if (!SLEEP_ENABLED) { delay(50); return; }  // old always-on behavior

    // Alert-poll wake: wait for the one-shot fetch, then re-sleep unless the
    // RES_ALERT handler found a new alert (which lights the screen).
    if (bootForAlertPoll) {
      if (alertPollDone) enterDeepSleep();  // no new alert -> deep sleep again
      delay(20);
      return;
    }

    // Normal idle ladder: light sleep, escalating to deep sleep after 15 min.
    // Only sleep once any in-flight network work is done, so we never suspend
    // mid-request or miss an alert that's already being fetched.
    bool netIdle = !workerBusy && uxQueueMessagesWaiting(apiQueue) == 0 &&
                   !alertPokePending && !statusPokePending && !needSync;
    if (netIdle) {
      if (millis() - screenOffSince >= DEEP_SLEEP_AFTER_MS) {
        enterDeepSleep();   // resets on wake; never returns
      }
      enterLightSleep();    // returns on touch or the short SSE-service timer
    } else {
      delay(50);            // let pending work finish before sleeping
    }
    return;
  }

  updateTouch();
  updateHeaderPeriodically();
  updateBacklight();

  // In-flight dot: repaint the connection icon whenever the busy state flips,
  // so a queued/running request is always visible. (§5.2)
  static bool lastBusyShown = false;
  bool busyNow = workerBusy || (apiQueue && uxQueueMessagesWaiting(apiQueue) > 0);
  if (busyNow != lastBusyShown && screenOn && !previewActive && !alertActive && !diagActive) {
    lastBusyShown = busyNow;
    drawConnIcon();
  }

  // Idle on a sub-page returns to the dashboard — but not on AIR, whose live
  // PM2.5/temp/humidity the user may be watching (§5.8); never under an alert.
  if (currentPage != PAGE_MAIN && currentPage != PAGE_AIR &&
      dragId < 0 && lastPressed < 0 && !alertActive && !diagActive &&
      millis() - lastActivityMs >= RETURN_MAIN_MS) {
    showPage(PAGE_MAIN);
  }

  delay(25);
}
