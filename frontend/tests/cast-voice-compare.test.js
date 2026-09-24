// 角色页 · 对比试听（DESIGN §29）：勾 2–6 个音色、同一句台词并排出结果卡，「用这个」走现有绑定接口。api 全部 mock，不联网。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url.endsWith('/voice-compare')) return { cast_id: 'lin', default_text: '你怎么才来？', bound: '', runs: [
      { at: 1, text: '上一批。', items: [{ voice_id: 'b', name: '音色b', file: 'b-1.mp3', url: '/f/b-old.mp3', in_library: true }] },
    ] }
    return {}
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    if (url.endsWith('/voice-compare')) return { cast_id: 'lin', text: body.text || '你怎么才来？', items: [
      { voice_id: 'a', name: '音色a', url: '/f/a.mp3', file: 'a.mp3' },
      { voice_id: 'b', name: '音色b', error: '试听合成失败：网关 502' },
    ] }
    return { id: 'lin', voice_ref: body.voice_id }
  }),
  put: vi.fn(), del: vi.fn(), ep: (id) => `/api/episodes/storyvideo/${id}`, fileToBase64: vi.fn(),
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({
    projectId: 'EP1', detail: { locked: false, readonly_reason: '', shots: [] },
    cast: [{ id: 'lin', kind: 'character', name: '林小满', voice: '原描述', block: 'b', locks: [], variants: {}, used_in: ['S1'], voice_ref: '', voice_name: '' }],
    voices: [
      { id: 'a', name: '音色a', voice: 'zh-CN-a', has_sample: false },
      { id: 'b', name: '音色b', voice: 'zh-CN-b', has_sample: false },
      { id: 'c', name: '音色c', voice: 'zh-CN-c', has_sample: false },
      { id: 'd', name: '只有描述', voice: '', description: 'x', has_sample: false },
    ],
  })
  return { store, refreshProject: vi.fn(async () => { calls.push(['refresh']) }), withBusy: vi.fn(async (fn) => fn()), loadVoices: vi.fn(), reviseEdit: vi.fn() }
})

import Cast from '../src/views/project/Cast.vue'

async function mountCast() {
  const r = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div/>' } }, { path: '/voices', component: { template: '<div/>' } },
    { path: '/drama/p/:id/cast/:castId?', component: Cast }, { path: '/drama/p/:id/shots/:shotId', component: { template: '<div/>' } },
  ] })
  r.push('/drama/p/EP1/cast/lin'); await r.isReady()
  const w = mount(Cast, { global: { plugins: [r] } })
  await flushPromises()
  return w
}

beforeEach(() => { calls.length = 0 })

describe('Cast · 对比试听', () => {
  it('进页面拉已有结果；只列带 TTS 的音色；不满 2 个不能跑', async () => {
    const w = await mountCast()
    const panel = w.find('[data-test="voice-compare"]')
    expect(panel.exists()).toBe(true)
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/episodes/storyvideo/EP1/cast/lin/voice-compare')).toBe(true)
    expect(panel.find('[data-test="compare-pick-a"]').exists()).toBe(true)
    expect(panel.find('[data-test="compare-pick-d"]').exists()).toBe(false)
    expect(panel.find('[data-test="compare-text"]').attributes('placeholder')).toContain('你怎么才来？')
    // 已有的一批列出来了
    expect(panel.text()).toContain('上一批。')
    expect(panel.findAll('[data-test="compare-card"]').length).toBe(1)
    const run = panel.find('[data-test="compare-run"]')
    expect(run.attributes('disabled')).toBeDefined()
    await panel.find('[data-test="compare-pick-a"] input').setValue(true)
    expect(panel.text()).toContain('再选 1 个')
    expect(panel.find('[data-test="compare-run"]').attributes('disabled')).toBeDefined()
    await panel.find('[data-test="compare-pick-b"] input').setValue(true)
    expect(panel.find('[data-test="compare-run"]').attributes('disabled')).toBeUndefined()
  })

  it('跑对比：带勾选的音色与自定义文字；结果卡有 audio 或错误；「用这个」调绑定接口', async () => {
    const w = await mountCast()
    const panel = w.find('[data-test="voice-compare"]')
    await panel.find('[data-test="compare-pick-a"] input').setValue(true)
    await panel.find('[data-test="compare-pick-b"] input').setValue(true)
    await panel.find('[data-test="compare-text"]').setValue('  自定义一句 ')
    await panel.find('[data-test="compare-run"]').trigger('click')
    await flushPromises()
    const postCall = calls.find(c => c[0] === 'post' && c[1].endsWith('/voice-compare'))
    expect(postCall[2]).toEqual({ voices: ['a', 'b'], text: '自定义一句' })
    const runs = w.findAll('[data-test="compare-run-result"]')
    expect(runs.length).toBe(2)                         // 新一批排最前
    expect(runs[0].text()).toContain('自定义一句')
    const cards = runs[0].findAll('[data-test="compare-card"]')
    expect(cards.length).toBe(2)
    expect(cards[0].find('audio').exists()).toBe(true)
    expect(cards[0].find('audio').attributes('src')).toBe('/f/a.mp3')
    expect(cards[1].find('audio').exists()).toBe(false)
    expect(cards[1].text()).toContain('试听合成失败：网关 502')
    expect(cards[1].find('[data-test="compare-adopt"]').attributes('disabled')).toBeDefined()
    await cards[0].find('[data-test="compare-adopt"]').trigger('click')
    await flushPromises()
    const bind = calls.find(c => c[0] === 'post' && c[1] === '/api/episodes/storyvideo/EP1/cast/lin/voice')
    expect(bind[2]).toEqual({ voice_id: 'a' })
    expect(calls.some(c => c[0] === 'refresh')).toBe(true)
  })

  it('接口报错显示在面板里，不吞掉', async () => {
    const { post } = await import('../src/api.js')
    post.mockImplementationOnce(async () => { throw new Error('这个角色没有台词，填一句试听文字') })
    const w = await mountCast()
    const panel = w.find('[data-test="voice-compare"]')
    await panel.find('[data-test="compare-pick-a"] input').setValue(true)
    await panel.find('[data-test="compare-pick-c"] input').setValue(true)
    await panel.find('[data-test="compare-run"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="compare-error"]').text()).toBe('这个角色没有台词，填一句试听文字')
  })
})
