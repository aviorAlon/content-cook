// 回收站（后端 routers/trash.py）：各模块「删除」都是归档到 .webui_deleted/<模块>/，这里列出、恢复、彻底删除。
// 以前只有短剧在侧栏有「已删除的项目」，其它模块的确认框只能教人「手动搬回来」——归档里文件被改了名，照做是找不回来的。
// 这里不 import router.js（router.js 引了回收站页，会成环），各模块的项目地址按各自的路由规则就地拼。
import { get, post, del } from './api.js'
import { loadEpisodes } from './store.js'
import { loadBooks } from './picturebook.js'
import { IMAGESET_MODULES, loadSets, setRoute } from './imageset.js'
import { clipRoute, loadClips } from './clip.js'
import { loadMusics, musicRoute } from './music.js'
import { loadPodcasts, podcastRoute } from './podcast.js'
import { loadScripts, scriptRoute } from './scriptlab.js'

export const TRASH_API = '/api/deleted'

// 归档目录用的是后端模块名（短剧是 storyvideo），界面模块名是 drama；icon / 名字跟侧栏一致。
// reload：恢复后刷新这个模块的侧栏列表；route：恢复后跳到的项目页
export const TRASH_MODULES = {
  storyvideo: { ui: 'drama', label: '短剧', icon: '🎬', reload: () => loadEpisodes(), route: (id) => `/drama/p/${id}/overview` },
  picturebook: { ui: 'picturebook', label: '绘本', icon: '📖', reload: () => loadBooks(), route: (id) => `/picturebook/b/${id}/overview` },
  ...Object.fromEntries(Object.values(IMAGESET_MODULES).map(m => [m.id, {
    ui: m.id, label: m.name, icon: m.icon, reload: () => loadSets(m.id), route: (id) => setRoute(m.id, id),
  }])),
  clip: { ui: 'clip', label: '单条视频', icon: '🎞', reload: () => loadClips(), route: (id) => clipRoute(id) },
  music: { ui: 'music', label: '配乐', icon: '🎵', reload: () => loadMusics(), route: (id) => musicRoute(id) },
  podcast: { ui: 'podcast', label: 'AI 播客', icon: '🎙', reload: () => loadPodcasts(), route: (id) => podcastRoute(id) },
  script: { ui: 'script', label: '剧本工作台', icon: '✍️', reload: () => loadScripts(), route: (id) => scriptRoute(id) },
}
// 模块分组的显示顺序：跟侧栏一致，不认识的模块（旧版本留下的归档）排最后
export const TRASH_ORDER = Object.keys(TRASH_MODULES)

// 界面模块名（drama）也认：侧栏短剧分组里的入口带的是 ?module=storyvideo，手敲 ?module=drama 也要能用
export function trashModuleKey(m) {
  const key = String(m || '')
  if (!key) return ''
  if (TRASH_MODULES[key]) return key
  const hit = Object.entries(TRASH_MODULES).find(([, v]) => v.ui === key)
  return hit ? hit[0] : key
}

export const moduleMeta = (m) => TRASH_MODULES[m] || { ui: m, label: m, icon: '🗂', reload: null, route: null }

const entryUrl = (x) => `${TRASH_API}/${encodeURIComponent(x.module)}/${encodeURIComponent(x.entry)}`

export async function fetchTrash(module = '') {
  const rows = await get(module ? `${TRASH_API}?module=${encodeURIComponent(module)}` : TRASH_API)
  return Array.isArray(rows) ? rows : []
}

// 按模块分组（组内新的在前，后端已排好）
export function groupTrash(rows) {
  const groups = new Map()
  for (const x of rows || []) {
    if (!groups.has(x.module)) groups.set(x.module, [])
    groups.get(x.module).push(x)
  }
  const rank = (m) => { const i = TRASH_ORDER.indexOf(m); return i < 0 ? TRASH_ORDER.length : i }
  return [...groups.entries()].sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
    .map(([module, items]) => ({ module, items, bytes: items.reduce((s, x) => s + (Number(x.bytes) || 0), 0) }))
}

// 恢复：放回原位后刷新那个模块的侧栏列表；返回后端结果（id / restored / warning）
export async function restoreEntry(x) {
  const r = await post(`${entryUrl(x)}/restore`)
  try { await moduleMeta(x.module).reload?.() } catch { /* 列表刷新失败不影响恢复本身 */ }
  return r
}

export const purgeEntry = (x) => del(entryUrl(x))

export function projectRoute(module, id) {
  const route = moduleMeta(module).route
  return route && id ? route(id) : null
}

export function fmtBytes(n) {
  const v = Number(n || 0)
  if (v < 1024) return `${v} B`
  if (v < 1024 * 1024) return `${Math.round(v / 1024)} KB`
  if (v < 1024 * 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} MB`
  return `${(v / 1024 / 1024 / 1024).toFixed(2)} GB`
}

// 删除时间：后端给 UTC（manifest 的 deleted_at，老归档从目录名 20260922T170403Z 推出来），显示成本地时间
export function fmtDeletedAt(x) {
  let d = x?.deleted_at ? new Date(x.deleted_at) : null
  if (!d || Number.isNaN(d.getTime())) {
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/.exec(String(x?.archived_at || ''))
    if (!m) return String(x?.archived_at || '')
    d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]))
  }
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
