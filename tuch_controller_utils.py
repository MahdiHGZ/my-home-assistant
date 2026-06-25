"""Touch-controller (ESP32 wall panel) utilities for Coukab LAN.

The panel is a *pull* client: it holds an SSE connection to ``web_server`` and
fetches server-rendered status, photos, and alerts. This module is the server
side of that relationship:

- **alerts**: store / get / render the popup the panel shows (:func:`send_alert`),
  rendered entirely server-side as the panel's native RGB565 pixels.
- **pixel format**: :func:`bgr_to_rgb565_bytes` (shared with the photo thumbnail).
- **status**: :func:`flatten_status` reduces the full status snapshot to the
  tiny flat payload the panel consumes.
- **discovery**: :func:`find_panel_ip` locates the panel on the LAN (mDNS, or
  the IP last seen on the SSE stream via :func:`register_panel`).

``web_server`` wires its SSE broadcaster in with :func:`set_broadcaster` at
startup and delegates the ``/api/panel/*`` endpoints here.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Callable, Optional

from tooling import brain_tool

logger = logging.getLogger(__name__)

# Default web port (used by the CLI to POST to a running server).
WEB_PORT = 8080

# ----------------------------------------------------------------------------
# Panel discovery
# ----------------------------------------------------------------------------
# ArduinoOTA advertises this mDNS hostname (see config.h: OTA_HOSTNAME).
PANEL_HOSTNAME = "coukab-panel.local"

_panel_lock = threading.Lock()
_panel_ips: set[str] = set()
_last_panel_ip: Optional[str] = None


def register_panel(ip: Optional[str]) -> None:
    """Records a panel IP seen on the SSE stream (called by web_server)."""
    global _last_panel_ip
    if not ip:
        return
    with _panel_lock:
        _panel_ips.add(ip)
        _last_panel_ip = ip


def connected_panels() -> list[str]:
    """IPs of panels seen connecting to the event stream this session."""
    with _panel_lock:
        return sorted(_panel_ips)


def find_panel_ip(hostname: str = PANEL_HOSTNAME) -> Optional[str]:
    """Best-effort panel IP: mDNS hostname first, else the last SSE client.

    Returns None if neither is available.
    """
    try:
        ip = socket.gethostbyname(hostname)
        register_panel(ip)
        return ip
    except OSError:
        pass
    with _panel_lock:
        return _last_panel_ip


# ----------------------------------------------------------------------------
# SSE broadcast hook (set by web_server so alerts can be pushed)
# ----------------------------------------------------------------------------
_broadcaster: Optional[Callable[[str, str], None]] = None


def set_broadcaster(fn: Optional[Callable[[str, str], None]]) -> None:
    """Registers the SSE broadcast function ``fn(event, json_payload)``."""
    global _broadcaster
    _broadcaster = fn


# ----------------------------------------------------------------------------
# Alerts
# ----------------------------------------------------------------------------
ALERT_MAX_LEN = 200
_VALID_LEVELS = ("info", "alert")

_alert_lock = threading.Lock()
_alert: Optional[dict] = None
_alert_seq = 0


def send_alert(text: str, level: str = "info") -> dict:
    """Stores a new alert and pushes an ``alert`` SSE event to all clients.

    Args:
        text: Message to show (trimmed, capped at ``ALERT_MAX_LEN``, Latin —
            the renderer uses cv2's ASCII-only Hershey fonts).
        level: ``"info"`` (cyan NOTICE) or ``"alert"`` (red ALERT).

    Returns:
        The stored alert dict: ``{ok, id, text, level, created}``.

    Raises:
        ValueError: If ``text`` is empty.
    """
    global _alert, _alert_seq

    text = (text or "").strip()
    if not text:
        raise ValueError("Alert text is empty.")
    text = text[:ALERT_MAX_LEN]
    level = (level or "info").lower()
    if level not in _VALID_LEVELS:
        level = "info"

    with _alert_lock:
        _alert_seq += 1
        _alert = {
            "id": _alert_seq,
            "text": text,
            "level": level,
            "created": time.strftime("%H:%M"),
        }
        alert = dict(_alert)

    if _broadcaster is not None:
        try:
            _broadcaster("alert", json.dumps({"id": alert["id"]}))
        except Exception:
            logger.exception("Alert SSE broadcast failed.")
    logger.info("Panel alert #%d (%s): %s", alert["id"], level, text)
    return {"ok": True, **alert}


def get_alert() -> dict:
    """Current alert metadata, or ``{ok, id: 0}`` when none is set."""
    with _alert_lock:
        if _alert is None:
            return {"ok": True, "id": 0}
        return {"ok": True, **_alert}


def clear_alert() -> None:
    """Forgets the current alert (used by tests / a future dismiss API)."""
    global _alert
    with _alert_lock:
        _alert = None


@brain_tool
def notify_panel(message: str, urgent: bool = False) -> dict:
    """Show a popup message on the wall touch panel.

    Use this to get the user's attention on the physical panel — e.g.
    "dinner is ready" or "laundry done". Latin text only.

    Args:
        message: The text to display.
        urgent: True for a red ALERT popup, False for a cyan NOTICE.

    Returns:
        Dict with ok and the alert id.
    """
    return send_alert(message, "alert" if urgent else "info")


# ----------------------------------------------------------------------------
# Pixel format + drawing helpers (panel TFT is big-endian RGB565)
# ----------------------------------------------------------------------------
def bgr_to_rgb565_bytes(img) -> bytes:
    """BGR ndarray -> raw big-endian RGB565 bytes for the panel's TFT."""
    import numpy as np

    b = img[:, :, 0].astype(np.uint16)
    g = img[:, :, 1].astype(np.uint16)
    r = img[:, :, 2].astype(np.uint16)
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return rgb565.astype(">u2").tobytes()


def _rounded_mask(h, w, x1, y1, x2, y2, r):
    import cv2
    import numpy as np

    m = np.zeros((h, w), np.uint8)
    cv2.rectangle(m, (x1 + r, y1), (x2 - r, y2), 255, -1)
    cv2.rectangle(m, (x1, y1 + r), (x2, y2 - r), 255, -1)
    for cx, cy in ((x1 + r, y1 + r), (x2 - r, y1 + r),
                   (x1 + r, y2 - r), (x2 - r, y2 - r)):
        cv2.circle(m, (cx, cy), r, 255, -1)
    return m


def _rounded_border(img, x1, y1, x2, y2, r, color, t):
    import cv2

    aa = cv2.LINE_AA
    cv2.line(img, (x1 + r, y1), (x2 - r, y1), color, t, aa)
    cv2.line(img, (x1 + r, y2), (x2 - r, y2), color, t, aa)
    cv2.line(img, (x1, y1 + r), (x1, y2 - r), color, t, aa)
    cv2.line(img, (x2, y1 + r), (x2, y2 - r), color, t, aa)
    cv2.ellipse(img, (x1 + r, y1 + r), (r, r), 180, 0, 90, color, t, aa)
    cv2.ellipse(img, (x2 - r, y1 + r), (r, r), 270, 0, 90, color, t, aa)
    cv2.ellipse(img, (x1 + r, y2 - r), (r, r), 90, 0, 90, color, t, aa)
    cv2.ellipse(img, (x2 - r, y2 - r), (r, r), 0, 0, 90, color, t, aa)


def render_alert_rgb565(w: int = 296, h: int = 150) -> Optional[bytes]:
    """Render the current alert as a modern dark-navy popup card.

    Returns raw big-endian RGB565 (``w*h*2`` bytes) ready to blit, or None if
    there is no alert. Latin text only (cv2 Hershey fonts are ASCII).
    """
    import cv2
    import numpy as np

    with _alert_lock:
        alert = dict(_alert) if _alert else None
    if alert is None:
        return None

    w = max(120, min(320, int(w)))
    h = max(80, min(240, int(h)))
    urgent = alert["level"] == "alert"

    # Palette (BGR). Matches the panel's dark-navy theme.
    bg_out = (24, 12, 8)          # outside the card == firmware COL_BG (blends)
    grad_top = (70, 42, 24)       # card gradient (lighter navy at top)
    grad_bot = (40, 22, 13)
    accent = (96, 96, 245) if urgent else (255, 194, 76)  # red / cyan-blue
    text_col = (240, 240, 235)
    dim_col = (170, 150, 130)
    div_col = (96, 72, 50)
    badge_fg = (28, 18, 10)
    font = cv2.FONT_HERSHEY_SIMPLEX
    R = 16

    # Card gradient masked to a rounded rectangle; corners keep bg_out so the
    # popup reads as a floating rounded card on the panel's background.
    img = np.full((h, w, 3), bg_out, np.uint8)
    ramp = np.linspace(0.0, 1.0, h)[:, None, None]
    grad = (np.array(grad_top, float) * (1 - ramp)
            + np.array(grad_bot, float) * ramp)
    grad = np.repeat(grad, w, axis=1).astype(np.uint8)
    mask = _rounded_mask(h, w, 2, 2, w - 3, h - 3, R)
    img[mask > 0] = grad[mask > 0]
    _rounded_border(img, 2, 2, w - 3, h - 3, R, accent, 2)

    # Header: round badge with a glyph, title, timestamp.
    bx, by = 28, 27
    cv2.circle(img, (bx, by), 13, accent, -1, cv2.LINE_AA)
    glyph = "!" if urgent else "i"
    (gw, gh), _ = cv2.getTextSize(glyph, font, 0.8, 2)
    cv2.putText(img, glyph, (bx - gw // 2, by + gh // 2), font, 0.8,
                badge_fg, 2, cv2.LINE_AA)

    title = "ALERT" if urgent else "NOTICE"
    cv2.putText(img, title, (bx + 22, by + 7), font, 0.66, text_col, 2, cv2.LINE_AA)

    (tw, _), _ = cv2.getTextSize(alert["created"], font, 0.44, 1)
    cv2.putText(img, alert["created"], (w - 16 - tw, by + 4), font, 0.44,
                dim_col, 1, cv2.LINE_AA)

    div_y = by + 22
    cv2.line(img, (16, div_y), (w - 16, div_y), div_col, 1, cv2.LINE_AA)

    # Body: greedy word-wrap against real glyph widths, vertically centered.
    scale, line_h, margin = 0.6, 26, 16
    lines: list[str] = []
    cur = ""
    for word in alert["text"].split():
        trial = (cur + " " + word).strip()
        if cv2.getTextSize(trial, font, scale, 1)[0][0] > w - 2 * margin and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)

    body_top, body_bot = div_y + 6, h - 12
    max_lines = max(1, (body_bot - body_top) // line_h)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:max(0, len(lines[-1]) - 1)] + "…".encode("ascii", "replace").decode()
    block_h = len(lines) * line_h
    y = body_top + (body_bot - body_top - block_h) // 2 + 18
    for line in lines:
        (lw, _), _ = cv2.getTextSize(line, font, scale, 1)
        cv2.putText(img, line, ((w - lw) // 2, y), font, scale,
                    text_col, 1, cv2.LINE_AA)
        y += line_h

    return bgr_to_rgb565_bytes(img)


# ----------------------------------------------------------------------------
# Status flattening (full snapshot -> tiny flat panel payload)
# ----------------------------------------------------------------------------
def _num(value, default):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def flatten_status(snapshot: dict, hour: Optional[int] = None) -> dict:
    """Reduce the full status snapshot to the flat ~250-byte panel payload.

    Args:
        snapshot: The dict from ``web_server._build_status``.
        hour: Server local hour (0-23) for the panel's night-mode fallback;
            defaults to the current local hour.
    """
    if hour is None:
        hour = time.localtime().tm_hour

    lights = snapshot.get("lights") or {}
    bulbs = lights.get("bulbs") or []
    state = lights.get("state") or {}
    vac = snapshot.get("vacuum") or {}
    pur = snapshot.get("purifier") or {}
    moments = snapshot.get("moments") or {}

    brightness = next(
        (_num(b.get("current_brightness"), None) for b in bulbs
         if b.get("current_brightness") is not None),
        None,
    )
    return {
        "ok": True,
        "hour": hour,
        "lights_avail": bool(lights.get("available")),
        "bulbs_on": sum(1 for b in bulbs if str(b.get("current_power")) == "on"),
        "bulbs_total": len(bulbs),
        "brightness": brightness,
        "last_mode": state.get("last_mode"),
        "vac_avail": bool(vac.get("available")),
        "vac_status": vac.get("status"),
        "vac_battery": _num(vac.get("battery"), -1),
        "pur_avail": bool(pur.get("available")),
        "pur_on": pur.get("power") == "ON",
        "pur_mode": pur.get("mode"),
        "pur_fan": pur.get("fan_level"),
        "pm25": _num(pur.get("pm25"), -1),
        "temp": _num(pur.get("temperature"), -999),
        "hum": _num(pur.get("humidity"), -1),
        "moments": _num(moments.get("count"), 0),
    }


# ----------------------------------------------------------------------------
# CLI: send an alert to a running server, or find the panel's IP.
# ----------------------------------------------------------------------------
def _post_alert_http(text: str, level: str, host: str, port: int) -> dict:
    import urllib.request

    data = json.dumps({"text": text, "level": level}).encode("utf-8")
    req = urllib.request.Request(
        f"http://{host}:{port}/api/panel/alert",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Coukab touch-panel utilities.")
    parser.add_argument("--alert", metavar="TEXT", help="Send an alert to the panel.")
    parser.add_argument("--level", choices=_VALID_LEVELS, default="info")
    parser.add_argument("--find-ip", action="store_true", help="Print the panel's LAN IP.")
    parser.add_argument("--host", default="127.0.0.1", help="Web server host (for --alert).")
    parser.add_argument("--port", type=int, default=WEB_PORT)
    args = parser.parse_args()

    if args.find_ip:
        ip = find_panel_ip()
        print(ip or "panel not found (mDNS unresolved and none seen on SSE)")
    if args.alert:
        try:
            print(_post_alert_http(args.alert, args.level, args.host, args.port))
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"Failed to send alert: {e}")
    if not args.find_ip and not args.alert:
        parser.print_help()
