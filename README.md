# Son NiuBi — 人民币兑卢布见顶日预测

本地网页仪表盘:预测 **1 人民币 = ? 卢布** 的官方日汇率,在未来 **7 / 30 个交易日**内**哪天达到最高点**,并如实展示历史回测命中率。

数据源:俄罗斯央行(CBR)官方日牌价,免密钥(`XML_dynamic`,币种 CNY,ID `R01375`),首次抓取自 2010-01-01,之后增量更新。若 CBR 网络不可用,会尝试 `open.er-api.com` 兜底补最新一日(标注 `erapi` 来源)。

## 准确率口径与目标

- 回测:walk-forward。对历史中每个评估起点 t,只用截至 t 的数据训练并预测 t+1 … t+N 窗口,取**预测的最高点所在交易日** p̂;实际最高点 p\* 取窗口内真实汇率最早达到最大值的那天。
- **命中 = |p̂ − p\*| ≤ 1 个交易日**。报告同时给出 ±0 / ±1 / ±2 三个容差的命中率。
- 目标:7 日 ≥ 90%、30 日 ≥ 50%。

> ⚠️ **如实声明**:金融市场的日频"见顶时机"可预测性有限,上述目标是**设计目标而非保证**。页面始终展示真实回测数字,并与「M0-常数(预测首日见顶)」和「均匀随机」两条基线并列对照,避免虚标。本项目不构成投资建议。

## 模型

| 成员 | 说明 |
| --- | --- |
| M0-常数 | 平直外推,见顶日 = 窗口第 1 天(下限校准) |
| M1-阻尼趋势 | 对数价格 EWMA/Holt 递推 + φ 阻尼趋势 |
| M2-ETS | statsmodels 阻尼趋势指数平滑 |
| M3R-直接岭回归 | 每个前瞻 k 一个岭回归,输入 60 日滚动统计特征 |
| M3H-梯度提升 | 把 k 作为输入特征、池化所有 (s,k) 样本的单模型 |
| ENS-集成 | 各成员按历史命中率 EWMA 加权平均路径(权重完全因果,先预测后更新) |

特征(全部只用 t 及之前信息):动量 5/20、波动 5/20、偏度 20、60 日区间位置、60 日 z 值、RSI14、周内日、隐含 CNY/USD 动量、USD/RUB 5 日收益。

## 快速开始

```powershell
.\scripts\setup.ps1    # 建 .venv、装依赖、下载 ECharts(仅首次)
.\scripts\run.ps1      # 自动:增量抓取 → 重建回测/预测(如过期)→ 启动 http://127.0.0.1:8000
```

分步执行:

```powershell
.\.venv\Scripts\python.exe -m app.cli fetch     # 增量抓取牌价
.\.venv\Scripts\python.exe -m app.cli backtest  # 全量 walk-forward 回测(约 1-3 分钟)
.\.venv\Scripts\python.exe -m app.cli forecast  # 生成当前 7/30 日预测
.\.venv\Scripts\python.exe -m app.cli serve     # 启动仪表盘(自动补齐缺失产物)
```

运行单元测试:`python -m unittest discover -s tests -v`(在项目根目录,使用 venv 的 python)。

## 页面内容

- KPI 卡:当前汇率与日涨跌、7/30 日预测见顶日期、回测命中率 @±1(与目标对照)、数据截止日。
- 主图:历史实线 + 未来 30 日预测(集成中位虚线、成员 p10–p90 置信带)、见顶日竖线与 7 日见顶星标。
- 概率图:30 个交易日内各日成为见顶日的概率(按成员权重投票)。
- 明细表:±0/±1/±2 容差命中率(含两条基线)、逐年命中率。

## 目录结构

```
app\config.py            配置(预测口径/回测参数/CBR 币种代码)
app\cli.py               命令入口 fetch|backtest|forecast|serve|all
app\data\fetcher.py      CBR 抓取 + er-api 兜底
app\data\store.py        SQLite 缓存(data\rates.db)
app\data\features.py     因果特征
app\models\*             模型池与集成择优
app\backtest\engine.py   walk-forward 回测引擎(输出 data\backtest_result.json)
app\forecast.py          当前时点预测(data\forecast_7.json / forecast_30.json)
app\web\server.py + static\   Flask 静态页 + JSON API
tests\                   单测(解析/存储/日历/特征/回测记账)
```

## 已知近似与口径说明

- **"交易日"**:回测中的历史窗口直接用 CBR 实际有牌价的日期序列,不依赖推算;只有"当前预测"的未来日期标签需要外推——按周末 + 俄法定固定假日(新年 1/1–1/8、2/23、3/8、5/1、5/9、6/12、11/4)推算,政府对节假日的调休不建模,个别日期可能偏差 1–2 天。
- 极个别 CBR 缺失的交易日做前一值填充并计入日志(`carry_forward_days`)。
- 回测参数(见 `app/config.py`):最短训练 500 行、滚动训练窗 750 行、每 5 个交易日评估并重训一次。
