"""启动冒烟测试: create_app + 关键 API 必须能正常响应。

防止 cli.serve 内部 import 漏写这类"py_compile 过但启动即崩"的问题
(v2.0.4 曾因漏写 from app.web.server import create_app 导致 UI 完全打不开)。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class ServeSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.web.server import create_app
        cls.app = create_app()
        cls.client = cls.app.test_client()

    def test_health_200(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertIn("db_last_date", r.get_json())

    def test_predict_200_and_has_refreshed_at(self):
        r = self.client.get("/api/predict?n=7")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("as_of", data)
        # refreshed_at 是"系统在刷新"的凭证, 缺失说明 forecast JSON 过旧
        self.assertIn("refreshed_at", data)

    def test_predict_all_horizons(self):
        from app import config
        for n in config.N_HORIZONS:
            r = self.client.get(f"/api/predict?n={n}")
            self.assertEqual(r.status_code, 200, f"horizon {n} 启动失败")


if __name__ == "__main__":
    unittest.main()
