from datetime import datetime, timedelta, timezone
import pytest
from web_watcher.models import Target, TargetStatus
from web_watcher.fetch_policy import (
    FetchPolicy,
    HostRateLimiter,
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
    now = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
    future_date = "Tue, 18 Aug 2026 10:05:00 GMT"
    diff = parse_retry_after(future_date, now=now)
    assert diff is not None
    assert abs(diff - 300.0) < 2.0


def test_parse_retry_after_malformed():
    assert parse_retry_after("not-a-date") is None
    assert parse_retry_after("") is None


def test_parse_retry_after_negative_seconds():
    # 负数秒数应视为 malformed（isdigit 返回 False）
    assert parse_retry_after("-1") is None
    assert parse_retry_after("-120") is None


def test_parse_retry_after_http_date_past():
    now = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
    past_date = "Tue, 18 Aug 2026 09:55:00 GMT"
    diff = parse_retry_after(past_date, now=now)
    assert diff is not None
    assert diff == 0.0


def test_parse_retry_after_huge_value():
    # 极大秒数应受 cap 限制（在 evaluate_response 中处理）
    huge = "999999999"
    assert parse_retry_after(huge) == 999999999.0


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
    now = datetime.now(timezone.utc)
    t = Target(
        id="t2",
        url="https://example.com",
        status=TargetStatus.BACKOFF,
        next_allowed_at=now + timedelta(seconds=60),
    )
    decision = policy.prepare_request(t, now=now)

    assert decision.allowed is False
    assert decision.delay_seconds > 0


def test_prepare_request_per_target_isolation():
    """Verify ETag/Last-Modified are per-target, not global."""
    policy = FetchPolicy()
    t1 = Target(id="t1", url="https://a.example.com", etag='"etag-a"', last_modified="A")
    t2 = Target(id="t2", url="https://b.example.com", etag='"etag-b"', last_modified="B")

    d1 = policy.prepare_request(t1)
    d2 = policy.prepare_request(t2)

    assert d1.headers["If-None-Match"] == '"etag-a"'
    assert d2.headers["If-None-Match"] == '"etag-b"'
    assert d1.headers["If-Modified-Since"] == "A"
    assert d2.headers["If-Modified-Since"] == "B"


def test_evaluate_response_304_short_circuit():
    policy = FetchPolicy()
    t = Target(id="t3", url="https://example.com", interval="15m", etag='"etag-1"')
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 304, now=now)

    assert ev.new_status == TargetStatus.NORMAL
    assert ev.should_emit_signal is False
    assert ev.consecutive_failures == 0
    assert ev.updated_etag == '"etag-1"'


def test_evaluate_response_200_success():
    policy = FetchPolicy()
    t = Target(id="t4", url="https://example.com", interval="10m")
    now = datetime.now(timezone.utc)
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
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 403, now=now)

    assert ev.new_status == TargetStatus.COOLDOWN
    assert ev.consecutive_failures == 1
    assert ev.next_allowed_at is not None
    assert (ev.next_allowed_at - now).total_seconds() == 1800.0  # 30min


def test_evaluate_response_404_not_found_backoff():
    policy = FetchPolicy()
    t = Target(id="t6", url="https://example.com", consecutive_failures=0)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 404, now=now)

    assert ev.new_status == TargetStatus.BACKOFF
    assert ev.consecutive_failures == 1
    assert ev.next_allowed_at is not None
    # raw backoff = 30s, jitter bounded [-10%, +10%] => [27, 33]
    delay = (ev.next_allowed_at - now).total_seconds()
    assert 27.0 <= delay <= 33.0


def test_evaluate_response_404_escalates_after_threshold():
    policy = FetchPolicy(max_consecutive_failures=3)
    t = Target(id="t6b", url="https://example.com", consecutive_failures=2)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 404, now=now)

    assert ev.new_status == TargetStatus.COOLDOWN
    assert ev.consecutive_failures == 3
    assert (ev.next_allowed_at - now).total_seconds() == 1800.0


def test_evaluate_response_429_with_retry_after_seconds():
    policy = FetchPolicy()
    t = Target(id="t7", url="https://example.com", consecutive_failures=0)
    now = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
    ev = policy.evaluate_response(t, 429, headers={"retry-after": "120"}, now=now)

    assert ev.new_status == TargetStatus.BACKOFF
    assert ev.consecutive_failures == 1
    assert ev.next_allowed_at is not None
    diff = (ev.next_allowed_at - now).total_seconds()
    assert abs(diff - 120.0) < 1.0


def test_evaluate_response_429_with_retry_after_http_date():
    policy = FetchPolicy()
    t = Target(id="t7b", url="https://example.com", consecutive_failures=0)
    now = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
    retry_date = "Tue, 18 Aug 2026 10:05:00 GMT"
    ev = policy.evaluate_response(t, 429, headers={"retry-after": retry_date}, now=now)

    assert ev.new_status == TargetStatus.BACKOFF
    assert ev.consecutive_failures == 1
    diff = (ev.next_allowed_at - now).total_seconds()
    assert 298.0 <= diff <= 302.0


def test_evaluate_response_429_with_malformed_retry_after():
    policy = FetchPolicy()
    t = Target(id="t7c", url="https://example.com", consecutive_failures=0)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 429, headers={"retry-after": "not-valid"}, now=now)

    assert ev.new_status == TargetStatus.BACKOFF
    # 应退回指数退避
    delay = (ev.next_allowed_at - now).total_seconds()
    assert 27.0 <= delay <= 33.0


def test_evaluate_response_429_with_negative_retry_after():
    policy = FetchPolicy()
    t = Target(id="t7d", url="https://example.com", consecutive_failures=0)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 429, headers={"retry-after": "-1"}, now=now)

    assert ev.new_status == TargetStatus.BACKOFF
    # 负数应视为 malformed，退回指数退避
    delay = (ev.next_allowed_at - now).total_seconds()
    assert 27.0 <= delay <= 33.0


def test_evaluate_response_429_with_huge_retry_after():
    policy = FetchPolicy(retry_after_cap_sec=3600.0)
    t = Target(id="t7e", url="https://example.com", consecutive_failures=0)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 429, headers={"retry-after": "999999999"}, now=now)

    assert ev.new_status == TargetStatus.BACKOFF
    delay = (ev.next_allowed_at - now).total_seconds()
    assert delay <= 3600.0


def test_evaluate_response_5xx_backoff():
    policy = FetchPolicy()
    t = Target(id="t8", url="https://example.com", consecutive_failures=0)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 502, now=now)

    assert ev.new_status == TargetStatus.BACKOFF
    assert ev.consecutive_failures == 1
    delay = (ev.next_allowed_at - now).total_seconds()
    assert 27.0 <= delay <= 33.0


def test_evaluate_response_5xx_escalates_to_cooldown_after_threshold():
    policy = FetchPolicy(max_consecutive_failures=3)
    t = Target(id="t9", url="https://example.com", consecutive_failures=2)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 502, now=now)

    assert ev.new_status == TargetStatus.COOLDOWN
    assert ev.consecutive_failures == 3
    assert ev.next_allowed_at is not None
    # ladder index 0: 30min
    diff = (ev.next_allowed_at - now).total_seconds()
    assert 1798.0 <= diff <= 1802.0


def test_evaluate_response_timeout_transport_failure():
    policy = FetchPolicy()
    t = Target(id="t10", url="https://example.com", consecutive_failures=0)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 0, error="Connection timeout", now=now)

    assert ev.new_status == TargetStatus.BACKOFF
    assert ev.consecutive_failures == 1
    assert "Transport failure" in ev.reason


def test_evaluate_response_dns_transport_failure():
    policy = FetchPolicy()
    t = Target(id="t11", url="https://example.com", consecutive_failures=0)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 0, error="DNS resolution failed for example.com", now=now)

    assert ev.new_status == TargetStatus.BACKOFF
    assert ev.consecutive_failures == 1
    assert "Transport failure" in ev.reason


def test_evaluate_response_connection_error_transport_failure():
    policy = FetchPolicy()
    t = Target(id="t12", url="https://example.com", consecutive_failures=0)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 0, error="Connection refused", now=now)

    assert ev.new_status == TargetStatus.BACKOFF
    assert ev.consecutive_failures == 1
    assert "Transport failure" in ev.reason


def test_backoff_contains_bounded_jitter():
    policy = FetchPolicy(base_backoff_sec=60.0, max_backoff_sec=600.0, jitter_ratio=0.2)
    t = Target(id="t13", url="https://example.com", consecutive_failures=1)
    now = datetime.now(timezone.utc)
    delays = []
    for _ in range(20):
        ev = policy.evaluate_response(t, 500, now=now)
        delays.append((ev.next_allowed_at - now).total_seconds())
    # raw backoff for failures=2: 60 * 2^(1) = 120s, with deterministic jitter
    assert all(96.0 <= d <= 144.0 for d in delays)
    # deterministic jitter: same target/state/backoff produces identical delay
    assert len(set(delays)) == 1


def test_evaluate_response_retry_after_cap():
    policy = FetchPolicy(retry_after_cap_sec=600.0)
    t = Target(id="t14", url="https://example.com", consecutive_failures=0)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 429, headers={"retry-after": "9999"}, now=now)

    delay = (ev.next_allowed_at - now).total_seconds()
    assert delay <= 600.0


def test_repeated_429_escalates_to_cooldown(tmp_path):
    policy = FetchPolicy(max_consecutive_failures=3)
    t = Target(id="t15", url="https://example.com", consecutive_failures=2)
    now = datetime.now(timezone.utc)
    # 第3次429，达到阈值，应进入 COOLDOWN（使用 cooldown ladder，而非 Retry-After）
    ev = policy.evaluate_response(t, 429, headers={"retry-after": "1"}, now=now)

    assert ev.new_status == TargetStatus.COOLDOWN
    assert ev.consecutive_failures == 3
    # 达到阈值后强制进入 COOLDOWN，使用 ladder 第一档 30min
    delay = (ev.next_allowed_at - now).total_seconds()
    assert abs(delay - 1800.0) < 1.0


def test_max_cooldown_ladder():
    policy = FetchPolicy(
        base_backoff_sec=30.0,
        max_backoff_sec=600.0,
        max_consecutive_failures=3,
        cooldown_ladder=[1800.0, 3600.0, 7200.0, 14400.0],
    )
    # 连续多次失败，应使用 ladder 最大值 14400s
    t = Target(id="t16", url="https://example.com", consecutive_failures=10)
    now = datetime.now(timezone.utc)
    ev = policy.evaluate_response(t, 502, now=now)

    assert ev.new_status == TargetStatus.COOLDOWN
    assert ev.consecutive_failures == 11
    diff = (ev.next_allowed_at - now).total_seconds()
    assert 14398.0 <= diff <= 14402.0


def test_clock_jump_backward_blocks_fetch():
    policy = FetchPolicy()
    # next_allowed_at 在"未来"（相对于跳变后的时间）
    future_time = datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc)
    t = Target(
        id="t17",
        url="https://example.com",
        status=TargetStatus.BACKOFF,
        next_allowed_at=future_time,
    )
    # 当前时间在 future_time 之前（时钟倒流）
    now = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
    decision = policy.prepare_request(t, now=now)

    assert decision.allowed is False
    assert decision.delay_seconds > 0


def test_clock_jump_forward_allows_fetch():
    policy = FetchPolicy()
    # next_allowed_at 在过去，但时钟跳变到更远的未来
    past_time = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
    t = Target(
        id="t18",
        url="https://example.com",
        status=TargetStatus.BACKOFF,
        next_allowed_at=past_time,
    )
    # 当前时间在 past_time 之后（时钟前进）
    now = datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc)
    decision = policy.prepare_request(t, now=now)

    assert decision.allowed is True


def test_two_targets_do_not_contaminate():
    policy = FetchPolicy(base_backoff_sec=60.0, max_backoff_sec=600.0, jitter_ratio=0.0)
    t_a = Target(id="t_a", url="https://a.example.com", consecutive_failures=0)
    t_b = Target(id="t_b", url="https://b.example.com", consecutive_failures=1)
    now = datetime.now(timezone.utc)

    ev_a = policy.evaluate_response(t_a, 500, now=now)
    ev_b = policy.evaluate_response(t_b, 500, now=now)

    # t_a 第1次失败，backoff = 60s
    # t_b 第2次失败，backoff = 120s
    delay_a = (ev_a.next_allowed_at - now).total_seconds()
    delay_b = (ev_b.next_allowed_at - now).total_seconds()
    assert abs(delay_a - 60.0) < 1.0
    assert abs(delay_b - 120.0) < 1.0
    assert ev_a.consecutive_failures == 1
    assert ev_b.consecutive_failures == 2


# ---------------------------------------------------------------------------
# FR-03: Deterministic Jitter & Strict Retry-After Semantics
# ---------------------------------------------------------------------------


def test_deterministic_jitter_same_target_same_state():
    """Same target at same failure count must produce identical jitter."""
    policy = FetchPolicy(base_backoff_sec=60.0, max_backoff_sec=600.0, jitter_ratio=0.2)
    t = Target(id="jitter-target", url="https://example.com", consecutive_failures=1)
    now = datetime.now(timezone.utc)

    delays = []
    for _ in range(10):
        ev = policy.evaluate_response(t, 500, now=now)
        delays.append((ev.next_allowed_at - now).total_seconds())

    assert len(set(delays)) == 1


def test_deterministic_jitter_different_targets_differ():
    """Different targets produce different jitter at the same failure count."""
    policy = FetchPolicy(base_backoff_sec=60.0, max_backoff_sec=600.0, jitter_ratio=0.2)
    t_a = Target(id="jitter-a", url="https://a.example.com", consecutive_failures=1)
    t_b = Target(id="jitter-b", url="https://b.example.com", consecutive_failures=1)
    now = datetime.now(timezone.utc)

    ev_a = policy.evaluate_response(t_a, 500, now=now)
    ev_b = policy.evaluate_response(t_b, 500, now=now)

    delay_a = (ev_a.next_allowed_at - now).total_seconds()
    delay_b = (ev_b.next_allowed_at - now).total_seconds()
    assert abs(delay_a - delay_b) > 0.001


def test_retry_after_is_effective_floor():
    """Retry-After must not be shortened by local cooldown escalation."""
    policy = FetchPolicy()
    t = Target(id="retry-floor", url="https://example.com", consecutive_failures=2, status=TargetStatus.COOLDOWN)
    now = datetime.now(timezone.utc)
    # Target already in COOLDOWN, server says wait 3600s
    ev = policy.evaluate_response(t, 429, headers={"retry-after": "3600"}, now=now)

    assert ev.new_status == TargetStatus.COOLDOWN
    delay = (ev.next_allowed_at - now).total_seconds()
    # Local cooldown ladder[0] = 1800s, but Retry-After = 3600s
    # Retry-After is the effective floor, so delay should be >= 3600s
    assert delay >= 3600.0


def test_host_rate_limiter_blocks_shared_host():
    limiter = HostRateLimiter()
    now = datetime.now(timezone.utc)
    assert limiter.prepare_request("example.com", now) == (True, None, None)

    # Simulate a 403 cooldown for example.com
    limiter.update_after_response("example.com", now + timedelta(seconds=120))
    allowed, remaining, reason = limiter.prepare_request("example.com", now)
    assert allowed is False
    assert remaining is not None
    assert remaining > 110


def test_host_rate_limiter_independent_hosts():
    limiter = HostRateLimiter()
    now = datetime.now(timezone.utc)
    limiter.update_after_response("a.example.com", now + timedelta(seconds=120))
    allowed, _, _ = limiter.prepare_request("b.example.com", now)
    assert allowed is True


def test_host_rate_limiter_most_restrictive_wins():
    limiter = HostRateLimiter()
    now = datetime.now(timezone.utc)
    limiter.update_after_response("example.com", now + timedelta(seconds=60))
    limiter.update_after_response("example.com", now + timedelta(seconds=180))
    allowed, remaining, _ = limiter.prepare_request("example.com", now)
    assert allowed is False
    assert 175 <= remaining <= 185


def test_host_rate_limiter_expiry_allows_request():
    limiter = HostRateLimiter()
    now = datetime.now(timezone.utc)
    limiter.update_after_response("example.com", now + timedelta(seconds=5))
    future = now + timedelta(seconds=10)
    allowed, _, _ = limiter.prepare_request("example.com", future)
    assert allowed is True


def test_prepare_request_blocks_shared_host_via_policy():
    policy = FetchPolicy()
    now = datetime.now(timezone.utc)
    t_a = Target(id="t-a", url="https://example.com/a", consecutive_failures=0)
    t_b = Target(id="t-b", url="https://example.com/b", consecutive_failures=0)

    # First target gets blocked by host limiter after a 403
    ev_a = policy.evaluate_response(t_a, 403, now=now)
    assert ev_a.new_status == TargetStatus.COOLDOWN

    # Second target on same host should be blocked by host limiter
    decision = policy.prepare_request(t_b, now=now)
    assert decision.allowed is False
    assert "Host 'example.com'" in (decision.reason or "")
