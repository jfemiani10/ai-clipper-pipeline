# AI Clipper

A self-hosted pipeline that automatically finds and clips viral moments from YouTube and Twitch VODs, then uploads them as YouTube Shorts.

## What It Does

1. Monitors YouTube channels via RSS (Peter Attia, Huberman Lab, etc.)
2. Downloads new videos automatically using yt-dlp
3. Transcribes with Whisper (GPU-accelerated via faster-whisper)
4. Sends transcript to Claude AI to identify the best viral moments
5. Cuts clips into 1080x1920 vertical format (9:16) for YouTube Shorts
6. Presents clips in a web dashboard for review
7. Approved clips are automatically uploaded to YouTube with AI-generated titles and descriptions

## Stack

- **FastAPI** — REST API + web dashboard
- **Redis + rq** — async job queue
- **SQLite** — job persistence
- **faster-whisper** — GPU-accelerated transcription
- **Claude API (Anthropic)** — highlight detection + title/description generation
- **yt-dlp** — video downloading
- **ffmpeg** — clip cutting and 9:16 formatting
- **n8n** — workflow automation (channel monitoring, email notifications)
- **Docker Compose** — runs everything

## Requirements

- Ubuntu server with NVIDIA GPU (tested on GTX 1660 Super)
- nvidia-driver-535 + nvidia-container-toolkit
- Docker + Docker Compose
- Anthropic API key
- YouTube Data API v3 credentials (for upload)

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/jfemiani10/ai-clipper-pipeline.git
cd ai-clipper-pipeline

# 2. Copy and fill in environment variables
cp .env.example .env
# Add your ANTHROPIC_API_KEY and other settings

# 3. Start the stack
docker compose up -d
