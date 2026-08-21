import yaml
from pathlib import Path
from typing import Union, Dict, Any, List
from web_watcher.rule_models import (
    RuleSet,
    WatcherRule,
    TargetConfig,
    ExtractorConfig,
    TriggerConfig,
    RoutingConfig,
)


class RuleParseError(ValueError):
    """YAML 规则解析与校验异常"""
    pass


class RuleParser:
    """声明式 YAML 规则解析与 Schema 校验器"""

    @classmethod
    def parse_yaml_str(cls, content: str) -> RuleSet:
        try:
            data = yaml.safe_load(content)
        except Exception as e:
            raise RuleParseError(f"Malformed YAML content: {e}") from e

        if not isinstance(data, dict):
            raise RuleParseError("Root element must be a dictionary")

        version = str(data.get("version", "1.0"))
        raw_rules = data.get("rules", [])
        if not isinstance(raw_rules, list):
            raise RuleParseError("'rules' must be a list")

        rules: List[WatcherRule] = []
        for idx, raw_rule in enumerate(raw_rules):
            rule_id = raw_rule.get("id") or f"rule_{idx + 1}"
            name = raw_rule.get("name") or rule_id

            target_dict = raw_rule.get("target")
            if not target_dict or not isinstance(target_dict, dict) or "url" not in target_dict:
                raise RuleParseError(f"Rule '{rule_id}' missing required target.url")

            target = TargetConfig(
                url=target_dict["url"],
                interval=str(target_dict.get("interval", "15m")),
                timeout=float(target_dict.get("timeout", 10.0)),
                headers=dict(target_dict.get("headers", {})),
            )

            extractors = []
            for ext in raw_rule.get("extractors", []):
                if not ext.get("name") or not ext.get("selector"):
                    raise RuleParseError(f"Rule '{rule_id}' extractor must have 'name' and 'selector'")
                extractors.append(ExtractorConfig(
                    name=ext["name"],
                    selector_type=ext.get("selector_type", "css"),
                    selector=ext["selector"],
                    transforms=list(ext.get("transforms", [])),
                    scope_selector=ext.get("scope_selector"),
                ))

            triggers = []
            for trg in raw_rule.get("triggers", []):
                if not trg.get("type") or not trg.get("field"):
                    raise RuleParseError(f"Rule '{rule_id}' trigger must have 'type' and 'field'")
                triggers.append(TriggerConfig(
                    type=trg["type"],
                    field=trg["field"],
                    condition=trg.get("condition"),
                    importance=trg.get("importance", "important"),
                    title_template=trg.get("title_template"),
                    body_template=trg.get("body_template"),
                ))

            routing_dict = raw_rule.get("routing", {})
            routing = RoutingConfig(
                channels=list(routing_dict.get("channels", ["console"])),
                cooldown=str(routing_dict.get("cooldown", "300s")),
            )

            rules.append(WatcherRule(
                id=rule_id,
                name=name,
                target=target,
                extractors=extractors,
                triggers=triggers,
                routing=routing,
            ))

        return RuleSet(version=version, rules=rules)

    @classmethod
    def parse_file(cls, file_path: Union[str, Path]) -> RuleSet:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Rule file not found: {path}")
        return cls.parse_yaml_str(path.read_text(encoding="utf-8"))
