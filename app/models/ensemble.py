"""集成择优:对成员模型做在线 EWMA 命中率跟踪,按命中率锐化后加权平均路径。

- 权重完全因果:第 i 个窗口用到的权重只来自 <i 的窗口表现(先预测、后更新)。
- 加权平均在"对数价格路径"上进行;输出成员路径矩阵供分位数带与逐日概率使用。
"""
import numpy as np

from app import config


def earliest_argmax(a: np.ndarray) -> int:
    """最早达到最大值的索引(并列取早,消除歧义)。"""
    return int(np.argmax(a))


class MemberTracker:
    """跟踪每个成员在回测中的 EWMA 命中率(tol=1),并给出软max权重。"""

    def __init__(self, names: list[str], decay: float = config.EWMA_DECAY):
        self.names = list(names)
        self.decay = decay
        self._h = {m: 0.5 for m in self.names}  # 初始 0.5,首个观测后按 EWMA 收敛
        self._seen = {m: False for m in self.names}

    def update(self, name: str, hit: bool) -> None:
        h = self._h[name]
        self._h[name] = h if not self._seen[name] else self.decay * h + (
            1 - self.decay
        ) * float(hit)
        self._seen[name] = True

    def weights(self) -> dict[str, float]:
        h = np.clip(np.array([self._h[m] for m in self.names]), 1e-3, 1.0)
        w = h ** config.ENS_POWER
        w = w / w.sum()
        return dict(zip(self.names, w.tolist()))

    def hit_rates(self) -> dict[str, float]:
        return {m: self._h[m] for m in self.names}


def weighted_path_quantiles(
    paths: dict[str, np.ndarray], weights: dict[str, float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """加权分位数(p10/p50/p90)。paths 须同长;各成员路径为列。"""
    names = [m for m in paths if m in weights]
    M = np.column_stack([paths[m] for m in names])
    w = np.array([weights[m] for m in names])
    order = np.argsort(M, axis=1)  # 每行(每日)排序
    Ms = np.take_along_axis(M, order, axis=1)
    Ws = np.take_along_axis(
        np.broadcast_to(w, M.shape), order, axis=1
    )
    cw = np.cumsum(Ws, axis=1)
    tot = cw[:, -1:]

    def q(p: float) -> np.ndarray:
        target = p * tot
        j = np.argmax(cw >= target, axis=1)
        return Ms[np.arange(M.shape[0]), j]

    return q(0.10), q(0.50), q(0.90)


def combine_paths(
    paths: dict[str, np.ndarray], weights: dict[str, float]
) -> np.ndarray:
    names = [m for m in paths if m in weights]
    total = sum(weights[m] for m in names)
    out = np.zeros_like(paths[names[0]], dtype=float)
    for m in names:
        out += weights[m] * paths[m]
    return out / total


def peak_probability_by_day(
    paths: dict[str, np.ndarray], weights: dict[str, float], N: int
) -> list[float]:
    """按成员权重投票:第 k 天成为(该成员)见顶日的概率。"""
    prob = np.zeros(N)
    for m, p in paths.items():
        if m not in weights:
            continue
        k = earliest_argmax(p)  # 0-based,对应第 k+1 个交易日
        prob[k] += weights[m]
    s = prob.sum()
    return (prob / s if s > 0 else prob).tolist()
