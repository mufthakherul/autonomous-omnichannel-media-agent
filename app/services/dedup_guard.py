import json
import re
from typing import Any, Dict, Optional, Set, Tuple

from app.database import get_connection, log_activity, utc_now_iso

STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "as", "is", "are", "was", "were", "be", "been", "this",
    "that", "it", "its", "new", "how", "why", "what", "when", "where", "who",
    "shorts", "short", "video", "hd", "4k", "stunning", "visuals", "breaking",
    "news", "update", "today", "live", "viral", "official", "clip", "mp4",
}


def extract_core_tokens(text: str) -> Set[str]:
    """Extracts meaningful lowercase alphanumeric tokens from a headline, filename, or title."""
    if not text:
        return set()
    # Strip trailing news publisher attribution like " - Reuters" or " | BBC News"
    cleaned = re.split(r"\s+[-|]\s+", text.strip())[0]
    words = re.findall(r"[a-z0-9]{3,}", cleaned.lower())
    core = {w for w in words if w not in STOPWORDS and not w.isdigit()}
    if not core and words:
        return set(words)
    return core


def normalize_text_fingerprint(text: str) -> str:
    """Builds a deterministic canonical fingerprint string from a headline, title, or filename."""
    tokens = sorted(extract_core_tokens(text))
    if not tokens:
        raw = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
        return raw[:80] or "empty"
    return "_".join(tokens)[:100]


def token_jaccard_similarity(text_a: str, text_b: str) -> float:
    """Computes keyword Jaccard overlap (0.0 to 1.0) between two headlines or titles."""
    set_a = extract_core_tokens(text_a)
    set_b = extract_core_tokens(text_b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def register_fingerprint(fingerprint_type: str, fingerprint_value: str, reference_label: str = "") -> None:
    """Permanently records a fingerprint in SQLite `dedup_registry`."""
    val = (fingerprint_value or "").strip()
    if not val:
        return
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO dedup_registry (fingerprint_type, fingerprint_value, reference_label, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (fingerprint_type, val, (reference_label or "")[:140], utc_now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def has_fingerprint(fingerprint_type: str, fingerprint_value: str) -> bool:
    val = (fingerprint_value or "").strip()
    if not val:
        return False
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM dedup_registry WHERE fingerprint_type = ? AND fingerprint_value = ?",
            (fingerprint_type, val),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def is_source_video_already_seen(
    external_id: Optional[str] = None,
    source_url: Optional[str] = None,
    filename: Optional[str] = None,
) -> bool:
    """
    Pre-download & pre-registration guard:
    Checks whether a candidate source video has EVER been collected before by external_id, source_url, or filename.
    """
    conn = get_connection()
    try:
        if external_id:
            row = conn.execute("SELECT id FROM videos WHERE external_id = ?", (external_id,)).fetchone()
            if row or has_fingerprint("source_external_id", external_id):
                return True
        if source_url and not source_url.startswith("local://") and not source_url.startswith("generated://"):
            row = conn.execute("SELECT id FROM videos WHERE source_url = ?", (source_url,)).fetchone()
            if row or has_fingerprint("source_url", source_url):
                return True
        if filename:
            clean_name = filename.strip().lower()
            if has_fingerprint("source_filename", clean_name):
                return True
        return False
    finally:
        conn.close()


def record_source_video_registered(
    video_id: int,
    original_filename: str,
    file_hash: str,
    external_id: Optional[str] = None,
    source_url: Optional[str] = None,
) -> None:
    """Records all source identifiers and binary SHA-256 hash in the permanent dedup_registry."""
    label = f"Video #{video_id}: {original_filename}"
    register_fingerprint("file_sha256", file_hash, label)
    if external_id:
        register_fingerprint("source_external_id", external_id, label)
    if source_url and not source_url.startswith("local://"):
        register_fingerprint("source_url", source_url, label)
    if original_filename:
        register_fingerprint("source_filename", original_filename.strip().lower(), label)


def is_news_or_topic_already_generated(
    headline_or_topic: str,
    target_creation_type: Optional[str] = "news_trend",
    similarity_threshold: float = 0.60,
    creation_type: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Prevents the agent from discovering the same news trend twice OR generating duplicate AI videos / image posts
    for a popular news story or event that was already covered!
    Returns (is_duplicate, reason, existing_creation_dict).
    """
    effective_type = creation_type if creation_type is not None else target_creation_type
    fp = normalize_text_fingerprint(headline_or_topic)

    conn = get_connection()
    try:
        if not effective_type or effective_type == "news_trend":
            rows = conn.execute(
                "SELECT * FROM studio_creations ORDER BY id DESC LIMIT 150"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM studio_creations WHERE creation_type = ? ORDER BY id DESC LIMIT 150",
                (effective_type,),
            ).fetchall()
    finally:
        conn.close()

    for r in rows:
        row_dict = dict(r)
        existing_hl = row_dict.get("headline") or ""
        if normalize_text_fingerprint(existing_hl) == fp:
            return True, f"Exact fingerprint match with '{existing_hl[:75]}'", row_dict
        sim = token_jaccard_similarity(headline_or_topic, existing_hl)
        if sim >= similarity_threshold:
            return True, f"High topic similarity ({int(sim * 100)}%) with '{existing_hl[:75]}'", row_dict

    if effective_type and has_fingerprint(f"topic_{effective_type}", fp):
        return True, f"Previously registered in dedup_registry ({effective_type})", None

    return False, None, None


def record_topic_generated(
    headline_or_topic: str,
    creation_type: str,
    creation_id: Optional[int] = None,
    video_id: Optional[int] = None,
    source_url: Optional[str] = None,
) -> None:
    fp = normalize_text_fingerprint(headline_or_topic)
    register_fingerprint(
        f"topic_{creation_type}",
        fp,
        f"Studio {creation_type} #{creation_id or video_id or 0}: {headline_or_topic[:80]}",
    )
    if source_url and not source_url.startswith("https://news.google.com"):
        register_fingerprint("news_source_url", source_url, headline_or_topic[:80])


def has_already_published_to_platform(
    platform: str,
    file_hash: str = "",
    title: str = "",
    title_or_headline: str = "",
    existing_platforms_dict: Optional[Dict[str, Any]] = None,
    creation_id: Optional[int] = None,
    video_id: Optional[int] = None,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Checks if a video or studio post has ALREADY been published to the given platform
    (by per-item platforms_published JSON, by file_hash on that platform, or by normalized title on that platform).
    Prevents ever uploading the same video or news post twice to YouTube, Facebook, Instagram, or TikTok!
    """
    plat = platform.lower().strip()
    effective_title = (title or title_or_headline or "").strip()

    if existing_platforms_dict and plat in existing_platforms_dict:
        prev = existing_platforms_dict[plat]
        if isinstance(prev, dict) and prev.get("status") in ("published", "scheduled"):
            return True, prev

    if file_hash:
        hash_key = f"{plat}:hash:{file_hash}"
        if has_fingerprint("platform_publish", hash_key):
            return True, {
                "platform": plat,
                "status": "already_published_dedup_blocked",
                "external_id": "dedup_hash_guard",
            }

    if effective_title and len(effective_title) >= 8:
        title_fp = normalize_text_fingerprint(effective_title)
        title_key = f"{plat}:title:{title_fp}"
        if has_fingerprint("platform_publish", title_key):
            return True, {
                "platform": plat,
                "status": "already_published_dedup_blocked",
                "external_id": "dedup_title_guard",
            }

    return False, None


def record_published_to_platform(
    platform: str,
    file_hash: str = "",
    title: str = "",
    title_or_headline: str = "",
    external_id: str = "",
    external_post_id: str = "",
    creation_id: Optional[int] = None,
    video_id: Optional[int] = None,
) -> None:
    plat = platform.lower().strip()
    effective_title = (title or title_or_headline or "").strip()
    effective_ext_id = (external_id or external_post_id or "").strip()
    label = f"Published to {plat.upper()} ({effective_ext_id}): {effective_title[:60]}"
    if file_hash:
        register_fingerprint("platform_publish", f"{plat}:hash:{file_hash}", label)
    if effective_title and len(effective_title) >= 8:
        title_fp = normalize_text_fingerprint(effective_title)
        register_fingerprint("platform_publish", f"{plat}:title:{title_fp}", label)


def get_dedup_stats() -> Dict[str, int]:
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) as c FROM dedup_registry").fetchone()["c"]
        hashes = conn.execute(
            "SELECT COUNT(*) as c FROM dedup_registry WHERE fingerprint_type IN ('file_sha256', 'source_external_id')"
        ).fetchone()["c"]
        topics = conn.execute(
            "SELECT COUNT(*) as c FROM dedup_registry WHERE fingerprint_type LIKE 'topic_%'"
        ).fetchone()["c"]
        publishes = conn.execute(
            "SELECT COUNT(*) as c FROM dedup_registry WHERE fingerprint_type = 'platform_publish'"
        ).fetchone()["c"]
        blocked_logs = conn.execute(
            "SELECT COUNT(*) as c FROM activity_logs WHERE stage IN ('DEDUP', 'DEDUP_GUARD') OR message LIKE '%duplicate%'"
        ).fetchone()["c"]
        return {
            "total_fingerprints": total,
            "unique_source_hashes": hashes,
            "unique_news_topics": topics,
            "platform_publish_locks": publishes,
            "duplicates_blocked_count": blocked_logs,
        }
    finally:
        conn.close()
