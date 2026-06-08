from __future__ import annotations

from datetime import date

from aiogram import Bot

from .access_repo import AccessRepository, STATUS_APPROVED
from .repository import MilestonesRepository


def _d(d):
    return d.strftime("%d.%m.%Y") if d else "-"


def _fmt(rec) -> str:
    return (
        f"Веха: {rec.milestone_key} | {rec.milestone_name}\n"
        f"Объект: {rec.object_name}\n"
        f"Шифр объекта: {rec.object_code or '-'}\n"
        f"Дата исполнения: {_d(rec.due_date)}\n"
        f"Целевая дата исполнения: {_d(rec.target_date)}\n"
        f"Дата за вчера: {_d(rec.due_date_yesterday)}\n"
        f"Статус сегодня (H): {rec.status_today or '-'}\n"
        f"Статус вчера (T): {rec.status_yesterday or '-'}\n"
        f"Ответственный: {rec.responsible}\n"
        f"Почта: {rec.responsible_email}\n"
        f"Отклонение: {rec.deviation or '-'}\n"
        f"Комментарий: {rec.comment or '-'}"
    )


def _chunked_send(text: str, limit: int = 3900, preferred_sep: str = "\n---\n") -> list[str]:
    if not text:
        return [""]
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind(preferred_sep, 0, limit)
        if cut == -1:
            cut = limit
            out.append(rest[:cut])
            rest = rest[cut:]
        else:
            cut_end = cut + len(preferred_sep)
            out.append(rest[:cut_end].rstrip())
            rest = rest[cut_end:]
    if rest:
        out.append(rest)
    return out


async def send_daily_changes(
    bot: Bot,
    repo: MilestonesRepository,
    access_repo: AccessRepository,
    label: str,
) -> None:
    repo.get_records(force_refresh=True)
    for user in access_repo.list_users():
        if not (user.is_admin or user.status == STATUS_APPROVED):
            continue
        allowed_codes = None if user.is_admin else user.object_codes
        if (not user.is_admin) and (not allowed_codes):
            await bot.send_message(user.telegram_id, f"{label}: у вас нет назначенных объектов.")
            continue
        changes = repo.today_changes(allowed_codes)
        if not changes:
            text = f"{label}: на текущий момент изменений дат/статусов нет."
        else:
            lines = [f"{label}: изменений {len(changes)}", ""]
            for rec in changes[:25]:
                lines.append(_fmt(rec))
                lines.append("---")
            text = "\n".join(lines)
        for part in _chunked_send(text):
            await bot.send_message(user.telegram_id, part)


async def send_friday_digest(
    bot: Bot,
    repo: MilestonesRepository,
    access_repo: AccessRepository,
) -> None:
    if date.today().weekday() != 4:
        return
    repo.get_records(force_refresh=True)
    for user in access_repo.list_users():
        if not (user.is_admin or user.status == STATUS_APPROVED):
            continue
        allowed_codes = None if user.is_admin else user.object_codes
        if (not user.is_admin) and (not allowed_codes):
            await bot.send_message(user.telegram_id, "Пятничный отчет: у вас нет назначенных объектов.")
            continue
        items = repo.friday_attention(allowed_object_codes=allowed_codes)
        if not items:
            text = "Пятничный отчет: критичных вех на эту неделю и по долгим переносам нет."
        else:
            lines = ["Пятничный отчет по вехам для контроля сроков:", ""]
            for rec in items[:30]:
                lines.append(_fmt(rec))
                lines.append("---")
            text = "\n".join(lines)
        for part in _chunked_send(text):
            await bot.send_message(user.telegram_id, part)
