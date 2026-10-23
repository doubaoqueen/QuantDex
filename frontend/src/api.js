async function req(path, opts) {
  const r = await fetch(path, opts)
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`)
  return r.json()
}

export const api = {
  get: (p) => req(p),
  post: (p, body) => req(p, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {})
  })
}

export const fmt = {
  n: (v, d = 2) => v == null || Number.isNaN(v) ? '-' :
    Number(v).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }),
  pct: (v, d = 2) => v == null || Number.isNaN(v) ? '-' :
    (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%',
  signCls: (v) => v > 0 ? 'text-red-600' : (v < 0 ? 'text-emerald-600' : 'text-gray-500')
}
