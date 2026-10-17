# 🚀 Autonomous Omnichannel AI Media & Content Agent

An enterprise-grade, multi-talented AI agent and multimedia production studio built with **FastAPI**, **Gemini 3.8 Flash**, **Gemini Nano Banana 2.1**, **Gemini Omni 1.1 Flash**, **FFmpeg**, and the **YouTube Data API v3** + **Meta Graph API** + **TikTok Posting API**.

The agent operates completely autonomously: collecting copyright-free media, remixing videos to ensure 100% copyright safety, discovering real-time trending news/events, generating original AI videos and image posts, distributing content omnichannel across YouTube, Facebook, Instagram, and TikTok, and intelligently replying to viewer comments.

---

## 🌟 Key Architecture & Capabilities

### 1. 🗂️ Recursive Mother Folder Collector
- Deep-crawls nested Google Drive shared folders and subfolders (`_discover_gdrive_videos_recursive`), local watch folders, Pexels, Pixabay, AWS S3, and RSS feeds.
- Easily adds and manages unlimited sources from the web dashboard or REST API.

### 2. 🛡️ 5-Layer Zero-Duplicate Protection (`dedup_guard`)
- **Pre-Download Guard**: Checks Drive file IDs, URLs, and filenames before wasting bandwidth.
- **Cryptographic File Hash**: Hashes every video with `SHA-256` so identical content with different names is never processed twice.
- **Semantic News & Event Deduplication**: Uses token extraction and Jaccard similarity (`>= 60%`) to guarantee the agent never generates duplicate AI videos or image posts for the same story, even when headlines differ across news publishers (e.g., Reuters vs. BBC).
- **Per-Platform Omnichannel Lock**: Locks each platform (`YouTube`, `Facebook`, `Instagram`, `TikTok`, `Webhook`) independently so a video or post is never uploaded to the same network twice.

### 3. 🔍 Multimodal AI Inspection & FFmpeg Remixing
- Evaluates video technical parameters (`ffprobe`) and multimodal frame analysis (`gemini-3.8-flash`) for audio risk, watermarks, and policy compliance.
- Automatically applies copyright-safe edits: micro-speed shifts, saturation/contrast adjustments, dynamic audio ducking, LUT grading, and frame-rate normalization.

### 4. 🎨 Generative Media Studio (News & Event Driven)
- **Live Trend Discovery**: Fetches breaking news via Google News RSS + Gemini 3.8 Flash Google Search grounding.
- **AI Image Posts**: Produces 1:1, 9:16, or 16:9 social graphics with `gemini-nano-banana-2.1` and viral SEO captions.
- **End-to-End AI Videos & Shorts**: Synthesizes expressive narration with `gemini-3.8-flash-tts`, animates footage with `gemini-omni-1.1-flash`, and renders broadcast-ready H.264/AAC MP4s with `FFmpeg`.

### 5. 🔑 Multi-Account Gemini API Key Pool
- Load-balances requests across multiple Google Pro API keys from family or team accounts.
- Intelligent round-robin selection with instant `<50ms` auto-failover, quota cooldown trackers, and zero rate-limit interruptions.

### 6. ⚡ Parallel Multi-Task Engine
- Concurrently runs source syncing, AI video inspections, FFmpeg remixes, trend research, media generations, and omnichannel uploads across configurable worker threads.

### 7. 💬 Smart Comment Responder
- Periodically fetches YouTube channel comments and triages them with `gemini-3.8-flash`.
- Automatically ignores spam, bots, trolls, and single emojis, while crafting warm, helpful replies to genuine viewer questions and feedback.

### 8. 🌐 Modern Real-Time Web Dashboard
- Single-page dashboard built with Tailwind CSS and Vanilla JS for zero-build friction.
- Live system metrics, task queues, source manager, video audit viewer, AI Studio canvas, key pool monitor, and activity logs.

---

## 📂 Project Structure

```
├── app/
│   ├── main.py                  # FastAPI application entrypoint & API routes
│   ├── config.py                # Environment configuration & path resolvers
│   ├── database.py              # SQLite schemas (videos, studio, dedup, keys, tasks)
│   ├── models.py                # Pydantic schemas for API requests & AI structured outputs
│   ├── services/
│   │   ├── agent_worker.py      # Autonomous background pipeline orchestration
│   │   ├── collector.py         # Recursive Google Drive & multi-source media crawler
│   │   ├── comment_responder.py # AI comment triage & auto-responder
│   │   ├── content_generator.py # Generative Media Studio (Trends, Images, Omni Videos)
│   │   ├── dedup_guard.py       # 5-Layer cryptographic & semantic deduplication engine
│   │   ├── inspector.py         # Gemini multimodal video quality & policy auditor
│   │   ├── key_pool.py          # Multi-account API key rotator & health monitor
│   │   ├── media_processor.py   # FFmpeg copyright-safe remixer & thumbnail extractor
│   │   ├── parallel_engine.py   # Thread pool worker queue & task telemetry
│   │   ├── social_publishers.py # Omnichannel publishing (FB, IG, TikTok, Webhooks)
│   │   └── youtube_service.py   # YouTube Data API v3 pre-check & upload client
│   └── static/                  # Web dashboard UI (HTML & JavaScript)
├── data/                        # Persistent storage (git-ignored media & SQLite DB)
├── Dockerfile                   # Production Python 3.12 + FFmpeg container image
├── firebase.json                # Firebase Hosting rewrite to Cloud Run container
├── requirements.txt             # Python dependencies
└── run.py                       # Local startup script
```

---

## 🚀 Quick Start (Local Development)

### 1. Prerequisites
- Python 3.11 or 3.12
- [FFmpeg](https://ffmpeg.org) installed and available in your system `PATH`

### 2. Setup Virtual Environment
```bash
python -m venv .venv

# Windows
.\.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Start the Agent & Web Dashboard
```bash
python run.py --host 127.0.0.1 --port 8000
```
Open **http://127.0.0.1:8000** in your browser.

---

## ☁️ Cloud Deployment (Firebase Hosting + Google Cloud Run)

Deploy seamlessly into Google Cloud's **Always-Free Tier** (under \$0–\$3/month):

```bash
# 1. Build and deploy container to Google Cloud Run
gcloud run deploy ai-media-agent \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 900 \
  --min-instances 0 \
  --max-instances 1

# 2. Deploy Firebase Hosting (*.web.app URL)
npx firebase-tools@latest deploy --only hosting

# 3. Schedule autonomous cycles every hour with Cloud Scheduler
gcloud scheduler jobs create http ai-agent-hourly-cycle \
  --location=us-central1 \
  --schedule="0 * * * *" \
  --uri="https://YOUR_PROJECT_ID.web.app/api/agent/run-cycle" \
  --http-method=POST
```

---

## 📄 License
This project is open-source under the MIT License.
