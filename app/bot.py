from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup

from .access_repo import (
    AccessRepository,
    FILTER_PIPELINE_STAGE_KEYS,
    STATUS_APPROVED,
    STATUS_BLOCKED,
    STATUS_PENDING,
)
from .config import Settings
from .models import MilestoneRecord
from .repository import MilestonesRepository

PAGE_SIZE = 6
MILESTONE_PAGE = 5
CONSTRUCTOR_KEY = "constructor"

BUTTONS = {
    "summary": "Сводка",
    "important_milestones": "Важные вехи",
    "usage_help": "Инструкция пользования",
    "responsibles": "Ответственные: список",
    "milestone_info": "Вехи: по объекту",
    "milestone_changes": "Изменения: по объекту",
    "milestone_object": "Объект: по вехе",
    "hot_week": "Горящие неделя",
    "friday": "Пятничный список",
    "notes": "Комментарий",
    "constructor": "Конструктор",
    "global_object_filter": "Глобальный фильтр объектов",
    "users": "Пользователи",
    "refresh": "Обновить кэш",
}
KEY_BY_LABEL = {v: k for k, v in BUTTONS.items()}
SPAM_WINDOW_SECONDS = 8
SPAM_MAX_EVENTS = 12
MUTE_SECONDS = 120

REPORT_FIELDS: list[tuple[str, str]] = [
    ("milestone", "Веха"),
    ("object", "Объект"),
    ("code", "Шифр"),
    ("date_today", "Дата сегодня"),
    ("forecast_fact_date", "Прогноз/Факт (F)"),
    ("date_yesterday", "Дата вчера"),
    ("target_date", "Дата (Целевая)"),
    ("status_today", "Статус сегодня"),
    ("status_yesterday", "Статус вчера"),
    ("responsible", "Ответственный"),
    ("email", "Почта"),
    ("role", "Роль"),
    ("deviation", "Отклонение"),
    ("comment", "Комментарий"),
    ("importance", "Важность"),
]
REPORT_FIELD_KEYS = [k for k, _ in REPORT_FIELDS]
REPORT_FIELD_LABEL = {k: v for k, v in REPORT_FIELDS}
CONFIGURABLE_REPORT_KEYS = [
    "milestone_info",
    "milestone_changes",
    "milestone_object",
    "important_milestones",
    "friday",
    "hot_week",
]

FILTER_STAGE_REPORT_KEYS = [
    "milestone_info",
    "milestone_changes",
    "milestone_object",
    "important_milestones",
]

RSC_KEY_TO_TAG = {
    "milestone_info": "mi",
    "milestone_changes": "mc",
    "milestone_object": "mo",
    "important_milestones": "im",
}
RSC_TAG_TO_KEY = {v: k for k, v in RSC_KEY_TO_TAG.items()}

PIPELINE_STAGE_LABEL: dict[str, str] = {
    "object": "Объект",
    "milestone": "Веха",
    "status": "Статус",
    "code": "Шифр",
    "responsible": "Ответственный",
    "deviation": "Отклонение",
}

BUTTON_HELP: dict[str, str] = {
    "summary": "Быстрый обзор по вашему доступу: активные, просроченные и риск 7 дней.",
    "important_milestones": "Выберите объекты и важные вехи, получите отсортированный отчёт по приоритету.",
    "usage_help": "Показывает инструкцию по доступным вам кнопкам.",
    "responsibles": "Показывает ответственных и распределение объектов по ролям.",
    "milestone_info": "Шаг 1: выберите объекты. Шаг 2: выберите вехи. Затем бот соберёт отчёт.",
    "milestone_changes": "Шаг 1: выберите объекты. Шаг 2: выберите изменённые вехи. Затем отчёт по изменениям.",
    "milestone_object": "Шаг 1: выберите вехи. Шаг 2: выберите объекты для этих вех. Затем отчёт.",
    "hot_week": "Показывает ближайшие к сроку вехи (целевая дата + факт).",
    "friday": "Показывает расширенный список внимания по срокам и отклонениям.",
    "notes": "Комментарии: добавить, отредактировать, удалить, привязать к объекту или вехе.",
    "constructor": "Настройка интерфейса, полей отчётов и (опционально) дополнительного шага фильтра по статусу; без настройки отчёты ведут себя как раньше.",
    "global_object_filter": "Админ: скрыть выбранные шифры объектов для всех пользователей.",
    "users": "Админ: управление пользователями, доступом и кнопками.",
    "refresh": "Админ: принудительно обновить кэш данных.",
}


def _d(d):
    return d.strftime("%d.%m.%Y") if d else "-"


def _fmt_record(r: MilestoneRecord, visible_fields: set[str] | None = None) -> str:
    vf = set(visible_fields or REPORT_FIELD_KEYS)
    lines: list[str] = []
    if "milestone" in vf:
        lines.append(f"Веха: {r.milestone_key} | {r.milestone_name}")
    if "object" in vf:
        lines.append(f"Объект: {r.object_name}")
    if "code" in vf:
        lines.append(f"Шифр: {r.object_code or '-'}")
    if "date_today" in vf:
        lines.append(f"Дата сегодня: {_d(r.due_date)}")
    if "forecast_fact_date" in vf:
        lines.append(f"Прогноз/Факт (F): {_d(r.forecast_fact_date)}")
    if "date_yesterday" in vf:
        lines.append(f"Дата вчера: {_d(r.due_date_yesterday)}")
    if "target_date" in vf:
        lines.append(f"Дата (Целевая): {_d(r.target_date)}")
    if "status_today" in vf:
        lines.append(f"Статус сегодня: {r.status_today or '-'}")
    if "status_yesterday" in vf:
        lines.append(f"Статус вчера: {r.status_yesterday or '-'}")
    if "responsible" in vf:
        lines.append(f"Ответственный: {r.responsible}")
    if "email" in vf:
        lines.append(f"Почта: {r.responsible_email}")
    if "role" in vf:
        lines.append(f"Роль: {r.role or '-'}")
    if "deviation" in vf:
        lines.append(f"Отклонение: {r.deviation or '-'}")
    if "comment" in vf:
        lines.append(f"Комментарий: {r.comment or '-'}")
    return "\n".join(lines) if lines else "Нет выбранных полей для отображения."


def _date_by_mode(r: MilestoneRecord, mode: str):
    m = (mode or "today").strip().lower()
    if m == "yesterday":
        return r.due_date_yesterday
    if m == "target":
        return r.target_date
    return r.due_date


def _date_mode_label(mode: str) -> str:
    m = (mode or "today").strip().lower()
    if m == "yesterday":
        return "дата вчера"
    if m == "target":
        return "целевая дата"
    return "дата сегодня"


def _hot_week_field_date(r: MilestoneRecord, field_mode: str):
    f = (field_mode or "e").strip().lower()
    if f == "f":
        return r.forecast_fact_date
    if f == "g":
        return r.target_date
    return r.due_date


def _menu(labels: list[str]) -> ReplyKeyboardMarkup:
    rows = []
    row = []
    for label in labels:
        row.append(KeyboardButton(text=label))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _paginate(items: list[str], page: int) -> tuple[list[str], int, int, int]:
    pages = max(1, math.ceil(len(items) / PAGE_SIZE)) if items else 1
    page = max(0, min(page, pages - 1)) if items else 0
    start = page * PAGE_SIZE
    return items[start : start + PAGE_SIZE], start, page, pages


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


def _milestone_group_key(name: str) -> str:
    raw = (name or "").strip().upper()
    if not raw:
        return "#"
    token = raw.split()[0]
    token = "".join(ch for ch in token if ch.isalnum() or ch in "-_/")
    if not token:
        return "#"
    return token[:3]


def _group_milestones(items: list[str]) -> list[tuple[str, list[str]]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for it in items:
        buckets[_milestone_group_key(it)].append(it)
    out = []
    for key in sorted(buckets.keys()):
        vals = sorted(buckets[key], key=lambda x: x.lower())
        out.append((key, vals))
    return out


def _indices_for_selected(items: list[str], selected_values: set[str]) -> set[int]:
    if not items or not selected_values:
        return set()
    return {i for i, v in enumerate(items) if v in selected_values}


def _union_filtered(base: list[str], selected_values: set[str], matches: list[str]) -> list[str]:
    keep = set(selected_values) | set(matches)
    return [v for v in base if v in keep]


def _kb_paginated(
    items: list[str],
    selected: set[int],
    page: int,
    ip: str,
    pp: str,
    select_all_cb: str,
    done_cb: str,
    clear_cb: str,
    cancel_cb: str = "menu_cancel",
    done_text: str = "Показать выбранные",
) -> InlineKeyboardMarkup:
    chunk, start, page, pages = _paginate(items, page)
    rows = []
    for j, name in enumerate(chunk):
        idx = start + j
        mark = "✅" if idx in selected else "☐"
        rows.append([InlineKeyboardButton(text=f"{mark} {idx+1}. {name[:28]}", callback_data=f"{ip}_{idx}")])
    rows.append(
        [
            InlineKeyboardButton(text="<", callback_data=f"{pp}_{page-1}" if page > 0 else "noop"),
            InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="noop"),
            InlineKeyboardButton(text=">", callback_data=f"{pp}_{page+1}" if page < pages - 1 else "noop"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="Выбрать всё", callback_data=select_all_cb),
            InlineKeyboardButton(text="Сброс", callback_data=clear_cb),
            InlineKeyboardButton(text=done_text, callback_data=done_cb),
        ]
    )
    rows.append([InlineKeyboardButton(text="Закрыть", callback_data=cancel_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_dispatcher(
    settings: Settings,
    repo: MilestonesRepository,
    access_repo: AccessRepository,
) -> Dispatcher:
    dp = Dispatcher()
    sessions: dict[tuple[int, int], dict] = {}
    spam_events: dict[int, list[datetime]] = defaultdict(list)
    muted_until: dict[int, datetime] = {}

    access_repo.upsert_button_catalog(BUTTONS)

    async def safe_edit_reply_markup(message: Message, markup: InlineKeyboardMarkup) -> None:
        try:
            await message.edit_reply_markup(reply_markup=markup)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            raise

    async def safe_edit_text(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        try:
            await message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            raise

    async def upsert_filter_menu(trigger_message: Message, s: dict, text: str, markup: InlineKeyboardMarkup) -> None:
        chat_id = s.get("menu_chat_id")
        message_id = s.get("menu_message_id")
        if chat_id and message_id:
            try:
                await trigger_message.bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=markup,
                )
                return
            except TelegramBadRequest as e:
                if "message is not modified" in str(e).lower():
                    return
            except Exception:
                pass
        sent = await trigger_message.answer(text, reply_markup=markup)
        s["menu_chat_id"] = sent.chat.id
        s["menu_message_id"] = sent.message_id

    async def notify_access_decision(bot: Bot, user_id: int, text: str) -> None:
        try:
            await bot.send_message(user_id, text)
        except Exception:
            # User may have blocked bot or not started dialog yet.
            pass

    def save_last_report(u, report_key: str, text: str) -> None:
        if not text.strip():
            return
        access_repo.save_user_last_report(
            u.telegram_id,
            text,
            {"report_key": report_key},
        )

    def fuzzy_values(values: list[str], query: str, limit: int = 80) -> list[str]:
        if hasattr(repo, "fuzzy_filter_values"):
            return repo.fuzzy_filter_values(values, query, limit=limit)
        q = (query or "").strip().lower()
        if not q:
            return values[:limit]
        exact = [v for v in values if q in (v or "").lower()]
        ranked = exact if exact else values
        return ranked[:limit]

    def S(chat_id: int, user_id: int) -> dict:
        return sessions.setdefault((chat_id, user_id), {})

    def clear_session(chat_id: int, user_id: int) -> None:
        sessions.pop((chat_id, user_id), None)

    async def close_previous_menu_if_active(message: Message) -> None:
        user = message.from_user
        if not user:
            return
        s = sessions.get((message.chat.id, user.id))
        if not s:
            return
        chat_id = s.get("menu_chat_id")
        message_id = s.get("menu_message_id")
        has_active_flow = bool(
            s.get("flow_stage")
            or s.get("list")
            or s.get("milestones")
            or s.get("changes_mks")
            or s.get("obj_info_mks")
            or s.get("bind_objects")
            or s.get("bind_mks")
            or s.get("fq_reports")
            or s.get("ff_apply_keys")
        )
        if chat_id and message_id and has_active_flow:
            try:
                await message.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="Предыдущее меню закрыто: начато новое действие.",
                    reply_markup=None,
                )
            except Exception:
                pass
        clear_session(message.chat.id, user.id)

    def _is_spam(uid: int) -> tuple[bool, int]:
        now = datetime.utcnow()
        # Prevent unbounded growth of anti-spam state.
        if len(spam_events) > 5000:
            cutoff = now - timedelta(minutes=10)
            stale = [k for k, vals in spam_events.items() if (not vals) or vals[-1] < cutoff]
            for k in stale:
                spam_events.pop(k, None)
                muted_until.pop(k, None)
        until = muted_until.get(uid)
        if until and until > now:
            left = int((until - now).total_seconds())
            return True, max(1, left)
        if until and until <= now:
            muted_until.pop(uid, None)
        events = [x for x in spam_events.get(uid, []) if (now - x).total_seconds() <= SPAM_WINDOW_SECONDS]
        events.append(now)
        spam_events[uid] = events
        if len(events) > SPAM_MAX_EVENTS:
            muted_until[uid] = now + timedelta(seconds=MUTE_SECONDS)
            return True, MUTE_SECONDS
        return False, 0

    async def guard_msg(message: Message):
        uid = message.from_user.id if message.from_user else 0
        spam, wait_s = _is_spam(uid)
        if spam:
            await message.answer(f"Слишком много действий. Пауза {wait_s} сек.")
            return None
        if uid == settings.admin_telegram_id:
            access_repo.ensure_admin(uid)
        if message.from_user:
            access_repo.upsert_seen_user(uid, message.from_user.username or "", message.from_user.full_name or "")
        u = access_repo.get_user_access(uid)
        if u is None:
            await message.answer("Профиль не найден.")
            return None
        if u.status == STATUS_BLOCKED:
            await message.answer("Доступ отклонен.")
            return None
        if u.status == STATUS_PENDING:
            access_repo.create_approval_request(uid)
            if uid != settings.admin_telegram_id:
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подтвердить", callback_data=f"aprv_{uid}"), InlineKeyboardButton(text="Отклонить", callback_data=f"rej_{uid}")]])
                await message.bot.send_message(settings.admin_telegram_id, f"Новая заявка: {uid} @{u.username or '-'} {u.full_name or ''}", reply_markup=kb)
            await message.answer("Ожидайте выдачи доступа от администратора.")
            return None
        return u

    async def guard_cb(cb: CallbackQuery):
        uid = cb.from_user.id if cb.from_user else 0
        spam, wait_s = _is_spam(uid)
        if spam:
            await cb.answer(f"Слишком часто. Подождите {wait_s} сек.", show_alert=True)
            return None
        if uid == settings.admin_telegram_id:
            access_repo.ensure_admin(uid)
        u = access_repo.get_user_access(uid)
        if u is None or (u.status != STATUS_APPROVED and not u.is_admin):
            await cb.answer("Нет доступа", show_alert=True)
            return None
        return u

    def labels_for(u) -> list[str]:
        if u.is_admin:
            keys = list(BUTTONS.keys())
        else:
            keys = sorted(access_repo.effective_buttons(u.telegram_id, CONSTRUCTOR_KEY))
            if "usage_help" in BUTTONS:
                keys = sorted(set(keys) | {"usage_help"})
        return [BUTTONS[k] for k in keys if k in BUTTONS]

    def allowed_codes(u):
        hidden_codes = {c.strip().lower() for c in access_repo.list_global_hidden_object_codes() if c.strip()}
        if u.is_admin:
            if not hidden_codes:
                return None
            all_codes = {c.strip() for c in repo.unique_object_codes() if c.strip()}
            return {c for c in all_codes if not any(c.lower().startswith(h) for h in hidden_codes)}
        return {c for c in u.object_codes if c.strip() and not any(c.strip().lower().startswith(h) for h in hidden_codes)}

    def configurable_reports_for_user(u) -> list[str]:
        available = set(KEY_BY_LABEL[label] for label in labels_for(u) if label in KEY_BY_LABEL)
        return [k for k in CONFIGURABLE_REPORT_KEYS if k in available]

    def filter_stage_reports_for_user(u) -> list[str]:
        available = set(KEY_BY_LABEL[label] for label in labels_for(u) if label in KEY_BY_LABEL)
        return [k for k in FILTER_STAGE_REPORT_KEYS if k in available]

    def filter_pipeline_for_user(u, report_key: str) -> list[str]:
        # Empty => opt-out: keep legacy behavior unchanged.
        return access_repo.get_user_filter_pipeline(u.telegram_id, report_key)

    def _norm_stage_key(stage: str) -> str:
        return " ".join((stage or "").strip().upper().split())

    def _stage_label(stage_key: str) -> str:
        return PIPELINE_STAGE_LABEL.get(stage_key, stage_key)

    def _pipeline_enabled(u, report_key: str) -> bool:
        return bool(filter_pipeline_for_user(u, report_key))

    def _pipeline_base_records(u, report_key: str, allowed) -> list[MilestoneRecord]:
        if report_key == "milestone_changes":
            rows = repo.today_changes(allowed)
        else:
            rows = repo.get_records()
        rows = [r for r in rows if is_visible_code(r.object_code)]
        if allowed is not None:
            rows = [r for r in rows if r.object_code in allowed]
        return rows

    def _pipeline_options(report_key: str, stage_key: str, records: list[MilestoneRecord], status_source: str) -> list[str]:
        if stage_key == "object":
            seen: dict[str, str] = {}
            for r in records:
                v = (r.object_name or "").strip()
                if not v:
                    continue
                seen.setdefault(v.lower(), v)
            return sorted(seen.values(), key=lambda x: x.lower())
        if stage_key == "milestone":
            if report_key == "important_milestones":
                return repo.priority_milestones_in_records(records)
            seen: dict[str, str] = {}
            for r in records:
                v = (r.milestone_key or "").strip()
                if not v:
                    continue
                seen.setdefault(v.lower(), v)
            return sorted(seen.values(), key=lambda x: x.lower())
        if stage_key == "status":
            return repo.unique_status_display_values(records, status_source)
        if stage_key == "code":
            return repo.unique_object_codes_in(records)
        if stage_key == "responsible":
            return repo.unique_responsibles_in(records)
        if stage_key == "deviation":
            return repo.unique_deviation_buckets_in(records)
        return []

    def _apply_stage_filter(
        report_key: str,
        stage_key: str,
        records: list[MilestoneRecord],
        selected: set[str] | None,
        status_source: str,
    ) -> list[MilestoneRecord]:
        if not selected:
            return records
        if stage_key == "object":
            sel = {x for x in selected if x}
            return [r for r in records if (r.object_name or "").strip() in sel]
        if stage_key == "milestone":
            sel = {x for x in selected if x}
            if report_key == "important_milestones":
                sel_norm = {_norm_stage_key(x) for x in sel}
                return [r for r in records if _norm_stage_key(r.milestone_key) in sel_norm]
            return [r for r in records if (r.milestone_key or "").strip() in sel]
        if stage_key == "status":
            return repo.filter_records_by_status_displays(records, status_source, set(selected))
        if stage_key == "code":
            return repo.filter_records_by_object_codes(records, set(selected))
        if stage_key == "responsible":
            return repo.filter_records_by_responsibles(records, set(selected))
        if stage_key == "deviation":
            return repo.filter_records_by_deviation_buckets(records, set(selected))
        return records

    def _pipeline_filtered_records(s: dict, upto_cursor: int | None = None) -> list[MilestoneRecord]:
        report_key = (s.get("pl_report_key") or "").strip()
        allowed = s.get("allowed")
        base = list(s.get("pl_base_records") or [])
        stages = list(s.get("pl_pipeline") or [])
        cursor = int(s.get("pl_cursor", 0))
        stop = cursor if upto_cursor is None else max(0, min(upto_cursor, len(stages)))
        status_source = (s.get("pl_status_source") or "today").strip().lower()
        if status_source not in {"today", "yesterday"}:
            status_source = "today"
        sel_map: dict[str, set[str]] = s.get("pl_selected", {}) or {}
        rows = base
        for i, st in enumerate(stages):
            if i >= stop:
                break
            rows = _apply_stage_filter(report_key, st, rows, sel_map.get(st), status_source)
        if allowed is not None:
            rows = [r for r in rows if r.object_code in allowed]
        rows = [r for r in rows if is_visible_code(r.object_code)]
        return rows

    def _kb_pipeline_status_source() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Статус: сегодня", callback_data="pl_src_today"),
                    InlineKeyboardButton(text="Статус: вчера", callback_data="pl_src_yesterday"),
                ],
                [InlineKeyboardButton(text="Назад", callback_data="pl_back")],
                [InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")],
            ]
        )

    def _kb_pipeline_list(items: list[str], selected_values: set[str], page: int) -> InlineKeyboardMarkup:
        selected = _indices_for_selected(items, selected_values)
        kb = _kb_paginated(items, selected, page, "pl_i", "pl_p", "pl_all", "pl_done", "pl_clear", done_text="Далее")
        rows = list(kb.inline_keyboard)
        if rows and rows[-1] and rows[-1][0].callback_data == "menu_cancel":
            rows.insert(-1, [InlineKeyboardButton(text="Назад", callback_data="pl_back")])
        else:
            rows.append([InlineKeyboardButton(text="Назад", callback_data="pl_back")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _pipeline_render_stage(message: Message, u, s: dict) -> None:
        stages: list[str] = list(s.get("pl_pipeline") or [])
        report_key = (s.get("pl_report_key") or "").strip()
        cursor = int(s.get("pl_cursor", 0))
        if cursor < 0:
            cursor = 0
        if cursor >= len(stages):
            await _pipeline_finalize(message, u, s)
            return
        stage_key = stages[cursor]
        s["pl_stage_key"] = stage_key
        s["flow_stage"] = "pipeline"
        before = _pipeline_filtered_records(s, upto_cursor=cursor)
        if stage_key == "status" and not s.get("pl_status_source"):
            await upsert_filter_menu(
                message,
                s,
                f"Этап {cursor+1}/{len(stages)}: {_stage_label(stage_key)}. Выберите источник статуса.",
                _kb_pipeline_status_source(),
            )
            return
        status_source = (s.get("pl_status_source") or "today").strip().lower()
        opts = _pipeline_options(report_key, stage_key, before, status_source)
        sel_map: dict[str, set[str]] = s.get("pl_selected", {}) or {}
        selected_vals = set(sel_map.get(stage_key, set()))
        s["pl_base_list"] = opts
        s["pl_list"] = opts
        s["pl_page"] = 0
        await upsert_filter_menu(
            message,
            s,
            f"Этап {cursor+1}/{len(stages)}: {_stage_label(stage_key)}. Можно не выбирать = все. Напишите текст для поиска.",
            _kb_pipeline_list(opts, selected_vals, 0),
        )

    async def _pipeline_finalize(message: Message, u, s: dict) -> None:
        report_key = (s.get("pl_report_key") or "").strip()
        rows = _pipeline_filtered_records(s, upto_cursor=len(list(s.get("pl_pipeline") or [])))
        vf = visible_fields_for_user(u, report_key)
        if report_key == "important_milestones":
            tuples = repo.important_milestones_from_records(rows, limit=400)
            lines: list[str] = []
            if tuples:
                for weight, rec in tuples:
                    lines.append(_fmt_record(rec, vf))
                    if "importance" in vf:
                        lines.append(f"Важность: {repo.milestone_importance_label(rec.milestone_key)}")
                    lines.append("-----")
            else:
                lines = ["Нет данных."]
            text = "\n".join(lines)
        elif report_key == "milestone_object":
            grouped: dict[str, list[MilestoneRecord]] = defaultdict(list)
            for r in rows[:80]:
                grouped[r.object_name].append(r)
            chunks: list[str] = []
            for obj in sorted(grouped.keys()):
                chunks.append(f"=== {obj} ===")
                for rec in grouped[obj]:
                    chunks.append(_fmt_record(rec, vf))
                    chunks.append("-----")
            text = "\n".join(chunks) or "Нет данных."
        else:
            lines = []
            for r in rows[:200]:
                lines.append(_fmt_record(r, vf))
                lines.append("-----")
            text = "\n".join(lines) or "Нет данных."
        save_last_report(u, report_key, text)
        for part in _chunked_send(text):
            await message.answer(part)
        for k in (
            "pl_report_key",
            "pl_pipeline",
            "pl_cursor",
            "pl_selected",
            "pl_status_source",
            "pl_stage_key",
            "pl_base_records",
            "pl_base_list",
            "pl_list",
            "pl_page",
        ):
            s.pop(k, None)
        s["flow_stage"] = ""

    def user_wants_status_filter(u, report_key: str) -> bool:
        return "status" in access_repo.get_user_optional_filter_stages(u.telegram_id, report_key)

    def lbl_1of3(u, report_key: str) -> str:
        return "Шаг 1/3" if user_wants_status_filter(u, report_key) else "Шаг 1/2"

    def lbl_2of3(u, report_key: str) -> str:
        return "Шаг 2/3" if user_wants_status_filter(u, report_key) else "Шаг 2/2"

    def collect_milestone_info_records(chosen_objs: set[str], chosen_mks: set[str], allowed) -> list[MilestoneRecord]:
        out: list[MilestoneRecord] = []
        for obj in sorted(chosen_objs):
            for r in repo.milestones_for_object(obj, allowed, False)[:120]:
                if chosen_mks and r.milestone_key not in chosen_mks:
                    continue
                out.append(r)
        return out

    def collect_milestone_object_records(s: dict, chosen_mks: set[str], names: set[str]) -> list[MilestoneRecord]:
        rows: list[MilestoneRecord] = []
        allowed = s.get("allowed")
        for rec in repo.get_records():
            if allowed is not None and rec.object_code not in allowed:
                continue
            if not is_visible_code(rec.object_code):
                continue
            if chosen_mks and rec.milestone_key not in chosen_mks:
                continue
            if names and rec.object_name not in names:
                continue
            rows.append(rec)
        return rows

    def _kb_rsc_source(tag: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Статус: сегодня", callback_data=f"rsc_{tag}_td"),
                    InlineKeyboardButton(text="Статус: вчера", callback_data=f"rsc_{tag}_yd"),
                ],
                [InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")],
            ]
        )

    def _kb_ff_panel(status_on: bool) -> InlineKeyboardMarkup:
        label = "✅ Статус: включён" if status_on else "❌ Статус: выключен"
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=label, callback_data="fft_s")],
                [InlineKeyboardButton(text="Применить", callback_data="ffc_apply")],
                [InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")],
            ]
        )

    async def finalize_status_filtered_report(cb: CallbackQuery, u, s: dict) -> None:
        rk = (s.get("rsc_report_key") or "").strip()
        if rk not in RSC_KEY_TO_TAG:
            s["flow_stage"] = ""
            return
        src = (s.get("rsc_status_source") or "today").strip().lower()
        if src not in {"today", "yesterday"}:
            src = "today"
        sel_disp = set(s.get("rsc_sf_sel_values", set()))
        tuples = s.get("rsc_pending_tuples")
        vf = visible_fields_for_user(u, rk)
        lines: list[str] = []

        if rk == "important_milestones" and tuples:
            recs = [r for _, r in tuples]
            filtered_recs = repo.filter_records_by_status_displays(recs, src, sel_disp)
            keep = {r.row_index for r in filtered_recs}
            filtered_tuples = [(w, r) for w, r in tuples if r.row_index in keep]
            if filtered_tuples:
                for weight, rec in filtered_tuples:
                    lines.append(_fmt_record(rec, vf))
                    if "importance" in vf:
                        lines.append(f"Важность: {repo.milestone_importance_label(rec.milestone_key)}")
                    lines.append("-----")
            else:
                lines = ["Нет данных."]
        else:
            recs = list(s.get("rsc_pending_rows") or [])
            filtered_recs = repo.filter_records_by_status_displays(recs, src, sel_disp)
            if rk == "milestone_object":
                grouped: dict[str, list[MilestoneRecord]] = defaultdict(list)
                for r in filtered_recs[:50]:
                    grouped[r.object_name].append(r)
                if grouped:
                    for obj in sorted(grouped.keys()):
                        lines.append(f"=== {obj} ===")
                        for rec in grouped[obj]:
                            lines.append(_fmt_record(rec, vf))
                            lines.append("-----")
                else:
                    lines = ["Нет данных."]
            elif rk == "milestone_changes":
                rows = filter_records_by_global_hidden(filtered_recs)
                if rows:
                    for r in rows[:120]:
                        lines.append(_fmt_record(r, vf))
                        lines.append("-----")
                else:
                    lines = ["Нет данных."]
            else:
                if filtered_recs:
                    for r in filtered_recs:
                        lines.append(_fmt_record(r, vf))
                        lines.append("-----")
                else:
                    lines = ["Нет данных."]

        text = "\n".join(lines) if lines else "Нет данных."
        save_last_report(u, rk, text)
        await safe_edit_text(cb.message, "Готово. Формирую отчёт...")
        for part in _chunked_send(text):
            await cb.message.answer(part)
        s["flow_stage"] = ""
        for k in (
            "rsc_report_key",
            "rsc_pending_rows",
            "rsc_pending_tuples",
            "rsc_status_source",
            "rsc_sf_sel_values",
            "rsc_status_list",
            "rsc_status_base",
            "rsc_status_page",
        ):
            s.pop(k, None)

    def visible_fields_for_user(u, report_key: str) -> set[str]:
        selected = access_repo.get_visible_fields_for_report(u.telegram_id, report_key)
        valid = {k for k in selected if k in REPORT_FIELD_LABEL}
        return valid or set(REPORT_FIELD_KEYS)

    def is_visible_code(code: str) -> bool:
        hidden = {x.lower() for x in access_repo.list_global_hidden_object_codes() if x}
        code_key = (code or "").strip().lower()
        return not any(code_key.startswith(h) for h in hidden)

    def filter_records_by_global_hidden(rows: list[MilestoneRecord]) -> list[MilestoneRecord]:
        return [r for r in rows if is_visible_code(r.object_code)]

    @dp.message(Command("start"))
    async def start(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await message.answer("Готов к работе", reply_markup=_menu(labels_for(u)))

    @dp.message(Command("help"))
    async def help_(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await message.answer("Действия доступны кнопками меню.", reply_markup=_menu(labels_for(u)))

    @dp.message(F.text == BUTTONS["summary"])
    async def summary(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        info = repo.overview(allowed_codes(u))
        text = f"Активных: {info['total_active']}\nПросрочено: {info['overdue']}\nРиск 7 дней: {info['due_7']}"
        save_last_report(u, "summary", text)
        await message.answer(text, reply_markup=_menu(labels_for(u)))

    @dp.message(F.text == BUTTONS["usage_help"])
    async def usage_help(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        labels = labels_for(u)
        lines = [
            "Инструкция по доступным кнопкам:",
            "",
            "Как пользоваться списками:",
            "1) Нажмите нужную кнопку отчёта.",
            "2) Отмечайте пункты галочками.",
            "3) Если список длинный — напишите текст для поиска.",
            "4) Нажмите кнопку подтверждения внизу (Показать/Далее/Применить).",
            "",
            "В Конструкторе пункт «Настроить фильтрацию» задаёт дополнительный шаг по статусу только для выбранных отчётов; по умолчанию ничего не меняется.",
            "",
        ]
        for label in labels:
            key = KEY_BY_LABEL.get(label, "")
            desc = BUTTON_HELP.get(key, "Описание не задано.")
            lines.append(f"• {label}: {desc}")
        lines.append("")
        lines.append("Если бот пишет «Сессия устарела» — просто заново откройте нужную кнопку.")
        await message.answer("\n".join(lines), reply_markup=_menu(labels))

    @dp.message(F.text == BUTTONS["responsibles"])
    async def responsibles(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        rows = repo.responsibles_with_objects(allowed_codes(u))
        lines = [f"Ответственных: {len(rows)}", ""]
        for r in rows:
            lines.append(f"{r['responsible']}\nПочта: {r['email']}")
            for rr in r["roles"]:
                lines.append(f"  {rr['role']}: {', '.join(rr['objects'][:8]) or '-'}")
            lines.append("---")
        text = "\n".join(lines)
        save_last_report(u, "responsibles", text)
        for part in _chunked_send(text):
            await message.answer(part, reply_markup=_menu(labels_for(u)))

    @dp.message(F.text == BUTTONS["important_milestones"])
    async def important_milestones(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        if _pipeline_enabled(u, "important_milestones"):
            s = S(message.chat.id, message.from_user.id)
            rk = "important_milestones"
            s["pl_report_key"] = rk
            s["pl_pipeline"] = filter_pipeline_for_user(u, rk)
            s["pl_cursor"] = 0
            s["pl_selected"] = {}
            s.pop("pl_status_source", None)
            s["allowed"] = allowed_codes(u)
            s["pl_base_records"] = _pipeline_base_records(u, rk, s.get("allowed"))
            await _pipeline_render_stage(message, u, s)
            return
        names = repo.unique_object_names(allowed_codes(u))
        if not names:
            await message.answer("Список объектов пуст.", reply_markup=_menu(labels_for(u)))
            return
        s = S(message.chat.id, message.from_user.id)
        s["flow"] = "important_milestones"
        s["flow_stage"] = "important_stage1"
        s["allowed"] = allowed_codes(u)
        s["im_objects"] = names
        s["im_objects_base"] = names
        s["im_obj_sel_values"] = set()
        s["im_obj_page"] = 0
        sent = await message.answer(
            f"{lbl_1of3(u, 'important_milestones')}: выберите объекты:",
            reply_markup=_kb_paginated(names, set(), 0, "poi", "pop", "po_all", "po_done", "po_clear"),
        )
        s["menu_chat_id"] = sent.chat.id
        s["menu_message_id"] = sent.message_id
        await message.answer("Можно писать часть названия объекта для фильтрации.")

    @dp.callback_query(F.data.startswith("po"))
    async def cb_priority_objects(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        s["menu_chat_id"] = cb.message.chat.id
        s["menu_message_id"] = cb.message.message_id
        arr = s.get("im_objects", [])
        if not arr:
            await cb.answer("Сессия устарела. Откройте меню заново.", show_alert=True)
            return
        sel_vals = set(s.get("im_obj_sel_values", set()))
        sel = _indices_for_selected(arr, sel_vals)
        d = cb.data
        if d.startswith("pop_"):
            p = int(d.split("_", 1)[1])
            s["im_obj_page"] = p
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, p, "poi", "pop", "po_all", "po_done", "po_clear"))
        elif d.startswith("poi_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(arr):
                v = arr[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            s["im_obj_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            page = int(s.get("im_obj_page", i // PAGE_SIZE))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "poi", "pop", "po_all", "po_done", "po_clear"))
        elif d == "po_all":
            sel_vals.update(arr)
            s["im_obj_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            page = int(s.get("im_obj_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "poi", "pop", "po_all", "po_done", "po_clear"))
        elif d == "po_clear":
            s["im_obj_sel_values"] = set()
            page = int(s.get("im_obj_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, set(), page, "poi", "pop", "po_all", "po_done", "po_clear"))
        elif d == "po_done":
            if not sel_vals:
                await cb.answer("Выберите минимум один объект", show_alert=True)
                return
            s["im_selected_objects"] = set(sel_vals)
            milestones = repo.priority_milestones_for_objects(set(sel_vals), s.get("allowed"))
            if not milestones:
                await cb.answer("Для выбранных объектов нет вех из списка 15.", show_alert=True)
                return
            s["im_milestones"] = milestones
            s["im_milestones_base"] = milestones
            s["im_mk_sel_values"] = set()
            s["im_mk_page"] = 0
            s["flow_stage"] = "important_stage2"
            await safe_edit_text(
                cb.message,
                f"{lbl_2of3(u, 'important_milestones')}: выберите важные вехи:",
                _kb_paginated(milestones, set(), 0, "pmi", "pmp", "pm_all", "pm_done", "pm_clear"),
            )
        await cb.answer()

    @dp.callback_query(F.data.startswith("pm"))
    async def cb_priority_milestones(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        s["menu_chat_id"] = cb.message.chat.id
        s["menu_message_id"] = cb.message.message_id
        arr = s.get("im_milestones", [])
        if not arr:
            await cb.answer("Сессия устарела. Откройте меню заново.", show_alert=True)
            return
        sel_vals = set(s.get("im_mk_sel_values", set()))
        sel = _indices_for_selected(arr, sel_vals)
        d = cb.data
        if d.startswith("pmp_"):
            p = int(d.split("_", 1)[1])
            s["im_mk_page"] = p
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, p, "pmi", "pmp", "pm_all", "pm_done", "pm_clear"))
        elif d.startswith("pmi_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(arr):
                v = arr[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            s["im_mk_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            page = int(s.get("im_mk_page", i // PAGE_SIZE))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "pmi", "pmp", "pm_all", "pm_done", "pm_clear"))
        elif d == "pm_all":
            sel_vals.update(arr)
            s["im_mk_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            page = int(s.get("im_mk_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "pmi", "pmp", "pm_all", "pm_done", "pm_clear"))
        elif d == "pm_clear":
            s["im_mk_sel_values"] = set()
            page = int(s.get("im_mk_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, set(), page, "pmi", "pmp", "pm_all", "pm_done", "pm_clear"))
        elif d == "pm_done":
            if not sel_vals:
                await cb.answer("Выберите минимум одну веху", show_alert=True)
                return
            selected_objects = set(s.get("im_selected_objects", set()))
            selected_mks = set(sel_vals)
            rows = repo.important_milestones(selected_objects, selected_mks, s.get("allowed"), limit=400)
            if user_wants_status_filter(u, "important_milestones"):
                if not rows:
                    await cb.answer("Нет данных для выбранных фильтров.", show_alert=True)
                    return
                s["rsc_report_key"] = "important_milestones"
                s["rsc_pending_tuples"] = rows
                s.pop("rsc_pending_rows", None)
                s["rsc_sf_sel_values"] = set()
                s["flow_stage"] = "status_wait_source"
                tag = RSC_KEY_TO_TAG["important_milestones"]
                await safe_edit_text(
                    cb.message,
                    f"{lbl_2of3(u, 'important_milestones')}: вехи выбраны. Шаг 3/3: источник статуса для дополнительного фильтра.",
                    _kb_rsc_source(tag),
                )
                await cb.answer()
                return
            if rows:
                lines = []
                vf = visible_fields_for_user(u, "important_milestones")
                for weight, rec in rows:
                    lines.append(_fmt_record(rec, vf))
                    if "importance" in vf:
                        lines.append(f"Важность: {repo.milestone_importance_label(rec.milestone_key)}")
                    lines.append("-----")
            else:
                lines = ["Нет данных."]
            await safe_edit_text(cb.message, "Готово. Формирую отчёт...")
            text = "\n".join(lines)
            save_last_report(u, "important_milestones", text)
            for part in _chunked_send(text):
                await cb.message.answer(part)
            s["flow_stage"] = ""
        await cb.answer()

    @dp.message(F.text.in_([BUTTONS["milestone_info"], BUTTONS["milestone_changes"]]))
    async def pick_object(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        changes = message.text == BUTTONS["milestone_changes"]
        rk = "milestone_changes" if changes else "milestone_info"
        if _pipeline_enabled(u, rk):
            s = S(message.chat.id, message.from_user.id)
            s["pl_report_key"] = rk
            s["pl_pipeline"] = filter_pipeline_for_user(u, rk)
            s["pl_cursor"] = 0
            s["pl_selected"] = {}
            s.pop("pl_status_source", None)
            s["allowed"] = allowed_codes(u)
            s["pl_base_records"] = _pipeline_base_records(u, rk, s.get("allowed"))
            await _pipeline_render_stage(message, u, s)
            return
        names = repo.objects_with_changes_today(allowed_codes(u)) if changes else repo.unique_object_names(allowed_codes(u))
        if not names:
            await message.answer("Список пуст.", reply_markup=_menu(labels_for(u)))
            return
        s = S(message.chat.id, message.from_user.id)
        s["await_note"] = False
        s["await_edit_note"] = 0
        s["flow"] = "obj_changes" if changes else "obj_info"
        s["flow_stage"] = "obj_stage1"
        s["allowed"] = allowed_codes(u)
        s["list"] = names
        s["base_list"] = names
        s["ob_sel_values"] = set()
        s["changes_mks"] = None
        s["changes_mks_base"] = None
        s["ck_sel_values"] = set()
        s["sel"] = set()
        sent = await message.answer("Выберите объект:", reply_markup=_kb_paginated(names, set(), 0, "obi", "obp", "ob_all", "ob_done", "ob_clear"))
        s["menu_chat_id"] = sent.chat.id
        s["menu_message_id"] = sent.message_id
        await message.answer("Можно написать часть названия/ключа вехи текстом — я подберу ближайшие варианты.")

    @dp.message(F.text == BUTTONS["milestone_object"])
    async def milestone_then_obj(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        if _pipeline_enabled(u, "milestone_object"):
            s = S(message.chat.id, message.from_user.id)
            rk = "milestone_object"
            s["pl_report_key"] = rk
            s["pl_pipeline"] = filter_pipeline_for_user(u, rk)
            s["pl_cursor"] = 0
            s["pl_selected"] = {}
            s.pop("pl_status_source", None)
            s["allowed"] = allowed_codes(u)
            s["pl_base_records"] = _pipeline_base_records(u, rk, s.get("allowed"))
            await _pipeline_render_stage(message, u, s)
            return
        milestones = repo.unique_milestones(allowed_codes(u))
        if not milestones:
            await message.answer("Нет вех.", reply_markup=_menu(labels_for(u)))
            return
        s = S(message.chat.id, message.from_user.id)
        s["await_note"] = False
        s["await_edit_note"] = 0
        s["flow"] = "milestone_object"
        s["flow_stage"] = "mo_stage1"
        s["allowed"] = allowed_codes(u)
        s["all_milestones"] = milestones
        s["mk_sel_values"] = set()
        s["sel"] = set()
        s["mk_page"] = 0
        s["mk_text_search"] = True
        s["milestones"] = milestones
        sent = await message.answer(f"{lbl_1of3(u, 'milestone_object')}: выберите веху:", reply_markup=_kb_paginated(milestones, set(), 0, "mki", "mkp", "mk_all", "mk_done", "mk_clear"))
        s["menu_chat_id"] = sent.chat.id
        s["menu_message_id"] = sent.message_id
        await message.answer("Можно написать часть названия/ключа вехи текстом — я подберу ближайшие варианты.")

    @dp.callback_query(F.data.startswith("mk"))
    async def cb_mk(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        s["menu_chat_id"] = cb.message.chat.id
        s["menu_message_id"] = cb.message.message_id
        arr = s.get("milestones", [])
        if not arr:
            await cb.answer("Сессия устарела. Откройте меню заново.", show_alert=True)
            return
        sel_vals = set(s.get("mk_sel_values", set()))
        sel = _indices_for_selected(arr, sel_vals)
        d = cb.data
        if d.startswith("mkp_"):
            p = int(d.split("_", 1)[1])
            s["mk_page"] = p
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, p, "mki", "mkp", "mk_all", "mk_done", "mk_clear"))
        elif d.startswith("mki_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(arr):
                v = arr[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            s["mk_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            s["sel"] = sel
            page = int(s.get("mk_page", i // PAGE_SIZE))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "mki", "mkp", "mk_all", "mk_done", "mk_clear"))
        elif d == "mk_all":
            sel_vals.update(arr)
            s["mk_sel_values"] = sel_vals
            s["sel"] = _indices_for_selected(arr, sel_vals)
            page = int(s.get("mk_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, s["sel"], page, "mki", "mkp", "mk_all", "mk_done", "mk_clear"))
        elif d == "mk_clear":
            s["mk_sel_values"] = set()
            s["sel"] = set()
            page = int(s.get("mk_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, set(), page, "mki", "mkp", "mk_all", "mk_done", "mk_clear"))
        elif d == "mk_done":
            if not sel_vals:
                await cb.answer("Выберите веху", show_alert=True)
                return
            chosen_mks = set(sel_vals)
            all_objects: set[str] = set()
            for mk in chosen_mks:
                all_objects.update(repo.objects_for_milestone(mk, s.get("allowed")))
            objs = sorted(all_objects, key=lambda x: x.lower())
            s["chosen_mks"] = chosen_mks
            s["list"] = objs
            s["base_mo_list"] = objs
            s["mo_sel_values"] = set()
            s["sel_obj"] = set()
            s["mo_page"] = 0
            s["mk_text_search"] = False
            s["flow_stage"] = "mo_stage2"
            await safe_edit_text(
                cb.message,
                f"{lbl_2of3(u, 'milestone_object')}: выберите объекты для выбранных вех ({len(chosen_mks)}).",
                _kb_paginated(objs, set(), 0, "moi", "mop", "mo_all", "mo_done", "mo_clear"),
            )
            s["menu_chat_id"] = cb.message.chat.id
            s["menu_message_id"] = cb.message.message_id
        await cb.answer()

    @dp.callback_query(F.data.startswith("mo"))
    async def cb_mo(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        s["menu_chat_id"] = cb.message.chat.id
        s["menu_message_id"] = cb.message.message_id
        arr = s.get("list", [])
        if not arr:
            await cb.answer("Сессия устарела. Откройте меню заново.", show_alert=True)
            return
        sel_vals = set(s.get("mo_sel_values", set()))
        sel = _indices_for_selected(arr, sel_vals)
        d = cb.data
        if d.startswith("mop_"):
            p = int(d.split("_", 1)[1])
            s["mo_page"] = p
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, p, "moi", "mop", "mo_all", "mo_done", "mo_clear"))
        elif d.startswith("moi_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(arr):
                v = arr[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            s["mo_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            s["sel_obj"] = sel
            page = int(s.get("mo_page", i // PAGE_SIZE))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "moi", "mop", "mo_all", "mo_done", "mo_clear"))
        elif d == "mo_all":
            sel_vals.update(arr)
            s["mo_sel_values"] = sel_vals
            s["sel_obj"] = _indices_for_selected(arr, sel_vals)
            page = int(s.get("mo_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, s["sel_obj"], page, "moi", "mop", "mo_all", "mo_done", "mo_clear"))
        elif d == "mo_clear":
            s["mo_sel_values"] = set()
            s["sel_obj"] = set()
            page = int(s.get("mo_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, set(), page, "moi", "mop", "mo_all", "mo_done", "mo_clear"))
        elif d == "mo_done":
            vf = visible_fields_for_user(u, "milestone_object")
            names = set(sel_vals)
            chosen_mks = set(s.get("chosen_mks", set()))
            recs = collect_milestone_object_records(s, chosen_mks, names)
            if user_wants_status_filter(u, "milestone_object") and recs:
                s["rsc_report_key"] = "milestone_object"
                s["rsc_pending_rows"] = recs
                s.pop("rsc_pending_tuples", None)
                s["rsc_sf_sel_values"] = set()
                s["flow_stage"] = "status_wait_source"
                tag = RSC_KEY_TO_TAG["milestone_object"]
                await safe_edit_text(
                    cb.message,
                    f"{lbl_2of3(u, 'milestone_object')}: объекты выбраны. Шаг 3/3: источник статуса для дополнительного фильтра.",
                    _kb_rsc_source(tag),
                )
                await cb.answer()
                return
            rows = recs
            grouped: dict[str, list[MilestoneRecord]] = defaultdict(list)
            for r in rows[:50]:
                grouped[r.object_name].append(r)
            chunks = []
            for obj in sorted(grouped.keys()):
                chunks.append(f"=== {obj} ===")
                for rec in grouped[obj]:
                    chunks.append(_fmt_record(rec, vf))
                    chunks.append("-----")
            text = "\n".join(chunks) or "Нет данных."
            save_last_report(u, "milestone_object", text)
            for part in _chunked_send(text):
                await cb.message.answer(part)
            s["flow_stage"] = ""
        await cb.answer()

    @dp.callback_query(F.data.startswith("ob"))
    async def cb_ob(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        s["menu_chat_id"] = cb.message.chat.id
        s["menu_message_id"] = cb.message.message_id
        arr = s.get("list", [])
        if not arr:
            await cb.answer("Сессия устарела. Откройте меню заново.", show_alert=True)
            return
        sel_vals = set(s.get("ob_sel_values", set()))
        sel = _indices_for_selected(arr, sel_vals)
        d = cb.data
        if d.startswith("obp_"):
            p = int(d.split("_", 1)[1])
            s["ob_page"] = p
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, p, "obi", "obp", "ob_all", "ob_done", "ob_clear"))
        elif d.startswith("obi_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(arr):
                v = arr[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            s["ob_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            s["sel"] = sel
            page = int(s.get("ob_page", i // PAGE_SIZE))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "obi", "obp", "ob_all", "ob_done", "ob_clear"))
        elif d == "ob_all":
            sel_vals.update(arr)
            s["ob_sel_values"] = sel_vals
            s["sel"] = _indices_for_selected(arr, sel_vals)
            page = int(s.get("ob_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, s["sel"], page, "obi", "obp", "ob_all", "ob_done", "ob_clear"))
        elif d == "ob_clear":
            s["ob_sel_values"] = set()
            s["sel"] = set()
            page = int(s.get("ob_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, set(), page, "obi", "obp", "ob_all", "ob_done", "ob_clear"))
        elif d == "ob_done":
            if not sel_vals:
                await cb.answer("Выберите объекты", show_alert=True)
                return
            only_changed = s.get("flow") == "obj_changes"
            chosen_objs = set(sel_vals)
            if only_changed:
                mk_list = repo.milestones_with_changes_for_objects(chosen_objs, s.get("allowed"))
                s["changes_objs"] = chosen_objs
                s["changes_mks"] = mk_list
                s["changes_mks_base"] = mk_list
                s["ck_sel_values"] = set()
                s["changes_sel"] = set()
                s["changes_page"] = 0
                s["flow_stage"] = "obj_changes_stage2"
                await cb.message.edit_text(
                    f"{lbl_2of3(u, 'milestone_changes')}: выберите вехи (измененные) для объектов ({len(chosen_objs)}). Можно не выбирать = все.",
                    reply_markup=_kb_paginated(mk_list, set(), 0, "cki", "ckp", "ck_all", "ck_done", "ck_clear"),
                )
                s["menu_chat_id"] = cb.message.chat.id
                s["menu_message_id"] = cb.message.message_id
                await cb.answer()
                return
            mk_set: set[str] = set()
            for obj in chosen_objs:
                for r in repo.milestones_for_object(obj, s.get("allowed"), False):
                    if r.milestone_key:
                        mk_set.add(r.milestone_key)
            mk_list = sorted(mk_set, key=lambda x: x.lower())
            s["obj_info_objs"] = chosen_objs
            s["obj_info_mks_base"] = mk_list
            s["obj_info_mks"] = mk_list
            s["obj_info_mk_sel_values"] = set()
            s["obj_info_mk_page"] = 0
            s["flow_stage"] = "obj_info_stage2"
            await safe_edit_text(
                cb.message,
                f"{lbl_2of3(u, 'milestone_info')}: выберите вехи по объектам ({len(chosen_objs)}). Можно не выбирать = все.",
                _kb_paginated(mk_list, set(), 0, "oki", "okp", "ok_all", "ok_done", "ok_clear"),
            )
            s["menu_chat_id"] = cb.message.chat.id
            s["menu_message_id"] = cb.message.message_id
        await cb.answer()

    @dp.callback_query(F.data.startswith("ok"))
    async def cb_ok(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        s["menu_chat_id"] = cb.message.chat.id
        s["menu_message_id"] = cb.message.message_id
        arr = s.get("obj_info_mks", [])
        if arr is None:
            await cb.answer("Сессия устарела. Откройте меню заново.", show_alert=True)
            return
        sel_vals = set(s.get("obj_info_mk_sel_values", set()))
        sel = _indices_for_selected(arr, sel_vals)
        d = cb.data
        if d.startswith("okp_"):
            p = int(d.split("_", 1)[1])
            s["obj_info_mk_page"] = p
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, p, "oki", "okp", "ok_all", "ok_done", "ok_clear"))
        elif d.startswith("oki_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(arr):
                v = arr[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            s["obj_info_mk_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            page = int(s.get("obj_info_mk_page", i // PAGE_SIZE))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "oki", "okp", "ok_all", "ok_done", "ok_clear"))
        elif d == "ok_all":
            sel_vals.update(arr)
            s["obj_info_mk_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            page = int(s.get("obj_info_mk_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "oki", "okp", "ok_all", "ok_done", "ok_clear"))
        elif d == "ok_clear":
            s["obj_info_mk_sel_values"] = set()
            page = int(s.get("obj_info_mk_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, set(), page, "oki", "okp", "ok_all", "ok_done", "ok_clear"))
        elif d == "ok_done":
            vf = visible_fields_for_user(u, "milestone_info")
            chosen_objs = set(s.get("obj_info_objs", set()))
            chosen_mks = set(sel_vals)
            if user_wants_status_filter(u, "milestone_info"):
                recs = collect_milestone_info_records(chosen_objs, chosen_mks, s.get("allowed"))
                if recs:
                    s["rsc_report_key"] = "milestone_info"
                    s["rsc_pending_rows"] = recs
                    s.pop("rsc_pending_tuples", None)
                    s["rsc_sf_sel_values"] = set()
                    s["flow_stage"] = "status_wait_source"
                    tag = RSC_KEY_TO_TAG["milestone_info"]
                    await safe_edit_text(
                        cb.message,
                        f"{lbl_2of3(u, 'milestone_info')}: вехи выбраны. Шаг 3/3: источник статуса для дополнительного фильтра.",
                        _kb_rsc_source(tag),
                    )
                    await cb.answer()
                    return
            lines = []
            for obj in sorted(chosen_objs):
                rows = repo.milestones_for_object(obj, s.get("allowed"), False)
                for r in rows[:120]:
                    if chosen_mks and r.milestone_key not in chosen_mks:
                        continue
                    lines.append(_fmt_record(r, vf))
                    lines.append("-----")
            await safe_edit_text(cb.message, "Готово. Формирую отчёт...")
            text = "\n".join(lines) or "Нет данных."
            save_last_report(u, "milestone_info", text)
            for part in _chunked_send(text):
                await cb.message.answer(part)
            s["flow_stage"] = ""
        await cb.answer()

    @dp.callback_query(F.data.startswith("ck"))
    async def cb_ck(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        s["menu_chat_id"] = cb.message.chat.id
        s["menu_message_id"] = cb.message.message_id
        arr = s.get("changes_mks", [])
        if arr is None:
            await cb.answer("Сессия устарела. Откройте меню заново.", show_alert=True)
            return
        sel_vals = set(s.get("ck_sel_values", set()))
        sel = _indices_for_selected(arr, sel_vals)
        d = cb.data
        if d.startswith("ckp_"):
            p = int(d.split("_", 1)[1])
            s["changes_page"] = p
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, p, "cki", "ckp", "ck_all", "ck_done", "ck_clear"))
        elif d.startswith("cki_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(arr):
                v = arr[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            s["ck_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            s["changes_sel"] = sel
            page = int(s.get("changes_page", i // PAGE_SIZE))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "cki", "ckp", "ck_all", "ck_done", "ck_clear"))
        elif d == "ck_all":
            sel_vals.update(arr)
            s["ck_sel_values"] = sel_vals
            s["changes_sel"] = _indices_for_selected(arr, sel_vals)
            page = int(s.get("changes_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, s["changes_sel"], page, "cki", "ckp", "ck_all", "ck_done", "ck_clear"))
        elif d == "ck_clear":
            s["ck_sel_values"] = set()
            s["changes_sel"] = set()
            page = int(s.get("changes_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, set(), page, "cki", "ckp", "ck_all", "ck_done", "ck_clear"))
        elif d == "ck_done":
            objs = s.get("changes_objs", set())
            mk_keys = set(sel_vals) if sel_vals else None
            rows = repo.changed_by_objects_then_milestones(objs, mk_keys, s.get("allowed"))
            rows = filter_records_by_global_hidden(rows)
            if user_wants_status_filter(u, "milestone_changes") and rows:
                s["rsc_report_key"] = "milestone_changes"
                s["rsc_pending_rows"] = rows
                s.pop("rsc_pending_tuples", None)
                s["rsc_sf_sel_values"] = set()
                s["flow_stage"] = "status_wait_source"
                tag = RSC_KEY_TO_TAG["milestone_changes"]
                await safe_edit_text(
                    cb.message,
                    f"{lbl_2of3(u, 'milestone_changes')}: вехи выбраны. Шаг 3/3: источник статуса для дополнительного фильтра.",
                    _kb_rsc_source(tag),
                )
                await cb.answer()
                return
            await safe_edit_text(cb.message, "Готово. Формирую отчёт...")
            vf = visible_fields_for_user(u, "milestone_changes")
            lines = []
            for r in rows[:120]:
                lines.append(_fmt_record(r, vf))
                lines.append("-----")
            text = "\n".join(lines) or "Нет данных."
            save_last_report(u, "milestone_changes", text)
            for part in _chunked_send(text):
                await cb.message.answer(part)
            s["flow_stage"] = ""
        await cb.answer()

    @dp.message(F.text == BUTTONS["hot_week"])
    async def hot_week(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        s = S(message.chat.id, message.from_user.id)
        s["flow_stage"] = "hot_week_base"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Относительно сегодняшней даты", callback_data="hwb_today")],
                [InlineKeyboardButton(text="Относительно целевой даты", callback_data="hwb_target")],
                [InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")],
            ]
        )
        sent = await message.answer("Шаг 1/2: выберите базу сравнения для просрочки.", reply_markup=kb)
        s["menu_chat_id"] = sent.chat.id
        s["menu_message_id"] = sent.message_id

    @dp.callback_query(F.data.startswith("hwb_"))
    async def hot_week_pick_base(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        base = cb.data.split("_", 1)[1]
        if base not in {"today", "target"}:
            await cb.answer("Неверный выбор", show_alert=True)
            return
        s["hw_base"] = base
        s["flow_stage"] = "hot_week_field"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Дата исполнения (с учетом факта)", callback_data="hwd_e")],
                [InlineKeyboardButton(text="Прогноз/Факт", callback_data="hwd_f")],
                [InlineKeyboardButton(text="Дата исполнения (целевая)", callback_data="hwd_g")],
                [InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")],
            ]
        )
        await safe_edit_text(cb.message, "Шаг 2/2: выберите дату, которую проверяем на просрочку.", kb)
        await cb.answer()

    @dp.message(F.text == BUTTONS["friday"])
    async def friday(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Дата сегодня", callback_data="fwd_today")],
                [InlineKeyboardButton(text="Дата вчера", callback_data="fwd_yesterday")],
                [InlineKeyboardButton(text="Целевая дата", callback_data="fwd_target")],
                [InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")],
            ]
        )
        await message.answer("Выберите тип даты для «Пятничного списка».", reply_markup=kb)

    @dp.callback_query(F.data.startswith("hwd_"))
    async def hot_week_by_date(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        field_mode = cb.data.split("_", 1)[1]
        if field_mode not in {"e", "f", "g"}:
            await cb.answer("Неверный тип даты", show_alert=True)
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        base = s.get("hw_base", "today")
        if base not in {"today", "target"}:
            base = "today"
        # Rule from requirements: for selected target date, compare against today.
        if field_mode == "g" and base == "target":
            base = "today"
        today = date.today()
        rows = []
        allowed = allowed_codes(u)
        for r in repo.get_records():
            if allowed is not None and r.object_code not in allowed:
                continue
            if not is_visible_code(r.object_code):
                continue
            candidate = _hot_week_field_date(r, field_mode)
            if candidate is None:
                continue
            if base == "target":
                if r.target_date is None:
                    continue
                if candidate <= r.target_date:
                    continue
                delta = (candidate - r.target_date).days
            else:
                if candidate >= today:
                    continue
                delta = (today - candidate).days
            rows.append((delta, r))
        rows.sort(key=lambda x: -x[0])
        vf = visible_fields_for_user(u, "hot_week")
        text = "\n\n".join(_fmt_record(r, vf) for _, r in rows[:25]) or "Пусто."
        base_label = "сегодняшней дате" if base == "today" else "целевой дате"
        field_label = {"e": "дате исполнения (с учетом факта)", "f": "прогнозу/факту", "g": "дате исполнения (целевой)"}[field_mode]
        text = f"Горящие неделя: просрочка по {field_label} относительно {base_label}\n\n{text}"
        save_last_report(u, "hot_week", text)
        await cb.message.edit_text("Готово. Формирую отчёт...")
        for part in _chunked_send(text):
            await cb.message.answer(part, reply_markup=_menu(labels_for(u)))
        s["flow_stage"] = ""
        await cb.answer()

    @dp.callback_query(F.data.startswith("fwd_"))
    async def friday_by_date(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        mode = cb.data.split("_", 1)[1]
        if mode not in {"today", "yesterday", "target"}:
            await cb.answer("Неверный тип даты", show_alert=True)
            return
        today = date.today()
        out = []
        for rec in repo.get_records():
            if allowed_codes(u) is not None and rec.object_code not in allowed_codes(u):
                continue
            if not is_visible_code(rec.object_code):
                continue
            dt = _date_by_mode(rec, mode)
            if dt is None:
                continue
            days = (dt - today).days
            dev_raw = (rec.deviation or "").strip()
            dev = int(float(dev_raw.replace(",", "."))) if dev_raw.replace("-", "").replace(",", "").replace(".", "").isdigit() else 0
            if days < 0 or days <= 7 or abs(dev) >= 7:
                out.append((days, -abs(dev), rec))
        out.sort(key=lambda x: (x[0], x[1]))
        vf = visible_fields_for_user(u, "friday")
        text = "\n\n".join(_fmt_record(r, vf) for _, __, r in out[:35]) or "Пусто."
        text = f"Пятничный список ({_date_mode_label(mode)}):\n\n{text}"
        save_last_report(u, "friday", text)
        await cb.message.edit_text("Готово. Формирую отчёт...")
        for part in _chunked_send(text):
            await cb.message.answer(part, reply_markup=_menu(labels_for(u)))
        await cb.answer()

    @dp.message(F.text == BUTTONS["notes"])
    async def notes_menu(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        notes = access_repo.list_notes(limit=30)
        rows = [[InlineKeyboardButton(text=f"#{n.id} {n.author_name}: {(n.text or '')[:20]}", callback_data=f"ntl_{n.id}")] for n in notes]
        rows.append([InlineKeyboardButton(text="Добавить новый", callback_data="nt_add")])
        rows.append([InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")])
        await message.answer("Комментарий: выберите комментарий или действие.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @dp.callback_query(F.data == "nt_add")
    async def note_add_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        s["await_note"] = True
        s["await_edit_note"] = 0
        await cb.message.answer("Отправьте текст нового комментария.")
        await cb.answer()

    @dp.callback_query(F.data.startswith("ntl_"))
    async def note_select_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        note_id = int(cb.data.split("_", 1)[1])
        note = access_repo.get_note(note_id)
        if note is None:
            await cb.answer("Комментарий не найден", show_alert=True)
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        s["selected_note_id"] = note_id
        text = (
            f"Комментарий #{note.id}\n"
            f"Автор: {note.author_name}\n"
            f"Объект: {note.object_name or '-'}\n"
            f"Веха: {note.milestone_key or '-'}\n"
            f"Текст: {note.text}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Редактировать", callback_data="nt_edit"), InlineKeyboardButton(text="Удалить", callback_data="nt_del")],
            [InlineKeyboardButton(text="Привязать к объекту", callback_data="nt_bind_obj"), InlineKeyboardButton(text="Привязать к вехе", callback_data="nt_bind_mk")],
            [InlineKeyboardButton(text="К списку", callback_data="nt_back")],
        ])
        await cb.message.edit_text(text, reply_markup=kb)
        await cb.answer()

    @dp.callback_query(F.data == "nt_back")
    async def note_back_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        notes = access_repo.list_notes(limit=30)
        rows = [[InlineKeyboardButton(text=f"#{n.id} {n.author_name}: {(n.text or '')[:20]}", callback_data=f"ntl_{n.id}")] for n in notes]
        rows.append([InlineKeyboardButton(text="Добавить новый", callback_data="nt_add")])
        rows.append([InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")])
        await cb.message.edit_text("Комментарий: выберите комментарий или действие.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await cb.answer()

    @dp.callback_query(F.data == "nt_edit")
    async def note_edit_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        nid = int(s.get("selected_note_id", 0))
        if nid <= 0:
            await cb.answer("Сначала выберите комментарий", show_alert=True)
            return
        s["await_edit_note"] = nid
        await cb.message.answer("Отправьте новый текст комментария.")
        await cb.answer()

    @dp.callback_query(F.data == "nt_del")
    async def note_delete_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        nid = int(s.get("selected_note_id", 0))
        ok = access_repo.delete_note(nid, u.telegram_id, u.is_admin) if nid > 0 else False
        await cb.message.answer("Комментарий удалён." if ok else "Удаление недоступно.")
        await cb.answer()

    @dp.callback_query(F.data == "nt_bind_obj")
    async def note_bind_obj_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        nid = int(s.get("selected_note_id", 0))
        if nid <= 0:
            await cb.answer("Сначала выберите комментарий", show_alert=True)
            return
        names = repo.unique_object_names(allowed_codes(u))
        s["bind_note_id"] = nid
        s["bind_objects"] = names
        s["bind_sel"] = set()
        s["bind_page"] = 0
        await cb.message.answer("Выберите объект для привязки:", reply_markup=_kb_paginated(names, set(), 0, "bni", "bnp", "bn_all", "bn_done", "bn_clear"))
        await cb.answer()

    @dp.callback_query(F.data == "nt_bind_mk")
    async def note_bind_mk_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        nid = int(s.get("selected_note_id", 0))
        if nid <= 0:
            await cb.answer("Сначала выберите комментарий", show_alert=True)
            return
        mks = repo.unique_milestones(allowed_codes(u))
        s["bind_note_id"] = nid
        s["bind_mks"] = mks
        s["bind_mk_sel"] = set()
        s["bind_mk_page"] = 0
        await cb.message.answer("Выберите веху для привязки:", reply_markup=_kb_paginated(mks, set(), 0, "bmi", "bmp", "bm_all", "bm_done", "bm_clear"))
        await cb.answer()

    @dp.message(F.text == BUTTONS["constructor"])
    async def constructor(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Кнопки", callback_data="ctor_ui")],
                [InlineKeyboardButton(text="Данные", callback_data="ctor_data")],
                [InlineKeyboardButton(text="Настроить фильтрацию", callback_data="ctor_filters")],
                [InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")],
            ]
        )
        await message.answer("Конструктор: выберите режим настройки.", reply_markup=kb)

    @dp.callback_query(F.data == "ctor_ui")
    async def constructor_ui_open(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        allowed = u.allowed_buttons or set(access_repo.all_button_keys())
        items = [k for k in sorted(allowed) if k in BUTTONS and k != CONSTRUCTOR_KEY]
        hidden = {i for i, k in enumerate(items) if k in u.hidden_buttons}
        s = S(cb.message.chat.id, cb.from_user.id)
        s["c_items"] = items
        s["c_sel"] = hidden
        labels = [BUTTONS[k] for k in items]
        await cb.message.edit_text(
            "Интерфейс: скройте ненужные кнопки меню.",
            reply_markup=_kb_paginated(labels, hidden, 0, "chi", "chp", "ch_all", "ch_done", "ch_clear"),
        )
        await cb.answer()

    @dp.callback_query(F.data == "ctor_data")
    async def constructor_data_open(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        reports = configurable_reports_for_user(u)
        if not reports:
            await cb.answer("Нет доступных отчётов для настройки.", show_alert=True)
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        s["dr_reports"] = reports
        s["dr_sel_values"] = set()
        s["dr_page"] = 0
        labels = [BUTTONS[k] for k in reports]
        await cb.message.edit_text(
            "Данные: выберите 1+ кнопок отчётов для настройки полей.",
            reply_markup=_kb_paginated(labels, set(), 0, "dri", "drp", "dr_all", "dr_done", "dr_clear", done_text="Далее"),
        )
        await cb.answer()

    @dp.callback_query(F.data == "ctor_filters")
    async def constructor_filters_open(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        reports = filter_stage_reports_for_user(u)
        if not reports:
            await cb.answer("Нет отчётов с пошаговой фильтрацией среди ваших кнопок.", show_alert=True)
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        s["fq_reports"] = reports
        s["fq_sel_values"] = set()
        s["fq_page"] = 0
        labels = [BUTTONS[k] for k in reports]
        await cb.message.edit_text(
            "Фильтрация: выберите один или несколько пошаговых отчётов с одинаковой схемой дополнительных этапов.",
            reply_markup=_kb_paginated(labels, set(), 0, "fqi", "fqp", "fq_all", "fq_done", "fq_clear", done_text="Далее"),
        )
        await cb.answer()

    @dp.callback_query(F.data.startswith("ch"))
    async def cb_constructor(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        items = s.get("c_items", [])
        if not items:
            await cb.answer("Сессия устарела. Откройте Конструктор заново.", show_alert=True)
            return
        labels = [BUTTONS[k] for k in items]
        sel = set(s.get("c_sel", set()))
        d = cb.data
        if d.startswith("chp_"):
            p = int(d.split("_", 1)[1])
            s["ch_page"] = p
            await safe_edit_reply_markup(cb.message, _kb_paginated(labels, sel, p, "chi", "chp", "ch_all", "ch_done", "ch_clear"))
        elif d.startswith("chi_"):
            i = int(d.split("_", 1)[1])
            if i in sel:
                sel.remove(i)
            else:
                sel.add(i)
            s["c_sel"] = sel
            page = int(s.get("ch_page", i // PAGE_SIZE))
            await safe_edit_reply_markup(cb.message, _kb_paginated(labels, sel, page, "chi", "chp", "ch_all", "ch_done", "ch_clear"))
        elif d == "ch_all":
            s["c_sel"] = set(range(len(items)))
            page = int(s.get("ch_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(labels, s["c_sel"], page, "chi", "chp", "ch_all", "ch_done", "ch_clear"))
        elif d == "ch_clear":
            s["c_sel"] = set()
            page = int(s.get("ch_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(labels, set(), page, "chi", "chp", "ch_all", "ch_done", "ch_clear"))
        elif d == "ch_done":
            hidden = {items[i] for i in s.get("c_sel", set()) if 0 <= i < len(items)}
            access_repo.set_hidden_buttons(u.telegram_id, hidden)
            await cb.message.answer("Настройки меню сохранены.", reply_markup=_menu(labels_for(access_repo.get_user_access(u.telegram_id))))
        await cb.answer()

    @dp.callback_query(F.data.startswith("dr"))
    async def config_data_reports_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        reports = s.get("dr_reports", [])
        if not reports:
            await cb.answer("Сессия устарела. Откройте Конструктор заново.", show_alert=True)
            return
        sel_vals = set(s.get("dr_sel_values", set()))
        labels = [BUTTONS[k] for k in reports]
        sel = _indices_for_selected(reports, sel_vals)
        d = cb.data
        if d.startswith("drp_"):
            p = int(d.split("_", 1)[1])
            s["dr_page"] = p
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(labels, sel, p, "dri", "drp", "dr_all", "dr_done", "dr_clear", done_text="Далее")
            )
        elif d.startswith("dri_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(reports):
                v = reports[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            s["dr_sel_values"] = sel_vals
            page = int(s.get("dr_page", i // PAGE_SIZE))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(labels, _indices_for_selected(reports, sel_vals), page, "dri", "drp", "dr_all", "dr_done", "dr_clear", done_text="Далее")
            )
        elif d == "dr_all":
            sel_vals.update(reports)
            s["dr_sel_values"] = sel_vals
            page = int(s.get("dr_page", 0))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(labels, _indices_for_selected(reports, sel_vals), page, "dri", "drp", "dr_all", "dr_done", "dr_clear", done_text="Далее")
            )
        elif d == "dr_clear":
            s["dr_sel_values"] = set()
            page = int(s.get("dr_page", 0))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(labels, set(), page, "dri", "drp", "dr_all", "dr_done", "dr_clear", done_text="Далее")
            )
        elif d == "dr_done":
            if not sel_vals:
                await cb.answer("Выберите минимум одну кнопку отчёта.", show_alert=True)
                return
            fields = REPORT_FIELD_KEYS
            s["df_fields"] = fields
            s["df_report_keys"] = set(sel_vals)
            if len(sel_vals) == 1:
                one = next(iter(sel_vals))
                selected = access_repo.get_visible_fields_for_report(u.telegram_id, one)
                s["df_sel_keys"] = set(k for k in selected if k in REPORT_FIELD_LABEL) or set(fields)
            else:
                s["df_sel_keys"] = set(fields)
            s["df_page"] = 0
            field_labels = [REPORT_FIELD_LABEL[k] for k in fields]
            await cb.message.edit_text(
                "Выберите поля и нажмите «Применить».",
                reply_markup=_kb_paginated(
                    field_labels,
                    _indices_for_selected(fields, s["df_sel_keys"]),
                    0,
                    "dfi",
                    "dfp",
                    "df_all",
                    "df_apply",
                    "df_clear",
                    done_text="Применить",
                ),
            )
        await cb.answer()

    @dp.callback_query(F.data.startswith("df"))
    async def config_fields_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        fields = s.get("df_fields", [])
        report_keys = set(s.get("df_report_keys", set()))
        if not fields or not report_keys:
            await cb.answer("Сессия устарела. Откройте Конструктор заново.", show_alert=True)
            return
        labels = [REPORT_FIELD_LABEL[k] for k in fields]
        sel_keys = set(s.get("df_sel_keys", set()))
        sel = _indices_for_selected(fields, sel_keys)
        d = cb.data
        if d.startswith("dfp_"):
            p = int(d.split("_", 1)[1])
            s["df_page"] = p
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(labels, sel, p, "dfi", "dfp", "df_all", "df_apply", "df_clear", done_text="Применить")
            )
        elif d.startswith("dfi_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(fields):
                k = fields[i]
                if k in sel_keys:
                    sel_keys.remove(k)
                else:
                    sel_keys.add(k)
            s["df_sel_keys"] = sel_keys
            sel = _indices_for_selected(fields, sel_keys)
            page = int(s.get("df_page", i // PAGE_SIZE))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(labels, sel, page, "dfi", "dfp", "df_all", "df_apply", "df_clear", done_text="Применить")
            )
        elif d == "df_all":
            s["df_sel_keys"] = set(fields)
            page = int(s.get("df_page", 0))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(labels, set(range(len(fields))), page, "dfi", "dfp", "df_all", "df_apply", "df_clear", done_text="Применить")
            )
        elif d == "df_clear":
            s["df_sel_keys"] = set()
            page = int(s.get("df_page", 0))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(labels, set(), page, "dfi", "dfp", "df_all", "df_apply", "df_clear", done_text="Применить")
            )
        elif d == "df_apply":
            if not sel_keys:
                await cb.answer("Нужно оставить минимум одно поле.", show_alert=True)
                return
            for rk in report_keys:
                access_repo.set_visible_fields_for_report(u.telegram_id, rk, sel_keys)
            targets = ", ".join(BUTTONS.get(k, k) for k in sorted(report_keys))
            await cb.message.edit_text(f"Поля применены для: {targets}")
        await cb.answer()

    @dp.callback_query(F.data.startswith("fq"))
    async def ctor_filter_reports_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        reports = s.get("fq_reports", [])
        if not reports:
            await cb.answer("Сессия устарела. Откройте Конструктор заново.", show_alert=True)
            return
        sel_vals = set(s.get("fq_sel_values", set()))
        labels = [BUTTONS[k] for k in reports]
        sel = _indices_for_selected(reports, sel_vals)
        d = cb.data
        if d.startswith("fqp_"):
            p = int(d.split("_", 1)[1])
            s["fq_page"] = p
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(labels, sel, p, "fqi", "fqp", "fq_all", "fq_done", "fq_clear", done_text="Далее")
            )
        elif d.startswith("fqi_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(reports):
                v = reports[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            s["fq_sel_values"] = sel_vals
            page = int(s.get("fq_page", i // PAGE_SIZE))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(
                    labels, _indices_for_selected(reports, sel_vals), page, "fqi", "fqp", "fq_all", "fq_done", "fq_clear", done_text="Далее"
                )
            )
        elif d == "fq_all":
            sel_vals.update(reports)
            s["fq_sel_values"] = sel_vals
            page = int(s.get("fq_page", 0))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(
                    labels, _indices_for_selected(reports, sel_vals), page, "fqi", "fqp", "fq_all", "fq_done", "fq_clear", done_text="Далее"
                )
            )
        elif d == "fq_clear":
            s["fq_sel_values"] = set()
            page = int(s.get("fq_page", 0))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(labels, set(), page, "fqi", "fqp", "fq_all", "fq_done", "fq_clear", done_text="Далее")
            )
        elif d == "fq_done":
            if not sel_vals:
                await cb.answer("Выберите минимум один отчёт.", show_alert=True)
                return
            configs = {rk: tuple(access_repo.get_user_filter_pipeline(u.telegram_id, rk)) for rk in sel_vals}
            if len(set(configs.values())) > 1:
                await cb.answer(
                    "У выбранных отчётов не совпадает сохранённая схема этапов фильтрации. "
                    "Настройте их по отдельности или выровняйте схему.",
                    show_alert=True,
                )
                return
            first = next(iter(sel_vals))
            pipeline = list(configs[first])
            s["pf_apply_keys"] = set(sel_vals)
            s["pf_pipeline"] = pipeline
            s["pf_pick_idx"] = -1
            s.pop("fq_reports", None)
            s.pop("fq_sel_values", None)
            s.pop("fq_page", None)
            names = ", ".join(BUTTONS.get(k, k) for k in sorted(sel_vals))
            await cb.message.edit_text(
                f"Отчёты: {names}\n\nЭтапы фильтрации (можно включать/выключать и менять порядок):",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=f"{'✅' if k in pipeline else '❌'} {_stage_label(k)}",
                                callback_data=f"pf_t_{k}",
                            )
                        ]
                        for k in FILTER_PIPELINE_STAGE_KEYS
                    ]
                    + [
                        [InlineKeyboardButton(text="Порядок этапов", callback_data="pf_order")],
                        [InlineKeyboardButton(text="Применить", callback_data="pf_apply")],
                        [InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")],
                    ],
                ),
            )
        await cb.answer()

    @dp.callback_query(F.data.startswith("pf_"))
    async def ctor_pipeline_config(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        keys = set(s.get("pf_apply_keys") or set())
        pipeline = list(s.get("pf_pipeline") or [])
        if not keys:
            await cb.answer("Сессия устарела. Откройте Конструктор заново.", show_alert=True)
            return
        names = ", ".join(BUTTONS.get(k, k) for k in sorted(keys))
        d = cb.data

        def render_settings() -> InlineKeyboardMarkup:
            return InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"{'✅' if k in pipeline else '❌'} {_stage_label(k)}",
                            callback_data=f"pf_t_{k}",
                        )
                    ]
                    for k in FILTER_PIPELINE_STAGE_KEYS
                ]
                + [
                    [InlineKeyboardButton(text="Порядок этапов", callback_data="pf_order")],
                    [InlineKeyboardButton(text="Применить", callback_data="pf_apply")],
                    [InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")],
                ]
            )

        def render_order() -> InlineKeyboardMarkup:
            pick = int(s.get("pf_pick_idx", -1))
            rows = []
            for i, st in enumerate(pipeline):
                mark = "👉" if i == pick else "  "
                rows.append([InlineKeyboardButton(text=f"{mark} {i+1}. {_stage_label(st)}", callback_data=f"pf_pick_{i}")])
            controls = []
            if 0 <= pick < len(pipeline):
                controls = [
                    InlineKeyboardButton(text="⬆️", callback_data="pf_up"),
                    InlineKeyboardButton(text="⬇️", callback_data="pf_down"),
                ]
            if controls:
                rows.append(controls)
            rows.append([InlineKeyboardButton(text="Назад", callback_data="pf_settings")])
            rows.append([InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")])
            return InlineKeyboardMarkup(inline_keyboard=rows)

        if d.startswith("pf_t_"):
            k = d.split("_", 2)[2]
            if k not in set(FILTER_PIPELINE_STAGE_KEYS):
                await cb.answer("Неизвестный этап", show_alert=True)
                return
            if k in pipeline:
                pipeline = [x for x in pipeline if x != k]
            else:
                pipeline.append(k)
            s["pf_pipeline"] = pipeline
            await safe_edit_text(
                cb.message,
                f"Отчёты: {names}\n\nЭтапы фильтрации (можно включать/выключать и менять порядок):",
                render_settings(),
            )
            await cb.answer()
            return

        if d == "pf_order":
            s["pf_pick_idx"] = -1
            await safe_edit_text(cb.message, f"Отчёты: {names}\n\nПорядок этапов:", render_order())
            await cb.answer()
            return

        if d == "pf_settings":
            s["pf_pick_idx"] = -1
            await safe_edit_text(
                cb.message,
                f"Отчёты: {names}\n\nЭтапы фильтрации (можно включать/выключать и менять порядок):",
                render_settings(),
            )
            await cb.answer()
            return

        if d.startswith("pf_pick_"):
            i = int(d.split("_", 2)[2])
            s["pf_pick_idx"] = i
            await safe_edit_text(cb.message, f"Отчёты: {names}\n\nПорядок этапов:", render_order())
            await cb.answer()
            return

        if d in {"pf_up", "pf_down"}:
            pick = int(s.get("pf_pick_idx", -1))
            if not (0 <= pick < len(pipeline)):
                await cb.answer("Выберите этап", show_alert=True)
                return
            if d == "pf_up" and pick > 0:
                pipeline[pick - 1], pipeline[pick] = pipeline[pick], pipeline[pick - 1]
                s["pf_pick_idx"] = pick - 1
            if d == "pf_down" and pick < len(pipeline) - 1:
                pipeline[pick + 1], pipeline[pick] = pipeline[pick], pipeline[pick + 1]
                s["pf_pick_idx"] = pick + 1
            s["pf_pipeline"] = pipeline
            await safe_edit_text(cb.message, f"Отчёты: {names}\n\nПорядок этапов:", render_order())
            await cb.answer()
            return

        if d == "pf_apply":
            # Save pipeline (may be empty => opt-out)
            norm = [x for x in pipeline if x in set(FILTER_PIPELINE_STAGE_KEYS)]
            for rk in keys:
                access_repo.set_user_filter_pipeline(u.telegram_id, rk, norm)
            targets = ", ".join(BUTTONS.get(k, k) for k in sorted(keys))
            for k in ("pf_apply_keys", "pf_pipeline", "pf_pick_idx"):
                s.pop(k, None)
            await cb.message.edit_text(f"Фильтрация сохранена для: {targets}")
            await cb.answer()
            return

        await cb.answer()

    @dp.message(F.text == BUTTONS["refresh"])
    async def refresh(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        if not u.is_admin:
            await message.answer("Только админ может обновлять кэш.", reply_markup=_menu(labels_for(u)))
            return
        repo.get_records(force_refresh=True)
        await message.answer("Кэш обновлён.", reply_markup=_menu(labels_for(u)))

    @dp.message(F.text == BUTTONS["users"])
    async def users(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        if not u.is_admin:
            await message.answer("Раздел только для админа.", reply_markup=_menu(labels_for(u)))
            return
        rows = []
        for uu in access_repo.list_users():
            mark = "ADMIN" if uu.is_admin else uu.status
            rows.append([InlineKeyboardButton(text=f"{uu.full_name or uu.username or uu.telegram_id} ({mark})", callback_data=f"usr_{uu.telegram_id}")])
        rows.append([InlineKeyboardButton(text=BUTTONS["global_object_filter"], callback_data="gof_open")])
        rows.append([InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")])
        await message.answer("Пользователи:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @dp.message(F.text == BUTTONS["global_object_filter"])
    async def global_object_filter_entry(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        await close_previous_menu_if_active(message)
        if not u.is_admin:
            await message.answer("Раздел только для админа.", reply_markup=_menu(labels_for(u)))
            return
        all_objects = repo.all_object_code_groups_catalog()
        hidden = access_repo.list_global_hidden_object_codes()
        s = S(message.chat.id, message.from_user.id)
        s["gof_list"] = all_objects
        s["gof_sel_values"] = set(hidden)
        s["gof_page"] = 0
        selected = _indices_for_selected(all_objects, hidden)
        await message.answer(
            "Глобальный фильтр: выберите буквенные шифры (ПБ, ПМ, КЛ...), которые нужно скрыть для всех.",
            reply_markup=_kb_paginated(all_objects, selected, 0, "gfi", "gfp", "gf_all", "gf_done", "gf_clear"),
        )

    @dp.callback_query(F.data == "gof_open")
    async def global_object_filter_open(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u or not u.is_admin:
            return
        all_objects = repo.all_object_code_groups_catalog()
        hidden = access_repo.list_global_hidden_object_codes()
        s = S(cb.message.chat.id, cb.from_user.id)
        s["gof_list"] = all_objects
        s["gof_sel_values"] = set(hidden)
        s["gof_page"] = 0
        selected = _indices_for_selected(all_objects, hidden)
        await cb.message.edit_text(
            "Глобальный фильтр: выберите буквенные шифры (ПБ, ПМ, КЛ...), которые нужно скрыть для всех.",
            reply_markup=_kb_paginated(all_objects, selected, 0, "gfi", "gfp", "gf_all", "gf_done", "gf_clear"),
        )
        await cb.answer()

    @dp.callback_query(F.data.startswith("gf"))
    async def global_object_filter_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u or not u.is_admin:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        arr = s.get("gof_list", [])
        if not arr:
            await cb.answer("Сессия устарела. Откройте раздел заново.", show_alert=True)
            return
        sel_vals = set(s.get("gof_sel_values", set()))
        sel = _indices_for_selected(arr, sel_vals)
        d = cb.data
        if d.startswith("gfp_"):
            p = int(d.split("_", 1)[1])
            s["gof_page"] = p
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(arr, sel, p, "gfi", "gfp", "gf_all", "gf_done", "gf_clear"))
        elif d.startswith("gfi_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(arr):
                v = arr[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            s["gof_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            page = int(s.get("gof_page", i // PAGE_SIZE))
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(arr, sel, page, "gfi", "gfp", "gf_all", "gf_done", "gf_clear"))
        elif d == "gf_all":
            s["gof_sel_values"] = set(arr)
            page = int(s.get("gof_page", 0))
            sel = _indices_for_selected(arr, s["gof_sel_values"])
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(arr, sel, page, "gfi", "gfp", "gf_all", "gf_done", "gf_clear"))
        elif d == "gf_clear":
            s["gof_sel_values"] = set()
            page = int(s.get("gof_page", 0))
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(arr, set(), page, "gfi", "gfp", "gf_all", "gf_done", "gf_clear"))
        elif d == "gf_done":
            access_repo.set_global_hidden_object_codes(set(sel_vals))
            await cb.message.edit_text("Глобальный фильтр шифров объектов обновлён.")
        await cb.answer()

    @dp.callback_query(F.data.regexp(r"^usr_\d+$"))
    async def user_card(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u or not u.is_admin:
            return
        uid = int(cb.data.split("_", 1)[1])
        target = access_repo.get_user_access(uid)
        if target is None:
            await cb.answer("Не найден", show_alert=True)
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Разрешить", callback_data=f"uok_{uid}"), InlineKeyboardButton(text="Заблокировать", callback_data=f"ublk_{uid}")],
            [InlineKeyboardButton(text="Кнопки пользователя", callback_data=f"ubtn_{uid}")],
            [InlineKeyboardButton(text="← К списку", callback_data="usr_back")],
        ])
        await cb.message.edit_text(
            f"ID: {uid}\nИмя: {target.full_name}\nСтатус: {'admin' if target.is_admin else target.status}",
            reply_markup=kb,
        )
        await cb.answer()

    @dp.callback_query(F.data == "usr_back")
    async def users_back(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u or not u.is_admin:
            return
        rows = []
        for uu in access_repo.list_users():
            mark = "ADMIN" if uu.is_admin else uu.status
            rows.append([InlineKeyboardButton(text=f"{uu.full_name or uu.username or uu.telegram_id} ({mark})", callback_data=f"usr_{uu.telegram_id}")])
        rows.append([InlineKeyboardButton(text=BUTTONS["global_object_filter"], callback_data="gof_open")])
        rows.append([InlineKeyboardButton(text="Закрыть", callback_data="menu_cancel")])
        await cb.message.edit_text("Пользователи:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await cb.answer()

    @dp.callback_query(F.data.startswith("ubtn_"))
    async def user_buttons(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u or not u.is_admin:
            return
        uid = int(cb.data.split("_", 1)[1])
        target = access_repo.get_user_access(uid)
        keys = [k for k in BUTTONS.keys() if k not in {"users", "refresh", "global_object_filter"}]
        selected = {i for i, k in enumerate(keys) if (not target.allowed_buttons) or (k in target.allowed_buttons)}
        s = S(cb.message.chat.id, cb.from_user.id)
        s["ab_uid"] = uid
        s["ab_keys"] = keys
        s["ab_sel"] = selected
        labels = [BUTTONS[k] for k in keys]
        await cb.message.edit_text(
            f"Пользователь {uid}: выберите разрешённые кнопки",
            reply_markup=_kb_paginated(labels, selected, 0, "abi", "abp", "ab_all", "ab_done", "ab_clear"),
        )
        await cb.answer()

    @dp.callback_query(F.data.startswith("ab"))
    async def admin_buttons_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u or not u.is_admin:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        keys = s.get("ab_keys", [])
        if not keys:
            await cb.answer("Сессия устарела. Откройте раздел заново.", show_alert=True)
            return
        labels = [BUTTONS[k] for k in keys]
        sel = set(s.get("ab_sel", set()))
        d = cb.data
        if d.startswith("abp_"):
            p = int(d.split("_", 1)[1])
            s["ab_page"] = p
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(labels, sel, p, "abi", "abp", "ab_all", "ab_done", "ab_clear"))
        elif d.startswith("abi_"):
            i = int(d.split("_", 1)[1])
            if i in sel:
                sel.remove(i)
            else:
                sel.add(i)
            s["ab_sel"] = sel
            page = int(s.get("ab_page", i // PAGE_SIZE))
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(labels, sel, page, "abi", "abp", "ab_all", "ab_done", "ab_clear"))
        elif d == "ab_all":
            s["ab_sel"] = set(range(len(keys)))
            page = int(s.get("ab_page", 0))
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(labels, s["ab_sel"], page, "abi", "abp", "ab_all", "ab_done", "ab_clear"))
        elif d == "ab_clear":
            s["ab_sel"] = set()
            page = int(s.get("ab_page", 0))
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(labels, set(), page, "abi", "abp", "ab_all", "ab_done", "ab_clear"))
        elif d == "ab_done":
            uid = int(s.get("ab_uid", 0))
            allowed = {keys[i] for i in s.get("ab_sel", set()) if 0 <= i < len(keys)}
            allowed.add(CONSTRUCTOR_KEY)
            access_repo.set_allowed_buttons(uid, allowed)
            await cb.message.edit_text("Список доступных кнопок сохранён.")
        await cb.answer()

    @dp.callback_query(F.data.startswith("uok_"))
    async def user_ok(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u or not u.is_admin:
            return
        uid = int(cb.data.split("_", 1)[1])
        access_repo.set_user_status(uid, STATUS_APPROVED)
        target = access_repo.get_user_access(uid)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Разрешить", callback_data=f"uok_{uid}"), InlineKeyboardButton(text="Заблокировать", callback_data=f"ublk_{uid}")],
            [InlineKeyboardButton(text="Кнопки пользователя", callback_data=f"ubtn_{uid}")],
            [InlineKeyboardButton(text="← К списку", callback_data="usr_back")],
        ])
        await cb.message.edit_text(
            f"ID: {uid}\nИмя: {target.full_name if target else '-'}\nСтатус: {'admin' if (target and target.is_admin) else (target.status if target else STATUS_APPROVED)}",
            reply_markup=kb,
        )
        await notify_access_decision(
            cb.bot,
            uid,
            "Ваш доступ к боту подтверждён администратором. Нажмите /start для начала работы.",
        )
        await cb.answer("Разрешен")

    @dp.callback_query(F.data.startswith("ublk_"))
    async def user_blk(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u or not u.is_admin:
            return
        uid = int(cb.data.split("_", 1)[1])
        access_repo.block_user(uid)
        target = access_repo.get_user_access(uid)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Разрешить", callback_data=f"uok_{uid}"), InlineKeyboardButton(text="Заблокировать", callback_data=f"ublk_{uid}")],
            [InlineKeyboardButton(text="Кнопки пользователя", callback_data=f"ubtn_{uid}")],
            [InlineKeyboardButton(text="← К списку", callback_data="usr_back")],
        ])
        await cb.message.edit_text(
            f"ID: {uid}\nИмя: {target.full_name if target else '-'}\nСтатус: {'admin' if (target and target.is_admin) else (target.status if target else STATUS_BLOCKED)}",
            reply_markup=kb,
        )
        await notify_access_decision(
            cb.bot,
            uid,
            "Ваш доступ к боту отклонён администратором.",
        )
        await cb.answer("Заблокирован")

    @dp.callback_query(F.data.startswith("aprv_"))
    async def approve(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u or not u.is_admin:
            return
        uid = int(cb.data.split("_", 1)[1])
        codes = repo.unique_object_codes()
        s = S(cb.message.chat.id, cb.from_user.id)
        s["oc_uid"] = uid
        s["oc_codes"] = codes
        s["oc_sel"] = set()
        await cb.message.answer("Выберите шифры объектов:", reply_markup=_kb_paginated(codes, set(), 0, "oci", "ocp", "oc_all", "oc_done", "oc_clear"))
        await cb.answer()

    @dp.callback_query(F.data.startswith("rej_"))
    async def reject(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u or not u.is_admin:
            return
        uid = int(cb.data.split("_", 1)[1])
        access_repo.block_user(uid)
        await notify_access_decision(
            cb.bot,
            uid,
            "Ваш запрос на доступ к боту отклонён администратором.",
        )
        await cb.answer("Отклонён")

    @dp.callback_query(F.data.startswith("oc"))
    async def object_codes_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u or not u.is_admin:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        codes = s.get("oc_codes", [])
        if not codes:
            await cb.answer("Сессия устарела. Откройте раздел заново.", show_alert=True)
            return
        sel = set(s.get("oc_sel", set()))
        d = cb.data
        if d.startswith("ocp_"):
            p = int(d.split("_", 1)[1])
            s["oc_page"] = p
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(codes, sel, p, "oci", "ocp", "oc_all", "oc_done", "oc_clear"))
        elif d.startswith("oci_"):
            i = int(d.split("_", 1)[1])
            if i in sel:
                sel.remove(i)
            else:
                sel.add(i)
            s["oc_sel"] = sel
            page = int(s.get("oc_page", i // PAGE_SIZE))
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(codes, sel, page, "oci", "ocp", "oc_all", "oc_done", "oc_clear"))
        elif d == "oc_all":
            s["oc_sel"] = set(range(len(codes)))
            page = int(s.get("oc_page", 0))
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(codes, s["oc_sel"], page, "oci", "ocp", "oc_all", "oc_done", "oc_clear"))
        elif d == "oc_clear":
            s["oc_sel"] = set()
            page = int(s.get("oc_page", 0))
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(codes, set(), page, "oci", "ocp", "oc_all", "oc_done", "oc_clear"))
        elif d == "oc_done":
            uid = int(s.get("oc_uid", 0))
            selected = {codes[i] for i in s.get("oc_sel", set()) if 0 <= i < len(codes)}
            if not selected:
                await cb.answer("Нужно выбрать хотя бы один", show_alert=True)
                return
            access_repo.approve_user(uid, selected)
            await cb.message.answer(f"Пользователь {uid} одобрен.")
            await notify_access_decision(
                cb.bot,
                uid,
                "Ваш доступ к боту подтверждён администратором. Вам назначены объекты для просмотра. Нажмите /start.",
            )
        await cb.answer()

    @dp.callback_query(F.data.startswith("rsc_"))
    async def rsc_status_source_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        if s.get("flow_stage") != "status_wait_source":
            await cb.answer("Сессия устарела. Начните отчёт заново.", show_alert=True)
            return
        rk = (s.get("rsc_report_key") or "").strip()
        exp_tag = RSC_KEY_TO_TAG.get(rk)
        parts = cb.data.split("_")
        if len(parts) != 3 or parts[0] != "rsc" or parts[1] != exp_tag or parts[2] not in ("td", "yd"):
            await cb.answer("Неверный выбор", show_alert=True)
            return
        src = "today" if parts[2] == "td" else "yesterday"
        tuples = s.get("rsc_pending_tuples")
        recs = list(s.get("rsc_pending_rows") or [])
        if tuples:
            base_recs = [r for _, r in tuples]
        else:
            base_recs = recs
        if not base_recs:
            await cb.answer("Нет данных для фильтрации.", show_alert=True)
            return
        base_list = repo.unique_status_display_values(base_recs, src)
        s["rsc_status_base"] = base_list
        s["rsc_status_list"] = base_list
        s["rsc_status_source"] = src
        s["rsc_sf_sel_values"] = set()
        s["rsc_status_page"] = 0
        s["flow_stage"] = "status_pick"
        src_label = "вчера" if src == "yesterday" else "сегодня"
        await safe_edit_text(
            cb.message,
            f"Шаг 3/3: отметьте статусы ({src_label}). Можно не выбирать = все.\n\nМожно написать текст для поиска по списку.",
            _kb_paginated(base_list, set(), 0, "sfi", "sfp", "sf_all", "sf_done", "sf_clear"),
        )
        await cb.answer()

    @dp.callback_query(F.data.startswith("sf"))
    async def cb_status_filter_pick(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        if s.get("flow_stage") != "status_pick":
            await cb.answer()
            return
        arr = s.get("rsc_status_list", [])
        if not arr:
            await cb.answer("Сессия устарела. Начните отчёт заново.", show_alert=True)
            return
        s["menu_chat_id"] = cb.message.chat.id
        s["menu_message_id"] = cb.message.message_id
        sel_vals = set(s.get("rsc_sf_sel_values", set()))
        sel = _indices_for_selected(arr, sel_vals)
        d = cb.data
        if d.startswith("sfp_"):
            p = int(d.split("_", 1)[1])
            s["rsc_status_page"] = p
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, p, "sfi", "sfp", "sf_all", "sf_done", "sf_clear"))
        elif d.startswith("sfi_"):
            i = int(d.split("_", 1)[1])
            if 0 <= i < len(arr):
                v = arr[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            s["rsc_sf_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            page = int(s.get("rsc_status_page", i // PAGE_SIZE))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "sfi", "sfp", "sf_all", "sf_done", "sf_clear"))
        elif d == "sf_all":
            sel_vals.update(arr)
            s["rsc_sf_sel_values"] = sel_vals
            sel = _indices_for_selected(arr, sel_vals)
            page = int(s.get("rsc_status_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, sel, page, "sfi", "sfp", "sf_all", "sf_done", "sf_clear"))
        elif d == "sf_clear":
            s["rsc_sf_sel_values"] = set()
            page = int(s.get("rsc_status_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_paginated(arr, set(), page, "sfi", "sfp", "sf_all", "sf_done", "sf_clear"))
        elif d == "sf_done":
            await finalize_status_filtered_report(cb, u, s)
        await cb.answer()

    @dp.callback_query(F.data.startswith("pl_"))
    async def cb_pipeline(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        if s.get("flow_stage") != "pipeline":
            await cb.answer()
            return
        s["menu_chat_id"] = cb.message.chat.id
        s["menu_message_id"] = cb.message.message_id

        d = cb.data
        if d == "pl_back":
            cur = int(s.get("pl_cursor", 0))
            if cur > 0:
                s["pl_cursor"] = cur - 1
            await _pipeline_render_stage(cb.message, u, s)
            await cb.answer()
            return

        if d in {"pl_src_today", "pl_src_yesterday"}:
            s["pl_status_source"] = "today" if d == "pl_src_today" else "yesterday"
            await _pipeline_render_stage(cb.message, u, s)
            await cb.answer()
            return

        arr = s.get("pl_list", [])
        if not arr:
            await cb.answer("Сессия устарела. Откройте отчёт заново.", show_alert=True)
            return
        stage_key = (s.get("pl_stage_key") or "").strip()
        if not stage_key:
            await cb.answer("Сессия устарела. Откройте отчёт заново.", show_alert=True)
            return
        sel_map: dict[str, set[str]] = s.get("pl_selected", {}) or {}
        sel_vals = set(sel_map.get(stage_key, set()))

        if d.startswith("pl_p_"):
            p = int(d.split("_", 2)[2])
            s["pl_page"] = p
            await safe_edit_reply_markup(cb.message, _kb_pipeline_list(arr, sel_vals, p))
            await cb.answer()
            return

        if d.startswith("pl_i_"):
            i = int(d.split("_", 2)[2])
            if 0 <= i < len(arr):
                v = arr[i]
                if v in sel_vals:
                    sel_vals.remove(v)
                else:
                    sel_vals.add(v)
            sel_map[stage_key] = sel_vals
            s["pl_selected"] = sel_map
            page = int(s.get("pl_page", i // PAGE_SIZE))
            await safe_edit_reply_markup(cb.message, _kb_pipeline_list(arr, sel_vals, page))
            await cb.answer()
            return

        if d == "pl_all":
            sel_vals.update(arr)
            sel_map[stage_key] = sel_vals
            s["pl_selected"] = sel_map
            page = int(s.get("pl_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_pipeline_list(arr, sel_vals, page))
            await cb.answer()
            return

        if d == "pl_clear":
            sel_map[stage_key] = set()
            s["pl_selected"] = sel_map
            page = int(s.get("pl_page", 0))
            await safe_edit_reply_markup(cb.message, _kb_pipeline_list(arr, set(), page))
            await cb.answer()
            return

        if d == "pl_done":
            # Proceed to next stage; empty selection = no filter.
            sel_map[stage_key] = sel_vals
            s["pl_selected"] = sel_map
            s["pl_cursor"] = int(s.get("pl_cursor", 0)) + 1
            # If next stage is status, force re-pick source each time unless already set.
            await _pipeline_render_stage(cb.message, u, s)
            await cb.answer()
            return

        await cb.answer()

    @dp.callback_query(F.data == "noop")
    async def noop(cb: CallbackQuery):
        await cb.answer()

    @dp.callback_query(F.data == "menu_cancel")
    async def menu_cancel(cb: CallbackQuery):
        clear_session(cb.message.chat.id, cb.from_user.id)
        try:
            await cb.message.edit_text("Закрыто.")
        except Exception:
            await cb.message.answer("Закрыто.")
        await cb.answer("Отменено")

    @dp.callback_query(F.data.startswith("bn"))
    async def bind_note_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        arr = s.get("bind_objects", [])
        if not arr:
            await cb.answer("Сессия устарела. Откройте привязку заново.", show_alert=True)
            return
        sel = set(s.get("bind_sel", set()))
        d = cb.data
        if d.startswith("bnp_"):
            p = int(d.split("_", 1)[1])
            s["bind_page"] = p
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(arr, sel, p, "bni", "bnp", "bn_all", "bn_done", "bn_clear")
            )
        elif d.startswith("bni_"):
            i = int(d.split("_", 1)[1])
            if i in sel:
                sel.remove(i)
            else:
                sel = {i}  # one object binding
            s["bind_sel"] = sel
            page = int(s.get("bind_page", i // PAGE_SIZE))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(arr, sel, page, "bni", "bnp", "bn_all", "bn_done", "bn_clear")
            )
        elif d == "bn_all":
            s["bind_sel"] = set(range(len(arr)))
            page = int(s.get("bind_page", 0))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(arr, s["bind_sel"], page, "bni", "bnp", "bn_all", "bn_done", "bn_clear")
            )
        elif d == "bn_clear":
            s["bind_sel"] = set()
            page = int(s.get("bind_page", 0))
            await cb.message.edit_reply_markup(
                reply_markup=_kb_paginated(arr, set(), page, "bni", "bnp", "bn_all", "bn_done", "bn_clear")
            )
        elif d == "bn_done":
            note_id = int(s.get("bind_note_id", 0))
            if not sel:
                await cb.answer("Выберите объект", show_alert=True)
                return
            obj = arr[min(sel)]
            ok = access_repo.bind_note(note_id, object_name=obj)
            await cb.message.answer("Комментарий привязан." if ok else "Не удалось привязать.")
        await cb.answer()

    @dp.callback_query(F.data.startswith("bm"))
    async def bind_note_mk_cb(cb: CallbackQuery):
        u = await guard_cb(cb)
        if not u:
            return
        s = S(cb.message.chat.id, cb.from_user.id)
        arr = s.get("bind_mks", [])
        if not arr:
            await cb.answer("Сессия устарела. Откройте привязку заново.", show_alert=True)
            return
        sel = set(s.get("bind_mk_sel", set()))
        d = cb.data
        if d.startswith("bmp_"):
            p = int(d.split("_", 1)[1])
            s["bind_mk_page"] = p
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(arr, sel, p, "bmi", "bmp", "bm_all", "bm_done", "bm_clear"))
        elif d.startswith("bmi_"):
            i = int(d.split("_", 1)[1])
            if i in sel:
                sel.remove(i)
            else:
                sel = {i}
            s["bind_mk_sel"] = sel
            page = int(s.get("bind_mk_page", i // PAGE_SIZE))
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(arr, sel, page, "bmi", "bmp", "bm_all", "bm_done", "bm_clear"))
        elif d == "bm_all":
            s["bind_mk_sel"] = set(range(len(arr)))
            page = int(s.get("bind_mk_page", 0))
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(arr, s["bind_mk_sel"], page, "bmi", "bmp", "bm_all", "bm_done", "bm_clear"))
        elif d == "bm_clear":
            s["bind_mk_sel"] = set()
            page = int(s.get("bind_mk_page", 0))
            await cb.message.edit_reply_markup(reply_markup=_kb_paginated(arr, set(), page, "bmi", "bmp", "bm_all", "bm_done", "bm_clear"))
        elif d == "bm_done":
            note_id = int(s.get("bind_note_id", 0))
            if not sel:
                await cb.answer("Выберите веху", show_alert=True)
                return
            mk = arr[min(sel)]
            ok = access_repo.bind_note(note_id, milestone_key=mk)
            await cb.message.answer("Комментарий привязан к вехе." if ok else "Не удалось привязать.")
        await cb.answer()

    @dp.message()
    async def fallback(message: Message):
        u = await guard_msg(message)
        if not u:
            return
        s = S(message.chat.id, message.from_user.id)
        txt = (message.text or "").strip()

        # 1) Фильтры в активных сценариях имеют приоритет над быстрыми комментариями.
        if s.get("flow_stage") == "obj_changes_stage2" and txt and s.get("changes_mks") is not None:
            base = s.get("changes_mks_base") or s.get("changes_mks") or []
            s["changes_mks_base"] = base
            sel_vals = set(s.get("ck_sel_values", set()))
            matches = fuzzy_values(base, txt, limit=80)
            ranked = _union_filtered(base, sel_vals, matches)
            s["changes_mks"] = ranked
            s["changes_sel"] = _indices_for_selected(ranked, sel_vals)
            s["changes_page"] = 0
            await upsert_filter_menu(
                message,
                s,
                f"Подобрал вех: {len(ranked)}. Выбрано всего: {len(sel_vals)}. Выберите:",
                _kb_paginated(ranked, s["changes_sel"], 0, "cki", "ckp", "ck_all", "ck_done", "ck_clear"),
            )
            return

        if s.get("flow_stage") == "obj_stage1" and txt and s.get("list"):
            base = s.get("base_list") or s.get("list") or []
            s["base_list"] = base
            sel_vals = set(s.get("ob_sel_values", set()))
            matches = fuzzy_values(base, txt, limit=80)
            ranked = _union_filtered(base, sel_vals, matches)
            s["list"] = ranked
            s["sel"] = _indices_for_selected(ranked, sel_vals)
            s["ob_page"] = 0
            await upsert_filter_menu(
                message,
                s,
                f"Подобрал объектов: {len(ranked)}. Выбрано всего: {len(sel_vals)}. Выберите:",
                _kb_paginated(ranked, s["sel"], 0, "obi", "obp", "ob_all", "ob_done", "ob_clear"),
            )
            return

        if s.get("flow_stage") == "obj_info_stage2" and txt and s.get("obj_info_mks") is not None:
            base = s.get("obj_info_mks_base") or s.get("obj_info_mks") or []
            s["obj_info_mks_base"] = base
            sel_vals = set(s.get("obj_info_mk_sel_values", set()))
            matches = fuzzy_values(base, txt, limit=80)
            ranked = _union_filtered(base, sel_vals, matches)
            s["obj_info_mks"] = ranked
            sel = _indices_for_selected(ranked, sel_vals)
            s["obj_info_mk_page"] = 0
            await upsert_filter_menu(
                message,
                s,
                f"Подобрал вех: {len(ranked)}. Выбрано всего: {len(sel_vals)}. Выберите:",
                _kb_paginated(ranked, sel, 0, "oki", "okp", "ok_all", "ok_done", "ok_clear"),
            )
            return

        if s.get("flow_stage") == "status_pick" and txt and s.get("rsc_status_list") is not None:
            base = s.get("rsc_status_base") or s.get("rsc_status_list") or []
            s["rsc_status_base"] = base
            sel_vals = set(s.get("rsc_sf_sel_values", set()))
            matches = fuzzy_values(base, txt, limit=80)
            ranked = _union_filtered(base, sel_vals, matches)
            s["rsc_status_list"] = ranked
            s["rsc_status_page"] = 0
            sel = _indices_for_selected(ranked, sel_vals)
            await upsert_filter_menu(
                message,
                s,
                f"Подобрал статусов: {len(ranked)}. Выбрано всего: {len(sel_vals)}. Выберите:",
                _kb_paginated(ranked, sel, 0, "sfi", "sfp", "sf_all", "sf_done", "sf_clear"),
            )
            return

        if s.get("flow_stage") == "pipeline" and txt and s.get("pl_list") is not None:
            base = s.get("pl_base_list") or s.get("pl_list") or []
            s["pl_base_list"] = base
            stage_key = (s.get("pl_stage_key") or "").strip()
            sel_map: dict[str, set[str]] = s.get("pl_selected", {}) or {}
            sel_vals = set(sel_map.get(stage_key, set()))
            matches = fuzzy_values(base, txt, limit=80)
            ranked = _union_filtered(base, sel_vals, matches)
            s["pl_list"] = ranked
            s["pl_page"] = 0
            await upsert_filter_menu(
                message,
                s,
                f"Подобрал значений: {len(ranked)}. Выбрано всего: {len(sel_vals)}. Выберите:",
                _kb_pipeline_list(ranked, sel_vals, 0),
            )
            return

        if s.get("await_edit_note"):
            note_id = int(s.get("await_edit_note", 0))
            s["await_edit_note"] = 0
            ok = access_repo.update_note_text(note_id, txt) if note_id > 0 else False
            await message.answer("Комментарий обновлён." if ok else "Не удалось обновить комментарий.", reply_markup=_menu(labels_for(u)))
            return
        if s.get("await_note"):
            s["await_note"] = False
            mk = ""
            obj = ""
            for token in txt.split():
                if token.startswith("#объект="):
                    obj = token.split("=", 1)[1]
                if token.startswith("#веха="):
                    mk = token.split("=", 1)[1]
            access_repo.add_note(u.telegram_id, txt, milestone_key=mk, object_name=obj)
            await message.answer("Комментарий сохранён.", reply_markup=_menu(labels_for(u)))
            return
        if s.get("flow_stage") == "mo_stage1" and txt:
            arr = s.get("all_milestones", []) or s.get("milestones", [])
            sel_vals = set(s.get("mk_sel_values", set()))
            matches = fuzzy_values(arr, txt, limit=50)
            ranked = _union_filtered(arr, sel_vals, matches)
            s["milestones"] = ranked
            s["sel"] = _indices_for_selected(ranked, sel_vals)
            s["mk_page"] = 0
            await upsert_filter_menu(
                message,
                s,
                f"Подобрал вех: {len(ranked)}. Выбрано всего: {len(sel_vals)}. Выберите:",
                _kb_paginated(ranked, s["sel"], 0, "mki", "mkp", "mk_all", "mk_done", "mk_clear"),
            )
            return
        if s.get("flow_stage") == "mo_stage2" and txt and s.get("list"):
            arr = s.get("base_mo_list") or s.get("list") or []
            s["base_mo_list"] = arr
            sel_vals = set(s.get("mo_sel_values", set()))
            matches = fuzzy_values(arr, txt, limit=80)
            ranked = _union_filtered(arr, sel_vals, matches)
            s["list"] = ranked
            s["sel_obj"] = _indices_for_selected(ranked, sel_vals)
            s["mo_page"] = 0
            await upsert_filter_menu(
                message,
                s,
                f"Подобрал объектов: {len(ranked)}. Выбрано всего: {len(sel_vals)}. Выберите:",
                _kb_paginated(ranked, s["sel_obj"], 0, "moi", "mop", "mo_all", "mo_done", "mo_clear"),
            )
            return
        if s.get("flow_stage") == "important_stage1" and txt and s.get("im_objects"):
            base = s.get("im_objects_base") or s.get("im_objects") or []
            s["im_objects_base"] = base
            sel_vals = set(s.get("im_obj_sel_values", set()))
            matches = fuzzy_values(base, txt, limit=80)
            ranked = _union_filtered(base, sel_vals, matches)
            s["im_objects"] = ranked
            s["im_obj_page"] = 0
            await upsert_filter_menu(
                message,
                s,
                f"Подобрал объектов: {len(ranked)}. Выбрано всего: {len(sel_vals)}. Выберите:",
                _kb_paginated(ranked, _indices_for_selected(ranked, sel_vals), 0, "poi", "pop", "po_all", "po_done", "po_clear"),
            )
            return
        if s.get("flow_stage") == "important_stage2" and txt and s.get("im_milestones"):
            base = s.get("im_milestones_base") or s.get("im_milestones") or []
            s["im_milestones_base"] = base
            sel_vals = set(s.get("im_mk_sel_values", set()))
            matches = fuzzy_values(base, txt, limit=80)
            # Keep list strictly inside 15 priority milestones, even during search.
            ranked = [v for v in base if v in set(matches) or v in sel_vals]
            s["im_milestones"] = ranked
            s["im_mk_page"] = 0
            await upsert_filter_menu(
                message,
                s,
                f"Подобрал вех: {len(ranked)}. Выбрано всего: {len(sel_vals)}. Выберите:",
                _kb_paginated(ranked, _indices_for_selected(ranked, sel_vals), 0, "pmi", "pmp", "pm_all", "pm_done", "pm_clear"),
            )
            return
        if txt.startswith("/delnote"):
            parts = txt.split()
            if len(parts) == 2 and parts[1].isdigit():
                ok = access_repo.delete_note(int(parts[1]), u.telegram_id, u.is_admin)
                await message.answer("Комментарий удалён." if ok else "Удаление недоступно.", reply_markup=_menu(labels_for(u)))
                return
        if txt.startswith("/mute ") and u.is_admin:
            parts = txt.split()
            if len(parts) >= 2 and parts[1].isdigit():
                target_id = int(parts[1])
                mins = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 2
                mins = max(1, min(mins, 120))
                muted_until[target_id] = datetime.utcnow() + timedelta(minutes=mins)
                await message.answer(f"Пользователь {target_id} в mute на {mins} мин.", reply_markup=_menu(labels_for(u)))
                return
        if txt.startswith("/unmute ") and u.is_admin:
            parts = txt.split()
            if len(parts) == 2 and parts[1].isdigit():
                target_id = int(parts[1])
                muted_until.pop(target_id, None)
                spam_events.pop(target_id, None)
                await message.answer(f"Пользователь {target_id} разmute.", reply_markup=_menu(labels_for(u)))
                return
        # 2) Быстрый комментарий только вне активных режимов выбора/фильтрации.
        has_active_filter_mode = any(
            [
                bool(s.get("flow_stage")),
                bool(s.get("list")),
                s.get("changes_mks") is not None,
                bool(s.get("chosen_mk")),
                bool(s.get("bind_objects")),
                bool(s.get("bind_mks")),
                bool(s.get("fq_reports")),
                bool(s.get("ff_apply_keys")),
            ]
        )
        if txt and txt not in KEY_BY_LABEL and not txt.startswith("/") and not has_active_filter_mode:
            access_repo.add_note(u.telegram_id, txt)
            await message.answer("Быстрый комментарий сохранён.", reply_markup=_menu(labels_for(u)))
            return
        await message.answer("Выберите действие кнопками меню или откройте «Инструкция пользования».", reply_markup=_menu(labels_for(u)))

    return dp


def build_bot(settings: Settings) -> Bot:
    return Bot(token=settings.bot_token)
