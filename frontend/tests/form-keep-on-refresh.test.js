// 刷新别把没保存的表单冲掉（发 tag 前审计 FE-01）：refreshProject 每次都把 store.detail / cast / postLayers 换成新对象
// （生成期间每 8 秒一次；Post 页保存一块、Cast 页绑音色也会刷），镜头 / 角色 / 旁白与插卡 / 概览契约四页的表单
// 有改动时不能被冲回服务端的值；没改动的表单照常跟着服务端走；换镜 / 换角色 / 保存成功后才重灌。api 全 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const clone = (x) => JSON.parse(JSON.stringify(x))
const calls = []
// 真 refreshProject 的样子：四份都换成内容相同的新对象；测试用 server 钩子模拟服务端在刷新时的变化
const server = { patch: null }
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({ projectId: 'EP1', detail: null, summary: null, cast: null, postLayers: null, voices: [], gen: null, modules: [] })
  return {
    store,
    refreshProject: vi.fn(async () => {
      calls.push(['refresh'])
      store.detail = clone(store.detail); store.cast = clone(store.cast); store.postLayers = clone(store.postLayers)
      if (server.patch) { server.patch(store); server.patch = null }
    }),
    withBusy: vi.fn(async (fn) => fn()),
    runGenerate: vi.fn(), retake: vi.fn(), review: vi.fn(), retryFailed: vi.fn(),
    cameraOptionsFor: () => ({ shots: [] }),
    reviseEdit: vi.fn(async () => null),
    loadVoices: vi.fn(async () => []),
    roleOk: () => true,
    capabilityCheck: vi.fn(async () => ({ issues: [], errors: 0, warnings: 0 })),
  }
})
vi.mock('../src/api.js', () => ({
  get: vi.fn(async () => ({})),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    const m = url.match(/\/cast\/([^/]+)\/variants\/([^/]+)\/asset$/)
    if (m) return { id: m[1], variant: m[2], asset: `content/assets/EP1/${m[1]}_${m[2]}-abc.png`, asset_url: `/api/episodes/storyvideo/EP1/cast/${m[1]}/variants/${m[2]}/asset` }
    return { status: 'saved' }
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, clone(body)]); return { status: 'saved' } }),
  del: vi.fn(), ep: (id) => `/api/episodes/storyvideo/${id}`, pollTask: vi.fn(), fileToBase64: vi.fn(),
}))
vi.mock('../src/ui.js', async () => {
  const real = await vi.importActual('../src/ui.js')
  return { ...real, notice: vi.fn(async () => {}), toast: vi.fn(), confirm: vi.fn(async () => true), prompt: vi.fn(), custom: vi.fn() }
})

import Shots from '../src/views/project/Shots.vue'
import Cast from '../src/views/project/Cast.vue'
import Post from '../src/views/project/Post.vue'
import Overview from '../src/views/project/Overview.vue'
import { store } from '../src/store.js'

const shot = (id, extra = {}) => ({
  id, dur: 4, cast: [], ref_frame: null, camera_move: '', shot: '中景', art: 'a', motion: 'm', sfx: '', entry_state: '', exit_state: '',
  purpose: '', scene_type: '', readable_text: [], entry: null, exit: null, axis: null, boundary_change: '', withhold: [], lines: [],
  budget: null, frame_url: null, video_url: null, links: { referenced_by: [], voice_over: [], inserts_after: [], notebook_text: [] }, ...extra,
})
const detail = () => ({ id: 'EP1', editable: true, locked: false, shots: [shot('S1'), shot('S2', { shot: '全景' })], warnings: [], problems: [] })
const castRows = () => [{ id: 'lin', kind: 'character', name: '林小满', voice: '原描述', block: 'b', locks: [], variants: {}, used_in: ['S1'], voice_ref: '', voice_name: '' }]
const postLayers = () => ({
  id: 'EP1', locked: false, shot_ids: ['S1', 'S2'],
  voice_over: { lines: [] },
  post_graphics: { dubbing: { mode: 'off', duck: 0.15 }, music: [{ label: 'BGM-A', file: 'a.mp3', start: 0, end: 35, gain: 0.35, duck: 1, fade_in: 2, fade_out: 2 }], phone_ui_inserts: [], notebook_text: [] },
  subtitle: { language: 'zh', chinese_font_size: 46 },
  subtitle_effective: { language: 'zh', position: 'bottom', display: 'speech', chinese_font_size: 46, chinese_color: '#FFFFFF', outline_color: '#111111', outline_width: 4, band_alpha: 0.22, safe_bottom_percent: 7 },
  subtitle_defaults: {}, fonts_available: [], overlays: { preset: 'none', custom: false, regions: [], presets: [] },
  readiness: { dubbing: { shots: [], missing_tts_cast: {}, lip_risk: [] }, music: [] },
})
const SUMMARY = {
  title: 'T', logline: '', shots_total: 2, total_seconds: 8, cast_count: 1, scene_count: 0, camera_profile: '', locked: false,
  frames: { total: 2, done: 0, passed: [], pending: [], retake: [], missing: [] }, videos: { total: 2, done: 0, passed: [], pending: [], retake: [], missing: [] },
  asr: { ran: false }, final: { exists: false }, post_layers: { present: false, composed: false }, next_step: { key: 'prepare', label: '开始制作', hint: 'h' }, failures: [],
}

function makeRouter(component, path) {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div/>' } }, { path: '/voices', component: { template: '<div/>' } },
    { path, component },
    { path: '/drama/p/:id/:rest(.*)*', component: { template: '<div/>' } },
  ] })
}
async function mountAt(component, path, url) {
  const router = makeRouter(component, path)
  await router.push(url); await router.isReady()
  const w = mount(component, { global: { plugins: [router] } })
  await flushPromises()
  return { w, router }
}
const fieldInput = (w, label, tag = 'input') => w.findAll('.field').find(f => f.text().includes(label)).find(tag)
const button = (w, text) => w.findAll('button').find(b => b.text() === text)

beforeEach(() => {
  calls.length = 0; server.patch = null
  store.projectId = 'EP1'; store.detail = detail(); store.cast = castRows(); store.postLayers = postLayers(); store.summary = clone(SUMMARY); store.gen = null
})

describe('Shots：表单有改动时刷新不冲掉', () => {
  it('改了「画面」不保存，store.detail 换成同内容的新对象 → 输入还在；换镜才重灌', async () => {
    const { w, router } = await mountAt(Shots, '/drama/p/:id/shots/:shotId?', '/drama/p/EP1/shots/S1')
    const area = () => fieldInput(w, '画面（景别', 'textarea')
    expect(area().element.value).toBe('中景')
    await area().setValue('特写：她抬头')
    store.detail = clone(store.detail); await flushPromises()
    expect(area().element.value).toBe('特写：她抬头')
    // 台词行这类靠按钮改的也算改动：加了一句再刷新，那一行还在
    await button(w, '+ 加一句').trigger('click'); await flushPromises()
    store.detail = clone(store.detail); await flushPromises()
    expect(w.findAll('.field').find(f => f.text().startsWith('台词')).findAll('tr').length).toBe(1)
    // 换镜：按新镜重灌
    await router.push('/drama/p/EP1/shots/S2'); await flushPromises()
    expect(area().element.value).toBe('全景')
  })

  it('没改动的表单照常跟着服务端；保存成功后按服务端的值重灌，之后的刷新也跟着走', async () => {
    const { w } = await mountAt(Shots, '/drama/p/:id/shots/:shotId?', '/drama/p/EP1/shots/S1')
    const area = () => fieldInput(w, '画面（景别', 'textarea')
    const next = detail(); next.shots[0].shot = '远景（后台改的）'
    store.detail = next; await flushPromises()
    expect(area().element.value).toBe('远景（后台改的）')
    await area().setValue('特写')
    server.patch = (s) => { s.detail.shots[0].shot = '特写（服务端规范化）' }
    await button(w, '保存这一镜').trigger('click'); await flushPromises()
    expect(calls.find(([m]) => m === 'put')[2].shot).toBe('特写')
    expect(area().element.value).toBe('特写（服务端规范化）')
    // 保存过就不再算有改动：下一次刷新照常跟着服务端
    const later = clone(store.detail); later.shots[0].shot = '又改了'
    store.detail = later; await flushPromises()
    expect(area().element.value).toBe('又改了')
  })
})

describe('Post：三块各自记改动，保存一块不冲掉另外两块', () => {
  it('字幕字号改 72、配乐加了一段都没保存，点「保存配音设置」（内部刷新）→ 字号还是 72、配乐行还在', async () => {
    const { w } = await mountAt(Post, '/drama/p/:id/post', '/drama/p/EP1/post')
    const fontSize = () => w.find('[data-test="subtitle-card"] input[type="number"][max="300"]')
    const musicRows = () => w.findAll('[data-test="music-card"] .card.small')
    expect(fontSize().element.value).toBe('46')
    await fontSize().setValue(72)
    await button(w, '+ 加一段').trigger('click'); await flushPromises()
    expect(musicRows().length).toBe(2)
    await fieldInput(w, '配音时原声保留比例').setValue(0.5)
    server.patch = (s) => { s.postLayers.post_graphics.dubbing = { mode: 'off', duck: 0.5 } }
    await button(w, '保存配音设置').trigger('click'); await flushPromises()
    expect(calls.map(c => c[0])).toEqual(['put', 'refresh'])
    expect(fontSize().element.value).toBe('72')
    expect(musicRows().length).toBe(2)
    // 保存过的配音块不再算有改动：之后的刷新跟着服务端；没保存的字幕块仍然守住
    const later = clone(store.postLayers); later.post_graphics.dubbing.duck = 0.9; later.subtitle_effective.chinese_font_size = 50
    store.postLayers = later; await flushPromises()
    expect(fieldInput(w, '配音时原声保留比例').element.value).toBe('0.9')
    expect(fontSize().element.value).toBe('72')
  })

  it('没改动时刷新照常同步三块', async () => {
    const { w } = await mountAt(Post, '/drama/p/:id/post', '/drama/p/EP1/post')
    const later = postLayers(); later.subtitle_effective.chinese_font_size = 60; later.post_graphics.music = []; later.post_graphics.dubbing = { mode: 'all', duck: 0.3 }
    store.postLayers = later; await flushPromises()
    expect(w.find('[data-test="subtitle-card"] input[type="number"][max="300"]').element.value).toBe('60')
    expect(w.findAll('[data-test="music-card"] .card.small').length).toBe(0)
    expect(fieldInput(w, '配音时原声保留比例').element.value).toBe('0.3')
  })
})

describe('Cast：表单有改动时刷新不冲掉', () => {
  it('改了名字不保存，store.cast 换成同内容的新数组 → 名字还在；绑音色后只更新声音描述', async () => {
    const { w } = await mountAt(Cast, '/drama/p/:id/cast/:castId?', '/drama/p/EP1/cast/lin')
    const name = () => fieldInput(w, '名字（角色用')
    expect(name().element.value).toBe('林小满')
    await name().setValue('林小满（改）')
    store.cast = clone(store.cast); await flushPromises()
    expect(name().element.value).toBe('林小满（改）')
    // 绑音色：服务端把声音描述拷进角色，表单里的描述要跟着变，名字的改动不能丢
    store.voices = [{ id: 'a', name: '音色a', voice: 'zh-CN-a', description: '低沉', has_sample: false }]
    await flushPromises()
    const bindSelect = () => w.findAll('select').find(s => s.findAll('option').some(o => o.element.value === 'a'))
    await bindSelect().setValue('a')
    // 下拉里选了还没点「绑定」：刷新（服务端绑定没变）不能把选项冲回去
    store.cast = clone(store.cast); await flushPromises()
    expect(bindSelect().element.value).toBe('a')
    server.patch = (s) => { s.cast[0].voice = '低沉（来自音色库）'; s.cast[0].voice_ref = 'a'; s.cast[0].voice_name = '音色a' }
    await button(w, '绑定').trigger('click'); await flushPromises()
    expect(name().element.value).toBe('林小满（改）')
    expect(fieldInput(w, '声音描述').element.value).toBe('低沉（来自音色库）')
  })

  it('改了名字不保存就上传变体锚图：名字还在，锚图也接进表单，之后「保存」不会拿旧变体表把锚图冲掉', async () => {
    store.cast = [{ ...castRows()[0], variants: { rain: { label: '雨天', delta: '头发湿贴额头', asset: '', asset_url: null, used_in: [] } } }]
    const { w } = await mountAt(Cast, '/drama/p/:id/cast/:castId?', '/drama/p/EP1/cast/lin')
    const name = () => fieldInput(w, '名字（角色用')
    await name().setValue('林小满（改）')
    server.patch = (s) => { Object.assign(s.cast[0].variants.rain, { asset: 'content/assets/EP1/lin_rain-abc.png', asset_url: '/api/episodes/storyvideo/EP1/cast/lin/variants/rain/asset' }) }
    const file = w.find('input[type="file"][accept="image/png,image/jpeg,image/webp"]')
    // 页面上第一个图片上传框是变体那一行的（锚图区的在后面）
    expect(file.element.closest('label').textContent).toContain('上传变体锚图')
    Object.defineProperty(file.element, 'files', { value: [new File(['x'], 'rain.png', { type: 'image/png' })] })
    await file.trigger('change'); await flushPromises()
    expect(calls.some(([m, url]) => m === 'post' && url.endsWith('/cast/lin/variants/rain/asset'))).toBe(true)
    expect(name().element.value).toBe('林小满（改）')
    expect(w.find('img[src="/api/episodes/storyvideo/EP1/cast/lin/variants/rain/asset"]').exists()).toBe(true)
    await button(w, '保存').trigger('click'); await flushPromises()
    const body = calls.find(([m]) => m === 'put')[2]
    expect(body.name).toBe('林小满（改）')
    expect(body.variants.rain.asset).toBe('content/assets/EP1/lin_rain-abc.png')
  })

  it('没改动时刷新照常跟着服务端；保存后按服务端重灌', async () => {
    const { w } = await mountAt(Cast, '/drama/p/:id/cast/:castId?', '/drama/p/EP1/cast/lin')
    const name = () => fieldInput(w, '名字（角色用')
    const next = castRows(); next[0].name = '林小满（后台改的）'
    store.cast = next; await flushPromises()
    expect(name().element.value).toBe('林小满（后台改的）')
    await name().setValue('小满')
    server.patch = (s) => { s.cast[0].name = '小满' }
    await button(w, '保存').trigger('click'); await flushPromises()
    expect(calls.find(([m]) => m === 'put')[2].name).toBe('小满')
    const later = clone(store.cast); later[0].name = '又改了'
    store.cast = later; await flushPromises()
    expect(name().element.value).toBe('又改了')
  })
})

describe('Overview：契约有改动时刷新不冲掉', () => {
  it('改了钩子（保存按钮已亮），store.detail 换成同内容的新对象 → 钩子还在、按钮还亮；保存后清掉', async () => {
    store.detail = { ...detail(), episode_contract: { premise: 'p', hook: '原钩子', cliffhanger: 'c', must_pay_off: [], carry_over: [] }, series: { id: 'kitchen', episode_no: 2 } }
    const { w } = await mountAt(Overview, '/drama/p/:id/overview', '/drama/p/EP1/overview')
    const hook = () => w.find('[data-test="contract-hook"]')
    expect(hook().element.value).toBe('原钩子')
    await hook().setValue('新钩子')
    expect(w.find('[data-test="save-contract"]').attributes('disabled')).toBeUndefined()
    store.detail = clone(store.detail); await flushPromises()
    expect(hook().element.value).toBe('新钩子')
    expect(w.find('[data-test="save-contract"]').attributes('disabled')).toBeUndefined()
    server.patch = (s) => { s.detail.episode_contract.hook = '新钩子' }
    await w.find('[data-test="save-contract"]').trigger('click'); await flushPromises()
    expect(hook().element.value).toBe('新钩子')
    expect(w.find('[data-test="save-contract"]').attributes('disabled')).toBeDefined()
    // 没改动：刷新跟着服务端
    const later = clone(store.detail); later.episode_contract.hook = '后台改的'
    store.detail = later; await flushPromises()
    expect(hook().element.value).toBe('后台改的')
  })
})
