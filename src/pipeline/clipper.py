"""
clipper.py — ffmpeg wrapper for cutting video clips.

For each ClipResult:
  - Adds CLIP_BUFFER_SECONDS of padding before/after the timestamp
  - Uses -ss before -i for fast keyframe seeking (no full decode)
  - Uses -c copy to avoid re-encoding (preserves quality, very fast)
  - Outputs: CLIPS_DIR/{job_id}/clip_{n:02d}.mp4
"""

import shutil
import subprocess
import sys
from pathlib import Path

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
import logging

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import settings
from src.models import ClipResult

log = structlog.get_logger(__name__)


class ClipError(Exception):
    """Raised when a clip cut fails after all retries."""


class Clipper:
    """Cuts video clips from a source file using ffmpeg."""

    def run(self, video_path: Path, clips: list[ClipResult], job_id: str) -> list[Path]:
        """
        Cut each ClipResult from *video_path* into separate MP4 files.

        Returns a list of Paths to the output clip files.
        Raises ClipError if any clip fails after retries.
        """
        if not clips:
            log.warning("clipper.no_clips", job_id=job_id)
            return []

        out_dir = settings.CLIPS_DIR / job_id
        out_dir.mkdir(parents=True, exist_ok=True)

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ClipError("ffmpeg not found. Install with: sudo apt install ffmpeg")

        output_paths = []
        for i, clip in enumerate(clips):
            out_path = out_dir / f"clip_{i:02d}.mp4"

            # Resume: skip if clip already exists with content
            if out_path.exists() and out_path.stat().st_size > 0:
                log.info("clipper.skipped", job_id=job_id, clip=i, path=str(out_path))
                output_paths.append(out_path)
                continue

            self._cut_clip_with_retry(ffmpeg, video_path, clip, out_path, i, job_id)
            output_paths.append(out_path)

        log.info("clipper.complete", job_id=job_id, clips_cut=len(output_paths))
        return output_paths

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        reraise=True,
    )
    def _cut_clip_with_retry(self, ffmpeg: str, video_path: Path, clip: ClipResult,
                              out_path: Path, index: int, job_id: str) -> None:
        # Apply buffer, clamped so start never goes below 0
        start = max(0.0, clip.start - settings.CLIP_BUFFER_SECONDS)
        end = clip.end + settings.CLIP_BUFFER_SECONDS
        duration = end - start

        log.info("clipper.cutting", job_id=job_id, clip=index,
                 start=round(start, 1), end=round(end, 1),
                 duration=round(duration, 1), score=clip.score)

        cmd = [
            ffmpeg,
            "-y",                           # overwrite without asking
            "-ss", str(start),              # seek BEFORE -i for fast seeking
            "-i", str(video_path),
            "-t", str(duration),            # duration of the clip
            # Scale to 1080 wide (height auto, must be even for libx264),
            # then pad to 1080x1920 (9:16 portrait) with black bars top/bottom
            "-vf", "scale=1080:-2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black",
            "-c:v", "libx264",
            "-crf", "23",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(out_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise ClipError(
                f"ffmpeg failed for clip {index}:\n{result.stderr[-500:]}"
            )

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise ClipError(
                f"ffmpeg reported success but clip {index} output is missing or empty: {out_path}"
            )

        log.info("clipper.clip_done", job_id=job_id, clip=index,
                 path=str(out_path),
                 size_mb=round(out_path.stat().st_size / 1_048_576, 2))
