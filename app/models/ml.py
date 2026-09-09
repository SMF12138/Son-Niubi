"""M3 直接多步机器学习成员。

目标:对每个前瞻 k ∈ [1..N],预测第 k 个交易日相对今天的累计对数收益
Δ_k = lp[t+k] - lp[t];预测对数价格路径 = lp[t] + 累计/直接预测。
窗口内见顶日 = 对数路径(也即价格路径)最早 argmax。

- M3R: 每个 k 一个岭回归(Ridge),轻量。
- M3H: 池化所有 (s,k) 样本、把 k 作为输入特征,单个梯度提升机输出 Δ_k。
两者都只用 t 之前的历史做训练,严格避免前视。
"""
import logging

import numpy as np

from app import config

log = logging.getLogger(__name__)


class _Ridge:
    """sklearn Ridge;缺失时退化为解析解岭回归,保证核心路径可用。"""

    def __init__(self, alpha=1.0):
        self.alpha = alpha
        self.w = None
        self.b = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray):
        try:
            from sklearn.linear_model import Ridge

            self._m = Ridge(alpha=self.alpha)
            self._m.fit(X, y)
            self.w = None
        except Exception:
            A = X.T @ X + self.alpha * np.eye(X.shape[1])
            self.w = np.linalg.solve(A, X.T @ y)
            self.b = 0.0
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(X)
        if self.w is None:
            return self._m.predict(X)
        return X @ self.w + self.b


def _standardize(X: np.ndarray, Xnow: np.ndarray):
    mu = X.mean(axis=0)
    sd = X.std(axis=0) + 1e-9
    return (X - mu) / sd, (Xnow - mu) / sd


class DirectMultiStepML:
    """kind ∈ {'ridge','hgb'}。"""

    def __init__(self, kind: str):
        self.kind = kind
        self.name = "M3R-直接岭回归" if kind == "ridge" else "M3H-梯度提升"

    def _training_rows(self, ctx, i: int, max_k: int) -> np.ndarray:
        valid = ctx["valid"]
        lo = i - config.TRAIN_WINDOW + 1
        return valid[(valid >= lo) & (valid <= i - max_k)]

    def predict(self, ctx, N: int) -> np.ndarray:
        lp: np.ndarray = ctx["lp"]
        i: int = ctx["i"]
        Xf: np.ndarray = ctx["Xf"]
        valid: np.ndarray = ctx["valid"]
        last = float(lp[i])
        pos_of = np.searchsorted(valid, i)
        if pos_of >= len(valid) or valid[pos_of] != i:
            return np.full(N, last)  # 当日特征缺失,退化为平直
        Xnow = Xf[valid[pos_of]]

        if self.kind == "ridge":
            out = np.empty(N)
            for k in range(1, N + 1):
                rows = self._training_rows(ctx, i, k)
                if len(rows) < 60:
                    out[k - 1] = last
                    continue
                Xtr, Xn = _standardize(Xf[rows], Xnow)
                ytr = lp[rows + k] - lp[rows]
                out[k - 1] = last + float(_Ridge().fit(Xtr, ytr).predict(Xn)[0])
            return out

        # HGB: 池化 horizon——每个样本 = (特征行 s, k) -> Δ_k
        try:
            from sklearn.ensemble import HistGradientBoostingRegressor
        except Exception:
            return np.full(N, last)
        rows = self._training_rows(ctx, i, 1)
        if len(rows) < 200:
            return np.full(N, last)
        ks = np.arange(1, N + 1)
        S = np.repeat(rows, N)  # s 重复 N 次
        K = np.tile(ks, len(rows))  # k 重复 rows 次
        # 只用 s+k<=i 的样本(避免泄漏)
        keep = S + K <= i
        S, K = S[keep], K[keep]
        Xtr_raw = np.hstack([Xf[S], (K / N)[:, None]])
        # 训练一次,预测 N 个 k 的输入
        Xall = np.hstack([np.tile(Xnow, (N, 1)), (ks / N)[:, None]])
        Xtr, _ = _standardize(Xtr_raw, Xall)
        ytr = lp[S + K] - lp[S]
        model = HistGradientBoostingRegressor(
            max_iter=120, learning_rate=0.08, max_leaf_nodes=15, random_state=0
        ).fit(Xtr, ytr)
        return last + model.predict(Xall)
