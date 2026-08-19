#!/usr/bin/env python3
"""Enforce that every connector ships its golden-snapshot test.

The "golden test" requirement from CONTRIBUTING.md is enforced by CI: for
every source listed in ``tests/golden/manifest.json`` there must be a test
file that calls ``assert_golden("<source>", ...)`` and a committed snapshot
``tests/golden/<source>.golden.json``. New connectors must add an entry to
the manifest; this script fails if they do not also add the test.

Run::

    python scripts/check_golden_coverage.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "golden" / "manifest.json"
GOLDEN_DIR = ROOT / "tests" / "golden"


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sources = manifest.get("sources", {})
    if not sources:
        print("manifest has no sources — add at least one golden-test entry")
        return 1

    failures: list[str] = []
    for source, test_file in sorted(sources.items()):
        test_path = ROOT / test_file
        if not test_path.exists():
            failures.append(f"{source}: test file missing: {test_file}")
            continue
        content = test_path.read_text(encoding="utf-8")
        if f'assert_golden("{source}"' not in content:
            failures.append(
                f"{source}: {test_file} does not call assert_golden({source!r})"
            )
        snapshot = GOLDEN_DIR / f"{source}.golden.json"
        if not snapshot.exists():
            failures.append(f"{source}: golden snapshot missing: {snapshot.relative_to(ROOT)}")

    if failures:
        print("golden coverage FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"golden coverage OK: {len(sources)} source(s) with fixture + golden test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
