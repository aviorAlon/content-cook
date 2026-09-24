// 绘本 / 有声书模块前端（docs/picturebook-spec.md §7）：首页列表与新建流程、分页表保存与 lint、朗读音色、成片装配轮询、
// 侧栏绘本块与路由。api 全部 mock；后端可能还没落地，这里只认契约里的接口形状。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let books, book, summary, lint, putResult, taskResult
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/picturebook') return books
    if (url.endsWith('/summary')) return summary
    if (url.endsWith('/lint')) return lint
    if (url === '/api/voices') return [{ id: 'girl_calm', name: '安静女声', voice: 'zh-CN-XiaoxiaoNeural' }, { id: 'en_story', name: 'Story voice', voice: 'en-US-AriaNeural' }]
    if (url.startsWith('/api/picturebook/')) return book
    if (url.startsWith('/api/tasks/')) return taskResult
    return {}
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    if (url === '/api/picturebook') return { id: 'PB-20260909-2', spec: { id: 'PB-20260909-2', ...body } }
    if (url.endsWith('/examples/create')) return { id: 'PB-20260909-2', example: body.name }
    if (url.endsWith('/jobs/draft')) return { task_id: 'draft-1', id: 'PB-20260909-2' }
    return { task_id: 't-1' }
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return putResult }),
  del: vi.fn(async (url) => { calls.push(['del', url]); return { archived_to: '.webui_deleted/picturebook/x' } }),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(async (id) => { calls.push(['poll', id]); return taskResult }),
  pollJob: vi.fn(), sleep: vi.fn(async () => {}), fileToBase64: vi.fn(async () => 'QUJD'),
}))
const confirmMock = vi.fn(async () => true)
vi.mock('../src/ui.js', () => ({
  prompt: vi.fn(), confirm: (...a) => confirmMock(...a), notice: vi.fn(), toast: vi.fn(), custom: vi.fn(), T: {}, toasts: [],
}))

import { store } from '../src/store.js'
import { routes } from '../src/router.js'
import { loadBook, progressOf, toCards } from '../src/picturebook.js'
import PbHome from '../src/views/picturebook/PbHome.vue'
import PbNew from '../src/views/picturebook/PbNew.vue'
import Book from '../src/views/picturebook/Book.vue'
import BookPages from '../src/views/picturebook/BookPages.vue'
import BookNarration from '../src/views/picturebook/BookNarration.vue'
import BookFinal from '../src/views/picturebook/BookFinal.vue'
import BookCharacters from '../src/views/picturebook/BookCharacters.vue'
import BookAdvanced from '../src/views/picturebook/BookAdvanced.vue'
import Sidebar from '../src/components/Sidebar.vue'

function stubRouter() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div/>' } }, { path: '/settings', component: { template: '<div/>' } }, { path: '/voices', component: { template: '<div/>' } },
    { path: '/drama', component: { template: '<div/>' } }, { path: '/drama/new', component: { template: '<div/>' } }, { path: '/drama/libraries', component: { template: '<div/>' } },
    { path: '/drama/p/:id/overview', component: { template: '<div/>' } },
    { path: '/picturebook', component: { template: '<div/>' } }, { path: '/picturebook/new', component: { template: '<div/>' } },
    { path: '/picturebook/b/:id/:sub', component: { template: '<div/>' } },
  ] })
}
async function mountAt(Comp, path = '/') {
  const r = stubRouter(); await r.push(path); await r.isReady()
  const w = mount(Comp, { global: { plugins: [r] } })
  await flushPromises()
  return { w, r }
}
// 项目文件（跟契约 §1 的示例书同一个故事，缩成 8 页）
function sampleSpec() {
  const kb = (dir) => ({ dir, scale: [1.0, 1.06] })
  return {
    id: 'PB-20260909-1', title_zh: '小狐狸的三只袜子', title_en: "Little Fox's Three Socks", age_band: '2-4', bilingual_mode: 'sentence_alternate',
    education: { cognition: '认颜色：红、黄、蓝', vocab: ['袜子/sock', '风/wind'], emotion: '东西飞走了，也可以分给朋友' },
    style_anchor: 'Soft watercolor', negative: 'text, letters',
    characters: { fox: { name: '小狐狸', name_en: 'Little Fox', block: '橘红色小狐狸', asset: null }, bird: { name: '小鸟', name_en: 'Little Bird', block: '天蓝色小鸟', asset: null } },
    scenes: { yard: { name: '小狐狸的院子', block: '晾衣绳下的院子', asset: null } },
    narration: { zh_voice: '', en_voice: '', en_rate: '-8%', pause: { lead: 0.4, between: 0.6, tail: 0.8 } },
    delivery: { aspect_ratio: '9:16', resolution: '1080p', landscape_variant: false },
    pages: [
      { id: 'P01', kind: 'cover', scene: '小狐狸站在院子里', characters: ['fox'], scene_ref: 'yard', zh: '小狐狸的三只袜子', en: "Little Fox's Three Socks", hook: '', education: '', kb: kb('push'), flip: 'page_turn' },
      { id: 'P02', kind: 'page', scene: '风吹起晾衣绳上的红袜子', characters: ['fox'], scene_ref: 'yard', zh: '呼呼呼，风把袜子吹走了！', en: 'Whoosh, the wind took the sock!', hook: '袜子飞到哪儿了？', education: '词汇:风/wind', kb: kb('left'), flip: 'page_turn' },
      { id: 'P03', kind: 'page', scene: '红袜子挂在树枝上，小鸟从袜口探头', characters: ['fox', 'bird'], scene_ref: null, zh: '小鸟住进了红袜子！', en: 'Little Bird moves into the red sock!', hook: '', education: '认知:红', kb: kb('push'), flip: 'page_turn' },
      { id: 'P04', kind: 'page', scene: '又一阵风吹过院子', characters: ['fox', 'bird'], scene_ref: 'yard', zh: '呼呼呼，风把袜子吹走了！', en: 'Whoosh, the wind took the sock!', hook: '这次飞去哪儿？', education: '', kb: kb('right'), flip: 'page_turn' },
      { id: 'P05', kind: 'page', scene: '黄袜子落在草丛里', characters: ['fox', 'bird'], scene_ref: null, zh: '黄袜子落进了草丛。', en: 'The yellow sock lands in the grass.', hook: '', education: '认知:黄', kb: kb('pull'), flip: 'page_turn' },
      { id: 'P06', kind: 'page', scene: '蓝袜子从窗口飘进小屋', characters: ['fox', 'bird'], scene_ref: null, zh: '蓝袜子飞回了小屋。', en: 'The blue sock flies back home.', hook: '它落在哪儿了？', education: '认知:蓝', kb: kb('up'), flip: 'page_turn' },
      { id: 'P07', kind: 'page', scene: '小狐狸朝树上的小鸟挥手', characters: ['fox', 'bird'], scene_ref: 'yard', zh: '一只给小鸟，一只留给我。', en: 'One for Little Bird, one for me.', hook: '', education: '情感:分给朋友', kb: kb('left'), flip: 'page_turn' },
      { id: 'P08', kind: 'back', scene: '小狐狸穿着蓝袜子睡着了', characters: ['fox', 'bird'], scene_ref: null, zh: '晚安，小狐狸。', en: 'Good night, Little Fox.', hook: '', education: '', kb: kb('pull'), flip: 'cut' },
    ],
  }
}
// 后端 detail 外壳：spec + 逐页产物行 + 时间线 + 成片信息（routers/picturebook.py::detail）
function sampleDetail(spec = sampleSpec(), { imageIds = ['P01', 'P02', 'P03'], audio = {}, final = false } = {}) {
  const id = spec.id
  return {
    id, module: 'picturebook', title: spec.title_zh, spec,
    warnings: [], notes: [], problems: [], stats: {},
    pages: spec.pages.map(p => ({
      id: p.id, kind: p.kind, image_url: imageIds.includes(p.id) ? `/api/picturebook/${id}/artifact/page/${p.id}` : null, image_stale: false, image_attempts: 0, image_error: null,
      audio: Object.fromEntries(['zh', 'en'].map(l => { const d = audio[p.id]?.[l]; return [l, d ? { done: true, duration: d, segments: 1, placeholder: false, source: 'tts', file: `${p.id}_${l}.mp3`, url: `/api/picturebook/${id}/artifact/audio/${p.id}_${l}` } : { done: false, duration: null, segments: 0, placeholder: false, source: null, file: null, url: null }] })),
    })),
    timeline: { total: 42.5, estimated: !Object.keys(audio).length, pages: spec.pages.map((p, i) => ({ id: p.id, start: i * 5, duration: audio[p.id] ? 4.2 : 5 })) },
    final_url: final ? `/api/picturebook/${id}/artifact/final` : null, final_path: final ? `output/picturebook/${id}/07_edit/${id}_book.mp4` : null, final_size_bytes: final ? 2097152 : null, landscape_url: null,
    busy: { draft: null, pages: null, narration: null, assemble: null }, fonts: [], default_font: '', voices: [],
  }
}

beforeEach(() => {
  calls.length = 0
  confirmMock.mockReset(); confirmMock.mockResolvedValue(true)
  books = [
    { id: 'PB-20260909-1', status: 'ok', title: '小狐狸的三只袜子', age_band: '2-4', bilingual_mode: 'sentence_alternate', page_count: 8, progress: { pages: 3, pages_total: 8, narration: 0, narration_total: 8, final: false, landscape: false }, cover_url: '/api/picturebook/PB-20260909-1/artifact/page/P01' },
    { id: 'PB-20260909-3', status: 'ok', title: '大象洗澡', age_band: '4-6', bilingual_mode: 'zh_only', page_count: 12, progress: { pages: 12, pages_total: 12, narration: 12, narration_total: 12, final: true, landscape: false }, cover_url: null },
  ]
  book = sampleDetail()
  // 概览（routers/picturebook.py::summary）：progress 是平铺的数字，下一步叫 next，问题卡是四要素形状
  summary = { id: 'PB-20260909-1', progress: { pages: 3, pages_total: 8, narration: 0, narration_total: 8, final: false, landscape: false }, problem_count: 2, problems: [], next: { key: 'pages', label: '生成插画', hint: '还有 5 页没有插画' }, busy: {}, final_url: null }
  // /lint：告警字符串（page P02: … / 全书：…）+ 后端拆的问题卡（它认不出 page 前缀，target 为空，前端从原文补）
  lint = { warnings: ['page P02: 翻页钩里有恐怖词「怪物」——换成让孩子好奇而不是害怕的问题', '全书：目标词「风/wind」全书只出现 2 次（要 ≥3 次）——把它放进重复句式里，每次重复都带上'], notes: ['全书：重复母题 1 组（P02/P04/P06）——最后一次出现时记得换个结果'], stats: {},
    problems: [{ scope: 'episode', target: '', kind: 'other', impact: 'page P02: 翻页钩里有恐怖词「怪物」', fix: '换成让孩子好奇而不是害怕的问题', text: 'page P02: 翻页钩里有恐怖词「怪物」——换成让孩子好奇而不是害怕的问题' }, { scope: 'episode', target: '', kind: 'other', impact: '目标词「风/wind」全书只出现 2 次（要 ≥3 次）', fix: '把它放进重复句式里，每次重复都带上', text: '全书：目标词「风/wind」全书只出现 2 次（要 ≥3 次）——把它放进重复句式里，每次重复都带上' }] }
  // PUT 返回 detail 外壳：warnings 在顶层
  putResult = { ...sampleDetail(), warnings: ['page P03: 中文 24 字超过 2-4 岁的 20 字——拆短或删一句'], problems: [{ scope: 'episode', target: '', kind: 'other', impact: 'page P03: 中文 24 字超过 2-4 岁的 20 字', fix: '拆短或删一句', text: 'page P03: 中文 24 字超过 2-4 岁的 20 字——拆短或删一句' }] }
  taskResult = { status: 'succeeded', result: {} }
  store.books = []; store.booksLoaded = false; store.book = null; store.bookSummary = null; store.bookLint = null; store.bookId = null; store.gen = null; store.voices = null
  store.modules = [
    { id: 'drama', name: '短剧', english: 'Short drama', route: '/drama', status: 'ready', available: true, project_count: 1 },
    { id: 'picturebook', name: '绘本 / 有声书', english: 'Picture book', route: '/picturebook', status: 'ready', available: true, project_count: 2 },
  ]
  store.modulesLoaded = true
  store.episodes = []; store.episodesLoaded = true
  store.health = { roles: { text: { configured: true } } }
})

describe('picturebook · 首页列表与新建', () => {
  it('列表：封面、标题、年龄段、页数、进度药丸；「复制示例」POST 示例书并进书', async () => {
    const { w, r } = await mountAt(PbHome, '/picturebook')
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/picturebook')).toBe(true)
    const rows = w.findAll('[data-test="pb-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe('/picturebook/b/PB-20260909-1/overview')
    expect(rows[0].find('img').attributes('src')).toContain('/artifact/page/P01')
    expect(rows[0].text()).toContain('小狐狸的三只袜子')
    expect(rows[0].text()).toContain('2-4')
    expect(rows[0].text()).toContain('8 页')
    expect(rows[0].text()).toContain('插画 3/8')
    expect(rows[0].text()).toContain('朗读 0/8')
    expect(rows[1].text()).toContain('成片 ✓')
    await w.find('[data-test="pb-example"]').trigger('click')
    await flushPromises()
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/picturebook/examples/create')
    expect(created[2]).toEqual({ name: 'fox_three_socks' })
    expect(calls.filter(c => c[0] === 'get' && c[1] === '/api/picturebook').length).toBe(2)
    expect(r.currentRoute.value.path).toBe('/picturebook/b/PB-20260909-2/overview')
  })

  it('新建：年龄段给页数提示、预设换文案；「空白开始」直接建书；「AI 起草」起任务 → 轮询 → 建书', async () => {
    const { w, r } = await mountAt(PbNew, '/picturebook/new')
    expect(w.find('[data-test="pb-page-hint"]').text()).toContain('8–10 页')
    await w.find('[data-test="pb-age"]').setValue('6-8')
    expect(w.find('[data-test="pb-page-hint"]').text()).toContain('12–16 页')
    expect(w.findAll('[data-test="pb-mode"] option').length).toBe(4)
    await w.find('[data-test="pb-preset"]').setValue('papercut')
    expect(w.find('[data-test="pb-anchor"]').element.value).toContain('paper-cut')
    await w.find('[data-test="pb-anchor"]').setValue('My own anchor')
    // 空白开始要书名
    await w.find('[data-test="pb-blank"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    await w.find('[data-test="pb-title"]').setValue('大象洗澡')
    await w.find('[data-test="pb-blank"]').trigger('click'); await flushPromises()
    let created = calls.find(c => c[0] === 'post' && c[1] === '/api/picturebook')
    expect(created[2]).toMatchObject({ title_zh: '大象洗澡', age_band: '6-8', bilingual_mode: 'sentence_alternate', style_anchor: 'My own anchor', pages: [] })
    expect(r.currentRoute.value.path).toBe('/picturebook/b/PB-20260909-2/overview')
    // AI 起草
    calls.length = 0
    taskResult = { status: 'succeeded', result: { id: 'PB-20260909-2', draft: { id: 'PB-20260909-2', title_zh: '大象洗澡', title_en: 'Elephant Bath', pages: [{ id: 'P01', scene: 'x', zh: '大象洗澡', en: 'Elephant Bath' }], characters: {} }, warnings: [], notes: [], cost_usd: 0.01, tokens: 800, backend: 'x', saved: false } }
    await w.find('[data-test="pb-direction"]').setValue('一头怕水的小象学会洗澡')
    await w.find('[data-test="pb-mode"]').setValue('zh_only')
    await w.find('[data-test="pb-draft"]').trigger('click'); await flushPromises()
    const draft = calls.find(c => c[0] === 'post' && c[1] === '/api/picturebook/jobs/draft')
    expect(draft[2]).toMatchObject({ direction: '一头怕水的小象学会洗澡', age_band: '6-8', bilingual_mode: 'zh_only' })
    expect(draft[2].education).toEqual({ cognition: '', vocab: [], emotion: '' })
    expect(calls.some(c => c[0] === 'poll' && c[1] === 'draft-1')).toBe(true)
    created = calls.find(c => c[0] === 'post' && c[1] === '/api/picturebook')
    expect(created[2].pages.length).toBe(1)
    expect(created[2].id).toBeUndefined()
    expect(created[2].cost_usd).toBeUndefined()
    expect(created[2].title_en).toBe('Elephant Bath')
    expect(created[2].style_anchor).toBe('My own anchor')
    expect(created[2].bilingual_mode).toBe('zh_only')
    expect(store.gen).toBeNull()
  })
})

describe('picturebook · 书页', () => {
  it('外壳：进书拉 detail/summary/lint，导航六项，顶部进度与下一步', async () => {
    const { w } = await mountAt(Book, '/picturebook/b/PB-20260909-1/overview')
    expect(calls.filter(c => c[0] === 'get').map(c => c[1])).toEqual(expect.arrayContaining(['/api/picturebook/PB-20260909-1', '/api/picturebook/PB-20260909-1/summary', '/api/picturebook/PB-20260909-1/lint']))
    const nav = w.findAll('.ws-nav a.nav').map(a => a.attributes('href'))
    expect(nav).toEqual(['overview', 'pages', 'characters', 'narration', 'final', 'advanced'].map(s => `/picturebook/b/PB-20260909-1/${s}`))
    expect(w.find('[data-test="book-progress"]').text()).toContain('插画 3/8')
    expect(w.find('[data-test="book-next"]').text()).toContain('还有 5 页没有插画')
    // 缩略图条只给有插画的页出图
    expect(w.findAll('.ws-nav .strip img').length).toBe(3)
  })

  it('分页表：编辑 → 保存 PUT 整本；返回的 lint 按页显示在行下；增删移动；单页重出走 only + force', async () => {
    await loadBook('PB-20260909-1')
    const { w } = await mountAt(BookPages, '/picturebook/b/PB-20260909-1/pages')
    expect(w.findAll('[data-test="page-row"]').length).toBe(8)
    // 进页先显示 /lint 的提醒：P02 那条在 P02 行下，全书那条在表上方
    expect(w.findAll('[data-test="page-lint"]').length).toBe(1)
    expect(w.find('[data-test="page-lint"]').text()).toContain('怪物')
    expect(w.find('[data-test="pages-lint-book"]').text()).toContain('风/wind')
    // 有插画的页有缩略图（概览说 P01–P03 有）
    expect(w.findAll('[data-test="page-thumb"]').length).toBe(3)
    expect(w.findAll('[data-test="page-thumb"]')[0].attributes('src')).toBe('/api/picturebook/PB-20260909-1/artifact/page/P01')
    expect(w.find('[data-test="pages-save"]').attributes('disabled')).toBeDefined()
    const rows = w.findAll('[data-test="page-row"]')
    await rows[1].find('[data-test="page-zh"]').setValue('呼呼呼，风把袜子吹走了！改过')
    await rows[1].find('[data-test="page-kb"]').setValue('up')
    expect(w.find('[data-test="pages-save"]').attributes('disabled')).toBeUndefined()
    // 移动 + 删除 + 新增
    await rows[2].find('[data-test="page-up"]').trigger('click')
    await w.findAll('[data-test="page-row"]')[7].find('[data-test="page-remove"]').trigger('click')
    await w.find('[data-test="page-add"]').trigger('click')
    const ids = w.findAll('[data-test="page-row"]').map(r => r.find('.mono b').text())
    expect(ids).toEqual(['P01', 'P03', 'P02', 'P04', 'P05', 'P06', 'P07', 'P08'])
    await w.find('[data-test="pages-save"]').trigger('click'); await flushPromises()
    const saved = calls.find(c => c[0] === 'put' && c[1] === '/api/picturebook/PB-20260909-1')
    expect(saved[2].pages.map(p => p.id)).toEqual(ids)
    expect(saved[2].pages[2]).toMatchObject({ id: 'P02', zh: '呼呼呼，风把袜子吹走了！改过', kb: { dir: 'up' } })
    expect(saved[2].pages[7].kind).toBe('page')
    expect(saved[2].title_zh).toBe('小狐狸的三只袜子')
    expect(saved[2].id).toBe('PB-20260909-1')
    expect(saved[2].output_root).toBeUndefined()
    // 保存返回的 lint 顶掉旧的：只剩 P03 那条
    expect(w.findAll('[data-test="page-lint"]').length).toBe(1)
    expect(w.find('[data-test="page-lint"]').text()).toContain('24 字')
    expect(w.find('[data-test="pages-lint-book"]').exists()).toBe(false)
    // 单页重出
    await w.findAll('[data-test="page-redo"]')[0].trigger('click'); await flushPromises()
    const redo = calls.find(c => c[0] === 'post' && c[1].endsWith('/pages/generate'))
    expect(redo[2]).toEqual({ only: ['P01'], force: true })
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-1')).toBe(true)
  })

  it('分页表：保存报错就地显示', async () => {
    await loadBook('PB-20260909-1')
    const { put } = await import('../src/api.js')
    put.mockImplementationOnce(async () => { throw new Error('page P02.zh 不能为空') })
    const { w } = await mountAt(BookPages, '/picturebook/b/PB-20260909-1/pages')
    await w.findAll('[data-test="page-row"]')[1].find('[data-test="page-zh"]').setValue('')
    await w.find('[data-test="pages-save"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="pages-error"]').text()).toContain('P02.zh 不能为空')
  })

  it('朗读：音色下拉来自音色库，改了保存进 narration；「合成朗读」起任务并轮询；有音频的页有播放器与时长', async () => {
    book = sampleDetail(sampleSpec(), { audio: { P01: { zh: 2.4, en: 2.1 } } })
    await loadBook('PB-20260909-1')
    const { w } = await mountAt(BookNarration, '/picturebook/b/PB-20260909-1/narration')
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/voices')).toBe(true)
    const zh = w.find('[data-test="zh-voice"]')
    expect(zh.findAll('option').map(o => o.attributes('value'))).toEqual(['', 'girl_calm', 'en_story'])
    await zh.setValue('girl_calm')
    await w.find('[data-test="en-voice"]').setValue('en_story')
    await w.find('[data-test="en-rate"]').setValue('-10%')
    await w.find('[data-test="narration-save"]').trigger('click'); await flushPromises()
    const saved = calls.find(c => c[0] === 'put')
    expect(saved[2].narration).toEqual({ zh_voice: 'girl_calm', en_voice: 'en_story', en_rate: '-10%', pause: { lead: 0.4, between: 0.6, tail: 0.8 } })
    const rows = w.findAll('[data-test="narration-row"]')
    expect(rows.length).toBe(8)
    expect(rows[0].find('[data-test="audio-zh"]').attributes('src')).toBe('/api/picturebook/PB-20260909-1/artifact/audio/P01_zh')
    expect(rows[0].find('[data-test="audio-en"]').attributes('src')).toBe('/api/picturebook/PB-20260909-1/artifact/audio/P01_en')
    expect(rows[0].text()).toContain('2.4s')
    expect(rows[0].text()).toContain('4.2s')      // 页时长来自 detail.timeline
    expect(rows[1].find('audio').exists()).toBe(false)
    expect(w.text()).toContain('全书 42.5s')
    await w.find('[data-test="narration-generate"]').trigger('click'); await flushPromises()
    expect(confirmMock).toHaveBeenCalled()
    const gen = calls.find(c => c[0] === 'post' && c[1].endsWith('/narration/generate'))
    expect(gen[2]).toEqual({ languages: ['zh', 'en'] })
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-1')).toBe(true)
  })

  it('成片：装配起任务并轮询，完成后刷新概览显示预览与下载；朗读稿两种语言；16:9 勾选走 PUT', async () => {
    await loadBook('PB-20260909-1')
    const { w } = await mountAt(BookFinal, '/picturebook/b/PB-20260909-1/final')
    expect(w.find('[data-test="final-card"]').exists()).toBe(false)
    expect(w.find('[data-test="sheet-zh"]').attributes('href')).toBe('/api/picturebook/PB-20260909-1/script-sheet?lang=zh')
    expect(w.find('[data-test="sheet-en"]').attributes('href')).toBe('/api/picturebook/PB-20260909-1/script-sheet?lang=en')
    summary = { ...summary, progress: { ...summary.progress, final: true }, final_url: '/api/picturebook/PB-20260909-1/artifact/final' }
    book = sampleDetail(sampleSpec(), { final: true })
    await w.find('[data-test="assemble"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post' && c[1] === '/api/picturebook/PB-20260909-1/assemble')).toBe(true)
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-1')).toBe(true)
    expect(calls.filter(c => c[0] === 'get' && c[1].endsWith('/summary')).length).toBe(2)
    expect(w.find('[data-test="final-card"]').exists()).toBe(true)
    expect(w.find('[data-test="final-video"]').attributes('src')).toBe('/api/picturebook/PB-20260909-1/artifact/final')
    expect(w.find('[data-test="final-download"]').attributes('href')).toBe('/api/picturebook/PB-20260909-1/artifact/final')
    expect(w.find('[data-test="final-card"]').text()).toContain('2.0 MB')
    expect(w.find('[data-test="final-card"]').text()).toContain('07_edit/PB-20260909-1_book.mp4')
    await w.find('[data-test="landscape"]').setValue(true); await flushPromises()
    const saved = calls.find(c => c[0] === 'put')
    expect(saved[2].delivery.landscape_variant).toBe(true)
  })

  it('装配失败：轮询到 failed 弹原因，进度条清掉', async () => {
    taskResult = { status: 'failed', error: '没找到 ffmpeg' }
    await loadBook('PB-20260909-1')
    const { w } = await mountAt(BookFinal, '/picturebook/b/PB-20260909-1/final')
    await w.find('[data-test="assemble"]').trigger('click'); await flushPromises()
    const { notice } = await import('../src/ui.js')
    expect(notice.mock.calls.some(c => String(c[0]).includes('没找到 ffmpeg'))).toBe(true)
    expect(store.gen).toBeNull()
  })
})

describe('picturebook · 角色与高级', () => {
  it('角色页：风格锚 / 角色表 / 场景锚表整本 PUT，key 从英文名推；定妆图上传先保存再 POST assets/character/{key}', async () => {
    await loadBook('PB-20260909-1')
    const { w } = await mountAt(BookCharacters, '/picturebook/b/PB-20260909-1/characters')
    expect(w.findAll('[data-test="char-row"]').length).toBe(2)
    expect(w.findAll('[data-test="scene-row"]').length).toBe(1)
    await w.find('[data-test="style-anchor"]').setValue('Crayon storybook')
    await w.find('[data-test="char-add"]').trigger('click')
    const rows = w.findAll('[data-test="char-row"]')
    await rows[2].findAll('input')[1].setValue('小老鼠')
    await rows[2].findAll('input')[2].setValue('Mouse')
    await w.find('[data-test="chars-save"]').trigger('click'); await flushPromises()
    const saved = calls.find(c => c[0] === 'put')
    expect(saved[2].style_anchor).toBe('Crayon storybook')
    expect(Object.keys(saved[2].characters)).toEqual(['fox', 'bird', 'mouse'])
    expect(saved[2].characters.mouse).toEqual({ name: '小老鼠', name_en: 'Mouse', block: '', asset: null })
    expect(saved[2].scenes.yard.block).toBe('晾衣绳下的院子')
    expect(saved[2].pages.length).toBe(8)
    // 上传：改了外形块没保存 → 先 PUT 再 POST，body 是 base64
    calls.length = 0
    await rows[0].find('textarea').setValue('橘红色小狐狸，绿背心')
    const file = new File(['x'], 'fox.png', { type: 'image/png' })
    const input = rows[0].find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change'); await flushPromises()
    expect(calls[0][0]).toBe('put')
    const up = calls.find(c => c[0] === 'post' && c[1] === '/api/picturebook/PB-20260909-1/assets/character/fox')
    expect(up[2]).toEqual({ filename: 'fox.png', data_base64: 'QUJD' })
  })

  it('高级页：JSON 直改 PUT（非法 JSON 就地报错）；删除 = 归档后回模块首页', async () => {
    await loadBook('PB-20260909-1')
    const { w, r } = await mountAt(BookAdvanced, '/picturebook/b/PB-20260909-1/advanced')
    const ta = w.find('[data-test="json-text"]')
    expect(JSON.parse(ta.element.value).title_zh).toBe('小狐狸的三只袜子')
    expect(ta.element.value).not.toContain('output_root')
    await ta.setValue('{ not json')
    await w.find('[data-test="json-save"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="json-error"]').text()).toContain('不是合法的 JSON')
    expect(calls.some(c => c[0] === 'put')).toBe(false)
    await ta.setValue(JSON.stringify({ ...sampleSpec(), title_zh: '小狐狸的三只袜子 2' }))
    await w.find('[data-test="json-save"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'put')[2].title_zh).toBe('小狐狸的三只袜子 2')
    await w.find('[data-test="book-archive"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'del' && c[1] === '/api/picturebook/PB-20260909-1')).toBe(true)
    expect(store.book).toBeNull()
    expect(r.currentRoute.value.path).toBe('/picturebook')
  })
})

describe('picturebook · 侧栏与路由', () => {
  it('模块可用时侧栏多一块「绘本」：新建 / 搜索 / 书列表指向 /picturebook/b，删除 ✕ 归档；不可用时没有这块', async () => {
    store.books = books; store.booksLoaded = true
    const { w, r } = await mountAt(Sidebar, '/')
    const block = w.find('[data-test="sidebar-module-picturebook"]')
    expect(block.exists()).toBe(true)
    expect(block.find('[data-test="module-header"]').text()).toContain('绘本')
    expect(block.findAll('a')[0].attributes('href')).toBe('/picturebook')   // 头行名字 → 模块首页；新建在模块首页上，侧栏不重复
    expect(block.html()).not.toContain('/picturebook/new')
    // 手风琴：在 / 上默认展开短剧，点绘本的头行才展开它；展开后短剧收起
    expect(block.classes()).not.toContain('open')
    await block.find('[data-test="module-header"]').trigger('click')
    expect(block.classes()).toContain('open')
    expect(w.find('[data-test="sidebar-module-drama"]').classes()).not.toContain('open')
    const rows = w.findAll('[data-test="book-list"] .proj')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe('/picturebook/b/PB-20260909-1/overview')
    expect(rows[0].text()).toContain('插画 3/8')
    expect(rows[1].text()).toContain('已有成片')
    // 列表短的时候没有搜索框（SEARCH_MIN=8）——搜索行为在 components.test.js 里单测
    expect(block.find('[data-test="book-search"]').exists()).toBe(false)
    // 短剧块不受影响
    expect(w.find('[data-test="sidebar-module-drama"]').exists()).toBe(true)
    // 删除 = 归档
    await w.findAll('[data-test="book-list"] .del')[0].trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'del' && c[1] === '/api/picturebook/PB-20260909-1')).toBe(true)
    expect(calls.filter(c => c[0] === 'get' && c[1] === '/api/picturebook').length).toBe(1)
    expect(r.currentRoute.value.path).toBe('/')
    store.modules = store.modules.filter(m => m.id !== 'picturebook')
    await w.vm.$nextTick()
    expect(w.find('[data-test="sidebar-module-picturebook"]').exists()).toBe(false)
  })

  it('路由：/picturebook 下的路径直接命中，不被旧路径重定向劫持；/b/:id 落到 overview', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/'); await r.isReady()
    for (const [from, to] of [
      ['/picturebook', '/picturebook'],
      ['/picturebook/new', '/picturebook/new'],
      ['/picturebook/b/PB-1', '/picturebook/b/PB-1/overview'],
      ['/picturebook/b/PB-1/pages', '/picturebook/b/PB-1/pages'],
      ['/picturebook/b/PB-1/characters', '/picturebook/b/PB-1/characters'],
      ['/picturebook/b/PB-1/narration', '/picturebook/b/PB-1/narration'],
      ['/picturebook/b/PB-1/final', '/picturebook/b/PB-1/final'],
      ['/picturebook/b/PB-1/advanced', '/picturebook/b/PB-1/advanced'],
      ['/p/EP1', '/drama/p/EP1/overview'],
    ]) {
      await r.push(from)
      expect(r.currentRoute.value.path, from).toBe(to)
      expect(r.currentRoute.value.matched.length, from).toBeGreaterThan(0)
    }
    expect(r.currentRoute.value.params.id).toBe('EP1')
  })

  it('helpers：进度形状兜底、lint 字符串拆成问题卡', () => {
    expect(progressOf({ progress: { pages: { done: 2, total: 5 }, narration: 1, final: { exists: true } } }, 5)).toEqual({ pages: { done: 2, total: 5, ids: null }, narration: { done: 1, total: 5 }, final: true })
    expect(progressOf(null, 3)).toEqual({ pages: { done: 0, total: 3 }, narration: { done: 0, total: 3 }, final: false })
    const cards = toCards({ warnings: ['page P02: 钩里有恐怖词——换掉', '全书：结尾不够安稳'] })
    expect(cards[0]).toMatchObject({ scope: 'page', target: 'P02', impact: '钩里有恐怖词', fix: '换掉' })
    expect(cards[1]).toMatchObject({ scope: 'book', target: '', impact: '结尾不够安稳', fix: '' })
    expect(toCards({ problems: [{ scope: 'page', target: 'P01', kind: 'hook', impact: 'x', fix: 'y' }] })[0]).toMatchObject({ kind: 'hook', target: 'P01', impact: 'x' })
    // 后端四要素卡认不出 page 前缀（target 空）：前端从原文补出页号，impact 去掉前缀
    const fromBackend = toCards({ problems: [{ scope: 'episode', target: '', kind: 'other', impact: 'page P05: 拟声词两组', fix: '留一组', text: 'page P05: 拟声词两组——留一组' }] })
    expect(fromBackend[0]).toMatchObject({ scope: 'page', target: 'P05', impact: '拟声词两组', fix: '留一组' })
  })
})
