// 出图风格卡（docs/image-style-cards.md §1 / §5）的前端胶水：整个卡库只拉一次（`GET /api/style-cards`），
// 缓存在 store.styleCards，所有入口（图片集新建 / 清单、绘本角色页、成片页海报卡）共用这一份。
// 后端还没落地时接口返回什么都不炸：归一化后是 {categories: [], cards: [], byId: {}}，界面只少了卡片区。
import { get } from './api.js'
import { store } from './store.js'
import { t, isEn } from './i18n.js'

// 色板小色块：只认表里有的色名，认不出的给中性块（不猜十六进制，docs/image-style-cards.md §1）
const SWATCHES = {
  // 白与米
  '纯白': '#ffffff', '白': '#ffffff', '暖白': '#f8f2e6', '冷白': '#f0f4f6', '奶油白': '#faf3e3', '奶油': '#f7ecd8',
  '象牙白': '#f6f1e4', '米白': '#f5f0e3', '米色': '#e8dcc0', '米黄': '#efe3c2', '沙色': '#ddc9a3', '燕麦色': '#e4d8bf',
  // 灰与黑
  '浅灰': '#cfcac2', '中灰': '#7d7873', '暖灰': '#a8a196', '冷灰': '#9aa3a8', '雾灰': '#b8b3ab', '深灰': '#4a4642',
  '炭黑': '#2b2724', '黑': '#1c1917', '银': '#c8cdd0',
  // 木与棕
  '浅木色': '#d8b58a', '原木': '#c8a374', '木色': '#b98d5f', '卡其': '#b6a17a', '驼色': '#c8a17a',
  '焦糖': '#a9662f', '暖棕': '#8a5a3b', '棕': '#6f4a2f', '深棕': '#4a2f1f', '巧克力': '#4b2e21', '铜': '#a8683a',
  // 红橙黄
  '正红': '#d02a24', '红': '#c0392b', '砖红': '#a94438', '酒红': '#6e2733', '珊瑚红': '#f0705a', '玫瑰金': '#d9a08a',
  '橙': '#e07b39', '暖橙': '#e8853a', '杏橙': '#f0b27a', '淡杏': '#f2d9bd', '桃色': '#f6c1b4',
  '明黄': '#f2c744', '柠檬黄': '#f4e07a', '奶黄': '#f6e7b4', '淡黄': '#f7edc0', '芥末黄': '#c9a227', '金': '#c9a227',
  // 绿青蓝
  '嫩绿': '#8fbf6a', '绿': '#4f7a4a', '橄榄绿': '#6b7a3a', '苔绿': '#7d8c5c', '竹青': '#9fb08a', '墨绿': '#2f4f3a',
  '薄荷绿': '#a8d5c2', '青绿': '#3f8f80', '天青': '#9dbfd0', '湖蓝': '#3a8fa8', '天蓝': '#8fc1e3', '浅蓝': '#bcd8ea',
  '雾霾蓝': '#8fa3b8', '蓝': '#3b5f9e', '靛蓝': '#3b4d80', '藏青': '#2a3550', '深蓝': '#26385c',
  // 粉紫
  '柔粉': '#f3c9cd', '粉': '#f2b8c6', '藕粉': '#e8c4c9', '裸粉': '#e9c9bd', '米粉': '#f2ded4',
  '淡紫': '#c3b0d8', '薰衣草': '#b9aee0', '紫': '#6b4c8a',
  // 英文卡的色名
  'white': '#ffffff', 'off-white': '#f5f0e3', 'cream': '#f7ecd8', 'ivory': '#f6f1e4', 'beige': '#e8dcc0',
  'sand': '#ddc9a3', 'khaki': '#b6a17a', 'tan': '#c8a374', 'brown': '#6f4a2f', 'chocolate': '#4b2e21',
  'black': '#1c1917', 'charcoal': '#2b2724', 'gray': '#7d7873', 'grey': '#7d7873', 'silver': '#c8cdd0',
  'gold': '#c9a227', 'copper': '#a8683a', 'red': '#c0392b', 'crimson': '#a94438', 'coral': '#f0705a',
  'orange': '#e07b39', 'amber': '#e8a33a', 'peach': '#f6c1b4', 'yellow': '#f2c744', 'lemon': '#f4e07a',
  'mustard': '#c9a227', 'green': '#4f7a4a', 'olive': '#6b7a3a', 'moss': '#7d8c5c', 'mint': '#a8d5c2',
  'teal': '#3f8f80', 'sky blue': '#8fc1e3', 'blue': '#3b5f9e', 'indigo': '#3b4d80', 'navy': '#2a3550',
  'purple': '#6b4c8a', 'lavender': '#b9aee0', 'violet': '#6b4c8a', 'pink': '#f2b8c6', 'blush': '#e8c4c9', 'rose': '#e9a3ae',
}

// 认得出 → 实色圆点；认不出 → 中性虚线圆点（鼠标悬停能看到原色名）
export function swatchStyle(name) {
  const hex = SWATCHES[String(name || '').trim()] || SWATCHES[String(name || '').trim().toLowerCase()]
  return hex
    ? { background: hex, border: '1px solid var(--border)' }
    : { background: 'var(--panel3)', border: '1px dashed var(--border)' }
}
export const knownSwatch = (name) => !!(SWATCHES[String(name || '').trim()] || SWATCHES[String(name || '').trim().toLowerCase()])

function normalizeCard(c) {
  const id = c.id || c.key || ''
  return {
    ...c,
    id,
    name: c.name || c.english || id,
    english: c.english || '',
    category: c.category || 'other',
    summary: c.summary || c.description || '',
    anchor_zh: c.anchor_zh || c.anchor || '',
    anchor_en: c.anchor_en || '',
    palette: Array.isArray(c.palette) ? c.palette.filter(Boolean).map(String) : [],
    fits: Array.isArray(c.fits) ? c.fits.filter(Boolean).map(String) : [],
    constraints: Array.isArray(c.constraints) ? c.constraints.filter(Boolean).map(String) : [],
    lighting: c.lighting || '', materials: c.materials || '', camera: c.camera || '',
    model_hint: c.model_hint || 'any',
  }
}

// {categories, cards} / 直接给数组 / {items} 都认；分类缺席时从卡里补一条「其他」
export function normalizeLibrary(r) {
  const cards = (Array.isArray(r) ? r : (r?.cards || r?.items || [])).map(normalizeCard).filter(c => c.id)
  const cats = (r?.categories || []).map(c => ({ id: c.id || c.key || '', name: c.name || c.english || c.id || '', english: c.english || '' })).filter(c => c.id)
  const known = new Set(cats.map(c => c.id))
  for (const c of cards) if (!known.has(c.category)) { known.add(c.category); cats.push({ id: c.category, name: c.category === 'other' ? '其他' : c.category, english: '' }) }
  const byId = {}
  for (const c of cards) byId[c.id] = c
  return { categories: cats, cards, byId }
}

const EMPTY = { categories: [], cards: [], byId: {} }
let inflight = null

// 整个卡库只拉一次（并发调用共用同一个请求）；force 用于设置页之类的手动刷新
export async function loadStyleCards({ force = false } = {}) {
  if (!force && store.styleCards) return store.styleCards
  if (!force && inflight) return inflight
  inflight = (async () => {
    let r = null
    try { r = await get('/api/style-cards') } catch { r = null }
    store.styleCards = normalizeLibrary(r)
    return store.styleCards
  })()
  try { return await inflight } finally { inflight = null }
}

export const cachedStyleCards = () => store.styleCards || EMPTY
export const styleCardById = (id) => (id ? cachedStyleCards().byId[id] || null : null)
export const isStyleCardId = (id) => !!styleCardById(id)

// 卡的锚句：界面语言优先，缺了用另一种（锚句永远不为空）
export const cardAnchor = (card) => (!card ? '' : ((isEn() ? card.anchor_en : card.anchor_zh) || card.anchor_zh || card.anchor_en || ''))
export const cardName = (card) => (!card ? '' : ((isEn() && card.english) ? card.english : card.name))
export const categoryName = (cat) => (!cat ? '' : ((isEn() && cat.english) ? cat.english : t(cat.name)))

// 「适合」判据：没写 fits 的卡到处都能用；写了的只有命中才算适合（筛选用，不做硬限制）
export const cardFits = (card, fits) => (!fits || !card?.fits?.length ? true : card.fits.includes(fits))

// 按分类分组：{id, name, cards}，空分类不出现；fits 为空或 only=false 时不筛
export function groupCards(fits = '', { only = false, keep = '' } = {}, lib = cachedStyleCards()) {
  const pass = (c) => (!only || cardFits(c, fits) || c.id === keep)
  return lib.categories
    .map(cat => ({ id: cat.id, name: categoryName(cat), cards: lib.cards.filter(c => c.category === cat.id && pass(c)) }))
    .filter(g => g.cards.length)
}

// 「适合」标签：模块级的写成人话，`<模块>.<模板>` 原样显示（够短、够准）
const FIT_LABELS = { picturebook: '绘本', poster: '海报' }
export const fitLabel = (f) => (FIT_LABELS[f] ? t(FIT_LABELS[f]) : String(f || ''))

// ---- 图像方言（docs/image-style-cards.md §2）：同一张卡按目标模型换组织方式。
// 界面只做两件事：把当前方言摆出来（自动判的也说清判成了什么），以及允许手选覆盖。
// 后端还没落地 `image_dialect` 时，选「自动」就不往项目文件里写这个键——老项目逐字节不变。
export const IMAGE_DIALECTS = [
  { id: '', name: '自动', hint: '按模型 id 猜：含 gpt-image 走五段带标签，含 nano-banana / gemini-image 走自然语言，其余按通用写法。' },
  { id: 'generic', name: '通用', hint: '风格锚 + 主体 + 画面 + 约束 + 无文字尾句，一段连写（老项目的写法）。' },
  { id: 'gpt_image', name: 'GPT Image', hint: '五段带标签（Scene / Subject / Key details / Intended use / Constraints）；不写「惊艳」「史诗」这类空话；画面里的文字用引号原样给。' },
  { id: 'nano_banana', name: 'Nano Banana', hint: '自然语言一到两段；不写数字焦段光圈（模型会忽略）；结尾单起一行 Aspect ratio: 比例。' },
]
export const dialectInfo = (id) => IMAGE_DIALECTS.find(d => d.id === (id || '')) || null
export const dialectName = (id) => { const d = dialectInfo(id); return d ? t(d.name) : String(id || '') }
export const dialectHint = (id) => { const d = dialectInfo(id); return d ? t(d.hint) : '' }

// 当前方言：项目文件里手写的 `image_dialect` 优先；否则用 detail 里后端自动判出来的；都没有就是「自动」。
export function dialectOf(detail, spec) {
  const override = spec?.image_dialect || spec?.image_params?.image_dialect || ''
  const detected = detail?.image_dialect || detail?.dialect || detail?.image?.dialect || detail?.image_params?.image_dialect || ''
  return { override, detected, effective: override || detected || '' }
}
// 可选项：后端给了 `image_dialects` 就以它为准（不认识的 id 也列出来），否则用内置四条
export function dialectOptions(detail) {
  const raw = detail?.image_dialects || detail?.dialects || null
  if (!Array.isArray(raw) || !raw.length) return IMAGE_DIALECTS
  const out = [IMAGE_DIALECTS[0]]
  for (const x of raw) {
    const id = (typeof x === 'string' ? x : x?.id) || ''
    if (!id || out.some(o => o.id === id)) continue
    out.push(dialectInfo(id) || { id, name: id, hint: '' })
  }
  return out
}
