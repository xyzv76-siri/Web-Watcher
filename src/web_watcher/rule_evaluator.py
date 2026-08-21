import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List, Optional
from web_watcher.rule_models import WatcherRule, TriggerConfig
from web_watcher.dom_extractor import DOMExtractor
from web_watcher.rule_models import ExtractionStatus


@dataclass
class TriggeredEvent:
    rule_id: str
    trigger_type: str
    field_name: str
    old_value: Any
    new_value: Any
    importance: str
    title: str
    body: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    rule_id: str
    extracted_values: Dict[str, Any]
    triggered_events: List[TriggeredEvent]
    is_triggered: bool


class RuleEvaluator:
    """网页规则评估器：执行 DOM 提取并比对变更触发条件"""

    @classmethod
    def evaluate_condition(cls, condition: Optional[str], old_val: Any, new_val: Any) -> bool:
        if not condition:
            return old_val != new_val

        try:
            old_num = float(old_val) if old_val is not None else 0.0
            new_num = float(new_val) if new_val is not None else 0.0
            delta = new_num - old_num
            abs_delta = abs(delta)
            pct_delta = ((new_num - old_num) / old_num * 100.0) if old_num != 0 else 0.0

            context = {
                "old": old_num,
                "new": new_num,
                "delta": delta,
                "abs_delta": abs_delta,
                "pct_delta": pct_delta,
                "abs": abs,
            }
            return bool(eval(condition, {"__builtins__": None}, context))
        except (SyntaxError, NameError, TypeError, ValueError, RuntimeError):
            return str(old_val) != str(new_val)

    @classmethod
    def evaluate_condition_group(cls, condition_group: Optional[List[Dict[str, Any]]], operator: Optional[str], old_val: Any, new_val: Any) -> bool:
        """Evaluate a group of conditions with AND/OR operator."""
        if not condition_group:
            return cls.evaluate_condition(None, old_val, new_val)

        results = []
        for cond in condition_group:
            cond_type = cond.get("type", "simple")
            if cond_type == "simple":
                cond_expr = cond.get("condition")
                results.append(cls.evaluate_condition(cond_expr, old_val, new_val))
            elif cond_type == "numeric_delta":
                cond_expr = cond.get("condition")
                results.append(cls.evaluate_condition(cond_expr, old_val, new_val))
            elif cond_type == "regex_match":
                pattern = cond.get("pattern")
                if pattern:
                    results.append(bool(re.search(pattern, str(new_val))))
                else:
                    results.append(old_val != new_val)
            else:
                results.append(old_val != new_val)

        if not results:
            return False

        if operator == "OR":
            return any(results)
        else:  # Default AND
            return all(results)

    @classmethod
    def check_time_window(cls, trigger: TriggerConfig, old_timestamp: Optional[datetime] = None, new_timestamp: Optional[datetime] = None) -> bool:
        """Check if change is within time window.
        
        Returns True if:
        - No time_window_minutes is set on the trigger
        - Timestamps are not available (cannot filter, pass through)
        - The time between old and new observation is within the window
        """
        if trigger.time_window_minutes is None:
            return True
        if old_timestamp is None or new_timestamp is None:
            return True
        delta_minutes = (new_timestamp - old_timestamp).total_seconds() / 60.0
        return delta_minutes <= trigger.time_window_minutes

    @classmethod
    def render_template(cls, template: Optional[str], context: Dict[str, Any], default: str) -> str:
        if not template:
            return default
        try:
            return template.format(**context)
        except (KeyError, IndexError, ValueError, TypeError):
            return template

    @classmethod
    def evaluate(
        cls,
        rule: WatcherRule,
        new_html: str,
        old_values: Optional[Dict[str, Any]] = None,
        old_timestamp: Optional[datetime] = None,
        new_timestamp: Optional[datetime] = None,
    ) -> EvaluationResult:
        old_values = old_values or {}
        extracted: Dict[str, Any] = {}

        # 1. 执行字段提取
        for ext in rule.extractors:
            result = DOMExtractor.extract(new_html, ext)
            if result.status == ExtractionStatus.FOUND:
                extracted[ext.name] = result.value
            else:
                extracted[ext.name] = None

        triggered_events: List[TriggeredEvent] = []

        # 2. 判定触发器
        for trg in rule.triggers:
            f_name = trg.field
            new_val = extracted.get(f_name)
            old_val = old_values.get(f_name)

            is_trg = False
            if old_val is not None:
                if trg.type == "text_diff":
                    is_trg = str(old_val) != str(new_val)
                elif trg.type == "numeric_delta":
                    # Support condition_group (AND/OR) or legacy single condition
                    if trg.condition_group:
                        is_trg = cls.evaluate_condition_group(trg.condition_group, trg.condition_operator, old_val, new_val)
                    else:
                        is_trg = cls.evaluate_condition(trg.condition, old_val, new_val)
                elif trg.type == "regex_match":
                    if trg.condition:
                        is_trg = bool(re.search(trg.condition, str(new_val)))
                    else:
                        is_trg = str(old_val) != str(new_val)
                elif trg.type == "node_changed":
                    is_trg = old_val != new_val
                else:
                    is_trg = old_val != new_val

            # Time window check (post-trigger filter)
            if is_trg and trg.time_window_minutes is not None:
                is_trg = cls.check_time_window(trg, old_timestamp=old_timestamp, new_timestamp=new_timestamp)

            if is_trg:
                delta_num = 0.0
                pct_num = 0.0
                try:
                    if old_val is not None and new_val is not None:
                        delta_num = float(new_val) - float(old_val)
                        pct_num = ((delta_num) / float(old_val) * 100.0) if float(old_val) != 0 else 0.0
                except (ValueError, TypeError):
                    pass

                tmpl_ctx = {
                    "rule_name": rule.name,
                    "field": f_name,
                    "old_value": old_val,
                    "new_value": new_val,
                    "delta": delta_num,
                    "abs_delta": abs(delta_num),
                    "delta_percent": f"{pct_num:.2f}",
                    **extracted,
                }

                title = cls.render_template(
                    trg.title_template,
                    tmpl_ctx,
                    f"[{rule.name}] Field '{f_name}' changed: {old_val} -> {new_val}",
                )
                body = cls.render_template(
                    trg.body_template,
                    tmpl_ctx,
                    f"Triggered by {trg.type} on '{f_name}'.\nOld: {old_val}\nNew: {new_val}",
                )

                triggered_events.append(TriggeredEvent(
                    rule_id=rule.id,
                    trigger_type=trg.type,
                    field_name=f_name,
                    old_value=old_val,
                    new_value=new_val,
                    importance=trg.importance,
                    title=title,
                    body=body,
                    metadata={"rule_name": rule.name, "target_url": rule.target.url},
                ))

        return EvaluationResult(
            rule_id=rule.id,
            extracted_values=extracted,
            triggered_events=triggered_events,
            is_triggered=len(triggered_events) > 0,
        )
