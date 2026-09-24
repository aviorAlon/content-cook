// 镜头编辑器「先不揭示」列表（DESIGN §28）：回读已有条目、揭示点只列本镜之后的镜、加一条并保存时随镜头字段一起 PUT，空行不发。
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
  speech_mode: '', gaze: '', purpose: '', readable_text: [], entry: null, exit: null, axis: null, boundary_change: '', withhold: [], lines: [],
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
  store.detail = { editable: true, locked: false, shots: [
    shot('S1', { withhold: [{ fact: '凶手是陈默', carrier: '脸', until: 'S3' }] }), shot('S2'), shot('S3'),
  ] }
})

describe('Shots · 先不揭示', () => {
  it('回读已有条目；揭示点下拉只列本镜之后的镜，外加「整集不揭示」', async () => {
    const w = await mountAt('S1')
    const editor = w.find('[data-test="withhold-editor"]')
    expect(editor.exists()).toBe(true)
    const inputs = editor.findAll('input')
    expect(inputs[0].element.value).toBe('凶手是陈默')
    expect(inputs[1].element.value).toBe('脸')
    const select = editor.find('select')
    expect(select.element.value).toBe('S3')
    expect(select.findAll('option').map((o) => o.element.value)).toEqual(['', 'S2', 'S3'])
    expect(editor.text()).toContain('不得揭示')   // 提示：只是提示词文本，不是保证
  })

  it('加一条填好后保存：withhold 随镜头字段一起发；整行空白的不发', async () => {
    const w = await mountAt('S2')
    const editor = w.find('[data-test="withhold-editor"]')
    expect(editor.find('table').exists()).toBe(false)
    const add = editor.findAll('button').find((b) => b.text().includes('加一条要藏的'))
    await add.trigger('click'); await add.trigger('click')
    await flushPromises()
    const rows = w.find('[data-test="withhold-editor"]').findAll('tr')
    expect(rows.length).toBe(2)
    await rows[0].findAll('input')[0].setValue('她怀孕了')
    await rows[0].findAll('input')[1].setValue('手上的戒指')
    await rows[0].find('select').setValue('S3')
    const save = w.findAll('button').find((b) => b.text() === '保存这一镜')
    await save.trigger('click')
    await flushPromises()
    expect(puts.length).toBe(1)
    expect(puts[0][0]).toBe('/api/episodes/storyvideo/EP1/shots/S2')
    expect(puts[0][1].withhold).toEqual([{ fact: '她怀孕了', carrier: '手上的戒指', until: 'S3' }])
  })
})
