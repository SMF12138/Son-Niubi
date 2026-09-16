"""验证 _load_image 的加载策略: Pillow 首选 / Tk 原生垫底 / 缺失不崩。"""
import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import floating_pet as fp


class _Stub:
    """绕过 FloatingPet.__init__ 的桩实例。"""


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
        self.pet._load_image = fp.FloatingPet._load_image.__get__(self.pet)

    def test_pillow_primary_returns_correct_size(self):
        """Pillow 可用时首选它, 返回按目标尺寸缩放的图(不经过 Tk 解码器)。"""
        img = self.pet._load_image(fp.FORM1)
        self.assertIsNotNone(img)
        self.assertLessEqual(img.height(), fp.IMG_TARGET_H)

    def test_pillow_form2(self):
        img = self.pet._load_image(fp.FORM2)
        self.assertIsNotNone(img)
        self.assertLessEqual(img.width(), fp.IMG_TARGET_W)

    def test_falls_back_to_tk_when_pillow_missing(self):
        """Pillow 导入失败时退回 Tk 原生路径(用桩模拟成功)。"""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name.startswith("PIL"):
                raise ImportError("simulated no pillow")
            return real_import(name, *a, **kw)

        class FakePhoto:
            def __init__(self, file=None):
                self._w, self._h = 1254, 1254
            def width(self):
                return self._w
            def height(self):
                return self._h
            def subsample(self, n):
                return ("tk-subsampled", n)

        with mock.patch.object(builtins, "__import__", fake_import), \
             mock.patch.object(fp.tk, "PhotoImage", FakePhoto):
            img = self.pet._load_image(fp.FORM2)
        self.assertEqual(img[0], "tk-subsampled")

    def test_missing_file_returns_none(self):
        result = self.pet._load_image(Path("data/pet/__nope__.png"))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
