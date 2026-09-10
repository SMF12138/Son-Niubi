"""桌面宠物浮窗：真实图片 + 交互 + 语音 + 最小化悬浮球。

- 纯黑背景，原图黑底直接嵌入
- 点击角色切换形态 + 播放语音
- — 最小化为圆球悬浮球，点击恢复
- ✕ 关闭桌宠 + 终止 Flask
- 🔊/🔇 静音
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

BG = "#000000"
TEXT_HI = "#FFFFFF"
TEXT_MD = "#D0C8B8"
TEXT_DIM = "#706858"
UP_COLOR = "#FF6B5E"
DOWN_COLOR = "#35D0A0"
IMG_TARGET_H = 130

W_FULL, H_FULL = 300, 165
W_MIN, H_MIN = 44, 44


class FloatingPet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CNY/RUB 桌宠")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.W = W_FULL
        self.H = H_FULL
        self.minimized = False
        self._save_pos = None
        self.root.geometry(
            f"{self.W}x{self.H}+{sw - self.W - 30}+{sh - self.H - 60}")

        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H,
                                bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.show_form1 = False
        self.mute = False
        self.data = {}
        self.last_mtime = 0
        self._drag_data = {"x": 0, "y": 0}
        self._img_refs = []

        self.img_form1 = None
        self.img_form2 = None
        self._load_images()

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._do_drag)

        self._draw()
        self._refresh_data()

    def _load_images(self):
        for attr, path in [("img_form1", FORM1), ("img_form2", FORM2)]:
            if path.exists():
                full = tk.PhotoImage(file=str(path))
                scale = max(1, full.height() // IMG_TARGET_H)
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

    def _on_click(self, e):
        if self.minimized:
            self._restore()
            return

        # ✕ 关闭
        if self.W - 22 < e.x < self.W - 2 and 2 < e.y < 22:
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
        if self.W - 42 < e.x < self.W - 22 and 2 < e.y < 22:
            self._minimize()
            return
        # 🔊/🔇 静音
        if self.W - 62 < e.x < self.W - 42 and 2 < e.y < 22:
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
        r = 20
        c.create_oval(cx - r, cy - r, cx + r, cy + r,
                       fill="#2A2A2A", outline="#5A4D3E", width=1)
        c.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, fill="white", outline="")
        c.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill="#2D8B57", outline="")
        c.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, fill="#1A1210", outline="")
        d = self.data.get("direction", {})
        color = UP_COLOR if d.get("prediction", 0) == 1 else DOWN_COLOR
        c.create_oval(cx + r - 5, cy - r + 2, cx + r + 1, cy - r + 8,
                       fill=color, outline="")

    def _draw_full(self):
        c = self.canvas

        # 角色图片
        img = self.img_form1 if self.show_form1 else self.img_form2
        if img:
            iw = img.width()
            c.create_image(5 + iw // 2, self.H // 2, image=img)

        # 分隔线
        c.create_line(150, 6, 150, self.H - 6, fill="#2A2A2A", width=1)

        # 数据（从 y=16 开始，每行留足间距）
        self._draw_data(162)

        # 按钮栏
        bx = self.W - 14
        c.create_text(bx, 12, text="✕", fill="#555",
                       font=("Microsoft YaHei", 12, "bold"))
        c.create_text(bx - 22, 12, text="—", fill="#555",
                       font=("Microsoft YaHei", 12, "bold"))
        c.create_text(bx - 44, 12, text="🔇" if self.mute else "🔊",
                       fill="#777", font=("Microsoft YaHei", 12))

    def _draw_data(self, x):
        c = self.canvas
        d = self.data.get("direction", {})
        pred = d.get("prediction", 0)
        conf = d.get("confidence", 0.5)
        rate = self.data.get("base_rate", 0)
        as_of = self.data.get("as_of", "")[:10]

        if not self.data:
            c.create_text(x, self.H // 2, anchor="w", text="等待数据…",
                          fill=TEXT_DIM, font=("Microsoft YaHei", 10))
            return

        color = UP_COLOR if pred == 1 else DOWN_COLOR
        arrow = "▲ 涨" if pred == 1 else "▼ 跌"
        pct = f"{conf * 100:.0f}%"
        rate_text = f"1元 = {rate:.2f} 卢布" if rate else ""

        # 4 行内容，均匀分布在可用高度内
        # 可用区域：从 y_start 到 self.H - 8
        top_pad = 8
        bot_pad = 8
        area_h = self.H - top_pad - bot_pad
        n_lines = 4
        line_gap = area_h // (n_lines + 1)  # 每行间距

        y = top_pad + line_gap  # 第1行中心

        # 1) 方向
        c.create_text(x, y, anchor="w", text=arrow, fill=color,
                       font=("Microsoft YaHei", 22, "bold"))
        y += line_gap

        # 2) 把握度
        c.create_text(x, y, anchor="w", text=f"{pct} 把握", fill=TEXT_HI,
                       font=("Microsoft YaHei", 14))
        y += line_gap

        # 3) 汇率
        if rate_text:
            c.create_text(x, y, anchor="w", text=rate_text, fill=TEXT_MD,
                           font=("Microsoft YaHei", 12))
        y += line_gap

        # 4) 日期
        c.create_text(x, y, anchor="w", text=as_of,
                       fill=TEXT_DIM, font=("Microsoft YaHei", 10))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    FloatingPet().run()
