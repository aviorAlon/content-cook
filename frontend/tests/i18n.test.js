import { describe, it, expect, beforeEach, vi } from 'vitest'
import { t, lang, setLang, LANGS } from '../src/i18n.js'
import { en } from '../src/locales/en.js'

describe('i18n', () => {
  beforeEach(() => setLang('zh'))

  it('中文原文就是 key：中文原样返回，英文查表，缺翻译回落中文', () => {
    const key = Object.keys(en)[0]
    expect(key).toBeTruthy()
    expect(t(key)).toBe(key)
    setLang('en')
    expect(t(key)).toBe(en[key])
    expect(t('这句肯定没有翻译 zzz')).toBe('这句肯定没有翻译 zzz')
  })

  it('占位符替换在两种语言下都生效', () => {
    expect(t('还有 {n} 镜', { n: 3 })).toBe('还有 3 镜')
    setLang('en')
    expect(t('x {a} y {a} z {b}', { a: 1, b: 2 })).toBe('x 1 y 1 z 2')
  })

  it('切换语言会持久化并改 <html lang>；不认识的语言忽略', () => {
    setLang('en')
    expect(lang.value).toBe('en')
    expect(localStorage.getItem('storystudio_lang')).toBe('en')
    expect(document.documentElement.lang).toBe('en')
    setLang('fr')
    expect(lang.value).toBe('en')
    setLang('zh')
    expect(document.documentElement.lang).toBe('zh-CN')
    expect(LANGS.map((l) => l.id)).toEqual(['zh', 'en'])
  })

  it('英文字典：每个值都是非空字符串，占位符和中文 key 一致', () => {
    const bad = []
    for (const [k, v] of Object.entries(en)) {
      if (typeof v !== 'string' || !v.trim()) { bad.push(k); continue }
      const kp = (k.match(/\{[a-zA-Z_]+\}/g) || []).sort().join(',')
      const vp = (v.match(/\{[a-zA-Z_]+\}/g) || []).sort().join(',')
      if (kp !== vp) bad.push(`${k} → ${v}`)
      if (/[一-鿿]/.test(v)) bad.push(`still Chinese: ${k}`)
    }
    expect(bad).toEqual([])
  })

  it('api 请求带语言头', async () => {
    const { api } = await import('../src/api.js')
    let seen
    globalThis.fetch = vi.fn(async (url, init) => { seen = init; return { ok: true, json: async () => ({}) } })
    setLang('en')
    await api('/api/x')
    expect(seen.headers['X-StoryStudio-Lang']).toBe('en')
  })
})
