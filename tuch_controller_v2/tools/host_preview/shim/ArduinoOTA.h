// Host shim for <ArduinoOTA.h>.
#pragma once
#include "Arduino.h"
struct ArduinoOTAClass {
  void setHostname(const char*) {}
  void setPassword(const char*) {}
  void begin() {}
  void handle() {}
};
inline ArduinoOTAClass ArduinoOTA;
