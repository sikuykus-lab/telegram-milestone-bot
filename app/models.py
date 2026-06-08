from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MilestoneRecord:
    row_index: int
    milestone_key: str  # B
    milestone_name: str  # C
    object_name: str  # W
    object_code: str  # V
    due_date: date | None  # E
    forecast_fact_date: date | None  # F (ПРОГНОЗ/ФАКТ)
    target_date: date | None  # G
    due_date_yesterday: date | None  # Q
    status_today: str  # H
    status_yesterday: str  # T (0-based index 19)
    responsible: str  # I
    responsible_email: str  # J
    role: str  # K
    deviation: str  # N
    comment: str  # M
    change_flag: str  # P (Да/Нет)
