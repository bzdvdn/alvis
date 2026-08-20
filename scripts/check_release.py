"""Validate that a release tag matches the package version."""

from __future__ import annotations

import os
import re
from pathlib import Path


def main() -> None:
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"$', project, re.MULTILINE)
    if match is None:
        raise SystemExit("project version is missing from pyproject.toml")
    version = match.group(1)

    tag = os.environ.get("GITHUB_REF_NAME") or os.environ.get("RELEASE_TAG")
    if not tag:
        raise SystemExit("GITHUB_REF_NAME or RELEASE_TAG is required")
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:a\d+|b\d+|rc\d+)?", tag):
        raise SystemExit(f"invalid release tag: {tag!r}")
    expected = f"v{version}"
    if tag != expected:
        raise SystemExit(f"tag {tag!r} does not match pyproject version {version!r}")

    print(f"release metadata OK: {tag}")


if __name__ == "__main__":
    main()
