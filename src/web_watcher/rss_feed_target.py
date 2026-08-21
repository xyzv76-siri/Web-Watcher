import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from web_watcher.models import Target, TargetStatus
from web_watcher.fetch_policy import FetchPolicy
from web_watcher.fetcher import SmartFetcher, FetchResult
from web_watcher.fetch import FetchStatus
from web_watcher.signal_types import SignalType
from web_watcher.execution_semantics import ExecutionOutcome, transition_for
from web_watcher.targets import _validate_url
from web_watcher.generic_web_target import TargetExecutionResult
from web_watcher.observation import ObservationResult, ObservationStatus
from web_watcher.dynamic_noise import FalsePositiveGuard, NoiseReductionLevel

logger = logging.getLogger(__name__)

try:
    from web_watcher.models import Signal
except ImportError:
    Signal = None


@dataclass
class FeedEntry:
    id: str
    title: str
    link: str
    published_at: Optional[str]
    content: str
    content_hash: str


class RSSFeedTarget:
    """RSS/Atom feed target adapter.

    Parses RSS 2.0 and Atom 1.0 feeds, tracks entries by GUID/ID,
    and emits signals for new or updated entries.
    """

    def __init__(
        self,
        target: Target,
        custom_headers: Optional[Dict[str, Any]] = None,
        timeout: float = 10.0,
        noise_reduction_level: str = "standard",
        rule_status: str = "enabled",
    ):
        _validate_url(target.url)
        self.target = target
        self.custom_headers = custom_headers or {}
        self.timeout = timeout
        self.false_positive_guard = FalsePositiveGuard(
            level=NoiseReductionLevel(noise_reduction_level),
        )
        self.rule_status = rule_status

    @staticmethod
    def _parse_rss(content: str) -> List[FeedEntry]:
        """Parse RSS 2.0 feed content."""
        entries = []
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return entries

        # RSS 2.0: <rss><channel><item>...</item></channel></rss>
        for item in root.iter("item"):
            title = ""
            link = ""
            entry_id = ""
            pub_date = ""
            description = ""

            title_el = item.find("title")
            if title_el is not None and title_el.text:
                title = title_el.text.strip()

            link_el = item.find("link")
            if link_el is not None and link_el.text:
                link = link_el.text.strip()

            guid_el = item.find("guid")
            if guid_el is not None and guid_el.text:
                entry_id = guid_el.text.strip()
            else:
                entry_id = link or title

            pub_date_el = item.find("pubDate")
            if pub_date_el is not None and pub_date_el.text:
                pub_date = pub_date_el.text.strip()

            desc_el = item.find("description")
            if desc_el is not None and desc_el.text:
                description = desc_el.text.strip()

            content_hash = hashlib.sha256(f"{title}\x1f{link}\x1f{description}".encode()).hexdigest()
            entries.append(FeedEntry(
                id=entry_id,
                title=title,
                link=link,
                published_at=pub_date,
                content=description,
                content_hash=content_hash,
            ))

        return entries

    @staticmethod
    def _parse_atom(content: str) -> List[FeedEntry]:
        """Parse Atom 1.0 feed content."""
        entries = []
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return entries

        # Atom 1.0: <feed><entry>...</entry></feed>
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = ""
            link = ""
            entry_id = ""
            updated = ""
            content = ""

            title_el = entry.find("atom:title", ns)
            if title_el is not None and title_el.text:
                title = title_el.text.strip()

            link_el = entry.find("atom:link", ns)
            if link_el is not None and link_el.get("href"):
                link = link_el.get("href").strip()

            id_el = entry.find("atom:id", ns)
            if id_el is not None and id_el.text:
                entry_id = id_el.text.strip()
            else:
                entry_id = link or title

            updated_el = entry.find("atom:updated", ns)
            if updated_el is not None and updated_el.text:
                updated = updated_el.text.strip()

            content_el = entry.find("atom:content", ns)
            if content_el is not None and content_el.text:
                content = content_el.text.strip()
            else:
                summary_el = entry.find("atom:summary", ns)
                if summary_el is not None and summary_el.text:
                    content = summary_el.text.strip()

            content_hash = hashlib.sha256(f"{title}\x1f{link}\x1f{content}".encode()).hexdigest()
            entries.append(FeedEntry(
                id=entry_id,
                title=title,
                link=link,
                published_at=updated,
                content=content,
                content_hash=content_hash,
            ))

        return entries

    @staticmethod
    def parse_feed(content: str) -> List[FeedEntry]:
        """Parse RSS 2.0 or Atom 1.0 feed content."""
        # Try RSS first (no namespace)
        entries = RSSFeedTarget._parse_rss(content)
        if entries:
            return entries
        # Try Atom
        entries = RSSFeedTarget._parse_atom(content)
        return entries

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

        if self.rule_status == "disabled":
            return TargetExecutionResult(
                target_id=self.target.id,
                allowed=False,
                status_code=None,
                new_status=self.target.status,
                signals_emitted=[],
                extracted_results={},
                extracted_values={},
                reason="Rule disabled",
                outcome=ExecutionOutcome.POLICY_BLOCKED,
                transition=transition_for(
                    ExecutionOutcome.POLICY_BLOCKED,
                    target=self.target,
                    now=now,
                    reason="Rule disabled",
                ),
            )

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

        meta = dict(self.target.metadata or {})
        cookies = meta.get("cookies") or {}
        basic_auth = meta.get("basic_auth")
        proxy = meta.get("proxy")

        try:
            fetch_res: FetchResult = fetcher.fetch(
                url=self.target.url,
                custom_headers=headers_to_send,
                etag=self.target.etag,
                last_modified=self.target.last_modified,
                timeout=self.timeout,
                cookies=cookies or None,
                auth=tuple(basic_auth.values()) if basic_auth and isinstance(basic_auth, dict) else None,
                proxy=proxy,
            )
        finally:
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

        # 4. Collect observation-only state updates
        observed_status = evaluation.new_status
        updated_etag = evaluation.updated_etag
        updated_last_modified = evaluation.updated_last_modified
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
                    reason="HTTP 304 Not Modified; short-circuited without extraction",
                    evidence={"http_status": 304},
                    observed_at=now,
                ),
            )

        # 6. Non-success status: return observation without extraction
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

        # 7. Parse feed
        entries = []
        parse_error = None
        try:
            entries = self.parse_feed(fetch_res.content)
        except Exception as exc:
            parse_error = str(exc)

        extracted_results = {
            "entries": {
                "status": "found" if entries else "not_found",
                "raw_value": fetch_res.content[:1000] if not entries else f"{len(entries)} entries",
                "normalized_value": f"{len(entries)} entries",
                "fingerprint": "",
                "previous_value": "",
                "changed": bool(entries),
                "diff_summary": f"Feed contains {len(entries)} entries",
                "selector_type": "feed",
                "selector": self.target.url,
                "parse_error": parse_error,
            }
        }

        # 8. Determine new/updated entries
        stored_entries = {}
        if isinstance(self.target.metadata, dict):
            stored_raw = self.target.metadata.get("feed_entries")
            if isinstance(stored_raw, dict):
                stored_entries = stored_raw

        new_signals: List[Any] = []
        updated_metadata = dict(self.target.metadata or {})
        updated_content_hash = self.target.content_hash

        # Build lookup of stored entries
        seen_ids = set()
        for entry in entries:
            entry_id = entry.id or entry.link or entry.title
            seen_ids.add(entry_id)
            stored = stored_entries.get(entry_id)
            is_new = stored is None
            is_updated = stored is not None and stored.get("content_hash") != entry.content_hash

            if is_new or is_updated:
                payload = {
                    "target_id": self.target.id,
                    "url": self.target.url,
                    "observation_status": ObservationStatus.CHANGED,
                    "feed_entry": {
                        "id": entry.id,
                        "title": entry.title,
                        "link": entry.link,
                        "published_at": entry.published_at,
                        "content": entry.content,
                    },
                    "change_type": "new_entry" if is_new else "updated_entry",
                    "status_code": fetch_res.status_code,
                    "captured_at": now.isoformat(),
                }

                sig_obj = None
                if Signal is not None:
                    try:
                        sig_obj = Signal(
                            id=f"sig_{self.target.id}_{entry_id}_{int(now.timestamp())}",
                            entity_id=self.target.id,
                            signal_type=SignalType.CONTENT_CHANGE,
                            value=json.dumps(payload, ensure_ascii=False),
                            observed_at=now,
                            fingerprint=entry.content_hash,
                        )
                    except (TypeError, ValueError) as exc:
                        logger.debug("Signal construction failed for %s: %s", entry_id, exc)
                        sig_obj = payload
                else:
                    sig_obj = payload
                new_signals.append(sig_obj)

        # 9. Update stored entries (keep last 50)
        new_stored = {}
        for entry in entries[:50]:
            entry_id = entry.id or entry.link or entry.title
            new_stored[entry_id] = {
                "id": entry.id,
                "title": entry.title,
                "link": entry.link,
                "published_at": entry.published_at,
                "content": entry.content,
                "content_hash": entry.content_hash,
            }
        updated_metadata["feed_entries"] = new_stored
        updated_metadata["last_feed_check"] = now.isoformat()
        if not updated_metadata.get("initialized"):
            updated_metadata["initialized"] = True

        # 10. Determine observation status
        if not entries:
            observation_status = ObservationStatus.EXTRACTION_FAILURE
            emit_reason = "Feed parsing returned no entries"
            outcome = ExecutionOutcome.SUCCESS_UNCHANGED
        elif new_signals:
            observation_status = ObservationStatus.CHANGED
            emit_reason = f"{len(new_signals)} new/updated feed entries(s)"
            outcome = ExecutionOutcome.SUCCESS_CHANGED
        else:
            observation_status = ObservationStatus.UNCHANGED
            emit_reason = "Feed unchanged"
            outcome = ExecutionOutcome.SUCCESS_UNCHANGED

        observation = ObservationResult(
            target_id=self.target.id,
            status=observation_status,
            status_code=fetch_res.status_code,
            extracted_results=extracted_results,
            normalized_values={"entries": f"{len(entries)} entries"},
            fingerprints={"entries": hashlib.sha256(fetch_res.content.encode()).hexdigest()},
            diffs={},
            previous_values={"entries": f"{len(stored_entries)} stored entries"},
            evidence={
                "target_id": self.target.id,
                "url": self.target.url,
                "status_code": fetch_res.status_code,
                "observed_at": now.isoformat(),
                "feed_entries_count": len(entries),
                "new_signals_count": len(new_signals),
                "parse_error": parse_error,
            },
            observed_at=now,
            reason=emit_reason,
        )

        return TargetExecutionResult(
            target_id=self.target.id,
            allowed=True,
            status_code=fetch_res.status_code,
            new_status=observed_status,
            signals_emitted=new_signals,
            extracted_results=extracted_results,
            extracted_values={"entries": [e.id for e in entries]},
            has_extraction_failures=not entries,
            reason=emit_reason,
            updated_etag=updated_etag,
            updated_last_modified=updated_last_modified,
            updated_content_hash=updated_content_hash,
            updated_metadata=updated_metadata,
            updated_url=None,
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
                emit_signal=bool(new_signals),
            ),
            observation=observation,
        )
