import os
import logging
import socket
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Union

from web_watcher.repository import Repository
from web_watcher.models import Target, TargetStatus
from web_watcher.config import AppConfig, get_config
from web_watcher.fetcher import SmartFetcher
from web_watcher.fetch_policy import FetchPolicy
from web_watcher.generic_web_target import GenericWebTarget
from web_watcher.github_target import GitHubTarget
from web_watcher.rule_parser import RuleParser
from web_watcher.rule_models import RuleSet, WatcherRule

try:
    from web_watcher.event_correlator import EventCorrelator
except ImportError:
    EventCorrelator = None

try:
    from web_watcher.notification_dispatcher import NotificationDispatcher
except ImportError:
    NotificationDispatcher = None

logger = logging.getLogger(__name__)


class ScheduledRunner:
    """自动化巡检管道调度中枢：
    1. 规则同步：从 YAML 规则文件自动同步 Target 元数据至 SQLite 仓储
    2. 调度执行：依据 TargetStatus 与调度周期，分发至 GenericWebTarget 或 GitHubTarget 适配器
    3. 领域闭环：收集 Signal -> 关联聚合 Event -> 触发 NotificationDispatcher 派发
    """

    def __init__(
        self,
        repo: Optional[Repository] = None,
        config: Optional[AppConfig] = None,
        rules_path: Optional[Union[str, Path]] = None,
        fetcher: Optional[SmartFetcher] = None,
        policy: Optional[FetchPolicy] = None,
        worker_id: Optional[str] = None,
    ):
        self.config = config or get_config()
        self.repo = repo
        self.rules_path = rules_path or os.getenv("WEB_WATCHER_RULES") or getattr(self.config, "rules_path", None)
        self.fetcher = fetcher or SmartFetcher(default_timeout=getattr(self.config, "default_timeout", 10.0))
        self.policy = policy or FetchPolicy()
        self._rule_cache: Dict[str, WatcherRule] = {}
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"

    def sync_rules(self, rules_path: Optional[Union[str, Path]] = None) -> List[Target]:
        path = rules_path or self.rules_path
        if not path:
            return []

        p = Path(path)
        if not p.exists():
            logger.warning(f"Rules file not found: {p}")
            return []

        ruleset = RuleParser.parse_file(p)
        synced_targets: List[Target] = []

        for rule in ruleset.rules:
            self._rule_cache[rule.id] = rule
            if not self.repo:
                continue

            existing = self.repo.get_target(rule.id)
            if existing is None:
                target = Target(
                    id=rule.id,
                    url=rule.target.url,
                    interval=rule.target.interval,
                    status=TargetStatus.NORMAL,
                    metadata={
                        "rule_name": rule.name,
                        "headers": rule.target.headers,
                        "routing_channels": rule.routing.channels,
                        "cooldown": rule.routing.cooldown,
                    },
                )
                self.repo.save_target(target)
                synced_targets.append(target)
            else:
                existing.url = rule.target.url
                existing.interval = rule.target.interval
                existing.metadata["rule_name"] = rule.name
                existing.metadata["headers"] = rule.target.headers
                existing.metadata["routing_channels"] = rule.routing.channels
                existing.metadata["cooldown"] = rule.routing.cooldown
                self.repo.save_target(existing)
                synced_targets.append(existing)

        return synced_targets

    def _resolve_adapter(self, target: Target, rule: Optional[WatcherRule] = None):
        url_lower = target.url.lower()
        is_github = (
            "github.com" in url_lower
            or "api.github.com" in url_lower
            or (not url_lower.startswith("http") and "/" in url_lower and not url_lower.startswith("."))
        )

        if is_github:
            watch_types = target.metadata.get("watch_types", ["releases", "stars", "tags"])
            token = target.metadata.get("github_token") or getattr(self.config, "github_token", None)
            return GitHubTarget(
                target=target,
                watch_types=watch_types,
                token=token,
                timeout=rule.target.timeout if rule else 10.0,
            )
        else:
            extractors = rule.extractors if rule else []
            custom_headers = (rule.target.headers if rule else None) or target.metadata.get("headers", {})
            timeout = rule.target.timeout if rule else 10.0
            return GenericWebTarget(
                target=target,
                extractors=extractors,
                custom_headers=custom_headers,
                timeout=timeout,
            )

    def _commit_or_release(
        self,
        target_id: str,
        claim_token: str,
        result,
        now: datetime,
    ) -> None:
        if not self.repo or not hasattr(self.repo, "commit_target_execution"):
            return

        new_status = getattr(result, "new_status", TargetStatus.NORMAL)
        etag = getattr(result, "updated_etag", None)
        last_modified = getattr(result, "updated_last_modified", None)
        content_hash = getattr(result, "updated_content_hash", None)
        metadata = getattr(result, "updated_metadata", None)
        consecutive_failures = getattr(result, "consecutive_failures", 0)
        next_allowed_at = getattr(result, "next_allowed_at", None)
        last_fetched_at = getattr(result, "last_fetched_at", now)

        committed = self.repo.commit_target_execution(
            target_id=target_id,
            claim_token=claim_token,
            new_status=new_status,
            etag=etag,
            last_modified=last_modified,
            content_hash=content_hash,
            consecutive_failures=consecutive_failures,
            next_allowed_at=next_allowed_at,
            metadata=metadata,
            now=now,
        )
        if not committed:
            logger.warning(f"Fenced commit failed for target '{target_id}'; lease may have been lost.")

    def run_once(
        self,
        auto_deliver: bool = False,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        now = now or datetime.utcnow()

        # 1. 自动同步 YAML 规则
        if self.rules_path:
            self.sync_rules(self.rules_path)

        # 2. Claim：生产路径必须通过 lease/fencing
        claimed: List[Any] = []
        if self.repo and hasattr(self.repo, "claim_targets"):
            claimed = self.repo.claim_targets(
                worker_id=self.worker_id,
                limit=100,
                lease_duration_sec=300.0,
                now=now,
            )
        elif self._rule_cache:
            for r_id, r in self._rule_cache.items():
                claimed.append(Target(id=r.id, url=r.target.url, interval=r.target.interval))

        summary = {
            "targets_evaluated": len(claimed),
            "signals_emitted": 0,
            "is_304_count": 0,
            "skipped_count": 0,
            "errors": [],
        }

        all_signals = []

        # 3. 逐个执行适配器并做 fenced persistence
        for target in claimed:
            rule = self._rule_cache.get(target.id)
            adapter = self._resolve_adapter(target, rule)
            claim_token = getattr(target, "claim_token", None)

            try:
                result = adapter.execute(
                    fetcher=self.fetcher,
                    policy=self.policy,
                    repo=self.repo,
                    now=now,
                )
                if not result.allowed:
                    summary["skipped_count"] += 1
                    if claim_token and hasattr(self.repo, "release_target_lease"):
                        self.repo.release_target_lease(target.id, claim_token, now=now)
                    continue

                if getattr(result, "is_304", False):
                    summary["is_304_count"] += 1

                if result.signals_emitted:
                    summary["signals_emitted"] += len(result.signals_emitted)
                    all_signals.extend(result.signals_emitted)

                if claim_token and hasattr(self.repo, "commit_target_execution"):
                    self._commit_or_release(target.id, claim_token, result, now)
                elif claim_token and hasattr(self.repo, "release_target_lease"):
                    self.repo.release_target_lease(target.id, claim_token, now=now)

            except Exception as e:
                logger.error(f"Error evaluating target '{target.id}': {e}", exc_info=True)
                summary["errors"].append({"target_id": target.id, "error": str(e)})
                if claim_token and hasattr(self.repo, "release_target_lease"):
                    try:
                        self.repo.release_target_lease(target.id, claim_token, now=now)
                    except Exception:
                        pass

        # 4. 信号聚合与事件关联
        if self.repo and EventCorrelator and all_signals:
            try:
                correlator = EventCorrelator(repository=self.repo)
                if hasattr(correlator, "correlate_signals"):
                    correlator.correlate_signals(all_signals)
                elif hasattr(correlator, "process_pending_signals"):
                    correlator.process_pending_signals()
            except Exception as e:
                logger.warning(f"Event correlation failed: {e}")

        # 5. 自动外发通知
        if auto_deliver and self.repo and NotificationDispatcher:
            try:
                dispatcher = NotificationDispatcher(repository=self.repo, config=self.config)
                dispatcher.run_once()
            except Exception as e:
                logger.warning(f"Auto delivery failed: {e}")

        return summary
