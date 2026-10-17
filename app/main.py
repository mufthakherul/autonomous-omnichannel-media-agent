import asyncio
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, GENERATED_DIR, INCOMING_DIR, THUMBNAILS_DIR
from app.database import (
    get_connection,
    get_settings,
    get_video,
    init_db,
    list_videos,
    log_activity,
    update_settings,
    update_video,
    utc_now_iso,
)
from app.models import (
    AddApiKeysRequest,
    BulkSourcesRequest,
    CreateSourceRequest,
    GenerateStudioContentRequest,
    OmnichannelPublishRequest,
    UpdateVideoMetadataRequest,
)
from app.services.agent_worker import agent_worker
from app.services.collector import (
    generate_sample_video,
    import_bulk_sources,
    import_pdf_bundle,
    register_video_file,
    sync_all_active_sources,
    sync_source,
)
from app.services.comment_responder import (
    fetch_new_youtube_comments,
    post_comment_reply,
    seed_sample_comments,
    triage_pending_comments,
)
from app.services.content_generator import (
    discover_trending_topics,
    generate_ai_image_post,
    generate_ai_video_from_topic,
    list_studio_creations,
    publish_image_post_omnichannel,
)
from app.services.dedup_guard import get_dedup_stats
from app.services.inspector import inspect_video
from app.services.key_pool import (
    add_api_keys_bulk,
    delete_api_key,
    has_configured_gemini_keys,
    list_api_keys,
    reset_all_key_cooldowns,
    toggle_api_key,
)
from app.services.media_processor import auto_remediate_video, extract_thumbnail
from app.services.parallel_engine import execute_jobs_in_parallel, list_parallel_tasks
from app.services.social_publishers import publish_video_omnichannel
from app.services.youtube_service import (
    get_youtube_client,
    upload_video_for_precheck,
    verify_precheck_and_publish,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log_activity("SYSTEM", "Autonomous Omnichannel & Generative AI Agent initialized and ready.")
    loop_task = asyncio.create_task(agent_worker.background_loop())
    yield
    loop_task.cancel()


app = FastAPI(
    title="Autonomous Omnichannel & Generative AI Agent",
    description=(
        "Multi-talented parallel AI agent: collects copyright-free videos & assets from recursive Mother Folders, "
        "audits & remixes videos, researches trending news/events, generates AI Videos & AI Image Posts, "
        "rotates across multiple family Google Pro Gemini API keys, publishes omnichannel, and smartly replies to comments."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/thumbnails", StaticFiles(directory=str(THUMBNAILS_DIR)), name="thumbnails")
app.mount("/generated", StaticFiles(directory=str(GENERATED_DIR)), name="generated")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_dashboard():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/dashboard")
async def get_dashboard_overview():
    conn = get_connection()
    videos_all = list_videos(limit=200)
    api_keys_list = list_api_keys()
    healthy_gemini_keys = [
        k for k in api_keys_list if k.get("provider") == "gemini" and k.get("active") and k.get("status") == "healthy"
    ]
    dedup_stats = get_dedup_stats()

    counts = {
        "total_videos": len(videos_all),
        "ready_as_is": sum(1 for v in videos_all if v.get("can_upload_as_is")),
        "needs_remediation": sum(1 for v in videos_all if v.get("status") == "needs_remediation"),
        "remediated": sum(1 for v in videos_all if v.get("status") == "remediated"),
        "precheck_pending": sum(1 for v in videos_all if v.get("status") == "precheck_pending"),
        "published": sum(1 for v in videos_all if v.get("status") in ("published", "scheduled")),
        "comments_needing_reply": conn.execute(
            "SELECT COUNT(*) as c FROM comments WHERE status = 'pending_approval'"
        ).fetchone()["c"],
        "comments_replied": conn.execute(
            "SELECT COUNT(*) as c FROM comments WHERE status = 'replied'"
        ).fetchone()["c"],
        "comments_ignored": conn.execute(
            "SELECT COUNT(*) as c FROM comments WHERE status = 'ignored'"
        ).fetchone()["c"],
        "api_keys_total": len(api_keys_list),
        "api_keys_healthy": len(healthy_gemini_keys),
        "studio_creations_total": conn.execute("SELECT COUNT(*) as c FROM studio_creations").fetchone()["c"],
        "ai_videos_generated": conn.execute(
            "SELECT COUNT(*) as c FROM studio_creations WHERE creation_type = 'ai_video'"
        ).fetchone()["c"],
        "ai_images_generated": conn.execute(
            "SELECT COUNT(*) as c FROM studio_creations WHERE creation_type = 'ai_image_post'"
        ).fetchone()["c"],
        "duplicates_blocked": dedup_stats.get("duplicates_blocked_count", 0),
    }

    logs = [
        dict(r)
        for r in conn.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT 40").fetchall()
    ]
    conn.close()

    settings = get_settings()
    safe_settings = dict(settings)
    for secret_key in ("gemini_api_key", "pexels_api_key", "pixabay_api_key", "telegram_bot_token", "aws_secret_access_key"):
        val = str(safe_settings.get(secret_key) or "")
        safe_settings[f"{secret_key}_configured"] = bool(val and val != "your_gemini_api_key_here")
    if has_configured_gemini_keys():
        safe_settings["gemini_api_key_configured"] = True

    return {
        "counts": counts,
        "dedup_stats": dedup_stats,
        "agent": {
            "is_running_cycle": agent_worker.is_running_cycle,
            "last_cycle_at": agent_worker.last_cycle_at,
            "cycle_count": agent_worker.cycle_count,
            "auto_loop_enabled": settings.get("agent_auto_loop_enabled", False),
            "simulation_mode": settings.get("youtube_simulation_mode", True),
            "max_parallel_workers": int(settings.get("max_parallel_workers") or 4),
        },
        "api_keys": api_keys_list,
        "parallel_tasks": list_parallel_tasks(limit=25),
        "settings": safe_settings,
        "logs": logs,
    }


@app.get("/api/dedup/stats")
async def get_dedup_stats_endpoint():
    return get_dedup_stats()


@app.post("/api/agent/run-cycle")
async def trigger_agent_cycle():
    result = await asyncio.to_thread(agent_worker.run_single_cycle_sync)
    return result


@app.post("/api/agent/toggle-loop")
async def toggle_agent_loop():
    settings = get_settings()
    new_val = not bool(settings.get("agent_auto_loop_enabled", False))
    update_settings({"agent_auto_loop_enabled": new_val})
    log_activity(
        "AGENT",
        f"Autonomous background loop {'ENABLED' if new_val else 'PAUSED'} by user.",
    )
    return {"auto_loop_enabled": new_val}


# --- Multi-Account API Key Pool Endpoints ---
@app.get("/api/keys")
async def get_api_keys_endpoint(provider: Optional[str] = None):
    return {"keys": list_api_keys(provider=provider)}


@app.post("/api/keys/bulk")
async def add_bulk_api_keys_endpoint(req: AddApiKeysRequest):
    result = await asyncio.to_thread(add_api_keys_bulk, req.raw_keys, req.provider)
    return result


@app.post("/api/keys/{key_id}/toggle")
async def toggle_api_key_endpoint(key_id: int):
    updated = await asyncio.to_thread(toggle_api_key, key_id)
    if not updated:
        raise HTTPException(status_code=404, detail="API Key not found")
    return {"key": updated, "keys": list_api_keys()}


@app.delete("/api/keys/{key_id}")
async def delete_api_key_endpoint(key_id: int):
    await asyncio.to_thread(delete_api_key, key_id)
    return {"deleted": True, "keys": list_api_keys()}


@app.post("/api/keys/reset-cooldowns")
async def reset_key_cooldowns_endpoint():
    keys = await asyncio.to_thread(reset_all_key_cooldowns)
    return {"keys": keys}


# --- AI News, Video & Image Studio Endpoints ---
@app.get("/api/studio/creations")
async def get_studio_creations_endpoint(creation_type: Optional[str] = None):
    creations = await asyncio.to_thread(list_studio_creations, creation_type, 60)
    return {"creations": creations}


@app.post("/api/studio/discover-trends")
async def discover_trends_endpoint(payload: Optional[Dict[str, Any]] = None):
    niche = (payload or {}).get("niche")
    max_items = int((payload or {}).get("max_items") or 4)
    trends = await asyncio.to_thread(discover_trending_topics, niche, max_items)
    return {"trends": trends, "creations": list_studio_creations(limit=60)}


@app.post("/api/studio/generate")
async def generate_studio_content_endpoint(req: GenerateStudioContentRequest):
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="Topic or news headline is required")

    if req.creation_type == "both":
        # Run BOTH AI Image Post generation AND AI Video Short generation in PARALLEL across workers!
        jobs = [
            {
                "task_type": "AI_IMAGE_POST",
                "title": f"Studio Image Post: {req.topic[:42]}",
                "fn": lambda: generate_ai_image_post(
                    topic=req.topic,
                    niche=req.niche,
                    aspect_ratio="1:1" if req.aspect_ratio == "1:1" else req.aspect_ratio,
                    use_search_grounding=req.use_search_grounding,
                    auto_publish=req.auto_publish,
                    platforms=req.platforms,
                ),
            },
            {
                "task_type": "AI_VIDEO",
                "title": f"Studio Video Short: {req.topic[:42]}",
                "fn": lambda: generate_ai_video_from_topic(
                    topic=req.topic,
                    niche=req.niche,
                    aspect_ratio="9:16" if req.aspect_ratio == "1:1" else req.aspect_ratio,
                    use_search_grounding=req.use_search_grounding,
                    auto_publish=req.auto_publish,
                    platforms=req.platforms,
                ),
            },
        ]
        parallel_res = await asyncio.to_thread(execute_jobs_in_parallel, jobs, 2)
        created = [r.get("output") for r in parallel_res if r.get("output")]
        return {"created": created, "creations": list_studio_creations(limit=60)}

    elif req.creation_type == "ai_video":
        jobs = [
            {
                "task_type": "AI_VIDEO",
                "title": f"Studio Video: {req.topic[:45]}",
                "fn": lambda: generate_ai_video_from_topic(
                    topic=req.topic,
                    niche=req.niche,
                    aspect_ratio="9:16" if req.aspect_ratio == "1:1" else req.aspect_ratio,
                    use_search_grounding=req.use_search_grounding,
                    auto_publish=req.auto_publish,
                    platforms=req.platforms,
                ),
            }
        ]
        res = await asyncio.to_thread(execute_jobs_in_parallel, jobs, 1)
        item = res[0].get("output") if res else None
        return {"created": [item] if item else [], "creations": list_studio_creations(limit=60)}

    else:
        jobs = [
            {
                "task_type": "AI_IMAGE_POST",
                "title": f"Studio Image Post: {req.topic[:45]}",
                "fn": lambda: generate_ai_image_post(
                    topic=req.topic,
                    niche=req.niche,
                    aspect_ratio=req.aspect_ratio,
                    use_search_grounding=req.use_search_grounding,
                    auto_publish=req.auto_publish,
                    platforms=req.platforms,
                ),
            }
        ]
        res = await asyncio.to_thread(execute_jobs_in_parallel, jobs, 1)
        item = res[0].get("output") if res else None
        return {"created": [item] if item else [], "creations": list_studio_creations(limit=60)}


@app.post("/api/studio/creations/{creation_id}/publish")
async def publish_studio_creation_endpoint(creation_id: int, req: OmnichannelPublishRequest):
    conn = get_connection()
    row = conn.execute("SELECT * FROM studio_creations WHERE id = ?", (creation_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Studio creation not found")

    c_dict = dict(row)
    if c_dict["creation_type"] == "ai_video" and c_dict.get("linked_video_id"):
        omni = await asyncio.to_thread(
            publish_video_omnichannel, int(c_dict["linked_video_id"]), req.platforms, req.schedule
        )
        import json
        conn = get_connection()
        conn.execute(
            "UPDATE studio_creations SET platforms_published = ?, status = 'published' WHERE id = ?",
            (json.dumps(omni.get("platform_results") or {}), creation_id),
        )
        conn.commit()
        conn.close()
        return {"result": omni, "creations": list_studio_creations(limit=60)}
    else:
        updated = await asyncio.to_thread(publish_image_post_omnichannel, creation_id, req.platforms)
        return {"result": updated, "creations": list_studio_creations(limit=60)}


# --- Parallel Tasks Telemetry Endpoint ---
@app.get("/api/parallel/tasks")
async def get_parallel_tasks_endpoint():
    return {"tasks": list_parallel_tasks(limit=40)}


# --- Sources Endpoints ---
@app.get("/api/sources")
async def get_sources():
    conn = get_connection()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sources ORDER BY id DESC").fetchall()]
    conn.close()
    return {"sources": rows}


@app.post("/api/sources")
async def create_source(req: CreateSourceRequest):
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO sources (name, source_type, config_value, max_items_per_sync, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (req.name, req.source_type, req.config_value, req.max_items_per_sync, 1 if req.active else 0, utc_now_iso()),
    )
    source_id = cur.lastrowid
    conn.commit()
    row = dict(conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone())
    conn.close()
    log_activity("COLLECTOR", f"Added new video source '{req.name}' ({req.source_type}: {req.config_value})")
    return {"source": row}


@app.post("/api/sources/{source_id}/sync")
async def sync_single_source(source_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Source not found")
    collected_ids = await asyncio.to_thread(sync_source, dict(row))
    return {"collected_count": len(collected_ids), "video_ids": collected_ids}


@app.post("/api/sources/sync-all")
async def sync_all_sources_endpoint():
    collected_ids = await asyncio.to_thread(sync_all_active_sources)
    return {"collected_count": len(collected_ids), "video_ids": collected_ids}


@app.post("/api/sources/import-pdf")
async def import_pdf_bundle_endpoint(file: UploadFile = File(...)):
    pdf_bytes = await file.read()
    bundle_name = Path(file.filename or "PDF Bundle").stem
    result = await asyncio.to_thread(import_pdf_bundle, pdf_bytes, bundle_name)
    return result


@app.post("/api/sources/bulk")
async def import_bulk_sources_endpoint(req: BulkSourcesRequest):
    result = await asyncio.to_thread(
        import_bulk_sources,
        req.raw_links,
        req.max_items_per_sync,
        "Mother Folder",
    )
    return result


@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()
    conn.close()
    return {"deleted": True}


# --- Videos & Inspection Pipeline Endpoints ---
@app.get("/api/videos")
async def get_videos_endpoint(status: Optional[str] = None):
    return {"videos": list_videos(status=status, limit=150)}


@app.post("/api/videos/upload-local")
async def upload_local_video(file: UploadFile = File(...)):
    dest = INCOMING_DIR / (file.filename or "uploaded_video.mp4")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    vid_id = await asyncio.to_thread(register_video_file, dest, 1, "local", file.filename)
    if vid_id is None:
        raise HTTPException(status_code=400, detail="Duplicate video detected (matching SHA-256 hash already in database).")
    inspected = await asyncio.to_thread(inspect_video, vid_id)
    await asyncio.to_thread(extract_thumbnail, vid_id)
    return {"video": get_video(vid_id) or inspected}


@app.post("/api/videos/generate-sample")
async def create_sample_video_endpoint(preset: str = "clean_landscape"):
    vid_id = await asyncio.to_thread(generate_sample_video, preset)
    if not vid_id:
        raise HTTPException(status_code=500, detail="Could not create sample video")
    await asyncio.to_thread(inspect_video, vid_id)
    await asyncio.to_thread(extract_thumbnail, vid_id)
    return {"video": get_video(vid_id)}


@app.post("/api/videos/{video_id}/inspect")
async def inspect_video_endpoint(video_id: int):
    updated = await asyncio.to_thread(inspect_video, video_id)
    await asyncio.to_thread(extract_thumbnail, video_id)
    return {"video": get_video(video_id) or updated}


@app.post("/api/videos/{video_id}/remediate")
async def remediate_video_endpoint(video_id: int):
    updated = await asyncio.to_thread(auto_remediate_video, video_id)
    await asyncio.to_thread(extract_thumbnail, video_id)
    return {"video": get_video(video_id) or updated}


@app.put("/api/videos/{video_id}")
async def update_video_metadata_endpoint(video_id: int, req: UpdateVideoMetadataRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates.get("can_upload_as_is") is True:
        updates["status"] = "ready_as_is"
    updated = update_video(video_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Video not found")
    log_activity("INSPECTOR", f"Updated SEO metadata for video #{video_id} ('{updated.get('seo_title')}')")
    return {"video": updated}


@app.post("/api/videos/{video_id}/preupload")
async def preupload_video_endpoint(video_id: int):
    updated = await asyncio.to_thread(upload_video_for_precheck, video_id)
    return {"video": updated}


@app.post("/api/videos/{video_id}/publish")
async def publish_video_endpoint(video_id: int, schedule: bool = False):
    video = get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if not video.get("youtube_video_id"):
        await asyncio.to_thread(upload_video_for_precheck, video_id)
    updated = await asyncio.to_thread(verify_precheck_and_publish, video_id, not schedule)
    return {"video": updated}


@app.post("/api/videos/{video_id}/publish-omnichannel")
async def publish_video_omnichannel_endpoint(video_id: int, req: OmnichannelPublishRequest):
    result = await asyncio.to_thread(publish_video_omnichannel, video_id, req.platforms, req.schedule)
    return result


# --- Smart Comment Responder Endpoints ---
@app.get("/api/comments")
async def get_comments_endpoint(filter_type: Optional[str] = None):
    conn = get_connection()
    if filter_type == "needs_reply":
        rows = conn.execute("SELECT * FROM comments WHERE needs_reply = 1 ORDER BY priority DESC, id DESC").fetchall()
    elif filter_type == "ignored":
        rows = conn.execute("SELECT * FROM comments WHERE needs_reply = 0 ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM comments ORDER BY id DESC LIMIT 100").fetchall()
    conn.close()
    return {"comments": [dict(r) for r in rows]}


@app.post("/api/comments/seed-samples")
async def seed_sample_comments_endpoint():
    comments = await asyncio.to_thread(seed_sample_comments)
    return {"comments": comments}


@app.post("/api/comments/custom-test")
async def add_custom_test_comment(payload: Dict[str, Any]):
    import secrets

    author = payload.get("author_name") or "@ViewerTest"
    video_title = payload.get("video_title") or "4K Nature Showcase"
    comment_text = (payload.get("comment_text") or "").strip()
    if not comment_text:
        raise HTTPException(status_code=400, detail="comment_text is required")

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO comments (
            youtube_comment_id, youtube_video_id, video_title,
            author_name, comment_text, published_at, status, created_at
        )
        VALUES (?, 'yt_custom_test', ?, ?, ?, ?, 'pending_triage', ?)
        """,
        (f"ugx_custom_{secrets.token_hex(4)}", video_title, author, comment_text, utc_now_iso(), utc_now_iso()),
    )
    conn.commit()
    conn.close()
    comments = await asyncio.to_thread(triage_pending_comments)
    return {"comments": comments}


@app.post("/api/comments/sync-and-triage")
async def sync_and_triage_comments_endpoint():
    await asyncio.to_thread(fetch_new_youtube_comments)
    comments = await asyncio.to_thread(triage_pending_comments)
    return {"comments": comments}


@app.post("/api/comments/{comment_id}/reply")
async def reply_to_comment_endpoint(comment_id: int, payload: Optional[Dict[str, Any]] = None):
    custom_text = (payload or {}).get("reply_text")
    updated = await asyncio.to_thread(post_comment_reply, comment_id, custom_text)
    return {"comment": updated}


@app.post("/api/comments/{comment_id}/ignore")
async def ignore_comment_endpoint(comment_id: int):
    conn = get_connection()
    conn.execute("UPDATE comments SET status = 'ignored', needs_reply = 0 WHERE id = ?", (comment_id,))
    conn.commit()
    conn.close()
    return {"ignored": True}


# --- Settings & OAuth Endpoints ---
@app.get("/api/settings")
async def get_settings_endpoint():
    return {"settings": get_settings()}


@app.put("/api/settings")
async def update_settings_endpoint(payload: Dict[str, Any]):
    updated = update_settings(payload)
    log_activity("SETTINGS", "Updated agent configuration and API credentials.")
    return {"settings": updated}


@app.post("/api/youtube/authorize")
async def authorize_youtube_oauth():
    try:
        update_settings({"youtube_simulation_mode": False})
        await asyncio.to_thread(get_youtube_client, True)
        log_activity("YOUTUBE", "Successfully connected and authorized YouTube Channel OAuth 2.0!")
        return {"authorized": True}
    except Exception as exc:
        update_settings({"youtube_simulation_mode": True})
        raise HTTPException(status_code=400, detail=str(exc))
