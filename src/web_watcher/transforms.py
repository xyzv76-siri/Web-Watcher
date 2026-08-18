import re
import hashlib
import json
from typing import Any, List, Optional


class TransformError(ValueError):
    """数据转换异常"""
    pass


def strip_tags(value: Any) -> str:
    s = str(value)
    return re.sub(r"<[^>]+>", "", s).strip()


def to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    m = re.search(r"[-+]?\d*\.?\d+", s)
    if m and m.group(0) not in ("", ".", "-"):
        return float(m.group(0))
    raise TransformError(f"Cannot convert '{value}' to float")


def to_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    s = str(value).strip().replace(",", "")
    m = re.search(r"[-+]?\d+", s)
    if m:
        return int(m.group(0))
    raise TransformError(f"Cannot convert '{value}' to int")


def apply_transform(value: Any, transform_spec: str) -> Any:
    if not transform_spec:
        return value
    spec = transform_spec.strip()

    if spec == "strip_tags":
        return strip_tags(value)
    elif spec in ("trim", "strip"):
        return str(value).strip()
    elif spec in ("lowercase", "lower"):
        return str(value).lower()
    elif spec in ("uppercase", "upper"):
        return str(value).upper()
    elif spec == "to_float":
        return to_float(value)
    elif spec == "to_int":
        return to_int(value)
    elif spec in ("to_str", "to_string"):
        return str(value)
    elif spec in ("hash", "sha256"):
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    elif spec.startswith("regex:"):
        pattern = spec[len("regex:"):].strip()
        m = re.search(pattern, str(value))
        if m:
            return m.group(1) if m.groups() else m.group(0)
        return ""
    elif spec.startswith("json_field:"):
        key = spec[len("json_field:"):].strip()
        try:
            data = json.loads(str(value))
            return data.get(key, "")
        except Exception as e:
            raise TransformError(f"JSON parsing failed for '{spec}': {e}")
    else:
        return value


def apply_transforms(value: Any, transforms: List[str]) -> Any:
    res = value
    for t in transforms:
        res = apply_transform(res, t)
    return res
