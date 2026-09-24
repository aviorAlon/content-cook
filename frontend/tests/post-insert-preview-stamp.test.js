// 插卡缩略图的缓存戳（发 tag 前审计 FE-07）：以前用 JSON 长度当戳，把 09:00 改成 10:30 这类等长改动后 URL 一字不变，
// 浏览器连请求都不发，缩略图停在旧图。现在戳按内容哈希：内容不变不多发请求，内容一变 URL 必变。api 全 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const clone = (x) => JSON.parse(JSON.stringify(x))
const puts = []
vi.mock('../src/api.js', () => ({
  get: vi.fn(async () => ({})), post: vi.fn(async () => ({})),
  put: vi.fn(async (url, body) => { puts.push([url, clone(body)]); return { status: 'saved' } }),
  del: vi.fn(), ep: (id) => `/api/episodes/storyvideo/${id}`, pollTask: vi.fn(), fileToBase64: vi.fn(),
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({ projectId: 'EP1', detail: { id: 'EP1', locked: false }, postLayers: null, cast: [], voices: [], gen: null })
  return {
    store,
    // 真刷新的样子：把刚 PUT 的块换回 store（新对象）
    refreshProject: vi.fn(async () => { const next = clone(store.postLayers); const last = puts[puts.length - 1]; if (last) Object.assign(next, last[1]); store.postLayers = next }),
    withBusy: vi.fn(async (fn) => fn()), loadVoices: vi.fn(async () => []), reviseEdit: vi.fn(),
  }
})
vi.mock('../src/ui.js', () => ({ confirm: vi.fn(async () => true), notice: vi.fn(), toast: vi.fn(), prompt: vi.fn(), T: {} }))

import { store } from '../src/store.js'
import Post from '../src/views/project/Post.vue'
import { contentStamp } from '../src/postlayers.js'

function postLayers(time = '09:00') {
  return {
    id: 'EP1', locked: false, shot_ids: ['S1', 'S2'], voice_over: { lines: [] },
    post_graphics: { phone_ui_inserts: [{ id: 'UI_1', after: 'S1', dur: 3, layout: 'notification', app: '微信', title: '通知', time, items: ['第一行'] }], notebook_text: [], fonts: { ui: 'C:/Windows/Fonts/msyh.ttc' } },
    subtitle: {}, subtitle_effective: {}, subtitle_defaults: {}, fonts_available: [], overlays: { preset: 'none', custom: false, regions: [], presets: [] }, readiness: {},
  }
}
async function mountPost() {
  const r = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div/>' } }, { path: '/drama/p/:id/:rest(.*)*', component: { template: '<div/>' } }] })
  await r.push('/'); await r.isReady()
  const w = mount(Post, { global: { plugins: [r] } })
  await flushPromises()
  return w
}
const previewSrc = (w) => w.findAll('img').find((i) => i.attributes('src').includes('/insert-preview/')).attributes('src')
const timeInput = (w) => w.findAll('.field').find((f) => f.text().startsWith('时间')).find('input')

beforeEach(() => { puts.length = 0; store.postLayers = postLayers() })

describe('contentStamp', () => {
  it('内容相同戳相同；等长的不同内容戳不同；字体也算进去', () => {
    const a = { id: 'UI_1', time: '09:00' }
    expect(contentStamp([a, 'f'])).toBe(contentStamp([clone(a), 'f']))
    expect(contentStamp([{ id: 'UI_1', time: '09:00' }, 'f'])).not.toBe(contentStamp([{ id: 'UI_1', time: '10:30' }, 'f']))
    expect(contentStamp([a, 'f1'])).not.toBe(contentStamp([a, 'f2']))
    expect(contentStamp([a, 'f'])).toMatch(/^[0-9a-z]+$/)
  })
})

describe('Post.vue · 插卡缩略图', () => {
  it('时间 09:00 → 10:30（等长）保存后：<img src> 跟着变', async () => {
    const w = await mountPost()
    const before = previewSrc(w)
    expect(before).toContain('/api/episodes/storyvideo/EP1/post-layers/insert-preview/UI_1?v=')
    const row = w.findAll('tr').find((tr) => tr.text().includes('UI_1'))
    await row.findAll('button').find((b) => b.text() === '改').trigger('click'); await flushPromises()
    await timeInput(w).setValue('10:30')
    await w.findAll('button').find((b) => b.text() === '保存').trigger('click'); await flushPromises()
    expect(puts[0][1].post_graphics.phone_ui_inserts[0].time).toBe('10:30')
    expect(store.postLayers.post_graphics.phone_ui_inserts[0].time).toBe('10:30')
    expect(previewSrc(w)).not.toBe(before)
  })

  it('内容没变（刷新换成同内容的新对象）：src 一字不变，不多发请求', async () => {
    const w = await mountPost()
    const before = previewSrc(w)
    store.postLayers = postLayers(); await flushPromises()
    expect(previewSrc(w)).toBe(before)
  })
})
