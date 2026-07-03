// ============================================================================
// preview.cpp — desktop renderer for the tuch_controller UI.
//
// It #includes the REAL firmware sketch (compiled against the host shims) and
// calls the genuine drawPage()/showDeviceInfo() for each screen, then writes the
// resulting 320x240 framebuffer to a PNG. Nothing here re-implements the UI —
// the layout comes from pages.h and the drawing from the .ino, exactly as the
// device runs it.
// ============================================================================
#include <string>
#include <cstdio>
#include <cstdint>
#include <cstddef>
#include <vector>

// millis() backing store (declared extern in the Arduino shim).
unsigned long _host_millis = 2UL * 3600 * 1000;   // pretend ~2h uptime

// The Arduino IDE auto-generates a forward prototype for every function in a
// .ino before the first definition, which lets functions call each other in any
// order. A plain C++ compile doesn't, so we declare the few functions that are
// used before they're defined in the sketch. (Mirrors the IDE's behaviour.)
#include "../../pages.h"
bool currentClock(char* buf, size_t n);
int  vacActiveId();
void calDrawCross(int16_t x, int16_t y, uint16_t color);
void runCalibration();
void loadConfig();
void saveConfig();

// Pull in the entire firmware (drawing code, state, globals, the `tft` object).
#include "../../tuch_controller.ino"

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "vendor/stb_image_write.h"

// ---- RGB565 framebuffer -> scaled RGB888 PNG --------------------------------
static void savePNG(const std::string& path, int scale) {
  const int W = Adafruit_ILI9341::FB_W, H = Adafruit_ILI9341::FB_H;
  const uint16_t* fb = tft.framebuffer();
  std::vector<unsigned char> img((size_t)W * scale * H * scale * 3);
  for (int y = 0; y < H; y++) {
    for (int x = 0; x < W; x++) {
      uint16_t c = fb[y * W + x];
      unsigned char r = ((c >> 11) & 0x1F) * 255 / 31;
      unsigned char g = ((c >> 5) & 0x3F) * 255 / 63;
      unsigned char b = (c & 0x1F) * 255 / 31;
      for (int sy = 0; sy < scale; sy++) {
        for (int sx = 0; sx < scale; sx++) {
          size_t idx = ((size_t)(y * scale + sy) * (W * scale) + (x * scale + sx)) * 3;
          img[idx] = r; img[idx + 1] = g; img[idx + 2] = b;
        }
      }
    }
  }
  stbi_write_png(path.c_str(), W * scale, H * scale, 3, img.data(), W * scale * 3);
}

// Populate the synced-state model with a realistic snapshot so every page shows
// live-looking data (lights warm-on, vacuum charging, air good, etc.).
static void sampleState() {
  st = DeviceState{};
  st.valid = true;
  st.lightsAvail = true; st.bulbsOn = 6; st.bulbsTotal = 6;
  strlcpy(st.lightMode, "warm white", sizeof(st.lightMode));
  st.vacAvail = true; strlcpy(st.vacStatus, "Charging Complete", sizeof(st.vacStatus)); st.vacBattery = 100;
  st.purAvail = true; st.purOn = true;
  strlcpy(st.purMode, "Auto", sizeof(st.purMode));
  strlcpy(st.purFan, "Medium", sizeof(st.purFan));
  st.pm25 = 12; st.tempC = 24; st.humidity = 45;
  st.moments = 3; st.serverHour = 19;

  curBrightness = 65; curHue = 30; curSat = 80;
  netReady = true; serverReachable = true; screenOn = true;
  previewActive = alertActive = diagActive = false;
  errorSticky = false; toastHoldUntil = 0;
}

int main(int argc, char** argv) {
  std::string out = (argc > 1) ? argv[1] : "preview";

  tft.setRotation(1);   // landscape 320x240 — the firmware does this in setup()
  applyTheme(true);     // dark palette -> COL_* runtime vars

  struct PG { Page p; const char* name; } pages[] = {
    { PAGE_MAIN,   "01_main" },   { PAGE_LIGHTS, "02_lights" },
    { PAGE_MODES,  "03_modes" },  { PAGE_COLOR,  "04_color" },
    { PAGE_VACUUM, "05_vacuum" }, { PAGE_AIR,    "06_air" },
    { PAGE_CAM,    "07_camera" }, { PAGE_SET,    "08_setup" },
  };

  for (auto& g : pages) {
    sampleState();
    currentPage = g.p;
    drawPage();
    savePNG(out + "/" + g.name + ".png", 2);
  }

  // The SETUP -> DEVICE full-screen overlay.
  sampleState();
  currentPage = PAGE_SET;
  showDeviceInfo();
  savePNG(out + "/09_device.png", 2);

  printf("rendered %d screens to %s/\n", (int)(sizeof(pages) / sizeof(pages[0])) + 1, out.c_str());
  return 0;
}
