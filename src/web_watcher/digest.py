from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .repository import Repository, Event
from .importance import Importance


@dataclass
class TargetDigest:
    target_id: str
    entity_id: int
    event_count: int
    importance_dist: Dict[str, int]
    latest_event_at: Optional[datetime]
    latest_summary: str
    events: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DigestReport:
    time_range: Tuple[datetime, datetime]
    total_events: int
    targets: Dict[str, TargetDigest]
    importance_distribution: Dict[str, int]
    summary: str

    def to_markdown(self) -> str:
        lines = [
            "# Digest Report",
            "",
            f"**Time Range:** {self.time_range[0].isoformat()} — {self.time_range[1].isoformat()}",
            "",
            f"**Summary:** {self.summary}",
            "",
            "## Overview",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total Events | {self.total_events} |",
        ]
        for imp, count in sorted(self.importance_distribution.items()):
            lines.append(f"| {imp} | {count} |")

        lines.append("")
        lines.append("## By Target")
        lines.append("")

        for target_id, digest in sorted(self.targets.items()):
            lines.append(f"### {target_id}")
            lines.append("")
            lines.append(f"- Events: {digest.event_count}")
            latest = digest.latest_event_at.isoformat() if digest.latest_event_at else "N/A"
            lines.append(f"- Latest: {latest}")
            lines.append(f"- Latest summary: {digest.latest_summary}")
            lines.append("")

        return "\n".join(lines)


class DigestBuilder:
    def __init__(self, repo: Repository):
        self.repo = repo

    def build(
        self,
        since: datetime,
        until: Optional[datetime] = None,
        min_importance: Importance = Importance.INTERESTING,
    ) -> DigestReport:
        if until is None:
            until = datetime.now()

        # 1. 查询事件（list_events 不支持 until，需在 Python 中过滤）
        events = self.repo.list_events(since=since, limit=1000)
        events = [e for e in events if e.created_at <= until]

        # 2. 过滤重要性
        importance_order = {
            Importance.IGNORE: 0,
            Importance.INTERESTING: 1,
            Importance.IMPORTANT: 2,
            Importance.CRITICAL: 3,
        }
        min_level = importance_order.get(min_importance, 1)
        events = [e for e in events if importance_order.get(e.importance, 0) >= min_level]

        # 3. 建立 entity_id -> canonical_key 映射
        entity_rows = self.repo.connection.execute(
            "SELECT id, canonical_key FROM entities"
        ).fetchall()
        entity_map = {r["id"]: r["canonical_key"] for r in entity_rows}

        # 4. 按 target 分组
        targets: Dict[str, TargetDigest] = {}
        for event in events:
            target_id = entity_map.get(event.entity_id, f"entity_{event.entity_id}")
            if target_id not in targets:
                targets[target_id] = TargetDigest(
                    target_id=target_id,
                    entity_id=event.entity_id,
                    event_count=0,
                    importance_dist={},
                    latest_event_at=None,
                    latest_summary="",
                    events=[],
                )
            digest = targets[target_id]
            digest.event_count += 1
            imp_str = str(event.importance)
            digest.importance_dist[imp_str] = digest.importance_dist.get(imp_str, 0) + 1
            if digest.latest_event_at is None or event.created_at > digest.latest_event_at:
                digest.latest_event_at = event.created_at
            summary = self._summarize_event(event)
            digest.events.append({
                "id": event.id,
                "event_type": str(event.event_type),
                "importance": imp_str,
                "created_at": event.created_at.isoformat(),
                "summary": summary,
            })
            digest.latest_summary = summary

        # 5. 总体统计
        importance_distribution: Dict[str, int] = {}
        for digest in targets.values():
            for imp, count in digest.importance_dist.items():
                importance_distribution[imp] = importance_distribution.get(imp, 0) + count

        total_events = sum(d.event_count for d in targets.values())
        summary = self._generate_summary(total_events, targets, importance_distribution)

        return DigestReport(
            time_range=(since, until),
            total_events=total_events,
            targets=targets,
            importance_distribution=importance_distribution,
            summary=summary,
        )

    def _summarize_event(self, event: Event) -> str:
        try:
            signals = self.repo.get_event_signals(event.id)
            if signals:
                sig = signals[0]
                if sig.value:
                    try:
                        data = json.loads(sig.value)
                        if isinstance(data, dict):
                            parts = [f"{event.event_type}"]
                            for key in ("target_id", "url", "repo"):
                                if key in data and data[key]:
                                    parts.append(f"{key}={data[key]}")
                            if "extracted_values" in data and isinstance(data["extracted_values"], dict):
                                for k, v in list(data["extracted_values"].items())[:2]:
                                    parts.append(f"{k}={v}")
                            if "tag_name" in data:
                                parts.append(f"tag={data['tag_name']}")
                            return ", ".join(parts)
                    except (json.JSONDecodeError, KeyError):
                        pass
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
        return f"{event.event_type} ({event.importance})"

    def _generate_summary(
        self,
        total: int,
        targets: Dict[str, TargetDigest],
        imp_dist: Dict[str, int],
    ) -> str:
        if total == 0:
            return "无事件发生。"
        critical = imp_dist.get("critical", 0)
        important = imp_dist.get("important", 0)
        parts = [f"过去共检测到 {total} 个事件，涉及 {len(targets)} 个目标。"]
        if critical > 0:
            parts.append(f"{critical} 个高优先级事件已实时推送。")
        if important > 0:
            parts.append(f"{important} 个重要事件已实时推送。")
        parts.append("其余事件已汇总到本报告。")
        return " ".join(parts)
