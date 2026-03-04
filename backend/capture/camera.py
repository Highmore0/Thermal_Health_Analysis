# backend/capture/camera.py
import time
import threading
import struct
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List, Any

import numpy as np

try:
    import cv2
except Exception:
    cv2 = None

import usb.core
import usb.util

from backend import config


CMAP = {
    "inferno": cv2.COLORMAP_INFERNO if cv2 else 0,
    "turbo":   cv2.COLORMAP_TURBO   if cv2 else 0,
    "jet":     cv2.COLORMAP_JET     if cv2 else 0,
    "plasma":  cv2.COLORMAP_PLASMA  if cv2 else 0,
    "viridis": cv2.COLORMAP_VIRIDIS if cv2 else 0,
}


def parse_int(v, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


# --------------------------
# Stream8 / USB constants
# --------------------------
VID = 0x2BDF
PID = 0x0102

VC_IFACE = 0
VS_IFACE = 1
UNIT_ID = 0x0A
EP_IN = 0x81

MAGIC_FRMI = b"\x73\x77\x82\x70"  # 0x70827773 little-endian


# --------------------------
# XU control helpers
# --------------------------
def xu_ctrl(dev, bmRequestType, bRequest, cs_id, wLength_or_data, timeout=2000):
    wValue = (cs_id << 8) | 0x00
    wIndex = (UNIT_ID << 8) | VC_IFACE
    return dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, wLength_or_data, timeout=timeout)


def get_protocol_version(dev) -> str:
    ret = xu_ctrl(dev, 0xA1, 0x81, 0x04, 4)  # GET_CUR, CS_ID=0x04, len=4
    return bytes(ret).decode(errors="ignore").rstrip("\x00")


def get_error_code(dev) -> int:
    ret = xu_ctrl(dev, 0xA1, 0x81, 0x06, 1)  # GET_CUR, CS_ID=0x06, len=1
    return int(ret[0])


def function_switch(dev, target_cs_id: int, sub_fn: int):
    # SET_CUR, CS_ID=0x05, Data=[CS_ID, Sub-function ID]
    xu_ctrl(dev, 0x21, 0x01, 0x05, [target_cs_id & 0xFF, sub_fn & 0xFF])


def get_len(dev, cs_id: int) -> int:
    ret = xu_ctrl(dev, 0xA1, 0x85, cs_id, 2)  # GET_LEN -> 2 bytes LE
    return int(ret[0]) | (int(ret[1]) << 8)


def wait_ready(dev, timeout_s=3.0, poll_interval_s=0.05):
    t0 = time.time()
    while True:
        ec = get_error_code(dev)
        if ec == 0x00:
            return
        if ec != 0x01:
            raise RuntimeError(f"Device error code = {hex(ec)}")
        if time.time() - t0 > timeout_s:
            raise TimeoutError("Timeout waiting device to finish (error stayed 0x01)")
        time.sleep(poll_interval_s)


def set_stream_type(dev, channel_id=1, stream_type=8):
    # THERMAL_STREAM_PARAM (CS_ID=0x03, Sub-function=0x05)
    function_switch(dev, 0x03, 0x05)
    wait_ready(dev, timeout_s=1.0)

    ln = get_len(dev, 0x03)
    if ln != 2:
        raise RuntimeError(f"Unexpected GET_LEN for stream param: {ln}, expected 2")

    xu_ctrl(dev, 0x21, 0x01, 0x03, [channel_id & 0xFF, stream_type & 0xFF])
    wait_ready(dev, timeout_s=3.0)

    ln2 = get_len(dev, 0x03)
    cur = xu_ctrl(dev, 0xA1, 0x81, 0x03, ln2)
    cur_list = list(cur)
    if cur_list != [channel_id, stream_type]:
        raise RuntimeError(f"Readback mismatch: {cur_list} != {[channel_id, stream_type]}")
    return cur_list


def detach_kernel_if_needed(dev, iface: int) -> bool:
    try:
        if dev.is_kernel_driver_active(iface):
            dev.detach_kernel_driver(iface)
            return True
    except Exception:
        pass
    return False


def select_vs_altsetting_with_ep(dev, vs_iface: int, target_ep: int) -> Optional[int]:
    cfg = dev.get_active_configuration()
    matches: List[int] = []
    for intf in cfg:
        if getattr(intf, "bInterfaceNumber", None) != vs_iface:
            continue
        alt = getattr(intf, "bAlternateSetting", None)
        if alt is None:
            continue
        try:
            eps = [ep.bEndpointAddress for ep in intf.endpoints()]
        except Exception:
            eps = []
        if target_ep in eps:
            matches.append(int(alt))
    return min(matches) if matches else None


# --------------------------
# UVC bulk frame assembler
# --------------------------
class UvcBulkFrameAssembler:
    EOF_BIT = 0x02

    def __init__(self):
        self._frame = bytearray()

    def push_payload(self, packet: bytes) -> Optional[bytes]:
        if not packet:
            return None
        hdr_len = packet[0]
        if hdr_len < 2 or hdr_len > len(packet):
            return None
        flags = packet[1]
        payload = packet[hdr_len:]
        if payload:
            self._frame += payload
        if flags & self.EOF_BIT:
            out = bytes(self._frame)
            self._frame.clear()
            return out
        return None


# --------------------------
# Stream8 header parsing
# --------------------------
@dataclass
class Stream8Header:
    u32MagicNo: int
    u32HeaderSize: int
    u32StreamType: int
    u32StreamLen: int
    bIFRYuv: int

    u32TmDataMode: int
    u32TmScale: int
    u32TmOffset: int
    tm_info_off: int
    tm_info_score: int
    byIsFreezed_raw32: int

    u32RTDataType: int
    u32FrmNum: int
    u32Width: int
    u32Height: int
    u32MatrixLenPlus4: int

    yuvFrmNum: int
    yuvW: int
    yuvH: int
    yuvLen: int


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def _f32(b: bytes, off: int) -> float:
    return struct.unpack_from("<f", b, off)[0]


def _s32_from_u32(x: int) -> int:
    return struct.unpack("<i", struct.pack("<I", x & 0xFFFFFFFF))[0]


def _find_frmi(frame: bytes) -> int:
    return frame.find(MAGIC_FRMI)


def _scan_tm_supple_info(h: bytes) -> Tuple[int, int, int, int, int, int]:
    scale_set = {1, 2, 4, 5, 8, 10, 16, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000, 65535}
    best = None
    for off in range(0, len(h) - 16, 4):
        mode = _u32(h, off)
        if mode not in (0, 1):
            continue
        scale = _u32(h, off + 4)
        offset_u32 = _u32(h, off + 8)
        freeze_raw32 = _u32(h, off + 12)

        if scale == 0 or scale > 10_000_000:
            continue

        score = 10
        score += 20 if scale in scale_set else (5 if 1 <= scale <= 1_000_000 else -10)
        off_s = _s32_from_u32(offset_u32)
        score += 15 if offset_u32 == 0 else 0
        score += 8 if -1_000_000 <= off_s <= 1_000_000 else -20
        score += 5 if freeze_raw32 in (0, 1) else 0

        bad_vals = {256, 192, 98304, 196608}
        if scale in bad_vals or offset_u32 in bad_vals:
            score -= 8

        cand = (score, off, mode, scale, offset_u32, freeze_raw32)
        if best is None or cand[0] > best[0]:
            best = cand

    if best is None:
        return (1, 1, 0, 0, -1, -999)
    score, off, mode, scale, offset_u32, freeze_raw32 = best
    return (mode, scale, offset_u32, freeze_raw32, off, score)


def _scan_global_minmaxavg_and_points(h: bytes) -> Optional[Dict[str, Any]]:
    best = None
    need = 3 * 4 + 6 * 4  # 36 bytes
    for off in range(0, len(h) - need, 4):
        try:
            mn = _f32(h, off + 0)
            mx = _f32(h, off + 4)
            av = _f32(h, off + 8)
            if not (-100.0 <= mn <= 1200.0 and -100.0 <= mx <= 1200.0 and -100.0 <= av <= 1200.0):
                continue

            x0 = _u32(h, off + 12); y0 = _u32(h, off + 16)
            x1 = _u32(h, off + 20); y1 = _u32(h, off + 24)
            x2 = _u32(h, off + 28); y2 = _u32(h, off + 32)
            if any(v > 1000 for v in (x0, y0, x1, y1, x2, y2)):
                continue

            score = 0
            score += 5 if mn <= mx else -5
            score += 8 if mn <= av <= mx else -3
            span = mx - mn
            score += 2 if 0.0 <= span <= 300.0 else -1
            score += 2 if (-40.0 <= mn <= 80.0 and -40.0 <= mx <= 200.0 and -40.0 <= av <= 80.0) else 0

            cand = {
                "off": off,
                "minTmp": float(mn),
                "maxTmp": float(mx),
                "avrTmp": float(av),
                "p_max": (int(x0), int(y0)),
                "p_min": (int(x1), int(y1)),
                "p_avg": (int(x2), int(y2)),
                "score": int(score),
            }
            if best is None or cand["score"] > best["score"]:
                best = cand
        except Exception:
            continue
    return best


def parse_stream8_header(frame: bytes) -> Tuple[Stream8Header, int, Optional[Dict[str, Any]]]:
    idx = _find_frmi(frame)
    if idx < 0:
        raise ValueError("FRMI magic not found")

    if len(frame) < idx + 20:
        raise ValueError("Frame too short for basic header fields")

    header_size = _u32(frame, idx + 4)
    if header_size < 132:
        raise ValueError(f"header_size too small: {header_size}")
    if len(frame) < idx + header_size:
        raise ValueError("Frame shorter than header_size")

    h = frame[idx: idx + header_size]

    u32MagicNo    = _u32(h, 0)
    u32HeaderSize = _u32(h, 4)
    u32StreamType = _u32(h, 8)
    u32StreamLen  = _u32(h, 12)
    bIFRYuv       = _u32(h, 16)

    mode, scale, offset_u32, freeze_raw32, tm_off, tm_score = _scan_tm_supple_info(h)
    osd = _scan_global_minmaxavg_and_points(h)

    u32RTDataType = _u32(h, 36) if len(h) >= 44 else 0
    u32FrmNum     = _u32(h, 40) if len(h) >= 44 else 0

    def find_tm_wh_len(buf: bytes, data_mode: int):
        pix_bytes = 4 if data_mode == 0 else 2
        allowed = {
            (256, 192), (256, 196), (256, 384),
            (240, 320), (320, 240),
            (160, 120), (120, 160),
        }
        for off in range(0, len(buf) - 12, 4):
            w = _u32(buf, off)
            hh = _u32(buf, off + 4)
            ln = _u32(buf, off + 8)
            if (w, hh) not in allowed:
                continue
            expect = w * hh * pix_bytes
            if ln in (expect, expect + 4, expect + 8, expect + 12, expect + 16):
                return off, w, hh, ln
        return None

    hit = find_tm_wh_len(h, mode)
    if not hit:
        raise ValueError("Cannot locate matrix width/height/len in header")
    off_wh, w, hh, mat_len_plus4 = hit

    yhit = None
    for off in range(off_wh + 12, len(h) - 12, 4):
        yW = _u32(h, off)
        yH = _u32(h, off + 4)
        yLen = _u32(h, off + 8)
        if 32 <= yW <= 1920 and 32 <= yH <= 1080 and yLen == yW * yH * 2:
            yhit = (off, yW, yH, yLen)
            break
    if not yhit:
        raise ValueError("Cannot locate YUV width/height/len in header")

    off_y, yW, yH, yLen = yhit
    yuvFrmNum = _u32(h, max(0, off_y - 4)) if off_y >= 4 else 0

    header = Stream8Header(
        u32MagicNo=u32MagicNo,
        u32HeaderSize=u32HeaderSize,
        u32StreamType=u32StreamType,
        u32StreamLen=u32StreamLen,
        bIFRYuv=bIFRYuv,

        u32TmDataMode=mode,
        u32TmScale=scale,
        u32TmOffset=offset_u32,
        tm_info_off=tm_off,
        tm_info_score=tm_score,
        byIsFreezed_raw32=freeze_raw32,

        u32RTDataType=u32RTDataType,
        u32FrmNum=u32FrmNum,
        u32Width=w,
        u32Height=hh,
        u32MatrixLenPlus4=mat_len_plus4,

        yuvFrmNum=yuvFrmNum,
        yuvW=yW,
        yuvH=yH,
        yuvLen=yLen,
    )
    data_start = idx + header.u32HeaderSize
    return header, data_start, osd


def fit_raw_to_celsius_from_osd(raw_min, raw_max, osd_min, osd_max) -> Optional[Tuple[float, float]]:
    if not (np.isfinite(raw_min) and np.isfinite(raw_max) and np.isfinite(osd_min) and np.isfinite(osd_max)):
        return None
    if raw_max <= raw_min or osd_max <= osd_min:
        return None
    a = (osd_max - osd_min) / (raw_max - raw_min)
    b = osd_min - a * raw_min
    if not (np.isfinite(a) and np.isfinite(b)):
        return None
    return float(a), float(b)


def extract_temp_and_yuv(frame: bytes, data_start: int, hdr: Stream8Header,
                         osd: Optional[Dict[str, Any]],
                         use_osd_fit: bool) -> Tuple[np.ndarray, bytes]:
    w, h = hdr.u32Width, hdr.u32Height
    n_pix = w * h

    mat_total = hdr.u32MatrixLenPlus4
    if len(frame) < data_start + mat_total:
        raise ValueError("Frame too short for matrix block")

    mat_block = frame[data_start: data_start + mat_total]
    mat_payload = mat_block[4:]  # skip leading 4 bytes

    pix_bytes = 4 if hdr.u32TmDataMode == 0 else 2
    need = n_pix * pix_bytes
    if len(mat_payload) < need:
        raise ValueError("Matrix payload too short")

    if hdr.u32TmDataMode == 0:
        tm = np.frombuffer(mat_payload[:need], dtype="<f4").reshape((h, w)).astype(np.float32, copy=False)
    else:
        raw_u16 = np.frombuffer(mat_payload[:need], dtype="<u2").reshape((h, w))
        raw = raw_u16.astype(np.float32)

        # doc fallback
        scale = float(hdr.u32TmScale) if hdr.u32TmScale != 0 else 1.0
        offset = float(_s32_from_u32(hdr.u32TmOffset))
        tm = raw / scale + offset - 273.15

        if use_osd_fit and osd is not None:
            ab = fit_raw_to_celsius_from_osd(
                float(raw_u16.min()), float(raw_u16.max()),
                float(osd["minTmp"]), float(osd["maxTmp"])
            )
            if ab is not None:
                a, b = ab
                tm = (a * raw + b).astype(np.float32, copy=False)

    yuv_start = data_start + mat_total
    if len(frame) < yuv_start + hdr.yuvLen:
        raise ValueError("Frame too short for YUV")
    yuv = frame[yuv_start: yuv_start + hdr.yuvLen]
    return tm.astype(np.float32, copy=False), yuv


def yuv422_to_y_plane(yuv: bytes, w: int, h: int) -> np.ndarray:
    b = np.frombuffer(yuv, dtype=np.uint8)
    if b.size != w * h * 2:
        raise ValueError("Bad yuv size")
    b2 = b.reshape((h, w * 2))
    return b2[:, 0::2].copy()


# --------------------------
# CameraService (public interface unchanged)
# --------------------------
class CameraService:
    """
    - 对外接口保持不变（给 Flask 用）：
      - read_y_from_cap()
      - get_latest_y_copy()
      - get_latest_temp_c_copy()
      - get_latest_stats()
    - 内部改为：StreamType=8 bulk 持续抓帧，缓存 Y + 温度矩阵
    - 提供 get_latest_bundle_copy()：拍照时冻结同一刻 Y + TM
    """
    def __init__(self) -> None:
        self.lock = threading.Lock()

        self.latest_Y_full: Optional[np.ndarray] = None
        self.latest_temp_c_full: Optional[np.ndarray] = None

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

            # temp stats
            "tmin": None, "tmax": None, "tmean": None,
            "temp_shape": None,
            "temp_meta": None,
        }

        # stream8 runtime
        self._dev = None
        self._detached_vc = False
        self._detached_vs = False
        self._stop = False
        self._ready_evt = threading.Event()

        # 你验证过 OSD-fit 更可靠：默认开启
        self.use_osd_fit = True
        # print("[CFGDBG]", config.DEFAULT_OW, config.DEFAULT_OH, config.DEFAULT_KEEP, config.DEFAULT_ROT, config.DEFAULT_FLIP)

    # --------- new: bundle snapshot-safe getter ---------
    def get_latest_bundle_copy(self):
        """
        原子地取出：Y + 温度矩阵 + stats
        用于“拍照那一刻”保存，避免跨帧
        """
        with self.lock:
            if self.latest_Y_full is None:
                return None
            Y = self.latest_Y_full.copy()
            tm = None if self.latest_temp_c_full is None else self.latest_temp_c_full.copy()
            info = dict(self.latest_info)
            return Y, tm, info

    # --------- keep your existing helpers ---------
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
        if cv2 is None:
            return img
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
        if cv2 is None:
            return img
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
        if cv2 is None:
            return None, {"min": 0, "max": 0, "mean": 0.0, "out_shape": (0, 0)}

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

    # --------- public: keep name, keep behavior ---------
    def read_y_from_cap(self) -> Optional[np.ndarray]:
        """
        以前：从 cap.bin 读
        现在：从缓存读最新帧
        Flask 的 /api/video.mjpg 不用改
        """
        with self.lock:
            if self.latest_Y_full is None:
                return None
            return self.latest_Y_full.copy()

    def get_latest_stats(self) -> Dict:
        with self.lock:
            return dict(self.latest_info)

    def get_latest_y_copy(self) -> Optional[np.ndarray]:
        with self.lock:
            return None if self.latest_Y_full is None else self.latest_Y_full.copy()

    def get_latest_temp_c_copy(self) -> Optional[np.ndarray]:
        with self.lock:
            return None if self.latest_temp_c_full is None else self.latest_temp_c_full.copy()

    # --------- background loop ---------
    def start_background(self) -> None:
        t = threading.Thread(target=self._capture_loop, daemon=True)
        t.start()
        self._ready_evt.wait(timeout=2.0)

    def _open_usb(self):
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is None:
            raise RuntimeError("USB device not found")

        self._detached_vc = detach_kernel_if_needed(dev, VC_IFACE)
        self._detached_vs = detach_kernel_if_needed(dev, VS_IFACE)

        try:
            dev.set_configuration()
        except usb.core.USBError as e:
            if getattr(e, "errno", None) != 16:
                raise

        usb.util.claim_interface(dev, VC_IFACE)
        usb.util.claim_interface(dev, VS_IFACE)

        ver = get_protocol_version(dev)
        if not ver.startswith("2.0"):
            raise RuntimeError(f"protocol not 2.0: {ver}")

        set_stream_type(dev, channel_id=1, stream_type=8)

        alt = select_vs_altsetting_with_ep(dev, VS_IFACE, EP_IN)
        if alt is not None:
            try:
                dev.set_interface_altsetting(interface=VS_IFACE, alternate_setting=alt)
            except Exception:
                pass

        return dev

    def _close_usb(self):
        dev = self._dev
        if dev is None:
            return
        try:
            usb.util.release_interface(dev, VC_IFACE)
        except Exception:
            pass
        try:
            usb.util.release_interface(dev, VS_IFACE)
        except Exception:
            pass
        try:
            if self._detached_vs:
                dev.attach_kernel_driver(VS_IFACE)
        except Exception:
            pass
        try:
            if self._detached_vc:
                dev.attach_kernel_driver(VC_IFACE)
        except Exception:
            pass
        self._dev = None

    def _capture_loop(self) -> None:
        interval = 1.0 / max(1, config.FPS)
        last_tick = 0.0

        asm = UvcBulkFrameAssembler()

        while not self._stop:
            try:
                if self._dev is None:
                    self._dev = self._open_usb()
                    self._ready_evt.set()

                pkt = self._dev.read(EP_IN, 16 * 1024, timeout=2000)
                frame = asm.push_payload(bytes(pkt))
                if frame is None:
                    continue

                hdr, data_start, osd = parse_stream8_header(frame)
                tm, yuv = extract_temp_and_yuv(
                    frame, data_start, hdr, osd=osd, use_osd_fit=self.use_osd_fit
                )

                Y = yuv422_to_y_plane(yuv, hdr.yuvW, hdr.yuvH)
                
                with self.lock:
                    self.latest_Y_full = Y
                    self.latest_temp_c_full = tm

                    tmin = float(np.nanmin(tm))
                    tmax = float(np.nanmax(tm))
                    tmean = float(np.nanmean(tm))

                    self.latest_info.update({
                        "dev": config.DEV,
                        "src_shape": (int(hdr.yuvH), int(hdr.yuvW)),

                        "tmin": tmin,
                        "tmax": tmax,
                        "tmean": tmean,
                        "temp_shape": (int(tm.shape[0]), int(tm.shape[1])),
                        "temp_meta": {
                            "stream_type": int(hdr.u32StreamType),
                            "frm": int(hdr.u32FrmNum),
                            "tm_mode": int(hdr.u32TmDataMode),
                            "tm_scale": int(hdr.u32TmScale),
                            "tm_offset_s32": int(_s32_from_u32(hdr.u32TmOffset)),
                            "tm_w": int(hdr.u32Width),
                            "tm_h": int(hdr.u32Height),
                            "yuv_w": int(hdr.yuvW),
                            "yuv_h": int(hdr.yuvH),
                        }
                    })

                now = time.time()
                dt = now - last_tick
                if dt < interval:
                    time.sleep(max(0.0, interval - dt))
                last_tick = time.time()

            except usb.core.USBError as e:
                if getattr(e, "errno", None) == 110:
                    continue
                self._close_usb()
                time.sleep(0.2)
                continue
            except Exception:
                time.sleep(0.01)
                continue

        self._close_usb()