"""Shared MIoT plumbing for Xiaomi devices (vacuum, air purifier, ...).

Each device module keeps its enums, PROP/ACTION maps, and high-level
functions; the connection handling and property/action access live here.

The device handle is cached after the first successful construction so we
do not pay a network handshake on every command. Call ``invalidate()`` after
a failed command to force a fresh connection on the next call.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# python-miio limit for properties per get_properties request.
_MAX_PROPS_PER_REQUEST = 15


class MiotHelper:
    """Connection cache plus property/action access for one MIoT device."""

    def __init__(
        self,
        *,
        label: str,
        ip: str,
        token: str,
        model: str,
        prop_map: dict[str, tuple[int, int]],
        action_map: dict[str, tuple[int, int]],
        error_cls: type[Exception],
        env_vars: dict[str, str],
        token_var: str,
    ) -> None:
        self.label = label
        self.ip = ip
        self.token = token
        self.model = model
        self.prop_map = prop_map
        self.action_map = action_map
        self.error_cls = error_cls
        self.env_vars = env_vars
        self.token_var = token_var
        self._device: Any = None
        self._lock = threading.Lock()
        # python-miio transports are request/response streams and are not safe
        # for overlapping calls on one cached connection.
        self._io_lock = threading.RLock()

    # -- config / connection ------------------------------------------------

    def validate_config(self) -> None:
        """Validate required connection env vars before device calls."""
        missing = [k for k, v in self.env_vars.items() if not v]
        if missing:
            raise self.error_cls(
                f"Missing environment variables: {', '.join(missing)}"
            )
        if len(self.token.strip()) != 32:
            logger.warning("%s should be 32 hex characters.", self.token_var)
        logger.info(
            "%s config OK — IP: %s, model: %s", self.label, self.ip, self.model
        )

    def get_device(self) -> Any:
        """Returns a cached MiotDevice, creating it on first use.

        No ``info()`` probe is made — the first real command surfaces any
        connectivity problem one round-trip cheaper.
        """
        with self._lock:
            if self._device is not None:
                return self._device
            from miio.miot_device import MiotDevice

            self.validate_config()
            try:
                device = MiotDevice(ip=self.ip, token=self.token, model=self.model)
            except Exception as e:
                raise self.error_cls(
                    f"Failed to connect to {self.label.lower()}: {e}"
                ) from e
            self._device = device
            logger.info("Connected to %s at %s.", self.label.lower(), self.ip)
            return device

    def invalidate(self) -> None:
        """Drop the cached device so the next call reconnects."""
        with self._lock:
            self._device = None

    def ensure(self, device: Any) -> Any:
        """Returns the device, creating one if None."""
        return device if device is not None else self.get_device()

    # -- property / action access -------------------------------------------

    def get_prop(self, device: Any, name: str) -> Any:
        with self._io_lock:
            siid, piid = self.prop_map[name]
            result = device.get_property_by(siid, piid)
            return result[0].get("value") if isinstance(result, list) else result

    def set_prop(self, device: Any, name: str, value: Any) -> Any:
        with self._io_lock:
            siid, piid = self.prop_map[name]
            return device.set_property_by(siid, piid, value)

    def call_action(
        self,
        device: Any,
        action_name: str,
        params: list[dict[str, Any]] | None = None,
    ) -> Any:
        with self._io_lock:
            if action_name not in self.action_map:
                raise self.error_cls(f"Unknown action: {action_name}")
            siid, aiid = self.action_map[action_name]
            return device.call_action_by(siid, aiid, params or [])

    def get_props(self, device: Any, names: list[str]) -> dict[str, Any]:
        """Reads many properties in batched requests (~15 per round-trip).

        Unreadable properties come back as None. Falls back to one-by-one
        reads if the batched request is not supported.
        """
        with self._io_lock:
            params = [
                {"did": name, **dict(zip(("siid", "piid"), self.prop_map[name]))}
                for name in names
            ]
            result: dict[str, Any] = {}
            try:
                responses = device.get_properties(
                    params,
                    property_getter="get_properties",
                    max_properties=_MAX_PROPS_PER_REQUEST,
                )
                for item in responses:
                    if isinstance(item, dict) and item.get("did") in self.prop_map:
                        ok = item.get("code", 0) == 0
                        result[item["did"]] = item.get("value") if ok else None
            except Exception as e:
                logger.debug(
                    "Batched property read failed (%s); falling back to per-property reads.", e
                )
                for name in names:
                    try:
                        result[name] = self.get_prop(device, name)
                    except Exception as e2:
                        logger.debug("Could not read %s: %s", name, e2)
                        result[name] = None
            for name in names:
                result.setdefault(name, None)
            return result
