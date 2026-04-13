"""
worker.py — pipeline orchestrator.

Can be run in two modes:
  1. Direct:  python src/worker.py <job_id> <url>   (for testing)
  2. rq worker: rq worker  (picks up jobs enqueued by api.py)

Each stage checks for existing output files before running, so the pipeline
is safe to resume from the last completed stage after a crash or restart.
"""

import sys
import uuid
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings
from src.models import (
    Job, JobStatus, ClipResult,
    init_db, save_job, load_job,
)
from src.pipeline.downloader import Downloader, DownloadError
from src.pipeline.transcriber import Transcriber, TranscribeError
from src.pipeline.analyzer import Analyzer, AnalyzeError
from src.pipeline.clipper import Clipper, ClipError
from src.pipeline.exporter import Exporter

log = structlog.get_logger(__name__)

# Pipeline errors we know how to categorise
_KNOWN_ERRORS = (DownloadError, TranscribeError, AnalyzeError, ClipError)


def process_video(job_id: str, url: str) -> None:
    """
    Main pipeline function — called by rq workers and by direct invocation.

    Resumes from the last completed stage by checking for existing output files.
    Updates the job record in SQLite at each stage transition.
    """
    init_db()

    # Load or create job record
    job = load_job(job_id)
    if job is None:
        job = Job(id=job_id, url=url)
        save_job(job)

    bound = log.bind(job_id=job_id, url=url)
    bound.info("pipeline.start", status=job.status.value)

    try:
        # ----------------------------------------------------------------
        # Stage 1: Download
        # ----------------------------------------------------------------
        _set_status(job, JobStatus.DOWNLOADING)
        downloader = Downloader()
        meta = downloader.fetch_metadata(url)
        job.title = meta["title"]
        job.uploader = meta["uploader"]
        save_job(job)
        video_path = downloader.run(url, job_id)

        # ----------------------------------------------------------------
        # Stage 2: Transcribe
        # ----------------------------------------------------------------
        _set_status(job, JobStatus.TRANSCRIBING)
        transcript_path = Transcriber().run(video_path, job_id)

        # ----------------------------------------------------------------
        # Stage 3: Analyze
        # ----------------------------------------------------------------
        _set_status(job, JobStatus.ANALYZING)
        clip_results: list[ClipResult] = Analyzer().run(transcript_path, job_id)

        if not clip_results:
            bound.warning("pipeline.no_clips",
                          msg="Claude found no clips above the score threshold")
            _set_status(job, JobStatus.DONE)
            return

        # ----------------------------------------------------------------
        # Stage 4: Cut clips
        # ----------------------------------------------------------------
        _set_status(job, JobStatus.CLIPPING)
        clip_paths = Clipper().run(video_path, clip_results, job_id)

        # ----------------------------------------------------------------
        # Stage 5: Export
        # ----------------------------------------------------------------
        Exporter().run(job_id, clip_paths, clip_results)

        # ----------------------------------------------------------------
        # Done
        # ----------------------------------------------------------------
        job.clips = clip_results
        _set_status(job, JobStatus.DONE)
        bound.info("pipeline.done", clips=len(clip_results))

    except _KNOWN_ERRORS as exc:
        bound.error("pipeline.failed", error=str(exc))
        job.error = str(exc)
        _set_status(job, JobStatus.FAILED)
        raise

    except Exception as exc:
        bound.error("pipeline.unexpected_error", error=str(exc))
        job.error = f"Unexpected error: {exc}"
        _set_status(job, JobStatus.FAILED)
        raise


def _set_status(job: Job, status: JobStatus) -> None:
    job.status = status
    save_job(job)
    log.info("pipeline.stage", job_id=job.id, status=status.value)


# ---------------------------------------------------------------------------
# Direct invocation for testing: python src/worker.py [job_id] <url>
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import logging
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.LOG_LEVEL, logging.INFO)
    ))

    args = sys.argv[1:]
    if len(args) == 1:
        _job_id = str(uuid.uuid4())
        _url = args[0]
    elif len(args) == 2:
        _job_id, _url = args
    else:
        print("Usage: python src/worker.py <url>")
        print("       python src/worker.py <job_id> <url>")
        sys.exit(1)

    process_video(_job_id, _url)
