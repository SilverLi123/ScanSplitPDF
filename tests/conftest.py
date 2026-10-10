import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.samplegen import build_scan_pdf, build_text_pdf  # noqa: E402


@pytest.fixture
def make_scan(tmp_path):
    def _make(name: str, kinds: list[str], seeds: list[int] | None = None) -> Path:
        p = tmp_path / name
        return build_scan_pdf(p, kinds, seeds)

    return _make


@pytest.fixture
def make_text(tmp_path):
    def _make(name: str, pages: list[list[tuple]]) -> Path:
        p = tmp_path / name
        return build_text_pdf(p, pages)

    return _make
