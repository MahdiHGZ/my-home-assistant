// pages.h — page model & layout data for tuch_controller_v2.
//
// This is the single source of truth for the screens: the page list, the button
// ids, the Btn layout arrays, and the mode/purifier label tables. It is shared
// by the firmware (tuch_controller_v2.ino) AND the host preview renderer
// (tools/host_preview), so the desktop PNGs are generated from the exact same
// layout the device uses — never a duplicated copy.
//
// Only pure data lives here (no colors, no drawing) so it has zero dependency on
// config.h or the Adafruit libraries.
#pragma once
#include <stdint.h>

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

// v2 layout: tight 5 px gutters for the biggest possible targets on a
// low-sensitivity resistive panel. BACK is a small circle in the top-left,
// clearly separated from the content (which starts at y=46); the Dynamic
// Island tab owns the top-center.
//   3-col: 5 |100| 5 |100| 5 |100| 5 = 320
Btn mainBtns[] = {
  {  12,  50,  94, 84, "LIGHTS", ID_NAV_LIGHTS },
  { 113,  50,  94, 84, "VACUUM", ID_NAV_VACUUM },
  { 214,  50,  94, 84, "AIR",    ID_NAV_AIR },
  {  12, 140,  94, 84, "CAMERA", ID_NAV_CAM },
  { 113, 140,  94, 84, "SETUP",  ID_NAV_SET },
  { 214, 140,  94, 84, "NIGHT",  ID_SCENE_NIGHT },
};

Btn lightsBtns[] = {
  {  12,  31,  48,  36, "",      ID_BACK },

  {   8,  73,  98,  52, "",      ID_MODE5 },   // MOVIE
  { 111,  73,  98,  52, "",      ID_MODE4 },   // LOVE
  { 214,  73,  98,  52, "MODES", ID_MORE },

  {   8, 130, 304,  46, "SPECTRUM COLOR", ID_COLOR_PAGE },

  {   8, 181,  48,  46, "",      ID_BR_OFF },
  {  64, 181, 248,  46, "",      ID_BRIGHT },
};

Btn modesBtns[] = {
  {  12,  31,  48,  36, "", ID_BACK },
  {  12,  86,  94,  44, "", ID_MODE0 },
  { 113,  86,  94,  44, "", ID_MODE1 },
  { 214,  86,  94,  44, "", ID_MODE2 },
  {  12, 136,  94,  44, "", ID_MODE3 },
  { 113, 136,  94,  44, "", ID_MODE4 },
  { 214, 136,  94,  44, "", ID_MODE5 },
};

Btn colorBtns[] = {
  {  12,  31,  48,  36, "",       ID_BACK },
  {   8,  94, 304,  28, "",       ID_HUE },   // hue 0..359
  {   8, 143, 304,  28, "",       ID_SAT },   // saturation white->full (§5.4)
  {  84, 183,  72,  44, "WARM",   ID_MODE1 },
  { 164, 183,  72,  44, "COLD",   ID_MODE0 },
  { 244, 183,  68,  44, "",       ID_RANDOM },
};

Btn vacuumBtns[] = {
  {  12,  31,  48,  36, "",      ID_BACK },
  // Status card occupies y46..110 (drawn by drawVacStatusCard, not a button).
  // Primary pair:
  {   8, 132, 148, 46, "START", ID_VAC_START },
  { 164, 132, 148, 46, "STOP",  ID_VAC_STOP },
  // Secondary row:
  {   8, 184,  98, 43, "DOCK",  ID_VAC_DOCK },
  { 111, 184,  98, 43, "PAUSE", ID_VAC_PAUSE },
  { 214, 184,  98, 43, "FIND",  ID_VAC_FIND },
};

Btn airBtns[] = {
  {  12,  31, 48, 36, "", ID_BACK },
  { 260,  31, 48, 36, "", ID_PUR_POWER },

  {  12, 156,  67, 34, "", ID_PMODE0 },
  {  84, 156,  67, 34, "", ID_PMODE1 },
  {  12, 195,  67, 34, "", ID_PMODE2 },
  {  84, 195,  67, 34, "", ID_PMODE3 },

  { 164, 172,  44, 52, "", ID_FAN0 },
  { 213, 172,  44, 52, "", ID_FAN1 },
  { 262, 172,  44, 52, "", ID_FAN2 },
};

Btn camBtns[] = {
  {  12,  31,  48,  36, "",        ID_BACK },
  {  12,  75, 174, 150, "VIEW",    ID_CAP_VIEW },
  { 194,  75, 114,  96, "CAPTURE", ID_CAP },
  { 194, 179, 114,  46, "FLASH",   ID_CAP_FLASH },
};

Btn setBtns[] = {
  {  12,  31,  48,  36, "",          ID_BACK },
  // Row 1
  {  12,  75,  94,  40, "CALIBRATE", ID_SET_CAL },
  { 113,  75,  94,  40, "SYNC",      ID_SET_SYNC },
  { 214,  75,  94,  40, "INFO",      ID_SET_DIAG },
  // Row 2
  {  12, 122, 143,  40, "",          ID_SET_THEME },
  { 165, 122, 143,  40, "",          ID_SET_TIMEOUT },
  // Row 3: display backlight brightness (drag bar)
  {  12, 176, 296,  40, "",          ID_SET_BRIGHT },
};
