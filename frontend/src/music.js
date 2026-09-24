// 配乐模块（docs/music-spec.md §5）的前端胶水：接口前缀、列表与当前配乐的加载、后台任务轮询、产物地址、
// 预设 brief、投放目标。视图只认这里的形状；后端列表 / 详情的字段名取值处都留了兜底（老形状：详情直接返回项目文件）。
import { get, post, del, pollTask } from './api.js'
import { store } from './store.js'
import { notice, toast } from './ui.js'
import { t } from './i18n.js'
import { MUSIC_SLOTS as PODCAST_SLOTS } from './podcast.js'

export const MUSIC_API = '/api/music'
export const mu = (id) => `${MUSIC_API}/${encodeURIComponent(id)}`
export const musicRoute = (id, sub = 'generate') => `/music/m/${id}/${sub}`
export const musicAudioUrl = (id, name, stamp = 0) =>
  name ? `${mu(id)}/artifact/audio/${encodeURIComponent(name)}${stamp ? `?t=${stamp}` : ''}` : null

export const EXAMPLE_NAME = 'lullaby_demo'
export const COUNT_MIN = 1
export const COUNT_MAX = 5
export const USE_MODULES = [
  { id: 'drama', label: '短剧剧集' },
  { id: 'picturebook', label: '绘本 / 有声书' },
  { id: 'podcast', label: 'AI 播客' },
]
// 播客的三个槽（id → 中文名，表在 podcast.js）：「放进项目」的槽下拉按这个顺序列，默认垫乐
export const PODCAST_SLOT_OPTIONS = Object.entries(PODCAST_SLOTS).map(([id, label]) => ({ id, label }))
export const PODCAST_DEFAULT_SLOT = 'bed'
export const podcastSlotLabel = (slot) => PODCAST_SLOTS[slot] || String(slot || '')

// brief 的空表单（形状对齐项目文件 §2）
export function emptyBrief() {
  return { usage: '', mood: [], genre: '', instruments: [], bpm: null, structure: '', avoid: [] }
}

// 「顿号 / 逗号分隔的一行」↔ 数组：chips 输入框两边都要用
export function splitList(text) {
  return String(text || '').split(/[、,，/]/).map(s => s.trim()).filter(Boolean)
}

// ---- 预设 brief（GET /api/music/briefs），全局只拉一次
let briefsCache = null
export async function loadBriefs({ force = false } = {}) {
  if (!force && briefsCache) return briefsCache
  let r
  try { r = await get(`${MUSIC_API}/briefs`) } catch { r = null }
  briefsCache = {
    briefs: (r?.briefs || []).map(b => ({ ...b, brief: { ...emptyBrief(), ...(b.brief || {}) } })),
    defaults: r?.defaults || { duration: 60, count: 1 },
    limits: r?.limits || { count: [COUNT_MIN, COUNT_MAX], duration: [5, 600], bpm: [30, 240], max_references: 16 },
    examples: r?.examples || [],
  }
  return briefsCache
}
export const cachedBriefs = () => briefsCache?.briefs || []
export function findBrief(id) {
  return cachedBriefs().find(b => b.id === id) || null
}

// ---- 列表 / 当前配乐（store.musics / store.music / store.musicDetail）
export async function loadMusics() {
  let rows
  try { rows = await get(MUSIC_API) } catch { rows = [] }
  store.musics = Array.isArray(rows) ? rows : (rows?.items || [])
  store.musicsLoaded = true
  return store.musics
}

// GET /api/music/{id} 是 detail 外壳：{id, spec, prompt, prompts:{zh,en}, attempts:[{file,url,current,error}], used_in, progress, busy}
export async function loadMusic(id, { keep = false } = {}) {
  if (!keep) { store.music = null; store.musicDetail = null }
  store.musicId = id
  const detail = await get(mu(id))
  if (store.musicId !== id) return   // 用户已经切走
  store.musicDetail = detail
  store.music = detail?.spec ? { ...detail.spec, id: detail.spec.id || detail.id || id } : detail
}

export async function refreshMusic() {
  if (!store.musicId) return
  await loadMusic(store.musicId, { keep: true })
  loadMusics().catch(() => {})
}

export function clearMusic() { store.musicId = null; store.music = null; store.musicDetail = null }

export async function archiveMusic(id) {
  await del(mu(id))
  await loadMusics()
  if (store.musicId === id) clearMusic()
}

export async function createFromExample(name = EXAMPLE_NAME) {
  const r = await post(`${MUSIC_API}/examples/create`, { name })
  await loadMusics()
  return r
}

// ---- 投放目标（GET /api/music/targets）：短剧剧集 + 绘本 + 播客，拍平成一个下拉；播客行带已有文件的槽名 slots
export async function loadTargets() {
  let r
  try { r = await get(`${MUSIC_API}/targets`) } catch { r = null }
  const rows = []
  for (const m of USE_MODULES) {
    for (const row of (r?.[m.id] || [])) {
      rows.push({
        module: m.id, id: row.id, title: row.title || row.id, tracks: Number(row.tracks || 0),
        slots: Array.isArray(row.slots) ? row.slots.map(String) : [], group: t(m.label),
      })
    }
  }
  return rows
}

// slot：短剧是起始秒（数字），播客是槽名（intro / bed / outro，空 = 垫乐），绘本不带
export async function useInProject(id, { module, target, slot = null }) {
  const body = { module, target }
  if (module === 'podcast') body.slot = String(slot || PODCAST_DEFAULT_SLOT)
  else if (slot !== null && slot !== '' && !Number.isNaN(Number(slot))) body.slot = Number(slot)
  return post(`${mu(id)}/use`, body)
}

// ---- 进度：列表行 / 详情 → {done, attempts, ok, used}
export function progressOf(src) {
  const p = src?.progress || src || {}
  const attempts = Array.isArray(src?.attempts) ? src.attempts : null
  const ok = Number(p.ok_attempts ?? (attempts ? attempts.filter(a => !a.error).length : 0))
  const count = Array.isArray(p.attempts) ? p.attempts.length : p.attempts
  return {
    done: !!(p.done ?? src?.current ?? src?.current_url),
    attempts: Number(count ?? attempts?.length ?? 0),
    ok,
    stale: !!p.stale,
    used: Number(p.used ?? (Array.isArray(src?.used_in) ? src.used_in.length : 0)),
  }
}
export function progressText(src) {
  const p = progressOf(src)
  const head = p.done ? t('已采用 1 条') : t('还没出')
  return `${head} · ${t('历次 {n}', { n: p.attempts })}` + (p.used ? ` · ${t('已投放 {n}', { n: p.used })}` : '')
}

// ---- 历次：detail.attempts 优先（带 url），老形状按文件名拼
export function attemptRows(id, detail = store.musicDetail, spec = store.music, stamp = 0) {
  const list = detail?.attempts || spec?.attempts || []
  const current = detail?.spec?.current ?? spec?.current
  return list.map((a, i) => {
    const row = typeof a === 'string' ? { file: a } : { ...a }
    const ok = !!row.file && !row.error
    return {
      ...row, index: i, key: row.file || `x${i}`, ok,
      current: row.current ?? (!!row.file && row.file === current),
      url: row.url || (ok ? musicAudioUrl(id, row.file, stamp) : null),
    }
  })
}

// 项目文件的可保存形状：去掉后端附带的目录字段
export function specBody(spec = store.music) {
  const { output_root: _o, project_root: _r, ...rest } = spec || {}
  return rest
}

// ---- 后台任务：起 → pollTask → 刷新。返回 task（失败时已弹过 notice）；起不来返回 null
export async function runMusicTask(url, body, label) {
  let created
  try { created = await post(url, body || {}) } catch (e) { await notice(e.message); return null }
  const taskId = created?.task_id
  if (!taskId) { await refreshMusic().catch(() => {}); return created }
  if (created.cost_note) toast(String(created.cost_note), { kind: 'warn', ttl: 6000 })
  store.gen = { text: `${label}…` }
  try {
    const task = await pollTask(taskId, (_t, sec) => { store.gen = { text: t('{label}…已经 {s} 秒（可以切走）', { label, s: sec }) } })
    if (task.status === 'failed') { await notice(t('{label}失败：', { label }) + '\n\n' + (task.error || '')); return task }
    const r = task.result || {}
    const summary = r.failed?.length
      ? t('{label}完成：成功 {a}，失败 {c}', { label, a: r.generated?.length ?? 0, c: r.failed.length })
      : (r.skipped?.length && !r.generated?.length ? t('请求没变，跳过了（想重出点「全部重出」）') : t('{label}完成', { label }))
    toast(summary, r.failed?.length ? { kind: 'warn', ttl: 6000 } : {})
    return task
  } catch (e) { await notice(e.message); return null } finally { store.gen = null; await refreshMusic().catch(() => {}) }
}
