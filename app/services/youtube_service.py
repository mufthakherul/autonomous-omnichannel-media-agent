import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.database import get_connection, get_settings, get_video, log_activity, update_video, utc_now_iso
from app.services.media_processor import extract_thumbnail

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def get_youtube_client(interactive_auth: bool = False):
    """Builds an authenticated YouTube Data API v3 resource, or returns None if in simulation mode."""
    settings = get_settings()
    if settings.get("youtube_simulation_mode", True):
        return None

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_file = Path(settings.get("youtube_token_file") or "data/youtube_token.json")
    secrets_file = Path(settings.get("youtube_client_secrets_file") or "client_secrets.json")

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), YOUTUBE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif interactive_auth and secrets_file.exists():
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), YOUTUBE_SCOPES)
            creds = flow.run_local_server(port=0)
        else:
            raise RuntimeError(
                f"YouTube OAuth token not found or expired. Place '{secrets_file.name}' in project root and click 'Authorize YouTube OAuth' in Settings, or enable Simulation Mode."
            )
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return build("youtube", "v3", credentials=creds)


def compute_next_schedule_slot() -> str:
    """Computes the next optimal upload time slot based on daily_upload_limit and upload_spacing_hours."""
    settings = get_settings()
    spacing_hours = max(1, int(settings.get("upload_spacing_hours") or 4))
    conn = get_connection()
    row = conn.execute(
        "SELECT scheduled_publish_at FROM videos WHERE scheduled_publish_at IS NOT NULL ORDER BY scheduled_publish_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    now = datetime.now(timezone.utc)
    if row and row["scheduled_publish_at"]:
        try:
            last_dt = datetime.fromisoformat(row["scheduled_publish_at"])
            if last_dt > now:
                return (last_dt + timedelta(hours=spacing_hours)).isoformat()
        except Exception:
            pass
    return (now + timedelta(hours=spacing_hours)).isoformat()


def ensure_playlist_and_add_video(youtube_client, youtube_video_id: str, playlist_name: str) -> Optional[str]:
    """Finds or creates a YouTube playlist by name and adds the video to it."""
    if not playlist_name:
        return None
    if youtube_client is None:
        return f"PL_sim_{playlist_name.lower().replace(' ', '_')[:16]}"

    pl_list = youtube_client.playlists().list(part="snippet", mine=True, maxResults=50).execute()
    target_pl_id = None
    for item in pl_list.get("items", []):
        if item.get("snippet", {}).get("title", "").strip().lower() == playlist_name.strip().lower():
            target_pl_id = item["id"]
            break

    if not target_pl_id:
        created = (
            youtube_client.playlists()
            .insert(
                part="snippet,status",
                body={
                    "snippet": {
                        "title": playlist_name,
                        "description": f"Curated {playlist_name} videos managed by AI Agent.",
                    },
                    "status": {"privacyStatus": "public"},
                },
            )
            .execute()
        )
        target_pl_id = created["id"]

    youtube_client.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": target_pl_id,
                "resourceId": {"kind": "youtube#video", "videoId": youtube_video_id},
            }
        },
    ).execute()
    return target_pl_id


def upload_video_for_precheck(video_id: int) -> Dict[str, Any]:
    """
    Stage 1 of Safe Publishing:
    Uploads the video (untouched original if can_upload_as_is=True, or remediated file if fixed)
    as 'private' or 'unlisted' so YouTube can process it and run Content ID checks before going public.
    """
    video = get_video(video_id)
    if not video:
        raise ValueError(f"Video {video_id} not found")

    settings = get_settings()
    precheck_privacy = settings.get("precheck_privacy_status") or "private"

    # Choose untouched original file if can_upload_as_is is True, else remediated_path
    upload_file_path = Path(
        video["local_path"]
        if video.get("can_upload_as_is") or not video.get("remediated_path")
        else video["remediated_path"]
    )
    if not upload_file_path.exists():
        raise FileNotFoundError(f"Video file to upload does not exist: {upload_file_path}")

    # Extract custom thumbnail at best timestamp if not already extracted
    thumb_path = video.get("thumbnail_path")
    if not thumb_path or not Path(thumb_path).exists():
        thumb_path = extract_thumbnail(video_id)

    update_video(video_id, {"status": "uploading_precheck"})
    youtube_client = get_youtube_client(interactive_auth=False)

    title = (video.get("seo_title") or video["original_filename"])[:100]
    description = video.get("seo_description") or ""
    tags = video.get("seo_tags") or []
    category_id = str(video.get("category_id") or "22")

    if youtube_client is None:
        # Simulation Mode
        sim_yt_id = f"yt_sim_{secrets.token_hex(4)}"
        updated = update_video(
            video_id,
            {
                "youtube_video_id": sim_yt_id,
                "youtube_privacy_status": precheck_privacy,
                "status": "precheck_pending",
            },
        )
        log_activity(
            "YOUTUBE",
            f"[Simulation] Pre-uploaded video #{video_id} ('{title}') as '{precheck_privacy}' (ID: {sim_yt_id}) for Content ID check",
            details={
                "video_id": video_id,
                "youtube_video_id": sim_yt_id,
                "uploaded_without_changes": bool(video.get("can_upload_as_is")),
                "privacy": precheck_privacy,
            },
        )
        return updated or {}

    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": precheck_privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(upload_file_path), chunksize=4 * 1024 * 1024, resumable=True)
    request = youtube_client.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()

    yt_video_id = response["id"]

    if thumb_path and Path(thumb_path).exists():
        try:
            youtube_client.thumbnails().set(
                videoId=yt_video_id,
                media_body=MediaFileUpload(str(thumb_path), mimetype="image/jpeg"),
            ).execute()
        except Exception as exc:
            log_activity("YOUTUBE", f"Thumbnail upload warning for {yt_video_id}: {exc}", level="WARN")

    updated = update_video(
        video_id,
        {
            "youtube_video_id": yt_video_id,
            "youtube_privacy_status": precheck_privacy,
            "status": "precheck_pending",
        },
    )
    log_activity(
        "YOUTUBE",
        f"Uploaded video #{video_id} ('{title}') as '{precheck_privacy}' (YouTube ID: {yt_video_id}) for Content ID verification",
        details={"video_id": video_id, "youtube_video_id": yt_video_id},
    )
    return updated or {}


def verify_precheck_and_publish(video_id: int, publish_immediately: bool = True) -> Dict[str, Any]:
    """
    Stage 2 of Safe Publishing:
    Checks YouTube processing & Content ID status for a pre-uploaded video.
    If clean, promotes the video to 'public' (or schedules it) and adds it to its YouTube Playlist.
    """
    video = get_video(video_id)
    if not video:
        raise ValueError(f"Video {video_id} not found")

    yt_video_id = video.get("youtube_video_id")
    if not yt_video_id:
        raise ValueError(f"Video #{video_id} has not been pre-uploaded yet.")

    youtube_client = get_youtube_client(interactive_auth=False)
    schedule_slot = None if publish_immediately else compute_next_schedule_slot()

    if youtube_client is None:
        # Simulation Mode verification & promotion
        pl_id = ensure_playlist_and_add_video(None, yt_video_id, video.get("playlist_name", "General"))
        final_status = "published" if publish_immediately else "scheduled"
        final_privacy = "public" if publish_immediately else "private"
        now = utc_now_iso()
        updated = update_video(
            video_id,
            {
                "content_id_clean": True,
                "youtube_privacy_status": final_privacy,
                "status": final_status,
                "scheduled_publish_at": schedule_slot,
                "published_at": now if publish_immediately else None,
            },
        )
        log_activity(
            "YOUTUBE",
            f"[Simulation] Content ID Pre-Check PASSED for {yt_video_id}. Status -> {final_status.upper()} (Playlist: {video.get('playlist_name')})",
            details={
                "video_id": video_id,
                "youtube_video_id": yt_video_id,
                "playlist_id": pl_id,
                "scheduled_publish_at": schedule_slot,
            },
        )
        return updated or {}

    resp = (
        youtube_client.videos()
        .list(part="status,processingDetails,contentDetails", id=yt_video_id)
        .execute()
    )
    items = resp.get("items", [])
    if not items:
        raise RuntimeError(f"Video {yt_video_id} not found on YouTube.")

    item = items[0]
    status_obj = item.get("status", {})
    content_details = item.get("contentDetails", {})
    upload_status = status_obj.get("uploadStatus")
    rejection_reason = status_obj.get("rejectionReason")
    has_restriction = bool(content_details.get("regionRestriction") or content_details.get("contentRating"))

    if upload_status in ("rejected", "failed") or rejection_reason or has_restriction:
        updated = update_video(
            video_id,
            {
                "content_id_clean": False,
                "status": "copyright_flagged",
                "error_message": f"YouTube Pre-Check flagged video: uploadStatus={upload_status}, reason={rejection_reason}",
            },
        )
        log_activity(
            "YOUTUBE",
            f"YouTube Pre-Check FLAGGED video #{video_id} ({yt_video_id}): {rejection_reason or upload_status}. Kept private!",
            level="ERROR",
        )
        return updated or {}

    if upload_status != "processed":
        log_activity("YOUTUBE", f"Video {yt_video_id} is still processing on YouTube ({upload_status}).")
        return video

    # Video is processed and clean! Promote to public or schedule
    status_body: Dict[str, Any] = {"selfDeclaredMadeForKids": False}
    if publish_immediately:
        status_body["privacyStatus"] = "public"
        final_status = "published"
    else:
        status_body["privacyStatus"] = "private"
        status_body["publishAt"] = schedule_slot
        final_status = "scheduled"

    youtube_client.videos().update(
        part="status",
        body={"id": yt_video_id, "status": status_body},
    ).execute()

    ensure_playlist_and_add_video(youtube_client, yt_video_id, video.get("playlist_name", "General"))

    updated = update_video(
        video_id,
        {
            "content_id_clean": True,
            "youtube_privacy_status": status_body["privacyStatus"],
            "status": final_status,
            "scheduled_publish_at": schedule_slot,
            "published_at": utc_now_iso() if publish_immediately else None,
        },
    )
    log_activity(
        "YOUTUBE",
        f"Content ID Pre-Check PASSED for {yt_video_id}. Promoted to {final_status.upper()}!",
        details={"video_id": video_id, "youtube_video_id": yt_video_id},
    )
    return updated or {}
