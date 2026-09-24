import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
vi.mock('../src/api.js', () => ({
  get: vi.fn(async () => []), put: vi.fn(async () => ({})), post: vi.fn(async () => ({})),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({ projectId: 'EP1', detail: { warnings: [] }, summary: null, gen: null })
  return {
    store,
    roleOk: () => true,
    capabilityCheck: vi.fn(async () => ({ issues: [], errors: 0, warnings: 0 })),
    retryFailed: vi.fn(async (only, opts) => { calls.push(['retryFailed', only, opts]); return { reset: ['frame-S1'], advice: [] } }),
    runGenerate: vi.fn(async (through, only) => { calls.push(['runGenerate', through, only]); return true }),
    withBusy: vi.fn(async (fn) => fn()),
    refreshProject: vi.fn(async () => {}),
    reviseEdit: vi.fn(async () => null),
  }
})

import Overview from '../src/views/project/Overview.vue'
import { store } from '../src/store.js'

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div/>' } }, { path: '/drama/p/:id/shots/:shotId', component: { template: '<div/>' } }] })
}

const SUMMARY = {
  title: 'T', logline: '', shots_total: 3, total_seconds: 12, cast_count: 1, scene_count: 0, camera_profile: '', locked: true,
  frames: { total: 3, done: 1, passed: [], pending: ['S3'], retake: [], missing: ['S1', 'S2'] },
  videos: { total: 3, done: 0, passed: [], pending: [], retake: [], missing: ['S1', 'S2', 'S3'] },
  asr: { ran: false, pass: 0, fail: 0 }, final: { exists: false }, post_layers: { present: false, composed: false },
  next_step: { key: 'failed', label: '处理失败的任务', hint: '2 镜提交失败', shots: ['S1', 'S2'] },
  failures: [
    { stage: 'frames', shot_id: 'S1', step_id: 'frame-S1', error: 'QuotaExhausted: HTTP 402', kind: 'quota', label: '余额或配额不足', advice: '充值后重提', retryable: true },
    { stage: 'frames', shot_id: 'S2', step_id: 'frame-S2', error: 'ProviderRejected: moderation', kind: 'moderation', label: '内容被审核拦截', advice: '改写后重拍', retryable: false },
  ],
}

beforeEach(() => { calls.length = 0; store.summary = JSON.parse(JSON.stringify(SUMMARY)) })

describe('Overview · 失败任务卡', () => {
  it('列出失败的镜、原因分类与建议；重提按钮只数可重提的镜，审核拦截的另有强制按钮', async () => {
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const card = w.find('[data-test="failures-card"]')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('余额或配额不足')
    expect(card.text()).toContain('内容被审核拦截')
    expect(card.text()).toContain('改写后重拍')
    const buttons = card.findAll('button').map((b) => b.text())
    expect(buttons.some((t) => t.includes('重提失败任务（1 镜）'))).toBe(true)
    expect(buttons.some((t) => t.includes('强制重提'))).toBe(true)
  })

  it('点重提：先重置（不花钱），再对重置回来的镜发起首帧生成', async () => {
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const btn = w.find('[data-test="failures-card"]').findAll('button').find((b) => b.text().includes('重提失败任务'))
    await btn.trigger('click')
    await flushPromises()
    expect(calls[0]).toEqual(['retryFailed', null, { force: false }])
    expect(calls[1]).toEqual(['runGenerate', 'frames', ['S1']])
  })

  it('没有失败任务时不显示卡片', async () => {
    store.summary.failures = []
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    expect(w.find('[data-test="failures-card"]').exists()).toBe(false)
  })

  it('在飞的镜：显示「生成中」药丸（后台在等），「刷新」重新拉概览；后台没在等时先补发 generate-resume', async () => {
    const { post } = await import('../src/api.js')
    const { refreshProject } = await import('../src/store.js')
    store.summary.failures = []
    store.summary.in_progress = ['S1', 'S2']
    store.summary.watching = true
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    const pill = w.find('[data-test="in-progress-card"] [data-test="in-progress-pill"]')
    expect(pill.text()).toBe('生成中：S1、S2（后台在等服务商）')
    await w.find('[data-test="in-progress-refresh"]').trigger('click')
    await flushPromises()
    expect(refreshProject).toHaveBeenCalledTimes(1)
    expect(post).not.toHaveBeenCalled()
    store.summary.watching = false
    await flushPromises()
    expect(w.find('[data-test="in-progress-pill"]').text()).toContain('后台没有在等')
    await w.find('[data-test="in-progress-refresh"]').trigger('click')
    await flushPromises()
    expect(post).toHaveBeenCalledWith('/api/episodes/storyvideo/EP1/generate-resume')
    expect(refreshProject).toHaveBeenCalledTimes(2)
  })

  it('没有在飞的镜时不显示生成中卡', async () => {
    store.summary.in_progress = []
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    expect(w.find('[data-test="in-progress-card"]').exists()).toBe(false)
  })

  it('失败卡与等尾帧卡并排：两边都有时都显示，标签指向各自的镜', async () => {
    store.summary.tail_pending = [{ shot_id: 'S3', source: 'S1', mode: 'reference', reason: '等 S1 视频过检后抽尾帧' }]
    const w = mount(Overview, { global: { plugins: [router()] } })
    await flushPromises()
    expect(w.find('[data-test="failures-card"]').exists()).toBe(true)
    const pill = w.find('[data-test="tail-pending-card"] [data-test="tail-pending-pill"]')
    expect(pill.text()).toBe('S3 ← 等 S1 视频过检')
    expect(pill.attributes('href')).toBe('/drama/p/EP1/shots/S3')
  })
})
