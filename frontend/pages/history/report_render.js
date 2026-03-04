// report_render.js
// Progressive report rendering for Stage 1 + Stage 2 (no React, vanilla HTML string rendering).
// This renderer outputs a human-readable report (titles + fields), NOT raw JSON dumps.

(function () {
  "use strict";

  /* ---------- Parsing helpers ---------- */

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function tryParseJsonFromText(text) {
    if (!text) return null;
    let t = String(text).trim();

    // Support fenced code blocks: ```json ... ```
    const m = t.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
    if (m && m[1]) t = m[1].trim();

    // Support "json ..." prefix
    t = t.replace(/^json\s*/i, "").trim();

    // Support quoted JSON
    if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) {
      t = t.slice(1, -1);
    }

    try {
      return JSON.parse(t);
    } catch {
      return null;
    }
  }

  /**
   * Extract analysis JSON object from server response.
   * Priority:
   *  1) a.json (object)
   *  2) a.json (string -> JSON.parse)
   *  3) parse from a.text
   */
  function extractAnalysisJson(a) {
    let j = a && a.json != null ? a.json : null;

    if (typeof j === "string") {
      try {
        j = JSON.parse(j);
      } catch {
        j = null;
      }
    }
    if (!j || typeof j !== "object") {
      j = tryParseJsonFromText(a && a.text ? a.text : "");
    }
    return j && typeof j === "object" ? j : null;
  }

  /* ---------- Formatting helpers ---------- */

  function yn(v) {
    return v === true ? "Yes" : v === false ? "No" : "Unknown";
  }

  function safeText(x, fallback = "—") {
    if (x === null || x === undefined) return fallback;
    const s = String(x).trim();
    return s ? s : fallback;
  }

  function fmtNum(n, digits = 2) {
    return typeof n === "number" && Number.isFinite(n) ? n.toFixed(digits) : "—";
  }

  function joinArr(a) {
    return Array.isArray(a) && a.length ? a.join(", ") : "—";
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

  function badgeForSeverity(sev) {
    const s = String(sev || "unknown").toLowerCase();
    if (s === "mild") return `<span class="badge ok">MILD</span>`;
    if (s === "moderate") return `<span class="badge warn">MODERATE</span>`;
    if (s === "high" || s === "severe") return `<span class="badge danger">${escapeHtml(s.toUpperCase())}</span>`;
    if (s === "none") return `<span class="badge">NONE</span>`;
    return `<span class="badge">${escapeHtml(String(sev || "UNKNOWN").toUpperCase())}</span>`;
  }

  function isPlainObject(x) {
    return x && typeof x === "object" && !Array.isArray(x);
  }

  function toBullets(items) {
    if (!Array.isArray(items) || !items.length) return `<p class="reportP muted">—</p>`;
    return `
      <ul style="margin: 8px 0 0; padding-left: 18px;">
        ${items.map((x) => `<li class="reportP">${escapeHtml(String(x))}</li>`).join("")}
      </ul>
    `;
  }

  function renderKeyValueTable(rows) {
    if (!Array.isArray(rows) || !rows.length) return `<p class="reportP muted">—</p>`;
    return `
      <div style="overflow-x:auto; margin-top: 8px;">
        <table style="width:100%; border-collapse: collapse;">
          <tbody>
            ${rows
              .map(
                (r) => `
              <tr>
                <td style="border-bottom:1px solid rgba(255,255,255,.06); padding:8px; width: 28%; color: rgba(255,255,255,.78);">
                  <strong>${escapeHtml(r.k)}</strong>
                </td>
                <td style="border-bottom:1px solid rgba(255,255,255,.06); padding:8px;">
                  ${escapeHtml(r.v)}
                </td>
              </tr>
            `
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  /* ---------- Stage 1 renderer (schema-based) ---------- */

  function renderStage1(stage1) {
    if (!stage1 || typeof stage1 !== "object") {
      return `
        <div class="reportSection">
          <div class="reportH">Stage 1</div>
          <p class="reportP muted">Stage 1 data is not available yet.</p>
        </div>
      `;
    }

    const occl = stage1.occlusion_assessment || {};
    const stats = stage1.input_temperature_stats || {};
    const regional = Array.isArray(stage1.regional_temperature_estimate_c)
      ? stage1.regional_temperature_estimate_c
      : [];
    const abn = stage1.temperature_abnormality_screen || {};

    const hasObs = occl.has_clothing_or_obstruction;
    const cred = safeText(occl.credibility, "unknown");

    const warn =
      hasObs === true || String(cred).toLowerCase() === "low"
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
          Clothing/occlusion detected or credibility is low: Stage 1 results may be biased. Interpret cautiously.
        </div>
      `
        : "";

    let factorsHtml = `<p class="reportP muted">No occlusion factors provided.</p>`;
    if (Array.isArray(occl.factors) && occl.factors.length) {
      factorsHtml = occl.factors
        .map((f) => {
          const sev = safeText(f.severity, "unknown");
          const sevClass =
            sev.toLowerCase() === "high"
              ? "danger"
              : sev.toLowerCase() === "medium"
              ? "warn"
              : "ok";

          return `
          <div style="border:1px solid rgba(255,255,255,.10); border-radius: 12px; padding: 10px 12px; margin-top: 10px;">
            <div class="reportRow" style="gap:8px; flex-wrap:wrap;">
              <span class="badge">${escapeHtml(safeText(f.type, "factor"))}</span>
              <span class="badge ${sevClass}">${escapeHtml(sev.toUpperCase())}</span>
            </div>
            <p class="reportP"><strong>Items:</strong> ${escapeHtml(joinArr(f.items))}</p>
            <p class="reportP"><strong>Location:</strong> ${escapeHtml(safeText(f.location_detail))}</p>
            <p class="reportP"><strong>Obscured parts:</strong> ${escapeHtml(joinArr(f.obscured_body_parts))}</p>
            <p class="reportP"><strong>Affected regions:</strong> ${escapeHtml(joinArr(f.affected_regions))}</p>
            <p class="reportP"><strong>Impact:</strong> ${escapeHtml(safeText(f.impact))}</p>
          </div>
        `;
        })
        .join("");
    }

    let limHtml = `<p class="reportP muted">—</p>`;
    if (Array.isArray(occl.limitations) && occl.limitations.length) {
      limHtml = `
        <ul style="margin: 8px 0 0; padding-left: 18px;">
          ${occl.limitations.map((x) => `<li class="reportP">${escapeHtml(String(x))}</li>`).join("")}
        </ul>
      `;
    }

    const unit = safeText(stats.unit, "C");
    const hotspot = stats.hotspot
      ? `(${safeText(stats.hotspot.x)}, ${safeText(stats.hotspot.y)}) @ ${fmtNum(stats.hotspot.temp_c)}${unit}`
      : "—";
    const coldspot = stats.coldspot
      ? `(${safeText(stats.coldspot.x)}, ${safeText(stats.coldspot.y)}) @ ${fmtNum(stats.coldspot.temp_c)}${unit}`
      : "—";

    let regionalHtml = `<p class="reportP muted">No regional estimates provided.</p>`;
    if (regional.length) {
      regionalHtml = `
        <div style="overflow-x:auto; margin-top: 8px;">
          <table style="width:100%; border-collapse: collapse;">
            <thead>
              <tr style="text-align:left;">
                <th style="border-bottom:1px solid rgba(255,255,255,.12); padding:8px;">Region</th>
                <th style="border-bottom:1px solid rgba(255,255,255,.12); padding:8px;">Temp</th>
                <th style="border-bottom:1px solid rgba(255,255,255,.12); padding:8px;">Visibility</th>
                <th style="border-bottom:1px solid rgba(255,255,255,.12); padding:8px;">Source grid</th>
                <th style="border-bottom:1px solid rgba(255,255,255,.12); padding:8px; min-width: 240px;">Note</th>
              </tr>
            </thead>
            <tbody>
              ${regional
                .map(
                  (r) => `
                <tr>
                  <td style="border-bottom:1px solid rgba(255,255,255,.06); padding:8px;">${escapeHtml(
                    safeText(r.region)
                  )}</td>
                  <td style="border-bottom:1px solid rgba(255,255,255,.06); padding:8px;">${escapeHtml(
                    fmtNum(r.temp_c)
                  )}${escapeHtml(unit)}</td>
                  <td style="border-bottom:1px solid rgba(255,255,255,.06); padding:8px;">${escapeHtml(
                    safeText(r.visibility)
                  )}</td>
                  <td style="border-bottom:1px solid rgba(255,255,255,.06); padding:8px;">${escapeHtml(
                    safeText(r.source_region_grid)
                  )}</td>
                  <td style="border-bottom:1px solid rgba(255,255,255,.06); padding:8px;">${escapeHtml(
                    safeText(r.note)
                  )}</td>
                </tr>
              `
                )
                .join("")}
            </tbody>
          </table>
        </div>
      `;
    }

    const flags = Array.isArray(abn.abnormal_flags) ? abn.abnormal_flags : [];
    const flagsHtml = flags.length
      ? flags.map((f) => `<span class="badge" style="margin-right:6px;">${escapeHtml(String(f))}</span>`).join("")
      : "—";

    return `
      ${warn}

      <div class="reportSection">
        <div class="reportH">Stage 1 · Visibility & Occlusion</div>
        <p class="reportP"><strong>Clothing/obstruction present:</strong> ${escapeHtml(yn(hasObs))}</p>
        <div class="reportRow">
          <span class="muted">Credibility:</span>
          ${badgeForConfidence(cred)}
        </div>
      </div>

      <div class="reportSection">
        <div class="reportH">Stage 1 · Occlusion Factors</div>
        ${factorsHtml}
        <div style="margin-top: 10px;">
          <div class="reportRow"><span class="muted">Limitations</span></div>
          ${limHtml}
        </div>
      </div>

      <div class="reportSection">
        <div class="reportH">Stage 1 · Temperature Statistics</div>
        ${renderKeyValueTable([
          { k: "Matrix available", v: yn(stats.matrix_available) },
          { k: "Original shape", v: Array.isArray(stats.original_shape) ? stats.original_shape.join(" × ") : "—" },
          { k: "Downsampled shape", v: Array.isArray(stats.downsampled_shape) ? stats.downsampled_shape.join(" × ") : "—" },
          { k: "Stride", v: Array.isArray(stats.downsample_stride) ? stats.downsample_stride.join(", ") : "—" },
          { k: "Max", v: `${fmtNum(stats.max_temp)}${unit}` },
          { k: "Min", v: `${fmtNum(stats.min_temp)}${unit}` },
          { k: "Mean", v: `${fmtNum(stats.mean_temp)}${unit}` },
          { k: "P10 / P50 / P90", v: `${fmtNum(stats.p10)} / ${fmtNum(stats.p50)} / ${fmtNum(stats.p90)} ${unit}` },
          { k: "Hotspot", v: hotspot },
          { k: "Coldspot", v: coldspot },
          { k: "Distal minus trunk", v: `${fmtNum(stats.indices && stats.indices.distal_minus_trunk_c)} °C` },
          { k: "Left-right mid diff", v: `${fmtNum(stats.indices && stats.indices.left_right_mid_mean_diff_c)} °C` },
        ])}
      </div>

      <div class="reportSection">
        <div class="reportH">Stage 1 · Regional Temperature Estimates</div>
        ${regionalHtml}
      </div>

      <div class="reportSection">
        <div class="reportH">Stage 1 · Abnormality Screen</div>
        <p class="reportP"><strong>Obvious abnormality:</strong> ${escapeHtml(yn(abn.has_obvious_abnormality))}</p>
        <div class="reportRow">
          <span class="muted">Confidence:</span>
          ${badgeForConfidence(safeText(abn.confidence, "unknown"))}
        </div>
        <p class="reportP"><strong>Flags:</strong> ${flagsHtml}</p>
      </div>
    `;
  }

  /* ---------- Stage 2 renderer (schema-based, human-readable) ---------- */

  function renderPatternFindings(items) {
    if (!Array.isArray(items) || !items.length) {
      return `<p class="reportP muted">—</p>`;
    }

    return items
      .map((p, idx) => {
        const type = safeText(p.type, "unknown");
        const sev = safeText(p.severity, "unknown");
        const regions = Array.isArray(p.regions_involved) ? p.regions_involved : [];
        const causes = Array.isArray(p.non_medical_possible_causes) ? p.non_medical_possible_causes : [];

        return `
          <div style="border:1px solid rgba(255,255,255,.10); border-radius: 12px; padding: 10px 12px; margin-top: 10px;">
            <div class="reportRow" style="gap:8px; flex-wrap:wrap;">
              <span class="badge">${escapeHtml(`Pattern ${idx + 1}`)}</span>
              <span class="badge">${escapeHtml(type)}</span>
              ${badgeForSeverity(sev)}
            </div>

            <div style="margin-top: 8px;">
              <p class="reportP"><strong>Regions:</strong> ${escapeHtml(regions.length ? regions.join(", ") : "—")}</p>
              <p class="reportP"><strong>Evidence:</strong> ${escapeHtml(safeText(p.evidence))}</p>
              <p class="reportP"><strong>Note:</strong> ${escapeHtml(safeText(p.note))}</p>
            </div>

            <div style="margin-top: 8px;">
              <div class="reportRow"><span class="muted">Non-medical possible causes</span></div>
              ${causes.length ? toBullets(causes) : `<p class="reportP muted">—</p>`}
            </div>

            ${
              p.detailed_explanation
                ? `<div style="margin-top: 8px;">
                     <div class="reportRow"><span class="muted">Detailed explanation</span></div>
                     <p class="reportP">${escapeHtml(safeText(p.detailed_explanation))}</p>
                   </div>`
                : ""
            }
          </div>
        `;
      })
      .join("");
  }

  function renderStage2(stage2) {
    if (!stage2 || typeof stage2 !== "object") {
      return `
        <div class="reportSection">
          <div class="reportH">Stage 2</div>
          <p class="reportP muted">Stage 2 report is generating…</p>
        </div>
      `;
    }

    const risk = safeText(stage2.overall_risk_level, "unknown");

    // summary can be either string or object: { expanded_summary: ... }
    let summaryText = "—";
    if (typeof stage2.summary === "string") summaryText = safeText(stage2.summary, "—");
    else if (isPlainObject(stage2.summary)) summaryText = safeText(stage2.summary.expanded_summary, "—");

    const stage1CredUsed = safeText(stage2.stage1_credibility_used, "—");

    const fat = stage2.fat_distribution_inference || {};
    const fatPerformed = fat && typeof fat.performed === "boolean" ? yn(fat.performed) : "Unknown";
    const fatConf = safeText(fat.confidence, "unknown");
    const fatAreas = Array.isArray(fat.inference) ? fat.inference : [];
    const fatNote = safeText(fat.note, "—");
    const fatSummary = safeText(fat.summary_paragraph, "—");

    // ---- TCM constitution tendency (new) ----
    const tcm = stage2.tcm_constitution_tendency || {};
    const tcmPerformed = tcm && typeof tcm.performed === "boolean" ? yn(tcm.performed) : "Unknown";
    const tcmType = safeText(tcm.possible_type, "—");
    const tcmConf = safeText(tcm.confidence, "unknown");
    const tcmReason = safeText(tcm.reasoning, "—");
    const tcmNote = safeText(tcm.note, "—");

    const advice = stage2.health_advice || {};
    const adviceItems = Array.isArray(advice.items) ? advice.items : [];
    const adviceNarrative = safeText(advice.narrative_block, "");

    const patterns = Array.isArray(stage2.pattern_findings) ? stage2.pattern_findings : [];

    // If stage2 is an object but basically empty, show generating state.
    if (
      !stage2.overall_risk_level &&
      !stage2.summary &&
      !stage2.health_advice &&
      !stage2.pattern_findings &&
      !stage2.fat_distribution_inference &&
      !stage2.tcm_constitution_tendency
    ) {
      return `
        <div class="reportSection">
          <div class="reportH">Stage 2</div>
          <p class="reportP muted">Stage 2 report is generating…</p>
        </div>
      `;
    }

    return `
      <div class="reportSection">
        <div class="reportH">Stage 2 · Summary & Risk</div>
        <div class="reportRow" style="gap:8px; flex-wrap:wrap;">
          <span class="badge">STAGE2</span>
          ${badgeForRisk(risk)}
          <span class="badge">Stage1 credibility used: ${escapeHtml(stage1CredUsed.toUpperCase())}</span>
        </div>
        <p class="reportP" style="margin-top: 10px;">${escapeHtml(summaryText)}</p>
      </div>

      <div class="reportSection">
        <div class="reportH">Stage 2 · Pattern Findings</div>
        ${renderPatternFindings(patterns)}
      </div>

      <div class="reportSection">
        <div class="reportH">Stage 2 · Fat Distribution Inference (Non-diagnostic)</div>
        ${renderKeyValueTable([
          { k: "Performed", v: fatPerformed },
          { k: "Confidence", v: fatConf.toUpperCase() },
          { k: "Inferred areas", v: fatAreas.length ? fatAreas.join(", ") : "—" },
        ])}
        <p class="reportP"><strong>Note:</strong> ${escapeHtml(fatNote)}</p>
        <p class="reportP"><strong>Summary:</strong> ${escapeHtml(fatSummary)}</p>
      </div>

      <div class="reportSection">
        <div class="reportH">Stage 2 · TCM Constitution Tendency (Non-diagnostic)</div>
        ${renderKeyValueTable([
          { k: "Performed", v: tcmPerformed },
          { k: "Possible type", v: tcmType },
          { k: "Confidence", v: tcmConf.toUpperCase() },
        ])}
        <p class="reportP"><strong>Reasoning:</strong> ${escapeHtml(tcmReason)}</p>
        <p class="reportP"><strong>Note:</strong> ${escapeHtml(tcmNote)}</p>
      </div>

      <div class="reportSection">
        <div class="reportH">Stage 2 · Advice (Non-medical)</div>
        ${adviceItems.length ? toBullets(adviceItems) : `<p class="reportP muted">—</p>`}
        ${adviceNarrative ? `<p class="reportP" style="margin-top:10px;">${escapeHtml(adviceNarrative)}</p>` : ""}
      </div>
    `;
  }

  /* ---------- Combined renderer ---------- */

  function renderAnalysisReportProgressive(a) {
    const j = extractAnalysisJson(a);

    if (!j || typeof j !== "object") {
      const fallbackText = a && a.text ? a.text : "No analysis result.";
      return `
        <div class="reportSection">
          <div class="reportH">Report</div>
          <p class="reportP">${escapeHtml(fallbackText)}</p>
        </div>
      `;
    }

    const progress = (j && j._progress) || {};
    const stage = progress.stage != null ? progress.stage : "";
    const status = safeText(progress.status, safeText(a && a.status, "unknown"));

    const raw = j._raw || {};
    const stage1 = raw.stage1 || null;
    const stage2 = raw.stage2 || null;

    // Prefer stage2 risk/summary if present
    const risk = safeText(
      j.overall_risk_level,
      stage2 ? safeText(stage2.overall_risk_level, "unknown") : "unknown"
    );

    let summary = "—";
    if (j.summary != null) summary = safeText(j.summary, "—");
    else if (stage2 && stage2.summary != null) {
      if (typeof stage2.summary === "string") summary = safeText(stage2.summary, "—");
      else if (isPlainObject(stage2.summary)) summary = safeText(stage2.summary.expanded_summary, "—");
    }

    return `
      <div class="reportSection">
        <div class="reportH">Progress</div>
        <div class="reportRow" style="gap:8px; flex-wrap:wrap;">
          <span class="badge">${escapeHtml("Status: " + status)}</span>
          <span class="badge">${escapeHtml("Stage: " + (stage || "—"))}</span>
          ${badgeForRisk(risk)}
        </div>
        <p class="reportP" style="margin-top:10px;">${escapeHtml(summary)}</p>
      </div>

      <div class="reportSection">
        <div class="reportH">Stage 1 report</div>
        ${renderStage1(stage1)}
      </div>

      <div class="reportSection">
        <div class="reportH">Stage 2 report</div>
        ${renderStage2(stage2)}
      </div>
    `;
  }

  /* ---------- Plain text for Copy ---------- */

  function reportToPlainTextProgressive(a) {
    const j = extractAnalysisJson(a);

    if (!j || typeof j !== "object") {
      return String(a && a.text ? a.text : "No analysis result.");
    }

    const raw = j._raw || {};
    const s1 = raw.stage1 || {};
    const s2 = raw.stage2 || {};

    const lines = [];

    const risk = safeText(
      j.overall_risk_level,
      s2 ? safeText(s2.overall_risk_level, "unknown") : "unknown"
    );

    let summary = "—";
    if (j.summary != null) summary = safeText(j.summary, "—");
    else if (s2 && s2.summary != null) {
      if (typeof s2.summary === "string") summary = safeText(s2.summary, "—");
      else if (isPlainObject(s2.summary)) summary = safeText(s2.summary.expanded_summary, "—");
    }

    lines.push(`Summary: ${summary}`);
    lines.push(`Risk level: ${risk}`);

    // Stage 1
    lines.push("");
    lines.push("[Stage 1]");
    const occl = s1.occlusion_assessment || {};
    lines.push(
      `Occlusion/clothing: ${yn(occl.has_clothing_or_obstruction)} (credibility: ${safeText(
        occl.credibility,
        "unknown"
      )})`
    );

    const stats = s1.input_temperature_stats || {};
    const unit = safeText(stats.unit, "C");
    const tParts = [];
    if (stats.max_temp != null) tParts.push(`Max ${stats.max_temp}${unit}`);
    if (stats.min_temp != null) tParts.push(`Min ${stats.min_temp}${unit}`);
    if (stats.mean_temp != null) tParts.push(`Mean ${stats.mean_temp}${unit}`);
    lines.push(`Temperature stats: ${tParts.length ? tParts.join(", ") : "—"}`);

    const abn = s1.temperature_abnormality_screen || {};
    lines.push(`Obvious abnormality: ${yn(abn.has_obvious_abnormality)} (confidence: ${safeText(abn.confidence, "unknown")})`);
    if (Array.isArray(abn.abnormal_flags) && abn.abnormal_flags.length) {
      lines.push(`Flags: ${abn.abnormal_flags.join(", ")}`);
    }

    // Stage 2
    lines.push("");
    lines.push("[Stage 2]");
    if (!s2 || typeof s2 !== "object" || Object.keys(s2).length === 0) {
      lines.push("Stage 2 is still generating.");
      return lines.join("\n");
    }

    lines.push(`Stage1 credibility used: ${safeText(s2.stage1_credibility_used, "—")}`);
    lines.push(`Risk level: ${safeText(s2.overall_risk_level, "unknown")}`);

    let s2Summary = "—";
    if (typeof s2.summary === "string") s2Summary = safeText(s2.summary, "—");
    else if (isPlainObject(s2.summary)) s2Summary = safeText(s2.summary.expanded_summary, "—");
    lines.push(`Summary: ${s2Summary}`);

    const patterns = Array.isArray(s2.pattern_findings) ? s2.pattern_findings : [];
    if (patterns.length) {
      lines.push("");
      lines.push("Pattern findings:");
      patterns.forEach((p, i) => {
        lines.push(`- #${i + 1} ${safeText(p.type, "unknown")} (${safeText(p.severity, "unknown")})`);
        const regs = Array.isArray(p.regions_involved) ? p.regions_involved : [];
        if (regs.length) lines.push(`  Regions: ${regs.join(", ")}`);
        if (p.evidence) lines.push(`  Evidence: ${safeText(p.evidence)}`);
        if (p.note) lines.push(`  Note: ${safeText(p.note)}`);
      });
    }

    const fat = s2.fat_distribution_inference || {};
    if (fat && typeof fat === "object" && Object.keys(fat).length) {
      lines.push("");
      lines.push("Fat distribution inference (non-diagnostic):");
      lines.push(`- Performed: ${typeof fat.performed === "boolean" ? yn(fat.performed) : "Unknown"}`);
      lines.push(`- Confidence: ${safeText(fat.confidence, "unknown")}`);
      if (Array.isArray(fat.inference) && fat.inference.length) lines.push(`- Areas: ${fat.inference.join(", ")}`);
      if (fat.note) lines.push(`- Note: ${safeText(fat.note)}`);
      if (fat.summary_paragraph) lines.push(`- Summary: ${safeText(fat.summary_paragraph)}`);
    }

    // ---- TCM constitution tendency (new) ----
    const tcm = s2.tcm_constitution_tendency || {};
    if (tcm && typeof tcm === "object" && Object.keys(tcm).length) {
      lines.push("");
      lines.push("TCM constitution tendency (non-diagnostic):");
      lines.push(`- Performed: ${typeof tcm.performed === "boolean" ? yn(tcm.performed) : "Unknown"}`);
      if (tcm.possible_type) lines.push(`- Possible type: ${safeText(tcm.possible_type, "—")}`);
      if (tcm.confidence) lines.push(`- Confidence: ${safeText(tcm.confidence, "unknown")}`);
      if (tcm.reasoning) lines.push(`- Reasoning: ${safeText(tcm.reasoning, "—")}`);
      if (tcm.note) lines.push(`- Note: ${safeText(tcm.note, "—")}`);
    }

    const advice = s2.health_advice || {};
    if (advice && typeof advice === "object" && Object.keys(advice).length) {
      lines.push("");
      lines.push("Advice (non-medical):");
      const items = Array.isArray(advice.items) ? advice.items : [];
      if (items.length) items.forEach((x) => lines.push(`- ${safeText(x)}`));
      if (advice.narrative_block) {
        lines.push("");
        lines.push(safeText(advice.narrative_block));
      }
    }

    return lines.join("\n");
  }

  // Expose APIs to window so process.js can call them.
  window.ReportRender = {
    extractAnalysisJson,
    renderAnalysisReportProgressive,
    reportToPlainTextProgressive,
  };
})();