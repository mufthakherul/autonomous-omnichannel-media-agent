import secrets
from typing import Any, Dict, List, Optional

from google import genai

from app.database import get_connection, get_settings, log_activity, utc_now_iso
from app.models import CommentTriageBatch, CommentTriageDecision
from app.services.key_pool import execute_with_gemini_pool
from app.services.youtube_service import get_youtube_client


def fetch_new_youtube_comments(max_results: int = 25) -> List[Dict[str, Any]]:
    """Fetches recent top-level comments on the channel and stores unseen ones in SQLite."""
    settings = get_settings()
    youtube_client = get_youtube_client(interactive_auth=False)
    if youtube_client is None:
        return []

    ch_resp = youtube_client.channels().list(part="id", mine=True).execute()
    channels = ch_resp.get("items", [])
    if not channels:
        return []
    channel_id = channels[0]["id"]

    threads_resp = (
        youtube_client.commentThreads()
        .list(
            part="snippet,replies",
            allThreadsRelatedToChannelId=channel_id,
            maxResults=max_results,
            textFormat="plainText",
        )
        .execute()
    )

    conn = get_connection()
    now = utc_now_iso()
    newly_inserted: List[Dict[str, Any]] = []

    for item in threads_resp.get("items", []):
        top_comment = item.get("snippet", {}).get("topLevelComment", {})
        c_id = top_comment.get("id")
        c_snippet = top_comment.get("snippet", {})
        if not c_id:
            continue

        # Skip if the channel owner already replied in this thread
        existing_replies = item.get("replies", {}).get("comments", [])
        owner_already_replied = any(
            r.get("snippet", {}).get("authorChannelId", {}).get("value") == channel_id
            for r in existing_replies
        )
        if owner_already_replied:
            continue

        exists = conn.execute(
            "SELECT id FROM comments WHERE youtube_comment_id = ?", (c_id,)
        ).fetchone()
        if exists:
            continue

        video_id = c_snippet.get("videoId", "")
        author = c_snippet.get("authorDisplayName", "Viewer")
        text = c_snippet.get("textDisplay", "")
        pub_at = c_snippet.get("publishedAt", now)

        cur = conn.execute(
            """
            INSERT INTO comments (
                youtube_comment_id, youtube_video_id, video_title,
                author_name, comment_text, published_at, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending_triage', ?)
            """,
            (c_id, video_id, f"Video {video_id}", author, text, pub_at, now),
        )
        row_id = cur.lastrowid
        newly_inserted.append(
            {
                "id": row_id,
                "youtube_comment_id": c_id,
                "youtube_video_id": video_id,
                "video_title": f"Video {video_id}",
                "author_name": author,
                "comment_text": text,
            }
        )

    conn.commit()
    conn.close()
    return newly_inserted


def evaluate_comments_with_gemini(comments_to_triage: List[Dict[str, Any]]) -> List[CommentTriageDecision]:
    """
    Uses Gemini 3.8 Flash structured output via the Multi-Key Pool to filter comments so we ONLY reply
    to comments that genuinely warrant a reply (questions, constructive feedback, timestamp requests,
    meaningful praise) and ignore spam, self-promotion, trolls, or generic single-emoji comments.
    """
    if not comments_to_triage:
        return []

    settings = get_settings()
    model_name = settings.get("gemini_model") or "gemini-3.8-flash"
    persona = settings.get("channel_persona") or "Friendly, helpful, and concise creator."

    def _triage_op(client: genai.Client, key_info: Dict[str, Any]) -> List[CommentTriageDecision]:
        system_instruction = (
            "You are an intelligent YouTube Community Manager. Your job is to evaluate viewer comments and "
            "strictly decide which comments genuinely NEED a reply (needs_reply = true) versus which should be "
            "skipped/ignored (needs_reply = false).\n"
            "RULES:\n"
            "- Set needs_reply = true ONLY for: genuine viewer questions, requests for timestamps/sources/details, "
            "constructive feedback, troubleshooting help, or thoughtful/meaningful praise where a warm creator reply adds real value.\n"
            "- Set needs_reply = false for: spam, self-promotion, bot links, crypto/scam comments, hostile trolling, "
            "or ultra-short generic reactions (like 'first', 'nice', '🔥🔥', 'cool') that do not need a text response.\n"
            f"- Channel Persona for draft_reply: {persona}"
        )

        lines = []
        for c in comments_to_triage:
            lines.append(
                f"- comment_id: {c['youtube_comment_id']} | Video: {c.get('video_title', 'Channel Video')} | "
                f"Author: {c['author_name']} | Text: {c['comment_text']}"
            )
        prompt = "Evaluate the following YouTube comments and return structured triage decisions:\n\n" + "\n".join(lines)

        interaction = client.interactions.create(
            model=model_name,
            system_instruction=system_instruction,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": CommentTriageBatch.model_json_schema(),
            },
        )
        batch = CommentTriageBatch.model_validate_json(interaction.output_text)
        return batch.decisions

    pool_decisions, _ = execute_with_gemini_pool(_triage_op, operation_name="Comment Triage Batch")
    if pool_decisions is not None:
        return pool_decisions


    # Fallback deterministic smart triage when GEMINI_API_KEY is not yet configured
    decisions: List[CommentTriageDecision] = []
    for c in comments_to_triage:
        cid = c["youtube_comment_id"]
        txt = (c.get("comment_text") or "").strip()
        lower = txt.lower()

        if any(spam_kw in lower for spam_kw in ("http://", "https://", "telegram", "crypto", "whatsapp", "sub4sub", "check my channel")):
            decisions.append(
                CommentTriageDecision(
                    comment_id=cid,
                    needs_reply=False,
                    category="spam_or_promo",
                    priority=1,
                    sentiment="neutral",
                    reason="Detected promotional/spam keywords or external link; no reply should be sent.",
                    draft_reply="",
                )
            )
        elif any(bad_kw in lower for bad_kw in ("trash", "worst video", "hate this", "stupid")):
            decisions.append(
                CommentTriageDecision(
                    comment_id=cid,
                    needs_reply=False,
                    category="troll_or_hostile",
                    priority=1,
                    sentiment="negative",
                    reason="Hostile/non-constructive comment; best practice is not to engage.",
                    draft_reply="",
                )
            )
        elif "?" in txt or any(q_kw in lower for q_kw in ("what", "where", "how", "which", "camera", "location", "music", "song", "can you")):
            decisions.append(
                CommentTriageDecision(
                    comment_id=cid,
                    needs_reply=True,
                    category="question",
                    priority=5,
                    sentiment="positive",
                    reason="Viewer asked a direct question that benefits from a helpful creator answer.",
                    draft_reply=f"Thanks for watching, {c['author_name']}! Great question — we curated this in full HD from our royalty-free collection. Let us know if you'd like more videos in this style!",
                )
            )
        elif len(txt.split()) >= 6 and any(p_kw in lower for p_kw in ("love", "amazing", "helpful", "relaxing", "beautiful", "thank", "awesome", "great")):
            decisions.append(
                CommentTriageDecision(
                    comment_id=cid,
                    needs_reply=True,
                    category="meaningful_praise",
                    priority=3,
                    sentiment="positive",
                    reason="Thoughtful, multi-word viewer appreciation worth acknowledging to build community.",
                    draft_reply=f"Thank you so much for the kind words, {c['author_name']}! Glad you enjoyed the video 🙌",
                )
            )
        else:
            decisions.append(
                CommentTriageDecision(
                    comment_id=cid,
                    needs_reply=False,
                    category="simple_reaction",
                    priority=1,
                    sentiment="positive",
                    reason="Short reaction/emoji that does not require a written reply.",
                    draft_reply="",
                )
            )
    return decisions


def post_comment_reply(comment_db_id: int, custom_reply_text: Optional[str] = None) -> Dict[str, Any]:
    """Posts a reply to a YouTube comment (or simulates it in Simulation Mode)."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_db_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Comment #{comment_db_id} not found")
    comment = dict(row)
    conn.close()

    reply_text = (custom_reply_text if custom_reply_text is not None else comment.get("draft_reply", "")).strip()
    if not reply_text:
        raise ValueError("Reply text cannot be empty")

    youtube_client = get_youtube_client(interactive_auth=False)
    now = utc_now_iso()

    if youtube_client is None:
        reply_id = f"reply_sim_{secrets.token_hex(4)}"
    else:
        resp = (
            youtube_client.comments()
            .insert(
                part="snippet",
                body={
                    "snippet": {
                        "parentId": comment["youtube_comment_id"],
                        "textOriginal": reply_text,
                    }
                },
            )
            .execute()
        )
        reply_id = resp["id"]

    conn = get_connection()
    conn.execute(
        """
        UPDATE comments
        SET draft_reply = ?, status = 'replied', reply_comment_id = ?, replied_at = ?
        WHERE id = ?
        """,
        (reply_text, reply_id, now, comment_db_id),
    )
    updated_row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_db_id,)).fetchone()
    conn.close()

    log_activity(
        "COMMENTS",
        f"Replied to {comment['author_name']}'s comment on '{comment['video_title']}': \"{reply_text[:60]}...\"",
        details={"comment_id": comment_db_id, "reply_id": reply_id},
    )
    return dict(updated_row)


def triage_pending_comments() -> List[Dict[str, Any]]:
    """Triages all 'pending_triage' comments in SQLite and auto-replies if auto_reply_comments is enabled."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM comments WHERE status = 'pending_triage' ORDER BY id ASC LIMIT 25").fetchall()
    conn.close()
    if not rows:
        return []

    pending_list = [dict(r) for r in rows]
    decisions = evaluate_comments_with_gemini(pending_list)
    decision_map = {d.comment_id: d for d in decisions}

    settings = get_settings()
    auto_reply = bool(settings.get("auto_reply_comments", False))

    conn = get_connection()
    for c in pending_list:
        d = decision_map.get(c["youtube_comment_id"])
        if not d:
            continue
        new_status = "pending_approval" if d.needs_reply else "ignored"
        conn.execute(
            """
            UPDATE comments
            SET needs_reply = ?, category = ?, priority = ?, sentiment = ?,
                ai_reason = ?, draft_reply = ?, status = ?
            WHERE id = ?
            """,
            (
                1 if d.needs_reply else 0,
                d.category,
                d.priority,
                d.sentiment,
                d.reason,
                d.draft_reply,
                new_status,
                c["id"],
            ),
        )
    conn.commit()
    conn.close()

    if auto_reply:
        conn = get_connection()
        to_reply = conn.execute("SELECT id FROM comments WHERE status = 'pending_approval'").fetchall()
        conn.close()
        for r in to_reply:
            try:
                post_comment_reply(r["id"])
            except Exception as exc:
                log_activity("COMMENTS", f"Auto-reply error for comment #{r['id']}: {exc}", level="ERROR")

    conn = get_connection()
    updated = [dict(r) for r in conn.execute("SELECT * FROM comments ORDER BY id DESC LIMIT 50").fetchall()]
    conn.close()
    return updated


def seed_sample_comments() -> List[Dict[str, Any]]:
    """Seeds realistic viewer comments (questions, praise, emoji-only, spam, troll) to test AI Comment Triage."""
    sample_comments = [
        {
            "author_name": "@AlexFilmmaker",
            "video_title": "4K Drone Footage: Misty Alpine Mountains at Sunrise",
            "comment_text": "Wait what drone and ND filter did you use for that shot at 01:15? The dynamic range is unreal!",
        },
        {
            "author_name": "@CryptoBot999",
            "video_title": "4K Drone Footage: Misty Alpine Mountains at Sunrise",
            "comment_text": "I made $15,000 this week trading with Maria on Telegram! Search @MariaCryptoSignals now!!",
        },
        {
            "author_name": "@QuickViewer",
            "video_title": "4K Drone Footage: Misty Alpine Mountains at Sunrise",
            "comment_text": "🔥🔥🔥",
        },
        {
            "author_name": "@SarahStudies",
            "video_title": "4K Drone Footage: Misty Alpine Mountains at Sunrise",
            "comment_text": "I've been playing your nature videos in the background while studying for my medical board exams all month. Thank you for keeping these so peaceful and ad-light!",
        },
        {
            "author_name": "@AngryAnon",
            "video_title": "4K Drone Footage: Misty Alpine Mountains at Sunrise",
            "comment_text": "boring video worst channel ever",
        },
    ]

    conn = get_connection()
    now = utc_now_iso()
    for sc in sample_comments:
        fake_yt_id = f"ugx_sim_{secrets.token_hex(4)}"
        conn.execute(
            """
            INSERT INTO comments (
                youtube_comment_id, youtube_video_id, video_title,
                author_name, comment_text, published_at, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending_triage', ?)
            """,
            (
                fake_yt_id,
                "yt_sim_alpine4k",
                sc["video_title"],
                sc["author_name"],
                sc["comment_text"],
                now,
                now,
            ),
        )
    conn.commit()
    conn.close()
    log_activity("COMMENTS", "Seeded 5 sample viewer comments and running Gemini comment triage...")
    return triage_pending_comments()
