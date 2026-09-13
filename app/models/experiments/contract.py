"""30 日方向实验 —— 标签口径契约（预注册，冻结）。

冻结历史
--------
2026-09-13 用户拍板冻结：
- 标签窗口固定为未来 30 个 CBR 牌价日（交易日）；
- FLAT 阈值直接预注册 ±1%，**不先看分布再调**；
- 二分类与三分类同时做对照；
- 即使事后发现 ±1% 不理想，也不得为结果好看而回改；
- 标签诊断只回答"标签有没有可预测性"，不用来挑阈值、不调样本/类别权重。

本模块是所有实验脚本标签定义的**单点真源**。它只含常量与纯函数，
不含任何可被诊断结果回写的参数，也不含模型超参、样本权重、类别权重。
若未来启用新口径，必须新增 CONTRACT_VERSION 而非修改本行。
"""

# === 冻结常量（禁止根据实验结果修改） ===
CONTRACT_VERSION = "2026-09-13-v1"
HORIZON_DAYS = 30                 # 30 个 CBR 实际有牌价的日期（与生产回测"交易日"口径一致）
FLAT_HALF_WIDTH = 0.01            # 三分类 FLAT 半宽：|R30| <= 1% 为震荡（预注册 ±1%）
LABEL_PRICE_COLUMN = "cny_rub"    # 标签只基于 CBR 官方人民币兑卢布牌价

# 三分类标签编码
LABEL_UP = 1
LABEL_FLAT = 0
LABEL_DOWN = -1

# 二分类标签编码（无弃权：FLAT 样本按 R30 符号强制归边，与现行生产 log 符号口径等价）
BIN_UP = 1
BIN_DOWN = 0


def r30_simple_return(p_t: float, p_t30: float) -> float:
    """R30 = (P(t+30) - P(t)) / P(t)。简单收益（场外指导口径），非对数收益。"""
    return (p_t30 - p_t) / p_t


def ternary_label(r30: float) -> int:
    """三分类：R30 > +1% → UP(1)；R30 < -1% → DOWN(-1)；其余 FLAT(0)。

    边界归属：恰好 +1%/-1% 归 FLAT（严格大于/小于才出 FLAT）。
    """
    if r30 > FLAT_HALF_WIDTH:
        return LABEL_UP
    if r30 < -FLAT_HALF_WIDTH:
        return LABEL_DOWN
    return LABEL_FLAT


def binary_label(r30: float) -> int:
    """二分类（无弃权）：R30 > 0 → UP(1)，否则 DOWN(0)。

    与现行生产方向口径（logP(t+30) > logP(t) 为涨）在符号上完全等价。
    """
    return BIN_UP if r30 > 0 else BIN_DOWN
