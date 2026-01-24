import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

from backend.config import DB_PATH


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    # Safer defaults for concurrent reads/writes on Raspberry Pi
    try:
        con.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    try:
        con.execute("PRAGMA foreign_keys=ON;")
    except Exception:
        pass
    try:
        con.execute("PRAGMA busy_timeout=3000;")
    except Exception:
        pass
    return con


def db_init() -> None:
    con = _connect()
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                name TEXT,
                ymin INTEGER,
                ymax INTEGER,
                ymean REAL,
                params_json TEXT,
                jpeg BLOB NOT NULL
            )
            """
        )

        # Backward-compat: try to add "name" for older databases (ignore if already exists)
        try:
            con.execute("ALTER TABLE photos ADD COLUMN name TEXT")
        except Exception:
            pass

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis (
                photo_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                text TEXT,
                json TEXT,
                updated_at REAL NOT NULL,
                FOREIGN KEY(photo_id) REFERENCES photos(id) ON DELETE CASCADE
            )
            """
        )

        con.commit()
    finally:
        con.close()


def db_insert_photo(
    ts: float,
    name: str,
    ymin: int,
    ymax: int,
    ymean: float,
    params: Dict[str, Any],
    jpeg: bytes,
) -> int:
    con = _connect()
    try:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO photos (ts, name, ymin, ymax, ymean, params_json, jpeg) VALUES (?,?,?,?,?,?,?)",
            (ts, name, ymin, ymax, ymean, json.dumps(params, ensure_ascii=False), sqlite3.Binary(jpeg)),
        )
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def db_list_photos(limit: int = 200) -> List[Dict[str, Any]]:
    """
    Returns a list of photos for the History UI.
    Column aliases (min/max/mean) match the current history.js usage.
    """
    con = _connect()
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT
                id,
                ts,
                name,
                ymin AS min,
                ymax AS max,
                ymean AS mean,
                params_json
            FROM photos
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def db_get_jpeg(photo_id: int) -> Optional[bytes]:
    con = _connect()
    try:
        cur = con.cursor()
        cur.execute("SELECT jpeg FROM photos WHERE id=?", (int(photo_id),))
        row = cur.fetchone()
        if not row:
            return None
        return row[0]
    finally:
        con.close()


def db_delete_photo(photo_id: int) -> bool:
    """
    Deletes a photo row. Returns True if a row was deleted.
    Note: analysis may be removed automatically via FK cascade if foreign_keys is enabled.
    """
    con = _connect()
    try:
        cur = con.cursor()
        cur.execute("DELETE FROM photos WHERE id=?", (int(photo_id),))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def db_upsert_analysis(
    photo_id: int,
    status: str,
    text: str,
    json_obj: Optional[Any] = None,
    updated_at: Optional[float] = None,
) -> None:
    """
    Creates or updates the analysis row for a given photo_id.
    """
    if updated_at is None:
        updated_at = time.time()

    json_text: Optional[str]
    if json_obj is None:
        json_text = None
    else:
        try:
            json_text = json.dumps(json_obj, ensure_ascii=False)
        except Exception:
            json_text = json.dumps({"_raw": str(json_obj)}, ensure_ascii=False)

    con = _connect()
    try:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO analysis (photo_id, status, text, json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(photo_id) DO UPDATE SET
                status=excluded.status,
                text=excluded.text,
                json=excluded.json,
                updated_at=excluded.updated_at
            """,
            (int(photo_id), str(status), str(text), json_text, float(updated_at)),
        )
        con.commit()
    finally:
        con.close()


def db_get_analysis(photo_id: int) -> Optional[Dict[str, Any]]:
    """
    Returns:
      { "photo_id": int, "status": str, "text": str, "json": object|str|None, "updated_at": float }
    or None if not found.
    """
    con = _connect()
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT photo_id, status, text, json, updated_at FROM analysis WHERE photo_id=?",
            (int(photo_id),),
        )
        row = cur.fetchone()
        if not row:
            return None

        out = dict(row)
        j = out.get("json")
        if j is not None:
            try:
                out["json"] = json.loads(j)
            except Exception:
                out["json"] = j
        return out
    finally:
        con.close()


def db_delete_analysis(photo_id: int) -> bool:
    """
    Deletes the analysis row for a given photo_id. Returns True if a row was deleted.
    """
    con = _connect()
    try:
        cur = con.cursor()
        cur.execute("DELETE FROM analysis WHERE photo_id=?", (int(photo_id),))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()
