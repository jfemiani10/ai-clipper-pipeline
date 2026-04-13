"""
analyzer.py — Claude API integration for viral highlight detection.

Reads a transcript JSON, sends it to Claude, and returns a filtered list
of ClipResult objects ranked by viral potential score.

Claude response format:
  {"clips": [{"start": float, "end": float, "reason": str, "score": float}]}
"""

import json
import re
import sys
from pathlib import Path

import anthropic
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
import logging

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import settings
from src.models import ClipResult

log = structlog.get_logger(__name__)


class AnalyzeError(Exception):
    """Raised when Claude analysis fails after all retries."""


class Analyzer:
    """Uses Claude to identify viral moments from a transcript."""

    def run(self, transcript_path: Path, job_id: str) -> list[ClipResult]:
        """
        Analyze the transcript at *transcript_path* and return scored ClipResults.

        Filters by MIN_CLIP_SCORE and MAX_CLIP_DURATION.
        Raises AnalyzeError on permanent failure.
        """
        log.info("analyze.start", job_id=job_id, transcript=str(transcript_path))

        transcript_data = json.loads(transcript_path.read_text())
        segments = transcript_data.get("segments", [])
        if not segments:
            raise AnalyzeError("Transcript has no segments — nothing to analyze.")

        formatted = self._format_transcript(segments)
        raw_clips = self._call_claude_with_retry(formatted, job_id)
        clips = self._filter_clips(raw_clips, job_id)

        log.info("analyze.complete", job_id=job_id,
                 total_from_claude=len(raw_clips), after_filter=len(clips))
        return clips

    # ------------------------------------------------------------------
    # Transcript formatting
    # ------------------------------------------------------------------

    def _format_transcript(self, segments: list[dict]) -> str:
        """Convert segment list to the '[start - end] text' format Claude expects."""
        lines = []
        for seg in segments:
            start = seg["start"]
            end = seg["end"]
            text = seg["text"].strip()
            if text:
                lines.append(f"[{start:.1f} - {end:.1f}] {text}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Claude API call (with retry)
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        reraise=True,
    )
    def _call_claude_with_retry(self, transcript_text: str, job_id: str) -> list[dict]:
        system_prompt = (settings.PROMPT_DIR / "highlight_detection.txt").read_text()

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        log.info("analyze.claude_request", job_id=job_id,
                 model=settings.CLAUDE_MODEL,
                 transcript_chars=len(transcript_text))

        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=settings.CLAUDE_MAX_TOKENS,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"Here is the transcript:\n\n{transcript_text}",
                }
            ],
        )

        raw_text = message.content[0].text.strip()
        log.debug("analyze.claude_response", job_id=job_id, response=raw_text[:300])

        return self._parse_response(raw_text, job_id)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw_text: str, job_id: str) -> list[dict]:
        """Extract and validate the JSON clips array from Claude's response."""
        # Strip markdown code fences if Claude added them despite instructions
        text = re.sub(r"```(?:json)?\s*", "", raw_text).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            # Try to extract the first {...} block as a fallback
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    raise AnalyzeError(
                        f"Claude returned invalid JSON: {raw_text[:200]}"
                    ) from exc
            else:
                raise AnalyzeError(
                    f"Claude returned invalid JSON: {raw_text[:200]}"
                ) from exc

        clips = data.get("clips", [])
        if not isinstance(clips, list):
            raise AnalyzeError(f"Expected 'clips' to be a list, got: {type(clips)}")

        # Validate required fields
        valid = []
        for i, clip in enumerate(clips):
            try:
                start = float(clip["start"])
                end = float(clip["end"])
                reason = str(clip.get("reason", ""))
                score = float(clip.get("score", 0.0))
                if end <= start:
                    log.warning("analyze.invalid_clip", job_id=job_id, index=i,
                                reason="end <= start", clip=clip)
                    continue
                valid.append({"start": start, "end": end, "reason": reason, "score": score})
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("analyze.skip_clip", job_id=job_id, index=i, error=str(exc))

        return valid

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _filter_clips(self, raw_clips: list[dict], job_id: str) -> list[ClipResult]:
        """Apply MIN_CLIP_SCORE and MAX_CLIP_DURATION filters, return sorted list."""
        results = []
        for clip in raw_clips:
            duration = clip["end"] - clip["start"]
            if clip["score"] < settings.MIN_CLIP_SCORE:
                log.debug("analyze.filtered_score", job_id=job_id,
                          score=clip["score"], threshold=settings.MIN_CLIP_SCORE)
                continue
            if duration > settings.MAX_CLIP_DURATION:
                log.debug("analyze.filtered_duration", job_id=job_id,
                          duration=round(duration, 1), max=settings.MAX_CLIP_DURATION)
                continue
            results.append(ClipResult(
                start=clip["start"],
                end=clip["end"],
                reason=clip["reason"],
                score=clip["score"],
            ))

        # Sort by score descending
        results.sort(key=lambda c: c.score, reverse=True)
        return results
