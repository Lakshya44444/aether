"""Where per-session state lives.

`session_tracker` and the cost detector both keep a map keyed by `session_id`. As
module-level dicts they made the gateway single-process by construction: a second
worker gets its own copy, so turn 2 of a conversation can land on a process that never
saw turn 1, and `max_session_exposure`, trajectory and retry counting stop firing --
with no error, which is the bad part.

This is the smallest thing that fixes that: a keyed JSON store with a TTL. Two
implementations, chosen by whether `AETHER_REDIS_URL` is set.

- `MemoryStore`  — a dict. Identical behaviour to before, still the default, and the
                   right choice for a single-process appliance.
- `RedisStore`   — the same interface over Redis, so any number of workers share one
                   view of a session.

The TTL replaces the hand-rolled `_evict_stale` sweeps both callers used to run: an
expiring key is what "idle sessions are dropped" actually means, and Redis does it
without a scan.

Deliberately not a general cache. Four methods, values are JSON dicts, and no
transactions -- see `read_modify_write` for why that is defensible here.
"""
import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, Optional


class StateStore:
    """Keyed JSON documents with a TTL."""

    async def get(self, key: str) -> Optional[dict]:
        raise NotImplementedError

    async def put(self, key: str, value: dict, ttl_s: int) -> None:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None

    async def read_modify_write(
        self, key: str, ttl_s: int, mutate: Callable[[Optional[dict]], dict]
    ) -> dict:
        """Applies `mutate` to the stored value and writes the result back.

        ponytail: last-writer-wins, not a transaction. Two concurrent turns of the SAME
        session can interleave and lose one turn's contribution to exposure or cost.
        That is a governance heuristic drifting slightly low for one turn, not a
        correctness failure -- and concurrent turns within a single conversation are
        rare, because a session is a sequence by definition. Add WATCH/MULTI here if
        that assumption stops holding.
        """
        current = await self.get(key)
        updated = mutate(current)
        await self.put(key, updated, ttl_s)
        return updated


class MemoryStore(StateStore):
    """Process-local. The default, and correct for a single-worker deployment."""

    def __init__(self) -> None:
        self._data: Dict[str, tuple] = {}

    def _sweep(self, now: float) -> None:
        for key in [k for k, (_, expires) in self._data.items() if expires <= now]:
            del self._data[key]

    async def get(self, key: str) -> Optional[dict]:
        now = time.time()
        self._sweep(now)
        entry = self._data.get(key)
        return json.loads(entry[0]) if entry else None

    async def put(self, key: str, value: dict, ttl_s: int) -> None:
        now = time.time()
        self._sweep(now)
        self._data[key] = (json.dumps(value), now + ttl_s)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        """Test helper. Not on the interface; nothing in the gateway calls it."""
        self._data.clear()

    def __len__(self) -> int:
        self._sweep(time.time())
        return len(self._data)


class RedisStore(StateStore):
    """Shared across processes, so the gateway can run more than one worker."""

    def __init__(self, url: str, prefix: str = "aether") -> None:
        import redis.asyncio as redis  # imported here so redis is an optional install

        self._redis = redis.from_url(url, decode_responses=True)
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    async def get(self, key: str) -> Optional[dict]:
        raw = await self._redis.get(self._key(key))
        return json.loads(raw) if raw else None

    async def put(self, key: str, value: dict, ttl_s: int) -> None:
        await self._redis.set(self._key(key), json.dumps(value), ex=max(1, ttl_s))

    async def delete(self, key: str) -> None:
        await self._redis.delete(self._key(key))

    async def close(self) -> None:
        await self._redis.aclose()

    async def ping(self) -> bool:
        return bool(await self._redis.ping())


def open_state_store(redis_url: str = "") -> StateStore:
    """Redis when a URL is configured, an in-process dict otherwise."""
    return RedisStore(redis_url) if redis_url else MemoryStore()
