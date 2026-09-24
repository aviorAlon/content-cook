// 成片页没有「成片重拍」：runner 不收成片的重拍裁决（成片是拼出来的，要重的是镜次），以前这个按钮每次点都是 400。
// 页面改成提示去镜头页重拍出问题的镜次；「成片通过」照旧记 pass。
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => (url.endsWith('/export') ? { packages: [] } : {})),
  post: vi.fn(async () => ({})), put: vi.fn(), del: vi.fn(),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(async () => ({ status: 'succeeded', result: {} })),
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({
    projectId: 'EP1', summary: null, gen: null,
    detail: { id: 'EP1', final_url: '/api/episodes/storyvideo/EP1/artifact/final/final', final_kind: 'final', final_path: 'output/x.mp4',
              final_qc_decision: null, has_post_layers: false },
  })
  return { store, runGenerate: vi.fn(), review: vi.fn(async () => ({})), withBusy: vi.fn(async (fn) => fn()), refreshProject: vi.fn(async () => {}) }
})
vi.mock('../src/ui.js', async (orig) => ({
  ...(await orig()), prompt: vi.fn(), confirm: vi.fn(async () => true), notice: vi.fn(), toast: vi.fn(),
}))

import Final from '../src/views/project/Final.vue'
import { review } from '../src/store.js'

describe('成片页 · 没有「成片重拍」', () => {
  it('只剩「成片通过 / 重新合成」，并提示去镜头页重拍镜次', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', component: { template: '<div/>' } }, { path: '/drama/p/:id/shots', component: { template: '<div/>' } },
    ] })
    await r.push('/'); await r.isReady()
    const w = mount(Final, { global: { plugins: [r] } })
    await flushPromises()
    const labels = w.findAll('button').map((b) => b.text())
    expect(labels).not.toContain('成片重拍')
    expect(labels).toContain('成片通过')
    const hint = w.find('[data-test="final-retake-hint"]')
    expect(hint.text()).toContain('回「镜头」页重拍出问题的镜次')
    expect(hint.find('a').attributes('href')).toBe('/drama/p/EP1/shots')
    await w.findAll('button').find((b) => b.text() === '成片通过').trigger('click')
    await flushPromises()
    expect(review).toHaveBeenCalledWith('final', 'pass', ['final'])
    expect(review.mock.calls.some(([gate, decision]) => gate === 'final' && decision === 'retake')).toBe(false)
  })
})
