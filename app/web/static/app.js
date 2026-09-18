/* CNY/RUB 古典国风双色仪表盘 */

const fmtPct = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");

async function fetchJson(url, timeoutMs = 15000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: ctl.signal });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    return j;
  } finally {
    clearTimeout(t);
  }
}

let _state = { data: null, hor: 7, showHist: true, chart: null, all: {} };
const HORIZONS = [7, 30, 60, 90];
const REFRESH_MS = 60000;   // 页面自动刷新间隔(与快层一致)

/* ---- Theme ---- */
function initTheme() {
  const saved = localStorage.getItem("theme") || "light";
  document.documentElement.setAttribute("data-theme", saved);
  document.getElementById("themeToggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    if (_state.chart) { _state.chart.dispose(); _state.chart = null; drawChart(); }
  });
}

/* ---- Main ---- */
async function main() {
  initTheme();
  bindResize();
  await Promise.all(HORIZONS.map(async (n) => {
    try { _state.all[n] = await fetchJson(`/api/predict?n=${n}`); }
    catch (e) { _state.all[n] = { _error: (e && e.message) || "加载失败" }; }
  }));
  await loadHorizon(7);
  await loadHealth();

  document.querySelectorAll(".hz-btn").forEach((b) => {
    b.addEventListener("click", () => loadHorizonUI(Number(b.dataset.hor)));
  });
  document.getElementById("histToggle").addEventListener("change", (e) => {
    _state.showHist = e.target.checked;
    drawChart();
  });

  _state.timer = setInterval(refreshTick, REFRESH_MS);
  window.addEventListener("pagehide", () => {
    if (_state.timer) { clearInterval(_state.timer); _state.timer = null; }
  });
}

/* 只注册一次(放在 main 里), 防抖, 避免每次 drawChart 叠加监听器 */
function bindResize() {
  let t = null;
  window.addEventListener("resize", () => {
    clearTimeout(t);
    t = setTimeout(() => _state.chart && _state.chart.resize(), 120);
  });
}

/* 定时刷新当前 horizon + 健康度(快层每 60s 更新数据) */
async function refreshTick() {
  const n = _state.hor;
  try {
    const data = await fetchJson(`/api/predict?n=${n}`);
    _state.all[n] = data;
    if (_state.hor === n) {
      _state.data = data;
      renderHero(data);
      drawChart();
      renderUncertainty(data);
    }
  } catch (e) { /* 静默失败, 下个周期重试 */ }
  loadHealth();
}

async function loadHorizonUI(n) {
  await loadHorizon(n);
  document.querySelectorAll(".hz-btn").forEach((x) =>
    x.classList.toggle("active", Number(x.dataset.hor) === n));
}

async function loadHorizon(n) {
  let data = _state.all[n];
  if (!data || data._error) {
    try { data = await fetchJson(`/api/predict?n=${n}`); _state.all[n] = data; }
    catch (e) { _state.all[n] = { _error: true }; renderError(n); return; }
  }
  _state.all[n] = data; _state.data = data; _state.hor = n;
  renderHero(data); drawChart(); renderUncertainty(data);
}

function renderError(n) {
  document.getElementById("hero").innerHTML = `
    <div class="hero-row"><div class="hero-main dir-down">
      <div class="hero-horizon">未来 ${n} 天</div>
      <div class="hero-direction" style="font-size:26px">— 暂不可用</div>
      <div class="hero-confidence"><span class="conf-label">数据加载失败</span></div>
    </div></div>`;
  const st = document.getElementById("topbarStatus");
  if (st) st.innerHTML = `<span class="dot"></span> 数据不可用`;
}

/* ---- Hero ---- */
function renderHero(d) {
  const el = document.getElementById("hero");
  const dir = d.direction || {};
  const hasDir = dir.prediction === 0 || dir.prediction === 1;
  const up = dir.prediction === 1;
  const conf = dir.confidence == null ? 0.5 : dir.confidence;
  const acc = d.model_acc || {};
  // 弱信号口径(单点真源): 把握度 < 55% 一律标弱(灰色"—"), 其余显示涨跌
  const WEAK_CONF = 0.55;
  const weak = hasDir && conf < WEAK_CONF;
  const cls = !hasDir ? "dir-none" : weak ? "dir-weak"
      : (up ? "dir-up" : "dir-down");
  const arrow = !hasDir ? "— 无明确信号"
      : weak ? "—"
      : (up ? "▲ 涨" : "▼ 跌");
  const pct = hasDir ? (conf * 100).toFixed(1) : "—";

  let badgeLabel = "把握有限", bc = "badge-low";
  const uBadge = (d.uncertainty || {}).badge;
  if (uBadge) {
    badgeLabel = uBadge.label;
    bc = "badge-" + uBadge.tier;
  }

  // 信号名统一中文口径(覆盖全部后端 signal 值, 缺项会漏出英文原文)
  const sigNames = {
    moex_dev: "市场偏离",
    moex_spread: "市场价差",
    meanrev_strong: "强均值回复",
    mean_rev: "均值回复",
    long_reversion: "长期均值回复",
    extreme_dist: "极值偏离",
    breakout: "区间突破",
  };
  const sig = sigNames[dir.signal] || "—";
  const cs = dir.confirms ? ` · ${dir.confirms}重确认` : "";
  // 副行统一口径: 近一年滚动命中率(与健康面板同算法同数字),
  // 与主行(历史准确率=全史)形成跨期对比——两者差距即模型近期漂移。
  const accSub = acc.rolling != null ? `近一年 ${fmtPct(acc.rolling)}` : "—";

  el.innerHTML = `
    <div class="hero-row">
      <div class="hero-main ${cls}">
        <div class="hero-horizon">未来 ${d.n} 天</div>
        <div class="hero-direction">${arrow}</div>
        <div class="hero-confidence">
          <span class="conf-value">${hasDir ? pct + "%" : "—"}</span>
          <span class="conf-label">把握程度</span>
          <span class="conf-badge ${bc}">${badgeLabel}</span>
        </div>
        <div class="hero-signal">信号: <b>${sig}</b>${cs}</div>
      </div>
      <div class="hero-stats">
        <div class="hero-stat">
          <span class="stat-label">当前汇率</span>
          <span class="stat-value">${d.current_rate.toFixed(4)}</span>
          <span class="stat-sub">1元 = ${d.current_rate.toFixed(2)} 卢布</span>
        </div>
        <div class="hero-stat">
          <span class="stat-label">历史准确率</span>
          <span class="stat-value">${fmtPct(acc.overall)}</span>
          <span class="stat-sub">${accSub}</span>
        </div>
        <div class="hero-stat">
          <span class="stat-label">数据截至</span>
          <span class="stat-value" style="font-size:15px">${d.as_of}</span>
          <span class="stat-sub">${d.refreshed_at ? "刷新于 " + d.refreshed_at.slice(11, 19) : "俄央行官方牌价"}</span>
        </div>
      </div>
    </div>`;
  const st = document.getElementById("topbarStatus");
  if (st) st.innerHTML = `<span class="dot"></span> ${d.as_of}`;
}

/* ---- Uncertainty ---- */
function renderUncertainty(d) {
  const u = d.uncertainty, sec = document.getElementById("uncertSection");
  if (!u || !u.points || !u.points.length) { sec.style.display = "none"; return; }
  sec.style.display = "";
  const uBadge = u.badge || {};
  const label = uBadge.label || "把握有限";
  const tier = uBadge.tier || "low";
  document.getElementById("uncertBadge").className = "uncert-badge u-" + tier;
  document.getElementById("uncertBadge").textContent = label;
  document.getElementById("uncertTitle").textContent = u.title;
  // 校准回退到内置默认表时必须可见 —— 否则把握度会无声地换成另一套数字
  const cal = d.calibration;
  const calNote = (cal && cal.is_fallback)
    ? `<p class="uncert-disc" style="color:var(--up)">注意：动态校准不可用（${
        cal.age_h == null ? "文件缺失或损坏" : "已过期 " + cal.age_h + " 小时"
      }），当前把握度已回退到内置默认表，可能偏离实测精度。</p>`
    : "";
  document.getElementById("uncertBody").innerHTML =
    u.points.map((p) => `<p>${p}</p>`).join("") +
    calNote +
    `<p class="uncert-disc">${u.disclaimer || ""}</p>`;
}

/* ---- Chart ---- */
function getColors() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  return {
    hist: dark ? "#8B7355" : "#5B7FA5",
    histArea: dark ? "rgba(139,115,85,0.12)" : "rgba(91,127,165,0.1)",
    up: dark ? "#E8615A" : "#C41E3A",
    down: dark ? "#5CB88A" : "#2E7D52",
    bandUp: dark ? "rgba(232,97,90,0.1)" : "rgba(196,30,58,0.08)",
    bandDown: dark ? "rgba(92,184,138,0.1)" : "rgba(46,125,82,0.08)",
    grid: dark ? "rgba(90,77,62,0.15)" : "rgba(214,206,189,0.4)",
    label: dark ? "#8A7E6E" : "#A99F8F",
    tooltip: dark ? "rgba(26,20,16,0.95)" : "rgba(44,24,16,0.92)",
    tooltipBorder: dark ? "#3D342A" : "#D6CEBD",
    tooltipText: dark ? "#E8DFD0" : "#F5EFE3",
    tooltipLabel: dark ? "#A99A85" : "#C4B7A2",
  };
}

function drawChart() {
  const d = _state.data;
  if (!d) return;
  const el = document.getElementById("mainChart");
  if (typeof echarts === "undefined") return;   // ECharts 未加载: 保留 KPI, 不抛异常
  if (!_state.chart) _state.chart = echarts.init(el);
  const C = getColors();
  const showHist = _state.showHist;
  let histDates = [], histVals = [];
  if (showHist) {
    const span = Math.max(_state.hor * 5, 60);
    const hist = d.hist.slice(-span);
    histDates = hist.map((p) => p.date);
    histVals = hist.map((p) => p.rate);
  }
  const fcDates = d.forecast.map((p) => p.date);
  const fcVals = d.forecast.map((p) => p.rate);
  const cats = histDates.concat(fcDates);
  const anchor = showHist && histVals.length ? histVals[histVals.length - 1] : d.current_rate;
  const fcSeries = showHist
    ? Array(histDates.length - 1).fill(null).concat([anchor], fcVals)
    : [d.current_rate].concat(fcVals);
  const finalCats = showHist ? cats : [d.as_of].concat(fcDates);
  const dirUp = (d.direction || {}).prediction === 1;
  const lineCol = dirUp ? C.up : C.down;
  const bandCol = dirUp ? C.bandUp : C.bandDown;
  const fcLow = d.forecast.map((p) => p.low);
  const fcHigh = d.forecast.map((p) => p.high);
  const hasBand = fcLow.length > 0 && fcHigh.length > 0 &&
    fcLow.every((v) => v != null) && fcHigh.every((v) => v != null);
  const bandPad = showHist ? Array(histDates.length - 1).fill(null) : [];
  const anchorBand = showHist && histVals.length ? histVals[histVals.length - 1] : d.current_rate;
  const lowS = hasBand ? bandPad.concat([anchorBand], fcLow) : [];
  const bandS = hasBand ? bandPad.concat([0], fcHigh.map((h, k) => h - fcLow[k])) : [];

  _state.chart.setOption({
    animation: true, animationDuration: 500,
    grid: { left: 60, right: 24, top: 48, bottom: 48 },
    tooltip: {
      trigger: "axis",
      backgroundColor: C.tooltip,
      borderColor: C.tooltipBorder,
      borderWidth: 1,
      textStyle: { color: C.tooltipText, fontSize: 12, fontFamily: "Georgia, 'Times New Roman', serif" },
      formatter: (params) => {
        let s = `<div style="font-weight:600;margin-bottom:4px;color:${C.tooltipLabel};font-size:11px">${params[0].axisValue}</div>`;
        params.forEach((it) => {
          if (it.value == null) return;
          s += `<div>${it.marker} ${it.seriesName}: <b>${Number(it.value).toFixed(4)}</b></div>`;
        });
        const fp = d.forecast.find((x) => x.date === params[0].axisValue);
        if (fp && fp.low != null) {
          s += `<div style="margin-top:3px;color:${C.tooltipLabel};font-size:11px">区间: ${fp.low.toFixed(4)} ~ ${fp.high.toFixed(4)}</div>`;
        }
        return s;
      },
    },
    legend: {
      top: 12, right: 24,
      data: showHist ? ["历史汇率", "预测"] : ["预测"],
      textStyle: { fontSize: 11, color: C.label, fontFamily: "'Noto Serif SC', serif" },
      itemWidth: 16, itemHeight: 2,
    },
    xAxis: {
      type: "category", data: finalCats, boundaryGap: false,
      axisLabel: { fontSize: 10, color: C.label, hideOverlap: true, fontFamily: "Georgia, serif" },
      axisLine: { lineStyle: { color: C.grid } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value", scale: true,
      name: "RUB", nameTextStyle: { fontSize: 10, color: C.label, fontFamily: "Georgia, serif" },
      axisLabel: { fontSize: 10, color: C.label, fontFamily: "Georgia, serif" },
      splitLine: { lineStyle: { color: C.grid } },
    },
    series: [
      ...(showHist ? [{
        name: "历史汇率", type: "line", symbol: "none", smooth: 0.3,
        data: (histVals.concat(Array(fcDates.length).fill(null))),
        lineStyle: { width: 2, color: C.hist },
        areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: C.histArea }, { offset: 1, color: "transparent" }] } },
      }] : []),
      ...(hasBand ? [{
        name: "__low", type: "line", stack: "b", symbol: "none", smooth: 0.3,
        data: lowS, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, silent: true, tooltip: { show: false },
      }, {
        name: "可能区间", type: "line", stack: "b", symbol: "none", smooth: 0.3,
        data: bandS, lineStyle: { opacity: 0 }, areaStyle: { color: bandCol }, silent: true, tooltip: { show: false },
      }] : []),
      {
        name: "预测", type: "line", symbol: "circle", symbolSize: 6, smooth: 0.3,
        data: fcSeries,
        lineStyle: { width: 2.5, type: "dashed", color: lineCol },
        itemStyle: { color: lineCol },
        markPoint: !showHist ? {
          symbol: "circle", symbolSize: 10,
          label: { show: true, formatter: "今日", fontSize: 10, color: C.label, position: "insideTop" },
          itemStyle: { color: C.hist, borderColor: "var(--bg-card)", borderWidth: 2 },
          data: [{ coord: [0, d.current_rate] }],
        } : undefined,
      },
    ],
  }, true);
}

/* ---- Health ---- */
async function loadHealth() {
  const el = document.getElementById("healthContent");
  try {
    const h = await fetchJson("/api/signal_health");
    // 兼容旧格式(单周期)和新格式(多周期 horizons)
    const horizons = h.horizons || {};
    const hs = Object.keys(horizons).length > 0
      ? Object.entries(horizons).map(([hz, v]) => ({ horizon: Number(hz), ...v }))
      : [{ horizon: 7, rolling_acc: h.rolling_acc || 0, status: h.status,
           name: "MOEX价差", window_days: h.window_days || 90,
           alert_threshold: h.alert_threshold, healthy_baseline: h.healthy_baseline,
           n: 0 }];

    const statusLabel = { healthy: "健康", degraded: "减弱", alert: "预警" };
    const statusCls = { healthy: "h-good", degraded: "h-warn", alert: "h-bad" };

    const rows = hs.map(v => {
      const acc = v.rolling_acc || 0;
      const pct = (acc * 100).toFixed(1);
      const warnAt = (v.alert_threshold != null ? v.alert_threshold * 100 : 50).toFixed(0);
      const goodAt = (v.healthy_baseline != null ? v.healthy_baseline * 100 : 60).toFixed(0);
      const cls = statusCls[v.status] || "h-warn";
      return `
      <div class="health-row">
        <span class="health-horizon">${v.horizon}日</span>
        <span class="health-badge ${cls}">${statusLabel[v.status] || "?"}</span>
        <span class="health-pct">${pct}%</span>
        <span class="health-meta">近1年已兑现 · ${v.n || 0}次</span>
      </div>
      <div class="health-bar">
        <div class="health-fill ${cls}" style="width:${Math.min(100, Math.max(0, acc*100))}%"></div>
        <div class="health-marker warn" style="left:${warnAt}%"></div>
        <div class="health-marker good" style="left:${goodAt}%"></div>
      </div>`;
    }).join("");

    // 总体提示
    const overall = h.overall_status || (h.status === "healthy" ? "healthy" : h.status);
    let overallDesc;
    if (overall === "healthy") {
      overallDesc = "全部周期信号健康，预测规律稳定。";
    } else if (overall === "degraded") {
      overallDesc = "部分周期信号有效性下降，相关预测请谨慎参考。";
    } else {
      overallDesc = "部分周期信号明显失效，建议忽略对应周期的方向预测。";
    }

    el.innerHTML = rows + `<div class="health-desc">${overallDesc}</div>`;
  } catch (e) {
    el.textContent = "";
    const div = document.createElement("div");
    div.className = "health-desc";
    div.style.color = "var(--up)";
    div.textContent = "健康度暂不可用：" + ((e && e.message) || "未知错误");
    el.appendChild(div);
  }
}

window.addEventListener("DOMContentLoaded", () => {
  main().catch((e) => {
    const hero = document.getElementById("hero");
    hero.textContent = "";
    const div = document.createElement("div");
    div.className = "hero-loading";
    div.style.color = "var(--up)";
    div.textContent = "加载失败：" + ((e && e.message) || "未知错误");
    hero.appendChild(div);
  });
});
