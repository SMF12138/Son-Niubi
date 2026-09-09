"""M4 峰值日分类器:直接预测"未来 N 天中哪天见顶"(而非先拟路径再 argmax)。

策略:对每个候选日 k,用一个二分类器预测"该日是否为窗口内最高点"。
特征 = 基础特征 + k 相关特征(距今天数/占窗口比)。
训练样本池化(所有历史窗口,所有 k),标签 = 实际最高点。

这比路径预测更直接优化目标函数,避免了"路径形状误差→argmax误差"的级联。
"""
import logging

import numpy as np

from app import config

log = logging.getLogger(__name__)


class PeakClassifier:
    """直接分类法:输出各日成为见顶日的概率,取概率最高者为预测见顶日。"""

    name = "M4-直接分类"

    def predict(self, ctx, N: int) -> np.ndarray:
        """返回长度 N 的对数价格路径(兼容现有集成框架)。
        实际上用分类概率加权偏移构造伪路径:argmax(伪路径) = 分类 argmax。"""
        lp: np.ndarray = ctx["lp"]
        i: int = ctx["i"]
        Xf: np.ndarray = ctx["Xf"]
        valid: np.ndarray = ctx["valid"]

        last = float(lp[i])
        pos_of = np.searchsorted(valid, i)
        if pos_of >= len(valid) or valid[pos_of] != i:
            return np.full(N, last)

        # 构造训练样本:所有历史窗口(s, s+1..s+N),找实际最高点
        first = max(config.MIN_TRAIN, int(valid[0]))
        rows = valid[(valid >= first) & (valid <= i - N)]
        if len(rows) < 200:
            # 样本不足,回退为平直
            return np.full(N, last)

        # 限制到最近 TRAIN_WINDOW 个起点
        if len(rows) > config.TRAIN_WINDOW:
            rows = rows[-config.TRAIN_WINDOW:]

        # 池化训练样本:每个 (s, k) 对
        ks = np.arange(1, N + 1)
        S = np.repeat(rows, N)
        K = np.tile(ks, len(rows))
        keep = S + K <= i
        S, K = S[keep], K[keep]

        if len(S) < 100:
            return np.full(N, last)

        # 标签:该 (s, k) 的 s+k 天是否是窗口内最高点
        y = np.zeros(len(S), dtype=int)
        for idx_s, s in enumerate(rows):
            if s + N > i:
                continue
            win = lp[s + 1 : s + N + 1]
            peak_k = int(np.argmax(win)) + 1  # 1-based
            mask = S == s
            y[mask & (K == peak_k)] = 1

        # 训练集标签比例
        pos_rate = y.mean()
        if pos_rate < 0.01:
            return np.full(N, last)

        # 训练:把 k 归一化作为额外特征
        Xtr_raw = np.column_stack([Xf[S], K / N])
        Xnow_base = Xf[valid[pos_of]]
        # 预测:对所有 k=1..N
        Xall = np.column_stack([np.tile(Xnow_base, (N, 1)), ks / N])

        # 标准化
        mu = Xtr_raw.mean(axis=0)
        sd = Xtr_raw.std(axis=0) + 1e-9
        Xtr = (Xtr_raw - mu) / sd
        Xall_n = (Xall - mu) / sd

        # 使用 Logistic 回归或朴素贝叶斯(无需 sklearn 则用手动 sigmoid 回归)
        try:
            from sklearn.linear_model import LogisticRegression
            model = LogisticRegression(
                C=1.0, max_iter=300, solver="lbfgs",
                class_weight="balanced", random_state=0
            )
            model.fit(Xtr, y)
            prob = model.predict_proba(Xall_n)[:, 1]
        except Exception:
            # 手动岭逻辑回归
            prob = self._manual_logistic(Xtr, y, Xall_n)

        # 转为伪路径:让 argmax(伪路径) = argmax(prob)
        # 用 prob 的排名作为偏移量,叠加到 last
        pseudo = last + (prob - prob.min()) * 0.001
        return pseudo

    @staticmethod
    def _manual_logistic(Xtr, y, Xall, alpha=1.0, lr=0.01, epochs=200):
        """简易梯度下降逻辑回归,无需 sklearn。"""
        n, d = Xtr.shape
        w = np.zeros(d)
        b = 0.0
        for _ in range(epochs):
            z = Xtr @ w + b
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
            err = p - y
            grad_w = Xtr.T @ err / n + alpha * w
            grad_b = err.mean()
            w -= lr * grad_w
            b -= lr * grad_b
        z = Xall @ w + b
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
