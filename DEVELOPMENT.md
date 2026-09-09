# DEVELOPMENT.md — 开发路线图

> 与代码同步日期：2026-09-10。本文件描述**下一步该做什么**；已知缺陷清单见 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)。

## 项目现状（一句话）

方向预测（MoexDirectionPredictor）是**生产核心且已验证**；见顶预测引擎（engine.py）**已实现但未接入**生产链路，处于"死代码 / 待决策"状态。仪表盘、每日自动调度、动态校准均可运行。

---

## 里程碑

- [x] CBR 官方牌价抓取 + SQLite 增量缓存
- [x] 见顶日 walk-forward 集成引擎（engine.py，6 成员 + 随机基线）
- [x] 线性合成均值回复方向预测（meanrev_dir，取代早期 XGBoost v4）
- [x] **MOEX 在岸偏离信号**（moex_dir）—— 方向能力从 ~50% 提升到全样本 56–61% / 高置信 63–72%
- [x] 多重同向确认闸门 + (|z|, 确认数) 联合置信度分档
- [x] 动态校准（calibrate_moex_z → calibration.json，48h 有效）
- [x] serve 内置每日自动更新调度器（09:00 MSK，失败重试 3 次）
- [x] Flask 仪表盘 + ECharts 可视化
- [x] Git 版本控制 + GitHub 私有仓库
- [ ] **见顶引擎接入决策**（见下方 P0）
- [ ] 新闻情绪特征质量提升
- [ ] 数据层健壮性（去重 / WAL / 连接管理）

---

## 待办（按优先级）

### P0 — 影响正确性/一致性，应尽快处理

1. **见顶引擎去留决策**（审计 C5）
   `engine.run_backtest` / `save_report` 不被 cli/scheduler 调用，`backtest_result.json` 从不生成，`/api/backtest` 恒 503。二选一：
   - (a) 若产品保留见顶功能 → 在 `cmd_backtest` 或 scheduler 里调用 `engine.run_backtest` + `save_report`（注意耗时，`ENABLE_HGB` 保持关闭）；
   - (b) 若只做方向 → 删除 engine.py / forecast 里的见顶路径 / `/api/backtest` 端点，README 相应精简。
   **决策未定前，README 已如实标注该功能未接入。**

2. **新闻表去重**（审计 C2，**仍未修**）
   `upsert_news()` 无 `ON CONFLICT`，每次 fetch 追加 ~500 行，DB 无限膨胀。
   → 加 `UNIQUE(date, title, source)` + `INSERT OR IGNORE`，并清理历史重复行。

### P1 — 影响可靠性/信号质量

3. **情绪评分质量**（审计 C3）
   `_score_text()` 用 `set()` 去重致词频丢失，实质三值 {-1,0,+1}，91% 交易日为 0。
   → 改词频加权或轻量预训练情绪模型；或若增量不显著，考虑降权/移除该特征。

4. **校准非单调性调查**（审计 H2）
   N=7 的 z>1.0 档命中率低于 z>0.5 档，疑样本量不足致方差大。
   → 检查各档样本量，考虑 Platt/等距平滑。

5. **数据层健壮性**（审计 H4/M5）
   moex_rates.py 手动 `con.close()` 有泄漏风险；store 无 WAL。
   → 统一 `with store.connect()`；开启 `PRAGMA journal_mode=WAL`。

6. **MeanRev 置信度校准**（审计 H5）
   `base_conf` 从 0.73 起步但均值回复整体仅 50–57%。
   → 对 `meanrev_strong` 子集独立统计并校准。

### P2 — 可维护性 / 清理

7. 前端未用端点收敛（server.py 6/11 端点前端不调用 → 保留为调试 API 或删）。
8. `features.slope20` 用 rolling+polyfit 慢 → 向量化。
9. save_forecasts 与 cmd_forecast 重复计算 lp/Fdf/valid → 抽公共函数。
10. monitor_signal.py 两函数 60% 重复 → 合并。
11. 死代码/未用 import 清理（详见 KNOWN_ISSUES 的 LOW 表）。
12. 测试覆盖：15+ 模块仅 2 个测试文件 → 补 moex_dir / meanrev_dir / 校准的单测。

---

## 不做（已验证无增量，避免重复踩坑）

来自 STRATEGY_FINDINGS 的实测结论，**不要再试**：

- 非线性 GBM 做方向 —— 比随机差（把线性均值回复信号过拟合丢失）。
- "加权融合" MOEX 与其他信号 —— 稀释纯 zdev（降到 63%）。正解是用确认信号做**闸门**，不混入方向。
- 三角分解（USD/RUB 动量）、直接加权 —— 无增量。
- 成交量特征 —— MOEX board 不提供。
- 拐点检测 —— 样本太少。

方向准确率天花板 ≈ 天天出手全样本 56–61%；靠置信过滤/长窗可上探 67–72%。

---

## 工作流

```powershell
# 改完代码
.\.venv\Scripts\python.exe -m unittest discover -s tests -v   # 先过测试
git add -A; git commit -m "..."; git push                      # 再提交
```
