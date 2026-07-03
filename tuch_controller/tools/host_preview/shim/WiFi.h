// Host shim for <WiFi.h>. Reports a plausible "connected" state with sample
// network details so the preview shows realistic header/island/device info.
#pragma once
#include "Arduino.h"

#define WL_CONNECTED 3
#define WL_DISCONNECTED 6
#define WIFI_STA 1
#define WIFI_OFF 0

struct IPAddress {
  uint8_t a, b, c, d;
  IPAddress(uint8_t a_ = 0, uint8_t b_ = 0, uint8_t c_ = 0, uint8_t d_ = 0)
      : a(a_), b(b_), c(c_), d(d_) {}
  String toString() const {
    char buf[16];
    snprintf(buf, sizeof buf, "%u.%u.%u.%u", a, b, c, d);
    return String(buf);
  }
};

class WiFiClient {
public:
  bool connect(const char*, uint16_t, int = 0) { return false; }
  bool connect(IPAddress, uint16_t, int = 0) { return false; }
  bool connected() { return true; }   // preview shows the green "stream up" dot
  size_t print(const String&) { return 0; }
  size_t print(const char*) { return 0; }
  int available() { return 0; }
  int read() { return -1; }
  int read(uint8_t*, size_t) { return 0; }
  void stop() {}
};

class WiFiClass {
public:
  void mode(int) {}
  void setAutoReconnect(bool) {}
  void setSleep(bool) {}
  void begin(const char*, const char*) {}
  int status() { return WL_CONNECTED; }
  IPAddress localIP() { return IPAddress(192, 168, 1, 124); }
  IPAddress gatewayIP() { return IPAddress(192, 168, 1, 1); }
  int RSSI() { return -58; }
  String SSID() { return String("MahdiHome"); }
  String macAddress() { return String("A0:B7:65:12:34:56"); }
};
inline WiFiClass WiFi;
