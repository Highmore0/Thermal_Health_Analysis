function $(id) {
  return document.getElementById(id);
}

function getQueryParam(name) {
  const u = new URL(window.location.href);
  return u.searchParams.get(name);
}

function setHidden(el, hidden) {
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

(function init() {
  // New flow: capture -> token (temp image), then process uses token
  const token = getQueryParam("token");

  const shotImg = $("shotImg");
  const imgHint = $("imgHint");
  const btnAnalyze = $("btnAnalyze");
  const btnSave = $("btnSave"); // Requires a button with id="btnSave" in process.html
  const loading = $("loading");
  const reportBox = $("reportBox");
  const reportText = $("reportText");
  const errBox = $("errBox");
  const btnCopy = $("btnCopy");

  $("btnBack").addEventListener("click", () => {
    window.location.href = "../capture/index.html";
  });

  $("btnHistory").addEventListener("click", () => {
    window.location.href = "../history/index.html";
  });

  if (!token) {
    imgHint.textContent = "Missing parameter: no temp token provided.";
    btnAnalyze.disabled = true;
    if (btnSave) btnSave.disabled = true;
    return;
  }

  // Display the frozen temp image
  const finalImgUrl = `/api/temp/${encodeURIComponent(token)}.jpg`;

  // Cache-busting to avoid stale images
  const bust = `t=${Date.now()}`;
  shotImg.src = finalImgUrl.includes("?") ? `${finalImgUrl}&${bust}` : `${finalImgUrl}?${bust}`;
  imgHint.textContent = `Temp token: ${token}`;

  btnCopy.addEventListener("click", async () => {
    const txt = reportText.textContent || "";
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

    btnAnalyze.disabled = true;
    if (btnSave) btnSave.disabled = true;

    try {
      const data = await fetchJson("/api/save_temp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });

      const id = data && (data.id || data.photo_id);
      imgHint.textContent = id ? `Saved as Photo ID: ${id}` : "Saved.";
    } catch (e) {
      errBox.textContent = `Save failed: ${e.message}`;
      setHidden(errBox, false);
    } finally {
      btnAnalyze.disabled = false;
      if (btnSave) btnSave.disabled = false;
    }
  }

  async function sleep(ms) {
  return new Promise((res) => setTimeout(res, ms));
}

async function pollAnalysis(photoId, { intervalMs = 1000, maxTries = 90 } = {}) {
  for (let i = 0; i < maxTries; i++) {
    try {
      const a = await fetchJson(`/api/photo/${photoId}/analysis`);
      // a: { photo_id, status, text, json, updated_at }
      if (a && (a.status === "done" || a.status === "error")) return a;
      // queued/running 继续等
    } catch (e) {
      // 404 代表 analysis 还没写入，继续等一等
    }
    await sleep(intervalMs);
  }
  throw new Error("Timeout waiting for analysis result.");
}

async function doAnalyzeAndSave() {
  setHidden(errBox, true);
  setHidden(reportBox, true);

  btnAnalyze.disabled = true;
  if (btnSave) btnSave.disabled = true;
  setHidden(loading, false);

  try {
    // 1) 保存并入队分析（立刻返回 id）
    const data = await fetchJson("/api/analyze_temp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });

    const id = data && (data.id || data.photo_id);
    if (!id) throw new Error("Missing photo id from /api/analyze_temp");
    imgHint.textContent = `Saved. Photo ID: ${id}. Analyzing...`;

    // 2) 轮询获取分析结果
    const a = await pollAnalysis(id, { intervalMs: 1000, maxTries: 120 });

    if (a.status === "error") {
      errBox.textContent = `Analysis failed: ${a.text || "unknown error"}`;
      setHidden(errBox, false);
      return;
    }

    // done：优先展示 text；如果你想也展示 JSON，可以把它拼上
    let report = a.text || "";
    if (a.json) {
      report += "\n\n---\nJSON:\n" + JSON.stringify(a.json, null, 2);
    }

    reportText.textContent = report;
    setHidden(reportBox, false);
    imgHint.textContent = `Saved & analyzed. Photo ID: ${id}`;
  } catch (e) {
    errBox.textContent = `Failed to get report: ${e.message}`;
    setHidden(errBox, false);
  } finally {
    setHidden(loading, true);
    btnAnalyze.disabled = false;
    if (btnSave) btnSave.disabled = false;
  }
}


  btnAnalyze.addEventListener("click", doAnalyzeAndSave);

  if (btnSave) {
    btnSave.addEventListener("click", doSaveOnly);
  } else {
    // If you forget to add the Save button, at least don't crash.
    console.warn('Missing "btnSave" element. Add a button with id="btnSave" to enable Save Image.');
  }
})();
