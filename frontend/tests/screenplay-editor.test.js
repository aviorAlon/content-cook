// 项目页 · 剧本标签与逐场编辑器（DESIGN §32）：GET 拉剧本 → 场次卡（节拍/对白/契约/人物表）→ 改 → PUT 整份；
// 告警顶部汇总 + 逐场贴在那一场；没有剧本时是空状态 + 「去新建页起草剧本」；拆镜带 use_screenplay 并跳到镜头页。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let screenplayResponse = null   // GET /screenplay 的返回；null = 404

function doc() {
  return {
    title: '厨房关停',
    logline: '排班员被当众揭开',
    contract: { premise: 'p', hook: '齐曼当众要求出示排班记录', cliffhanger: 'c', must_pay_off: ['订单缺口的原因'], carry_over: [] },
    cast: [{ id: 'ye', name: '叶澄', block: '三十岁' }, { id: 'qi', name: '齐曼', block: '' }],
    scenes: [
      { id: 'S01', heading: '内 · 共享厨房 · 清晨', place: '', time: '', beats: ['齐曼把订单拍在桌上', '叶澄合上排班本'],
        dialogue: [{ who: 'qi', zh: '把完整的排班记录拿出来。' }], note: '' },
      { id: 'S02', heading: '回忆', place: '', time: '', beats: [], dialogue: [], note: '' },
    ],
  }
}
const WARNINGS = [
  'scene S02: 这一场既没有节拍也没有对白——写出这一场发生了什么（谁做了什么），或者删掉这一场',
  '全集：集尾压力「c」没有落在最后一场——最后一场要写出某人已经决定做什么、或者某个后果已经启动',
]

vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url.includes('/screenplay')) {
      if (!screenplayResponse) { const e = new Error('没有剧本'); e.status = 404; throw e }
      return screenplayResponse
    }
    if (url.startsWith('/api/episodes/next-id')) return { id: 'EP-9' }
    return {}
  }),
  put: vi.fn(async (url, body) => {
    calls.push(['put', url, body])
    return { episode_id: 'EP-1', screenplay: body.screenplay, warnings: ['scene S01: 又一条提醒——改法'], stats: { scenes: 1, dialogue_lines: 0, words: 0 } }
  }),
  post: vi.fn(async (url, body) => { calls.push(['post', url, body]); return { job_id: 'j1', episode_id: body?.episode_id } }),
  pollJob: vi.fn(async (id) => { calls.push(['poll', id]); return { status: 'succeeded', result: { spec: { id: 'EP-9', shots: [] } } } }),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({ projectId: 'EP-1', detail: { id: 'EP-1', camera_profile: 'dark_comedy_vertical', logline: 'l', shots: [{ id: 'S1', dur: 6 }] } })
  return { store, loadEpisodes: vi.fn(async () => { calls.push(['loadEpisodes']) }), withBusy: vi.fn(async (fn) => fn()) }
})
vi.mock('../src/ui.js', () => ({
  notice: vi.fn(async () => {}), confirm: vi.fn(async () => true), toast: vi.fn((m) => calls.push(['toast', m])), prompt: vi.fn(),
}))

import Screenplay from '../src/views/project/Screenplay.vue'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/drama/new', component: { template: '<div>new</div>' } },
      { path: '/drama/p/:id/screenplay', component: Screenplay },
      { path: '/drama/p/:id/shots', component: { template: '<div>shots</div>' } },
    ],
  })
}
async function mountTab() {
  const router = makeRouter()
  router.push('/drama/p/EP-1/screenplay')
  await router.isReady()
  const w = mount(Screenplay, { global: { plugins: [router] } })
  await flushPromises()
  return { w, router }
}

beforeEach(() => {
  calls.length = 0
  screenplayResponse = { episode_id: 'EP-1', screenplay: doc(), warnings: [...WARNINGS], stats: { scenes: 2, dialogue_lines: 1, words: 30 } }
})

describe('剧本标签 · 打开与告警', () => {
  it('GET 拉剧本，场次卡逐场铺开；告警顶部汇总一份，逐场的贴在那一场里', async () => {
    const { w } = await mountTab()
    expect(calls[0]).toEqual(['get', '/api/episodes/storyvideo/EP-1/screenplay'])
    const cards = w.findAll('[data-test="scene-card"]')
    expect(cards.length).toBe(2)
    expect(cards[0].find('[data-test="scene-heading"]').element.value).toBe('内 · 共享厨房 · 清晨')
    expect(cards[0].findAll('[data-test="beat-input"]').map(i => i.element.value)).toEqual(['齐曼把订单拍在桌上', '叶澄合上排班本'])
    expect(w.find('[data-test="sp-hook"]').element.value).toBe('齐曼当众要求出示排班记录')
    expect(w.findAll('[data-test="sp-cast-row"]').length).toBe(2)
    // 顶部汇总：两条都在
    const summary = w.find('[data-test="sp-warning-summary"]')
    expect(summary.text()).toContain('这一场既没有节拍也没有对白')
    expect(summary.text()).toContain('集尾压力')
    // 逐场：只有 S02 那一张卡有，而且去掉了「scene S02:」前缀
    expect(cards[0].find('[data-test="scene-warnings"]').exists()).toBe(false)
    const inline = cards[1].find('[data-test="scene-warnings"]')
    expect(inline.exists()).toBe(true)
    expect(inline.text()).toContain('这一场既没有节拍也没有对白')
    expect(inline.text()).not.toContain('scene S02:')
  })

  it('没有剧本：空状态 + 「去新建页起草剧本」，不显示编辑器', async () => {
    screenplayResponse = null
    const { w } = await mountTab()
    expect(w.find('[data-test="sp-empty"]').exists()).toBe(true)
    expect(w.find('[data-test="sp-go-new"]').attributes('href')).toContain('/drama/new')
    expect(w.find('[data-test="screenplay-editor"]').exists()).toBe(false)
  })
})

describe('剧本标签 · 逐场编辑', () => {
  it('加场 / 删场 / 上下移动，新场的 id 自动续号', async () => {
    const { w } = await mountTab()
    await w.find('[data-test="scene-add"]').trigger('click')
    let cards = w.findAll('[data-test="scene-card"]')
    expect(cards.length).toBe(3)
    expect(cards[2].find('.mono').text()).toBe('S03')
    // 在第一场下面插一场：id 不跟已有的撞
    await cards[0].find('[data-test="scene-insert"]').trigger('click')
    cards = w.findAll('[data-test="scene-card"]')
    expect(cards.map(c => c.find('.mono').text())).toEqual(['S01', 'S04', 'S02', 'S03'])
    // 往下移一格
    await cards[0].find('[data-test="scene-down"]').trigger('click')
    expect(w.findAll('[data-test="scene-card"]').map(c => c.find('.mono').text())).toEqual(['S04', 'S01', 'S02', 'S03'])
    // 删掉两场
    await w.findAll('[data-test="scene-card"]')[0].find('[data-test="scene-del"]').trigger('click')
    await w.findAll('[data-test="scene-card"]')[2].find('[data-test="scene-del"]').trigger('click')
    expect(w.findAll('[data-test="scene-card"]').map(c => c.find('.mono').text())).toEqual(['S01', 'S02'])
  })

  it('节拍：改、加、删、上下移动', async () => {
    const { w } = await mountTab()
    const scene = w.findAll('[data-test="scene-card"]')[0]
    await scene.findAll('[data-test="beat-input"]')[0].setValue('齐曼把订单摔在桌上')
    await scene.find('[data-test="beat-add"]').trigger('click')
    await scene.findAll('[data-test="beat-input"]')[2].setValue('叶澄没说话')
    expect(scene.findAll('[data-test="beat-input"]').map(i => i.element.value))
      .toEqual(['齐曼把订单摔在桌上', '叶澄合上排班本', '叶澄没说话'])
    await scene.findAll('[data-test="beat-up"]')[2].trigger('click')
    expect(scene.findAll('[data-test="beat-input"]').map(i => i.element.value))
      .toEqual(['齐曼把订单摔在桌上', '叶澄没说话', '叶澄合上排班本'])
    await scene.findAll('[data-test="beat-del"]')[0].trigger('click')
    expect(scene.findAll('[data-test="beat-input"]').map(i => i.element.value)).toEqual(['叶澄没说话', '叶澄合上排班本'])
  })

  it('对白：说话人下拉只有人物表里的人和旁白；加句、改词、删、移动', async () => {
    const { w } = await mountTab()
    const scene = w.findAll('[data-test="scene-card"]')[0]
    const who = scene.find('[data-test="line-who"]')
    expect(who.findAll('option').map(o => o.element.value)).toEqual(['ye', 'qi', 'narrator'])
    expect(who.findAll('option').map(o => o.text())).toEqual(['叶澄（ye）', '齐曼（qi）', '旁白'])
    expect(who.element.value).toBe('qi')
    await scene.find('[data-test="line-add"]').trigger('click')
    const lines = scene.findAll('[data-test="line-row"]')
    expect(lines.length).toBe(2)
    expect(lines[1].find('[data-test="line-who"]').element.value).toBe('ye')   // 默认人物表第一个
    await lines[1].find('[data-test="line-zh"]').setValue('记录就在这里。')
    await lines[1].find('[data-test="line-up"]').trigger('click')
    expect(scene.findAll('[data-test="line-zh"]').map(i => i.element.value)).toEqual(['记录就在这里。', '把完整的排班记录拿出来。'])
    await scene.findAll('[data-test="line-del"]')[1].trigger('click')
    expect(scene.findAll('[data-test="line-row"]').length).toBe(1)
  })

  it('保存：PUT 整份剧本（改过的场次、节拍、对白、契约都在），告警按响应刷新', async () => {
    const { w } = await mountTab()
    expect(w.find('[data-test="sp-save"]').attributes('disabled')).toBeDefined()
    const scene = w.findAll('[data-test="scene-card"]')[1]
    await scene.find('[data-test="scene-heading"]').setValue('外 · 巷口 · 夜')
    await scene.find('[data-test="beat-add"]').trigger('click')
    await scene.findAll('[data-test="beat-input"]')[0].setValue('叶澄把钥匙塞进齐曼手里')
    await w.find('[data-test="sp-cliffhanger"]').setValue('叶澄把备用钥匙交给齐曼')
    expect(w.find('[data-test="sp-save"]').attributes('disabled')).toBeUndefined()
    await w.find('[data-test="sp-save"]').trigger('click')
    await flushPromises()
    const [, url, body] = calls.find(([m]) => m === 'put')
    expect(url).toBe('/api/episodes/storyvideo/EP-1/screenplay')
    expect(body.screenplay.scenes.length).toBe(2)
    expect(body.screenplay.scenes[1]).toMatchObject({ id: 'S02', heading: '外 · 巷口 · 夜', beats: ['叶澄把钥匙塞进齐曼手里'], dialogue: [] })
    expect(body.screenplay.scenes[0].dialogue[0]).toEqual({ who: 'qi', zh: '把完整的排班记录拿出来。' })
    expect(body.screenplay.contract.cliffhanger).toBe('叶澄把备用钥匙交给齐曼')
    expect(body.screenplay.cast.map(c => c.id)).toEqual(['ye', 'qi'])
    // 保存后：按钮回到「已保存」，告警换成响应里的那份
    expect(w.find('[data-test="sp-save"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="sp-warning-summary"]').text()).toContain('又一条提醒')
    expect(calls.some(([m]) => m === 'toast')).toBe(true)
  })

  it('人物表加人 / 删人，契约的清单也能加条', async () => {
    const { w } = await mountTab()
    await w.find('[data-test="sp-cast-add"]').trigger('click')
    const rows = w.findAll('[data-test="sp-cast-row"]')
    expect(rows.length).toBe(3)
    await rows[2].find('[data-test="sp-cast-id"]').setValue('zhou')
    await rows[2].find('[data-test="sp-cast-name"]').setValue('周姨')
    // 新角色立刻出现在说话人下拉里
    expect(w.find('[data-test="line-who"]').findAll('option').map(o => o.element.value)).toEqual(['ye', 'qi', 'zhou', 'narrator'])
    await w.find('[data-test="sp-chip-add-carry_over"]').trigger('click')
    await w.findAll('[data-test="sp-chip-carry_over"]')[0].find('input').setValue('齐曼不知道冷柜后的账本')
    await w.find('[data-test="sp-save"]').trigger('click')
    await flushPromises()
    const body = calls.find(([m]) => m === 'put')[2]
    expect(body.screenplay.cast[2]).toMatchObject({ id: 'zhou', name: '周姨' })
    expect(body.screenplay.contract.carry_over).toEqual(['齐曼不知道冷柜后的账本'])
    await w.findAll('[data-test="sp-cast-del"]')[0].trigger('click')
    expect(w.findAll('[data-test="sp-cast-row"]').length).toBe(2)
  })
})

describe('剧本标签 · 拆镜', () => {
  it('拆镜：脏了先保存 → 剧本复制到新编号 → draft job 带 use_screenplay → 存成新项目 → 跳到镜头页', async () => {
    const { w, router } = await mountTab()
    await w.find('[data-test="sp-breakdown"]').trigger('click')
    await flushPromises()
    expect(calls.some(([m, u]) => m === 'get' && u.startsWith('/api/episodes/next-id'))).toBe(true)
    const copied = calls.find(([m, u]) => m === 'put' && u === '/api/episodes/storyvideo/EP-9/screenplay')
    expect(copied[2].screenplay.scenes.length).toBe(2)
    const job = calls.find(([m, u]) => m === 'post' && u === '/api/scripts/jobs/draft')
    expect(job[2]).toMatchObject({ episode_id: 'EP-9', use_screenplay: true, camera_profile: 'dark_comedy_vertical', target_seconds: 6 })
    expect(calls.some(([m, id]) => m === 'poll' && id === 'j1')).toBe(true)
    const saved = calls.find(([m, u]) => m === 'post' && u === '/api/scripts/save')
    expect(saved[2].episode_id).toBe('EP-9')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/drama/p/EP-9/shots')
  })

  it('拆镜前编辑器是脏的：先把这份剧本存回原项目，再拿去拆', async () => {
    const { w } = await mountTab()
    await w.find('[data-test="sp-title"]').setValue('厨房关停 · 改')
    await w.find('[data-test="sp-breakdown"]').trigger('click')
    await flushPromises()
    const puts = calls.filter(([m]) => m === 'put')
    expect(puts[0][1]).toBe('/api/episodes/storyvideo/EP-1/screenplay')
    expect(puts[0][2].screenplay.title).toBe('厨房关停 · 改')
    expect(puts[1][1]).toBe('/api/episodes/storyvideo/EP-9/screenplay')
  })
})
