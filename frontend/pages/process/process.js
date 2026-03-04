// process.js

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

/* ---------- Polling helpers ---------- */

function sleep(ms) {
  return new Promise((res) => setTimeout(res, ms));
}

/**
 * Poll analysis endpoint and call onUpdate for every successful response.
 * Stop when status becomes "done" or "error".
 */
async function pollAnalysisProgressive(
  photoId,
  {
    intervalMs = 1000,
    maxTries = 120,
    onUpdate = null,
  } = {}
) {
  let lastSerialized = null;

  for (let i = 0; i < maxTries; i++) {
    try {
      const a = await fetchJson(`/api/photo/${photoId}/analysis`);

      // Call onUpdate only if payload changes (reduce DOM flicker).
      if (onUpdate && a) {
        const serialized = JSON.stringify({
          status: a.status,
          text: a.text,
          json: a.json,
        });

        if (serialized !== lastSerialized) {
          lastSerialized = serialized;
          onUpdate(a);
        }
      }

      if (a && (a.status === "done" || a.status === "error")) {
        return a;
      }
    } catch (e) {
      // It's normal to see 404 before analysis is created in DB.
      // Keep polling.
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
  const btnSave = $("btnSave"); // optional
  const loading = $("loading");
  const reportBox = $("reportBox");
  const reportText = $("reportText");
  const errBox = $("errBox");
  const btnCopy = $("btnCopy");

  // Store last analysis object for Copy
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

  // Show temp image
  const finalImgUrl = `/api/temp/${encodeURIComponent(token)}.jpg`;
  const bust = `t=${Date.now()}`;
  if (shotImg) {
    shotImg.src = finalImgUrl.includes("?") ? `${finalImgUrl}&${bust}` : `${finalImgUrl}?${bust}`;
  }
  if (imgHint) imgHint.textContent = `Temp token: ${token}`;

  // Copy: copy progressive plain text report (Stage1 + Stage2)
  btnCopy?.addEventListener("click", async () => {
    const a = window.__lastAnalysis;
    const txt = a && window.ReportRender
      ? window.ReportRender.reportToPlainTextProgressive(a)
      : (reportText?.textContent || "");

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

    // Show report container immediately with a placeholder, so stage1 can appear as soon as it exists.
    setHidden(reportBox, false);
    if (reportText) {
      reportText.innerHTML = `
        <div class="reportSection">
          <div class="reportH">Progress</div>
          <p class="reportP muted">Waiting for analysis data…</p>
        </div>
      `;
    }

    if (btnAnalyze) btnAnalyze.disabled = true;
    if (btnSave) btnSave.disabled = true;
    setHidden(loading, false);

    try {
      // 1) Save and enqueue analysis
      const data = await fetchJson("/api/analyze_temp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });

      const id = data && (data.id || data.photo_id);
      if (!id) throw new Error("Missing photo id from /api/analyze_temp");
      if (imgHint) imgHint.textContent = `Saved. Photo ID: ${id}. Analyzing...`;

      // 2) Poll analysis result progressively (render stage1 immediately when available)
      const aFinal = await pollAnalysisProgressive(id, {
        intervalMs: 1000,
        maxTries: 180,
        onUpdate: (a) => {
          window.__lastAnalysis = a;

          if (!window.ReportRender || !reportText) return;

          reportText.innerHTML = window.ReportRender.renderAnalysisReportProgressive(a);
          setHidden(reportBox, false);
        },
      });

      window.__lastAnalysis = aFinal;

      if (aFinal.status === "error") {
        if (errBox) {
          errBox.textContent = `Analysis failed: ${aFinal.text || "unknown error"}`;
          setHidden(errBox, false);
        }
        return;
      }

      // Ensure final render on completion
      if (window.ReportRender && reportText) {
        reportText.innerHTML = window.ReportRender.renderAnalysisReportProgressive(aFinal);
      }
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