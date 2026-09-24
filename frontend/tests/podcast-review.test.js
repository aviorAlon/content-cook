// 播客脚本页 · 「真人感审稿」卡（跟短剧的题材 / 工艺卡共用 ModelReviewCard，接口前缀与引用号链接由 props 决定）：永远显示；
// 跑审稿先弹成本确认（取消不发请求），确认后 POST /api/podcast/{id}/humanlike-review → 轮询任务 → 重新拉审稿并刷新播客详情；
// 结果按 违反 / 有风险 / 没问题 分组，每条带组名（口语 / 对谈动态 / 内容（实质））、核对过的引文高亮、环节号链到本页的环节卡；
// 没对上原文的条目收在默认折叠的一组里，说明用播客的话（稿子 / 环节号）；稿子改过显示过时横幅 + 重新审稿按钮；页脚写明这是模型的判断。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
const state = { review: null, confirm: true, task: { status: 'succeeded', result: {} }, detail: null }
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/podcast/formats') return null
    if (url.endsWith('/humanlike-review')) return state.review
    if (url.startsWith('/api/podcast/')) return state.detail
    return {}
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return {} }),
  post: vi.fn(async (url) => { calls.push(['post', url]); return { task_id: 'T1', cost_note: '发 1 次文本模型请求' } }),
  del: vi.fn(),
  pollTask: vi.fn(async (id, onTick) => { calls.push(['poll', id]); onTick?.({ status: 'running' }, 3); return state.task }),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
}))
vi.mock('../src/ui.js', async () => {
  const real = await vi.importActual('../src/ui.js')
  return {
    ...real,
    notice: vi.fn(async (m) => calls.push(['notice', m])),
    toast: vi.fn((m) => calls.push(['toast', m])),
    confirm: vi.fn(async (m) => { calls.push(['confirm', m]); return state.confirm }),
    prompt: vi.fn(async () => null),
  }
})

import PodcastScript from '../src/views/podcast/PodcastScript.vue'
import { store } from '../src/store.js'
import { loadPodcast, clearPodcast } from '../src/podcast.js'
import { KIND_LABELS } from '../src/tasks.js'

const ID = 'PC-20260916-1'
const URL = `/api/podcast/${ID}/humanlike-review`
const stub = { template: '<div/>' }
function router() {
  return createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: stub }, { path: '/podcast/p/:id/:sub', component: stub }] })
}
async function mountScript() {
  const r = router(); await r.push(`/podcast/p/${ID}/script`); await r.isReady()
  await loadPodcast(ID)
  const w = mount(PodcastScript, { global: { plugins: [r] } })
  await flushPromises()
  return { w, r }
}

function sampleDetail() {
  const spec = {
    id: ID, title: '为什么我们戒不掉短视频', format: 'duo', language: 'zh', target_minutes: 4,
    brief: { topic: '刷短视频停不下来', angle: '', audience: '', takeaways: [], materials: '', tone: '', avoid: [] },
    hosts: [{ id: 'zhou', name: '老周', role: 'host', persona: '', stance: '', voice: '' }, { id: 'xiao', name: '小夏', role: 'cohost', persona: '', stance: '', voice: '' }],
    segments: [
      { id: 'G01', kind: 'cold_open', title: '凌晨两点十七', goal: '', beats: ['小夏昨晚刷到两点多'], questions: [], lines: [{ who: 'xiao', text: '我昨天晚上看了眼时间，两点十七。' }] },
      { id: 'G02', kind: 'topic', title: '无限下滑', goal: '', beats: [], questions: [], lines: [{ who: 'zhou', text: '首先我们来看第一点，其次是第二点。' }, { who: 'xiao', text: '对对对，你说得对。' }] },
    ],
    music: { intro: '', bed: '', outro: '', bed_gain: 0.18, duck: 0.45 }, pause: { line: 0.35, segment: 1.2 }, draft: {},
  }
  return {
    id: ID, module: 'podcast', title: spec.title, spec,
    estimate: { chars: 40, lines: 3, seconds: 10, minutes: 0.2 }, warnings: [], problems: [],
    hosts: spec.hosts, audio: { lines: [], synthesized: 0, total: 3, stale: true, mixed: false, mix_url: null, chapters: [], ffmpeg: true },
    texts: {}, draft: {}, busy: { draft: null, audio: null, review: null }, limits: { target_minutes: [1, 90], hosts: [1, 4] },
  }
}
const ITEMS = [
  { source: 'speech', index: 1, item: '台词是说出口的话，不是写出来的文章。', verdict: 'violated', shots: ['G02'], quote: '首先我们来看第一点，其次是第二点', why: '整句是分点列举', fix: '删掉连接词，直接说事', evidence_verified: true },
  { source: 'dynamics', index: 1, item: '双人以上整期至少有一处明确的不同意见。', verdict: 'risk', shots: ['G09'], quote: '对对对，你说得对', why: '反对被收掉了', fix: '', evidence_verified: true },
  { source: 'substance', index: 3, item: '冷开场不自我介绍，收尾不总结全篇。', verdict: 'ok', shots: [], quote: '', why: '', fix: '' },
  { source: 'substance', index: 1, item: '具体压倒观点。', verdict: 'risk', model_verdict: 'violated', shots: ['G01'], quote: '有个调查说八成人都刷', why: '编出来的', fix: '', evidence_verified: false, evidence_problem: 'quote_not_found' },
]
const REVIEW = {
  checklist: { id: 'podcast-humanlike-v1', name: '真人感清单' },
  review: { checklist_id: 'podcast-humanlike-v1', checklist_name: '真人感清单', model: 'm1', backend: 'settings', created_at: 1757550000, spec_hash: 'h',
    result: { summary: '开场像念稿，一处编数据', items: ITEMS }, verification: { checked: 3, verified: 2, demoted: 1, dropped: 0, missing: 8 } },
  stale: false, note: '', problems: [], cost_note: '发 1 次文本模型请求', task_id: null,
}
const clone = (x) => JSON.parse(JSON.stringify(x))

beforeEach(() => {
  calls.length = 0
  state.review = clone({ ...REVIEW, review: null })
  state.confirm = true
  state.task = { status: 'succeeded', result: {} }
  state.detail = sampleDetail()
  clearPodcast(); store.gen = null
})

describe('PodcastScript · 真人感审稿', () => {
  it('永远出卡：写明清单名、做法和页脚；拉的是播客的接口；跑之前弹成本确认，取消不发请求', async () => {
    state.confirm = false
    const { w } = await mountScript()
    const card = w.find('[data-test="humanlike-review-card"]')
    expect(card.exists()).toBe(true)
    expect(card.find('[data-test="humanlike-review-card-name"]').text()).toBe('真人感清单')
    expect(card.text()).toContain('像不像真人在聊')
    expect(card.text()).toContain('引文对不上的不算')
    expect(card.find('[data-test="humanlike-review-empty"]').exists()).toBe(true)
    expect(card.find('[data-test="humanlike-review-footer"]').text()).toBe('这是模型按真人感清单的判断，不是保证')
    expect(calls.some(([m, u]) => m === 'get' && u === URL)).toBe(true)
    expect(calls.some(([m, u]) => m === 'get' && u.startsWith('/api/episodes/'))).toBe(false)
    await card.find('[data-test="humanlike-review-run"]').trigger('click')
    await flushPromises()
    expect(calls.find(([m]) => m === 'confirm')[1]).toBe('按真人感清单审一遍稿子：发 1 次文本模型请求。')
    expect(calls.some(([m]) => m === 'post')).toBe(false)
  })

  it('确认后 POST 播客的接口 → 轮询任务 → 重新拉审稿并刷新播客详情', async () => {
    const { w } = await mountScript()
    calls.length = 0
    state.review = clone(REVIEW)
    await w.find('[data-test="humanlike-review-run"]').trigger('click')
    await flushPromises()
    expect(calls.find(([m]) => m === 'post')[1]).toBe(URL)
    expect(calls.find(([m]) => m === 'poll')[1]).toBe('T1')
    expect(calls.some(([m, u]) => m === 'get' && u === URL)).toBe(true)
    expect(calls.some(([m, u]) => m === 'get' && u === `/api/podcast/${ID}`)).toBe(true)   // refreshPodcast
    expect(calls.find(([m]) => m === 'toast')[1]).toBe('审稿完成')
    expect(w.findAll('[data-test="humanlike-review-item"]').length).toBe(3)
    expect(w.find('[data-test="humanlike-review-run"]').text()).toBe('重新审稿')
  })

  it('任务失败：弹出原因', async () => {
    state.task = { status: 'failed', error: 'HumanlikeReviewError: 模型没有按审稿结构返回（缺 items 列表）' }
    const { w } = await mountScript()
    await w.find('[data-test="humanlike-review-run"]').trigger('click')
    await flushPromises()
    expect(calls.find(([m]) => m === 'notice')[1]).toContain('模型没有按审稿结构返回')
  })

  it('结果分组：带组名、引文高亮、环节号链到本页的环节卡、不存在的环节号不链；没对上原文的收在折叠组里并用播客的话说明', async () => {
    state.review = clone(REVIEW)
    const { w } = await mountScript()
    const violated = w.find('[data-test="humanlike-review-group-violated"]')
    expect(violated.findAll('[data-test="humanlike-review-item"]').length).toBe(1)
    expect(violated.text()).toContain('口语 1')
    expect(violated.find('mark[data-test="humanlike-review-quote"]').text()).toBe('首先我们来看第一点，其次是第二点')
    expect(violated.find('[data-test="humanlike-review-shot"]').attributes('href')).toBe(`/podcast/p/${ID}/script#segment-G02`)
    expect(violated.text()).toContain('整句是分点列举')
    expect(violated.text()).toContain('删掉连接词，直接说事')
    const risk = w.find('[data-test="humanlike-review-group-risk"]')
    expect(risk.findAll('[data-test="humanlike-review-item"]').length).toBe(1)
    expect(risk.text()).toContain('对谈动态 1')
    expect(risk.text()).toContain('G09')
    expect(risk.find('[data-test="humanlike-review-shot"]').exists()).toBe(false)
    const ok = w.find('[data-test="humanlike-review-group-ok"]')
    expect(ok.findAll('[data-test="humanlike-review-item"]').length).toBe(1)
    expect(ok.text()).toContain('内容（实质） 3')
    expect(ok.find('mark').exists()).toBe(false)
    const unverified = w.find('[data-test="humanlike-review-unverified"]')
    expect(unverified.element.tagName).toBe('DETAILS')
    expect(unverified.attributes('open')).toBeUndefined()
    expect(unverified.text()).toContain('内容（实质） 1')
    expect(unverified.text()).toContain('有个调查说八成人都刷')
    expect(unverified.text()).toContain('模型原判：违反')
    expect(unverified.text()).toContain('引文在稿子里找不到')
    expect(unverified.text()).not.toContain('剧本')
    const meta = w.find('[data-test="humanlike-review-meta"]').text()
    expect(meta).toContain('真人感清单')
    expect(meta).toContain('核对了 3 条问题：2 条引文对上，1 条没对上')
    expect(meta).toContain('模型漏答了 8 条清单条目')
    // 环节卡本身带锚点 id，链接才有地方落
    expect(w.findAll('[data-test="segment"]').map(x => x.attributes('id'))).toEqual(['segment-G01', 'segment-G02'])
    expect(w.find('[data-test="humanlike-review-stale"]').exists()).toBe(false)
  })

  it('引用了不存在的环节号：折叠组里的说明是「环节号」，不是「镜号」', async () => {
    const review = clone(REVIEW)
    review.review.result.items = [{ ...ITEMS[3], shots: ['G01', 'G09'], evidence_problem: 'unknown_ids' }]
    state.review = review
    const { w } = await mountScript()
    const unverified = w.find('[data-test="humanlike-review-unverified"]')
    expect(unverified.text()).toContain('引用了不存在的环节号')
    expect(unverified.text()).not.toContain('镜号')
  })

  it('稿子改过：显示过时横幅（话以接口的 note 为准），横幅上的按钮重新审（同样先确认）', async () => {
    state.review = clone({ ...REVIEW, stale: true, note: '稿子改过，这份审稿可能过时——重新审一次' })
    const { w } = await mountScript()
    const banner = w.find('[data-test="humanlike-review-stale"]')
    expect(banner.text()).toContain('稿子改过，这份审稿可能过时')
    expect(banner.text()).not.toContain('剧本')
    await banner.find('[data-test="humanlike-review-rerun"]').trigger('click')
    await flushPromises()
    expect(calls.some(([m]) => m === 'confirm')).toBe(true)
    expect(calls.find(([m]) => m === 'post')[1]).toBe(URL)
  })

  it('打开页面时已有审稿在跑：接着轮询它', async () => {
    state.review = clone({ ...REVIEW, review: null, task_id: 'T9' })
    await mountScript()
    expect(calls.find(([m]) => m === 'poll')[1]).toBe('T9')
    expect(calls.some(([m]) => m === 'confirm')).toBe(false)
  })

  it('任务中心认识 pc-review 这种任务', () => {
    expect(KIND_LABELS['pc-review']).toBe('播客审稿')
  })
})
