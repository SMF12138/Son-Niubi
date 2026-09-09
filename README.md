# Son Niubi — 人民币兑卢布方向预测仪表盘

本地网页仪表盘：基于俄罗斯央行（CBR）官方日牌价 + 莫斯科交易所（MOEX）在岸成交价，预测 **1 人民币 = ? 卢布** 汇率的未来涨跌方向（7 / 30 / 60 / 90 交易日）。

页面如实展示真实回测命中率，并与基线并列对照，不虚标。**本项目不构成投资建议。**

> 仓库：https://github.com/SMF12138/Son-Niubi （private）

---

## 数据源（全部免密钥）

| 源 | 用途 | 说明 |
| --- | --- | --- |
| **CBR** `XML_dynamic` | 官方日牌价（主） | 币种 CNY（`R01375`）、USD（`R01235`，衍生特征）。首抓自 2010-01-01，之后增量。 |
| **CBR** `KeyRate` HTML | 关键利率 | 在线抓取 3255 行（2013-09→今），失败回退 38 行硬编码。 |
| **MOEX ISS** | 在岸成交价（核心信号） | CNYRUB_TOM 日线，2022-06 起。**领先 CBR 次日牌价**，是方向预测的主信号。 |
| **er-api** | 兜底 | CBR 不可用时补最新一日，标 `erapi` 来源。 |
| Brent 油价 | 特征 | GitHub datasets/oil-prices（主）+ Yahoo Finance BZ=F（备用）。 |
| 新闻情绪 | 特征 | Google News RSS 关键词情绪打分（扩展词频版，~100 词，分辨率有限）。 |

---

## 方向预测能力（真实回测 · MoexDirectionPredictor）

严格 walk-forward，滚动标准化只用 ≤t 数据，无前视。最新回测（`data/direction_result.json`，as_of 2026-09-10，4123 行）：

| Horizon | 全样本 | 高置信(>60%) | **MOEX 期** | 多数类基线 |
| --- | --- | --- | --- | --- |
| 7 日 | 56.0% | 62.8% | **67.5%** | 52.2% |
| 30 日 | 57.5% | 62.4% | 61.8% | 51.9% |
| 60 日 | 57.9% | 67.4% | 61.9% | 51.8% |
| 90 日 | **60.8%** | **72.4%** | 59.0% | 54.2% |

**按信号强度分档（校准表 = 实际历史命中率）：**

| | z>1.5（极强）| z>1.0 | z>0.5 | z≤0.5（弱）|
|---|---|---|---|---|
| N=7 | **79.2%** | 64.5% | 72.1% | 61.8% |
| N=30 | **74.8%** | 59.8% | 67.2% | 56.2% |
| N=60 | **76.0%** | 74.1% | 64.7% | 53.5% |
| N=90 | **76.3%** | 73.6% | 58.4% | 49.5% |

> **口径提醒**：头条数字（如 MOEX 期 67.5%）来自 2022-06 起有 MOEX 数据的子集；全样本超额约为基线 +4 个百分点。真实、可复现，但别只记大数字。

**核心信号**：`dev = log(MOEX市场价) − log(CBR官方价)`。市场价高于官方 → 官方价未来上追 → CNY/RUB 上涨。这是结构性套利偏离，非过拟合，短窗（1–7 日）最强符合机理。滚动标准化 zdev + 多重同向确认闸门（MOEX 动量 / 偏离加深 / 高波动 / 连续偏离 / 油价 / 新闻情绪）联合分档置信度，动态校准写回 `calibration.json`（48h 有效，过期回退默认）。

无 MOEX 数据的历史日期退回 `MeanRevDirectionPredictor`（线性合成均值回复）。

### 数据刷新机制

- **快层**（每 60 秒）：拉取 5 个数据源 + 重算预测（~3 秒）
- **慢层**（每天 09:00 MSK）：完整回测 + 校准 + 预测（~8 秒）
- **serve 启动时**：完整同步 + 回测 + 校准 + 预测

---

## 快速开始

```powershell
.\scripts\setup.ps1    # 建 .venv、装依赖、下载 ECharts（仅首次）
.\scripts\run.ps1      # 启动仪表盘（自动同步→回测→校准→预测→双层调度）
```

分步执行（venv 的 python）：

```powershell
.\.venv\Scripts\python.exe -m app.cli fetch      # 增量抓取：CBR / Brent / 新闻 / 利率
.\.venv\Scripts\python.exe -m app.cli backtest   # 方向 walk-forward 回测（约 3s）→ direction_result.json
.\.venv\Scripts\python.exe -m app.cli calibrate  # MOEX z 分档置信度校准 → calibration.json
.\.venv\Scripts\python.exe -m app.cli forecast   # 生成当前 7/30/60/90 日预测 → forecast_*.json
.\.venv\Scripts\python.exe -m app.cli serve      # 启动仪表盘（自动全链路 + 双层调度）
```

> `serve` 启动时同步全部数据源 + 回测 + 校准 + 预测。之后快层每 60 秒拉数据+刷新预测，慢层每天 09:00 MSK 重跑回测。

单元测试：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`（项目根目录）。

---

## 页面内容

- KPI 卡：当前汇率与日涨跌、方向命中率、信号来源、数据截止日。
- 主图：历史实线 + 未来预测（方向投影 + 喇叭口不确定性带）。
- 方向表：全样本 / MOEX 期 / MOEX 高置信 命中率。
- 健康卡：MOEX 信号滚动命中率监控。
- 不确定性说明：展开显示预测依据和风险提示。

前端实际调用端点：`/api/predict`、`/api/signal_health`。其余端点已实现但未被当前前端使用（备用/调试）。

---

## 目录结构

```
app/config.py            配置（horizon / 回测参数 / 数据源代码）
app/cli.py               命令入口 fetch | backtest | calibrate | forecast | serve
app/scheduler.py         双层调度器：快层(60s数据+预测) + 慢层(日级回测+校准)
app/data/fetcher.py      CBR 抓取 + er-api 兜底 + Yahoo 油价备用
app/data/moex_rates.py   MOEX 在岸价抓取/加载（核心信号源）
app/data/store.py        SQLite 缓存（data/rates.db, WAL 模式）
app/data/features.py     因果特征（动量/波动/RSI/油价/情绪/利率…）
app/data/{cbr_rates,news,calendar}.py  利率 / 新闻情绪 / 交易日历
app/models/moex_dir.py   MoexDirectionPredictor（生产方向模型 + 动态校准）
app/models/meanrev_dir.py 线性合成均值回复（无 MOEX 时 fallback）
app/models/mean_reversion.py 均值回复信号计算
app/forecast.py          方向投影生成 → forecast_*.json
app/monitor_signal.py    MOEX 信号健康监控
app/web/server.py + static/  Flask API + 前端仪表盘
tests/                   单测
```

---

## 已知近似与口径说明

- **"交易日"**：回测历史窗口直接用 CBR 实际有牌价的日期序列，不推算；仅「当前预测」的未来日期标签需外推（周末 + 俄法定固定假日：1/1–1/8、2/23、3/8、5/1、5/9、6/12、11/4），调休不建模，个别日期可能偏差 1–2 天。
- 极个别 CBR 缺失交易日做前值填充并记日志（`carry_forward_days`）。
- 回测参数（见 `app/config.py`）：`MIN_TRAIN=300`、`TRAIN_WINDOW=800`、`REFIT_STRIDE=10`。

## 诚实边界

- 方向预测超越随机是**微弱但真实**的（全样本 +4 点，高置信/长窗更强）。
- 新闻情绪特征覆盖率低（约 5% 交易日有数据），贡献有限但非零。
- **准确率天花板已确认**：5 轮独立验证（参数调优 / XGBoost / 每日信号 / 信号组合 / 系统审计）确认 56-68% 为当前数据源下的真实上限。
- **不构成投资建议。**

## 相关文档

- [DEVELOPMENT.md](DEVELOPMENT.md) — 开发路线图与待办优先级
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md) — 已知问题清单（系统审计，含修复状态）
- [STRATEGY_FINDINGS.md](STRATEGY_FINDINGS.md) — 方向信号研发历程（历史记录）
