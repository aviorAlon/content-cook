// 本地任务轮询出错要有提示（发 tag 前审计 FE-06）：pollTask 在服务重启 / 任务记录 404 时抛错，
// Post（台词识别 / 后期合成）、Shots（首帧对比）、Final（导出交付包 / 海报）四处以前只有 try/finally 没有 catch——
// 进度条无声消失，错误只进控制台。这里钉住：错误不逃出组件，给一条 notice / 错误文案，store.gen 清掉，
// finally 里的刷新自己兜底。api 全 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const OFFLINE = '连不上本地服务（可能正在重启）——等几秒再试'
const calls = []
const refreshFail = { value: false }
const pollError = { value: null }   // null = 连不上本地服务；否则按这个 { status, message } 抛
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url.endsWith('/poster')) return { latest: null, history: [], suggested_title: '标题', suggested_subtitle: '', fonts: [], default_font: '', shots_with_frames: [], model: 'm', prompt: '', negative: '', ffmpeg_ok: true, busy_task: null }
    if (url.endsWith('/export')) return { packages: [] }
    if (url.includes('/compare/frames/')) return { candidates: [] }
    return {}
  }),
  post: vi.fn(async (url, body) => { calls.push(['post', url, body]); return { task_id: 't1', model: 'm', cost_note: '' } }),
  put: vi.fn(), del: vi.fn(), ep: (id) => `/api/episodes/storyvideo/${id}`, fileToBase64: vi.fn(),
  pollTask: vi.fn(async (id) => {
    calls.push(['poll', id])
    const { status = 0, message = OFFLINE } = pollError.value || {}
    const e = new Error(message); e.status = status; throw e
  }),
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({ projectId: 'EP1', detail: null, summary: null, cast: [], postLayers: null, voices: [], gen: null })
  return {
    store,
    refreshProject: vi.fn(async () => { calls.push(['refresh']); if (refreshFail.value) throw new Error(OFFLINE) }),
    withBusy: vi.fn(async (fn) => fn()),
    runGenerate: vi.fn(), retake: vi.fn(), review: vi.fn(), reviseEdit: vi.fn(), loadVoices: vi.fn(async () => []),
    cameraOptionsFor: () => ({ shots: [] }),
  }
})
vi.mock('../src/ui.js', async () => {
  const real = await vi.importActual('../src/ui.js')
  return { ...real, confirm: vi.fn(async () => true), notice: vi.fn(async (m) => { calls.push(['notice', m]) }), toast: vi.fn(), prompt: vi.fn(), custom: vi.fn() }
})
vi.mock('../src/stylecards.js', () => ({
  loadStyleCards: vi.fn(async () => {}), groupCards: () => [], cardName: (c) => c?.name || '', styleCardById: () => null, cardAnchor: () => '',
}))

import Final from '../src/views/project/Final.vue'
import Post from '../src/views/project/Post.vue'
import Shots from '../src/views/project/Shots.vue'
import { store } from '../src/store.js'

const escaped = []
async function mountView(component, path, url) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div/>' } }, { path, component }, { path: '/drama/p/:id/:rest(.*)*', component: { template: '<div/>' } },
  ] })
  await router.push(url); await router.isReady()
  const w = mount(component, { global: { plugins: [router], config: { errorHandler: (e) => escaped.push(String(e?.message || e)) } } })
  await flushPromises()
  return w
}
const shot = (id) => ({
  id, dur: 4, cast: [], ref_frame: null, camera_move: '', shot: '中景', art: 'a', motion: 'm', sfx: '', entry_state: '', exit_state: '',
  purpose: '', scene_type: '', readable_text: [], entry: null, exit: null, axis: null, boundary_change: '', withhold: [], lines: [],
  budget: null, frame_url: null, video_url: null, links: { referenced_by: [], voice_over: [], inserts_after: [], notebook_text: [] },
})
const notices = () => calls.filter(([m]) => m === 'notice').map(([, msg]) => msg)

beforeEach(() => {
  calls.length = 0; escaped.length = 0; refreshFail.value = false; pollError.value = null
  store.gen = null
  store.detail = { id: 'EP1', editable: false, locked: true, final_url: null, has_post_layers: false, shots: [shot('S1')] }
  store.postLayers = { id: 'EP1', shot_ids: ['S1'], voice_over: { lines: [] }, post_graphics: {}, subtitle: {}, subtitle_effective: {}, subtitle_defaults: {}, fonts_available: [], overlays: { regions: [], presets: [] }, readiness: {} }
})

describe('pollTask 抛错（服务重启 / 任务 404）', () => {
  it('Final · 导出交付包：弹 notice，错误不逃出组件，进度条清掉', async () => {
    const w = await mountView(Final, '/drama/p/:id/final', '/drama/p/EP1/final')
    await w.find('[data-test="export-card"] button').trigger('click'); await flushPromises()
    expect(calls.some(([m, id]) => m === 'poll' && id === 't1')).toBe(true)
    expect(escaped).toEqual([])
    expect(notices()).toEqual([expect.stringContaining(OFFLINE)])
    expect(notices()[0]).toContain('打包失败')
    expect(store.gen).toBe(null)
  })

  it('Final · 模型生成海报：错误落到海报卡的错误文案，不逃出组件', async () => {
    const w = await mountView(Final, '/drama/p/:id/final', '/drama/p/EP1/final')
    await w.find('[data-test="poster-generate"]').trigger('click'); await flushPromises()
    expect(escaped).toEqual([])
    expect(w.find('[data-test="poster-error"]').text()).toContain(OFFLINE)
    expect(store.gen).toBe(null)
  })

  it('Post · 后期合成：弹 notice；finally 里的刷新也连不上时不再抛第二次', async () => {
    refreshFail.value = true
    const w = await mountView(Post, '/drama/p/:id/post', '/drama/p/EP1/post')
    await w.findAll('button').find((b) => b.text() === '生成后期合成版').trigger('click'); await flushPromises()
    expect(escaped).toEqual([])
    expect(notices()).toEqual([expect.stringContaining(OFFLINE)])
    expect(notices()[0]).toContain('失败')
    expect(store.gen).toBe(null)
  })

  it('Post · 台词识别：任务记录 404 也弹 notice（带后端原话），不逃出组件', async () => {
    pollError.value = { status: 404, message: '没有这个任务：t1' }
    const w = await mountView(Post, '/drama/p/:id/post', '/drama/p/EP1/post')
    await w.findAll('button').find((b) => b.text() === '台词识别').trigger('click'); await flushPromises()
    expect(calls.some(([m, url]) => m === 'post' && url.endsWith('/asr-qc'))).toBe(true)
    expect(escaped).toEqual([])
    expect(notices()).toEqual([expect.stringContaining('没有这个任务：t1')])
    expect(notices()[0]).toContain('失败')
    expect(store.gen).toBe(null)
  })

  it('Shots · 首帧对比试跑：弹 notice，进度条清掉', async () => {
    const w = await mountView(Shots, '/drama/p/:id/shots/:shotId?', '/drama/p/EP1/shots/S1')
    const section = w.find('[data-test="compare-section"]')
    await section.find('input').setValue('m1, m2')
    await section.findAll('button').find((b) => b.text() === '各出一张').trigger('click'); await flushPromises()
    expect(calls.some(([m, url]) => m === 'post' && url.endsWith('/compare/frames'))).toBe(true)
    expect(escaped).toEqual([])
    expect(notices()).toEqual([expect.stringContaining(OFFLINE)])
    expect(notices()[0]).toContain('出候选失败')
    expect(store.gen).toBe(null)
  })
})
