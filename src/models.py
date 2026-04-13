import sqlite3
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    CLIPPING = "clipping"
    DONE = "done"
    FAILED = "failed"


class ClipApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ClipResult:
    start: float
    end: float
    reason: str
    score: float
    approval: str = ClipApprovalStatus.PENDING.value
    file_deleted: bool = False
    youtube_url: Optional[str] = None

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Job:
    id: str
    url: str
    status: JobStatus = JobStatus.PENDING
    error: Optional[str] = None
    clips: list[ClipResult] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    title: Optional[str] = None
    uploader: Optional[str] = None
    clips_deleted: bool = False


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    settings.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                url         TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                error       TEXT,
                clips       TEXT NOT NULL DEFAULT '[]',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                title       TEXT,
                uploader    TEXT
            )
        """)
        # Migrate existing DBs that predate these columns
        for col, typedef in [("title", "TEXT"), ("uploader", "TEXT"), ("clips_deleted", "INTEGER NOT NULL DEFAULT 0")]:
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # column already exists
        conn.commit()


def save_job(job: Job) -> None:
    """Insert or replace a job record."""
    job.updated_at = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs
                (id, url, status, error, clips, created_at, updated_at, title, uploader, clips_deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.url,
                job.status.value,
                job.error,
                json.dumps([asdict(c) for c in job.clips]),
                job.created_at,
                job.updated_at,
                job.title,
                job.uploader,
                int(job.clips_deleted),
            ),
        )
        conn.commit()


def load_job(job_id: str) -> Optional[Job]:
    """Load a job by ID, or return None if not found."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    clips = [ClipResult(**c) for c in json.loads(row["clips"])]
    return Job(
        id=row["id"],
        url=row["url"],
        status=JobStatus(row["status"]),
        error=row["error"],
        clips=clips,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        title=row["title"],
        uploader=row["uploader"],
        clips_deleted=bool(row["clips_deleted"]),
    )


def list_jobs(limit: int = 100) -> list[Job]:
    """Return the most recent jobs."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    result = []
    for row in rows:
        clips = [ClipResult(**c) for c in json.loads(row["clips"])]
        result.append(
            Job(
                id=row["id"],
                url=row["url"],
                status=JobStatus(row["status"]),
                error=row["error"],
                clips=clips,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                title=row["title"],
                uploader=row["uploader"],
                clips_deleted=bool(row["clips_deleted"]),
            )
        )
    return result
