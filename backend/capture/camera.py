import time
import threading
import subprocess
from typing import Dict, Optional, Tuple

import numpy as np
import cv2

from backend import config

CMAP = {
    "inferno": cv2.COLORMAP_INFERNO,
    "turbo":   cv2.COLORMAP_TURBO,
    "jet":     cv2.COLORMAP_JET,
    "plasma":  cv2.COLORMAP_PLASMA,
    "viridis": cv2.COLORMAP_VIRIDIS,
}

def parse_int(v, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default

class CameraService:
    """
    - 后台线程持续抓帧，缓存 latest_Y_full + latest_info
    - video.mjpg 路由可直接 read cap.bin（和你原始代码一致）
    """
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.latest_Y_full: Optional[np.ndarray] = None
        self.latest_info: Dict = {
            "dev": config.DEV,
            "src_shape": (config.H, config.W),
            "half": config.DEFAULT_HALF,
            "out_shape": (config.DEFAULT_OH, config.DEFAULT_OW),
            "min": 0, "max": 0, "mean": 0.0,
            "mode": config.DEFAULT_MODE, "cm": config.DEFAULT_CM, "stretch": config.DEFAULT_STRETCH,
            "keep": int(config.DEFAULT_KEEP),
            "rot": int(config.DEFAULT_ROT),
            "flip": int(config.DEFAULT_FLIP),
        }

    # --------- capture low-level ---------
    def grab_one_frame_to_file(self) -> bool:
        r = subprocess.run(
            ["sudo", "v4l2-ctl", "-d", config.DEV,
             "--stream-mmap", "--stream-count=1", f"--stream-to={config.CAP_PATH}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return r.returncode == 0

    def read_y_from_cap(self) -> Optional[np.ndarray]:
        try:
            data = open(config.CAP_PATH, "rb").read()
        except Exception:
            return None
        if len(data) != config.W * config.H * 2:
            return None
        b = np.frombuffer(data, dtype=np.uint8).reshape(config.H, config.W * 2)
        return b[:, 0::2].copy()

    def looks_like_stacked_two_views(self, Y: np.ndarray) -> bool:
        h, _ = Y.shape
        if h % 2 != 0:
            return False
        top = Y[:h//2, :]
        bot = Y[h//2:, :]
        mt, mb = float(top.mean()), float(bot.mean())
        vt, vb = float(top.var()), float(bot.var())
        return (abs(mt - mb) > 3.0) or (abs(vt - vb) > 50.0)

    def select_half(self, Y: np.ndarray, half: str) -> np.ndarray:
        h, _ = Y.shape
        if half == "full":
            return Y
        if h % 2 != 0:
            return Y
        if half == "top":
            return Y[:h//2, :]
        if half == "bottom":
            return Y[h//2:, :]
        if self.looks_like_stacked_two_views(Y):
            return Y[h//2:, :]
        return Y

    def apply_rot_flip(self, img: np.ndarray, rot: int, flip: int) -> np.ndarray:
        r = rot % 360
        if r == 90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif r == 180:
            img = cv2.rotate(img, cv2.ROTATE_180)
        elif r == 270:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

        if flip == 1:
            img = cv2.flip(img, 1)
        elif flip == 2:
            img = cv2.flip(img, 0)
        elif flip == 3:
            img = cv2.flip(img, -1)
        return img

    def resize_output(self, img: np.ndarray, ow: int, oh: int, keep: bool) -> np.ndarray:
        if ow <= 0 or oh <= 0:
            return img
        h, w = img.shape[:2]
        if w <= 0 or h <= 0:
            return img

        if not keep:
            return cv2.resize(img, (ow, oh), interpolation=cv2.INTER_NEAREST)

        scale = min(ow / w, oh / h)
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_NEAREST)

        if img.ndim == 2:
            canvas = np.zeros((oh, ow), dtype=np.uint8)
        else:
            canvas = np.zeros((oh, ow, 3), dtype=np.uint8)

        x0 = (ow - nw) // 2
        y0 = (oh - nh) // 2
        canvas[y0:y0+nh, x0:x0+nw] = resized
        return canvas

    def render_frame(self, Y: np.ndarray, mode: str, cm: str, stretch: str,
                     ow: int, oh: int, keep: bool, rot: int, flip: int) -> Tuple[Optional[bytes], Dict]:
        ymin, ymax = int(Y.min()), int(Y.max())
        ymean = float(Y.mean())

        if stretch == "1":
            show_u8 = cv2.normalize(Y, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        else:
            show_u8 = Y.astype(np.uint8)

        show_u8 = self.apply_rot_flip(show_u8, rot, flip)

        if mode == "gray":
            out = self.resize_output(show_u8, ow, oh, keep)
        else:
            cmap = CMAP.get(cm, cv2.COLORMAP_INFERNO)
            out = cv2.applyColorMap(show_u8, cmap)
            out = self.resize_output(out, ow, oh, keep)

        ok, jpg = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), config.JPEG_QUALITY])
        info = {"min": ymin, "max": ymax, "mean": ymean, "out_shape": (out.shape[0], out.shape[1])}
        if not ok:
            return None, info
        return jpg.tobytes(), info

    # --------- background loop ---------
    def start_background(self) -> None:
        t = threading.Thread(target=self._capture_loop, daemon=True)
        t.start()

    def _capture_loop(self) -> None:
        interval = 1.0 / max(1, config.FPS)
        last = 0.0

        while True:
            if not self.grab_one_frame_to_file():
                time.sleep(0.05)
                continue

            Y = self.read_y_from_cap()
            if Y is None:
                time.sleep(0.02)
                continue

            with self.lock:
                self.latest_Y_full = Y

            # 用默认参数渲染一次，只为更新 stats（跟你原逻辑一致）
            Yh = self.select_half(Y, config.DEFAULT_HALF)
            _, info = self.render_frame(
                Yh, config.DEFAULT_MODE, config.DEFAULT_CM, config.DEFAULT_STRETCH,
                config.DEFAULT_OW, config.DEFAULT_OH, keep=(config.DEFAULT_KEEP == "1"),
                rot=parse_int(config.DEFAULT_ROT, 0),
                flip=parse_int(config.DEFAULT_FLIP, 0)
            )

            with self.lock:
                self.latest_info.update({
                    "dev": config.DEV,
                    "src_shape": (config.H, config.W),
                    "half": config.DEFAULT_HALF,
                    "out_shape": (int(info["out_shape"][0]), int(info["out_shape"][1])),
                    "min": int(info["min"]),
                    "max": int(info["max"]),
                    "mean": float(info["mean"]),
                    "mode": config.DEFAULT_MODE, "cm": config.DEFAULT_CM, "stretch": config.DEFAULT_STRETCH,
                    "keep": int(config.DEFAULT_KEEP),
                    "rot": int(config.DEFAULT_ROT),
                    "flip": int(config.DEFAULT_FLIP),
                })

            now = time.time()
            dt = now - last
            if dt < interval:
                time.sleep(interval - dt)
            last = time.time()

    # --------- helpers for routes ---------
    def get_latest_stats(self) -> Dict:
        with self.lock:
            return dict(self.latest_info)

    def get_latest_y_copy(self) -> Optional[np.ndarray]:
        with self.lock:
            return None if self.latest_Y_full is None else self.latest_Y_full.copy()
