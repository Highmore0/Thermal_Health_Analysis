import json
from backend.db.photos import db_get_jpeg, db_upsert_analysis
from backend.analysis.qwen_vlm import analyze_image
from backend.analysis.prompts import THERMAL_ANALYSIS_PROMPT

def analyze_photo(photo_id: int):
    jpg = db_get_jpeg(photo_id)
    if jpg is None:
        raise ValueError("photo not found")

    # sqlite BLOB 可能是 memoryview
    if isinstance(jpg, memoryview):
        jpg = jpg.tobytes()

    raw = analyze_image(jpg, THERMAL_ANALYSIS_PROMPT)
    content = raw["choices"][0]["message"]["content"]

    # 1) 先尝试把模型输出当 JSON
    json_obj = None
    try:
        json_obj = json.loads(content)
        report_text = json_obj.get("summary") or content
    except Exception:
        report_text = content

    # 2) 落库：done + text + json
    db_upsert_analysis(photo_id, "done", report_text, json_obj)

    return {"status": "done", "text": report_text, "json": json_obj}
