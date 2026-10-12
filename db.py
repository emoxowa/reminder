"""Слой доступа к данным: SQLite3-хранилище напоминаний."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from recurrence import REPEAT_NONE, REPEAT_RULES, next_occurrence

STATUS_PENDING = "Ожидает"
STATUS_DONE = "Готово"
STATUS_OVERDUE = "Просрочено"
STATUS_CANCELLED = "Отменено"
STATUSES = (STATUS_PENDING, STATUS_DONE, STATUS_OVERDUE, STATUS_CANCELLED)

KIND_ONCE = "once"
KIND_RECURRING = "recurring"

DEFAULT_DB_PATH = Path.home() / ".reminder_app" / "reminders.db"

# Формат хранения даты в БД (ISO, корректно сортируется как строка)
DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# due_at       — плановое время текущего срабатывания;
# start_at     — начало серии, от него считаются повторы;
# snooze_until — если задано, напоминание отложено до этого времени.
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS reminders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    due_at       TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT '{STATUS_PENDING}'
                 CHECK (status IN ({", ".join(f"'{s}'" for s in STATUSES)})),
    notified     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    repeat       TEXT    NOT NULL DEFAULT '{REPEAT_NONE}',
    start_at     TEXT,
    snooze_until TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_status_due ON reminders (status, due_at);
"""

# Колонки, добавленные после первой версии: (имя, определение)
MIGRATIONS = (
    ("repeat", f"TEXT NOT NULL DEFAULT '{REPEAT_NONE}'"),
    ("start_at", "TEXT"),
    ("snooze_until", "TEXT"),
)

# Фактическое время срабатывания с учётом «отложить»
TRIGGER_AT = "COALESCE(snooze_until, due_at)"


@dataclass
class Reminder:
    id: int
    title: str
    description: str
    due_at: datetime
    status: str
    notified: bool
    created_at: datetime
    repeat: str
    start_at: datetime
    snooze_until: Optional[datetime]

    @property
    def is_recurring(self) -> bool:
        return self.repeat != REPEAT_NONE

    @property
    def trigger_at(self) -> datetime:
        return self.snooze_until or self.due_at

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Reminder":
        return cls(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            due_at=_parse(row["due_at"]),
            status=row["status"],
            notified=bool(row["notified"]),
            created_at=_parse(row["created_at"]),
            repeat=row["repeat"],
            start_at=_parse(row["start_at"] or row["due_at"]),
            snooze_until=_parse(row["snooze_until"]) if row["snooze_until"] else None,
        )


def _fmt(dt: datetime) -> str:
    return dt.strftime(DB_DATETIME_FORMAT)


def _parse(value: str) -> datetime:
    return datetime.strptime(value, DB_DATETIME_FORMAT)


class ReminderDB:
    def __init__(self, path: Path = DEFAULT_DB_PATH):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        """Создаёт таблицы и индексы, если их ещё нет, и дополняет старую схему."""
        with self.conn:
            self.conn.executescript(SCHEMA)
            existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(reminders)")}
            for column, definition in MIGRATIONS:
                if column not in existing:
                    self.conn.execute(f"ALTER TABLE reminders ADD COLUMN {column} {definition}")
            self.conn.execute("UPDATE reminders SET start_at = due_at WHERE start_at IS NULL")

    def close(self) -> None:
        self.conn.close()

    # --- CRUD ---------------------------------------------------------------

    def add(self, title: str, description: str, due_at: datetime,
            repeat: str = REPEAT_NONE) -> int:
        if repeat not in REPEAT_RULES:
            raise ValueError(f"Неизвестное правило повтора: {repeat}")
        start_at = due_at
        if repeat != REPEAT_NONE and due_at <= datetime.now():
            # Серия, начавшаяся в прошлом, сработает в ближайший будущий раз
            due_at = next_occurrence(start_at, repeat, datetime.now())
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO reminders "
                "(title, description, due_at, start_at, repeat, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, description, _fmt(due_at), _fmt(start_at), repeat,
                 STATUS_PENDING, _fmt(datetime.now())),
            )
        return cur.lastrowid

    def delete(self, reminder_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))

    def get(self, reminder_id: int) -> Optional[Reminder]:
        row = self.conn.execute(
            "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
        return Reminder.from_row(row) if row else None

    def list(self, status: Optional[str] = None, kind: Optional[str] = None) -> List[Reminder]:
        """Все напоминания; `status` — фильтр по статусу,
        `kind` — KIND_ONCE (одноразовые) или KIND_RECURRING (повторяющиеся)."""
        conditions, params = [], []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if kind == KIND_ONCE:
            conditions.append("repeat = ?")
            params.append(REPEAT_NONE)
        elif kind == KIND_RECURRING:
            conditions.append("repeat != ?")
            params.append(REPEAT_NONE)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.conn.execute(
            f"SELECT * FROM reminders {where} ORDER BY {TRIGGER_AT}", params
        )
        return [Reminder.from_row(r) for r in rows]

    def set_status(self, reminder_id: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"Неизвестный статус: {status}")
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id)
            )

    def complete(self, reminder_id: int) -> Optional[datetime]:
        """Отмечает напоминание выполненным. Одноразовое получает статус «Готово»,
        повторяющееся переносится на следующее срабатывание (его время и возвращается)."""
        reminder = self.get(reminder_id)
        if reminder is None:
            return None
        if not reminder.is_recurring:
            self.set_status(reminder_id, STATUS_DONE)
            return None
        if reminder.status == STATUS_CANCELLED:
            return None  # отменённая серия не возобновляется
        # Выполнили заранее — следующее срабатывание после текущего, иначе — после «сейчас»
        return self._advance(reminder, after=max(datetime.now(), reminder.due_at))

    def snooze(self, reminder_id: int, minutes: int) -> None:
        """Откладывает напоминание на N минут от текущего момента.
        Расписание повторяющейся серии при этом не сдвигается."""
        until = datetime.now().replace(microsecond=0) + timedelta(minutes=minutes)
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET snooze_until = ?, status = ?, notified = 0 WHERE id = ?",
                (_fmt(until), STATUS_PENDING, reminder_id),
            )

    def _advance(self, reminder: Reminder, after: datetime) -> datetime:
        next_due = next_occurrence(reminder.start_at, reminder.repeat, after)
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET due_at = ?, status = ?, notified = 0, snooze_until = NULL "
                "WHERE id = ?",
                (_fmt(next_due), STATUS_PENDING, reminder.id),
            )
        return next_due

    # --- Логика планировщика ----------------------------------------------

    def due_unnotified(self, now: datetime) -> List[Reminder]:
        """Ожидающие напоминания, время которых наступило, но о которых ещё не уведомили."""
        rows = self.conn.execute(
            f"SELECT * FROM reminders WHERE status = ? AND notified = 0 AND {TRIGGER_AT} <= ? "
            f"ORDER BY {TRIGGER_AT}",
            (STATUS_PENDING, _fmt(now)),
        )
        return [Reminder.from_row(r) for r in rows]

    def mark_notified(self, reminder_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE reminders SET notified = 1 WHERE id = ?", (reminder_id,)
            )

    def process_missed(self, now: datetime, grace: timedelta) -> int:
        """Обрабатывает напоминания, на которые не отреагировали в течение `grace`
        после уведомления: одноразовые становятся «Просрочено», повторяющиеся
        переносятся на следующее срабатывание. Возвращает число изменённых."""
        rows = self.conn.execute(
            f"SELECT * FROM reminders WHERE status = ? AND notified = 1 AND {TRIGGER_AT} <= ?",
            (STATUS_PENDING, _fmt(now - grace)),
        ).fetchall()
        for row in rows:
            reminder = Reminder.from_row(row)
            if reminder.is_recurring:
                self._advance(reminder, after=now)
            else:
                self.set_status(reminder.id, STATUS_OVERDUE)
        return len(rows)
