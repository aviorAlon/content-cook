// 删除短剧项目 = 归档：项目文件里的输出目录不在数据目录里（或不以 id 结尾）时后端不搬它、只归档项目文件，回一句 warning
// （2026-09-23 发布前自查 PB-2）。侧栏删除和「高级」页归档都要把这句话弹给人看，不能只报「已删除」。api 全 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const WARNING = '项目文件里的输出目录（D:/elsewhere/A-2）不在数据目录里、或不是以项目 id 结尾，没有搬动它，留在原处；只归档了项目文件'
let delResult = {}
vi.mock('../src/api.js', async (orig) => ({
  ...(await orig()),
  get: vi.fn(async (url) => (url === '/api/episodes' ? [] : url.startsWith('/api/deleted') ? [] : {})),
  post: vi.fn(async () => ({})),
  del: vi.fn(async () => delResult),
}))
const noticeMock = vi.fn(async () => {})
vi.mock('../src/ui.js', async (orig) => ({ ...(await orig()), confirm: vi.fn(async () => true), notice: (...a) => noticeMock(...a), toast: vi.fn() }))

import Sidebar from '../src/components/Sidebar.vue'
import Advanced from '../src/views/project/Advanced.vue'
import { store } from '../src/store.js'

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div/>' } }, { path: '/drama', component: { template: '<div/>' } },
    { path: '/drama/libraries', component: { template: '<div/>' } }, { path: '/drama/p/:id/overview', component: { template: '<div/>' } },
    { path: '/libraries', component: { template: '<div/>' } },
  ] })
}

beforeEach(() => {
  noticeMock.mockClear()
  store.episodes = [{ id: 'A-2', title: '加班夜', status: 'error', locked: false, shot_count: 0, progress: null }]
  store.episodesLoaded = true
})

describe('删除项目时的 warning', () => {
  it('侧栏删除：有 warning 就弹出来', async () => {
    delResult = { archived_to: '.webui_deleted/storyvideo/A-2__x', output_moved: false, warning: WARNING }
    const r = router(); await r.push('/'); await r.isReady()
    const w = mount(Sidebar, { global: { plugins: [r] } }); await flushPromises()
    await w.find('.proj .del').trigger('click'); await flushPromises()
    expect(noticeMock).toHaveBeenCalledWith(WARNING)
  })

  it('侧栏删除：没有 warning 不弹', async () => {
    delResult = { archived_to: '.webui_deleted/storyvideo/A-2__x', output_moved: true }
    const r = router(); await r.push('/'); await r.isReady()
    const w = mount(Sidebar, { global: { plugins: [r] } }); await flushPromises()
    await w.find('.proj .del').trigger('click'); await flushPromises()
    expect(noticeMock).not.toHaveBeenCalled()
  })

  it('「高级」页归档：有 warning 就弹出来', async () => {
    delResult = { archived_to: '.webui_deleted/storyvideo/A-2__x', output_moved: false, warning: WARNING }
    store.projectId = 'A-2'
    store.detail = { id: 'A-2', locked: false, shots: [] }
    const r = router(); await r.push('/'); await r.isReady()
    const w = mount(Advanced, { global: { plugins: [r] } }); await flushPromises()
    const button = w.findAll('button').find((b) => b.text() === '删除这个项目')
    await button.trigger('click'); await flushPromises()
    expect(noticeMock).toHaveBeenCalledWith(WARNING)
  })
})
