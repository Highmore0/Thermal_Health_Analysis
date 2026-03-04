#!/usr/bin/env python3
# backend/analysis/analyze_local.py
"""
Analyze a local thermal image + optional temperature matrix (.npy) using the same
two-stage pipeline used by backend/analysis/worker.py, but WITHOUT touching DB.

It will:
- read image bytes from disk
- optionally read a 2D temperature matrix from .npy (assumed Celsius)
- build injected temperature features JSON (downsample + region stats + points + indices)
- run analyze_image_two_prompts() twice (stage1/stage2)
- parse JSON outputs (fallback to {"raw_text": "..."} if parsing fails)
- normalize into legacy schema for frontend compatibility
- optionally save outputs to JSON files

----------------------------------------------------------------------
Usage examples:

1) Image only (no temperature matrix):
    python3 -m backend.analysis.analyze_local \
      --image ./test/test1.jpg \
      --out ./test_results/test1_v3.json \
      --pretty

2) Image + temperature matrix (.npy, 2D):
    python3 -m backend.analysis.analyze_local \
      --image ./testdata/test.jpg \
      --tm ./testdata/test_tm.npy \
      --out ./out/result.json \
      --pretty

Outputs (when --out is provided):
- ./out/result.json            (full result JSON)
- ./out/result_stage1.json     (stage1 JSON only)
- ./out/result_stage2.json     (stage2 JSON only)

The full result JSON shape is like:

{
  "status": "done",
  "text": "...",
  "json": { ... legacy frontend-compatible fields ..., "_raw": {...} },
  "merged": {
    "injected_temperature_meta": {...},
    "injection_mode": "kwargs" | "prompt_prepend",
    "stage1": {...},
    "stage2": {...}
  }
}

(Your sample output in the prompt is consistent with this structure.)
----------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from backend.analysis.qwen_vlm import analyze_image_two_prompts
from backend.analysis.prompts import THERMAL_VISIBILITY_PROMPT, THERMAL_HEALTH_PROMPT

# Reuse internal helpers from worker.py to ensure consistent behavior.
from backend.analysis.worker import (  # noqa: F401
    _build_injected_temperature_text,
    _normalize_for_frontend,
    _safe_parse_json,
)


def _meta_from_tm(tm: Optional[np.ndarray]) -> Dict[str, Any]:
    """Compute global stats meta similar to worker._load_temp_matrix output."""
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
    """
    Load temperature matrix .npy (must be a 2D numpy array).
    NOTE: assumed Celsius already. If your .npy is raw values, convert before calling analyze_local().
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Temp matrix file not found: {path}")

    raw = p.read_bytes()
    # Prefer allow_pickle=False for safety
    try:
        tm = np.load(io.BytesIO(raw), allow_pickle=False)
    except TypeError:
        # Compatibility for older numpy where allow_pickle might not exist
        tm = np.load(io.BytesIO(raw))

    if not isinstance(tm, np.ndarray) or tm.ndim != 2:
        raise ValueError(
            f"Temp matrix must be a 2D numpy array, got: {type(tm)} ndim={getattr(tm,'ndim',None)}"
        )

    return tm.astype(np.float32, copy=False)


def analyze_local(
    *,
    image_bytes: bytes,
    tm: Optional[np.ndarray],
    stride_y: int = 4,
    stride_x: int = 4,
    temperature: float = 0.1,
    max_tokens: int = 2000,
) -> Dict[str, Any]:
    """
    Run two-stage analysis for local files, returning:
      {
        "status": "done",
        "text": <summary>,
        "json": <legacy frontend-compatible>,
        "merged": <full merged stages + meta>
      }
    """
    tm_meta = _meta_from_tm(tm)

    injected_text = _build_injected_temperature_text(
        tm,
        tm_meta,
        stride_y=stride_y,
        stride_x=stride_x,
    )

    prompt_a = THERMAL_VISIBILITY_PROMPT
    prompt_b = THERMAL_HEALTH_PROMPT

    supports_inject_params = False
    try:
        # Preferred: qwen_vlm supports inject_text_a/inject_text_b
        result = analyze_image_two_prompts(
            image_bytes=image_bytes,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            temperature=temperature,
            max_tokens=max_tokens,
            inject_text_a=injected_text,
            inject_text_b=injected_text,
        )
        supports_inject_params = True
    except TypeError:
        # Fallback: prepend injected data to prompt
        prompt_a2 = injected_text + "\n\n" + prompt_a
        prompt_b2 = injected_text + "\n\n" + prompt_b
        result = analyze_image_two_prompts(
            image_bytes=image_bytes,
            prompt_a=prompt_a2,
            prompt_b=prompt_b2,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    a_text = result["prompt_a"]["content"]
    b_text = result["prompt_b"]["content"]

    a_json = _safe_parse_json(a_text)
    b_json = _safe_parse_json(b_text)

    merged: Dict[str, Any] = {
        "injected_temperature_meta": tm_meta,
        "injection_mode": "kwargs" if supports_inject_params else "prompt_prepend",
        "stage1": a_json if a_json is not None else {"raw_text": a_text},
        "stage2": b_json if b_json is not None else {"raw_text": b_text},
    }

    legacy_json = _normalize_for_frontend(
        {
            "injected_temperature_meta": tm_meta,
            "injection_mode": merged["injection_mode"],
            "stage1": merged["stage1"],
            "stage2": merged["stage2"],
        }
    )

    # Ensure we keep the raw merged object in legacy schema for debugging
    # (worker._normalize_for_frontend already puts "_raw": merged by default)
    report_text = legacy_json.get("summary") or "Analysis completed."

    return {
        "status": "done",
        "text": report_text,
        "json": legacy_json,
        "merged": merged,
    }


def _write_json(path: Path, obj: Any, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        s = json.dumps(obj, ensure_ascii=False, indent=2)
    else:
        s = json.dumps(obj, ensure_ascii=False)
    path.write_text(s, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Analyze local thermal image + optional temperature matrix (.npy), and save stage1/stage2 JSON files."
    )
    ap.add_argument("--image", required=True, help="Path to thermal image (jpg/png/...)")
    ap.add_argument("--tm", default=None, help="Path to temperature matrix .npy (2D, Celsius). Optional.")
    ap.add_argument("--out", default=None, help="Output JSON file path (optional). Also writes *_stage1.json and *_stage2.json.")
    ap.add_argument("--stride-y", type=int, default=4, help="Downsample stride in y (default: 4)")
    ap.add_argument("--stride-x", type=int, default=4, help="Downsample stride in x (default: 4)")
    ap.add_argument("--temperature", type=float, default=0.1, help="LLM sampling temperature (default: 0.1)")
    ap.add_argument("--max-tokens", type=int, default=2000, help="Max tokens for each prompt (default: 2000)")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = ap.parse_args()

    img_bytes = _load_image_bytes(args.image)
    tm = _load_tm_npy(args.tm) if args.tm else None

    result = analyze_local(
        image_bytes=img_bytes,
        tm=tm,
        stride_y=args.stride_y,
        stride_x=args.stride_x,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )

    # Always print to stdout
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))

    # Save files if requested
    if args.out:
        outp = Path(args.out)

        # 1) Full result
        _write_json(outp, result, args.pretty)

        # 2) Stage1/Stage2 only
        merged = result.get("merged") or {}
        stage1 = merged.get("stage1")
        stage2 = merged.get("stage2")

        stage1_path = outp.with_name(outp.stem + "_stage1.json")
        stage2_path = outp.with_name(outp.stem + "_stage2.json")

        _write_json(stage1_path, stage1, True)  # stage files pretty by default
        _write_json(stage2_path, stage2, True)


if __name__ == "__main__":
    main()