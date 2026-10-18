import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.database import get_connection, get_settings, list_videos, log_activity, utc_now_iso
from app.services.collector import sync_source
from app.services.comment_responder import fetch_new_youtube_comments, triage_pending_comments
from app.services.content_generator import (
    discover_trending_topics,
    generate_ai_image_post,
    generate_ai_video_from_topic,
)
from app.services.dedup_guard import is_news_or_topic_already_generated
from app.services.gdrive_storage import sync_all_data_and_resources_to_gdrive
from app.services.inspector import inspect_video
from app.services.media_processor import auto_remediate_video, extract_thumbnail
from app.services.parallel_engine import execute_jobs_in_parallel
from app.services.social_publishers import publish_video_omnichannel
from app.services.youtube_service import upload_video_for_precheck


class AutonomousAgentWorker:
    def __init__(self) -> None:
        self.is_running_cycle: bool = False
        self.last_cycle_at: str = ""
        self.cycle_count: int = 0
        self._task: asyncio.Task | None = None

    def run_single_cycle_sync(self) -> Dict[str, Any]:
        """
        Executes a complete end-to-end autonomous agent cycle using the Parallel Multi-Task Execution Engine
        and Multi-Account Gemini API Key Pool.
        """
        if self.is_running_cycle:
            return {"status": "already_running"}

        self.is_running_cycle = True
        summary = {
            "collected": 0,
            "inspected": 0,
            "ready_as_is": 0,
            "remediated": 0,
            "trends_discovered": 0,
            "ai_posts_generated": 0,
            "preuploaded": 0,
            "published_or_scheduled": 0,
            "comments_triaged": 0,
            "parallel_workers": 4,
        }

        try:
            settings = get_settings()
            workers = max(1, min(16, int(settings.get("max_parallel_workers") or 4)))
            summary["parallel_workers"] = workers
            log_activity(
                "AGENT",
                f"Starting Parallel Autonomous Cycle across {workers} concurrent workers...",
            )

            # --- STAGE 1: Parallel Source Collection + Live Trending News Discovery ---
            conn = get_connection()
            active_sources = [
                dict(r)
                for r in conn.execute("SELECT * FROM sources WHERE active = 1 ORDER BY id ASC LIMIT 8").fetchall()
            ]
            conn.close()

            stage1_jobs: List[Dict[str, Any]] = []
            for src in active_sources:
                def _make_sync_fn(s: Dict[str, Any]):
                    def _run():
                        ids = sync_source(s)
                        return {"collected_ids": ids, "summary_text": f"Collected {len(ids)} videos from {s['name']}"}
                    return _run

                stage1_jobs.append(
                    {
                        "task_type": "SYNC_SOURCE",
                        "title": f"Sync Source: {src['name']}",
                        "fn": _make_sync_fn(src),
                    }
                )

            if settings.get("auto_generate_trending_posts", True):
                def _discover_fn():
                    trends = discover_trending_topics(max_items=3)
                    return {"trends": trends, "summary_text": f"Discovered {len(trends)} breaking news trends"}

                stage1_jobs.append(
                    {
                        "task_type": "TREND_RESEARCH",
                        "title": "Discover Live Trending News & Events",
                        "fn": _discover_fn,
                    }
                )

            stage1_results = execute_jobs_in_parallel(stage1_jobs, max_workers=workers)
            discovered_trends: List[Dict[str, Any]] = []
            for res in stage1_results:
                out = res.get("output") or {}
                if "collected_ids" in out:
                    summary["collected"] += len(out["collected_ids"])
                if "trends" in out:
                    discovered_trends = out["trends"]
                    summary["trends_discovered"] += len(discovered_trends)

            # --- STAGE 2: Parallel Gemini Multimodal Video Inspection + AI Studio Media Generation ---
            discovered_videos = list_videos(status="discovered", limit=12)
            stage2_jobs: List[Dict[str, Any]] = []

            for v in discovered_videos:
                def _make_inspect_fn(vid_id: int, fname: str):
                    def _run():
                        inspected = inspect_video(vid_id)
                        extract_thumbnail(vid_id)
                        ready = bool(inspected.get("can_upload_as_is"))
                        return {
                            "ready": ready,
                            "summary_text": f"Inspected #{vid_id} ({'Ready As-Is' if ready else 'Needs Remix'}) — Score {inspected.get('readiness_score', 0)}/100",
                        }
                    return _run

                stage2_jobs.append(
                    {
                        "task_type": "AI_INSPECT",
                        "title": f"Inspect Video #{v['id']}: {v['original_filename'][:40]}",
                        "fn": _make_inspect_fn(v["id"], v["original_filename"]),
                    }
                )

            if settings.get("auto_generate_trending_posts", True) and discovered_trends:
                fresh_trends = [
                    t
                    for t in discovered_trends
                    if not is_news_or_topic_already_generated(t["headline"], creation_type="ai_image_post")[0]
                ]
                if fresh_trends:
                    top_trend = fresh_trends[0]
                    def _gen_trend_post_fn():
                        img_post = generate_ai_image_post(
                            topic=top_trend["headline"],
                            niche=top_trend.get("niche", "Trending News"),
                            aspect_ratio="1:1",
                            use_search_grounding=True,
                            auto_publish=True,
                        )
                        return {
                            "created_post": img_post,
                            "summary_text": f"Generated & Published AI News Image Post: {top_trend['headline'][:45]}",
                        }

                    stage2_jobs.append(
                        {
                            "task_type": "AI_IMAGE_POST",
                            "title": f"Generate AI News Post: {top_trend['headline'][:42]}",
                            "fn": _gen_trend_post_fn,
                        }
                    )

            stage2_results = execute_jobs_in_parallel(stage2_jobs, max_workers=workers)
            for res in stage2_results:
                out = res.get("output") or {}
                if "ready" in out:
                    summary["inspected"] += 1
                    if out["ready"]:
                        summary["ready_as_is"] += 1
                if "created_post" in out and not (out["created_post"] or {}).get("dedup_skipped"):
                    summary["ai_posts_generated"] += 1

            # --- STAGE 3: Parallel FFmpeg Copyright-Safe Remix Engine ---
            always_transform = bool(settings.get("always_apply_copyright_safe_edit", True))
            stage3_jobs: List[Dict[str, Any]] = []
            if settings.get("auto_remediate_failed_videos", True) or always_transform:
                needs_fix_videos = list_videos(status="needs_remediation", limit=6)
                if always_transform:
                    needs_fix_videos.extend(
                        [
                            v
                            for v in list_videos(status="ready_as_is", limit=6)
                            if not v.get("remediated_path") and v.get("source_type") != "ai_generated"
                        ]
                    )
                for v in needs_fix_videos:
                    def _make_remix_fn(vid_id: int):
                        def _run():
                            auto_remediate_video(vid_id, ["copyright_safe_remix", "transcode_h264"])
                            extract_thumbnail(vid_id)
                            return {"remediated": True, "summary_text": f"Applied Copyright-Safe Remix to Video #{vid_id}"}
                        return _run

                    stage3_jobs.append(
                        {
                            "task_type": "FFMPEG_REMIX",
                            "title": f"Copyright-Safe Remix Video #{v['id']}",
                            "fn": _make_remix_fn(v["id"]),
                        }
                    )

            stage3_results = execute_jobs_in_parallel(stage3_jobs, max_workers=workers)
            for res in stage3_results:
                if (res.get("output") or {}).get("remediated"):
                    summary["remediated"] += 1

            # --- STAGE 4: Parallel Omnichannel Publishing + Smart Comment Triage ---
            daily_limit = int(settings.get("daily_upload_limit") or 4)
            today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            conn = get_connection()
            uploaded_today = conn.execute(
                "SELECT COUNT(*) as cnt FROM videos WHERE updated_at LIKE ? AND status IN ('precheck_pending', 'published', 'scheduled')",
                (f"{today_prefix}%",),
            ).fetchone()["cnt"]
            conn.close()

            remaining_quota = max(0, daily_limit - uploaded_today)
            ready_candidates = list_videos(status="remediated", limit=remaining_quota)
            if len(ready_candidates) < remaining_quota:
                ready_candidates.extend(
                    list_videos(status="ready_as_is", limit=remaining_quota - len(ready_candidates))
                )

            target_platforms = settings.get("target_platforms") or ["youtube", "facebook", "instagram", "tiktok"]
            stage4_jobs: List[Dict[str, Any]] = []

            for v in ready_candidates[:remaining_quota]:
                def _make_publish_fn(vid_id: int, title_str: str):
                    def _run():
                        upload_video_for_precheck(vid_id)
                        omni = publish_video_omnichannel(vid_id, target_platforms, schedule=False)
                        updated_v = omni.get("video") or {}
                        pub_ok = updated_v.get("status") in ("published", "scheduled")
                        return {
                            "preuploaded": True,
                            "published": pub_ok,
                            "summary_text": f"Published Video #{vid_id} across {', '.join(target_platforms)}",
                        }
                    return _run

                stage4_jobs.append(
                    {
                        "task_type": "OMNI_PUBLISH",
                        "title": f"Omnichannel Publish Video #{v['id']}: {(v.get('seo_title') or v['original_filename'])[:38]}",
                        "fn": _make_publish_fn(v["id"], v.get("seo_title") or v["original_filename"]),
                    }
                )

            # Also include any already precheck_pending videos
            for v in list_videos(status="precheck_pending", limit=6):
                def _make_verify_pub_fn(vid_id: int):
                    def _run():
                        omni = publish_video_omnichannel(vid_id, target_platforms, schedule=False)
                        updated_v = omni.get("video") or {}
                        pub_ok = updated_v.get("status") in ("published", "scheduled")
                        return {
                            "published": pub_ok,
                            "summary_text": f"Verified Content ID & Published Video #{vid_id}",
                        }
                    return _run

                stage4_jobs.append(
                    {
                        "task_type": "OMNI_PUBLISH",
                        "title": f"Verify & Publish Video #{v['id']}",
                        "fn": _make_verify_pub_fn(v["id"]),
                    }
                )

            def _comments_job_fn():
                fetch_new_youtube_comments()
                triaged = triage_pending_comments()
                return {
                    "triaged_count": len(triaged),
                    "summary_text": f"Triaged {len(triaged)} viewer comments",
                }

            stage4_jobs.append(
                {
                    "task_type": "COMMENT_TRIAGE",
                    "title": "Fetch & Smart-Triage Viewer Comments",
                    "fn": _comments_job_fn,
                }
            )

            stage4_results = execute_jobs_in_parallel(stage4_jobs, max_workers=workers)
            for res in stage4_results:
                out = res.get("output") or {}
                if out.get("preuploaded"):
                    summary["preuploaded"] += 1
                if out.get("published"):
                    summary["published_or_scheduled"] += 1
                if "triaged_count" in out:
                    summary["comments_triaged"] += out["triaged_count"]

            if settings.get("gdrive_storage_enabled", True):
                try:
                    vault_res = sync_all_data_and_resources_to_gdrive()
                    summary["gdrive_vault_synced"] = vault_res.get("synced_count", 0)
                except Exception as vault_exc:
                    log_activity("GDRIVE_VAULT", f"Auto-backup note: {vault_exc}", level="WARN")

            self.last_cycle_at = utc_now_iso()
            self.cycle_count += 1
            log_activity(
                "AGENT",
                f"Completed Parallel Cycle #{self.cycle_count} ({workers} workers): "
                f"{summary['collected']} collected, {summary['inspected']} inspected, "
                f"{summary['remediated']} remixed, {summary['trends_discovered']} trends, "
                f"{summary['ai_posts_generated']} AI news posts, {summary['published_or_scheduled']} published.",
                details=summary,
            )
            return {"status": "completed", "summary": summary, "last_cycle_at": self.last_cycle_at}
        except Exception as exc:
            log_activity("AGENT", f"Autonomous parallel cycle error: {exc}", level="ERROR")
            return {"status": "error", "error": str(exc)}
        finally:
            self.is_running_cycle = False

    async def background_loop(self) -> None:
        while True:
            try:
                settings = get_settings()
                if settings.get("agent_auto_loop_enabled", False) and not self.is_running_cycle:
                    await asyncio.to_thread(self.run_single_cycle_sync)
                interval = max(15, int(settings.get("agent_loop_interval_seconds") or 300))
            except Exception:
                interval = 60
            await asyncio.sleep(interval)


agent_worker = AutonomousAgentWorker()
