"""桌面宠物浮窗：现代暗色圆角卡片 + 真实图片 + 交互 + 语音 + 最小化悬浮球。

视觉：
- 透明窗口 + Canvas 圆角卡片（#1E222A）
- 文字层级：标题 #FFFFFF / 正文 #B8C0CC / 弱化 #6B7380，青色霓虹点缀
- 右上角三个圆形 icon 按钮（✕ / — / 🔊），hover 变色

交互（逻辑未改）：
- 点击角色区切换形态 + 播放语音 + 打开网页
- 右上按钮：关闭 / 最小化 / 静音
- 右侧数据区拖拽移动；最小化后点击悬浮球恢复
- 每 5s 读 forecast_*.json 刷新数据
- 日期块切换 7/30/60/90 日 horizon，数据联动
"""
import json
import math
import tkinter as tk
from pathlib import Path

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

# 透明键色
TRANSPARENT_KEY = "#FF00FE"

BG = "#1E222A"
PANEL = "#14171D"
DIVIDER = "#2A303C"
TEXT_HI = "#FFFFFF"
TEXT_MD = "#B8C0CC"
TEXT_DIM = "#6B7380"
ACCENT = "#4FD1C5"
BTN_BG = "#2A333C"
BTN_HOVER = "#3B4757"
BTN_CLOSE_HOVER = "#E5484D"
UP_COLOR = "#FF6B5E"
DOWN_COLOR = "#35D0A0"
BOX_BG = "#1A1D24"

IMG_TARGET_H = 120
IMG_TARGET_W = 118

W_FULL, H_FULL = 300, 165
W_MIN, H_MIN = 48, 48

BTN_R = 7
BTN_Y = 20
BTN_XS = {"mute": 248, "min": 267, "close": 286}

FONT = "Microsoft YaHei"  # 全局统一字体


class FloatingPet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CNY/RUB 桌宠")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT_KEY)
        self.root.attributes("-transparentcolor", TRANSPARENT_KEY)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.W = W_FULL
        self.H = H_FULL
        self.minimized = False
        self._save_pos = None
        self.root.geometry(
            f"{self.W}x{self.H}+{sw - self.W - 30}+{sh - self.H - 60}")

        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H,
                                bg=TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.show_form1 = False
        self.mute = False
        self.data = {}
        self.last_mtime = 0
        self.horizon = 7
        self._hovers = set()
        self._drag_data = {"x": 0, "y": 0}
        self._img_refs = []
        self._hover = None
        self.frame = 0  # 动画帧计数器

        self.img_form1 = None
        self.img_form2 = None
        self._load_images()

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._do_drag)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)

        self._draw()
        self._refresh_data()

    def _load_images(self):
        for attr, path in [("img_form1", FORM1), ("img_form2", FORM2)]:
            if path.exists():
                full = tk.PhotoImage(file=str(path))
                if full.height() > full.width():
                    scale = max(1, round(full.height() / IMG_TARGET_H))
                else:
                    scale = max(1, round(full.width() / IMG_TARGET_W))
                img = full.subsample(scale)
                setattr(self, attr, img)
                self._img_refs.append(img)
            else:
                setattr(self, attr, None)

    def _minimize(self):
        self._save_pos = (self.root.winfo_x(), self.root.winfo_y())
        self.minimized = True
        self.W = W_MIN
        self.H = W_MIN
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
        for bid, cx in BTN_XS.items():
            if (x - cx) ** 2 + (y - BTN_Y) ** 2 <= (BTN_R + 3) ** 2:
                return bid
        return None

    def _hit_horizon(self, x, y):
        """日期块区域: 右侧底部，返回 horizon 或 None"""
        if x < 160 or not (128 <= y <= 154):
            return None
        seg_w = 28  # 实际按钮宽度
        idx = int((x - 160) // seg_w)
        if 0 <= idx < len(HORIZONS):
            return HORIZONS[idx]
        return None

    def _on_click(self, e):
        if self.minimized:
            self._restore()
            return

        bid = self._hit_button(e.x, e.y)
        if bid == "close":
            import subprocess
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" "
                 "| Where-Object { $_.CommandLine -match 'app.cli serve' } "
                 "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                timeout=5, capture_output=True)
            self.root.destroy()
            return
        if bid == "min":
            self._minimize()
            return
        if bid == "mute":
            self.mute = not self.mute
            self._draw()
            return
        # 左侧角色区
        if e.x < 150:
            self.show_form1 = not self.show_form1
            self._draw()
            if not self.mute:
                self._play_voice()
            import webbrowser
            webbrowser.open("http://127.0.0.1:8000")
            return
        # 日期块
        h = self._hit_horizon(e.x, e.y)
        if h is not None:
            self._set_horizon(h)
            return
        # 拖拽
        self._drag_data = {"x": e.x, "y": e.y}

    def _do_drag(self, e):
        dx = e.x - self._drag_data["x"]
        dy = e.y - self._drag_data["y"]
        self.root.geometry(
            f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")

    def _on_motion(self, e):
        if self.minimized:
            return
        bid = self._hit_button(e.x, e.y)
        hov_now = set()
        if e.x >= 160 and 128 <= e.y <= 154:
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
        import winsound
        try:
            winsound.PlaySound(
                str(voice), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

    def _horizon_file(self):
        return FORECAST_FILES.get(self.horizon, FORECAST_FILE)

    def _refresh_data(self):
        try:
            f = self._horizon_file()
            if f.exists():
                mtime = f.stat().st_mtime
                if mtime != self.last_mtime:
                    self.data = json.loads(
                        f.read_text(encoding="utf-8"))
                    self.last_mtime = mtime
        except Exception:
            pass
        self.root.after(5000, self._refresh_data)
        self._draw()

    def _set_horizon(self, h):
        if h == self.horizon:
            return
        self.horizon = h
        self.last_mtime = -1
        self.data = {}
        self._refresh_data()
        self._draw()

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
        # 悬浮球动画帧递增
        if self.minimized:
            self.frame += 1
            self.root.after(200, self._draw)  # 200ms 更新一次

    def _draw_ball(self):
        """悬浮球：第一版风格（黄色圆+绿眼睛+呼吸动画）"""
        c = self.canvas
        cx, cy = W_MIN // 2, H_MIN // 2
        
        # 呼吸动画（上下浮动）
        breath = math.sin(self.frame * math.pi / 2) * 2
        cy += breath
        
        # 身体（黄色圆）
        body_r = 20
        c.create_oval(cx - body_r, cy - body_r, cx + body_r, cy + body_r,
                      fill="#FFD93D", outline="#D4A843", width=2)
        
        # 眼睛（绿色虹膜）
        eye_x, eye_y = cx - 3, cy - 2
        eye_r = 7
        # 白底
        c.create_oval(eye_x - eye_r, eye_y - eye_r, eye_x + eye_r, eye_y + eye_r,
                      fill="white", outline="#D4A843", width=1)
        # 绿虹膜
        iris_r = 5
        c.create_oval(eye_x - iris_r, eye_y - iris_r, eye_x + iris_r, eye_y + iris_r,
                      fill="#4FD1C5", outline="")
        # 黑瞳孔
        pupil_r = 2
        c.create_oval(eye_x - pupil_r, eye_y - pupil_r, eye_x + pupil_r, eye_y + pupil_r,
                      fill="#1A1D24", outline="")
        # 高光
        hl_x, hl_y = eye_x - 2, eye_y - 2
        c.create_oval(hl_x - 1, hl_y - 1, hl_x + 1, hl_y + 1,
                      fill="white", outline="")
        
        # 嘴巴（根据情绪）
        mouth_y = cy + 10
        d = self.data.get("direction", {})
        pred = d.get("prediction", 0)
        if pred == 1:
            # 看涨：开心弧线
            c.create_arc(cx - 6, mouth_y - 4, cx + 6, mouth_y + 4,
                         start=200, extent=140, style="arc", outline="#D4A843", width=2)
        else:
            # 看跌：担心弧线
            c.create_arc(cx - 6, mouth_y - 2, cx + 6, mouth_y + 6,
                         start=20, extent=140, style="arc", outline="#D4A843", width=2)

    def _draw_full(self):
        c = self.canvas
        self._round_rect(c, 3, 3, self.W - 3, self.H - 3, 16,
                         fill=BG, outline=DIVIDER, width=1)
        self._round_rect(c, 10, 6, 146, 134, 12, fill=PANEL, outline="")
        img = self.img_form1 if self.show_form1 else self.img_form2
        if img:
            c.create_image(78, 70, image=img)
        label = "儿子" if self.show_form1 else "奶龙"
        self._round_rect(c, 55, 138, 101, 154, 8, fill=BTN_BG, outline="")
        c.create_text(78, 146, text=label, fill=ACCENT,
                      font=(FONT, 10))
        c.create_line(150, 16, 150, 148, fill=DIVIDER, width=1)
        self._draw_data(160)
        self._draw_buttons()

    def _draw_buttons(self):
        c = self.canvas
        for bid, cx in BTN_XS.items():
            hover = self._hover == bid
            if bid == "close":
                bg = BTN_CLOSE_HOVER if hover else BTN_BG
            else:
                bg = BTN_HOVER if hover else BTN_BG
            c.create_oval(cx - BTN_R, BTN_Y - BTN_R, cx + BTN_R, BTN_Y + BTN_R,
                          fill=bg, outline="", tags="btn")
            fg = TEXT_HI if hover else TEXT_MD
            if bid == "close":
                txt = "✕"
            elif bid == "min":
                txt = "—"
            else:
                txt = "🔇" if self.mute else "🔊"
            c.create_text(cx, BTN_Y, text=txt, fill=fg,
                          font=(FONT, 8), tags="btn")

    def _redraw_buttons(self):
        self.canvas.delete("btn")
        self._draw_buttons()

    def _draw_data(self, x0):
        c = self.canvas
        d = self.data.get("direction", {})
        pred = d.get("prediction", 0)
        conf = d.get("confidence", 0.5)
        rate = self.data.get("base_rate", 0)
        as_of = self.data.get("as_of", "")[:10]

        color = UP_COLOR if pred == 1 else DOWN_COLOR
        word = "涨" if pred == 1 else "跌"
        arrow = "▲" if pred == 1 else "▼"
        pct = f"{conf * 100:.2f}%"
        rate_text = f"{rate:.2f} ₽/¥" if rate else ""

        # 标题区
        c.create_oval(x0, 20, x0 + 6, 26, fill=ACCENT, outline="")
        c.create_text(x0 + 12, 23, anchor="w", text="CNY / RUB", fill=TEXT_DIM,
                      font=(FONT, 10))

        if not self.data:
            c.create_text(x0, self.H // 2, anchor="w", text="等待数据…",
                          fill=TEXT_DIM, font=(FONT, 12))
            self._draw_horizon_bar(160, 138)
            return

        # 容器参数
        box_w = 120
        box_h = 26
        gap = 4
        F = (FONT, 11, "bold")

        # 容器1: 涨跌 + 概率
        box1_y = 40
        self._round_rect(c, x0, box1_y, x0 + box_w, box1_y + box_h, 8,
                         fill=BOX_BG, outline=DIVIDER)
        txt1 = f"{arrow} {word}  {pct}"
        c.create_text(x0 + box_w // 2, box1_y + box_h // 2,
                      text=txt1, fill=color, font=F, anchor="center")

        # 容器2: 汇率
        box2_y = box1_y + box_h + gap
        self._round_rect(c, x0, box2_y, x0 + box_w, box2_y + box_h, 8,
                         fill=BOX_BG, outline=DIVIDER)
        c.create_text(x0 + box_w // 2, box2_y + box_h // 2,
                      text=rate_text, fill=TEXT_MD, font=F, anchor="center")

        # 容器3: 日期
        box3_y = box2_y + box_h + gap
        self._round_rect(c, x0, box3_y, x0 + box_w, box3_y + box_h, 8,
                         fill=BOX_BG, outline=DIVIDER)
        c.create_text(x0 + box_w // 2, box3_y + box_h // 2,
                      text=as_of, fill=TEXT_DIM, font=F, anchor="center")

        # 日期块按钮栏（紧贴容器3下方）
        self._draw_horizon_bar(160, box3_y + box_h + gap)

    def _draw_horizon_bar(self, x0, y):
        """4 段 horizon 日期块按钮栏，当前选中高亮，hover 变色"""
        c = self.canvas
        seg_w = 30
        for i, h in enumerate(HORIZONS):
            x1 = x0 + i * seg_w
            x2 = x1 + seg_w - 2
            is_active = (h == self.horizon)
            is_hov = (h in self._hovers)
            if is_active:
                bg = ACCENT
                fg = BG
            elif is_hov:
                bg = BTN_HOVER
                fg = TEXT_HI
            else:
                bg = BTN_BG
                fg = TEXT_DIM
            self._round_rect(c, x1, y, x2, y + 16, 6, fill=bg, outline="")
            c.create_text((x1 + x2) // 2, y + 8, text=f"{h}日", fill=fg,
                          font=(FONT, 8, "bold"))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    FloatingPet().run()
