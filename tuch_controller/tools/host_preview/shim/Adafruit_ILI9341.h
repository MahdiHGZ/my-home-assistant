// Host shim for <Adafruit_ILI9341.h>.
//
// This is the heart of the preview: a real Adafruit_GFX subclass whose only job
// is to implement drawPixel() into an in-memory 320x240 RGB565 framebuffer. All
// the actual geometry/text (rounded rects, circles, fonts, getTextBounds, ...)
// comes from the genuine vendored Adafruit_GFX.cpp — so the desktop output is
// drawn by the same code the device runs.
#pragma once
#include "Adafruit_GFX.h"
#include "SPI.h"
#include <cstring>

#define ILI9341_TFTWIDTH 240
#define ILI9341_TFTHEIGHT 320
#define ILI9341_BLACK 0x0000
#define ILI9341_WHITE 0xFFFF

class Adafruit_ILI9341 : public Adafruit_GFX {
public:
  static const int FB_W = 320;   // landscape (rotation 1) — matches the firmware
  static const int FB_H = 240;
  uint16_t fb[FB_W * FB_H];

  // The firmware uses the (SPIClass*, dc, cs, rst) constructor; provide the
  // common ones for completeness. GFX is built in portrait, setRotation(1) flips
  // it to 320x240 just like the real driver.
  Adafruit_ILI9341(SPIClass*, int8_t, int8_t, int8_t = -1)
      : Adafruit_GFX(ILI9341_TFTWIDTH, ILI9341_TFTHEIGHT) { clear(); }
  Adafruit_ILI9341(int8_t, int8_t, int8_t = -1)
      : Adafruit_GFX(ILI9341_TFTWIDTH, ILI9341_TFTHEIGHT) { clear(); }

  void begin(uint32_t = 0) {}
  void clear() { memset(fb, 0, sizeof(fb)); }
  uint16_t* framebuffer() { return fb; }

  void drawPixel(int16_t x, int16_t y, uint16_t color) override {
    if (x < 0 || y < 0 || x >= width() || y >= height()) return;
    fb[y * FB_W + x] = color;   // width()==320 after setRotation(1)
  }

  // Present in Adafruit_SPITFT on the device (not in GFX core), so we add it.
  uint16_t color565(uint8_t r, uint8_t g, uint8_t b) {
    return ((uint16_t)(r & 0xF8) << 8) | ((uint16_t)(g & 0xFC) << 3) | (b >> 3);
  }
};
