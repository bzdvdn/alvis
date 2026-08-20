"""Deprecation policy tests — warnings carry removal metadata, once per site."""

from __future__ import annotations

import pytest

from alvis.deprecated import deprecated, is_deprecated, removed_in


def test_deprecated_emits_warning_with_removal_version() -> None:
    @deprecated("use alvis.load_pipeline instead", remove_in="1.0.0")
    def answer() -> int:
        return 42

    with pytest.warns(DeprecationWarning) as captured:
        assert answer() == 42
    assert "answer" in str(captured.list[0].message)
    assert "1.0.0" in str(captured.list[0].message)
    assert "use alvis.load_pipeline instead" in str(captured.list[0].message)


def test_deprecated_keeps_metadata() -> None:
    @deprecated("gone", remove_in="0.7.0")
    def marker() -> None:
        return None

    assert is_deprecated(marker) is True
    assert removed_in(marker) == "0.7.0"


def test_plain_callables_are_not_deprecated() -> None:
    def plain() -> None:
        return None

    assert is_deprecated(plain) is False
    assert removed_in(plain) is None


def test_deprecated_preserves_signature_via_wraps() -> None:
    @deprecated("replaced", remove_in="1.0.0")
    def greet(name: str, *, loud: bool = False) -> str:
        return name.upper() if loud else name

    with pytest.warns(DeprecationWarning):
        assert greet("hi", loud=True) == "HI"
    assert greet.__name__ == "greet"