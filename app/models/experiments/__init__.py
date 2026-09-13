"""30 日 ML 实验 —— 实验模块（与生产严格隔离）。

本包内一切代码/产物仅供 Phase 2 实验使用：
- 不被 app.cli / app.forecast / app.web 等生产链路 import；
- 产物只写本包 artifacts/，不写 data/ 下任何生产 JSON；
- 前端零改动。
"""
