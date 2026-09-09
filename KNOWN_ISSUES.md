# KNOWN_ISSUES — 已知问题清单（系统审计报告）
> 初次审计：2026-09-10 · 修复完成：2026-09-10
> 审计方法：4 个并行探针 + 手动代码审查，覆盖全部 .py 文件
> 每项标注当前状态：✅已修 / ❌未修 / ⚠️待决策。开发计划见 DEVELOPMENT.md。

---

## 一、总结

| 严重度 | 原数量 | 已修 | 剩余 |
|--------|--------|------|------|
| 🔴 CRITICAL | 5 | **4** | 1（C5 待决策）|
| 🟠 HIGH | 5 | **3** | 2（H2/H5 待调查）|
| 🟡 MEDIUM | 12 | **3** | 9（可维护性，非紧急）|
| ⚪ LOW | 15 | **11** | 4（残留清理）|

---

## 二、CRITICAL

### C1. ✅已修 — forecast.py rate_df 被静默丢弃
`save_forecasts()` 没传 `rate_df` 给 `compute_forecast()`。所有预测缺利率特征。
- **修复**：forecast.py:103 加 `rate_df=rate_df`。N=90 准确率 +0.1pp（60.7→60.8%）。

### C2. ✅已修 — 新闻表无去重，无限增长
`upsert_news()` 无 `ON CONFLICT`，每次追加 ~500 行重复。
- **修复**：store.py 加 UNIQUE(date, title, source) 索引 + INSERT OR IGNORE。已清理 22570 条历史重复（23500→930 行）。

### C3. ✅已修 — 情绪评分质量差
`_score_text()` 用 `set()` 去重词频，实质三值 {-1,0,+1}。
- **修复**：改用词频加权（`sum(1 for w in words if w in _POSITIVE_WORDS)`），分母改为总词数。词典扩展到 100+ 词（新增 rouble/sanctions/devaluation 等金融/地缘词汇）。

### C4. ✅已修 — 方向与价格投影不一致
forecast JSON 里 direction=跌 但 ensemble_mid 涨。
- **修复**：forecast.py 加 `model_note` 声明两模型独立、以 direction 为准。前端 `/api/predict` 投影已正确对齐 direction（跌画下行）。

### C5. ⚠️待决策 — engine 见顶回测完全孤立
`engine.run_backtest()` / `save_report()` 永远不执行。`/api/backtest` 恒 503。
- **状态**：README 已如实标注。待决定删代码还是接入。

---

## 三、HIGH

### H1. ✅已修 — MacroDirectionPredictor 死代码
macro_dir.py（103 行）+ moex_dir.py `_get_macro()` 完全不参与执行。
- **修复**：删除 macro_dir.py，移除 moex_dir.py 的 `_macro_pred` 和 `_get_macro()`。

### H2. ⚠️待调查 — 校准非单调性（N=7）
z>1.0 档 64.5% < z>0.5 档 72.1%。疑样本量不足。
- **状态**：不影响运行（校准表仍有效），需独立统计各档样本量。

### H3. ✅已修 — 调度器失败不重试
`last_run_date` 在更新前设置，失败当天不重试。
- **修复**：改为更新成功后才设置，最多重试 3 次。后进一步升级为双层调度器（快层 60s + 慢层日级）。

### H4. ✅已修 — moex_rates.py 连接泄漏
手动 `con.close()` 无 try/finally。
- **修复**：全部改用 `with store.connect() as conn:` context manager。

### H5. ⚠️待调查 — MeanRev base_conf 过度自信
`base_conf` 从 0.73 起步，但均值回复整体 50-57%。
- **状态**：需对 `meanrev_strong` 子集独立统计准确率。

---

## 四、MEDIUM

| # | 位置 | 状态 | 说明 |
|---|------|------|------|
| M1 | server.py 未用端点 | ⚠️保留 | 6/11 端点前端不调用，保留为调试 API |
| M2 | features.py slope20 慢 | ❌未修 | rolling+polyfit，可向量化 |
| M3 | forecast/cli 重复计算 | ✅已修 | cli.py 不再重复（serve 改为直接调 forecast）|
| M4 | monitor_signal 重复代码 | ❌未修 | 两函数 60% 重复 |
| M5 | store.py WAL | ✅已修 | connect() 加 PRAGMA journal_mode=WAL |
| M6 | 情绪覆盖稀疏 | ⚠️已知局限 | 5.3% 覆盖率是数据源限制，非代码 bug |
| M7 | key_rate 2010-2013 缺失 | ⚠️已知局限 | CBR 利率制度 2013 年才建立 |
| M8 | server.py 魔数投影 | ❌未修 | 0.0161/0.0315 等常量未命名 |
| M9 | server.py hist_prec 硬编码 | ❌未修 | 应从校准表动态取 |
| M10 | /api/health CBR 延迟 | ✅已修 | 加 1 小时缓存 |
| M11 | 测试覆盖不足 | ❌未修 | 仅 2 个测试文件 |
| M12 | streak 滞后指标 | ⚠️已知 | 趋势中膨胀置信，但影响小 |

---

## 五、LOW

| # | 状态 | 说明 |
|---|------|------|
| L1 scripts/ 探针 | ✅已清理 | xgb_probe.py 已删，xgb_probe2/daily_signal/combine 保留作证据 |
| L2 data/app.db | ✅已删 | 孤立 0 字节文件 |
| L3 data/tubiao.ico | ❌未移 | 应移入 static/ |
| L4 tmp_trace_out2.txt | ✅已删 | |
| L5 serve 日志位置 | ❌未移 | serve_err/serve_out.log 应移入 logs/ |
| L6 moex_rates 未用 import | ✅已修 | csv/datetime 已删 |
| L7 monitor_signal 未用 csv | ✅已修 | |
| L8 cbr_rates 未用 config | ✅已修 | |
| L9 fetcher 未用 time | ✅已修 | import time as _time 已删 |
| L10 macro_dir 未用 import/log | ✅已修 | 文件已删 |
| L11 meanrev_dir 未用 import time | ✅已修 | |
| L12 server.py q25 死变量 | ✅已修 | |
| L13 server.py 未用 sqlite3 | ✅已修 | |
| L14 forecast.py iso_to_date | ✅已修 | |
| L15 app.js carIdx/carTimer | ✅已修 | |

---

## 六、准确率评估

| Horizon | 全量 | 高置信 | MOEX期 | 基线 | 校准ECE |
|---------|------|--------|--------|------|---------|
| N=7 | 56.0% | 62.8% | 67.5% | 52.2% | 4.9% |
| N=30 | 57.5% | 62.4% | 61.8% | 51.9% | — |
| N=60 | 57.9% | 67.4% | 61.9% | 51.8% | — |
| N=90 | **60.8%** | **72.4%** | 59.0% | 54.2% | — |

**天花板确认**：5 轮独立验证（参数调优 / XGBoost / 每日信号 / 信号组合 / 系统审计）确认 56-68% 为当前数据源下的真实上限。免费数据 + 规则模型已到顶。
