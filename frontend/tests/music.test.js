// 配乐模块前端（docs/music-spec.md §5）：首页列表与复制示例、新建（预设 brief → 表单 → POST）、
// 生成页（brief 表单 → PUT、提示词预览、生成 N 条并轮询、<audio> 播放与采用、「放进项目」下拉 + 短剧起始秒 / 播客槽 + 成功提示）、
// 高级（JSON / 参考图 / 归档）、侧栏块与路由。api 全部 mock；只认契约里的接口形状。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let list, detail, briefs, targets, taskResult, useResult
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/music') return list
    if (url === '/api/music/briefs') return briefs
    if (url === '/api/music/targets') return targets
    if (url.startsWith('/api/tasks/')) return taskResult
    if (url.startsWith('/api/music/')) return detail
    return {}
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    if (url === '/api/music') return { id: 'MU-20260910-2', spec: { ...body } }
    if (url.endsWith('/examples/create')) return { id: 'MU-20260910-9', example: body.name }
    if (url.endsWith('/generate')) return { task_id: 't-gen', cost_note: '2 次配乐请求（@music），每次出 1 条' }
    if (url.endsWith('/use')) return useResult
    return { ok: true }
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return { id: 'MU-20260910-1', spec: body } }),
  del: vi.fn(async (url) => { calls.push(['del', url]); return { archived_to: '.webui_deleted/music/x' } }),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(async (id) => { calls.push(['poll', id]); return taskResult }),
  pollJob: vi.fn(), sleep: vi.fn(async () => {}), fileToBase64: vi.fn(async () => 'QUJD'),
}))
const confirmMock = vi.fn(async () => true)
const toastMock = vi.fn()
vi.mock('../src/ui.js', () => ({
  prompt: vi.fn(), confirm: (...a) => confirmMock(...a), notice: vi.fn(), toast: (...a) => toastMock(...a), custom: vi.fn(), toasts: [],
  T: { role: { text: '剧本文本', image: '生图', voice: '语音', video: '视频', music: '配乐' } },
}))

import { store } from '../src/store.js'
import { routes } from '../src/router.js'
import { setLang } from '../src/i18n.js'
import { loadMusic, clearMusic, progressOf, attemptRows, splitList, emptyBrief, musicRoute } from '../src/music.js'
import MusicHome from '../src/views/music/MusicHome.vue'
import MusicNew from '../src/views/music/MusicNew.vue'
import Music from '../src/views/music/Music.vue'
import MusicGenerate from '../src/views/music/MusicGenerate.vue'
import MusicAdvanced from '../src/views/music/MusicAdvanced.vue'
import Sidebar from '../src/components/Sidebar.vue'

const stub = { template: '<div/>' }
function stubRouter() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: stub }, { path: '/settings', component: stub }, { path: '/voices', component: stub },
    { path: '/drama', component: stub }, { path: '/drama/new', component: stub }, { path: '/drama/libraries', component: stub }, { path: '/drama/p/:id/overview', component: stub },
    { path: '/picturebook', component: stub }, { path: '/picturebook/new', component: stub }, { path: '/picturebook/b/:id/:sub', component: stub },
    { path: '/clip', component: stub }, { path: '/clip/new', component: stub }, { path: '/clip/c/:id/:sub', component: stub },
    ...['sticker', 'portrait', 'image'].flatMap(m => [
      { path: `/${m}`, component: stub, meta: { module: m } }, { path: `/${m}/new`, component: stub, meta: { module: m } },
      { path: `/${m}/s/:id/:sub`, component: stub, meta: { module: m } },
    ]),
    { path: '/music', component: stub }, { path: '/music/new', component: stub }, { path: '/music/m/:id/:sub', component: stub },
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
    id: 'MU-20260910-1', module: 'music', title: '睡前摇篮曲',
    brief: { usage: '绘本睡前朗读的底噪', mood: ['温柔', '安定'], genre: '摇篮曲', instruments: ['音乐盒'], bpm: 70,
             structure: '前 8 秒渐入', avoid: ['鼓组'] },
    prompt_language: 'zh', duration: 120, count: 2, reference_images: ['content/assets/MU-20260910-1/a.png'],
    music_params: { model: '@music' },
    attempts: [
      { file: 'MU-20260910-1-1.mp3', created_at: 1, model: 'lyria', request_hash: 'h', task_id: 'url:x', error: null },
      { file: 'MU-20260910-1-2.mp3', created_at: 2, model: 'lyria', request_hash: 'h', task_id: 'url:y', error: null },
      { file: '', created_at: 3, model: 'lyria', request_hash: 'h', task_id: null, error: '内容被审核拦截' },
    ],
    current: 'MU-20260910-1-1.mp3',
    used_in: [{ module: 'drama', id: 'EP-1', slot: '12', at: '2026-09-10T00:00:00Z' }],
  }
}
function sampleDetail(spec = sampleSpec()) {
  const id = spec.id
  return {
    id, module: 'music', title: spec.title, spec,
    prompt: '用途：绘本睡前朗读的底噪；体裁：摇篮曲；情绪：温柔、安定；不要：鼓组。',
    prompts: { zh: '用途：绘本睡前朗读的底噪；体裁：摇篮曲；情绪：温柔、安定；不要：鼓组。', en: 'Use: bedtime bed; Genre: lullaby.' },
    attempts: spec.attempts.map(a => ({
      ...a, current: !!a.file && a.file === spec.current,
      url: a.file && !a.error ? `/api/music/${id}/artifact/audio/${a.file}` : null,
    })),
    current_url: `/api/music/${id}/artifact/audio/${spec.current}`,
    references: spec.reference_images.map(f => ({ file: f, exists: true })),
    used_in: spec.used_in,
    progress: { done: true, attempts: 3, ok_attempts: 2, stale: false, used: 1, count: 2 },
    busy: { generate: null },
    limits: { count: [1, 4], duration: [5, 600], bpm: [30, 240], max_references: 10 },
  }
}

beforeEach(() => {
  calls.length = 0
  confirmMock.mockReset(); confirmMock.mockResolvedValue(true)
  toastMock.mockReset()
  list = [
    { id: 'MU-20260910-1', title: '睡前摇篮曲', genre: '摇篮曲', duration: 120, count: 2,
      progress: { done: true, attempts: 3, ok_attempts: 2, used: 1 }, used_in: [{ module: 'drama', id: 'EP-1' }] },
    { id: 'MU-20260910-3', title: '史诗战鼓', genre: '史诗管弦', duration: 90, count: 1,
      progress: { done: false, attempts: 0, ok_attempts: 0, used: 0 }, used_in: [] },
  ]
  detail = sampleDetail()
  briefs = {
    briefs: [
      { id: 'bedtime_lullaby', name: '睡前摇篮曲', english: 'Bedtime lullaby', note: '绘本睡前朗读的底噪：不抢旁白。', note_en: 'Under a bedtime narration: never competes with the voice.', duration: 120,
        brief: { usage: '绘本睡前朗读的底噪', mood: ['温柔', '安定'], genre: '摇篮曲', instruments: ['音乐盒', '轻钢琴'], bpm: 70, structure: '前 8 秒渐入', avoid: ['鼓组', '人声'] } },
      { id: 'epic_war_drums', name: '史诗战鼓', english: 'Epic war drums', note: '对峙、进场、反击的高点。', duration: 90,
        brief: { usage: '短剧对峙段落', mood: ['紧迫'], genre: '史诗管弦', instruments: ['大鼓'], bpm: 120, structure: '四小节起势', avoid: ['甜美和声'] } },
    ],
    defaults: { duration: 60, count: 1 },
    limits: { count: [1, 4], duration: [5, 600], bpm: [30, 240], max_references: 10 },
    examples: ['lullaby_demo'],
  }
  targets = {
    modules: ['drama', 'picturebook', 'podcast'],
    drama: [{ id: 'EP-1', title: '加班夜', tracks: 1 }, { id: 'EP-2', title: '第二集', tracks: 0 }],
    picturebook: [{ id: 'PB-1', title: '小狐狸的三只袜子', tracks: 0 }],
    podcast: [{ id: 'PC-1', title: '为什么我们戒不掉短视频', tracks: 1, slots: ['bed'] }],
  }
  taskResult = { status: 'succeeded', result: { generated: ['MU-20260910-1-3.mp3', 'MU-20260910-1-4.mp3'], skipped: [], failed: [] } }
  useResult = { ok: true, module: 'drama', target: 'EP-1', target_title: '加班夜', file: 'content/assets/EP-1/music/x.mp3',
                slot: '12', tracks: 1, message: '已放进《加班夜》的配乐层', detail: sampleDetail() }
  store.musics = []; store.musicsLoaded = false
  clearMusic(); store.gen = null
  store.modules = [
    { id: 'drama', name: '短剧', english: 'Short drama', route: '/drama', status: 'ready', available: true, project_count: 1 },
    { id: 'music', name: '配乐', english: 'Music', icon: '🎵', route: '/music', status: 'ready', available: true, project_count: 2 },
  ]
  store.modulesLoaded = true
  store.episodes = []; store.episodesLoaded = true
  store.books = []; store.booksLoaded = true
  store.clips = []; store.clipsLoaded = true
  store.sets = { sticker: [], portrait: [], image: [] }; store.setsLoaded = { sticker: true, portrait: true, image: true }
  store.health = { roles: { music: { configured: true } } }
})

describe('music · 首页与新建', () => {
  it('首页：标题 / 体裁 / 进度药丸 / 投放数；「复制示例」POST 示例并进生成页', async () => {
    const { w, r } = await mountAt(MusicHome, '/music')
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/music')).toBe(true)
    const rows = w.findAll('[data-test="music-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe('/music/m/MU-20260910-1/generate')
    expect(rows[0].text()).toContain('睡前摇篮曲')
    expect(rows[0].find('[data-test="music-genre"]').text()).toBe('摇篮曲')
    expect(rows[0].find('[data-test="music-progress"]').text()).toContain('已采用 1 条')
    expect(rows[0].find('[data-test="music-used"]').text()).toContain('已投放 1')
    expect(rows[1].find('[data-test="music-progress"]').text()).toContain('还没出')
    await w.find('[data-test="music-example"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === '/api/music/examples/create')[2]).toEqual({ name: 'lullaby_demo' })
    expect(r.currentRoute.value.path).toBe('/music/m/MU-20260910-9/generate')
  })

  it('首页空状态与「+ 新建配乐」', async () => {
    list = []
    const { w, r } = await mountAt(MusicHome, '/music')
    expect(w.find('[data-test="music-empty"]').exists()).toBe(true)
    await w.find('[data-test="music-new"]').trigger('click'); await flushPromises()
    expect(r.currentRoute.value.path).toBe('/music/new')
  })

  it('新建：预设 brief 填进表单可再改；chips 增删；没标题不建；POST 后进生成页', async () => {
    const { w, r } = await mountAt(MusicNew, '/music/new')
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/music/briefs')).toBe(true)
    expect(w.findAll('[data-test="music-preset"] option').map(o => o.attributes('value'))).toEqual(['', 'bedtime_lullaby', 'epic_war_drums'])
    // 没标题不建
    await w.find('[data-test="music-create"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    // 选预设 → 整份 brief 填进表单，标题和时长也带上
    await w.find('[data-test="music-preset"]').setValue('bedtime_lullaby'); await flushPromises()
    expect(w.find('[data-test="music-preset-note"]').text()).toContain('不抢旁白')
    expect(w.find('[data-test="music-title"]').element.value).toBe('睡前摇篮曲')
    expect(w.find('[data-test="brief-usage"]').element.value).toBe('绘本睡前朗读的底噪')
    expect(w.find('[data-test="brief-genre"]').element.value).toBe('摇篮曲')
    expect(w.find('[data-test="music-duration"]').element.value).toBe('120')
    const mood = w.find('[data-test="brief-mood"]')
    expect(mood.findAll('[data-test="brief-chip"]').map(c => c.text())).toEqual(['温柔 ✕', '安定 ✕'])
    // 改：删一个情绪、加两个乐器
    await mood.findAll('[data-test="brief-chip"]')[1].trigger('click')
    const inst = w.find('[data-test="brief-instruments"]')
    await inst.find('[data-test="brief-chip-input"]').setValue('弦乐衬底、竖琴')
    await inst.find('[data-test="brief-chip-add"]').trigger('click')
    expect(inst.findAll('[data-test="brief-chip"]').length).toBe(4)
    await w.find('[data-test="music-count"]').setValue('3')
    await w.find('[data-test="music-create"]').trigger('click'); await flushPromises()
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/music')
    expect(created[2]).toEqual({
      title: '睡前摇篮曲', duration: 120, count: 3, prompt_language: 'zh',
      brief: { usage: '绘本睡前朗读的底噪', mood: ['温柔'], genre: '摇篮曲', instruments: ['音乐盒', '轻钢琴', '弦乐衬底', '竖琴'],
               bpm: 70, structure: '前 8 秒渐入', avoid: ['鼓组', '人声'] },
    })
    expect(r.currentRoute.value.path).toBe('/music/m/MU-20260910-2/generate')
  })
})

describe('music · 项目页', () => {
  it('外壳：进项目拉 detail，导航两项，顶部进度', async () => {
    const { w } = await mountAt(Music, '/music/m/MU-20260910-1/generate')
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/music/MU-20260910-1')).toBe(true)
    const nav = w.findAll('.ws-nav a.nav').map(a => a.attributes('href'))
    expect(nav).toEqual(['/music/m/MU-20260910-1/generate', '/music/m/MU-20260910-1/advanced'])
    const prog = w.find('[data-test="music-progress"]').text()
    expect(prog).toContain('3')       // 历次
    expect(prog).toContain('✓')       // 已采用
    expect(w.find('.ws-nav .head').text()).toContain('睡前摇篮曲')
  })

  it('生成页：提示词预览、生成按钮写清 N 条请求、确认后 POST 并轮询、cost_note 弹出', async () => {
    await loadMusic('MU-20260910-1')
    const { w } = await mountAt(MusicGenerate, '/music/m/MU-20260910-1/generate')
    expect(w.find('[data-test="music-prompt"]').text()).toContain('用途：绘本睡前朗读的底噪')
    expect(w.find('[data-test="music-generate-btn"]').text()).toContain('2')
    calls.length = 0
    await w.find('[data-test="music-generate-btn"]').trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('2')
    const gen = calls.find(c => c[0] === 'post' && c[1] === '/api/music/MU-20260910-1/generate')
    expect(gen[2]).toEqual({ count: 2 })
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-gen')).toBe(true)
    expect(toastMock.mock.calls.some(c => String(c[0]).includes('2 次配乐请求'))).toBe(true)
    expect(store.gen).toBeNull()
    // 拒绝确认就不发
    calls.length = 0
    confirmMock.mockResolvedValueOnce(false)
    await w.find('[data-test="music-regenerate"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    // 全部重出：force
    await w.find('[data-test="music-regenerate"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/generate'))[2]).toEqual({ count: 2, force: true })
  })

  it('生成页：改 brief → 保存 PUT 整份；chips 也进 PUT', async () => {
    await loadMusic('MU-20260910-1')
    const { w } = await mountAt(MusicGenerate, '/music/m/MU-20260910-1/generate')
    expect(w.find('[data-test="music-save"]').attributes('disabled')).toBeDefined()
    await w.find('[data-test="brief-genre"]').setValue('氛围')
    expect(w.find('[data-test="music-save"]').attributes('disabled')).toBeUndefined()
    const avoid = w.find('[data-test="brief-avoid"]')
    await avoid.find('[data-test="brief-chip-input"]').setValue('人声')
    await avoid.find('[data-test="brief-chip-add"]').trigger('click')
    await w.find('[data-test="music-save"]').trigger('click'); await flushPromises()
    const saved = calls.find(c => c[0] === 'put' && c[1] === '/api/music/MU-20260910-1')
    expect(saved[2].brief.genre).toBe('氛围')
    expect(saved[2].brief.avoid).toEqual(['鼓组', '人声'])
    expect(saved[2].title).toBe('睡前摇篮曲')
    expect(saved[2].output_root).toBeUndefined()
  })

  it('历次：每条一个 <audio>，失败的那条只显示原因；采用 → POST pick', async () => {
    await loadMusic('MU-20260910-1')
    const { w } = await mountAt(MusicGenerate, '/music/m/MU-20260910-1/generate')
    const rows = w.findAll('[data-test="music-attempt"]')
    expect(rows.length).toBe(3)
    const players = w.findAll('[data-test="music-audio"]')
    expect(players.length).toBe(2)
    expect(players[0].attributes('src')).toBe('/api/music/MU-20260910-1/artifact/audio/MU-20260910-1-1.mp3')
    expect(rows[0].find('[data-test="music-attempt-current"]').exists()).toBe(true)
    expect(rows[2].find('[data-test="music-attempt-error"]').text()).toContain('审核拦截')
    expect(rows[2].find('[data-test="music-audio"]').exists()).toBe(false)
    // 当前那条不能再采用；第二条可以
    expect(rows[0].find('[data-test="music-adopt"]').attributes('disabled')).toBeDefined()
    calls.length = 0
    await rows[1].find('[data-test="music-adopt"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/pick'))[2]).toEqual({ attempt: 'MU-20260910-1-2.mp3' })
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/music/MU-20260910-1')).toBe(true)
  })

  it('「放进项目」：目标下拉来自 /targets；短剧多一个起始秒；成功 toast 点名目标项目', async () => {
    await loadMusic('MU-20260910-1')
    const { w } = await mountAt(MusicGenerate, '/music/m/MU-20260910-1/generate')
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/music/targets')).toBe(true)
    const opts = w.findAll('[data-test="use-target"] option').map(o => o.attributes('value'))
    expect(opts).toEqual(['', 'drama:EP-1', 'drama:EP-2', 'picturebook:PB-1', 'podcast:PC-1'])
    expect(w.find('[data-test="use-target"] option:nth-child(2)').text()).toContain('加班夜')
    expect(w.find('[data-test="use-target"] option:nth-child(5)').text()).toContain('AI 播客 · 为什么我们戒不掉短视频')
    // 没选目标：按钮禁用，没有起始秒输入
    expect(w.find('[data-test="use-submit"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="use-slot-field"]').exists()).toBe(false)
    // 绘本目标：没有起始秒
    await w.find('[data-test="use-target"]').setValue('picturebook:PB-1')
    expect(w.find('[data-test="use-slot-field"]').exists()).toBe(false)
    // 短剧目标：有起始秒
    await w.find('[data-test="use-target"]').setValue('drama:EP-1')
    expect(w.find('[data-test="use-slot-field"]').exists()).toBe(true)
    await w.find('[data-test="use-slot"]').setValue('12')
    calls.length = 0
    await w.find('[data-test="use-submit"]').trigger('click'); await flushPromises()
    const used = calls.find(c => c[0] === 'post' && c[1] === '/api/music/MU-20260910-1/use')
    expect(used[2]).toEqual({ module: 'drama', target: 'EP-1', slot: 12 })
    expect(toastMock.mock.calls.some(c => String(c[0]).includes('加班夜'))).toBe(true)
    expect(toastMock.mock.calls.some(c => String(c[0]).includes('配乐层'))).toBe(true)
    // 已投放列表
    expect(w.find('[data-test="music-used-in"]').text()).toContain('EP-1')
  })

  it('「放进项目」：绘本目标不带 slot', async () => {
    await loadMusic('MU-20260910-1')
    const { w } = await mountAt(MusicGenerate, '/music/m/MU-20260910-1/generate')
    await w.find('[data-test="use-target"]').setValue('picturebook:PB-1')
    await w.find('[data-test="use-submit"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/use'))[2]).toEqual({ module: 'picturebook', target: 'PB-1' })
  })

  it('「放进项目」：播客目标显示槽下拉（片头 / 垫乐 / 片尾）代替起始秒，默认垫乐、已有的槽标出来；POST 带槽名；toast 按槽说话', async () => {
    const spec = sampleSpec()
    spec.used_in = [...spec.used_in, { module: 'podcast', id: 'PC-1', slot: 'bed', at: '2026-09-16T00:00:00Z' }]
    detail = sampleDetail(spec)
    useResult = { ok: true, module: 'podcast', target: 'PC-1', target_title: '为什么我们戒不掉短视频', file: 'content/assets/PC-1/music/x.mp3',
                  slot: 'intro', slot_label: '片头', tracks: 2, message: '已放进《为什么我们戒不掉短视频》的片头', detail: sampleDetail(spec) }
    await loadMusic('MU-20260910-1')
    const { w } = await mountAt(MusicGenerate, '/music/m/MU-20260910-1/generate')
    // 已投放药丸：播客的位置写槽的中文名，不是「beds」
    const pills = w.findAll('[data-test="music-used-in"] .pill').map(p => p.text())
    expect(pills).toEqual(['drama · EP-1 · 12s', 'podcast · PC-1 · 垫乐'])
    await w.find('[data-test="use-target"]').setValue('podcast:PC-1')
    expect(w.find('[data-test="use-slot-field"]').exists()).toBe(false)
    const slotSelect = w.find('[data-test="use-podcast-slot"]')
    expect(slotSelect.exists()).toBe(true)
    expect(slotSelect.element.value).toBe('bed')
    const slotOpts = slotSelect.findAll('option')
    expect(slotOpts.map(o => o.attributes('value'))).toEqual(['intro', 'bed', 'outro'])
    expect(slotOpts.map(o => o.text())).toEqual(['片头', '垫乐（已有，会覆盖）', '片尾'])
    // 不动下拉直接投：槽名 bed
    calls.length = 0
    await w.find('[data-test="use-submit"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === '/api/music/MU-20260910-1/use')[2]).toEqual({ module: 'podcast', target: 'PC-1', slot: 'bed' })
    // 换成片头再投：槽名 intro；toast 用后端的按槽提示
    await w.find('[data-test="use-podcast-slot"]').setValue('intro')
    calls.length = 0; toastMock.mockReset()
    await w.find('[data-test="use-submit"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/use'))[2]).toEqual({ module: 'podcast', target: 'PC-1', slot: 'intro' })
    expect(toastMock.mock.calls.some(c => String(c[0]) === '已放进《为什么我们戒不掉短视频》的片头')).toBe(true)
    // 切回短剧：槽下拉收起、起始秒回来
    await w.find('[data-test="use-target"]').setValue('drama:EP-1')
    expect(w.find('[data-test="use-podcast-slot"]').exists()).toBe(false)
    expect(w.find('[data-test="use-slot-field"]').exists()).toBe(true)
  })

  it('高级：JSON 直改 PUT（非法 JSON 就地报错）；参考图列表与上传；删除 = 归档后回模块首页', async () => {
    await loadMusic('MU-20260910-1')
    const { w, r } = await mountAt(MusicAdvanced, '/music/m/MU-20260910-1/advanced')
    const ta = w.find('[data-test="json-text"]')
    expect(JSON.parse(ta.element.value).title).toBe('睡前摇篮曲')
    expect(w.findAll('[data-test="music-reference"]').length).toBe(1)
    expect(w.find('[data-test="music-reference"]').text()).toContain('a.png')
    await ta.setValue('{ not json')
    await w.find('[data-test="json-save"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="json-error"]').text()).toContain('不是合法的 JSON')
    expect(calls.some(c => c[0] === 'put')).toBe(false)
    await ta.setValue(JSON.stringify({ ...sampleSpec(), title: '睡前摇篮曲 2' }))
    await w.find('[data-test="json-save"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'put')[2].title).toBe('睡前摇篮曲 2')
    // 上传参考图
    const input = w.find('[data-test="reference-upload"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['x'], 'cover.png', { type: 'image/png' })] })
    await input.trigger('change'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/references'))[2]).toEqual({ filename: 'cover.png', data_base64: 'QUJD' })
    // 归档
    await w.find('[data-test="music-archive"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'del' && c[1] === '/api/music/MU-20260910-1')).toBe(true)
    expect(store.music).toBeNull()
    expect(r.currentRoute.value.path).toBe('/music')
  })
})

describe('music · 侧栏、路由与 helpers', () => {
  it('模块可用时侧栏多一块：新建 / 搜索 / 列表指向 /music/m；删除 ✕ 归档；不可用时没有', async () => {
    store.musics = list; store.musicsLoaded = true
    const { w, r } = await mountAt(Sidebar, '/')
    const block = w.find('[data-test="sidebar-module-music"]')
    expect(block.exists()).toBe(true)
    expect(block.find('[data-test="module-header"]').text()).toContain('配乐')
    expect(block.find('[data-test="module-header"]').text()).toContain('2')
    expect(block.findAll('a')[0].attributes('href')).toBe('/music')   // 头行名字 → 模块首页；新建在模块首页上，侧栏不重复
    expect(block.html()).not.toContain('/music/new')
    await block.find('[data-test="module-header"]').trigger('click')
    expect(block.classes()).toContain('open')
    const rows = w.findAll('[data-test="music-list"] .proj')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe('/music/m/MU-20260910-1/generate')
    expect(rows[0].text()).toContain('已采用 1 条')
    expect(rows[0].text()).toContain('已投放 1')
    // 列表短的时候没有搜索框（SEARCH_MIN=8）——搜索行为在 components.test.js 里单测
    expect(block.find('[data-test="music-search"]').exists()).toBe(false)
    await w.findAll('[data-test="music-list"] .del')[0].trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'del' && c[1] === '/api/music/MU-20260910-1')).toBe(true)
    expect(r.currentRoute.value.path).toBe('/')
    store.modules = store.modules.filter(m => m.id !== 'music')
    await w.vm.$nextTick()
    expect(w.find('[data-test="sidebar-module-music"]').exists()).toBe(false)
  })

  it('路由：/music 下的路径直接命中；/m/:id 落到 generate', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/'); await r.isReady()
    for (const [from, to] of [
      ['/music', '/music'], ['/music/new', '/music/new'],
      ['/music/m/MU-1', '/music/m/MU-1/generate'], ['/music/m/MU-1/generate', '/music/m/MU-1/generate'],
      ['/music/m/MU-1/advanced', '/music/m/MU-1/advanced'],
    ]) {
      await r.push(from)
      expect(r.currentRoute.value.path, from).toBe(to)
      expect(r.currentRoute.value.matched.length, from).toBeGreaterThan(0)
    }
    expect(r.currentRoute.value.params.id).toBe('MU-1')
  })

  it('helpers：进度形状兜底、chips 拆分、历次地址、路由拼接', async () => {
    expect(progressOf({ progress: { done: true, attempts: 3, ok_attempts: 2, used: 1 } }))
      .toEqual({ done: true, attempts: 3, ok: 2, stale: false, used: 1 })
    expect(progressOf({ attempts: [{ file: 'a' }, { error: 'x' }], current: 'a', used_in: [] }))
      .toEqual({ done: true, attempts: 2, ok: 1, stale: false, used: 0 })
    expect(progressOf(null)).toEqual({ done: false, attempts: 0, ok: 0, stale: false, used: 0 })
    expect(splitList('温柔、安定, 困倦')).toEqual(['温柔', '安定', '困倦'])
    expect(splitList('')).toEqual([])
    expect(emptyBrief()).toEqual({ usage: '', mood: [], genre: '', instruments: [], bpm: null, structure: '', avoid: [] })
    expect(musicRoute('MU-1')).toBe('/music/m/MU-1/generate')
    // 老形状：详情直接是项目文件（没有 attempts 行的 url），地址按文件名拼
    const spec = sampleSpec()
    const rows = attemptRows(spec.id, null, spec)
    expect(rows.map(a => a.url)).toEqual([
      '/api/music/MU-20260910-1/artifact/audio/MU-20260910-1-1.mp3',
      '/api/music/MU-20260910-1/artifact/audio/MU-20260910-1-2.mp3',
      null,
    ])
    expect(rows[0].current).toBe(true)
    expect(rows[2].ok).toBe(false)
  })

  it('英文界面：预设名 / 一句话用库里自带的 english / note_en（没有 note_en 的预设仍显示中文 note）', async () => {
    setLang('en')
    try {
      const { w } = await mountAt(MusicNew, '/music/new')
      expect(w.findAll('[data-test="music-preset"] option').map(o => o.text()).slice(1)).toEqual(['Bedtime lullaby', 'Epic war drums'])
      await w.find('[data-test="music-preset"]').setValue('bedtime_lullaby'); await flushPromises()
      expect(w.find('[data-test="music-title"]').element.value).toBe('Bedtime lullaby')   // 默认标题跟着下拉里看到的名字
      expect(w.find('[data-test="music-preset-note"]').text()).toBe('Under a bedtime narration: never competes with the voice.')
      await w.find('[data-test="music-preset"]').setValue('epic_war_drums'); await flushPromises()
      expect(w.find('[data-test="music-preset-note"]').text()).toBe('对峙、进场、反击的高点。')
    } finally {
      setLang('zh')
    }
  })
})
