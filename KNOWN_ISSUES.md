# KNOWN_ISSUES — 已知问题清单（原系统审计报告）
> 初次审计：2026-09-10 · 修复状态复核：2026-09-10
> 审计方法：4 个并行探针 + 手动代码审查，覆盖全部 .py 文件
> 每项标注当前状态：✅已修 / ❌未修 / ⚠️待决策。开发计划见 DEVELOPMENT.md。

---

## 一、总结

| 严重度 | 数量 | 说明 |
|--------|------|------|
| 🔴 CRITICAL | **5** | 必须修复，影响数据正确性或用户展示 |
| 🟠 HIGH | **5** | 应该修复，影响可靠性或性能 |
| 🟡 MEDIUM | **12** | 建议修复，影响可维护性 |
| ⚪ LOW | **15** | 可以清理，不影响功能 |

---

## 二、CRITICAL（必须修复）

### C1. ✅已修 — forecast.py rate_df 被静默丢弃
`save_forecasts()` 接收了 `rate_df` 参数但**没传给 `compute_forecast()`**。所有 4 个 horizon 的预测计算都没有关键利率特征。回测用的权重是在有 rate_df 时训练的，但预测时缺失了这个特征。
- **影响**：预测结果比回测弱，权重失配
- **修复**：forecast.py:103 加 `rate_df=rate_df`

### C2. ❌未修 — 新闻表无去重，无限增长
`upsert_news()` 没有 `ON CONFLICT` 策略，每次 `fetch_news()` 都追加 ~500 行。当前 23500 行只有 217 个唯一日期（平均每日期 ~108 条重复）。scheduler 每天跑一次就多 500 行。
- **影响**：DB 无限膨胀，查询变慢
- **修复**：加 UNIQUE(date, title, source) + INSERT OR IGNORE

### C3. ❌未修 — 情绪评分质量极差
`_score_text()` 用 `set(re.findall(...))` 去重词频，"sanctions sanctions sanctions" 和 "sanctions" 得分相同。DB 统计：54% 得分=0.0，36.2%=-1.0，8.9%=+1.0——实质是三值 {-1, 0, +1}，信号分辨率极低。
- **影响**：sentiment_7d 等特征 91% 的交易日为 0，对模型几乎是噪声
- **修复**：改用词频加权或预训练情绪模型

### C4. ⚠️已加说明 — 方向与价格投影可能不一致
> forecast.py 已加 `model_note` 声明这是两个独立模型、以 direction 为准的设计意图。前端投影是否改为随方向走仍待产品决策。原描述：
所有 4 个 horizon 的 forecast JSON 里：direction=prediction=0（看跌，68% 置信），但 ensemble_mid 价格路径全部上行（12.88→12.97/12.99/12.90/12.90）。前端 `/api/predict` 的投影曲线也是上行的。用户看到"看跌 68%"但图表画的是涨。
- **影响**：用户直接看到自相矛盾的预测
- **修复**：前端投影应以 direction 为准（跌则画下行路径），或明确标注"价格路径与方向模型独立"

### C5. ⚠️仍成立/待决策 — engine 见顶回测完全孤立
生产 pipeline（cli/scheduler）只调 `moex_dir.run_direction_backtest()`。`engine.py` 的 117 行 peak-day 回测 + save_report 永远不执行。`/api/backtest` 端点返回 503（因为 BACKTEST_JSON 从未被写入）。
- **影响**：见顶预测的回测引擎完全失效，API 端点不可用
- **修复**：二选一——要么删掉 engine.py 死代码，要么在 cli 里调用它

---

## 三、HIGH（应该修复）

### H1. ✅已修 — MacroDirectionPredictor 死代码已删除
> macro_dir.py 已删除，moex_dir.py 的 `_get_macro()` 已移除。原描述：
`moex_dir.py:80-84` 的 `_get_macro()` 懒加载 `MacroDirectionPredictor`，但 **`_get_macro()` 从未被调用**。macro_dir.py（103 行）完全不参与任何执行路径。
- **修复**：删除 macro_dir.py 和 moex_dir.py 里的 `_get_macro()`

### H2. 校准非单调性（N=7）
calibration.json：z>1.0 档准确率 64.5% **低于** z>0.5 档的 72.1%。高偏离反而不如中偏离准，说明校准表在该区间有噪声或分桶逻辑有坑。
- **修复**：检查样本量是否足够（z>1.0 档可能样本太少导致方差大），考虑平滑

### H3. ✅已修 — 调度器失败重试
> scheduler.py 已加 MAX_RETRIES=3 重试逻辑，成功后才置 last_run_date。原描述：
scheduler.py:107 `last_run_date = now.date()` 在 `_run_update()` **之前**设置。如果 09:00 的更新因网络超时失败，当天不会重试，要等到明天。
- **修复**：把 `last_run_date` 赋值移到 `_run_update()` 成功之后

### H4. moex_rates.py 连接泄漏风险
`_ensure_table()`、`_save()`、`load_moex()`、`load_moex_hl()` 全用手动 `con.close()` 而非 context manager。异常时连接泄漏。
- **修复**：改用 `with store.connect() as conn:` 模式

### H5. MeanRev base_conf 可能过度自信
meanrev_dir.py:57 `min(0.73 + strength * 0.02, 0.82)`——基线 73% 起步。但均值回复整体准确率只有 50-57%。"强信号"子集可能更高，但无独立校准数据验证。
- **修复**：对 meanrev_strong 子集单独统计准确率，校准 base_conf

---

## 四、MEDIUM（建议修复）

| # | 位置 | 问题 |
|---|------|------|
| M1 | server.py:23-73 | 6/11 个 API 端点未被前端调用（/api/rates,forecast,backtest,direction,oil,meanrev）|
| M2 | features.py:46-49 | `slope20` 用 `rolling().apply(polyfit)` 极慢，O(N×20) Python 调用 |
| M3 | forecast.py:87-96 vs cli.py:60-84 | save_forecasts 和 cmd_forecast 重复计算 lp/Fdf/valid/Xf |
| M4 | monitor_signal.py:24-70 vs 73-104 | evaluate() 和 daily_accuracy_series() 60% 代码重复 |
| M5 | store.py 无 WAL 模式 | 并发写+读可能 "database is locked" |
| M6 | features.py:104-108 | 情绪覆盖极稀疏：217/4122 天有数据，91% 行=0 |
| M7 | features.py:114-122 | key_rate 2010-2013 无数据，bfill 后 3 年常数 |
| M8 | server.py:156-157 | 投影用魔数 0.0161/0.0315/0.012/0.7，非动态计算 |
| M9 | server.py:82 | hist_prec 硬编码 {7:0.647,30:0.777,...}，应从校准表取 |
| M10 | server.py:297 | /api/health 每次请求都 HTTP 调 CBR，增加延迟 |
| M11 | 测试覆盖 | 仅 2 个测试文件覆盖 15+ 模块 |
| M12 | moex_dir.py:197-207 | streak 确认是滞后指标，趋势中膨胀置信 |

---

## 五、LOW（可清理）

| # | 位置 | 问题 |
|---|------|------|
| L1 | scripts/ | 3 个探针脚本（xgb_probe2/daily_signal/combine）可删 |
| L2 | data/app.db | 0 字节孤立文件，无引用 |
| L3 | data/tubiao.ico | 放错目录，应在 static/ |
| L4 | tmp_trace_out2.txt | 项目根目录临时文件 |
| L5 | serve_err.log/serve_out.log | 应移入 logs/ |
| L6 | moex_rates.py:11,15 | 未使用 import csv/datetime |
| L7 | monitor_signal.py:10 | 未使用 import csv |
| L8 | cbr_rates.py:13 | 未使用 from app import config |
| L9 | fetcher.py:137 | 未使用 import time as _time |
| L10 | macro_dir.py:14,16 | 未使用 import config / 未使用 log |
| L11 | meanrev_dir.py:88 | 未使用 import time |
| L12 | server.py:157 | 死代码 q25 变量（算了没用）|
| L13 | server.py:288 | 未使用 import sqlite3 |
| L14 | forecast.py:125 | 死函数 iso_to_date() |
| L15 | app.js:12 | 死状态 carIdx/carTimer |

---

## 六、准确率评估

| Horizon | 全量 | 高置信 | MOEX期 | 基线 | 校准ECE |
|---------|------|--------|--------|------|---------|
| N=7 | 56.0% | 62.9% | 67.6% | 52.2% | 4.9% |
| N=30 | 57.5% | 62.4% | 61.8% | 51.9% | — |
| N=60 | 57.9% | 67.4% | 61.8% | 51.8% | — |
| N=90 | **60.7%** | **72.4%** | 59.0% | 54.2% | — |

**诊断**：
- N=7 高置信 62.9% < MOEX 全量 67.6% → 置信过滤在 N=7 反而剔除了好预测
- N=90 高置信 72.4% 是系统最强指标，但仅覆盖 24.5% 窗口
- 校准 ECE=4.9% 良好，但 N=7 的 z>1.0 档非单调（见 H2）

---

## 七、修复优先级建议

**第一批（影响数据正确性，应立即修）**：
1. ~~C1 — forecast.py 加 `rate_df=rate_df`~~ ✅已修
2. C2 — upsert_news 加去重（改 store.py/news.py）—— ❌未修
3. C3 — 情绪评分改用词频（改 news.py）—— ❌未修
4. C4 — 前端投影与方向对齐（改 server.py）—— ⚠️待产品决策（已加说明）
5. ~~H3 — 调度器重试逻辑~~ ✅已修

**第二批（影响可靠性，本周修）**：
6. ~~H1 — 删除 macro_dir.py 死代码~~ ✅已修
7. H4 — moex_rates.py 改 context manager —— ❌未修
8. H5 — MeanRev base_conf 校准 —— ❌未修
9. H2 — 校准非单调性调查 —— ❌未修

**第三批（可维护性，有空时清理）**：
10. M1-M12 中各项
11. L1-L15 清理
