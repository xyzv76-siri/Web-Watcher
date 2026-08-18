import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Union
from web_watcher.models import Target, TargetStatus
from web_watcher.fetch_policy import FetchPolicy, FetchDecision, FetchEvaluation
from web_watcher.fetcher import SmartFetcher, FetchResult
from web_watcher.dom_extractor import DOMExtractor
from web_watcher.rule_models import ExtractorConfig
try:
    from web_watcher.models import Signal
except ImportError:
    Signal = None


@dataclass
class TargetExecutionResult:
    target_id: str
    allowed: bool
    status_code: Optional[int]
    new_status: TargetStatus
    signals_emitted: List[Any]
    extracted_values: Dict[str, Any]
    is_304: bool = False
    reason: str = ""


class GenericWebTarget:
    """通用 Web 页面监控目标适配器：执行礼貌抓取、协商缓存判定、DOM/正则字段提取与 Signal 生产"""

    def __init__(
        self,
        target: Target,
        extractors: Optional[List[ExtractorConfig]] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ):
        self.target = target
        self.extractors = extractors or []
        self.custom_headers = custom_headers or {}
        self.timeout = timeout

    def execute(
        self,
        fetcher: Optional[SmartFetcher] = None,
        policy: Optional[FetchPolicy] = None,
        repo: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> TargetExecutionResult:
        now = now or datetime.utcnow()
        fetcher = fetcher or SmartFetcher(default_timeout=self.timeout)
        policy = policy or FetchPolicy()

        # 1. 策略前置判定（退避/冷却/ETag 装配）
        decision = policy.prepare_request(self.target, now=now)
        if not decision.allowed:
            return TargetExecutionResult(
                target_id=self.target.id,
                allowed=False,
                status_code=None,
                new_status=self.target.status,
                signals_emitted=[],
                extracted_values={},
                reason=decision.reason or "Execution skipped by policy",
            )

        # 2. 发起 HTTP 抓取
        headers_to_send = dict(self.custom_headers)
        headers_to_send.update(decision.headers)

        fetch_res: FetchResult = fetcher.fetch(
            url=self.target.url,
            custom_headers=headers_to_send,
            etag=self.target.etag,
            last_modified=self.target.last_modified,
            timeout=self.timeout,
        )

        # 3. 策略后置评估
        headers_dict = {}
        if fetch_res.etag:
            headers_dict["etag"] = fetch_res.etag
        if fetch_res.last_modified:
            headers_dict["last-modified"] = fetch_res.last_modified

        evaluation = policy.evaluate_response(
            target=self.target,
            status_code=fetch_res.status_code,
            headers=headers_dict,
            error=fetch_res.error,
            now=now,
        )

        # 4. 同步更新 Target 状态机
        self.target.status = evaluation.new_status
        self.target.etag = evaluation.updated_etag
        self.target.last_modified = evaluation.updated_last_modified
        self.target.consecutive_failures = evaluation.consecutive_failures
        self.target.next_allowed_at = evaluation.next_allowed_at
        self.target.last_fetched_at = now

        # 5. 命中 304：协商缓存短路
        if evaluation.status_code == 304 or fetch_res.is_304_not_modified:
            if repo and hasattr(repo, "save_target"):
                repo.save_target(self.target)
            return TargetExecutionResult(
                target_id=self.target.id,
                allowed=True,
                status_code=304,
                new_status=self.target.status,
                signals_emitted=[],
                extracted_values={},
                is_304=True,
                reason=evaluation.reason,
            )

        # 6. 非成功状态（429/403/5xx）：持久化后返回
        if not evaluation.should_emit_signal:
            if repo and hasattr(repo, "save_target"):
                repo.save_target(self.target)
            return TargetExecutionResult(
                target_id=self.target.id,
                allowed=True,
                status_code=evaluation.status_code,
                new_status=self.target.status,
                signals_emitted=[],
                extracted_values={},
                reason=evaluation.reason,
            )

        # 7. HTTP 200 成功响应：执行 DOM / Regex 提取
        extracted: Dict[str, Any] = {}
        for ext in self.extractors:
            extracted[ext.name] = DOMExtractor.extract(fetch_res.content, ext)

        new_hash = hashlib.sha256(fetch_res.content.encode("utf-8")).hexdigest()
        prev_hash = self.target.content_hash

        signals: List[Any] = []
        # 首次抓取或内容 Hash 改变时产生 Signal
        if prev_hash is None or prev_hash != new_hash or not self.target.metadata.get("initialized"):
            self.target.content_hash = new_hash
            self.target.metadata["initialized"] = True
            self.target.metadata["last_extracted"] = extracted

            payload = {
                "target_id": self.target.id,
                "url": self.target.url,
                "extracted_values": extracted,
                "content_hash": new_hash,
                "previous_hash": prev_hash,
                "status_code": fetch_res.status_code,
                "captured_at": now.isoformat(),
            }

            sig_obj = None
            if Signal is not None:
                try:
                    sig_obj = Signal(
                        id=f"sig_{self.target.id}_{int(now.timestamp())}",
                        entity_id=self.target.id,
                        signal_type="WEB_CONTENT_CHANGED",
                        payload=payload,
                        created_at=now,
                    )
                except Exception:
                    try:
                        sig_obj = Signal(
                            entity_id=self.target.id,
                            signal_type="WEB_CONTENT_CHANGED",
                            payload=payload,
                        )
                    except Exception:
                        sig_obj = payload
            else:
                sig_obj = payload

            signals.append(sig_obj)

            if repo and hasattr(repo, "save_signal") and sig_obj is not None:
                try:
                    repo.save_signal(sig_obj)
                except Exception:
                    pass

        # 8. 保存最新 target 状态到仓储
        if repo and hasattr(repo, "save_target"):
            repo.save_target(self.target)

        return TargetExecutionResult(
            target_id=self.target.id,
            allowed=True,
            status_code=fetch_res.status_code,
            new_status=self.target.status,
            signals_emitted=signals,
            extracted_values=extracted,
            is_304=False,
            reason="Signal emitted on content change" if signals else "Content identical, no signal emitted",
        )
