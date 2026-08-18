import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from web_watcher.models import Target, TargetStatus
from web_watcher.fetch_policy import FetchPolicy
from web_watcher.fetcher import SmartFetcher, FetchResult
from web_watcher.dom_extractor import DOMExtractor
from web_watcher.rule_models import ExtractorConfig, ExtractionResult
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
    extracted_results: Dict[str, ExtractionResult]
    extracted_values: Dict[str, Any]
    is_304: bool = False
    has_extraction_failures: bool = False
    reason: str = ""


class GenericWebTarget:
    """General-purpose web target adapter with polite fetch, cache handling, extraction, and false-positive guards."""

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

        # 1. Policy pre-check
        decision = policy.prepare_request(self.target, now=now)
        if not decision.allowed:
            return TargetExecutionResult(
                target_id=self.target.id,
                allowed=False,
                status_code=None,
                new_status=self.target.status,
                signals_emitted=[],
                extracted_results={},
                extracted_values={},
                reason=decision.reason or "Execution skipped by policy",
            )

        # 2. Fetch
        headers_to_send = dict(self.custom_headers)
        headers_to_send.update(decision.headers)

        fetch_res: FetchResult = fetcher.fetch(
            url=self.target.url,
            custom_headers=headers_to_send,
            etag=self.target.etag,
            last_modified=self.target.last_modified,
            timeout=self.timeout,
        )

        # 3. Policy post-evaluation
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

        # 4. Update target state machine
        self.target.status = evaluation.new_status
        self.target.etag = evaluation.updated_etag
        self.target.last_modified = evaluation.updated_last_modified
        self.target.consecutive_failures = evaluation.consecutive_failures
        self.target.next_allowed_at = evaluation.next_allowed_at
        self.target.last_fetched_at = now

        # 5. 304 short circuit
        if evaluation.status_code == 304 or fetch_res.is_304_not_modified:
            if repo and hasattr(repo, "save_target"):
                repo.save_target(self.target)
            return TargetExecutionResult(
                target_id=self.target.id,
                allowed=True,
                status_code=304,
                new_status=self.target.status,
                signals_emitted=[],
                extracted_results={},
                extracted_values={},
                is_304=True,
                reason=evaluation.reason,
            )

        # 6. Non-success status: persist and return
        if not evaluation.should_emit_signal:
            if repo and hasattr(repo, "save_target"):
                repo.save_target(self.target)
            return TargetExecutionResult(
                target_id=self.target.id,
                allowed=True,
                status_code=evaluation.status_code,
                new_status=self.target.status,
                signals_emitted=[],
                extracted_results={},
                extracted_values={},
                reason=evaluation.reason,
            )

        # 7. Successful fetch: extract fields
        extracted_results: Dict[str, ExtractionResult] = {}
        extracted_values: Dict[str, Any] = {}
        has_failures = False

        for ext in self.extractors:
            result = DOMExtractor.extract(fetch_res.content, ext)
            extracted_results[ext.name] = result
            if not result.is_found:
                has_failures = True
            else:
                extracted_values[ext.name] = result.value

        # 8. Content-hash change detection with false-positive guards
        new_hash = hashlib.sha256(fetch_res.content.encode("utf-8")).hexdigest()
        prev_hash = self.target.content_hash
        should_emit = False
        emit_reason = "Content identical, no signal emitted"

        if prev_hash is None or not self.target.metadata.get("initialized"):
            should_emit = True
            emit_reason = "Initial fetch"
        elif prev_hash != new_hash:
            # Content changed; if configured extractors all failed to match, suppress as a likely false positive
            if self.extractors and all(not extracted_results[e.name].is_found for e in self.extractors):
                should_emit = False
                emit_reason = "Content changed but all configured extractors failed; suppressing as potential false positive"
            else:
                should_emit = True
                emit_reason = "Content changed with at least one successful extraction"
        else:
            should_emit = False
            emit_reason = "Content identical, no signal emitted"

        signals: List[Any] = []
        if should_emit:
            self.target.content_hash = new_hash
            self.target.metadata["initialized"] = True
            self.target.metadata["last_extracted"] = {k: v.value for k, v in extracted_results.items() if v.is_found}

            payload = {
                "target_id": self.target.id,
                "url": self.target.url,
                "extracted_values": extracted_values,
                "extraction_results": {k: {"status": v.status.value, "value": v.value} for k, v in extracted_results.items()},
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

        # 9. Persist latest target state
        if repo and hasattr(repo, "save_target"):
            repo.save_target(self.target)

        return TargetExecutionResult(
            target_id=self.target.id,
            allowed=True,
            status_code=fetch_res.status_code,
            new_status=self.target.status,
            signals_emitted=signals,
            extracted_results=extracted_results,
            extracted_values=extracted_values,
            is_304=False,
            has_extraction_failures=has_failures,
            reason=emit_reason,
        )
