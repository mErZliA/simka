"""Small per-user command token bucket to suppress Telegram command floods."""
from __future__ import annotations

import time


class CommandFloodGuard:
    """Allow short bursts, then silently drop commands until tokens refill."""

    def __init__(self, capacity: float = 5.0, refill_per_second: float = 1.0):
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self._buckets: dict[int, tuple[float, float]] = {}
        self._calls = 0

    def allow(self, user_id: int | None, text: str | None, now: float | None = None) -> bool:
        if not user_id or not text or not text.lstrip().startswith("/"):
            return True

        now = time.monotonic() if now is None else float(now)
        uid = int(user_id)
        tokens, updated = self._buckets.get(uid, (self.capacity, now))
        tokens = min(self.capacity, tokens + max(0.0, now - updated) * self.refill_per_second)
        if tokens < 1.0:
            allowed = False
        else:
            tokens -= 1.0
            allowed = True
        self._buckets[uid] = (tokens, now)

        self._calls += 1
        if self._calls % 1024 == 0:
            cutoff = now - max(60.0, self.capacity / self.refill_per_second * 4)
            self._buckets = {key: state for key, state in self._buckets.items()
                             if state[1] >= cutoff}
        return allowed


command_flood_guard = CommandFloodGuard()


async def drop_flooded_commands(event) -> None:
    """Telethon handler registered before command handlers; drop bursts silently."""
    if not command_flood_guard.allow(event.sender_id, event.raw_text):
        event.stop_propagation()
