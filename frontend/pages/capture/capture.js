const LS_KEY = "thermal.capture.settings.v2";

const defaults = {
  operator: "",
  half: "auto",
  mode: "color",
  cm: "turbo",
  stretch: "1",
  shotMode: "instant", // "instant" or "timer3"

  // Keep these defaults to avoid undefined in the form
  ow: "",
  oh: "",
  keep: "1",
  rot: "0",
  flip: "0",
  showHud: "1",
};

function el(id) {
  return document.getElementById(id);
}

function loadSettings() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return { ...defaults };
    const obj = JSON.parse(raw);
    return { ...defaults, ...obj };
  } catch {
    return { ...defaults };
  }
}

function saveSettings(s) {
  localStorage.setItem(LS_KEY, JSON.stringify(s));
}

function qs(params) {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === null || v === undefined) return;
    const sv = String(v);
    if (sv.length === 0) return;
    u.set(k, sv);
  });
  return u.toString();
}

function setFormSettings(s) {
  // Defensive: if some inputs do not exist, do not throw
  if (el("operator")) el("operator").value = s.operator || "";
  if (el("half")) el("half").value = s.half;
  if (el("mode")) el("mode").value = s.mode;
  if (el("cm")) el("cm").value = s.cm;
  if (el("stretch")) el("stretch").value = s.stretch;

  if (el("ow")) el("ow").value = s.ow ?? "";
  if (el("oh")) el("oh").value = s.oh ?? "";
  if (el("keep")) el("keep").value = s.keep ?? "1";
  if (el("rot")) el("rot").value = s.rot ?? "0";
  if (el("flip")) el("flip").value = s.flip ?? "0";
  if (el("showHud")) el("showHud").value = s.showHud ?? "1";

  if (el("shotMode")) el("shotMode").value = s.shotMode || "instant";
}

function getFormSettings() {
  const v = (id, fallback = "") => {
    const e = el(id);
    return e ? e.value : fallback;
  };

  return {
    operator: (v("operator", "") || "").trim(),
    half: v("half", defaults.half),
    mode: v("mode", defaults.mode),
    cm: v("cm", defaults.cm),
    stretch: v("stretch", defaults.stretch),
    ow: v("ow", defaults.ow),
    oh: v("oh", defaults.oh),
    keep: v("keep", defaults.keep),
    rot: v("rot", defaults.rot),
    flip: v("flip", defaults.flip),
    showHud: v("showHud", defaults.showHud),
    shotMode: v("shotMode", defaults.shotMode),
  };
}

function updateOperatorLabel(s) {
  const name = (s.operator || "").trim();
  const lab = el("operatorLabel");
  if (lab) lab.textContent = name.length ? name : "—";
}

function applyStream(s) {
  const params = {
    half: s.half,
    mode: s.mode,
    cm: s.cm,
    stretch: s.stretch,

    // Pass through optional render params if present
    ow: s.ow,
    oh: s.oh,
    keep: s.keep,
    rot: s.rot,
    flip: s.flip,

    _t: Date.now(),
  };

  const img = el("mjpg");
  if (!img) return;
  img.src = "/api/video.mjpg?" + qs(params);
}

async function refreshStatsAndHud(s) {
  try {
    const r = await fetch("/api/stats", { cache: "no-store" });
    const j = await r.json();

    const hud = el("hud");
    if (!hud) return;

    if (s.showHud === "1") {
      hud.style.display = "block";
      const fmt = (v) =>
        v === null || v === undefined || Number.isNaN(Number(v)) ? "--" : Number(v).toFixed(2);

      hud.textContent =
        `tmin: ${fmt(j.tmin)} °C\n` +
        `tmax: ${fmt(j.tmax)} °C\n` +
        `tmean: ${fmt(j.tmean)} °C\n` +
        `${j.mode}/${j.cm} half=${j.half}\n` +
        `out: ${j.out_shape?.[0]}x${j.out_shape?.[1]}`;
    } else {
      hud.style.display = "none";
    }
  } catch {
    /* ignore */
  }
}

let shutterBusy = false;

/* Utility: sleep helper */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/* Show countdown overlay */
function showCountdown(n) {
  const overlay = el("countdownOverlay");
  const text = el("countdownText");
  if (!overlay || !text) return;

  text.textContent = String(n);
  overlay.classList.remove("hidden");
  overlay.setAttribute("aria-hidden", "false");
}

/* Hide countdown overlay */
function hideCountdown() {
  const overlay = el("countdownOverlay");
  if (!overlay) return;

  overlay.classList.add("hidden");
  overlay.setAttribute("aria-hidden", "true");
}

/* Run 3..2..1 countdown */
async function runCountdown3() {
  for (let t = 3; t >= 1; t--) {
    showCountdown(t);
    await sleep(1000);
  }
  hideCountdown();
}

/* Capture snapshot into TEMP and go to process page */
async function doSnapshot(s) {
  const st = el("statusText");
  if (st) st.textContent = "Capturing...";

  try {
    const payload = {
      half: s.half,
      mode: s.mode,
      cm: s.cm,
      stretch: s.stretch,

      // Optional render params
      ow: s.ow,
      oh: s.oh,
      keep: s.keep,
      rot: s.rot,
      flip: s.flip,

      // Store operator as name
      name: s.operator || "",
    };

    const r = await fetch("/api/capture_temp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || "snapshot failed");

    if (st) st.textContent = "Saved";
    const last = el("lastShot");
    if (last) last.textContent = `token: ${j.token}`;

    window.location.href = `/pages/process/?token=${encodeURIComponent(j.token)}`;
  } catch (e) {
    if (st) st.textContent = "Error";
    console.error(e);
    setTimeout(() => {
      if (st) st.textContent = "Ready";
    }, 1200);
  }
}

function setupDrawer() {
  const drawer = el("settingsDrawer");
  const btnSettings = el("btnSettings");
  const btnClose = el("btnCloseSettings");
  const mjpg = el("mjpg");

  if (btnSettings && drawer) btnSettings.onclick = () => drawer.classList.toggle("hidden");
  if (btnClose && drawer) btnClose.onclick = () => drawer.classList.add("hidden");
  if (mjpg && drawer) mjpg.onclick = () => drawer.classList.add("hidden");
}

function setupAutoSaveAndApply() {
  const idsChange = ["half", "mode", "cm", "stretch", "ow", "oh", "keep", "rot", "flip", "showHud", "shotMode"];
  idsChange.forEach((id) => {
    const e = el(id);
    if (!e) return;
    e.addEventListener("change", () => {
      const s = getFormSettings();
      saveSettings(s);
      applyStream(s);
      refreshStatsAndHud(s);
      updateOperatorLabel(s);
    });
  });

  const op = el("operator");
  if (op) {
    op.addEventListener("input", () => {
      const s = getFormSettings();
      saveSettings(s);
      updateOperatorLabel(s);
    });
  }
}

function startHudLoop() {
  setInterval(() => {
    const s = getFormSettings();
    refreshStatsAndHud(s);
  }, 800);
}

/* -----------------------------
   Small gray upload button (bottom-left)
   Requires in HTML:
     <button id="btnUploadLocal" class="upload-btn">...</button>
     <input id="fileInput" type="file" ... style="display:none;">
   Backend:
     POST /api/upload_temp (multipart/form-data)
----------------------------- */
function setupUploadButton() {
  const btn = el("btnUploadLocal");
  const fileInput = el("fileInput");
  const statusText = el("statusText");

  // If upload UI is not present, silently skip
  if (!btn || !fileInput) return;

  function isAllowedImage(f) {
    if (!f) return false;
    const t = (f.type || "").toLowerCase();
    if (t === "image/jpeg" || t === "image/png") return true;
    const n = (f.name || "").toLowerCase();
    return n.endsWith(".jpg") || n.endsWith(".jpeg") || n.endsWith(".png");
  }

  btn.addEventListener("click", () => {
    fileInput.click();
  });

  fileInput.addEventListener("change", async () => {
    const f = fileInput.files && fileInput.files[0];
    if (!f) return;

    if (!isAllowedImage(f)) {
      alert("Only JPG/PNG supported.");
      fileInput.value = "";
      return;
    }

    if (statusText) statusText.textContent = "Uploading...";

    const fd = new FormData();
    fd.append("file", f);

    // Use operator as default name (optional)
    const s = getFormSettings();
    const name = (s.operator || "").trim();
    if (name) fd.append("name", name);

    try {
      const resp = await fetch("/api/upload_temp", { method: "POST", body: fd });
      const data = await resp.json().catch(() => ({}));

      if (!resp.ok || !data.ok) {
        const msg = "Upload failed: " + (data.error || resp.statusText);
        if (statusText) statusText.textContent = "Error";
        alert(msg);
        return;
      }

      // Redirect to process page with token
      const url = data.process_url || ("/pages/process/?token=" + encodeURIComponent(data.token));
      window.location.href = url;
    } catch (err) {
      if (statusText) statusText.textContent = "Error";
      alert("Upload error: " + String(err));
    } finally {
      // Reset selection to allow re-upload of the same file
      fileInput.value = "";
    }
  });
}

window.addEventListener("DOMContentLoaded", () => {
  const s = loadSettings();
  setFormSettings(s);
  updateOperatorLabel(s);

  setupDrawer();
  setupAutoSaveAndApply();
  setupUploadButton();

  applyStream(s);
  refreshStatsAndHud(s);
  startHudLoop();

  const btn = el("btnShutter");
  if (btn) {
    btn.onclick = async () => {
      if (shutterBusy) return;
      shutterBusy = true;
      btn.disabled = true;

      try {
        const s = getFormSettings();
        const mode = s.shotMode || "instant"; // "instant" or "timer3"

        // Live preview continues underneath the countdown overlay
        if (mode === "timer3") {
          await runCountdown3();
        }

        await doSnapshot(s);
      } finally {
        hideCountdown();
        btn.disabled = false;
        shutterBusy = false;
      }
    };
  }
});