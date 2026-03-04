#!/usr/bin/env python3
# backend/analysis/analyze_local_stage2.py

"""
Stage2 local test:
- Input: thermal image + stage2 prompt + full stage1 output JSON
- Output: ONLY stage2 result (parsed JSON or {"raw_text": ...})

Usage:

1) stage1 -> stage2
python3 -m backend.analysis.analyze_local_stage1 \
  --image ./test/test1.jpg \
  --out ./test_results/test1_stage1.json \
  --pretty

python3 -m backend.analysis.analyze_local_stage2 \
  --image ./test/test1.jpg \
  --stage1 ./test_results/test1_stage1.json \
  --out ./test_results/test1_stage2.json \
  --pretty
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

from backend.analysis.qwen_vlm import analyze_image_one_prompt

from backend.analysis.prompt_health import THERMAL_HEALTH_PROMPT  

# Reuse worker helper for robust JSON parsing
from backend.analysis.worker import _safe_parse_json  # noqa: F401


def _load_image_bytes(path: str) -> bytes:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Image not found: {path}")
    return p.read_bytes()


def _read_json_file(path: str) -> Any:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Stage1 JSON not found: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"Failed to parse stage1 JSON: {path}: {e}") from e


def _build_stage2_inject_text(
    *,
    stage1_obj: Any,
    extra_inject_text: Optional[str] = None,
) -> str:
    """
    Build injected text for stage2:
    - includes full stage1 output JSON (verbatim)
    - optional extra injection text (e.g. temperature matrix features) can be appended
    """
    stage1_str = json.dumps(stage1_obj, ensure_ascii=False, separators=(",", ":"))

    parts = [
        "### Stage1 Output (Full JSON)\n"
        "Below is the full JSON output from stage1. Use it as structured evidence for stage2.\n"
        f"{stage1_str}"
    ]

    if extra_inject_text:
        parts.append("\n\n### Extra Injected Data\n" + extra_inject_text.strip())

    return "\n\n".join(parts).strip()


def analyze_local_stage2(
    *,
    image_bytes: bytes,
    stage1_obj: Any,
    stage2_prompt: str,
    temperature: float = 0.1,
    max_tokens: int = 2000,
    extra_inject_text: Optional[str] = None,
) -> Dict[str, Any]:
    inject_text = _build_stage2_inject_text(stage1_obj=stage1_obj, extra_inject_text=extra_inject_text)

    resp = analyze_image_one_prompt(
        image_bytes=image_bytes,
        prompt=stage2_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        inject_text=inject_text,
    )

    stage2_text = resp["content"]
    stage2_json = _safe_parse_json(stage2_text)

    return stage2_json if stage2_json is not None else {"raw_text": stage2_text}


def _write_json(path: Path, obj: Any, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = json.dumps(obj, ensure_ascii=False, indent=2) if pretty else json.dumps(obj, ensure_ascii=False)
    path.write_text(s, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze local thermal image using ONLY stage2 (stage1 JSON as input).")
    ap.add_argument("--image", required=True, help="Path to thermal image (jpg/png/...)")
    ap.add_argument("--stage1", required=True, help="Path to stage1 output JSON file (full object).")
    ap.add_argument("--out", default=None, help="Output JSON file path (optional).")
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--pretty", action="store_true")

    # 如果你后面还想把温度矩阵特征也塞给 stage2（可选），可以加一个 --inject-file 或 --tm 参数
    # ap.add_argument("--inject-file", default=None, help="Optional path to extra injected text file.")

    args = ap.parse_args()

    img_bytes = _load_image_bytes(args.image)
    stage1_obj = _read_json_file(args.stage1)

    stage2_only = analyze_local_stage2(
        image_bytes=img_bytes,
        stage1_obj=stage1_obj,
        stage2_prompt=THERMAL_HEALTH_PROMPT,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        extra_inject_text=None,
    )

    print(json.dumps(stage2_only, ensure_ascii=False, indent=2 if args.pretty else None))

    if args.out:
        _write_json(Path(args.out), stage2_only, args.pretty)


if __name__ == "__main__":
    main()