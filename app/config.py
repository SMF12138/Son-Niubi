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

# ---- 回测参数 ----
MIN_TRAIN = 300       # 窗口起点前至少需要的历史行数
TRAIN_WINDOW = 800    # 滚动训练窗口行数
REFIT_STRIDE = 10     # 每 N 个交易日评估一个窗口

# ---- Web ----
HOST = "127.0.0.1"
PORT = 8000
