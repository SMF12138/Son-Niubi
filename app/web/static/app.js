/* 人民币兑卢布预测 — 面向普通用户的精简仪表盘 */

const fmtPct = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");

async function fetchJson(url) {
  const r = await fetch(url);
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  return j;
}

let _state = { data: null, hor: 7, showHist: true, chart: null, accMap: {}, all: {} };

const HORIZONS = [7, 30, 60, 90];
const HOR_LABEL = { 7: "未来7天", 30: "未来30天", 60: "未来60天", 90: "未来90天" };

async function main() {
  // 预拉取所有时长(失败的记录错误标记,切换时给提示,不静默吞)
  await Promise.all(HORIZONS.map(async (n) => {
    try {
      _state.all[n] = await fetchJson(`/api/predict?n=${n}`);
    } catch (e) {
      _state.all[n] = { _error: (e && e.message) || "加载失败" };
    }
  }));
  await loadHorizon(7);
  await loadHealth();

  // 视距切换按钮(手动,无自动轮播)
  document.querySelectorAll("#mainRange .rgbtn").forEach((b) => {
    b.addEventListener("click", () => loadHorizonUI(Number(b.dataset.hor)));
  });
  // 历史开关
  document.getElementById("histToggle").addEventListener("change", (e) => {
    _state.showHist = e.target.checked;
    drawChart();
  });
}

async function loadHorizonUI(n) {
  await loadHorizon(n);
  document.querySelectorAll("#mainRange .rgbtn").forEach((x) =>
    x.classList.toggle("active", Number(x.dataset.hor) === n));
}

async function loadHorizon(n) {
  let data = _state.all[n];
  // 无缓存或上次预拉失败 -> 重试一次
  if (!data || data._error) {
    try {
      data = await fetchJson(`/api/predict?n=${n}`);
      _state.all[n] = data;
    } catch (e) {
      _state.all[n] = { _error: (e && e.message) || "加载失败" };
      renderError(n, _state.all[n]._error);
      return;
    }
  }
  _state.all[n] = data;
  _state.data = data;
  _state.hor = n;
  _state.accMap = {};
  (data.daily_acc || []).forEach((p) => { _state.accMap[p.date] = p.acc; });
  renderHero(data);
  drawChart();
}

/* ---------- 错误提示卡 ---------- */
function renderError(n, msg) {
  const el = document.getElementById("hero");
  if (el) {
    el.innerHTML = `
      <div class="hero-row">
        <div class="hero-main down">
          <div class="hero-label">未来 ${n} 天判断</div>
          <div class="hero-dir">— 暂不可用</div>
          <div class="hero-conf">该时长数据加载失败，稍后重试</div>
        </div>
      </div>`;
  }
}

/* ---------- 核心预测卡 ---------- */
function renderHero(d) {
  const el = document.getElementById("hero");
  const dir = d.direction || {};
  const up = dir.prediction === 1;
  const conf = dir.confidence || 0.5;
  const acc = d.model_acc || {};
  const arrow = up ? "▲" : "▼";
  const dirText = up ? "上涨" : "下跌";
  const dirClass = up ? "up" : "down";
  const confPct = (conf * 100).toFixed(0);
  // 置信度等级
  let tier = "一般", tierCls = "t-low";
  if (conf >= 0.8) { tier = "很高"; tierCls = "t-high"; }
  else if (conf >= 0.72) { tier = "较高"; tierCls = "t-mid"; }
  else if (conf >= 0.6) { tier = "中等"; tierCls = "t-mid"; }

  // 信号来源说明
  const signalNames = {
    moex_dev: "MOEX市场偏离",
    meanrev_strong: "均值回复(强偏离)",
    mean_rev: "均值回复",
    meanrev_medium: "均值回复(中等)",
    macro: "宏观因子(油价+利率)",
    macro_fallback: "宏观因子(油价+利率)",
    macro_none: "无信号",
    macro_weak: "宏观信号弱",
  };
  const sigKey = dir.signal || "unknown";
  const sigName = signalNames[sigKey] || sigKey;
  const sigDetail = dir.sub_signals ? ` (${dir.sub_signals.join(", ")})` : "";
  const confStr = dir.confirms ? `· ${dir.confirms}重确认` : "";

  el.innerHTML = `
    <div class="hero-row">
      <div class="hero-main ${dirClass}">
        <div class="hero-label">未来 ${d.n} 天判断</div>
        <div class="hero-dir">${arrow} ${dirText}</div>
        <div class="hero-conf">把握程度 <b>${confPct}%</b> <span class="tier ${tierCls}">${tier}</span></div>
        <div class="hero-signal" style="font-size:11px;color:#999;margin-top:4px">信号: ${sigName}${sigDetail}${confStr}</div>
      </div>
      <div class="hero-side">
        <div class="hero-stat"><span>当前汇率</span><b>${d.current_rate.toFixed(4)}</b><small>1元 = ${d.current_rate.toFixed(2)} 卢布</small></div>
        <div class="hero-stat"><span>历史准确率</span><b>${fmtPct(acc.overall)}</b><small>高把握时 ${fmtPct(acc.confident)}</small></div>
        <div class="hero-stat"><span>数据截至</span><b>${d.as_of}</b><small>俄央行官方牌价</small></div>
      </div>
    </div>
    <div class="hero-dots" id="heroDots">
      ${HORIZONS.map((hn) => `<span class="hdot ${hn === d.n ? "on" : ""}" data-n="${hn}" title="未来${hn}天"></span>`).join("")}
    </div>
    ${renderUncertainty(d)}`;

  // 绑定圆点点击切换(与主图按钮联动)
  el.querySelectorAll(".hdot").forEach((dot) => {
    dot.addEventListener("click", () => loadHorizonUI(Number(dot.dataset.n)));
  });
}

/* 不确定性解释区 */
function renderUncertainty(d) {
  const u = d.uncertainty;
  if (!u) return "";
  const lvlMap = { low: ["中等把握", "u-mid"], medium: ["有风险", "u-warn"], high: ["把握有限", "u-high"], unknown: ["不明", "u-high"] };
  const lvl = lvlMap[u.level] || lvlMap.medium;
  return `
    <details class="uncert" open>
      <summary>
        <span class="u-badge ${lvl[1]}">${lvl[0]}</span>
        <span class="u-title">${u.title} — 点开看判断依据与风险</span>
      </summary>
      <div class="u-body">
        ${(u.points || []).map((p) => `<p>${p}</p>`).join("")}
        <p class="u-disc">${u.disclaimer}</p>
      </div>
    </details>`;
}

/* ---------- 主图 ---------- */
function drawChart() {
  const d = _state.data;
  if (!d) return;
  const el = document.getElementById("mainChart");
  if (!_state.chart) _state.chart = echarts.init(el);

  const showHist = _state.showHist;
  let histDates = [], histVals = [];
  if (showHist) {
    // 历史视距: 展示最近一段(约预测长度的4倍, 最少60天)
    const span = Math.max(_state.hor * 5, 60);
    const hist = d.hist.slice(-span);
    histDates = hist.map((p) => p.date);
    histVals = hist.map((p) => p.rate);
  }
  const fcDates = d.forecast.map((p) => p.date);
  const fcVals = d.forecast.map((p) => p.rate);

  // 连接点: 历史最后一点接预测第一点
  const cats = histDates.concat(fcDates);
  const histSeries = histVals.concat(Array(fcDates.length).fill(null));
  // 预测线: 从今天(历史末点)开始
  const anchorVal = showHist && histVals.length ? histVals[histVals.length - 1] : d.current_rate;
  const fcSeries = showHist
    ? Array(histDates.length - 1).fill(null).concat([anchorVal], fcVals)
    : [d.current_rate].concat(fcVals);
  const fcCats = showHist ? cats : [d.as_of].concat(fcDates);
  const finalCats = showHist ? cats : fcCats;

  const accMap = _state.accMap;
  const upColor = "#e8663d", downColor = "#2f9e78";
  const dirUp = (d.direction || {}).prediction === 1;
  const bandColor = dirUp ? "rgba(232,102,61,0.16)" : "rgba(47,158,120,0.16)";

  // 喻叭口不确定带: low 为透明底座, (high-low) 堆叠为可见填充带
  const fcLow = d.forecast.map((p) => p.low);
  const fcHigh = d.forecast.map((p) => p.high);
  const hasBand = fcLow.every((v) => v != null) && fcHigh.every((v) => v != null);
  // 对齐到 finalCats: showHist 时预测线从历史末点(anchor)起, 带也从同一锚点起(首天宽≈anchor无区间 → 喻叭口从0张开)
  const bandPad = showHist ? Array(histDates.length - 1).fill(null) : [];
  const anchorForBand = showHist && histVals.length ? histVals[histVals.length - 1] : d.current_rate;
  const lowSeries = hasBand ? bandPad.concat([anchorForBand], fcLow) : [];
  const bandSeries = hasBand ? bandPad.concat([0], fcHigh.map((h, k) => h - fcLow[k])) : [];

  _state.chart.setOption({
    animation: true,
    animationDuration: 400,
    grid: { left: 58, right: 20, top: 46, bottom: 48 },
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(30,34,45,0.92)",
      borderWidth: 0,
      textStyle: { color: "#fff", fontSize: 13 },
      formatter: (params) => {
        const p = params[0];
        const date = p.axisValue;
        let s = `<div style="font-weight:600;margin-bottom:4px">${date}</div>`;
        params.forEach((it) => {
          if (it.value == null) return;
          s += `<div>${it.marker} ${it.seriesName}: <b>${Number(it.value).toFixed(4)}</b> 卢布</div>`;
        });
        // 预测日: 显示未来预测的不确定区间 + 这条预测的准确率
        const fp = d.forecast.find((x) => x.date === date);
        if (fp) {
          if (fp.low != null && fp.high != null) {
            s += `<div style="margin-top:3px;color:rgba(255,255,255,0.82)">可能区间: ${fp.low.toFixed(4)} ~ ${fp.high.toFixed(4)}</div>`;
          }
          const macc = (d.model_acc || {}).overall;
          if (macc != null) {
            s += `<div style="margin-top:4px;color:#ffd166">📊 该预测历史准确率: <b>${(macc * 100).toFixed(1)}%</b></div>`;
          }
        }
        return s;
      },
    },
    legend: {
      top: 10, right: 20,
      data: showHist ? ["历史汇率", "预测"] : ["预测"],
      textStyle: { fontSize: 12, color: "#5a6472" },
    },
    xAxis: {
      type: "category", data: finalCats, boundaryGap: false,
      axisLabel: { fontSize: 11, color: "#8a94a3", hideOverlap: true },
      axisLine: { lineStyle: { color: "#e2e6ec" } },
    },
    yAxis: {
      type: "value", scale: true,
      name: "卢布", nameTextStyle: { fontSize: 11, color: "#8a94a3" },
      axisLabel: { fontSize: 11, color: "#8a94a3" },
      splitLine: { lineStyle: { color: "#eef1f5" } },
    },
    series: [
      ...(showHist ? [{
        name: "历史汇率", type: "line", symbol: "none", smooth: true,
        data: histSeries,
        lineStyle: { width: 2.5, color: "#3b7fd4" },
        areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: "rgba(59,127,212,0.15)" }, { offset: 1, color: "rgba(59,127,212,0.01)" }] } },
      }] : []),
      // 喻叭口不确定带(堆叠法): 底座透明 + 可见填充
      ...(hasBand ? [{
        name: "__bandLow", type: "line", stack: "band", symbol: "none",
        smooth: true, data: lowSeries, lineStyle: { opacity: 0 },
        areaStyle: { opacity: 0 }, silent: true, tooltip: { show: false },
      }, {
        name: "可能区间", type: "line", stack: "band", symbol: "none",
        smooth: true, data: bandSeries, lineStyle: { opacity: 0 },
        areaStyle: { color: bandColor }, silent: true, tooltip: { show: false },
      }] : []),
      {
        name: "预测", type: "line", symbol: "circle", symbolSize: 5, smooth: true,
        data: fcSeries,
        lineStyle: { width: 3, type: "dashed", color: dirUp ? upColor : downColor },
        itemStyle: { color: dirUp ? upColor : downColor },
        markPoint: !showHist ? {
          symbol: "pin", symbolSize: 46,
          label: { formatter: "今天", fontSize: 11, color: "#fff" },
          itemStyle: { color: "#3b7fd4" },
          data: [{ coord: [0, d.current_rate] }],
        } : undefined,
      },
    ],
  }, true);
}

/* ---------- 系统健康度 ---------- */
async function loadHealth() {
  const el = document.getElementById("healthCard");
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
      desc = "预测有效性有所下降，可能受近期政策或市场变化影响，建议谨慎参考。";
    } else if (h.status === "alert") {
      status = "预警"; cls = "h-bad";
      desc = "预测规律明显减弱，可能因政策/机制变化导致，模型可靠性降低。";
    } else {
      status = "数据不足"; cls = "h-warn"; desc = "样本不足，暂无法评估。";
    }
    const barW = Math.min(100, Math.max(0, acc * 100));
    el.innerHTML = `
      <div class="health-top">
        <div class="health-badge ${cls}">${status}</div>
        <div class="health-num">${pct}%</div>
        <div class="health-cap">近${h.window_days || 90}天滚动命中率</div>
      </div>
      <div class="health-bar"><div class="health-fill ${cls}" style="width:${barW}%"></div>
        <div class="health-mark" style="left:55%" title="预警线55%"></div>
        <div class="health-mark good" style="left:65%" title="健康线65%"></div>
      </div>
      <div class="health-desc">${desc}</div>`;
  } catch (e) {
    el.innerHTML = `<div class="health-desc">健康度暂不可用：${e.message}</div>`;
  }
}

document.getElementById("foot").textContent =
  "本页为数据分析展示，不构成任何投资建议。预测基于历史规律，无法保证未来表现。";

window.addEventListener("DOMContentLoaded", () => {
  main().catch((e) => {
    document.getElementById("hero").innerHTML =
      `<div class="hero-loading" style="color:#c0392b">加载失败：${e.message}</div>`;
  });
});
