from __future__ import annotations

import sqlite3
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_BLOCKED = "blocked"

# Ordered optional filter stages (extensible). Keys stored in user_report_filter_stages.stages_json.
OPTIONAL_FILTER_STAGE_KEYS: tuple[str, ...] = ("status",)

# Ordered pipeline stages for the flexible filter engine (full list, including core stages).
FILTER_PIPELINE_STAGE_KEYS: tuple[str, ...] = (
    "object",
    "milestone",
    "status",
    "code",
    "responsible",
    "deviation",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class UserAccess:
    telegram_id: int
    username: str
    full_name: str
    status: str
    is_admin: bool
    object_codes: set[str]
    allowed_buttons: set[str]
    hidden_buttons: set[str]


@dataclass(frozen=True)
class NoteItem:
    id: int
    author_id: int
    author_name: str
    text: str
    milestone_key: str
    object_name: str
    created_at: str


@dataclass(frozen=True)
class UserCustomReport:
    telegram_id: int
    date_mode: str
    object_codes: set[str]
    milestone_keys: set[str]
    field_keys: set[str]
    updated_at: str


@dataclass(frozen=True)
class UserLastReport:
    telegram_id: int
    report_text: str
    report_meta: dict[str, str]
    created_at: str


@dataclass(frozen=True)
class UserEmailPref:
    telegram_id: int
    email: str
    confirmed: bool
    opt_in: bool
    updated_at: str


class AccessRepository:
    def __init__(self, db_path: Path, admin_telegram_id: int) -> None:
        self._db_path = db_path
        self._admin_telegram_id = admin_telegram_id
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.ensure_admin(admin_telegram_id)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL DEFAULT '',
                    full_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_object_codes (
                    telegram_id INTEGER NOT NULL,
                    object_code TEXT NOT NULL,
                    PRIMARY KEY (telegram_id, object_code)
                );

                CREATE TABLE IF NOT EXISTS approval_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS button_catalog (
                    button_key TEXT PRIMARY KEY,
                    button_label TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_allowed_buttons (
                    telegram_id INTEGER NOT NULL,
                    button_key TEXT NOT NULL,
                    PRIMARY KEY (telegram_id, button_key)
                );

                CREATE TABLE IF NOT EXISTS user_hidden_buttons (
                    telegram_id INTEGER NOT NULL,
                    button_key TEXT NOT NULL,
                    PRIMARY KEY (telegram_id, button_key)
                );

                CREATE TABLE IF NOT EXISTS user_visible_fields (
                    telegram_id INTEGER NOT NULL,
                    field_key TEXT NOT NULL,
                    PRIMARY KEY (telegram_id, field_key)
                );

                CREATE TABLE IF NOT EXISTS user_visible_fields_by_report (
                    telegram_id INTEGER NOT NULL,
                    report_key TEXT NOT NULL,
                    field_key TEXT NOT NULL,
                    PRIMARY KEY (telegram_id, report_key, field_key)
                );

                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    author_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    milestone_key TEXT NOT NULL DEFAULT '',
                    object_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notes_object_name ON notes(object_name);
                CREATE INDEX IF NOT EXISTS idx_notes_milestone_key ON notes(milestone_key);
                CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at);

                CREATE TABLE IF NOT EXISTS global_hidden_objects (
                    object_name TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS global_hidden_object_codes (
                    object_code TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_custom_reports (
                    telegram_id INTEGER PRIMARY KEY,
                    date_mode TEXT NOT NULL DEFAULT 'today',
                    object_codes_json TEXT NOT NULL DEFAULT '[]',
                    milestone_keys_json TEXT NOT NULL DEFAULT '[]',
                    field_keys_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_last_reports (
                    telegram_id INTEGER PRIMARY KEY,
                    report_text TEXT NOT NULL,
                    report_meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_email_prefs (
                    telegram_id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL DEFAULT '',
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    opt_in INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_email_send_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_user_email_send_log_uid ON user_email_send_log(telegram_id);

                CREATE TABLE IF NOT EXISTS user_report_filter_stages (
                    telegram_id INTEGER NOT NULL,
                    report_key TEXT NOT NULL,
                    stages_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (telegram_id, report_key)
                );

                CREATE TABLE IF NOT EXISTS user_report_filter_pipeline (
                    telegram_id INTEGER NOT NULL,
                    report_key TEXT NOT NULL,
                    pipeline_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (telegram_id, report_key)
                );
                """
            )
            conn.commit()

    @staticmethod
    def _loads_set(payload: str) -> set[str]:
        try:
            vals = json.loads(payload or "[]")
        except Exception:
            return set()
        if not isinstance(vals, list):
            return set()
        return {str(v).strip() for v in vals if str(v).strip()}

    @staticmethod
    def _norm_email(email: str) -> str:
        return (email or "").strip().lower()

    def upsert_button_catalog(self, button_map: dict[str, str]) -> None:
        with self._connect() as conn:
            for key, label in sorted(button_map.items()):
                conn.execute(
                    """
                    INSERT INTO button_catalog (button_key, button_label)
                    VALUES (?, ?)
                    ON CONFLICT(button_key) DO UPDATE SET button_label = excluded.button_label
                    """,
                    (key, label),
                )
            conn.commit()

    def all_button_keys(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT button_key FROM button_catalog ORDER BY button_key"
            ).fetchall()
        return [r["button_key"] for r in rows]

    def _get_buttons(self, table: str, telegram_id: int) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT button_key FROM {table} WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchall()
        return {r["button_key"] for r in rows}

    def set_allowed_buttons(self, telegram_id: int, button_keys: set[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM user_allowed_buttons WHERE telegram_id = ?",
                (telegram_id,),
            )
            for key in sorted(button_keys):
                conn.execute(
                    "INSERT INTO user_allowed_buttons (telegram_id, button_key) VALUES (?, ?)",
                    (telegram_id, key),
                )
            conn.commit()

    def set_hidden_buttons(self, telegram_id: int, button_keys: set[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM user_hidden_buttons WHERE telegram_id = ?",
                (telegram_id,),
            )
            for key in sorted(button_keys):
                conn.execute(
                    "INSERT INTO user_hidden_buttons (telegram_id, button_key) VALUES (?, ?)",
                    (telegram_id, key),
                )
            conn.commit()

    def get_visible_fields(self, telegram_id: int) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT field_key FROM user_visible_fields WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchall()
        return {str(r["field_key"]) for r in rows}

    def set_visible_fields(self, telegram_id: int, field_keys: set[str]) -> None:
        cleaned = sorted({k.strip() for k in field_keys if k and k.strip()})
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM user_visible_fields WHERE telegram_id = ?",
                (telegram_id,),
            )
            for key in cleaned:
                conn.execute(
                    "INSERT INTO user_visible_fields (telegram_id, field_key) VALUES (?, ?)",
                    (telegram_id, key),
                )
            conn.commit()

    def get_visible_fields_for_report(self, telegram_id: int, report_key: str) -> set[str]:
        rk = (report_key or "").strip()
        if not rk:
            return set()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT field_key
                FROM user_visible_fields_by_report
                WHERE telegram_id = ? AND report_key = ?
                """,
                (telegram_id, rk),
            ).fetchall()
        return {str(r["field_key"]) for r in rows}

    def set_visible_fields_for_report(self, telegram_id: int, report_key: str, field_keys: set[str]) -> None:
        rk = (report_key or "").strip()
        if not rk:
            return
        cleaned = sorted({k.strip() for k in field_keys if k and k.strip()})
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM user_visible_fields_by_report
                WHERE telegram_id = ? AND report_key = ?
                """,
                (telegram_id, rk),
            )
            for key in cleaned:
                conn.execute(
                    """
                    INSERT INTO user_visible_fields_by_report (telegram_id, report_key, field_key)
                    VALUES (?, ?, ?)
                    """,
                    (telegram_id, rk, key),
                )
            conn.commit()

    def effective_buttons(self, telegram_id: int, constructor_key: str) -> set[str]:
        all_buttons = set(self.all_button_keys())
        allowed = self._get_buttons("user_allowed_buttons", telegram_id)
        hidden = self._get_buttons("user_hidden_buttons", telegram_id)
        if not allowed:
            allowed = set(all_buttons)
        effective = (allowed & all_buttons) - hidden
        effective.add(constructor_key)
        return effective

    def add_note(
        self,
        author_id: int,
        text: str,
        milestone_key: str = "",
        object_name: str = "",
    ) -> int:
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO notes (author_id, text, milestone_key, object_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (author_id, text.strip(), milestone_key.strip(), object_name.strip(), now, now),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_notes(
        self,
        object_name: str = "",
        milestone_key: str = "",
        limit: int = 100,
    ) -> list[NoteItem]:
        clauses = []
        params: list = []
        if object_name.strip():
            clauses.append("LOWER(object_name) = LOWER(?)")
            params.append(object_name.strip())
        if milestone_key.strip():
            clauses.append("LOWER(milestone_key) = LOWER(?)")
            params.append(milestone_key.strip())
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT n.id, n.author_id, n.text, n.milestone_key, n.object_name, n.created_at, u.full_name
            FROM notes n
            LEFT JOIN users u ON u.telegram_id = n.author_id
            {where_sql}
            ORDER BY n.created_at DESC
            LIMIT ?
        """
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        out: list[NoteItem] = []
        for r in rows:
            out.append(
                NoteItem(
                    id=r["id"],
                    author_id=r["author_id"],
                    author_name=r["full_name"] or str(r["author_id"]),
                    text=r["text"],
                    milestone_key=r["milestone_key"] or "",
                    object_name=r["object_name"] or "",
                    created_at=r["created_at"],
                )
            )
        return out

    def get_note(self, note_id: int) -> NoteItem | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT n.id, n.author_id, n.text, n.milestone_key, n.object_name, n.created_at, u.full_name
                FROM notes n
                LEFT JOIN users u ON u.telegram_id = n.author_id
                WHERE n.id = ?
                """,
                (note_id,),
            ).fetchone()
        if row is None:
            return None
        return NoteItem(
            id=row["id"],
            author_id=row["author_id"],
            author_name=row["full_name"] or str(row["author_id"]),
            text=row["text"],
            milestone_key=row["milestone_key"] or "",
            object_name=row["object_name"] or "",
            created_at=row["created_at"],
        )

    def update_note_text(self, note_id: int, text: str) -> bool:
        now = _now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                return False
            conn.execute(
                "UPDATE notes SET text = ?, updated_at = ? WHERE id = ?",
                (text.strip(), now, note_id),
            )
            conn.commit()
            return True

    def delete_note(self, note_id: int, requestor_id: int, is_admin: bool) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT author_id FROM notes WHERE id = ?",
                (note_id,),
            ).fetchone()
            if row is None:
                return False
            if (not is_admin) and (row["author_id"] != requestor_id):
                return False
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            conn.commit()
            return True

    def last_note_id_by_author(self, author_id: int) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM notes WHERE author_id = ? ORDER BY id DESC LIMIT 1",
                (author_id,),
            ).fetchone()
        return int(row["id"]) if row else None

    def bind_note(self, note_id: int, object_name: str = "", milestone_key: str = "") -> bool:
        now = _now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                return False
            conn.execute(
                """
                UPDATE notes
                SET object_name = ?, milestone_key = ?, updated_at = ?
                WHERE id = ?
                """,
                (object_name.strip(), milestone_key.strip(), now, note_id),
            )
            conn.commit()
            return True

    def ensure_admin(self, telegram_id: int) -> None:
        now = _now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT telegram_id FROM users WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO users
                    (telegram_id, username, full_name, status, is_admin, created_at, updated_at)
                    VALUES (?, '', 'Admin', ?, 1, ?, ?)
                    """,
                    (telegram_id, STATUS_APPROVED, now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET is_admin = 1, status = ?, updated_at = ?
                    WHERE telegram_id = ?
                    """,
                    (STATUS_APPROVED, now, telegram_id),
                )
            conn.commit()

    def get_user_access(self, telegram_id: int) -> UserAccess | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT telegram_id, username, full_name, status, is_admin
                FROM users
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
            if row is None:
                return None
            codes = {
                r["object_code"]
                for r in conn.execute(
                    "SELECT object_code FROM user_object_codes WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchall()
            }
            allowed = self._get_buttons("user_allowed_buttons", telegram_id)
            hidden = self._get_buttons("user_hidden_buttons", telegram_id)
            return UserAccess(
                telegram_id=row["telegram_id"],
                username=row["username"],
                full_name=row["full_name"],
                status=row["status"],
                is_admin=bool(row["is_admin"]),
                object_codes=codes,
                allowed_buttons=allowed,
                hidden_buttons=hidden,
            )

    def upsert_seen_user(self, telegram_id: int, username: str, full_name: str) -> None:
        now = _now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT telegram_id FROM users WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO users
                    (telegram_id, username, full_name, status, is_admin, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, ?, ?)
                    """,
                    (telegram_id, username, full_name, STATUS_PENDING, now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET username = ?, full_name = ?, updated_at = ?
                    WHERE telegram_id = ?
                    """,
                    (username, full_name, now, telegram_id),
                )
            conn.commit()

    def create_approval_request(self, telegram_id: int) -> None:
        now = _now_iso()
        with self._connect() as conn:
            open_req = conn.execute(
                """
                SELECT id FROM approval_requests
                WHERE telegram_id = ? AND state = 'pending'
                ORDER BY id DESC LIMIT 1
                """,
                (telegram_id,),
            ).fetchone()
            if open_req is None:
                conn.execute(
                    """
                    INSERT INTO approval_requests (telegram_id, state, created_at, updated_at)
                    VALUES (?, 'pending', ?, ?)
                    """,
                    (telegram_id, now, now),
                )
                conn.commit()

    def resolve_approval_request(self, telegram_id: int, state: str) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE approval_requests
                SET state = ?, updated_at = ?
                WHERE telegram_id = ? AND state = 'pending'
                """,
                (state, now, telegram_id),
            )
            conn.commit()

    def set_user_status(self, telegram_id: int, status: str) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET status = ?, updated_at = ? WHERE telegram_id = ?",
                (status, now, telegram_id),
            )
            conn.commit()

    def set_user_object_codes(self, telegram_id: int, object_codes: set[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM user_object_codes WHERE telegram_id = ?",
                (telegram_id,),
            )
            for code in sorted(c for c in object_codes if c.strip()):
                conn.execute(
                    """
                    INSERT INTO user_object_codes (telegram_id, object_code)
                    VALUES (?, ?)
                    """,
                    (telegram_id, code),
                )
            conn.commit()

    def approve_user(self, telegram_id: int, object_codes: set[str]) -> None:
        self.set_user_object_codes(telegram_id, object_codes)
        self.set_user_status(telegram_id, STATUS_APPROVED)
        self.resolve_approval_request(telegram_id, "approved")

    def block_user(self, telegram_id: int) -> None:
        self.set_user_status(telegram_id, STATUS_BLOCKED)
        self.resolve_approval_request(telegram_id, "blocked")

    def list_users(self) -> list[UserAccess]:
        out: list[UserAccess] = []
        with self._connect() as conn:
            users = conn.execute(
                """
                SELECT telegram_id, username, full_name, status, is_admin
                FROM users
                ORDER BY is_admin DESC, updated_at DESC
                """
            ).fetchall()
            all_codes_rows = conn.execute(
                "SELECT telegram_id, object_code FROM user_object_codes"
            ).fetchall()
            all_allowed_rows = conn.execute(
                "SELECT telegram_id, button_key FROM user_allowed_buttons"
            ).fetchall()
            all_hidden_rows = conn.execute(
                "SELECT telegram_id, button_key FROM user_hidden_buttons"
            ).fetchall()

            code_map: dict[int, set[str]] = {}
            for r in all_codes_rows:
                code_map.setdefault(r["telegram_id"], set()).add(r["object_code"])
            allowed_map: dict[int, set[str]] = {}
            for r in all_allowed_rows:
                allowed_map.setdefault(r["telegram_id"], set()).add(r["button_key"])
            hidden_map: dict[int, set[str]] = {}
            for r in all_hidden_rows:
                hidden_map.setdefault(r["telegram_id"], set()).add(r["button_key"])

            for row in users:
                tid = row["telegram_id"]
                out.append(
                    UserAccess(
                        telegram_id=tid,
                        username=row["username"],
                        full_name=row["full_name"],
                        status=row["status"],
                        is_admin=bool(row["is_admin"]),
                        object_codes=code_map.get(tid, set()),
                        allowed_buttons=allowed_map.get(tid, set()),
                        hidden_buttons=hidden_map.get(tid, set()),
                    )
                )
        return out

    def list_global_hidden_objects(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT object_name FROM global_hidden_objects"
            ).fetchall()
        return {str(r["object_name"]).strip() for r in rows if str(r["object_name"]).strip()}

    def set_global_hidden_objects(self, object_names: set[str]) -> None:
        now = _now_iso()
        cleaned = sorted({x.strip() for x in object_names if x and x.strip()})
        with self._connect() as conn:
            conn.execute("DELETE FROM global_hidden_objects")
            for name in cleaned:
                conn.execute(
                    "INSERT INTO global_hidden_objects (object_name, updated_at) VALUES (?, ?)",
                    (name, now),
                )
            conn.commit()

    def apply_global_object_filter(self, object_names: list[str]) -> list[str]:
        hidden = {x.lower() for x in self.list_global_hidden_objects()}
        if not hidden:
            return object_names
        return [x for x in object_names if (x or "").strip().lower() not in hidden]

    def list_global_hidden_object_codes(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT object_code FROM global_hidden_object_codes"
            ).fetchall()
        return {str(r["object_code"]).strip() for r in rows if str(r["object_code"]).strip()}

    def set_global_hidden_object_codes(self, object_codes: set[str]) -> None:
        now = _now_iso()
        cleaned = sorted({x.strip() for x in object_codes if x and x.strip()})
        with self._connect() as conn:
            conn.execute("DELETE FROM global_hidden_object_codes")
            for code in cleaned:
                conn.execute(
                    "INSERT INTO global_hidden_object_codes (object_code, updated_at) VALUES (?, ?)",
                    (code, now),
                )
            conn.commit()

    def get_user_custom_report(self, telegram_id: int) -> UserCustomReport | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT telegram_id, date_mode, object_codes_json, milestone_keys_json, field_keys_json, updated_at
                FROM user_custom_reports
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
        if row is None:
            return None
        return UserCustomReport(
            telegram_id=int(row["telegram_id"]),
            date_mode=(row["date_mode"] or "today").strip() or "today",
            object_codes=self._loads_set(row["object_codes_json"]),
            milestone_keys=self._loads_set(row["milestone_keys_json"]),
            field_keys=self._loads_set(row["field_keys_json"]),
            updated_at=row["updated_at"],
        )

    def set_user_custom_report(
        self,
        telegram_id: int,
        date_mode: str,
        object_codes: set[str],
        milestone_keys: set[str],
        field_keys: set[str],
    ) -> None:
        now = _now_iso()
        mode = (date_mode or "today").strip().lower()
        if mode not in {"today", "yesterday", "target"}:
            mode = "today"
        obj = sorted({x.strip() for x in object_codes if x and x.strip()})
        mks = sorted({x.strip() for x in milestone_keys if x and x.strip()})
        fields = sorted({x.strip() for x in field_keys if x and x.strip()})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_custom_reports
                (telegram_id, date_mode, object_codes_json, milestone_keys_json, field_keys_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    date_mode = excluded.date_mode,
                    object_codes_json = excluded.object_codes_json,
                    milestone_keys_json = excluded.milestone_keys_json,
                    field_keys_json = excluded.field_keys_json,
                    updated_at = excluded.updated_at
                """,
                (
                    telegram_id,
                    mode,
                    json.dumps(obj, ensure_ascii=False),
                    json.dumps(mks, ensure_ascii=False),
                    json.dumps(fields, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()

    def save_user_last_report(self, telegram_id: int, report_text: str, report_meta: dict[str, str]) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_last_reports (telegram_id, report_text, report_meta_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    report_text = excluded.report_text,
                    report_meta_json = excluded.report_meta_json,
                    created_at = excluded.created_at
                """,
                (
                    telegram_id,
                    (report_text or "").strip(),
                    json.dumps(report_meta or {}, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()

    def get_user_last_report(self, telegram_id: int) -> UserLastReport | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT telegram_id, report_text, report_meta_json, created_at
                FROM user_last_reports
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            meta = json.loads(row["report_meta_json"] or "{}")
            if not isinstance(meta, dict):
                meta = {}
        except Exception:
            meta = {}
        return UserLastReport(
            telegram_id=int(row["telegram_id"]),
            report_text=row["report_text"] or "",
            report_meta={str(k): str(v) for k, v in meta.items()},
            created_at=row["created_at"],
        )

    def get_user_email_pref(self, telegram_id: int) -> UserEmailPref | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT telegram_id, email, confirmed, opt_in, updated_at
                FROM user_email_prefs
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
        if row is None:
            return None
        return UserEmailPref(
            telegram_id=int(row["telegram_id"]),
            email=row["email"] or "",
            confirmed=bool(row["confirmed"]),
            opt_in=bool(row["opt_in"]),
            updated_at=row["updated_at"],
        )

    def set_user_email_pref(self, telegram_id: int, email: str, confirmed: bool, opt_in: bool) -> None:
        now = _now_iso()
        norm = self._norm_email(email)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_email_prefs (telegram_id, email, confirmed, opt_in, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    email = excluded.email,
                    confirmed = excluded.confirmed,
                    opt_in = excluded.opt_in,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, norm, 1 if confirmed else 0, 1 if opt_in else 0, now),
            )
            conn.commit()

    @staticmethod
    def _normalize_optional_filter_stages(stages: list[str]) -> list[str]:
        allowed = set(OPTIONAL_FILTER_STAGE_KEYS)
        out: list[str] = []
        for x in stages or []:
            k = str(x).strip().lower()
            if k not in allowed or k in out:
                continue
            out.append(k)
        priority = {k: i for i, k in enumerate(OPTIONAL_FILTER_STAGE_KEYS)}
        return sorted(out, key=lambda k: priority[k])

    def get_user_optional_filter_stages(self, telegram_id: int, report_key: str) -> list[str]:
        rk = (report_key or "").strip()
        if not rk:
            return []
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT stages_json
                FROM user_report_filter_stages
                WHERE telegram_id = ? AND report_key = ?
                """,
                (telegram_id, rk),
            ).fetchone()
        if row is None:
            return []
        try:
            raw = json.loads(row["stages_json"] or "[]")
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        return self._normalize_optional_filter_stages([str(x) for x in raw])

    def set_user_optional_filter_stages(self, telegram_id: int, report_key: str, stages: list[str]) -> None:
        rk = (report_key or "").strip()
        if not rk:
            return
        now = _now_iso()
        norm = self._normalize_optional_filter_stages(stages)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_report_filter_stages (telegram_id, report_key, stages_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id, report_key) DO UPDATE SET
                    stages_json = excluded.stages_json,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, rk, json.dumps(norm, ensure_ascii=False), now),
            )
            conn.commit()

    @staticmethod
    def _normalize_filter_pipeline(stages: list[str]) -> list[str]:
        allowed = set(FILTER_PIPELINE_STAGE_KEYS)
        out: list[str] = []
        for x in stages or []:
            k = str(x).strip().lower()
            if k not in allowed or k in out:
                continue
            out.append(k)
        return out

    def get_user_filter_pipeline(self, telegram_id: int, report_key: str) -> list[str]:
        rk = (report_key or "").strip()
        if not rk:
            return []
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT pipeline_json
                FROM user_report_filter_pipeline
                WHERE telegram_id = ? AND report_key = ?
                """,
                (telegram_id, rk),
            ).fetchone()
        if row is None:
            return []
        try:
            raw = json.loads(row["pipeline_json"] or "[]")
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        return self._normalize_filter_pipeline([str(x) for x in raw])

    def set_user_filter_pipeline(self, telegram_id: int, report_key: str, stages: list[str]) -> None:
        rk = (report_key or "").strip()
        if not rk:
            return
        now = _now_iso()
        norm = self._normalize_filter_pipeline(stages)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_report_filter_pipeline (telegram_id, report_key, pipeline_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id, report_key) DO UPDATE SET
                    pipeline_json = excluded.pipeline_json,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, rk, json.dumps(norm, ensure_ascii=False), now),
            )
            conn.commit()

    def add_email_send_log(self, telegram_id: int, email: str, status: str, error_text: str = "") -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_email_send_log (telegram_id, email, status, error_text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (telegram_id, self._norm_email(email), (status or "").strip() or "unknown", (error_text or "").strip(), now),
            )
            conn.commit()

