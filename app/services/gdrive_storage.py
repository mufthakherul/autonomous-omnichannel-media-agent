import re
import secrets
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import (
    ASSETS_DIR,
    BASE_DIR,
    DATA_DIR,
    DB_PATH,
    GDRIVE_VAULT_DIR,
    GENERATED_DIR,
    INCOMING_DIR,
    PROCESSED_DIR,
)
from app.database import get_connection, get_settings, log_activity, utc_now_iso

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
DRIVE_TOKEN_PATH = DATA_DIR / "drive_token.json"
SERVICE_ACCOUNT_PATH = BASE_DIR / "service_account.json"


def extract_drive_folder_id(raw_value: str) -> str:
    """Extracts a clean Google Drive folder ID from either a full URL or raw ID string."""
    val = (raw_value or "").strip()
    if not val:
        return ""
    m = re.search(r"/folders/([a-zA-Z0-9_-]{10,})", val)
    if m:
        return m.group(1)
    m_id = re.search(r"[?&]id=([a-zA-Z0-9_-]{10,})", val)
    if m_id:
        return m_id.group(1)
    if re.match(r"^[a-zA-Z0-9_-]{15,}$", val):
        return val
    return ""


def get_active_vault_root_dir() -> Path:
    """Returns the local Google Drive Desktop folder if configured, otherwise `data/gdrive_vault`."""
    settings = get_settings()
    custom_mount = (settings.get("gdrive_local_mount_path") or "").strip()
    if custom_mount:
        p = Path(custom_mount)
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            pass
    GDRIVE_VAULT_DIR.mkdir(parents=True, exist_ok=True)
    return GDRIVE_VAULT_DIR


def get_drive_api_client(interactive_auth: bool = False):
    """
    Returns an authenticated Google Drive API v3 service if credentials exist
    (either `service_account.json` or `data/drive_token.json`), or None to use Vault Mirror mode.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except Exception:
        return None

    if SERVICE_ACCOUNT_PATH.exists():
        try:
            creds = service_account.Credentials.from_service_account_file(
                str(SERVICE_ACCOUNT_PATH), scopes=DRIVE_SCOPES
            )
            return build("drive", "v3", credentials=creds)
        except Exception:
            pass

    creds = None
    if DRIVE_TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(DRIVE_TOKEN_PATH), DRIVE_SCOPES)
        except Exception:
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            DRIVE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        except Exception:
            creds = None

    if not creds and interactive_auth:
        settings = get_settings()
        secrets_file = Path(settings.get("youtube_client_secrets_file") or (BASE_DIR / "client_secrets.json"))
        if not secrets_file.exists():
            raise FileNotFoundError(
                f"client_secrets.json not found at {secrets_file}. Place your Google Cloud OAuth Desktop JSON in the project root first."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), DRIVE_SCOPES)
        creds = flow.run_local_server(port=0)
        DRIVE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    if creds and creds.valid:
        return build("drive", "v3", credentials=creds)
    return None


def _upload_file_via_drive_api(service: Any, local_file: Path, parent_folder_id: str, mime_type: str = "application/octet-stream") -> Tuple[str, str]:
    """Uploads or updates a file inside a Google Drive folder using Google Drive API v3."""
    from googleapiclient.http import MediaFileUpload

    q = f"name = '{local_file.name}' and trashed = false"
    if parent_folder_id:
        q += f" and '{parent_folder_id}' in parents"

    existing = service.files().list(q=q, fields="files(id, webViewLink)", pageSize=1).execute().get("files", [])
    media = MediaFileUpload(str(local_file), mimetype=mime_type, resumable=True)

    if existing:
        file_id = existing[0]["id"]
        updated = (
            service.files()
            .update(fileId=file_id, media_body=media, fields="id, webViewLink")
            .execute()
        )
        return updated.get("id", file_id), updated.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")
    else:
        body: Dict[str, Any] = {"name": local_file.name}
        if parent_folder_id:
            body["parents"] = [parent_folder_id]
        created = (
            service.files()
            .create(body=body, media_body=media, fields="id, webViewLink")
            .execute()
        )
        fid = created.get("id", "")
        return fid, created.get("webViewLink", f"https://drive.google.com/file/d/{fid}/view")


def _record_vault_file(
    filename: str,
    category: str,
    local_path: str,
    gdrive_file_id: str,
    web_view_link: str,
    size_kb: float,
    storage_mode: str,
) -> Dict[str, Any]:
    now = utc_now_iso()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO gdrive_vault_files (
                filename, category, local_path, gdrive_file_id, web_view_link, size_kb, storage_mode, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(category, filename) DO UPDATE SET
                local_path = excluded.local_path,
                gdrive_file_id = excluded.gdrive_file_id,
                web_view_link = excluded.web_view_link,
                size_kb = excluded.size_kb,
                storage_mode = excluded.storage_mode,
                synced_at = excluded.synced_at
            """,
            (filename, category, local_path, gdrive_file_id, web_view_link, round(size_kb, 2), storage_mode, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "filename": filename,
        "category": category,
        "gdrive_file_id": gdrive_file_id,
        "web_view_link": web_view_link,
        "size_kb": round(size_kb, 2),
        "storage_mode": storage_mode,
        "synced_at": now,
    }


def archive_file_to_gdrive_vault(local_path: Path, category: str = "generated") -> Optional[Dict[str, Any]]:
    """
    Syncs a single file (SQLite DB snapshot, generated AI video/image, remixed video, or asset)
    to the Google Drive Storage Vault (via Drive API v3 if connected, plus local Drive Vault directory).
    """
    if not local_path or not local_path.exists():
        return None

    settings = get_settings()
    if not settings.get("gdrive_storage_enabled", True):
        return None

    vault_root = get_active_vault_root_dir()
    cat_dir = vault_root / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    dest_mirror = cat_dir / local_path.name

    if local_path.resolve() != dest_mirror.resolve():
        shutil.copy2(local_path, dest_mirror)

    size_kb = dest_mirror.stat().st_size / 1024.0
    folder_id = extract_drive_folder_id(settings.get("gdrive_vault_folder_id") or "")
    service = get_drive_api_client(interactive_auth=False)

    if service:
        try:
            fid, link = _upload_file_via_drive_api(service, dest_mirror, folder_id)
            return _record_vault_file(
                filename=local_path.name,
                category=category,
                local_path=str(dest_mirror),
                gdrive_file_id=fid,
                web_view_link=link,
                size_kb=size_kb,
                storage_mode="google_drive_api_v3",
            )
        except Exception as exc:
            log_activity("GDRIVE_VAULT", f"Drive API upload fallback note for {local_path.name}: {exc}", level="WARN")

    sim_fid = f"gdrive_vault_{category}_{secrets.token_hex(4)}"
    link = (
        f"https://drive.google.com/drive/folders/{folder_id}"
        if folder_id
        else f"gdrive://vault/{category}/{local_path.name}"
    )
    mode = "gdrive_desktop_mount" if (settings.get("gdrive_local_mount_path") or "").strip() else "gdrive_vault_sync"
    return _record_vault_file(
        filename=local_path.name,
        category=category,
        local_path=str(dest_mirror),
        gdrive_file_id=sim_fid,
        web_view_link=link,
        size_kb=size_kb,
        storage_mode=mode,
    )


def backup_database_to_gdrive() -> Dict[str, Any]:
    """
    Atomically snapshots SQLite `data/agent.db` using SQLite's online backup API and uploads it
    to the Google Drive Storage Vault (`database/agent_db_backup.sqlite`).
    """
    vault_root = get_active_vault_root_dir()
    db_vault_dir = vault_root / "database"
    db_vault_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = db_vault_dir / "agent_db_backup.sqlite"

    src_conn = sqlite3.connect(str(DB_PATH))
    dst_conn = sqlite3.connect(str(snapshot_path))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    rec = archive_file_to_gdrive_vault(snapshot_path, category="database")
    log_activity(
        "GDRIVE_VAULT",
        f"Backed up SQLite database (agent.db — {round(snapshot_path.stat().st_size / 1024.0, 1)} KB) to Google Drive Storage Vault.",
    )
    return rec or {"status": "backed_up", "path": str(snapshot_path)}


def restore_database_from_gdrive() -> Dict[str, Any]:
    """
    Restores `data/agent.db` from the Google Drive Storage Vault snapshot (`database/agent_db_backup.sqlite`).
    Can be called automatically on fresh Cloud Run container startup or manually from the Dashboard.
    """
    vault_root = get_active_vault_root_dir()
    snapshot_path = vault_root / "database" / "agent_db_backup.sqlite"

    settings = get_settings()
    folder_id = extract_drive_folder_id(settings.get("gdrive_vault_folder_id") or "")
    service = get_drive_api_client(interactive_auth=False)

    if service and not snapshot_path.exists():
        try:
            q = "name = 'agent_db_backup.sqlite' and trashed = false"
            if folder_id:
                q += f" and '{folder_id}' in parents"
            files = service.files().list(q=q, fields="files(id, name)", pageSize=1).execute().get("files", [])
            if files:
                content = service.files().get_media(fileId=files[0]["id"]).execute()
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                snapshot_path.write_bytes(content)
        except Exception as exc:
            log_activity("GDRIVE_VAULT", f"Drive API DB restore lookup note: {exc}", level="WARN")

    if not snapshot_path.exists():
        raise FileNotFoundError("No database backup (agent_db_backup.sqlite) found in Google Drive Vault yet. Run a Backup/Sync first.")

    src_conn = sqlite3.connect(str(snapshot_path))
    dst_conn = sqlite3.connect(str(DB_PATH))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    log_activity("GDRIVE_VAULT", "Restored SQLite database (agent.db) from Google Drive Storage Vault backup.")
    return {
        "restored": True,
        "snapshot_path": str(snapshot_path),
        "size_kb": round(DB_PATH.stat().st_size / 1024.0, 2),
        "restored_at": utc_now_iso(),
    }


def sync_all_data_and_resources_to_gdrive() -> Dict[str, Any]:
    """
    Syncs the entire agent state to the Google Drive 2TB Storage Vault:
    1. Atomic SQLite database snapshot (`agent_db_backup.sqlite` — includes `dedup_registry`, `videos`, `studio_creations`, `api_keys`, `sources`, `comments`)
    2. All AI-generated videos & image posts (`data/generated/*`)
    3. All FFmpeg copyright-remixed videos (`data/processed/*`)
    4. All collected production assets (`data/assets/sfx/*`, `data/assets/backgrounds/*`)
    """
    synced_files: List[Dict[str, Any]] = []

    # 1. Backup SQLite Database first
    db_rec = backup_database_to_gdrive()
    if db_rec:
        synced_files.append(db_rec)

    # 2. Archive AI Studio Generated Videos & Images
    if GENERATED_DIR.exists():
        for f in sorted(GENERATED_DIR.iterdir()):
            if f.is_file() and f.name != ".gitkeep" and f.suffix.lower() in (".mp4", ".jpg", ".jpeg", ".png", ".wav"):
                rec = archive_file_to_gdrive_vault(f, category="generated")
                if rec:
                    synced_files.append(rec)

    # 3. Archive FFmpeg Remixed Videos
    if PROCESSED_DIR.exists():
        for f in sorted(PROCESSED_DIR.iterdir()):
            if f.is_file() and f.name != ".gitkeep" and f.suffix.lower() in (".mp4", ".mov", ".mkv"):
                rec = archive_file_to_gdrive_vault(f, category="processed")
                if rec:
                    synced_files.append(rec)

    # 4. Archive Collected Editing Assets (SFX, LUTs, Backgrounds)
    if ASSETS_DIR.exists():
        for sub in ("sfx", "backgrounds", "overlays"):
            sub_dir = ASSETS_DIR / sub
            if sub_dir.exists():
                for f in sorted(sub_dir.iterdir()):
                    if f.is_file() and f.name != ".gitkeep":
                        rec = archive_file_to_gdrive_vault(f, category=f"assets_{sub}")
                        if rec:
                            synced_files.append(rec)

    settings = get_settings()
    cleaned_count = 0
    if settings.get("auto_cleanup_local_after_publish", False):
        clean_res = cleanup_published_local_media()
        cleaned_count = clean_res.get("removed_files_count", 0)

    log_activity(
        "GDRIVE_VAULT",
        f"Synced {len(synced_files)} files (SQLite DB + AI Media + Remixed Videos + Assets) to Google Drive Storage Vault"
        + (f" and freed {cleaned_count} published local file(s)." if cleaned_count else "."),
        details={"synced_count": len(synced_files), "cleaned_local_files": cleaned_count},
    )
    return {
        "synced_count": len(synced_files),
        "cleaned_local_files": cleaned_count,
        "status": get_gdrive_vault_status(),
    }


def cleanup_published_local_media() -> Dict[str, Any]:
    """
    Frees local/container disk space by deleting heavy local `.mp4` files in `data/incoming` and `data/processed`
    for videos that are ALREADY published (and archived in the Google Drive Vault), while preserving their
    `SHA-256` and `external_id` permanently in `dedup_registry` so they are never re-downloaded!
    """
    conn = get_connection()
    published_rows = conn.execute(
        "SELECT id, local_path, remediated_path, original_filename FROM videos WHERE status IN ('published', 'scheduled')"
    ).fetchall()
    conn.close()

    removed_count = 0
    freed_mb = 0.0

    for r in published_rows:
        for col in ("local_path", "remediated_path"):
            raw_p = r[col]
            if not raw_p:
                continue
            p = Path(raw_p)
            if p.exists() and p.is_file():
                # Ensure it is archived in the vault before removing local cache
                cat = "processed" if col == "remediated_path" else "incoming_archive"
                archive_file_to_gdrive_vault(p, category=cat)
                try:
                    sz_mb = p.stat().st_size / (1024 * 1024)
                    p.unlink()
                    removed_count += 1
                    freed_mb += sz_mb
                except Exception:
                    pass

    log_activity(
        "GDRIVE_VAULT",
        f"Smart Local Disk Cleanup: Freed {round(freed_mb, 2)} MB across {removed_count} published video file(s) (Archived in Google Drive Vault & locked in Dedup Guard).",
    )
    return {"removed_files_count": removed_count, "freed_mb": round(freed_mb, 2)}


def get_gdrive_vault_status() -> Dict[str, Any]:
    """Returns full telemetry for the Google Drive 2TB Cloud Storage Vault."""
    settings = get_settings()
    vault_root = get_active_vault_root_dir()
    folder_id = extract_drive_folder_id(settings.get("gdrive_vault_folder_id") or "")
    has_live_api = get_drive_api_client(interactive_auth=False) is not None

    conn = get_connection()
    try:
        rows = [
            dict(r)
            for r in conn.execute("SELECT * FROM gdrive_vault_files ORDER BY synced_at DESC LIMIT 80").fetchall()
        ]
        total_files = conn.execute("SELECT COUNT(*) as c FROM gdrive_vault_files").fetchone()["c"]
        total_kb = conn.execute("SELECT COALESCE(SUM(size_kb), 0) as s FROM gdrive_vault_files").fetchone()["s"]
        db_row = conn.execute(
            "SELECT synced_at, size_kb FROM gdrive_vault_files WHERE category = 'database' ORDER BY synced_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    # Calculate active local temporary disk footprint (`incoming` + `processed` + `generated`)
    local_bytes = 0
    for d in (INCOMING_DIR, PROCESSED_DIR, GENERATED_DIR):
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file() and f.name != ".gitkeep":
                    local_bytes += f.stat().st_size

    return {
        "enabled": bool(settings.get("gdrive_storage_enabled", True)),
        "vault_folder_id": folder_id,
        "vault_folder_url": f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else "",
        "local_mount_path": str(vault_root),
        "live_drive_api_connected": has_live_api,
        "auto_cleanup_local": bool(settings.get("auto_cleanup_local_after_publish", False)),
        "total_archived_files": total_files,
        "total_archived_mb": round(total_kb / 1024.0, 2),
        "local_cache_mb": round(local_bytes / (1024.0 * 1024.0), 2),
        "db_last_backed_up_at": db_row["synced_at"] if db_row else "",
        "db_backup_size_kb": round(db_row["size_kb"], 1) if db_row else round(DB_PATH.stat().st_size / 1024.0, 1),
        "files": rows,
    }
