// 图片集模块前端（docs/imageset-spec.md §5）：表情与头像 sticker / 纪念写真 portrait 共用一套页面——首页列表与新建流程、
// 清单表（改 prompt → PUT、重出 → generate only+force、采用 → pick）、产物（后处理 + 导出）、高级、侧栏块与手风琴、路由 meta。
// api 全部 mock；后端并行落地，这里只认契约里的接口形状。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let lists, detail, templates, taskResult, postResult, exportResult
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    const mm = /^\/api\/(sticker|portrait)(?:\/(.*))?$/.exec(url)
    if (mm && !mm[2]) return lists[mm[1]]
    if (mm && mm[2] === 'templates') return templates[mm[1]]
    if (mm) return detail
    if (url.startsWith('/api/tasks/')) return taskResult
    return {}
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    const mm = /^\/api\/(sticker|portrait)(?:\/(.*))?$/.exec(url)
    if (mm && !mm[2]) return { id: `${mm[1] === 'sticker' ? 'ST' : 'PT'}-20260909-2`, spec: { ...body } }
    if (url.endsWith('/examples/create')) return { id: `${mm[1] === 'sticker' ? 'ST' : 'PT'}-20260909-9`, example: body.name }
    if (url.endsWith('/generate')) return { task_id: 't-gen', cost_note: '3 次生图请求' }
    if (url.endsWith('/post')) return postResult
    if (url.endsWith('/export')) return exportResult
    if (url.endsWith('/pick') || url.endsWith('/expand') || url.endsWith('/assets')) return { ok: true }
    return { task_id: 't-1' }
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return { id: 'ST-20260909-1', spec: body } }),
  del: vi.fn(async (url) => { calls.push(['del', url]); return { archived_to: '.webui_deleted/x' } }),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(async (id) => { calls.push(['poll', id]); return taskResult }),
  pollJob: vi.fn(), sleep: vi.fn(async () => {}), fileToBase64: vi.fn(async () => 'QUJD'),
}))
const confirmMock = vi.fn(async () => true)
vi.mock('../src/ui.js', () => ({
  prompt: vi.fn(), confirm: (...a) => confirmMock(...a), notice: vi.fn(), toast: vi.fn(), custom: vi.fn(), toasts: [],
  T: { role: { text: '剧本文本', image: '生图', voice: '语音', video: '视频' } },
}))

import { store } from '../src/store.js'
import { routes } from '../src/router.js'
import { setLang } from '../src/i18n.js'
import { loadSet, loadTemplates, progressOf, normalizeSchema, defaultOptions, itemImageUrl, itemAttempts, clearSet, templateLabel, templateDescription, choiceLabel } from '../src/imageset.js'
import SetHome from '../src/views/imageset/SetHome.vue'
import SetNew from '../src/views/imageset/SetNew.vue'
import Set from '../src/views/imageset/Set.vue'
import SetItems from '../src/views/imageset/SetItems.vue'
import SetOutputs from '../src/views/imageset/SetOutputs.vue'
import SetAdvanced from '../src/views/imageset/SetAdvanced.vue'
import Sidebar from '../src/components/Sidebar.vue'
import Home from '../src/views/Home.vue'

const stub = { template: '<div/>' }
function stubRouter(module = 'sticker') {
  const meta = { module }
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: stub }, { path: '/settings', component: stub }, { path: '/voices', component: stub },
    { path: '/drama', component: stub }, { path: '/drama/new', component: stub }, { path: '/drama/libraries', component: stub }, { path: '/drama/p/:id/overview', component: stub },
    { path: '/picturebook', component: stub }, { path: '/picturebook/new', component: stub }, { path: '/picturebook/b/:id/:sub', component: stub },
    ...['sticker', 'portrait'].flatMap(m => [
      { path: `/${m}`, component: stub, meta: { module: m } }, { path: `/${m}/new`, component: stub, meta: { module: m } },
      { path: `/${m}/s/:id/:sub`, component: stub, meta: { module: m } },
    ]),
  ] })
}
async function mountAt(Comp, path = '/') {
  const r = stubRouter(); await r.push(path); await r.isReady()
  const w = mount(Comp, { global: { plugins: [r] } })
  await flushPromises()
  return { w, r }
}
function sampleSpec() {
  return {
    id: 'ST-20260909-1', module: 'sticker', title: '小满表情包', template: 'emoji_pack',
    subject: { kind: 'character', name: '小满', block: '二十六七岁，齐肩黑发低马尾，圆框眼镜', assets: ['content/assets/ST-20260909-1/ref1.png'], fidelity: 85 },
    style: 'flat_cute', style_anchor: '', options: { expressions: ['开心', '大笑', '哭'], with_caption: false },
    image_params: { model: '@image', image_size: '2K' },
    items: [
      { id: 'I01', label: '开心', prompt: 'happy face', aspect_ratio: '1:1', status: 'done', current: 'I01-2.png', attempts: [{ file: 'I01-1.png', created_at: '2026-09-09T10:00:00', model: 'x' }, { file: 'I01-2.png', created_at: '2026-09-09T10:05:00', model: 'x' }] },
      { id: 'I02', label: '大笑', prompt: 'laughing', aspect_ratio: '1:1', status: 'todo', current: null, attempts: [] },
      { id: 'I03', label: '哭', prompt: 'crying', aspect_ratio: '1:1', status: 'failed', current: null, attempts: [] },
    ],
    picks: ['I01'],
  }
}
// 后端 detail 外壳：spec + 逐张行（status / current_url / attempts[].url / outputs）+ exports
function sampleDetail(spec = sampleSpec(), { outputs = false, exports = [] } = {}) {
  const id = spec.id, m = spec.module
  return {
    id, module: m, title: spec.title, template: spec.template, template_name: '表情包', spec,
    progress: { done: 1, total: 3, picked: 1, exported: exports.length > 0 },
    items: spec.items.map(it => ({
      id: it.id, status: it.status, current: it.current, current_url: it.current ? `/api/${m}/${id}/artifact/raw/${it.current}` : null,
      attempts: it.attempts.map(a => ({ ...a, url: `/api/${m}/${id}/artifact/raw/${a.file}` })),
      outputs: outputs && it.current ? [{ file: `${it.id}-240.png`, url: `/api/${m}/${id}/artifact/out/${it.id}-240.png`, size: '240×240', bytes: 20480 }, { file: `${it.id}-120.png`, url: `/api/${m}/${id}/artifact/out/${it.id}-120.png`, size: '120×120', bytes: 8192 }] : [],
      error: it.status === 'failed' ? '审核拦截' : null,
    })),
    exports,
  }
}

beforeEach(() => {
  calls.length = 0
  confirmMock.mockReset(); confirmMock.mockResolvedValue(true)
  lists = {
    sticker: [
      { id: 'ST-20260909-1', title: '小满表情包', template: 'emoji_pack', style: 'flat_cute', progress: { done: 1, total: 3, picked: 1, exported: false }, cover_url: '/api/sticker/ST-20260909-1/artifact/raw/I01-2.png' },
      { id: 'ST-20260909-3', title: '咖啡贴图', template: 'sticker_set', template_name: '贴图', progress: { done: 8, total: 8, picked: 0, exported: true }, cover_url: null },
    ],
    portrait: [
      { id: 'PT-20260909-1', title: '宝宝百天', template: 'baby_100', progress: { done: 0, total: 6, picked: 0, exported: false }, cover_url: null },
    ],
  }
  templates = {
    sticker: { templates: [
      { id: 'emoji_pack', name: '表情包', english: 'Emoji pack', description: '每个表情一张 1:1，白底转透明，微信表情包目录', styles: [{ id: 'flat_cute', name: '扁平可爱', anchor: 'flat cute vector sticker' }, { id: 'line_doodle', name: '线稿涂鸦', anchor: 'line doodle' }],
        options_schema: { expressions: { type: 'list', default: ['开心', '大笑'], label: '表情清单' }, with_caption: { type: 'bool', default: false, label: '叠中文短语' } } },
      { id: 'avatar_set', name: '头像', description: '每风格一张 1:1', styles: [{ id: 'real', name: '写实', anchor: 'photo real' }],
        options_schema: { styles: { type: 'list', default: ['写实', '油画'] }, crop: { type: 'choice', choices: ['头肩', '半身'], default: '头肩' } } },
    ] },
    portrait: { templates: [
      { id: 'baby_100', name: '百天', description: '百天蛋糕 / 数字气球 / 抓周预演…', styles: [{ id: 'studio', name: '影楼柔光', anchor: 'soft studio light' }],
        options_schema: { scenes: { type: 'list', default: ['百天蛋糕', '数字气球'] }, count_per_scene: { type: 'int', default: 1 }, text: { type: 'string', default: '' }, aspect: { type: 'string', default: '3:4' } } },
      { id: 'wedding', name: '婚纱', description: '两人，教堂 / 海边 / 城市夜景…', styles: [{ id: 'film', name: '复古胶片', anchor: 'vintage film' }],
        options_schema: { scenes: { type: 'list', default: ['教堂', '海边'] }, count_per_scene: { type: 'int', default: 1 }, text: { type: 'string', default: '' } } },
    ] },
  }
  detail = sampleDetail()
  taskResult = { status: 'succeeded', result: { done: ['I02', 'I03'], skipped: ['I01'], failed: [] } }
  postResult = { items: { I01: [{ file: 'I01-240.png', size: '240×240', bytes: 20480 }] } }
  exportResult = { file: 'ST-20260909-1-20260909.zip', url: '/api/sticker/ST-20260909-1/artifact/export/ST-20260909-1-20260909.zip', bytes: 123456, sha256: 'abcdef0123456789' }
  store.sets = { sticker: [], portrait: [] }; store.setsLoaded = { sticker: false, portrait: false }
  clearSet(); store.gen = null
  store.modules = [
    { id: 'drama', name: '短剧', english: 'Short drama', route: '/drama', status: 'ready', available: true, project_count: 1 },
    { id: 'picturebook', name: '绘本 / 有声书', english: 'Picture book', route: '/picturebook', status: 'ready', available: true, project_count: 2 },
    { id: 'sticker', name: '表情与头像', english: 'Stickers & avatars', icon: '🙂', route: '/sticker', status: 'ready', available: true, project_count: 2 },
    { id: 'portrait', name: '纪念写真', english: 'Milestone portraits', icon: '📷', route: '/portrait', status: 'ready', available: true, project_count: 1 },
  ]
  store.modulesLoaded = true
  store.episodes = []; store.episodesLoaded = true; store.books = []; store.booksLoaded = true
  store.health = { roles: { image: { configured: true } } }
})

describe('imageset · 首页列表与新建', () => {
  it('sticker 首页：封面、标题、模板名、进度药丸；「复制示例」POST 示例并进清单页', async () => {
    const { w, r } = await mountAt(SetHome, '/sticker')
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/sticker')).toBe(true)
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/sticker/templates')).toBe(true)
    const rows = w.findAll('[data-test="set-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe('/sticker/s/ST-20260909-1/items')
    expect(rows[0].find('img').attributes('src')).toContain('/artifact/raw/I01-2.png')
    expect(rows[0].text()).toContain('小满表情包')
    expect(rows[0].find('[data-test="set-template"]').text()).toBe('表情包')   // 模板名来自 /templates
    expect(rows[0].text()).toContain('出图 1/3')
    expect(rows[0].text()).toContain('挑选 1')
    expect(rows[1].find('[data-test="set-template"]').text()).toBe('贴图')     // 列表行自带 template_name
    expect(rows[1].text()).toContain('导出 ✓')
    await w.find('[data-test="set-example"]').trigger('click'); await flushPromises()
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/sticker/examples/create')
    expect(created[2]).toEqual({ name: 'xiaoman_emoji' })
    expect(calls.filter(c => c[0] === 'get' && c[1] === '/api/sticker').length).toBe(2)
    expect(r.currentRoute.value.path).toBe('/sticker/s/ST-20260909-9/items')
  })

  it('portrait 首页：同一页面按 meta.module 切模块；示例名与路由前缀都是 portrait 的', async () => {
    const { w, r } = await mountAt(SetHome, '/portrait')
    expect(w.find('[data-test="set-home"]').attributes('data-module')).toBe('portrait')
    expect(w.text()).toContain('纪念写真')
    const rows = w.findAll('[data-test="set-row"]')
    expect(rows.length).toBe(1)
    expect(rows[0].attributes('href')).toBe('/portrait/s/PT-20260909-1/items')
    expect(rows[0].find('[data-test="set-template"]').text()).toBe('百天')
    expect(rows[0].text()).toContain('出图 0/6')
    await w.find('[data-test="set-new"]').trigger('click'); await flushPromises()
    expect(r.currentRoute.value.path).toBe('/portrait/new')
    await r.push('/portrait')
    await w.find('[data-test="set-example"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === '/api/portrait/examples/create')[2]).toEqual({ name: 'baby_100_demo' })
    expect(r.currentRoute.value.path).toBe('/portrait/s/PT-20260909-9/items')
  })

  it('sticker 新建：模板说明 + 风格预设换锚 + 角色主体 + 定妆图 + 选项 chips / 布尔 → POST 建、逐张传参考图、展开清单、进项目', async () => {
    const { w, r } = await mountAt(SetNew, '/sticker/new')
    expect(w.findAll('[data-test="set-template"] option').map(o => o.attributes('value'))).toEqual(['emoji_pack', 'avatar_set'])
    expect(w.find('[data-test="set-template-desc"]').text()).toContain('微信表情包')
    expect(w.find('[data-test="set-anchor"]').element.value).toBe('flat cute vector sticker')
    await w.find('[data-test="set-style"]').setValue('line_doodle')
    expect(w.find('[data-test="set-anchor"]').element.value).toBe('line doodle')
    await w.find('[data-test="set-anchor"]').setValue('my own doodle anchor')
    // 主体 = 角色：名字 + 身份块；没有 who 标签
    expect(w.find('[data-test="subject-name"]').exists()).toBe(true)
    expect(w.find('[data-test="subject-block"]').exists()).toBe(true)
    await w.find('[data-test="subject-name"]').setValue('小满')
    await w.find('[data-test="subject-block"]').setValue('齐肩黑发，圆框眼镜')
    const file = new File(['x'], 'ref1.png', { type: 'image/png' })
    const input = w.find('[data-test="asset-file"]')
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change'); await flushPromises()
    expect(w.findAll('[data-test="asset-row"]').length).toBe(1)
    expect(w.find('[data-test="asset-who"]').exists()).toBe(false)
    // 选项：清单 chips（默认两项 → 删一项 → 加两项），布尔勾上
    const exp = w.find('[data-test="opt-expressions"]')
    expect(exp.findAll('[data-test="opt-chip"]').map(c => c.text())).toEqual(['开心 ✕', '大笑 ✕'])
    await exp.findAll('[data-test="opt-chip"]')[1].trigger('click')
    await exp.find('[data-test="opt-chip-input"]').setValue('哭, 委屈')
    await exp.find('[data-test="opt-chip-add"]').trigger('click')
    expect(exp.findAll('[data-test="opt-chip"]').length).toBe(3)
    await w.find('[data-test="opt-with_caption"] [data-test="opt-bool"]').setValue(true)
    // 没标题不建
    await w.find('[data-test="set-create"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    await w.find('[data-test="set-title"]').setValue('小满表情包')
    await w.find('[data-test="set-create"]').trigger('click'); await flushPromises()
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/sticker')
    expect(created[2]).toEqual({
      title: '小满表情包', template: 'emoji_pack', style: 'line_doodle', style_anchor: 'my own doodle anchor',
      subject: { kind: 'character', name: '小满', block: '齐肩黑发，圆框眼镜', assets: [], fidelity: 85 },
      options: { expressions: ['开心', '哭', '委屈'], with_caption: true },
    })
    const up = calls.find(c => c[0] === 'post' && c[1] === '/api/sticker/ST-20260909-2/assets')
    expect(up[2]).toEqual({ filename: 'ref1.png', data_base64: 'QUJD' })
    expect(calls.some(c => c[0] === 'post' && c[1] === '/api/sticker/ST-20260909-2/expand')).toBe(true)
    expect(calls.filter(c => c[0] === 'get' && c[1] === '/api/sticker').length).toBe(1)
    expect(r.currentRoute.value.path).toBe('/sticker/s/ST-20260909-2/items')
  })

  it('portrait 新建：真人参考照主体，婚纱模板每张照片标 who；整数 / 字符串选项；没有照片先确认', async () => {
    const { w, r } = await mountAt(SetNew, '/portrait/new')
    expect(w.find('[data-test="subject-block"]').exists()).toBe(false)
    expect(w.find('[data-test="opt-count_per_scene"] [data-test="opt-int"]').element.value).toBe('1')
    await w.find('[data-test="set-template"]').setValue('wedding')
    expect(w.find('[data-test="set-template-desc"]').text()).toContain('两人')
    const input = w.find('[data-test="asset-file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['a'], 'bride.jpg', { type: 'image/jpeg' }), new File(['b'], 'groom.jpg', { type: 'image/jpeg' })] })
    await input.trigger('change'); await flushPromises()
    const whos = w.findAll('[data-test="asset-who"]')
    expect(whos.length).toBe(2)
    await whos[1].setValue('groom')
    await w.find('[data-test="opt-count_per_scene"] [data-test="opt-int"]').setValue('2')
    await w.find('[data-test="opt-text"] [data-test="opt-string"]').setValue('百年好合 2026.10.01')
    await w.find('[data-test="subject-fidelity"]').setValue('90')
    await w.find('[data-test="set-title"]').setValue('我们的婚纱照')
    await w.find('[data-test="set-create"]').trigger('click'); await flushPromises()
    expect(confirmMock).not.toHaveBeenCalled()   // 有照片不用确认
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/portrait')
    expect(created[2]).toMatchObject({ title: '我们的婚纱照', template: 'wedding', style: 'film', subject: { kind: 'photo', fidelity: 90, assets: [] }, options: { scenes: ['教堂', '海边'], count_per_scene: 2, text: '百年好合 2026.10.01' } })
    const ups = calls.filter(c => c[0] === 'post' && c[1] === '/api/portrait/PT-20260909-2/assets')
    expect(ups.map(c => c[2])).toEqual([{ filename: 'bride.jpg', data_base64: 'QUJD', who: 'bride' }, { filename: 'groom.jpg', data_base64: 'QUJD', who: 'groom' }])
    expect(r.currentRoute.value.path).toBe('/portrait/s/PT-20260909-2/items')
    // 没有照片：先确认；拒绝就不建
    calls.length = 0
    const { w: w2 } = await mountAt(SetNew, '/portrait/new')
    confirmMock.mockResolvedValueOnce(false)
    await w2.find('[data-test="set-title"]').setValue('百天')
    await w2.find('[data-test="set-create"]').trigger('click'); await flushPromises()
    expect(confirmMock).toHaveBeenCalled()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
  })
})

describe('imageset · 项目页', () => {
  it('外壳：进项目拉 detail，导航三项，顶部进度，缩略条只给有 current 的', async () => {
    const { w } = await mountAt(Set, '/sticker/s/ST-20260909-1/items')
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/sticker/ST-20260909-1')).toBe(true)
    const nav = w.findAll('.ws-nav a.nav').map(a => a.attributes('href'))
    expect(nav).toEqual(['items', 'outputs', 'advanced'].map(s => `/sticker/s/ST-20260909-1/${s}`))
    expect(w.find('[data-test="set-progress"]').text()).toContain('出图 1/3')
    expect(w.find('[data-test="set-progress"]').text()).toContain('挑选 1')
    expect(w.findAll('.ws-nav .strip img').length).toBe(1)
    expect(w.find('.ws-nav .head').text()).toContain('表情包')
  })

  it('清单：缩略图 / 状态 / 历次；改 prompt → 保存 PUT 整份；重出 → 确认后 generate only+force 并轮询；采用 → pick；勾选 → pick picks', async () => {
    await loadSet('sticker', 'ST-20260909-1')
    const { w } = await mountAt(SetItems, '/sticker/s/ST-20260909-1/items')
    const rows = w.findAll('[data-test="item-row"]')
    expect(rows.length).toBe(3)
    expect(rows[0].find('[data-test="item-thumb"]').attributes('src')).toBe('/api/sticker/ST-20260909-1/artifact/raw/I01-2.png')
    expect(rows[1].find('[data-test="item-thumb"]').exists()).toBe(false)
    expect(rows[0].find('[data-test="item-status"]').text()).toBe('已出')
    expect(rows[2].find('[data-test="item-status"]').text()).toBe('失败')
    expect(rows[2].text()).toContain('审核拦截')
    // 历次：两次，第二次是当前（按钮禁用），第一次可「采用」
    const attempts = rows[0].findAll('[data-test="item-attempt"]')
    expect(attempts.length).toBe(2)
    expect(attempts[1].find('[data-test="item-adopt"]').attributes('disabled')).toBeDefined()
    expect(attempts[0].find('[data-test="item-adopt"]').attributes('disabled')).toBeUndefined()
    // 挑选勾：I01 勾着；没图的不能勾
    expect(rows[0].find('[data-test="item-pick"]').element.checked).toBe(true)
    expect(rows[1].find('[data-test="item-pick"]').attributes('disabled')).toBeDefined()
    // 改 prompt → 保存
    expect(w.find('[data-test="items-save"]').attributes('disabled')).toBeDefined()
    await rows[1].find('[data-test="item-prompt"]').setValue('laughing, eyes closed')
    expect(w.find('[data-test="items-save"]').attributes('disabled')).toBeUndefined()
    await w.find('[data-test="items-save"]').trigger('click'); await flushPromises()
    const saved = calls.find(c => c[0] === 'put' && c[1] === '/api/sticker/ST-20260909-1')
    expect(saved[2].items[1].prompt).toBe('laughing, eyes closed')
    expect(saved[2].items[0].current).toBe('I01-2.png')
    expect(saved[2].picks).toEqual(['I01'])
    expect(saved[2].title).toBe('小满表情包')
    expect(saved[2].output_root).toBeUndefined()
    // 重出这一张：确认框写清 1 次请求；only + force
    calls.length = 0
    await rows[0].find('[data-test="item-redo"]').trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('1 次生图请求')
    const redo = calls.find(c => c[0] === 'post' && c[1] === '/api/sticker/ST-20260909-1/generate')
    expect(redo[2]).toEqual({ only: ['I01'], force: true })
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-gen')).toBe(true)
    expect(calls.filter(c => c[0] === 'get' && c[1] === '/api/sticker/ST-20260909-1').length).toBe(1)   // 轮询完刷新
    expect(store.gen).toBeNull()
    // 采用第一次
    calls.length = 0
    await w.findAll('[data-test="item-row"]')[0].findAll('[data-test="item-adopt"]')[0].trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/pick'))[2]).toEqual({ item: 'I01', attempt: 'I01-1.png' })
    // 取消挑选
    calls.length = 0
    await w.findAll('[data-test="item-row"]')[0].find('[data-test="item-pick"]').setValue(false); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/pick'))[2]).toEqual({ picks: [] })
  })

  it('清单头部：「生成缺的」确认写 2 次请求、body 空；「全部重出」写 3 次、force；拒绝就不发；「展开清单」POST expand', async () => {
    await loadSet('sticker', 'ST-20260909-1')
    const { w } = await mountAt(SetItems, '/sticker/s/ST-20260909-1/items')
    expect(w.find('[data-test="items-generate"]').text()).toContain('(2)')
    await w.find('[data-test="items-generate"]').trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('2 次生图请求')
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/generate'))[2]).toEqual({})
    calls.length = 0
    confirmMock.mockResolvedValueOnce(false)
    await w.find('[data-test="items-regenerate"]').trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('3 次生图请求')
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    await w.find('[data-test="items-regenerate"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/generate'))[2]).toEqual({ force: true })
    calls.length = 0
    await w.find('[data-test="items-expand"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post' && c[1] === '/api/sticker/ST-20260909-1/expand')).toBe(true)
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/sticker/ST-20260909-1')).toBe(true)
  })

  it('出图失败：轮询到 failed 弹原因，进度条清掉', async () => {
    taskResult = { status: 'failed', error: '生图来源没配置' }
    await loadSet('sticker', 'ST-20260909-1')
    const { w } = await mountAt(SetItems, '/sticker/s/ST-20260909-1/items')
    await w.find('[data-test="items-generate"]').trigger('click'); await flushPromises()
    const { notice } = await import('../src/ui.js')
    expect(notice.mock.calls.some(c => String(c[0]).includes('生图来源没配置'))).toBe(true)
    expect(store.gen).toBeNull()
  })

  it('产物：后处理 POST 后按张列产物与尺寸；导出 POST 给下载链接；导出历史来自 detail', async () => {
    await loadSet('sticker', 'ST-20260909-1')
    const { w } = await mountAt(SetOutputs, '/sticker/s/ST-20260909-1/outputs')
    expect(w.findAll('[data-test="output-item"]').length).toBe(1)   // 挑选了 I01 → 只列 I01
    expect(w.findAll('[data-test="output-file"]').length).toBe(0)
    expect(w.find('[data-test="export-history"]').text()).toContain('还没有导出过')
    // 后处理：同步返回，detail 刷新后带 outputs
    detail = sampleDetail(sampleSpec(), { outputs: true, exports: [{ file: 'ST-20260909-1-20260909.zip', bytes: 123456, created_at: '2026-09-09 12:00' }] })
    await w.find('[data-test="outputs-post"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post' && c[1] === '/api/sticker/ST-20260909-1/post')).toBe(true)
    const files = w.findAll('[data-test="output-file"]')
    expect(files.length).toBe(2)
    expect(files[0].find('img').attributes('src')).toBe('/api/sticker/ST-20260909-1/artifact/out/I01-240.png')
    expect(files[0].text()).toContain('240×240')
    expect(files[0].text()).toContain('20 KB')
    // 导出
    await w.find('[data-test="outputs-export"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post' && c[1] === '/api/sticker/ST-20260909-1/export')).toBe(true)
    expect(w.find('[data-test="export-download"]').attributes('href')).toBe('/api/sticker/ST-20260909-1/artifact/export/ST-20260909-1-20260909.zip')
    expect(w.find('[data-test="export-card"]').text()).toContain('121 KB')
    const hist = w.find('[data-test="export-history"]')
    expect(hist.text()).toContain('ST-20260909-1-20260909.zip')
    expect(hist.find('a').attributes('href')).toBe('/api/sticker/ST-20260909-1/artifact/export/ST-20260909-1-20260909.zip')
  })

  it('后处理是后台任务时：轮询后从 task.result 取产物', async () => {
    postResult = { task_id: 't-post' }
    taskResult = { status: 'succeeded', result: { items: { I01: [{ file: 'I01-240.png', size: '240×240' }] } } }
    await loadSet('sticker', 'ST-20260909-1')
    const { w } = await mountAt(SetOutputs, '/sticker/s/ST-20260909-1/outputs')
    await w.find('[data-test="outputs-post"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-post')).toBe(true)
    expect(w.findAll('[data-test="output-file"]').length).toBe(1)
    expect(w.find('[data-test="output-file"] img').attributes('src')).toBe('/api/sticker/ST-20260909-1/artifact/out/I01-240.png')
  })

  it('高级：JSON 直改 PUT（非法 JSON 就地报错）；参考图列表；删除 = 归档后回模块首页', async () => {
    await loadSet('sticker', 'ST-20260909-1')
    const { w, r } = await mountAt(SetAdvanced, '/sticker/s/ST-20260909-1/advanced')
    const ta = w.find('[data-test="json-text"]')
    expect(JSON.parse(ta.element.value).title).toBe('小满表情包')
    expect(w.findAll('[data-test="set-asset"]').length).toBe(1)
    expect(w.find('[data-test="set-asset"] img').attributes('src')).toBe('/api/sticker/ST-20260909-1/artifact/asset/ref1.png')
    await ta.setValue('{ not json')
    await w.find('[data-test="json-save"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="json-error"]').text()).toContain('不是合法的 JSON')
    expect(calls.some(c => c[0] === 'put')).toBe(false)
    await ta.setValue(JSON.stringify({ ...sampleSpec(), title: '小满表情包 2' }))
    await w.find('[data-test="json-save"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'put')[2].title).toBe('小满表情包 2')
    await w.find('[data-test="set-archive"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'del' && c[1] === '/api/sticker/ST-20260909-1')).toBe(true)
    expect(store.set).toBeNull()
    expect(r.currentRoute.value.path).toBe('/sticker')
  })
})

describe('imageset · 侧栏、首页卡与路由', () => {
  it('模块可用时侧栏各多一块：新建 / 搜索 / 列表指向 /{m}/s，手风琴一次只开一个；删除 ✕ 归档；不可用时没有', async () => {
    store.sets = lists; store.setsLoaded = { sticker: true, portrait: true }
    const { w, r } = await mountAt(Sidebar, '/')
    const st = w.find('[data-test="sidebar-module-sticker"]')
    const pt = w.find('[data-test="sidebar-module-portrait"]')
    expect(st.exists()).toBe(true); expect(pt.exists()).toBe(true)
    expect(st.find('[data-test="module-header"]').text()).toContain('表情与头像')
    expect(st.find('[data-test="module-header"]').text()).toContain('2')
    expect(pt.find('[data-test="module-header"]').text()).toContain('纪念写真')
    expect(st.findAll('a')[0].attributes('href')).toBe('/sticker')   // 头行名字 → 模块首页；新建不在侧栏
    expect(pt.findAll('a')[0].attributes('href')).toBe('/portrait')
    expect(st.html()).not.toContain('/sticker/new')
    expect(st.classes()).not.toContain('open')
    await st.find('[data-test="module-header"]').trigger('click')
    expect(st.classes()).toContain('open')
    expect(w.find('[data-test="sidebar-module-drama"]').classes()).not.toContain('open')
    const rows = w.findAll('[data-test="set-list-sticker"] .proj')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe('/sticker/s/ST-20260909-1/items')
    expect(rows[0].text()).toContain('出图 1/3')
    expect(rows[1].text()).toContain('已导出')
    // 列表短的时候没有搜索框（SEARCH_MIN=8）——搜索行为在 components.test.js 里单测
    expect(st.find('[data-test="set-search-sticker"]').exists()).toBe(false)
    await pt.find('[data-test="module-header"]').trigger('click')
    expect(pt.classes()).toContain('open'); expect(st.classes()).not.toContain('open')
    expect(w.findAll('[data-test="set-list-portrait"] .proj')[0].attributes('href')).toBe('/portrait/s/PT-20260909-1/items')
    // 路由进 /portrait/… 时自动展开纪念写真
    await r.push('/sticker/s/ST-20260909-1/items'); await flushPromises()
    expect(w.find('[data-test="sidebar-module-sticker"]').classes()).toContain('open')
    // 删除 = 归档
    await w.findAll('[data-test="set-list-sticker"] .del')[0].trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'del' && c[1] === '/api/sticker/ST-20260909-1')).toBe(true)
    expect(calls.filter(c => c[0] === 'get' && c[1] === '/api/sticker').length).toBe(1)
    expect(r.currentRoute.value.path).toBe('/sticker')
    store.modules = store.modules.filter(m => m.id !== 'sticker' && m.id !== 'portrait')
    await w.vm.$nextTick()
    expect(w.find('[data-test="sidebar-module-sticker"]').exists()).toBe(false)
    expect(w.find('[data-test="sidebar-module-portrait"]').exists()).toBe(false)
  })

  it('工作台首页的模块卡从注册表读，链接指向模块路由', async () => {
    const { w } = await mountAt(Home, '/')
    expect(w.find('[data-test="module-sticker"]').attributes('href')).toBe('/sticker')
    expect(w.find('[data-test="module-portrait"]').attributes('href')).toBe('/portrait')
  })

  it('路由：/sticker 与 /portrait 下的路径直接命中并带 meta.module；/s/:id 落到 items', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/'); await r.isReady()
    for (const m of ['sticker', 'portrait']) {
      for (const [from, to] of [
        [`/${m}`, `/${m}`], [`/${m}/new`, `/${m}/new`],
        [`/${m}/s/X-1`, `/${m}/s/X-1/items`], [`/${m}/s/X-1/items`, `/${m}/s/X-1/items`],
        [`/${m}/s/X-1/outputs`, `/${m}/s/X-1/outputs`], [`/${m}/s/X-1/advanced`, `/${m}/s/X-1/advanced`],
      ]) {
        await r.push(from)
        expect(r.currentRoute.value.path, from).toBe(to)
        expect(r.currentRoute.value.matched.length, from).toBeGreaterThan(0)
        expect(r.currentRoute.value.meta.module, from).toBe(m)
      }
      expect(r.currentRoute.value.params.id).toBe('X-1')
    }
    await r.push('/p/EP1')
    expect(r.currentRoute.value.path).toBe('/drama/p/EP1/overview')
  })

  it('helpers：进度形状兜底、options_schema 归一化、产物地址', async () => {
    expect(progressOf({ progress: { done: 2, total: 5, picked: 1, exported: { exists: true } } })).toEqual({ done: 2, total: 5, picked: 1, exported: true })
    expect(progressOf({ items: [{ id: 'a', current: 'a-1.png' }, { id: 'b', current: null }], picks: [] })).toEqual({ done: 1, total: 2, picked: 0, exported: false })
    expect(progressOf(null)).toEqual({ done: 0, total: 0, picked: 0, exported: false })
    const fields = normalizeSchema({ expressions: ['开心'], with_caption: false, count: 2, shape: { type: 'choice', choices: ['圆形', '方形'] }, text: '' })
    expect(fields.map(f => [f.key, f.type])).toEqual([['expressions', 'list'], ['with_caption', 'bool'], ['count', 'int'], ['shape', 'choice'], ['text', 'string']])
    expect(defaultOptions(fields)).toEqual({ expressions: ['开心'], with_caption: false, count: 2, shape: '圆形', text: '' })
    expect(normalizeSchema([{ key: 'scenes', type: 'array', default: ['a'] }, { key: 'aspect', type: 'str', default: '3:4' }]).map(f => f.type)).toEqual(['list', 'string'])
    // 老形状：详情直接是项目文件（没有 items 行 / url），地址按文件名拼
    const spec = sampleSpec()
    expect(itemImageUrl('sticker', spec.id, spec.items[0], 0, null)).toBe('/api/sticker/ST-20260909-1/artifact/raw/I01-2.png')
    expect(itemAttempts('sticker', spec.id, spec.items[0], 7, null).map(a => a.url)).toEqual(['/api/sticker/ST-20260909-1/artifact/raw/I01-1.png?t=7', '/api/sticker/ST-20260909-1/artifact/raw/I01-2.png?t=7'])
    expect((await loadTemplates('portrait')).map(x => x.id)).toEqual(['baby_100', 'wedding'])
  })

  it('英文界面：options_schema 自带的 label_en 优先，没有的字段仍用 label', async () => {
    const schema = [
      { key: 'expressions', label: '表情清单', label_en: 'Expressions', type: 'list', default: ['开心'] },
      { key: 'with_caption', label: '叠中文短语', type: 'bool', default: false },
    ]
    expect(normalizeSchema(schema).map(f => f.label)).toEqual(['表情清单', '叠中文短语'])
    setLang('en')
    try {
      expect(normalizeSchema(schema).map(f => f.label)).toEqual(['Expressions', '叠中文短语'])
    } finally {
      setLang('zh')
    }
  })

  it('英文界面：模板名 / 模板说明 / 枚举选项名用后端自带的 english / description_en（没有英文的仍显示中文）', async () => {
    templates.sticker.templates[0].description_en = 'One 1:1 image per expression, white background to transparent'
    templates.sticker.templates[1].options_schema.crop.choices = [{ id: 'headshot', name: '头肩', english: 'Head and shoulders' }, { id: 'half', name: '半身' }]
    await loadTemplates('sticker', { force: true })   // 模板按模块缓存，前面的用例已经拉过一份
    setLang('en')
    try {
      const { w } = await mountAt(SetHome, '/sticker')
      const rows = w.findAll('[data-test="set-row"]')
      expect(rows[0].find('[data-test="set-template"]').text()).toBe('Emoji pack')
      expect(rows[1].find('[data-test="set-template"]').text()).toBe('贴图')   // 模板列表里没有这一个：退回行自带的 template_name
      const { w: w2 } = await mountAt(SetNew, '/sticker/new')
      expect(w2.findAll('[data-test="set-template"] option').map(o => o.text())).toEqual(['Emoji pack', '头像'])
      expect(w2.find('[data-test="set-template-desc"]').text()).toBe('One 1:1 image per expression, white background to transparent')
      await w2.find('[data-test="set-template"]').setValue('avatar_set'); await flushPromises()
      expect(w2.findAll('[data-test="opt-choice"] option').map(o => o.text())).toEqual(['Head and shoulders', '半身'])
    } finally {
      setLang('zh')
    }
    expect(templateLabel(templates.sticker.templates[0])).toBe('表情包')
    expect(templateDescription(templates.sticker.templates[0])).toContain('微信表情包')
    expect(choiceLabel({ id: 'headshot', name: '头肩', english: 'Head and shoulders' })).toBe('头肩')
    expect(choiceLabel('半身')).toBe('半身')
  })
})
