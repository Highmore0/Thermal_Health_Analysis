// /pages/history/history.js

function $(id) { return document.getElementById(id); }

function setHidden(el, hidden) {
  if (!el) return;
  if (hidden) el.classList.add("hidden");
  else el.classList.remove("hidden");
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;")
    .replace(/'/g,"&#39;");
}

function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
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

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

let STATE = {
  photos: [],
  selectedId: null,
  pollToken: 0, // bump to cancel previous poll when selection changes
  lastAnalysis: null,
};

function updateSelectedUI() {
  const list = $("photoList");
  const kids = list.querySelectorAll(".photoItem");
  kids.forEach((el) => {
    const isSel = el.dataset.id === String(STATE.selectedId);
    el.classList.toggle("selected", isSel);
    el.setAttribute("aria-selected", isSel ? "true" : "false");
  });
}

function renderList(photos) {
  const list = $("photoList");
  list.innerHTML = "";

  if (!photos || !photos.length) {
    list.innerHTML = `<div class="hint">No records.</div>`;
    return;
  }

  for (const p of photos) {
    const id = p.id ?? p.photo_id;
    const item = document.createElement("div");
    item.className = "photoItem";
    item.setAttribute("role", "option");
    item.dataset.id = String(id);

    const name = (p.name || p.operator || "").trim() || "—";
    const ts = p.ts ?? p.timestamp ?? p.time ?? null;

    // These are typical fields from db_list_photos; fallback if missing.
    const tmin = (p.min ?? p.tmin);
    const tmax = (p.max ?? p.tmax);
    const mean = (p.mean ?? p.tmean);

    item.innerHTML = `
      <div class="photoItemTop">
        <div class="photoItemId">#${escapeHtml(id)}</div>
        <div class="photoItemTime">${escapeHtml(fmtTime(ts))}</div>
      </div>
      <div class="photoItemMid">
        <div class="photoItemName">${escapeHtml(name)}</div>
      </div>
      <div class="photoItemBot">
        <span class="pill">min: ${escapeHtml(tmin ?? "—")}</span>
        <span class="pill">max: ${escapeHtml(tmax ?? "—")}</span>
        <span class="pill">mean: ${escapeHtml(mean ?? "—")}</span>
      </div>
    `;

    item.addEventListener("click", () => selectPhoto(id, p));
    list.appendChild(item);
  }

  updateSelectedUI();
}

function setReportPlaceholder(msg) {
  const reportText = $("reportText");
  if (!reportText) return;
  reportText.innerHTML = `
    <div class="reportSection">
      <div class="reportH">Report</div>
      <p class="reportP muted">${escapeHtml(msg)}</p>
    </div>
  `;
}

function renderReportFromAnalysis(a) {
  const reportText = $("reportText");
  if (!reportText) return;

  STATE.lastAnalysis = a;

  if (window.ReportRender && typeof window.ReportRender.renderAnalysisReportProgressive === "function") {
    reportText.innerHTML = window.ReportRender.renderAnalysisReportProgressive(a);
  } else {
    // Fallback
    const txt = (a && a.text) ? String(a.text) : "No analysis result.";
    reportText.innerHTML = `
      <div class="reportSection">
        <div class="reportH">Report</div>
        <p class="reportP">${escapeHtml(txt)}</p>
      </div>
    `;
  }
}

async function loadPhotos() {
  const errBox = $("errBox");
  setHidden(errBox, true);

  const data = await fetchJson("/api/photos?limit=200", { cache: "no-store" });
  const photos = data.photos || data || [];
  STATE.photos = photos;

  renderList(photos);

  if (!STATE.selectedId && photos.length) {
    const first = photos[0];
    const id = first.id ?? first.photo_id;
    await selectPhoto(id, first);
  } else {
    updateSelectedUI();
  }
}

async function selectPhoto(id, metaObj = null) {
  STATE.selectedId = id;
  updateSelectedUI();

  const titleText = $("titleText");
  const metaText = $("metaText");
  const reportStatus = $("reportStatus");
  const preview = $("preview");
  const previewErr = $("previewErr");
  const btnDownloadTm = $("btnDownloadTm");
  const errBox = $("errBox");

  setHidden(errBox, true);
  setHidden(previewErr, true);

  const p = metaObj || STATE.photos.find(x => (x.id ?? x.photo_id) === id) || {};
  const ts = p.ts ?? p.timestamp ?? null;
  const name = (p.name || p.operator || "").trim() || "—";

  if (titleText) titleText.textContent = `Photo #${id}`;
  if (metaText) metaText.textContent = `Captured by: ${name}  |  Time: ${fmtTime(ts)}`;

  // Always use absolute path, not relative.
  const imgUrl = `/api/photo/${id}.jpg?t=${Date.now()}`;

  if (preview) {
    preview.onload = () => setHidden(previewErr, true);
    preview.onerror = () => {
      if (previewErr) {
        previewErr.textContent =
          "Image failed to load. Check that /api/photo/<id>.jpg returns 200 and that CSS is not hiding the image.";
        setHidden(previewErr, false);
      }
    };
    preview.src = imgUrl;
  }

  // TM download (may 404 if matrix not stored; still OK)
  if (btnDownloadTm) {
    btnDownloadTm.href = `/api/photo/${id}.tm.npy`;
    btnDownloadTm.classList.remove("hidden");
  }

  if (reportStatus) reportStatus.textContent = "Loading…";
  setReportPlaceholder("Waiting for analysis data…");

  // cancel previous polling
  const myPoll = ++STATE.pollToken;
  await pollAndRenderAnalysis(id, myPoll);
}

async function pollAndRenderAnalysis(photoId, myPollToken) {
  const reportStatus = $("reportStatus");
  const errBox = $("errBox");

  const intervalMs = 1000;
  const maxTries = 180;

  let lastSerialized = null;

  for (let i = 0; i < maxTries; i++) {
    // selection changed => stop
    if (myPollToken !== STATE.pollToken) return;

    try {
      const a = await fetchJson(`/api/photo/${photoId}/analysis`, { cache: "no-store" });

      const serialized = JSON.stringify({ status: a.status, text: a.text, json: a.json });
      if (serialized !== lastSerialized) {
        lastSerialized = serialized;

        if (reportStatus) reportStatus.textContent = String(a.status || "unknown");
        renderReportFromAnalysis(a);
      }

      if (a && (a.status === "done" || a.status === "error")) return;
    } catch (e) {
      // 404 before analysis exists is normal; keep polling.
      if (reportStatus) reportStatus.textContent = "none / pending";
      setHidden(errBox, true);
    }

    await sleep(intervalMs);
  }

  if (reportStatus) reportStatus.textContent = "timeout";
  if (errBox) {
    errBox.textContent = "Timeout waiting for analysis result.";
    setHidden(errBox, false);
  }
}

async function deleteSelected() {
  const id = STATE.selectedId;
  if (!id) return;

  const errBox = $("errBox");
  setHidden(errBox, true);

  try {
    await fetchJson(`/api/photo/${id}`, { method: "DELETE" });
    STATE.selectedId = null;
    STATE.lastAnalysis = null;
    $("titleText").textContent = "Select an image";
    $("metaText").textContent = "";
    $("reportStatus").textContent = "";
    setReportPlaceholder("No selection.");
    $("preview").src = "";
    $("btnDownloadTm")?.classList.add("hidden");

    await loadPhotos();
  } catch (e) {
    if (errBox) {
      errBox.textContent = `Delete failed: ${e.message}`;
      setHidden(errBox, false);
    }
  }
}

function setupCopy() {
  const btnCopy = $("btnCopy");
  btnCopy?.addEventListener("click", async () => {
    const a = STATE.lastAnalysis;
    const txt = a && window.ReportRender
      ? window.ReportRender.reportToPlainTextProgressive(a)
      : ($("reportText")?.textContent || "");

    try {
      await navigator.clipboard.writeText(txt);
      btnCopy.textContent = "Copied";
      setTimeout(() => (btnCopy.textContent = "Copy"), 900);
    } catch (e) {
      alert("Copy failed: clipboard permission denied by the browser.");
    }
  });
}

window.addEventListener("DOMContentLoaded", () => {
  $("btnRefresh")?.addEventListener("click", () => loadPhotos().catch(console.error));
  $("btnDelete")?.addEventListener("click", () => deleteSelected().catch(console.error));
  setupCopy();

  loadPhotos().catch((e) => {
    const errBox = $("errBox");
    if (errBox) {
      errBox.textContent = `Failed to load history: ${e.message}`;
      setHidden(errBox, false);
    }
  });
});