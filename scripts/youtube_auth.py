"""
youtube_auth.py — One-time OAuth setup for YouTube uploads.

Run this once on the server (outside Docker):
  python scripts/youtube_auth.py

It will open a browser (or give you a URL to visit) to authorize your
Google account. The resulting token is saved to config/youtube_token.json
and will be reused (with automatic refresh) for all future uploads.

Prerequisites:
  1. Create a Google Cloud project at https://console.cloud.google.com
  2. Enable the YouTube Data API v3
  3. Create OAuth 2.0 credentials (type: Desktop app)
  4. Download the JSON and save it as config/youtube_client_secrets.json
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    if not settings.YOUTUBE_CLIENT_SECRETS.exists():
        print(f"ERROR: Client secrets not found at {settings.YOUTUBE_CLIENT_SECRETS}")
        print("Download OAuth credentials from Google Cloud Console and save there.")
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.YOUTUBE_CLIENT_SECRETS), SCOPES
    )
    creds = flow.run_local_server(port=8765, open_browser=False)

    settings.YOUTUBE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    settings.YOUTUBE_TOKEN_PATH.write_text(creds.to_json())
    print(f"Token saved to {settings.YOUTUBE_TOKEN_PATH}")
    print("YouTube uploads are now enabled.")


if __name__ == "__main__":
    main()
