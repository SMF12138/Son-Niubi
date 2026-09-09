/* CNY/RUB 古典国风双色仪表盘 */

const fmtPct = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");

async function fetchJson(url) {
  const r = await fetch(url);
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  return j;
}

let _state = { data: null, hor: 7, showHist: true, chart: null, all: {} };
const HORIZONS = [7, 30, 60, 90];

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
}

/* ---- Hero ---- */
function renderHero(d) {
  const el = document.getElementById("hero");
  const dir = d.direction || {};
  const up = dir.prediction === 1;
  const conf = dir.confidence || 0.5;
  const acc = d.model_acc || {};
  const cls = up ? "dir-up" : "dir-down";
  const arrow = up ? "▲ 涨" : "▼ 跌";
  const pct = (conf * 100).toFixed(0);

  let tier = "一般", bc = "badge-low";
  if (conf >= 0.72) { tier = "较高"; bc = "badge-mid"; }
  if (conf >= 0.8) { tier = "很高"; bc = "badge-high"; }

  const sigNames = { moex_dev: "MOEX市场偏离", meanrev_strong: "均值回复(强偏离)", mean_rev: "均值回复" };
  const sig = sigNames[dir.signal] || dir.signal || "—";
  const cs = dir.confirms ? ` · ${dir.confirms}重确认` : "";

  el.innerHTML = `
    <div class="hero-row">
      <div class="hero-main ${cls}">
        <div class="hero-horizon">未来 ${d.n} 天</div>
        <div class="hero-direction">${arrow}</div>
        <div class="hero-confidence">
          <span class="conf-value">${pct}%</span>
          <span class="conf-label">把握程度</span>
          <span class="conf-badge ${bc}">${tier}</span>
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
          <span class="stat-sub">高把握时 ${fmtPct(acc.confident)}</span>
        </div>
        <div class="hero-stat">
          <span class="stat-label">数据截至</span>
          <span class="stat-value" style="font-size:15px">${d.as_of}</span>
          <span class="stat-sub">俄央行官方牌价</span>
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
  const lm = { low: ["中等把握", "u-mid"], medium: ["有风险", "u-warn"], high: ["把握有限", "u-high"] };
  const l = lm[u.level] || lm.medium;
  document.getElementById("uncertBadge").className = "uncert-badge " + l[1];
  document.getElementById("uncertBadge").textContent = l[0];
  document.getElementById("uncertTitle").textContent = u.title;
  document.getElementById("uncertBody").innerHTML =
    u.points.map((p) => `<p>${p}</p>`).join("") +
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
    tooltipText: dark ? "#E8DFD0" : "#2C1810",
  };
}

function drawChart() {
  const d = _state.data;
  if (!d) return;
  const el = document.getElementById("mainChart");
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
  const hasBand = fcLow.every((v) => v != null) && fcHigh.every((v) => v != null);
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
        let s = `<div style="font-weight:600;margin-bottom:4px;color:${C.label};font-size:11px">${params[0].axisValue}</div>`;
        params.forEach((it) => {
          if (it.value == null) return;
          s += `<div>${it.marker} ${it.seriesName}: <b>${Number(it.value).toFixed(4)}</b></div>`;
        });
        const fp = d.forecast.find((x) => x.date === params[0].axisValue);
        if (fp && fp.low != null) {
          s += `<div style="margin-top:3px;color:${C.label};font-size:11px">区间: ${fp.low.toFixed(4)} ~ ${fp.high.toFixed(4)}</div>`;
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
    if (h.status === "healthy") { status = "健康"; cls = "h-good"; desc = "模型运行良好，预测规律稳定有效。"; }
    else if (h.status === "degraded") { status = "减弱"; cls = "h-warn"; desc = "预测有效性有所下降。"; }
    else if (h.status === "alert") { status = "预警"; cls = "h-bad"; desc = "预测规律明显减弱。"; }
    else { status = "数据不足"; cls = "h-warn"; desc = "样本不足，暂无法评估。"; }
    const barW = Math.min(100, Math.max(0, acc * 100));
    el.innerHTML = `
      <div class="health-row">
        <span class="health-badge ${cls}">${status}</span>
        <span class="health-pct">${pct}%</span>
        <span class="health-meta">近${h.window_days || 90}天滚动命中率</span>
      </div>
      <div class="health-bar">
        <div class="health-fill ${cls}" style="width:${barW}%"></div>
        <div class="health-marker warn" style="left:55%"></div>
        <div class="health-marker good" style="left:65%"></div>
      </div>
      <div class="health-desc">${desc}</div>`;
  } catch (e) {
    el.innerHTML = `<div class="health-desc" style="color:var(--up)">健康度暂不可用：${e.message}</div>`;
  }
}

window.addEventListener("DOMContentLoaded", () => {
  main().catch((e) => {
    document.getElementById("hero").innerHTML =
      `<div class="hero-loading" style="color:var(--up)">加载失败：${e.message}</div>`;
  });
});
