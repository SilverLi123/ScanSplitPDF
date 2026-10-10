"""设置持久化：%APPDATA%/ScanSplitPDF/settings.json（离线本地存储）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .blankdetect import DetectParams
from .naming import DEFAULT_TEMPLATE, UnitHistory


def settings_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "ScanSplitPDF"


def settings_path() -> Path:
    return settings_dir() / "settings.json"


DEFAULT_SETTINGS = {
    "detect_params": DetectParams().to_dict(),
    "template": DEFAULT_TEMPLATE,
    "unit_history": [],
    "output_dir": "",
    "window_geometry": "",
    "zoom": 1.0,
}


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update(raw)
    except (OSError, json.JSONDecodeError):
        pass
    return data


def save_settings(settings: dict) -> None:
    try:
        d = settings_dir()
        d.mkdir(parents=True, exist_ok=True)
        settings_path().write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def load_unit_history(settings: dict) -> UnitHistory:
    return UnitHistory.from_json(settings.get("unit_history"))


def load_detect_params(settings: dict) -> DetectParams:
    try:
        return DetectParams.from_dict(settings.get("detect_params") or {})
    except Exception:
        return DetectParams()
