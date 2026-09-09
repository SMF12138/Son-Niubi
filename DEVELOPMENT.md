# DEVELOPMENT.md — 开发路线图

> 与代码同步日期：2026-09-10。本文件描述**当前状态和剩余待办**；已知缺陷清单见 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)。

## 项目现状（一句话）

方向预测（MoexDirectionPredictor）是**生产核心且已验证**，准确率 56-68% 已确认为天花板。见顶预测引擎（engine.py）**代码存在但从未接入生产**，属于死代码。双层调度器（快层 60s + 慢层日级）已上线，数据自动刷新。

---

## 里程碑

- [x] CBR 官方牌价抓取 + SQLite 增量缓存
- [x] CBR 关键利率在线抓取（3255 行，替代 38 行硬编码）
- [x] 见顶日 walk-forward 集成引擎（engine.py，6 成员 + 随机基线）
- [x] 线性合成均值回复方向预测（meanrev_dir，取代早期 XGBoost v4）
- [x] **MOEX 在岸偏离信号**（moex_dir）—— 方向能力从 ~50% 提升到全样本 56–61% / 高置信 63–72%
- [x] 多重同向确认闸门 + (|z|, 确认数) 联合置信度分档
- [x] 动态校准（calibrate_moex_z → calibration.json，48h 有效）
- [x] 双层调度器（快层 60s 数据+预测 / 慢层日级回测+校准）
- [x] serve 启动时全链路同步（不再跳过已有数据）
- [x] 数据管道修复：新闻去重 / 油价 Yahoo 备用 / WAL 模式 / 连接管理
- [x] 情绪评分升级：词频加权（非 set 去重）+ 词典扩展 100+ 词
- [x] 死代码清理：macro_dir.py / 未用 import / 死函数 / 孤立文件
- [x] Flask 仪表盘 + ECharts 可视化
- [x] Git 版本控制 + GitHub 私有仓库
- [ ] **见顶引擎接入决策**（见下方 P0）
- [ ] 测试覆盖补全

---

## 待办（按优先级）

### P0 — 影响正确性/一致性

1. **见顶引擎去留决策**（审计 C5）
   `engine.run_backtest` / `save_report` 不被 cli/scheduler 调用，`/api/backtest` 恒 503。二选一：
   - (a) 若产品保留见顶功能 → 在 `cmd_backtest` 或 scheduler 里调用 `engine.run_backtest` + `save_report`；
   - (b) 若只做方向 → 删除 engine.py 死代码、`/api/backtest` 端点，README 相应精简。
   **决策未定前，README 已如实标注该功能未接入。**

### P1 — 可维护性

2. **校准非单调性调查**（审计 H2）
   N=7 的 z>1.0 档命中率低于 z>0.5 档，疑样本量不足致方差大。
3. **MeanRev base_conf 校准**（审计 H5）
   `base_conf` 从 0.73 起步但均值回复整体仅 50–57%。需对 `meanrev_strong` 子集独立统计。
4. **测试覆盖**：15+ 模块仅 2 个测试文件 → 补 moex_dir / meanrev_dir / 校准的单测。
5. **前端未用端点收敛**：6/11 端点前端不调用 → 保留为调试 API 或删。
6. **monitor_signal.py 重复代码**：两函数 60% 重复 → 合并。
7. **features.slope20 性能**：rolling+polyfit → 向量化。

---

## 不做（已验证无增量，避免重复踩坑）

来自 STRATEGY_FINDINGS + 5 轮独立探针的实测结论，**不要再试**：

- 非线性 GBM/XGBoost 做方向 —— 探针实测 53.3% vs 规则 56.0%，落后 3pp。
- "加权融合" MOEX 与其他信号 —— 稀释纯 zdev（降到 63%）。正解是用确认信号做**闸门**，不混入方向。
- 引入每日外部信号（USD/RUB/Brent/利率/情绪组合）—— 探针实测 +0.3-0.5pp，不值得接入。
- 情绪词典扩展 → 回测数字不变（覆盖率 5.3% 是瓶颈，非词典大小）。
- 成交量特征 —— MOEX board 不提供。
- 拐点检测 —— 样本太少。
- 分钟级数据/预测 —— 随机游走更严重，准确率会降到 ~50%。

**方向准确率天花板 ≈ 天天出手全样本 56–61%；靠置信过滤/长窗可上探 67–72%。已到顶。**

---

## 工作流

```powershell
# 改完代码
.\.venv\Scripts\python.exe -m unittest discover -s tests -v   # 先过测试
git add -A; git commit -m "..."; git push                      # 再提交
```
