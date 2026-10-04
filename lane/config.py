"""Settings: config.yaml plus secrets from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
ASSETS_DIR = ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
MUSIC_DIR = ASSETS_DIR / "music"
WORK_DIR = Path(os.environ.get("LANE_WORK_DIR", ROOT / "work"))


@dataclass(frozen=True)
class Secrets:
    yt_client_id: str
    yt_client_secret: str
    yt_refresh_token: str
    yt_download_cookies: str
    telegram_bot_token: str
    telegram_chat_id: str


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_secrets() -> Secrets:
    env = os.environ.get
    return Secrets(
        yt_client_id=env("YT_CLIENT_ID", ""),
        yt_client_secret=env("YT_CLIENT_SECRET", ""),
        yt_refresh_token=env("YT_REFRESH_TOKEN", ""),
        yt_download_cookies=env("YT_DOWNLOAD_COOKIES", ""),
        telegram_bot_token=env("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=env("TELEGRAM_CHAT_ID", ""),
    )
