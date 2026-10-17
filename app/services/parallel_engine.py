from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from typing import Any, Callable, Dict, List

from app.database import get_connection, get_settings, log_activity, utc_now_iso


def record_parallel_task_start(task_type: str, title: str, worker_slot: int) -> int:
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO parallel_tasks (task_type, title, worker_slot, status, created_at)
        VALUES (?, ?, ?, 'running', ?)
        """,
        (task_type, title[:140], worker_slot, utc_now_iso()),
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(task_id or 0)


def record_parallel_task_finish(
    task_id: int,
    status: str,
    duration_ms: int,
    result_summary: str,
    api_key_label: str = "",
) -> None:
    conn = get_connection()
    conn.execute(
        """
        UPDATE parallel_tasks
        SET status = ?, duration_ms = ?, result_summary = ?, api_key_label = ?, completed_at = ?
        WHERE id = ?
        """,
        (status, duration_ms, result_summary[:240], api_key_label, utc_now_iso(), task_id),
    )
    conn.commit()
    conn.close()


def list_parallel_tasks(limit: int = 40) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM parallel_tasks ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def execute_jobs_in_parallel(
    jobs: List[Dict[str, Any]],
    max_workers: int | None = None,
) -> List[Dict[str, Any]]:
    """
    Executes a list of heterogeneous agent jobs concurrently across a ThreadPoolExecutor.
    Each job dict contains:
      - 'task_type': str (e.g. 'INSPECT', 'REMIX', 'SYNC', 'AI_VIDEO', 'AI_IMAGE', 'PUBLISH', 'COMMENTS')
      - 'title': str
      - 'fn': Callable[[], Any]
    """
    if not jobs:
        return []

    settings = get_settings()
    workers = max_workers or max(1, min(16, int(settings.get("max_parallel_workers") or 4)))

    results: List[Dict[str, Any]] = []

    def _worker_runner(idx: int, job: Dict[str, Any]) -> Dict[str, Any]:
        slot = (idx % workers) + 1
        t_type = job.get("task_type", "TASK")
        title = job.get("title", "Parallel Job")
        fn: Callable[[], Any] = job["fn"]

        task_id = record_parallel_task_start(t_type, title, slot)
        t0 = time.perf_counter()
        try:
            out = fn()
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            summary_str = str(out.get("summary_text") if isinstance(out, dict) and "summary_text" in out else "Completed")
            key_label = str(out.get("key_label", "")) if isinstance(out, dict) else ""
            record_parallel_task_finish(task_id, "completed", elapsed_ms, summary_str, key_label)
            return {
                "task_id": task_id,
                "task_type": t_type,
                "title": title,
                "worker_slot": slot,
                "status": "completed",
                "duration_ms": elapsed_ms,
                "output": out,
            }
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            record_parallel_task_finish(task_id, "failed", elapsed_ms, f"Error: {exc}")
            log_activity("PARALLEL", f"[Worker #{slot}] Failed '{title}': {exc}", level="ERROR")
            return {
                "task_id": task_id,
                "task_type": t_type,
                "title": title,
                "worker_slot": slot,
                "status": "failed",
                "duration_ms": elapsed_ms,
                "error": str(exc),
            }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(_worker_runner, idx, job): job
            for idx, job in enumerate(jobs)
        }
        for future in as_completed(future_map):
            results.append(future.result())

    return results
