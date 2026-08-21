import os
import json
import logging
import socket
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Union, Tuple

from web_watcher.repository import Repository
from web_watcher.models import Target, TargetStatus
from web_watcher.config import AppConfig, get_config
from web_watcher.fetcher import SmartFetcher
from web_watcher.fetch_policy import FetchPolicy
from web_watcher.generic_web_target import GenericWebTarget
from web_watcher.github_target import GitHubTarget
from web_watcher.rss_feed_target import RSSFeedTarget
from web_watcher.rule_parser import RuleParser
from web_watcher.rule_models import RuleSet, WatcherRule
from web_watcher.signal_types import SignalType

try:
    from web_watcher.event_correlator import EventCorrelator, CorrelationPlan
except ImportError:
    EventCorrelator = None
    CorrelationPlan = None

try:
    from web_watcher.cross_target_correlator import CrossTargetRule, CrossTargetCorrelator
except ImportError:
    CrossTargetRule = None
    CrossTargetCorrelator = None

try:
    from web_watcher.models import Signal
except ImportError:
    Signal = None

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
        metrics: Optional[Any] = None,
        include_tags: Optional[List[str]] = None,
        exclude_tags: Optional[List[str]] = None,
    ):
        self.config = config or get_config()
        self.repo = repo
        self.rules_path = rules_path or os.getenv("WEB_WATCHER_RULES") or getattr(self.config, "rules_path", None)
        self.fetcher = fetcher or SmartFetcher(default_timeout=getattr(self.config, "default_timeout", 10.0))
        host_limiter = None
        if repo is not None and policy is None:
            from web_watcher.host_rate_limiter import HostRateLimiter
            host_limiter = HostRateLimiter(repository=repo)
        self.policy = policy or FetchPolicy(host_rate_limiter=host_limiter)
        self._rule_cache: Dict[str, WatcherRule] = {}
        self._last_rules_mtime: Optional[float] = None
        self._last_rules_hash: Optional[str] = None
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.metrics = metrics
        self.include_tags = include_tags or []
        self.exclude_tags = exclude_tags or []
        self.registry = None
        if self.repo is not None:
            try:
                from .rule_registry import RuleRegistry
                self.registry = RuleRegistry(self.repo)
            except Exception:
                self.registry = None

        self.cross_target_correlator = None
        self._cross_target_rules_path = getattr(self.config, "cross_target_rules_path", None) or "config/cross_target_rules.yaml"
        self._last_cross_target_rules_mtime: Optional[float] = None
        try:
            rules = self._load_cross_target_rules_from_yaml(self._cross_target_rules_path)
            self.cross_target_correlator = CrossTargetCorrelator(rules=rules)
            p = Path(self._cross_target_rules_path)
            if p.exists():
                self._last_cross_target_rules_mtime = p.stat().st_mtime
        except FileNotFoundError:
            # Only tolerate missing file when the path is the default fallback
            # and the user did not explicitly configure cross_target_rules_path.
            if getattr(self.config, "cross_target_rules_path", None):
                raise
            self.cross_target_correlator = None
        except Exception:
            # Any other validation/parse error must not be silenced.
            raise

    def _inc(self, name: str, tags: Optional[Dict[str, str]] = None, amount: int = 1) -> None:
        if not self.metrics:
            return
        try:
            self.metrics.increment(name, tags=tags, amount=amount)
        except Exception:
            pass

    def _get_rules_snapshot(self, path: Optional[Union[str, Path]] = None) -> Tuple[Optional[float], Optional[str]]:
        """Get mtime and hash of rules file for change detection."""
        p = Path(path or self.rules_path)
        if not p.exists():
            return None, None
        try:
            mtime = p.stat().st_mtime
            content = p.read_bytes()
            import hashlib
            file_hash = hashlib.sha256(content).hexdigest()
            return mtime, file_hash
        except Exception:
            return None, None

    def _check_rules_changed(self, path: Optional[Union[str, Path]] = None) -> bool:
        """Check if rules file has changed since last sync."""
        if not self.rules_path:
            return False
        mtime, file_hash = self._get_rules_snapshot(path)
        if mtime is None or file_hash is None:
            return False
        if self._last_rules_mtime is None or self._last_rules_hash is None:
            return True
        return mtime != self._last_rules_mtime or file_hash != self._last_rules_hash

    def reload_rules(self, path: Optional[Union[str, Path]] = None, include_tags: Optional[List[str]] = None, exclude_tags: Optional[List[str]] = None) -> Dict[str, Any]:
        """Reload rules from YAML file with optional tag filtering.
        
        Returns dict with reload stats.
        """
        start_time = datetime.now(timezone.utc)
        path = path or self.rules_path
        if not path:
            return {"reloaded": 0, "filtered": 0, "skipped": 0}

        p = Path(path)
        if not p.exists():
            logger.warning(f"Rules file not found during reload: {p}")
            return {"reloaded": 0, "filtered": 0, "skipped": 0, "error": "file_not_found"}

        # Parse new ruleset
        try:
            ruleset = RuleParser.parse_file(p)
        except Exception as e:
            logger.error(f"Failed to parse rules file during reload: {e}")
            return {"reloaded": 0, "filtered": 0, "skipped": 0, "error": str(e)}

        # Apply tag filtering if specified
        include_tags = include_tags or []
        exclude_tags = exclude_tags or []
        filtered_rules = []
        filtered_count = 0
        
        for rule in ruleset.rules:
            rule_tags = set(getattr(rule, "tags", None) or [])
            
            # exclude 优先
            if exclude_tags and rule_tags & set(exclude_tags):
                filtered_count += 1
                continue
            
            # include 检查
            if include_tags and not (rule_tags & set(include_tags)):
                filtered_count += 1
                continue
            
            filtered_rules.append(rule)

        # Update rule cache
        old_cache_size = len(self._rule_cache)
        self._rule_cache = {rule.id: rule for rule in filtered_rules}
        new_cache_size = len(self._rule_cache)

        # Sync targets to repo if available
        synced_targets = []
        if self.repo:
            for rule in filtered_rules:
                existing = self.repo.get_target(rule.id)
                if existing is None:
                    target = Target(
                        id=rule.id,
                        url=rule.target.url,
                        interval=rule.target.interval,
                        status=TargetStatus.NORMAL,
                        tags=list(rule.tags or []),
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
                    existing.tags = list(rule.tags or [])
                    existing.metadata["rule_name"] = rule.name
                    existing.metadata["headers"] = rule.target.headers
                    existing.metadata["routing_channels"] = rule.routing.channels
                    existing.metadata["cooldown"] = rule.routing.cooldown
                    self.repo.save_target(existing)
                    synced_targets.append(existing)

        # Update snapshot
        mtime, file_hash = self._get_rules_snapshot(path)
        self._last_rules_mtime = mtime
        self._last_rules_hash = file_hash

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        return {
            "reloaded": new_cache_size,
            "filtered": filtered_count,
            "skipped": old_cache_size - new_cache_size + filtered_count,
            "synced_targets": len(synced_targets),
            "elapsed_seconds": round(elapsed, 3),
        }

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
            rule_meta = {
                "rule_name": rule.name,
                "headers": rule.target.headers,
                "routing_channels": rule.routing.channels,
                "cooldown": rule.routing.cooldown,
                "cookies": rule.target.cookies,
                "basic_auth": rule.target.basic_auth,
                "proxy": rule.target.proxy,
                "js_render": rule.target.js_render,
            }
            if existing is None:
                target = Target(
                    id=rule.id,
                    url=rule.target.url,
                    interval=rule.target.interval,
                    status=TargetStatus.NORMAL,
                    tags=list(rule.tags or []),
                    metadata=rule_meta,
                )
                self.repo.save_target(target)
                synced_targets.append(target)
            else:
                existing.url = rule.target.url
                existing.interval = rule.target.interval
                existing.tags = list(rule.tags or [])
                # Merge rule-defined metadata on top of existing runtime metadata
                # so that fields like normalized_values / initialized survive sync.
                merged_meta = dict(existing.metadata or {})
                merged_meta.update(rule_meta)
                existing.metadata = merged_meta
                self.repo.save_target(existing)
                synced_targets.append(existing)

        # Update snapshot after successful sync
        mtime, file_hash = self._get_rules_snapshot(path)
        self._last_rules_mtime = mtime
        self._last_rules_hash = file_hash

        return synced_targets

    def _load_cross_target_rules_from_yaml(self, path: Optional[Union[str, Path]] = None) -> List[CrossTargetRule]:
        """Load cross_target rules from YAML file top-level `cross_target_rules` section.

        Raises:
            FileNotFoundError: If the rules file does not exist.
            ValueError: If the YAML structure or rule content is invalid.
        """
        p = Path(path or self.rules_path or "")
        if not p.exists():
            raise FileNotFoundError(f"Cross-target rules file not found: {p}")
        try:
            import yaml
            data = yaml.safe_load(p.read_bytes())
        except Exception as exc:
            raise ValueError(f"Invalid cross_target rules YAML: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError("Cross-target rules file must be a mapping at top level")
        if "version" not in data:
            raise ValueError("Cross-target rules file missing required 'version' field")
        if "cross_target_rules" not in data:
            raise ValueError("Cross-target rules file missing 'cross_target_rules' section")

        raw = data.get("cross_target_rules")
        if not isinstance(raw, list):
            raise TypeError("'cross_target_rules' must be a list")

        rules: List[CrossTargetRule] = []
        for idx, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"Rule at index {idx} must be a mapping")
            if not item.get("name"):
                raise ValueError(f"Rule at index {idx} missing required 'name'")
            if not item.get("entity_ids"):
                raise ValueError(f"Rule '{item.get('name')}' missing required 'entity_ids'")
            try:
                rules.append(CrossTargetRule(
                    name=item.get("name", "unnamed"),
                    entity_ids=list(item.get("entity_ids", [])),
                    window_seconds=int(item.get("window_seconds", 3600)),
                    min_signals=int(item.get("min_signals", 2)),
                    importance_boost=item.get("importance_boost", "important"),
                ))
            except Exception as exc:
                raise ValueError(f"Invalid rule '{item.get('name')}': {exc}") from exc
        return rules

    def reload_cross_target_rules(self, path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """Reload cross_target rules from YAML and update correlator.

        If the new configuration is invalid, the existing active rules are kept
        and the returned dict contains an ``error`` field.
        """
        target_path = Path(path or self._cross_target_rules_path)
        previous_rules = list(self.cross_target_correlator.rules) if self.cross_target_correlator is not None else []
        try:
            new_rules = self._load_cross_target_rules_from_yaml(target_path)
        except Exception as exc:
            return {
                "reloaded": 0,
                "error": str(exc),
                "path": str(target_path),
                "kept_previous_rules": len(previous_rules),
            }
        self.cross_target_correlator = CrossTargetCorrelator(rules=new_rules)
        if target_path.exists():
            self._last_cross_target_rules_mtime = target_path.stat().st_mtime
        return {"reloaded": len(new_rules)}

    def _resolve_adapter(self, target: Target, rule: Optional[WatcherRule] = None):
        url_lower = target.url.lower()
        is_github = (
            "github.com" in url_lower
            or "api.github.com" in url_lower
            or (not url_lower.startswith("http") and "/" in url_lower and not url_lower.startswith("."))
        )
        is_feed = (
            url_lower.endswith(".rss")
            or url_lower.endswith(".atom")
            or url_lower.endswith(".xml")
            or "/feed" in url_lower
            or "feed" in target.metadata.get("target_type", "")
        )
        rule_status = getattr(rule, "status", "enabled") if rule else "enabled"

        if is_github:
            watch_types = target.metadata.get("watch_types", ["releases", "stars", "tags"])
            token = target.metadata.get("github_token") or getattr(self.config, "github_token", None)
            return GitHubTarget(
                target=target,
                watch_types=watch_types,
                token=token,
                timeout=rule.target.timeout if rule else 10.0,
                rule_status=rule_status,
            )
        elif is_feed:
            custom_headers = (rule.target.headers if rule else None) or target.metadata.get("headers", {})
            timeout = rule.target.timeout if rule else 10.0
            noise_reduction_level = getattr(self.config, "noise_reduction_level", "standard")
            return RSSFeedTarget(
                target=target,
                custom_headers=custom_headers,
                timeout=timeout,
                noise_reduction_level=noise_reduction_level,
                rule_status=rule_status,
            )
        else:
            extractors = rule.extractors if rule else []
            custom_headers = (rule.target.headers if rule else None) or target.metadata.get("headers", {})
            timeout = rule.target.timeout if rule else 10.0
            noise_reduction_level = getattr(self.config, "noise_reduction_level", "standard")
            return GenericWebTarget(
                target=target,
                extractors=extractors,
                custom_headers=custom_headers,
                timeout=timeout,
                noise_reduction_level=noise_reduction_level,
                rule_status=rule_status,
                rule=rule,
            )

    @staticmethod
    def _normalize_signal(sig: Any, target_id: str) -> Any:
        """Convert dict-based signal payloads into Signal model instances."""
        if Signal is None:
            return sig
        if isinstance(sig, Signal):
            return sig
        if isinstance(sig, dict):
            try:
                observed_at = sig.get("captured_at") or sig.get("fetched_at") or sig.get("published_at") or sig.get("updated_at")
                if isinstance(observed_at, str):
                    observed_at = datetime.fromisoformat(observed_at)
                else:
                    observed_at = observed_at or datetime.now(timezone.utc)
                signal_type = sig.get("signal_type")
                if signal_type is None:
                    signal_type = SignalType.CONTENT_CHANGE.value
                elif hasattr(signal_type, "value"):
                    signal_type = signal_type.value
                return Signal(
                    id=sig.get("id"),
                    entity_id=sig.get("target_id", target_id),
                    signal_type=signal_type,
                    observed_at=observed_at,
                    value=sig.get("value") or json.dumps(sig, ensure_ascii=False),
                    fingerprint=sig.get("content_hash") or sig.get("fingerprint"),
                )
            except (TypeError, ValueError) as exc:
                logger.debug("Signal normalization failed for %s: %s", target_id, exc)
                return sig
        return sig

    def _commit_or_release(
        self,
        target_id: str,
        claim_token: str,
        result,
        now: datetime,
    ) -> None:
        if not self.repo:
            return

        transition = getattr(result, "transition", None)
        if transition is None:
            # Fallback for legacy results without explicit transition
            new_status = getattr(result, "new_status", TargetStatus.NORMAL)
            etag = getattr(result, "updated_etag", None)
            last_modified = getattr(result, "updated_last_modified", None)
            content_hash = getattr(result, "updated_content_hash", None)
            metadata = getattr(result, "updated_metadata", None)
            consecutive_failures = getattr(result, "consecutive_failures", 0)
            next_allowed_at = getattr(result, "next_allowed_at", None)
            last_fetched_at = getattr(result, "last_fetched_at", now)

            if hasattr(self.repo, "commit_target_execution"):
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
            return

        # Build CorrelationPlan from emitted signals
        correlation_plan = None
        if EventCorrelator is not None:
            try:
                correlator = EventCorrelator(repository=self.repo)
                raw_signals = getattr(result, "signals_emitted", []) or []
                signals = [self._normalize_signal(s, target_id) for s in raw_signals]
                if signals:
                    correlation_plan = CorrelationPlan()
                    for sig in signals:
                        try:
                            plan = correlator.process_signal(sig)
                            # Merge plans
                            correlation_plan.events_to_create.extend(plan.events_to_create)
                            correlation_plan.events_to_update.extend(plan.events_to_update)
                            correlation_plan.signals_to_persist.extend(plan.signals_to_persist)
                            correlation_plan.links.extend(plan.links)
                        except Exception:
                            pass
            except Exception:
                correlation_plan = None

        if correlation_plan is not None:
            self._inc("events_created_total", {"target_id": target_id}, amount=len(getattr(correlation_plan, "events_to_create", []) or []))

        # Use finalize_execution if available (atomic finalization)
        if hasattr(self.repo, "finalize_execution"):
            raw_signals = getattr(result, "signals_emitted", []) or []
            signals = [self._normalize_signal(s, target_id) for s in raw_signals]
            committed = self.repo.finalize_execution(
                target_id=target_id,
                claim_token=claim_token,
                worker_id=self.worker_id,
                transition=transition,
                signals=signals,
                correlation_plan=correlation_plan,
                now=now,
            )
            logger.debug(f"finalize_execution committed={committed}, signals_count={len(signals)}, correlation_plan={correlation_plan}")
            if not committed:
                logger.warning(
                    f"Fenced finalize failed for target '{target_id}'; lease may have been lost.",
                    extra={"target_id": target_id, "worker_id": self.worker_id},
                )
        elif hasattr(self.repo, "commit_target_execution"):
            # Legacy fallback
            new_status = transition.status
            etag = getattr(transition, 'etag', None)
            last_modified = getattr(transition, 'last_modified', None)
            content_hash = getattr(transition, 'content_hash', None)
            metadata = getattr(transition, 'metadata', None)
            consecutive_failures = getattr(transition, 'consecutive_failures', 0)
            next_allowed_at = getattr(transition, 'next_allowed_at', None)
            last_fetched_at = getattr(transition, 'last_fetched_at', now)

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
                logger.warning(
                    f"Fenced commit failed for target '{target_id}'; lease may have been lost.",
                    extra={"target_id": target_id, "worker_id": self.worker_id},
                )

    def run_once(
        self,
        auto_deliver: bool = False,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)

        # 1. Hot reload if rules file changed
        reload_stats = None
        if self.rules_path and self._check_rules_changed():
            logger.info(f"Rules file changed, triggering hot reload: {self.rules_path}")
            reload_stats = self.reload_rules()
            if reload_stats.get("error"):
                logger.warning(f"Hot reload encountered error: {reload_stats['error']}")
            else:
                logger.info(
                    f"Hot reload completed: {reload_stats.get('reloaded', 0)} rules loaded, "
                    f"{reload_stats.get('filtered', 0)} filtered, "
                    f"{reload_stats.get('skipped', 0)} skipped, "
                    f"{reload_stats.get('synced_targets', 0)} targets synced "
                    f"in {reload_stats.get('elapsed_seconds', 0)}s"
                )

        # 1.01 Hot reload cross_target rules if changed
        if self._cross_target_rules_path:
            try:
                p = Path(self._cross_target_rules_path)
                current_mtime = p.stat().st_mtime if p.exists() else None
                if current_mtime is not None and self._last_cross_target_rules_mtime is not None:
                    if current_mtime != self._last_cross_target_rules_mtime:
                        logger.info("Cross-target rules changed, reloading")
                        self.reload_cross_target_rules(self._cross_target_rules_path)
                        self._last_cross_target_rules_mtime = current_mtime
                elif current_mtime is not None and self._last_cross_target_rules_mtime is None:
                    self._last_cross_target_rules_mtime = current_mtime
            except Exception as exc:
                logger.warning(f"Cross-target rules hot reload failed: {exc}")

        # 1. 自动同步 YAML 规则
        if self.rules_path:
            self.sync_rules(self.rules_path)

        # 1.1 Reap stale host claims so crashed workers do not block future requests.
        if self.repo is not None and hasattr(self.repo, "reap_stale_claims"):
            try:
                self.repo.reap_stale_claims(older_than=now)
            except Exception:
                pass

        # 1.2 Tag filtering (before execution)
        rules_filtered = 0
        if self.repo and (getattr(self, "include_tags", None) or getattr(self, "exclude_tags", None)):
            # Get all targets from repo
            all_targets = self.repo.list_targets()
            filtered_targets = []
            include_tags = getattr(self, "include_tags", None) or []
            exclude_tags = getattr(self, "exclude_tags", None) or []
            
            for t in all_targets:
                target_tags = list(getattr(t, "tags", None) or [])
                
                # exclude 优先
                if exclude_tags and any(tag in target_tags for tag in exclude_tags):
                    rules_filtered += 1
                    continue
                
                # include 检查
                if include_tags and not any(tag in target_tags for tag in include_tags):
                    rules_filtered += 1
                    continue
                
                filtered_targets.append(t)
            
            # Replace rule cache with filtered rules
            if include_tags or exclude_tags:
                filtered_rule_ids = {t.id for t in filtered_targets}
                self._rule_cache = {k: v for k, v in self._rule_cache.items() if k in filtered_rule_ids}

        # 1.3 Rule registry filtering (after tag filtering)
        registry_filtered = 0
        if self.registry is not None and self._rule_cache:
            all_registry_rules = self.registry.list_rules()
            registry_map = {r["rule_id"]: r for r in all_registry_rules}

            filtered_cache = {}
            for r_id, r in self._rule_cache.items():
                if r_id in registry_map:
                    if registry_map[r_id]["enabled"]:
                        filtered_cache[r_id] = r
                    else:
                        registry_filtered += 1
                else:
                    # Not in registry = default enabled
                    filtered_cache[r_id] = r

            self._rule_cache = filtered_cache
            # Sort by priority (descending); missing registry entry = priority 0
            self._rule_cache = dict(sorted(
                self._rule_cache.items(),
                key=lambda item: registry_map.get(item[0], {}).get("priority", 0),
                reverse=True,
            ))

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

        # 2.1 Filter claimed targets against rule cache (post tag/registry filtering)
        # and release leases for filtered-out targets
        filtered_claimed = []
        for target in claimed:
            if target.id in self._rule_cache:
                filtered_claimed.append(target)
            elif hasattr(self.repo, "release_target_lease") and getattr(target, "claim_token", None):
                try:
                    self.repo.release_target_lease(target.id, target.claim_token, now=now)
                except Exception:
                    pass
        claimed = filtered_claimed

        # 2.2 Sort claimed targets by registry priority (descending)
        if self.registry is not None and claimed:
            all_registry_rules = self.registry.list_rules()
            registry_map = {r["rule_id"]: r for r in all_registry_rules}
            claimed.sort(
                key=lambda t: registry_map.get(t.id, {}).get("priority", 0),
                reverse=True,
            )

        summary = {
            "targets_evaluated": len(claimed),
            "signals_emitted": 0,
            "is_304_count": 0,
            "skipped_count": 0,
            "errors": [],
            "rules_filtered": rules_filtered,
            "registry_filtered": registry_filtered,
            "reload": reload_stats,
        }
        logger.debug(f"claimed count={len(claimed)}, ids={[c.id for c in claimed]}, rules_filtered={rules_filtered}")

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

                self._inc("fetch_total", {"target_id": target.id})
                if getattr(result, "is_304", False):
                    summary["is_304_count"] += 1
                    self._inc("fetch_304_total", {"target_id": target.id})

                status_code = getattr(result, "status_code", None)
                if status_code == 429:
                    self._inc("fetch_429_total", {"target_id": target.id})
                elif status_code == 403:
                    self._inc("fetch_403_total", {"target_id": target.id})

                if result.signals_emitted:
                    summary["signals_emitted"] += len(result.signals_emitted)
                    all_signals.extend(result.signals_emitted)
                    self._inc("signals_created_total", {"target_id": target.id}, amount=len(result.signals_emitted))

                if claim_token:
                    self._commit_or_release(target.id, claim_token, result, now)
                else:
                    # Unclaimed target (fallback path): nothing to commit or release
                    pass

            except Exception as e:
                self._inc("fetch_error_total", {"target_id": target.id})
                logger.error(
                    f"Error evaluating target '{target.id}': {e}",
                    exc_info=True,
                    extra={"target_id": target.id, "worker_id": self.worker_id},
                )
                summary["errors"].append({"target_id": target.id, "error": str(e)})
                if claim_token and hasattr(self.repo, "release_target_lease"):
                    try:
                        self.repo.release_target_lease(target.id, claim_token, now=now)
                    except Exception:
                        pass

        # 4. Cross-target correlation
        cross_target_groups: List[Any] = []
        if self.cross_target_correlator is not None and self.repo is not None:
            try:
                cutoff = now - timedelta(minutes=10)
                signals = self.repo.list_signals(created_after=cutoff, created_before=now, limit=1000)
                signal_tuples = []
                for s in signals:
                    sig_type = s.signal_type.value if hasattr(s.signal_type, "value") else str(s.signal_type)
                    signal_tuples.append((s.entity_id, sig_type, s.observed_at, s.value or "", s.fingerprint or "", None, s.id))
                cross_target_groups = self.cross_target_correlator.evaluate_signals(signal_tuples, now=now)
                for group in cross_target_groups:
                    try:
                        entity_key = f"cross_target:{group.rule_name}:{'-'.join(sorted(group.entity_ids))}"
                        entity = self.repo.get_or_create_entity(
                            canonical_key=entity_key,
                            name=group.rule_name,
                            entity_type="cross_target",
                        )

                        # Dedup/merge: find the most recent open cross_target event for this entity
                        existing_event = self.repo.find_open_event_for_entity(
                            entity_id=entity.id,
                            cutoff=now - timedelta(hours=24),
                            event_type="cross_target",
                        )

                        if existing_event is not None:
                            existing_meta = {}
                            row = self.repo.connection.execute(
                                "SELECT metadata_json FROM events WHERE id = ?",
                                (existing_event.id,),
                            ).fetchone()
                            if row and row["metadata_json"]:
                                import json as _json
                                existing_meta = _json.loads(row["metadata_json"]) or {}

                            existing_window_end = existing_meta.get("window_end")
                            if existing_window_end:
                                try:
                                    existing_end = datetime.fromisoformat(existing_window_end)
                                    if group.window_start <= existing_end + timedelta(minutes=10):
                                        # Merge into existing event
                                        merged_entity_ids = list(set(existing_meta.get("entity_ids", []) + list(group.entity_ids)))
                                        merged_signal_count = int(existing_meta.get("signal_count", 0)) + len(group.signals)
                                        merged_metadata = dict(existing_meta)
                                        merged_metadata.update({
                                            "entity_ids": merged_entity_ids,
                                            "signal_count": merged_signal_count,
                                            "window_start": min(
                                                datetime.fromisoformat(existing_meta.get("window_start", group.window_start.isoformat())),
                                                group.window_start,
                                            ).isoformat(),
                                            "window_end": max(
                                                datetime.fromisoformat(existing_meta.get("window_end", group.window_end.isoformat())),
                                                group.window_end,
                                            ).isoformat(),
                                            "source": "signal_based",
                                            "correlation_type": "signal_based",
                                        })
                                        self.repo.connection.execute(
                                            "UPDATE events SET metadata_json = ?, updated_at = ? WHERE id = ?",
                                            (
                                                json.dumps(merged_metadata, default=str),
                                                now.isoformat(),
                                                existing_event.id,
                                            ),
                                        )
                                        self.repo.connection.commit()
                                        for sig in group.signals:
                                            if sig.signal_id is not None:
                                                self.repo.attach_signal_to_event(existing_event.id, sig.signal_id)
                                        event = existing_event
                                    else:
                                        event = self.repo.create_event(
                                            entity_id=entity.id,
                                            event_type="cross_target",
                                            status="open",
                                            importance=group.importance,
                                            created_at=now,
                                            metadata={
                                                "rule_name": group.rule_name,
                                                "entity_ids": list(group.entity_ids),
                                                "signal_count": len(group.signals),
                                                "window_start": group.window_start.isoformat(),
                                                "window_end": group.window_end.isoformat(),
                                                "source": "signal_based",
                                                "correlation_type": "signal_based",
                                            },
                                        )
                                        for sig in group.signals:
                                            if sig.signal_id is not None:
                                                self.repo.attach_signal_to_event(event.id, sig.signal_id)
                                except Exception:
                                    event = self.repo.create_event(
                                        entity_id=entity.id,
                                        event_type="cross_target",
                                        status="open",
                                        importance=group.importance,
                                        created_at=now,
                                        metadata={
                                            "rule_name": group.rule_name,
                                            "entity_ids": list(group.entity_ids),
                                            "signal_count": len(group.signals),
                                            "window_start": group.window_start.isoformat(),
                                            "window_end": group.window_end.isoformat(),
                                            "source": "signal_based",
                                            "correlation_type": "signal_based",
                                        },
                                    )
                                    for sig in group.signals:
                                        if sig.signal_id is not None:
                                            self.repo.attach_signal_to_event(event.id, sig.signal_id)
                            else:
                                event = self.repo.create_event(
                                    entity_id=entity.id,
                                    event_type="cross_target",
                                    status="open",
                                    importance=group.importance,
                                    created_at=now,
                                    metadata={
                                        "rule_name": group.rule_name,
                                        "entity_ids": list(group.entity_ids),
                                        "signal_count": len(group.signals),
                                        "window_start": group.window_start.isoformat(),
                                        "window_end": group.window_end.isoformat(),
                                        "source": "signal_based",
                                        "correlation_type": "signal_based",
                                    },
                                )
                                for sig in group.signals:
                                    if sig.signal_id is not None:
                                        self.repo.attach_signal_to_event(event.id, sig.signal_id)
                        else:
                            event = self.repo.create_event(
                                entity_id=entity.id,
                                event_type="cross_target",
                                status="open",
                                importance=group.importance,
                                created_at=now,
                                metadata={
                                    "rule_name": group.rule_name,
                                    "entity_ids": list(group.entity_ids),
                                    "signal_count": len(group.signals),
                                    "window_start": group.window_start.isoformat(),
                                    "window_end": group.window_end.isoformat(),
                                    "source": "signal_based",
                                    "correlation_type": "signal_based",
                                },
                            )
                            for sig in group.signals:
                                if sig.signal_id is not None:
                                    self.repo.attach_signal_to_event(event.id, sig.signal_id)

                        channels = ["console"]
                        if getattr(self.config, "webhook_url", None):
                            channels.append("webhook")
                        if getattr(self.config, "smtp_host", None):
                            channels.append("email")

                        for ch in channels:
                            try:
                                self.repo.create_notification(
                                    event_id=event.id,
                                    channel=ch,
                                    status="pending",
                                    payload={
                                        "rule_name": group.rule_name,
                                        "entity_ids": list(group.entity_ids),
                                        "signal_count": len(group.signals),
                                        "importance": group.importance,
                                        "window_start": group.window_start.isoformat(),
                                        "window_end": group.window_end.isoformat(),
                                        "has_investigation": False,
                                    },
                                )
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning(f"Cross-target correlation failed: {exc}")

        # 4.1 Event-based cross-target correlation
        if self.cross_target_correlator is not None and self.repo is not None:
            try:
                event_cutoff = now - timedelta(hours=24)
                rows = self.repo.connection.execute(
                    """
                    SELECT e.id, e.entity_id, e.event_type, e.status, e.created_at, e.updated_at
                    FROM events e
                    WHERE e.event_type = ? AND e.created_at >= ?
                    ORDER BY e.created_at DESC
                    LIMIT 500
                    """,
                    ("cross_target", event_cutoff.isoformat()),
                ).fetchall()
                event_tuples = []
                for r in rows:
                    event_tuples.append((r["id"], r["entity_id"], r["event_type"], r["status"], datetime.fromisoformat(r["created_at"]), datetime.fromisoformat(r["updated_at"])))
                event_groups = self.cross_target_correlator.evaluate_events(event_tuples, now=now)
                for group in event_groups:
                    try:
                        entity_key = f"cross_target_event:{group.rule_name}:{'-'.join(sorted(group.entity_ids))}"
                        entity = self.repo.get_or_create_entity(
                            canonical_key=entity_key,
                            name=group.rule_name,
                            entity_type="cross_target",
                        )
                        source_event_ids = [sig.event_id for sig in group.signals if sig.event_id is not None]
                        event = self.repo.create_event(
                            entity_id=entity.id,
                            event_type="cross_target",
                            status="open",
                            importance=group.importance,
                            created_at=now,
                            metadata={
                                "rule_name": group.rule_name,
                                "entity_ids": list(group.entity_ids),
                                "signal_count": len(group.signals),
                                "window_start": group.window_start.isoformat(),
                                "window_end": group.window_end.isoformat(),
                                "source": "event_based",
                                "event_ids": source_event_ids,
                                "correlation_type": "event_based",
                            },
                        )
                        self.repo.create_notification(
                            event_id=event.id,
                            channel="console",
                            status="pending",
                            payload={
                                "rule_name": group.rule_name,
                                "entity_ids": list(group.entity_ids),
                                "signal_count": len(group.signals),
                                "importance": group.importance,
                                "window_start": group.window_start.isoformat(),
                                "window_end": group.window_end.isoformat(),
                                "has_investigation": False,
                            },
                        )
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning(f"Event-based cross-target correlation failed: {exc}")

        # 5. 自动外发通知
        if auto_deliver and self.repo and NotificationDispatcher:
            try:
                dispatcher = NotificationDispatcher(repository=self.repo, config=self.config)
                dispatcher.run_once()
            except Exception as e:
                logger.warning(f"Auto delivery failed: {e}")

        return summary
