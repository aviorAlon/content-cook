// 概览页 · 单集契约卡与系列交接（DESIGN §32）：详情里的 episode_contract / series 填进表单；改了走 globals PUT
// （列表字段一行一条）；「系列交接」POST handoff 并 toast；没填系列时按钮禁用；问题卡「契约」分类有标签。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
vi.mock('../src/api.js', () => ({
  get: vi.fn(async () => ({})),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return {} }),
  post: vi.fn(async (url, body) => { calls.push(['post', url, body]); return { series_id: 'kitchen', entry: { established: ['a'], open_threads: ['b', 'c'] } } }),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({ projectId: 'EP1', detail: { warnings: [], problems: [] }, summary: null, gen: null })
  return {
    store,
    roleOk: () => true,
    capabilityCheck: vi.fn(async () => ({ issues: [], errors: 0, warnings: 0 })),
    retryFailed: vi.fn(), runGenerate: vi.fn(),
    withBusy: vi.fn(async (fn) => fn()),
    reviseEdit: vi.fn(async (m) => { calls.push(['revise', m]); return { id: 'EP1-v2' } }),
    refreshProject: vi.fn(async () => { calls.push(['refresh']) }),
  }
})
vi.mock('../src/ui.js', async () => {
  const real = await vi.importActual('../src/ui.js')
  return { ...real, notice: vi.fn(async () => {}), toast: vi.fn((m) => calls.push(['toast', m])) }
})

import Overview from '../src/views/project/Overview.vue'
import { store } from '../src/store.js'

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div/>' } }, { path: '/drama/p/:id/shots/:shotId', component: { template: '<div/>' } }] })
}
const SUMMARY = {
  title: 'T', logline: '', shots_total: 3, total_seconds: 12, cast_count: 1, scene_count: 0, camera_profile: '', locked: false,
  frames: { total: 3, done: 0, passed: [], pending: [], retake: [], missing: [] }, videos: { total: 3, done: 0, passed: [], pending: [], retake: [], missing: [] },
  asr: { ran: false }, final: { exists: false }, post_layers: { present: false, composed: false }, next_step: { key: 'prepare', label: '开始制作', hint: 'h' }, failures: [],
}
const CONTRACT = { premise: 'p', hook: '齐曼当众要求出示排班记录', cliffhanger: 'c', must_pay_off: ['一', '二'], carry_over: ['三'] }

beforeEach(() => {
  calls.length = 0
  store.summary = JSON.parse(JSON.stringify(SUMMARY))
  store.detail = { warnings: [], problems: [{ scope: 'episode', target: '', kind: 'contract', impact: '契约里的钩子「x」在前 2 镜的正文里找不到', fix: '改契约', text: 't' }], episode_contract: CONTRACT, series: { id: 'kitchen', episode_no: 2 }, locked: false }
})

describe('Overview · 单集契约', () => {
  it('详情里的契约与系列填进表单，问题卡带「契约」标签', async () => {
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const card = w.find('[data-test="contract-card"]')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('系列 kitchen')
    expect(card.text()).toContain('第 2 集')
    expect(w.find('[data-test="contract-hook"]').element.value).toBe('齐曼当众要求出示排班记录')
    expect(w.find('[data-test="contract-must-pay-off"]').element.value).toBe('一\n二')
    expect(w.find('[data-test="series-id"]').element.value).toBe('kitchen')
    expect(w.find('[data-test="save-contract"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="problems"]').text()).toContain('契约')
  })

  it('改了钩子和兑现清单 → 保存走 globals PUT，列表按行拆；刷新后 toast', async () => {
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    await w.find('[data-test="contract-hook"]').setValue('审查员提前进入厨房')
    await w.find('[data-test="contract-must-pay-off"]').setValue('一\n\n 四 ')
    expect(w.find('[data-test="save-contract"]').attributes('disabled')).toBeUndefined()
    await w.find('[data-test="save-contract"]').trigger('click')
    await flushPromises()
    const put = calls.find(([m]) => m === 'put')
    expect(put[1]).toBe('/api/episodes/storyvideo/EP1/globals')
    expect(put[2].values.episode_contract).toEqual({ premise: 'p', hook: '审查员提前进入厨房', cliffhanger: 'c', must_pay_off: ['一', '四'], carry_over: ['三'] })
    expect(put[2].values.series).toEqual({ id: 'kitchen', episode_no: 2 })
    expect(calls.some(([m]) => m === 'refresh')).toBe(true)
    expect(calls.some(([m, msg]) => m === 'toast' && msg.includes('契约已保存'))).toBe(true)
  })

  it('全部清空 → episode_contract 发 null（删键）；制作中的项目走 revise', async () => {
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    for (const k of ['premise', 'hook', 'cliffhanger', 'must-pay-off', 'carry-over']) await w.find(`[data-test="contract-${k}"]`).setValue('')
    await w.find('[data-test="series-id"]').setValue('')
    await w.find('[data-test="save-contract"]').trigger('click')
    await flushPromises()
    expect(calls.find(([m]) => m === 'put')[2].values).toEqual({ episode_contract: null, series: null })
    calls.length = 0
    store.detail.locked = true
    await flushPromises()
    await w.find('[data-test="contract-hook"]').setValue('h2')
    await w.find('[data-test="save-contract"]').trigger('click')
    await flushPromises()
    expect(calls.some(([m]) => m === 'put')).toBe(false)
    expect(calls.find(([m]) => m === 'revise')[1].kind).toBe('globals')
  })

  it('系列交接：POST handoff，toast 里报已成立/未决条数；没填系列时按钮禁用', async () => {
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    await w.find('[data-test="series-handoff"]').trigger('click')
    await flushPromises()
    expect(calls.find(([m, u]) => m === 'post' && u === '/api/episodes/storyvideo/EP1/series/handoff')).toBeTruthy()
    expect(calls.find(([m]) => m === 'toast')[1]).toContain('已成立 1 条、未决 2 条')
    store.detail = { warnings: [], problems: [], episode_contract: null, series: null, locked: false }
    await flushPromises()
    expect(w.find('[data-test="series-handoff"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="contract-hook"]').element.value).toBe('')
  })
})
