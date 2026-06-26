// config.h — hardware pins, palette, and tuning for tuch_controller_v2.
//
// Identical hardware contract to v1. The dark COL_* palette below is the
// base the v2 "liquid glass" renderer tints over (see the glass primitives
// in tuch_controller_v2.ino and DESIGN.md). COL_CARD/COL_CARD_HI/COL_EDGE
// are retained for the theme plumbing but the frosted look is computed at
// runtime by alpha-blending over COL_BG.
//
// Pin and touch-mapping values are the validated setup documented in
// HARDWARE.md. Do not change TOUCH_MAP_MODE unless the physical
// orientation changes; if edge accuracy drifts, use the on-device
// calibration (SETUP -> CALIBRATE, or hold the COUKAB title ~3 s)
// instead of editing the TS_* defaults.
#pragma once

// --------------------------------------------------
// Behaviour switches
// --------------------------------------------------
#define ENABLE_WIFI true

// --------------------------------------------------
// TFT / touch pins
// --------------------------------------------------
#define TFT_SCK   12
#define TFT_MOSI  11
#define TFT_MISO  13
#define TFT_CS    10
#define TFT_DC     9
#define TFT_RST   14
#define TFT_BL    21

#define TOUCH_CS   8
#define TOUCH_IRQ  7

#define SCREEN_W 320
#define SCREEN_H 240

// --------------------------------------------------
// Touch calibration defaults (overridden by NVS after on-device calibration)
// --------------------------------------------------
#define TS_MINX 250
#define TS_MAXX 3800
#define TS_MINY 250
#define TS_MAXY 3800

#define TOUCH_MAP_MODE 5
#define TOUCH_MIN_PRESSURE 250   // anti-ghosting: ignore feather touches (lower = more sensitive)
#define HIT_SLOP 8               // accept touches this many px outside a button
                                 // (kept modest: big targets + 5 px gutters, so a
                                 //  large slop would bleed one button into the next)

// --------------------------------------------------
// Timing
// --------------------------------------------------
const uint16_t HTTP_TIMEOUT_MS    = 3500;
const uint16_t STATUS_TIMEOUT_MS  = 2500;
// Default screen-off timeout; user-adjustable on the SETUP page (30/60/120 s,
// stored in NVS). The backlight dims at half this value.
const unsigned long OFF_AFTER_MS  = 60000;
const unsigned long RETURN_MAIN_MS = 45000; // idle on a sub-page -> MAIN
const uint16_t HOLD_RAMP_MS       = 600;    // hold off/on this long to ramp

// --------------------------------------------------
// Night mode (NTP): lower full-backlight level during these hours
// --------------------------------------------------
const long TZ_OFFSET_SEC = 3 * 3600 + 1800;  // UTC+3:30 — adjust to your zone
const int NIGHT_START_HOUR = 22;
const int NIGHT_END_HOUR   = 7;
const uint8_t BL_DUTY_FULL  = 255;
const uint8_t BL_DUTY_NIGHT = 120;
const uint8_t BL_DUTY_DIM   = 40;

const char* OTA_HOSTNAME = "coukab-panel";

// --------------------------------------------------
// Dark / dark-navy palette (RGB565)
// --------------------------------------------------
#define COL_BG       0x0022   // background        deep navy rgb(0,4,16)
#define COL_CARD     0x10E7   // card / button     rgb(22,30,58)
#define COL_CARD_HI  0x218B   // pressed card      rgb(36,48,92)
#define COL_EDGE     0x29CC   // subtle border     rgb(40,58,100)
#define COL_TEXT     0xEF7D   // soft white
#define COL_DIM      0x6BD1   // dim grey-blue
#define COL_ACCENT   0x4DBF   // cyan-blue accent
#define COL_OK       0x56EF   // soft green
#define COL_ERR      0xF2CB   // soft red
#define COL_WARM     0xFD8E   // warm yellow
