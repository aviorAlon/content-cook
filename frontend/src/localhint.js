// 本地模型提示（DESIGN §45「推荐而不强制」）：问一句 GET /api/system/local-hint?role=…——
// 「本机现在有没有能顶这个角色的服务」。有就在角色没配好的地方、花钱之前多**一行**；
// 没探到 / 这个角色已经是本机 / 用户点过「不再提示」，就什么都不显示。
//
// 三条纪律：
// 1. **从不拦路**：拿不到答案（后端旧版本、服务在重启）就是没有提示，绝不因此拦下任何操作；
// 2. **不重复问**：每个角色一次会话只问一次（后端自己还有一分钟缓存），并发调用共用同一个请求；
// 3. **关掉就不再回来**：「不再提示」写进设置文件（服务端），换浏览器、重启程序都还在。

import { reactive } from 'vue'
import { get, post } from './api.js'

// role → { role, available, current_is_local, suggestion, dismissed, scanned } 或 null（问不到）
export const localHints = reactive({})

const asked = {}      // role → true：问过了（成功或失败都算，失败不重试，免得每次开弹窗都打一遍）
const inflight = {}   // role → Promise：并发调用共用

export async function loadLocalHint(role) {
  if (!role) return null
  if (asked[role]) return localHints[role] || null
  if (!inflight[role]) {
    inflight[role] = (async () => {
      try {
        const r = await get(`/api/system/local-hint?role=${encodeURIComponent(role)}`)
        localHints[role] = r && typeof r === 'object' ? r : null
      } catch {
        localHints[role] = null      // 问不到 = 没有提示
      }
      asked[role] = true
      delete inflight[role]
      return localHints[role] || null
    })()
  }
  return inflight[role]
}

// 花钱之前那一行：只有真探到了在跑的服务才有（文案会点名字，不能靠猜）
export function localSuggestion(role) {
  return (role && localHints[role]?.suggestion) || null
}

// 角色还没配好时那一行：探到了在跑的服务，或者用户从来没扫过本机（这时文案只说「有显卡的话可以本地跑」，
// 不声称本机有什么在跑）。已经是本机来源、或关过提示的一律不显示。
export function localTipVisible(role) {
  const h = role ? localHints[role] : null
  if (!h || h.dismissed || h.current_is_local) return false
  return (h.available?.length || 0) > 0 || !h.scanned
}

export async function dismissLocalHint(role) {
  const body = role ? { role } : { role: 'all' }
  if (localHints[role]) { localHints[role].suggestion = null; localHints[role].dismissed = true }
  try { await post('/api/system/local-hint/dismiss', body) } catch { /* 关不上也不再骚扰这一次 */ }
}

// 设置页应用了本机来源之后：下次再问一遍（这个角色已经是本地了，提示该自己消失）
export function forgetLocalHints() {
  for (const key of Object.keys(localHints)) delete localHints[key]
  for (const key of Object.keys(asked)) delete asked[key]
}

export default { localHints, loadLocalHint, localSuggestion, localTipVisible, dismissLocalHint, forgetLocalHints }
