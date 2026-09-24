// 概览 next_step 在「项目文件 / 账本 / 冻结快照对不上」时指向处理这件事，而不是生成（DRAMA-12 审查后补）：
// key=freeze → 去概览看问题卡，不是花钱按钮；key=import_legacy（快照和产物在、账本没有记录）→ 直接重新登记，不花钱。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
vi.mock('../src/api.js', () => ({
  get: vi.fn(async () => ({})),
  post: vi.fn(async (url) => { calls.push(['post', url]); return {} }),
  put: vi.fn(), del: vi.fn(),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({ projectId: 'EP1', detail: null, summary: null, cast: [], postLayers: null, gen: null })
  return {
    store,
    loadProject: vi.fn(async () => {}),
    refreshProject: vi.fn(async () => { calls.push(['refresh']) }),
    runGenerate: vi.fn(async (...a) => { calls.push(['generate', ...a]) }),
    withBusy: vi.fn(async (fn) => fn()),
    capabilityCheck: vi.fn(async () => null),
  }
})
vi.mock('../src/ui.js', () => ({ confirm: vi.fn(async () => true), notice: vi.fn(), toast: vi.fn(), custom: vi.fn() }))

import Project from '../src/views/Project.vue'
import { store } from '../src/store.js'

function summary(next) {
  const gate = { total: 5, done: 5, missing: [], passed: [], pending: [], retake: [], stale: [] }
  return { frames: gate, videos: gate, final: { exists: false, kind: null, decision: null }, next_step: next }
}

async function mountAt(next) {
  store.detail = { id: 'EP1', title: 't', locked: true, shots: [], has_screenplay: false }
  store.summary = summary(next)
  const r = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/drama/p/:id', component: Project, children: [
      { path: 'overview', component: { template: '<div/>' } }, { path: 'final', component: { template: '<div/>' } },
    ] },
  ] })
  await r.push('/drama/p/EP1/final'); await r.isReady()
  const w = mount({ template: '<router-view />' }, { global: { plugins: [r] } })
  await flushPromises()
  return { w, r }
}

beforeEach(() => { calls.length = 0 })

describe('项目页主按钮 · 冻结对不上', () => {
  it('key=freeze：不是花钱按钮，点了去概览看问题卡，不触发生成', async () => {
    const { w, r } = await mountAt({ key: 'freeze', label: '处理版本冲突', hint: '项目文件在开始制作之后被外部改过' })
    const btn = w.find('.next .btn')
    expect(btn.text()).toBe('处理版本冲突')
    expect(btn.classes()).not.toContain('spend')
    await btn.trigger('click')
    await flushPromises()
    expect(r.currentRoute.value.path).toBe('/drama/p/EP1/overview')
    expect(calls.some(([k]) => k === 'generate' || k === 'post')).toBe(false)
  })

  it('key=import_legacy：直接 POST import-legacy 重新登记，然后刷新', async () => {
    const { w } = await mountAt({ key: 'import_legacy', label: '导入已有产物', hint: '账本里没有这个项目的制作记录' })
    await w.find('.next .btn').trigger('click')
    await flushPromises()
    expect(calls).toEqual([['post', '/api/episodes/storyvideo/EP1/import-legacy'], ['refresh']])
  })
})
