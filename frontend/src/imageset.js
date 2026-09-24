// 图片集引擎的前端胶水（docs/imageset-spec.md §2 / §5、docs/imagestudio-spec.md §4）：「表情与头像」`sticker`、
// 「纪念写真」`portrait`、「图片工作台」`image` 三个模块共用一套页面，
// 这里按 module 参数化——接口前缀、列表 / 当前图片集的加载、后台任务轮询、产物地址、进度。视图只认这里的形状；
// 后端列表 / 详情的字段名还在落地，取值处都留了兜底（老形状：详情直接返回项目文件）。
import { get, post, del, pollTask } from './api.js'
import { store } from './store.js'
import { notice, toast } from './ui.js'
import { t, isEn } from './i18n.js'

// 每个模块的静态信息：图标 / 名字 / 编号前缀 / 主体种类 / 示例名（examples/<module>/<name>.json）；
// `optionalSubject` = 主体可以整个留空（自由出图只写画面，提示词里连身份锁都不出现）
export const IMAGESET_MODULES = {
  sticker: { id: 'sticker', icon: '🙂', name: '表情与头像', prefix: 'ST', kind: 'character', example: 'xiaoman_emoji',
    titleHint: '如「小满表情包」',
    blurb: '表情包 / 贴图 / 头像：一个角色按模板出一组图，白底转透明，打包成微信表情目录。' },
  portrait: { id: 'portrait', icon: '📷', name: '纪念写真', prefix: 'PT', kind: 'photo', example: 'baby_100_demo',
    titleHint: '如「宝宝百天」',
    blurb: '满月 / 百天 / 周岁 / 婚纱：上传真人参考照，按场景出影楼级写真，3:4 与 4:3 两版裁切，可叠祝语与日期。' },
  image: { id: 'image', icon: '🖼', name: '图片工作台', prefix: 'IM', kind: 'character', example: 'lantern_night',
    optionalSubject: true, titleHint: '如「河灯夜」',
    blurb: '一句话 + 风格卡出一组候选；同一句话换模型横向比；挑中的可以本机叠标题做成图卡。' },
}
export const IMAGESET_IDS = Object.keys(IMAGESET_MODULES)
export const isImagesetModule = (m) => !!IMAGESET_MODULES[m]
export const moduleInfo = (m) => IMAGESET_MODULES[m] || IMAGESET_MODULES.sticker

// ---- 地址
export const setApi = (m) => `/api/${m}`
export const setUrl = (m, id) => `${setApi(m)}/${encodeURIComponent(id)}`
export const setRoute = (m, id, sub = 'items') => `/${m}/s/${id}/${sub}`
const stampQ = (stamp) => (stamp ? `?t=${stamp}` : '')
export const setArtifact = {
  raw: (m, id, name, stamp = 0) => `${setUrl(m, id)}/artifact/raw/${encodeURIComponent(name)}${stampQ(stamp)}`,
  out: (m, id, name, stamp = 0) => `${setUrl(m, id)}/artifact/out/${encodeURIComponent(name)}${stampQ(stamp)}`,
  asset: (m, id, name) => `${setUrl(m, id)}/artifact/asset/${encodeURIComponent(name)}`,
  export: (m, id, name) => `${setUrl(m, id)}/artifact/export/${encodeURIComponent(name)}`,
}
export const baseName = (v) => (v ? String(v).split(/[\\/]/).pop() : '')
// 后端给的 url 优先；没有就按文件名拼产物流地址
export function urlOf(m, id, kind, obj, stamp = 0) {
  if (!obj) return null
  if (typeof obj === 'string') return setArtifact[kind](m, id, baseName(obj), stamp)
  if (obj.url) return `${obj.url}${stamp ? `${obj.url.includes('?') ? '&' : '?'}t=${stamp}` : ''}`
  const file = obj.file || obj.path || obj.name
  return file ? setArtifact[kind](m, id, baseName(file), stamp) : null
}

// ---- 列表 / 当前图片集（store.sets[module] / store.set / store.setDetail）
export async function loadSets(m) {
  let rows
  try { rows = await get(setApi(m)) } catch { rows = [] }
  store.sets[m] = Array.isArray(rows) ? rows : (rows?.items || rows?.sets || [])
  store.setsLoaded[m] = true
  return store.sets[m]
}

// GET /api/{m}/{id} 是 detail 外壳：{id, spec, items: [{id, status, current_url, attempts: [{file, url…}], outputs}], exports, cost_note…}；
// 视图认 store.set = 项目文件（spec），逐张产物信息从 store.setDetail 取。老形状（直接返回 spec）也认。
export async function loadSet(m, id, { keep = false } = {}) {
  if (!keep) { store.set = null; store.setDetail = null }
  store.setModule = m; store.setId = id
  const detail = await get(setUrl(m, id))
  if (store.setId !== id || store.setModule !== m) return   // 用户已经切走
  store.setDetail = detail
  store.set = detail?.spec ? { ...detail.spec, id: detail.spec.id || detail.id || id, module: detail.spec.module || m } : detail
}

export async function refreshSet() {
  if (!store.setId || !store.setModule) return
  await loadSet(store.setModule, store.setId, { keep: true })
  loadSets(store.setModule).catch(() => {})
}

export function clearSet() { store.setModule = null; store.setId = null; store.set = null; store.setDetail = null }

export async function archiveSet(m, id) {
  await del(setUrl(m, id))
  await loadSets(m)
  if (store.setId === id && store.setModule === m) clearSet()
}

// 示例：POST /api/{m}/examples/create（每模块一个，不花钱）
export async function createFromExample(m, name = moduleInfo(m).example) {
  const r = await post(`${setApi(m)}/examples/create`, { name })
  await loadSets(m)
  return r
}

// ---- 模板与风格预设：GET /api/{m}/templates，按模块缓存一次（新建页 / 首页列表都要模板名）
const templateCache = {}
// 同一份响应里还带 model_candidates（[{id, label, source}]，后端按「已配置 → 模型目录 → 本机」排好）：
// 多模型对比的模型多选就吃它，后端没给（老服务端）时留空，前端退回模板自己的默认清单
const candidateCache = {}
export async function loadTemplates(m, { force = false } = {}) {
  if (!force && templateCache[m]) return templateCache[m]
  let r
  try { r = await get(`${setApi(m)}/templates`) } catch { r = [] }
  const list = (Array.isArray(r) ? r : (r?.templates || [])).map(normalizeTemplate)
  templateCache[m] = list
  candidateCache[m] = (Array.isArray(r?.model_candidates) ? r.model_candidates : [])
    .map(c => (typeof c === 'string' ? { id: c, label: c, source: 'catalog' } : c))
    .filter(c => c && c.id)
  return list
}
export const cachedTemplates = (m) => templateCache[m] || []
export const cachedModelCandidates = (m) => candidateCache[m] || []
// 模板名 / 模板说明 / 枚举选项名：后端同时给中文（name / description）和英文（english / description_en）；
// 中间件只翻文案键（label / hint…），不碰 name / description（这两个键多半装的是用户内容），英文界面在这里挑英文那份
export const templateLabel = (tpl) => (!tpl ? '' : ((isEn() && tpl.english) || tpl.name || tpl.id || ''))
export const templateDescription = (tpl) => (!tpl ? '' : ((isEn() && tpl.description_en) || tpl.description || ''))
export const choiceLabel = (c) => (c && typeof c === 'object' ? ((isEn() && c.english) || c.name || c.label || c.id || '') : c)
// fallback：模板列表还没拉回来时用（列表 / 详情接口带的 template_name，中文）
export const templateName = (m, id, fallback = '') => {
  const x = cachedTemplates(m).find(x => x.id === id)
  return x ? templateLabel(x) : (fallback || id || '')
}

function normalizeTemplate(tpl) {
  const styles = (tpl.styles || tpl.style_presets || []).map(s => (typeof s === 'string' ? { id: s, name: s, anchor: '' } : { id: s.id, name: s.name || s.id, anchor: s.anchor || s.style_anchor || '' }))
  return { ...tpl, id: tpl.id, name: tpl.name || tpl.id, description: tpl.description || '', styles, options: normalizeSchema(tpl.options_schema || tpl.options || {}), subject_kind: tpl.subject_kind || tpl.kind || null }
}

// options_schema 两种写法都认：{key: {type, default, label, description, choices}} 或 [{key, …}]；缺 type 从 default 推
export function normalizeSchema(schema) {
  const entries = Array.isArray(schema) ? schema.map(f => [f.key || f.id || f.name, f]) : Object.entries(schema || {})
  return entries.filter(([k]) => k).map(([key, f]) => {
    const spec = (f && typeof f === 'object' && !Array.isArray(f)) ? f : { default: f }
    const dflt = spec.default ?? spec.value
    let type = spec.type
    if (!type) type = Array.isArray(dflt) ? 'list' : typeof dflt === 'boolean' ? 'bool' : typeof dflt === 'number' ? 'int' : (spec.choices?.length ? 'choice' : 'string')
    if (type === 'array' || type === 'list[str]' || type === 'strings') type = 'list'
    if (type === 'boolean') type = 'bool'
    if (type === 'integer' || type === 'number' || type === 'float') type = 'int'
    if (type === 'enum' || type === 'select') type = 'choice'
    if (type === 'str' || type === 'text') type = 'string'
    if (type === 'string' && spec.choices?.length) type = 'choice'
    // 模板的 options_schema 自带 label_en / hint_en：英文界面优先用它（后端 tr 对这些模板文案没规则，原样中文）
    const label = (isEn() && spec.label_en) || spec.label || spec.name || key
    const description = (isEn() && (spec.description_en || spec.hint_en)) || spec.description || spec.hint || ''
    return { key, type, label, description, choices: spec.choices || spec.options || spec.enum || [], default: dflt, min: spec.min, max: spec.max }
  })
}
export function defaultOptions(fields) {
  const out = {}
  for (const f of fields || []) {
    if (f.type === 'list') out[f.key] = Array.isArray(f.default) ? [...f.default] : []
    else if (f.type === 'bool') out[f.key] = !!f.default
    else if (f.type === 'int') out[f.key] = f.default == null ? null : Number(f.default)
    else out[f.key] = f.default == null ? (f.type === 'choice' ? (f.choices?.[0]?.id ?? f.choices?.[0] ?? '') : '') : String(f.default)
  }
  return out
}

// ---- 进度：列表行 / 详情 → {done, total, picked, exported}；后端字段名可能是 progress.{done,total} 或 items_done / item_count
export function progressOf(src) {
  const p = src?.progress || src || {}
  const items = Array.isArray(src?.items) ? src.items : null
  const total = Number(p.total ?? p.item_count ?? p.items_total ?? src?.item_count ?? items?.length ?? 0)
  const done = Number(p.done ?? p.items_done ?? p.generated ?? (items ? items.filter(i => i.current || i.status === 'done').length : 0))
  const picked = Number(p.picked ?? (Array.isArray(src?.picks) ? src.picks.length : 0))
  const expRaw = p.exported ?? p.export ?? src?.exported
  const exported = !!(expRaw && typeof expRaw === 'object' ? (expRaw.exists ?? expRaw.done ?? expRaw.url ?? expRaw.file) : expRaw) || (Array.isArray(src?.exports) && src.exports.length > 0)
  return { done, total, picked, exported }
}
export function progressText(src) {
  const p = progressOf(src)
  return t('出图 {a}/{n} · 挑选 {p}', { a: p.done, n: p.total, p: p.picked }) + (p.exported ? ` · ${t('已导出')}` : '')
}

// ---- 逐张：detail.items 里的行（status / current_url / attempts[].url / outputs）优先，其次项目文件里的 item
export function itemRow(iid, detail = store.setDetail) {
  return (detail?.items || []).find(r => r.id === iid) || null
}
export function itemStatus(item, detail = store.setDetail) {
  const row = itemRow(item?.id, detail)
  const s = row?.status || item?.status
  if (s) return s
  return (row?.current || item?.current) ? 'done' : 'todo'
}
export function hasCurrent(item, detail = store.setDetail) {
  const row = itemRow(item?.id, detail)
  return !!(row?.current_url || row?.current || item?.current)
}
export function itemImageUrl(m, id, item, stamp = 0, detail = store.setDetail) {
  const row = itemRow(item?.id, detail)
  if (row?.current_url) return urlOf(m, id, 'raw', { url: row.current_url }, stamp)
  const cur = row?.current ?? item?.current
  if (!cur) return null
  if (typeof cur === 'object') return urlOf(m, id, 'raw', cur, stamp)
  // current 可能是文件名，也可能是 attempts 的序号
  const attempts = itemAttempts(m, id, item, 0, detail)
  const byIndex = Number.isInteger(cur) ? attempts[cur] : null
  return byIndex?.url || setArtifact.raw(m, id, baseName(cur), stamp)
}
export function itemAttempts(m, id, item, stamp = 0, detail = store.setDetail) {
  const row = itemRow(item?.id, detail)
  const list = row?.attempts || item?.attempts || []
  return list.map((a, i) => ({ ...(typeof a === 'string' ? { file: a } : a), index: i, key: (typeof a === 'string' ? a : (a.id || a.file)) || String(i), url: urlOf(m, id, 'raw', a, stamp) }))
}
export function isCurrentAttempt(item, a, detail = store.setDetail) {
  const row = itemRow(item?.id, detail)
  const cur = row?.current ?? item?.current
  if (cur == null) return false
  if (typeof cur === 'object') return baseName(cur.file || cur.url) === baseName(a.file || a.url)
  if (Number.isInteger(cur)) return cur === a.index
  return baseName(cur) === baseName(a.file || a.key) || (row?.current_url && baseName(row.current_url) === baseName(a.url))
}
export function itemOutputs(m, id, item, stamp = 0, detail = store.setDetail) {
  const row = itemRow(item?.id, detail)
  const list = row?.outputs || item?.outputs || []
  return list.map(o => ({ ...(typeof o === 'string' ? { file: o } : o), url: urlOf(m, id, 'out', o, stamp) }))
}
export function setExports(m, id, detail = store.setDetail, spec = store.set) {
  const list = detail?.exports || spec?.exports || []
  return list.map(e => ({ ...(typeof e === 'string' ? { file: e } : e), url: urlOf(m, id, 'export', e) }))
}
// 项目文件的可保存形状：去掉后端附带的目录字段
export function specBody(spec = store.set) {
  const { output_root: _o, project_root: _r, ...rest } = spec || {}
  return rest
}
// 生图前的确认文案：缺的 = 没有 current 的；全部重出 = 全部
export function countRequests(items, { force = false, only = null } = {}, detail = store.setDetail) {
  const list = (items || []).filter(i => !only || only.includes(i.id))
  return force ? list.length : list.filter(i => !hasCurrent(i, detail)).length
}

// ---- 后台任务：起 → pollTask → 刷新。返回 task（失败时已弹过 notice）；起不来返回 null。
// 同步接口（后处理 / 导出没有 task_id）直接返回响应体并刷新。
export async function runSetTask(url, body, label) {
  let created
  try { created = await post(url, body || {}) } catch (e) { await notice(e.message); return null }
  const taskId = created?.task_id
  if (!taskId) { await refreshSet().catch(() => {}); return created }
  if (created.cost_note) toast(String(created.cost_note), { kind: 'warn', ttl: 6000 })
  store.gen = { text: `${label}…` }
  try {
    const task = await pollTask(taskId, (_t, sec) => { store.gen = { text: t('{label}…已经 {s} 秒（可以切走）', { label, s: sec }) } })
    if (task.status === 'failed') { await notice(t('{label}失败：', { label }) + '\n\n' + (task.error || '')); return task }
    const r = task.result || {}
    const summary = r.failed?.length ? t('{label}完成：成功 {a}，跳过 {b}，失败 {c}', { label, a: r.done?.length ?? r.done ?? 0, b: r.skipped?.length ?? r.skipped ?? 0, c: r.failed.length }) : t('{label}完成', { label })
    toast(summary, r.failed?.length ? { kind: 'warn', ttl: 6000 } : {})
    return task
  } catch (e) { await notice(e.message); return null } finally { store.gen = null; await refreshSet().catch(() => {}) }
}
