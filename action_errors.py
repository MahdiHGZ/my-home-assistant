"""Outcomes shared by the action worker and HTTP handlers."""


class ActionQueueFullError(Exception):
    """The bounded controller queue cannot accept another action."""


class ActionCancelledError(TimeoutError):
    """An action timed out while queued and was cancelled before execution."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"Action {job_id} timed out and was cancelled before execution.")


class ActionInProgressError(TimeoutError):
    """The caller deadline expired after device I/O had already started."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"Action {job_id} is still running.")
