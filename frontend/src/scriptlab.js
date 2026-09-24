// 剧本工作台模块（docs/scriptlab-spec.md）的前端胶水：接口前缀、列表与当前项目的加载、后台任务轮询、
// 目标形态 / cue / 种类的常量表、选项与投放目标缓存、版本摘要与逐行差异。
// 视图只认这里的形状；后端列表 / 详情的字段名取值处都留了兜底（列表里的 speakers / blocks / versions 可能是数组也可能是条数）。
import { get, post, del, pollTask } from './api.js'
import { store } from './store.js'
import { notice, toast } from './ui.js'
import { t, isEn, lang } from './i18n.js'
// 台词种类与 cue 只认播客那套（后端是 podcast.spec.CUES），这里不另起一份词表：把播客模块的常量原样转出去，
// 播客改了词表工作台跟着改（music.js 引 podcast.js 的 MUSIC_SLOTS 是同一个做法）
import { CUES, LINE_KINDS } from './podcast.js'

export const SCRIPT_API = '/api/script'
export const sc = (id) => `${SCRIPT_API}/${encodeURIComponent(id)}`
export const scriptRoute = (id, sub = 'write') => `/script/p/${id}/${sub}`

export const SPEAKERS_MIN = 1
export const SPEAKERS_MAX = 6

// 目标形态：决定用哪套写法与哪套检查（契约 §3 / §4）。值保持中文（同时是字典 key），渲染时 t() 翻译。
// options 接口拉不到时用这份兜底；english 跟后端 script.spec.TARGETS 的 english 同一套措辞（英文界面直接用它，
// 后端 options 的 name 也按同一套规则翻，两边翻出来必须一样）。
export const TARGETS = [
  {
    id: 'podcast', name: '播客对谈', english: 'Podcast',
    note: '按播客那八条写法写（口语不是书面语、有人不同意、具体压倒观点、每个人是一个人…），检查用播客的台词判据：书面语、独白太长、机械轮流、一人独大、全程没分歧、填充词、没有具体例子、回答复述问题。',
  },
  {
    id: 'drama_screenplay', name: '短剧剧本', english: 'Short drama scene',
    note: '按短剧台词那几条写：留潜台词、嘴上和身上各演一半、只写拍得到的、不用台词交代前情；检查单句超过 25 字、开口的人超过 3 个、对白密度。',
  },
  {
    id: 'drama_shot', name: '短剧分镜台词', english: 'Short drama shot',
    note: '写法跟短剧剧本一样，但每一段当一镜看；检查除了单句长度与开口人数，还按 4.5 字/秒估这一镜的时长。',
  },
  {
    id: 'generic', name: '通用', english: 'Generic dialogue',
    note: '两套写法的交集（口语、有分歧、具体、不用台词交代前情）；检查只跑书面语、独白太长、一人独大、没有具体例子四条。',
  },
]
export { CUES, LINE_KINDS }
// 段种类（契约 §2）：只有「听友问答」要显式标——回答太短 / 复述问题 / 没标回答那三条判据只对它生效，
// 对谈段里的短接话（「嗯」「对」）本来就该短，按它判就是误报
export const BLOCK_KINDS = [
  { id: 'talk', label: '对谈' },
  { id: 'qa', label: '听友问答' },
]

// 版本来源（契约 §2 的 source）
export const SOURCE_LABELS = { model: '模型出的', hand: '手改另存', rewrite: '重写了几段', import: '从项目导入' }

// 投放 / 导入的三种去处（契约 §7）：module 是接口收的值，source 是 targets 接口里的那一栏，spot 是落点在界面上的叫法
export const USE_MODULES = [
  { id: 'podcast', label: 'AI 播客', source: 'podcast', spot: '环节', canNew: true },
  { id: 'drama_screenplay', label: '短剧剧本', source: 'drama', spot: '场次', canNew: true },
  { id: 'drama_shot', label: '短剧分镜', source: 'drama', spot: '镜号', canNew: false },
]
export const useModule = (id) => USE_MODULES.find(m => m.id === id) || USE_MODULES[0]

// brief 的空表单（形状对齐项目文件 §2）
export function emptyBrief() {
  return { topic: '', angle: '', audience: '', materials: '', tone: '', avoid: [] }
}
export function emptySpeaker(i = 0) {
  return { id: `s${i + 1}`, name: '', persona: '', stance: '' }
}
export function emptyBlock(id = 'B01') {
  return { id, kind: 'talk', title: '', goal: '', note: '' }
}
export function emptyLine(who = '') {
  return { who, text: '', cue: '', kind: 'talk' }
}

// 段编号：B01… 按已有的最大号往后排，不重复
export function nextBlockId(blocks) {
  let max = 0
  for (const b of blocks || []) { const m = /^B(\d+)$/.exec(String(b?.id || '')); if (m) max = Math.max(max, Number(m[1])) }
  return `B${String(max + 1).padStart(2, '0')}`
}

// 「顿号 / 逗号分隔的一行」↔ 数组：chips 输入框两边都要用
export function splitList(text) {
  return String(text || '').split(/[、,，/]/).map(s => s.trim()).filter(Boolean)
}

// 形态名：英文界面优先用后端 / 兜底表给的 english（跟 stylecards.js 的 cardName 一个做法）——后端 options 的 name
// 是「短剧剧本（场）」这种带括号的名字，前端字典里没有，t() 只会原样回落成中文
export const targetName = (id) => {
  const row = cachedTargets().find(x => x.id === id) || TARGETS.find(x => x.id === id)
  if (!row) return String(id || '')
  return (isEn() && row.english) ? row.english : t(row.name)
}
export const targetNote = (id) => {
  const row = cachedTargets().find(x => x.id === id) || TARGETS.find(x => x.id === id)
  return row?.note ? t(row.note) : ''
}

// ---- 目标形态 / cue / 写法约束（GET /api/script/options），每种界面语言只拉一次
// 缓存按拉取时的界面语言记：后端按语言翻了 targets 的 name / note，英文界面拉到的缓存切回中文还接着用，
// 中文界面就露「Podcast」。语言对不上时先回落前端兜底表（t() 能翻），下次 loadOptions 再按新语言重拉
let optionsCache = null
export async function loadOptions({ force = false } = {}) {
  if (!force && optionsCache && optionsCache.lang === lang.value) return optionsCache
  const asked = lang.value
  let r
  try { r = await get(`${SCRIPT_API}/options`) } catch { r = null }
  optionsCache = {
    lang: asked,
    targets: (r?.targets?.length ? r.targets : TARGETS).map(x => (typeof x === 'string' ? { id: x, name: x, note: '' } : { note: '', ...x })),
    cues: r?.cues?.length ? r.cues : CUES,
    kinds: (r?.kinds?.length ? r.kinds : Object.keys(LINE_KINDS)).map(k => (typeof k === 'string' ? k : k.id)),
    limits: { speakers: [SPEAKERS_MIN, SPEAKERS_MAX], ...(r?.limits || {}) },
    craft: r?.craft || {},
  }
  return optionsCache
}
export const cachedTargets = () => (optionsCache && optionsCache.lang === lang.value ? optionsCache.targets : TARGETS)

// ---- 列表 / 当前项目（store.scripts / store.script / store.scriptDetail）
export async function loadScripts() {
  let rows
  try { rows = await get(SCRIPT_API) } catch { rows = [] }
  store.scripts = Array.isArray(rows) ? rows : (rows?.items || [])
  store.scriptsLoaded = true
  return store.scripts
}

// GET /api/script/{id} 是 detail 外壳（§8）：
// {id, spec, current_version, estimate, warnings, problems, versions:[…摘要], review, targets_hint, busy, limits}
export async function loadScript(id, { keep = false } = {}) {
  if (!keep) { store.script = null; store.scriptDetail = null }
  store.scriptId = id
  const detail = await get(sc(id))
  if (store.scriptId !== id) return   // 用户已经切走
  store.scriptDetail = detail
  store.script = detail?.spec ? { ...detail.spec, id: detail.spec.id || detail.id || id } : detail
}

export async function refreshScript() {
  if (!store.scriptId) return
  await loadScript(store.scriptId, { keep: true })
  loadScripts().catch(() => {})
}

export function clearScript() { store.scriptId = null; store.script = null; store.scriptDetail = null }

export async function archiveScript(id) {
  await del(sc(id))
  await loadScripts()
  if (store.scriptId === id) clearScript()
}

// ---- 投放 / 导入目标（GET /api/script/targets）→ {podcast: [...], drama: [...]}
// 每一行归一成 {id, title, locked, segments, scenes, shots, roles}；roles 是目标项目的主播 / 人物（投放时要一一映射），
// 后端可能叫 hosts / cast / characters，这里都认。
function normalizeSpots(rows) {
  return (Array.isArray(rows) ? rows : []).map(x => (typeof x === 'string'
    ? { id: x, title: '', lines: 0 }
    : { id: String(x?.id ?? ''), title: x?.title || '', lines: Number(x?.lines || 0) })).filter(x => x.id)
}
function normalizeRoles(row) {
  const raw = row?.roles || row?.hosts || row?.cast || row?.characters || []
  return (Array.isArray(raw) ? raw : []).map(x => (typeof x === 'string'
    ? { id: x, name: x }
    : { id: String(x?.id ?? ''), name: x?.name || String(x?.id ?? '') })).filter(x => x.id)
}
function normalizeTargetRow(row) {
  return {
    id: String(row?.id ?? ''), title: row?.title || String(row?.id ?? ''), locked: !!row?.locked,
    segments: normalizeSpots(row?.segments), scenes: normalizeSpots(row?.scenes), shots: normalizeSpots(row?.shots),
    roles: normalizeRoles(row),
  }
}
export async function loadTargets() {
  let r
  try { r = await get(`${SCRIPT_API}/targets`) } catch { r = null }
  return {
    podcast: (r?.podcast || []).map(normalizeTargetRow).filter(x => x.id),
    drama: (r?.drama || []).map(normalizeTargetRow).filter(x => x.id),
  }
}
export const targetRows = (targets, moduleId) => (targets?.[useModule(moduleId).source] || [])
// 一个目标项目在某个模块下的落点清单：播客是环节、剧本是场次、分镜是镜号
export function spotsOf(row, moduleId) {
  if (!row) return []
  if (moduleId === 'podcast') return row.segments
  if (moduleId === 'drama_shot') return row.shots
  return row.scenes
}

// ---- 版本：摘要行与「采用的是第几版」
// 列表行的 versions 可能是数组也可能是条数；拿不到数组时按 id 里的编号（归一化保证是 V1…）当序号
export function versionIndex(current, versions) {
  const id = String(current || '')
  if (!id) return 0
  if (Array.isArray(versions)) {
    const i = versions.findIndex(v => (typeof v === 'string' ? v : v?.id) === id)
    if (i >= 0) return i + 1
  }
  const m = /^V(\d+)$/.exec(id)
  return m ? Number(m[1]) : 0
}
export const sourceLabel = (source) => t(SOURCE_LABELS[source] || source || '')

// 审稿结论一行字：没审过就空着，审过写违反 / 有风险的条数，台词改过补一句「已过时」
export function verdictText(review) {
  if (!review) return ''
  const v = review.verdicts || {}
  const head = t('违反 {a} · 有风险 {b}', { a: Number(v.violated || 0), b: Number(v.risk || 0) })
  return review.stale ? `${head} · ${t('已过时')}` : head
}

// 估算一行字：契约 §4 的 {chars, lines, blocks, seconds, minutes}
export function estimateText(est) {
  if (!est) return ''
  return t('预计 {m} 分钟（{c} 字，{l} 句）', { m: Number(est.minutes || 0).toFixed(1), c: est.chars || 0, l: est.lines || 0 })
}

// detail / spec 里当前采用的那一版（带 blocks）；detail.current_version 优先
export function currentVersion(detail = store.scriptDetail, spec = store.script) {
  const cur = detail?.current_version
  if (cur && typeof cur === 'object' && Array.isArray(cur.blocks)) return cur
  const id = String(detail?.spec?.current ?? spec?.current ?? '')
  const list = spec?.versions || detail?.spec?.versions || []
  return list.find(v => v.id === id) || list[list.length - 1] || null
}
// 某一版里某一段的台词（段的骨架在 spec.blocks，台词在版本里）
export function linesOf(version, blockId) {
  const b = (version?.blocks || []).find(x => x.id === blockId)
  return (b?.lines || []).map(l => ({ cue: '', kind: 'talk', ...l }))
}

// ---- 逐行差异（契约 §6：后端不做 diff）
// 两版按段 id 对齐（左边的顺序为准，右边多出来的段接在后面），段内按行号对齐：
// 两边都有且说话人 / 台词 / cue / 种类全一样的行算「相同」（界面淡显），其余算「不同」（界面高亮）。
// 工作台的两版共用同一套段落骨架，按行号对齐就够，不引第三方 diff 库。
const sameLine = (a, b) => !!a && !!b && a.who === b.who && String(a.text || '') === String(b.text || '')
  && String(a.cue || '') === String(b.cue || '') && (a.kind || 'talk') === (b.kind || 'talk')

export function compareVersions(left, right, blocks = []) {
  // 两边都没有版本（还没起草，或者下拉还没选）就没什么可比的：再按段落骨架铺一遍，
  // 界面上就是一串「0 行不一样 / 两版在这一段都没有台词」的空段卡
  if (!left && !right) return []
  const ids = []
  const push = (id) => { if (id && !ids.includes(id)) ids.push(id) }
  for (const b of blocks || []) push(b.id)
  for (const b of left?.blocks || []) push(b.id)
  for (const b of right?.blocks || []) push(b.id)
  return ids.map(id => {
    const a = linesOf(left, id)
    const c = linesOf(right, id)
    const n = Math.max(a.length, c.length)
    const rows = []
    for (let i = 0; i < n; i++) rows.push({ index: i, left: a[i] || null, right: c[i] || null, same: sameLine(a[i], c[i]) })
    return {
      id,
      title: (blocks || []).find(b => b.id === id)?.title || '',
      rows,
      changed: rows.filter(r => !r.same).length,
    }
  })
}
// 一行台词的显示文本：「阿杰：……（轻笑）」
export function lineText(line, speakers = []) {
  if (!line) return ''
  const who = speakers.find(s => s.id === line.who)?.name || line.who || ''
  const cue = line.cue ? `（${t(line.cue)}）` : ''
  return `${who ? `${who}：` : ''}${line.text || ''}${cue}`
}

// ---- 进度：列表行 / 详情 → {speakers, blocks, versions, current, currentIndex, minutes, used}
export function progressOf(src) {
  const spec = src?.spec || src || {}
  const num = (v, fallback = 0) => (Array.isArray(v) ? v.length : Number(v ?? fallback) || 0)
  const versions = Array.isArray(src?.versions) ? src.versions : (Array.isArray(spec.versions) ? spec.versions : null)
  const current = String(src?.current ?? spec.current ?? '')
  return {
    speakers: num(src?.speakers ?? spec.speakers),
    blocks: num(src?.blocks ?? spec.blocks),
    versions: versions ? versions.length : num(src?.versions),
    current,
    currentIndex: versionIndex(current, versions),
    minutes: Number(src?.minutes ?? src?.estimate?.minutes ?? 0),
    used: num(src?.used_in ?? spec.used_in),
  }
}
export function progressText(src) {
  const p = progressOf(src)
  if (!p.versions) return `${t('还没起草')} · ${t('{n} 段', { n: p.blocks })}`
  const picked = p.currentIndex ? t('采用第 {k} 版', { k: p.currentIndex }) : t('还没挑一版')
  const used = p.used ? ` · ${t('已投放 {n} 处', { n: p.used })}` : ''
  return `${t('{n} 版', { n: p.versions })} · ${picked}${used}`
}

// 项目文件的可保存形状：去掉后端附带的目录字段
export function specBody(spec = store.script) {
  const { output_root: _o, project_root: _r, ...rest } = spec || {}
  return rest
}

// ---- 后台任务：起 → pollTask → 刷新。返回 task（失败时已弹过 notice）；起不来返回 null
export async function runScriptTask(url, body, label) {
  let created
  try { created = await post(url, body || {}) } catch (e) { await notice(e.message); return null }
  const taskId = created?.task_id
  if (!taskId) { await refreshScript().catch(() => {}); return created }
  if (created.cost_note) toast(String(created.cost_note), { kind: 'warn', ttl: 6000 })
  store.gen = { text: `${label}…` }
  let refreshed = false
  try {
    const task = await pollTask(taskId, (_t, sec) => { store.gen = { text: t('{label}…已经 {s} 秒（可以切走）', { label, s: sec }) } })
    if (task.status === 'failed') { await notice(t('{label}失败：', { label }) + '\n\n' + (task.error || '')); return task }
    const r = task.result || {}
    if (r.version) {
      // 项目还没采用任何一版时新出的这一版会被后端直接采用（adopt_if_first）：toast 按刷新后的 current 说话，
      // 别对着已经采用的 V1 说「还没采用」
      await refreshScript().catch(() => {}); refreshed = true
      const adopted = String(store.scriptDetail?.spec?.current ?? store.script?.current ?? '') === String(r.version)
      toast(adopted
        ? t('{label}完成，出了 {v}；项目之前没采用任何一版，这一版已直接采用', { label, v: r.version })
        : t('{label}完成，出了新的一版 {v}（还没采用）', { label, v: r.version }))
    } else toast(t('{label}完成', { label }))
    return task
  } catch (e) { await notice(e.message); return null } finally { store.gen = null; if (!refreshed) await refreshScript().catch(() => {}) }
}
