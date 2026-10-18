import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
INCOMING_DIR = DATA_DIR / "incoming"
PROCESSED_DIR = DATA_DIR / "processed"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"
ASSETS_DIR = DATA_DIR / "assets"
SFX_DIR = ASSETS_DIR / "sfx"
OVERLAYS_DIR = ASSETS_DIR / "overlays"
BACKGROUNDS_DIR = ASSETS_DIR / "backgrounds"
GENERATED_DIR = DATA_DIR / "generated"
GDRIVE_VAULT_DIR = DATA_DIR / "gdrive_vault"
DB_PATH = DATA_DIR / "agent.db"

for directory in (
    DATA_DIR,
    INCOMING_DIR,
    PROCESSED_DIR,
    THUMBNAILS_DIR,
    ASSETS_DIR,
    SFX_DIR,
    OVERLAYS_DIR,
    BACKGROUNDS_DIR,
    GENERATED_DIR,
    GDRIVE_VAULT_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)


DEFAULT_SETTINGS = {
    "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
    "gemini_api_keys_pool": [],
    "gemini_model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
    "gemini_image_model": os.getenv("GEMINI_IMAGE_MODEL", "gemini-nano-banana-2.1"),
    "gemini_video_model": os.getenv("GEMINI_VIDEO_MODEL", "gemini-omni-1.1-flash"),
    "gemini_tts_model": os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"),
    "max_parallel_workers": int(os.getenv("MAX_PARALLEL_WORKERS", "4")),
    "auto_generate_trending_posts": os.getenv("AUTO_GENERATE_TRENDING_POSTS", "true").lower() == "true",
    "trending_niches": os.getenv("TRENDING_NICHES", "Technology & AI, World News, Science & Space, Viral Pop Culture"),
    "youtube_client_secrets_file": os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", str(BASE_DIR / "client_secrets.json")),
    "youtube_token_file": os.getenv("YOUTUBE_TOKEN_FILE", str(DATA_DIR / "youtube_token.json")),
    "youtube_simulation_mode": os.getenv("YOUTUBE_SIMULATION_MODE", "true").lower() == "true",
    "gdrive_storage_enabled": os.getenv("GDRIVE_STORAGE_ENABLED", "true").lower() == "true",
    "gdrive_vault_folder_id": os.getenv("GDRIVE_VAULT_FOLDER_ID", ""),
    "gdrive_local_mount_path": os.getenv("GDRIVE_LOCAL_MOUNT_PATH", ""),
    "auto_cleanup_local_after_publish": os.getenv("AUTO_CLEANUP_LOCAL_AFTER_PUBLISH", "false").lower() == "true",
    "pexels_api_key": os.getenv("PEXELS_API_KEY", ""),
    "pixabay_api_key": os.getenv("PIXABAY_API_KEY", ""),
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", ""),
    "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", ""),
    "aws_region": os.getenv("AWS_REGION", "us-east-1"),
    "facebook_page_id": os.getenv("FACEBOOK_PAGE_ID", ""),
    "facebook_access_token": os.getenv("FACEBOOK_ACCESS_TOKEN", ""),
    "instagram_account_id": os.getenv("INSTAGRAM_ACCOUNT_ID", ""),
    "tiktok_access_token": os.getenv("TIKTOK_ACCESS_TOKEN", ""),
    "omnichannel_webhook_url": os.getenv("OMNICHANNEL_WEBHOOK_URL", ""),
    "target_platforms": ["youtube", "facebook", "instagram", "tiktok"],
    "always_apply_copyright_safe_edit": True,
    "agent_auto_loop_enabled": os.getenv("AGENT_AUTO_LOOP_ENABLED", "false").lower() == "true",
    "agent_loop_interval_seconds": int(os.getenv("AGENT_LOOP_INTERVAL_SECONDS", "300")),
    "auto_remediate_failed_videos": os.getenv("AUTO_REMEDIATE_FAILED_VIDEOS", "true").lower() == "true",
    "precheck_privacy_status": os.getenv("PRECHECK_PRIVACY_STATUS", "private"),
    "daily_upload_limit": int(os.getenv("DAILY_UPLOAD_LIMIT", "4")),
    "upload_spacing_hours": int(os.getenv("UPLOAD_SPACING_HOURS", "4")),
    "auto_reply_comments": os.getenv("AUTO_REPLY_COMMENTS", "false").lower() == "true",
    "channel_persona": os.getenv(
        "CHANNEL_PERSONA",
        "Friendly, knowledgeable, and concise creator who appreciates viewers and answers questions clearly without fluff.",
    ),
}

