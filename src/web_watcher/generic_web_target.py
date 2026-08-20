import hashlib
import json
from dataclasses import dataclass, field
from web_watcher.signal_types import SignalType
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from web_watcher.models import Target, TargetStatus
from web_watcher.fetch_policy import FetchPolicy
from web_watcher.fetcher import SmartFetcher, FetchResult
from web_watcher.fetch import FetchStatus
from web_watcher.dom_extractor import DOMExtractor
from web_watcher.rule_models import ExtractorConfig, ExtractionResult
from web_watcher.execution_semantics import ExecutionOutcome, transition_for
from web_watcher.targets import validate_selector, _validate_url
from web_watcher.normalizer import normalize_extracted_text
from web_watcher.web_fingerprint import observation_fingerprint, selector_config_fingerprint
from web_watcher.diff import compute_diff, DiffResult
from web_watcher.observation import ObservationResult, ObservationStatus
from web_watcher.dynamic_noise import FalsePositiveGuard, DynamicNoiseFilter, dynamic_noise_ratio
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
    # observation-only durable-state fields for scheduler commit
    updated_etag: Optional[str] = None
    updated_last_modified: Optional[str] = None
    updated_content_hash: Optional[str] = None
    updated_metadata: Optional[Dict[str, Any]] = None
    updated_url: Optional[str] = None
    consecutive_failures: int = 0
    next_allowed_at: Optional[datetime] = None
    last_fetched_at: Optional[datetime] = None
    outcome: Any = None
    transition: Any = None
    observation: Optional[ObservationResult] = None


class GenericWebTarget:
    """General-purpose web target adapter with polite fetch, cache handling, extraction, normalization, fingerprinting, diffing, and false-positive guards."""

    def __init__(
        self,
        target: Target,
        extractors: Optional[List[ExtractorConfig]] = None,
        custom_headers: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
        false_positive_guard: Optional[FalsePositiveGuard] = None,
    ):
        _validate_url(target.url)
        for ext in (extractors or []):
            validate_selector(ext.selector_type, ext.selector)
        self.target = target
        self.extractors = extractors or []
        self.custom_headers = custom_headers or {}
        self.timeout = timeout
        self.false_positive_guard = false_positive_guard or FalsePositiveGuard()

    def execute(
        self,
        fetcher: Optional[SmartFetcher] = None,
        policy: Optional[FetchPolicy] = None,
        repo: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> TargetExecutionResult:
        now = now or datetime.now(timezone.utc)
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
                outcome=ExecutionOutcome.POLICY_BLOCKED,
                transition=transition_for(
                    ExecutionOutcome.POLICY_BLOCKED,
                    target=self.target,
                    now=now,
                ),
            )

        # 2. Fetch
        headers_to_send = dict(self.custom_headers)
        headers_to_send.update(decision.headers)

        try:
            fetch_res: FetchResult = fetcher.fetch(
                url=self.target.url,
                custom_headers=headers_to_send,
                etag=self.target.etag,
                last_modified=self.target.last_modified,
                timeout=self.timeout,
            )
        finally:
            # Release host claim after fetch completes (success or failure)
            if decision.host and policy.host_rate_limiter:
                policy.host_rate_limiter.release_request(decision.host)

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

        # 3-1. Explicit redirect handling: update target URL on permanent redirect
        redirect_url = None
        if fetch_res.status == FetchStatus.REDIRECT and fetch_res.metadata:
            redirect_url = fetch_res.metadata.get("redirect_url")
            if redirect_url and fetch_res.status_code == 301:
                # Permanent redirect: update target URL
                pass

        # 4. Collect observation-only state updates; do NOT mutate self.target durable state here.
        observed_status = evaluation.new_status
        updated_etag = evaluation.updated_etag
        updated_last_modified = evaluation.updated_last_modified
        updated_url = redirect_url if (fetch_res.status_code == 301 and redirect_url) else None
        observed_consecutive_failures = evaluation.consecutive_failures
        observed_next_allowed_at = evaluation.next_allowed_at
        observed_last_fetched_at = now

        # 5. 304 short circuit
        if evaluation.status_code == 304 or fetch_res.status == FetchStatus.NOT_MODIFIED:
            return TargetExecutionResult(
                target_id=self.target.id,
                allowed=True,
                status_code=304,
                new_status=observed_status,
                signals_emitted=[],
                extracted_results={},
                extracted_values={},
                is_304=True,
                reason=evaluation.reason,
                updated_etag=updated_etag,
                updated_last_modified=updated_last_modified,
                updated_url=updated_url,
                consecutive_failures=observed_consecutive_failures,
                next_allowed_at=observed_next_allowed_at,
                last_fetched_at=observed_last_fetched_at,
                outcome=ExecutionOutcome.NOT_MODIFIED,
                transition=transition_for(
                    ExecutionOutcome.NOT_MODIFIED,
                    target=self.target,
                    now=now,
                    etag=updated_etag,
                    last_modified=updated_last_modified,
                ),
                observation=ObservationResult(
                    target_id=self.target.id,
                    status=ObservationStatus.UNCHANGED,
                    status_code=304,
                    reason="HTTP 304 Not Modified; short-circuited without extraction or fingerprinting",
                    evidence={"http_status": 304},
                    observed_at=now,
                ),
            )

        # 6. Non-success status: return observation without extraction/fingerprint/diff
        if not evaluation.should_emit_signal:
            if evaluation.new_status == TargetStatus.COOLDOWN:
                outcome = ExecutionOutcome.POLICY_COOLDOWN
            elif fetch_res.status == FetchStatus.TIMEOUT or (fetch_res.status_code is not None and fetch_res.status_code == 0):
                outcome = ExecutionOutcome.TIMEOUT
            elif fetch_res.error is not None or (fetch_res.status_code is not None and fetch_res.status_code >= 400 and fetch_res.status_code != 404):
                outcome = ExecutionOutcome.FETCH_FAILED
            else:
                outcome = ExecutionOutcome.SUCCESS_UNCHANGED
            return TargetExecutionResult(
                target_id=self.target.id,
                allowed=True,
                status_code=evaluation.status_code,
                new_status=observed_status,
                signals_emitted=[],
                extracted_results={},
                extracted_values={},
                reason=evaluation.reason,
                updated_etag=updated_etag,
                updated_last_modified=updated_last_modified,
                updated_url=updated_url,
                consecutive_failures=observed_consecutive_failures,
                next_allowed_at=observed_next_allowed_at,
                last_fetched_at=observed_last_fetched_at,
                outcome=outcome,
                transition=transition_for(
                    outcome,
                    target=self.target,
                    now=now,
                    etag=updated_etag,
                    last_modified=updated_last_modified,
                    consecutive_failures=observed_consecutive_failures,
                    next_allowed_at=observed_next_allowed_at,
                ),
                observation=ObservationResult(
                    target_id=self.target.id,
                    status=ObservationStatus.HTTP_FAILURE if outcome == ExecutionOutcome.FETCH_FAILED else ObservationStatus.UNCHANGED,
                    status_code=evaluation.status_code,
                    reason=evaluation.reason,
                    evidence={"outcome": outcome.value if hasattr(outcome, "value") else str(outcome)},
                    observed_at=now,
                ),
            )

        # 7. Successful fetch: extract, normalize, fingerprint, diff
        extracted_results: Dict[str, ExtractionResult] = {}
        normalized_values: Dict[str, str] = {}
        fingerprints: Dict[str, str] = {}
        previous_values: Dict[str, str] = {}
        diffs: Dict[str, DiffResult] = {}
        extractor_configs: Dict[str, ExtractorConfig] = {}
        has_failures = False

        # Recover previous normalized values from target metadata if available.
        stored_previous = ((self.target.metadata or {}).get("normalized_values") or {}) if isinstance(self.target.metadata, dict) else {}

        for ext in self.extractors:
            result = DOMExtractor.extract(fetch_res.content, ext)
            extracted_results[ext.name] = result
            extractor_configs[ext.name] = ext

            if not result.is_found:
                has_failures = True
                normalized_values[ext.name] = ""
                fingerprints[ext.name] = ""
                prev = stored_previous.get(ext.name, "")
                previous_values[ext.name] = prev
                diffs[ext.name] = compute_diff(prev, "")
                continue

            raw_value = result.value or ""
            normalized = normalize_extracted_text(raw_value)
            selector_fp = selector_config_fingerprint(ext.selector_type, ext.selector)
            fp = observation_fingerprint(
                target_id=self.target.id,
                normalized_content=normalized,
                selector_fingerprint=selector_fp,
            )

            prev = stored_previous.get(ext.name, "")
            diff = compute_diff(prev, normalized)

            normalized_values[ext.name] = normalized
            fingerprints[ext.name] = fp
            previous_values[ext.name] = prev
            diffs[ext.name] = diff

        # 8. Determine observation status and signals
        # First observation: establish baseline, do NOT emit a fake change event.
        is_first_observation = not bool(self.target.metadata and self.target.metadata.get("initialized"))

        any_changed = any(d.changed for d in diffs.values())
        all_extractors_failed = bool(self.extractors) and all(not v.is_found for v in extracted_results.values())
        any_extractor_failed = bool(self.extractors) and any(not v.is_found for v in extracted_results.values())

        if is_first_observation:
            observation_status = ObservationStatus.FIRST_OBSERVATION
            should_emit_signal = False
            emit_reason = "First successful fetch; baseline established"
        elif all_extractors_failed:
            observation_status = ObservationStatus.EXTRACTION_FAILURE
            should_emit_signal = False
            emit_reason = "All extractors failed; potential selector or content change"
        elif any_extractor_failed and not all_extractors_failed:
            # Partial selector failure: treat as extraction failure, not content change.
            # Selector disappearance ≠ business deletion.
            observation_status = ObservationStatus.EXTRACTION_FAILURE
            should_emit_signal = False
            emit_reason = "Partial selector failure; cannot confirm content change"
        elif any_changed:
            # Apply false positive guard before emitting a signal.
            suppress, guard_reason = self.false_positive_guard.should_suppress_signal(
                diffs=diffs,
                normalized_values=normalized_values,
                previous_values=previous_values,
                all_extractors_failed=all_extractors_failed,
                is_first_observation=is_first_observation,
                http_status_code=fetch_res.status_code,
            )
            if suppress:
                observation_status = ObservationStatus.UNCHANGED
                should_emit_signal = False
                emit_reason = f"Change suppressed: {guard_reason}"
            else:
                observation_status = ObservationStatus.CHANGED
                should_emit_signal = True
                emit_reason = guard_reason
        else:
            observation_status = ObservationStatus.UNCHANGED
            should_emit_signal = False
            emit_reason = "All normalized values identical to previous observation"

        # 9. Build evidence chain
        evidence = {
            "target_id": self.target.id,
            "url": self.target.url,
            "status_code": fetch_res.status_code,
            "observed_at": now.isoformat(),
            "extractor_results": {
                name: {
                    "status": result.status.value,
                    "raw_value": result.raw_value,
                    "normalized_value": normalized_values.get(name, ""),
                    "fingerprint": fingerprints.get(name, ""),
                    "previous_value": previous_values.get(name, ""),
                    "changed": diffs.get(name, DiffResult.unchanged("", "")).changed,
                    "diff_summary": diffs.get(name, DiffResult.unchanged("", "")).summary,
                    "selector_type": extractor_configs[name].selector_type,
                    "selector": extractor_configs[name].selector,
                    # Dynamic noise analysis for investigation.
                    "noise_filtered_previous": self.false_positive_guard.noise_filter.filter(
                        previous_values.get(name, "")
                    ),
                    "noise_filtered_current": self.false_positive_guard.noise_filter.filter(
                        normalized_values.get(name, "")
                    ),
                    "dynamic_noise_ratio_previous": dynamic_noise_ratio(previous_values.get(name, "")),
                    "dynamic_noise_ratio_current": dynamic_noise_ratio(normalized_values.get(name, "")),
                }
                for name, result in extracted_results.items()
            },
        }

        observation = ObservationResult(
            target_id=self.target.id,
            status=observation_status,
            status_code=fetch_res.status_code,
            extracted_results=extracted_results,
            normalized_values=normalized_values,
            fingerprints=fingerprints,
            diffs=diffs,
            previous_values=previous_values,
            evidence=evidence,
            observed_at=now,
            reason=emit_reason,
        )

        # 10. Build signals and durable-state updates
        signals: List[Any] = []
        updated_metadata = dict(self.target.metadata or {})
        updated_content_hash = self.target.content_hash

        # Always persist normalized values on successful fetch so that
        # first observation establishes a baseline and subsequent observations
        # can diff against it.
        updated_metadata["normalized_values"] = normalized_values
        updated_metadata["last_extracted"] = {
            k: v.value for k, v in extracted_results.items() if v.is_found
        }

        if is_first_observation:
            updated_metadata["initialized"] = True
            should_emit_signal = False
            emit_reason = "First successful fetch; baseline established"
        elif should_emit_signal:
            pass  # already determined above
        else:
            pass  # unchanged or extraction failure

        if should_emit_signal:
            # Canonical content fingerprint: stable hash of all normalized values.
            # This allows distinct content changes to produce distinct signals
            # and supports the UNIQUE(entity_id, signal_type, fingerprint) dedup.
            canonical_parts = [
                f"{k}\x1f{v}" for k, v in sorted(normalized_values.items())
            ]
            content_hash = hashlib.sha256(
                "\x1f".join(canonical_parts).encode("utf-8")
            ).hexdigest()

            # Composite payload for downstream Signal/Event creation.
            payload = {
                "target_id": self.target.id,
                "url": self.target.url,
                "observation_status": observation_status,
                "extracted_values": {k: v.value for k, v in extracted_results.items() if v.is_found},
                "normalized_values": normalized_values,
                "fingerprints": fingerprints,
                "content_hash": content_hash,
                "diffs": {
                    name: {
                        "changed": diff.changed,
                        "before": diff.before,
                        "after": diff.after,
                        "summary": diff.summary,
                        "regions": diff.regions,
                        # Noise-filtered values for downstream verification.
                        "noise_filtered_before": self.false_positive_guard.noise_filter.filter(diff.before),
                        "noise_filtered_after": self.false_positive_guard.noise_filter.filter(diff.after),
                    }
                    for name, diff in diffs.items()
                },
                "status_code": fetch_res.status_code,
                "captured_at": now.isoformat(),
                # False positive guard metadata.
                "false_positive_guard": {
                    "dynamic_noise_threshold": self.false_positive_guard.dynamic_noise_threshold,
                },
            }

            sig_obj = None
            if Signal is not None:
                try:
                    sig_obj = Signal(
                        id=f"sig_{self.target.id}_{int(now.timestamp())}",
                        entity_id=self.target.id,
                        signal_type=SignalType.CONTENT_CHANGE,
                        payload=payload,
                        observed_at=now,
                    )
                except Exception:
                    try:
                        sig_obj = Signal(
                            entity_id=self.target.id,
                            signal_type=SignalType.CONTENT_CHANGE,
                            payload=payload,
                            observed_at=now,
                        )
                    except Exception:
                        sig_obj = payload
            else:
                sig_obj = payload

            signals.append(sig_obj)

        # 11. Determine outcome
        if signals:
            outcome = ExecutionOutcome.SUCCESS_CHANGED
        elif has_failures:
            if all_extractors_failed:
                outcome = ExecutionOutcome.SELECTOR_NOT_FOUND
            else:
                outcome = ExecutionOutcome.TRANSFORM_ERROR
        else:
            outcome = ExecutionOutcome.SUCCESS_UNCHANGED

        return TargetExecutionResult(
            target_id=self.target.id,
            allowed=True,
            status_code=fetch_res.status_code,
            new_status=observed_status,
            signals_emitted=signals,
            extracted_results=extracted_results,
            extracted_values={k: v.value for k, v in extracted_results.items() if v.is_found},
            is_304=False,
            has_extraction_failures=has_failures,
            reason=emit_reason,
            updated_etag=updated_etag,
            updated_last_modified=updated_last_modified,
            updated_content_hash=updated_content_hash,
            updated_metadata=updated_metadata,
            updated_url=updated_url,
            consecutive_failures=observed_consecutive_failures,
            next_allowed_at=observed_next_allowed_at,
            last_fetched_at=observed_last_fetched_at,
            outcome=outcome,
            transition=transition_for(
                outcome,
                target=self.target,
                now=now,
                etag=updated_etag,
                last_modified=updated_last_modified,
                content_hash=updated_content_hash,
                metadata=updated_metadata,
                emit_signal=bool(signals),
            ),
            observation=observation,
        )
