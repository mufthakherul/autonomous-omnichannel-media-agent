import json
import secrets
from pathlib import Path
from typing import Any, Dict, List

import httpx

from app.database import get_connection, get_settings, get_video, log_activity, utc_now_iso
from app.services.dedup_guard import (
    has_already_published_to_platform,
    record_published_to_platform,
)
from app.services.youtube_service import upload_video_for_precheck, verify_precheck_and_publish

SUPPORTED_PLATFORMS = ["youtube", "facebook", "instagram", "tiktok", "webhook"]



def _get_video_upload_path(video: Dict[str, Any]) -> Path:
    """Returns the copyright-safe remediated file if present, otherwise the original local file."""
    rem = video.get("remediated_path")
    if rem and Path(rem).exists():
        return Path(rem)
    return Path(video["local_path"])


def publish_to_facebook_page(video: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Publishes video/Reel to a Facebook Page via Meta Graph API (or Simulation Mode)."""
    page_id = (settings.get("facebook_page_id") or "").strip()
    access_token = (settings.get("facebook_access_token") or "").strip()
    sim_mode = bool(settings.get("youtube_simulation_mode", True)) or not (page_id and access_token)

    caption = f"{video.get('seo_title', '')}\n\n{video.get('seo_description', '')}".strip()
    file_path = _get_video_upload_path(video)

    if sim_mode:
        post_id = f"fb_reel_sim_{secrets.token_hex(4)}"
        log_activity(
            "FACEBOOK",
            f"[Simulation] Published Reel/Video #{video['id']} to Facebook Page ({post_id})",
            details={"video_id": video["id"], "fb_post_id": post_id},
        )
        return {"platform": "facebook", "status": "published", "external_id": post_id, "mode": "simulation"}

    # Live Meta Graph API upload
    url = f"https://graph-video.facebook.com/v21.0/{page_id}/videos"
    with httpx.Client(timeout=120.0) as client:
        with open(file_path, "rb") as f:
            resp = client.post(
                url,
                data={"description": caption, "access_token": access_token},
                files={"source": (file_path.name, f, "video/mp4")},
            )
            resp.raise_for_status()
            data = resp.json()

    post_id = data.get("id", f"fb_{secrets.token_hex(4)}")
    log_activity("FACEBOOK", f"Published Video #{video['id']} to Facebook Page (ID: {post_id})")
    return {"platform": "facebook", "status": "published", "external_id": post_id, "mode": "live"}


def publish_to_instagram_reels(video: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Publishes a 9:16 Reel to an Instagram Professional Account via Instagram Graph API."""
    ig_user_id = (settings.get("instagram_account_id") or "").strip()
    access_token = (settings.get("facebook_access_token") or "").strip()
    sim_mode = bool(settings.get("youtube_simulation_mode", True)) or not (ig_user_id and access_token)

    caption = f"{video.get('seo_title', '')}\n\n{video.get('seo_description', '')}"[:2150]
    file_path = _get_video_upload_path(video)

    if sim_mode:
        media_id = f"ig_reel_sim_{secrets.token_hex(4)}"
        log_activity(
            "INSTAGRAM",
            f"[Simulation] Published Reel #{video['id']} to Instagram Business Account ({media_id})",
            details={"video_id": video["id"], "ig_media_id": media_id},
        )
        return {"platform": "instagram", "status": "published", "external_id": media_id, "mode": "simulation"}

    # Resumable upload to Instagram Graph API Reels container
    init_url = f"https://graph.facebook.com/v21.0/{ig_user_id}/media"
    with httpx.Client(timeout=120.0) as client:
        init_resp = client.post(
            init_url,
            data={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": caption,
                "access_token": access_token,
            },
        )
        init_resp.raise_for_status()
        init_data = init_resp.json()
        container_id = init_data["id"]
        upload_uri = init_data.get("uri")

        if upload_uri:
            file_bytes = file_path.read_bytes()
            up_resp = client.post(
                upload_uri,
                headers={
                    "Authorization": f"OAuth {access_token}",
                    "offset": "0",
                    "file_size": str(len(file_bytes)),
                },
                content=file_bytes,
            )
            up_resp.raise_for_status()

        pub_resp = client.post(
            f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": access_token},
        )
        pub_resp.raise_for_status()
        media_id = pub_resp.json().get("id", container_id)

    log_activity("INSTAGRAM", f"Published Reel #{video['id']} to Instagram (Media ID: {media_id})")
    return {"platform": "instagram", "status": "published", "external_id": media_id, "mode": "live"}


def publish_to_tiktok(video: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Publishes vertical video to TikTok via TikTok Content Posting API v2 (or Simulation Mode)."""
    tiktok_token = (settings.get("tiktok_access_token") or "").strip()
    sim_mode = bool(settings.get("youtube_simulation_mode", True)) or not tiktok_token

    title_and_tags = f"{video.get('seo_title', '')} " + " ".join(
        f"#{t.replace(' ', '')}" for t in (video.get("seo_tags") or [])[:6]
    )
    file_path = _get_video_upload_path(video)

    if sim_mode:
        tt_id = f"tt_post_sim_{secrets.token_hex(4)}"
        log_activity(
            "TIKTOK",
            f"[Simulation] Published Video #{video['id']} to TikTok ({tt_id})",
            details={"video_id": video["id"], "tiktok_publish_id": tt_id},
        )
        return {"platform": "tiktok", "status": "published", "external_id": tt_id, "mode": "simulation"}

    file_size = file_path.stat().st_size
    init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {tiktok_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    body = {
        "post_info": {
            "title": title_and_tags[:150],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        },
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(init_url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        publish_id = data.get("publish_id", f"tt_{secrets.token_hex(4)}")
        upload_url = data.get("upload_url")
        if upload_url:
            video_bytes = file_path.read_bytes()
            up_resp = client.put(
                upload_url,
                headers={
                    "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                    "Content-Type": "video/mp4",
                },
                content=video_bytes,
            )
            up_resp.raise_for_status()

    log_activity("TIKTOK", f"Published Video #{video['id']} to TikTok (Publish ID: {publish_id})")
    return {"platform": "tiktok", "status": "published", "external_id": publish_id, "mode": "live"}


def publish_to_custom_webhook(video: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Sends video metadata & file path to an external automation webhook (n8n, Make.com, Zapier, X, LinkedIn)."""
    webhook_url = (settings.get("omnichannel_webhook_url") or "").strip()
    if not webhook_url:
        wh_id = f"webhook_sim_{secrets.token_hex(4)}"
        log_activity("WEBHOOK", f"[Simulation] Triggered omnichannel webhook for Video #{video['id']} ({wh_id})")
        return {"platform": "webhook", "status": "published", "external_id": wh_id, "mode": "simulation"}

    payload = {
        "video_id": video["id"],
        "title": video.get("seo_title"),
        "description": video.get("seo_description"),
        "tags": video.get("seo_tags"),
        "file_path": str(_get_video_upload_path(video)),
        "is_short": video.get("is_short"),
        "published_at": utc_now_iso(),
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(webhook_url, json=payload)
        resp.raise_for_status()
    return {"platform": "webhook", "status": "published", "external_id": "webhook_200_ok", "mode": "live"}


def publish_video_omnichannel(video_id: int, platforms: List[str], schedule: bool = False) -> Dict[str, Any]:
    """Publishes a video across all selected platforms (YouTube, Facebook, Instagram, TikTok, Webhook)."""
    video = get_video(video_id)
    if not video:
        raise ValueError(f"Video {video_id} not found")

    settings = get_settings()
    results: Dict[str, Any] = {}
    existing_platforms_raw = video.get("platforms_published")
    if isinstance(existing_platforms_raw, str):
        try:
            existing_platforms = json.loads(existing_platforms_raw)
        except Exception:
            existing_platforms = {}
    elif isinstance(existing_platforms_raw, dict):
        existing_platforms = dict(existing_platforms_raw)
    else:
        existing_platforms = {}

    file_hash = str(video.get("file_hash") or "")
    title = str(video.get("seo_title") or video.get("original_filename") or "")

    for plat in platforms:
        plat = plat.lower().strip()
        already_pub, prev_res = has_already_published_to_platform(
            platform=plat,
            file_hash=file_hash,
            title=title,
            existing_platforms_dict=existing_platforms,
        )
        if already_pub and prev_res:
            log_activity(
                "DEDUP_GUARD",
                f"Skipped duplicate upload of Video #{video_id} ('{title[:42]}') to {plat.upper()} — already published ({prev_res.get('external_id')})",
            )
            results[plat] = prev_res
            existing_platforms[plat] = prev_res
            continue

        try:
            if plat == "youtube":
                if not video.get("youtube_video_id"):
                    upload_video_for_precheck(video_id)
                yt_updated = verify_precheck_and_publish(video_id, publish_immediately=not schedule)
                res = {
                    "platform": "youtube",
                    "status": yt_updated.get("status", "published"),
                    "external_id": yt_updated.get("youtube_video_id"),
                }
            elif plat == "facebook":
                res = publish_to_facebook_page(video, settings)
            elif plat == "instagram":
                res = publish_to_instagram_reels(video, settings)
            elif plat == "tiktok":
                res = publish_to_tiktok(video, settings)
            elif plat == "webhook":
                res = publish_to_custom_webhook(video, settings)
            else:
                continue
            results[plat] = res
            existing_platforms[plat] = res
            record_published_to_platform(
                platform=plat,
                file_hash=file_hash,
                title=title,
                external_id=str(res.get("external_id") or ""),
            )
        except Exception as exc:
            log_activity("OMNICHANNEL", f"Failed publishing Video #{video_id} to {plat}: {exc}", level="ERROR")
            results[plat] = {"platform": plat, "status": "error", "error": str(exc)}


    conn = get_connection()
    try:
        conn.execute(
            "UPDATE videos SET platforms_published = ?, updated_at = ? WHERE id = ?",
            (json.dumps(existing_platforms), utc_now_iso(), video_id),
        )
        conn.commit()
    except Exception:
        pass
    conn.close()

    return {"video": get_video(video_id), "platform_results": results}
