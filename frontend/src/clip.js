// 单条视频模块（docs/clip-spec.md §6）的前端胶水：接口前缀、列表与当前片子的加载、后台任务轮询、产物地址、
// 三段式编辑器要的空白结构与时间条计算。视图只认这里的形状；后端详情的字段名取值处都留了兜底。
import { get, post, del, pollTask } from './api.js'
import { store } from './store.js'
import { notice, toast } from './ui.js'
import { t } from './i18n.js'

export const CLIP_API = '/api/clip'
export const clipUrl = (id) => `${CLIP_API}/${encodeURIComponent(id)}`
export const clipRoute = (id, sub = 'write') => `/clip/c/${id}/${sub}`
const stampQ = (url, stamp) => (stamp ? `${url.includes('?') ? '&' : '?'}t=${stamp}` : '')
const artifact = (id, kind, name, stamp) => {
  const url = `${clipUrl(id)}/artifact/${kind}/${encodeURIComponent(name)}`
  return url + stampQ(url, stamp)
}
export const clipArtifact = {
  video: (id, name, stamp = 0) => artifact(id, 'video', name, stamp),
  frame: (id, name, stamp = 0) => artifact(id, 'frame', name, stamp),
}

// 新建页要的选项（契约 §1）：画幅、常见时长、首帧模式、提示词语言、方言
export const ASPECT_RATIOS = ['16:9', '9:16', '1:1', '4:3', '3:4', '21:9']
export const DURATIONS = [5, 10, 15, 20, 30]
export const FIRST_FRAME_MODES = [
  { id: 'none', label: '不用首帧（文生视频）' },
  { id: 'upload', label: '用我上传的图' },
  { id: 'generate', label: '先出一张首帧再生视频' },
]
export const PROMPT_LANGUAGES = [{ id: 'zh', label: '中文' }, { id: 'en', label: 'English' }]
export const DIALECTS = [
  { id: 'auto', label: '自动（按视频模型猜）' },
  { id: 'generic', label: '通用写法' },
  { id: 'nano_banana', label: 'Nano Banana（不写数字镜头参数，最后一行单写画幅）' },
]
export const EXAMPLE_NAME = 'city_morning'

// 空白项目：三段都在，用户填哪段是哪段（契约 §1）
export function blankClip(title = '') {
  return {
    title,
    prompt_language: 'zh',
    style: 'custom',
    style_anchor: '',
    dialect: 'auto',
    global: { look: '', palette: [], light: '', aspect_ratio: '16:9', safe_area: '', text_policy: '', audio: '' },
    beats: [],
    continuity: { one_take: true, transitions: '', last_frame: '', forbid: [] },
    video_params: { model: '@video', duration: 15, resolution: '1080p', generate_audio: false },
    image_params: { model: '@image', image_size: '2K' },
    first_frame: { mode: 'none', asset: null, prompt: '' },
    attempts: [],
    current: null,
  }
}

export function newBeat(beats, seconds = 3) {
  const n = beats.length + 1
  const start = beats.length ? Number(beats[beats.length - 1].end || 0) : 0
  return { id: `B${String(n).padStart(2, '0')}`, start, end: round(start + seconds), shot: '', picture: '', sound: '', text: '' }
}
export const round = (v) => Math.round(Number(v || 0) * 100) / 100
export const fmtSecs = (v) => String(round(v)).replace(/\.0+$/, '')

// 重排后重新编号（B01、B02…）并按 start 排序——后端也会排，这里先让界面所见即所得
export function renumber(beats) {
  return [...beats]
    .sort((a, b) => Number(a.start || 0) - Number(b.start || 0))
    .map((b, i) => ({ ...b, id: `B${String(i + 1).padStart(2, '0')}` }))
}
export function moveBeat(beats, index, delta) {
  const to = index + delta
  if (to < 0 || to >= beats.length) return beats
  const out = [...beats]
  const [a, b] = [out[index], out[to]]
  // 交换的是「内容」，时间段留在原位：时间轴不许因为挪一行就出现空洞
  const spans = [{ start: a.start, end: a.end }, { start: b.start, end: b.end }]
  out[index] = { ...b, ...spans[0] }
  out[to] = { ...a, ...spans[1] }
  return renumber(out)
}
// 删一拍：后面的拍依次往前贴住，不留空洞
export function removeBeat(beats, index) {
  const out = beats.filter((_, i) => i !== index)
  let cursor = beats[index] ? Number(beats[index].start || 0) : 0
  return renumber(out.map((b, i) => {
    if (i < index) return b
    const span = round(Number(b.end || 0) - Number(b.start || 0))
    const row = { ...b, start: round(cursor), end: round(cursor + span) }
    cursor += span
    return row
  }))
}

// 这一拍编译出来的那一行（跟 clip/prompts.py::beat_line 同形）。整条提示词由后端编译（保存后刷新），
// 逐行预览在本地算——边打字边能看到这一拍会变成什么样。
export function previewLine(beat, zh = true) {
  const span = `${fmtSecs(beat?.start)}–${fmtSecs(beat?.end)}`
  const body = [beat?.shot, beat?.picture].map(x => String(x || '').trim()).filter(Boolean)
  let line = (zh ? `${span} 秒：` : `${span}s: `) + body.join(zh ? '，' : ', ')
  const sound = String(beat?.sound || '').trim()
  if (sound) line += (zh ? '；声音：' : '; sound: ') + sound
  const text = String(beat?.text || '').trim()
  if (text) line += (zh ? '；画面文字："' : '; on-screen text: "') + text + '"'
  return line + (zh ? '。' : '.')
}

// 时间条：每拍在总时长里的百分比（总时长取 duration 与末拍 end 的较大者，超时的那一段也画得出来）
export function timeline(beats, duration) {
  const total = Math.max(Number(duration || 0), ...(beats || []).map(b => Number(b.end || 0)), 1)
  return (beats || []).map(b => ({
    id: b.id,
    left: (Number(b.start || 0) / total) * 100,
    width: Math.max(((Number(b.end || 0) - Number(b.start || 0)) / total) * 100, 0.5),
    label: `${fmtSecs(b.start)}–${fmtSecs(b.end)}s`,
  }))
}

// ---- 列表 / 当前片子
export async function loadClips() {
  try { store.clips = await get(CLIP_API) } catch { store.clips = [] }
  store.clipsLoaded = true
  return store.clips
}

// GET /api/clip/{id} 是 detail 外壳：{id, spec, prompt, sections, beats, warnings, problems, status, attempts, first_frame…}；
// 视图认 store.clip = 项目文件（spec），产物 / 提示词预览从 store.clipDetail 取。老形状（直接返回 spec）也认。
export async function loadClip(id, { keep = false } = {}) {
  if (!keep) { store.clip = null; store.clipDetail = null }
  store.clipId = id
  const detail = await get(clipUrl(id))
  if (store.clipId !== id) return          // 用户已经切走
  store.clipDetail = detail
  store.clip = detail?.spec ? { ...detail.spec, id: detail.spec.id || detail.id || id } : detail
}

export async function refreshClip() {
  if (!store.clipId) return
  await loadClip(store.clipId, { keep: true })
  loadClips().catch(() => {})
}

export function clearClip() { store.clipId = null; store.clip = null; store.clipDetail = null }

export async function archiveClip(id) {
  await del(clipUrl(id))
  await loadClips()
  if (store.clipId === id) clearClip()
}

export async function createFromExample(name = EXAMPLE_NAME) {
  const r = await post(`${CLIP_API}/examples/create`, { name })
  await loadClips()
  return r
}

// 项目文件的可保存形状：去掉后端附带的目录字段
export function specBody(spec = store.clip) {
  const { output_root: _o, project_root: _r, ...rest } = spec || {}
  return rest
}

// ---- 进度 / 产物：detail.status 优先，其次列表行的 progress
export function progressOf(src) {
  const p = src?.progress || src?.status || src || {}
  const done = !!(p.done ?? (p.status === 'done'))
  return { done, status: p.status || (done ? 'done' : 'todo'), attempts: Number(p.attempts || 0), beats: Number(p.beats || 0), duration: p.duration ?? null }
}
export function progressText(row) {
  const p = progressOf(row)
  const head = t('{n} 拍 · {s} 秒', { n: p.beats || (row?.beats ?? 0), s: row?.duration ?? p.duration ?? 0 })
  if (p.status === 'running') return `${head} · ${t('出片中')}`
  if (p.done) return `${head} · ${t('已有成片')}`
  if (p.status === 'failed') return `${head} · ${t('上次失败')}`
  return head
}

export function attempts(detail = store.clipDetail) {
  return (detail?.attempts || []).map((a, i) => ({ ...a, index: i, key: a.file || String(i) }))
}
export function currentUrl(detail = store.clipDetail, stamp = 0) {
  const url = detail?.current_url
  return url ? url + stampQ(url, stamp) : null
}
export function frameUrl(detail = store.clipDetail, stamp = 0) {
  const url = detail?.first_frame?.url
  return url ? url + stampQ(url, stamp) : null
}

// ---- 问题卡：/lint 或 PUT 返回的 problems（四要素卡）；只有 warnings 字符串时前端拆成卡
// 告警字符串的形状（clip/lint.py）：「beat B02: 影响——怎么做」/「全片：影响——怎么做」
const BEAT_RE = /^(?:beat\s+)?(B\d{2,3})\s*[：:]\s*(.*)$/s
const CLIP_RE = /^(全片|clip)\s*[：:]\s*(.*)$/s
export function parseWarning(w) {
  let scope = 'clip', target = '', body = String(w).trim()
  const m = BEAT_RE.exec(body)
  if (m) { scope = 'beat'; target = m[1]; body = m[2] } else { const c = CLIP_RE.exec(body); if (c) body = c[2] }
  let impact = body, fix = ''
  for (const sep of ['——', '--']) { if (body.includes(sep)) { [impact, fix] = body.split(sep, 2); break } }
  return { scope, target, kind: 'clip', impact: impact.trim().replace(/[；;]$/, ''), fix: fix.trim(), text: String(w) }
}
export function toCards(src) {
  if (!src) return []
  const cards = src.problems || src.cards
  if (Array.isArray(cards) && cards.length) {
    return cards.map(c => {
      const parsed = parseWarning(c.text || c.impact || '')
      const target = c.target || parsed.target
      return { scope: target ? 'beat' : 'clip', target, kind: c.kind || 'clip',
        impact: (c.target ? c.impact : parsed.impact) || c.impact || c.text || '', fix: c.fix || parsed.fix || '', text: c.text || c.impact || '' }
    })
  }
  const warnings = src.warnings || src.lint?.warnings || []
  return warnings.filter(w => typeof w === 'string' && w.trim()).map(parseWarning)
}
export const beatCards = (cards, id) => cards.filter(c => c.scope === 'beat' && c.target === id)
export const clipCards = (cards) => cards.filter(c => c.scope !== 'beat')

// ---- 后台任务：起 → pollTask → 刷新。返回 task（失败时已弹过 notice）；起不来返回 null。
// 同步接口（上传首帧没有 task_id）直接返回响应体并刷新。
export async function runClipTask(url, body, label) {
  let created
  try { created = await post(url, body || {}) } catch (e) { await notice(e.message); return null }
  const taskId = created?.task_id
  if (!taskId) { await refreshClip().catch(() => {}); return created }
  if (created.cost_note) toast(String(created.cost_note), { kind: 'warn', ttl: 6000 })
  store.gen = { text: `${label}…` }
  try {
    const task = await pollTask(taskId, (_t, sec) => { store.gen = { text: t('{label}…已经 {s} 秒（可以切走）', { label, s: sec }) } })
    if (task.status === 'failed') { await notice(t('{label}失败：', { label }) + '\n\n' + (task.error || '')); return task }
    toast(t('{label}完成', { label }))
    return task
  } catch (e) { await notice(e.message); return null } finally { store.gen = null; await refreshClip().catch(() => {}) }
}
