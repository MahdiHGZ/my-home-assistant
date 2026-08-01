// ============================================================================
// Host shim for <Arduino.h> — just enough of the Arduino/ESP32 runtime for the
// tuch_controller firmware (and Adafruit_GFX) to COMPILE and run natively so
// we can render the real UI to PNG. Nothing here talks to hardware.
// ============================================================================
#pragma once

// Pull STL in FIRST, before we define the Arduino min/max/abs macros, so the
// macros can't poison the standard headers.
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <strings.h>
#include <ctype.h>
#include <math.h>
#include <string>

#include "Print.h"

// ---- basic Arduino-isms -----------------------------------------------------
#ifndef PI
#define PI 3.1415926535897932384626433832795
#endif
#define HALF_PI 1.5707963267948966192313216916398
#define TWO_PI 6.283185307179586476925286766559
#define DEG_TO_RAD 0.017453292519943295769236907684886
#define RAD_TO_DEG 57.295779513082320876798154814105
#define radians(deg) ((deg) * DEG_TO_RAD)
#define degrees(rad) ((rad) * RAD_TO_DEG)
#define sq(x) ((x) * (x))
#define PROGMEM
#define RTC_DATA_ATTR
#define F(x) (x)
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
class __FlashStringHelper;

typedef uint8_t byte;
typedef bool boolean;

// pgm_read_* fall back to plain dereference on host (Adafruit_GFX also guards
// these, but the font headers reference them too).
#ifndef pgm_read_byte
#define pgm_read_byte(a) (*(const unsigned char*)(a))
#endif
#ifndef pgm_read_word
#define pgm_read_word(a) (*(const unsigned short*)(a))
#endif
#ifndef pgm_read_dword
#define pgm_read_dword(a) (*(const unsigned long*)(a))
#endif

// ---- timing / io (no-ops) ---------------------------------------------------
extern unsigned long _host_millis;
inline unsigned long millis() { return _host_millis; }
inline unsigned long micros() { return _host_millis * 1000UL; }
inline void delay(unsigned long) {}
inline void delayMicroseconds(unsigned int) {}
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline int  digitalRead(int) { return HIGH; }
inline void analogWrite(int, int) {}
inline void yield() {}

// ---- math helpers (macros, defined AFTER the STL includes above) ------------
#ifndef min
#define min(a, b) ((a) < (b) ? (a) : (b))
#endif
#ifndef max
#define max(a, b) ((a) > (b) ? (a) : (b))
#endif
#ifndef abs
#define abs(x) ((x) > 0 ? (x) : -(x))
#endif
#ifndef constrain
#define constrain(a, l, h) ((a) < (l) ? (l) : ((a) > (h) ? (h) : (a)))
#endif
#ifndef map
#define map(x, il, ih, ol, oh) (((x) - (il)) * ((oh) - (ol)) / ((ih) - (il)) + (ol))
#endif
#ifndef isDigit
#define isDigit(c) (isdigit((int)(c)) != 0)
#endif

// ---- minimal Arduino String over std::string --------------------------------
class String {
  std::string s;
public:
  String() {}
  String(const char* p) : s(p ? p : "") {}
  String(const std::string& x) : s(x) {}
  explicit String(char c) : s(1, c) {}
  String(int v)           { char b[24]; snprintf(b, sizeof b, "%d", v);  s = b; }
  String(unsigned v)      { char b[24]; snprintf(b, sizeof b, "%u", v);  s = b; }
  String(long v)          { char b[24]; snprintf(b, sizeof b, "%ld", v); s = b; }
  String(unsigned long v) { char b[24]; snprintf(b, sizeof b, "%lu", v); s = b; }

  int length() const { return (int)s.size(); }
  const char* c_str() const { return s.c_str(); }
  char operator[](int i) const { return (i >= 0 && i < (int)s.size()) ? s[i] : 0; }
  char charAt(int i) const { return (*this)[i]; }

  int indexOf(const String& p, int from = 0) const {
    if (from < 0) from = 0;
    std::string::size_type r = s.find(p.s, from);
    return r == std::string::npos ? -1 : (int)r;
  }
  int indexOf(char c, int from = 0) const {
    if (from < 0) from = 0;
    std::string::size_type r = s.find(c, from);
    return r == std::string::npos ? -1 : (int)r;
  }
  String substring(int a) const {
    if (a < 0) a = 0; if (a > (int)s.size()) a = (int)s.size();
    return String(s.substr(a));
  }
  String substring(int a, int b) const {
    if (a < 0) a = 0; if (b > (int)s.size()) b = (int)s.size(); if (b < a) b = a;
    return String(s.substr(a, b - a));
  }
  long toInt() const { return atol(s.c_str()); }

  String operator+(const String& o) const { return String(s + o.s); }
  String operator+(const char* o) const { return String(s + (o ? o : "")); }
  String operator+(char c) const { return String(s + std::string(1, c)); }
  String operator+(int v) const { return *this + String(v); }
  String operator+(unsigned v) const { return *this + String(v); }
  String operator+(long v) const { return *this + String(v); }
  String operator+(unsigned long v) const { return *this + String(v); }
  String& operator+=(const String& o) { s += o.s; return *this; }
  bool operator==(const char* o) const { return s == (o ? o : ""); }
  bool operator==(const String& o) const { return s == o.s; }
};
inline String operator+(const char* a, const String& b) {
  return String(std::string(a ? a : "") + b.c_str());
}

// ---- Serial (no-op sink) ----------------------------------------------------
struct HostSerial {
  void begin(long) {}
  void flush() {}
  template <class... A> void print(A...) {}
  template <class... A> void println(A...) {}
  template <class... A> int  printf(A...) { return 0; }
};
inline HostSerial Serial;

// ---- ESP info ---------------------------------------------------------------
struct HostESP {
  uint32_t getFreeHeap()    { return 186u * 1024; }
  uint32_t getMinFreeHeap() { return 120u * 1024; }
  uint32_t getHeapSize()    { return 320u * 1024; }
  const char* getChipModel() { return "ESP32-S3"; }
  uint8_t  getChipCores()   { return 2; }
  uint32_t getFlashChipSize() { return 8u * 1024 * 1024; }
};
inline HostESP ESP;

// ---- FreeRTOS (stubs; never actually scheduled on host) ---------------------
typedef void* QueueHandle_t;
typedef void* TaskHandle_t;
typedef int   BaseType_t;
typedef unsigned UBaseType_t;
#define pdTRUE 1
#define pdFALSE 0
#define portMAX_DELAY 0xffffffffUL
inline QueueHandle_t xQueueCreate(UBaseType_t, UBaseType_t) { return (QueueHandle_t)1; }
inline BaseType_t xQueueReceive(QueueHandle_t, void*, uint32_t) { return pdFALSE; }
inline BaseType_t xQueueSend(QueueHandle_t, const void*, uint32_t) { return pdTRUE; }
inline BaseType_t xQueueSendToFront(QueueHandle_t, const void*, uint32_t) { return pdTRUE; }
inline UBaseType_t uxQueueMessagesWaiting(QueueHandle_t) { return 0; }
inline BaseType_t xTaskCreatePinnedToCore(void (*)(void*), const char*, uint32_t,
                                          void*, UBaseType_t, TaskHandle_t*, BaseType_t) { return pdTRUE; }
inline void vTaskDelay(uint32_t) {}
inline UBaseType_t uxTaskGetStackHighWaterMark(TaskHandle_t) { return 4096; }

// ---- NTP helper (Arduino/ESP) ----------------------------------------------
inline void configTime(long, int, const char*) {}
inline void configTime(long, int, const char*, const char*) {}
