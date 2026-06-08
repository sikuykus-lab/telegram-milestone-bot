from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable

import gspread
from oauth2client.service_account import ServiceAccountCredentials

LOGGER = logging.getLogger(__name__)
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


class SheetsClient:
    def __init__(self, creds_path: Path, retries: int = 3, retry_delay: float = 1.5) -> None:
        self._creds_path = creds_path
        self._retries = retries
        self._retry_delay = retry_delay
        self._client = self._authorize()

    def _authorize(self) -> gspread.Client:
        if not self._creds_path.is_file():
            raise FileNotFoundError(f"Google credentials not found: {self._creds_path}")
        credentials = ServiceAccountCredentials.from_json_keyfile_name(
            str(self._creds_path), SCOPE
        )
        return gspread.authorize(credentials)

    def read_ranges(
        self, spreadsheet_id: str, sheet_name: str, ranges: Iterable[str]
    ) -> dict[str, list[list[str]]]:
        ws = self._client.open_by_key(spreadsheet_id).worksheet(sheet_name)
        ranges = list(ranges)
        for attempt in range(1, self._retries + 1):
            try:
                blocks = ws.batch_get(ranges)
                return {a1: (block or []) for a1, block in zip(ranges, blocks)}
            except Exception as exc:
                LOGGER.warning("batch_get failed attempt %s/%s: %s", attempt, self._retries, exc)
                if attempt == self._retries:
                    break
                time.sleep(self._retry_delay * attempt)

        fallback: dict[str, list[list[str]]] = {}
        for a1 in ranges:
            for attempt in range(1, self._retries + 1):
                try:
                    fallback[a1] = ws.get(a1)
                    break
                except Exception as exc:
                    LOGGER.warning(
                        "single-range get failed for %s attempt %s/%s: %s",
                        a1,
                        attempt,
                        self._retries,
                        exc,
                    )
                    if attempt == self._retries:
                        fallback[a1] = []
                    else:
                        time.sleep(self._retry_delay * attempt)
        return fallback
