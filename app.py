import time
from flask import Flask, Response, request, jsonify, abort, send_from_directory, redirect

from backend.capture.camera import CameraService, parse_int
from backend import config
from backend.db.photos import (
    db_init,
    db_insert_photo,
    db_list_photos,
    db_get_jpeg,
    db_delete_photo,
    db_delete_analysis,
    db_upsert_analysis,
    db_get_analysis,
)

from flask import Flask, jsonify
from backend.analysis.worker import analyze_photo

from pathlib import Path

import threading
from backend.db.photos import db_upsert_analysis
from backend.analysis.worker import analyze_photo

from dotenv import load_dotenv
load_dotenv()  # 默认会找当前工作目录下的 .env


def create_app() -> Flask:
    BASE_DIR = Path(__file__).resolve().parent
    FRONTEND_DIR = BASE_DIR / "frontend"
    CAPTURE_DIR = FRONTEND_DIR / "pages" / "capture"

    app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")

    cam = CameraService()
    db_init()
    cam.start_background()

    @app.route("/")
    def root():
        return redirect("/pages/capture/")

    @app.route("/pages/capture/")
    def capture_page():
        index = CAPTURE_DIR / "index.html"
        if not index.exists():
            return f"index.html not found at {index}", 404
        return send_from_directory(str(CAPTURE_DIR), "index.html")

    # ---------- API ----------
    @app.get("/api/stats")
    def api_stats():
        return jsonify(cam.get_latest_stats())

    @app.get("/api/video.mjpg")
    def api_video():
        half = request.args.get("half", config.DEFAULT_HALF)
        mode = request.args.get("mode", config.DEFAULT_MODE)
        cm = request.args.get("cm", config.DEFAULT_CM)
        stretch = request.args.get("stretch", config.DEFAULT_STRETCH)

        ow = parse_int(request.args.get("ow", config.DEFAULT_OW), config.DEFAULT_OW)
        oh = parse_int(request.args.get("oh", config.DEFAULT_OH), config.DEFAULT_OH)
        keep = request.args.get("keep", config.DEFAULT_KEEP) == "1"

        rot = parse_int(request.args.get("rot", config.DEFAULT_ROT), parse_int(config.DEFAULT_ROT, 0))
        flip = parse_int(request.args.get("flip", config.DEFAULT_FLIP), parse_int(config.DEFAULT_FLIP, 0))

        def gen():
            boundary = b"--frame\r\n"
            while True:
                Y = cam.read_y_from_cap()
                if Y is None:
                    time.sleep(0.02)
                    continue

                Yh = cam.select_half(Y, half)
                jpg, info = cam.render_frame(Yh, mode, cm, stretch, ow, oh, keep, rot, flip)
                if jpg is None:
                    time.sleep(0.01)
                    continue

                # 更新 stats
                with cam.lock:
                    cam.latest_info.update({
                        "dev": config.DEV,
                        "src_shape": (config.H, config.W),
                        "half": half,
                        "out_shape": (int(info["out_shape"][0]), int(info["out_shape"][1])),
                        "min": int(info["min"]),
                        "max": int(info["max"]),
                        "mean": float(info["mean"]),
                        "mode": mode, "cm": cm, "stretch": stretch,
                        "keep": int(keep),
                        "rot": int(rot),
                        "flip": int(flip),
                    })

                header = (
                    b"Content-Type: image/jpeg\r\n"
                    + b"Content-Length: " + str(len(jpg)).encode("ascii") + b"\r\n\r\n"
                )
                yield boundary + header + jpg + b"\r\n"
                time.sleep(0.001)

        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/pages/history/")
    def history_page():
        return send_from_directory(str((FRONTEND_DIR / "pages/history").resolve()), "index.html")


    @app.post("/api/snapshot")
    def api_snapshot():
        try:
            payload = request.get_json(force=True, silent=False) or {}
        except Exception:
            return jsonify({"error": "bad json"}), 400

        half = str(payload.get("half", config.DEFAULT_HALF))
        mode = str(payload.get("mode", config.DEFAULT_MODE))
        cm = str(payload.get("cm", config.DEFAULT_CM))
        stretch = str(payload.get("stretch", config.DEFAULT_STRETCH))
        ow = parse_int(payload.get("ow", config.DEFAULT_OW), config.DEFAULT_OW)
        oh = parse_int(payload.get("oh", config.DEFAULT_OH), config.DEFAULT_OH)
        keep = str(payload.get("keep", config.DEFAULT_KEEP)) == "1"
        rot = parse_int(payload.get("rot", config.DEFAULT_ROT), parse_int(config.DEFAULT_ROT, 0))
        flip = parse_int(payload.get("flip", config.DEFAULT_FLIP), parse_int(config.DEFAULT_FLIP, 0))

        name = str(payload.get("name", "")).strip()
        if len(name) > 128:
            name = name[:128]

        Y0 = cam.get_latest_y_copy()
        if Y0 is None:
            Y0 = cam.read_y_from_cap()
        if Y0 is None:
            return jsonify({"error": "no frame"}), 503

        Yh = cam.select_half(Y0, half)
        jpg, info = cam.render_frame(Yh, mode, cm, stretch, ow, oh, keep, rot, flip)
        if jpg is None:
            return jsonify({"error": "encode fail"}), 500

        ts = time.time()
        params = {
            "dev": config.DEV,
            "src_w": config.W, "src_h": config.H,
            "half": half,
            "mode": mode, "cm": cm, "stretch": stretch,
            "ow": ow, "oh": oh, "keep": int(keep),
            "rot": int(rot), "flip": int(flip),
            "jpeg_quality": config.JPEG_QUALITY,
        }

        photo_id = db_insert_photo(ts, name, int(info["min"]), int(info["max"]), float(info["mean"]), params, jpg)
        return jsonify({"ok": True, "id": photo_id, "ts": ts, "name": name})

    # （可选）给你后面 gallery/history 用
    @app.get("/api/photos")
    def api_photos():
        limit = parse_int(request.args.get("limit", 200), 200)
        return jsonify({"photos": db_list_photos(limit=limit)})

    @app.get("/api/photo/<int:photo_id>.jpg")
    def api_photo(photo_id: int):
        jpg = db_get_jpeg(photo_id)
        if jpg is None:
            abort(404)

        # sqlite may return memoryview for BLOB; force bytes
        if isinstance(jpg, memoryview):
            jpg = jpg.tobytes()

        resp = Response(jpg, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    
    PROCESS_DIR = FRONTEND_DIR / "pages" / "process"

    @app.route("/pages/process/")
    def process_page():
        return send_from_directory(str(PROCESS_DIR.resolve()), "index.html")
    
    @app.post("/api/analyze")
    def api_analyze():
        try:
            payload = request.get_json(force=True, silent=False) or {}
        except Exception:
            return jsonify({"error": "bad json"}), 400

        photo_id = payload.get("photo_id")
        if photo_id is None:
            return jsonify({"error": "photo_id required"}), 400

        try:
            photo_id = int(photo_id)
        except Exception:
            return jsonify({"error": "photo_id must be int"}), 400

        # 直接复用同一套逻辑：调用上面的 /api/analyze/<id>
        return api_analyze_photo(photo_id)

    
    @app.post("/api/analyze/<int:photo_id>")
    def api_analyze_photo(photo_id: int):
        # 1) 立刻落库 queued（让前端能立刻看到“已开始/排队”）
        db_upsert_analysis(photo_id, "queued", "Queued for analysis.", None)

        # 2) 后台线程跑分析
        def run():
            try:
                db_upsert_analysis(photo_id, "running", "Analyzing...", None)
                analyze_photo(photo_id)  # worker 内部会写 done
            except Exception as e:
                db_upsert_analysis(photo_id, "error", f"{type(e).__name__}: {e}", None)

        threading.Thread(target=run, daemon=True).start()

        # 3) 立即返回
        return jsonify({"ok": True, "photo_id": photo_id, "status": "queued"})

    @app.route("/api/photo/<int:photo_id>", methods=["DELETE"])
    def api_delete_photo(photo_id: int):
        ok = db_delete_photo(photo_id)
        db_delete_analysis(photo_id)
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "id": photo_id})


    import secrets

    TEMP = {}  # token -> {"jpg": bytes, "ts": float, "name": str, "params": dict, "min": int, "max": int, "mean": float}
    TEMP_TTL = 300  # seconds
    
    def temp_gc():
        now = time.time()
        dead = [k for k,v in TEMP.items() if now - v.get("ts", 0) > TEMP_TTL]
        for k in dead:
            TEMP.pop(k, None)
    
    @app.post("/api/capture_temp")
    def api_capture_temp():
        temp_gc()
        try:
            payload = request.get_json(force=True, silent=False) or {}
        except Exception:
            return jsonify({"error": "bad json"}), 400

        half = str(payload.get("half", config.DEFAULT_HALF))
        mode = str(payload.get("mode", config.DEFAULT_MODE))
        cm = str(payload.get("cm", config.DEFAULT_CM))
        stretch = str(payload.get("stretch", config.DEFAULT_STRETCH))
        ow = parse_int(payload.get("ow", config.DEFAULT_OW), config.DEFAULT_OW)
        oh = parse_int(payload.get("oh", config.DEFAULT_OH), config.DEFAULT_OH)
        keep = str(payload.get("keep", config.DEFAULT_KEEP)) == "1"
        rot = parse_int(payload.get("rot", config.DEFAULT_ROT), parse_int(config.DEFAULT_ROT, 0))
        flip = parse_int(payload.get("flip", config.DEFAULT_FLIP), parse_int(config.DEFAULT_FLIP, 0))

        name = str(payload.get("name", "")).strip()[:128]

        Y0 = cam.get_latest_y_copy()
        if Y0 is None:
            Y0 = cam.read_y_from_cap()

        if Y0 is None:
            return jsonify({"error": "no frame"}), 503

        Yh = cam.select_half(Y0, half)
        jpg, info = cam.render_frame(Yh, mode, cm, stretch, ow, oh, keep, rot, flip)
        if jpg is None:
            return jsonify({"error": "encode fail"}), 500

        token = secrets.token_urlsafe(16)
        ts = time.time()

        params = {
            "dev": config.DEV,
            "src_w": config.W, "src_h": config.H,
            "half": half,
            "mode": mode, "cm": cm, "stretch": stretch,
            "ow": ow, "oh": oh, "keep": int(keep),
            "rot": int(rot), "flip": int(flip),
            "jpeg_quality": config.JPEG_QUALITY,
        }

        TEMP[token] = {
            "jpg": jpg,
            "ts": ts,
            "name": name,
            "params": params,
            "min": int(info["min"]),
            "max": int(info["max"]),
            "mean": float(info["mean"]),
        }

        return jsonify({"ok": True, "token": token, "ts": ts})

    @app.get("/api/temp/<token>.jpg")
    def api_temp_jpg(token: str):
        temp_gc()
        item = TEMP.get(token)
        if not item:
            abort(404)
        return Response(item["jpg"], mimetype="image/jpeg")

    @app.post("/api/save_temp")
    def api_save_temp():
        temp_gc()
        try:
            payload = request.get_json(force=True, silent=False) or {}
        except Exception:
            return jsonify({"error": "bad json"}), 400

        token = str(payload.get("token", "")).strip()
        item = TEMP.get(token)
        if not item:
            return jsonify({"error": "temp token not found"}), 404

        ts = item["ts"]
        photo_id = db_insert_photo(
            ts,
            item["name"],
            item["min"],
            item["max"],
            item["mean"],
            item["params"],
            item["jpg"],
        )

        # Optional: keep TEMP or delete it. I'd delete to avoid duplicates.
        TEMP.pop(token, None)

        return jsonify({"ok": True, "id": photo_id, "ts": ts, "name": item["name"]})

    
    @app.post("/api/analyze_temp")
    def api_analyze_temp():
        temp_gc()
        try:
            payload = request.get_json(force=True, silent=False) or {}
        except Exception:
            return jsonify({"error": "bad json"}), 400

        token = str(payload.get("token", "")).strip()
        item = TEMP.get(token)
        if not item:
            return jsonify({"error": "temp token not found"}), 404

        # 1) 先保存到 DB（永远优先保存）
        ts = item["ts"]
        photo_id = db_insert_photo(
            ts,
            item["name"],
            item["min"],
            item["max"],
            item["mean"],
            item["params"],
            item["jpg"],
        )

        # 保存完就删 TEMP，避免重复保存
        TEMP.pop(token, None)

        # 2) 写入 queued 状态（前端开始轮询）
        db_upsert_analysis(photo_id, "queued", "Queued for analysis.", None)

        # 3) 后台线程执行分析（不要阻塞 HTTP）
        def run():
            try:
                db_upsert_analysis(photo_id, "running", "Analyzing...", None)
                analyze_photo(photo_id)  # 你的 worker 成功会写 done
            except Exception as e:
                db_upsert_analysis(photo_id, "error", f"{type(e).__name__}: {e}", None)

        threading.Thread(target=run, daemon=True).start()

        # 4) 立刻返回给前端：已保存 + 已入队
        return jsonify({"ok": True, "id": photo_id, "status": "queued"})


    @app.get("/api/photo/<int:photo_id>/analysis")
    def api_get_analysis(photo_id: int):
        a = db_get_analysis(photo_id)  # -> {"status":..., "text":...} or None
        if not a:
            return jsonify({"status": "none", "text": "No analysis available."}), 404
        return jsonify(a)


    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8080, threaded=True)
