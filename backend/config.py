# ---------------- Config ----------------
DEV = "/dev/video0"
W, H = 256, 392
FPS = 15
JPEG_QUALITY = 75
CAP_PATH = "/tmp/cap.bin"
DB_PATH = "./thermal_photos.db"

DEFAULT_HALF = "auto"     # auto/top/bottom/full
DEFAULT_MODE = "color"    # color/gray
DEFAULT_CM = "turbo"
DEFAULT_STRETCH = "1"     # 1/0

DEFAULT_OW = 512
DEFAULT_OH = 512
DEFAULT_KEEP = "1"        # 1=保持比例letterbox, 0=强拉伸

DEFAULT_ROT = "270"         # 0/90/180/270
DEFAULT_FLIP = "0"        # 0=不翻转, 1=水平翻转, 2=垂直翻转, 3=水平+垂直
