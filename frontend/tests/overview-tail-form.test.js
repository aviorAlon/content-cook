// 概览页 · 等尾帧卡（DESIGN §30）与形态下拉（DESIGN §31）：summary.tail_pending 渲染成跳到镜头页的标签，空了不显示；
// 形态下拉从 /api/scripts/forms 取选项、当前值跟 summary.form 走，改了走 globals PUT（「不指定」= form: null），制作中走 revise。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => { calls.push(['get', url]); return url === '/api/scripts/forms' ? [{ id: 'vertical_drama', name: '竖屏短剧' }, { id: 'horizontal_short', name: '横屏短片' }] : {} }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); if (body?.values?.form === 'boom') throw new Error('不认识的形态：boom'); return {} }),
  post: vi.fn(async () => ({})),
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
  return { ...real, notice: vi.fn(async (m) => calls.push(['notice', m])), toast: vi.fn((m) => calls.push(['toast', m])) }
})

import Overview from '../src/views/project/Overview.vue'
import { store } from '../src/store.js'

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div/>' } }, { path: '/drama/p/:id/shots/:shotId', component: { template: '<div/>' } }] })
}
const SUMMARY = {
  title: 'T', logline: '', shots_total: 3, total_seconds: 12, cast_count: 1, scene_count: 0, camera_profile: '', locked: true, form: null,
  frames: { total: 3, done: 1, passed: ['S1'], pending: [], retake: [], missing: ['S2', 'S3'] },
  videos: { total: 3, done: 0, passed: [], pending: [], retake: [], missing: ['S1', 'S2', 'S3'] },
  asr: { ran: false }, final: { exists: false }, post_layers: { present: false, composed: false },
  next_step: { key: 'tail_wait', label: '等尾帧', hint: '2 镜在等 S1 的视频过检后抽尾帧', shots: ['S2', 'S3'], sources: ['S1'] },
  failures: [],
  tail_pending: [
    { shot_id: 'S2', source: 'S1', mode: 'reference', reason: '等 S1 视频过检后抽尾帧' },
    { shot_id: 'S3', source: 'S1', mode: 'direct', reason: '等 S1 视频过检后抽尾帧' },
  ],
}

beforeEach(() => { calls.length = 0; store.summary = JSON.parse(JSON.stringify(SUMMARY)); store.detail = { warnings: [], problems: [], locked: false } })

describe('Overview · 等尾帧卡', () => {
  it('列出在等的镜：标签写「镜 ← 等 源镜 视频过检」、直接当首帧的标出来、点了跳到镜头页；下一步文案照后端', async () => {
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const card = w.find('[data-test="tail-pending-card"]')
    expect(card.exists()).toBe(true)
    const pills = card.findAll('[data-test="tail-pending-pill"]')
    expect(pills.length).toBe(2)
    expect(pills[0].text()).toBe('S2 ← 等 S1 视频过检')
    expect(pills[1].text()).toBe('S3 ← 等 S1 视频过检（直接当首帧）')
    expect(pills[1].attributes('href')).toBe('/drama/p/EP1/shots/S3')
    expect(pills[0].attributes('title')).toBe('等 S1 视频过检后抽尾帧')
    expect(w.text()).toContain('下一步：等尾帧')
  })

  it('没有在等的镜（或老后端没带字段）时不显示卡片', async () => {
    store.summary.tail_pending = []
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    expect(w.find('[data-test="tail-pending-card"]').exists()).toBe(false)
    delete store.summary.tail_pending
    await flushPromises()
    expect(w.find('[data-test="tail-pending-card"]').exists()).toBe(false)
  })
})

describe('Overview · 形态下拉', () => {
  it('选项来自 /api/scripts/forms，首项「不指定」；当前值跟 summary.form 走', async () => {
    store.summary.form = { id: 'horizontal_short', name: '横屏短片' }
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    expect(calls.some(([m, u]) => m === 'get' && u === '/api/scripts/forms')).toBe(true)
    const select = w.find('[data-test="form-select"]')
    expect(select.findAll('option').map((o) => o.text())).toEqual(['（不指定）', '竖屏短剧', '横屏短片'])
    expect(select.element.value).toBe('horizontal_short')
    store.summary.form = null
    await flushPromises()
    expect(select.element.value).toBe('')
  })

  it('草稿：改形态走 globals PUT 并 toast；选「不指定」发 form: null；后端拒绝时回退并提示', async () => {
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const select = w.find('[data-test="form-select"]')
    await select.setValue('vertical_drama')
    await flushPromises()
    const put = calls.find(([m]) => m === 'put')
    expect(put[1]).toBe('/api/episodes/storyvideo/EP1/globals')
    expect(put[2]).toEqual({ values: { form: 'vertical_drama' } })
    expect(calls.some(([m]) => m === 'refresh')).toBe(true)
    expect(calls.find(([m]) => m === 'toast')[1]).toBe('形态已改为 竖屏短剧')
    calls.length = 0
    store.summary.form = { id: 'vertical_drama', name: '竖屏短剧' }
    await flushPromises()
    await select.setValue('')
    await flushPromises()
    expect(calls.find(([m]) => m === 'put')[2]).toEqual({ values: { form: null } })
    expect(calls.find(([m]) => m === 'toast')[1]).toBe('已清掉形态')
    // 后端 400：提示原因，下拉回到 summary 里的值
    calls.length = 0
    select.element.appendChild(Object.assign(document.createElement('option'), { value: 'boom', text: 'boom' }))
    await select.setValue('boom')
    await flushPromises()
    expect(calls.find(([m]) => m === 'notice')[1]).toBe('不认识的形态：boom')
    expect(select.element.value).toBe('vertical_drama')
  })

  it('制作中：走 revise（kind=globals），不直接 PUT', async () => {
    store.detail = { warnings: [], problems: [], locked: true }
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    await w.find('[data-test="form-select"]').setValue('horizontal_short')
    await flushPromises()
    expect(calls.some(([m]) => m === 'put')).toBe(false)
    expect(calls.find(([m]) => m === 'revise')[1]).toEqual({ kind: 'globals', values: { form: 'horizontal_short' } })
  })
})
