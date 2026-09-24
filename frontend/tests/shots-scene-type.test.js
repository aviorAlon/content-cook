// 镜头编辑器「场景类型（可选）」下拉（剧本工艺清单里关系戏的那几条）：回读已有值、五种类型 + 不声明、改了随镜头字段一起 PUT。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const puts = []
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({ projectId: 'EP1', detail: null, cast: [], summary: null, gen: null })
  return {
    store,
    refreshProject: vi.fn(async () => {}),
    withBusy: vi.fn(async (fn) => fn()),
    runGenerate: vi.fn(), retake: vi.fn(), review: vi.fn(),
    cameraOptionsFor: () => ({ shots: [] }),
    reviseEdit: vi.fn(async () => null),
  }
})
vi.mock('../src/api.js', () => ({
  get: vi.fn(async () => ({ candidates: [] })), post: vi.fn(), del: vi.fn(), pollTask: vi.fn(),
  put: vi.fn(async (url, body) => { puts.push([url, body]); return {} }),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
}))

import Shots from '../src/views/project/Shots.vue'
import { store } from '../src/store.js'

const shot = (id, extra = {}) => ({
  id, dur: 4, cast: [], ref_frame: null, camera_move: '', shot: '中景', art: 'a', motion: 'm', sfx: '', entry_state: '', exit_state: '',
  speech_mode: '', gaze: '', purpose: '', scene_type: '', readable_text: [], entry: null, exit: null, axis: null, boundary_change: '', withhold: [], lines: [],
  budget: null, frame_url: null, video_url: null, links: { referenced_by: [], voice_over: [], inserts_after: [], notebook_text: [] }, ...extra,
})

async function mountAt(shotId) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/drama/p/:id/shots/:shotId?', component: Shots }] })
  await router.push(`/drama/p/EP1/shots/${shotId}`)
  await router.isReady()
  const w = mount(Shots, { global: { plugins: [router] } })
  await flushPromises()
  return w
}

beforeEach(() => {
  puts.length = 0
  store.detail = { editable: true, locked: false, shots: [shot('S1', { scene_type: 'rejection' }), shot('S2')] }
})

describe('Shots · 场景类型', () => {
  it('回读已有值；下拉是「不声明」加五种关系场景', async () => {
    const w = await mountAt('S1')
    const select = w.find('[data-test="scene-type"]')
    expect(select.exists()).toBe(true)
    expect(select.element.value).toBe('rejection')
    expect(select.findAll('option').map((o) => o.element.value)).toEqual(['', 'confession', 'reunion', 'forgiveness', 'rejection', 'breakup'])
    expect(select.findAll('option').map((o) => o.text())).toEqual(['（不声明）', '表白', '重逢', '原谅', '拒绝', '分手'])
  })

  it('没标的镜默认「不声明」；改成分手后保存，scene_type 随镜头字段一起发；清空发空串', async () => {
    const w = await mountAt('S2')
    const select = w.find('[data-test="scene-type"]')
    expect(select.element.value).toBe('')
    await select.setValue('breakup')
    const save = w.findAll('button').find((b) => b.text() === '保存这一镜')
    await save.trigger('click')
    await flushPromises()
    expect(puts.length).toBe(1)
    expect(puts[0][0]).toBe('/api/episodes/storyvideo/EP1/shots/S2')
    expect(puts[0][1].scene_type).toBe('breakup')
    await select.setValue('')
    await save.trigger('click')
    await flushPromises()
    expect(puts[1][1].scene_type).toBe('')
  })
})
