// 内容创作工作台外壳（DESIGN §38）：工作台首页 = 启动台（模块卡 / 最近 / 在跑的任务 / 第一次用），
// 短剧首页在 /drama（2026-09-10 拆分：首页不再嵌短剧区）；侧栏分组、路由命名空间与旧路径重定向。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

const get = vi.fn()
vi.mock('../src/api.js', async (orig) => ({ ...(await orig()), get: (...a) => get(...a), del: vi.fn().mockResolvedValue({ archived_to: '.webui_deleted/storyvideo/A-1__x' }) }))
vi.mock('../src/ui.js', async (orig) => ({ ...(await orig()), confirm: vi.fn().mockResolvedValue(true) }))
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import Home from '../src/views/Home.vue'
import DramaHome from '../src/views/DramaHome.vue'
import Sidebar from '../src/components/Sidebar.vue'
import { routes, dramaProject } from '../src/router.js'
import { store, productName, loadModules } from '../src/store.js'
import { setLang } from '../src/i18n.js'

const MODULES = [
  { id: 'drama', name: '短剧', english: 'Short drama', description: '剧本 → 分镜 → 首帧', description_en: 'Script → storyboard → first frames', icon: '🎬', route: '/drama', status: 'ready', available: true, project_count: 2 },
  { id: 'picturebook', name: '绘本 / 有声书', english: 'Picture book', description: '插画 + 朗读 + 双语字幕的有声绘本', description_en: 'Narrated picture books', icon: '📖', route: '/picturebook', status: 'ready', available: true, project_count: 1 },
  { id: 'image', name: '图片工作台', english: 'Image studio', description: '单张 / 成组出图', description_en: 'Single or batch images', icon: '🖼', route: null, status: 'planned', available: false, project_count: 0 },
]

const NOW = Date.now()
const RECENT = [
  { module: 'picturebook', module_name: '绘本 / 有声书', module_name_en: 'Picture book', icon: '📖', id: 'PB-1', title: '小狐狸的三只袜子', url: '/picturebook/b/PB-1/overview', mtime: (NOW - 180_000) / 1000, path: 'content/picturebook/PB-1.json' },
  { module: 'drama', module_name: '短剧', module_name_en: 'Short drama', icon: '🎬', id: 'A-1', title: '加班夜', url: '/drama/p/A-1/overview', mtime: (NOW - 30 * 3600_000) / 1000, path: 'content/story_video/A-1.json' },
]

// /api/recent 与 /api/tasks 的假响应；每条用例按需要改
let recent = []
let tasks = []
function stubApi() {
  get.mockImplementation(async (url) => {
    if (String(url).startsWith('/api/recent')) return recent
    if (String(url).startsWith('/api/tasks')) return tasks
    // 别的接口（/api/modules…）照旧走 fetch 替身
    const r = await globalThis.fetch(String(url))
    return r.json()
  })
}

function stubRouter() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div/>' } }, { path: '/drama', component: { template: '<div/>' } },
    { path: '/drama/new', component: { template: '<div/>' } }, { path: '/drama/libraries', component: { template: '<div/>' } },
    { path: '/voices', component: { template: '<div/>' } }, { path: '/settings', component: { template: '<div/>' } },
    { path: '/tasks', component: { template: '<div/>' } }, { path: '/trash', component: { template: '<div/>' } },
    { path: '/picturebook', component: { template: '<div/>' } }, { path: '/picturebook/b/:id/overview', component: { template: '<div/>' } },
    { path: '/drama/p/:id/overview', component: { template: '<div/>' } },
  ] })
}

// 真实路由表，换成内存历史（hash 历史在 jsdom 里也能跑，但内存历史让断言更直接）
function realRouter() {
  return createRouter({ history: createMemoryHistory(), routes })
}

async function mountHome() {
  const r = stubRouter(); await r.push('/'); await r.isReady()
  const w = mount(Home, { global: { plugins: [r] } })
  await flushPromises()
  return w
}

beforeEach(() => {
  setLang('zh')
  store.modules = MODULES; store.modulesLoaded = true; store.product = null
  store.episodes = []; store.episodesLoaded = true
  recent = []; tasks = []
  stubApi()
})
afterEach(() => { get.mockReset() })

describe('Home · 启动台', () => {
  it('头部是产品名 + 一句定位；模块卡照旧（可用的是链接带项目数，规划中的灰掉）', async () => {
    store.product = { name: 'Content Cook · 内容创作工作台', name_en: 'Content Cook · Content creation workbench' }
    const w = await mountHome()
    const head = w.find('[data-test="launcher-head"]')
    expect(head.text()).toContain('Content Cook · 内容创作工作台')
    expect(head.text()).toContain('挑一个模块开始')
    const cards = w.find('[data-test="module-cards"]')
    expect(cards.findAll('.module-card').length).toBe(3)
    const drama = w.find('[data-test="module-drama"]')
    expect(drama.element.tagName).toBe('A')
    expect(drama.attributes('href')).toBe('/drama')
    expect(drama.text()).toContain('短剧')
    expect(drama.text()).toContain('2 个项目')
    const image = w.find('[data-test="module-image"]')
    expect(image.element.tagName).toBe('DIV')
    expect(image.classes()).toContain('planned')
    expect(image.text()).toContain('规划中')
    expect(image.text()).toContain('单张 / 成组出图')
  })

  it('不再嵌短剧首页：首页上没有短剧区、也没有短剧的三步引导', async () => {
    const w = await mountHome()
    expect(w.find('[data-test="drama-section"]').exists()).toBe(false)
    expect(w.find('[data-test="drama-home"]').exists()).toBe(false)
    expect(w.find('[data-test="drama-new"]').exists()).toBe(false)
    expect(w.text()).not.toContain('三步出第一条首帧')
    expect(w.findComponent(DramaHome).exists()).toBe(false)
  })

  it('「最近」跨模块列最近改过的项目：图标 / 标题 / id / 相对时间，点一行进那个项目', async () => {
    recent = RECENT
    const w = await mountHome()
    expect(get).toHaveBeenCalledWith('/api/recent?limit=12')
    const rows = w.findAll('[data-test="recent-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].text()).toContain('小狐狸的三只袜子')
    expect(rows[0].text()).toContain('绘本 / 有声书')
    expect(rows[0].text()).toContain('PB-1')
    expect(rows[0].text()).toContain('3 分钟前')
    expect(rows[0].attributes('href')).toBe('/picturebook/b/PB-1/overview')
    expect(rows[1].text()).toContain('昨天')
    expect(rows[1].attributes('href')).toBe('/drama/p/A-1/overview')
  })

  it('没有最近记录时整块不出现', async () => {
    const w = await mountHome()
    expect(w.find('[data-test="recent"]').exists()).toBe(false)
  })

  it('有 pending / running 的任务才出现「N 个任务在跑」，链到任务中心', async () => {
    recent = RECENT
    tasks = [
      { task_id: 't1', kind: 'pb-pages', module: 'picturebook', status: 'running' },
      { task_id: 't2', kind: 'set-generate', module: 'sticker', status: 'pending' },
      { task_id: 't3', kind: 'post', module: 'drama', status: 'succeeded' },
    ]
    const w = await mountHome()
    const line = w.find('[data-test="running-tasks"]')
    expect(line.text()).toContain('2 个任务在跑')
    expect(line.attributes('href')).toBe('/tasks')

    tasks = [{ task_id: 't3', kind: 'post', module: 'drama', status: 'succeeded' }]
    const w2 = await mountHome()
    expect(w2.find('[data-test="running-tasks"]').exists()).toBe(false)
  })

  it('所有模块都没有项目时给三步「第一次用」（模块无关，不是短剧那三步）', async () => {
    store.modules = MODULES.map(m => ({ ...m, project_count: 0 }))
    const w = await mountHome()
    const first = w.find('[data-test="first-run"]')
    expect(first.exists()).toBe(true)
    expect(first.text()).toContain('配好模型')
    expect(first.text()).toContain('选一个模块开始')
    expect(first.text()).toContain('花钱前都会先问你')
    expect(first.find('[data-test="first-run-settings"]').attributes('href')).toBe('/settings')
    expect(first.find('[data-test="first-run-local"]').attributes('href')).toBe('/settings')
    expect(first.text()).not.toContain('复制示例剧集')
  })

  it('任意模块有项目、或有最近记录时不给「第一次用」', async () => {
    expect((await mountHome()).find('[data-test="first-run"]').exists()).toBe(false)   // drama 有 2 个
    store.modules = MODULES.map(m => ({ ...m, project_count: 0 }))
    recent = RECENT
    expect((await mountHome()).find('[data-test="first-run"]').exists()).toBe(false)
  })

  it('英文界面用模块的英文名与英文说明，最近行用模块英文名', async () => {
    recent = RECENT
    const w = await mountHome()
    setLang('en')
    await w.vm.$nextTick()
    expect(w.find('[data-test="module-drama"]').text()).toContain('Short drama')
    expect(w.find('[data-test="module-image"]').text()).toContain('Single or batch images')
    expect(w.find('[data-test="module-image"]').text()).toContain('Planned')
    expect(w.findAll('[data-test="recent-row"]')[0].text()).toContain('Picture book')
  })
})

describe('Sidebar · 分组', () => {
  it('上块是全局入口（首页 / 任务中心 / 模型设置 / 音色库），下块是短剧模块', async () => {
    store.episodes = [
      { id: 'A-1', title: '加班夜', status: 'ok', locked: false, shot_count: 5, progress: null, module: 'drama' },
      { id: 'A-2', title: '别的模块', status: 'ok', locked: false, shot_count: 1, progress: null, module: 'image' },
    ]
    store.episodesLoaded = true
    const r = stubRouter(); await r.push('/'); await r.isReady()
    const w = mount(Sidebar, { global: { plugins: [r] } })
    const global = w.find('[data-test="sidebar-global"]')
    expect(global.findAll('a').map(a => a.attributes('href'))).toEqual(['/', '/tasks', '/settings', '/voices', '/trash'])
    expect(global.text()).toContain('任务中心')
    expect(global.find('select').exists()).toBe(true)
    const drama = w.find('[data-test="sidebar-module-drama"]')
    expect(drama.find('[data-test="module-header"]').text()).toContain('短剧')
    // 侧栏只管导航：头行名字 → 模块首页，再就是镜头库这种页面；新建是动作，放在模块首页上
    expect(drama.findAll('a').map(a => a.attributes('href')).slice(0, 2)).toEqual(['/drama', '/drama/libraries'])
    expect(drama.html()).not.toContain('/drama/new')
    expect(drama.find('[data-test="project-search"]').exists()).toBe(false)   // 只有两个项目，搜索框是噪音
    // 项目列表只列短剧模块的项目，行链接在 /drama 命名空间下，删除 ✕ 还在
    const rows = w.findAll('.proj')
    expect(rows.length).toBe(1)
    expect(rows[0].attributes('href')).toBe('/drama/p/A-1/overview')
    expect(rows[0].find('.del').exists()).toBe(true)
  })
})

describe('DramaHome · 短剧模块首页留在 /drama', () => {
  it('短剧自己的入口与三步引导还在（首页搬走了，模块首页没动）', async () => {
    store.episodes = []; store.episodesLoaded = true; store.health = { roles: {} }
    const r = stubRouter(); await r.push('/drama'); await r.isReady()
    const w = mount(DramaHome, { global: { plugins: [r] } })
    expect(w.find('[data-test="drama-new"]').exists()).toBe(true)
    expect(w.text()).toContain('三步出第一条首帧')
  })
})

describe('router · 命名空间、任务中心与旧路径重定向', () => {
  it('新路径直接命中，旧路径重定向到 /drama 下（子路径与查询串保留）', async () => {
    const r = realRouter()
    await r.push('/'); await r.isReady()
    for (const [from, to] of [
      ['/new', '/drama/new'],
      ['/libraries', '/drama/libraries'],
      ['/p/EP1', '/drama/p/EP1/overview'],
      ['/p/EP1/overview', '/drama/p/EP1/overview'],
      ['/p/EP1/shots/S2', '/drama/p/EP1/shots/S2'],
      ['/p/EP1/cast/lin', '/drama/p/EP1/cast/lin'],
      ['/p/EP1/post', '/drama/p/EP1/post'],
      ['/drama/p/EP1', '/drama/p/EP1/overview'],
      ['/drama', '/drama'],
      ['/settings', '/settings'],
      ['/tasks', '/tasks'],
      ['/voices/v1', '/voices/v1'],
    ]) {
      await r.push(from)
      expect(r.currentRoute.value.path, from).toBe(to)
      expect(r.currentRoute.value.matched.length, from).toBeGreaterThan(0)
    }
    await r.push('/p/EP1/shots/S2?x=1')
    expect(r.currentRoute.value.fullPath).toBe('/drama/p/EP1/shots/S2?x=1')
    expect(r.currentRoute.value.params.id).toBe('EP1')
    expect(r.currentRoute.value.params.shotId).toBe('S2')
    expect(dramaProject('EP1')).toBe('/drama/p/EP1/overview')
    expect(dramaProject('EP1', 'shots/S1')).toBe('/drama/p/EP1/shots/S1')
  })
})

describe('store · 产品名与模块表', () => {
  it('产品名从后端读，按界面语言二选一；没拉到时用工作名兜底', async () => {
    store.product = null
    expect(productName()).toBe('Content Cook')
    store.product = { name: 'Content Cook · 内容创作工作台', name_en: 'Content Cook · Content creation workbench', short: 'Content Cook' }
    expect(productName()).toBe('Content Cook · 内容创作工作台')
    setLang('en')
    expect(productName()).toBe('Content Cook · Content creation workbench')
  })
  it('loadModules 拉 /api/modules，顺手带回产品名；失败保持空列表', async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ product: { name: 'X', name_en: 'X-en' }, modules: MODULES }) }))
    store.modules = []; store.modulesLoaded = false
    await loadModules()
    expect(store.modules.map(m => m.id)).toEqual(['drama', 'picturebook', 'image'])
    expect(store.product.name).toBe('X')
    expect(store.modulesLoaded).toBe(true)
    globalThis.fetch = vi.fn(async () => ({ ok: false, status: 500, statusText: 'boom', json: async () => ({ detail: 'x' }) }))
    await loadModules()
    expect(store.modules).toEqual([])
  })
})
