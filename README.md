# Son Niubi — 人民币兑卢布方向 & 见顶日预测仪表盘

本地网页仪表盘：基于俄罗斯央行（CBR）官方日牌价 + 莫斯科交易所（MOEX）在岸成交价，预测 **1 人民币 = ? 卢布** 汇率的：

1. **未来涨跌方向**（7 / 30 / 60 / 90 交易日）—— 核心能力，经严格 walk-forward 回测验证；
2. **见顶日**（窗口内哪天达到最高点）—— 辅助展示，天花板较低（见「诚实边界」）。

页面如实展示真实回测命中率，并与基线并列对照，不虚标。**本项目不构成投资建议。**

> 仓库：https://github.com/SMF12138/Son-Niubi （private）

---

## 数据源（全部免密钥）

| 源 | 用途 | 说明 |
| --- | --- | --- |
| **CBR** `XML_dynamic` | 官方日牌价（主） | 币种 CNY（`R01375`）、USD（`R01235`，衍生特征）。首抓自 2010-01-01，之后增量。 |
| **MOEX ISS** | 在岸成交价（核心信号） | CNYRUB_TOM 日线，2022-06 起。**领先 CBR 次日牌价**，是方向预测的主信号。 |
| **er-api** | 兜底 | CBR 不可用时补最新一日，标 `erapi` 来源。 |
| Brent 油价 | 特征 | datasets/oil-prices（GitHub）。 |
| 新闻情绪 | 特征 | 关键词情绪打分（分辨率有限，见 KNOWN_ISSUES）。 |
| CBR 关键利率 | 特征 | 抓取不稳时回退硬编码校验值。 |

---

## 方向预测能力（真实回测 · MoexDirectionPredictor）

严格 walk-forward，滚动标准化只用 ≤t 数据，无前视。最新回测（`data/direction_result.json`，as_of 2026-09-09，4122 行）：

| Horizon | 全样本 | 高置信(>60%) | **MOEX 期** | 多数类基线 |
| --- | --- | --- | --- | --- |
| 7 日 | 56.0% | 62.9% | **67.6%** | 52.2% |
| 30 日 | 57.5% | 62.4% | 61.8% | 51.9% |
| 60 日 | 57.9% | 67.4% | 61.8% | 51.8% |
| 90 日 | **60.7%** | **72.4%** | 59.0% | 54.2% |

> **口径提醒**：头条数字（如 MOEX 期 67.6%）来自 2022-06 起有 MOEX 数据的子集；全样本超额约为基线 +4 个百分点。真实、可复现，但别只记大数字。

**核心信号**：`dev = log(MOEX市场价) − log(CBR官方价)`。市场价高于官方 → 官方价未来上追 → CNY/RUB 上涨。这是结构性套利偏离，非过拟合，短窗（1–7 日）最强符合机理。滚动标准化 zdev + 多重同向确认闸门（MOEX 动量 / 偏离加深 / 高波动 / 连续偏离 / 油价 / 新闻情绪）联合分档置信度，动态校准写回 `calibration.json`（48h 有效，过期回退默认）。

无 MOEX 数据的历史日期退回 `MeanRevDirectionPredictor`（线性合成均值回复）。

## 见顶日预测（EnsemblePeak · 辅助）

见顶日预测由 `app/backtest/engine.py` 的 walk-forward 集成实现（M0-常数 / M1-阻尼趋势 / M2-ETS / M3R-岭回归 / M3H-梯度提升 / ENS-集成），命中 = |p̂ − p*| ≤ tol，报告 ±0/±1/±2 三档 + 均匀随机基线。

> ⚠️ **当前状态**：见顶回测引擎（`engine.run_backtest` / `save_report`）**未接入生产链路**，`data/backtest_result.json` 不会自动生成，`/api/backtest` 端点会返回 503。前端仪表盘展示的价格路径由 `forecast.compute_forecast`（集成成员实时重训）生成，方向结论以 `direction` 字段为准。见 [DEVELOPMENT.md](DEVELOPMENT.md)。

---

## 快速开始

```powershell
.\scripts\setup.ps1    # 建 .venv、装依赖、下载 ECharts（仅首次）
.\scripts\run.ps1      # 增量抓取 → 重跑方向回测/校准/预测 → 启动 http://127.0.0.1:8000
```

分步执行（venv 的 python）：

```powershell
.\.venv\Scripts\python.exe -m app.cli fetch      # 增量抓取：CBR / Brent / 新闻 / 利率
.\.venv\Scripts\python.exe -m app.cli backtest   # 方向 walk-forward 回测（约 2s）→ direction_result.json
.\.venv\Scripts\python.exe -m app.cli calibrate  # MOEX z 分档置信度校准 → calibration.json
.\.venv\Scripts\python.exe -m app.cli forecast   # 生成当前 7/30/60/90 日预测 → forecast_*.json
.\.venv\Scripts\python.exe -m app.cli serve      # 启动仪表盘（自动补齐产物 + 内置每日调度）
```

> `serve` 内置每日自动更新调度器（默认莫斯科时间 09:00：fetch → backtest → calibrate → forecast），失败最多重试 3 次，产物写盘后 API 实时读取，无需重启。

单元测试：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`（项目根目录）。

---

## 页面内容

- KPI 卡：当前汇率与日涨跌、7/30 日预测见顶日期、方向命中率、数据截止日。
- 主图：历史实线 + 未来预测（集成中位虚线、成员 p10–p90 置信带）、见顶日竖线。
- 概率图：窗口内各交易日成为见顶日的概率（成员权重投票）。
- 方向表：全样本 / MOEX 期 / MOEX 高置信 命中率。

前端实际调用端点：`/api/predict`、`/api/signal_health`。其余 `/api/rates|forecast|backtest|direction|oil|meanrev` 已实现但未被当前前端使用（备用/调试）。

---

## 目录结构

```
app/config.py            配置（horizon / 回测参数 / 数据源代码）
app/cli.py               命令入口 fetch | backtest | calibrate | forecast | serve
app/scheduler.py         serve 内置每日自动更新（threading，无外部依赖）
app/data/fetcher.py      CBR 抓取 + er-api 兜底
app/data/moex_rates.py   MOEX 在岸价抓取/加载（核心信号源）
app/data/store.py        SQLite 缓存（data/rates.db）
app/data/features.py     因果特征（动量/波动/RSI/油价/情绪/利率…）
app/data/{cbr_rates,news,calendar}.py  利率 / 新闻情绪 / 交易日历
app/models/moex_dir.py   MoexDirectionPredictor（生产方向模型 + 动态校准）
app/models/meanrev_dir.py 线性合成均值回复（无 MOEX 时 fallback）
app/models/*             集成成员（见顶）+ 基线
app/backtest/engine.py   见顶 walk-forward 引擎（当前未接入生产，见 DEVELOPMENT.md）
app/forecast.py          当前时点预测（价格路径 + 方向）→ forecast_*.json
app/web/server.py + static/  Flask API + 前端仪表盘
tests/                   单测（CBR 解析 / 存储 / 日历 / 特征因果性 / 回测记账）
```

---

## 已知近似与口径说明

- **"交易日"**：回测历史窗口直接用 CBR 实际有牌价的日期序列，不推算；仅「当前预测」的未来日期标签需外推（周末 + 俄法定固定假日：1/1–1/8、2/23、3/8、5/1、5/9、6/12、11/4），调休不建模，个别日期可能偏差 1–2 天。
- 极个别 CBR 缺失交易日做前值填充并记日志（`carry_forward_days`）。
- 回测参数（见 `app/config.py`）：`MIN_TRAIN=300`、`TRAIN_WINDOW=800`、`REFIT_STRIDE=10`、`EWMA_DECAY=0.98`、`ENS_POWER=8`、`ENABLE_HGB=False`。

## 诚实边界

- 方向预测超越随机是**微弱但真实**的（全样本 +4 点，高置信/长窗更强）。见顶日精度天花板低，作辅助展示。
- 方向（direction）与价格路径（ensemble）是两个独立模型，可能不一致——这是设计，非 bug；对外结论以方向模型为准。
- 新闻情绪特征分辨率有限，覆盖稀疏（约 5% 交易日有数据）。详见 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)。
- **不构成投资建议。**

## 相关文档

- [DEVELOPMENT.md](DEVELOPMENT.md) — 开发路线图与待办优先级
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md) — 已知问题清单（原系统审计，含修复状态）
- [STRATEGY_FINDINGS.md](STRATEGY_FINDINGS.md) — 方向信号研发历程（历史记录）
