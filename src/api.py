"""
api.py — FastAPI webhook endpoint.

Endpoints:
  POST /jobs           Submit a video URL for processing → returns {job_id}
  GET  /jobs/{job_id}  Poll job status and results
  GET  /jobs           List recent jobs
  GET  /health         Liveness check
"""

import re
import uuid
import sys
import threading
from pathlib import Path
from typing import Optional
from dataclasses import asdict

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings
from src.models import Job, JobStatus, ClipApprovalStatus, init_db, save_job, load_job, list_jobs


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI Clipper", version="1.0.0", lifespan=lifespan)

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

def _status_emoji(status: str) -> str:
    return {"pending": "⏳", "downloading": "⬇️", "transcribing": "📝",
            "analyzing": "🤖", "clipping": "✂️", "done": "✅", "failed": "❌"}.get(status, "❓")

def _approval_emoji(approval: str) -> str:
    return {"approved": "✅", "rejected": "❌", "pending": "⏳"}.get(approval, "")

templates.env.globals["status_emoji"] = _status_emoji
templates.env.globals["approval_emoji"] = _approval_emoji


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SubmitRequest(BaseModel):
    url: str


class ClipResponse(BaseModel):
    start: float
    end: float
    reason: str
    score: float
    approval: str = "pending"
    file_deleted: bool = False
    youtube_url: Optional[str] = None


class JobResponse(BaseModel):
    job_id: str
    url: str
    status: str
    error: Optional[str] = None
    clips: list[ClipResponse] = []
    created_at: str
    updated_at: str
    title: Optional[str] = None
    uploader: Optional[str] = None
    clips_deleted: bool = False


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

_YOUTUBE_PATTERN = re.compile(
    r"^https?://(www\.)?(youtube\.com/watch|youtu\.be/|youtube\.com/shorts/)"
)
_TWITCH_PATTERN = re.compile(
    r"^https?://(www\.)?twitch\.tv/"
)


def _is_supported_url(url: str) -> bool:
    return bool(_YOUTUBE_PATTERN.match(url) or _TWITCH_PATTERN.match(url))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Dashboard routes
# ---------------------------------------------------------------------------

@app.get("/")
def dashboard_index(request: Request, view: str = "active"):
    return templates.TemplateResponse(request, "index.html", {"view": view})


@app.get("/jobs/{job_id}/view")
def dashboard_job(request: Request, job_id: str):
    job = load_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return templates.TemplateResponse(request, "job.html", {"job": _job_to_template(job)})


@app.get("/clips/{job_id}/{filename}")
def serve_clip(job_id: str, filename: str):
    clip_path = settings.CLIPS_DIR / job_id / filename
    if not clip_path.exists() or not clip_path.is_file():
        raise HTTPException(status_code=404, detail="Clip not found.")
    return FileResponse(str(clip_path), media_type="video/mp4")


@app.post("/jobs", status_code=202)
def submit_job(body: SubmitRequest):
    """Submit a YouTube or Twitch URL for processing."""
    if not _is_supported_url(body.url):
        raise HTTPException(
            status_code=400,
            detail="URL must be a YouTube or Twitch link.",
        )

    job_id = str(uuid.uuid4())
    job = Job(id=job_id, url=body.url, status=JobStatus.PENDING)
    save_job(job)

    # Enqueue with rq — import here so the API starts even if Redis is down
    try:
        from src.job_queue import get_queue
        from src.worker import process_video
        q = get_queue()
        q.enqueue(process_video, job_id, body.url, job_timeout=3600)
    except Exception as exc:
        # If Redis is unavailable, still return the job_id — worker can be
        # triggered manually. Log the failure but don't crash the API.
        import structlog
        structlog.get_logger(__name__).error(
            "api.enqueue_failed", job_id=job_id, error=str(exc)
        )

    return {"job_id": job_id}


@app.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str):
    """Return the current status and results for a job."""
    job = load_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_to_response(job)


@app.get("/jobs", response_model=list[JobResponse])
def list_all_jobs(limit: int = 50):
    """Return the most recent jobs (default 50)."""
    jobs = list_jobs(limit=min(limit, 200))
    return [_job_to_response(j) for j in jobs]


# ---------------------------------------------------------------------------
# Clip approval endpoints
# ---------------------------------------------------------------------------

@app.post("/jobs/{job_id}/clips/{clip_index}/approve")
def approve_clip(job_id: str, clip_index: int):
    return _set_clip_approval(job_id, clip_index, ClipApprovalStatus.APPROVED)


@app.post("/jobs/{job_id}/clips/{clip_index}/reject")
def reject_clip(job_id: str, clip_index: int):
    return _set_clip_approval(job_id, clip_index, ClipApprovalStatus.REJECTED)


@app.delete("/jobs/{job_id}/clips")
def delete_job_clips(job_id: str):
    """Delete all clip files for a job (called by n8n cleanup workflow)."""
    job = load_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    clip_dir = settings.CLIPS_DIR / job_id
    if clip_dir.exists():
        for f in clip_dir.glob("*.mp4"):
            f.unlink(missing_ok=True)
    for clip in job.clips:
        clip.file_deleted = True
    job.clips_deleted = True
    save_job(job)
    return {"job_id": job_id, "clips_deleted": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_clip_approval(job_id: str, clip_index: int, status: ClipApprovalStatus):
    job = load_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if clip_index < 0 or clip_index >= len(job.clips):
        raise HTTPException(status_code=404, detail="Clip index out of range.")

    clip = job.clips[clip_index]
    clip.approval = status.value

    if status == ClipApprovalStatus.REJECTED:
        clip_path = settings.CLIPS_DIR / job_id / f"clip_{clip_index:02d}.mp4"
        clip_path.unlink(missing_ok=True)
        clip.file_deleted = True

    save_job(job)

    if status == ClipApprovalStatus.APPROVED and settings.YOUTUBE_ENABLED:
        clip_path = settings.CLIPS_DIR / job_id / f"clip_{clip_index:02d}.mp4"
        print(f"[approve] YOUTUBE_ENABLED=True, clip_path={clip_path}, exists={clip_path.exists()}", flush=True)
        threading.Thread(
            target=_upload_async, args=(job_id, clip_index), daemon=True
        ).start()
        print(f"[approve] thread started", flush=True)

    return {"clip_index": clip_index, "approval": status.value, "file_deleted": clip.file_deleted}


def _upload_async(job_id: str, clip_index: int) -> None:
    """Background thread: upload clip to YouTube and save the URL."""
    import traceback
    print(f"[upload] thread started job_id={job_id} clip_index={clip_index}", flush=True)
    try:
        from src.pipeline.uploader import YouTubeUploader
        job = load_job(job_id)
        if job is None or clip_index >= len(job.clips):
            print(f"[upload] job or clip not found, aborting", flush=True)
            return
        clip = job.clips[clip_index]
        clip_path = settings.CLIPS_DIR / job_id / f"clip_{clip_index:02d}.mp4"
        if not clip_path.exists():
            print(f"[upload] clip file not found: {clip_path}", flush=True)
            return
        youtube_url = YouTubeUploader().upload(clip_path, clip, job)
        job = load_job(job_id)
        if job and clip_index < len(job.clips):
            job.clips[clip_index].youtube_url = youtube_url
            save_job(job)
        print(f"[upload] done: {youtube_url}", flush=True)
    except Exception as exc:
        print(f"[upload] ERROR: {exc}\n{traceback.format_exc()}", flush=True)


def _job_to_response(job: Job) -> JobResponse:
    return JobResponse(
        job_id=job.id,
        url=job.url,
        status=job.status.value,
        error=job.error,
        clips=[ClipResponse(**asdict(c)) for c in job.clips],
        created_at=job.created_at,
        updated_at=job.updated_at,
        title=job.title,
        uploader=job.uploader,
        clips_deleted=job.clips_deleted,
    )


class _TemplateClip:
    """Simple object for Jinja2 template access."""
    def __init__(self, clip, index):
        self.start = clip.start
        self.end = clip.end
        self.reason = clip.reason
        self.score = clip.score
        self.approval = clip.approval
        self.file_deleted = clip.file_deleted
        self.youtube_url = clip.youtube_url


class _TemplateJob:
    def __init__(self, job: Job):
        self.job_id = job.id
        self.url = job.url
        self.status = job.status.value
        self.error = job.error
        self.clips = [_TemplateClip(c, i) for i, c in enumerate(job.clips)]
        self.created_at = job.created_at
        self.title = job.title
        self.uploader = job.uploader
        self.updated_at = job.updated_at
        self.clips_deleted = job.clips_deleted


def _job_to_template(job: Job) -> _TemplateJob:
    return _TemplateJob(job)
