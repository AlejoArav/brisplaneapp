from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    load_dotenv(ROOT / ".env")


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_refresh_ttl_seconds(config: dict[str, Any]) -> int:
    env_value = os.getenv("REFRESH_INTERVAL_HOURS")
    hours = env_value or config.get("app", {}).get("refresh_interval_hours", 6)
    try:
        return int(float(hours) * 3600)
    except (TypeError, ValueError):
        return 6 * 3600
