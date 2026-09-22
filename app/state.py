from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.models import AnalyzedImage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class UploadRecord:
    sha256: str
    source_path: str
    current_path: str
    status: str
    photo_id: str | None
    share_link: str | None
    maps_publish_status: str | None
    error: str | None
    file_moved: bool
    updated_at: str


class UploadState:
    def __init__(self, database_path: Path):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS uploads (
                sha256 TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                current_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                capture_time TEXT,
                latitude REAL,
                longitude REAL,
                status TEXT NOT NULL,
                upload_url TEXT,
                photo_id TEXT,
                share_link TEXT,
                maps_publish_status TEXT,
                error TEXT,
                file_moved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _record(row: sqlite3.Row | None) -> UploadRecord | None:
        if row is None:
            return None
        return UploadRecord(
            sha256=row["sha256"],
            source_path=row["source_path"],
            current_path=row["current_path"],
            status=row["status"],
            photo_id=row["photo_id"],
            share_link=row["share_link"],
            maps_publish_status=row["maps_publish_status"],
            error=row["error"],
            file_moved=bool(row["file_moved"]),
            updated_at=row["updated_at"],
        )

    def get(self, sha256: str) -> UploadRecord | None:
        row = self.connection.execute(
            "SELECT * FROM uploads WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return self._record(row)

    def begin(self, image: AnalyzedImage) -> None:
        metadata = image.metadata
        if metadata is None:
            raise ValueError("Cannot create upload state for an unreadable image")
        now = _now()
        self.connection.execute(
            """
            INSERT INTO uploads (
                sha256, source_path, current_path, size_bytes, capture_time,
                latitude, longitude, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET
                source_path = excluded.source_path,
                current_path = excluded.current_path,
                size_bytes = excluded.size_bytes,
                capture_time = excluded.capture_time,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                status = 'pending',
                upload_url = NULL,
                error = NULL,
                updated_at = excluded.updated_at
            """,
            (
                image.sha256,
                str(image.path),
                str(image.path),
                metadata.size_bytes,
                image.capture_time_utc.isoformat() if image.capture_time_utc else None,
                metadata.latitude,
                metadata.longitude,
                now,
                now,
            ),
        )
        self.connection.commit()

    def _set_status(
        self,
        sha256: str,
        status: str,
        *,
        upload_url: str | None = None,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE uploads
            SET status = ?, upload_url = COALESCE(?, upload_url), error = ?, updated_at = ?
            WHERE sha256 = ?
            """,
            (status, upload_url, error, _now(), sha256),
        )
        self.connection.commit()

    def mark_uploading(self, sha256: str, upload_url: str) -> None:
        self._set_status(sha256, "uploading", upload_url=upload_url)

    def mark_publishing(self, sha256: str, upload_url: str) -> None:
        self._set_status(sha256, "publishing", upload_url=upload_url)

    def mark_failed(self, sha256: str, error: str) -> None:
        self._set_status(sha256, "failed", error=error)

    def mark_uncertain(self, sha256: str, error: str) -> None:
        self._set_status(sha256, "uncertain", error=error)

    def mark_published(self, sha256: str, response: dict) -> None:
        photo_id_value = response.get("photoId") or {}
        photo_id = photo_id_value.get("id") if isinstance(photo_id_value, dict) else None
        self.connection.execute(
            """
            UPDATE uploads
            SET status = 'published', photo_id = ?, share_link = ?,
                maps_publish_status = ?, error = NULL, updated_at = ?
            WHERE sha256 = ?
            """,
            (
                photo_id,
                response.get("shareLink"),
                response.get("mapsPublishStatus"),
                _now(),
                sha256,
            ),
        )
        self.connection.commit()

    def mark_moved(self, sha256: str, destination: Path) -> None:
        self.connection.execute(
            """
            UPDATE uploads
            SET status = 'published', current_path = ?, file_moved = 1,
                error = NULL, updated_at = ?
            WHERE sha256 = ?
            """,
            (str(destination), _now(), sha256),
        )
        self.connection.commit()

    def mark_move_failed(self, sha256: str, error: str) -> None:
        self._set_status(sha256, "move_failed", error=error)

    def update_remote_status(self, sha256: str, response: dict) -> None:
        self.connection.execute(
            """
            UPDATE uploads
            SET maps_publish_status = ?, share_link = COALESCE(?, share_link), updated_at = ?
            WHERE sha256 = ?
            """,
            (
                response.get("mapsPublishStatus"),
                response.get("shareLink"),
                _now(),
                sha256,
            ),
        )
        self.connection.commit()

    def list_recent(self, limit: int = 100) -> list[UploadRecord]:
        rows = self.connection.execute(
            "SELECT * FROM uploads ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [record for row in rows if (record := self._record(row)) is not None]

    def published_records(self) -> list[UploadRecord]:
        rows = self.connection.execute(
            """
            SELECT * FROM uploads
            WHERE status IN ('published', 'move_failed') AND photo_id IS NOT NULL
            ORDER BY updated_at DESC
            """
        ).fetchall()
        return [record for row in rows if (record := self._record(row)) is not None]

    def move_failed_records(self) -> list[UploadRecord]:
        rows = self.connection.execute(
            "SELECT * FROM uploads WHERE status = 'move_failed' ORDER BY updated_at"
        ).fetchall()
        return [record for row in rows if (record := self._record(row)) is not None]
