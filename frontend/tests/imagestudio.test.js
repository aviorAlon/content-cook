// 图片工作台 image（docs/imagestudio-spec.md §4）：它是图片集引擎的第三个模块，页面全部复用 views/imageset/*，
// 所以这里只测「多出来的那些」——路由与 meta.module、首页 / 新建 / 清单在 /image 下走通、多模型对比的模型多选、
// 主体可以整个留空、清单里每行显示这一张用的模型、侧栏多一块。api 全部 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let lists, detail, templates, catalog
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/system/model-catalog') return catalog
    const mm = /^\/api\/(image)(?:\/(.*))?$/.exec(url)
    if (mm && !mm[2]) return lists
    if (mm && mm[2] === 'templates') return templates
    if (mm) return detail
    return {}
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    if (url === '/api/image') return { id: 'IM-20260910-2', spec: { ...body } }
    if (url.endsWith('/examples/create')) return { id: 'IM-20260910-9', example: body.name }
    if (url.endsWith('/generate')) return { task_id: 't-gen', cost_note: '最多 2 次生图请求' }
    return { ok: true }
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return { id: 'IM-20260910-1', spec: body } }),
  del: vi.fn(async (url) => { calls.push(['del', url]); return { archived_to: '.webui_deleted/x' } }),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(async (id) => { calls.push(['poll', id]); return { status: 'succeeded', result: {} } }),
  pollJob: vi.fn(), sleep: vi.fn(async () => {}), fileToBase64: vi.fn(async () => 'QUJD'),
}))
const confirmMock = vi.fn(async () => true)
vi.mock('../src/ui.js', () => ({
  prompt: vi.fn(), confirm: (...a) => confirmMock(...a), notice: vi.fn(), toast: vi.fn(), custom: vi.fn(), toasts: [],
  T: { role: { text: '剧本文本', image: '生图', voice: '语音', video: '视频' } },
}))

import { store } from '../src/store.js'
import { routes } from '../src/router.js'
import { loadSet, clearSet, moduleInfo, loadTemplates, IMAGESET_IDS } from '../src/imageset.js'
import SetHome from '../src/views/imageset/SetHome.vue'
import SetNew from '../src/views/imageset/SetNew.vue'
import SetItems from '../src/views/imageset/SetItems.vue'
import Sidebar from '../src/components/Sidebar.vue'
import Home from '../src/views/Home.vue'

const stub = { template: '<div/>' }
function stubRouter() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: stub }, { path: '/settings', component: stub }, { path: '/voices', component: stub },
    { path: '/drama', component: stub }, { path: '/drama/new', component: stub }, { path: '/drama/libraries', component: stub },
    { path: '/picturebook', component: stub }, { path: '/picturebook/new', component: stub },
    ...['sticker', 'portrait', 'image'].flatMap(m => [
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
    id: 'IM-20260910-1', module: 'image', title: '猫在窗台', template: 'model_compare',
    subject: { kind: 'character', name: '', block: '', assets: [], fidelity: 85 },
    style: 'cinematic_key_art', style_anchor: '',
    options: { brief: '一只三花猫坐在窗台', models: ['@image', 'gpt-image-2'], aspect: '1:1', negative: '' },
    image_params: { model: '@image', image_size: '2K' },
    items: [
      { id: 'I01', label: '@image', prompt: 'cat', aspect_ratio: '1:1', meta: { model: '@image' }, status: 'done', current: 'I01-1.png', attempts: [{ file: 'I01-1.png', model: '@image' }] },
      { id: 'I02', label: 'gpt-image-2', prompt: 'cat', aspect_ratio: '1:1', meta: { model: 'gpt-image-2' }, status: 'todo', current: null, attempts: [] },
    ],
    picks: [],
  }
}
function sampleDetail(spec = sampleSpec()) {
  return {
    id: spec.id, module: 'image', title: spec.title, template: spec.template, template_name: '多模型对比', spec,
    progress: { done: 1, total: 2, picked: 0, exported: false },
    items: spec.items.map(it => ({
      id: it.id, status: it.status, current: it.current, meta: it.meta,
      current_url: it.current ? `/api/image/${spec.id}/artifact/raw/${it.current}` : null,
      attempts: it.attempts.map(a => ({ ...a, url: `/api/image/${spec.id}/artifact/raw/${a.file}` })),
      error: null,
    })),
    exports: [],
  }
}

beforeEach(() => {
  calls.length = 0
  confirmMock.mockReset(); confirmMock.mockResolvedValue(true)
  lists = [
    { id: 'IM-20260910-1', title: '猫在窗台', template: 'model_compare', template_name: '多模型对比', style: 'cinematic_key_art', progress: { done: 1, total: 2, picked: 0, exported: false }, cover_url: '/api/image/IM-20260910-1/artifact/raw/I01-1.png' },
  ]
  templates = { templates: [
    { id: 'freeform', name: '自由出图', description: '一句话画面出 N 张候选', styles: [{ id: 'photo_real', name: '真实照片', anchor: '真实摄影质感' }],
      options_schema: [
        { key: 'brief', type: 'string', default: '', label: '画面一句话' },
        { key: 'variants', type: 'int', default: 4, min: 1, max: 8, label: '候选张数' },
        { key: 'aspect', type: 'string', default: '1:1', label: '画幅', choices: [{ id: '1:1', name: '1:1' }, { id: '3:4', name: '3:4' }] },
      ] },
    { id: 'model_compare', name: '多模型对比', description: '同一句提示词换模型各出一张', styles: [{ id: 'photo_real', name: '真实照片', anchor: '真实摄影质感' }],
      options_schema: [
        { key: 'brief', type: 'string', default: '', label: '画面一句话' },
        { key: 'models', type: 'list', default: ['@image', 'gpt-image-2', 'nano-banana'], label: '参与对比的模型' },
      ] },
    { id: 'text_card', name: '图卡', description: '出底图后本机叠标题', styles: [{ id: 'photo_real', name: '真实照片', anchor: '真实摄影质感' }],
      options_schema: [{ key: 'title', type: 'string', default: '', label: '标题（本机叠）' }] },
  ],
  // 后端排好序的候选（docs/imagestudio-spec.md §6.5）：已配置 → 模型目录 → 本机
  model_candidates: [
    { id: '@image', label: '@image → my-configured-image-model', source: 'configured' },
    { id: 'my-configured-image-model', label: 'my-configured-image-model', source: 'configured' },
    { id: 'seedream-4', label: 'Seedream 4', source: 'catalog' },
    { id: 'gpt-image-2', label: 'GPT Image 2', source: 'catalog' },
    { id: 'sd_xl_base_1.0.safetensors', label: 'sd_xl_base_1.0.safetensors · SD WebUI', source: 'local' },
  ] }
  catalog = { roles: { image: [{ id: 'seedream-4', name: 'Seedream 4' }, { id: 'gpt-image-2', name: 'GPT Image 2' }] } }
  detail = sampleDetail()
  store.sets = { sticker: [], portrait: [], image: [] }
  store.setsLoaded = { sticker: false, portrait: false, image: false }
  clearSet(); store.gen = null
  store.modules = [
    { id: 'drama', name: '短剧', icon: '🎬', route: '/drama', status: 'ready', available: true, project_count: 0 },
    { id: 'image', name: '图片工作台', english: 'Image studio', icon: '🖼', route: '/image', status: 'ready', available: true, project_count: 1 },
  ]
  store.modulesLoaded = true
  store.episodes = []; store.episodesLoaded = true; store.books = []; store.booksLoaded = true
  store.health = { roles: { image: { configured: true, model: 'my-configured-image-model' } } }
})

describe('图片工作台 · 模块接线', () => {
  it('模块表：图标 / 前缀 / 示例名 / 主体可留空；引擎的模块清单里有它', () => {
    const info = moduleInfo('image')
    expect([info.id, info.icon, info.prefix, info.example]).toEqual(['image', '🖼', 'IM', 'lantern_night'])
    expect(info.optionalSubject).toBe(true)
    expect(IMAGESET_IDS).toContain('image')
  })

  it('路由：/image、/image/new、/image/s/:id/{items,outputs,advanced} 都带 meta.module=image', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/'); await r.isReady()
    for (const [from, to] of [
      ['/image', '/image'], ['/image/new', '/image/new'],
      ['/image/s/IM-1', '/image/s/IM-1/items'], ['/image/s/IM-1/items', '/image/s/IM-1/items'],
      ['/image/s/IM-1/outputs', '/image/s/IM-1/outputs'], ['/image/s/IM-1/advanced', '/image/s/IM-1/advanced'],
    ]) {
      await r.push(from)
      expect(r.currentRoute.value.path, from).toBe(to)
      expect(r.currentRoute.value.meta.module, from).toBe('image')
      expect(r.currentRoute.value.matched.length, from).toBeGreaterThan(0)
    }
  })

  it('首页：列表来自 /api/image，行指向 /image/s/…；「复制示例」用 lantern_night', async () => {
    const { w, r } = await mountAt(SetHome, '/image')
    expect(w.find('[data-test="set-home"]').attributes('data-module')).toBe('image')
    expect(w.text()).toContain('图片工作台')
    const rows = w.findAll('[data-test="set-row"]')
    expect(rows.length).toBe(1)
    expect(rows[0].attributes('href')).toBe('/image/s/IM-20260910-1/items')
    expect(rows[0].find('[data-test="set-template"]').text()).toBe('多模型对比')
    expect(rows[0].text()).toContain('出图 1/2')
    await w.find('[data-test="set-example"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === '/api/image/examples/create')[2]).toEqual({ name: 'lantern_night' })
    expect(r.currentRoute.value.path).toBe('/image/s/IM-20260910-9/items')
  })

  it('侧栏多一块：新建 / 搜索 / 列表都在 /image 下', async () => {
    store.sets.image = lists; store.setsLoaded.image = true
    const { w } = await mountAt(Sidebar, '/')
    const block = w.find('[data-test="sidebar-module-image"]')
    expect(block.exists()).toBe(true)
    expect(block.find('[data-test="module-header"]').text()).toContain('图片工作台')
    expect(block.findAll('a')[0].attributes('href')).toBe('/image')   // 头行名字 → 模块首页；新建在模块首页上，侧栏不重复
    expect(block.html()).not.toContain('/image/new')
    await block.find('[data-test="module-header"]').trigger('click')
    const rows = w.findAll('[data-test="set-list-image"] .proj')
    expect(rows[0].attributes('href')).toBe('/image/s/IM-20260910-1/items')
    // 列表短的时候没有搜索框（SEARCH_MIN=8）——搜索行为在 components.test.js 里单测
    expect(block.find('[data-test="set-search-image"]').exists()).toBe(false)
  })

  it('工作台首页的模块卡指向 /image', async () => {
    const { w } = await mountAt(Home, '/')
    expect(w.find('[data-test="module-image"]').attributes('href')).toBe('/image')
  })
})

describe('图片工作台 · 新建', () => {
  it('自由出图：主体可以整个留空，不弹确认；选项按 schema 渲染（一句话 / 张数 / 画幅）', async () => {
    const { w, r } = await mountAt(SetNew, '/image/new')
    expect(w.findAll('[data-test="set-template"] option').map(o => o.attributes('value'))).toEqual(['freeform', 'model_compare', 'text_card'])
    expect(w.find('[data-test="subject-optional"]').exists()).toBe(true)
    await w.find('[data-test="opt-brief"] [data-test="opt-string"]').setValue('夜里的石桥下有人放河灯')
    await w.find('[data-test="opt-variants"] [data-test="opt-int"]').setValue('6')
    await w.find('[data-test="opt-aspect"] [data-test="opt-choice"]').setValue('3:4')
    await w.find('[data-test="set-title"]').setValue('河灯夜')
    await w.find('[data-test="set-create"]').trigger('click'); await flushPromises()
    expect(confirmMock).not.toHaveBeenCalled()          // 主体空着也不催
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/image')
    expect(created[2]).toEqual({
      title: '河灯夜', template: 'freeform', style: 'photo_real', style_anchor: '真实摄影质感',
      subject: { kind: 'character', name: '', block: '', assets: [], fidelity: 85 },
      options: { brief: '夜里的石桥下有人放河灯', variants: 6, aspect: '3:4' },
    })
    expect(calls.some(c => c[0] === 'post' && c[1] === '/api/image/IM-20260910-2/expand')).toBe(true)
    expect(r.currentRoute.value.path).toBe('/image/s/IM-20260910-2/items')
  })

  it('多模型对比：models 多选按 model_candidates 分组（已配置 / 模型目录 / 本机）；还能手写加一个', async () => {
    const { w } = await mountAt(SetNew, '/image/new')
    await w.find('[data-test="set-template"]').setValue('model_compare')
    await flushPromises()
    const field = w.find('[data-test="opt-models"]')
    const sel = field.find('[data-test="opt-model-select"]')
    expect(sel.exists()).toBe(true)
    expect(sel.element.multiple).toBe(true)
    // 候选跟着 /templates 一起来（按模块缓存过一次），不再单独拉一次模型目录
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/system/model-catalog')).toBe(false)
    const groups = sel.findAll('[data-test="opt-model-group"]')
    expect(groups.map(g => g.attributes('data-source'))).toEqual(['configured', 'catalog', 'local', 'custom'])
    expect(groups.map(g => g.attributes('label'))).toEqual(['已配置', '模型目录', '本机', '手写'])
    expect(groups[0].findAll('option').map(o => o.attributes('value'))).toEqual(['@image', 'my-configured-image-model'])
    expect(groups[0].findAll('option')[0].text()).toBe('@image → my-configured-image-model')
    expect(groups[2].findAll('option').map(o => o.attributes('value'))).toEqual(['sd_xl_base_1.0.safetensors'])
    // 模板默认里那个候选清单里没有的 id（nano-banana）落进「手写」组，仍然选得上
    expect(groups[3].findAll('option').map(o => o.attributes('value'))).toEqual(['nano-banana'])
    const values = sel.findAll('option').map(o => o.attributes('value'))
    expect(new Set(values).size).toBe(values.length)                               // 不重复
    // 目录里没有的 id 手写加进来
    await field.find('[data-test="opt-chip-input"]').setValue('flux-pro')
    await field.find('[data-test="opt-chip-add"]').trigger('click')
    expect(sel.findAll('option').map(o => o.attributes('value'))).toContain('flux-pro')
    // 多选：只留两个
    const opts = sel.findAll('option')
    for (const o of opts) o.element.selected = false
    opts.find(o => o.attributes('value') === 'seedream-4').element.selected = true
    opts.find(o => o.attributes('value') === 'gpt-image-2').element.selected = true
    await sel.trigger('change')
    await w.find('[data-test="set-title"]').setValue('猫在窗台')
    await w.find('[data-test="set-create"]').trigger('click'); await flushPromises()
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/image')
    // 新建请求的形状一个字没变：分组只是显示方式，发出去的还是一串模型 id
    expect(created[2]).toEqual({
      title: '猫在窗台', template: 'model_compare', style: 'photo_real', style_anchor: '真实摄影质感',
      subject: { kind: 'character', name: '', block: '', assets: [], fidelity: 85 },
      options: { brief: '', models: ['seedream-4', 'gpt-image-2'] },
    })
  })

  it('后端没给候选（老服务端 / 什么都没配）：多选退回模板默认的那几个，照样能建', async () => {
    templates = { templates: templates.templates }          // 去掉 model_candidates
    await loadTemplates('image', { force: true })           // 重新拉一次，把缓存里的候选也清掉
    const { w } = await mountAt(SetNew, '/image/new')
    await w.find('[data-test="set-template"]').setValue('model_compare')
    await flushPromises()
    const sel = w.find('[data-test="opt-models"] [data-test="opt-model-select"]')
    expect(sel.findAll('[data-test="opt-model-group"]').map(g => g.attributes('data-source'))).toEqual(['custom'])
    expect(sel.findAll('option').map(o => o.attributes('value'))).toEqual(['@image', 'gpt-image-2', 'nano-banana'])
    await w.find('[data-test="set-title"]').setValue('猫在窗台')
    await w.find('[data-test="set-create"]').trigger('click'); await flushPromises()
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/image')
    expect(created[2].options.models).toEqual(['@image', 'gpt-image-2', 'nano-banana'])
  })
})

describe('图片工作台 · 清单', () => {
  it('每行显示这一张用的模型；没有 meta.model 的模板不显示', async () => {
    await loadSet('image', 'IM-20260910-1')
    const { w } = await mountAt(SetItems, '/image/s/IM-20260910-1/items')
    const rows = w.findAll('[data-test="item-row"]')
    expect(rows.length).toBe(2)
    expect(rows.map(r => r.find('[data-test="item-model"]').text())).toEqual(['@image', 'gpt-image-2'])
    expect(rows[0].find('[data-test="item-thumb"]').attributes('src')).toBe('/api/image/IM-20260910-1/artifact/raw/I01-1.png')
    // 重出这一张：只发这一张，force
    await rows[1].find('[data-test="item-redo"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === '/api/image/IM-20260910-1/generate')[2]).toEqual({ only: ['I02'], force: true })
  })

  it('别的模板（没有 meta.model）不显示模型药丸', async () => {
    const spec = sampleSpec()
    spec.template = 'freeform'
    spec.items = spec.items.map(it => ({ ...it, meta: { variant: 1 } }))
    detail = sampleDetail(spec)
    await loadSet('image', 'IM-20260910-1')
    const { w } = await mountAt(SetItems, '/image/s/IM-20260910-1/items')
    expect(w.findAll('[data-test="item-model"]').length).toBe(0)
  })
})
