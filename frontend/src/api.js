// D-105：会话过期（401）时自动整页刷新一次——服务器对未认证的文档请求返回登录页，
// 用户登录后 location.reload() 回到 SPA。此前 SPA 常驻标签页在 cookie 7 天 TTL 到期后
// 只会显示"接口异常/401"，没有任何回登录的路径（2026-11-25 更新行情 401 实录）。
sessionStorage.removeItem('qd_reloaded')

async function req(path, opts) {
  const r = await fetch(path, opts)
  if (r.status === 401) {
    if (!sessionStorage.getItem('qd_reloaded')) {
      sessionStorage.setItem('qd_reloaded', '1')
      location.reload()
      return new Promise(() => {})   // 页面即将整页替换，挂起调用方
    }
    throw new Error('登录已过期，请刷新页面重新登录')
  }
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
