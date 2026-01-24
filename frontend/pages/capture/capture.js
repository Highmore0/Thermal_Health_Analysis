const LS_KEY = "thermal.capture.settings.v2";

const defaults = {
  operator: "",
  half: "auto",
  mode: "color",
  cm: "inferno",
  stretch: "1",
  ow: 512,
  oh: 512,
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
  Object.entries(params).forEach(([k, v]) => u.set(k, String(v)));
  return u.toString();
}

function setFormSettings(s) {
  el("operator").value = s.operator || "";
  el("half").value = s.half;
  el("mode").value = s.mode;
  el("cm").value = s.cm;
  el("stretch").value = s.stretch;
  el("ow").value = s.ow;
  el("oh").value = s.oh;
  el("keep").value = s.keep;
  el("rot").value = s.rot;
  el("flip").value = s.flip;
  el("showHud").value = s.showHud;
}

function getFormSettings() {
  return {
    operator: (el("operator").value || "").trim(),
    half: el("half").value,
    mode: el("mode").value,
    cm: el("cm").value,
    stretch: el("stretch").value,
    ow: Number(el("ow").value || defaults.ow),
    oh: Number(el("oh").value || defaults.oh),
    keep: el("keep").value,
    rot: el("rot").value,
    flip: el("flip").value,
    showHud: el("showHud").value,
  };
}

function updateOperatorLabel(s) {
  const name = (s.operator || "").trim();
  el("operatorLabel").textContent = name.length ? name : "—";
}

function applyStream(s) {
  const params = {
    half: s.half,
    mode: s.mode,
    cm: s.cm,
    stretch: s.stretch,
    ow: s.ow,
    oh: s.oh,
    keep: s.keep,
    rot: s.rot,
    flip: s.flip,
    _t: Date.now(),
  };
  el("mjpg").src = "/api/video.mjpg?" + qs(params);
}

async function refreshStatsAndHud(s) {
  try {
    const r = await fetch("/api/stats", { cache: "no-store" });
    const j = await r.json();

    if (s.showHud === "1") {
      el("hud").style.display = "block";
      el("hud").textContent =
        `min: ${j.min}\nmax: ${j.max}\nmean: ${Number(j.mean).toFixed(2)}\n` +
        `${j.mode}/${j.cm} half=${j.half}\n` +
        `out: ${j.out_shape?.[0]}x${j.out_shape?.[1]}`;
    } else {
      el("hud").style.display = "none";
    }
  } catch {
    /* ignore */
  }
}

async function doSnapshot(s) {
  el("statusText").textContent = "Capturing...";
  try {
    const payload = {
      half: s.half,
      mode: s.mode,
      cm: s.cm,
      stretch: s.stretch,
      ow: s.ow,
      oh: s.oh,
      keep: s.keep,
      rot: s.rot,
      flip: s.flip,
      name: s.operator || "",
    };

    const r = await fetch("/api/capture_temp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "snapshot failed");

    // 原本的状态显示保留
    el("statusText").textContent = "Saved";
    el("lastShot").textContent = `#${j.id}`;

    // ✅ 新增：跳转到处理页（定格到刚拍的这张）
    // 需要你新增 frontend/pages/process/index.html 等文件
    window.location.href = `../process/index.html?token=${encodeURIComponent(j.token)}`;
  } catch (e) {
    el("statusText").textContent = "Error";
    console.error(e);
    setTimeout(() => (el("statusText").textContent = "Ready"), 1200);
  }
}

function setupDrawer() {
  const drawer = el("settingsDrawer");
  el("btnSettings").onclick = () => drawer.classList.toggle("hidden");
  el("btnCloseSettings").onclick = () => drawer.classList.add("hidden");
  el("mjpg").onclick = () => drawer.classList.add("hidden");
}

function setupAutoSaveAndApply() {
  const idsChange = ["half","mode","cm","stretch","ow","oh","keep","rot","flip","showHud"];
  idsChange.forEach((id) => {
    el(id).addEventListener("change", () => {
      const s = getFormSettings();
      saveSettings(s);
      applyStream(s);
      refreshStatsAndHud(s);
      updateOperatorLabel(s);
    });
  });

  el("operator").addEventListener("input", () => {
    const s = getFormSettings();
    saveSettings(s);
    updateOperatorLabel(s);
  });
}

function startHudLoop() {
  setInterval(() => {
    const s = getFormSettings();
    refreshStatsAndHud(s);
  }, 800);
}

window.addEventListener("DOMContentLoaded", () => {
  const s = loadSettings();
  setFormSettings(s);
  updateOperatorLabel(s);

  setupDrawer();
  setupAutoSaveAndApply();

  applyStream(s);
  refreshStatsAndHud(s);
  startHudLoop();

  el("btnShutter").onclick = () => doSnapshot(getFormSettings());
});
