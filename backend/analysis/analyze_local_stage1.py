#!/usr/bin/env python3
# backend/analysis/analyze_local_stage1.py

"""
python3 -m backend.analysis.analyze_local_stage1 \
  --image ./test/test1.jpg \
  --out ./test_results/test1_stage1.json \
  --pretty
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from backend.analysis.qwen_vlm import analyze_image_one_prompt
from backend.analysis.prompt_visible import THERMAL_VISIBILITY_PROMPT

# Reuse internal helpers from worker.py to ensure consistent behavior.
from backend.analysis.worker import (  # noqa: F401
    _build_injected_temperature_text,
    _safe_parse_json,
)


def _meta_from_tm(tm: Optional[np.ndarray]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "matrix_available": False,
        "matrix_shape": None,
        "max_temp": None,
        "min_temp": None,
        "mean_temp": None,
        "p10": None,
        "p50": None,
        "p90": None,
        "unit": "C",
    }

    if tm is None or not isinstance(tm, np.ndarray) or tm.ndim != 2:
        return meta

    tm = tm.astype(np.float32, copy=False)
    flat = tm[np.isfinite(tm)]
    if flat.size == 0:
        return meta

    meta.update(
        {
            "matrix_available": True,
            "matrix_shape": [int(tm.shape[0]), int(tm.shape[1])],
            "max_temp": float(np.max(flat)),
            "min_temp": float(np.min(flat)),
            "mean_temp": float(np.mean(flat)),
            "p10": float(np.percentile(flat, 10)),
            "p50": float(np.percentile(flat, 50)),
            "p90": float(np.percentile(flat, 90)),
        }
    )
    return meta


def _load_image_bytes(path: str) -> bytes:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Image not found: {path}")
    return p.read_bytes()


def _load_tm_npy(path: str) -> np.ndarray:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Temp matrix file not found: {path}")

    raw = p.read_bytes()
    try:
        tm = np.load(io.BytesIO(raw), allow_pickle=False)
    except TypeError:
        tm = np.load(io.BytesIO(raw))

    if not isinstance(tm, np.ndarray) or tm.ndim != 2:
        raise ValueError(
            f"Temp matrix must be a 2D numpy array, got: {type(tm)} ndim={getattr(tm,'ndim',None)}"
        )

    return tm.astype(np.float32, copy=False)


def analyze_local_stage1(
    *,
    image_bytes: bytes,
    tm: Optional[np.ndarray],
    stride_y: int = 4,
    stride_x: int = 4,
    temperature: float = 0.2,
    max_tokens: int = 2000,
) -> Dict[str, Any]:
    """
    Return ONLY stage1 result object:
      - If stage1 output parses as JSON -> return that dict
      - Else -> return {"raw_text": "..."}
    """
    tm_meta = _meta_from_tm(tm)

    injected_text = _build_injected_temperature_text(
        tm,
        tm_meta,
        stride_y=stride_y,
        stride_x=stride_x,
    )

    resp = analyze_image_one_prompt(
        image_bytes=image_bytes,
        prompt=THERMAL_VISIBILITY_PROMPT,
        temperature=temperature,
        max_tokens=max_tokens,
        inject_text=injected_text,
    )

    stage1_text = resp["content"]
    stage1_json = _safe_parse_json(stage1_text)

    # ✅ No merged / no legacy normalize / no wrappers
    return stage1_json if stage1_json is not None else {"raw_text": stage1_text}


def _write_json(path: Path, obj: Any, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = json.dumps(obj, ensure_ascii=False, indent=2) if pretty else json.dumps(obj, ensure_ascii=False)
    path.write_text(s, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze local thermal image (+optional tm.npy) using ONLY stage1.")
    ap.add_argument("--image", required=True, help="Path to thermal image (jpg/png/...)")
    ap.add_argument("--tm", default=None, help="Path to temperature matrix .npy (2D, Celsius). Optional.")
    ap.add_argument("--out", default=None, help="Output JSON file path (optional).")
    ap.add_argument("--stride-y", type=int, default=4)
    ap.add_argument("--stride-x", type=int, default=4)
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    img_bytes = _load_image_bytes(args.image)
    tm = _load_tm_npy(args.tm) if args.tm else None

    stage1_only = analyze_local_stage1(
        image_bytes=img_bytes,
        tm=tm,
        stride_y=args.stride_y,
        stride_x=args.stride_x,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )

    print(json.dumps(stage1_only, ensure_ascii=False, indent=2 if args.pretty else None))

    if args.out:
        _write_json(Path(args.out), stage1_only, args.pretty)


if __name__ == "__main__":
    main()