# Host preview renderer

Renders every `tuch_controller` screen to a PNG **on the desktop, from the
real firmware code** — so you can see UI changes instantly without flashing.

## Run it

From the sketch folder:

```bash
python3 render_preview.py          # build + render to ./preview/
python3 render_preview.py --run-only   # re-render without rebuilding
```

Output lands in `tuch_controller/preview/` (one PNG per screen, 2×).
Needs a C++17 compiler (`g++`/`clang++`); no Arduino toolchain required.

## How it works (no duplicated UI code)

```
render_preview.py
   └─ g++  preview.cpp  +  vendor/Adafruit_GFX/Adafruit_GFX.cpp
            │
            ├─ #include "../../tuch_controller.ino"      ← the REAL firmware
            │        └─ #include "pages.h"               ← the REAL layouts
            └─ shims/ replace the hardware (WiFi, ESP, SPI, touch, ...)
```

- `preview.cpp` includes the actual sketch and calls the genuine `drawPage()` /
  `showDeviceInfo()` for each page, then writes `tft`'s 320×240 RGB565
  framebuffer to PNG.
- `shim/` provides desktop stand-ins for the Arduino/ESP32 headers so the sketch
  compiles natively. `shim/Adafruit_ILI9341.h` is a real `Adafruit_GFX` subclass
  whose `drawPixel()` writes into a memory buffer — all geometry and text come
  from the **vendored, genuine `Adafruit_GFX`**, so the output matches the device
  (same fonts, same rounded-rect/circle math).
- The screen list and every button rectangle come from `pages.h`, shared
  verbatim with the firmware. There is **no Python/C++ re-implementation of the
  UI** — change the sketch or `pages.h` and the preview updates automatically.

## Layout

```
tools/host_preview/
  preview.cpp            host main: sample state -> drawPage() -> PNG
  shim/                  Arduino/WiFi/ESP/SPI/touch/... desktop stubs
    Adafruit_ILI9341.h   GFX subclass -> RGB565 framebuffer
  vendor/
    Adafruit_GFX/        genuine Adafruit_GFX (drawing + fonts)
    stb_image_write.h    single-header PNG writer
  build/                 compiled binary (gitignored)
```

## Tweaking

- **Different on-screen data** (battery %, which mode is active, lights off,
  etc.): edit `sampleState()` in `preview.cpp`.
- **A new page**: add it to the `pages[]` table in `preview.cpp` `main()`.
- The shims only need to *compile* the networking/sleep code, not run it; if you
  add a firmware call to a new Arduino API, add a stub for it under `shim/`.
