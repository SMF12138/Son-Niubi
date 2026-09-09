/* CNY/RUB Premium Dashboard — App Logic */

const fmtPct = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");

async function fetchJson(url) {
  const r = await fetch(url);
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  return j;
}

let _state = { data: null, hor: 7, showHist: true, chart: null, all: {} };

const HORIZONS = [7, 30, 60, 90];

async function main() {
  await Promise.all(HORIZONS.map(async (n) => {
    try {
      _state.all[n] = await fetchJson(`/api/predict?n=${n}`);
    } catch (e) {
      _state.all[n] = { _error: (e && e.message) || "加载失败" };
    }
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
}

async function loadHorizonUI(n) {
  await loadHorizon(n);
  document.querySelectorAll(".hz-btn").forEach((x) =>
    x.classList.toggle("active", Number(x.dataset.hor) === n));
}

async function loadHorizon(n) {
  let data = _state.all[n];
  if (!data || data._error) {
    try {
      data = await fetchJson(`/api/predict?n=${n}`);
      _state.all[n] = data;
    } catch (e) {
      _state.all[n] = { _error: (e && e.message) || "加载失败" };
      renderError(n);
      return;
    }
  }
  _state.all[n] = data;
  _state.data = data;
  _state.hor = n;
  renderHero(data);
  drawChart();
  renderUncertainty(data);
}

function renderError(n) {
  const el = document.getElementById("hero");
  el.innerHTML = `
    <div class="hero-row">
      <div class="hero-main dir-down">
        <div class="hero-horizon">未来 ${n} 天</div>
        <div class="hero-direction" style="font-size:28px">— 暂不可用</div>
        <div class="hero-confidence"><span class="conf-label">数据加载失败，稍后重试</span></div>
      </div>
    </div>`;
}

/* ---- Hero Card ---- */
function renderHero(d) {
  const el = document.getElementById("hero");
  const dir = d.direction || {};
  const up = dir.prediction === 1;
  const conf = dir.confidence || 0.5;
  const acc = d.model_acc || {};
  const dirClass = up ? "dir-up" : "dir-down";
  const arrow = up ? "▲ 涨" : "▼ 跌";
  const confPct = (conf * 100).toFixed(0);

  let tier = "一般", badgeCls = "badge-low";
  if (conf >= 0.72) { tier = "较高"; badgeCls = "badge-mid"; }
  if (conf >= 0.8)  { tier = "很高"; badgeCls = "badge-high"; }

  const signalNames = {
    moex_dev: "MOEX市场偏离", meanrev_strong: "均值回复(强偏离)",
    mean_rev: "均值回复", meanrev_medium: "均值回复(中等)",
  };
  const sigName = signalNames[dir.signal] || dir.signal || "—";
  const confStr = dir.confirms ? ` · ${dir.confirms}重确认` : "";

  el.innerHTML = `
    <div class="hero-row">
      <div class="hero-main ${dirClass}">
        <div class="hero-horizon">未来 ${d.n} 天</div>
        <div class="hero-direction">${arrow}</div>
        <div class="hero-confidence">
          <span class="conf-value">${confPct}%</span>
          <span class="conf-label">把握程度</span>
          <span class="conf-badge ${badgeCls}">${tier}</span>
        </div>
        <div class="hero-signal">信号: <b>${sigName}</b>${confStr}</div>
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
          <span class="stat-sub">高把握时 ${fmtPct(acc.confident)}</span>
        </div>
        <div class="hero-stat">
          <span class="stat-label">数据截至</span>
          <span class="stat-value" style="font-size:16px">${d.as_of}</span>
          <span class="stat-sub">俄央行官方牌价</span>
        </div>
      </div>
    </div>`;

  // Update top bar status
  const statusEl = document.getElementById("topbarStatus");
  if (statusEl) {
    statusEl.innerHTML = `<span class="dot"></span> 实时 · ${d.as_of}`;
  }
}

/* ---- Uncertainty ---- */
function renderUncertainty(d) {
  const u = d.uncertainty;
  const sec = document.getElementById("uncertSection");
  if (!u || !u.points || !u.points.length) { sec.style.display = "none"; return; }
  sec.style.display = "";
  const lvlMap = { low: ["中等把握", "u-mid"], medium: ["有风险", "u-warn"], high: ["把握有限", "u-high"] };
  const lvl = lvlMap[u.level] || lvlMap.medium;
  document.getElementById("uncertBadge").className = "uncert-badge " + lvl[1];
  document.getElementById("uncertBadge").textContent = lvl[0];
  document.getElementById("uncertTitle").textContent = u.title;
  document.getElementById("uncertBody").innerHTML =
    u.points.map((p) => `<p>${p}</p>`).join("") +
    `<p class="uncert-disc">${u.disclaimer || ""}</p>`;
}

/* ---- Chart ---- */
function drawChart() {
  const d = _state.data;
  if (!d) return;
  const el = document.getElementById("mainChart");
  if (!_state.chart) _state.chart = echarts.init(el, null, { renderer: "canvas" });

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
  const histSeries = histVals.concat(Array(fcDates.length).fill(null));
  const anchorVal = showHist && histVals.length ? histVals[histVals.length - 1] : d.current_rate;
  const fcSeries = showHist
    ? Array(histDates.length - 1).fill(null).concat([anchorVal], fcVals)
    : [d.current_rate].concat(fcVals);
  const finalCats = showHist ? cats : [d.as_of].concat(fcDates);

  const dirUp = (d.direction || {}).prediction === 1;
  const lineColor = dirUp ? "#ef6c42" : "#22c59e";
  const bandColor = dirUp ? "rgba(239,108,66,0.12)" : "rgba(34,197,158,0.12)";

  const fcLow = d.forecast.map((p) => p.low);
  const fcHigh = d.forecast.map((p) => p.high);
  const hasBand = fcLow.every((v) => v != null) && fcHigh.every((v) => v != null);
  const bandPad = showHist ? Array(histDates.length - 1).fill(null) : [];
  const anchorForBand = showHist && histVals.length ? histVals[histVals.length - 1] : d.current_rate;
  const lowSeries = hasBand ? bandPad.concat([anchorForBand], fcLow) : [];
  const bandSeries = hasBand ? bandPad.concat([0], fcHigh.map((h, k) => h - fcLow[k])) : [];

  _state.chart.setOption({
    animation: true,
    animationDuration: 600,
    animationEasing: "cubicOut",
    grid: { left: 62, right: 24, top: 50, bottom: 48 },
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(10, 14, 24, 0.95)",
      borderColor: "rgba(56, 90, 150, 0.2)",
      borderWidth: 1,
      textStyle: { color: "#e2e8f0", fontSize: 12, fontFamily: "SF Mono, Consolas, monospace" },
      formatter: (params) => {
        const p = params[0];
        const date = p.axisValue;
        let s = `<div style="font-weight:600;margin-bottom:4px;color:#94a3b8;font-size:11px">${date}</div>`;
        params.forEach((it) => {
          if (it.value == null) return;
          s += `<div>${it.marker} ${it.seriesName}: <b style="color:#e2e8f0">${Number(it.value).toFixed(4)}</b></div>`;
        });
        const fp = d.forecast.find((x) => x.date === date);
        if (fp) {
          if (fp.low != null && fp.high != null) {
            s += `<div style="margin-top:4px;color:#64748b;font-size:11px">区间: ${fp.low.toFixed(4)} ~ ${fp.high.toFixed(4)}</div>`;
          }
          const macc = (d.model_acc || {}).overall;
          if (macc != null) {
            s += `<div style="margin-top:4px;color:#f59e0b;font-size:11px">历史准确率: ${(macc * 100).toFixed(1)}%</div>`;
          }
        }
        return s;
      },
    },
    legend: {
      top: 12, right: 24,
      data: showHist ? ["历史汇率", "预测"] : ["预测"],
      textStyle: { fontSize: 11, color: "#64748b" },
      itemWidth: 16, itemHeight: 2,
    },
    xAxis: {
      type: "category", data: finalCats, boundaryGap: false,
      axisLabel: { fontSize: 10, color: "#475569", hideOverlap: true, fontFamily: "SF Mono, Consolas, monospace" },
      axisLine: { lineStyle: { color: "rgba(56, 90, 150, 0.15)" } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value", scale: true,
      name: "RUB", nameTextStyle: { fontSize: 10, color: "#475569", fontFamily: "SF Mono, Consolas, monospace" },
      axisLabel: { fontSize: 10, color: "#475569", fontFamily: "SF Mono, Consolas, monospace" },
      splitLine: { lineStyle: { color: "rgba(56, 90, 150, 0.06)" } },
    },
    series: [
      ...(showHist ? [{
        name: "历史汇率", type: "line", symbol: "none", smooth: 0.3,
        data: histSeries,
        lineStyle: { width: 2, color: "#3b82f6" },
        areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: "rgba(59, 130, 246, 0.1)" }, { offset: 1, color: "rgba(59, 130, 246, 0)" }] } },
      }] : []),
      ...(hasBand ? [{
        name: "__bandLow", type: "line", stack: "band", symbol: "none",
        smooth: 0.3, data: lowSeries, lineStyle: { opacity: 0 },
        areaStyle: { opacity: 0 }, silent: true, tooltip: { show: false },
      }, {
        name: "可能区间", type: "line", stack: "band", symbol: "none",
        smooth: 0.3, data: bandSeries, lineStyle: { opacity: 0 },
        areaStyle: { color: bandColor }, silent: true, tooltip: { show: false },
      }] : []),
      {
        name: "预测", type: "line", symbol: "circle", symbolSize: 6, smooth: 0.3,
        data: fcSeries,
        lineStyle: { width: 2.5, type: "dashed", color: lineColor },
        itemStyle: { color: lineColor, borderColor: lineColor },
        emphasis: { itemStyle: { borderColor: "#fff", borderWidth: 2 } },
        markPoint: !showHist ? {
          symbol: "circle", symbolSize: 10,
          label: { show: true, formatter: "今天", fontSize: 10, color: "#94a3b8", position: "insideTop" },
          itemStyle: { color: "#3b82f6", borderColor: "#080c14", borderWidth: 2 },
          data: [{ coord: [0, d.current_rate] }],
        } : undefined,
      },
    ],
  }, true);

  // Responsive resize
  window.addEventListener("resize", () => _state.chart && _state.chart.resize());
}

/* ---- Health ---- */
async function loadHealth() {
  const el = document.getElementById("healthContent");
  try {
    const h = await fetchJson("/api/signal_health");
    const acc = h.rolling_acc || 0;
    const pct = (acc * 100).toFixed(1);
    let status, cls, desc;
    if (h.status === "healthy") {
      status = "健康"; cls = "h-good";
      desc = "模型运行良好，预测规律稳定有效。";
    } else if (h.status === "degraded") {
      status = "减弱"; cls = "h-warn";
      desc = "预测有效性有所下降，可能受近期政策或市场变化影响。";
    } else if (h.status === "alert") {
      status = "预警"; cls = "h-bad";
      desc = "预测规律明显减弱，模型可靠性降低。";
    } else {
      status = "数据不足"; cls = "h-warn"; desc = "样本不足，暂无法评估。";
    }
    const barW = Math.min(100, Math.max(0, acc * 100));
    el.innerHTML = `
      <div class="health-row">
        <span class="health-badge ${cls}">${status}</span>
        <span class="health-pct">${pct}%</span>
        <span class="health-meta">近${h.window_days || 90}天滚动命中率</span>
      </div>
      <div class="health-bar">
        <div class="health-fill ${cls}" style="width:${barW}%"></div>
        <div class="health-marker warn" style="left:55%" title="预警线 55%"></div>
        <div class="health-marker good" style="left:65%" title="健康线 65%"></div>
      </div>
      <div class="health-desc">${desc}</div>`;
  } catch (e) {
    el.innerHTML = `<div class="health-desc" style="color:#ef4444">健康度暂不可用：${e.message}</div>`;
  }
}

window.addEventListener("DOMContentLoaded", () => {
  main().catch((e) => {
    document.getElementById("hero").innerHTML =
      `<div class="hero-loading" style="color:#ef4444">加载失败：${e.message}</div>`;
  });
});
