import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from web_watcher.rule_models import ExtractorConfig, ExtractionStatus, ExtractionResult
from web_watcher.transforms import apply_transform


class DOMExtractor:
    """Structured DOM/text extractor with explicit status for selector misses, empty results, and transform failures."""

    @classmethod
    def extract(cls, html_content: str, config: ExtractorConfig) -> ExtractionResult:
        meta = {
            "selector": config.selector,
            "selector_type": config.selector_type,
            "match_count": 0,
        }

        if not html_content:
            return ExtractionResult(
                status=ExtractionStatus.SELECTOR_NOT_FOUND,
                error_message="Empty HTML content provided",
                metadata=meta,
            )

        try:
            if config.selector_type == "raw":
                meta["match_count"] = 1
                raw_text = html_content
                return cls._apply_transforms(raw_text, config, meta)

            elif config.selector_type == "regex":
                matches = re.findall(config.selector, html_content)
                meta["match_count"] = len(matches)
                if not matches:
                    return ExtractionResult(
                        status=ExtractionStatus.SELECTOR_NOT_FOUND,
                        error_message=f"Regex pattern '{config.selector}' matched 0 times",
                        metadata=meta,
                    )
                raw_text = matches[0] if isinstance(matches[0], str) else matches[0][0]
                return cls._apply_transforms(raw_text, config, meta)

            elif config.selector_type in ("css", "xpath"):
                soup = BeautifulSoup(html_content, "html.parser")
                elements = soup.select(config.selector)
                meta["match_count"] = len(elements)

                if not elements:
                    return ExtractionResult(
                        status=ExtractionStatus.SELECTOR_NOT_FOUND,
                        error_message=f"CSS selector '{config.selector}' found 0 matching elements",
                        metadata=meta,
                    )

                raw_text = elements[0].get_text()
                return cls._apply_transforms(raw_text, config, meta)

            else:
                return ExtractionResult(
                    status=ExtractionStatus.TRANSFORM_ERROR,
                    error_message=f"Unsupported selector_type: '{config.selector_type}'",
                    metadata=meta,
                )

        except Exception as e:
            return ExtractionResult(
                status=ExtractionStatus.TRANSFORM_ERROR,
                error_message=f"Extractor internal failure: {str(e)}",
                metadata=meta,
            )

    @classmethod
    def _apply_transforms(cls, raw_text: str, config: ExtractorConfig, meta: Dict[str, Any]) -> ExtractionResult:
        if not config.transforms:
            cleaned_val = raw_text.strip()
            if not cleaned_val and raw_text != "":
                return ExtractionResult(
                    status=ExtractionStatus.EMPTY_AFTER_TRANSFORM,
                    raw_value=raw_text,
                    value="",
                    metadata=meta,
                )
            return ExtractionResult(
                status=ExtractionStatus.FOUND,
                raw_value=raw_text,
                value=cleaned_val,
                metadata=meta,
            )

        try:
            val = raw_text
            for t_rule in config.transforms:
                val = apply_transform(val, t_rule)

            if isinstance(val, str) and val.strip() == "":
                return ExtractionResult(
                    status=ExtractionStatus.EMPTY_AFTER_TRANSFORM,
                    raw_value=raw_text,
                    value="",
                    metadata=meta,
                )

            return ExtractionResult(
                status=ExtractionStatus.FOUND,
                raw_value=raw_text,
                value=val,
                metadata=meta,
            )
        except Exception as e:
            return ExtractionResult(
                status=ExtractionStatus.TRANSFORM_ERROR,
                raw_value=raw_text,
                value=None,
                error_message=f"Transform chain failed: {str(e)}",
                metadata=meta,
            )
