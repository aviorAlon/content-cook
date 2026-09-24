// 轻量 i18n：中文原文就是 key，缺翻译回落中文（界面永远不会出现空白 key）。
// 用法：模板里 {{ $t('概览') }}；脚本里 import { t } 后 t('还有 {n} 镜没有可用首帧', { n })。
// 语言存 localStorage，切换后所有模板因为读了 lang.value 而自动重渲染；api.js 把语言带给后端。
import { ref } from 'vue'
import { en } from './locales/en.js'

const KEY = 'storystudio_lang'
export const LANGS = [{ id: 'zh', label: '中文' }, { id: 'en', label: 'English' }]
const TABLES = { en }

function detect() {
  try { const saved = localStorage.getItem(KEY); if (saved && TABLES[saved] !== undefined || saved === 'zh') return saved } catch { /* 隐私模式等 */ }
  const nav = (typeof navigator !== 'undefined' && navigator.language) || 'zh'
  return nav.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

export const lang = ref(detect())

export function setLang(id) {
  if (!LANGS.some((l) => l.id === id)) return
  lang.value = id
  try { localStorage.setItem(KEY, id) } catch { /* ignore */ }
  if (typeof document !== 'undefined') document.documentElement.lang = id === 'zh' ? 'zh-CN' : 'en'
}

export function t(text, params) {
  const table = TABLES[lang.value]
  let out = (table && Object.prototype.hasOwnProperty.call(table, text)) ? table[text] : text
  if (params) for (const [k, v] of Object.entries(params)) out = out.split(`{${k}}`).join(String(v))
  return out
}

// 给 store/ui 这类非组件模块用：按当前语言在两段文案里选一段（不想进字典的一次性文案）
export const isEn = () => lang.value === 'en'
