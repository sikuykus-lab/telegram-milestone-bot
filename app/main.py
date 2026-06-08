from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .access_repo import AccessRepository
from .bot import build_bot, build_dispatcher
from .config import load_settings
from .notifier import send_daily_changes, send_friday_digest
from .repository import MilestonesRepository
from .sheets_client import SheetsClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger(__name__)


async def run() -> None:
    settings = load_settings()
    sheets_client = SheetsClient(settings.creds_path)
    repo = MilestonesRepository(
        client=sheets_client,
        spreadsheet_id=settings.spreadsheet_id,
        sheet_name=settings.export_sheet_name,
        ttl_seconds=settings.cache_ttl_seconds,
        min_read_interval_seconds=settings.min_sheets_read_interval_seconds,
    )
    access_repo = AccessRepository(settings.access_db_path, settings.admin_telegram_id)
    bot = build_bot(settings)
    dp = build_dispatcher(settings, repo, access_repo)

    tz = "Europe/Moscow"
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(repo.get_records, "interval", minutes=5, kwargs={"force_refresh": True})
    scheduler.add_job(
        send_daily_changes,
        CronTrigger(hour=15, minute=0, timezone=tz),
        kwargs={"bot": bot, "repo": repo, "access_repo": access_repo, "label": "Дайджест 15:00"},
    )
    scheduler.add_job(
        send_daily_changes,
        CronTrigger(hour=18, minute=0, timezone=tz),
        kwargs={"bot": bot, "repo": repo, "access_repo": access_repo, "label": "Дайджест 18:00"},
    )
    scheduler.add_job(
        send_friday_digest,
        CronTrigger(day_of_week="fri", hour=9, minute=0, timezone=tz),
        kwargs={"bot": bot, "repo": repo, "access_repo": access_repo},
    )
    scheduler.start()
    LOGGER.info("Bot started")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
