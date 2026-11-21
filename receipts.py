"""Short-lived HTTP mutation receipts. Reserve a key before touching a device."""

from dataclasses import dataclass
import re
import threading
import time
import uuid


class ReceiptError(ValueError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


@dataclass
class Receipt:
    id: str
    fingerprint: bytes
    response: tuple | None = None
    completed_at: float | None = None


def accepted(job_id):
    return 202, {"ok": True, "accepted": True, "job_id": job_id, "status_url": f"/api/actions/{job_id}"}


class Receipts:
    def __init__(self, capacity=1024, lifetime=600):
        self.lock = threading.Lock()
        self.entries = {}
        self.capacity = capacity
        self.lifetime = lifetime

    def reserve(self, key, fingerprint):
        if not re.fullmatch(r"[a-zA-Z0-9._-]{1,128}", key):
            raise ReceiptError("Invalid Idempotency-Key.", 400)
        with self.lock:
            cutoff = time.monotonic() - self.lifetime
            for old, receipt in list(self.entries.items()):
                if receipt.completed_at is not None and receipt.completed_at < cutoff:
                    del self.entries[old]
            existing = self.entries.get(key)
            if existing:
                if existing.fingerprint != fingerprint:
                    raise ReceiptError("Idempotency key already used for another request.", 409)
                return existing, existing.response or accepted(existing.id)
            if len(self.entries) >= self.capacity:
                raise ReceiptError("Retry receipt capacity reached.", 429)
            receipt = Receipt(uuid.uuid4().hex, fingerprint)
            self.entries[key] = receipt
            return receipt, None

    def finish(self, receipt, response):
        with self.lock:
            receipt.response = response
            receipt.completed_at = time.monotonic()

    def follow(self, receipt, controller, job_id):
        def complete(job):
            if job["status"] == "done":
                response = 200, job.get("result", {"ok": True})
            else:
                response = 502, {"ok": False, "error": job.get("error", "Device action failed.")}
            self.finish(receipt, response)
        controller.on_job_finished(job_id, complete)
        return accepted(receipt.id)

    def state(self, job_id):
        with self.lock:
            entry = next((r for r in self.entries.values() if r.id == job_id), None)
            if entry is None:
                return None
            response = entry.response
        if response is None:
            return {"id": job_id, "status": "running"}
        code, payload = response
        if code >= 400 or payload.get("ok") is False:
            return {"id": job_id, "status": "failed", "error": payload.get("error", "Action failed."),
                    "result": payload}
        return {"id": job_id, "status": "done", "result": payload}
