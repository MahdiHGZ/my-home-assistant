#!/usr/bin/env python3
"""Render every tuch_controller screen to a PNG — from the real firmware code.

This builds a tiny host program (tools/host_preview/preview.cpp) that #includes
the actual sketch, compiled against desktop shims, and calls the genuine
drawPage()/showDeviceInfo() for each page. The layout comes from pages.h and the
drawing from tuch_controller.ino — there is NO duplicated Python UI code here;
this script only compiles and runs the C++ renderer.

Usage:
    python3 render_preview.py            # build + render to ./preview/
    python3 render_preview.py --run-only # skip rebuild, just re-render

Output: tuch_controller/preview/*.png
Requires: a C++17 compiler (g++ or clang++). No Arduino toolchain needed.
"""
import os
import shutil
import subprocess
import sys

SKETCH_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.join(SKETCH_DIR, "tools", "host_preview")
BUILD = os.path.join(HOST, "build")
PREVIEW = os.path.join(SKETCH_DIR, "preview")
BIN = os.path.join(BUILD, "preview")


def find_compiler():
    for c in ("g++", "clang++"):
        if shutil.which(c):
            return c
    sys.exit("error: no C++ compiler found (need g++ or clang++).")


def build():
    cc = find_compiler()
    os.makedirs(BUILD, exist_ok=True)
    sources = [
        os.path.join(HOST, "preview.cpp"),
        os.path.join(HOST, "vendor", "Adafruit_GFX", "Adafruit_GFX.cpp"),
    ]
    cmd = [
        cc, "-std=c++17", "-O1", "-w",
        "-DARDUINO=10819",                       # take the "modern" include paths in libs
        "-I", os.path.join(HOST, "shim"),
        "-I", os.path.join(HOST, "vendor", "Adafruit_GFX"),
        *sources,
        "-o", BIN,
    ]
    print("building:", " ".join(os.path.relpath(c, SKETCH_DIR) if os.path.isabs(c) else c for c in cmd))
    r = subprocess.run(cmd, cwd=HOST)
    if r.returncode != 0:
        sys.exit("build failed.")


def render():
    if not os.path.exists(BIN):
        sys.exit("renderer not built yet — run without --run-only first.")
    os.makedirs(PREVIEW, exist_ok=True)
    r = subprocess.run([BIN, PREVIEW], cwd=SKETCH_DIR)
    if r.returncode != 0:
        sys.exit("render failed.")
    pngs = sorted(f for f in os.listdir(PREVIEW) if f.endswith(".png"))
    print(f"\n{len(pngs)} screens -> {os.path.relpath(PREVIEW, os.getcwd())}/")
    for p in pngs:
        print("  ", p)


if __name__ == "__main__":
    if "--run-only" not in sys.argv:
        build()
    render()
