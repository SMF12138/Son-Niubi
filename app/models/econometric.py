"""M2 统计模型:statsmodels 阻尼趋势指数平滑(ETS)。装不上/拟合失败时回退 M1。"""
import logging

import numpy as np

from app.models.baselines import DampedTrendModel

log = logging.getLogger(__name__)


class EtsModel:
    """M2 ETS(add, damped):对对数价格整路径外推。"""

    name = "M2-ETS"

    def __init__(self):
        self._fallback = DampedTrendModel()

    def predict(self, ctx, N: int) -> np.ndarray:
        try:
            from statsmodels.tsa.exponential_smoothing.ets import ETSModel
        except Exception:
            return self._fallback.predict(ctx, N)
        y = ctx["lp"][: ctx["i"] + 1]
        n = len(y)
        if n < 30:
            return self._fallback.predict(ctx, N)
        try:
            model = ETSModel(
                y,
                trend="add",
                damped_trend=True,
                initialization_method="estimated",
            )
            fit = model.fit(disp=False)
            return np.asarray(fit.forecast(N))
        except Exception:
            log.warning("ETS 拟合失败,回退 M1", exc_info=True)
            return self._fallback.predict(ctx, N)
