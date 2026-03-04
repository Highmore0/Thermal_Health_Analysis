# backend/db/photos.py
import io
import json
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

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

        # ------------------------------
        # NEW: temperature matrix table
        # One row per photo_id
        # tm_npy stores numpy .npy bytes (typically float16 or float32)
        # ------------------------------
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS photo_temp (
                photo_id INTEGER PRIMARY KEY,
                created_ts REAL NOT NULL,

                tm_npy BLOB NOT NULL,
                tm_h INTEGER NOT NULL,
                tm_w INTEGER NOT NULL,
                tm_dtype TEXT NOT NULL,

                tmin REAL,
                tmax REAL,
                tmean REAL,

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
    Note: analysis/photo_temp may be removed automatically via FK cascade if foreign_keys is enabled.
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


# ============================================================
# NEW: temperature matrix storage helpers
# ============================================================

def _npy_bytes_from_array(arr: np.ndarray) -> bytes:
    bio = io.BytesIO()
    np.save(bio, arr, allow_pickle=False)
    return bio.getvalue()


def db_upsert_temp_matrix(
    photo_id: int,
    created_ts: float,
    tm: np.ndarray,
    compress: str = "f16",
) -> None:
    """
    Save the temperature matrix for a photo_id.

    Args:
      photo_id: the photo row id
      created_ts: usually the same ts you used for db_insert_photo()
      tm: temperature matrix in Celsius (float32 recommended input)
      compress:
        - "f16": store as float16 (recommended; half size)
        - "f32": store as float32
    """
    if tm is None:
        return

    if not isinstance(tm, np.ndarray):
        raise TypeError("tm must be a numpy ndarray")

    if tm.ndim != 2:
        # If someone passes (H,W,1) or similar, try to squeeze
        tm2 = np.squeeze(tm)
        if tm2.ndim != 2:
            raise ValueError(f"tm must be 2D (H,W), got shape={tm.shape}")
        tm = tm2

    if compress == "f16":
        tm_save = tm.astype(np.float16, copy=False)
    elif compress == "f32":
        tm_save = tm.astype(np.float32, copy=False)
    else:
        raise ValueError("compress must be 'f16' or 'f32'")

    h, w = tm_save.shape
    blob = _npy_bytes_from_array(tm_save)

    # stats (use original tm to keep precision in stats)
    tmin = float(np.nanmin(tm))
    tmax = float(np.nanmax(tm))
    tmean = float(np.nanmean(tm))

    con = _connect()
    try:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO photo_temp (photo_id, created_ts, tm_npy, tm_h, tm_w, tm_dtype, tmin, tmax, tmean)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(photo_id) DO UPDATE SET
                created_ts=excluded.created_ts,
                tm_npy=excluded.tm_npy,
                tm_h=excluded.tm_h,
                tm_w=excluded.tm_w,
                tm_dtype=excluded.tm_dtype,
                tmin=excluded.tmin,
                tmax=excluded.tmax,
                tmean=excluded.tmean
            """,
            (
                int(photo_id),
                float(created_ts),
                sqlite3.Binary(blob),
                int(h),
                int(w),
                str(tm_save.dtype),
                float(tmin),
                float(tmax),
                float(tmean),
            ),
        )
        con.commit()
    finally:
        con.close()


def db_get_temp_matrix_npy(photo_id: int) -> Optional[bytes]:
    """
    Returns raw .npy bytes (BLOB) for the saved temperature matrix, or None if not found.
    """
    con = _connect()
    try:
        cur = con.cursor()
        cur.execute("SELECT tm_npy FROM photo_temp WHERE photo_id=?", (int(photo_id),))
        row = cur.fetchone()
        if not row:
            return None
        blob = row[0]
        if isinstance(blob, memoryview):
            blob = blob.tobytes()
        return blob
    finally:
        con.close()


def db_get_temp_meta(photo_id: int) -> Optional[Dict[str, Any]]:
    """
    Returns metadata for temperature matrix:
      {photo_id, created_ts, tm_h, tm_w, tm_dtype, tmin, tmax, tmean}
    """
    con = _connect()
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT photo_id, created_ts, tm_h, tm_w, tm_dtype, tmin, tmax, tmean
            FROM photo_temp
            WHERE photo_id=?
            """,
            (int(photo_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        con.close()


def db_delete_temp_matrix(photo_id: int) -> bool:
    """
    Deletes the temp matrix row for a given photo_id. Returns True if a row was deleted.
    """
    con = _connect()
    try:
        cur = con.cursor()
        cur.execute("DELETE FROM photo_temp WHERE photo_id=?", (int(photo_id),))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()