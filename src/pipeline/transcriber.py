"""
transcriber.py — faster-whisper wrapper for speech-to-text transcription.

Pipeline:
  1. Extract audio from video (ffmpeg → 16kHz mono mp3)
  2. Transcribe with faster-whisper
  3. Write timestamped JSON to TRANSCRIPT_DIR/{job_id}/transcript.json

Output format:
  {"segments": [{"start": float, "end": float, "text": str}, ...]}
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
import logging

try:
    import ctranslate2
    _CUDA_AVAILABLE = ctranslate2.get_cuda_device_count() > 0
except Exception:
    _CUDA_AVAILABLE = False

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import settings

log = structlog.get_logger(__name__)


class TranscribeError(Exception):
    """Raised when transcription fails after all retries."""


class Transcriber:
    """Extracts audio from a video file and transcribes it with faster-whisper."""

    def run(self, video_path: Path, job_id: str) -> Path:
        """
        Transcribe the audio in *video_path*.

        Returns the Path to the transcript JSON file.
        Raises TranscribeError on permanent failure.
        """
        transcript_dir = settings.TRANSCRIPT_DIR / job_id
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcript_dir / "transcript.json"

        # Resume: if transcript already exists with content, skip
        if transcript_path.exists() and transcript_path.stat().st_size > 0:
            log.info("transcribe.skipped", job_id=job_id, path=str(transcript_path),
                     reason="transcript already exists")
            return transcript_path

        audio_path = transcript_dir / "audio.mp3"
        self._extract_audio(video_path, audio_path, job_id)
        self._transcribe_with_retry(audio_path, transcript_path, job_id)
        return transcript_path

    # ------------------------------------------------------------------
    # Audio extraction
    # ------------------------------------------------------------------

    def _extract_audio(self, video_path: Path, audio_path: Path, job_id: str) -> None:
        """Extract 16kHz mono mp3 from the video using ffmpeg."""
        if audio_path.exists() and audio_path.stat().st_size > 0:
            log.info("transcribe.audio_skipped", job_id=job_id,
                     reason="audio already extracted")
            return

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise TranscribeError("ffmpeg not found. Install with: sudo apt install ffmpeg")

        log.info("transcribe.extract_audio", job_id=job_id, src=str(video_path))
        cmd = [
            ffmpeg,
            "-y",                    # overwrite output without asking
            "-i", str(video_path),
            "-vn",                   # drop video stream
            "-acodec", "libmp3lame",
            "-ar", "16000",          # 16kHz sample rate (Whisper requirement)
            "-ac", "1",              # mono
            "-ab", "64k",            # 64kbps bitrate
            str(audio_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise TranscribeError(
                f"ffmpeg audio extraction failed:\n{result.stderr[-500:]}"
            )
        log.info("transcribe.audio_ready", job_id=job_id,
                 size_kb=round(audio_path.stat().st_size / 1024, 1))

    # ------------------------------------------------------------------
    # Transcription (with retry)
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        reraise=True,
    )
    def _transcribe_with_retry(self, audio_path: Path, transcript_path: Path,
                                job_id: str) -> None:
        from faster_whisper import WhisperModel

        device = "cuda" if _CUDA_AVAILABLE else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"

        log.info("transcribe.start", job_id=job_id,
                 model=settings.WHISPER_MODEL, device=device)

        model = WhisperModel(
            settings.WHISPER_MODEL,
            device=device,
            compute_type=compute_type,
        )

        segments_iter, info = model.transcribe(
            str(audio_path),
            language=settings.WHISPER_LANGUAGE if settings.WHISPER_LANGUAGE != "auto" else None,
            beam_size=5,
        )

        log.info("transcribe.language_detected", job_id=job_id,
                 language=info.language,
                 probability=round(info.language_probability, 2))

        segments = []
        for seg in segments_iter:
            segments.append({
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text.strip(),
            })

        transcript = {"segments": segments}
        transcript_path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False))

        log.info("transcribe.complete", job_id=job_id,
                 segments=len(segments),
                 path=str(transcript_path))
