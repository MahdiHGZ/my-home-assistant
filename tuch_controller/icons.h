// icons.h — vector icon library for tuch_controller.
//
// Every icon is drawn from Adafruit_GFX primitives (circles, rects, triangles,
// lines) centered on (cx, cy). No bitmaps, so they scale/recolor for free and
// cost only a few draw calls each. Typical footprint is ~26x26 px, which fits
// the dashboard tiles, mode buttons and control chips.
//
// Dependencies: this header is #included from tuch_controller.ino AFTER the
// global `tft` (Adafruit_ILI9341) and <Adafruit_GFX.h>/<Arduino.h> are in
// scope. It defines free functions only — pure drawing, no state.
//
// Convention:
//   * `color` is the ink.
//   * `bg` (where present) is the surface behind the icon, used to punch holes
//     / mask (e.g. a crescent, a keyhole, a lens). Pass the panel's fill color.
//
// Many icons here are not wired into a screen yet — they exist so new controls
// can grab a matching glyph without redrawing one from scratch.
#pragma once

// =====================================================================
// Core UI / device icons (used across the dashboard and pages)
// =====================================================================
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

void iconDice(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillRoundRect(cx - 13, cy - 13, 26, 26, 5, color);
  tft.fillCircle(cx - 6, cy - 6, 2, bg);
  tft.fillCircle(cx + 6, cy - 6, 2, bg);
  tft.fillCircle(cx,     cy,     2, bg);
  tft.fillCircle(cx - 6, cy + 6, 2, bg);
  tft.fillCircle(cx + 6, cy + 6, 2, bg);
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

// =====================================================================
// Lighting-scene icons (COOL / WARM / SUNSET / SLEEP / LOVE / MOVIE and more)
// =====================================================================
void iconSnowflake(int16_t cx, int16_t cy, uint16_t color) {
  for (int i = 0; i < 6; i++) {
    float a = i * PI / 3.0f;
    int16_t ex = cx + (int16_t)(cosf(a) * 12);
    int16_t ey = cy + (int16_t)(sinf(a) * 12);
    tft.drawLine(cx, cy, ex, ey, color);
    // little V-branch near each tip
    int16_t bx = cx + (int16_t)(cosf(a) * 7);
    int16_t by = cy + (int16_t)(sinf(a) * 7);
    tft.drawLine(bx, by, bx + (int16_t)(cosf(a + 1.05f) * 4), by + (int16_t)(sinf(a + 1.05f) * 4), color);
    tft.drawLine(bx, by, bx + (int16_t)(cosf(a - 1.05f) * 4), by + (int16_t)(sinf(a - 1.05f) * 4), color);
  }
}

void iconMoon(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillCircle(cx, cy, 11, color);
  tft.fillCircle(cx + 5, cy - 4, 11, bg);   // carve the crescent
}

void iconSunset(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  int16_t hy = cy + 7;                        // horizon line
  tft.fillCircle(cx, cy + 3, 8, color);       // sun disc
  tft.fillRect(cx - 15, hy, 30, 12, bg);      // clip lower half -> dome
  tft.drawFastHLine(cx - 15, hy, 11, color);  // horizon (left)
  tft.drawFastHLine(cx + 4, hy, 11, color);   // horizon (right)
  tft.drawLine(cx, cy - 13, cx, cy - 9, color);        // up ray
  tft.drawLine(cx - 11, cy - 5, cx - 8, cy - 2, color); // left ray
  tft.drawLine(cx + 11, cy - 5, cx + 8, cy - 2, color); // right ray
}

void iconStar(int16_t cx, int16_t cy, uint16_t color) {
  int16_t px[10], py[10];
  for (int i = 0; i < 10; i++) {
    float a = -PI / 2 + i * PI / 5.0f;
    float r = (i & 1) ? 5.0f : 12.0f;
    px[i] = cx + (int16_t)(cosf(a) * r);
    py[i] = cy + (int16_t)(sinf(a) * r);
  }
  for (int i = 0; i < 10; i++) {
    int j = (i + 1) % 10;
    tft.fillTriangle(cx, cy, px[i], py[i], px[j], py[j], color);
  }
}

void iconFlame(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillTriangle(cx, cy - 13, cx - 8, cy + 4, cx + 8, cy + 4, color);
  tft.fillCircle(cx, cy + 5, 8, color);
  tft.fillCircle(cx, cy + 7, 4, bg);          // hollow core
}

void iconLeaf(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillCircle(cx, cy, 12, color);
  tft.fillCircle(cx - 15, cy - 15, 14, bg);   // carve upper-left -> teardrop
  tft.drawLine(cx - 6, cy + 6, cx + 7, cy - 7, bg);  // central vein
}

// =====================================================================
// Weather / climate icons
// =====================================================================
void iconThermometer(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillRoundRect(cx - 4, cy - 13, 8, 22, 4, color);
  tft.fillCircle(cx, cy + 9, 6, color);
  tft.fillRoundRect(cx - 2, cy - 11, 4, 15, 2, bg);   // hollow tube
  tft.fillCircle(cx, cy + 9, 4, bg);
  tft.fillCircle(cx, cy + 9, 3, color);               // mercury bulb
  tft.fillRect(cx - 1, cy - 1, 2, 10, color);         // mercury column
}

void iconDroplet(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillCircle(cx, cy + 4, 8, color);
  tft.fillTriangle(cx - 8, cy + 2, cx + 8, cy + 2, cx, cy - 12, color);
}

void iconBolt(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillTriangle(cx + 4, cy - 13, cx - 8, cy + 4, cx + 2, cy + 4, color);
  tft.fillTriangle(cx - 4, cy + 13, cx + 8, cy - 4, cx - 2, cy - 4, color);
}

void iconWind(int16_t cx, int16_t cy, uint16_t color) {
  tft.drawFastHLine(cx - 12, cy - 6, 14, color);
  tft.drawLine(cx + 2, cy - 6, cx + 6, cy - 9, color);
  tft.drawLine(cx + 6, cy - 9, cx + 2, cy - 12, color);
  tft.drawFastHLine(cx - 12, cy, 20, color);
  tft.drawLine(cx + 8, cy, cx + 12, cy - 3, color);
  tft.drawLine(cx + 12, cy - 3, cx + 8, cy - 6, color);
  tft.drawFastHLine(cx - 12, cy + 6, 12, color);
  tft.drawLine(cx, cy + 6, cx + 4, cy + 9, color);
  tft.drawLine(cx + 4, cy + 9, cx, cy + 12, color);
}

// =====================================================================
// Connectivity / status icons
// =====================================================================
void iconWifi(int16_t cx, int16_t cy, uint16_t color) {
  int16_t by = cy + 8;
  tft.fillCircle(cx, by, 2, color);            // node
  tft.drawCircleHelper(cx, by, 6, 0x3, color); // top-half arcs
  tft.drawCircleHelper(cx, by, 10, 0x3, color);
  tft.drawCircleHelper(cx, by, 14, 0x3, color);
}

void iconBluetooth(int16_t cx, int16_t cy, uint16_t color) {
  tft.drawLine(cx, cy - 12, cx, cy + 12, color);
  tft.drawLine(cx, cy - 12, cx + 7, cy - 5, color);
  tft.drawLine(cx + 7, cy - 5, cx - 7, cy + 5, color);
  tft.drawLine(cx, cy + 12, cx + 7, cy + 5, color);
  tft.drawLine(cx + 7, cy + 5, cx - 7, cy - 5, color);
}

void iconBattery(int16_t cx, int16_t cy, uint16_t color) {
  tft.drawRoundRect(cx - 13, cy - 7, 24, 14, 2, color);
  tft.drawRoundRect(cx - 12, cy - 6, 22, 12, 2, color);
  tft.fillRect(cx + 11, cy - 3, 3, 6, color);   // terminal nub
  tft.fillRect(cx - 9, cy - 3, 12, 6, color);   // ~60% charge
}

void iconVolume(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillRect(cx - 12, cy - 4, 5, 8, color);
  tft.fillTriangle(cx - 7, cy - 9, cx - 7, cy + 9, cx + 1, cy, color);
  tft.drawCircleHelper(cx + 1, cy, 6, 0x2 | 0x4, color);   // right-side waves
  tft.drawCircleHelper(cx + 1, cy, 10, 0x2 | 0x4, color);
}

void iconBell(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillCircle(cx, cy - 9, 2, color);         // top knob
  tft.fillTriangle(cx - 10, cy + 6, cx + 10, cy + 6, cx, cy - 7, color);
  tft.fillRoundRect(cx - 11, cy + 5, 22, 4, 2, color);   // rim
  tft.fillCircle(cx, cy + 11, 2, color);        // clapper
}

// =====================================================================
// Action / control icons
// =====================================================================
void iconHome(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillTriangle(cx - 13, cy - 1, cx + 13, cy - 1, cx, cy - 13, color);
  tft.fillRect(cx - 9, cy - 1, 18, 13, color);
  tft.fillRect(cx - 3, cy + 4, 6, 8, bg);       // door
}

void iconLock(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.drawCircleHelper(cx, cy - 2, 6, 0x3, color);   // shackle arc
  tft.drawCircleHelper(cx, cy - 2, 7, 0x3, color);
  tft.drawFastVLine(cx - 6, cy - 8, 6, color);
  tft.drawFastVLine(cx - 7, cy - 8, 6, color);
  tft.drawFastVLine(cx + 6, cy - 8, 6, color);
  tft.drawFastVLine(cx + 7, cy - 8, 6, color);
  tft.fillRoundRect(cx - 9, cy - 2, 18, 15, 3, color);   // body
  tft.fillCircle(cx, cy + 3, 2, bg);            // keyhole
  tft.fillRect(cx - 1, cy + 3, 2, 5, bg);
}

void iconTrash(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillRect(cx - 10, cy - 8, 20, 3, color);  // lid
  tft.fillRect(cx - 4, cy - 12, 8, 3, color);   // handle
  tft.fillRoundRect(cx - 8, cy - 4, 16, 18, 2, color);   // can
  tft.drawFastVLine(cx - 3, cy - 1, 12, bg);    // ribs
  tft.drawFastVLine(cx,     cy - 1, 12, bg);
  tft.drawFastVLine(cx + 3, cy - 1, 12, bg);
}

void iconPlus(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillRect(cx - 10, cy - 2, 20, 4, color);
  tft.fillRect(cx - 2, cy - 10, 4, 20, color);
}

void iconMinus(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillRect(cx - 10, cy - 2, 20, 4, color);
}

void iconCheck(int16_t cx, int16_t cy, uint16_t color) {
  tft.drawLine(cx - 9, cy + 1, cx - 3, cy + 8, color);
  tft.drawLine(cx - 8, cy + 1, cx - 2, cy + 8, color);
  tft.drawLine(cx - 3, cy + 8, cx + 10, cy - 8, color);
  tft.drawLine(cx - 2, cy + 8, cx + 11, cy - 8, color);
}

void iconCross(int16_t cx, int16_t cy, uint16_t color) {
  for (int o = -1; o <= 1; o++) {
    tft.drawLine(cx - 9, cy - 9 + o, cx + 9, cy + 9 + o, color);
    tft.drawLine(cx - 9, cy + 9 + o, cx + 9, cy - 9 + o, color);
  }
}

void iconClock(int16_t cx, int16_t cy, uint16_t color) {
  tft.drawCircle(cx, cy, 12, color);
  tft.drawCircle(cx, cy, 11, color);
  tft.drawLine(cx, cy, cx, cy - 7, color);      // minute hand
  tft.drawLine(cx, cy, cx + 5, cy + 2, color);  // hour hand
}

void iconTimer(int16_t cx, int16_t cy, uint16_t color) {
  tft.drawFastHLine(cx - 9, cy - 11, 18, color);
  tft.drawFastHLine(cx - 9, cy + 11, 18, color);
  tft.fillTriangle(cx - 9, cy - 10, cx + 9, cy - 10, cx, cy, color);
  tft.fillTriangle(cx - 9, cy + 10, cx + 9, cy + 10, cx, cy, color);
}

void iconMusic(int16_t cx, int16_t cy, uint16_t color) {
  tft.fillCircle(cx - 5, cy + 8, 4, color);
  tft.fillCircle(cx + 8, cy + 5, 4, color);
  tft.fillRect(cx - 2, cy - 10, 3, 18, color);
  tft.fillRect(cx + 8, cy - 13, 3, 18, color);
  tft.fillRect(cx - 2, cy - 13, 13, 4, color);  // beam
}

void iconPalette(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillCircle(cx, cy, 12, color);
  tft.fillCircle(cx + 5, cy + 6, 4, bg);        // thumb hole
  tft.fillCircle(cx - 6, cy - 4, 2, bg);        // paint wells
  tft.fillCircle(cx, cy - 7, 2, bg);
  tft.fillCircle(cx + 6, cy - 4, 2, bg);
}

void iconWarning(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillTriangle(cx, cy - 13, cx - 13, cy + 10, cx + 13, cy + 10, color);
  tft.fillRect(cx - 1, cy - 5, 3, 8, bg);       // bang stem
  tft.fillRect(cx - 1, cy + 5, 3, 3, bg);       // bang dot
}

void iconInfo(int16_t cx, int16_t cy, uint16_t color, uint16_t bg) {
  tft.fillCircle(cx, cy, 12, color);
  tft.fillCircle(cx, cy - 5, 2, bg);            // dot
  tft.fillRect(cx - 1, cy - 1, 3, 8, bg);       // stem
}
