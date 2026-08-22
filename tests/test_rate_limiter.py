"""
tests/test_rate_limiter.py
Stage 6 Verification: Tests for adaptive rate limiter, 429 backoff, key
rotation, and async queue.
"""

import asyncio

import pytest

from app.schemas.product import ProductIdentity, ProductRecord, RawInputData
from app.services.rate_limiter import (
    AdaptiveRateLimiter,
    AsyncPipelineQueue,
    _MAX_BACKOFF_SECONDS,
    buffered_backoff,
    parse_retry_after_from_exception,
)


@pytest.mark.asyncio
async def test_acquire_respects_rpm_limit() -> None:
    limiter = AdaptiveRateLimiter(max_rpm=2, max_tpm=1000)

    # First 2 requests should acquire immediately
    await limiter.acquire(estimated_tokens=100)
    await limiter.acquire(estimated_tokens=100)
    assert limiter.current_rpm() == 2

    # Third request should block until the sliding window clears
    acquire_task = asyncio.create_task(limiter.acquire(estimated_tokens=100))
    await asyncio.sleep(0.1)
    assert not acquire_task.done()
    acquire_task.cancel()
    await asyncio.gather(acquire_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_acquire_respects_tpm_limit() -> None:
    limiter = AdaptiveRateLimiter(max_rpm=100, max_tpm=300)

    await limiter.acquire(estimated_tokens=100)
    await limiter.acquire(estimated_tokens=100)
    await limiter.acquire(estimated_tokens=100)
    assert limiter.current_tpm() == 300

    # A 4th request would push tokens over max_tpm -> blocks
    acquire_task = asyncio.create_task(limiter.acquire(estimated_tokens=100))
    await asyncio.sleep(0.1)
    assert not acquire_task.done()
    acquire_task.cancel()
    await asyncio.gather(acquire_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_handle_429_pauses_dispatch() -> None:
    limiter = AdaptiveRateLimiter(max_rpm=10, max_tpm=5000)
    backoff_time = limiter.handle_429(retry_after_seconds=2.0)

    assert backoff_time == 2.0

    # Immediate acquire should block because limiter is paused
    acquire_task = asyncio.create_task(limiter.acquire(100))
    await asyncio.sleep(0.1)
    assert not acquire_task.done()
    acquire_task.cancel()
    await asyncio.gather(acquire_task, return_exceptions=True)


def test_handle_429_exponential_backoff_without_header() -> None:
    limiter = AdaptiveRateLimiter()
    first = limiter.handle_429()
    second = limiter.handle_429()

    # 2.0 * 2**0 + jitter(0.1-0.5), then 2.0 * 2**1 + jitter
    assert 2.0 < first < 3.0
    assert second > first


@pytest.mark.asyncio
async def test_acquire_oversized_estimate_does_not_hang() -> None:
    """A degenerate estimate exceeding max_tpm fails fast instead of looping."""
    limiter = AdaptiveRateLimiter(max_rpm=2, max_tpm=100)
    # Would otherwise spin forever; must return promptly without reserving.
    await limiter.acquire(estimated_tokens=500)
    assert limiter.current_rpm() == 0


# ---------------------------------------------------------------------------
# Retry-After parsing (header + Groq message body) and safety buffering
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for an httpx-style exception response."""

    def __init__(self, headers=None):
        self.headers = headers or {}


class _SdkStyleError(RuntimeError):
    """Simulates a Groq SDK error: short rendered text, real detail in body."""

    def __init__(self, message, body=None, status_code=None, headers=None):
        super().__init__(message)
        self.body = body
        self.status_code = status_code
        self.response = _FakeResponse(headers)


def test_parse_retry_after_from_message_body() -> None:
    """The Groq TPM message "Please try again in 3.455s" is parsed as the
    backoff window even without a Retry-After header."""
    err = RuntimeError(
        "Rate limit reached for model openai/gpt-oss-120b on tokens per "
        "minute (TPM): Limit 12000, Used 11898, Requested 886. "
        "Please try again in 3.455s."
    )
    assert parse_retry_after_from_exception(err) == 3.455


def test_parse_retry_after_from_sdk_error_body() -> None:
    """Groq SDK errors render str(exc) as a short label; the retry hint lives
    in the JSON body and must be extracted from there."""
    err = _SdkStyleError(
        "429",
        body={
            "error": {
                "message": (
                    "Rate limit reached for model openai/gpt-oss-120b on "
                    "tokens per minute (TPM): Limit 12000, Used 11898, "
                    "Requested 886. Please try again in 3.455s."
                )
            }
        },
        status_code=429,
    )
    assert parse_retry_after_from_exception(err) == 3.455


def test_parse_retry_after_prefers_header_over_message() -> None:
    """When both a header and a message hint exist, the header wins."""
    err = _SdkStyleError(
        "429",
        body={"error": {"message": "Please try again in 2s."}},
        status_code=429,
        headers={"retry-after": "5"},
    )
    assert parse_retry_after_from_exception(err) == 5.0


def test_parse_retry_after_returns_none_without_signal() -> None:
    assert parse_retry_after_from_exception(RuntimeError("generic failure")) is None


def test_buffered_backoff_adds_safety_margin() -> None:
    # 2.0s raw + 25% safety + ~0.3s jitter -> in [2.8, 3.2]
    backoff = buffered_backoff(2.0)
    assert 2.7 <= backoff <= 3.3


def test_buffered_backoff_caps_at_max() -> None:
    # 1000s raw capped at 120s + jitter -> in [120, 121]
    assert buffered_backoff(1000.0) <= 121.0


def test_rotate_api_key_failover() -> None:
    pool = ["key_1", "key_2", "key_3"]

    next_key = AdaptiveRateLimiter.rotate_api_key("key_1", pool)
    assert next_key == "key_2"

    last_key = AdaptiveRateLimiter.rotate_api_key("key_3", pool)
    assert last_key == "key_1"


def test_rotate_api_key_fallbacks() -> None:
    pool = ["key_1", "key_2"]
    # Current key not in pool -> first pool key
    assert AdaptiveRateLimiter.rotate_api_key("unknown", pool) == "key_1"
    # Empty / whitespace-only pool -> keep current key
    assert AdaptiveRateLimiter.rotate_api_key("key_1", []) == "key_1"
    assert AdaptiveRateLimiter.rotate_api_key("key_1", ["  ", ""]) == "key_1"


@pytest.mark.asyncio
async def test_async_pipeline_queue_operations() -> None:
    queue = AsyncPipelineQueue(maxsize=5)
    identity = ProductIdentity(
        row_id=1,
        mfg_part_number="775L",
        manufacturer="3M",
        raw_description="3M Disc",
    )
    record = ProductRecord(
        identity=identity, raw_data=RawInputData(original_row_index=0)
    )

    await queue.enqueue_record(record)
    assert queue.size() == 1

    dequeued = await queue.dequeue_record()
    assert dequeued.identity.row_id == 1
    assert queue.size() == 0


def test_is_tpd_exhausted_detection() -> None:
    from app.services.rate_limiter import is_tpd_exhausted

    err1 = RuntimeError(
        "Rate limit reached for model openai/gpt-oss-120b on tokens per day (TPD): Limit 100000, Used 99954. Please try again in 10m34s."
    )
    assert is_tpd_exhausted(err1) is True

    err2 = RuntimeError(
        "Rate limit reached for model openai/gpt-oss-120b on tokens per minute (TPM): Limit 12000, Used 11898. Please try again in 3s."
    )
    assert is_tpd_exhausted(err2) is False


def test_parse_retry_after_minutes_and_seconds() -> None:
    from app.services.rate_limiter import parse_retry_after_from_exception

    err1 = RuntimeError("Please try again in 10m34.176s.")
    assert parse_retry_after_from_exception(err1) == pytest.approx(634.176)

    err2 = RuntimeError("Please try again in 3m6.624s.")
    assert parse_retry_after_from_exception(err2) == pytest.approx(186.624)


def test_buffered_backoff_max_cap() -> None:
    from app.services.rate_limiter import buffered_backoff

    # Large retry values like 10 minutes (634s) must be capped at 30.0s
    assert buffered_backoff(634.176) == 30.0
    assert buffered_backoff(186.624) == 30.0
