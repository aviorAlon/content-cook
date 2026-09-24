// 后期层（PUT /api/episodes/storyvideo/{id}/post-layers）的 body 拼法。
// 后端整块替换 post_graphics / voice_over / subtitle，所以前端只能「展开原对象再覆盖已知字段」：
// 界面只编辑它认识的键，不认识的键——静音区间 silence、文字角标 labels、逐句旁白声线 lines[i].edge、
// 旁白字幕 voice_over.subtitles、字幕卡的 background / motion，以及以后再加的——原样带回去，保存一次不会被冲掉。

export const START_ANCHOR = '__start__'   // 插卡的 after 写这个 = 插在第一镜之前（片头卡）

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)
const blank = (v) => v === '' || v === null || v === undefined

// 原对象（不是对象就当空）上覆盖 patch；patch 里值为 undefined 的键 = 删掉这个键
export function overlay(base, patch) {
  const out = { ...(isObj(base) ? base : {}) }
  for (const [k, v] of Object.entries(patch)) { if (v === undefined) delete out[k]; else out[k] = v }
  return out
}

// post_graphics.dubbing：mode / duck 覆盖；只在「只配指定的镜」时带 shots，别的模式把旧的 shots 清掉
export function dubbingBlock(current, dub) {
  return overlay(current, { mode: dub.mode, duck: Number(dub.duck), shots: dub.mode === 'shots' ? [...dub.shots] : undefined })
}

// post_graphics.music：每条在原条目上覆盖界面里的八个字段；没选文件的空行不存
export function musicRows(rows) {
  return rows.filter(m => m.file).map(m => overlay(m, {
    label: m.label || undefined, file: m.file, start: Number(m.start) || 0, end: blank(m.end) ? null : Number(m.end),
    gain: Number(m.gain), duck: Number(m.duck), fade_in: Number(m.fade_in), fade_out: Number(m.fade_out),
  }))
}

// post_graphics 上换一块：value 为空数组 / undefined 时删掉这块
export function withGraphics(pg, key, value) {
  return overlay(pg, { [key]: Array.isArray(value) && !value.length ? undefined : value })
}

// spec.subtitle 里的旧别名：fontfile 就是 font_path（后端取字体是 font_path || fontfile）。
// 表单的字体下拉只管 font_path，保存时要把别名一起删掉：不然选「自动」清掉 font_path 后，成片仍按 fontfile 那款字画，
// 界面显示的和实际用的对不上，而且再也切不回默认字体
const SUBTITLE_ALIASES = { fontfile: 'font_path' }

// spec.subtitle：表单里的键覆盖原对象，清空的键删掉（回落默认值），表单不认识的键原样保留；表单管着的键的旧别名删掉
export function subtitleBlock(current, form, keys, numericKeys = []) {
  const patch = {}
  for (const k of keys) {
    const v = form[k]
    patch[k] = blank(v) ? undefined : (numericKeys.includes(k) ? Number(v) : v)
  }
  for (const [alias, key] of Object.entries(SUBTITLE_ALIASES)) if (keys.includes(key)) patch[alias] = undefined
  return overlay(current, patch)
}

// 字幕实际用的字体（表单显示用）：font_path 没写就看旧别名 fontfile，跟后端的取法一致
export function subtitleFontOf(subtitle) {
  return (isObj(subtitle) && (subtitle.font_path || subtitle.fontfile)) || ''
}

// 插卡「文字（每行一条）」文本框 → lines。字幕卡（layout=card）保留中间的空行——后端把空串行当行间空白，
// 片尾字幕卡靠它分段——只去掉首尾的空行；别的版式照旧丢掉空行
export function insertLinesFromText(text, layout) {
  const rows = String(text ?? '').replace(/\r\n?/g, '\n').split('\n')
  if (layout !== 'card') return rows.filter(Boolean)
  let a = 0, b = rows.length
  while (a < b && !rows[a].trim()) a++
  while (b > a && !rows[b - 1].trim()) b--
  return rows.slice(a, b)
}

// voice_over.edge：音色 / 语速 / 音调，留空的删掉（跟随模型设置），别的键保留
export function edgeBlock(current, form) {
  const patch = {}
  for (const k of ['voice', 'rate', 'pitch']) patch[k] = String(form[k] ?? '').trim() ? form[k] : undefined
  return overlay(current, patch)
}

// 插卡 id 能不能用（跟后端 post.py 的 _validate_inserts 同一条规则）：中文照收，只拦路径分隔符、Windows 文件名保留字符、
// % 和控制字符，最长 64 字。在输入框下面先提示，免得点保存才被 400 拦下（2026-09-23 发布前自查 F1 / F6）
export const INSERT_ID_FORBIDDEN = '/\\:*?"<>|%'
export function insertIdProblem(id) {
  const text = String(id ?? '').trim()
  if (!text) return ''
  // eslint-disable-next-line no-control-regex
  if (text.length > 64 || [...text].some(ch => INSERT_ID_FORBIDDEN.includes(ch) || /[\u0000-\u001f\u007f]/.test(ch))) {
    return 'id 不能超过 64 个字，也不能含 / \\ : * ? " < > | % 或控制字符——改成 UI_2 这样的写法'
  }
  return ''
}

// 插卡缩略图 URL 的缓存戳：按内容哈希（FNV-1a，36 进制）。以前用 JSON 长度，把 09:00 改成 10:30 这类等长改动后
// URL 一字不变，浏览器连请求都不发，缩略图停在旧图；后端本来就按 [插卡, 字体] 的内容哈希缓存，这里跟它对齐
export function contentStamp(value) {
  const text = JSON.stringify(value)
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) }
  return (h >>> 0).toString(36)
}
