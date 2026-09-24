// 单条视频模块前端（docs/clip-spec.md §6）：首页列表与复制示例、新建、写法页三段式编辑器（节拍增删排序 + 时间条 +
// 实时行预览 + 保存后刷新提示词与告警）、出片页（首帧三种模式、确认花钱、历次片子与采用）、高级页、侧栏与路由。
// api 全部 mock，只认契约里的接口形状。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let clips, detail, putResult, taskResult
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/clip') return clips
    if (url === '/api/style-cards') return { categories: [], cards: [] }
    if (url.startsWith('/api/tasks/')) return taskResult
    if (url.startsWith('/api/clip/')) return detail
    return {}
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    if (url === '/api/clip') return { id: 'CL-20260910-2', spec: { id: 'CL-20260910-2', ...body } }
    if (url.endsWith('/examples/create')) return { id: 'CL-20260910-2', example: body.name }
    if (url.endsWith('/pick')) return detail
    if (url.endsWith('/first-frame') && body.mode !== 'generate') return detail
    return { task_id: 't-1', cost_note: '1 次视频请求（@video，30 秒）' }
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return putResult }),
  del: vi.fn(async (url) => { calls.push(['del', url]); return { archived_to: '.webui_deleted/clip/x' } }),
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
import { loadClip, timeline, moveBeat, removeBeat, previewLine, toCards, progressText } from '../src/clip.js'
import ClipHome from '../src/views/clip/ClipHome.vue'
import ClipNew from '../src/views/clip/ClipNew.vue'
import Clip from '../src/views/clip/Clip.vue'
import ClipWrite from '../src/views/clip/ClipWrite.vue'
import ClipRender from '../src/views/clip/ClipRender.vue'
import ClipAdvanced from '../src/views/clip/ClipAdvanced.vue'
import Sidebar from '../src/components/Sidebar.vue'

function stubRouter() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div/>' } }, { path: '/settings', component: { template: '<div/>' } }, { path: '/voices', component: { template: '<div/>' } },
    { path: '/drama', component: { template: '<div/>' } }, { path: '/drama/new', component: { template: '<div/>' } }, { path: '/drama/libraries', component: { template: '<div/>' } },
    { path: '/drama/p/:id/overview', component: { template: '<div/>' } },
    { path: '/clip', component: { template: '<div/>' } }, { path: '/clip/new', component: { template: '<div/>' } },
    { path: '/clip/c/:id/:sub', component: { template: '<div/>' } },
  ] })
}
async function mountAt(Comp, path = '/') {
  const r = stubRouter(); await r.push(path); await r.isReady()
  const w = mount(Comp, { global: { plugins: [r] } })
  await flushPromises()
  return { w, r }
}

// 项目文件（契约 §1）
function sampleSpec() {
  return {
    id: 'CL-20260910-1', module: 'clip', title: '城市清晨短片', prompt_language: 'zh', style: 'custom',
    style_anchor: '清冷写实纪录片风', dialect: 'auto',
    global: { look: '安静的城市晨光', palette: ['雾蓝', '米白', '砖红', '青灰', '暖金', '旧铜'],
      light: '日出前的低光', aspect_ratio: '16:9', safe_area: '主体留在画面中间', text_policy: '只出现片头一行标题', audio: '只有环境声' },
    beats: [
      { id: 'B01', start: 0, end: 1.5, shot: '远景俯拍', picture: '晨雾里的城市天际线', sound: '远处的电车铃声', text: 'GOOD MORNING' },
      { id: 'B02', start: 1.5, end: 3, shot: '近景', picture: '掠过早点摊的蒸汽', sound: '锅铲轻响', text: '' },
    ],
    continuity: { one_take: true, transitions: '借路过的电车挡一下镜头', last_frame: '停在第一班电车进站的画面上', forbid: ['路人对着镜头招手'] },
    video_params: { model: '@video', duration: 3, resolution: '1080p', generate_audio: false },
    image_params: { model: '@image', image_size: '2K' },
    first_frame: { mode: 'none', asset: null, prompt: '', error: null },
    attempts: [], current: null,
  }
}
// 后端 detail 外壳（routers/clip.py::detail）
function sampleDetail(spec = sampleSpec(), { attempts = [], current = null, warnings = [], frame = null } = {}) {
  const id = spec.id
  return {
    id, module: 'clip', title: spec.title, spec: { ...spec, attempts, current },
    prompt: '【全片设定】视频时长 3 秒，1080p。清冷写实纪录片风。\n【分段时间线】0–1.5 秒：远景俯拍，晨雾里的城市天际线。\n【衔接与收尾】一镜到底：摄影机从第一秒拍到最后一秒，中间没有剪辑点。',
    frame_prompt: '【全片设定】单帧静止画面（这条视频的首帧）。',
    sections: { global: '【全片设定】…', timeline: '【分段时间线】…', continuity: '【衔接与收尾】…' },
    prompt_language: 'zh', dialect: 'generic', aspect_ratio: '16:9', palette: spec.global.palette, style_anchor: spec.style_anchor,
    beats: spec.beats.map(b => ({ ...b, line: `${b.start}–${b.end} 秒：${b.shot}，${b.picture}。` })),
    warnings, notes: [], problems: warnings.map(w => ({ scope: 'episode', target: '', kind: 'clip', impact: w.split('——')[0], fix: w.split('——')[1] || '', text: w })),
    stats: { beats: spec.beats.length, covered: 3, duration: spec.video_params.duration, palette: 6, dialect: 'generic', prompt_chars: 120 },
    status: { done: !!current, file: current, attempts: attempts.length, stale: false, request_hash: 'h', inflight: null, error: null,
      first_frame: frame, status: current ? 'done' : 'todo' },
    progress: { done: !!current, attempts: attempts.length, beats: spec.beats.length, duration: spec.video_params.duration, status: current ? 'done' : 'todo' },
    attempts: attempts.map(a => ({ ...a, url: a.error ? null : `/api/clip/${id}/artifact/video/${a.file}`, current: a.file === current, exists: !a.error })),
    current_url: current ? `/api/clip/${id}/artifact/video/${current}` : null,
    first_frame: { ...spec.first_frame, url: frame ? `/api/clip/${id}/artifact/frame/${frame}` : null },
    cost_note: '1 次视频请求（@video，3 秒）；请求没变且片子已在的会跳过',
    busy: { generate: null, frame: null },
  }
}

beforeEach(() => {
  calls.length = 0
  confirmMock.mockReset(); confirmMock.mockResolvedValue(true)
  clips = [
    { id: 'CL-20260910-1', status: 'ok', title: '城市清晨短片', aspect_ratio: '16:9', duration: 30, beats: 10,
      progress: { done: false, attempts: 0, beats: 10, duration: 30, status: 'todo' }, video_url: null },
    { id: 'CL-20260910-2', status: 'ok', title: '产品介绍', aspect_ratio: '9:16', duration: 15, beats: 5,
      progress: { done: true, attempts: 2, beats: 5, duration: 15, status: 'done' }, video_url: '/api/clip/CL-20260910-2/artifact/video/CL-20260910-2-2.mp4' },
  ]
  detail = sampleDetail()
  putResult = sampleDetail(sampleSpec(), { warnings: ['全片：【衔接与收尾】没写最后一帧——写清片子最后停在什么画面上'] })
  taskResult = { status: 'succeeded', result: {} }
  store.clips = []; store.clipsLoaded = false; store.clip = null; store.clipDetail = null; store.clipId = null; store.gen = null
  store.modules = [
    { id: 'drama', name: '短剧', route: '/drama', status: 'ready', available: true, project_count: 1 },
    { id: 'clip', name: '单条视频', english: 'Single clip', icon: '🎞', route: '/clip', status: 'ready', available: true, project_count: 2 },
  ]
  store.modulesLoaded = true
  store.episodes = []; store.episodesLoaded = true
  store.health = { roles: { video: { configured: true } } }
})

describe('clip · 首页与新建', () => {
  it('列表：标题、画幅、时长、拍数、进度；「复制示例」POST 示例并进片子', async () => {
    const { w, r } = await mountAt(ClipHome, '/clip')
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/clip')).toBe(true)
    const rows = w.findAll('[data-test="clip-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe('/clip/c/CL-20260910-1/write')
    expect(rows[0].text()).toContain('城市清晨短片')
    expect(rows[0].text()).toContain('16:9')
    expect(rows[0].text()).toContain('30 秒')
    expect(rows[0].text()).toContain('10 拍')
    expect(rows[0].text()).toContain('还没出片')
    expect(rows[1].text()).toContain('已有成片')
    expect(rows[1].text()).toContain('出过 2 次')
    expect(rows[1].find('video').attributes('src')).toContain('/artifact/video/')
    await w.find('[data-test="clip-example"]').trigger('click')
    await flushPromises()
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/clip/examples/create')
    expect(created[2]).toEqual({ name: 'city_morning' })
    expect(r.currentRoute.value.path).toBe('/clip/c/CL-20260910-2/write')
  })

  it('新建：标题必填、色板计数、画幅与时长进 global / video_params，建完进写法页', async () => {
    const { w, r } = await mountAt(ClipNew, '/clip/new')
    await w.find('[data-test="clip-create"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)          // 没名字不提交
    await w.find('[data-test="clip-title"]').setValue('城市清晨短片')
    await w.find('[data-test="clip-anchor"]').setValue('清冷写实纪录片风')
    await w.find('[data-test="clip-palette"]').setValue('雾蓝、米白、砖红')
    expect(w.find('[data-test="clip-palette-hint"]').text()).toContain('3')
    await w.find('[data-test="clip-aspect"]').setValue('9:16')
    await w.find('[data-test="clip-duration"]').setValue('30')
    await w.find('[data-test="clip-create"]').trigger('click'); await flushPromises()
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/clip')
    expect(created[2].title).toBe('城市清晨短片')
    expect(created[2].style_anchor).toBe('清冷写实纪录片风')
    expect(created[2].global).toMatchObject({ aspect_ratio: '9:16', palette: ['雾蓝', '米白', '砖红'] })
    expect(created[2].video_params).toMatchObject({ duration: 30, model: '@video' })
    expect(created[2].beats).toEqual([])
    expect(r.currentRoute.value.path).toBe('/clip/c/CL-20260910-2/write')
  })
})

describe('clip · 写法页', () => {
  it('外壳：进页拉 detail，导航三项，顶部节拍 / 覆盖 / 出片状态', async () => {
    const { w } = await mountAt(Clip, '/clip/c/CL-20260910-1/write')
    expect(calls.filter(c => c[0] === 'get').map(c => c[1])).toContain('/api/clip/CL-20260910-1')
    const nav = w.findAll('.ws-nav a.nav').map(a => a.attributes('href'))
    expect(nav).toEqual(['write', 'render', 'advanced'].map(s => `/clip/c/CL-20260910-1/${s}`))
    expect(w.find('[data-test="clip-progress"]').text()).toContain('2')
    expect(w.find('[data-test="clip-progress"]').text()).toContain('3/3s')
  })

  it('三段式编辑器：全片设定表单 + 节拍表（时间条 / 增删排序 / 实时行预览）+ 衔接与收尾表单；保存 PUT 整份', async () => {
    await loadClip('CL-20260910-1')
    const { w } = await mountAt(ClipWrite, '/clip/c/CL-20260910-1/write')
    expect(w.find('[data-test="write-global"]').exists()).toBe(true)
    expect(w.find('[data-test="write-continuity"]').exists()).toBe(true)
    expect(w.find('[data-test="write-palette"]').element.value).toBe('雾蓝、米白、砖红、青灰、暖金、旧铜')
    expect(w.find('[data-test="write-palette-count"]').text()).toContain('6')
    expect(w.find('[data-test="write-covered"]').text()).toContain('3/3')
    expect(w.find('[data-test="write-forbid"]').element.value).toBe('路人对着镜头招手')
    // 时间条：一拍一段
    expect(w.findAll('[data-test="beat-bar"] span[data-test^="bar-"]').length).toBe(2)
    // 节拍行 + 实时行预览
    let rows = w.findAll('[data-test="beat-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].find('[data-test="beat-line"]').text()).toContain('0–1.5 秒：远景俯拍，晨雾里的城市天际线；声音：远处的电车铃声；画面文字："GOOD MORNING"。')
    expect(w.find('[data-test="write-save"]').attributes('disabled')).toBeDefined()
    await rows[0].find('[data-test="beat-picture"]').setValue('晨雾里的天际线与铁桥同框')
    expect(rows[0].find('[data-test="beat-line"]').text()).toContain('铁桥')   // 实时更新
    expect(w.find('[data-test="write-save"]').attributes('disabled')).toBeUndefined()
    expect(w.find('[data-test="preview-stale"]').exists()).toBe(true)
    // 加一拍、上移、删除
    await w.find('[data-test="beat-add"]').trigger('click')
    rows = w.findAll('[data-test="beat-row"]')
    expect(rows.length).toBe(3)
    expect(rows[2].find('[data-test="beat-start"]').element.value).toBe('3')
    await rows[1].find('[data-test="beat-up"]').trigger('click')
    expect(w.findAll('[data-test="beat-row"]')[0].find('[data-test="beat-picture"]').element.value).toBe('掠过早点摊的蒸汽')
    await w.findAll('[data-test="beat-row"]')[2].find('[data-test="beat-remove"]').trigger('click')
    expect(w.findAll('[data-test="beat-row"]').length).toBe(2)
    // 保存：PUT 整份项目文件，色板与禁止清单拆回数组
    await w.find('[data-test="write-save"]').trigger('click'); await flushPromises()
    const saved = calls.find(c => c[0] === 'put' && c[1] === '/api/clip/CL-20260910-1')
    expect(saved[2].global.palette).toEqual(['雾蓝', '米白', '砖红', '青灰', '暖金', '旧铜'])
    expect(saved[2].global.paletteText).toBeUndefined()
    expect(saved[2].continuity.forbid).toEqual(['路人对着镜头招手'])
    expect(saved[2].beats.map(b => b.id)).toEqual(['B01', 'B02'])
    expect(saved[2].beats[0].picture).toBe('掠过早点摊的蒸汽')
    expect(saved[2].output_root).toBeUndefined()
    // 保存返回的告警顶掉旧的
    expect(w.find('[data-test="clip-lint-card"]').text()).toContain('没写最后一帧')
    expect(w.find('[data-test="preview-stale"]').exists()).toBe(false)
  })

  it('提示词预览与检查：预览是后端编译的整条，节拍告警挂在自己那一拍下面', async () => {
    detail = sampleDetail(sampleSpec(), { warnings: ['beat B02: 画面里有连接词「然后」——一个节拍只做一件事'] })
    detail.problems = [{ scope: 'beat', target: 'B02', kind: 'clip', impact: '画面里有连接词「然后」', fix: '一个节拍只做一件事', text: 'beat B02: 画面里有连接词「然后」——一个节拍只做一件事' }]
    await loadClip('CL-20260910-1')
    const { w } = await mountAt(ClipWrite, '/clip/c/CL-20260910-1/write')
    expect(w.find('[data-test="preview-text"]').text()).toContain('【全片设定】')
    expect(w.find('[data-test="preview-text"]').text()).toContain('【分段时间线】')
    const rows = w.findAll('[data-test="beat-row"]')
    expect(rows[0].find('[data-test="beat-lint"]').exists()).toBe(false)
    expect(rows[1].find('[data-test="beat-lint"]').text()).toContain('连接词')
    expect(w.find('[data-test="clip-lint-card"]').exists()).toBe(false)
  })

  it('保存报错就地显示', async () => {
    await loadClip('CL-20260910-1')
    const { put } = await import('../src/api.js')
    put.mockImplementationOnce(async () => { throw new Error('beat B02 的 end 要大于 start') })
    const { w } = await mountAt(ClipWrite, '/clip/c/CL-20260910-1/write')
    await w.findAll('[data-test="beat-row"]')[1].find('[data-test="beat-picture"]').setValue('x')
    await w.find('[data-test="write-save"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="write-error"]').text()).toContain('end 要大于 start')
  })
})

describe('clip · 出片页', () => {
  it('出片：确认花钱 → POST generate → 轮询；cost_note 摆出来', async () => {
    await loadClip('CL-20260910-1')
    const { w } = await mountAt(ClipRender, '/clip/c/CL-20260910-1/render')
    expect(w.find('[data-test="render-cost"]').text()).toContain('1 次视频请求')
    expect(w.find('[data-test="take-none"]').exists()).toBe(true)
    await w.find('[data-test="render-go"]').trigger('click'); await flushPromises()
    expect(confirmMock).toHaveBeenCalled()
    const gen = calls.find(c => c[0] === 'post' && c[1] === '/api/clip/CL-20260910-1/generate')
    expect(gen[2]).toEqual({ force: false })
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-1')).toBe(true)
    expect(store.gen).toBeNull()
  })

  it('不确认就不提交；「再出一条」带 force', async () => {
    detail = sampleDetail(sampleSpec(), { attempts: [{ file: 'CL-20260910-1-1.mp4', model: '@video', created_at: 1757500000, error: null }], current: 'CL-20260910-1-1.mp4' })
    await loadClip('CL-20260910-1')
    const { w } = await mountAt(ClipRender, '/clip/c/CL-20260910-1/render')
    confirmMock.mockResolvedValueOnce(false)
    await w.find('[data-test="render-go"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    await w.find('[data-test="render-force"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/generate'))[2]).toEqual({ force: true })
  })

  it('历次片子：播放器 + 采用 + 下载；失败那条显示原因，在飞那条说还在等', async () => {
    detail = sampleDetail(sampleSpec(), {
      attempts: [
        { file: 'CL-20260910-1-1.mp4', model: '@video', created_at: 1757500000, error: null },
        { file: 'CL-20260910-1-2.mp4', model: '@video', created_at: 1757500900, error: '余额不足' },
        { file: 'CL-20260910-1-3.mp4', model: '@video', created_at: 1757501900, error: null },
      ],
      current: 'CL-20260910-1-3.mp4',
    })
    detail.attempts[2] = { ...detail.attempts[2], url: null, exists: false, current: true }
    await loadClip('CL-20260910-1')
    const { w } = await mountAt(ClipRender, '/clip/c/CL-20260910-1/render')
    const rows = w.findAll('[data-test="take-row"]')
    expect(rows.length).toBe(3)
    expect(rows[0].find('[data-test="take-player"]').attributes('src')).toContain('/artifact/video/CL-20260910-1-1.mp4')
    expect(rows[1].find('[data-test="take-error"]').text()).toContain('余额不足')
    expect(rows[1].find('[data-test="take-player"]').exists()).toBe(false)
    expect(rows[2].find('[data-test="take-inflight"]').exists()).toBe(true)
    await rows[0].find('[data-test="take-pick"]').trigger('click'); await flushPromises()
    const picked = calls.find(c => c[0] === 'post' && c[1].endsWith('/pick'))
    expect(picked[2]).toEqual({ attempt: 'CL-20260910-1-1.mp4' })
    // 当前采用的片子有播放器与下载
    expect(w.find('[data-test="take-video"]').attributes('src')).toContain('/artifact/video/CL-20260910-1-3.mp4')
    expect(w.find('[data-test="take-download"]').attributes('href')).toContain('/artifact/video/')
  })

  it('首帧：三种模式；上传走 base64，生成走任务并弹确认', async () => {
    await loadClip('CL-20260910-1')
    const { w } = await mountAt(ClipRender, '/clip/c/CL-20260910-1/render')
    expect(w.find('[data-test="frame-mode-none"]').element.checked).toBe(true)
    // 切到「用我上传的图」
    await w.find('[data-test="frame-mode-upload"]').trigger('change')
    await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/first-frame'))[2]).toEqual({ mode: 'upload' })
    detail = sampleDetail({ ...sampleSpec(), first_frame: { mode: 'upload', asset: 'output/clip/CL-20260910-1/00_frame/a.png', prompt: '', error: null } }, { frame: 'a.png' })
    await loadClip('CL-20260910-1')
    const { w: w2 } = await mountAt(ClipRender, '/clip/c/CL-20260910-1/render')
    expect(w2.find('[data-test="frame-image"]').attributes('src')).toContain('/artifact/frame/a.png')
    const file = new File(['x'], 'ref.png', { type: 'image/png' })
    const input = w2.find('[data-test="frame-upload"]')
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change'); await flushPromises()
    const up = calls.find(c => c[0] === 'post' && c[1].endsWith('/first-frame') && c[2].filename)
    expect(up[2]).toEqual({ mode: 'upload', filename: 'ref.png', data_base64: 'QUJD' })
    // 切到「先出一张首帧」：弹确认 → 后台任务
    calls.length = 0
    await w2.find('[data-test="frame-mode-generate"]').trigger('change'); await flushPromises()
    expect(confirmMock).toHaveBeenCalled()
    const gen = calls.find(c => c[0] === 'post' && c[1].endsWith('/first-frame'))
    expect(gen[2]).toEqual({ mode: 'generate', force: false })
    expect(calls.some(c => c[0] === 'poll')).toBe(true)
  })
})

describe('clip · 高级页、侧栏、路由、helpers', () => {
  it('高级页：JSON 直改 PUT（非法 JSON 就地报错）；删除 = 归档后回模块首页', async () => {
    await loadClip('CL-20260910-1')
    const { w, r } = await mountAt(ClipAdvanced, '/clip/c/CL-20260910-1/advanced')
    const ta = w.find('[data-test="json-text"]')
    expect(JSON.parse(ta.element.value).title).toBe('城市清晨短片')
    expect(ta.element.value).not.toContain('output_root')
    await ta.setValue('{ not json')
    await w.find('[data-test="json-save"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="json-error"]').text()).toContain('不是合法的 JSON')
    expect(calls.some(c => c[0] === 'put')).toBe(false)
    await ta.setValue(JSON.stringify({ ...sampleSpec(), title: '城市清晨短片 2' }))
    await w.find('[data-test="json-save"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'put')[2].title).toBe('城市清晨短片 2')
    await w.find('[data-test="clip-archive"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'del' && c[1] === '/api/clip/CL-20260910-1')).toBe(true)
    expect(store.clip).toBeNull()
    expect(r.currentRoute.value.path).toBe('/clip')
  })

  it('侧栏多一块「单条视频」：新建 / 搜索 / 列表指向 /clip/c，删除 ✕ 归档；模块不可用时没有这块', async () => {
    store.clips = clips; store.clipsLoaded = true
    const { w, r } = await mountAt(Sidebar, '/')
    const block = w.find('[data-test="sidebar-module-clip"]')
    expect(block.exists()).toBe(true)
    expect(block.find('[data-test="module-header"]').text()).toContain('单条视频')
    await block.find('[data-test="module-header"]').trigger('click')
    expect(block.classes()).toContain('open')
    expect(block.findAll('a')[0].attributes('href')).toBe('/clip')   // 头行名字 → 模块首页；新建在模块首页上，侧栏不重复
    expect(block.html()).not.toContain('/clip/new')
    const rows = w.findAll('[data-test="clip-list"] .proj')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe('/clip/c/CL-20260910-1/write')
    expect(rows[1].text()).toContain('已有成片')
    // 列表短的时候没有搜索框（SEARCH_MIN=8）——搜索行为在 components.test.js 里单测
    expect(block.find('[data-test="clip-search"]').exists()).toBe(false)
    await w.findAll('[data-test="clip-list"] .del')[0].trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'del' && c[1] === '/api/clip/CL-20260910-1')).toBe(true)
    expect(r.currentRoute.value.path).toBe('/')
    store.modules = store.modules.filter(m => m.id !== 'clip')
    await w.vm.$nextTick()
    expect(w.find('[data-test="sidebar-module-clip"]').exists()).toBe(false)
  })

  it('路由：/clip 下的路径直接命中，/c/:id 落到 write', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/'); await r.isReady()
    for (const [from, to] of [
      ['/clip', '/clip'],
      ['/clip/new', '/clip/new'],
      ['/clip/c/CL-1', '/clip/c/CL-1/write'],
      ['/clip/c/CL-1/write', '/clip/c/CL-1/write'],
      ['/clip/c/CL-1/render', '/clip/c/CL-1/render'],
      ['/clip/c/CL-1/advanced', '/clip/c/CL-1/advanced'],
    ]) {
      await r.push(from)
      expect(r.currentRoute.value.path, from).toBe(to)
      expect(r.currentRoute.value.matched.length, from).toBeGreaterThan(0)
    }
  })

  it('helpers：时间条百分比、挪一拍留住时间段、删一拍往前贴、行预览、告警拆卡、列表副标题', () => {
    const beats = [{ id: 'B01', start: 0, end: 2 }, { id: 'B02', start: 2, end: 5 }]
    expect(timeline(beats, 10)).toEqual([
      { id: 'B01', left: 0, width: 20, label: '0–2s' },
      { id: 'B02', left: 20, width: 30, label: '2–5s' },
    ])
    // 挪一拍：内容换位置，时间段留在原位（不许因为挪一行就出现空洞）
    const moved = moveBeat([{ id: 'B01', start: 0, end: 2, picture: 'a' }, { id: 'B02', start: 2, end: 5, picture: 'b' }], 0, 1)
    expect(moved.map(b => [b.id, b.start, b.end, b.picture])).toEqual([['B01', 0, 2, 'b'], ['B02', 2, 5, 'a']])
    // 删一拍：后面的往前贴
    const dropped = removeBeat([{ id: 'B01', start: 0, end: 2 }, { id: 'B02', start: 2, end: 5 }, { id: 'B03', start: 5, end: 6 }], 1)
    expect(dropped.map(b => [b.id, b.start, b.end])).toEqual([['B01', 0, 2], ['B02', 2, 3]])
    expect(previewLine({ start: 0, end: 2.4, shot: '特写', picture: '路牌', sound: '车铃', text: 'GO' }))
      .toBe('0–2.4 秒：特写，路牌；声音：车铃；画面文字："GO"。')
    expect(previewLine({ start: 0, end: 1, picture: 'lanterns' }, false)).toBe('0–1s: lanterns.')
    const cards = toCards({ warnings: ['beat B02: 有连接词——拆两拍', '全片：没写最后一帧——写清停在什么上'] })
    expect(cards[0]).toMatchObject({ scope: 'beat', target: 'B02', impact: '有连接词', fix: '拆两拍' })
    expect(cards[1]).toMatchObject({ scope: 'clip', target: '', impact: '没写最后一帧' })
    expect(progressText(clips[1])).toContain('已有成片')
  })
})
