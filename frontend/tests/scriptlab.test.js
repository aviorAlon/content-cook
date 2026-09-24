// 剧本工作台模块前端（docs/scriptlab-spec.md §8–§9）：首页列表与新建 / 从现有项目导入、新建页 POST 的 body 形状、
// 写页（逐段逐句编辑、保存段落骨架 PUT、另存为新版本 POST）、试页（生成一版 / 只重写勾上的几段 / 跑检查 / 审稿卡 / 版本列表）、
// 比页（两版并排、相同行淡显不同行高亮、用哪一版）、用页（落点下拉与角色映射后 POST 的 body）、侧栏块、任务中心表与路由。
// api 全部 mock；只认契约里的接口形状。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let list, detail, options, targetsData, reviewData, taskResult, importFail
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/script') return list
    if (url === '/api/script/options') return options
    if (url === '/api/script/targets') return targetsData
    if (url.startsWith('/api/tasks/')) return taskResult
    if (url.endsWith('/review')) return reviewData
    if (url.startsWith('/api/script/')) return detail
    return {}
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    if (url === '/api/script') return { id: 'SC-20260920-2', spec: { ...body } }
    if (url.endsWith('/import')) { if (importFail) throw new Error('那边这一期还没有台词可导'); return { imported: 2 } }
    if (url.endsWith('/generate')) return { task_id: 't-draft', cost_note: '发 1 次文本模型请求（@text）' }
    if (url.endsWith('/versions')) return { version: 'V3' }
    if (url.endsWith('/use')) return { message: '已放进《为什么我们戒不掉短视频》的〈G01〉', target_title: '为什么我们戒不掉短视频' }
    return { ok: true }
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return { id: 'SC-20260920-1', spec: body } }),
  del: vi.fn(async (url) => { calls.push(['del', url]); return { archived_to: '.webui_deleted/script/x' } }),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(async (id) => { calls.push(['poll', id]); return taskResult }),
  pollJob: vi.fn(), sleep: vi.fn(async () => {}), fileToBase64: vi.fn(async () => 'QUJD'),
}))
const confirmMock = vi.fn(async () => true)
const promptMock = vi.fn(async () => '')
const toastMock = vi.fn()
vi.mock('../src/ui.js', () => ({
  prompt: (...a) => promptMock(...a), confirm: (...a) => confirmMock(...a), notice: vi.fn(), toast: (...a) => toastMock(...a), custom: vi.fn(), toasts: [],
  T: { role: { text: '剧本文本', image: '生图', voice: '语音', video: '视频', music: '配乐' } },
}))

import { store } from '../src/store.js'
import { setLang } from '../src/i18n.js'
import { routes } from '../src/router.js'
import { KIND_LABELS, PROJECT_ROUTES, taskTarget } from '../src/tasks.js'
import {
  loadScript, clearScript, progressOf, progressText, nextBlockId, versionIndex, compareVersions, lineText,
  scriptRoute, specBody, emptyBrief, estimateText, verdictText, sourceLabel, loadOptions, targetName,
} from '../src/scriptlab.js'
import ScriptHome from '../src/views/script/ScriptHome.vue'
import ScriptNew from '../src/views/script/ScriptNew.vue'
import Script from '../src/views/script/Script.vue'
import ScriptWrite from '../src/views/script/ScriptWrite.vue'
import ScriptTry from '../src/views/script/ScriptTry.vue'
import ScriptCompare from '../src/views/script/ScriptCompare.vue'
import ScriptUse from '../src/views/script/ScriptUse.vue'
import Sidebar from '../src/components/Sidebar.vue'

const stub = { template: '<div/>' }
function stubRouter() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: stub }, { path: '/settings', component: stub }, { path: '/voices/:voiceId?', component: stub }, { path: '/tasks', component: stub },
    { path: '/drama', component: stub }, { path: '/drama/new', component: stub }, { path: '/drama/libraries', component: stub }, { path: '/drama/p/:id/overview', component: stub },
    { path: '/picturebook', component: stub }, { path: '/picturebook/b/:id/:sub', component: stub },
    { path: '/clip', component: stub }, { path: '/clip/c/:id/:sub', component: stub },
    ...['sticker', 'portrait', 'image'].flatMap(m => [
      { path: `/${m}`, component: stub, meta: { module: m } }, { path: `/${m}/s/:id/:sub`, component: stub, meta: { module: m } },
    ]),
    { path: '/music', component: stub }, { path: '/music/m/:id/:sub', component: stub },
    { path: '/podcast', component: stub }, { path: '/podcast/p/:id/:sub', component: stub },
    { path: '/script', component: stub }, { path: '/script/new', component: stub }, { path: '/script/p/:id/:sub', component: stub },
  ] })
}
async function mountAt(Comp, path = '/') {
  const r = stubRouter(); await r.push(path); await r.isReady()
  const w = mount(Comp, { global: { plugins: [r] } })
  await flushPromises()
  return { w, r }
}

const ID = 'SC-20260920-1'
const clone = (x) => JSON.parse(JSON.stringify(x))

function sampleSpec() {
  return {
    id: ID, module: 'script', title: '下班后的消息，要不要回', target: 'podcast', language: 'zh',
    brief: {
      topic: '下班后收到工作消息，回还是不回', angle: '不站队，两个处境不同的人各讲各的', audience: '刚工作两三年的人',
      materials: '阿杰上个月周六晚上十点被拉进新群', tone: '像朋友下班后吐槽', avoid: ['说教'],
    },
    speakers: [
      { id: 'jie', name: '阿杰', persona: '大厂产品经理，说话慢', stance: '边界感是必须的' },
      { id: 'lin', name: '小林', persona: '三人小公司合伙人，语速快', stance: '客户在等就得回' },
    ],
    blocks: [
      { id: 'B01', title: '冷开场', goal: '用一个具体场景把人拽进来', note: '' },
      { id: 'B02', title: '两边各讲各的', goal: '', note: '' },
    ],
    versions: [
      {
        id: 'V1', label: '第一版', source: 'model', model: 'gpt-4o-mini', backend: 'settings', note: '', created_at: 1789000000, from_version: '',
        blocks: [
          { id: 'B01', lines: [{ who: 'jie', text: '周六晚上十点，我被拉进一个新群。', cue: '', kind: 'talk' }] },
          { id: 'B02', lines: [{ who: 'lin', text: '客户在等，我能不回吗？', cue: '', kind: 'talk' }, { who: 'jie', text: '能。', cue: '停顿', kind: 'talk' }] },
        ],
      },
      {
        id: 'V2', label: '硬气一点', source: 'rewrite', model: 'gpt-4o-mini', backend: 'settings', note: '阿杰更硬气', created_at: 1789000900, from_version: 'V1',
        blocks: [
          { id: 'B01', lines: [{ who: 'jie', text: '周六晚上十点，我被拉进一个新群。', cue: '', kind: 'talk' }] },
          { id: 'B02', lines: [{ who: 'lin', text: '客户在等，我能不回吗？', cue: '', kind: 'talk' }, { who: 'jie', text: '我十点以后不看手机。', cue: '', kind: 'talk' }] },
        ],
      },
    ],
    current: 'V1',
    used_in: [{ module: 'podcast', id: 'PC-1', target: 'G01', at: '2026-09-20T10:00:00' }],
    output_root: `output/script/${ID}`, project_root: 'D:/x',
  }
}
function sampleDetail(spec = sampleSpec()) {
  return {
    id: spec.id, module: 'script', title: spec.title, spec,
    current_version: spec.versions.find(v => v.id === spec.current) || null,
    estimate: { chars: 44, lines: 3, blocks: 2, seconds: 9.8, minutes: 0.16 },
    warnings: ['block B02: 回答太短——补一句理由'],
    problems: [{ scope: 'block', target: 'B02', kind: 'dialogue', impact: '回答太短', fix: '补一句理由' }],
    versions: [
      { id: 'V1', label: '第一版', source: 'model', model: 'gpt-4o-mini', note: '', created_at: 1789000000,
        estimate: { chars: 44, lines: 3, minutes: 0.16 }, warnings_count: 1, review: { verdicts: { violated: 1, risk: 2, ok: 5 }, stale: false } },
      { id: 'V2', label: '硬气一点', source: 'rewrite', model: 'gpt-4o-mini', note: '阿杰更硬气', created_at: 1789000900,
        estimate: { chars: 52, lines: 3, minutes: 0.19 }, warnings_count: 0, review: null },
    ],
    review: null, targets_hint: {}, busy: { draft: null, review: null },
    limits: { speakers: [1, 6] }, used_in: spec.used_in,
  }
}

beforeEach(() => {
  calls.length = 0
  confirmMock.mockReset(); confirmMock.mockResolvedValue(true)
  promptMock.mockReset(); promptMock.mockResolvedValue('')
  toastMock.mockReset()
  list = [
    { id: ID, module: 'script', title: '下班后的消息，要不要回', target: 'podcast', speakers: 2, blocks: 2, versions: 2,
      current: 'V1', minutes: 0.2, used_in: 1, path: `content/script/${ID}.json` },
    { id: 'SC-20260920-3', module: 'script', title: '第三集第七场', target: 'drama_screenplay', speakers: 3, blocks: 1, versions: 0,
      current: '', minutes: 0, used_in: 0, path: 'content/script/SC-20260920-3.json' },
  ]
  detail = sampleDetail()
  options = {
    targets: [
      { id: 'podcast', name: '播客对谈', english: 'Podcast', note: '按播客那八条写法写' },
      { id: 'drama_screenplay', name: '短剧剧本', english: 'Short drama scene', note: '按短剧台词那几条写' },
      { id: 'drama_shot', name: '短剧分镜台词', english: 'Short drama shot', note: '每一段当一镜看' },
      { id: 'generic', name: '通用', english: 'Generic dialogue', note: '两套写法的交集' },
    ],
    cues: ['笑', '轻笑', '停顿', '打断', '叹气', '加快', '放慢', '低声', '强调'],
    kinds: ['talk', 'question', 'answer'],
    limits: { speakers: [1, 6] },
    craft: {},
  }
  targetsData = {
    podcast: [{ id: 'PC-1', title: '为什么我们戒不掉短视频',
      segments: [{ id: 'G01', title: '冷开场', lines: 2 }, { id: 'G02', title: '话题', lines: 0 }],
      hosts: [{ id: 'zhou', name: '老周' }, { id: 'xiao', name: '小夏' }] }],
    drama: [{ id: 'EP-1', title: '第一集', locked: true,
      scenes: [{ id: 'S1', title: '办公室', lines: 3 }, { id: 'S2', title: '走廊', lines: 0 }],
      shots: [{ id: 'S1-01', title: '', lines: 1 }],
      cast: [{ id: 'c1', name: '林小满' }, { id: 'c2', name: '阿杰' }] }],
  }
  reviewData = { review: null, stale: false, note: '', problems: [], cost_note: '发 1 次文本模型请求', task_id: null }
  taskResult = { status: 'succeeded', result: { version: 'V3' } }
  importFail = false
  store.scripts = []; store.scriptsLoaded = false
  clearScript(); store.gen = null
  store.modules = [
    { id: 'drama', name: '短剧', route: '/drama', status: 'ready', available: true, project_count: 1 },
    { id: 'script', name: '剧本工作台', english: 'Script lab', icon: '✍️', route: '/script', status: 'ready', available: true, project_count: 2 },
  ]
  store.modulesLoaded = true
  store.episodes = []; store.episodesLoaded = true
  store.books = []; store.booksLoaded = true
  store.clips = []; store.clipsLoaded = true
  store.musics = []; store.musicsLoaded = true
  store.podcasts = []; store.podcastsLoaded = true
  store.sets = { sticker: [], portrait: [], image: [] }; store.setsLoaded = { sticker: true, portrait: true, image: true }
  store.health = { roles: { text: { configured: true } } }
})

describe('scriptlab · 首页与新建', () => {
  it('首页：标题 / 目标形态 / 段数 / 版本数 / 采用第几版 / 已投放几处；「+ 新建剧本」进新建页', async () => {
    const { w, r } = await mountAt(ScriptHome, '/script')
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/script')).toBe(true)
    const rows = w.findAll('[data-test="script-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe(`/script/p/${ID}/write`)
    expect(rows[0].text()).toContain('下班后的消息，要不要回')
    expect(rows[0].find('[data-test="script-target"]').text()).toBe('播客对谈')
    expect(rows[0].text()).toContain('2 段')
    expect(rows[0].text()).toContain('2 版')
    expect(rows[0].text()).toContain('采用第 1 版')
    expect(rows[0].text()).toContain('已投放 1 处')
    expect(rows[1].find('[data-test="script-target"]').text()).toBe('短剧剧本')
    expect(rows[1].text()).toContain('0 版')
    expect(rows[1].text()).not.toContain('采用第')
    await w.find('[data-test="script-new"]').trigger('click'); await flushPromises()
    expect(r.currentRoute.value.path).toBe('/script/new')
  })

  it('首页空状态；「从现有项目导入」先 POST 建壳再 POST import，然后进写页', async () => {
    list = []
    const { w, r } = await mountAt(ScriptHome, '/script')
    expect(w.find('[data-test="script-empty"]').exists()).toBe(true)
    expect(w.find('[data-test="import-module"]').element.value).toBe('podcast')
    expect(w.findAll('[data-test="import-target"] option').map(o => o.attributes('value'))).toEqual(['', 'PC-1'])
    await w.find('[data-test="import-module"]').setValue('drama_screenplay'); await flushPromises()
    expect(w.findAll('[data-test="import-target"] option').map(o => o.attributes('value'))).toEqual(['', 'EP-1'])
    await w.find('[data-test="import-module"]').setValue('podcast'); await flushPromises()
    await w.find('[data-test="import-target"]').setValue('PC-1')
    calls.length = 0
    await w.find('[data-test="import-submit"]').trigger('click'); await flushPromises()
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/script')
    expect(created[2]).toMatchObject({ title: '为什么我们戒不掉短视频', target: 'podcast', language: 'zh', speakers: [], blocks: [], versions: [] })
    expect(calls.find(c => c[0] === 'post' && c[1] === '/api/script/SC-20260920-2/import')[2]).toEqual({ module: 'podcast', target_id: 'PC-1' })
    expect(r.currentRoute.value.path).toBe('/script/p/SC-20260920-2/write')
  })

  it('从现有项目导入：第二步没成就把刚建出来的空壳删掉，不在列表里留一个没角色没台词的项目', async () => {
    list = []
    importFail = true
    const { w, r } = await mountAt(ScriptHome, '/script')
    await w.find('[data-test="import-target"]').setValue('PC-1')
    calls.length = 0
    await w.find('[data-test="import-submit"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post' && c[1] === '/api/script')).toBe(true)
    expect(calls.some(c => c[0] === 'del' && c[1] === '/api/script/SC-20260920-2')).toBe(true)
    expect(r.currentRoute.value.path).toBe('/script')
    expect(w.find('.status').text()).toContain('那边这一期还没有台词可导')
  })

  it('英文界面：形态名用后端给的 english（新建下拉 / 首页 pill / 试页 pill），不露「短剧剧本（场）」这种字典里没有的中文', async () => {
    setLang('en')
    try {
      const { w } = await mountAt(ScriptNew, '/script/new')
      expect(w.findAll('[data-test="script-target"] option').map(o => o.text())).toEqual(['Podcast', 'Short drama scene', 'Short drama shot', 'Generic dialogue'])
      const { w: home } = await mountAt(ScriptHome, '/script')
      expect(home.findAll('[data-test="script-target"]').map(x => x.text())).toEqual(['Podcast', 'Short drama scene'])
      await loadScript(ID)
      const { w: tryPage } = await mountAt(ScriptTry, `/script/p/${ID}/try`)
      expect(tryPage.find('[data-test="try-target"]').text()).toBe('Podcast')
    } finally { setLang('zh') }
  })

  it('英文界面拉到的形态表（后端已把 name 翻成英文）切回中文不再接着用：先回落前端兜底表，再按中文重拉', async () => {
    const zhTargets = options.targets
    // 后端按 X-StoryStudio-Lang 翻了 name：英文界面拉到的就是英文名
    options = { ...options, targets: zhTargets.map(x => ({ ...x, name: x.english })) }
    setLang('en')
    try { await loadOptions({ force: true }) } finally { setLang('zh') }
    expect(targetName('drama_shot')).toBe('短剧分镜台词')          // 缓存是英文界面拉的：不拿它来显示中文界面
    options = { ...options, targets: zhTargets.map(x => (x.id === 'drama_shot' ? { ...x, name: '短剧分镜（镜）' } : x)) }
    calls.length = 0
    await loadOptions()
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/script/options')).toBe(true)   // 语言换了就重拉
    expect(targetName('drama_shot')).toBe('短剧分镜（镜）')
  })

  it('新建：形态下拉带一句说明；没标题 / 有人没名字都不建；POST 正确的 body 后进写页', async () => {
    const { w, r } = await mountAt(ScriptNew, '/script/new')
    expect(w.findAll('[data-test="script-target"] option').map(o => o.attributes('value'))).toEqual(['podcast', 'drama_screenplay', 'drama_shot', 'generic'])
    expect(w.find('[data-test="script-target-note"]').text()).toContain('按播客那八条写法写')
    await w.find('[data-test="script-target"]').setValue('drama_screenplay')
    expect(w.find('[data-test="script-target-note"]').text()).toContain('按短剧台词那几条写')
    // 没标题不建
    await w.find('[data-test="script-create"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    await w.find('[data-test="script-title"]').setValue('下班后的消息，要不要回')
    await w.find('[data-test="brief-topic"]').setValue('下班后收到工作消息，回还是不回')
    await w.find('[data-test="brief-angle"]').setValue('不站队')
    await w.find('[data-test="brief-audience"]').setValue('刚工作两三年的人')
    await w.find('[data-test="brief-materials"]').setValue('阿杰上个月周六晚上十点被拉进新群')
    await w.find('[data-test="brief-tone"]').setValue('像朋友下班后吐槽')
    const avoid = w.find('[data-test="brief-avoid"]')
    await avoid.find('[data-test="brief-chip-input"]').setValue('说教、编造调查数据')
    await avoid.find('[data-test="brief-chip-add"]').trigger('click')
    expect(avoid.findAll('[data-test="brief-chip"]').length).toBe(2)
    await avoid.findAll('[data-test="brief-chip"]')[1].trigger('click')
    // 第二位没名字不建
    const speakers = w.findAll('[data-test="speaker-row"]')
    expect(speakers.length).toBe(2)
    await speakers[0].find('[data-test="speaker-name"]').setValue('阿杰')
    await speakers[0].find('[data-test="speaker-persona"]').setValue('大厂产品经理，说话慢')
    await speakers[0].find('[data-test="speaker-stance"]').setValue('边界感是必须的')
    await w.find('[data-test="script-create"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    expect(w.find('.status').text()).toContain('每个说话的人都要有名字')
    await speakers[1].find('[data-test="speaker-name"]').setValue('小林')
    // 段落骨架：默认一段，再加一段
    await w.find('[data-test="block-add"]').trigger('click')
    const blocks = w.findAll('[data-test="block-row"]')
    expect(blocks.length).toBe(2)
    expect(blocks[1].text()).toContain('B02')
    await blocks[0].find('[data-test="block-title"]').setValue('冷开场')
    await blocks[0].find('[data-test="block-goal"]').setValue('用一个具体场景把人拽进来')
    await blocks[1].find('[data-test="block-title"]').setValue('两边各讲各的')
    await w.find('[data-test="script-create"]').trigger('click'); await flushPromises()
    const body = calls.find(c => c[0] === 'post' && c[1] === '/api/script')[2]
    expect(body).toEqual({
      title: '下班后的消息，要不要回', target: 'drama_screenplay', language: 'zh', versions: [],
      brief: { topic: '下班后收到工作消息，回还是不回', angle: '不站队', audience: '刚工作两三年的人', materials: '阿杰上个月周六晚上十点被拉进新群', tone: '像朋友下班后吐槽', avoid: ['说教'] },
      speakers: [
        { id: 's1', name: '阿杰', persona: '大厂产品经理，说话慢', stance: '边界感是必须的' },
        { id: 's2', name: '小林', persona: '', stance: '' },
      ],
      blocks: [
        { id: 'B01', kind: 'talk', title: '冷开场', goal: '用一个具体场景把人拽进来', note: '' },
        { id: 'B02', kind: 'talk', title: '两边各讲各的', goal: '', note: '' },
      ],
    })
    expect(r.currentRoute.value.path).toBe('/script/p/SC-20260920-2/write')
  })
})

describe('scriptlab · 项目页', () => {
  it('外壳：进项目拉 detail，导航四项，顶部摘要 段 / 版本 / 采用 / 预计 / 已投放', async () => {
    const { w } = await mountAt(Script, `/script/p/${ID}/write`)
    expect(calls.some(c => c[0] === 'get' && c[1] === `/api/script/${ID}`)).toBe(true)
    const nav = w.findAll('.ws-nav a.nav').map(a => a.attributes('href'))
    expect(nav).toEqual([`/script/p/${ID}/write`, `/script/p/${ID}/try`, `/script/p/${ID}/compare`, `/script/p/${ID}/use`])
    const top = w.find('[data-test="script-summary"]').text()
    expect(top).toContain('段 2')
    expect(top).toContain('版本 2')
    expect(top).toContain('第 1 版')
    expect(top).toContain('已投放 1')
    expect(w.find('.ws-nav .head').text()).toContain('下班后的消息，要不要回')
    expect(w.find('.ws-nav .head').text()).toContain('播客对谈')
  })

  it('写页：逐段逐句渲染、估算、问题卡；改台词 / 加一句 → 另存为新版本 POST（空 cue 与 talk 不落盘）', async () => {
    await loadScript(ID)
    const { w } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    const blocks = w.findAll('[data-test="block"]')
    expect(blocks.length).toBe(2)
    expect(blocks.map(b => b.attributes('id'))).toEqual(['block-B01', 'block-B02'])
    expect(w.find('[data-test="write-version"]').text()).toBe('V1')
    expect(blocks[0].findAll('[data-test="line-row"]').length).toBe(1)
    expect(blocks[0].find('[data-test="line-who"]').element.value).toBe('jie')
    expect(blocks[0].find('[data-test="line-who"]').findAll('option').map(o => o.text())).toEqual(['阿杰', '小林'])
    expect(blocks[1].findAll('[data-test="line-row"]')[1].find('[data-test="line-cue"]').element.value).toBe('停顿')
    expect(w.find('[data-test="write-estimate"]').text()).toContain('44 字')
    const prob = w.find('[data-test="write-problem"]')
    expect(prob.find('[data-test="problem-target"]').text()).toBe('B02')
    expect(prob.text()).toContain('回答太短')
    expect(prob.text()).toContain('补一句理由')
    expect(prob.text()).toContain('台词')
    // 改台词 + 在第一段末尾加一句（默认换一个人说）
    await blocks[1].findAll('[data-test="line-text"]')[1].setValue('我十点以后不看手机。')
    await blocks[1].findAll('[data-test="line-cue"]')[1].setValue('')
    await blocks[0].find('[data-test="line-add"]').trigger('click')
    const added = blocks[0].findAll('[data-test="line-row"]')
    expect(added.length).toBe(2)
    expect(added[1].find('[data-test="line-who"]').element.value).toBe('lin')
    await added[1].find('[data-test="line-text"]').setValue('那你回了吗？')
    await added[1].find('[data-test="line-kind"]').setValue('question')
    await w.find('[data-test="version-label"]').setValue('硬气一点')
    calls.length = 0
    await w.find('[data-test="version-save"]').trigger('click'); await flushPromises()
    const saved = calls.find(c => c[0] === 'post' && c[1] === `/api/script/${ID}/versions`)
    expect(saved[2]).toEqual({
      label: '硬气一点', from_version: 'V1',
      blocks: [
        { id: 'B01', lines: [
          { who: 'jie', text: '周六晚上十点，我被拉进一个新群。' },
          { who: 'lin', text: '那你回了吗？', kind: 'question' },
        ] },
        { id: 'B02', lines: [
          { who: 'lin', text: '客户在等，我能不回吗？' },
          { who: 'jie', text: '我十点以后不看手机。' },
        ] },
      ],
    })
    expect(calls.some(c => c[0] === 'put')).toBe(false)   // 骨架没动就不发 PUT
    expect(calls.some(c => c[0] === 'get' && c[1] === `/api/script/${ID}`)).toBe(true)
    expect(toastMock.mock.calls.some(c => String(c[0]).includes('V3'))).toBe(true)
  })

  it('写页：改段落骨架 / 加段 / 上下移 → 「保存段落骨架」PUT 整份（去掉目录字段，不带台词）', async () => {
    await loadScript(ID)
    const { w } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    expect(w.find('[data-test="skeleton-save"]').attributes('disabled')).toBeDefined()
    await w.findAll('[data-test="block-goal"]')[1].setValue('让两个人正面撞上')
    expect(w.find('[data-test="skeleton-save"]').attributes('disabled')).toBeUndefined()
    await w.find('[data-test="block-add"]').trigger('click')
    const all = w.findAll('[data-test="block"]')
    expect(all.length).toBe(3)
    expect(all[2].text()).toContain('B03')
    await all[2].find('[data-test="block-title"]').setValue('收尾')
    await all[2].find('[data-test="block-up"]').trigger('click')
    calls.length = 0
    await w.find('[data-test="skeleton-save"]').trigger('click'); await flushPromises()
    const put = calls.find(c => c[0] === 'put' && c[1] === `/api/script/${ID}`)
    expect(put[2].title).toBe('下班后的消息，要不要回')
    expect(put[2].output_root).toBeUndefined()
    expect(put[2].project_root).toBeUndefined()
    expect(put[2].blocks).toEqual([
      { id: 'B01', kind: 'talk', title: '冷开场', goal: '用一个具体场景把人拽进来', note: '' },
      { id: 'B03', kind: 'talk', title: '收尾', goal: '', note: '' },
      { id: 'B02', kind: 'talk', title: '两边各讲各的', goal: '让两个人正面撞上', note: '' },
    ])
    expect(put[2].versions.length).toBe(2)   // 版本原样带回去，PUT 不碰台词
  })

  it('写页：删段 → 确认文案数全部版本里的句数；「保存段落骨架」的 PUT 把那一段从每一版里剔掉；没台词的段不弹确认', async () => {
    await loadScript(ID)
    const { w } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    // 删 B02：V1 / V2 里各 2 句 → 全部 2 个版本里的 4 句一起没了；不能再说「旧版本不受影响」（后端各版都按骨架对齐，做不到）
    await w.findAll('[data-test="block-remove"]')[1].trigger('click'); await flushPromises()
    const msg = String(confirmMock.mock.calls.at(-1)[0])
    expect(msg).toContain('全部 2 个版本')
    expect(msg).toContain('4 句')
    expect(msg).not.toContain('旧版本不受影响')
    expect(w.findAll('[data-test="block"]').length).toBe(1)
    calls.length = 0
    await w.find('[data-test="skeleton-save"]').trigger('click'); await flushPromises()
    const put = calls.find(c => c[0] === 'put' && c[1] === `/api/script/${ID}`)
    expect(put[2].blocks.map(b => b.id)).toEqual(['B01'])
    // 后端要求每一版的段都在骨架里：版本原样带回去必 400，所以每一版都剔掉被删的那段（别的段一句不动）
    expect(put[2].versions.map(v => v.blocks.map(b => b.id))).toEqual([['B01'], ['B01']])
    expect(put[2].versions[0].blocks[0].lines.length).toBe(1)
    // 拒绝确认就不删
    detail = sampleDetail(); clearScript(); await loadScript(ID)
    const { w: w2 } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    confirmMock.mockResolvedValueOnce(false)
    await w2.findAll('[data-test="block-remove"]')[1].trigger('click'); await flushPromises()
    expect(w2.findAll('[data-test="block"]').length).toBe(2)
    // 刚加的、哪一版都没有台词的段：直接删，不弹框
    confirmMock.mockClear()
    await w2.find('[data-test="block-add"]').trigger('click')
    await w2.findAll('[data-test="block-remove"]')[2].trigger('click'); await flushPromises()
    expect(confirmMock).not.toHaveBeenCalled()
    expect(w2.findAll('[data-test="block"]').length).toBe(2)
    // 一版都没有、句子是屏幕上刚写的：确认框说的是「刚写的 N 句（还没另存）」，不说「全部 0 个版本」
    const fresh = sampleSpec(); fresh.versions = []; fresh.current = ''
    detail = sampleDetail(fresh); clearScript(); await loadScript(ID)
    const { w: w3 } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    await w3.findAll('[data-test="block"]')[0].find('[data-test="line-add"]').trigger('click')
    await w3.findAll('[data-test="block-remove"]')[0].trigger('click'); await flushPromises()
    const fresh_msg = String(confirmMock.mock.calls.at(-1)[0])
    expect(fresh_msg).toContain('刚写的 1 句')
    expect(fresh_msg).not.toContain('0 个版本')
  })

  it('写页：删段后没保存就加一段 → 新段不复用被删的段号；保存骨架的 PUT 里哪一版都不再带被删那段的旧台词', async () => {
    await loadScript(ID)
    const { w } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    await w.findAll('[data-test="block-remove"]')[1].trigger('click'); await flushPromises()
    expect(w.findAll('[data-test="block"]').length).toBe(1)
    // 只看表单会又排出 B02：saveSkeleton 按表单里的段号过滤各版，被删 B02 的旧台词就原样挂到这个新段下
    await w.find('[data-test="block-add"]').trigger('click')
    const all = w.findAll('[data-test="block"]')
    expect(all.length).toBe(2)
    expect(all[1].text()).toContain('B03')
    expect(all[1].text()).not.toContain('B02')
    calls.length = 0
    await w.find('[data-test="skeleton-save"]').trigger('click'); await flushPromises()
    const put = calls.find(c => c[0] === 'put' && c[1] === `/api/script/${ID}`)
    expect(put[2].blocks.map(b => b.id)).toEqual(['B01', 'B03'])
    expect(put[2].versions.map(v => v.blocks.map(b => b.id))).toEqual([['B01'], ['B01']])
    const texts = put[2].versions.flatMap(v => v.blocks.flatMap(b => b.lines.map(l => l.text)))
    for (const gone of ['客户在等，我能不回吗？', '能。', '我十点以后不看手机。']) expect(texts).not.toContain(gone)
  })

  it('写页：播客稿的段能标成「听友问答」，保存时带上段种类；别的形态不显示这个下拉', async () => {
    await loadScript(ID)
    const { w } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    const kinds = w.findAll('[data-test="block-kind"]')
    expect(kinds.length).toBe(2)
    await kinds[0].setValue('qa')
    calls.length = 0
    await w.find('[data-test="skeleton-save"]').trigger('click'); await flushPromises()
    const put = calls.find(c => c[0] === 'put' && c[1] === `/api/script/${ID}`)
    expect(put[2].blocks.map(b => b.kind)).toEqual(['qa', 'talk'])
    // 短剧稿没有「听友问答」这回事：下拉不出现
    detail = sampleDetail(); detail.spec.target = 'drama_screenplay'
    clearScript(); await loadScript(ID)
    const { w: w2 } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    expect(w2.findAll('[data-test="block-kind"]').length).toBe(0)
  })

  it('写页：另存为新版本之后停在新出的那一版上（屏幕上的台词不许换回旧版）；起草在跑时两个保存都是灰的', async () => {
    await loadScript(ID)
    const { w } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    await w.findAll('[data-test="line-text"]')[0].setValue('新台词，我刚改的')
    // 后端把新的一版存在末尾，current 不动（新版本不自动采用）
    const after = sampleSpec()
    after.versions.push({
      id: 'V3', label: '', source: 'hand', model: '', backend: '', note: '', created_at: 1789001000, from_version: 'V1',
      blocks: [
        { id: 'B01', lines: [{ who: 'jie', text: '新台词，我刚改的', cue: '', kind: 'talk' }] },
        { id: 'B02', lines: clone(after.versions[0].blocks[1].lines) },
      ],
    })
    detail = sampleDetail(after)
    await w.find('[data-test="version-save"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="write-version"]').text()).toBe('V3')
    expect(w.find('[data-test="write-not-current"]').text()).toContain('还没采用')
    expect(w.findAll('[data-test="line-text"]')[0].element.value).toBe('新台词，我刚改的')
    // 在「试」页采用了别的版本 → 回到跟着 current 走
    const picked = clone(after); picked.current = 'V2'
    detail = sampleDetail(picked)
    await loadScript(ID); await flushPromises()
    expect(w.find('[data-test="write-version"]').text()).toBe('V2')
    expect(w.find('[data-test="write-not-current"]').exists()).toBe(false)
    // 起草任务在跑：PUT 整份会把它写出来的新版本抹掉，两个保存都禁掉
    const busy = sampleDetail(); busy.busy = { draft: { id: 't-draft' }, review: null }
    detail = busy
    await loadScript(ID); await flushPromises()
    await w.findAll('[data-test="block-goal"]')[1].setValue('让两个人正面撞上')
    expect(w.find('[data-test="skeleton-save"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="version-save"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="write-busy"]').text()).toContain('先等它写完')
  })

  it('写页：项目一版都没有时手写另存的第一版会被后端直接采用，toast 说「已直接采用」，不说「旧版还在，去试页挑」', async () => {
    const empty = sampleSpec(); empty.versions = []; empty.current = ''
    detail = sampleDetail(empty)
    await loadScript(ID)
    const { w } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    await w.findAll('[data-test="block"]')[0].find('[data-test="line-add"]').trigger('click')
    await w.findAll('[data-test="line-text"]')[0].setValue('第一句手写的')
    // 后端 adopt_if_first：刷新出来的 current 就是刚存的 V3
    const adopted = sampleSpec(); adopted.versions = [{ ...adopted.versions[0], id: 'V3' }]; adopted.current = 'V3'
    detail = sampleDetail(adopted)
    await w.find('[data-test="version-save"]').trigger('click'); await flushPromises()
    const done = toastMock.mock.calls.map(c => String(c[0])).find(s => s.includes('V3'))
    expect(done).toContain('已直接采用')
    expect(done).not.toContain('旧版还在')
    // 已经有采用的版本：照旧说「旧版还在，去试页挑」
    detail = sampleDetail(); clearScript(); await loadScript(ID)
    const { w: w2 } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    await w2.findAll('[data-test="line-text"]')[0].setValue('改一句')
    toastMock.mockClear()
    await w2.find('[data-test="version-save"]').trigger('click'); await flushPromises()
    expect(toastMock.mock.calls.map(c => String(c[0])).find(s => s.includes('V3'))).toContain('旧版还在')
  })

  it('写页：带 #block-B02 打开就滚到那一段；不在角色表里的说话人有兜底项；角色表空着加不了句', async () => {
    const seen = []
    const orig = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = function () { seen.push(this.id) }
    try {
      await loadScript(ID)
      const r = stubRouter(); await r.push(`/script/p/${ID}/write#block-B02`); await r.isReady()
      const w = mount(ScriptWrite, { global: { plugins: [r] }, attachTo: document.body })
      await flushPromises()
      expect(seen).toContain('block-B02')   // 挂载时就滚，不是等 hash 再变一次
      w.unmount()
    } finally {
      if (orig) Element.prototype.scrollIntoView = orig; else delete Element.prototype.scrollIntoView
    }
    // 导入带进来的说话人不在角色表里：下拉要看得见、改得掉
    const ghost = sampleSpec()
    ghost.versions[0].blocks[1].lines[0].who = 'guest'
    detail = sampleDetail(ghost)
    await loadScript(ID)
    const { w: w2 } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    const unknown = w2.findAll('[data-test="block"]')[1].find('[data-test="line-who-unknown"]')
    expect(unknown.attributes('value')).toBe('guest')
    expect(unknown.text()).toContain('不在角色表里')
    // 角色表空着：加句会造出 who 为空的句子，先禁掉并说去哪儿补人
    const nobody = sampleSpec(); nobody.speakers = []
    detail = sampleDetail(nobody)
    await loadScript(ID)
    const { w: w3 } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    expect(w3.find('[data-test="line-add"]').attributes('disabled')).toBeDefined()
    expect(w3.find('[data-test="write-no-speaker"]').text()).toContain('从现有项目导入')
  })

  it('试页：版本列表（来源 / 字数 / 告警 / 审稿结论 / 采用 / 删除）、审稿卡按目标形态选清单', async () => {
    await loadScript(ID)
    const { w } = await mountAt(ScriptTry, `/script/p/${ID}/try`)
    // 播客形态 → 真人感清单
    expect(w.find('[data-test="script-review-card"]').exists()).toBe(true)
    expect(w.find('[data-test="script-review-card-name"]').text()).toBe('真人感清单')
    expect(w.find('[data-test="script-review-card"]').text()).toContain('引文对不上的不算')
    expect(calls.some(c => c[0] === 'get' && c[1] === `/api/script/${ID}/review`)).toBe(true)
    const rows = w.findAll('[data-test="version-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].find('[data-test="version-current"]').exists()).toBe(true)
    expect(rows[1].find('[data-test="version-current"]').exists()).toBe(false)
    const meta = rows[0].find('[data-test="version-meta"]').text()
    expect(meta).toContain('模型出的')
    expect(meta).toContain('gpt-4o-mini')
    expect(meta).toContain('44 字')
    expect(meta).toContain('1 条提醒')
    expect(rows[0].find('[data-test="version-verdict"]').text()).toBe('违反 1 · 有风险 2')
    expect(rows[1].find('[data-test="version-verdict"]').text()).toBe('还没审过')
    expect(rows[1].find('[data-test="version-meta"]').text()).toContain('重写了几段')
    // 采用中的那一版不能采用也不能删
    expect(rows[0].find('[data-test="version-pick"]').attributes('disabled')).toBeDefined()
    expect(rows[0].find('[data-test="version-remove"]').attributes('disabled')).toBeDefined()
    calls.length = 0
    await rows[1].find('[data-test="version-pick"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === `/api/script/${ID}/pick`)[2]).toEqual({ version: 'V2' })
    calls.length = 0
    await rows[1].find('[data-test="version-remove"]').trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('找不回来')
    expect(calls.some(c => c[0] === 'del' && c[1] === `/api/script/${ID}/versions/V2`)).toBe(true)
    // 短剧形态 → 工艺清单
    const spec = sampleSpec(); spec.target = 'drama_screenplay'
    detail = sampleDetail(spec)
    await loadScript(ID); await flushPromises()
    expect(w.find('[data-test="script-review-card-name"]').text()).toBe('剧本工艺清单')
  })

  it('试页：项目还没采用任何一版时，「生成一版」的确认说这一版会直接采用，完成的 toast 也不说「还没采用」', async () => {
    const empty = sampleSpec(); empty.versions = []; empty.current = ''
    detail = sampleDetail(empty)
    await loadScript(ID)
    const { w } = await mountAt(ScriptTry, `/script/p/${ID}/try`)
    // 后端 adopt_if_first：任务完成后刷新出来的 current 就是新出的那一版
    const adopted = sampleSpec(); adopted.versions = [{ ...adopted.versions[0], id: 'V3' }]; adopted.current = 'V3'
    detail = sampleDetail(adopted)
    await w.find('[data-test="draft-btn"]').trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('会直接采用')
    const done = toastMock.mock.calls.map(c => String(c[0])).find(s => s.includes('V3'))
    expect(done).toContain('已直接采用')
    expect(done).not.toContain('还没采用')
    // 已经有采用的版本：文案照旧说「不会自动采用」，toast 说「还没采用」
    detail = sampleDetail(); clearScript(); await loadScript(ID)
    const { w: w2 } = await mountAt(ScriptTry, `/script/p/${ID}/try`)
    toastMock.mockClear()
    await w2.find('[data-test="draft-btn"]').trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('不会自动采用')
    expect(toastMock.mock.calls.map(c => String(c[0])).find(s => s.includes('V3'))).toContain('还没采用')
  })

  it('试页：「生成一版」确认后 POST generate 并轮询；「只重写这几段」带 blocks 与 from_version；拒绝确认就不发；跑检查只重拉详情', async () => {
    await loadScript(ID)
    const { w } = await mountAt(ScriptTry, `/script/p/${ID}/try`)
    await w.find('[data-test="draft-note"]').setValue('开头先说一个周六晚上的具体场景')
    calls.length = 0
    await w.find('[data-test="draft-btn"]').trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('1 次文本模型请求')
    expect(confirmMock.mock.calls.at(-1)[1]).toMatchObject({ localRole: 'text' })
    expect(calls.find(c => c[0] === 'post' && c[1] === `/api/script/${ID}/generate`)[2]).toEqual({ note: '开头先说一个周六晚上的具体场景' })
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-draft')).toBe(true)
    expect(toastMock.mock.calls.some(c => String(c[0]).includes('1 次文本模型请求'))).toBe(true)
    expect(store.gen).toBeNull()
    // 勾选之前重写按钮是灰的
    expect(w.find('[data-test="redraft-btn"]').attributes('disabled')).toBeDefined()
    expect(w.findAll('[data-test="redraft-block"]').length).toBe(2)
    await w.find('[data-test="redraft-check-B02"]').setValue(true)
    await w.find('[data-test="redraft-note"]').setValue('分歧再明显一点')
    expect(w.find('[data-test="redraft-btn"]').text()).toContain('1 段')
    calls.length = 0
    await w.find('[data-test="redraft-btn"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === `/api/script/${ID}/generate`)[2]).toEqual({ blocks: ['B02'], note: '分歧再明显一点', from_version: 'V1' })
    // 拒绝确认就不发
    calls.length = 0
    confirmMock.mockResolvedValueOnce(false)
    await w.find('[data-test="draft-btn"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    // 跑检查：只重拉详情，不发 POST
    calls.length = 0
    await w.find('[data-test="lint-btn"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    expect(calls.some(c => c[0] === 'get' && c[1] === `/api/script/${ID}`)).toBe(true)
    expect(w.find('[data-test="lint-status"]').text()).toContain('1 条提醒')
    // 界面上摆的是问题卡（段号 + 影响 + 改法），不是带 block B02: 前缀的原始告警字符串
    const prob = w.find('[data-test="try-problem"]')
    expect(prob.find('[data-test="try-problem-target"]').text()).toBe('B02')
    expect(prob.text()).toContain('回答太短')
    expect(prob.text()).toContain('补一句理由')
    expect(w.find('[data-test="try-problems"]').text()).not.toContain('block B02')
    // 段号链回「写」页的那一段
    expect(prob.find('[data-test="try-problem-target"]').attributes('href')).toBe(`/script/p/${ID}/write#block-B02`)
  })

  it('比页：默认摆采用中的与最新的一版，逐段逐行并排，相同行淡显 / 不同行高亮；「用右边这版」采用它', async () => {
    await loadScript(ID)
    const { w } = await mountAt(ScriptCompare, `/script/p/${ID}/compare`)
    expect(w.find('[data-test="compare-left"]').element.value).toBe('V1')
    expect(w.find('[data-test="compare-right"]').element.value).toBe('V2')
    expect(w.find('[data-test="compare-left-meta"]').text()).toContain('44 字')
    expect(w.find('[data-test="compare-right-meta"]').text()).toContain('52 字')
    const blocks = w.findAll('[data-test="compare-block"]')
    expect(blocks.length).toBe(2)
    const first = blocks[0].findAll('[data-test="compare-row"]')
    expect(first.length).toBe(1)
    expect(first[0].classes()).toContain('same')
    const second = blocks[1].findAll('[data-test="compare-row"]')
    expect(second.length).toBe(2)
    expect(second[0].classes()).toContain('same')
    expect(second[1].classes()).toContain('diff')
    expect(second[1].find('[data-test="compare-cell-left"]').text()).toBe('阿杰：能。（停顿）')
    expect(second[1].find('[data-test="compare-cell-right"]').text()).toBe('阿杰：我十点以后不看手机。')
    expect(w.find('[data-test="compare-count"]').text()).toContain('1 行不一样')
    expect(blocks[1].find('[data-test="compare-block-count"]').text()).toContain('1 行不一样')
    // 左边就是采用中的那一版，不用再采用
    expect(w.find('[data-test="pick-left"]').attributes('disabled')).toBeDefined()
    calls.length = 0
    await w.find('[data-test="pick-right"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === `/api/script/${ID}/pick`)[2]).toEqual({ version: 'V2' })
  })

  it('用页：落点下拉（环节 + 新建）、角色映射按顺序预填、POST use 的 body；分镜落点遇到已锁定的剧集拦住', async () => {
    await loadScript(ID)
    const { w } = await mountAt(ScriptUse, `/script/p/${ID}/use`)
    expect(w.find('[data-test="use-module"]').element.value).toBe('podcast')
    expect(w.find('[data-test="use-version"]').text()).toBe('V1')
    expect(w.find('[data-test="use-history-row"]').text()).toContain('PC-1')
    await w.find('[data-test="use-target"]').setValue('PC-1'); await flushPromises()
    const spot = w.find('[data-test="use-spot-B01"]')
    expect(spot.findAll('option').map(o => o.attributes('value'))).toEqual(['', 'new', 'G01', 'G02'])
    expect(spot.findAll('option')[2].text()).toContain('已有 2 句')
    await spot.setValue('G01')
    await w.find('[data-test="use-spot-B02"]').setValue('new')
    // 角色映射：名字对不上就按顺序预填
    expect(w.find('[data-test="use-map-jie"]').element.value).toBe('zhou')
    expect(w.find('[data-test="use-map-lin"]').element.value).toBe('xiao')
    expect(w.findAll('[data-test="use-map-jie"] option').map(o => o.attributes('value'))).toEqual(['', 'zhou', 'xiao'])
    calls.length = 0
    await w.find('[data-test="use-submit"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === `/api/script/${ID}/use`)[2]).toEqual({
      module: 'podcast', target_id: 'PC-1', blocks: { B01: 'G01', B02: 'new' }, mapping: { jie: 'zhou', lin: 'xiao' }, mode: 'replace',
    })
    expect(toastMock.mock.calls.some(c => String(c[0]).includes('已放进'))).toBe(true)
    // 缺一个映射就投不了
    await w.find('[data-test="use-map-lin"]').setValue('')
    expect(w.find('[data-test="use-submit"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="use-missing"]').text()).toContain('小林')
    // 短剧剧本：落点是场次，映射到人物；同名的自动对上
    await w.find('[data-test="use-module"]').setValue('drama_screenplay'); await flushPromises()
    await w.find('[data-test="use-target"]').setValue('EP-1'); await flushPromises()
    // 短剧也要选「怎么写进去」：后端三种去处都支持 replace / append，以前只给播客选、短剧就静默整段覆盖了目标原有的台词
    expect(w.find('[data-test="use-mode-field"]').exists()).toBe(true)
    expect(w.findAll('[data-test="use-spot-B01"] option').map(o => o.attributes('value'))).toEqual(['', 'new', 'S1', 'S2'])
    expect(w.findAll('[data-test="use-spot-B01"] option')[2].text()).toContain('已有 3 句，会被整段替换')
    await w.find('[data-test="use-mode"]').setValue('append')
    expect(w.findAll('[data-test="use-spot-B01"] option')[2].text()).toContain('已有 3 句，会追加在后面')
    expect(w.find('[data-test="use-spot-B01"]').text()).not.toContain('按上面的方式')
    expect(w.find('[data-test="use-map-jie"]').element.value).toBe('c2')   // 「阿杰」同名对上
    await w.find('[data-test="use-spot-B01"]').setValue('S1')
    calls.length = 0
    await w.find('[data-test="use-submit"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === `/api/script/${ID}/use`)[2]).toMatchObject({ module: 'drama_screenplay', target_id: 'EP-1', mode: 'append' })
    // 分镜：没有「新建」，而且剧集已锁定 → 拦住
    await w.find('[data-test="use-module"]').setValue('drama_shot'); await flushPromises()
    await w.find('[data-test="use-target"]').setValue('EP-1'); await flushPromises()
    expect(w.findAll('[data-test="use-spot-B01"] option').map(o => o.attributes('value'))).toEqual(['', 'S1-01'])
    expect(w.find('[data-test="use-locked"]').text()).toContain('已经开始制作')
    await w.find('[data-test="use-spot-B01"]').setValue('S1-01')
    expect(w.find('[data-test="use-submit"]').attributes('disabled')).toBeDefined()
  })

  it('用页：还没采用任何一版就不许投——版本按 current 严格取，不回落到最后一版', async () => {
    const spec = sampleSpec(); spec.current = ''     // 有版本但没采用（整体保存时 current 被清空了）
    detail = sampleDetail(spec)
    await loadScript(ID)
    const { w } = await mountAt(ScriptUse, `/script/p/${ID}/use`)
    expect(w.find('[data-test="use-version"]').exists()).toBe(false)
    expect(w.find('[data-test="use-no-version"]').text()).toContain('还没采用任何一版')
    await w.find('[data-test="use-target"]').setValue('PC-1'); await flushPromises()
    await w.find('[data-test="use-spot-B01"]').setValue('G01')
    // 落点选了、角色也自动对上了，仍然不许投（后端投的是空的 current）
    expect(w.find('[data-test="use-map-jie"]').element.value).toBe('zhou')
    expect(w.find('[data-test="use-submit"]').attributes('disabled')).toBeDefined()
    // 句数也跟着 current 走，不显示回落版本的句数
    expect(w.findAll('[data-test="use-block-row"]')[0].text()).toContain('0 句')
  })

  it('比页：一版都没有时只出「还没有任何一版可比」，不按骨架铺出一串空段卡', async () => {
    const spec = sampleSpec(); spec.versions = []; spec.current = ''
    detail = sampleDetail(spec)
    await loadScript(ID)
    const { w } = await mountAt(ScriptCompare, `/script/p/${ID}/compare`)
    expect(w.find('[data-test="compare-empty"]').exists()).toBe(true)
    expect(w.findAll('[data-test="compare-block"]').length).toBe(0)
  })

  it('问题卡分类：复用短剧判据落到 screenplay 也显示中文，不把内部键名露到界面上', async () => {
    const spec = sampleSpec(); spec.target = 'drama_screenplay'
    detail = sampleDetail(spec)
    detail.problems = [{ scope: 'block', target: 'B01', kind: 'screenplay', impact: '剧本里有 4 个人物开口说话', fix: '合并两个人' }]
    await loadScript(ID)
    const { w } = await mountAt(ScriptWrite, `/script/p/${ID}/write`)
    const prob = w.find('[data-test="write-problem"]')
    expect(prob.text()).toContain('剧本')
    expect(w.find('[data-test="write-problems"]').text()).not.toContain('screenplay')
    const { w: w2 } = await mountAt(ScriptTry, `/script/p/${ID}/try`)
    expect(w2.find('[data-test="try-problem"]').text()).toContain('剧本')
    expect(w2.find('[data-test="try-problems"]').text()).not.toContain('screenplay')
  })

  it('用页：换「投到哪儿」把已选的项目一起清掉（上一个模块的项目 id 在新清单里没有）', async () => {
    await loadScript(ID)
    const { w } = await mountAt(ScriptUse, `/script/p/${ID}/use`)
    await w.find('[data-test="use-target"]').setValue('PC-1'); await flushPromises()
    expect(w.find('[data-test="use-blocks"]').exists()).toBe(true)
    await w.find('[data-test="use-module"]').setValue('drama_screenplay'); await flushPromises()
    expect(w.find('[data-test="use-target"]').element.value).toBe('')
    expect(w.find('[data-test="use-blocks"]').exists()).toBe(false)
  })
})

describe('scriptlab · 侧栏、任务中心、路由与 helpers', () => {
  it('模块可用时侧栏多一块：列表指向 /script/p/…/write；删除 ✕ 归档；不可用时没有', async () => {
    store.scripts = list; store.scriptsLoaded = true
    const { w, r } = await mountAt(Sidebar, '/')
    const block = w.find('[data-test="sidebar-module-script"]')
    expect(block.exists()).toBe(true)
    expect(block.find('[data-test="module-header"]').text()).toContain('剧本工作台')
    expect(block.find('[data-test="module-header"]').text()).toContain('2')
    expect(block.findAll('a')[0].attributes('href')).toBe('/script')
    expect(block.html()).not.toContain('/script/new')
    await block.find('[data-test="module-header"]').trigger('click')
    const rows = w.findAll('[data-test="script-list"] .proj')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe(`/script/p/${ID}/write`)
    expect(rows[0].text()).toContain('2 版')
    expect(rows[0].text()).toContain('采用第 1 版')
    expect(rows[1].text()).toContain('还没起草')
    expect(block.find('[data-test="script-search"]').exists()).toBe(false)
    await w.findAll('[data-test="script-list"] .del')[0].trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('「回收站」里恢复')
    expect(calls.some(c => c[0] === 'del' && c[1] === `/api/script/${ID}`)).toBe(true)
    expect(r.currentRoute.value.path).toBe('/')
    store.modules = store.modules.filter(m => m.id !== 'script')
    await w.vm.$nextTick()
    expect(w.find('[data-test="sidebar-module-script"]').exists()).toBe(false)
  })

  it('任务中心：种类名与项目地址', () => {
    expect(KIND_LABELS['sc-draft']).toBe('台词起草')
    expect(KIND_LABELS['sc-review']).toBe('台词审稿')
    expect(PROJECT_ROUTES.script('SC-1')).toBe('/script/p/SC-1/write')
    expect(taskTarget({ kind: 'sc-draft', module: 'script', episode_id: 'SC-1' })).toEqual({ id: 'SC-1', url: '/script/p/SC-1/write', kind: 'project' })
  })

  it('路由：/script 下的路径直接命中；/script/p/:id 落到 write', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/'); await r.isReady()
    for (const [from, to] of [
      ['/script', '/script'], ['/script/new', '/script/new'],
      ['/script/p/SC-1', '/script/p/SC-1/write'], ['/script/p/SC-1/write', '/script/p/SC-1/write'],
      ['/script/p/SC-1/try', '/script/p/SC-1/try'], ['/script/p/SC-1/compare', '/script/p/SC-1/compare'],
      ['/script/p/SC-1/use', '/script/p/SC-1/use'],
    ]) {
      await r.push(from)
      expect(r.currentRoute.value.path, from).toBe(to)
      expect(r.currentRoute.value.matched.length, from).toBeGreaterThan(0)
    }
    expect(r.currentRoute.value.params.id).toBe('SC-1')
  })

  it('helpers：进度形状兜底、段编号、版本序号、逐行差异、摘要文字、地址拼接', () => {
    expect(progressOf(list[0])).toEqual({ speakers: 2, blocks: 2, versions: 2, current: 'V1', currentIndex: 1, minutes: 0.2, used: 1 })
    expect(progressOf(sampleDetail())).toMatchObject({ speakers: 2, blocks: 2, versions: 2, current: 'V1', currentIndex: 1 })
    expect(progressOf(null)).toEqual({ speakers: 0, blocks: 0, versions: 0, current: '', currentIndex: 0, minutes: 0, used: 0 })
    expect(progressText(list[0])).toBe('2 版 · 采用第 1 版 · 已投放 1 处')
    expect(progressText(list[1])).toBe('还没起草 · 1 段')
    expect(nextBlockId([{ id: 'B01' }, { id: 'B07' }, { id: 'x' }])).toBe('B08')
    expect(nextBlockId([])).toBe('B01')
    expect(versionIndex('V3', [{ id: 'V1' }, { id: 'V3' }])).toBe(2)
    expect(versionIndex('V3', 5)).toBe(3)
    expect(versionIndex('', [])).toBe(0)
    expect(emptyBrief()).toEqual({ topic: '', angle: '', audience: '', materials: '', tone: '', avoid: [] })
    expect(estimateText({ chars: 44, lines: 3, minutes: 0.16 })).toBe('预计 0.2 分钟（44 字，3 句）')
    expect(verdictText(null)).toBe('')
    expect(verdictText({ verdicts: { violated: 0, risk: 1 }, stale: true })).toBe('违反 0 · 有风险 1 · 已过时')
    expect(sourceLabel('hand')).toBe('手改另存')
    // 逐行差异：同一段按行号对齐，全等的行 same
    const spec = sampleSpec()
    const diff = compareVersions(spec.versions[0], spec.versions[1], spec.blocks)
    expect(diff.map(b => b.id)).toEqual(['B01', 'B02'])
    expect(diff[0].changed).toBe(0)
    expect(diff[1].changed).toBe(1)
    expect(diff[1].rows[1].same).toBe(false)
    // 两边都没有版本：没什么可比的，不按骨架铺出一串空段卡
    expect(compareVersions(null, null, spec.blocks)).toEqual([])
    // 一边比另一边长：多出来的行算不同，另一边是空
    const short = { blocks: [{ id: 'B01', lines: [] }] }
    const one = compareVersions(short, spec.versions[0], spec.blocks)
    expect(one[0].rows.length).toBe(1)
    expect(one[0].rows[0].left).toBeNull()
    expect(one[0].rows[0].same).toBe(false)
    expect(lineText({ who: 'jie', text: '能。', cue: '停顿' }, spec.speakers)).toBe('阿杰：能。（停顿）')
    expect(lineText(null)).toBe('')
    expect(scriptRoute('SC-1')).toBe('/script/p/SC-1/write')
    expect(scriptRoute('SC-1', 'use')).toBe('/script/p/SC-1/use')
    expect(specBody({ id: 'x', output_root: 'o', project_root: 'p', title: 't' })).toEqual({ id: 'x', title: 't' })
  })

  it('常量不另起一份：cue 与台词种类就是播客那两份，审稿清单组名全前端一份', async () => {
    const podcast = await import('../src/podcast.js')
    const scriptlab = await import('../src/scriptlab.js')
    expect(scriptlab.CUES).toBe(podcast.CUES)
    expect(scriptlab.LINE_KINDS).toBe(podcast.LINE_KINDS)
    const checklists = await import('../src/checklists.js')
    expect(Object.keys(checklists.HUMANLIKE_GROUPS)).toEqual(['speech', 'dynamics', 'substance'])
    expect(Object.keys(checklists.CRAFT_GROUPS)).toEqual(['tension', 'emotion', 'rhythm', 'compliance'])
    // 问题卡分类名也是共用的一份（镜像 webui/problems.py），表里没有的落到「其他」，不回落成英文键名
    const problems = await import('../src/problems.js')
    expect(problems.problemKind('screenplay')).toBe('剧本')
    expect(problems.problemKind('dialogue')).toBe('台词')
    expect(problems.problemKind('zzz')).toBe('其他')
    expect(problems.problemKind(undefined)).toBe('其他')
  })
})
