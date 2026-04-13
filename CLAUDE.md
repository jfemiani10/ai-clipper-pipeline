# AI Clipper — Project Documentation

## What This Is

AI Clipper is a headless video processing pipeline that runs 24/7 on a Ubuntu home server. It automatically downloads YouTube and Twitch VODs, transcribes them with Whisper (GPU-accelerated), sends the transcript to Claude to identify viral-worthy moments, and cuts those moments into vertical 9:16 short clips using ffmpeg. Clips are reviewed via a web dashboard — approved clips are automatically uploaded to YouTube Shorts with Claude-generated titles and descriptions. Jobs are submitted via a REST API, web dashboard, or automatically via n8n RSS monitoring of configured YouTube channels.

**Target channels being monitored:** Peter Attia, Huberman Lab (health/science content for a YouTube Shorts channel).

---

## Architecture

```
Browser / n8n / curl
        │
        ▼
┌──────────────────┐       ┌──────────────────┐
│   FastAPI (8000) │──────▶│  Redis (6379)     │
│   + Dashboard    │       │  rq job queue     │
└──────────────────┘       └────────┬─────────┘
        │                           │
        │  SQLite                   ▼
        │  (data/jobs.db)  ┌──────────────────┐
        └─────────────────▶│  rq Worker        │
                           │                  │
                           │  1. yt-dlp        │
                           │  2. ffmpeg (audio)│
                           │  3. faster-whisper│
                           │  4. Claude API    │
                           │  5. ffmpeg (clips)│
                           └──────────────────┘
                                    │
                                    ▼
                           data/clips/{job_id}/
                                    │
                           (on approve)
                                    ▼
                           YouTube Shorts upload
```

**Services (docker-compose.yml):**

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| redis | redis:7-alpine | 6379 | Job queue backend |
| worker | ./Dockerfile | — | Runs the pipeline via rq |
| api | ./Dockerfile | 8000 | FastAPI REST API + dashboard |
| n8n | n8nio/n8n | 5679 | Workflow automation |

**Data flow:**
1. `POST /jobs` → creates SQLite record → enqueues rq job
2. Worker fetches video metadata (title/uploader) → downloads → transcribes (GPU) → analyzes with Claude → cuts 9:16 clips
3. Dashboard auto-refreshes; clips appear when job is done
4. User approves clip → YouTube upload triggers automatically in background
5. User rejects clip → file deleted from disk immediately
6. Jobs with all clips reviewed move to Archive tab; files auto-deleted after 7 days via n8n

---

## How to Run

### Development (without Docker)

```bash
# 1. Activate virtual environment
source .venv/bin/activate

# 2. Start Redis (need Docker for this)
docker run -d -p 6379:6379 redis:7-alpine

# 3. Run a job directly (bypasses rq queue)
python src/worker.py 'https://www.youtube.com/watch?v=YOUR_VIDEO'

# 4. Or start the API server
uvicorn src.api:app --reload --port 8000

# 5. Run tests
pytest tests/ -v -m "not integration"
```

### Production (Docker Compose)

```bash
# Start everything
docker compose up -d

# View logs
docker logs ai-clipper-worker-1 -f
docker logs ai-clipper-api-1 -f

# Restart a single service
docker compose restart worker

# Rebuild after code changes
docker compose up --build -d

# Stop everything
docker compose down
```

### Auto-start on reboot

The systemd service is installed and enabled at `/etc/systemd/system/ai-clipper.service`:

```bash
sudo systemctl status ai-clipper.service
sudo systemctl start ai-clipper.service
sudo systemctl stop ai-clipper.service
```

---

## Environment Variables

All variables live in `.env` (copy from `.env.example`). Never commit `.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | *required* | Your Anthropic API key |
| `WHISPER_MODEL` | `base` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large-v2`, `large-v3` |
| `WHISPER_LANGUAGE` | `en` | Transcription language. Use `auto` for auto-detect |
| `MIN_CLIP_SCORE` | `0.7` | Minimum viral score (0.0–1.0) to keep a clip |
| `CLIP_BUFFER_SECONDS` | `2` | Padding added before/after each clip timestamp |
| `MAX_CLIP_DURATION` | `59` | Maximum clip length in seconds (59 = YouTube Shorts limit) |
| `CLIP_RETENTION_DAYS` | `7` | Days before archived clip files are deleted by n8n cleanup workflow |
| `DOWNLOAD_DIR` | `data/downloads` | Where yt-dlp saves videos |
| `TRANSCRIPT_DIR` | `data/transcripts` | Where Whisper saves transcripts |
| `CLIPS_DIR` | `data/clips` | Where ffmpeg saves clips |
| `EXPORT_DIR` | `data/exports` | Where job summaries are saved |
| `REDIS_URL` | `redis://redis:6379` | Redis connection URL (`redis://localhost:6379` outside Docker) |
| `DATABASE_PATH` | `data/jobs.db` | SQLite database path |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude model to use for analysis |
| `CLAUDE_MAX_TOKENS` | `1024` | Max tokens in Claude response |

---

## API Reference

### Submit a job
```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
# → {"job_id": "uuid"}  HTTP 202
```

### Poll job status
```bash
curl http://localhost:8000/jobs/{job_id}
# → {job_id, url, title, uploader, status, clips: [{start, end, reason, score, approval, file_deleted, youtube_url}], clips_deleted, ...}
```

### List all jobs
```bash
curl http://localhost:8000/jobs?limit=50
```

### Health check
```bash
curl http://localhost:8000/health
# → {"status": "ok"}
```

### Approve/reject a clip
```bash
curl -X POST http://localhost:8000/jobs/{job_id}/clips/0/approve
# → triggers YouTube upload in background if credentials present
curl -X POST http://localhost:8000/jobs/{job_id}/clips/0/reject
# → deletes clip file from disk immediately
```

### Delete all clip files for a job (used by n8n cleanup)
```bash
curl -X DELETE http://localhost:8000/jobs/{job_id}/clips
```

### Job statuses
`pending` → `downloading` → `transcribing` → `analyzing` → `clipping` → `done` / `failed`

---

## Dashboard

Access at `http://<tailscale-ip>:8000`. Tailscale IP: `100.72.208.112`.

**Active tab:** Jobs currently processing or with unreviewed clips.
**Archive tab:** Jobs where all clips are approved/rejected. Shows scheduled delete date (updated_at + 7 days). Shows "Files deleted" once the n8n cleanup workflow runs.

**Per-clip actions:**
- **Approve** — marks clip approved, triggers YouTube upload automatically
- **Reject** — marks clip rejected, deletes MP4 from disk immediately
- **Download** — downloads the clip file
- **YouTube** button appears once upload is complete, links to the video

**n8n** at `http://100.72.208.112:5679`

---

## n8n Workflows

Three active workflows configured in n8n:

### 1. Job completion email
- Schedule: every 5 minutes
- Fetches `GET http://api:8000/jobs?limit=20`
- Filters: `status == done`
- Deduplicates by `job_id` (Remove Duplicates node, keyed on `{{ $json.job_id }}`)
- Sends email to configured address with dashboard link

### 2. Peter Attia channel monitor
- RSS Feed Trigger (every 30 min): `https://www.youtube.com/feeds/videos.xml?channel_id=UC8kGsMa0LygSX9nkBcBH1Sg`
- Auto-submits new videos: `POST http://api:8000/jobs` with `{"url": "{{ $json.link }}"}`

### 3. Huberman Lab channel monitor
- RSS Feed Trigger (every 30 min): `https://www.youtube.com/feeds/videos.xml?channel_id=UC2D2CMWXMOVWx7giW1n3LIg`
- Auto-submits new videos: `POST http://api:8000/jobs` with `{"url": "{{ $json.link }}"}`

### 4. Clip file cleanup (configure this)
- Schedule: daily at 3am
- Fetches `GET http://api:8000/jobs?limit=200`
- Filters: `clips_deleted == false` AND `status == done` AND `updated_at` before `{{ $now.minus({days: 7}).toISO() }}`
- Calls `DELETE http://api:8000/jobs/{{ $json.job_id }}/clips` for each

---

## YouTube Upload Setup

Upload triggers automatically when a clip is approved, if `config/youtube_token.json` exists.

### One-time setup
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create project → enable **YouTube Data API v3**
3. Create **OAuth 2.0 credentials** (Desktop app type) → download JSON
4. Save as `config/youtube_client_secrets.json`
5. Add your email as a test user under **OAuth consent screen → Test users**
6. Run the auth script (outside Docker, in venv):
   ```bash
   source .venv/bin/activate
   python scripts/youtube_auth.py
   ```
7. Open the printed URL in browser, authorize, complete flow
8. Token saved to `config/youtube_token.json` — persists via Docker volume mount

The `config/` directory is mounted as a volume in the API container so tokens survive rebuilds.

**Note:** Clips are uploaded as **public** videos. Huberman Lab and Peter Attia content is registered with YouTube Content ID — uploaded clips will likely be auto-claimed. This is expected behavior.

---

## Clip Format

All clips are output as **1080x1920 (9:16 vertical)** MP4 files for YouTube Shorts compatibility.

ffmpeg filter applied: `scale=1080:-2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black`

This scales the source video (typically 16:9 landscape) to 1080px wide, then pads with black bars top and bottom to fill the 1920px height. Re-encoding uses `libx264 crf=23 preset=fast` with `aac 128k` audio.

Maximum clip duration: 59 seconds (configured via `MAX_CLIP_DURATION` in `.env`).

---

## Key Design Decisions

**Why rq instead of Celery?**
Simpler to operate headlessly. rq is just Redis + a worker process with no additional config.

**Why SQLite instead of Postgres?**
One server, one writer, simple queries. Zero-dependency and survives reboots. Easy to upgrade later.

**Why faster-whisper instead of openai-whisper?**
4x faster, uses CTranslate2 for GPU inference. GPU detection uses `ctranslate2.get_cuda_device_count()` — NOT `torch.cuda` (torch isn't installed). This was a bug that caused CPU fallback even with the GTX 1660 Super available.

**Why ffmpeg via subprocess?**
The `-ss` before `-i` fast-seek and `-c copy` no-reencode patterns are critical for performance. Clips now re-encode (for 9:16 padding) but still use fast seek.

**Why `src/queue.py` was renamed to `src/job_queue.py`**
`queue` is a Python stdlib module — shadowing it broke huggingface_hub inside faster-whisper.

**Why the pipeline resumes instead of restarting?**
Each stage checks if its output already exists. A crash at transcription won't re-download a 2GB video.

**Why YouTube upload is a background thread in the API?**
Uploads can take 1–5 minutes. Blocking the approve endpoint would time out. The thread saves the YouTube URL to the DB when done; the dashboard shows the link on next poll.

---

## Known Gotchas

- **Docker group**: `sudo usermod -aG docker $USER` requires full SSH logout/login to take effect.

- **Docker Compose plugin**: Installed as a binary at `~/.docker/cli-plugins/docker-compose` and `/usr/libexec/docker/cli-plugins/docker-compose`.

- **HDD build times**: First build takes 10–20 minutes. Subsequent builds use Docker cache and are fast.

- **yt-dlp binary path**: In dev mode, `yt-dlp` is at `.venv/bin/yt-dlp`. Resolved via `Path(sys.executable).parent / "yt-dlp"`.

- **n8n port**: Default n8n port 5678 was already in use. Maps to **5679** externally.

- **n8n secure cookie**: `N8N_SECURE_COOKIE=false` is set in docker-compose.yml to allow HTTP access over Tailscale.

- **Whisper model download**: First transcription downloads the model (~150MB for `base`) from Hugging Face. Expect a delay on first run.

- **GPU detection**: Uses `ctranslate2.get_cuda_device_count()`, not torch. This was a bug fix — torch isn't installed but ctranslate2 (used by faster-whisper) is, and it has direct CUDA access.

- **Resume logic skips re-encoding**: If clips already exist from a previous run, the clipper skips them. If you change the ffmpeg filter (e.g. aspect ratio), old clips won't be re-processed. Delete the job's clip directory to force re-cutting.

- **YouTube OAuth**: `run_local_server` requires an SSH tunnel (`ssh -L 8765:localhost:8765`) since the server is headless. Auth script at `scripts/youtube_auth.py`.

- **ClipResult JSON in DB**: `ClipResult` is stored as a JSON blob in the `clips` column. Adding new fields (e.g. `file_deleted`, `youtube_url`) only requires dataclass default values — no DB migration needed since missing keys use defaults on load.

---

## GPU Setup (GTX 1660 Super — already done)

```bash
sudo apt install -y nvidia-driver-535
# install nvidia-container-toolkit (see original setup notes)
sudo reboot
nvidia-smi  # verify
# GPU block already uncommented in docker-compose.yml worker service
```

Verify GPU is being used:
```bash
docker logs ai-clipper-worker-1 2>&1 | grep "device"
# should show device=cuda
watch -n 1 nvidia-smi  # while a job is transcribing
```

---

## Data Model

### Job fields
`id`, `url`, `status`, `error`, `clips` (JSON), `created_at`, `updated_at`, `title`, `uploader`, `clips_deleted`

### ClipResult fields
`start`, `end`, `reason`, `score`, `approval` (pending/approved/rejected), `file_deleted`, `youtube_url`

### Job lifecycle
1. Created → `pending`
2. Worker picks up → `downloading` (metadata fetched here: title, uploader)
3. → `transcribing` (GPU, faster-whisper)
4. → `analyzing` (Claude API)
5. → `clipping` (ffmpeg, 9:16 output)
6. → `done` or `failed`
7. User reviews clips → approved clips upload to YouTube, rejected clips deleted from disk
8. All clips reviewed → job moves to Archive tab
9. After 7 days → n8n cleanup deletes remaining files, sets `clips_deleted=True`

---

## Roadmap

- [ ] **Twitch EventSub** — auto-trigger jobs when a streamer ends a stream
- [ ] **TikTok upload** — publish approved clips to TikTok via API
- [ ] **More channels** — duplicate n8n RSS workflows for other podcasters
- [ ] **Postgres migration** — when job volume grows beyond SQLite comfort zone
- [ ] **Clip score tuning** — adjust `MIN_CLIP_SCORE` in `.env` if too many/few clips
- [ ] **Multi-language support** — set `WHISPER_LANGUAGE=auto` in `.env`
