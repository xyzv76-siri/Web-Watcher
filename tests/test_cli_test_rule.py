from unittest.mock import MagicMock, patch
from web_watcher.cli import main


def test_cli_test_rule_success(tmp_path, capsys):
    rule_file = tmp_path / "rule.yaml"
    rule_file.write_text("""
version: "1.0"
rules:
  - id: "sample"
    name: "Sample"
    target:
      url: "https://example.com"
    extractors:
      - name: "title"
        selector_type: "css"
        selector: "h1"
""", encoding="utf-8")

    html_file = tmp_path / "page.html"
    html_file.write_text("<h1>Hello World</h1>", encoding="utf-8")

    ret = main(["test-rule", str(rule_file), "--html-file", str(html_file)])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Loaded 1 rule" in captured.out
    assert "Extracted Values" in captured.out
    assert "Hello World" in captured.out


def test_cli_test_rule_missing_file(capsys):
    ret = main(["test-rule", "/nonexistent/rule.yaml"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "[ERROR]" in captured.out
