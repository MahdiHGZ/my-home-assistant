// Host shim for Arduino's Print base class (enough for Adafruit_GFX + firmware).
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstring>

class Print {
public:
  virtual ~Print() {}
  virtual size_t write(uint8_t) = 0;
  virtual size_t write(const uint8_t* buf, size_t n) {
    size_t c = 0;
    while (n--) c += write(*buf++);
    return c;
  }
  size_t write(const char* s) { return s ? write((const uint8_t*)s, strlen(s)) : 0; }
  size_t print(const char* s) { return write(s); }
  size_t print(char c) { return write((uint8_t)c); }
  size_t print(int n) { char b[16]; int l = snprintf(b, sizeof b, "%d", n); return write((const uint8_t*)b, l); }
  size_t print(unsigned n) { char b[16]; int l = snprintf(b, sizeof b, "%u", n); return write((const uint8_t*)b, l); }
  size_t print(long n) { char b[24]; int l = snprintf(b, sizeof b, "%ld", n); return write((const uint8_t*)b, l); }
  size_t print(unsigned long n) { char b[24]; int l = snprintf(b, sizeof b, "%lu", n); return write((const uint8_t*)b, l); }
  size_t println(const char* s = "") { size_t r = print(s); r += write((uint8_t)'\n'); return r; }
};
