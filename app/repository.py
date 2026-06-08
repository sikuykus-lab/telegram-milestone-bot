from __future__ import annotations

from difflib import SequenceMatcher
from datetime import date, datetime
import re
from typing import Any

from .cache import ReadRateLimiter, TTLCache
from .models import MilestoneRecord
from .sheets_client import SheetsClient

MILESTONE_WEIGHTS: dict[str, float] = {
    "РНС": 10.0,
    "НПРАБ": 3.0,
    "ОРК": 3.0,
    "ОМР": 5.0,
    "ОКР": 3.0,
    "НВИС": 3.0,
    "ОВИС": 5.0,
    "НОТД МОП": 3.0,
    "ООТД МОП": 10.0,
    "ОМ ЛО": 7.0,
    "ПТ": 3.0,
    "ОФАС": 5.0,
    "РВ": 23.0,
    "АЧР": 7.0,
    "ОЗ ДДУ": 10.0,
}


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _score(query: str, value: str) -> float:
    q = _norm(query)
    v = _norm(value)
    if not q or not v:
        return 0.0
    if q in v:
        return 1.0 + (len(q) / max(len(v), 1))
    return SequenceMatcher(a=q, b=v).ratio()


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    formats = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y")
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _is_done(status: str) -> bool:
    s = _norm(status)
    return ("выполн" in s) or ("done" in s) or ("completed" in s)


def _is_yes(raw: str) -> bool:
    s = _norm(raw)
    return s in {"да", "yes", "true", "1"}


def _safe(row: list[str], idx: int) -> str:
    return row[idx].strip() if idx < len(row) and row[idx] else ""


def _parse_deviation(raw: str) -> int:
    txt = (raw or "").strip().replace(",", ".")
    if not txt:
        return 0
    num = []
    for ch in txt:
        if ch.isdigit() or ch in "-.":
            num.append(ch)
    try:
        return int(float("".join(num))) if num else 0
    except ValueError:
        return 0


def _norm_stage_key(stage: str) -> str:
    return " ".join((stage or "").strip().upper().split())


def _weight_label(weight: float) -> str:
    if weight >= 10:
        return "Очень важно"
    if weight >= 7:
        return "Важно"
    if weight >= 5:
        return "Средняя важность"
    return "Низкая важность"


EMPTY_STATUS_DISPLAY = "— (пусто)"


def _status_display_for_record(rec: MilestoneRecord, source: str) -> str:
    src = (source or "today").strip().lower()
    raw = (rec.status_yesterday if src == "yesterday" else rec.status_today) or ""
    raw = raw.strip()
    return EMPTY_STATUS_DISPLAY if not raw else raw


class MilestonesRepository:
    def __init__(
        self,
        client: SheetsClient,
        spreadsheet_id: str,
        sheet_name: str,
        ttl_seconds: int,
        min_read_interval_seconds: int,
    ) -> None:
        self._client = client
        self._spreadsheet_id = spreadsheet_id
        self._sheet_name = sheet_name
        self._cache: TTLCache[list[MilestoneRecord]] = TTLCache(ttl_seconds)
        self._limiter = ReadRateLimiter(min_read_interval_seconds)

    @staticmethod
    def _add_months(d: date, months: int) -> date:
        total = (d.year * 12 + (d.month - 1)) + months
        year = total // 12
        month = (total % 12) + 1
        max_day = [
            31,
            29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][month - 1]
        return date(year, month, min(d.day, max_day))

    def _is_in_window(self, dt: date | None) -> bool:
        if dt is None:
            return False
        today = date.today()
        left = self._add_months(today, -3)
        right = self._add_months(today, 18)
        return left <= dt <= right

    def _record_in_window(self, rec: MilestoneRecord) -> bool:
        return (
            self._is_in_window(rec.due_date)
            or self._is_in_window(rec.forecast_fact_date)
            or self._is_in_window(rec.target_date)
            or self._is_in_window(rec.due_date_yesterday)
        )

    def _build_records(self, rows: list[list[str]]) -> list[MilestoneRecord]:
        if len(rows) < 2:
            return []

        out: list[MilestoneRecord] = []
        for i, row in enumerate(rows[1:], start=2):
            due = _parse_date(_safe(row, 4))
            forecast_fact = _parse_date(_safe(row, 5))
            target = _parse_date(_safe(row, 6))
            due_y = _parse_date(_safe(row, 16))
            status_today = _safe(row, 7)
            if _is_done(status_today):
                continue
            rec = MilestoneRecord(
                row_index=i,
                milestone_key=_safe(row, 1),
                milestone_name=_safe(row, 2),
                object_name=_safe(row, 22),
                object_code=_safe(row, 21),
                due_date=due,
                forecast_fact_date=forecast_fact,
                target_date=target,
                due_date_yesterday=due_y,
                status_today=status_today,
                status_yesterday=_safe(row, 19),
                responsible=_safe(row, 8),
                responsible_email=_safe(row, 9),
                role=_safe(row, 10),
                deviation=_safe(row, 13),
                comment=_safe(row, 12),
                change_flag=_safe(row, 15),
            )
            if self._record_in_window(rec):
                out.append(rec)
        return out

    def get_records(self, force_refresh: bool = False) -> list[MilestoneRecord]:
        if not force_refresh:
            cached = self._cache.get()
            if cached is not None:
                return cached
        self._limiter.wait_turn()
        data = self._client.read_ranges(self._spreadsheet_id, self._sheet_name, ["A1:W20000"])
        rows = data.get("A1:W20000", [])
        records = self._build_records(rows)
        self._cache.set(records)
        return records

    def _filter_by_codes(
        self, records: list[MilestoneRecord], allowed_object_codes: set[str] | None
    ) -> list[MilestoneRecord]:
        if allowed_object_codes is None:
            return records
        codes = {c.strip().lower() for c in allowed_object_codes if c.strip()}
        if not codes:
            return []
        out: list[MilestoneRecord] = []
        for r in records:
            rc = (r.object_code or "").strip().lower()
            if not rc:
                continue
            if any(rc == c or rc.startswith(c) for c in codes):
                out.append(r)
        return out

    def _deviation_weight(self, rec: MilestoneRecord) -> int:
        raw = (rec.deviation or "").strip()
        # Without "-" means delay, with "-" means ahead of schedule.
        sign = -1 if raw.startswith("-") else 1
        return sign * abs(_parse_deviation(raw))

    def overview(self, allowed_object_codes: set[str] | None = None) -> dict[str, Any]:
        records = self._filter_by_codes(self.get_records(), allowed_object_codes)
        today = date.today()
        due_7, overdue = 0, 0
        by_resp: dict[str, int] = {}
        for rec in records:
            if rec.due_date is None:
                continue
            days = (rec.due_date - today).days
            by_resp[rec.responsible] = by_resp.get(rec.responsible, 0) + 1
            if days < 0:
                overdue += 1
            elif days <= 7:
                due_7 += 1
        return {
            "total_active": len(records),
            "overdue": overdue,
            "due_7": due_7,
            "responsibles": sorted(by_resp.items(), key=lambda x: x[1], reverse=True),
        }

    def responsibles_with_objects(
        self, allowed_object_codes: set[str] | None = None
    ) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str], dict[str, set[str]]] = {}
        for rec in self._filter_by_codes(self.get_records(), allowed_object_codes):
            key = (rec.responsible, rec.responsible_email)
            by_role = groups.setdefault(key, {})
            role_bucket = by_role.setdefault(rec.role or "Без роли", set())
            role_bucket.add(rec.object_name)
        out = []
        for (resp, email), by_role in sorted(groups.items(), key=lambda x: x[0][0].lower()):
            role_rows = []
            for role, objects in sorted(by_role.items(), key=lambda x: x[0].lower()):
                role_rows.append({"role": role, "objects": sorted(o for o in objects if o)})
            out.append({"responsible": resp, "email": email, "roles": role_rows})
        return out

    def records_for_responsible(
        self, responsible_query: str, allowed_object_codes: set[str] | None = None
    ) -> list[MilestoneRecord]:
        q = _norm(responsible_query)
        return [
            r
            for r in self._filter_by_codes(self.get_records(), allowed_object_codes)
            if q in _norm(r.responsible)
        ]

    def records_for_responsible_index(
        self, index: int, allowed_object_codes: set[str] | None = None
    ) -> list[MilestoneRecord]:
        entries = self.responsibles_with_objects(allowed_object_codes)
        if index < 0 or index >= len(entries):
            return []
        e = entries[index]
        resp, email = e["responsible"], e["email"]
        return [
            r
            for r in self._filter_by_codes(self.get_records(), allowed_object_codes)
            if r.responsible == resp and r.responsible_email == email
        ]

    def unique_object_names(self, allowed_object_codes: set[str] | None = None) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for r in self._filter_by_codes(self.get_records(), allowed_object_codes):
            name = (r.object_name or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
        return sorted(out, key=lambda x: x.lower())

    def unique_object_codes(self) -> list[str]:
        seen: dict[str, str] = {}
        for r in self.get_records():
            code = (r.object_code or "").strip()
            if not code:
                continue
            seen.setdefault(code.lower(), code)
        return sorted(seen.values(), key=lambda x: x.lower())

    def all_object_codes_catalog(self) -> list[str]:
        self._limiter.wait_turn()
        data = self._client.read_ranges(self._spreadsheet_id, self._sheet_name, ["A1:W20000"])
        rows = data.get("A1:W20000", [])
        seen: dict[str, str] = {}
        for row in rows[1:]:
            # Code source: column V (already normalized to pure code values).
            code = _safe(row, 21)
            code = (code or "").strip()
            if not code:
                continue
            seen.setdefault(code.lower(), code)
        return sorted(seen.values(), key=lambda x: x.lower())

    def all_object_code_groups_catalog(self) -> list[str]:
        seen: dict[str, str] = {}
        for code in self.all_object_codes_catalog():
            m = re.match(r"^([A-ZА-ЯЁ]+)", (code or "").strip().upper())
            if not m:
                continue
            grp = m.group(1).strip()
            if not grp:
                continue
            seen.setdefault(grp.lower(), grp)
        return sorted(seen.values(), key=lambda x: x.lower())

    def unique_milestones(self, allowed_object_codes: set[str] | None = None) -> list[str]:
        seen: dict[str, str] = {}
        for r in self._filter_by_codes(self.get_records(), allowed_object_codes):
            name = (r.milestone_key or "").strip()
            if not name:
                continue
            seen.setdefault(name.lower(), name)
        return sorted(seen.values(), key=lambda x: x.lower())

    def fuzzy_filter_values(self, values: list[str], query: str, limit: int = 40) -> list[str]:
        q = _norm(query)
        if not q:
            return values[:limit]
        ranked = sorted(values, key=lambda x: _score(q, x), reverse=True)
        filtered = [x for x in ranked if _score(q, x) >= 0.35]
        if not filtered:
            filtered = ranked
        return filtered[:limit]

    def objects_for_milestone(
        self, milestone_key: str, allowed_object_codes: set[str] | None = None
    ) -> list[str]:
        needle = _norm(milestone_key)
        seen: dict[str, str] = {}
        for r in self._filter_by_codes(self.get_records(), allowed_object_codes):
            if _norm(r.milestone_key) != needle:
                continue
            name = (r.object_name or "").strip()
            if not name:
                continue
            seen.setdefault(name.lower(), name)
        return sorted(seen.values(), key=lambda x: x.lower())

    def objects_with_changes_today(
        self, allowed_object_codes: set[str] | None = None
    ) -> list[str]:
        by_key: dict[str, str] = {}
        for r in self.today_changes(allowed_object_codes):
            name = (r.object_name or "").strip()
            if not name:
                continue
            by_key.setdefault(name.lower(), name)
        return sorted(by_key.values(), key=lambda x: x.lower())

    def milestones_with_changes_for_objects(
        self,
        object_names: set[str],
        allowed_object_codes: set[str] | None = None,
    ) -> list[str]:
        obj_keys = {_norm(x) for x in object_names if _norm(x)}
        seen: dict[str, str] = {}
        for r in self.today_changes(allowed_object_codes):
            if obj_keys and _norm(r.object_name) not in obj_keys:
                continue
            mk = (r.milestone_key or "").strip()
            if not mk:
                continue
            seen.setdefault(mk.lower(), mk)
        return sorted(seen.values(), key=lambda x: x.lower())

    def changed_by_objects_then_milestones(
        self,
        object_names: set[str],
        milestone_keys: set[str] | None = None,
        allowed_object_codes: set[str] | None = None,
    ) -> list[MilestoneRecord]:
        obj_keys = {_norm(x) for x in object_names if _norm(x)}
        mk_keys = {_norm(x) for x in (milestone_keys or set()) if _norm(x)}
        out: list[MilestoneRecord] = []
        for r in self.today_changes(allowed_object_codes):
            if obj_keys and _norm(r.object_name) not in obj_keys:
                continue
            if mk_keys and _norm(r.milestone_key) not in mk_keys:
                continue
            out.append(r)
        out.sort(key=lambda r: (_norm(r.object_name), r.due_date or date.max, _norm(r.milestone_key)))
        return out

    def hottest_week(
        self, limit: int = 20, allowed_object_codes: set[str] | None = None
    ) -> list[MilestoneRecord]:
        today = date.today()
        items = []
        for rec in self._filter_by_codes(self.get_records(), allowed_object_codes):
            # "Горящие вехи" считаем по целевой дате исполнения (колонка G).
            if rec.target_date is None:
                continue
            d = (rec.target_date - today).days
            if d <= 7:
                items.append((d, rec))
        items.sort(key=lambda x: x[0])
        return [r for _, r in items[:limit]]

    def today_changes(
        self, allowed_object_codes: set[str] | None = None
    ) -> list[MilestoneRecord]:
        out = []
        for rec in self._filter_by_codes(self.get_records(), allowed_object_codes):
            if _is_yes(rec.change_flag):
                out.append(rec)
        return out

    def changes_by_date_mode(
        self,
        date_mode: str,
        allowed_object_codes: set[str] | None = None,
    ) -> list[MilestoneRecord]:
        mode = (date_mode or "today").strip().lower()
        rows = self.today_changes(allowed_object_codes)
        if mode == "target":
            rows = [r for r in rows if r.target_date is not None]
            rows.sort(key=lambda r: (r.target_date or date.max, _norm(r.object_name), _norm(r.milestone_key)))
            return rows
        if mode == "yesterday":
            rows = [r for r in rows if r.due_date_yesterday is not None]
            rows.sort(key=lambda r: (r.due_date_yesterday or date.max, _norm(r.object_name), _norm(r.milestone_key)))
            return rows
        rows = [r for r in rows if r.due_date is not None]
        rows.sort(key=lambda r: (r.due_date or date.max, _norm(r.object_name), _norm(r.milestone_key)))
        return rows

    def object_changes(
        self, object_query: str, allowed_object_codes: set[str] | None = None
    ) -> list[MilestoneRecord]:
        q = _norm(object_query)
        return [r for r in self.today_changes(allowed_object_codes) if q in _norm(r.object_name)]

    def object_changes_exact(
        self, object_name: str, allowed_object_codes: set[str] | None = None
    ) -> list[MilestoneRecord]:
        needle = (object_name or "").strip().lower()
        return [
            r for r in self.today_changes(allowed_object_codes) if _norm(r.object_name) == needle
        ]

    def object_team(
        self, object_query: str, allowed_object_codes: set[str] | None = None
    ) -> list[MilestoneRecord]:
        q = _norm(object_query)
        seen = set()
        out = []
        for r in self._filter_by_codes(self.get_records(), allowed_object_codes):
            if q not in _norm(r.object_name):
                continue
            key = (r.object_name, r.responsible, r.responsible_email, r.role)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        out.sort(key=lambda r: (r.object_name.lower(), r.responsible.lower()))
        return out

    def object_team_exact(
        self, object_name: str, allowed_object_codes: set[str] | None = None
    ) -> list[MilestoneRecord]:
        needle = (object_name or "").strip().lower()
        seen = set()
        out = []
        for r in self._filter_by_codes(self.get_records(), allowed_object_codes):
            if _norm(r.object_name) != needle:
                continue
            key = (r.responsible, r.responsible_email, r.role)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        out.sort(key=lambda r: r.responsible.lower())
        return out

    def friday_attention(
        self, limit: int = 30, allowed_object_codes: set[str] | None = None
    ) -> list[MilestoneRecord]:
        today = date.today()
        out = []
        for rec in self._filter_by_codes(self.get_records(), allowed_object_codes):
            if rec.due_date is None:
                continue
            days = (rec.due_date - today).days
            dev = abs(self._deviation_weight(rec))
            if 0 <= days <= 7 or dev >= 7:
                out.append((days, -dev, rec))
        out.sort(key=lambda x: (x[0], x[1]))
        return [r for _, __, r in out[:limit]]

    def milestones_for_object(
        self,
        object_name: str,
        allowed_object_codes: set[str] | None = None,
        only_changed: bool = False,
    ) -> list[MilestoneRecord]:
        needle = _norm(object_name)
        source = (
            self.today_changes(allowed_object_codes)
            if only_changed
            else self._filter_by_codes(self.get_records(), allowed_object_codes)
        )
        rows = [r for r in source if _norm(r.object_name) == needle]
        rows.sort(key=lambda r: (r.due_date or date.max, r.milestone_name.lower()))
        return rows

    def milestones_for_milestone_and_objects(
        self,
        milestone_key: str,
        object_names: set[str],
        allowed_object_codes: set[str] | None = None,
    ) -> list[MilestoneRecord]:
        needle = _norm(milestone_key)
        obj_keys = {_norm(x) for x in object_names}
        out = []
        for r in self._filter_by_codes(self.get_records(), allowed_object_codes):
            if _norm(r.milestone_key) != needle:
                continue
            if obj_keys and _norm(r.object_name) not in obj_keys:
                continue
            out.append(r)
        out.sort(key=lambda r: (_norm(r.object_name), r.due_date or date.max))
        return out

    def passive_search(
        self,
        query: str,
        limit: int = 20,
        allowed_object_codes: set[str] | None = None,
    ) -> list[MilestoneRecord]:
        q = _norm(query)
        if not q:
            return []
        out = []
        for r in self._filter_by_codes(self.get_records(), allowed_object_codes):
            hay = " | ".join(
                [
                    r.milestone_key,
                    r.milestone_name,
                    r.object_name,
                    r.object_code,
                    r.status_today,
                    r.status_yesterday,
                    r.responsible,
                    r.responsible_email,
                    r.role,
                    r.comment,
                ]
            ).lower()
            if q in hay:
                out.append(r)
                if len(out) >= limit:
                    break
        return out

    def priority_milestone_keys(self) -> list[str]:
        return sorted(MILESTONE_WEIGHTS.keys(), key=lambda k: (-MILESTONE_WEIGHTS[k], k))

    def priority_milestones_for_objects(
        self,
        object_names: set[str],
        allowed_object_codes: set[str] | None = None,
    ) -> list[str]:
        obj_keys = {_norm(x) for x in object_names if _norm(x)}
        present: set[str] = set()
        for rec in self._filter_by_codes(self.get_records(), allowed_object_codes):
            if obj_keys and _norm(rec.object_name) not in obj_keys:
                continue
            key = _norm_stage_key(rec.milestone_key)
            if key in MILESTONE_WEIGHTS:
                present.add(key)
        ordered = self.priority_milestone_keys()
        return [k for k in ordered if k in present]

    def milestone_weight(self, milestone_key: str) -> float:
        return MILESTONE_WEIGHTS.get(_norm_stage_key(milestone_key), 0.0)

    def milestone_importance_label(self, milestone_key: str) -> str:
        return _weight_label(self.milestone_weight(milestone_key))

    def important_milestones(
        self,
        object_names: set[str],
        milestone_keys: set[str],
        allowed_object_codes: set[str] | None = None,
        limit: int = 300,
    ) -> list[tuple[float, MilestoneRecord]]:
        obj_keys = {_norm(x) for x in object_names if _norm(x)}
        mk_keys = {_norm_stage_key(x) for x in milestone_keys if _norm_stage_key(x)}
        out: list[tuple[float, MilestoneRecord]] = []
        for rec in self._filter_by_codes(self.get_records(), allowed_object_codes):
            if obj_keys and _norm(rec.object_name) not in obj_keys:
                continue
            stage_key = _norm_stage_key(rec.milestone_key)
            if mk_keys and stage_key not in mk_keys:
                continue
            weight = self.milestone_weight(stage_key)
            if weight <= 0:
                continue
            out.append((weight, rec))
        out.sort(key=lambda x: (-x[0], _norm(x[1].object_name), _norm(x[1].milestone_key)))
        return out[:limit]

    def unique_status_display_values(self, records: list[MilestoneRecord], source: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for r in records:
            disp = _status_display_for_record(r, source)
            if disp in seen:
                continue
            seen.add(disp)
            out.append(disp)
        return sorted(out, key=lambda x: (x != EMPTY_STATUS_DISPLAY, x.lower()))

    def filter_records_by_status_displays(
        self,
        records: list[MilestoneRecord],
        source: str,
        selected_status_displays: set[str] | None,
    ) -> list[MilestoneRecord]:
        if not selected_status_displays:
            return records
        sel = {x for x in selected_status_displays if x}
        if not sel:
            return records
        return [r for r in records if _status_display_for_record(r, source) in sel]

    def unique_object_codes_in(self, records: list[MilestoneRecord]) -> list[str]:
        seen: dict[str, str] = {}
        for r in records:
            code = (r.object_code or "").strip()
            if not code:
                continue
            seen.setdefault(code.lower(), code)
        return sorted(seen.values(), key=lambda x: x.lower())

    def unique_responsibles_in(self, records: list[MilestoneRecord]) -> list[str]:
        seen: dict[str, str] = {}
        for r in records:
            name = (r.responsible or "").strip()
            if not name:
                continue
            seen.setdefault(name.lower(), name)
        return sorted(seen.values(), key=lambda x: x.lower())

    @staticmethod
    def deviation_bucket_label(value: int) -> str:
        if value <= -7:
            return "<= -7"
        if -6 <= value <= -1:
            return "-6..-1"
        if value == 0:
            return "0"
        if 1 <= value <= 6:
            return "1..6"
        return ">= 7"

    def unique_deviation_buckets_in(self, records: list[MilestoneRecord]) -> list[str]:
        seen: set[str] = set()
        for r in records:
            v = _parse_deviation(r.deviation or "")
            seen.add(self.deviation_bucket_label(v))
        order = {"<= -7": 0, "-6..-1": 1, "0": 2, "1..6": 3, ">= 7": 4}
        return sorted(seen, key=lambda x: order.get(x, 99))

    def filter_records_by_object_codes(
        self, records: list[MilestoneRecord], selected_codes: set[str] | None
    ) -> list[MilestoneRecord]:
        if not selected_codes:
            return records
        sel = {x.strip() for x in selected_codes if (x or "").strip()}
        if not sel:
            return records
        return [r for r in records if (r.object_code or "").strip() in sel]

    def filter_records_by_responsibles(
        self, records: list[MilestoneRecord], selected_responsibles: set[str] | None
    ) -> list[MilestoneRecord]:
        if not selected_responsibles:
            return records
        sel = {x.strip() for x in selected_responsibles if (x or "").strip()}
        if not sel:
            return records
        return [r for r in records if (r.responsible or "").strip() in sel]

    def filter_records_by_deviation_buckets(
        self, records: list[MilestoneRecord], selected_buckets: set[str] | None
    ) -> list[MilestoneRecord]:
        if not selected_buckets:
            return records
        sel = {x.strip() for x in selected_buckets if (x or "").strip()}
        if not sel:
            return records
        return [r for r in records if self.deviation_bucket_label(_parse_deviation(r.deviation or "")) in sel]

    def priority_milestones_in_records(self, records: list[MilestoneRecord]) -> list[str]:
        present: set[str] = set()
        for r in records:
            key = _norm_stage_key(r.milestone_key)
            if key in MILESTONE_WEIGHTS and self.milestone_weight(key) > 0:
                present.add(key)
        ordered = self.priority_milestone_keys()
        return [k for k in ordered if k in present]

    def important_milestones_from_records(
        self, records: list[MilestoneRecord], limit: int = 400
    ) -> list[tuple[float, MilestoneRecord]]:
        out: list[tuple[float, MilestoneRecord]] = []
        for r in records:
            key = _norm_stage_key(r.milestone_key)
            weight = self.milestone_weight(key)
            if weight <= 0:
                continue
            out.append((weight, r))
        out.sort(key=lambda x: (-x[0], _norm(x[1].object_name), _norm(x[1].milestone_key)))
        return out[:limit]

