// 概览页 · 题材下拉与「按题材审稿」卡（DESIGN §49）：没选题材不出卡；下拉从 /api/scripts/genres 取、改了走 globals PUT；
// 跑审稿先弹成本确认（取消不发请求），确认后 POST → 轮询任务 → 重新拉审稿；结果按 违反 / 有风险 / 没问题 分组，
// 核对过的引文高亮、镜号可点；没对上原文的条目收在默认折叠的一组里；剧本改过显示过时横幅 + 重新审稿按钮。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
const state = { review: null, confirm: true, task: { status: 'succeeded', result: {} } }
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/scripts/genres') return [{ id: 'qinzi-yinmi', name: '亲子隐秘' }, { id: 'fuchou-dalian', name: '复仇打脸' }]
    if (url.endsWith('/genre-review')) return state.review
    return {}
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return {} }),
  post: vi.fn(async (url) => { calls.push(['post', url]); return { task_id: 'T1', cost_note: '发 1 次文本模型请求' } }),
  pollTask: vi.fn(async (id, onTick) => { calls.push(['poll', id]); onTick?.({ status: 'running' }, 3); return state.task }),
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
  return {
    ...real,
    notice: vi.fn(async (m) => calls.push(['notice', m])),
    toast: vi.fn((m) => calls.push(['toast', m])),
    confirm: vi.fn(async (m) => { calls.push(['confirm', m]); return state.confirm }),
  }
})

import Overview from '../src/views/project/Overview.vue'
import { store } from '../src/store.js'

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div/>' } }, { path: '/drama/p/:id/shots/:shotId', component: { template: '<div/>' } }] })
}
const SUMMARY = {
  title: 'T', logline: '', shots_total: 2, total_seconds: 10, cast_count: 1, scene_count: 0, camera_profile: '', locked: false, form: null,
  genre: { id: 'qinzi-yinmi', name: '亲子隐秘' },
  frames: { total: 2, done: 0, passed: [], pending: [], retake: [], missing: [] }, videos: { total: 2, done: 0, passed: [], pending: [], retake: [], missing: [] },
  asr: { ran: false }, final: { exists: false }, post_layers: { present: false, composed: false }, next_step: { key: 'prepare', label: '开始制作', hint: 'h' }, failures: [],
}
const ITEMS = [
  { source: 'pitfall', index: 1, item: '孩子只负责卖萌、撮合和说金句', verdict: 'violated', shots: ['S01'], quote: '妈妈别哭，我给你唱首歌', why: '孩子只在安慰', fix: '让孩子交出体检单', evidence_verified: true },
  { source: 'drift', index: 1, item: '不漂成家庭关系戏', verdict: 'risk', shots: ['SC02'], quote: '你欠这个家的', why: '冲突是旧账', fix: '把冲突落回没被承认的关系', evidence_verified: true },
  { source: 'core', index: 1, item: '孩子的存在就是未披露的信息', verdict: 'ok', shots: [], quote: '', why: '', fix: '' },
  { source: 'pitfall', index: 2, item: '孩子说超出认知的成人式台词推剧情', verdict: 'risk', model_verdict: 'violated', shots: ['S01'], quote: '我是爸爸的秘密', why: '编出来的', fix: '', evidence_verified: false, evidence_problem: 'quote_not_found' },
]
const REVIEW = {
  genre: { id: 'qinzi-yinmi', name: '亲子隐秘' },
  review: { card_id: 'qinzi-yinmi', card_name: '亲子隐秘', model: 'm1', backend: 'settings', created_at: 1757550000, spec_hash: 'h',
    result: { summary: '整体守住了题材，一处踩坑', items: ITEMS }, verification: { checked: 3, verified: 2, demoted: 1, dropped: 0, missing: 7 } },
  stale: false, note: '', problems: [], cost_note: '发 1 次文本模型请求', task_id: null,
}
const clone = (x) => JSON.parse(JSON.stringify(x))

beforeEach(() => {
  calls.length = 0
  state.review = clone({ ...REVIEW, review: null })
  state.confirm = true
  state.task = { status: 'succeeded', result: {} }
  store.summary = clone(SUMMARY)
  store.detail = { warnings: [], problems: [], locked: false, shots: [{ id: 'S01' }, { id: 'S02' }] }
})

describe('Overview · 题材下拉', () => {
  it('没选题材：下拉在、不出审稿卡、也不去拉审稿', async () => {
    store.summary.genre = null
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const select = w.find('[data-test="genre-select"]')
    expect(select.findAll('option').map((o) => o.text())).toEqual(['（不指定）', '亲子隐秘', '复仇打脸'])
    expect(select.element.value).toBe('')
    expect(w.find('[data-test="genre-review-card"]').exists()).toBe(false)
    expect(calls.some(([m, u]) => m === 'get' && String(u).endsWith('/genre-review'))).toBe(false)
  })

  it('草稿：改题材走 globals PUT 并 toast；制作中走 revise', async () => {
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const select = w.find('[data-test="genre-select"]')
    expect(select.element.value).toBe('qinzi-yinmi')
    await select.setValue('fuchou-dalian')
    await flushPromises()
    expect(calls.find(([m]) => m === 'put')).toEqual(['put', '/api/episodes/storyvideo/EP1/globals', { values: { genre: 'fuchou-dalian' } }])
    expect(calls.find(([m]) => m === 'toast')[1]).toBe('题材已改为 复仇打脸')
    calls.length = 0
    store.detail.locked = true
    await select.setValue('')
    await flushPromises()
    expect(calls.some(([m]) => m === 'put')).toBe(false)
    expect(calls.find(([m]) => m === 'revise')[1]).toEqual({ kind: 'globals', values: { genre: null } })
  })
})

describe('Overview · 按题材审稿', () => {
  it('卡片写明卡名和做法；跑之前弹成本确认，取消不发请求', async () => {
    state.confirm = false
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const card = w.find('[data-test="genre-review-card"]')
    expect(card.exists()).toBe(true)
    expect(card.find('[data-test="genre-review-card-name"]').text()).toBe('亲子隐秘')
    expect(card.text()).toContain('每条问题都要引用剧本原文，引文对不上的不算')
    expect(card.find('[data-test="genre-review-empty"]').exists()).toBe(true)
    expect(card.find('[data-test="genre-review-footer"]').text()).toBe('这是模型按题材卡的判断，不是保证')
    await card.find('[data-test="genre-review-run"]').trigger('click')
    await flushPromises()
    const ask = calls.find(([m]) => m === 'confirm')
    expect(ask[1]).toContain('亲子隐秘')
    expect(ask[1]).toContain('发 1 次文本模型请求')
    expect(calls.some(([m]) => m === 'post')).toBe(false)
  })

  it('确认后 POST → 轮询任务 → 重新拉审稿并刷新项目', async () => {
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    calls.length = 0
    state.review = clone(REVIEW)
    await w.find('[data-test="genre-review-run"]').trigger('click')
    await flushPromises()
    expect(calls.find(([m]) => m === 'post')[1]).toBe('/api/episodes/storyvideo/EP1/genre-review')
    expect(calls.find(([m]) => m === 'poll')[1]).toBe('T1')
    expect(calls.some(([m, u]) => m === 'get' && u === '/api/episodes/storyvideo/EP1/genre-review')).toBe(true)
    expect(calls.some(([m]) => m === 'refresh')).toBe(true)
    expect(calls.find(([m]) => m === 'toast')[1]).toBe('审稿完成')
    expect(w.findAll('[data-test="genre-review-item"]').length).toBe(3)
  })

  it('任务失败：弹出原因', async () => {
    state.task = { status: 'failed', error: 'ScriptGenError: 还没有可用的剧本文本模型' }
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    await w.find('[data-test="genre-review-run"]').trigger('click')
    await flushPromises()
    expect(calls.find(([m]) => m === 'notice')[1]).toContain('还没有可用的剧本文本模型')
  })

  it('结果按 违反 / 有风险 / 没问题 分组：引文高亮、镜号能点、场号不链；没对上原文的收在折叠组里', async () => {
    state.review = clone(REVIEW)
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const violated = w.find('[data-test="genre-review-group-violated"]')
    expect(violated.findAll('[data-test="genre-review-item"]').length).toBe(1)
    expect(violated.find('mark[data-test="genre-review-quote"]').text()).toBe('妈妈别哭，我给你唱首歌')
    expect(violated.find('[data-test="genre-review-shot"]').attributes('href')).toBe('/drama/p/EP1/shots/S01')
    expect(violated.text()).toContain('孩子只负责卖萌、撮合和说金句')
    expect(violated.text()).toContain('让孩子交出体检单')
    expect(violated.text()).not.toContain('我是爸爸的秘密')
    const risk = w.find('[data-test="genre-review-group-risk"]')
    expect(risk.findAll('[data-test="genre-review-item"]').length).toBe(1)
    expect(risk.text()).toContain('SC02')
    expect(risk.find('[data-test="genre-review-shot"]').exists()).toBe(false)
    const ok = w.find('[data-test="genre-review-group-ok"]')
    expect(ok.findAll('[data-test="genre-review-item"]').length).toBe(1)
    expect(ok.find('mark').exists()).toBe(false)
    const unverified = w.find('[data-test="genre-review-unverified"]')
    expect(unverified.exists()).toBe(true)
    expect(unverified.element.tagName).toBe('DETAILS')
    expect(unverified.attributes('open')).toBeUndefined()
    expect(unverified.find('summary').text()).toContain('模型说有问题但没给出能对上的原文（不计入）')
    expect(unverified.text()).toContain('我是爸爸的秘密')
    expect(unverified.text()).toContain('模型原判：违反')
    expect(unverified.text()).toContain('引文在剧本里找不到')
    expect(unverified.find('mark').exists()).toBe(false)
    expect(w.find('[data-test="genre-review-meta"]').text()).toContain('核对了 3 条问题：2 条引文对上，1 条没对上')
    expect(w.find('[data-test="genre-review-meta"]').text()).toContain('模型漏答了 7 条卡片条目')
    expect(w.find('[data-test="genre-review-stale"]').exists()).toBe(false)
    expect(w.find('[data-test="genre-review-run"]').text()).toBe('重新审稿')
  })

  it('剧本改过：显示过时横幅，横幅上的按钮重新审（同样先确认）', async () => {
    state.review = clone({ ...REVIEW, stale: true, note: '剧本改过，这份审稿可能过时——重新审一次' })
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const banner = w.find('[data-test="genre-review-stale"]')
    expect(banner.text()).toContain('剧本改过，这份审稿可能过时')
    await banner.find('[data-test="genre-review-rerun"]').trigger('click')
    await flushPromises()
    expect(calls.some(([m]) => m === 'confirm')).toBe(true)
    expect(calls.find(([m]) => m === 'post')[1]).toBe('/api/episodes/storyvideo/EP1/genre-review')
  })

  it('打开页面时已有审稿在跑：接着轮询它', async () => {
    state.review = clone({ ...REVIEW, review: null, task_id: 'T9' })
    mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    expect(calls.find(([m]) => m === 'poll')[1]).toBe('T9')
    expect(calls.some(([m]) => m === 'confirm')).toBe(false)
  })
})
