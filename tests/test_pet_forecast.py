"""桌宠 forecast 读取: 新鲜文件正常读, 陈旧/缺失文件进入等待态(不显示冻结数字)。"""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import floating_pet as fp


class _Stub:
    horizon = 7
    last_mtime = 0
    data = {}


class ForecastStalenessTest(unittest.TestCase):
    def setUp(self):
        self.pet = _Stub()
        self.pet._horizon_file = fp.FloatingPet._horizon_file.__get__(self.pet)
        self.tmp = Path(__file__).resolve().parent / "_tmp_fc.json"
        self.patch = mock.patch.dict(fp.FORECAST_FILES, {7: self.tmp})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        if self.tmp.exists():
            self.tmp.unlink()

    def _write(self, ts):
        self.tmp.write_text(json.dumps({"as_of": "2026-09-18", "direction": {}}),
                            encoding="utf-8")
        import os
        os.utime(self.tmp, (ts, ts))

    def test_fresh_file_loads(self):
        self._write(time.time() - 30)
        self.pet._read_forecast = fp.FloatingPet._read_forecast.__get__(self.pet)
        self.pet._read_forecast()
        self.assertEqual(self.pet.data.get("as_of"), "2026-09-18")

    def test_stale_file_clears(self):
        self.pet.data = {"old": True}
        self._write(time.time() - fp.FORECAST_STALE_SEC - 60)
        self.pet._read_forecast = fp.FloatingPet._read_forecast.__get__(self.pet)
        self.pet._read_forecast()
        self.assertEqual(self.pet.data, {})

    def test_missing_file_clears(self):
        self.pet.data = {"old": True}
        self.pet._read_forecast = fp.FloatingPet._read_forecast.__get__(self.pet)
        self.pet._read_forecast()
        self.assertEqual(self.pet.data, {})


if __name__ == "__main__":
    unittest.main()
