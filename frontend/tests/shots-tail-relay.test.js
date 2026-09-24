// 镜头编辑器「尾帧续接」与「参考图用途」（DESIGN §30）：源镜下拉只列前面的镜、模式单选；用途的参考图下拉按本镜真的会带的参考图列，
// 尾帧选了就把依赖槽换成「尾帧」；chip 切换控制/不得控制；保存时随镜头字段一起 PUT（空行不发，不续接发空串）。
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

const LIN = { index: 1, target: 'cast:lin', source: 'lin', label_zh: '林小满定妆', label_en: 'Lin anchor', aliases: [] }
const shot = (id, extra = {}) => ({
  id, dur: 4, cast: ['lin'], ref_frame: null, camera_move: '', shot: '中景', art: 'a', motion: 'm', sfx: '', entry_state: '', exit_state: '',
  speech_mode: '', gaze: '', purpose: '', readable_text: [], entry: null, exit: null, axis: null, boundary_change: '', withhold: [], lines: [],
  ref_tail: null, reference_roles: [], reference_slots: [LIN],
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
  store.cast = [{ id: 'lin', name: '林小满', kind: 'character', asset_url: '/x' }]
  store.detail = { editable: true, locked: false, shots: [
    shot('S1'), shot('S2', { ref_frame: 'S1', reference_slots: [{ index: 0, target: 'prev_frame', source: 'S1', label_zh: '上一镜 S1 首帧', aliases: [] }, LIN] }),
    shot('S3', { ref_tail: { shot: 'S1', mode: 'direct' }, reference_roles: [{ target: 'cast:lin', use: '只取脸', control: ['脸'], ignore: ['服装'] }] }),
  ] }
})

describe('Shots · 尾帧续接与参考图用途', () => {
  it('回读：S3 的续接源镜与模式、用途条目；源镜下拉只列前面的镜', async () => {
    const w = await mountAt('S3')
    const tail = w.find('[data-test="tail-editor"]')
    expect(tail.find('select').element.value).toBe('S1')
    expect(tail.find('select').findAll('option').map((o) => o.element.value)).toEqual(['', 'S1', 'S2'])
    expect(tail.find('input[type="radio"][value="direct"]').element.checked).toBe(true)
    expect(w.find('[data-test="tail-info"]').text()).toContain('S1')
    const roles = w.find('[data-test="roles-editor"]')
    expect(roles.find('select').element.value).toBe('cast:lin')
    expect(roles.find('input').element.value).toBe('只取脸')
    // direct 模式下没有依赖槽：下拉只有角色定妆
    expect(roles.find('select').findAll('option').map((o) => o.element.value)).toEqual(['', 'cast:lin'])
  })

  it('S2 用参考镜：下拉先列「上一镜首帧」；选了尾帧续接（作参考图）后换成「尾帧」；加一条用途、切 chip、保存', async () => {
    const w = await mountAt('S2')
    const roles = w.find('[data-test="roles-editor"]')
    const add = roles.findAll('button').find((b) => b.text().includes('加一条用途'))
    await add.trigger('click'); await flushPromises()
    let select = w.find('[data-test="roles-editor"]').find('select')
    expect(select.findAll('option').map((o) => o.element.value)).toEqual(['', 'prev_frame', 'cast:lin'])
    expect(select.element.value).toBe('prev_frame')
    await w.find('[data-test="tail-editor"] select').setValue('S1')
    await flushPromises()
    select = w.find('[data-test="roles-editor"]').find('select')
    expect(select.findAll('option').map((o) => o.element.value)).toEqual(['', 'tail', 'cast:lin'])
    await select.setValue('tail')
    await w.find('[data-test="roles-editor"] input').setValue('只取场景与光线')
    const chips = w.find('[data-test="roles-editor"]').findAll('.chip')
    const ignorePose = chips.filter((c) => c.text() === '姿势')[1]   // 第二组是「不得控制」
    await ignorePose.trigger('click')
    const save = w.findAll('button').find((b) => b.text() === '保存这一镜')
    await save.trigger('click')
    await flushPromises()
    expect(puts.length).toBe(1)
    expect(puts[0][0]).toBe('/api/episodes/storyvideo/EP1/shots/S2')
    expect(puts[0][1].ref_tail).toEqual({ shot: 'S1', mode: 'reference' })
    expect(puts[0][1].reference_roles).toEqual([{ target: 'tail', use: '只取场景与光线', control: [], ignore: ['姿势'] }])
  })

  it('不续接 → 发空串；整行空白的用途不发', async () => {
    const w = await mountAt('S1')
    const add = w.find('[data-test="roles-editor"]').findAll('button').find((b) => b.text().includes('加一条用途'))
    await add.trigger('click'); await flushPromises()
    const save = w.findAll('button').find((b) => b.text() === '保存这一镜')
    await save.trigger('click')
    await flushPromises()
    expect(puts[0][1].ref_tail).toBe('')
    expect(puts[0][1].reference_roles).toEqual([])
  })
})
