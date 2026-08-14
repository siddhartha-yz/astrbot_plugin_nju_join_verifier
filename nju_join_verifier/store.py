from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReviewState:
    flag: str
    outcome: str
    updated_at: int


class ReviewStore:
    """Minimal idempotency/audit store.

    Intentionally stores no applicant name, student ID, or raw answer.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS reviews (
                flag TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                detail TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        self._db.commit()

    def get(self, flag: str) -> ReviewState | None:
        row = self._db.execute(
            "SELECT flag, outcome, updated_at FROM reviews WHERE flag = ?",
            (flag,),
        ).fetchone()
        if row is None:
            return None
        return ReviewState(flag=str(row[0]), outcome=str(row[1]), updated_at=int(row[2]))

    def record(
        self,
        *,
        flag: str,
        group_id: str,
        user_id: str,
        outcome: str,
        detail: str,
    ) -> None:
        now = int(time.time())
        self._db.execute(
            """
            INSERT INTO reviews(flag, group_id, user_id, outcome, detail, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(flag) DO UPDATE SET
                group_id = excluded.group_id,
                user_id = excluded.user_id,
                outcome = excluded.outcome,
                detail = excluded.detail,
                updated_at = excluded.updated_at
            """,
            (flag, group_id, user_id, outcome, detail, now),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()
