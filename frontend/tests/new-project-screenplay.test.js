// 新建项目页 · 两步起草与系列（DESIGN §32）：勾「两步起草」→ 出剧本 job → 逐场编辑器 → 保存 → 拆镜带 use_screenplay；
// 系列下拉从 /api/series 来，选了已有系列预填集数，提交时带 series_id / episode_no。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
const SCREENPLAY = { title: '厨房关停', logline: 'l', contract: { hook: 'h' }, cast: [{ id: 'ye', name: '叶澄', role: 'r' }], scenes: [{ id: 'SC01', heading: '内 · 厨房', beats: ['b'], dialogue: [] }] }
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url.startsWith('/api/episodes/next-id')) return { id: 'EP-2' }
    if (url === '/api/camera-options') return { profiles: ['dark_comedy_vertical'] }
    if (url === '/api/scripts/genres') return []
    if (url === '/api/scripts/forms') return []
    if (url === '/api/series') return [{ id: 'kitchen', episodes: 1, latest_no: 1, latest_title: '厨房关停' }]
    if (url === '/api/scripts/jobs') return []
    if (url === '/api/examples') return []
    return {}
  }),
  post: vi.fn(async (url, body) => { calls.push(['post', url, body]); return { job_id: url.endsWith('screenplay') ? 'sp1' : 'd1', episode_id: 'EP-2' } }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return { episode_id: 'EP-2', screenplay: body.screenplay, text: '' } }),
  pollJob: vi.fn(async (jobId) => {
    calls.push(['poll', jobId])
    if (jobId === 'sp1') return { status: 'succeeded', kind: 'generate_screenplay', result: { screenplay: SCREENPLAY, cost_usd: null, usage_tokens: 5 } }
    return { status: 'succeeded', result: { spec: { title_zh: '第二集', logline: '', shots: [] }, shots: [], warnings: [] } }
  }),
  del: vi.fn(), ep: (id) => `/api/episodes/storyvideo/${id}`,
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  return { store: reactive({ episodes: [] }), roleOk: () => true, loadEpisodes: vi.fn(async () => {}), withBusy: vi.fn(async (fn) => fn()) }
})
vi.mock('../src/ui.js', () => ({ notice: vi.fn(async () => {}), prompt: vi.fn(), confirm: vi.fn(async () => true), toast: vi.fn() }))

import NewProject from '../src/views/NewProject.vue'

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div/>' } }, { path: '/settings', component: { template: '<div/>' } }] })
}
async function mountPage() {
  const w = mount(NewProject, { global: { plugins: [router()] } })
  await flushPromises()
  return w
}
beforeEach(() => { calls.length = 0 })

describe('NewProject · 两步起草', () => {
  it('默认一步起草：没有剧本面板，按钮是「生成草稿」，提交不带 use_screenplay', async () => {
    const w = await mountPage()
    expect(w.find('[data-test="screenplay-panel"]').exists()).toBe(false)
    expect(w.find('[data-test="write-screenplay"]').exists()).toBe(false)
    expect(w.find('[data-test="submit-draft"]').text()).toBe('生成草稿')
    await w.find('textarea').setValue('一个人加班')
    await w.find('[data-test="submit-draft"]').trigger('click')
    await flushPromises()
    const draft = calls.find(([m, u]) => m === 'post' && u === '/api/scripts/jobs/draft')
    expect(draft[2].use_screenplay).toBeUndefined()
    expect(draft[2].series_id).toBeNull()
  })

  it('勾两步起草 → 出剧本 → 逐场编辑器 → 改了先保存 → 拆镜带 use_screenplay 和剧本的项目 id', async () => {
    const w = await mountPage()
    await w.find('[data-test="two-step-toggle"] input').setValue(true)
    expect(w.find('[data-test="submit-draft"]').exists()).toBe(false)
    await w.find('textarea').setValue('厨房关停')
    await w.find('[data-test="write-screenplay"]').trigger('click')
    await flushPromises()
    const sp = calls.find(([m, u]) => m === 'post' && u === '/api/scripts/jobs/screenplay')
    expect(sp[2].direction).toBe('厨房关停')
    expect(calls.some(([m, id]) => m === 'poll' && id === 'sp1')).toBe(true)
    const panel = w.find('[data-test="screenplay-panel"]')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('EP-2')
    // 不再是裸 JSON：场次卡里就是那一场，标题在输入框里
    expect(panel.find('[data-test="screenplay-editor"]').exists()).toBe(true)
    expect(panel.findAll('[data-test="scene-card"]').length).toBe(1)
    const heading = panel.find('[data-test="scene-heading"]')
    expect(heading.element.value).toBe('内 · 厨房')
    expect(panel.find('[data-test="sp-title"]').element.value).toBe('厨房关停')
    expect(w.find('[data-test="save-screenplay"]').attributes('disabled')).toBeDefined()
    // 改一处，保存按钮亮起；拆镜先保存再提交
    await heading.setValue('外 · 巷口')
    expect(w.find('[data-test="save-screenplay"]').attributes('disabled')).toBeUndefined()
    await w.find('[data-test="breakdown"]').trigger('click')
    await flushPromises()
    const saved = calls.find(([m, u]) => m === 'put' && u === '/api/episodes/storyvideo/EP-2/screenplay')
    expect(saved[2].screenplay.scenes[0].heading).toBe('外 · 巷口')
    const draft = calls.find(([m, u]) => m === 'post' && u === '/api/scripts/jobs/draft')
    expect(draft[2].use_screenplay).toBe(true)
    expect(draft[2].episode_id).toBe('EP-2')
    expect(w.text()).toContain('第二集')     // 拆镜结果进了草稿预览
  })

  it('保存被后端拒绝：报错显示在面板里，编辑内容不丢，仍是脏的', async () => {
    const api = await import('../src/api.js')
    const w = await mountPage()
    await w.find('[data-test="two-step-toggle"] input').setValue(true)
    await w.find('textarea').setValue('x')
    await w.find('[data-test="write-screenplay"]').trigger('click')
    await flushPromises()
    await w.find('[data-test="scene-heading"]').setValue('')
    api.put.mockRejectedValueOnce(new Error('剧本没通过校验，没有保存：场 SC01 没有场景标题'))
    await w.find('[data-test="save-screenplay"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="screenplay-error"]').text()).toContain('没有场景标题')
    expect(w.find('[data-test="scene-heading"]').element.value).toBe('')
    expect(w.find('[data-test="save-screenplay"]').attributes('disabled')).toBeUndefined()
  })
})

describe('NewProject · 系列', () => {
  it('系列下拉列出 /api/series，选已有系列预填集数 = 最新集 + 1，提交时带 series_id / episode_no', async () => {
    const w = await mountPage()
    const select = w.find('[data-test="series-select"]')
    expect(select.findAll('option').map((o) => o.text())).toEqual(['（单集，不属于系列）', 'kitchen（已到第 1 集）', '（新系列…）'])
    expect(w.find('[data-test="series-episode-no"]').exists()).toBe(false)
    await select.setValue('kitchen')
    await select.trigger('change')
    expect(w.find('[data-test="series-episode-no"]').element.value).toBe('2')
    expect(w.text()).toContain('厨房关停')
    await w.find('textarea').setValue('续')
    await w.find('[data-test="submit-draft"]').trigger('click')
    await flushPromises()
    const draft = calls.find(([m, u]) => m === 'post' && u === '/api/scripts/jobs/draft')
    expect(draft[2].series_id).toBe('kitchen')
    expect(draft[2].episode_no).toBe(2)
  })

  it('新系列：手填 id，集数默认 1', async () => {
    const w = await mountPage()
    const select = w.find('[data-test="series-select"]')
    await select.setValue('__new__')
    await select.trigger('change')
    await w.find('[data-test="series-new-id"]').setValue('  north-wind ')
    expect(w.find('[data-test="series-episode-no"]').element.value).toBe('1')
    await w.find('textarea').setValue('x')
    await w.find('[data-test="submit-draft"]').trigger('click')
    await flushPromises()
    const draft = calls.find(([m, u]) => m === 'post' && u === '/api/scripts/jobs/draft')
    expect(draft[2].series_id).toBe('north-wind')
    expect(draft[2].episode_no).toBe(1)
  })
})
