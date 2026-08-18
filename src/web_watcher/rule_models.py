from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any


@dataclass
class TargetConfig:
    url: str
    interval: str = "15m"
    timeout: float = 10.0
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class ExtractorConfig:
    name: str
    selector_type: str = "css"
    selector: str = ""
    transforms: List[str] = field(default_factory=list)


@dataclass
class TriggerConfig:
    type: str
    field: str
    condition: Optional[str] = None
    importance: str = "important"
    title_template: Optional[str] = None
    body_template: Optional[str] = None


@dataclass
class RoutingConfig:
    channels: List[str] = field(default_factory=lambda: ["console"])
    cooldown: str = "300s"


@dataclass
class WatcherRule:
    id: str
    name: str
    target: TargetConfig
    extractors: List[ExtractorConfig] = field(default_factory=list)
    triggers: List[TriggerConfig] = field(default_factory=list)
    routing: RoutingConfig = field(default_factory=RoutingConfig)


@dataclass
class RuleSet:
    version: str
    rules: List[WatcherRule] = field(default_factory=list)
