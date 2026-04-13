"""
exporter.py — saves clip metadata to disk for downstream use.

Currently writes a JSON summary to EXPORT_DIR/{job_id}/clips.json.
Extend this later to upload to S3, trigger webhooks, etc.
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import settings
from src.models import ClipResult

log = structlog.get_logger(__name__)


class Exporter:
    def run(self, job_id: str, clip_paths: list[Path],
            clip_results: list[ClipResult]) -> Path:
        """
        Write a JSON summary of all clips to EXPORT_DIR/{job_id}/clips.json.
        Returns the path to the summary file.
        """
        out_dir = settings.EXPORT_DIR / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        summary_path = out_dir / "clips.json"

        summary = []
        for path, result in zip(clip_paths, clip_results):
            summary.append({
                **asdict(result),
                "filename": path.name,
                "path": str(path),
            })

        summary_path.write_text(json.dumps(summary, indent=2))
        log.info("exporter.complete", job_id=job_id,
                 clips=len(summary), path=str(summary_path))
        return summary_path
