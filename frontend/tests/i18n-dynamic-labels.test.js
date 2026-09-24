// 动态取 key 的 $t（发 tag 前审计 FE-04）：配乐页的 $t({ mood: '情绪', … }[key])、绘本分页表的 $t(cond ? '硬切' : '翻页')
// 不是字面量，字面量扫描扫不到；这里把这几张表的每个值都钉进英文字典，缺了英文界面会直接漏中文。
import { describe, it, expect, afterEach } from 'vitest'
import { t, setLang } from '../src/i18n.js'
import { en } from '../src/locales/en.js'

// 跟 MusicGenerate.vue / MusicNew.vue 的 brief 标签表、BookPages.vue 的翻页方式一致
const MUSIC_BRIEF_LABELS = { mood: '情绪', instruments: '乐器', avoid: '不要（避免）' }
const FLIP_LABELS = ['硬切', '翻页']

afterEach(() => setLang('zh'))

describe('i18n · 动态标签', () => {
  it('配乐 brief 的三个标签、绘本翻页方式的两个选项都有英文', () => {
    const keys = [...Object.values(MUSIC_BRIEF_LABELS), ...FLIP_LABELS]
    const missing = keys.filter((k) => !Object.prototype.hasOwnProperty.call(en, k))
    expect(missing).toEqual([])
    setLang('en')
    for (const k of keys) expect(t(k), k).not.toMatch(/[一-鿿]/)
  })
})
