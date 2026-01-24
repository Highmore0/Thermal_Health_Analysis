// 统一 API 前缀：你后端改成 /api 后，这里就不用动了
const API_BASE = "/api";

export function apiUrl(path){
  if (!path.startsWith("/")) path = "/" + path;
  return API_BASE + path;
}

async function readJsonSafely(resp){
  const ct = resp.headers.get("content-type") || "";
  if (ct.includes("application/json")) return await resp.json();
  const text = await resp.text();
  // 尝试把非 JSON 的错误也包装一下
  return { raw: text };
}

export async function apiGetJson(path){
  const r = await fetch(apiUrl(path), { method: "GET" });
  const j = await readJsonSafely(r);
  if (!r.ok) {
    const msg = j?.error || j?.message || j?.raw || `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return j;
}

export async function apiPostJson(path, body){
  const r = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {})
  });
  const j = await readJsonSafely(r);
  if (!r.ok) {
    const msg = j?.error || j?.message || j?.raw || `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return j;
}
