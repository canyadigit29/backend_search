import time


class Stopwatch:
    """Simple stopwatch for request-scoped timing.

    Methods:
    - elapsed(): seconds since start
    - remaining(cap): max(1.0, cap - elapsed())
    - lap(): seconds since last lap/reset_lap()
    - reset_lap(): set lap anchor to now
    """

    def __init__(self) -> None:
        now = time.monotonic()
        self._start = now
        self._lap_anchor = now

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def remaining(self, cap_seconds: float) -> float:
        rem = cap_seconds - self.elapsed()
        return 1.0 if rem < 1.0 else rem

    def reset_lap(self) -> None:
        self._lap_anchor = time.monotonic()

    def lap(self) -> float:
        now = time.monotonic()
        delta = now - self._lap_anchor
        self._lap_anchor = now
        return delta
