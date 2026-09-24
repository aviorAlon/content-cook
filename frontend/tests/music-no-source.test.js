// 配乐生成页 × 没有配乐来源：内置来源不覆盖这一档，得装一个提供它的插件——
// 页面说清楚、「生成」不可点，brief 和提示词照样能写；选过的来源插件没装上时，把健康检查给的原因摆出来。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('../src/api.js', () => ({
  get: vi.fn(async () => ({})), post: vi.fn(async () => ({})), put: vi.fn(async () => ({})), del: vi.fn(async () => ({})),
  ep: (id) => `/api/episodes/storyvideo/${id}`, pollTask: vi.fn(), pollJob: vi.fn(), sleep: vi.fn(async () => {}),
  fileToBase64: vi.fn(async () => 'QUJD'),
}))
vi.mock('../src/ui.js', () => ({
  prompt: vi.fn(), confirm: vi.fn(async () => true), notice: vi.fn(), toast: vi.fn(), custom: vi.fn(), toasts: [],
  T: { role: { music: '配乐' } },
}))

import { store } from '../src/store.js'
import MusicGenerate from '../src/views/music/MusicGenerate.vue'

const SPEC = {
  id: 'MU-1', module: 'music', title: '摇篮曲', brief: { usage: '睡前', genre: '摇篮曲', mood: [], instruments: [], avoid: [] },
  prompt_language: 'zh', duration: 60, count: 1, reference_images: [], music_params: { model: '@music' }, attempts: [], current: null, used_in: [],
}

beforeEach(() => {
  store.music = SPEC
  store.musicDetail = { id: 'MU-1', spec: SPEC, prompt: '用途：睡前', attempts: [], used_in: [], busy: { generate: null },
                        limits: { count: [1, 4], duration: [5, 600], bpm: [30, 240], max_references: 10 } }
})
afterEach(() => { store.health = null; store.music = null; store.musicDetail = null })

describe('配乐生成页 · 没有来源', () => {
  it('这一档一个来源都没有：提示要装插件，「生成」与「全部重出」不可点，提示词照常显示', async () => {
    store.health = { roles: { music: { adapter: '', configured: false, missing: ['music: 没选适配器'], sources: 0, unavailable: '' } } }
    const w = mount(MusicGenerate)
    await flushPromises()
    expect(w.find('[data-test="music-no-source"]').text()).toContain('要装一个提供它的插件')
    expect(w.find('[data-test="music-generate-btn"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="music-regenerate"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="music-prompt"]').text()).toContain('用途：睡前')
  })

  it('选过的来源没装上：摆出原因，「生成」与「全部重出」也不可点', async () => {
    const why = '「配乐」的来源 plug_music 现在用不了：它不是内置来源，提供它的插件没有加载——把插件放进 ~/.storystudio/plugins 后重启程序，或去「模型设置」给「配乐」换一个来源'
    store.health = { roles: { music: { adapter: 'plug_music', configured: false, sources: 0, unavailable: why } } }
    store.musicDetail = { ...store.musicDetail, attempts: [{ n: 1, ok: true, file: 'a.mp3' }] }
    const w = mount(MusicGenerate)
    await flushPromises()
    expect(w.find('[data-test="music-no-source"]').exists()).toBe(false)
    expect(w.find('[data-test="music-source-unavailable"]').text()).toBe(why)
    expect(w.find('[data-test="music-generate-btn"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="music-regenerate"]').attributes('disabled')).toBeDefined()
  })

  it('有来源（插件已装）或健康检查还没拉到：什么都不提示', async () => {
    store.health = { roles: { music: { adapter: 'demo_music', configured: true, sources: 1, unavailable: '' } } }
    let w = mount(MusicGenerate)
    await flushPromises()
    expect(w.find('[data-test="music-no-source"]').exists()).toBe(false)
    expect(w.find('[data-test="music-source-unavailable"]').exists()).toBe(false)
    store.health = null
    w = mount(MusicGenerate)
    await flushPromises()
    expect(w.find('[data-test="music-no-source"]').exists()).toBe(false)
    expect(w.find('[data-test="music-generate-btn"]').attributes('disabled')).toBeUndefined()
  })
})
