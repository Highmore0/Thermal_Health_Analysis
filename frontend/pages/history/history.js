function el(id) {
  return document.getElementById(id);
}

// Currently selected photo id (for delete action)
let currentPhotoId = null;

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours()
  )}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

async function fetchPhotos(limit = 200) {
  const r = await fetch(`/api/photos?limit=${limit}`, { cache: "no-store" });
  const j = await r.json();
  return j.photos || [];
}

// GET /api/photo/<id>/analysis -> { status, text, json, updated_at, ... }
async function fetchAnalysis(photoId) {
  const r = await fetch(`/api/photo/${photoId}/analysis`, { cache: "no-store" });
  if (!r.ok) {
    return {
      status: "none",
      text: "No analysis available (endpoint not implemented or this image has not been analyzed).",
    };
  }

  const j = await r.json();

  return {
    status: j.status || "done",
    text: j.text || (j.json ? JSON.stringify(j.json, null, 2) : "(empty)"),
  };
}

function setActiveCard(photoId) {
  document.querySelectorAll(".card").forEach((c) => {
    c.classList.toggle("active", c.dataset.id === String(photoId));
  });
}

function statusText(s) {
  if (!s) return "";
  if (s === "pending") return "🟡 pending";
  if (s === "running") return "🔵 running";
  if (s === "done") return "🟢 done";
  if (s === "error") return "🔴 error";
  if (s === "none") return "⚪ none";
  return String(s);
}

async function deleteCurrent() {
  if (!currentPhotoId) {
    alert("No photo selected.");
    return;
  }

  const ok = confirm(`Delete photo #${currentPhotoId}? This cannot be undone.`);
  if (!ok) return;

  const r = await fetch(`/api/photo/${currentPhotoId}`, { method: "DELETE" });
  if (!r.ok) {
    const t = await r.text().catch(() => "");
    alert(`Delete failed: ${t || r.statusText}`);
    return;
  }

  currentPhotoId = null;
  await refresh();
}

async function selectPhoto(p) {
  setActiveCard(p.id);
  currentPhotoId = p.id;

  el("titleText").textContent = `#${p.id} ${p.name || ""}`.trim();
  el("metaText").textContent = `${fmtTime(p.ts)}  min:${p.min} max:${p.max} mean:${Number(
    p.mean
  ).toFixed(2)}`;

  // Full-size preview
  el("preview").src = `/api/photo/${p.id}.jpg?_t=${Date.now()}`;

  // Report (show loading first)
  el("reportStatus").textContent = "Loading...";
  el("report").textContent = "Fetching analysis report...";

  const a = await fetchAnalysis(p.id);
  el("reportStatus").textContent = statusText(a.status);
  el("report").textContent = a.text || "(empty)";
}

function renderList(photos) {
  const list = el("photoList");
  list.innerHTML = "";

  if (!photos.length) {
    list.innerHTML =
      `<div style="color: rgba(231,238,247,.65); padding: 8px 4px;">No photos yet.</div>`;

    el("titleText").textContent = "Select an image";
    el("metaText").textContent = "";
    el("reportStatus").textContent = "";
    el("report").textContent = "The analysis report will be displayed here (scrollable).";
    el("preview").removeAttribute("src");

    return;
  }

  for (const p of photos) {
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.id = String(p.id);

    const img = document.createElement("img");
    img.className = "thumb";
    img.alt = `#${p.id}`;
    img.loading = "lazy";
    // Reuse the original image as a thumbnail for now; you can add a dedicated thumb endpoint later.
    img.src = `/api/photo/${p.id}.jpg`;

    const meta = document.createElement("div");
    meta.className = "cardMeta";

    const title = document.createElement("div");
    title.className = "cardTitle";
    title.textContent = `#${p.id} ${p.name || ""}`.trim();

    const sub = document.createElement("div");
    sub.className = "cardSub";
    sub.textContent = `${fmtTime(p.ts)}  min:${p.min} max:${p.max}`;

    meta.appendChild(title);
    meta.appendChild(sub);

    card.appendChild(img);
    card.appendChild(meta);

    card.onclick = () => selectPhoto(p);

    list.appendChild(card);
  }

  // Default to the first photo (latest)
  selectPhoto(photos[0]);
}

async function refresh() {
  el("titleText").textContent = "Loading...";
  el("metaText").textContent = "";
  el("reportStatus").textContent = "";
  el("report").textContent = "Loading...";

  try {
    const photos = await fetchPhotos(200);
    renderList(photos);
  } catch (e) {
    console.error(e);
    el("titleText").textContent = "Failed to load";
    el("report").textContent = String(e);
  }
}

window.addEventListener("DOMContentLoaded", () => {
  el("btnRefresh").onclick = refresh;
  el("btnDelete").onclick = deleteCurrent;
  refresh();
});
