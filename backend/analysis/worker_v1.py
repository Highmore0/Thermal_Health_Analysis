# backend/analysis/worker.py
from __future__ import annotations

import io
import json
import re
from typing import Any, Dict, Optional, Tuple, List

import numpy as np

from backend.db.photos import (
    db_get_jpeg,
    db_upsert_analysis,
    db_get_temp_matrix_npy,
)
from backend.analysis.qwen_vlm import analyze_image_one_prompt
from backend.analysis.prompt_visible import (
    THERMAL_VISIBILITY_PROMPT)


# -----------------------------
# JSON parsing helpers
# -----------------------------
def _strip_fenced_code_block(text: str) -> str:
    """Remove ```json ... ``` (or ``` ... ```) fenced block if present."""
    if text is None:
        return ""
    s = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return s


def _safe_parse_json(text: str) -> Optional[Any]:
    """Safely parse JSON from model output (supports fenced code blocks)."""
    s = _strip_fenced_code_block(text)
    try:
        return json.loads(s)
    except Exception:
        return None


def _pick(d: Any, *keys: str, default: Any = None) -> Any:
    """Pick first existing key from a dict-like object."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d:
            return d[k]
    return default


# -----------------------------
# Temperature matrix helpers
# -----------------------------
def _load_temp_matrix(photo_id: int) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Load temperature matrix from DB as numpy array (Celsius).
    Returns (tm, meta).
    """
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

    blob = db_get_temp_matrix_npy(photo_id)
    if blob is None:
        return None, meta

    if isinstance(blob, memoryview):
        blob = blob.tobytes()

    try:
        tm = np.load(io.BytesIO(blob))
    except Exception:
        return None, meta

    if not isinstance(tm, np.ndarray) or tm.ndim != 2:
        return None, meta

    tm = tm.astype(np.float32, copy=False)

    # Flatten finite values for robust stats
    flat = tm[np.isfinite(tm)]
    if flat.size == 0:
        return None, meta

    meta.update({
        "matrix_available": True,
        "matrix_shape": [int(tm.shape[0]), int(tm.shape[1])],
        "max_temp": float(np.max(flat)),
        "min_temp": float(np.min(flat)),
        "mean_temp": float(np.mean(flat)),
        "p10": float(np.percentile(flat, 10)),
        "p50": float(np.percentile(flat, 50)),
        "p90": float(np.percentile(flat, 90)),
    })
    return tm, meta


def _downsample_matrix(tm: np.ndarray, stride_y: int, stride_x: int) -> np.ndarray:
    """Downsample 2D matrix by striding."""
    stride_y = max(1, int(stride_y))
    stride_x = max(1, int(stride_x))
    return tm[::stride_y, ::stride_x].astype(np.float32, copy=False)


def _region_stat(block: np.ndarray) -> Dict[str, Any]:
    """Compute robust region stats ignoring NaNs."""
    flat = block[np.isfinite(block)]
    if flat.size == 0:
        return {
            "coverage": 0.0,
            "mean": None,
            "min": None,
            "max": None,
            "p10": None,
            "p50": None,
            "p90": None,
        }
    return {
        "coverage": float(flat.size / block.size) if block.size > 0 else 0.0,
        "mean": float(np.mean(flat)),
        "min": float(np.min(flat)),
        "max": float(np.max(flat)),
        "p10": float(np.percentile(flat, 10)),
        "p50": float(np.percentile(flat, 50)),
        "p90": float(np.percentile(flat, 90)),
    }


def _compute_region_grid_stats(tm_small: np.ndarray) -> List[Dict[str, Any]]:
    """
    Compute coarse region stats by splitting the matrix into approximate body zones.
    This is not true anatomical segmentation; it is a grid-based approximation.
    """
    h, w = tm_small.shape

    def clip(a, lo, hi):
        return max(lo, min(hi, a))

    # Define y bands (fractions)
    # These are heuristics: head/neck/top trunk/mid trunk/pelvis/legs/feet.
    y0 = 0
    y_head_end = clip(int(round(h * 0.15)), 1, h)
    y_neck_end = clip(int(round(h * 0.25)), 1, h)
    y_chest_end = clip(int(round(h * 0.45)), 1, h)
    y_abd_end = clip(int(round(h * 0.60)), 1, h)
    y_pelvis_end = clip(int(round(h * 0.72)), 1, h)
    y_thigh_end = clip(int(round(h * 0.86)), 1, h)
    y_end = h

    # Define x zones
    x_left = 0
    x_left_end = clip(int(round(w * 0.33)), 1, w)
    x_center_end = clip(int(round(w * 0.67)), 1, w)
    x_end = w

    regions = []

    def add_region(name: str, ys: Tuple[int, int], xs: Tuple[int, int], note: str):
        y1, y2 = ys
        x1, x2 = xs
        if y2 <= y1 or x2 <= x1:
            return
        blk = tm_small[y1:y2, x1:x2]
        st = _region_stat(blk)
        regions.append({
            "name": name,
            "y_range": [int(y1), int(y2)],
            "x_range": [int(x1), int(x2)],
            **st,
            "note": note,
        })

    # Center trunk bands
    add_region("head_center", (y0, y_head_end), (x_left_end, x_center_end),
               "Approx head/face zone (grid-based).")
    add_region("neck_center", (y_head_end, y_neck_end), (x_left_end, x_center_end),
               "Approx neck zone (grid-based).")
    add_region("chest_center", (y_neck_end, y_chest_end), (x_left_end, x_center_end),
               "Approx upper trunk zone (grid-based).")
    add_region("abdomen_center", (y_chest_end, y_abd_end), (x_left_end, x_center_end),
               "Approx mid trunk zone (grid-based).")
    add_region("pelvis_center", (y_abd_end, y_pelvis_end), (x_left_end, x_center_end),
               "Approx pelvis/hip zone (grid-based).")

    # Lower body center
    add_region("thighs_center", (y_pelvis_end, y_thigh_end), (x_left_end, x_center_end),
               "Approx upper legs zone (grid-based).")
    add_region("lower_legs_center", (y_thigh_end, y_end), (x_left_end, x_center_end),
               "Approx lower legs/feet zone (grid-based).")

    # Side zones (often include arms/outer legs + background; still useful for asymmetry hints)
    add_region("left_side_mid", (y_neck_end, y_pelvis_end), (x_left, x_left_end),
               "Approx left side mid-body (may include left arm/background).")
    add_region("right_side_mid", (y_neck_end, y_pelvis_end), (x_center_end, x_end),
               "Approx right side mid-body (may include right arm/background).")

    add_region("left_side_lower", (y_pelvis_end, y_end), (x_left, x_left_end),
               "Approx left lower side (may include left leg/background).")
    add_region("right_side_lower", (y_pelvis_end, y_end), (x_center_end, x_end),
               "Approx right lower side (may include right leg/background).")

    return regions


def _find_hot_cold_points(tm_small: np.ndarray) -> Dict[str, Any]:
    """Find approximate hotspot/coldspot coordinates on downsampled matrix."""
    flat_mask = np.isfinite(tm_small)
    if not np.any(flat_mask):
        return {"hotspot": None, "coldspot": None}

    # Use masked values
    vals = np.where(flat_mask, tm_small, np.nan)

    hot_idx = np.nanargmax(vals)
    cold_idx = np.nanargmin(vals)

    h, w = tm_small.shape
    hy, hx = divmod(int(hot_idx), int(w))
    cy, cx = divmod(int(cold_idx), int(w))

    return {
        "hotspot": {"y": int(hy), "x": int(hx), "temp_c": float(tm_small[hy, hx])},
        "coldspot": {"y": int(cy), "x": int(cx), "temp_c": float(tm_small[cy, cx])},
    }


def _compute_indices(region_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute a few simple indices from region stats.
    These are heuristic and depend on grid-based regions.
    """
    def get_mean(name: str) -> Optional[float]:
        for r in region_stats:
            if r.get("name") == name:
                return r.get("mean")
        return None

    chest = get_mean("chest_center")
    abdomen = get_mean("abdomen_center")
    trunk = None
    if chest is not None and abdomen is not None:
        trunk = (chest + abdomen) / 2.0
    elif chest is not None:
        trunk = chest
    elif abdomen is not None:
        trunk = abdomen

    lower = get_mean("lower_legs_center")

    left_mid = get_mean("left_side_mid")
    right_mid = get_mean("right_side_mid")

    left_right_mid_diff = None
    if left_mid is not None and right_mid is not None:
        left_right_mid_diff = float(left_mid - right_mid)

    distal_vs_trunk = None
    if trunk is not None and lower is not None:
        distal_vs_trunk = float(lower - trunk)

    return {
        "left_right_mid_mean_diff_c": left_right_mid_diff,
        "distal_minus_trunk_c": distal_vs_trunk,
        "notes": [
            "Indices are grid-based approximations; clothing/background may affect values."
        ],
    }


def _build_injected_temperature_text(
    tm: Optional[np.ndarray],
    meta: Dict[str, Any],
    *,
    stride_y: int = 4,
    stride_x: int = 4,
) -> str:
    """
    Build injected JSON text with:
    - global stats
    - downsampled matrix
    - coarse region grid stats
    - hotspot/coldspot and simple indices
    """
    payload: Dict[str, Any] = {
        "unit": meta.get("unit", "C"),
        "matrix_available": bool(meta.get("matrix_available")),
        "original_shape": meta.get("matrix_shape"),
        "max_temp": meta.get("max_temp"),
        "min_temp": meta.get("min_temp"),
        "mean_temp": meta.get("mean_temp"),
        "p10": meta.get("p10"),
        "p50": meta.get("p50"),
        "p90": meta.get("p90"),
        "downsample_stride": [int(stride_y), int(stride_x)],
        "downsampled_shape": None,
        "temp_matrix_downsampled": None,
        "region_grid_stats": [],
        "points": {"hotspot": None, "coldspot": None},
        "indices": {},
    }

    if tm is None:
        return "SYSTEM_TEMPERATURE_FEATURES_JSON=" + json.dumps(payload, ensure_ascii=False)

    tm_small = _downsample_matrix(tm, stride_y, stride_x)
    payload["downsampled_shape"] = [int(tm_small.shape[0]), int(tm_small.shape[1])]

    # Keep downsampled matrix as list of lists (manageable)
    payload["temp_matrix_downsampled"] = tm_small.tolist()

    # Coarse grid-based region stats
    regions = _compute_region_grid_stats(tm_small)
    payload["region_grid_stats"] = regions

    # Hot/cold points on downsampled grid
    payload["points"] = _find_hot_cold_points(tm_small)

    # Simple derived indices
    payload["indices"] = _compute_indices(regions)

    return "SYSTEM_TEMPERATURE_FEATURES_JSON=" + json.dumps(payload, ensure_ascii=False)


# -----------------------------
# Frontend compatibility mapping
# -----------------------------
def _normalize_for_frontend(merged: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize two-stage outputs into the legacy schema expected by the frontend UI.
    """
    stage1 = merged.get("stage1") if isinstance(merged.get("stage1"), dict) else {}
    stage2 = merged.get("stage2") if isinstance(merged.get("stage2"), dict) else {}

    # Some UIs still expect is_thermal; keep as None unless your prompt includes it
    is_thermal = _pick(stage1, "is_thermal", default=None)

    visibility_assessment = _pick(stage1, "occlusion_assessment", "visibility_assessment", default=None)

    # Keep "temperature_analysis" as the stage1 abnormality screen (or temperature_analysis)
    temperature_analysis = _pick(
        stage1,
        "temperature_abnormality_screen",
        "temperature_analysis",
        default=None
    )

    overall_risk_level = _pick(stage2, "overall_risk_level", default="unknown")
    summary = _pick(stage2, "summary", default="")

    out: Dict[str, Any] = {
        "is_thermal": is_thermal,
        "overall_risk_level": overall_risk_level,
        "summary": summary,
        "visibility_assessment": visibility_assessment,
        "temperature_analysis": temperature_analysis,
        "health_advice": _pick(stage2, "health_advice", default=None),
        "fat_distribution_inference": _pick(stage2, "fat_distribution_inference", default=None),
        "pattern_findings": _pick(stage2, "pattern_findings", default=None),
        "_raw": merged,
    }

    return {k: v for k, v in out.items() if v is not None}


# -----------------------------
# Main entry
# -----------------------------
def analyze_photo(photo_id: int) -> Dict[str, Any]:
    """
    Two-stage analysis:
    - Inject downsampled matrix + region stats into both stages
    - Parse JSON outputs
    - Store legacy-compatible JSON for frontend rendering
    """
    jpg = db_get_jpeg(photo_id)
    if jpg is None:
        raise RuntimeError(f"Photo not found: {photo_id}")

    if isinstance(jpg, memoryview):
        jpg = jpg.tobytes()

    # Load temperature matrix and compute global stats
    tm, tm_meta = _load_temp_matrix(photo_id)

    # Build injected features text (downsample + region stats)
    injected_text = _build_injected_temperature_text(
        tm,
        tm_meta,
        stride_y=4,
        stride_x=4,
    )

    prompt_a = THERMAL_VISIBILITY_PROMPT
    prompt_b = THERMAL_HEALTH_PROMPT

    supports_inject_params = False
    try:
        # Preferred: qwen_vlm supports inject_text_a/inject_text_b
        result = analyze_image_two_prompts(
            image_bytes=jpg,
            prompt_a=prompt_a,
            prompt_b=prompt_b,
            temperature=0.1,
            max_tokens=2000,
            inject_text_a=injected_text,
            inject_text_b=injected_text,
        )
        supports_inject_params = True
    except TypeError:
        # Fallback: prepend injected data to the prompt
        prompt_a2 = injected_text + "\n\n" + prompt_a
        prompt_b2 = injected_text + "\n\n" + prompt_b
        result = analyze_image_two_prompts(
            image_bytes=jpg,
            prompt_a=prompt_a2,
            prompt_b=prompt_b2,
            temperature=0.1,
            max_tokens=2000,
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

    legacy_json = _normalize_for_frontend(merged)
    report_text = legacy_json.get("summary") or "Analysis completed."

    db_upsert_analysis(photo_id, "done", report_text, legacy_json)

    return {"status": "done", "text": report_text, "json": legacy_json}