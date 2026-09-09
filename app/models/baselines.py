"""M0/M1 基线模型。统一接口 predict(ctx, N) -> 对数价格路径(N 个值)。"""
import numpy as np


def _last(ctx) -> float:
    return float(ctx["lp"][ctx["i"]])


class ConstantModel:
    """M0 常数/随机游走:路径平直,见顶日恒为窗口第 1 天(下限校准)。"""

    name = "M0-常数"

    def predict(self, ctx, N: int) -> np.ndarray:
        return np.full(N, _last(ctx))


class DampedTrendModel:
    """M1 阻尼趋势外推:对历史对数价格做 Holt 递推,外推时按 phi 阻尼。"""

    name = "M1-阻尼趋势"
    ALPHA = 0.35
    BETA = 0.15
    PHI = 0.97

    def predict(self, ctx, N: int) -> np.ndarray:
        y = ctx["lp"][: ctx["i"] + 1]
        n = len(y)
        if n < 3:
            return np.full(N, y[-1])
        lvl = float(y[0])
        trend = float(y[-1] - y[0]) / max(n - 1, 1)
        for t in range(1, n):
            prev = lvl
            lvl = self.ALPHA * y[t] + (1 - self.ALPHA) * (prev + trend)
            trend = self.BETA * (lvl - prev) + (1 - self.BETA) * self.PHI * trend
        out = np.empty(N)
        damp_sum = 0.0
        for k in range(N):
            damp_sum += self.PHI ** (k + 1)
            out[k] = lvl + trend * damp_sum
        return out
