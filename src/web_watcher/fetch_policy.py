import re
import random
import email.utils
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from web_watcher.models import Target, TargetStatus


def _to_utc_naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_interval_seconds(interval_str: str) -> float:
    s = interval_str.strip().lower()
    m = re.match(r"^(\d+)([smhd])$", s)
    if not m:
        try:
            return float(s)
        except ValueError:
            return 900.0  # 默认 15m
    val, unit = int(m.group(1)), m.group(2)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return float(val * mult[unit])


def parse_retry_after(retry_after_str: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not retry_after_str:
        return None
    s = retry_after_str.strip()
    # 1. 秒数格式 (e.g., "120")
    if s.isdigit():
        return float(s)
    # 2. HTTP-Date 格式 (e.g., "Wed, 21 Oct 2026 07:28:00 GMT")
    try:
        parsed_dt = email.utils.parsedate_to_datetime(s)
        if parsed_dt:
            now_dt = _to_utc_naive(now) or datetime.utcnow()
            target_dt = _to_utc_naive(parsed_dt)
            diff = (target_dt - now_dt).total_seconds()
            return max(0.0, diff)
    except Exception:
        pass
    return None


@dataclass
class FetchDecision:
    allowed: bool
    headers: Dict[str, str] = field(default_factory=dict)
    delay_seconds: float = 0.0
    reason: Optional[str] = None


@dataclass
class FetchEvaluation:
    target_id: str
    status_code: int
    new_status: TargetStatus
    should_emit_signal: bool
    consecutive_failures: int
    next_allowed_at: Optional[datetime]
    updated_etag: Optional[str] = None
    updated_last_modified: Optional[str] = None
    reason: str = ""


class FetchPolicy:
    """礼貌抓取与韧性决策引擎：协商缓存、退避与状态机流转"""

    def __init__(
        self,
        base_backoff_sec: float = 30.0,
        max_backoff_sec: float = 600.0,
        max_consecutive_failures: int = 3,
        cooldown_ladder: Optional[List[float]] = None,
        jitter_ratio: float = 0.1,
    ):
        self.base_backoff_sec = base_backoff_sec
        self.max_backoff_sec = max_backoff_sec
        self.max_consecutive_failures = max_consecutive_failures
        # 冷却阶梯: 30min -> 1h -> 2h -> 4h
        self.cooldown_ladder = cooldown_ladder or [1800.0, 3600.0, 7200.0, 14400.0]
        self.jitter_ratio = jitter_ratio

    def prepare_request(self, target: Target, now: Optional[datetime] = None) -> FetchDecision:
        now_dt = _to_utc_naive(now) or datetime.utcnow()
        target_next = _to_utc_naive(target.next_allowed_at)

        # 1. 检查是否在冷却或退避保护期内
        if target_next and now_dt < target_next:
            remaining = (target_next - now_dt).total_seconds()
            status_val = target.status.value if hasattr(target.status, "value") else str(target.status)
            return FetchDecision(
                allowed=False,
                delay_seconds=remaining,
                reason=f"Target '{target.id}' is in {status_val} window ({int(remaining)}s remaining)",
            )

        # 2. 组装协商缓存头
        headers: Dict[str, str] = {}
        if target.etag:
            headers["If-None-Match"] = target.etag
        if target.last_modified:
            headers["If-Modified-Since"] = target.last_modified

        status_val = target.status.value if hasattr(target.status, "value") else str(target.status)
        return FetchDecision(
            allowed=True,
            headers=headers,
            reason=f"Target '{target.id}' ready for fetch (status: {status_val})",
        )

    def evaluate_response(
        self,
        target: Target,
        status_code: int,
        headers: Optional[Dict[str, str]] = None,
        error: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> FetchEvaluation:
        now_dt = _to_utc_naive(now) or datetime.utcnow()
        headers_map = {k.lower(): v for k, v in (headers or {}).items()}
        interval_sec = parse_interval_seconds(target.interval)

        # A. 304 Not Modified: 协商缓存命中，直接短路
        if status_code == 304:
            next_allowed = now_dt + timedelta(seconds=interval_sec)
            return FetchEvaluation(
                target_id=target.id,
                status_code=304,
                new_status=TargetStatus.NORMAL,
                should_emit_signal=False,
                consecutive_failures=0,
                next_allowed_at=next_allowed,
                updated_etag=target.etag,
                updated_last_modified=target.last_modified,
                reason="304 Not Modified: Cache valid, no change detected",
            )

        # B. 200-299 Success: 正常抓取成功
        if 200 <= status_code < 300:
            res_etag = headers_map.get("etag") or target.etag
            res_last_mod = headers_map.get("last-modified") or target.last_modified
            next_allowed = now_dt + timedelta(seconds=interval_sec)
            return FetchEvaluation(
                target_id=target.id,
                status_code=status_code,
                new_status=TargetStatus.NORMAL,
                should_emit_signal=True,
                consecutive_failures=0,
                next_allowed_at=next_allowed,
                updated_etag=res_etag,
                updated_last_modified=res_last_mod,
                reason=f"HTTP {status_code} Success: Content fetched",
            )

        # C. 403 Forbidden: 触发风控或权限拒绝，直接进入长周期 COOLDOWN
        if status_code == 403:
            failures = target.consecutive_failures + 1
            cd_sec = self.cooldown_ladder[0]
            next_allowed = now_dt + timedelta(seconds=cd_sec)
            return FetchEvaluation(
                target_id=target.id,
                status_code=403,
                new_status=TargetStatus.COOLDOWN,
                should_emit_signal=False,
                consecutive_failures=failures,
                next_allowed_at=next_allowed,
                reason=f"HTTP 403 Forbidden: Entering cooldown ({int(cd_sec)}s)",
            )

        # D. 429 Too Many Requests / 5xx Server Error / 网络故障 (status_code == 0)
        failures = target.consecutive_failures + 1
        retry_after_sec = parse_retry_after(headers_map.get("retry-after"), now=now_dt)

        # 连续失败达标，或在 RECOVERING/COOLDOWN 状态下探针失败 -> 进入/升级 COOLDOWN
        if failures >= self.max_consecutive_failures or target.status in (TargetStatus.COOLDOWN, TargetStatus.RECOVERING):
            ladder_idx = min(max(0, failures - self.max_consecutive_failures), len(self.cooldown_ladder) - 1)
            cd_sec = self.cooldown_ladder[ladder_idx]
            next_allowed = now_dt + timedelta(seconds=cd_sec)
            return FetchEvaluation(
                target_id=target.id,
                status_code=status_code,
                new_status=TargetStatus.COOLDOWN,
                should_emit_signal=False,
                consecutive_failures=failures,
                next_allowed_at=next_allowed,
                reason=f"Consecutive failures ({failures}) reached threshold: Escalating to cooldown ({int(cd_sec)}s)",
            )

        # 临时抖动 -> 进入 BACKOFF 指数退避
        if retry_after_sec is not None:
            delay = retry_after_sec
        else:
            raw_backoff = min(self.base_backoff_sec * (2 ** (failures - 1)), self.max_backoff_sec)
            jitter = raw_backoff * random.uniform(-self.jitter_ratio, self.jitter_ratio)
            delay = max(1.0, raw_backoff + jitter)

        next_allowed = now_dt + timedelta(seconds=delay)
        return FetchEvaluation(
            target_id=target.id,
            status_code=status_code,
            new_status=TargetStatus.BACKOFF,
            should_emit_signal=False,
            consecutive_failures=failures,
            next_allowed_at=next_allowed,
            reason=f"Temporary failure (HTTP {status_code}): Backing off for {int(delay)}s",
        )
