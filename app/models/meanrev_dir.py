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

from app.models.mean_reversion import synthetic_meanrev_score

MREV_FEATS = ["ma60_dev", "ma20_dev", "mom20", "pos60", "cusd_mom20"]

# 校准缺失/过期时的回退命中率, 镜像 data/calibration.json 的 meanrev 段实测值。
# 取代原先单一常数 0.73 —— 后者把 N=7 抬到 0.73, 比实测 0.5386 高估 19pp。
# 必须 > 0.5: 强信号分支用 conf 决定 p_up 与最终方向, 取到 0.5 会让 score>0 被判成跌。
_DEFAULT_MEANREV = {7: 0.5386, 30: 0.5775, 60: 0.657, 90: 0.7482}


class MeanRevDirectionPredictor:
    name = "MeanRev-DIR"

    def __init__(self):
        self._meanrev_conf = {}  # {N: actual_accuracy} 从 calibration.json 加载

    def set_calibration(self, meanrev_conf: dict):
        """设置数据驱动的 base_conf: {N: accuracy}。"""
        self._meanrev_conf = meanrev_conf

    def predict_direction(self, ctx, N, conf_override=None):
        """conf_override: {N: hit_rate} 回测时传入的"扩展窗样本外"命中率,
        优先于实例自身的校准; None 时用 set_calibration 注入的全样本校准。"""
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

        # 置信度随 strength 连续过渡: 弱信号用公式 0.5+0.06*score, 强信号用
        # 校准命中率 base_conf, 两者在 strength 上线性混合消除 strength=1.0 边界跳变。
        cal = conf_override if conf_override is not None else self._meanrev_conf
        base_conf = cal.get(N, _DEFAULT_MEANREV.get(N, 0.55))
        p_up_med = 0.5 + 0.06 * np.clip(score, -2.0, 2.0)
        if confirms >= 1:
            p_up_med = 0.5 + (p_up_med - 0.5) * 1.2
        p_up_med = float(np.clip(p_up_med, 0, 1))
        med_conf = max(p_up_med, 1 - p_up_med)
        # strength=1 处完全用公式值, strength>=2 处完全用校准值, 中间线性混合
        blend = min(max((strength - 1.0) / 1.0, 0.0), 1.0)
        conf = med_conf * (1.0 - blend) + base_conf * blend
        p_up = p_up_med if strength < 1.0 else (conf if score > 0 else 1 - conf)
        return {"prediction": 1 if p_up > 0.5 else 0,
                "confidence": round(conf, 3),
                "prob_up": round(p_up, 3),
                "prob_down": round(float(1 - p_up), 3),
                "signal": "meanrev_strong" if strength >= 1.0 else "meanrev_medium",
                "confirms": confirms, "horizon": N}

    @staticmethod
    def _flat():
        return {"prediction": 0, "confidence": 0.5, "prob_up": 0.5, "prob_down": 0.5}
