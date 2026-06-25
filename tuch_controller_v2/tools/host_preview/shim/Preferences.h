// Host shim for <Preferences.h>. begin() returns false so the firmware falls
// back to its compiled-in defaults (the same defaults a fresh device uses).
#pragma once
#include "Arduino.h"
class Preferences {
public:
  bool begin(const char*, bool = false) { return false; }
  void end() {}
  bool isKey(const char*) { return false; }
  int getInt(const char*, int d = 0) { return d; }
  unsigned long getULong(const char*, unsigned long d = 0) { return d; }
  bool getBool(const char*, bool d = false) { return d; }
  uint8_t getUChar(const char*, uint8_t d = 0) { return d; }
  size_t putInt(const char*, int) { return 0; }
  size_t putULong(const char*, unsigned long) { return 0; }
  size_t putBool(const char*, bool) { return 0; }
  size_t putUChar(const char*, uint8_t) { return 0; }
};
