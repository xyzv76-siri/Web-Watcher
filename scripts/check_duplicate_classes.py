#!/usr/bin/env python3
"""Check for duplicate class definitions in the codebase."""

import sys
from collections import defaultdict
from pathlib import Path


def find_duplicate_classes(root: Path):
    """Find files that define the same class name in src/."""
    class_definitions = defaultdict(list)

    # Only check src/ directory to avoid false positives from test dummies
    src_root = root / "src"
    if not src_root.exists():
        return {}

    for py_file in src_root.rglob("*.py"):
        if ".venv" in str(py_file) or "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
            for line_num, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("class ") and "(" in stripped:
                    # Extract class name
                    class_name = stripped.split("(")[0].replace("class ", "").strip()
                    if class_name:
                        class_definitions[class_name].append((py_file, line_num))
        except Exception:
            continue

    duplicates = {name: locs for name, locs in class_definitions.items() if len(locs) > 1}
    return duplicates


def main():
    root = Path(__file__).resolve().parent.parent
    duplicates = find_duplicate_classes(root)

    if duplicates:
        print("ERROR: Duplicate class definitions found:")
        for class_name, locations in duplicates.items():
            for file_path, line_num in locations:
                print(f"  {class_name}: {file_path}:{line_num}")
        sys.exit(1)
    else:
        print("OK: No duplicate class definitions found.")
        sys.exit(0)


if __name__ == "__main__":
    main()
