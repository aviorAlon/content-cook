// 短剧删除 = 归档（项目文件、剧本、锚图、产物、账本记录一起进 .webui_deleted/）：找回走全局「回收站」页（/trash）。
// 以前删除确认框教人「想找回手动搬回来即可」——归档时文件被改成固定名、账本记录也收进了归档，照做只会走进
// 「开始制作 400 / 导入已有产物 409」的死路；后来侧栏短剧分组里就地展开「已删除的项目」，但只有短剧能恢复。
// 这里确认：短剧分组底下的「已删除的项目」指向回收站页（只看短剧）、全局区有回收站入口、确认框指向回收站。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
vi.mock('../src/api.js', async (orig) => ({
  ...(await orig()),
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/episodes') return []
    return {}
  }),
  post: vi.fn(async (url) => { calls.push(['post', url]); return {} }),
  del: vi.fn(async (url) => { calls.push(['del', url]); return { archived_to: '.webui_deleted/storyvideo/A-1__x' } }),
}))
const confirmMock = vi.fn(async () => true)
const toastMock = vi.fn()
vi.mock('../src/ui.js', async (orig) => ({ ...(await orig()), confirm: (...a) => confirmMock(...a), notice: vi.fn(), toast: (...a) => toastMock(...a) }))

import Sidebar from '../src/components/Sidebar.vue'
import { store } from '../src/store.js'

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div/>' } }, { path: '/drama', component: { template: '<div/>' } },
    { path: '/drama/libraries', component: { template: '<div/>' } }, { path: '/drama/p/:id/overview', component: { template: '<div/>' } },
    { path: '/trash', component: { template: '<div/>' } },
  ] })
}

async function mountSidebar() {
  const r = router(); await r.push('/'); await r.isReady()
  const w = mount(Sidebar, { global: { plugins: [r] } })
  await flushPromises()
  return { w, r }
}

beforeEach(() => {
  calls.length = 0
  confirmMock.mockClear(); toastMock.mockClear()
  store.episodes = [{ id: 'A-2', title: '加班夜', status: 'ok', locked: true, shot_count: 5, progress: null }]
  store.episodesLoaded = true
})

describe('侧栏 · 回收站入口', () => {
  it('短剧分组底下的「已删除的项目」指向回收站页（只看短剧），不再就地拉列表', async () => {
    const { w, r } = await mountSidebar()
    const link = w.find('[data-test="drama-trash-link"]')
    expect(link.text()).toContain('已删除的项目')
    expect(link.attributes('href')).toBe('/trash?module=storyvideo')
    expect(calls.some(([, url]) => url.startsWith('/api/deleted'))).toBe(false)
    await link.trigger('click'); await flushPromises()
    expect(r.currentRoute.value.fullPath).toBe('/trash?module=storyvideo')
  })

  it('全局区有「回收站」入口', async () => {
    const { w } = await mountSidebar()
    const link = w.find('[data-test="sidebar-trash"]')
    expect(link.attributes('href')).toBe('/trash')
    expect(link.text()).toContain('回收站')
  })

  it('删除确认框指向回收站，不再说「手动搬回来」；删完的提示也说能在回收站里恢复', async () => {
    const { w } = await mountSidebar()
    await w.find('.proj .del').trigger('click')
    await flushPromises()
    const text = confirmMock.mock.calls[0][0]
    expect(text).toContain('删除后可在「回收站」里恢复')
    expect(text).toContain('账本记录')
    expect(text).not.toContain('手动搬回来')
    expect(text).not.toContain('.webui_deleted')
    expect(calls).toContainEqual(['del', '/api/episodes/storyvideo/A-2'])
    expect(toastMock.mock.calls[0][0]).toBe('已删除「加班夜」（可在「回收站」里恢复）')
  })
})
