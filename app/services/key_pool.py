import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from google import genai
from google.genai import errors

from app.database import get_connection, get_settings, log_activity, utc_now_iso

T = TypeVar("T")
_POOL_LOCK = threading.Lock()


def mask_api_key(raw_key: str) -> str:
    clean = (raw_key or "").strip()
    if len(clean) <= 10:
        return "••••••••"
    return f"{clean[:6]}••••••{clean[-4:]}"


def sync_primary_setting_key_to_pool() -> None:
    """Ensures that if a single gemini_api_key or pexels/pixabay key is set in settings, it is registered in api_keys."""
    settings = get_settings()
    now = utc_now_iso()
    conn = get_connection()
    try:
        primary_gemini = (settings.get("gemini_api_key") or "").strip()
        if primary_gemini and primary_gemini != "your_gemini_api_key_here" and len(primary_gemini) >= 12:
            conn.execute(
                """
                INSERT OR IGNORE INTO api_keys (label, provider, api_key, masked_key, active, status, created_at)
                VALUES (?, 'gemini', ?, ?, 1, 'healthy', ?)
                """,
                ("Primary Google Pro Key", primary_gemini, mask_api_key(primary_gemini), now),
            )
        conn.commit()
    finally:
        conn.close()


def add_api_keys_bulk(raw_keys_text: str, provider: str = "gemini") -> Dict[str, Any]:
    """
    Parses one or more API keys from user input.
    Supports formats:
      - One key per line: AIzaSy...
      - Labeled key per line: Family Account 1 | AIzaSy...  or  Brother Pro: AIzaSy...
      - Space or comma separated keys
    """
    lines = [line.strip() for line in (raw_keys_text or "").splitlines() if line.strip()]
    parsed_entries: List[Tuple[str, str]] = []

    conn = get_connection()
    existing_count = conn.execute(
        "SELECT COUNT(*) as c FROM api_keys WHERE provider = ?", (provider,)
    ).fetchone()["c"]
    conn.close()

    idx_counter = existing_count + 1
    for line in lines:
        if "|" in line:
            parts = line.split("|", 1)
            label = parts[0].strip() or f"{provider.title()} Pro Key #{idx_counter}"
            key_val = parts[1].strip()
            if len(key_val) >= 8:
                parsed_entries.append((label, key_val))
                idx_counter += 1
            continue

        if ":" in line and not line.startswith("AIza") and not line.startswith("AQ."):
            parts = line.split(":", 1)
            label = parts[0].strip() or f"{provider.title()} Pro Key #{idx_counter}"
            key_val = parts[1].strip()
            if len(key_val) >= 8:
                parsed_entries.append((label, key_val))
                idx_counter += 1
            continue

        tokens = [t.strip() for t in re.split(r"[\s,;]+", line) if len(t.strip()) >= 8]
        for token in tokens:
            parsed_entries.append((f"Google Pro Key #{idx_counter}" if provider == "gemini" else f"{provider.title()} Key #{idx_counter}", token))
            idx_counter += 1

    added = 0
    skipped = 0
    now = utc_now_iso()
    conn = get_connection()
    for label, key_val in parsed_entries:
        if key_val == "your_gemini_api_key_here":
            skipped += 1
            continue
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO api_keys (label, provider, api_key, masked_key, active, status, created_at)
            VALUES (?, ?, ?, ?, 1, 'healthy', ?)
            """,
            (label, provider, key_val, mask_api_key(key_val), now),
        )
        if cur.rowcount and cur.rowcount > 0:
            added += 1
        else:
            skipped += 1
    conn.commit()
    conn.close()

    if added > 0:
        log_activity(
            "KEY_POOL",
            f"Added {added} new {provider.upper()} API key(s) to the Multi-Account Key Rotator Pool.",
            details={"added": added, "skipped": skipped, "provider": provider},
        )
    return {"added": added, "skipped": skipped, "keys": list_api_keys()}


def list_api_keys(provider: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lists all API keys in the pool with their live health, usage, and cooldown status (never exposing raw api_key)."""
    sync_primary_setting_key_to_pool()
    now_ts = time.time()
    conn = get_connection()
    # Automatically clear expired cooldowns
    conn.execute(
        """
        UPDATE api_keys
        SET status = 'healthy', cooldown_until = 0.0
        WHERE status = 'cooldown' AND cooldown_until > 0 AND cooldown_until <= ?
        """,
        (now_ts,),
    )
    conn.commit()

    if provider:
        rows = conn.execute(
            "SELECT * FROM api_keys WHERE provider = ? ORDER BY id ASC", (provider,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM api_keys ORDER BY id ASC").fetchall()
    conn.close()

    results: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d.pop("api_key", None)  # Never expose raw secret in API list response
        cd_until = float(d.get("cooldown_until") or 0.0)
        d["cooldown_remaining_sec"] = max(0, int(round(cd_until - now_ts))) if cd_until > now_ts else 0
        d["active"] = bool(d.get("active", 1))
        results.append(d)
    return results


def delete_api_key(key_id: int) -> bool:
    conn = get_connection()
    conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
    conn.commit()
    conn.close()
    return True


def toggle_api_key(key_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
    if not row:
        conn.close()
        return None
    new_active = 0 if row["active"] else 1
    new_status = "healthy" if new_active else "paused"
    conn.execute(
        "UPDATE api_keys SET active = ?, status = ?, cooldown_until = 0.0 WHERE id = ?",
        (new_active, new_status, key_id),
    )
    conn.commit()
    conn.close()
    keys = [k for k in list_api_keys() if k["id"] == key_id]
    return keys[0] if keys else None


def reset_all_key_cooldowns() -> List[Dict[str, Any]]:
    conn = get_connection()
    conn.execute(
        "UPDATE api_keys SET status = 'healthy', cooldown_until = 0.0, last_error = '' WHERE active = 1"
    )
    conn.commit()
    conn.close()
    log_activity("KEY_POOL", "Reset cooldowns and restored all active API keys to healthy state.")
    return list_api_keys()


def acquire_next_gemini_key() -> Optional[Dict[str, Any]]:
    """
    Thread-safely selects the next healthy Gemini API key using Least-Recently-Used (LRU)
    and lowest call count ordering so parallel workers spread requests evenly across all family accounts.
    """
    sync_primary_setting_key_to_pool()
    with _POOL_LOCK:
        now_ts = time.time()
        now_iso = utc_now_iso()
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE api_keys
                SET status = 'healthy', cooldown_until = 0.0
                WHERE status = 'cooldown' AND cooldown_until > 0 AND cooldown_until <= ?
                """,
                (now_ts,),
            )
            row = conn.execute(
                """
                SELECT * FROM api_keys
                WHERE provider = 'gemini' AND active = 1 AND status = 'healthy'
                ORDER BY COALESCE(last_used_at, '') ASC, total_calls ASC, id ASC
                LIMIT 1
                """
            ).fetchone()

            if not row:
                # If all active keys are in cooldown, pick the one whose cooldown expires soonest
                row = conn.execute(
                    """
                    SELECT * FROM api_keys
                    WHERE provider = 'gemini' AND active = 1 AND status != 'invalid'
                    ORDER BY cooldown_until ASC, id ASC
                    LIMIT 1
                    """
                ).fetchone()

            if not row:
                return None

            key_dict = dict(row)
            conn.execute(
                """
                UPDATE api_keys
                SET total_calls = total_calls + 1, last_used_at = ?
                WHERE id = ?
                """,
                (now_iso, key_dict["id"]),
            )
            conn.commit()
            return key_dict
        finally:
            conn.close()


def mark_key_success(key_id: int) -> None:
    with _POOL_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE api_keys
                SET success_calls = success_calls + 1, status = 'healthy', cooldown_until = 0.0, last_error = ''
                WHERE id = ?
                """,
                (key_id,),
            )
            conn.commit()
        finally:
            conn.close()


def mark_key_error(key_id: int, error_msg: str, is_rate_limit: bool = False, is_invalid: bool = False) -> None:
    with _POOL_LOCK:
        conn = get_connection()
        try:
            if is_invalid:
                conn.execute(
                    """
                    UPDATE api_keys
                    SET status = 'invalid', last_error = ?
                    WHERE id = ?
                    """,
                    (error_msg[:250], key_id),
                )
            elif is_rate_limit:
                cooldown_until = time.time() + 90.0  # 90s automatic cooldown on 429
                conn.execute(
                    """
                    UPDATE api_keys
                    SET status = 'cooldown',
                        rate_limit_hits = rate_limit_hits + 1,
                        cooldown_until = ?,
                        last_error = ?
                    WHERE id = ?
                    """,
                    (cooldown_until, error_msg[:250], key_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE api_keys
                    SET last_error = ?
                    WHERE id = ?
                    """,
                    (error_msg[:250], key_id),
                )
            conn.commit()
        finally:
            conn.close()


def has_configured_gemini_keys() -> bool:
    """Returns True if at least one active Gemini API key is configured in the pool."""
    sync_primary_setting_key_to_pool()
    conn = get_connection()
    cnt = conn.execute(
        "SELECT COUNT(*) as c FROM api_keys WHERE provider = 'gemini' AND active = 1 AND status != 'invalid'"
    ).fetchone()["c"]
    conn.close()
    return cnt > 0


def execute_with_gemini_pool(
    operation_fn: Callable[[genai.Client, Dict[str, Any]], T],
    operation_name: str = "Gemini API Call",
    timeout_ms: Optional[int] = None,
) -> Tuple[Optional[T], Optional[Dict[str, Any]]]:
    """
    Executes a Gemini SDK operation using the Multi-Account Key Pool.
    - Automatically rotates across family Google Pro keys (LRU / Round-Robin).
    - If a key hits a 429 Rate Limit / Quota error, places that key into a 90s cooldown
      and immediately fails over to the next key in the pool!
    - Returns (result, key_info) or (None, None) if no keys are configured (triggering Simulation Mode).
    """
    if not has_configured_gemini_keys():
        return None, None

    conn = get_connection()
    active_count = conn.execute(
        "SELECT COUNT(*) as c FROM api_keys WHERE provider = 'gemini' AND active = 1"
    ).fetchone()["c"]
    conn.close()

    max_attempts = max(2, min(active_count + 1, 8))
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        key_info = acquire_next_gemini_key()
        if not key_info:
            break

        raw_key = key_info["api_key"]
        key_label = key_info["label"]
        key_id = key_info["id"]

        try:
            if timeout_ms:
                from google.genai import types
                client = genai.Client(api_key=raw_key, http_options=types.HttpOptions(timeout=timeout_ms))
            else:
                client = genai.Client(api_key=raw_key)

            result = operation_fn(client, key_info)
            mark_key_success(key_id)
            return result, key_info

        except Exception as exc:
            last_exc = exc
            err_str = str(exc)
            lower_err = err_str.lower()
            is_429 = (
                "429" in err_str
                or "resource_exhausted" in lower_err
                or "resourceexhausted" in lower_err
                or "quota" in lower_err
                or "rate limit" in lower_err
                or (isinstance(exc, errors.APIError) and getattr(exc, "code", None) == 429)
            )
            is_invalid_key = (
                "api_key_invalid" in lower_err
                or "api key not valid" in lower_err
                or (isinstance(exc, errors.APIError) and getattr(exc, "code", None) in (400, 401, 403) and "key" in lower_err)
            )

            mark_key_error(key_id, err_str, is_rate_limit=is_429, is_invalid=is_invalid_key)

            if is_429:
                log_activity(
                    "KEY_POOL",
                    f"Rate limit (429) on '{key_label}' ({key_info['masked_key']}) during {operation_name}. "
                    f"Cooling down key for 90s and failing over to next account (Attempt {attempt}/{max_attempts})...",
                    level="WARN",
                )
                continue
            elif is_invalid_key:
                log_activity(
                    "KEY_POOL",
                    f"Invalid API key detected on '{key_label}' ({key_info['masked_key']}). Failing over to next key...",
                    level="ERROR",
                )
                continue
            else:
                log_activity(
                    "KEY_POOL",
                    f"Error on '{key_label}' during {operation_name}: {err_str[:160]}",
                    level="WARN",
                )
                if attempt < max_attempts:
                    continue
                raise

    if last_exc:
        log_activity(
            "KEY_POOL",
            f"All available Gemini API keys exhausted for {operation_name}; falling back to intelligent simulation.",
            level="WARN",
        )
    return None, None
