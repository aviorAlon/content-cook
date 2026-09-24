// 任务中心（DESIGN §38）与工作台首页共用的胶水：任务记录 → 人话（种类 / 状态 / 用时 / 失败原因）、
// 项目地址、相对时间、「最近」列表。后台任务记录的形状见 src/webui/tasks.py：
// {task_id, kind, episode_id, module, status, created_at, started_at, finished_at, error, result, params}
import { get } from './api.js'
import { t } from './i18n.js'

export const TASKS_API = '/api/tasks'
export const RECENT_API = '/api/recent'

// 种类 → 人话。键是 tasks.start(kind, …) 里的字符串（各 routers 的 KINDS 表 + generate.py 的 WATCH_KIND）。
// 认不出的种类原样显示 kind，不猜。
export const KIND_LABELS = {
  'generate-watch': '等服务商返回',
  'asr-qc': '台词识别',
  'post': '后期合成',
  'poster': '海报',
  'export': '导出交付包',
  'compare': '多模型对比',
  'install-asr': '安装语音识别',
  'asr-model': '下载识别模型',
  'voice-clone': '音色克隆',
  'clip-frame': '单条视频首帧',
  'clip-generate': '出片',
  'set-generate': '出图',
  'set-post': '图片后处理',
  'music-generate': '配乐',
  'pc-draft': '播客起草',
  'pc-audio': '播客配音',
  'pc-export': '播客导出',
  'pb-draft': '绘本起草',
  'pb-pages': '绘本插画',
  'pb-narration': '绘本朗读',
  'pb-assemble': '绘本装配',
  'genre-review': '按题材审稿',
  'craft-review': '工艺审稿',
  'pc-review': '播客审稿',
  'sc-draft': '台词起草',
  'sc-review': '台词审稿',
}

export const kindLabel = (kind) => (KIND_LABELS[kind] ? t(KIND_LABELS[kind]) : String(kind || '—'))

export const STATUS_LABELS = { pending: '排队中', running: '在跑', succeeded: '成功', failed: '失败' }
export const statusLabel = (status) => (STATUS_LABELS[status] ? t(STATUS_LABELS[status]) : String(status || '—'))
export const isActive = (task) => task?.status === 'pending' || task?.status === 'running'
export const activeCount = (rows) => (rows || []).filter(isActive).length

// 状态过滤：界面上只给三档（在跑 = 排队中 + 在跑）
export const STATUS_FILTERS = [
  { id: '', label: '全部' },
  { id: 'active', label: '在跑' },
  { id: 'succeeded', label: '成功' },
  { id: 'failed', label: '失败' },
]
export function matchesStatus(task, filter) {
  if (!filter) return true
  if (filter === 'active') return isActive(task)
  return task.status === filter
}

// 项目页地址：跟 router.js 与后端 routers/recent.py 的 PROJECT_ROUTES 一一对应
export const PROJECT_ROUTES = {
  drama: (id) => `/drama/p/${id}/overview`,
  picturebook: (id) => `/picturebook/b/${id}/overview`,
  sticker: (id) => `/sticker/s/${id}/items`,
  portrait: (id) => `/portrait/s/${id}/items`,
  image: (id) => `/image/s/${id}/items`,
  clip: (id) => `/clip/c/${id}/write`,
  music: (id) => `/music/m/${id}/generate`,
  podcast: (id) => `/podcast/p/${id}/script`,
  script: (id) => `/script/p/${id}/write`,
}

// 这些任务的 episode_id 不是项目 id（安装依赖、下载模型），别拼出一个打不开的项目地址
const NON_PROJECT_KINDS = new Set(['install-asr', 'asr-model'])

// 任务记录 → {id, url}：多模型对比的 episode_id 是「剧集:镜号」，音色克隆的是「voice:<音色 id>」
export function taskTarget(task) {
  const raw = String(task?.episode_id || '')
  if (!raw || raw === '-' || NON_PROJECT_KINDS.has(task?.kind)) return null
  if (raw.startsWith('voice:')) {
    const voiceId = raw.slice('voice:'.length)
    return voiceId ? { id: voiceId, url: `/voices/${voiceId}`, kind: 'voice' } : null
  }
  const id = raw.includes(':') ? raw.split(':')[0] : raw
  const route = PROJECT_ROUTES[task?.module || 'drama']
  return { id, url: route ? route(id) : null, kind: 'project' }
}

// ---- 时间

const MINUTE = 60, HOUR = 3600, DAY = 86400

// 后端的时间戳是秒（time.time()）；这里统一成毫秒
export const toMs = (ts) => (ts ? (Number(ts) < 1e12 ? Number(ts) * 1000 : Number(ts)) : 0)

export function relTime(ts, now = Date.now()) {
  const ms = toMs(ts)
  if (!ms) return '—'
  const sec = Math.max(0, Math.round((now - ms) / 1000))
  if (sec < MINUTE) return t('刚刚')
  if (sec < HOUR) return t('{n} 分钟前', { n: Math.floor(sec / MINUTE) })
  if (sec < DAY) return t('{n} 小时前', { n: Math.floor(sec / HOUR) })
  if (sec < 2 * DAY) return t('昨天')
  if (sec < 30 * DAY) return t('{n} 天前', { n: Math.floor(sec / DAY) })
  return new Date(ms).toLocaleDateString()
}

export function absTime(ts) {
  const ms = toMs(ts)
  return ms ? new Date(ms).toLocaleString() : '—'
}

export function durationText(task, now = Date.now()) {
  const start = toMs(task?.started_at || task?.created_at)
  if (!start) return '—'
  const end = toMs(task?.finished_at) || (isActive(task) ? now : toMs(task?.updated_at))
  const sec = Math.max(0, Math.round(((end || now) - start) / 1000))
  if (sec < MINUTE) return t('{n} 秒', { n: sec })
  const min = Math.floor(sec / MINUTE)
  if (min < MINUTE) return t('{m} 分 {s} 秒', { m: min, s: sec % MINUTE })
  return t('{h} 小时 {m} 分', { h: Math.floor(min / MINUTE), m: min % MINUTE })
}

// 失败原因 → 一行人话：老记录（和新记录里的程序 bug，如「KeyError: 'shots'」）是「类型名: 原因」这种形状，
// 界面只显示冒号后面那句的第一行，原文（含类型名与多行）留给「展开」。新记录的中文人话 error 本身不带类型名，
// 类型名另存在 error_type。
export function failureLine(task) {
  const raw = String(task?.error || '').trim()
  if (!raw) return ''
  const first = raw.split('\n')[0].trim()
  const m = first.match(/^([A-Za-z_][\w.]*(?:Error|Exception|Busy)[\w.]*):\s*(.+)$/)
  return (m ? m[2] : first).trim()
}

// 「展开」看的原文：把异常类型名拼回来。后端对中文人话把类型名另存在 error_type、error 里只放人话（各模块弹窗
// 原样展示 error），光显示 error 的话「展开」就看不到是哪一类异常了。老记录没有 error_type、以及后端照旧把类型名
// 留在 error 里的（程序 bug），原样显示；消息为空时后端拿类型名兜底，别拼成「X: X」。
export function failureDetail(task) {
  const raw = String(task?.error || '')
  const type = String(task?.error_type || '').trim()
  if (!type || raw.trim() === type || raw.startsWith(`${type}:`)) return raw
  return `${type}: ${raw}`
}

// ---- 拉数据（都不花钱：只读服务端已经落盘的记录）

export async function fetchTasks({ module = '' } = {}) {
  const rows = await get(module ? `${TASKS_API}?module=${encodeURIComponent(module)}` : TASKS_API)
  return Array.isArray(rows) ? rows : []
}

export async function fetchRecent(limit = 12) {
  try {
    const rows = await get(`${RECENT_API}?limit=${limit}`)
    return Array.isArray(rows) ? rows : (rows?.items || [])
  } catch { return [] }
}
