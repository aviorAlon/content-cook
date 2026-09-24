// 本地模型（DESIGN §45）：地址是不是本机 / 本地网段。跟后端 genkit/local.py::is_local_url 同一套判据——
// localhost、127.0.0.0/8、::1、私有网段；域名一律不算（http://127.0.0.1.evil.com 这种要挡掉）。
// 前端只用它决定「key 输入框要不要显示『本机服务通常不需要』」，真正的拦截在后端。

const V4 = [
  [[127, 0, 0, 0], 8],
  [[10, 0, 0, 0], 8],
  [[172, 16, 0, 0], 12],
  [[192, 168, 0, 0], 16],
  [[169, 254, 0, 0], 16],
]

function v4InNet(parts, [base, bits]) {
  const n = (a) => ((a[0] << 24) >>> 0) + (a[1] << 16) + (a[2] << 8) + a[3]
  const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0
  return ((n(parts) & mask) >>> 0) === ((n(base) & mask) >>> 0)
}

export function isLocalUrl(url) {
  const text = String(url || '').trim()
  if (!text) return false
  let parsed
  try { parsed = new URL(text.includes('://') ? text : 'http://' + text) } catch { return false }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false
  let host = parsed.hostname.toLowerCase().replace(/\.$/, '')
  if (host === 'localhost') return true
  if (host.startsWith('[') && host.endsWith(']')) host = host.slice(1, -1)
  if (host.includes(':')) {                     // IPv6：::1 与 fc00::/7、fe80::/10
    if (host === '::1') return true
    const head = host.split(':')[0]
    return /^f[cd]/.test(head) || /^fe[89ab]/.test(head)
  }
  const parts = host.split('.')
  if (parts.length !== 4 || !parts.every((p) => /^\d{1,3}$/.test(p) && Number(p) <= 255)) return false
  const nums = parts.map(Number)
  return V4.some((net) => v4InNet(nums, net))
}

export default { isLocalUrl }
