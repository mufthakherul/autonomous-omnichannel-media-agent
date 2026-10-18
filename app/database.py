import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import DB_PATH, DEFAULT_SETTINGS, INCOMING_DIR


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            config_value TEXT NOT NULL,
            max_items_per_sync INTEGER DEFAULT 5,
            active INTEGER DEFAULT 1,
            last_synced_at TEXT,
            total_collected INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            source_type TEXT NOT NULL,
            external_id TEXT,
            source_url TEXT,
            original_filename TEXT NOT NULL,
            local_path TEXT NOT NULL,
            remediated_path TEXT,
            thumbnail_path TEXT,
            file_hash TEXT UNIQUE,
            file_size_mb REAL DEFAULT 0.0,
            duration_sec REAL DEFAULT 0.0,
            width INTEGER DEFAULT 0,
            height INTEGER DEFAULT 0,
            fps REAL DEFAULT 0.0,
            video_codec TEXT DEFAULT '',
            audio_codec TEXT DEFAULT '',
            has_audio INTEGER DEFAULT 1,
            is_short INTEGER DEFAULT 0,
            can_upload_as_is INTEGER DEFAULT 0,
            readiness_score INTEGER DEFAULT 0,
            technical_passed INTEGER DEFAULT 0,
            ai_passed INTEGER DEFAULT 0,
            watermark_detected INTEGER DEFAULT 0,
            audio_risk_level TEXT DEFAULT 'low',
            policy_safe INTEGER DEFAULT 1,
            issues_detected TEXT DEFAULT '[]',
            recommended_fixes TEXT DEFAULT '[]',
            remediation_applied TEXT DEFAULT '[]',
            content_summary TEXT DEFAULT '',
            seo_title TEXT DEFAULT '',
            seo_description TEXT DEFAULT '',
            seo_tags TEXT DEFAULT '[]',
            category_id TEXT DEFAULT '22',
            playlist_name TEXT DEFAULT 'General',
            best_thumbnail_timestamp_sec REAL DEFAULT 1.0,
            status TEXT DEFAULT 'discovered',
            youtube_video_id TEXT,
            youtube_privacy_status TEXT,
            content_id_clean INTEGER DEFAULT 0,
            scheduled_publish_at TEXT,
            published_at TEXT,
            platforms_published TEXT DEFAULT '{}',
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    try:
        cur.execute("ALTER TABLE videos ADD COLUMN platforms_published TEXT DEFAULT '{}'")
    except Exception:
        pass

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            youtube_comment_id TEXT UNIQUE NOT NULL,
            youtube_video_id TEXT NOT NULL,
            video_title TEXT DEFAULT '',
            author_name TEXT NOT NULL,
            comment_text TEXT NOT NULL,
            published_at TEXT NOT NULL,
            needs_reply INTEGER DEFAULT 0,
            category TEXT DEFAULT 'simple_reaction',
            priority INTEGER DEFAULT 1,
            sentiment TEXT DEFAULT 'neutral',
            ai_reason TEXT DEFAULT '',
            draft_reply TEXT DEFAULT '',
            status TEXT DEFAULT 'pending_triage',
            reply_comment_id TEXT,
            replied_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            details TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            provider TEXT DEFAULT 'gemini',
            api_key TEXT UNIQUE NOT NULL,
            masked_key TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            status TEXT DEFAULT 'healthy',
            total_calls INTEGER DEFAULT 0,
            success_calls INTEGER DEFAULT 0,
            rate_limit_hits INTEGER DEFAULT 0,
            cooldown_until REAL DEFAULT 0.0,
            last_used_at TEXT,
            last_error TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS studio_creations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creation_type TEXT NOT NULL,
            headline TEXT NOT NULL,
            niche TEXT DEFAULT 'General',
            summary TEXT DEFAULT '',
            caption TEXT DEFAULT '',
            hashtags TEXT DEFAULT '[]',
            image_prompt TEXT DEFAULT '',
            video_prompt TEXT DEFAULT '',
            voiceover_script TEXT DEFAULT '',
            media_path TEXT DEFAULT '',
            media_url TEXT DEFAULT '',
            audio_path TEXT DEFAULT '',
            linked_video_id INTEGER,
            source_urls TEXT DEFAULT '[]',
            status TEXT DEFAULT 'ready',
            platforms_published TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS parallel_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            title TEXT NOT NULL,
            worker_slot INTEGER DEFAULT 1,
            api_key_label TEXT DEFAULT '',
            status TEXT DEFAULT 'running',
            duration_ms INTEGER DEFAULT 0,
            result_summary TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dedup_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint_type TEXT NOT NULL,
            fingerprint_value TEXT NOT NULL,
            reference_label TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(fingerprint_type, fingerprint_value)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS gdrive_vault_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            category TEXT NOT NULL,
            local_path TEXT DEFAULT '',
            gdrive_file_id TEXT DEFAULT '',
            web_view_link TEXT DEFAULT '',
            size_kb REAL DEFAULT 0.0,
            storage_mode TEXT DEFAULT 'local_vault_mirror',
            synced_at TEXT NOT NULL,
            UNIQUE(category, filename)
        )
        """
    )

    now = utc_now_iso()

    for key, val in DEFAULT_SETTINGS.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(val), now),
        )

    # Ensure default local incoming watch folder source exists
    cur.execute("SELECT COUNT(*) as cnt FROM sources")
    if cur.fetchone()["cnt"] == 0:
        cur.execute(
            """
            INSERT INTO sources (name, source_type, config_value, max_items_per_sync, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Local Incoming Watch Folder", "local", str(INCOMING_DIR), 10, 1, now),
        )

    conn.commit()
    conn.close()



def log_activity(stage: str, message: str, level: str = "INFO", details: Optional[Dict[str, Any]] = None) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO activity_logs (stage, level, message, details, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (stage, level, message, json.dumps(details or {}), utc_now_iso()),
    )
    conn.commit()
    conn.close()


def get_settings() -> Dict[str, Any]:
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    result = dict(DEFAULT_SETTINGS)
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except Exception:
            result[row["key"]] = row["value"]
    return result


def update_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_connection()
    now = utc_now_iso()
    for key, val in updates.items():
        if key in DEFAULT_SETTINGS:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, json.dumps(val), now),
            )
    conn.commit()
    conn.close()
    return get_settings()


def row_to_video_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for json_field in ("issues_detected", "recommended_fixes", "remediation_applied", "seo_tags"):
        raw = d.get(json_field)
        if isinstance(raw, str):
            try:
                d[json_field] = json.loads(raw)
            except Exception:
                d[json_field] = []
    plat_raw = d.get("platforms_published")
    if isinstance(plat_raw, str):
        try:
            d["platforms_published"] = json.loads(plat_raw)
        except Exception:
            d["platforms_published"] = {}
    elif not isinstance(plat_raw, dict):
        d["platforms_published"] = {}
    for bool_field in (
        "has_audio",
        "is_short",
        "can_upload_as_is",
        "technical_passed",
        "ai_passed",
        "watermark_detected",
        "policy_safe",
        "content_id_clean",
    ):
        d[bool_field] = bool(d.get(bool_field))
    return d


def list_videos(status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM videos WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM videos ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [row_to_video_dict(r) for r in rows]


def get_video(video_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    conn.close()
    return row_to_video_dict(row) if row else None


def update_video(video_id: int, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not fields:
        return get_video(video_id)
    conn = get_connection()
    fields = dict(fields)
    fields["updated_at"] = utc_now_iso()
    for json_field in ("issues_detected", "recommended_fixes", "remediation_applied", "seo_tags", "platforms_published"):
        if json_field in fields and not isinstance(fields[json_field], str):
            fields[json_field] = json.dumps(fields[json_field])
    for bool_field in (
        "has_audio",
        "is_short",
        "can_upload_as_is",
        "technical_passed",
        "ai_passed",
        "watermark_detected",
        "policy_safe",
        "content_id_clean",
    ):
        if bool_field in fields and isinstance(fields[bool_field], bool):
            fields[bool_field] = 1 if fields[bool_field] else 0

    columns = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [video_id]
    conn.execute(f"UPDATE videos SET {columns} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return get_video(video_id)
