"""桌面宠物浮窗：现代暗色圆角卡片 + 真实图片 + 交互 + 语音 + 最小化悬浮球。

视觉：
- 透明窗口 + Canvas 圆角卡片（#1E222A）
- 文字层级：标题 #FFFFFF / 正文 #B8C0CC / 弱化 #6B7380，青色霓虹点缀
- 右上角三个圆形 icon 按钮（✕ / — / 🔊），hover 变色

交互（逻辑未改）：
- 点击角色区切换形态 + 播放语音 + 打开网页
- 右上按钮：关闭 / 最小化 / 静音
- 右侧数据区拖拽移动；最小化后点击悬浮球恢复
- 每 5s 读 forecast_7.json 刷新数据
"""
import json
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FORECAST_FILE = ROOT / "data" / "forecast_7.json"
FORM1 = ROOT / "data" / "pet" / "form1.png"
FORM2 = ROOT / "data" / "pet" / "form2.png"
VOICE1 = ROOT / "data" / "pet" / "voice1.wav"
VOICE2 = ROOT / "data" / "pet" / "voice2.wav"

# 透明键色：窗口里这个颜色会被挖成透明，露出桌面
TRANSPARENT_KEY = "#FF00FE"

BG = "#1E222A"           # 卡片底色
PANEL = "#14171D"        # 角色图承接底板
DIVIDER = "#2A303C"      # 分隔线
TEXT_HI = "#FFFFFF"      # 标题 / 强调
TEXT_MD = "#B8C0CC"      # 正文
TEXT_DIM = "#6B7380"     # 弱化
ACCENT = "#4FD1C5"       # 青色霓虹点缀
BTN_BG = "#2A333C"       # 按钮底
BTN_HOVER = "#3B4757"    # 次级按钮 hover
BTN_CLOSE_HOVER = "#E5484D"
UP_COLOR = "#FF6B5E"     # 涨（红）
DOWN_COLOR = "#35D0A0"   # 跌（绿）

IMG_TARGET_H = 120       # 竖图按高度适配
IMG_TARGET_W = 118       # 方图按宽度适配

W_FULL, H_FULL = 300, 165
W_MIN, H_MIN = 48, 48

BTN_R = 7
BTN_Y = 20
BTN_XS = {"mute": 248, "min": 267, "close": 286}

CN_FONT = "Microsoft YaHei"   # 中文
NUM_FONT = "Bahnschrift"      # 数字/英文(高级 DIN 风, Win10+ 自带)
RATE_FONT = "Segoe UI"        # 汇率(含 ₽ 符号)
TITLE_FONT = CN_FONT           # 兼容别名


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
        self._drag_data = {"x": 0, "y": 0}
        self._img_refs = []
        self._hover = None

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
        for bid, cx in BTN_XS.items():
            if (x - cx) ** 2 + (y - BTN_Y) ** 2 <= (BTN_R + 3) ** 2:
                return bid
        return None

    def _on_click(self, e):
        if self.minimized:
            self._restore()
            return

        bid = self._hit_button(e.x, e.y)
        # ✕ 关闭
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
        # — 最小化
        if bid == "min":
            self._minimize()
            return
        # 🔊/🔇 静音
        if bid == "mute":
            self.mute = not self.mute
            self._draw()
            return
        # 左侧角色区 → 切换形态 + 播放语音 + 打开网页
        if e.x < 150:
            self.show_form1 = not self.show_form1
            self._draw()
            if not self.mute:
                self._play_voice()
            import webbrowser
            webbrowser.open("http://127.0.0.1:8000")
            return
        # 右侧数据区 → 拖拽
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
        if bid != self._hover:
            self._hover = bid
            self._redraw_buttons()

    def _on_leave(self, _e):
        if self._hover is not None:
            self._hover = None
            self._redraw_buttons()

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

    def _refresh_data(self):
        try:
            if FORECAST_FILE.exists():
                mtime = FORECAST_FILE.stat().st_mtime
                if mtime != self.last_mtime:
                    self.data = json.loads(
                        FORECAST_FILE.read_text(encoding="utf-8"))
                    self.last_mtime = mtime
        except Exception:
            pass
        self.root.after(5000, self._refresh_data)
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

    def _draw_ball(self):
        c = self.canvas
        cx, cy = W_MIN // 2, H_MIN // 2
        # 底部阴影
        c.create_oval(cx - 16, cy - 8, cx + 16, cy + 20,
                      fill="#12161D", outline="")
        # 外光环
        c.create_oval(cx - 21, cy - 21, cx + 21, cy + 21,
                      fill="#2A333C", outline="")
        # 同心椭圆叠出渐变光泽
        for r, col in [(19, "#26303E"), (16, "#2C3948"), (13, "#35455A"),
                       (10, "#3F536B"), (7, "#4A617C")]:
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=col, outline="")
        # 左上高光
        c.create_oval(cx - 12, cy - 14, cx - 3, cy - 5,
                      fill="#6E8BAE", outline="")
        c.create_oval(cx - 10, cy - 12, cx - 5, cy - 7,
                      fill="#A6BEDA", outline="")
        # 状态点 + 圆环
        d = self.data.get("direction", {})
        color = UP_COLOR if d.get("prediction", 0) == 1 else DOWN_COLOR
        dx, dy = cx + 11, cy - 11
        c.create_oval(dx - 7, dy - 7, dx + 7, dy + 7,
                      fill="#1E222A", outline=color)
        c.create_oval(dx - 4, dy - 4, dx + 4, dy + 4, fill=color, outline="")

    def _draw_full(self):
        c = self.canvas

        # 圆角卡片
        self._round_rect(c, 3, 3, self.W - 3, self.H - 3, 16,
                         fill=BG, outline=DIVIDER, width=1)

        # 角色图底板
        self._round_rect(c, 10, 6, 146, 134, 12, fill=PANEL, outline="")

        # 角色图片（黑底原图，直接承接在卡片上）
        img = self.img_form1 if self.show_form1 else self.img_form2
        if img:
            c.create_image(78, 70, image=img)

        # 当前形态小标签
        label = "儿子" if self.show_form1 else "奶龙"
        self._round_rect(c, 55, 138, 101, 154, 8, fill=BTN_BG, outline="")
        c.create_text(78, 146, text=label, fill=ACCENT,
                      font=(CN_FONT, 10))

        # 竖向分隔线
        c.create_line(150, 16, 150, 148, fill=DIVIDER, width=1)

        # 数据区
        self._draw_data(160)

        # 按钮栏
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
                          font=(CN_FONT, 8), tags="btn")

    def _redraw_buttons(self):
        self.canvas.delete("btn")
        self._draw_buttons()

    def _draw_data(self, x):
        c = self.canvas
        d = self.data.get("direction", {})
        pred = d.get("prediction", 0)
        conf = d.get("confidence", 0.5)
        rate = self.data.get("base_rate", 0)
        as_of = self.data.get("as_of", "")[:10]

        # 标题区
        c.create_oval(x, 22, x + 6, 28, fill=ACCENT, outline="")
        c.create_text(x + 12, 25, anchor="w", text="CNY / RUB", fill=TEXT_DIM,
                      font=(NUM_FONT, 9))

        if not self.data:
            c.create_text(x, self.H // 2, anchor="w", text="等待数据…",
                          fill=TEXT_DIM, font=(CN_FONT, 13))
            return

        color = UP_COLOR if pred == 1 else DOWN_COLOR
        arrow = "▲" if pred == 1 else "▼"
        word = "涨" if pred == 1 else "跌"
        pct = f"{conf * 100:.2f}%"
        rate_text = f"{rate:.2f} ₽/¥" if rate else ""

        c.create_text(x, 52, anchor="w", text=arrow, fill=color,
                      font=(NUM_FONT, 16, "bold"))
        c.create_text(x + 30, 58, anchor="w", text=word, fill=color,
                      font=(CN_FONT, 12, "bold"))
        c.create_text(x, 78, anchor="w", text=pct, fill=TEXT_HI,
                      font=(NUM_FONT, 15, "bold"))
        # 把握独立块: 与数字同字号 15、同行、字重一致, 右对齐固定在 x+118 列的右侧
        c.create_text(x + 118, 78, anchor="e", text="把握", fill=TEXT_HI,
                      font=(CN_FONT, 15, "bold"))
        if rate_text:
            c.create_text(x, 98, anchor="w", text=rate_text, fill=TEXT_MD,
                          font=(RATE_FONT, 12, "bold"))
        if as_of:
            c.create_text(x, 114, anchor="w", text=as_of, fill=TEXT_DIM,
                          font=(NUM_FONT, 8))

        # 置信度条
        bx1, bx2, by = x, x + 118, 130
        self._round_rect(c, bx1, by, bx2, by + 4, 2, fill=DIVIDER, outline="")
        ratio = max(0.0, min(1.0, conf))
        fillw = bx1 + max(4, int((bx2 - bx1) * ratio))
        self._round_rect(c, bx1, by, fillw, by + 4, 2, fill=color, outline="")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    FloatingPet().run()
