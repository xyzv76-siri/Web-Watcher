from datetime import datetime, timedelta
from web_watcher.models import Target, TargetStatus
from web_watcher.fetch_policy import (
    FetchPolicy,
    parse_retry_after,
    parse_interval_seconds,
)


def test_parse_interval_seconds():
    assert parse_interval_seconds("30s") == 30.0
    assert parse_interval_seconds("15m") == 900.0
    assert parse_interval_seconds("2h") == 7200.0
    assert parse_interval_seconds("1d") == 86400.0
    assert parse_interval_seconds("invalid") == 900.0


def test_parse_retry_after():
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after(None) is None

    # HTTP-Date 格式
    now = datetime(2026, 8, 18, 10, 0, 0)
    future_date = "Tue, 18 Aug 2026 10:05:00 GMT"
    diff = parse_retry_after(future_date, now=now)
    assert diff is not None
    assert abs(diff - 300.0) < 2.0


def test_prepare_request_injects_caching_headers():
    policy = FetchPolicy()
    t = Target(
        id="t1",
        url="https://example.com",
        etag='"etag-12345"',
        last_modified="Wed, 21 Oct 2025 07:28:00 GMT",
    )
    decision = policy.prepare_request(t)

    assert decision.allowed is True
    assert decision.headers["If-None-Match"] == '"etag-12345"'
    assert decision.headers["If-Modified-Since"] == "Wed, 21 Oct 2025 07:28:00 GMT"


def test_prepare_request_blocks_if_not_yet_allowed():
    policy = FetchPolicy()
    now = datetime.utcnow()
    t = Target(
        id="t2",
        url="https://example.com",
        status=TargetStatus.BACKOFF,
        next_allowed_at=now + timedelta(seconds=60),
    )
    decision = policy.prepare_request(t, now=now)

    assert decision.allowed is False
    assert decision.delay_seconds > 0


def test_evaluate_response_304_short_circuit():
    policy = FetchPolicy()
    t = Target(id="t3", url="https://example.com", interval="15m", etag='"etag-1"')
    now = datetime.utcnow()
    ev = policy.evaluate_response(t, 304, now=now)

    assert ev.new_status == TargetStatus.NORMAL
    assert ev.should_emit_signal is False
    assert ev.consecutive_failures == 0
    assert ev.updated_etag == '"etag-1"'


def test_evaluate_response_200_success():
    policy = FetchPolicy()
    t = Target(id="t4", url="https://example.com", interval="10m")
    now = datetime.utcnow()
    headers = {"etag": '"new-etag"', "last-modified": "Wed, 18 Aug 2026 12:00:00 GMT"}
    ev = policy.evaluate_response(t, 200, headers=headers, now=now)

    assert ev.new_status == TargetStatus.NORMAL
    assert ev.should_emit_signal is True
    assert ev.consecutive_failures == 0
    assert ev.updated_etag == '"new-etag"'
    assert (ev.next_allowed_at - now).total_seconds() == 600.0


def test_evaluate_response_403_forbidden_cooldown():
    policy = FetchPolicy()
    t = Target(id="t5", url="https://example.com", consecutive_failures=0)
    now = datetime.utcnow()
    ev = policy.evaluate_response(t, 403, now=now)

    assert ev.new_status == TargetStatus.COOLDOWN
    assert ev.consecutive_failures == 1
    assert ev.next_allowed_at is not None
    assert (ev.next_allowed_at - now).total_seconds() == 1800.0  # 30min


def test_evaluate_response_429_backoff_with_retry_after():
    policy = FetchPolicy()
    t = Target(id="t6", url="https://example.com", consecutive_failures=0)
    now = datetime(2026, 8, 18, 10, 0, 0)
    retry_date = "Tue, 18 Aug 2026 10:05:00 GMT"
    ev = policy.evaluate_response(t, 429, headers={"retry-after": retry_date}, now=now)

    assert ev.new_status == TargetStatus.BACKOFF
    assert ev.consecutive_failures == 1
    assert ev.next_allowed_at is not None
    # diff should be 300s
    diff = (ev.next_allowed_at - now).total_seconds()
    assert 298.0 <= diff <= 302.0


def test_evaluate_response_5xx_escalates_to_cooldown_after_threshold():
    policy = FetchPolicy(max_consecutive_failures=3)
    t = Target(id="t7", url="https://example.com", consecutive_failures=2)
    now = datetime.utcnow()
    ev = policy.evaluate_response(t, 502, now=now)

    assert ev.new_status == TargetStatus.COOLDOWN
    assert ev.consecutive_failures == 3
    assert ev.next_allowed_at is not None
    # ladder index 0: 30min
    diff = (ev.next_allowed_at - now).total_seconds()
    assert 1798.0 <= diff <= 1802.0


def test_backoff_contains_jitter():
    policy = FetchPolicy(base_backoff_sec=60.0, max_backoff_sec=600.0, jitter_ratio=0.2)
    t = Target(id="t8", url="https://example.com", consecutive_failures=1)
    now = datetime.utcnow()
    delays = []
    for _ in range(20):
        ev = policy.evaluate_response(t, 500, now=now)
        delays.append((ev.next_allowed_at - now).total_seconds())
    # raw backoff for failures=2: 60 * 2^(1) = 120s, with +/-20% jitter => [96, 144]
    assert all(96.0 <= d <= 144.0 for d in delays)
    # at least some variety
    assert len(set(delays)) > 1
