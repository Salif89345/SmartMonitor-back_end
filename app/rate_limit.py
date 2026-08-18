from collections import defaultdict, deque
from math import ceil
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request, status


MAX_WINDOW_SECONDS = 600
CLEANUP_INTERVAL_SECONDS = 60


class InMemoryRateLimiter:
    def __init__(self):
        self._events: dict[
            str,
            deque[float],
        ] = defaultdict(deque)

        self._lock = Lock()
        self._last_cleanup = monotonic()

    def check(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        now = monotonic()

        with self._lock:
            self._cleanup_if_needed(now)

            events = self._events[key]

            cutoff = (
                now
                - window_seconds
            )

            while (
                events
                and events[0] <= cutoff
            ):
                events.popleft()

            if len(events) >= limit:
                retry_after = max(
                    1,
                    ceil(
                        events[0]
                        + window_seconds
                        - now
                    ),
                )

                return (
                    False,
                    retry_after,
                )

            events.append(now)

            return (
                True,
                0,
            )

    def _cleanup_if_needed(
        self,
        now: float,
    ) -> None:
        if (
            now
            - self._last_cleanup
            < CLEANUP_INTERVAL_SECONDS
        ):
            return

        stale_before = (
            now
            - MAX_WINDOW_SECONDS
        )

        stale_keys = [
            key
            for key, events
            in self._events.items()
            if (
                not events
                or events[-1] <= stale_before
            )
        ]

        for key in stale_keys:
            del self._events[key]

        self._last_cleanup = now


rate_limiter = InMemoryRateLimiter()


def _client_ip(
    request: Request,
) -> str:
    if request.client is None:
        return "unknown"

    return request.client.host


def _enforce(
    key: str,
    limit: int,
    window_seconds: int,
    detail: str,
) -> None:
    allowed, retry_after = (
        rate_limiter.check(
            key=key,
            limit=limit,
            window_seconds=window_seconds,
        )
    )

    if allowed:
        return

    raise HTTPException(
        status_code=(
            status.HTTP_429_TOO_MANY_REQUESTS
        ),
        detail=detail,
        headers={
            "Retry-After": str(
                retry_after
            ),
        },
    )


def enforce_login_rate_limit(
    request: Request,
) -> None:
    _enforce(
        key=(
            "login:"
            + _client_ip(request)
        ),
        limit=10,
        window_seconds=60,
        detail=(
            "Too many login attempts"
        ),
    )


def enforce_register_rate_limit(
    request: Request,
) -> None:
    _enforce(
        key=(
            "register:"
            + _client_ip(request)
        ),
        limit=5,
        window_seconds=600,
        detail=(
            "Too many registration attempts"
        ),
    )


def enforce_command_rate_limit(
    user_id: int,
) -> None:
    _enforce(
        key=(
            "commands:user:"
            + str(user_id)
        ),
        limit=30,
        window_seconds=60,
        detail=(
            "Too many device commands"
        ),
    )