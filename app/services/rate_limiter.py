"""
app/services/rate_limiter.py
Stage 6: Adaptive Global Rate Limiter & Async Work Queue for UniPulse AI.

Centralized token-bucket rate limiting for Groq API quotas, shared across
every async worker: RPM/TPM capacity is enforced globally before any LLM
call, HTTP 429 ``Retry-After`` headers pause dispatch (with exponential
backoff + jitter when the header is absent), and the API-key pool rotates on
credential exhaustion. ``AsyncPipelineQueue`` provides backpressure-managed
handoff of ``ProductRecord`` objects between pipeline stages.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time

from app.schemas.product import ProductRecord

logger = logging.getLogger(__name__)

#: Groq's error bodies embed the exact wait window, e.g. "Please try again in
#: 3.455s" or "10m34s". Parsing it lets retries honor the provider's own
#: TPM/TPD backoff window.
_RETRY_AFTER_MESSAGE_RE = re.compile(
    r"(?:try again in|retry in|in)\s+(?:(?P<min>\d+)\s*m)?\s*(?P<sec>\d+(?:\.\d+)?)\s*s?",
    re.IGNORECASE,
)

#: Safety buffer applied on top of a provider-suggested retry delay so a retry
#: never fires *just* before the window reopens (clock skew, float rounding).
_RETRY_AFTER_SAFETY_FACTOR = 1.25
_RETRY_AFTER_SAFETY_PADDING = 0.5
#: Hard ceiling on a single backoff sleep so a pathological provider response
#: can't stall a worker for minutes.
_MAX_BACKOFF_SECONDS = 30.0

#: Safe sliding-window TPM dispatch ceiling. Groq's free-tier per-key limit is
#: 12,000 TPM; capping the window at 9,500 (~80%) keeps concurrent workers
#: safely under the provider ceiling even when token estimates run hot.
_DEFAULT_MAX_TPM = 9500


def _error_message_text(exc: Exception) -> str:
    """Best-effort textual message from an SDK exception.

    Groq/OpenAI SDK errors render ``str(exc)`` as a short label and carry the
    real detail in ``exc.body["error"]["message"]``, so the body is checked
    before falling back to the rendered text.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        for part in ("message", "msg"):
            value = error.get(part)
            if value:
                return str(value)
    message = getattr(exc, "message", None)
    if message:
        return str(message)
    return str(exc)


def is_tpd_exhausted(exc: Exception) -> bool:
    """Return True if the exception indicates Tokens Per Day (TPD) exhaustion."""
    msg = _error_message_text(exc).lower()
    return "tokens per day" in msg or "tpd" in msg


def parse_retry_after_from_exception(exc: Exception) -> float | None:
    """Extract a provider-suggested retry delay from an API exception.

    Checks the ``Retry-After`` response header first, then Groq's message-body
    hint ("Please try again in 3.455s" or "10m34s"). Returns ``None`` when
    neither is present so callers can fall back to exponential backoff.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is not None:
        try:
            if isinstance(headers, dict):
                for key, value in headers.items():
                    if key.lower() == "retry-after":
                        return float(value)
            else:
                value = headers.get("retry-after")
                if value is not None:
                    return float(value)
        except (TypeError, ValueError, AttributeError):
            pass
    match = _RETRY_AFTER_MESSAGE_RE.search(_error_message_text(exc))
    if match:
        try:
            minutes = float(match.group("min") or 0)
            seconds = float(match.group("sec") or 0)
            return minutes * 60 + seconds
        except (TypeError, ValueError):
            pass
    return None


def buffered_backoff(seconds: float) -> float:
    """Scale a provider-suggested retry delay with a safety buffer.

    ``seconds * 1.25 + 0.5s`` so a retry lands comfortably *after* the window
    reopens, capped at ``_MAX_BACKOFF_SECONDS``.
    """
    if seconds <= 0:
        return 0.0
    return min(
        seconds * _RETRY_AFTER_SAFETY_FACTOR + _RETRY_AFTER_SAFETY_PADDING,
        _MAX_BACKOFF_SECONDS,
    )


#: Provider safety ceilings
GROQ_DEFAULT_RPM = 25
GROQ_DEFAULT_TPM = 5000


class AdaptiveRateLimiter:
    """Centralized, token-bucket global rate limiter for Groq API quotas.

    Tracks RPM and TPM globally across async workers, handles 429
    Retry-After headers, tracks TPD key depletion, and manages API key failover.
    """

    def __init__(
        self,
        max_rpm: int = GROQ_DEFAULT_RPM,
        max_tpm: int = GROQ_DEFAULT_TPM,
        max_tpd: int = 500000,
        retry_base_backoff: float = 2.0,
    ) -> None:
        self.max_rpm = max_rpm
        self.max_tpm = max_tpm
        self.max_tpd = max_tpd
        self.retry_base_backoff = retry_base_backoff

        self._request_timestamps: list[float] = []
        self._token_timestamps: list[tuple[float, int]] = []
        self._lock = asyncio.Lock()
        self._paused_until: float = 0.0
        self._consecutive_429s: int = 0
        self._depleted_keys: dict[str, float] = {}

    def mark_key_depleted(self, key: str, duration_seconds: float = 86400.0) -> None:
        """Mark an API key as depleted due to TPD exhaustion for duration_seconds (default 24h)."""
        if not key:
            return
        self._depleted_keys[key.strip()] = time.time() + duration_seconds
        logger.warning(
            "API Key %r marked as DEPLETED for %.0f seconds due to TPD exhaustion",
            key[:8] + "...",
            duration_seconds,
        )

    def is_key_depleted(self, key: str) -> bool:
        """Check if an API key is currently marked as depleted."""
        if not key:
            return False
        clean_key = key.strip()
        expiry = self._depleted_keys.get(clean_key)
        if expiry is None:
            return False
        if time.time() >= expiry:
            del self._depleted_keys[clean_key]
            return False
        return True

    def get_active_keys(self, key_pool: list[str]) -> list[str]:
        """Return non-depleted keys from key_pool. Falls back to full pool if all are depleted."""
        clean_pool = [k.strip() for k in key_pool if k and k.strip()]
        active = [k for k in clean_pool if not self.is_key_depleted(k)]
        return active if active else clean_pool

    def get_cooldown_wait(self, key_pool: list[str]) -> float:
        """If all keys in key_pool are depleted/on cooldown, return seconds to wait until earliest reopens."""
        clean_pool = [k.strip() for k in key_pool if k and k.strip()]
        if not clean_pool:
            return 0.0

        now = time.time()
        active = [k for k in clean_pool if not self.is_key_depleted(k)]
        if active:
            return 0.0

        expirations = [self._depleted_keys.get(k, 0.0) for k in clean_pool]
        valid_expirations = [exp for exp in expirations if exp > now]
        if not valid_expirations:
            return 0.0

        earliest = min(valid_expirations)
        return max(0.1, earliest - now)

    def _clean_window(self, now: float) -> None:
        """Remove entries older than 60 seconds from the tracking windows."""
        window_start = now - 60.0
        self._request_timestamps = [
            ts for ts in self._request_timestamps if ts > window_start
        ]
        self._token_timestamps = [
            entry for entry in self._token_timestamps if entry[0] > window_start
        ]

    # Note: current_rpm/current_tpm mutate the tracking lists without the
    # lock — safe because they only run on the event-loop thread and
    # ``acquire``'s critical section contains no awaits, so the two can never
    # interleave. Keep them sync (tests call them directly).
    def current_rpm(self) -> int:
        now = time.time()
        self._clean_window(now)
        return len(self._request_timestamps)

    def current_tpm(self) -> int:
        now = time.time()
        self._clean_window(now)
        return sum(tokens for _, tokens in self._token_timestamps)

    async def acquire(self, estimated_tokens: int = 200) -> None:
        """Block asynchronously until capacity for 1 request and
        *estimated_tokens* is available under the RPM and TPM limits.
        """
        if estimated_tokens > self.max_tpm:
            # A single request exceeding the entire token budget can never
            # pass the TPM check — waiting would spin forever. Log and let
            # the provider's own limits (and the 429 handler) arbitrate.
            logger.warning(
                "estimated_tokens=%d exceeds max_tpm=%d; skipping rate-limit wait",
                estimated_tokens,
                self.max_tpm,
            )
            return

        while True:
            async with self._lock:
                now = time.time()

                # Check if paused due to 429
                if now < self._paused_until:
                    sleep_needed = self._paused_until - now
                else:
                    self._clean_window(now)
                    rpm_ok = len(self._request_timestamps) < self.max_rpm
                    tpm_ok = (
                        sum(tok for _, tok in self._token_timestamps)
                        + estimated_tokens
                    ) <= self.max_tpm

                    if rpm_ok and tpm_ok:
                        self._request_timestamps.append(now)
                        self._token_timestamps.append((now, estimated_tokens))
                        return

                    sleep_needed = 1.0  # Check back in 1s for sliding window capacity

            await asyncio.sleep(sleep_needed)

    def handle_429(self, retry_after_seconds: float | None = None) -> float:
        """Pause dispatch when a 429 occurs.

        Prefers the ``Retry-After`` value (already parsed from the header or
        the "Please try again in X.XXXs" message by the caller); without it,
        applies exponential backoff with jitter. The internal pause window is
        buffered (see :func:`buffered_backoff`) so *all* concurrent workers
        wait out the provider window; the raw value is still returned so
        callers can apply their own sleep policy.
        """
        now = time.time()
        self._consecutive_429s += 1

        if retry_after_seconds is not None and retry_after_seconds > 0:
            backoff = retry_after_seconds
        else:
            # Exponential backoff: 2.0 * (2 ** (n-1)) + jitter
            exponential = self.retry_base_backoff * (
                2 ** (self._consecutive_429s - 1)
            )
            jitter = random.uniform(0.1, 0.5)
            backoff = exponential + jitter

        # The internal pause window is buffered so *all* concurrent workers
        # wait out the provider window; the raw value is returned because
        # callers are expected to apply the same ``buffered_backoff`` before
        # their own ``asyncio.sleep``.
        self._paused_until = max(
            self._paused_until, now + buffered_backoff(backoff)
        )
        return backoff

    def reset_429_backoff(self) -> None:
        """Reset the 429 penalty counter on a successful request."""
        self._consecutive_429s = 0

    @staticmethod
    def rotate_api_key(current_key: str, key_pool: list[str]) -> str:
        """Return the next API key from *key_pool* for credential failover.

        Wraps around to the start; falls back to *current_key* when the pool
        is empty and to the pool's first key when *current_key* is absent.
        """
        if not key_pool:
            return current_key
        clean_pool = [k.strip() for k in key_pool if k and k.strip()]
        if not clean_pool:
            return current_key
        try:
            idx = clean_pool.index(current_key.strip())
            next_idx = (idx + 1) % len(clean_pool)
            return clean_pool[next_idx]
        except ValueError:
            return clean_pool[0]

    def rotate_active_key(self, current_key: str, key_pool: list[str]) -> str:
        """Return the next non-depleted API key from *key_pool* for credential failover."""
        active_pool = self.get_active_keys(key_pool)
        return self.rotate_api_key(current_key, active_pool)


class AsyncPipelineQueue:
    """Backpressure-managed async work queue for ProductRecords progressing
    through the pipeline.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self.queue: asyncio.Queue[ProductRecord] = asyncio.Queue(maxsize=maxsize)

    async def enqueue_record(self, record: ProductRecord) -> None:
        """Enqueue a record, blocking while the queue is full (backpressure)."""
        await self.queue.put(record)

    async def dequeue_record(self) -> ProductRecord:
        """Dequeue a record, blocking while the queue is empty."""
        return await self.queue.get()

    def task_done(self) -> None:
        self.queue.task_done()

    def size(self) -> int:
        return self.queue.qsize()
