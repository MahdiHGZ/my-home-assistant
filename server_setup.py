"""Discoverability and transactional reload of locally edited device credentials."""

import ipaddress
import logging
import os
from pathlib import Path
import re
import socket
import threading

from dotenv.parser import parse_stream
from zeroconf import IPVersion, ServiceInfo, Zeroconf

import tapo_camera_utils as camera
import xiaomi_airpurifier_utils as purifier
import xiaomi_vacuum_utils as vacuum
import yeelight_bulb_utils as lights

GROUPS = {"vacuum": ("VACUUM_IP", "VACUUM_TOKEN"),
          "purifier": ("AIRPURIFIER_IP", "AIRPURIFIER_TOKEN"),
          "camera": ("TAPO_IP", "TAPO_USERNAME", "TAPO_PASSWORD")}
_reload_lock = threading.Lock()


def configuration():
    with _reload_lock:
        active = {**vacuum._HELPER.env_vars, **purifier._HELPER.env_vars,
                  **dict(zip(GROUPS["camera"], camera.credentials()))}
        missing = {device: [key for key in group if not active.get(key)] for device, group in GROUPS.items()}
    try:
        missing["lights"] = [] if lights.load_registered_bulbs() else ["bulb.json"]
    except (OSError, ValueError):
        missing["lights"] = ["bulb.json"]
    return missing


def reload_devices(path=Path(".env")):
    """Validate every replacement before installing any. Omitted groups stay active."""
    try:
        with path.open(encoding="utf8") as file:
            parsed = list(parse_stream(file))
    except OSError as exc:
        raise ValueError("Cannot read the local .env file.") from exc
    if any(entry.error for entry in parsed):
        raise ValueError("Invalid .env syntax.")
    values = {entry.key: entry.value or "" for entry in parsed if entry.key}
    updates = {}
    for name, keys in GROUPS.items():
        optional = {"TAPO_CLOUD_PASSWORD"} if name == "camera" else set()
        if not values.keys() & (set(keys) | optional):
            continue
        if not all(k in values for k in keys):
            raise ValueError(f"Provide the complete {name} group: {', '.join(keys)}.")
        fields = [values[k] for k in keys]
        if any(fields):
            if not all(fields):
                raise ValueError(f"Incomplete {name} settings.")
            try:
                ipaddress.IPv4Address(fields[0])
            except ValueError as exc:
                raise ValueError(f"{keys[0]} must be an IPv4 address.") from exc
            if name != "camera" and not re.fullmatch("[0-9A-Fa-f]{32}", fields[1]):
                raise ValueError(f"{keys[1]} requires 32 hexadecimal characters.")
        if name == "camera":
            fields.append(values.get("TAPO_CLOUD_PASSWORD", ""))
        updates[name] = fields
    with _reload_lock:
        for name, fields in updates.items():
            if name == "camera":
                camera.set_credentials(tuple(fields))
            else:
                helper = (vacuum if name == "vacuum" else purifier)._HELPER
                helper.reconfigure(fields[0], fields[1], dict(zip(GROUPS[name], fields)))
    return sorted(updates)


class Advertisement:
    def __init__(self, server, get_address):
        self.server = server
        self.get_address = get_address
        self.hostname = os.getenv("COUKAB_LOCAL_NAME", "coukab").lower()
        self.local_url = None
        self.address = None
        self.thread = threading.Thread(target=self._loop, name="http-mdns", daemon=True)

    def current_address(self):
        bound = self.server.server_address[0]
        return self.get_address() if bound in ("", "0.0.0.0") else bound

    def _loop(self):
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", self.hostname):
            return
        zc = None
        port = self.server.server_address[1]
        address = None
        try:
            while not self.server.stopped.is_set():
                try:
                    ip = self.current_address()
                    if ip != address:
                        if zc:
                            zc.close()
                            zc = None
                        self.local_url = self.address = None
                        address = None
                    if ip != address and not ipaddress.ip_address(ip).is_loopback:
                        name = self.hostname
                        info = ServiceInfo("_http._tcp.local.", f"{name}._http._tcp.local.",
                                           addresses=[socket.inet_aton(ip)], port=port,
                                           server=f"{name}.local.", properties={"path": "/"})
                        candidate = Zeroconf(interfaces=[ip], ip_version=IPVersion.V4Only)
                        try:
                            candidate.register_service(info)
                        except Exception:
                            candidate.close()
                            raise
                        zc = candidate
                        address = self.address = ip
                        self.local_url = f"http://{name}.local:{port}/"
                except Exception:
                    logging.getLogger(__name__).debug("mDNS unavailable", exc_info=True)
                self.server.stopped.wait(15)
        finally:
            if zc:
                zc.close()
