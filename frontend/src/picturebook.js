// 绘本 / 有声书模块（docs/picturebook-spec.md §6–§7）的前端胶水：接口前缀、列表与当前书的加载、后台任务轮询、
// 新建页的预设文案与示例书。视图只认这里的形状；后端概览 / 列表的字段名还在落地，取值处都留了兜底。
import { get, post, del, pollTask } from './api.js'
import { store } from './store.js'
import { notice, toast } from './ui.js'
import { t } from './i18n.js'

export const PB_API = '/api/picturebook'
export const pb = (id) => `${PB_API}/${encodeURIComponent(id)}`
export const pbArtifact = {
  page: (id, pid, stamp = 0) => `${pb(id)}/artifact/page/${encodeURIComponent(pid)}${stamp ? `?t=${stamp}` : ''}`,
  audio: (id, pid, lang, stamp = 0) => `${pb(id)}/artifact/audio/${encodeURIComponent(pid)}_${lang}${stamp ? `?t=${stamp}` : ''}`,
  final: (id, stamp = 0) => `${pb(id)}/artifact/final${stamp ? `?t=${stamp}` : ''}`,
  scriptSheet: (id, lang) => `${pb(id)}/script-sheet?lang=${lang}`,
}

export const AGE_BANDS = ['2-4', '4-6', '6-8']
// 分级判据（spec §3）：页数区间与每页中文字数上限
export const AGE_RULES = {
  '2-4': { pages: [8, 10], zh_max: 20 },
  '4-6': { pages: [10, 14], zh_max: 40 },
  '6-8': { pages: [12, 16], zh_max: 70 },
}
export const BILINGUAL_MODES = [
  { id: 'sentence_alternate', label: '逐句交替（中句 → 英句）' },
  { id: 'page_zh_then_en', label: '整页先中后英' },
  { id: 'zh_only', label: '只有中文' },
  { id: 'en_only', label: '只有英文' },
]
export const KB_DIRS = ['push', 'pull', 'left', 'right', 'up', 'down']
export const FLIPS = ['page_turn', 'cut']
export const PAGE_KINDS = ['cover', 'page', 'back']

// 风格预设（spec §7）：四段可改的风格锚文案 + 各自的负面词
export const STYLE_PRESETS = [
  { id: 'watercolor', name: '水彩', anchor: 'Soft watercolor picture-book art, rounded friendly shapes, airy pastel washes, visible paper grain',
    negative: 'photo, 3D render, text, letters, watermark, frightening, gloomy' },
  { id: 'crayon', name: '蜡笔', anchor: 'Crayon and colored-pencil children\'s book illustration, visible waxy strokes, chunky rounded shapes, bright cheerful palette, paper grain',
    negative: 'photo, 3D render, text, letters, watermark, frightening, gloomy, glossy' },
  { id: 'papercut', name: '剪纸', anchor: 'Layered paper-cut children\'s book illustration, flat cut-paper shapes with soft drop shadows, textured cardstock, limited warm palette',
    negative: 'photo, 3D render, text, letters, watermark, frightening, gloomy, thin lines' },
  { id: 'flat', name: '扁平矢量', anchor: 'Flat vector children\'s book illustration, clean geometric shapes, bold simple outlines, cheerful limited palette, no gradients',
    negative: 'photo, 3D render, text, letters, watermark, frightening, gloomy, texture, noise' },
]

export function pageCountHint(ageBand) {
  const r = AGE_RULES[ageBand]
  return r ? t('{lo}–{hi} 页（含封面封底），正文每页中文 ≤{n} 字', { lo: r.pages[0], hi: r.pages[1], n: r.zh_max }) : ''
}

export function newPage(idx, kind = 'page') {
  return { id: `P${String(idx).padStart(2, '0')}`, kind, scene: '', characters: [], scene_ref: null, zh: '', en: '', hook: '', education: '',
    kb: { dir: KB_DIRS[idx % 2 === 0 ? 0 : 2], scale: [1.0, 1.06] }, flip: 'page_turn' }
}

// 示例书：后端 examples/picturebook/*.json，POST /api/picturebook/examples/create 复制成新书（不花钱）
export const EXAMPLE_NAME = 'fox_three_socks'
export async function createFromExample(name = EXAMPLE_NAME) {
  const r = await post(`${PB_API}/examples/create`, { name })
  await loadBooks()
  return r
}

// ---- 列表 / 当前书
export async function loadBooks() {
  try { store.books = await get(PB_API) } catch { store.books = [] }
  store.booksLoaded = true
  return store.books
}

// GET /api/picturebook/{id} 是 detail 外壳：{id, spec, pages: [{id, image_url, audio: {zh: {done, duration, url…}}}], timeline, final_url…}；
// 视图认 store.book = 项目文件（spec），逐页产物信息从 store.bookDetail 取。老形状（直接返回 spec）也认。
export async function loadBook(id, { keep = false } = {}) {
  if (!keep) { store.book = null; store.bookDetail = null; store.bookSummary = null; store.bookLint = null }
  store.bookId = id
  const [detail, summary, lint] = await Promise.all([
    get(pb(id)), get(`${pb(id)}/summary`).catch(() => null), get(`${pb(id)}/lint`).catch(() => null),
  ])
  if (store.bookId !== id) return   // 用户已经切走
  store.bookDetail = detail
  store.book = detail?.spec ? { ...detail.spec, id: detail.spec.id || detail.id || id } : detail
  store.bookSummary = summary
  store.bookLint = lint || (detail?.warnings ? { warnings: detail.warnings, notes: detail.notes, problems: detail.problems } : null)
}

export async function refreshBook() {
  if (!store.bookId) return
  await loadBook(store.bookId, { keep: true })
  loadBooks().catch(() => {})
}

export async function archiveBook(id) {
  await del(pb(id))
  await loadBooks()
  if (store.bookId === id) { store.bookId = null; store.book = null; store.bookDetail = null; store.bookSummary = null; store.bookLint = null }
}

// ---- 概览 / 列表行里的进度：后端字段名可能是 progress.pages / pages / pages_done，这里统一成 {done,total}
function pair(v, total) {
  if (v && typeof v === 'object') return { done: Number(v.done ?? v.count ?? 0), total: Number(v.total ?? total ?? 0), ids: v.ids || v.done_ids || null }
  return { done: Number(v || 0), total: Number(total || 0) }
}
export function progressOf(src, total) {
  const p = src?.progress || src || {}
  const pages = pair(p.pages ?? p.pages_done ?? p.illustrations, p.pages_total ?? p.page_count ?? total)
  const narration = pair(p.narration ?? p.narration_done ?? p.audio, p.narration_total ?? p.page_count ?? total)
  const finalRaw = p.final
  const final = !!(finalRaw && typeof finalRaw === 'object' ? (finalRaw.exists ?? finalRaw.done) : finalRaw)
  return { pages, narration, final }
}

// 逐页产物信息：detail.pages 里的行（image_url / audio.{lang}.{done,duration,url,placeholder}）优先，其次页对象自带标记，再次概览的已完成 id 列表
export function pageRow(pid, detail = store.bookDetail) {
  return (detail?.pages || []).find(r => r.id === pid) || null
}
export function hasPageImage(page, summary, detail = store.bookDetail) {
  const row = pageRow(page?.id, detail)
  if (row) return !!row.image_url
  if (page?.has_image != null) return !!page.has_image
  if (page?.image_url) return true
  const ids = progressOf(summary).pages.ids
  return Array.isArray(ids) ? ids.includes(page?.id) : false
}
export function pageImageUrl(bookId, page, stamp = 0, detail = store.bookDetail) {
  const row = pageRow(page?.id, detail)
  if (row?.image_url) return `${row.image_url}${stamp ? `${row.image_url.includes('?') ? '&' : '?'}t=${stamp}` : ''}`
  return pbArtifact.page(bookId, page?.id, stamp)
}
export function audioInfo(page, lang, detail = store.bookDetail) {
  const row = pageRow(page?.id, detail)
  const a = row?.audio?.[lang] ?? page?.audio?.[lang]
  return a && typeof a === 'object' ? a : (a ? { done: true } : null)
}
export function hasPageAudio(page, lang, summary, detail = store.bookDetail) {
  const a = audioInfo(page, lang, detail)
  if (a) return !!(a.done ?? a.url ?? a.duration ?? a.seconds)
  return durationOf(page, lang, summary, detail) != null
}
// 实测时长：detail 行的 duration / seconds，其次概览 durations[P01].zh
export function durationOf(page, lang, summary, detail = store.bookDetail) {
  const a = audioInfo(page, lang, detail)
  const v0 = a?.duration ?? a?.seconds
  if (v0 != null) return Number(v0)
  const map = summary?.durations || summary?.narration?.durations || summary?.progress?.narration?.durations
  const v = map?.[page?.id]?.[lang] ?? map?.[`${page?.id}_${lang}`]
  return v == null ? null : Number(v)
}
export function audioUrl(bookId, page, lang, stamp = 0, detail = store.bookDetail) {
  const a = audioInfo(page, lang, detail)
  if (a?.url) return `${a.url}${stamp ? `${a.url.includes('?') ? '&' : '?'}t=${stamp}` : ''}`
  return pbArtifact.audio(bookId, page?.id, lang, stamp)
}
// 页时长：detail.timeline（实测 + 估算）优先
export function timelinePage(pid, detail = store.bookDetail) {
  return (detail?.timeline?.pages || []).find(p => p.id === pid) || null
}

// ---- 问题卡：/lint 或 PUT 返回的 problems（四要素卡）；只有 warnings 字符串时前端拆成卡
// 告警字符串的形状（picturebook/lint.py）：「page P02: 影响——怎么改」/「全书：影响——怎么改」
const PAGE_RE = /^(?:page\s+)?(P\d{2,3})\s*[：:]\s*(.*)$/s
const BOOK_RE = /^(全书|book)\s*[：:]\s*(.*)$/s
function parseWarning(w) {
  let scope = 'book', target = '', body = String(w).trim()
  const m = PAGE_RE.exec(body)
  if (m) { scope = 'page'; target = m[1]; body = m[2] } else { const b = BOOK_RE.exec(body); if (b) body = b[2] }
  let impact = body, fix = ''
  for (const sep of ['——', '--']) { if (body.includes(sep)) { [impact, fix] = body.split(sep, 2); break } }
  return { scope, target, kind: 'text', impact: impact.trim().replace(/[；;]$/, ''), fix: fix.trim(), text: String(w) }
}
export function toCards(lint) {
  if (!lint) return []
  const cards = lint.problems || lint.cards
  if (Array.isArray(cards) && cards.length) {
    return cards.map(c => {
      // 后端四要素卡认的是 shot/cast 前缀，绘本的「page P02:」它拆不出 target——这里从原文补
      const parsed = parseWarning(c.text || c.impact || '')
      const target = c.target || parsed.target
      return { scope: target ? 'page' : 'book', target, kind: c.kind || 'text', impact: (c.target ? c.impact : parsed.impact) || c.impact || c.text || '', fix: c.fix || parsed.fix || '', text: c.text || c.impact || '' }
    })
  }
  const warnings = lint.warnings || lint.lint?.warnings || []
  return warnings.filter(w => typeof w === 'string' && w.trim()).map(parseWarning)
}
export function lintWarnings(r) {
  const l = r?.lint || r
  return (l?.warnings || []).filter(w => typeof w === 'string')
}

// ---- 后台任务：起 → pollTask → 刷新。返回 task（失败时已弹过 notice）；起不来返回 null
export async function runBookTask(url, body, label) {
  let created
  try { created = await post(url, body || {}) } catch (e) { await notice(e.message); return null }
  const taskId = created?.task_id
  if (!taskId) { await refreshBook(); return created }
  store.gen = { text: `${label}…` }
  try {
    const task = await pollTask(taskId, (_t, sec) => { store.gen = { text: t('{label}…已经 {s} 秒（可以切走）', { label, s: sec }) } })
    if (task.status === 'failed') { await notice(t('{label}失败：', { label }) + '\n\n' + (task.error || '')); return task }
    toast(t('{label}完成', { label }))
    return task
  } catch (e) { await notice(e.message); return null } finally { store.gen = null; await refreshBook().catch(() => {}) }
}
