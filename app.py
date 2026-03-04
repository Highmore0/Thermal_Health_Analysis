# app.py
import time
import threading
import secrets
from pathlib import Path

from flask import Flask, Response, request, jsonify, abort, send_from_directory, redirect, url_for

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
    db_upsert_temp_matrix,
    db_get_temp_matrix_npy,
    db_delete_temp_matrix,
)

from backend.analysis.worker import analyze_photo

from dotenv import load_dotenv
load_dotenv()  


def create_app() -> Flask:
    BASE_DIR = Path(__file__).resolve().parent
    FRONTEND_DIR = BASE_DIR / "frontend"
    CAPTURE_DIR = FRONTEND_DIR / "pages" / "capture"
    PROCESS_DIR = FRONTEND_DIR / "pages" / "process"

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

    @app.route("/pages/process/")
    def process_page():
        return send_from_directory(str(PROCESS_DIR.resolve()), "index.html")

    # --------------------------
    # Snapshot:
    # --------------------------
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

        bundle = None
        if hasattr(cam, "get_latest_bundle_copy"):
            bundle = cam.get_latest_bundle_copy()

        if bundle is not None:
            Y0, tm0, _stats0 = bundle
        else:
            Y0 = cam.get_latest_y_copy()
            if Y0 is None:
                Y0 = cam.read_y_from_cap()
            tm0 = cam.get_latest_temp_c_copy() if hasattr(cam, "get_latest_temp_c_copy") else None

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


        if tm0 is not None:
            try:
                db_upsert_temp_matrix(photo_id, ts, tm0, compress="f16")
            except Exception:
                pass

        return jsonify({"ok": True, "id": photo_id, "ts": ts, "name": name, "has_temp": bool(tm0 is not None)})


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


    @app.get("/api/photo/<int:photo_id>.tm.npy")
    def api_photo_tm(photo_id: int):
        blob = db_get_temp_matrix_npy(photo_id)
        if blob is None:
            abort(404)
        resp = Response(blob, mimetype="application/octet-stream")
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Disposition"] = f'attachment; filename="photo_{photo_id}_tm.npy"'
        return resp

    # --------------------------
    # Analyze
    # --------------------------
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

        return api_analyze_photo(photo_id)

    @app.post("/api/analyze/<int:photo_id>")
    def api_analyze_photo(photo_id: int):
        db_upsert_analysis(photo_id, "queued", "Queued for analysis.", None)

        def run():
            try:
                db_upsert_analysis(photo_id, "running", "Analyzing...", None)
                analyze_photo(photo_id)
            except Exception as e:
                db_upsert_analysis(photo_id, "error", f"{type(e).__name__}: {e}", None)

        threading.Thread(target=run, daemon=True).start()
        return jsonify({"ok": True, "photo_id": photo_id, "status": "queued"})

    @app.route("/api/photo/<int:photo_id>", methods=["DELETE"])
    def api_delete_photo(photo_id: int):
        ok = db_delete_photo(photo_id)
        db_delete_analysis(photo_id)

        try:
            db_delete_temp_matrix(photo_id)
        except Exception:
            pass

        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "id": photo_id})

    @app.post("/api/upload_temp")
    def api_upload_temp():
        """
        Upload a local image (JPG/PNG) into TEMP storage and return a token.
        Frontend can redirect to /pages/process/?token=... for analysis.
        """
        temp_gc()

        # Expect multipart/form-data with a file field named "file"
        if "file" not in request.files:
            return jsonify({"error": "file required (multipart/form-data, field name: file)"}), 400

        f = request.files["file"]
        if f is None or not f.filename:
            return jsonify({"error": "empty file"}), 400

        # Read bytes
        raw = f.read()
        if not raw or len(raw) < 100:
            return jsonify({"error": "file too small"}), 400

        # Basic content-type check (best-effort; do not fully trust it)
        ct = (f.mimetype or "").lower()
        allowed = {"image/jpeg", "image/jpg", "image/png", "application/octet-stream"}
        if ct not in allowed:
            return jsonify({"error": f"unsupported content-type: {ct}"}), 415

        # Optional name from form field
        name = (request.form.get("name") or "").strip()[:128]

        # Keep a params schema similar to camera snapshots for downstream consistency
        ts = time.time()
        token = secrets.token_urlsafe(16)
        params = {
            "dev": config.DEV,
            "src_w": config.W, "src_h": config.H,
            "half": "upload",
            "mode": "upload",
            "cm": "upload",
            "stretch": "upload",
            "ow": None, "oh": None, "keep": None,
            "rot": 0, "flip": 0,
            "jpeg_quality": getattr(config, "JPEG_QUALITY", None),
            "source": "upload",
            "filename": f.filename,
            "content_type": ct,
        }

        # Store into TEMP. No temperature matrix for uploads (tm=None).
        # min/max/mean are unknown here; keep placeholders (won't block analysis).
        TEMP[token] = {
            "jpg": raw,
            "tm": None,
            "ts": ts,
            "name": name,
            "params": params,
            "min": 0,
            "max": 0,
            "mean": 0.0,
        }

        process_url = f"/pages/process/?token={token}"
        return jsonify({"ok": True, "token": token, "ts": ts, "has_temp": False, "process_url": process_url})

    # --------------------------
    # TEMP capture/save/analyze
    # --------------------------
    TEMP = {}  # token -> {"jpg": bytes, "tm": np.ndarray|None, "ts": float, ...}
    TEMP_TTL = 300  # seconds

    def temp_gc():
        now = time.time()
        dead = [k for k, v in TEMP.items() if now - v.get("ts", 0) > TEMP_TTL]
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

        bundle = None
        if hasattr(cam, "get_latest_bundle_copy"):
            bundle = cam.get_latest_bundle_copy()

        if bundle is not None:
            Y0, tm0, _stats0 = bundle
        else:
            Y0 = cam.get_latest_y_copy()
            if Y0 is None:
                Y0 = cam.read_y_from_cap()
            tm0 = cam.get_latest_temp_c_copy() if hasattr(cam, "get_latest_temp_c_copy") else None

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
            "tm": tm0,  
            "ts": ts,
            "name": name,
            "params": params,
            "min": int(info["min"]),
            "max": int(info["max"]),
            "mean": float(info["mean"]),
        }

        return jsonify({"ok": True, "token": token, "ts": ts, "has_temp": bool(tm0 is not None)})

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

        tm0 = item.get("tm")
        if tm0 is not None:
            try:
                db_upsert_temp_matrix(photo_id, ts, tm0, compress="f16")
            except Exception:
                pass

        TEMP.pop(token, None)
        return jsonify({"ok": True, "id": photo_id, "ts": ts, "name": item["name"], "has_temp": bool(tm0 is not None)})

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

        tm0 = item.get("tm")
        if tm0 is not None:
            try:
                db_upsert_temp_matrix(photo_id, ts, tm0, compress="f16")
            except Exception:
                pass

        TEMP.pop(token, None)

        db_upsert_analysis(photo_id, "queued", "Queued for analysis.", None)

        def run():
            try:
                db_upsert_analysis(photo_id, "running", "Analyzing...", None)
                analyze_photo(photo_id)
            except Exception as e:
                db_upsert_analysis(photo_id, "error", f"{type(e).__name__}: {e}", None)

        threading.Thread(target=run, daemon=True).start()

        return jsonify({"ok": True, "id": photo_id, "status": "queued", "has_temp": bool(tm0 is not None)})

    @app.get("/api/photo/<int:photo_id>/analysis")
    def api_get_analysis(photo_id: int):
        a = db_get_analysis(photo_id)
        if not a:
            return jsonify({"status": "none", "text": "No analysis available."}), 404
        return jsonify(a)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8080, threaded=True)