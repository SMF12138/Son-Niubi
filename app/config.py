"""全局配置:路径、数据源、回测参数。"""
from pathlib import Path

# ---- 路径 ----
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "rates.db"
FORECAST_7_JSON = DATA_DIR / "forecast_7.json"
FORECAST_30_JSON = DATA_DIR / "forecast_30.json"
FORECAST_60_JSON = DATA_DIR / "forecast_60.json"
FORECAST_90_JSON = DATA_DIR / "forecast_90.json"
DIRECTION_JSON = DATA_DIR / "direction_result.json"
MOEX_LIVE_JSON = DATA_DIR / "moex_live.json"
FORECAST_JSONS = {7: FORECAST_7_JSON, 30: FORECAST_30_JSON,
                  60: FORECAST_60_JSON, 90: FORECAST_90_JSON}
STATIC_DIR = ROOT / "app" / "web" / "static"

# ---- CBR 数据源 ----
CBR_DYNAMIC_URL = "https://www.cbr.ru/scripts/XML_dynamic.asp"
CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
ER_API_LATEST_URL = "https://open.er-api.com/v6/latest/CNY"
BRENT_CSV_URL = "https://raw.githubusercontent.com/datasets/oil-prices/master/data/brent-daily.csv"
CODE_CNY = "R01375"  # 1 人民币 = X 卢布
CODE_USD = "R01235"  # 1 美元 = X 卢布(衍生特征用)
DATA_START = "2010-01-01"
HTTP_TIMEOUT = 30

# ---- 预测口径(交易日) ----
N_HORIZONS = [7, 30, 60, 90]

# ---- 信号参数 ----
# MOEX 偏离滚动标准化窗口。生产模型(moex_dir)与健康监控(monitor_signal)必须一致,
# 否则健康度卡片测的是另一个信号。改这里两处同时生效。
# 150 为实测选值:优于 100/200 窗。
ZDEV_WINDOW = 150

# ---- 回测参数 ----
MIN_TRAIN = 300       # 窗口起点前至少需要的历史行数
# "高置信"门槛。旧值 0.60 对 MOEX 信号几乎无筛选力(z00 弱信号桶 N=7 校准值
# 0.6176, 所有 MOEX 日都算高置信); 0.65 才能真正筛掉 |z|<=0.5 的弱信号。
CONFIDENT_THRESHOLD = 0.65

# ---- Web ----
HOST = "127.0.0.1"
PORT = 8000
