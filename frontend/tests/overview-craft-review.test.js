// 概览页 · 「剧本工艺审稿」卡（跟题材卡共用 ModelReviewCard）：不用先选题材，永远显示；
// 跑审稿先弹成本确认（取消不发请求），确认后 POST → 轮询任务 → 重新拉审稿；结果按 违反 / 有风险 / 没问题 分组，
// 每条带组名（张力 / 情绪可见 / 节奏 / 合规）、核对过的引文高亮、镜号可点；没对上原文的条目收在默认折叠的一组里；
// 剧本改过显示过时横幅 + 重新审稿按钮；页脚写明这是模型的判断。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
const state = { review: null, confirm: true, task: { status: 'succeeded', result: {} } }
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/scripts/genres') return [{ id: 'qinzi-yinmi', name: '亲子隐秘' }]
    if (url.endsWith('/craft-review')) return state.review
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
import { KIND_LABELS } from '../src/tasks.js'

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div/>' } }, { path: '/drama/p/:id/shots/:shotId', component: { template: '<div/>' } }] })
}
// 故意不选题材：工艺卡不依赖它
const SUMMARY = {
  title: 'T', logline: '', shots_total: 2, total_seconds: 10, cast_count: 1, scene_count: 0, camera_profile: '', locked: false, form: null, genre: null,
  frames: { total: 2, done: 0, passed: [], pending: [], retake: [], missing: [] }, videos: { total: 2, done: 0, passed: [], pending: [], retake: [], missing: [] },
  asr: { ran: false }, final: { exists: false }, post_layers: { present: false, composed: false }, next_step: { key: 'prepare', label: '开始制作', hint: 'h' }, failures: [],
}
const ITEMS = [
  { source: 'tension', index: 3, item: '集末要收在悬而未决处：问题刚被抛出来、人物还没做选择，画面就断。', verdict: 'violated', shots: ['S02'], quote: '两人抱在一起，镜头拉远', why: '结尾把冲突收掉了', fix: '停在她伸手去拿同意书的那一刻', evidence_verified: true },
  { source: 'compliance', index: 3, item: '高风险题材只写后果、不写过程，真名一律换掉：……', verdict: 'risk', shots: ['SC02'], quote: '市第一人民医院', why: '真实机构名', fix: '改成化名', evidence_verified: true },
  { source: 'rhythm', index: 1, item: '观众隔一小段就该知道点新东西：……', verdict: 'ok', shots: [], quote: '', why: '', fix: '' },
  { source: 'emotion', index: 2, item: '嘴上压着、身上漏出来：……', verdict: 'risk', model_verdict: 'violated', shots: ['S01'], quote: '我一点也不难过', why: '编出来的', fix: '', evidence_verified: false, evidence_problem: 'quote_not_found' },
]
const REVIEW = {
  checklist: { id: 'craft-v1', name: '剧本工艺清单' },
  review: { checklist_id: 'craft-v1', checklist_name: '剧本工艺清单', model: 'm1', backend: 'settings', created_at: 1757550000, spec_hash: 'h',
    result: { summary: '集末收掉了冲突，一处真实机构名', items: ITEMS }, verification: { checked: 3, verified: 2, demoted: 1, dropped: 0, missing: 12 } },
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

describe('Overview · 剧本工艺审稿', () => {
  it('没选题材也出卡：写明清单名和做法；跑之前弹成本确认，取消不发请求', async () => {
    state.confirm = false
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    expect(w.find('[data-test="genre-review-card"]').exists()).toBe(false)
    const card = w.find('[data-test="craft-review-card"]')
    expect(card.exists()).toBe(true)
    expect(card.find('[data-test="craft-review-card-name"]').text()).toBe('剧本工艺清单')
    expect(card.text()).toContain('跟题材无关')
    expect(card.text()).toContain('引文对不上的不算')
    expect(card.text()).toContain('一票否决')
    expect(card.find('[data-test="craft-review-empty"]').exists()).toBe(true)
    expect(card.find('[data-test="craft-review-footer"]').text()).toBe('这是模型按工艺清单的判断，不是保证')
    expect(calls.some(([m, u]) => m === 'get' && u === '/api/episodes/storyvideo/EP1/craft-review')).toBe(true)
    await card.find('[data-test="craft-review-run"]').trigger('click')
    await flushPromises()
    const ask = calls.find(([m]) => m === 'confirm')
    expect(ask[1]).toBe('按工艺清单审一遍剧本：发 1 次文本模型请求。')
    expect(calls.some(([m]) => m === 'post')).toBe(false)
  })

  it('确认后 POST → 轮询任务 → 重新拉审稿并刷新项目', async () => {
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    calls.length = 0
    state.review = clone(REVIEW)
    await w.find('[data-test="craft-review-run"]').trigger('click')
    await flushPromises()
    expect(calls.find(([m]) => m === 'post')[1]).toBe('/api/episodes/storyvideo/EP1/craft-review')
    expect(calls.find(([m]) => m === 'poll')[1]).toBe('T1')
    expect(calls.some(([m, u]) => m === 'get' && u === '/api/episodes/storyvideo/EP1/craft-review')).toBe(true)
    expect(calls.some(([m]) => m === 'refresh')).toBe(true)
    expect(calls.find(([m]) => m === 'toast')[1]).toBe('审稿完成')
    expect(w.findAll('[data-test="craft-review-item"]').length).toBe(3)
  })

  it('任务失败：弹出原因', async () => {
    state.task = { status: 'failed', error: 'CraftReviewError: 模型没有按审稿结构返回（缺 items 列表）' }
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    await w.find('[data-test="craft-review-run"]').trigger('click')
    await flushPromises()
    expect(calls.find(([m]) => m === 'notice')[1]).toContain('模型没有按审稿结构返回')
  })

  it('结果按 违反 / 有风险 / 没问题 分组：带组名、引文高亮、镜号能点、场号不链；没对上原文的收在折叠组里', async () => {
    state.review = clone(REVIEW)
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const violated = w.find('[data-test="craft-review-group-violated"]')
    expect(violated.findAll('[data-test="craft-review-item"]').length).toBe(1)
    expect(violated.text()).toContain('张力 3')
    expect(violated.find('mark[data-test="craft-review-quote"]').text()).toBe('两人抱在一起，镜头拉远')
    expect(violated.find('[data-test="craft-review-shot"]').attributes('href')).toBe('/drama/p/EP1/shots/S02')
    expect(violated.text()).toContain('集末要收在悬而未决处')
    expect(violated.text()).toContain('结尾把冲突收掉了')
    expect(violated.text()).toContain('停在她伸手去拿同意书的那一刻')
    expect(violated.text()).not.toContain('我一点也不难过')
    const risk = w.find('[data-test="craft-review-group-risk"]')
    expect(risk.findAll('[data-test="craft-review-item"]').length).toBe(1)
    expect(risk.text()).toContain('合规 3')
    expect(risk.text()).toContain('SC02')
    expect(risk.find('[data-test="craft-review-shot"]').exists()).toBe(false)
    const ok = w.find('[data-test="craft-review-group-ok"]')
    expect(ok.findAll('[data-test="craft-review-item"]').length).toBe(1)
    expect(ok.text()).toContain('节奏 1')
    expect(ok.find('mark').exists()).toBe(false)
    const unverified = w.find('[data-test="craft-review-unverified"]')
    expect(unverified.exists()).toBe(true)
    expect(unverified.element.tagName).toBe('DETAILS')
    expect(unverified.attributes('open')).toBeUndefined()
    expect(unverified.find('summary').text()).toContain('模型说有问题但没给出能对上的原文（不计入）')
    expect(unverified.text()).toContain('情绪可见 2')
    expect(unverified.text()).toContain('我一点也不难过')
    expect(unverified.text()).toContain('模型原判：违反')
    expect(unverified.text()).toContain('引文在剧本里找不到')
    expect(unverified.find('mark').exists()).toBe(false)
    const meta = w.find('[data-test="craft-review-meta"]').text()
    expect(meta).toContain('剧本工艺清单')
    expect(meta).toContain('核对了 3 条问题：2 条引文对上，1 条没对上')
    expect(meta).toContain('模型漏答了 12 条清单条目')
    expect(w.find('[data-test="craft-review-stale"]').exists()).toBe(false)
    expect(w.find('[data-test="craft-review-run"]').text()).toBe('重新审稿')
  })

  it('剧本改过：显示过时横幅，横幅上的按钮重新审（同样先确认）', async () => {
    state.review = clone({ ...REVIEW, stale: true, note: '剧本改过，这份审稿可能过时——重新审一次' })
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const banner = w.find('[data-test="craft-review-stale"]')
    expect(banner.text()).toContain('剧本改过，这份审稿可能过时')
    await banner.find('[data-test="craft-review-rerun"]').trigger('click')
    await flushPromises()
    expect(calls.some(([m]) => m === 'confirm')).toBe(true)
    expect(calls.find(([m]) => m === 'post')[1]).toBe('/api/episodes/storyvideo/EP1/craft-review')
  })

  it('清单文件读不出来：横幅说的是清单的事，不是「剧本改过」', async () => {
    const note = '工艺清单文件读不出来——重装一次程序，或检查 skills/ai-short-film/scripts/craft_checklist.json'
    state.review = clone({ ...REVIEW, checklist: null, stale: true, note })
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const banner = w.find('[data-test="craft-review-stale"]')
    expect(banner.text()).toContain('工艺清单文件读不出来')
    expect(banner.text()).not.toContain('剧本改过')
  })

  it('打开页面时已有审稿在跑：接着轮询它', async () => {
    state.review = clone({ ...REVIEW, review: null, task_id: 'T9' })
    mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    expect(calls.find(([m]) => m === 'poll')[1]).toBe('T9')
    expect(calls.some(([m]) => m === 'confirm')).toBe(false)
  })

  it('选了题材：两张卡并排，各拉各的接口', async () => {
    store.summary.genre = { id: 'qinzi-yinmi', name: '亲子隐秘' }
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    expect(w.find('[data-test="genre-review-card"]').exists()).toBe(true)
    expect(w.find('[data-test="craft-review-card"]').exists()).toBe(true)
    expect(calls.some(([m, u]) => m === 'get' && u === '/api/episodes/storyvideo/EP1/genre-review')).toBe(true)
    expect(calls.some(([m, u]) => m === 'get' && u === '/api/episodes/storyvideo/EP1/craft-review')).toBe(true)
  })

  it('任务中心认识 craft-review 这种任务', () => {
    expect(KIND_LABELS['craft-review']).toBe('工艺审稿')
  })
})
