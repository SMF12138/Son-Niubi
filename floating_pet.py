"""桌面宠物浮窗：真实图片 + 交互 + 语音。

- 纯黑背景，原图黑底直接嵌入
- 点击角色切换 形态一/形态二
- 切到形态一播放语音（右上静音可关）
- 右上 ✕ 关闭桌宠
- 每 5s 读 forecast_7.json 刷新数据
"""
import json
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FORECAST_FILE = ROOT / "data" / "forecast_7.json"
FORM1 = ROOT / "data" / "pet" / "form1.png"  # 侧面穿T恤
FORM2 = ROOT / "data" / "pet" / "form2.png"  # 正面无衣
VOICE1 = ROOT / "data" / "pet" / "voice1.wav"  # 形态一语音
VOICE2 = ROOT / "data" / "pet" / "voice2.wav"  # 形态二语音

BG = "#000000"
TEXT_HI = "#FFFFFF"
TEXT_MD = "#D8D0C0"
TEXT_DIM = "#8A8478"
UP_COLOR = "#FF6B5E"
DOWN_COLOR = "#35D0A0"
IMG_TARGET_H = 150


class FloatingPet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CNY/RUB 桌宠")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.W = 330
        self.H = 190
        self.root.geometry(f"{self.W}x{self.H}+{sw - self.W - 30}+{sh - self.H - 60}")

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
        self.canvas.bind("<Double-Button-1>", lambda e: None)  # 阻止双击穿透

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

    def _on_click(self, e):
        # ✕ 关闭
        if self.W - 26 < e.x < self.W - 4 and 4 < e.y < 26:
            self.root.destroy()
            return
        # 🔊/🔇 静音
        if self.W - 52 < e.x < self.W - 30 and 4 < e.y < 26:
            self.mute = not self.mute
            self._draw()
            return
        # 左侧角色区 → 切换形态 + 播放语音 + 弹出网页
        if e.x < 170:
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
        self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")

    def _play_voice(self):
        """播放语音：winsound 直接播 wav（零窗口、毫秒级）。"""
        voice = VOICE1 if self.show_form1 else VOICE2
        if not voice.exists():
            return
        import winsound
        try:
            winsound.PlaySound(str(voice), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

    def _refresh_data(self):
        try:
            if FORECAST_FILE.exists():
                mtime = FORECAST_FILE.stat().st_mtime
                if mtime != self.last_mtime:
                    self.data = json.loads(FORECAST_FILE.read_text(encoding="utf-8"))
                    self.last_mtime = mtime
        except Exception:
            pass
        self.root.after(5000, self._refresh_data)
        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete("all")

        img = self.img_form1 if self.show_form1 else self.img_form2
        if img:
            iw, ih = img.width(), img.height()
            c.create_image(5 + iw // 2, self.H // 2, image=img)

        c.create_line(172, 8, 172, self.H - 8, fill="#2A2A2A", width=1)
        self._draw_data(184, 20)

        c.create_text(self.W - 15, 15, text="✕", fill="#666",
                       font=("Arial", 13, "bold"))
        c.create_text(self.W - 41, 15, text="🔇" if self.mute else "🔊",
                       fill="#888", font=("Arial", 11))

        label = "形态一·穿T恤" if self.show_form1 else "形态二·正面"
        c.create_text(10, self.H - 6, anchor="sw", text=label,
                       fill="#3A3A3A", font=("Microsoft YaHei", 8))

    def _draw_data(self, x, y):
        c = self.canvas
        d = self.data.get("direction", {})
        pred = d.get("prediction", 0)
        conf = d.get("confidence", 0.5)
        rate = self.data.get("base_rate", 0)
        signal = d.get("signal", "—")
        confirms = d.get("confirms", 0)
        color = UP_COLOR if pred == 1 else DOWN_COLOR
        arrow = "▲ 涨" if pred == 1 else "▼ 跌"
        pct = f"{conf * 100:.0f}%"
        as_of = self.data.get("as_of", "")[:10]

        if not self.data:
            c.create_text(x, y + 40, anchor="w", text="等待数据…",
                          fill=TEXT_DIM, font=("Microsoft YaHei", 11))
            return

        c.create_text(x, y, anchor="w", text=arrow, fill=color,
                       font=("Microsoft YaHei", 26, "bold"))
        c.create_text(x, y + 38, anchor="w", text=f"{pct} 把握", fill=TEXT_HI,
                       font=("Microsoft YaHei", 15, "bold"))
        if rate:
            c.create_text(x, y + 66, anchor="w",
                           text=f"1元 = {rate:.2f} 卢布", fill=TEXT_MD,
                           font=("Microsoft YaHei", 12))
        sig = {"moex_dev": "MOEX偏离", "mean_rev": "均值回复",
               "meanrev_strong": "均值回复强"}.get(signal, signal)
        c.create_text(x, y + 92, anchor="w",
                       text=f"{sig}·{confirms}确认", fill=TEXT_DIM,
                       font=("Microsoft YaHei", 9))
        c.create_text(x, y + 114, anchor="w", text=f"截至 {as_of}",
                       fill="#4A4A4A", font=("Microsoft YaHei", 9))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    FloatingPet().run()
