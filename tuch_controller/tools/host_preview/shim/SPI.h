// Host shim for <SPI.h>.
#pragma once
#include "Arduino.h"
#define FSPI 0
#define HSPI 1
#define VSPI 2
class SPIClass {
public:
  SPIClass(uint8_t = 0) {}
  void begin(int = -1, int = -1, int = -1, int = -1) {}
  void end() {}
};
