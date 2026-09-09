"""桌面宠物浮窗：手绘角色 + 预测数据卡片。

tkinter 透明背景窗口，左侧 Canvas 手绘黄色独眼小萌物（idle/up/down），
右侧显示方向+置信度+汇率。每 60s 读 forecast_7.json 刷新。
"""
import json
import math
import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FORECAST_FILE = ROOT / "data" / "forecast_7.json"

# 配色
BG_DARK = "#1A1410"
BG_CARD = "#2A231A"
TEXT = "#E8DFD0"
TEXT_DIM = "#8A7E6E"
GOLD = "#D4A843"
UP_COLOR = "#E8615A"
DOWN_COLOR = "#5CB88A"
BODY_YELLOW = "#F5D547"
BODY_DARK = "#E8C83A"
BELLY_WHITE = "#FFF8E8"
EYE_GREEN = "#2D8B57"
EYE_BLACK = "#1A1210"
MOUTH_PINK = "#E85A6A"
ARM_YELLOW = "#EDCA3C"

# 动画帧率
FPS = 4
FRAME_MS = 1000 // FPS


class FloatingPet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CNY/RUB 桌宠")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)
        self.root.configure(bg=BG_DARK)

        # 窗口尺寸与位置（屏幕右下角）
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"320x160+{sw - 340}+{sh - 200}")

        # Canvas（透明背景绘制）
        self.canvas = tk.Canvas(self.root, width=320, height=160,
                                bg=BG_DARK, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # 状态
        self.mood = "idle"  # idle / up / down
        self.frame = 0
        self.data = {}
        self.last_mtime = 0
        self._drag_data = {"x": 0, "y": 0}

        # 绑定事件
        self.canvas.bind("<Button-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._do_drag)
        self.canvas.bind("<Double-Button-1>", self._open_browser)

        # 初始绘制
        self._draw()

        # 定时刷新
        self._refresh_data()
        self._animate()

    def _start_drag(self, e):
        self._drag_data["x"] = e.x
        self._drag_data["y"] = e.y

    def _do_drag(self, e):
        dx = e.x - self._drag_data["x"]
        dy = e.y - self._drag_data["y"]
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy
        self.root.geometry(f"+{x}+{y}")

    def _open_browser(self, e):
        import webbrowser
        webbrowser.open("http://127.0.0.1:8000")

    def _refresh_data(self):
        """读取 forecast JSON，更新数据和情绪状态。"""
        try:
            if FORECAST_FILE.exists():
                mtime = FORECAST_FILE.stat().st_mtime
                if mtime != self.last_mtime:
                    self.data = json.loads(FORECAST_FILE.read_text(encoding="utf-8"))
                    self.last_mtime = mtime
                    d = self.data.get("direction", {})
                    pred = d.get("prediction", 0)
                    self.mood = "up" if pred == 1 else "down"
        except Exception:
            pass
        self.root.after(5000, self._refresh_data)  # 5s 检查一次文件

    def _animate(self):
        """动画帧切换。"""
        self.frame = (self.frame + 1) % 4
        self._draw()
        self.root.after(FRAME_MS, self._animate)

    def _draw(self):
        """绘制全部内容。"""
        c = self.canvas
        c.delete("all")

        # 左侧：角色
        self._draw_character(70, 85)

        # 右侧：数据卡片
        self._draw_data_card(155, 10)

    def _draw_character(self, cx, cy):
        """在 (cx, cy) 为中心绘制角色。"""
        c = self.canvas
        mood = self.mood
        f = self.frame

        # 呼吸动画：身体轻微上下浮动
        breath = math.sin(f * math.pi / 2) * 2

        # 身体（黄色圆）
        body_r = 42
        c.create_oval(cx - body_r, cy - body_r + breath,
                       cx + body_r, cy + body_r + breath,
                       fill=BODY_YELLOW, outline=BODY_DARK, width=2)

        # 白肚皮（下方椭圆）
        belly_w, belly_h = 28, 22
        c.create_oval(cx - belly_w, cy + 5 + breath,
                       cx + belly_w, cy + 5 + belly_h + breath,
                       fill=BELLY_WHITE, outline="")

        # 手臂（右侧小圆）
        arm_x = cx + body_r - 8
        arm_y = cy + 5 + breath
        arm_r = 12
        c.create_oval(arm_x - arm_r, arm_y - arm_r,
                       arm_x + arm_r, arm_y + arm_r,
                       fill=ARM_YELLOW, outline=BODY_DARK, width=1)

        # 眼睛
        eye_x, eye_y = cx - 6, cy - 10 + breath
        eye_r = 14
        # 白底
        c.create_oval(eye_x - eye_r, eye_y - eye_r,
                       eye_x + eye_r, eye_y + eye_r,
                       fill="white", outline=BODY_DARK, width=1)
        # 绿虹膜
        iris_r = 10
        c.create_oval(eye_x - iris_r, eye_y - iris_r,
                       eye_x + iris_r, eye_y + iris_r,
                       fill=EYE_GREEN, outline="")
        # 黑瞳孔
        pupil_r = 5
        c.create_oval(eye_x - pupil_r, eye_y - pupil_r,
                       eye_x + pupil_r, eye_y + pupil_r,
                       fill=EYE_BLACK, outline="")
        # 高光
        hl_x, hl_y = eye_x - 3, eye_y - 4
        c.create_oval(hl_x - 2, hl_y - 2, hl_x + 2, hl_y + 2,
                       fill="white", outline="")

        # 嘴巴
        mouth_y = cy + 18 + breath
        if mood == "up":
            # 开心：张开的笑嘴
            c.create_arc(cx - 10, mouth_y - 6, cx + 10, mouth_y + 8,
                         start=200, extent=140, style="arc",
                         outline=MOUTH_PINK, width=2)
            c.create_oval(cx - 5, mouth_y, cx + 5, mouth_y + 6,
                          fill=MOUTH_PINK, outline="")
        elif mood == "down":
            # 难过：小扁嘴
            c.create_arc(cx - 8, mouth_y + 4, cx + 8, mouth_y - 2,
                         start=20, extent=140, style="arc",
                         outline=MOUTH_PINK, width=2)
        else:
            # idle：微笑弧
            c.create_arc(cx - 10, mouth_y - 8, cx + 10, mouth_y + 4,
                         start=200, extent=140, style="arc",
                         outline=MOUTH_PINK, width=2)

    def _draw_data_card(self, x, y):
        """右侧数据卡片。"""
        c = self.canvas
        w, h = 155, 140

        # 卡片背景
        c.create_rectangle(x, y, x + w, y + h,
                           fill=BG_CARD, outline="#3D342A", width=1)

        # 分割线
        c.create_line(x + 10, y + h - 28, x + w - 10, y + h - 28,
                       fill="#3D342A", width=1)

        d = self.data.get("direction", {})
        pred = d.get("prediction", 0)
        conf = d.get("confidence", 0.5)
        rate = self.data.get("base_rate", 0)
        signal = d.get("signal", "—")
        confirms = d.get("confirms", 0)

        color = UP_COLOR if pred == 1 else DOWN_COLOR
        arrow = "▲ 涨" if pred == 1 else "▼ 跌"
        conf_pct = f"{conf * 100:.0f}%"

        # 方向 + 置信度
        c.create_text(x + 12, y + 18, anchor="w",
                       text=arrow, fill=color,
                       font=("Microsoft YaHei", 16, "bold"))
        c.create_text(x + w - 12, y + 18, anchor="e",
                       text=conf_pct, fill=TEXT,
                       font=("Georgia", 14, "bold"))

        # 汇率
        if rate:
            c.create_text(x + 12, y + 52, anchor="w",
                           text=f"1元 = {rate:.2f}卢布", fill=TEXT_DIM,
                           font=("Microsoft YaHei", 11))

        # 信号
        sig_short = {"moex_dev": "MOEX偏离", "mean_rev": "均值回复",
                     "meanrev_strong": "均值回复强", "meanrev_medium": "均值回复"}.get(signal, signal)
        c.create_text(x + 12, y + 76, anchor="w",
                       text=f"信号: {sig_short}·{confirms}确认", fill=TEXT_DIM,
                       font=("Microsoft YaHei", 9))

        # 截至日期
        as_of = self.data.get("as_of", "")[:10]
        c.create_text(x + 12, y + h - 12, anchor="w",
                       text=f"截至: {as_of}", fill="#5E5448",
                       font=("Georgia", 8))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    pet = FloatingPet()
    pet.run()
