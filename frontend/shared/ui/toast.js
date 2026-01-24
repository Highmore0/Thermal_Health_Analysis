let root;

function ensureRoot(){
  if (root) return root;
  root = document.createElement("div");
  root.style.position = "fixed";
  root.style.right = "16px";
  root.style.bottom = "16px";
  root.style.display = "flex";
  root.style.flexDirection = "column";
  root.style.gap = "10px";
  root.style.zIndex = "9999";
  document.body.appendChild(root);
  return root;
}

export function toast(message, type="info", ms=2500){
  const r = ensureRoot();
  const el = document.createElement("div");
  el.textContent = String(message);

  el.style.padding = "10px 12px";
  el.style.borderRadius = "12px";
  el.style.border = "1px solid #444";
  el.style.background = "#0f0f0f";
  el.style.color = "#ddd";
  el.style.maxWidth = "360px";
  el.style.whiteSpace = "pre-wrap";
  el.style.boxShadow = "0 8px 24px rgba(0,0,0,.35)";
  el.style.fontSize = "13px";

  if (type === "ok") el.style.borderColor = "#2a6";
  if (type === "err") el.style.borderColor = "#a44";
  if (type === "warn") el.style.borderColor = "#aa6";

  r.appendChild(el);
  window.setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 200ms ease";
    window.setTimeout(()=> el.remove(), 220);
  }, ms);
}
