"""
uploader.py — YouTube upload via Google Data API v3.

Requires one-time OAuth setup:
  python scripts/youtube_auth.py
This generates config/youtube_token.json which is loaded on every upload.

Upload is triggered automatically when a clip is approved in the dashboard.
"""

import json
import sys
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import settings

log = structlog.get_logger(__name__)


class UploadError(Exception):
    pass


class YouTubeUploader:

    def upload(self, clip_path: Path, clip, job) -> str:
        """
        Upload *clip_path* to YouTube.
        Returns the YouTube watch URL (https://youtu.be/{video_id}).
        """
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        if not settings.YOUTUBE_TOKEN_PATH.exists():
            raise UploadError(
                "YouTube token not found. Run: python scripts/youtube_auth.py"
            )

        creds = Credentials.from_authorized_user_file(
            str(settings.YOUTUBE_TOKEN_PATH),
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            settings.YOUTUBE_TOKEN_PATH.write_text(creds.to_json())

        title, description = self._generate_metadata(clip, job)

        youtube = build("youtube", "v3", credentials=creds)
        media = MediaFileUpload(str(clip_path), mimetype="video/mp4", resumable=True)

        log.info("upload.start", job_id=job.id, clip_path=str(clip_path), title=title)

        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": ["clips", "highlights", job.uploader or ""],
                    "categoryId": "22",  # People & Blogs
                },
                "status": {
                    "privacyStatus": "public",
                },
            },
            media_body=media,
        )

        response = None
        while response is None:
            _, response = request.next_chunk()

        video_id = response["id"]
        url = f"https://youtu.be/{video_id}"
        log.info("upload.complete", job_id=job.id, url=url)
        return url

    def _generate_metadata(self, clip, job) -> tuple[str, str]:
        """Use Claude to generate a viral title and description for the clip."""
        import anthropic

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        prompt = f"""Generate a YouTube Short title and description for this clip.

Video: {job.title or job.url}
Creator: {job.uploader or "Unknown"}
Original URL: {job.url}
Clip summary: {clip.reason}
Clip score: {clip.score:.2f}

Return JSON only:
{{
  "title": "short viral title under 70 chars",
  "description": "2-3 sentence description ending with:\\n\\nOriginal video: {job.url}\\nCredit: {job.uploader or 'Original creator'}"
}}"""

        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )

        text = message.content[0].text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]

        data = json.loads(text)
        return data["title"], data["description"]
