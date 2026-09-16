"""验证 _load_image 三级兜底: 原生 / TclError 走 Pillow / 缺失不崩。"""
import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import floating_pet as fp


class _Stub:
    """绕过 Tk 真实窗口的桩实例。"""


class ImageLoadFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # ImageTk.PhotoImage 需要一个真实 Tk root 上下文
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.pet = _Stub()
        # 绑定真实方法
        self.pet._load_image = fp.FloatingPet._load_image.__get__(self.pet)

    def test_pillow_path_when_tk_rejects_png(self):
        """模拟 Tk 8.5: tk.PhotoImage(file=) 抛 TclError, 应走 Pillow 返回图片。

        注意 ImageTk.PhotoImage 内部也会无参调用 tk.PhotoImage() 创建空图,
        所以只拦截带 file= 的"读文件"调用。"""
        real_photo = tk.PhotoImage

        def fake_photo(*a, **kw):
            if kw.get("file"):
                raise tk.TclError("couldn't recognize data in image file")
            return real_photo(*a, **kw)

        with mock.patch.object(fp.tk, "PhotoImage", fake_photo):
            img = self.pet._load_image(fp.FORM1)
        self.assertIsNotNone(img, "Pillow 兜底必须返回图片")
        self.assertLessEqual(img.height(), fp.IMG_TARGET_H + 2)

    def test_pillow_form2(self):
        real_photo = tk.PhotoImage

        def fake_photo(*a, **kw):
            if kw.get("file"):
                raise tk.TclError("png unsupported")
            return real_photo(*a, **kw)

        with mock.patch.object(fp.tk, "PhotoImage", fake_photo):
            img = self.pet._load_image(fp.FORM2)
        self.assertIsNotNone(img)
        self.assertLessEqual(img.width(), fp.IMG_TARGET_W + 2)

    def test_missing_file_returns_none(self):
        result = self.pet._load_image(Path("data/pet/__nope__.png"))
        self.assertIsNone(result)

    def test_native_path_when_tk_supports_png(self):
        """Tk 8.6 正常路径: 不依赖 Pillow 也能返回 PhotoImage 桩。"""
        class FakePhoto:
            def __init__(self, file):
                self._h, self._w = 1448, 1086
            def width(self):
                return self._w
            def height(self):
                return self._h
            def subsample(self, n):
                return ("subsampled", n)
        with mock.patch.object(fp.tk, "PhotoImage", FakePhoto):
            img = self.pet._load_image(fp.FORM1)
        self.assertEqual(img[0], "subsampled")


if __name__ == "__main__":
    unittest.main()
