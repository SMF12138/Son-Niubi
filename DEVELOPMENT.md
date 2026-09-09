# DEVELOPMENT.md — 开发路线图

> 与代码同步日期：2026-09-10。当前状态见本文件；已知缺陷见 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)。

## 项目现状

方向预测（MoexDirectionPredictor）是**唯一生产模型**，准确率 56-68% 已确认为天花板。纯方向预测系统，无死代码。双层调度器（快层 60s + 慢层日级）已上线。

---

## 里程碑

- [x] CBR 官方牌价抓取 + SQLite 增量缓存
- [x] CBR 关键利率在线抓取（3255 行，替代 38 行硬编码）
- [x] MOEX 在岸偏离信号 — 方向能力 50% → 56-68%
- [x] 多重同向确认闸门 + 联合置信度分档
- [x] 动态校准（calibration.json，48h 有效）
- [x] 双层调度器（快层 60s 数据+预测 / 慢层日级回测+校准）
- [x] serve 启动时全链路同步
- [x] 数据管道修复（新闻去重 / 油价备用 / WAL / 连接管理）
- [x] 死代码清理（见顶引擎 + 5 成员模块 + 探针脚本，共 -1266 行）
- [x] Flask 仪表盘 + ECharts 可视化

---

## 待办

### P0

1. **校准非单调性调查**：N=7 的 z>1.0 档命中率低于 z>0.5 档，疑样本量不足。
2. **MeanRev base_conf 校准**：`base_conf` 从 0.73 起步，需对 `meanrev_strong` 子集独立统计。

### P1

3. **测试覆盖**：补 moex_dir / meanrev_dir / 校准的单测。
4. **features.slope20 性能**：rolling+polyfit → 向量化。
5. **前端未用端点收敛**：保留为调试 API 或删。

---

## 不做（已验证无增量）

- XGBoost 做方向 — 探针实测 53.3% 落后规则 56.0%
- 每日外部信号组合 — +0.3-0.5pp，不值得接入
- 情绪词典扩展 → 回测不变（覆盖率 5.3% 是瓶颈）
- 分钟级数据/预测 — 随机游走更严重

**方向准确率天花板 ≈ 全样本 56-61%；高置信/长窗 67-72%。已到顶。**

---

## 工作流

```powershell
# 改完代码
.\.venv\Scripts\python.exe -m unittest discover -s tests -v   # 先过测试
git add -A; git commit -m "..."; git push                      # 再提交
```
