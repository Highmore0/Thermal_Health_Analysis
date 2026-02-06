function $(id) {
  return document.getElementById(id);
}

function getQueryParam(name) {
  const u = new URL(window.location.href);
  return u.searchParams.get(name);
}

function setHidden(el, hidden) {
  if (!el) return;
  if (hidden) el.classList.add("hidden");
  else el.classList.remove("hidden");
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function fetchJson(url, opts) {
  const res = await fetch(url, opts);
  const ct = res.headers.get("content-type") || "";

  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;

    if (ct.includes("application/json")) {
      const j = await res.json().catch(() => null);
      if (j && (j.error || j.message)) msg = j.error || j.message;
    } else {
      const t = await res.text().catch(() => null);
      if (t) msg = t.slice(0, 300);
    }

    throw new Error(msg);
  }

  if (ct.includes("application/json")) return await res.json();
  return await res.text();
}

/* ---------- Report rendering helpers ---------- */

function tryParseJsonFromText(text) {
  if (!text) return null;
  let t = String(text).trim();

  // 兼容：```json ... ``` 代码块
  const m = t.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  if (m && m[1]) t = m[1].trim();

  // 兼容：前面带 "json " 或其它前缀
  t = t.replace(/^json\s*/i, "").trim();

  // 兼容：有时外面会包一层引号
  if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) {
    t = t.slice(1, -1);
  }

  try {
    return JSON.parse(t);
  } catch {
    return null;
  }
}

function yn(v) {
  return v === true ? "是" : v === false ? "否" : "未知";
}

function safeText(x, fallback = "—") {
  if (x === null || x === undefined) return fallback;
  const s = String(x).trim();
  return s ? s : fallback;
}

function badgeForRisk(risk) {
  const r = String(risk || "unknown").toLowerCase();
  if (r === "low") return `<span class="badge ok">LOW</span>`;
  if (r === "medium") return `<span class="badge warn">MEDIUM</span>`;
  if (r === "high") return `<span class="badge danger">HIGH</span>`;
  return `<span class="badge">UNKNOWN</span>`;
}

function badgeForConfidence(conf) {
  const c = String(conf || "unknown").toLowerCase();
  if (c === "high") return `<span class="badge ok">HIGH</span>`;
  if (c === "medium") return `<span class="badge warn">MEDIUM</span>`;
  if (c === "low") return `<span class="badge danger">LOW</span>`;
  return `<span class="badge">UNKNOWN</span>`;
}

function shouldWarnLowReliability(vis) {
  const hasObs = vis && vis.has_clothing_or_obstruction === true;
  const conf = String(vis && vis.confidence ? vis.confidence : "").toLowerCase();
  const lowConf = conf === "low";
  return hasObs || lowConf;
}

/** 把分析结果对象 a 渲染成 HTML（小标题 + 文本） */
function renderAnalysisReport(a) {
  // 1) 优先用 a.json；2) 否则从 a.text 提取 JSON
  let j = a && a.json != null ? a.json : null;

  if (typeof j === "string") {
    try { j = JSON.parse(j); } catch { j = null; }
  }
  if (!j || typeof j !== "object") {
    j = tryParseJsonFromText(a && a.text ? a.text : "");
  }

  // 实在没有结构化数据，就显示文本（但做成更友好的样式）
  if (!j || typeof j !== "object") {
    const fallbackText = a && a.text ? a.text : "No analysis result.";
    return `
      <div class="reportSection">
        <div class="reportH">报告</div>
        <p class="reportP">${escapeHtml(fallbackText)}</p>
      </div>
    `;
  }

  const isThermal = j.is_thermal;
  const vis = j.visibility_assessment || {};
  const temp = j.temperature_analysis || {};
  const ir = j.infrared_signal_analysis || {};
  const body = j.body_distribution_analysis || {};
  const fat = j.fat_estimation || {};

  const risk = j.overall_risk_level || "unknown";
  const summary = j.summary || "—";

  // Image type
  let typeDesc = "";
  if (typeof isThermal === "boolean") {
    typeDesc = isThermal
      ? "图像被判断为红外/热成像画面。"
      : "图像未被判断为典型的红外/热成像画面，后续分析可能不可靠。";
  } else {
    typeDesc = "图像类型未知。";
  }

  // Visibility / obstruction
  const visHas = yn(vis.has_clothing_or_obstruction);
  const visDesc = safeText(vis.description, "暂无遮挡/穿戴说明。");
  const visConf = safeText(vis.confidence, "unknown");

  // Temperature
  const unit = temp.unit || "C";
  const temps = [];
  if (temp.max_temp != null) temps.push(`最高温度：${temp.max_temp}${unit}`);
  if (temp.min_temp != null) temps.push(`最低温度：${temp.min_temp}${unit}`);
  if (temp.avg_temp != null) temps.push(`平均温度：${temp.avg_temp}${unit}`);
  const tempLine = temps.length ? temps.join("，") : "未获取到有效温度数值数据。";

  // Body distribution analysis
  const bodyPerformed = yn(body.performed);
  const bodyNote = safeText(body.note, "暂无说明。");

  // Fat estimation
  const fatFlag = yn(fat.possible_fat_related_feature);
  const fatConf = safeText(fat.confidence, "unknown");
  const fatNote = safeText(fat.note, "暂无脂肪相关推断说明。");

  // Top warning banner (inline styles so you don't have to change CSS)
  const warnBanner = shouldWarnLowReliability(vis)
    ? `
      <div style="
        border:1px solid rgba(255,200,0,.35);
        background: rgba(255,200,0,.10);
        color: rgba(255,255,255,.92);
        padding: 10px 12px;
        border-radius: 12px;
        margin-bottom: 12px;
        line-height: 1.6;
        font-weight: 700;
      ">
        检测到衣物/遮挡或可信度较低：本次分析结果可能存在偏差，请谨慎解读。
      </div>
    `
    : "";

  // Compose sections
  return `
    ${warnBanner}

    <div class="reportSection">
      <div class="reportH">整体结论</div>
      <p class="reportP">${escapeHtml(safeText(summary))}</p>
      <div class="reportRow">
        <span class="muted">风险等级：</span>
        ${badgeForRisk(risk)}
      </div>
    </div>

    <div class="reportSection">
      <div class="reportH">图像类型判断</div>
      <p class="reportP">${escapeHtml(typeDesc)}</p>
    </div>

    <div class="reportSection">
      <div class="reportH">可见性与遮挡评估</div>
      <p class="reportP"><strong>是否存在衣物/遮挡：</strong>${escapeHtml(visHas)}</p>
      <div class="reportRow">
        <span class="muted">可信度：</span>
        ${badgeForConfidence(visConf)}
      </div>
      <p class="reportP">${escapeHtml(visDesc)}</p>
    </div>

    <div class="reportSection">
      <div class="reportH">温度数据分析</div>
      <p class="reportP">${escapeHtml(tempLine)}</p>
      <p class="reportP"><strong>是否存在明显温度异常：</strong>${escapeHtml(yn(temp.has_temp_abnormality))}</p>
      <p class="reportP">${escapeHtml(safeText(temp.temp_conclusion, "暂无进一步温度结论。"))}</p>
    </div>

    <div class="reportSection">
      <div class="reportH">红外信号分析</div>
      <p class="reportP"><strong>是否存在明显异常信号：</strong>${escapeHtml(yn(ir.has_obvious_abnormality))}</p>
      <p class="reportP">${escapeHtml(safeText(ir.description, "暂无红外异常细节描述。"))}</p>
    </div>

    <div class="reportSection">
      <div class="reportH">人体热分布分析（初步）</div>
      <p class="reportP"><strong>是否执行：</strong>${escapeHtml(bodyPerformed)}</p>
      <p class="reportP">${escapeHtml(bodyNote)}</p>
    </div>

    <div class="reportSection">
      <div class="reportH">脂肪相关特征（非诊断性）</div>
      <p class="reportP"><strong>可能存在脂肪相关特征：</strong>${escapeHtml(fatFlag)}</p>
      <div class="reportRow">
        <span class="muted">置信度：</span>
        ${badgeForConfidence(fatConf)}
      </div>
      <p class="reportP">${escapeHtml(fatNote)}</p>
    </div>
  `;
}

/** 把报告转成可复制的纯文本（不会复制 HTML 标签） */
function reportToPlainText(a) {
  let j = a && a.json != null ? a.json : null;

  if (typeof j === "string") {
    try { j = JSON.parse(j); } catch { j = null; }
  }
  if (!j || typeof j !== "object") {
    j = tryParseJsonFromText(a && a.text ? a.text : "");
  }
  if (!j || typeof j !== "object") {
    return String((a && a.text) ? a.text : "No analysis result.");
  }

  const lines = [];

  lines.push(`整体结论：${safeText(j.summary)}`);
  lines.push(`风险等级：${safeText(j.overall_risk_level, "unknown")}`);

  const isThermal = j.is_thermal;
  lines.push(`图像类型：${typeof isThermal === "boolean" ? (isThermal ? "红外/热成像" : "非典型热成像") : "未知"}`);

  const vis = j.visibility_assessment || {};
  lines.push(`遮挡/穿戴：${yn(vis.has_clothing_or_obstruction)}（可信度：${safeText(vis.confidence, "unknown")}）`);
  lines.push(`遮挡说明：${safeText(vis.description, "—")}`);

  const temp = j.temperature_analysis || {};
  const unit = temp.unit || "C";
  const tParts = [];
  if (temp.max_temp != null) tParts.push(`最高温度 ${temp.max_temp}${unit}`);
  if (temp.min_temp != null) tParts.push(`最低温度 ${temp.min_temp}${unit}`);
  if (temp.avg_temp != null) tParts.push(`平均温度 ${temp.avg_temp}${unit}`);
  lines.push(`温度数据：${tParts.length ? tParts.join("，") : "—"}`);
  lines.push(`明显温度异常：${yn(temp.has_temp_abnormality)}`);
  lines.push(`温度结论：${safeText(temp.temp_conclusion, "—")}`);

  const ir = j.infrared_signal_analysis || {};
  lines.push(`红外异常信号：${yn(ir.has_obvious_abnormality)}`);
  lines.push(`红外说明：${safeText(ir.description, "—")}`);

  const body = j.body_distribution_analysis || {};
  lines.push(`人体热分布分析是否执行：${yn(body.performed)}`);
  lines.push(`人体热分布说明：${safeText(body.note, "—")}`);

  const fat = j.fat_estimation || {};
  lines.push(`脂肪相关特征：${yn(fat.possible_fat_related_feature)}（置信度：${safeText(fat.confidence, "unknown")}）`);
  lines.push(`脂肪说明：${safeText(fat.note, "—")}`);

  return lines.join("\n");
}

/* ---------- Polling ---------- */

function sleep(ms) {
  return new Promise((res) => setTimeout(res, ms));
}

async function pollAnalysis(photoId, { intervalMs = 1000, maxTries = 90 } = {}) {
  for (let i = 0; i < maxTries; i++) {
    try {
      const a = await fetchJson(`/api/photo/${photoId}/analysis`);
      if (a && (a.status === "done" || a.status === "error")) return a;
    } catch (e) {
      // 可能 404：analysis 尚未写入，继续轮询
    }
    await sleep(intervalMs);
  }
  throw new Error("Timeout waiting for analysis result.");
}

/* ---------- Init ---------- */

(function init() {
  const token = getQueryParam("token");

  const shotImg = $("shotImg");
  const imgHint = $("imgHint");
  const btnAnalyze = $("btnAnalyze");
  const btnSave = $("btnSave"); // 可选
  const loading = $("loading");
  const reportBox = $("reportBox");
  const reportText = $("reportText");
  const errBox = $("errBox");
  const btnCopy = $("btnCopy");

  // 保存最近一次报告（用于 copy）
  window.__lastAnalysis = null;

  $("btnBack")?.addEventListener("click", () => {
    window.location.href = "../capture/index.html";
  });

  $("btnHistory")?.addEventListener("click", () => {
    window.location.href = "../history/index.html";
  });

  if (!token) {
    if (imgHint) imgHint.textContent = "Missing parameter: no temp token provided.";
    if (btnAnalyze) btnAnalyze.disabled = true;
    if (btnSave) btnSave.disabled = true;
    return;
  }

  // 展示 temp 图
  const finalImgUrl = `/api/temp/${encodeURIComponent(token)}.jpg`;
  const bust = `t=${Date.now()}`;
  if (shotImg) {
    shotImg.src = finalImgUrl.includes("?") ? `${finalImgUrl}&${bust}` : `${finalImgUrl}?${bust}`;
  }
  if (imgHint) imgHint.textContent = `Temp token: ${token}`;

  // Copy：复制纯文本报告
  btnCopy?.addEventListener("click", async () => {
    const a = window.__lastAnalysis;
    const txt = a ? reportToPlainText(a) : (reportText?.textContent || "");
    try {
      await navigator.clipboard.writeText(txt);
      btnCopy.textContent = "Copied";
      setTimeout(() => (btnCopy.textContent = "Copy"), 900);
    } catch (e) {
      alert("Copy failed: clipboard permission denied by the browser.");
    }
  });

  async function doSaveOnly() {
    setHidden(errBox, true);

    if (btnAnalyze) btnAnalyze.disabled = true;
    if (btnSave) btnSave.disabled = true;

    try {
      const data = await fetchJson("/api/save_temp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });

      const id = data && (data.id || data.photo_id);
      if (imgHint) imgHint.textContent = id ? `Saved as Photo ID: ${id}` : "Saved.";
    } catch (e) {
      if (errBox) {
        errBox.textContent = `Save failed: ${e.message}`;
        setHidden(errBox, false);
      }
    } finally {
      if (btnAnalyze) btnAnalyze.disabled = false;
      if (btnSave) btnSave.disabled = false;
    }
  }

  async function doAnalyzeAndSave() {
    setHidden(errBox, true);
    setHidden(reportBox, true);

    if (btnAnalyze) btnAnalyze.disabled = true;
    if (btnSave) btnSave.disabled = true;
    setHidden(loading, false);

    try {
      // 1) 保存并入队分析
      const data = await fetchJson("/api/analyze_temp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });

      const id = data && (data.id || data.photo_id);
      if (!id) throw new Error("Missing photo id from /api/analyze_temp");
      if (imgHint) imgHint.textContent = `Saved. Photo ID: ${id}. Analyzing...`;

      // 2) 轮询分析结果
      const a = await pollAnalysis(id, { intervalMs: 1000, maxTries: 120 });
      window.__lastAnalysis = a;

      if (a.status === "error") {
        if (errBox) {
          errBox.textContent = `Analysis failed: ${a.text || "unknown error"}`;
          setHidden(errBox, false);
        }
        return;
      }

      // 3) 渲染报告（HTML 分块）
      if (reportText) reportText.innerHTML = renderAnalysisReport(a);
      setHidden(reportBox, false);
      if (imgHint) imgHint.textContent = `Saved & analyzed. Photo ID: ${id}`;
    } catch (e) {
      if (errBox) {
        errBox.textContent = `Failed to get report: ${e.message}`;
        setHidden(errBox, false);
      }
    } finally {
      setHidden(loading, true);
      if (btnAnalyze) btnAnalyze.disabled = false;
      if (btnSave) btnSave.disabled = false;
    }
  }

  btnAnalyze?.addEventListener("click", doAnalyzeAndSave);

  if (btnSave) {
    btnSave.addEventListener("click", doSaveOnly);
  } else {
    console.warn('Missing "btnSave" element. Add a button with id="btnSave" if you need Save-only.');
  }
})();
