// Host shim for <HTTPClient.h>. No real I/O — networking code only needs to
// compile (it's never exercised by the static page renderer).
#pragma once
#include "Arduino.h"
#include "WiFi.h"

class HTTPClient {
public:
  bool begin(WiFiClient&, const String&) { return true; }
  bool begin(WiFiClient&, const char*) { return true; }
  void addHeader(const String&, const String&) {}
  void addHeader(const char*, const char*) {}
  void setTimeout(uint16_t) {}
  void setConnectTimeout(int32_t) {}
  int POST(uint8_t*, size_t) { return -1; }
  int POST(const String&) { return -1; }
  int GET() { return -1; }
  String getString() { return String(""); }
  WiFiClient* getStreamPtr() { static WiFiClient c; return &c; }
  void end() {}
};
