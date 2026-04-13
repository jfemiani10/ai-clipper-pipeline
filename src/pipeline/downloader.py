"""
downloader.py — yt-dlp wrapper for downloading YouTube and Twitch VODs.

Output: DOWNLOAD_DIR/{job_id}/video.mp4
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

log = structlog.get_logger(__name__)


def _find_binary(name: str) -> str:
    """Return the path to *name*, preferring the current venv's bin directory."""
    # Check the same bin/ as the running Python interpreter first
    venv_bin = Path(sys.executable).parent / name
    if venv_bin.exists():
        return str(venv_bin)
    system = shutil.which(name)
    if system:
        return system
    raise FileNotFoundError(
        f"'{name}' not found. Install it with: pip install {name}"
    )


def _find_ffmpeg() -> str | None:
    """Return the path to ffmpeg if available, or None."""
    return shutil.which("ffmpeg")


class DownloadError(Exception):
    """Raised when a download fails after all retries."""


class Downloader:
    """Downloads a video from a YouTube or Twitch URL using yt-dlp."""

    # yt-dlp format string: prefer mp4 video + m4a audio; fall back to best mp4
    FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

    def fetch_metadata(self, url: str) -> dict:
        """Return {title, uploader} for *url* without downloading the video."""
        import json as _json
        try:
            result = subprocess.run(
                [_find_binary("yt-dlp"), "--dump-json", "--skip-download", "--no-playlist", url],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                data = _json.loads(result.stdout)
                return {
                    "title": data.get("title"),
                    "uploader": data.get("uploader") or data.get("channel"),
                }
        except Exception:
            pass
        return {"title": None, "uploader": None}

    def run(self, url: str, job_id: str) -> Path:
        """
        Download the video at *url* into DOWNLOAD_DIR/{job_id}/video.mp4.

        Returns the Path to the downloaded file.
        Raises DownloadError on permanent failure (after retries).
        """
        out_dir = settings.DOWNLOAD_DIR / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "video.mp4"

        # Resume: if the file already exists and has content, skip download
        if out_path.exists() and out_path.stat().st_size > 0:
            log.info("download.skipped", job_id=job_id, path=str(out_path),
                     reason="file already exists")
            return out_path

        log.info("download.start", job_id=job_id, url=url)
        self._download_with_retry(url, out_path, job_id)
        log.info("download.complete", job_id=job_id, path=str(out_path),
                 size_mb=round(out_path.stat().st_size / 1_048_576, 1))
        return out_path

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        reraise=True,
    )
    def _download_with_retry(self, url: str, out_path: Path, job_id: str) -> None:
        cmd = [
            _find_binary("yt-dlp"),
            "--format", self.FORMAT,
            "--output", str(out_path),
            "--no-playlist",
            "--no-warnings",
            "--progress",
            url,
        ]

        # Pass ffmpeg location if available (needed for merging video+audio streams)
        ffmpeg_path = _find_ffmpeg()
        if ffmpeg_path:
            cmd += ["--ffmpeg-location", str(Path(ffmpeg_path).parent)]
        else:
            log.warning("download.ffmpeg_missing",
                        msg="ffmpeg not found — stream merging may fail. Install with: sudo apt install ffmpeg")

        log.debug("download.cmd", job_id=job_id, cmd=" ".join(cmd))

        result = subprocess.run(
            cmd,
            capture_output=False,  # let yt-dlp print progress to stdout
            text=True,
        )

        if result.returncode != 0:
            raise DownloadError(
                f"yt-dlp exited with code {result.returncode} for URL: {url}"
            )

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise DownloadError(
                f"yt-dlp reported success but output file is missing or empty: {out_path}"
            )
