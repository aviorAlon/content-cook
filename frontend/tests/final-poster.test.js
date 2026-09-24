// 成片页 · 海报卡（DESIGN §36）：预填标题/副标题、来源单选、模型来源先确认再发 1 次生图、首帧来源直接出、只重排文字。api 全部 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let posterState
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url.endsWith('/poster')) return posterState
    if (url.endsWith('/export')) return { packages: [] }
    if (url.startsWith('/api/tasks/')) return { status: 'succeeded', result: {} }
    return {}
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    if (url.endsWith('/poster/generate') && body.source === 'model') return { task_id: 't1', model: 'gpt-image-2', cost_note: '1 次生图请求（gpt-image-2）' }
    return { entry: {}, ...posterState }
  }),
  put: vi.fn(), del: vi.fn(), ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(async (id) => { calls.push(['poll', id]); return { status: 'succeeded', result: {} } }),
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({ projectId: 'EP1', detail: { id: 'EP1', final_url: null, has_post_layers: false }, summary: null, gen: null })
  return { store, runGenerate: vi.fn(), review: vi.fn(), withBusy: vi.fn(async (fn) => fn()), refreshProject: vi.fn(async () => { calls.push(['refresh']) }) }
})
const confirmMock = vi.fn(async () => true)
vi.mock('../src/ui.js', () => ({
  prompt: vi.fn(), confirm: (...a) => confirmMock(...a), notice: vi.fn(), toast: vi.fn(), T: {},
}))

import Final from '../src/views/project/Final.vue'

async function mountFinal() {
  const r = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div/>' } }, { path: '/drama/p/:id/post', component: { template: '<div/>' } }] })
  await r.push('/'); await r.isReady()
  const w = mount(Final, { global: { plugins: [r] } })
  await flushPromises()
  return w
}

beforeEach(() => {
  calls.length = 0
  confirmMock.mockReset(); confirmMock.mockResolvedValue(true)
  posterState = {
    latest: null, history: [], suggested_title: '夜色下的第七封信', suggested_subtitle: '她以为烧掉的信回来了',
    fonts: [{ path: 'C:/f/msyh.ttc', label: '微软雅黑' }], default_font: 'C:/f/msyh.ttc', shots_with_frames: ['S1', 'S2'],
    model: 'gpt-image-2', cost_note: '1 次生图请求（gpt-image-2）', prompt: 'p', negative: 'n', ffmpeg_ok: true, busy_task: null,
  }
})

describe('Final · 海报卡', () => {
  it('进页面拉海报；标题/副标题预填；没有海报时显示空位；模型来源要先确认再发请求并轮询', async () => {
    const w = await mountFinal()
    const card = w.find('[data-test="poster-card"]')
    expect(card.exists()).toBe(true)
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/episodes/storyvideo/EP1/poster')).toBe(true)
    expect(card.find('[data-test="poster-empty"]').exists()).toBe(true)
    expect(card.find('[data-test="poster-title"]').element.value).toBe('夜色下的第七封信')
    expect(card.find('[data-test="poster-subtitle"]').element.value).toBe('她以为烧掉的信回来了')
    expect(card.find('[data-test="poster-rerender"]').attributes('disabled')).toBeDefined()   // 还没底图
    // 用户取消确认 → 不发请求
    confirmMock.mockResolvedValueOnce(false)
    await card.find('[data-test="poster-generate"]').trigger('click')
    await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    // 确认 → 一次 generate（source=model）→ 轮询任务 → 重新拉海报 + 刷新项目
    await card.find('[data-test="poster-generate"]').trigger('click')
    await flushPromises()
    expect(confirmMock.mock.calls[1][0]).toContain('1 次生图请求')
    const gen = calls.find(c => c[0] === 'post' && c[1].endsWith('/poster/generate'))
    expect(gen[2]).toEqual({ source: 'model', title: '夜色下的第七封信', subtitle: '她以为烧掉的信回来了', font: null, shot_id: null, layout: 'auto', kicker: '' })
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't1')).toBe(true)
    expect(calls.filter(c => c[0] === 'get' && c[1].endsWith('/poster')).length).toBe(2)
    expect(calls.some(c => c[0] === 'refresh')).toBe(true)
  })

  it('首帧来源：选镜、不弹确认、直接出；有海报后显示预览与历史；只重排文字走 render 接口', async () => {
    const w = await mountFinal()
    const card = w.find('[data-test="poster-card"]')
    await card.find('[data-test="poster-source-frame"]').setValue(true)
    expect(card.find('[data-test="poster-shot"]').exists()).toBe(true)
    await card.find('[data-test="poster-shot"]').setValue('S2')
    await card.find('[data-test="poster-subtitle"]').setValue('')
    posterState = { ...posterState, latest: { url: '/f/poster-2.png', base_url: '/f/base-2.png', poster: 'poster-2.png', title: '夜色下的第七封信', subtitle: '', source: 'frame', shot_id: 'S2', created_at: 1757000000 },
      history: [{ url: '/f/poster-2.png', poster: 'poster-2.png', title: 'b' }, { url: '/f/poster-1.png', poster: 'poster-1.png', title: 'a' }] }
    await card.find('[data-test="poster-generate"]').trigger('click')
    await flushPromises()
    expect(confirmMock).not.toHaveBeenCalled()
    const gen = calls.find(c => c[0] === 'post' && c[1].endsWith('/poster/generate'))
    expect(gen[2]).toEqual({ source: 'frame', title: '夜色下的第七封信', subtitle: '', font: null, shot_id: 'S2', layout: 'auto', kicker: '' })
    expect(w.find('[data-test="poster-preview"]').attributes('src')).toBe('/f/poster-2.png')
    expect(w.findAll('[data-test="poster-history"]').length).toBe(2)
    expect(w.text()).toContain('来自首帧 S2')
    // 只重排文字：改标题、选字体 → render 接口，不再 generate
    await w.find('[data-test="poster-title"]').setValue('第七封信')
    await w.find('[data-test="poster-font"]').setValue('C:/f/msyh.ttc')
    await w.find('[data-test="poster-rerender"]').trigger('click')
    await flushPromises()
    const render = calls.find(c => c[0] === 'post' && c[1].endsWith('/poster/render'))
    expect(render[2]).toEqual({ title: '第七封信', subtitle: '', font: 'C:/f/msyh.ttc', layout: 'auto', kicker: '' })
    expect(calls.filter(c => c[0] === 'post' && c[1].endsWith('/poster/generate')).length).toBe(1)
  })

  it('接口报错显示在卡里；没有任何首帧时首帧来源禁用', async () => {
    posterState = { ...posterState, shots_with_frames: [] }
    const { post } = await import('../src/api.js')
    post.mockImplementationOnce(async () => { throw new Error('没找到 ffmpeg，海报要用它叠字——把 ffmpeg 放进 PATH 再试') })
    const w = await mountFinal()
    const card = w.find('[data-test="poster-card"]')
    expect(card.find('[data-test="poster-source-frame"]').attributes('disabled')).toBeDefined()
    await card.find('[data-test="poster-generate"]').trigger('click')
    await flushPromises()
    expect(card.find('[data-test="poster-error"]').text()).toContain('没找到 ffmpeg')
  })
})
