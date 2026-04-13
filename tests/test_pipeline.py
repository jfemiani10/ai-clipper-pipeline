"""
test_pipeline.py — unit and integration tests for the AI Clipper pipeline.

Run with: .venv/bin/pytest tests/ -v
"""

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Unit tests — Analyzer (no real API calls)
# ---------------------------------------------------------------------------

class TestAnalyzerParsing:
    def setup_method(self):
        from src.pipeline.analyzer import Analyzer
        self.analyzer = Analyzer()

    def test_parse_clean_json(self):
        raw = '{"clips": [{"start": 10.0, "end": 45.0, "reason": "Funny", "score": 0.9}]}'
        clips = self.analyzer._parse_response(raw, "test")
        assert len(clips) == 1
        assert clips[0]["start"] == 10.0
        assert clips[0]["score"] == 0.9

    def test_parse_strips_markdown_fences(self):
        raw = "```json\n{\"clips\": [{\"start\": 5.0, \"end\": 30.0, \"reason\": \"Shocking\", \"score\": 0.85}]}\n```"
        clips = self.analyzer._parse_response(raw, "test")
        assert len(clips) == 1
        assert clips[0]["end"] == 30.0

    def test_parse_invalid_clip_end_before_start(self):
        raw = '{"clips": [{"start": 50.0, "end": 10.0, "reason": "Bad", "score": 0.9}]}'
        clips = self.analyzer._parse_response(raw, "test")
        assert clips == []

    def test_parse_missing_required_field(self):
        raw = '{"clips": [{"start": 10.0, "reason": "Missing end", "score": 0.9}]}'
        clips = self.analyzer._parse_response(raw, "test")
        assert clips == []

    def test_filter_by_min_score(self):
        from src.pipeline.analyzer import Analyzer
        raw_clips = [
            {"start": 0.0, "end": 30.0, "reason": "High", "score": 0.9},
            {"start": 60.0, "end": 90.0, "reason": "Low", "score": 0.3},
        ]
        results = Analyzer()._filter_clips(raw_clips, "test")
        assert len(results) == 1
        assert results[0].score == 0.9

    def test_filter_by_max_duration(self):
        from src.pipeline.analyzer import Analyzer
        raw_clips = [
            {"start": 0.0, "end": 30.0, "reason": "Short", "score": 0.9},
            {"start": 100.0, "end": 400.0, "reason": "Too long", "score": 0.95},
        ]
        results = Analyzer()._filter_clips(raw_clips, "test")
        assert len(results) == 1
        assert results[0].reason == "Short"

    def test_filter_sorted_by_score_descending(self):
        from src.pipeline.analyzer import Analyzer
        raw_clips = [
            {"start": 0.0, "end": 30.0, "reason": "B", "score": 0.75},
            {"start": 60.0, "end": 90.0, "reason": "A", "score": 0.95},
        ]
        results = Analyzer()._filter_clips(raw_clips, "test")
        assert results[0].score == 0.95
        assert results[1].score == 0.75

    def test_format_transcript(self):
        from src.pipeline.analyzer import Analyzer
        segments = [
            {"start": 0.0, "end": 5.5, "text": "Hello world"},
            {"start": 5.5, "end": 10.0, "text": "How are you"},
        ]
        formatted = Analyzer()._format_transcript(segments)
        assert "[0.0 - 5.5] Hello world" in formatted
        assert "[5.5 - 10.0] How are you" in formatted


# ---------------------------------------------------------------------------
# Unit tests — Models / Database
# ---------------------------------------------------------------------------

class TestModels:
    def test_job_create_and_load(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")

        from src.models import init_db, save_job, load_job, Job, JobStatus, ClipResult
        init_db()

        job_id = str(uuid.uuid4())
        job = Job(id=job_id, url="https://youtube.com/watch?v=test")
        save_job(job)

        loaded = load_job(job_id)
        assert loaded is not None
        assert loaded.url == "https://youtube.com/watch?v=test"
        assert loaded.status == JobStatus.PENDING

    def test_job_status_update(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")

        from src.models import init_db, save_job, load_job, Job, JobStatus, ClipResult
        init_db()

        job_id = str(uuid.uuid4())
        job = Job(id=job_id, url="https://youtube.com/watch?v=test2")
        save_job(job)

        job.status = JobStatus.DONE
        job.clips = [ClipResult(start=10.0, end=40.0, reason="Great moment", score=0.88)]
        save_job(job)

        loaded = load_job(job_id)
        assert loaded.status == JobStatus.DONE
        assert len(loaded.clips) == 1
        assert loaded.clips[0].score == 0.88

    def test_load_nonexistent_job(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")

        from src.models import init_db, load_job
        init_db()
        assert load_job("does-not-exist") is None


# ---------------------------------------------------------------------------
# Unit tests — API
# ---------------------------------------------------------------------------

class TestAPI:
    def test_url_validation_youtube(self):
        from src.api import _is_supported_url
        assert _is_supported_url("https://www.youtube.com/watch?v=abc")
        assert _is_supported_url("https://youtu.be/abc")
        assert _is_supported_url("https://youtube.com/shorts/abc")

    def test_url_validation_twitch(self):
        from src.api import _is_supported_url
        assert _is_supported_url("https://www.twitch.tv/channel/videos/123")

    def test_url_validation_rejects_other(self):
        from src.api import _is_supported_url
        assert not _is_supported_url("https://vimeo.com/123")
        assert not _is_supported_url("https://example.com")
        assert not _is_supported_url("not-a-url")

    def test_health_endpoint(self):
        from fastapi.testclient import TestClient
        from src.api import app
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_submit_invalid_url_returns_400(self):
        from fastapi.testclient import TestClient
        from src.api import app
        client = TestClient(app)
        resp = client.post("/jobs", json={"url": "https://vimeo.com/123"})
        assert resp.status_code == 400

    def test_submit_valid_url_returns_202(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")

        from src.models import init_db
        init_db()

        # Patch get_queue where it is imported inside the endpoint
        with patch("src.job_queue.get_queue") as mock_get_q:
            mock_get_q.return_value.enqueue = MagicMock()

            from fastapi.testclient import TestClient
            from src.api import app
            client = TestClient(app)
            resp = client.post("/jobs", json={"url": "https://www.youtube.com/watch?v=abc"})
            assert resp.status_code == 202
            assert "job_id" in resp.json()

    def test_get_nonexistent_job_returns_404(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "test.db")

        # Must init the db in the tmp location before the TestClient startup runs
        from src.models import init_db
        init_db()

        from fastapi.testclient import TestClient
        from src.api import app
        client = TestClient(app)
        resp = client.get("/jobs/does-not-exist")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Integration test — Downloader (requires network)
# ---------------------------------------------------------------------------

class TestDownloaderIntegration:
    @pytest.mark.integration
    def test_download_short_video(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "DOWNLOAD_DIR", tmp_path)

        from src.pipeline.downloader import Downloader
        job_id = "integ-download"
        # "Me at the zoo" — first YouTube video, 19 seconds
        path = Downloader().run("https://www.youtube.com/watch?v=jNQXAC9IVRw", job_id)
        assert path.exists()
        assert path.stat().st_size > 10_000  # at least 10KB
        assert path.suffix == ".mp4"


# ---------------------------------------------------------------------------
# Integration test — Full pipeline (requires network + API key)
# ---------------------------------------------------------------------------

class TestFullPipelineIntegration:
    @pytest.mark.integration
    def test_pipeline_produces_transcript(self, tmp_path, monkeypatch):
        """Downloads a short video and verifies transcription output."""
        from config import settings
        monkeypatch.setattr(settings, "DOWNLOAD_DIR", tmp_path / "downloads")
        monkeypatch.setattr(settings, "TRANSCRIPT_DIR", tmp_path / "transcripts")

        from src.pipeline.downloader import Downloader
        from src.pipeline.transcriber import Transcriber

        job_id = "integ-full"
        video = Downloader().run("https://www.youtube.com/watch?v=jNQXAC9IVRw", job_id)
        transcript = Transcriber().run(video, job_id)

        assert transcript.exists()
        data = json.loads(transcript.read_text())
        assert "segments" in data
        assert len(data["segments"]) > 0
        seg = data["segments"][0]
        assert "start" in seg and "end" in seg and "text" in seg
