from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_telegram_ids: set[int]
    spreadsheet_id: str
    export_sheet_name: str
    creds_path: Path
    cache_ttl_seconds: int
    min_sheets_read_interval_seconds: int
    notify_horizons: tuple[int, ...]
    admin_telegram_id: int
    access_db_path: Path
    email_enabled: bool
    email_smtp_host: str
    email_smtp_port: int
    email_smtp_user: str
    email_smtp_password: str
    email_from: str
    email_use_tls: bool
    email_timeout_seconds: int


def _parse_allowed_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for item in (raw or "").split(","):
        item = item.strip()
        if item:
            result.add(int(item))
    return result


def _parse_horizons(raw: str) -> tuple[int, ...]:
    vals = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        vals.append(int(item))
    return tuple(sorted(set(vals)))


def load_settings() -> Settings:
    load_dotenv()
    allowed = _parse_allowed_ids(os.getenv("ALLOWED_TELEGRAM_IDS", ""))
    if not allowed:
        raise ValueError("ALLOWED_TELEGRAM_IDS must be set")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("BOT_TOKEN must be set")

    spreadsheet_id = os.getenv("SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        raise ValueError("SPREADSHEET_ID must be set")

    return Settings(
        bot_token=token,
        allowed_telegram_ids=allowed,
        spreadsheet_id=spreadsheet_id,
        export_sheet_name=os.getenv("EXPORT_SHEET_NAME", "Экспорт").strip(),
        creds_path=Path(os.getenv("CREDS_PATH", "service-account.json")).expanduser(),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "180")),
        min_sheets_read_interval_seconds=int(
            os.getenv("MIN_SHEETS_READ_INTERVAL_SECONDS", "60")
        ),
        notify_horizons=_parse_horizons(os.getenv("NOTIFY_HOURS", "7,14,30")),
        admin_telegram_id=int(os.getenv("ADMIN_TELEGRAM_ID", str(min(allowed)))),
        access_db_path=Path(os.getenv("ACCESS_DB_PATH", "data/access.sqlite3")).expanduser(),
        email_enabled=os.getenv("EMAIL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"},
        email_smtp_host=os.getenv("EMAIL_SMTP_HOST", "").strip(),
        email_smtp_port=int(os.getenv("EMAIL_SMTP_PORT", "587")),
        email_smtp_user=os.getenv("EMAIL_SMTP_USER", "").strip(),
        email_smtp_password=os.getenv("EMAIL_SMTP_PASSWORD", "").strip(),
        email_from=os.getenv("EMAIL_FROM", "").strip(),
        email_use_tls=os.getenv("EMAIL_USE_TLS", "1").strip().lower() in {"1", "true", "yes", "on"},
        email_timeout_seconds=int(os.getenv("EMAIL_TIMEOUT_SECONDS", "20")),
    )
