// Host shim for <XPT2046_Touchscreen.h>. No touch on the desktop renderer.
#pragma once
#include "Arduino.h"
#include "SPI.h"

class TS_Point {
public:
  int16_t x, y;
  int16_t z;
  TS_Point() : x(0), y(0), z(0) {}
  TS_Point(int16_t x_, int16_t y_, int16_t z_) : x(x_), y(y_), z(z_) {}
};

class XPT2046_Touchscreen {
public:
  XPT2046_Touchscreen(uint8_t = 255, uint8_t = 255) {}
  bool begin() { return true; }
  bool begin(SPIClass&) { return true; }
  void setRotation(uint8_t) {}
  bool touched() { return false; }
  TS_Point getPoint() { return TS_Point(); }
};
