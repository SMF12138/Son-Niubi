"""桌面宠物浮窗：现代暗色圆角卡片 + 真实图片 + 交互 + 语音 + 最小化悬浮球。

视觉：
- 透明窗口 + Canvas 圆角卡片（#1E222A）
- 文字层级：标题 #FFFFFF / 正文 #B8C0CC / 弱化 #8B95A3，青色霓虹点缀
- 右上角四个圆形 icon 按钮（地球 / 静音 / 最小化 / 关闭），hover 变色

交互（逻辑未改）：
- 点击角色区切换形态 + 播放语音 + 打开网页
- 右上按钮：关闭 / 最小化 / 静音
- 右侧数据区拖拽移动；最小化后点击悬浮球恢复
- 每 5s 读 forecast_*.json 刷新数据
- 日期块切换 7/30/60/90 日 horizon，数据联动
"""
import atexit
import datetime as dt
import json
import math
import platform
import signal
import subprocess
import sys
import tkinter as tk
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

ROOT = Path(__file__).resolve().parent
FORECAST_FILE = ROOT / "data" / "forecast_7.json"
FORECAST_FILES = {
    7: ROOT / "data" / "forecast_7.json",
    30: ROOT / "data" / "forecast_30.json",
    60: ROOT / "data" / "forecast_60.json",
    90: ROOT / "data" / "forecast_90.json",
}
HORIZONS = [7, 30, 60, 90]
FORM1 = ROOT / "data" / "pet" / "form1.png"
FORM2 = ROOT / "data" / "pet" / "form2.png"
VOICE1 = ROOT / "data" / "pet" / "voice1.wav"
VOICE2 = ROOT / "data" / "pet" / "voice2.wav"
# MOEX 实时市场价(Flask 快层每 60s 写一次), 仅供桌宠显示, 不参与预测
LIVE_FILE = ROOT / "data" / "moex_live.json"
LIVE_STALE_SEC = 600        # 超过 10 分钟没更新视为失效, 回退官方牌价
# 快层每 60s 重写 forecast。超过此时长没更新 = 本文件夹没有服务在写数据
# (多版本文件夹并存、端口被旧安装占住时会发生)。继续显示等于拿冻结的旧
# 日期/旧置信度骗人, 不如显示"等待数据"。
FORECAST_STALE_SEC = 600

# 透明键色
TRANSPARENT_KEY = "#FF00FE"  # 仅 Windows; macOS 不需要

BG = "#1E222A"              # 卡片底
PANEL = "#14171D"           # 角色面板底
DIVIDER = "#2A303C"         # 细描边
TEXT_HI = "#FFFFFF"
TEXT_MD = "#B8C0CC"
TEXT_DIM = "#8B95A3"        # 小字在 BG 上的对比度 5.3:1, 满足小字号 4.5:1 下限
ACCENT = "#4FD1C5"          # 强调色(标题点 / 选中日期块 / 开关 ON)
BTN_BG = "#2A333C"
BTN_HOVER = "#3B4757"
BTN_CLOSE_HOVER = "#E5484D"
UP_COLOR = "#FF6B5E"
DOWN_COLOR = "#35D0A0"
WEAK_COLOR = "#8C94A0"   # 实验性弱倾向专用: 中性灰, 不与涨跌红绿混淆

IMG_TARGET_H = 104
IMG_TARGET_W = 100

W_FULL, H_FULL = 320, 176
W_MIN, H_MIN = 52, 52       # 悬浮球尺寸(与 _draw_ball 的 r=23 匹配)
DRAG_THRESHOLD = 4          # 位移超过该像素才算拖拽, 否则视为点击

# ---- 卡片布局(320x176, 内边距 13px) ----
# 所有坐标按 canvas bbox 实测校准过, 改字号必须重新量一遍再动这里。
CARD_X0, CARD_Y0, CARD_X1, CARD_Y1 = 3, 3, 317, 173
CARD_R = 18
CHAR_X0, CHAR_Y0, CHAR_X1, CHAR_Y1 = 16, 16, 148, 160   # 左: 角色面板
CHAR_R = 14
CHAR_ZONE_R = 156           # 点击角色区的 x 上界(命中区)
CHIP_X0, CHIP_Y0, CHIP_X1, CHIP_Y1 = 54, 140, 110, 156  # 形态芯片
CHIP_R = 10
DATA_X0, DATA_X1 = 164, 304                             # 右: 数据区
HEAD_Y = 24                 # 标题行(币种)与按钮同排的垂直中心
HERO_X0, HERO_Y0, HERO_X1, HERO_Y1 = 164, 40, 304, 84   # hero 预测块(高 44)
HERO_R = 14
HERO_ROW_Y = 62             # hero 单行(方向 + 概率)垂直中心
HERO_PCT_X = 222            # 概率左缘(避让方向词)
INFO1_Y = 100               # 字段行 1(汇率)
INFO2_Y = 124               # 字段行 2(日期)
INFO_VALUE_X = 197          # 字段值左缘(避让 2 字标签)

BTN_R = 7
BTN_Y = HEAD_Y
BTN_XS = {"browser": 240, "mute": 259, "min": 278, "close": 297}

SEG_W = 35          # 日期块宽度(绘制与命中检测共用, 不可分别硬编码)
BAR_H = 20          # 日期块高度
BAR_Y = 140         # 日期块顶边 y
BAR_X = DATA_X0     # 日期块左边 x

CONFIG_PATH = ROOT / "data" / "pet_config.json"
HERO_TINT = 0.12    # hero 底色中方向色的混合比例

# 字号体系(按 canvas 实测宽度定, 保证各元素不互相压到)
FS_HERO_PCT = 18
FS_HERO_DIR = 15
FS_VALUE = 11
FS_SMALL = 11       # 形态名 / 日期值
FS_CHIP = 10
FS_HEAD = 9
FS_LABEL = 9

# 悬浮球: tkinter Canvas 无抗锯齿, 所以自己超采样光栅化成 PhotoImage 贴图。
# 结构(由外向内): 深色剪影 -> 金色环(角向金属渐变) -> 径向渐变底盘 -> 眼睛 -> 涨跌点
BALL_SS = 3                 # 超采样倍数(每轴 BALL_SS 个子样本)
BALL_RIM_OUT = 23.0         # 球外半径 / 深色剪影 外径
BALL_RIM_IN = 22.0          # 深色剪影 内径
BALL_RING_R = 21.0          # 金色环 中心半径
BALL_RING_W = 2.0           # 金色环 线宽(细)
BALL_GRAD_R = 20.0          # 径向渐变 外径(= 金环内缘)
BALL_GRAD_IN = 8.0          # 径向渐变 内径(= 眼球半径)
BALL_RIM = "#14171D"        # 深色剪影色: 让圆边过渡落在深色上而非亮金上
BALL_GRAD_OUTER = (0x22, 0x27, 0x30)   # 渐变外缘色(深)
BALL_GRAD_INNER = (0x4B, 0x57, 0x70)   # 渐变内缘色(亮)
BALL_LIGHT_DEG = 135.0      # 金属光源方位角(逆时针, 0°=右, 90°=上)
BALL_GOLD_STOPS = [         # (t, 金色) t: 0=背光 1=正对光源 —— 金属靠"暗铜→亮金→近白高光"
    (0.00, "#7A5A0E"),
    (0.35, "#B8860B"),
    (0.62, "#E8C24A"),
    (0.85, "#FFF3B8"),
    (1.00, "#FFFDE8"),
]
BALL_EYE = [(2.0, "#1A1210"), (5.0, "#2D8B57"), (8.0, "white")]
# 眼球层: (半径, 色) 必须由小到大 —— 渲染时首个 d < 半径 的层胜出,
# 由小到大才能让内层(瞳孔)覆盖外层(眼白)。由大到小会导致整只眼变纯白。
BALL_DOT_R = 4.0                # 涨跌指示点半径
BALL_DOT_POS = (11.0, -11.0)    # 指示点圆心相对球心


def _srgb_to_linear(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c):
    c = max(0.0, min(1.0, c))
    v = 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
    return round(v * 255)


def _hex_rgb(s):
    if s == "white":
        return (255, 255, 255)
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _mix_linear(a, b, u):
    """线性光空间插值, 避免 sRGB 直接插值产生的暗带。"""
    return tuple(_linear_to_srgb(x + (y - x) * u)
                 for x, y in zip((_srgb_to_linear(v) for v in a),
                                 (_srgb_to_linear(v) for v in b)))


def _pick_stops(stops, t):
    """stops: [(t, '#rrggbb')] 递增。"""
    if t <= stops[0][0]:
        return _hex_rgb(stops[0][1])
    if t >= stops[-1][0]:
        return _hex_rgb(stops[-1][1])
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            u = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return _mix_linear(_hex_rgb(c0), _hex_rgb(c1), u)
    return _hex_rgb(stops[-1][1])


def _ball_luts():
    """预算两张查找表, 把 pow/atan2 从像素内循环里挪出去。"""
    grad = [_mix_linear(BALL_GRAD_OUTER, BALL_GRAD_INNER, i / 255.0)
            for i in range(256)]
    gold = [_pick_stops(BALL_GOLD_STOPS,
                        (math.cos(math.radians(a - BALL_LIGHT_DEG)) + 1.0) / 2.0)
            for a in range(360)]
    return grad, gold


def render_ball(dot_color, size, ss=BALL_SS):
    """把悬浮球超采样光栅化为 tk.PhotoImage(带抗锯齿)。

    圆外像素填 TRANSPARENT_KEY, 由窗口的 -transparentcolor 变透明。
    """
    grad_lut, gold_lut = _ball_luts()
    rim = _hex_rgb(BALL_RIM)
    eye = [(r, _hex_rgb(c)) for r, c in BALL_EYE]
    dot = _hex_rgb(dot_color)

    cx = cy = (size - 1) / 2.0
    step = 1.0 / ss
    offs = [(i + 0.5) * step for i in range(ss)]
    samples = ss * ss

    ring_out = BALL_RING_R + BALL_RING_W / 2.0
    ring_in = BALL_RING_R - BALL_RING_W / 2.0
    dot_dx, dot_dy = BALL_DOT_POS
    dot_r2 = BALL_DOT_R ** 2

    img = tk.PhotoImage(width=size, height=size)
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            r = g = b = 0.0
            hit = 0
            for oy in offs:
                for ox in offs:
                    dx = px + ox - cx
                    dy = py + oy - cy
                    d = math.sqrt(dx * dx + dy * dy)
                    if d > BALL_RIM_OUT:
                        continue
                    hit += 1
                    if d > BALL_RIM_IN:
                        cr, cg, cb = rim
                    elif d > ring_out:
                        cr, cg, cb = rim
                    elif d > ring_in:
                        ang = int(math.degrees(math.atan2(-dy, dx))) % 360
                        cr, cg, cb = gold_lut[ang]
                    else:
                        u = (BALL_GRAD_R - d) / (BALL_GRAD_R - BALL_GRAD_IN)
                        u = 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)
                        cr, cg, cb = grad_lut[int(u * 255)]
                        for er, ecol in eye:
                            if d < er:
                                cr, cg, cb = ecol
                                break
                    ddx, ddy = dx - dot_dx, dy - dot_dy
                    if ddx * ddx + ddy * ddy <= dot_r2:
                        cr, cg, cb = dot
                    r += cr
                    g += cg
                    b += cb
            if hit == 0:
                row.append(TRANSPARENT_KEY)
            else:
                row.append("#%02X%02X%02X"
                           % (round(r / hit), round(g / hit), round(b / hit)))
        rows.append("{" + " ".join(row) + "}")
    img.put(" ".join(rows))
    return img


def _blend_over(bg_hex, fg_hex, alpha):
    """把 fg 以 alpha 覆盖到 bg 上(线性光空间混合, 避免发灰)。"""
    return "#%02X%02X%02X" % _mix_linear(_hex_rgb(bg_hex), _hex_rgb(fg_hex), alpha)


def _load_config():
    """读桌宠配置。文件缺失/损坏/非 dict 一律回落空字典, 绝不抛异常。"""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _save_config(cfg):
    """写桌宠配置。**读-改-写合并**: 只带部分键写入会抹掉其它键(如存位置时清掉开关)。失败静默。"""
    try:
        CONFIG_PATH.parent.mkdir(exist_ok=True)
        cur = _load_config()
        cur.update(cfg)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _clamp_pos(root, x, y, w, h):
    """把窗口左上角夹进当前屏幕范围。

    必要性: 存下来的位置在改分辨率/换显示器后可能整个落在屏外, 用户既看不到也拖不回来。
    取值非法(非数值)返回 None, 由调用方回退默认位。
    """
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    return (max(0, min(int(x), max(0, sw - w))),
            max(0, min(int(y), max(0, sh - h))))


# hero 预测块底色: 方向色按 HERO_TINT 混到卡片底上, 一眼看出涨跌
HERO_BG = {
    1: _blend_over(BG, UP_COLOR, HERO_TINT),
    0: _blend_over(BG, DOWN_COLOR, HERO_TINT),
    "weak": _blend_over(BG, WEAK_COLOR, HERO_TINT),
}

# 字体: 运行时从系统已装字体里挑首选。优先用真实字重变体(如 Medium),
# 取不到才退回 Tk 合成粗体 —— 合成粗体在小字号下发糊。
FONT_REG_PREFS = ["HarmonyOS Sans SC", "Noto Sans SC",
                  "PingFang SC", "Hiragino Sans GB",
                  "Microsoft YaHei UI", "Microsoft YaHei"]
FONT_STRONG_PREFS = ["HarmonyOS Sans SC Medium", "Noto Sans SC Medium",
                     "PingFang SC Medium"]
FONT_FALLBACK = "PingFang SC" if IS_MAC else "Microsoft YaHei"


def resolve_fonts():
    """返回 (常规族, 强调族, 强调族是否为真实字重)。需要 Tk 根窗口已存在。"""
    try:
        from tkinter import font as tkfont
        have = {f.lower() for f in tkfont.families()}
    except Exception:
        return FONT_FALLBACK, FONT_FALLBACK, False

    def pick(prefs):
        for name in prefs:
            if name.lower() in have:
                return name
        return None

    reg = pick(FONT_REG_PREFS) or FONT_FALLBACK
    strong = pick(FONT_STRONG_PREFS)
    if strong:
        return reg, strong, True
    return reg, reg, False


class FloatingPet:
    def __init__(self):
        self.root = tk.Tk()
        print(f"[pet] 启动 | Python {sys.version.split()[0]} | {sys.platform} | "
              f"Tcl/Tk {self.root.tk.call('info', 'patchlevel')}",
              file=sys.stderr, flush=True)
        self.root.title("CNY/RUB 桌宠")
        self.font, self.font_strong, self._strong_real = resolve_fonts()
        self.root.overrideredirect(True)
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        # macOS: aqua Tk 没有 -transparent 属性(那是 X11 专属), 也没有
        # "systemTransparent" 颜色名——之前这两行让宠物进程启动即崩,
        # 表现为桌宠闪退无窗口。改用与卡片底同色的深色窗口底代替抠透明。
        if IS_MAC:
            self._canvas_bg = BG
        else:
            self.root.configure(bg=TRANSPARENT_KEY)
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
            self._canvas_bg = TRANSPARENT_KEY

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.W = W_FULL
        self.H = H_FULL
        self.minimized = False
        self._save_pos = None       # 仅服务"最小化→恢复", 不持久化
        cfg = _load_config()
        pos = _clamp_pos(self.root, cfg.get("pos_x"), cfg.get("pos_y"),
                         self.W, self.H)
        if pos is None:
            pos = (sw - self.W - 30, sh - self.H - 60)
        self.root.geometry(f"{self.W}x{self.H}+{pos[0]}+{pos[1]}")

        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H,
                                bg=self._canvas_bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.show_form1 = False
        self.mute = False
        self.auto_browser = bool(cfg.get("auto_browser", True))
        self.data = {}
        self.last_mtime = 0
        self.live = {}
        self._live_mtime = 0
        self.horizon = cfg.get("horizon") if cfg.get("horizon") in HORIZONS else 7
        self._hovers = set()
        self._drag_offset = (0, 0)   # 按下时 光标屏幕坐标 - 窗口左上角
        self._dragging = False       # 只有真正落在拖拽区才为 True
        self._press_pos = None       # 本次按下时光标的屏幕坐标
        self._moved = False          # 本次按下后位移是否已超过 DRAG_THRESHOLD
        self._img_refs = []
        self._hover = None
        self._ball_imgs = {}         # {涨跌点色: 已光栅化的悬浮球贴图}
        self._voice_proc = None      # afplay/aplay 子进程, 切换前先杀旧的

        self.img_form1 = None
        self.img_form2 = None
        self._load_images()

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._do_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)

        self._draw()
        self._refresh_data()

        # 退出兜底(三重): 朋友不关机, Flask 残留会越积越多占 8000 端口。
        # 1) atexit: 正常退出(mainloop 结束/Python 退出)必跑
        # 2) WM_DELETE_WINDOW: 窗口关闭事件(含 macOS 红点)
        # 3) SIGTERM/SIGINT: 外部 kill / Ctrl-C
        # 关闭按钮走 _on_close 会主动杀一次, 这里再兜一次(pkill 重复无副作用)。
        atexit.register(self._kill_backend)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        for sig in (getattr(signal, "SIGTERM", None),
                    getattr(signal, "SIGINT", None)):
            if sig is not None:
                try:
                    signal.signal(sig, lambda *a: (self._kill_backend(),
                                                   sys.exit(0)))
                except (OSError, ValueError):
                    pass   # 非主线程/平台不支持时跳过, atexit 仍兜底

    def _load_image(self, path):
        """加载单张角色图, 优先 Pillow, Tk 原生垫底。

        历史教训: 用 Tk 自带解码器读大尺寸 RGBA PNG 再 subsample, 在
        macOS Tk 上虽不报错, 却会把图画坏(通道错位/花屏, 形似"默认图");
        Windows 又正常, 极具迷惑性。Pillow 的 PNG 解码与缩放在所有平台
        结果一致, 因此只要 Pillow 可用就全程走它; Tk 原生仅在 Pillow
        缺失时作为最后手段。两者都失败返回 None, 卡片照显、角色位留空。"""
        if not path.exists():
            print(f"[pet] 图片缺失: {path}", file=sys.stderr, flush=True)
            return None
        # 1) Pillow: 解码 + 高质量缩放一次完成(跨平台一致, 首选)
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            if im.height > im.width:
                nh, nw = IMG_TARGET_H, max(1, round(im.width * IMG_TARGET_H / im.height))
            else:
                nw, nh = IMG_TARGET_W, max(1, round(im.height * IMG_TARGET_W / im.width))
            resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            return ImageTk.PhotoImage(im.resize((nw, nh), resample))
        except Exception as e:
            print(f"[pet] Pillow 读图失败(退回 Tk 原生): {path.name} | {e}",
                  file=sys.stderr, flush=True)
        # 2) Tk 原生(Pillow 缺失时垫底; 老版本 Tk 对 PNG 支持有限)
        try:
            full = tk.PhotoImage(file=str(path))
            if full.height() > full.width():
                scale = max(1, round(full.height() / IMG_TARGET_H))
            else:
                scale = max(1, round(full.width() / IMG_TARGET_W))
            return full.subsample(scale)
        except tk.TclError as e:
            print(f"[pet] Tk 原生读图也失败(角色位留空): {path.name} | {e}",
                  file=sys.stderr, flush=True)
            return None

    def _load_images(self):
        for attr, path in [("img_form1", FORM1), ("img_form2", FORM2)]:
            img = self._load_image(path)
            setattr(self, attr, img)
            if img is not None:
                self._img_refs.append(img)

    def _minimize(self):
        self._save_pos = (self.root.winfo_x(), self.root.winfo_y())
        self.minimized = True
        self.W = W_MIN
        self.H = H_MIN
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"{W_MIN}x{H_MIN}+{sw - W_MIN - 20}+60")
        self.canvas.config(width=W_MIN, height=H_MIN)
        self._draw()

    def _restore(self):
        self.minimized = False
        self.W = W_FULL
        self.H = H_FULL
        self.root.geometry(
            f"{W_FULL}x{H_FULL}+{self._save_pos[0]}+{self._save_pos[1]}")
        self.canvas.config(width=W_FULL, height=H_FULL)
        self._draw()

    def _hit_button(self, x, y):
        """按钮命中检测。容差 BTN_R+2 使相邻按钮命中圈恰好相切(间距 20 = 10+10), 不重叠。"""
        for bid, cx in BTN_XS.items():
            if (x - cx) ** 2 + (y - BTN_Y) ** 2 <= (BTN_R + 2) ** 2:
                return bid
        return None

    def _hit_horizon(self, x, y):
        """日期块区域: 右侧底部，返回 horizon 或 None。
        与 _draw_horizon_bar 共用 SEG_W / BAR_* 常量, 避免命中区与绘制区错位。"""
        if x < BAR_X or not (BAR_Y <= y <= BAR_Y + BAR_H):
            return None
        idx = int((x - BAR_X) // SEG_W)
        if 0 <= idx < len(HORIZONS):
            return HORIZONS[idx]
        return None

    def _on_click(self, e):
        # 先置否, 只有落到拖拽分支才置真 —— 防止其它分支留下脏状态导致 <B1-Motion> 时窗口瞬移
        self._dragging = False
        self._press_pos = (e.x_root, e.y_root)
        self._moved = False

        if self.minimized:
            # 悬浮球: 允许拖拽移动; 若松键前没有位移, 当作点击 → 恢复窗口
            self._dragging = True
            self._drag_offset = (e.x_root - self.root.winfo_x(),
                                 e.y_root - self.root.winfo_y())
            return

        bid = self._hit_button(e.x, e.y)
        if bid == "close":
            self._on_close()
            return
        if bid == "min":
            self._minimize()
            return
        if bid == "mute":
            self.mute = not self.mute
            self._draw()
            return
        if bid == "browser":
            self.auto_browser = not self.auto_browser
            _save_config({"auto_browser": self.auto_browser})
            self._draw()
            return
        # 左侧角色区
        if e.x < CHAR_ZONE_R:
            self.show_form1 = not self.show_form1
            self._draw()
            if not self.mute:
                self._play_voice()
            if self.auto_browser:
                import webbrowser
                webbrowser.open("http://127.0.0.1:8000")
            return
        # 日期块
        h = self._hit_horizon(e.x, e.y)
        if h is not None:
            self._set_horizon(h)
            return
        # 拖拽: 记录光标相对窗口左上角的偏移(屏幕坐标, 窗口移动后依然成立)
        self._dragging = True
        self._drag_offset = (e.x_root - self.root.winfo_x(),
                             e.y_root - self.root.winfo_y())

    def _do_drag(self, e):
        if not self._dragging:
            return
        # 未超过阈值前不动, 避免把点击误判成拖拽
        if not self._moved:
            if (abs(e.x_root - self._press_pos[0]) < DRAG_THRESHOLD
                    and abs(e.y_root - self._press_pos[1]) < DRAG_THRESHOLD):
                return
            self._moved = True
        self.root.geometry(
            f"+{e.x_root - self._drag_offset[0]}"
            f"+{e.y_root - self._drag_offset[1]}")

    def _on_release(self, _e):
        # 悬浮球上"没移动的按下" = 点击 → 恢复窗口; 拖动过则只结束拖拽
        clicked_ball = self._dragging and self.minimized and not self._moved
        moved = self._moved
        self._dragging = False
        self._press_pos = None
        self._moved = False
        if clicked_ball:
            self._restore()
            return
        if moved:      # 拖完才落盘, 避免每次点击都写文件
            _save_config({"pos_x": self.root.winfo_x(),
                          "pos_y": self.root.winfo_y()})

    def _on_motion(self, e):
        if self.minimized:
            return
        bid = self._hit_button(e.x, e.y)
        hov_now = set()
        if e.x >= BAR_X and BAR_Y <= e.y <= BAR_Y + BAR_H:
            h = self._hit_horizon(e.x, e.y)
            if h is not None:
                hov_now.add(h)
        if bid != self._hover or hov_now != self._hovers:
            self._hover = bid
            self._hovers = hov_now
            self._draw()

    def _on_leave(self, _e):
        if self._hover is not None or self._hovers:
            self._hover = None
            self._hovers = set()
            self._draw()

    def _play_voice(self):
        voice = VOICE1 if self.show_form1 else VOICE2
        if not voice.exists():
            return
        try:
            if IS_MAC:
                self._stop_voice_proc()
                self._voice_proc = subprocess.Popen(
                    ["afplay", str(voice)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
            elif IS_WIN:
                import winsound
                winsound.PlaySound(
                    str(voice), winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                # Linux: 尝试 aplay
                self._stop_voice_proc()
                self._voice_proc = subprocess.Popen(
                    ["aplay", "-q", str(voice)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _stop_voice_proc(self):
        """杀掉上一个声音子进程, 避免新旧 afplay 抢音频设备导致静默失败。"""
        p = self._voice_proc
        if p is not None and p.poll() is None:
            try:
                p.kill()
                p.wait(timeout=1)
            except Exception:
                pass
        self._voice_proc = None

    def _kill_backend(self) -> bool:
        """杀掉本夹启动的 Flask 服务(app.cli serve)。
        返回 True=已杀或本就没有, False=尝试失败(可能残留)。
        关闭桌宠时必调: 否则 Flask 会一直占着 8000 端口, 朋友不关机时
        残留进程越积越多(Cmd+Q/Dock退出都不经过关闭按钮)。"""
        try:
            if IS_MAC or not IS_WIN:
                r = subprocess.run(["pkill", "-f", "app.cli serve"],
                                   timeout=5, capture_output=True)
                return r.returncode in (0, 1)
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | Where-Object { "
                 "($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') "
                 "-and $_.CommandLine -match 'app.cli serve' } "
                 "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                timeout=5, capture_output=True, text=True)
            return r.returncode == 0
        except Exception:
            return False

    def _on_close(self):
        """统一关闭入口: 杀 Flask + 杀声音 + 销毁窗口。
        关闭按钮、原生窗口×、Cmd+Q 都走这里。"""
        if not self._kill_backend():
            try:
                from tkinter import messagebox
                messagebox.showwarning(
                    "服务未能自动关闭",
                    "后台汇率服务(app.cli serve)未能自动结束,\n"
                    "请在任务管理器中手动关闭 python.exe / pythonw.exe,\n"
                    "否则它会继续每小时更新数据。")
            except Exception:
                pass
        self._stop_voice_proc()
        self.root.destroy()

    def _horizon_file(self):
        return FORECAST_FILES.get(self.horizon, FORECAST_FILE)

    def _read_forecast(self):
        """只读文件, 不重排定时器。供 _set_horizon 复用。"""
        try:
            f = self._horizon_file()
            if not f.exists():
                # 刚解压、服务尚未写出首个预测: 进入等待态而不是沿用上一周期
                self.data = {}
                return
            mtime = f.stat().st_mtime
            # 陈旧保护: 文件超过 10 分钟没被重写, 视为"无本文件夹服务在更新",
            # 主动清空 —— 服务恢复写入后(本文件 mtime 一变)自动恢复显示。
            if dt.datetime.now().timestamp() - mtime > FORECAST_STALE_SEC:
                self.data = {}
                return
            if mtime != self.last_mtime:
                self.last_mtime = mtime
                obj = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(obj, dict):    # 顶层非对象则丢弃, 防下游 .get 崩
                    self.data = obj
        except Exception:
            pass

    def _read_live(self):
        """读 MOEX 实时市场价。文件缺失/损坏/过期 -> 清空, 由 _draw_data 回退官方牌价。"""
        try:
            if not LIVE_FILE.exists():
                self.live = {}
                return
            mtime = LIVE_FILE.stat().st_mtime
            if mtime != self._live_mtime:
                self._live_mtime = mtime
                obj = json.loads(LIVE_FILE.read_text(encoding="utf-8"))
                self.live = obj if isinstance(obj, dict) else {}
        except Exception:
            self.live = {}
            return
        # 新鲜度: 快层每 60s 写一次, 太久没更新说明调度器/网络已断, 不能当实时价用
        price = self.live.get("price")
        stamp = self.live.get("fetched_at")
        if (not isinstance(stamp, str)
                or not isinstance(price, (int, float)) or price <= 0):
            self.live = {}
            return
        try:
            age = (dt.datetime.now()
                   - dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")).total_seconds()
        except ValueError:
            self.live = {}
            return
        if age > LIVE_STALE_SEC or age < -60:
            self.live = {}

    def _refresh_data(self):
        """唯一一条 5s 定时链, 只在 __init__ 启动一次。
        先排下一拍再绘制: 绘制出任何异常都不得掐断刷新链(无控制台, 断了无人知)。"""
        self.root.after(5000, self._refresh_data)
        self._read_live()
        self._read_forecast()
        self._draw()

    def _set_horizon(self, h):
        if h == self.horizon:
            return
        self.horizon = h
        self.last_mtime = -1
        self.data = {}
        self._read_forecast()
        self._draw()
        _save_config({"horizon": h})

    # ---------- 绘制 ----------

    @staticmethod
    def _round_rect(c, x1, y1, x2, y2, r, **kw):
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return c.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        c = self.canvas
        c.delete("all")
        if self.minimized:
            self._draw_ball()
        else:
            self._draw_full()

    def _ball_image(self, pred):
        """取缓存的悬浮球贴图(按涨跌点颜色缓存, 每色只光栅化一次)。"""
        color = UP_COLOR if pred == 1 else DOWN_COLOR
        img = self._ball_imgs.get(color)
        if img is None:
            img = render_ball(color, W_MIN)
            self._ball_imgs[color] = img
            self._img_refs.append(img)      # 持有引用, 防止被 GC
        return img

    def _draw_ball(self):
        """绘制最小化悬浮球。Windows用光栅化贴图, macOS用Canvas矢量绘制。"""
        pred = (self.data.get("direction") or {}).get("prediction", 0)
        _conf = (self.data.get("direction") or {}).get("confidence")
        _conf = _conf if isinstance(_conf, (int, float)) else 0.5
        weak = _conf < 0.55
        c = self.canvas
        dot_color = WEAK_COLOR if weak else (UP_COLOR if pred == 1 else DOWN_COLOR)

        if IS_MAC:
            # macOS: Canvas矢量绘制(不依赖transparentcolor)
            c.delete("all")
            cx, cy, r = W_MIN // 2, W_MIN // 2, 22
            # 外圈深色
            c.create_oval(cx - r, cy - r, cx + r, cy + r,
                          fill="#14171D", outline="")
            # 金色环
            c.create_oval(cx - r + 1, cy - r + 1, cx + r - 1, cy + r - 1,
                          outline="#D4A843", width=2)
            # 内部渐变底盘
            c.create_oval(cx - r + 3, cy - r + 3, cx + r - 3, cy + r - 3,
                          fill="#3A4250", outline="")
            # 眼睛
            c.create_oval(cx - 6, cy - 4, cx + 2, cy + 4,
                          fill="#2D8B57", outline="#1A1210", width=1)
            c.create_oval(cx + 2, cy - 4, cx + 10, cy + 4,
                          fill="#2D8B57", outline="#1A1210", width=1)
            c.create_oval(cx - 3, cy - 1, cx - 1, cy + 1, fill="white", outline="")
            c.create_oval(cx + 5, cy - 1, cx + 7, cy + 1, fill="white", outline="")
            # 涨跌指示点
            c.create_oval(cx + 8, cy - 14, cx + 16, cy - 6,
                          fill=dot_color, outline="")
        else:
            # Windows: 光栅化抗锯齿贴图
            c.create_image(0, 0, image=self._ball_image(pred), anchor="nw")

    def _f(self, size, strong=False):
        """构造 Tk 字体规格。强调文字优先用真实字重, 取不到才加合成 bold。"""
        if strong:
            return ((self.font_strong, size) if self._strong_real
                    else (self.font_strong, size, "bold"))
        return (self.font, size)

    def _draw_full(self):
        c = self.canvas
        # 卡片
        self._round_rect(c, CARD_X0, CARD_Y0, CARD_X1, CARD_Y1, CARD_R,
                         fill=BG, outline=DIVIDER, width=1)
        # 左: 角色面板 + 角色图 + 形态芯片
        self._round_rect(c, CHAR_X0, CHAR_Y0, CHAR_X1, CHAR_Y1, CHAR_R,
                         fill=PANEL, outline="")
        img = self.img_form1 if self.show_form1 else self.img_form2
        if img:
            # 垂直居中于「面板顶 .. 芯片顶」之间, 避免压到芯片
            c.create_image((CHAR_X0 + CHAR_X1) // 2,
                           (CHAR_Y0 + CHIP_Y0) // 2 - 4, image=img)
        label = "儿子" if self.show_form1 else "奶龙"
        self._round_rect(c, CHIP_X0, CHIP_Y0, CHIP_X1, CHIP_Y1, CHIP_R,
                         fill=BTN_BG, outline="")
        c.create_text((CHIP_X0 + CHIP_X1) // 2, (CHIP_Y0 + CHIP_Y1) // 2,
                      text=label, fill=ACCENT, font=self._f(FS_SMALL, True))
        # 右: 数据区
        self._draw_data()
        # 右上: 图标按钮
        self._draw_buttons()

    def _draw_buttons(self):
        """四个矢量图标按钮(弃用 emoji, 保证风格统一)。"""
        c = self.canvas
        for bid, cx in BTN_XS.items():
            hover = self._hover == bid
            if bid == "close":
                bg = BTN_CLOSE_HOVER if hover else BTN_BG
            else:
                bg = BTN_HOVER if hover else BTN_BG
            c.create_oval(cx - BTN_R, BTN_Y - BTN_R, cx + BTN_R, BTN_Y + BTN_R,
                          fill=bg, outline="", tags="btn")
            self._draw_icon(c, bid, cx, BTN_Y, hover, "btn")

    def _draw_icon(self, c, bid, cx, cy, hover, tag):
        """在 (cx, cy) 画一个 16x16 内的单色矢量图标。"""
        if bid == "close":
            fg = TEXT_HI if hover else TEXT_MD
            k = 3.5
            c.create_line(cx - k, cy - k, cx + k, cy + k,
                          fill=fg, width=1.4, capstyle="round", tags=tag)
            c.create_line(cx - k, cy + k, cx + k, cy - k,
                          fill=fg, width=1.4, capstyle="round", tags=tag)
            return

        if bid == "min":
            fg = TEXT_HI if hover else TEXT_MD
            c.create_line(cx - 4, cy, cx + 4, cy,
                          fill=fg, width=1.5, capstyle="round", tags=tag)
            return

        if bid == "mute":
            on = not self.mute
            fg = (TEXT_HI if hover else TEXT_MD) if on else TEXT_DIM
            # 喇叭主体
            c.create_polygon(cx - 5, cy - 2, cx - 2, cy - 2, cx + 1, cy - 5,
                             cx + 1, cy + 5, cx - 2, cy + 2, cx - 5, cy + 2,
                             fill=fg, outline="", tags=tag)
            # 声波弧(静音时不画)
            if on:
                c.create_arc(cx - 4, cy - 5, cx + 4, cy + 5,
                             start=-50, extent=100, style="arc",
                             outline=fg, width=1.3, tags=tag)
                c.create_arc(cx - 3, cy - 8, cx + 7, cy + 8,
                             start=-50, extent=100, style="arc",
                             outline=fg, width=1.3, tags=tag)
            # 斜杠表示已静音
            if not on:
                c.create_line(cx - 6, cy + 6, cx + 6, cy - 6,
                              fill=TEXT_DIM, width=1.3, capstyle="round",
                              tags=tag)
            return

        # browser: 地球(圆 + 竖椭圆经线 + 赤道)
        on = self.auto_browser
        fg = (TEXT_HI if hover else ACCENT) if on else TEXT_DIM
        r = 5
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                      outline=fg, width=1.3, tags=tag)
        c.create_oval(cx - r * 0.5, cy - r, cx + r * 0.5, cy + r,
                      outline=fg, width=1.0, tags=tag)
        c.create_line(cx - r, cy, cx + r, cy, fill=fg, width=1.0, tags=tag)
        if not on:
            c.create_line(cx - 6, cy + 6, cx + 6, cy - 6,
                          fill=TEXT_DIM, width=1.3, capstyle="round", tags=tag)

    def _draw_data(self):
        """右区: 标题行 + hero 预测块 + 两列字段 + 日期块。"""
        c = self.canvas
        d = self.data.get("direction") or {}
        pred = 1 if d.get("prediction") == 1 else 0   # 归一化, 兼防 HERO_BG[pred] KeyError
        # 下面三个字段一律类型收敛: forecast JSON 一旦被写坏, 桌宠无控制台, 崩了没人知道
        conf = d.get("confidence")
        conf = conf if isinstance(conf, (int, float)) else 0.5
        # 弱信号口径(与网页端一致): 把握度 < 55% 一律标弱, 其余显示涨跌
        weak = conf < 0.55
        # 显示价: 优先 MOEX 实时市场价(第二行配时间 HH:MM), 取不到回退 forecast 里的
        # 官方牌价(第二行配日期 YYYY-MM-DD)。标签文字不变, 口径差异靠这一行格式区分。
        live_price = self.live.get("price")
        live_price = (live_price if isinstance(live_price, (int, float))
                      and live_price > 0 else None)
        rate = self.data.get("base_rate")
        rate = rate if isinstance(rate, (int, float)) else 0
        as_of = str(self.data.get("as_of") or "")[:10]
        if live_price:
            rate = live_price
            as_of = str(self.live.get("time") or "")

        color = UP_COLOR if pred == 1 else DOWN_COLOR
        word = "涨" if pred == 1 else "跌"
        arrow = "▲" if pred == 1 else "▼"
        if weak:
            color = WEAK_COLOR
            word = "弱"
            arrow = "—"
        pct = f"{conf * 100:.1f}%"

        # 标题行: 币种(与右上按钮同排; 文案与字号按实测留出按钮左缘 233 的空间)
        c.create_text(DATA_X0, HEAD_Y, anchor="w", text="CNY/RUB",
                      fill=TEXT_DIM, font=self._f(FS_HEAD, True))

        if not self.data:
            c.create_text(DATA_X0, HERO_ROW_Y, anchor="w",
                          text="等待数据…", fill=TEXT_DIM,
                          font=self._f(FS_VALUE))
            self._draw_horizon_bar()
            return

        # hero: 方向色淡染底色(弱信号用灰)
        self._round_rect(c, HERO_X0, HERO_Y0, HERO_X1, HERO_Y1, HERO_R,
                         fill=HERO_BG["weak"] if weak
                         else HERO_BG[pred], outline="")

        # 方向词 + 概率 并排一行, 整行在 hero 内垂直居中
        c.create_text(DATA_X0, HERO_ROW_Y, anchor="w", text=f"{arrow} {word}",
                      fill=color, font=self._f(FS_HERO_DIR, True))
        c.create_text(HERO_PCT_X, HERO_ROW_Y, anchor="w", text=pct,
                      fill=TEXT_HI, font=self._f(FS_HERO_PCT, True))

        # 字段行: 短标签 + 值 同行左对齐(两列并排放不下日期, 实测宽 86px)
        c.create_text(DATA_X0, INFO1_Y, anchor="w", text="汇率",
                      fill=TEXT_DIM, font=self._f(FS_LABEL))
        c.create_text(INFO_VALUE_X, INFO1_Y, anchor="w",
                      text=f"{rate:.2f} ₽/¥" if rate else "—",
                      fill=TEXT_MD, font=self._f(FS_VALUE, True))
        c.create_text(DATA_X0, INFO2_Y, anchor="w", text="截至",
                      fill=TEXT_DIM, font=self._f(FS_LABEL))
        c.create_text(INFO_VALUE_X, INFO2_Y, anchor="w", text=as_of or "—",
                      fill=TEXT_MD, font=self._f(FS_VALUE, True))

        # 日期块
        self._draw_horizon_bar()

    def _draw_horizon_bar(self):
        """4 段 horizon 日期块, 拉满数据区宽度, 当前选中高亮, hover 变色。
        尺寸必须与 _hit_horizon 的 SEG_W / BAR_* 保持一致。"""
        c = self.canvas
        label = {7: "7日", 30: "30日", 60: "60日", 90: "90日"}
        for i, h in enumerate(HORIZONS):
            x1 = BAR_X + i * SEG_W
            x2 = x1 + SEG_W - 2
            is_active = (h == self.horizon)
            is_hov = (h in self._hovers)
            if is_active:
                bg, fg = ACCENT, BG
            elif is_hov:
                bg, fg = BTN_HOVER, TEXT_HI
            else:
                bg, fg = BTN_BG, TEXT_DIM
            self._round_rect(c, x1, BAR_Y, x2, BAR_Y + BAR_H, 7,
                             fill=bg, outline="")
            c.create_text((x1 + x2) // 2, BAR_Y + BAR_H // 2,
                          text=label.get(h, f"{h}日"), fill=fg,
                          font=self._f(FS_CHIP, True))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    FloatingPet().run()
