import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import feedparser
import httpx

from app.config import BACKGROUNDS_DIR, INCOMING_DIR, OVERLAYS_DIR, SFX_DIR
from app.database import get_connection, get_settings, log_activity, utc_now_iso
from app.services.dedup_guard import (
    has_fingerprint,
    is_source_video_already_seen,
    record_source_video_registered,
)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".aac"}
OVERLAY_EXTENSIONS = {".png", ".webp"}


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def register_video_file(
    file_path: Path,
    source_id: Optional[int],
    source_type: str,
    external_id: Optional[str] = None,
    source_url: Optional[str] = None,
) -> Optional[int]:
    """
    Registers a video file in the database ONLY if its SHA-256 hash, external_id, source_url,
    and filename have NEVER been collected before (even if previously deleted).
    """
    if not file_path.exists() or not file_path.is_file():
        return None

    file_hash = compute_sha256(file_path)
    if has_fingerprint("file_sha256", file_hash) or is_source_video_already_seen(
        external_id=external_id,
        source_url=source_url,
        filename=file_path.name if source_type != "ai_generated" else None,
    ):
        return None

    file_size_mb = round(file_path.stat().st_size / (1024 * 1024), 2)
    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM videos WHERE file_hash = ? OR (external_id = ? AND external_id != '')",
        (file_hash, external_id or ""),
    ).fetchone()
    if existing:
        conn.close()
        record_source_video_registered(
            existing["id"], file_path.name, file_hash, external_id, source_url
        )
        return None

    now = utc_now_iso()
    cur = conn.execute(
        """
        INSERT INTO videos (
            source_id, source_type, external_id, source_url,
            original_filename, local_path, file_hash, file_size_mb,
            status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?)
        """,
        (
            source_id,
            source_type,
            external_id or file_path.name,
            source_url or "",
            file_path.name,
            str(file_path.resolve()),
            file_hash,
            file_size_mb,
            now,
            now,
        ),
    )
    video_id = cur.lastrowid
    if source_id:
        conn.execute(
            "UPDATE sources SET total_collected = total_collected + 1, last_synced_at = ? WHERE id = ?",
            (now, source_id),
        )
    conn.commit()
    conn.close()

    if video_id:
        record_source_video_registered(
            int(video_id), file_path.name, file_hash, external_id, source_url
        )

    log_activity(
        "COLLECTOR",
        f"Collected unique video '{file_path.name}' ({file_size_mb} MB, SHA-256: {file_hash[:10]}...) from {source_type}",
        details={"video_id": video_id, "source_type": source_type, "file_hash": file_hash[:12]},
    )
    return video_id



def download_stream_to_incoming(url: str, filename_hint: str, headers: Optional[Dict[str, str]] = None) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", filename_hint)
    if not any(safe_name.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
        safe_name += ".mp4"
    dest = INCOMING_DIR / safe_name
    counter = 1
    while dest.exists():
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        dest = INCOMING_DIR / f"{stem}_{counter}{suffix}"
        counter += 1

    with httpx.Client(follow_redirects=True, timeout=60.0, headers=headers) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=512 * 1024):
                    f.write(chunk)
    return dest


def sync_local_folder(source_id: int, folder_path: str, max_items: int) -> List[int]:
    path = Path(folder_path)
    if not path.exists() or not path.is_dir():
        return []
    collected: List[int] = []
    for item in sorted(path.iterdir()):
        if len(collected) >= max_items:
            break
        if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS:
            vid_id = register_video_file(item, source_id, "local", external_id=item.name)
            if vid_id:
                collected.append(vid_id)
    return collected


def sync_pexels_source(source_id: int, query: str, max_items: int) -> List[int]:
    settings = get_settings()
    api_key = settings.get("pexels_api_key", "").strip()
    if not api_key:
        log_activity("COLLECTOR", "Skipped Pexels source sync: PEXELS_API_KEY not configured", level="WARN")
        return []

    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": max_items, "size": "medium"}
    collected: List[int] = []

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

    for video in data.get("videos", []):
        if len(collected) >= max_items:
            break
        ext_id = f"pexels_{video.get('id')}"
        video_files = video.get("video_files", [])
        hd_files = [vf for vf in video_files if vf.get("file_type") == "video/mp4" and vf.get("width", 0) >= 720]
        chosen = hd_files[0] if hd_files else (video_files[0] if video_files else None)
        if not chosen or not chosen.get("link"):
            continue
        dest = download_stream_to_incoming(chosen["link"], f"{ext_id}.mp4")
        vid_id = register_video_file(dest, source_id, "pexels", external_id=ext_id, source_url=video.get("url"))
        if vid_id:
            collected.append(vid_id)
    return collected


def sync_pixabay_source(source_id: int, query: str, max_items: int) -> List[int]:
    settings = get_settings()
    api_key = settings.get("pixabay_api_key", "").strip()
    if not api_key:
        log_activity("COLLECTOR", "Skipped Pixabay source sync: PIXABAY_API_KEY not configured", level="WARN")
        return []

    url = "https://pixabay.com/api/videos/"
    params = {"key": api_key, "q": query, "per_page": max(3, max_items)}
    collected: List[int] = []

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    for hit in data.get("hits", []):
        if len(collected) >= max_items:
            break
        ext_id = f"pixabay_{hit.get('id')}"
        videos_dict = hit.get("videos", {})
        medium_or_large = videos_dict.get("medium") or videos_dict.get("large") or videos_dict.get("small")
        if not medium_or_large or not medium_or_large.get("url"):
            continue
        dest = download_stream_to_incoming(medium_or_large["url"], f"{ext_id}.mp4")
        vid_id = register_video_file(dest, source_id, "pixabay", external_id=ext_id, source_url=hit.get("pageURL"))
        if vid_id:
            collected.append(vid_id)
    return collected


def sync_rss_source(source_id: int, feed_url: str, max_items: int) -> List[int]:
    feed = feedparser.parse(feed_url)
    collected: List[int] = []
    for entry in feed.entries:
        if len(collected) >= max_items:
            break
        video_url = None
        for enc in entry.get("enclosures", []):
            if "video" in enc.get("type", "") or any(enc.get("href", "").lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
                video_url = enc.get("href")
                break
        if not video_url and "media_content" in entry:
            for mc in entry["media_content"]:
                if "video" in mc.get("type", "") or any(mc.get("url", "").lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
                    video_url = mc.get("url")
                    break
        if video_url:
            title_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", entry.get("title", "rss_video"))[:40]
            dest = download_stream_to_incoming(video_url, f"{title_slug}.mp4")
            vid_id = register_video_file(dest, source_id, "rss", external_id=entry.get("id", title_slug), source_url=video_url)
            if vid_id:
                collected.append(vid_id)
    return collected


def sync_telegram_source(source_id: int, channel_or_chat_id: str, max_items: int) -> List[int]:
    settings = get_settings()
    bot_token = settings.get("telegram_bot_token", "").strip()
    if not bot_token:
        log_activity("COLLECTOR", "Skipped Telegram sync: TELEGRAM_BOT_TOKEN not configured", level="WARN")
        return []

    base_url = f"https://api.telegram.org/bot{bot_token}"
    collected: List[int] = []
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"{base_url}/getUpdates", params={"limit": 30})
        resp.raise_for_status()
        updates = resp.json().get("result", [])

        for upd in updates:
            if len(collected) >= max_items:
                break
            msg = upd.get("message") or upd.get("channel_post") or {}
            chat = msg.get("chat", {})
            if channel_or_chat_id and str(chat.get("id")) != channel_or_chat_id and chat.get("username") != channel_or_chat_id.lstrip("@"):
                continue
            video_obj = msg.get("video") or msg.get("document")
            if not video_obj or not video_obj.get("file_id"):
                continue
            file_id = video_obj["file_id"]
            f_resp = client.get(f"{base_url}/getFile", params={"file_id": file_id}).json()
            file_path = f_resp.get("result", {}).get("file_path")
            if not file_path:
                continue
            dl_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            dest = download_stream_to_incoming(dl_url, f"tg_{msg.get('message_id', 'video')}.mp4")
            vid_id = register_video_file(dest, source_id, "telegram", external_id=file_id, source_url=dl_url)
            if vid_id:
                collected.append(vid_id)
    return collected


def sync_s3_source(source_id: int, bucket_and_prefix: str, max_items: int) -> List[int]:
    import boto3

    settings = get_settings()
    parts = bucket_and_prefix.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""

    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.get("aws_access_key_id") or None,
        aws_secret_access_key=settings.get("aws_secret_access_key") or None,
        region_name=settings.get("aws_region") or "us-east-1",
    )
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=max_items * 3)
    collected: List[int] = []
    for obj in resp.get("Contents", []):
        if len(collected) >= max_items:
            break
        key = obj["Key"]
        if not any(key.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
            continue
        dest = INCOMING_DIR / Path(key).name
        if not dest.exists():
            s3.download_file(bucket, key, str(dest))
        vid_id = register_video_file(dest, source_id, "s3", external_id=key, source_url=f"s3://{bucket}/{key}")
        if vid_id:
            collected.append(vid_id)
    return collected


def _parse_gdrive_folder_entries(client: httpx.Client, folder_id: str) -> List[tuple[str, str]]:
    """
    Lists (entry_id, entry_name) inside a public Google Drive folder.
    Uses embeddedfolderview first, and automatically falls back to main folder HTML for 1000+ item folders.
    """
    url = f"https://drive.google.com/embeddedfolderview?id={folder_id}#list"
    try:
        resp = client.get(url, timeout=14.0)
        if resp.status_code == 200:
            entries = re.findall(
                r'id="entry-([a-zA-Z0-9_-]+)".*?class="flip-entry-title">([^<]+)<',
                resp.text,
                re.S,
            )
            if entries:
                return entries
    except Exception:
        pass

    # Fast fallback for massive 1,000+ file folders where embeddedfolderview times out
    try:
        main_resp = client.get(f"https://drive.google.com/drive/folders/{folder_id}", timeout=15.0)
        if main_resp.status_code == 200:
            unescaped = main_resp.text.replace("&quot;", '"').replace("\\x22", '"')
            matches = re.findall(
                r'"([a-zA-Z0-9_-]{28,38})"[^[]{0,120}"([^"]+\.(?:mp4|mov|mkv|mp3|wav|png|pdf))"',
                unescaped,
                re.I,
            )
            return list(dict.fromkeys(matches))
    except Exception:
        pass
    return []


def _download_gdrive_asset_if_needed(client: httpx.Client, file_id: str, filename: str, dest_dir: Path) -> None:
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", f"{file_id[:8]}_{filename}")
    dest = dest_dir / safe_name
    if dest.exists() or len(list(dest_dir.iterdir())) >= 25:
        return
    try:
        dl_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        with client.stream("GET", dl_url, timeout=30.0) as resp:
            if resp.status_code == 200:
                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=256 * 1024):
                        f.write(chunk)
    except Exception:
        pass


def _discover_gdrive_videos_recursive(
    client: httpx.Client,
    folder_id: str,
    known_external_ids: set,
    max_needed: int,
    depth: int = 0,
    visited_folders: Optional[set] = None,
) -> List[tuple[str, str]]:
    """
    Recursively crawls public Google Drive Mother Folders & Subfolders (up to depth 8)
    to collect videos, creative editing assets (SFX, PNG overlays), and nested PDF link sheets.
    """
    if visited_folders is None:
        visited_folders = set()
    if depth > 8 or max_needed <= 0 or folder_id in visited_folders:
        return []
    visited_folders.add(folder_id)

    entries = _parse_gdrive_folder_entries(client, folder_id)
    found_videos: List[tuple[str, str]] = []
    subfolders: List[tuple[str, str]] = []

    for entry_id, entry_name in entries:
        clean_name = entry_name.strip()
        lower_name = clean_name.lower()

        if any(lower_name.endswith(ext) for ext in VIDEO_EXTENSIONS):
            if entry_id not in known_external_ids and not is_source_video_already_seen(
                external_id=entry_id, filename=clean_name
            ):
                found_videos.append((entry_id, clean_name))
                known_external_ids.add(entry_id)
                if len(found_videos) >= max_needed:
                    return found_videos
        elif any(lower_name.endswith(ext) for ext in AUDIO_EXTENSIONS):
            _download_gdrive_asset_if_needed(client, entry_id, clean_name, SFX_DIR)
        elif any(lower_name.endswith(ext) for ext in OVERLAY_EXTENSIONS):
            _download_gdrive_asset_if_needed(client, entry_id, clean_name, OVERLAYS_DIR)
        elif lower_name.endswith(".pdf"):
            # Automatically extract Google Drive folder links embedded inside nested PDFs!
            try:
                pdf_resp = client.get(f"https://drive.google.com/uc?export=download&id={entry_id}", timeout=20.0)
                if pdf_resp.status_code == 200 and pdf_resp.content.startswith(b"%PDF"):
                    for link in extract_links_from_pdf_bytes(pdf_resp.content):
                        m = re.search(r"/folders/([a-zA-Z0-9_-]{15,})", link)
                        if m and m.group(1) not in visited_folders:
                            subfolders.append((m.group(1), f"Nested PDF Link ({clean_name})"))
            except Exception:
                pass
        elif "." not in clean_name or not any(
            lower_name.endswith(ext) for ext in (".xlsx", ".docx", ".txt", ".zip", ".rar", ".jpg", ".jpeg")
        ):
            subfolders.append((entry_id, clean_name))

    for sub_id, _ in subfolders:
        if len(found_videos) >= max_needed:
            break
        more = _discover_gdrive_videos_recursive(
            client,
            sub_id,
            known_external_ids,
            max_needed - len(found_videos),
            depth + 1,
            visited_folders,
        )
        found_videos.extend(more)

    return found_videos


def sync_gdrive_source(source_id: int, folder_or_file_url: str, max_items: int) -> List[int]:
    """Supports Google Drive public mother folders (deep recursive crawl), direct file links/IDs, or local paths."""
    if Path(folder_or_file_url).exists():
        return sync_local_folder(source_id, folder_or_file_url, max_items)

    folder_match = re.search(r"/folders/([a-zA-Z0-9_-]{15,})", folder_or_file_url)
    file_match = re.search(r"(?:id=|/d/)([a-zA-Z0-9_-]{15,})", folder_or_file_url)

    conn = get_connection()
    known_ids = {
        r["external_id"]
        for r in conn.execute("SELECT external_id FROM videos WHERE external_id IS NOT NULL").fetchall()
    }
    known_ids.update(
        r["fingerprint_value"]
        for r in conn.execute(
            "SELECT fingerprint_value FROM dedup_registry WHERE fingerprint_type = 'source_external_id'"
        ).fetchall()
    )
    conn.close()


    target_id = (
        folder_match.group(1)
        if folder_match
        else (file_match.group(1) if file_match else folder_or_file_url.strip())
    )
    is_folder = bool(folder_match) or ("/file/d/" not in folder_or_file_url and "export=download" not in folder_or_file_url)

    if is_folder:
        collected: List[int] = []
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            candidates = _discover_gdrive_videos_recursive(client, target_id, known_ids, max_items)
        for file_id, filename in candidates:
            dl_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            try:
                dest = download_stream_to_incoming(dl_url, f"gdrive_{file_id[:8]}_{filename}")
                # Also copy motion background clips to BACKGROUNDS_DIR if from a background pack
                if "background" in filename.lower() and len(list(BACKGROUNDS_DIR.iterdir())) < 15:
                    shutil.copy2(dest, BACKGROUNDS_DIR / dest.name)
                vid_id = register_video_file(
                    dest,
                    source_id,
                    "gdrive",
                    external_id=file_id,
                    source_url=f"https://drive.google.com/file/d/{file_id}/view",
                )
                if vid_id:
                    collected.append(vid_id)
            except Exception as exc:
                log_activity("COLLECTOR", f"Failed downloading GDrive file {filename} ({file_id}): {exc}", level="WARN")
        return collected

    dl_url = f"https://drive.google.com/uc?export=download&id={target_id}"
    dest = download_stream_to_incoming(dl_url, f"gdrive_{target_id[:10]}.mp4")
    vid_id = register_video_file(dest, source_id, "gdrive", external_id=target_id, source_url=dl_url)
    return [vid_id] if vid_id else []


def extract_links_from_pdf_bytes(data: bytes) -> List[str]:
    """Extracts all hyperlink /URI annotations and URLs embedded inside a PDF file."""
    import zlib

    all_text = data.decode("latin1", errors="ignore")
    for m in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", data, re.S):
        try:
            all_text += "\n" + zlib.decompress(m.group(1)).decode("latin1", errors="ignore")
        except Exception:
            pass
    uris = re.findall(r"/URI\s*\(([^)]+)\)", all_text)
    raw_urls = re.findall(r"https?://[^\)\s>\"\'\\]+", all_text)
    return list(dict.fromkeys(uris + raw_urls))


def _fetch_gdrive_folder_title(url: str, fallback_label: str) -> str:
    try:
        with httpx.Client(follow_redirects=True, timeout=10.0) as client:
            r = client.get(url)
            m = re.search(r"<title>(.*?)</title>", r.text, re.S)
            if m:
                title = (
                    m.group(1)
                    .strip()
                    .replace(" - Google ড্রাইভ", "")
                    .replace(" - Google Drive", "")
                    .strip()
                )
                if title and "Error 404" not in title:
                    return title
    except Exception:
        pass
    return fallback_label


def import_bulk_sources(raw_links: str, max_items_per_sync: int = 4, prefix: str = "Mother Folder") -> Dict[str, Any]:
    """Parses space-, comma-, or newline-separated URLs, auto-detects folder titles, and adds them to SQLite."""
    urls = re.findall(r"https?://[^\s,;\"\'<>]+", raw_links)
    urls = list(dict.fromkeys(urls))
    added_sources: List[Dict[str, Any]] = []
    skipped_links: List[str] = []

    conn = get_connection()
    existing_configs = {r["config_value"] for r in conn.execute("SELECT config_value FROM sources").fetchall()}
    now = utc_now_iso()

    for idx, link in enumerate(urls, 1):
        norm_link = link.replace("http://drive.google.com", "https://drive.google.com")
        if norm_link in existing_configs or link in existing_configs:
            continue
        if "drive.google.com" in norm_link:
            stype = "gdrive"
            folder_title = _fetch_gdrive_folder_title(norm_link, f"Google Drive #{idx}")
            src_name = f"{prefix} — {folder_title}"
        elif "t.me/" in norm_link or "telegram" in norm_link.lower():
            stype = "telegram"
            src_name = f"{prefix} — Telegram ({norm_link.split('/')[-1]})"
        elif any(norm_link.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
            stype = "url"
            src_name = f"{prefix} — Direct Video #{idx}"
        else:
            skipped_links.append(norm_link)
            continue

        cur = conn.execute(
            """
            INSERT INTO sources (name, source_type, config_value, max_items_per_sync, active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (src_name, stype, norm_link, max_items_per_sync, now),
        )
        existing_configs.add(norm_link)
        added_sources.append({"id": cur.lastrowid, "name": src_name, "source_type": stype, "config_value": norm_link})

    conn.commit()
    conn.close()
    log_activity(
        "COLLECTOR",
        f"Bulk-imported {len(added_sources)} source(s) (from {len(urls)} link(s) provided).",
        details={"added_count": len(added_sources), "skipped": skipped_links},
    )
    return {"total_links_found": len(urls), "added_sources": added_sources, "unsupported_or_external_links": skipped_links}


def import_pdf_bundle(pdf_bytes: bytes, bundle_name: str = "PDF Bundle") -> Dict[str, Any]:
    """Extracts video source links from a PDF bundle and registers active sources in SQLite."""
    links = extract_links_from_pdf_bytes(pdf_bytes)
    return import_bulk_sources("\n".join(links), max_items_per_sync=3, prefix=bundle_name)


def sync_direct_url_source(source_id: int, url: str, max_items: int) -> List[int]:
    dest = download_stream_to_incoming(url, Path(url.split("?")[0]).name or "web_video.mp4")
    vid_id = register_video_file(dest, source_id, "url", external_id=url, source_url=url)
    return [vid_id] if vid_id else []


def sync_source(source_row: Dict[str, Any]) -> List[int]:
    source_id = source_row["id"]
    stype = source_row["source_type"]
    cfg = source_row["config_value"]
    max_items = int(source_row.get("max_items_per_sync") or 5)

    try:
        if stype == "local":
            ids = sync_local_folder(source_id, cfg, max_items)
        elif stype == "pexels":
            ids = sync_pexels_source(source_id, cfg, max_items)
        elif stype == "pixabay":
            ids = sync_pixabay_source(source_id, cfg, max_items)
        elif stype == "rss":
            ids = sync_rss_source(source_id, cfg, max_items)
        elif stype == "telegram":
            ids = sync_telegram_source(source_id, cfg, max_items)
        elif stype == "s3":
            ids = sync_s3_source(source_id, cfg, max_items)
        elif stype == "gdrive":
            ids = sync_gdrive_source(source_id, cfg, max_items)
        elif stype == "url":
            ids = sync_direct_url_source(source_id, cfg, max_items)
        else:
            ids = []

        conn = get_connection()
        conn.execute("UPDATE sources SET last_synced_at = ? WHERE id = ?", (utc_now_iso(), source_id))
        conn.commit()
        conn.close()
        return ids
    except Exception as exc:
        log_activity(
            "COLLECTOR",
            f"Error syncing source '{source_row.get('name')}' ({stype}): {exc}",
            level="ERROR",
        )
        return []


def sync_all_active_sources() -> List[int]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM sources WHERE active = 1").fetchall()
    conn.close()
    all_new_ids: List[int] = []
    for row in rows:
        all_new_ids.extend(sync_source(dict(row)))
    return all_new_ids


def generate_sample_video(preset: str = "clean_landscape") -> Optional[int]:
    """Generates a real MP4 test video using FFmpeg (or fallback binary) for testing the pipeline."""
    ffmpeg_bin = shutil.which("ffmpeg")
    timestamp_tag = utc_now_iso().replace(":", "").replace("-", "").replace(".", "")[:15]
    if preset == "vertical_short":
        filename = f"sample_short_{timestamp_tag}.mp4"
        resolution = "1080x1920"
        duration = "8"
        color = "0x1e3a8a"
    elif preset == "needs_fix_silent":
        filename = f"sample_needs_fix_{timestamp_tag}.mp4"
        resolution = "1280x720"
        duration = "6"
        color = "0x7c2d12"
    else:
        filename = f"sample_nature_4k_{timestamp_tag}.mp4"
        resolution = "1920x1080"
        duration = "10"
        color = "0x065f46"

    out_path = INCOMING_DIR / filename
    if ffmpeg_bin:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={resolution}:rate=30:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=44100:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(out_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # Fallback to simple color source if testsrc2 not available
            cmd_simple = [
                ffmpeg_bin,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s={resolution}:r=30:d={duration}",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r=44100:cl=stereo:d={duration}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(out_path),
            ]
            subprocess.run(cmd_simple, capture_output=True, text=True)

    if not out_path.exists():
        # Create a minimal valid MP4 container stub if ffmpeg is not on PATH
        minimal_mp4 = (
            b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
            b"\x00\x00\x00\x08free" + os.urandom(2048)
        )
        out_path.write_bytes(minimal_mp4)

    return register_video_file(out_path, source_id=1, source_type="local", external_id=filename)
