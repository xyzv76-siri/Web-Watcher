import re
from html.parser import HTMLParser
from typing import Any, List, Optional, Dict
from web_watcher.rule_models import ExtractorConfig
from web_watcher.transforms import apply_transforms


class DOMNode:
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, tag: str, attrs: Dict[str, str], parent=None):
        self.tag = tag.lower()
        self.attrs = {k.lower(): v for k, v in attrs.items()}
        self.parent = parent
        self.children: List['DOMNode'] = []
        self.text_chunks: List[str] = []

    @property
    def inner_text(self) -> str:
        texts = []
        for chunk in self.text_chunks:
            texts.append(chunk)
        for child in self.children:
            texts.append(child.inner_text)
        return " ".join("".join(texts).split())

    @property
    def classes(self) -> List[str]:
        return self.attrs.get("class", "").split()

    @property
    def id(self) -> str:
        return self.attrs.get("id", "")

    def matches_selector_part(self, part: str) -> bool:
        part = part.strip()
        if not part or part == "*":
            return True

        pattern = r'^(?:([a-zA-Z0-9]+))?(?:#([a-zA-Z0-9_-]+))?((?:\.[a-zA-Z0-9_-]+)*)$'
        m = re.match(pattern, part)
        if not m:
            if part.startswith("."):
                return part[1:] in self.classes
            if part.startswith("#"):
                return self.id == part[1:]
            return self.tag == part.lower()

        tag_part, id_part, classes_part = m.groups()
        if tag_part and self.tag != tag_part.lower():
            return False
        if id_part and self.id != id_part:
            return False
        if classes_part:
            req_classes = [c[1:] for c in re.findall(r'\.[a-zA-Z0-9_-]+', classes_part)]
            node_classes = set(self.classes)
            for req in req_classes:
                if req not in node_classes:
                    return False
        return True

    def find_descendants(self, selector_parts: List[str]) -> List['DOMNode']:
        if not selector_parts:
            return []

        target_part = selector_parts[0]
        remaining = selector_parts[1:]

        matches = []
        queue = list(self.children)
        while queue:
            curr = queue.pop(0)
            if curr.matches_selector_part(target_part):
                if not remaining:
                    matches.append(curr)
                else:
                    matches.extend(curr.find_descendants(remaining))
            else:
                queue.extend(curr.children)

        return matches


class _DOMBuilder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = DOMNode("root", {})
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = DOMNode(tag, dict(attrs), parent=self.current)
        self.current.children.append(node)
        if tag.lower() not in DOMNode.VOID_TAGS:
            self.current = node

    def handle_endtag(self, tag):
        if tag.lower() in DOMNode.VOID_TAGS:
            return
        node = self.current
        while node != self.root:
            if node.tag == tag.lower():
                self.current = node.parent or self.root
                break
            node = node.parent

    def handle_data(self, data):
        if self.current:
            self.current.text_chunks.append(data)


class DOMExtractor:
    """网页 DOM 内容与正则提取器"""

    @classmethod
    def parse_html(cls, html_content: str) -> DOMNode:
        parser = _DOMBuilder()
        parser.feed(html_content)
        return parser.root

    @classmethod
    def extract_by_css(cls, html_content: str, selector: str) -> str:
        root = cls.parse_html(html_content)
        parts = selector.strip().split()
        if not parts:
            return ""

        matches = root.find_descendants(parts)
        if matches:
            return matches[0].inner_text
        return ""

    @classmethod
    def extract_by_regex(cls, content: str, pattern: str) -> str:
        m = re.search(pattern, content)
        if m:
            return m.group(1) if m.groups() else m.group(0)
        return ""

    @classmethod
    def extract(cls, content: str, config: ExtractorConfig) -> Any:
        sel_type = (config.selector_type or "css").lower()

        if sel_type == "css":
            raw_value = cls.extract_by_css(content, config.selector)
        elif sel_type == "regex":
            raw_value = cls.extract_by_regex(content, config.selector)
        elif sel_type in ("raw", "text"):
            raw_value = content
        else:
            raw_value = cls.extract_by_css(content, config.selector)

        if config.transforms:
            return apply_transforms(raw_value, config.transforms)
        return raw_value
