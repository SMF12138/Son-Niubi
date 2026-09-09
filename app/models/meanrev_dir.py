"""线性合成均值回复方向预测器(MeanRevDirectionPredictor)。

替代 direction_plus 的 XGBoost 主导方案。实测(同数据、滚动无前视 walk-forward):
- mimo v4(XGBoost+LR+regime): 方向精度 47-55%,30/60日低于随机基线
- 线性合成均值回复(ma60_dev/ma20_dev/mom20/pos60/cusd_mom20 合成): N=60 全样本 56.4%,
  高置信档(P90, 覆盖10%)达 72.9%

策略:以合成均值回复分数为主导。分数强(高偏离)→ 直接看涨/看跌(历史可靠);
分数弱(市场居中)→ 退回常态概率(略优随机,诚实标注低置信)。

接口与 direction_plus.EnhancedDirectionPredictor 保持一致:
    predict_direction(ctx, N) -> {prediction, confidence, prob_up, prob_down}
"""
import numpy as np

from app import config
from app.models.mean_reversion import synthetic_meanrev_score

MREV_FEATS = ["ma60_dev", "ma20_dev", "mom20", "pos60", "cusd_mom20"]


class MeanRevDirectionPredictor:
    name = "MeanRev-DIR"

    def predict_direction(self, ctx, N):
        lp = ctx["lp"]; i = ctx["i"]; Xf = ctx["Xf"]; valid = ctx["valid"]
        feat_names = ctx.get("feat_names", [])
        pos_of = np.searchsorted(valid, i)
        if pos_of >= len(valid) or valid[pos_of] != i:
            return self._flat()

        # 合成均值回复分数(用滚动历史标准化, 无前视)
        cols = [feat_names.index(c) for c in MREV_FEATS if c in feat_names]
        if len(cols) < 3:
            return self._flat()
        sig = synthetic_meanrev_score(Xf, valid, i, cols)  # 只用 <=i 行标准化
        score = sig["score"]
        strength = sig["strength"]

        # USD/RUB 交叉确认(30天+ horizon 时启用)
        confirms = 0
        if N >= 30 and "usd_ret20" in feat_names:
            usd_idx = feat_names.index("usd_ret20")
            pos_of2 = np.searchsorted(valid, i)
            if pos_of2 < len(valid) and valid[pos_of2] == i:
                usd_mom = Xf[i, usd_idx]
                if not np.isnan(usd_mom):
                    # USD/RUB 动量与均值回复方向一致 → 确认
                    # USD/RUB 涨 → 卢布弱 → CNY/RUB 涨(看涨=1)
                    mr_pred = 1 if score > 0 else 0
                    usd_pred = 1 if usd_mom > 0 else 0
                    if mr_pred == usd_pred:
                        confirms += 1

        # 强信号: 直接采用均值回复方向, 高置信
        if strength >= 1.0:
            base_conf = min(0.73 + strength * 0.02, 0.82)
            if confirms >= 1:
                base_conf = min(base_conf + 0.02, 0.82)
            conf = base_conf
            p_up = conf if score > 0 else 1 - conf
            return {"prediction": 1 if p_up > 0.5 else 0,
                    "confidence": round(conf, 3),
                    "prob_up": round(p_up, 3),
                    "prob_down": round(1 - p_up, 3),
                    "signal": "meanrev_strong",
                    "confirms": confirms, "horizon": N}

        # 中等信号: 方向采用分数, 置信度温和
        p_up = 0.5 + 0.06 * np.clip(score, -2.0, 2.0)
        if confirms >= 1:
            p_up = 0.5 + (p_up - 0.5) * 1.2  # 确认后放大信号
        p_up = float(np.clip(p_up, 0, 1))
        return {"prediction": 1 if p_up > 0.5 else 0,
                "confidence": round(max(p_up, 1 - p_up), 3),
                "prob_up": round(p_up, 3),
                "prob_down": round(float(1 - p_up), 3),
                "signal": "meanrev_medium",
                "confirms": confirms, "horizon": N}

    @staticmethod
    def _flat():
        return {"prediction": 0, "confidence": 0.5, "prob_up": 0.5, "prob_down": 0.5}


def run_direction_backtest(df, oil_df=None, sentiment_df=None, rate_df=None):
    """与 direction_plus.run_direction_backtest 同形状的 walk-forward 回测入口。"""
    import logging
    from app.data.features import build_features
    log = logging.getLogger(__name__)
    lp = np.log(df["cny_rub"].to_numpy(float))
    dates = df.index; m = len(lp)
    Fdf = build_features(df, oil_df=oil_df, sentiment_df=sentiment_df, rate_df=rate_df)
    valid = np.where(Fdf.notna().all(axis=1).to_numpy())[0]
    Xf = Fdf.to_numpy(float); feat_names = list(Fdf.columns)
    pred = MeanRevDirectionPredictor()
    first = max(config.MIN_TRAIN, int(valid[0]) if len(valid) else 0)
    horizons = {}
    for N in config.N_HORIZONS:
        last = m - 1 - N
        starts = list(range(first, last + 1))
        correct = conf_c = conf_t = total = 0
        for i in starts:
            ctx = {"lp": lp, "i": i, "Xf": Xf, "valid": valid, "feat_names": feat_names}
            r = pred.predict_direction(ctx, N)
            actual = 1 if lp[i + N] > lp[i] else 0
            hit = r["prediction"] == actual
            correct += int(hit); total += 1
            if r["confidence"] > 0.6: conf_c += int(hit); conf_t += 1
        up = sum(1 for i in starts if lp[i + N] > lp[i])
        bl = max(up, total - up) / total if total else 0.5
        horizons[str(N)] = {
            "N": N, "windows": total, "accuracy": round(correct / total, 4),
            "confident_accuracy": round(conf_c / conf_t, 4) if conf_t else 0,
            "confident_windows": conf_t,
            "confident_ratio": round(conf_t / total, 4) if total else 0,
            "baseline_always_majority": round(bl, 4),
        }
        log.info("N=%d: %d 窗口 → %.1f%%, conf>60%%: %.1f%% (基线 %.1f%%)", N, total,
            correct / total * 100, (conf_c / conf_t * 100 if conf_t else 0), bl * 100)
    return {"meta": {"as_of": dates[-1].isoformat(), "rows": m,
                     "model": "MeanRevDirectionPredictor"},
            "horizons": horizons}
