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
  {   5,  40, 100, 95, "LIGHTS", ID_NAV_LIGHTS },
  { 110,  40, 100, 95, "VACUUM", ID_NAV_VACUUM },
  { 215,  40, 100, 95, "AIR",    ID_NAV_AIR },
  {   5, 141, 100, 95, "CAMERA", ID_NAV_CAM },
  { 110, 141, 100, 95, "SETUP",  ID_NAV_SET },
  { 215, 141, 100, 95, "NIGHT",  ID_SCENE_NIGHT },
};

Btn lightsBtns[] = {
  {   6,   3,  38,  38, "",      ID_BACK },     // circle

  {   5,  46, 100,  66, "",      ID_MODE5 },   // MOVIE
  { 110,  46, 100,  66, "",      ID_MODE4 },   // LOVE
  { 215,  46, 100,  66, "MORE",  ID_MORE },

  {   5, 118, 310,  46, "COLOR", ID_COLOR_PAGE },

  {   5, 170,  62,  62, "",      ID_BR_OFF },   // circle
  {  73, 176, 174,  50, "",      ID_BRIGHT },   // pill bar
  { 253, 170,  62,  62, "",      ID_BR_ON },    // circle
};

Btn modesBtns[] = {
  {   6,   3,  38,  38, "", ID_BACK },
  {   5,  46, 100,  92, "", ID_MODE0 },
  { 110,  46, 100,  92, "", ID_MODE1 },
  { 215,  46, 100,  92, "", ID_MODE2 },
  {   5, 144, 100,  92, "", ID_MODE3 },
  { 110, 144, 100,  92, "", ID_MODE4 },
  { 215, 144, 100,  92, "", ID_MODE5 },
};

Btn colorBtns[] = {
  {   6,   3,  38,  38, "",       ID_BACK },
  {   5,  46, 310,  62, "",       ID_HUE },   // hue 0..359
  {   5, 116, 310,  38, "",       ID_SAT },   // saturation white->full (§5.4)
  { 110, 166, 100,  66, "WHITE",  ID_WHITE },
  { 215, 166, 100,  66, "RANDOM", ID_RANDOM },
};

Btn vacuumBtns[] = {
  {   6,   3,  38,  38, "",      ID_BACK },
  // Status card occupies y46..110 (drawn by drawVacStatusCard, not a button).
  // Primary pair:
  {   5, 116, 152, 56, "START", ID_VAC_START },
  { 163, 116, 152, 56, "STOP",  ID_VAC_STOP },
  // Secondary row:
  {   5, 178, 100, 56, "DOCK",  ID_VAC_DOCK },
  { 110, 178, 100, 56, "PAUSE", ID_VAC_PAUSE },
  { 215, 178, 100, 56, "FIND",  ID_VAC_FIND },
};

Btn airBtns[] = {
  {   6,   3,  38, 38, "", ID_BACK },
  { 268,   2, 40, 40, "", ID_PUR_POWER },   // circle, top-right

  {   5, 108,  74, 56, "", ID_PMODE0 },
  {  84, 108,  74, 56, "", ID_PMODE1 },
  { 163, 108,  74, 56, "", ID_PMODE2 },
  { 242, 108,  74, 56, "", ID_PMODE3 },

  {   5, 170, 100, 56, "", ID_FAN0 },
  { 110, 170, 100, 56, "", ID_FAN1 },
  { 215, 170, 100, 56, "", ID_FAN2 },
};

Btn camBtns[] = {
  {   6,   3,  38,  38, "",        ID_BACK },
  {   5,  46, 152, 114, "CAPTURE", ID_CAP },
  { 163,  46, 152, 114, "FLASH",   ID_CAP_FLASH },
  {   5, 166, 310,  66, "VIEW LAST PHOTO", ID_CAP_VIEW },
};

Btn setBtns[] = {
  {   6,   3,  38,  38, "",          ID_BACK },
  // Row 1
  {   5,  46, 100,  54, "CALIBRATE", ID_SET_CAL },
  { 110,  46, 100,  54, "SYNC",      ID_SET_SYNC },
  { 215,  46, 100,  54, "DEVICE",    ID_SET_DIAG },
  // Row 2
  {   5, 106, 100,  54, "",          ID_SET_TIMEOUT },
  { 110, 106, 100,  54, "",          ID_SET_NIGHT },
  { 215, 106, 100,  54, "",          ID_SET_THEME },
  // Row 3: display backlight brightness (drag bar)
  {   5, 176, 310,  48, "",          ID_SET_BRIGHT },
};
