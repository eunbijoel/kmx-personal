from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from manufacturing_agent.schemas import WorkbenchRun, utc_now

ROOT_DIR = Path(__file__).resolve().parents[2]
STORE_DIR = ROOT_DIR / "data" / "agent_workbench" / "runs"
DB_PATH = STORE_DIR / "workbench.sqlite3"


def ensure_store() -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                review_status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                raw_hash TEXT,
                payload_path TEXT NOT NULL
            )
            """
        )
        conn.commit()


def save_run(run: WorkbenchRun) -> Path:
    ensure_store()
    run.updated_at = utc_now()
    path = STORE_DIR / f"{run.run_id}.json"
    payload = run.model_dump()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO runs(run_id, status, review_status, created_at, updated_at, raw_hash, payload_path)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET
              status=excluded.status,
              review_status=excluded.review_status,
              updated_at=excluded.updated_at,
              raw_hash=excluded.raw_hash,
              payload_path=excluded.payload_path
            """,
            (
                run.run_id,
                run.status,
                run.review_status.value,
                run.created_at,
                run.updated_at,
                run.profile.raw_hash,
                str(path),
            ),
        )
        conn.commit()
    return path


def load_run(run_id: str) -> WorkbenchRun:
    ensure_store()
    path = STORE_DIR / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(run_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return WorkbenchRun.model_validate(payload)


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    ensure_store()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT run_id, status, review_status, created_at, updated_at, raw_hash
            FROM runs
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "run_id": row[0],
            "status": row[1],
            "review_status": row[2],
            "created_at": row[3],
            "updated_at": row[4],
            "raw_hash": row[5],
        }
        for row in rows
    ]
