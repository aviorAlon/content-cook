// AI 播客模块（docs/podcast-spec.md §5）的前端胶水：接口前缀、列表与当前播客的加载、后台任务轮询、产物地址、
// 形式 / 环节种类 / cue 的常量表、进度形状。视图只认这里的形状；后端列表 / 详情的字段名取值处都留了兜底。
import { get, post, del, pollTask } from './api.js'
import { store } from './store.js'
import { notice, toast } from './ui.js'
import { t } from './i18n.js'

export const PODCAST_API = '/api/podcast'
export const pc = (id) => `${PODCAST_API}/${encodeURIComponent(id)}`
export const podcastRoute = (id, sub = 'script') => `/podcast/p/${id}/${sub}`
export const audioUrl = (id, name) => (name ? `${pc(id)}/artifact/audio/${encodeURIComponent(name)}` : null)
export const textUrl = (id, name) => (name ? `${pc(id)}/artifact/text/${encodeURIComponent(name)}` : null)
export const exportUrl = (id, name) => (name ? `${pc(id)}/export/${encodeURIComponent(name)}` : null)

export const EXAMPLE_NAME = 'short_video_demo'
export const HOSTS_MIN = 1
export const HOSTS_MAX = 4
export const MINUTES_MIN = 1
export const MINUTES_MAX = 90

// 常量表：值保持中文（同时是字典 key），渲染时 t() 翻译。formats 接口拉不到时用这份兜底。
export const FORMATS = [
  { id: 'solo', name: '单人独白', hosts: [1, 1] },
  { id: 'duo', name: '双人对谈', hosts: [2, 2] },
  { id: 'interview', name: '访谈', hosts: [2, 4] },
  { id: 'roundtable', name: '圆桌', hosts: [3, 4] },
]
export const SEGMENT_KINDS = { cold_open: '冷开场', intro: '开场', topic: '话题', qa: '听友问答', outro: '收尾' }
export const LINE_KINDS = { talk: '普通台词', question: '提问', answer: '回答' }
export const HOST_ROLES = { host: '主播', cohost: '搭档', guest: '嘉宾' }
export const CUES = ['笑', '轻笑', '停顿', '打断', '叹气', '加快', '放慢', '低声', '强调']
export const MUSIC_SLOTS = { intro: '片头', bed: '垫乐', outro: '片尾' }

// brief 的空表单（形状对齐项目文件 §1）
export function emptyBrief() {
  return { topic: '', angle: '', audience: '', takeaways: [], materials: '', tone: '', avoid: [] }
}
export function emptyHost(i = 0, role = 'host') {
  return { id: `h${i + 1}`, name: '', role, persona: '', stance: '', voice: '' }
}
export function emptySegment(id = 'G01', kind = 'topic') {
  return { id, kind, title: '', goal: '', beats: [], questions: [], lines: [] }
}
export function emptyLine(who = '') {
  return { who, text: '', cue: '', kind: 'talk' }
}
// 环节编号：G01… 按已有的最大号往后排，不重复
export function nextSegmentId(segments) {
  let max = 0
  for (const s of segments || []) { const m = /^G(\d+)$/.exec(String(s?.id || '')); if (m) max = Math.max(max, Number(m[1])) }
  return `G${String(max + 1).padStart(2, '0')}`
}

// 「顿号 / 逗号分隔的一行」↔ 数组：chips 输入框两边都要用
export function splitList(text) {
  return String(text || '').split(/[、,，/]/).map(s => s.trim()).filter(Boolean)
}

export function fmtClock(sec) {
  const n = Math.max(0, Math.round(Number(sec || 0)))
  return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`
}

// 交付包大小：KB / MB 一位小数；0 或没有 → '—'
export function fmtBytes(bytes) {
  const n = Number(bytes || 0)
  if (!n) return '—'
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

// 交付包时间：接口给 ISO（带时区），列表里只看到分钟
export function fmtWhen(iso) {
  const s = String(iso || '')
  return s ? s.replace('T', ' ').slice(0, 16) : ''
}

// ---- 形式 / 种类 / cue（GET /api/podcast/formats），全局只拉一次
let formatsCache = null
export async function loadFormats({ force = false } = {}) {
  if (!force && formatsCache) return formatsCache
  let r
  try { r = await get(`${PODCAST_API}/formats`) } catch { r = null }
  formatsCache = {
    formats: (r?.formats?.length ? r.formats : FORMATS).map(f => ({ ...f, hosts: f.hosts || [HOSTS_MIN, HOSTS_MAX] })),
    // 后端给的是 [{id, name}]，页面只认 id（中文名走 SEGMENT_KINDS 的 $t）
    kinds: (r?.kinds?.length ? r.kinds : Object.keys(SEGMENT_KINDS)).map(k => (typeof k === 'string' ? k : k.id)),
    cues: r?.cues?.length ? r.cues : CUES,
    defaults: { target_minutes: 12, pause: { line: 0.35, segment: 1.2 }, music: { bed_gain: 0.18, duck: 0.45 }, ...(r?.defaults || {}) },
    limits: { target_minutes: [MINUTES_MIN, MINUTES_MAX], hosts: [HOSTS_MIN, HOSTS_MAX], ...(r?.limits || {}) },
    examples: r?.examples || [],
    craft: r?.craft || [],
  }
  return formatsCache
}
export const cachedFormats = () => formatsCache?.formats || FORMATS
export function findFormat(id) {
  return cachedFormats().find(f => f.id === id) || FORMATS.find(f => f.id === id) || null
}
export const formatName = (id) => { const f = findFormat(id); return f ? t(f.name) : String(id || '') }

// ---- 列表 / 当前播客（store.podcasts / store.podcast / store.podcastDetail）
export async function loadPodcasts() {
  let rows
  try { rows = await get(PODCAST_API) } catch { rows = [] }
  store.podcasts = Array.isArray(rows) ? rows : (rows?.items || [])
  store.podcastsLoaded = true
  return store.podcasts
}

// GET /api/podcast/{id} 是 detail 外壳（§5）：{id, spec, estimate, warnings, problems, hosts, audio, texts, draft, busy, limits}
export async function loadPodcast(id, { keep = false } = {}) {
  if (!keep) { store.podcast = null; store.podcastDetail = null }
  store.podcastId = id
  const detail = await get(pc(id))
  if (store.podcastId !== id) return   // 用户已经切走
  store.podcastDetail = detail
  store.podcast = detail?.spec ? { ...detail.spec, id: detail.spec.id || detail.id || id } : detail
}

export async function refreshPodcast() {
  if (!store.podcastId) return
  await loadPodcast(store.podcastId, { keep: true })
  loadPodcasts().catch(() => {})
}

export function clearPodcast() { store.podcastId = null; store.podcast = null; store.podcastDetail = null }

// ---- 交付包（GET /api/podcast/{id}/export → {id, packages:[{name, size, created, files, bytes, mixed, lines, url}], export_dir}）
// 每行都补上 url（后端给了就用后端的）；接口挂了返回 null，卡片退回 detail.exports
export function normalizeExports(id, rows) {
  const list = Array.isArray(rows) ? rows : (rows?.packages || rows?.exports || [])
  return list.filter(e => e && e.name).map(e => ({ ...e, url: e.url || exportUrl(id, e.name) }))
}
export async function loadExports(id) {
  let r
  try { r = await get(`${pc(id)}/export`) } catch { return null }
  return normalizeExports(id, r)
}

export async function archivePodcast(id) {
  await del(pc(id))
  await loadPodcasts()
  if (store.podcastId === id) clearPodcast()
}

export async function createFromExample(name = EXAMPLE_NAME) {
  const r = await post(`${PODCAST_API}/examples/create`, { name })
  await loadPodcasts()
  return r
}

// ---- 进度：列表行 / 详情 → {drafted, audio, mixed, minutes, hosts, segments, lines}
export function progressOf(src) {
  const p = src?.progress || {}
  const spec = src?.spec || src || {}
  const segments = Array.isArray(spec.segments) ? spec.segments : null
  const lineCount = segments ? segments.reduce((n, s) => n + (s.lines?.length || 0), 0) : 0
  const audio = src?.audio || null
  const hosts = src?.hosts ?? spec.hosts
  return {
    drafted: !!(p.drafted ?? (lineCount > 0)),
    audio: !!(p.audio ?? (audio?.lines?.length > 0)),
    mixed: !!(p.mixed ?? !!audio?.mix_url),
    minutes: Number(src?.minutes ?? src?.estimate?.minutes ?? 0),
    hosts: Number(Array.isArray(hosts) ? hosts.length : (hosts ?? 0)),
    segments: Number(src?.segments_count ?? (segments ? segments.length : src?.segments ?? 0)),
    lines: Number(src?.lines_count ?? (segments ? lineCount : src?.lines ?? 0)),
  }
}
export function progressText(src) {
  const p = progressOf(src)
  const stage = p.mixed ? t('已混音') : p.audio ? t('已配音') : p.drafted ? t('已起草') : t('还没起草')
  return `${stage} · ${t('{n} 位主播', { n: p.hosts })} · ${t('预计 {m} 分钟', { m: p.minutes })}`
}

// 项目文件的可保存形状：去掉后端附带的目录字段
export function specBody(spec = store.podcast) {
  const { output_root: _o, project_root: _r, ...rest } = spec || {}
  return rest
}

// ---- 后台任务：起 → pollTask → 刷新。返回 task（失败时已弹过 notice）；起不来返回 null
export async function runPodcastTask(url, body, label) {
  let created
  try { created = await post(url, body || {}) } catch (e) { await notice(e.message); return null }
  const taskId = created?.task_id
  if (!taskId) { await refreshPodcast().catch(() => {}); return created }
  if (created.cost_note) toast(String(created.cost_note), { kind: 'warn', ttl: 6000 })
  store.gen = { text: `${label}…` }
  try {
    const task = await pollTask(taskId, (_t, sec) => { store.gen = { text: t('{label}…已经 {s} 秒（可以切走）', { label, s: sec }) } })
    if (task.status === 'failed') { await notice(t('{label}失败：', { label }) + '\n\n' + (task.error || '')); return task }
    const r = task.result || {}
    const failed = Array.isArray(r.failed) ? r.failed.length : 0
    const warn = failed ? t('{label}完成：成功 {a}，失败 {c}', { label, a: r.generated?.length ?? r.synthesized?.length ?? 0, c: failed })
      : (r.mix_error ? t('{label}完成，但没混音：{msg}', { label, msg: r.mix_error }) : '')
    toast(warn || t('{label}完成', { label }), warn ? { kind: 'warn', ttl: 6000 } : {})
    return task
  } catch (e) { await notice(e.message); return null } finally { store.gen = null; await refreshPodcast().catch(() => {}) }
}
