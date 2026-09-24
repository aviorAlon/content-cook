// AI 播客模块前端（docs/podcast-spec.md §5–6）：首页列表与复制示例、新建（形式预置主播行 → 表单 → POST）、
// 脚本页（环节 / 台词渲染、起草整期确认后起任务、重写这一段带 note、保存 PUT 整份）、
// 配音页（合成整期任务、稿子改过提示、成品播放器、章节、上传音乐、音色保存）、高级（JSON / 归档）、侧栏块、任务中心表与路由。
// api 全部 mock；只认契约里的接口形状。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let list, detail, formats, voices, taskResult
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/podcast') return list
    if (url === '/api/podcast/formats') return formats
    if (url === '/api/voices') return voices
    if (url.startsWith('/api/tasks/')) return taskResult
    if (url.startsWith('/api/podcast/')) return detail
    return {}
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    if (url === '/api/podcast') return { id: 'PC-20260916-2', spec: { ...body } }
    if (url.endsWith('/examples/create')) return { id: 'PC-20260916-9', example: body.name }
    if (url.endsWith('/draft') || url.endsWith('/redraft')) return { task_id: 't-draft', cost_note: '1 次文本模型请求（@text）' }
    if (url.endsWith('/synthesize')) return { task_id: 't-audio', cost_note: 'edge 免费；minimax 按音色库计费' }
    return { ok: true }
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return { id: 'PC-20260916-1', spec: body } }),
  del: vi.fn(async (url) => { calls.push(['del', url]); return { archived_to: '.webui_deleted/podcast/x' } }),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(async (id) => { calls.push(['poll', id]); return taskResult }),
  pollJob: vi.fn(), sleep: vi.fn(async () => {}), fileToBase64: vi.fn(async () => 'QUJD'),
}))
const confirmMock = vi.fn(async () => true)
const promptMock = vi.fn(async () => '老周要多反问')
const toastMock = vi.fn()
vi.mock('../src/ui.js', () => ({
  prompt: (...a) => promptMock(...a), confirm: (...a) => confirmMock(...a), notice: vi.fn(), toast: (...a) => toastMock(...a), custom: vi.fn(), toasts: [],
  T: { role: { text: '剧本文本', image: '生图', voice: '语音', video: '视频', music: '配乐' } },
}))

import { store } from '../src/store.js'
import { routes } from '../src/router.js'
import { KIND_LABELS, PROJECT_ROUTES, taskTarget } from '../src/tasks.js'
import {
  loadPodcast, clearPodcast, progressOf, progressText, nextSegmentId, fmtClock, splitList, emptyBrief,
  podcastRoute, audioUrl, textUrl, specBody,
} from '../src/podcast.js'
import PodcastHome from '../src/views/podcast/PodcastHome.vue'
import PodcastNew from '../src/views/podcast/PodcastNew.vue'
import Podcast from '../src/views/podcast/Podcast.vue'
import PodcastScript from '../src/views/podcast/PodcastScript.vue'
import PodcastAudio from '../src/views/podcast/PodcastAudio.vue'
import PodcastAdvanced from '../src/views/podcast/PodcastAdvanced.vue'
import Sidebar from '../src/components/Sidebar.vue'

const stub = { template: '<div/>' }
function stubRouter() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: stub }, { path: '/settings', component: stub }, { path: '/voices/:voiceId?', component: stub }, { path: '/tasks', component: stub },
    { path: '/drama', component: stub }, { path: '/drama/new', component: stub }, { path: '/drama/libraries', component: stub }, { path: '/drama/p/:id/overview', component: stub },
    { path: '/picturebook', component: stub }, { path: '/picturebook/new', component: stub }, { path: '/picturebook/b/:id/:sub', component: stub },
    { path: '/clip', component: stub }, { path: '/clip/new', component: stub }, { path: '/clip/c/:id/:sub', component: stub },
    ...['sticker', 'portrait', 'image'].flatMap(m => [
      { path: `/${m}`, component: stub, meta: { module: m } }, { path: `/${m}/new`, component: stub, meta: { module: m } },
      { path: `/${m}/s/:id/:sub`, component: stub, meta: { module: m } },
    ]),
    { path: '/music', component: stub }, { path: '/music/new', component: stub }, { path: '/music/m/:id/:sub', component: stub },
    { path: '/podcast', component: stub }, { path: '/podcast/new', component: stub }, { path: '/podcast/p/:id/:sub', component: stub },
  ] })
}
async function mountAt(Comp, path = '/') {
  const r = stubRouter(); await r.push(path); await r.isReady()
  const w = mount(Comp, { global: { plugins: [r] } })
  await flushPromises()
  return { w, r }
}

const ID = 'PC-20260916-1'
function sampleSpec() {
  return {
    id: ID, title: '为什么我们戒不掉短视频', format: 'duo', language: 'zh',
    brief: {
      topic: '刷短视频停不下来，到底是我们意志力差还是产品设计使然', angle: '不做道德批判', audience: '25–35 岁上班族',
      takeaways: ['无限流是怎么设计的', '两个亲测有效的收手办法'], materials: '老周去年戒了三个月又复发', tone: '轻松但不水', avoid: ['说教'],
    },
    target_minutes: 12,
    hosts: [
      { id: 'zhou', name: '老周', role: 'host', persona: '做了十年产品', stance: '认为是设计的锅', voice: 'zhou-voice' },
      { id: 'xiao', name: '小夏', role: 'cohost', persona: '语速快', stance: '主要还是自己的问题', voice: '' },
    ],
    segments: [
      { id: 'G01', kind: 'cold_open', title: '冷开场', goal: '一个具体场景把人拽进来', beats: ['小夏凌晨两点还在刷'], questions: [],
        lines: [{ who: 'xiao', text: '……我看了眼时间，两点十七。', cue: '轻笑' }] },
      { id: 'G02', kind: 'qa', title: '听友问答', goal: '', beats: [], questions: [{ from: '听友阿May', text: '把手机设成黑白真的有用吗？' }],
        lines: [{ who: 'zhou', text: '阿May 问：把手机设成黑白有用吗？', kind: 'question' },
                { who: 'xiao', text: '有用，三天。第四天我就调回来了。', kind: 'answer' }] },
    ],
    music: { intro: '', bed: `content/assets/${ID}/bed.mp3`, outro: '', bed_gain: 0.18, duck: 0.45 },
    pause: { line: 0.35, segment: 1.2 },
    draft: { model: 'gpt-x', backend: 'gateway', created_at: 1758000000, usage_tokens: 1200, note: '' },
    output_root: `output/podcast/${ID}`, project_root: 'D:/x',
  }
}
function sampleDetail(spec = sampleSpec()) {
  const id = spec.id
  const lines = [
    { segment: 'G01', index: 0, who: 'xiao', text: '……我看了眼时间，两点十七。', file: 'G01_00.mp3', duration: 3.2, stale: false },
    { segment: 'G02', index: 0, who: 'zhou', text: '阿May 问：把手机设成黑白有用吗？', file: 'G02_00.mp3', duration: 4.1, stale: true },
    { segment: 'G02', index: 1, who: 'xiao', text: '有用，三天。第四天我就调回来了。', file: 'G02_01.mp3', duration: 3.8, stale: false },
  ].map(l => ({ ...l, url: `/api/podcast/${id}/artifact/audio/${l.file}` }))
  return {
    id, module: 'podcast', title: spec.title, spec,
    estimate: { chars: 62, lines: 3, seconds: 15.4, minutes: 0.26 },
    warnings: ['segment G02: 回答复述了问题——先给结论再讲原因', '全期：全程没分歧——加一处不同意'],
    problems: [
      { scope: 'segment', target: 'G02', kind: 'podcast', impact: '回答复述了问题', fix: '先给结论再讲原因' },
      { scope: 'episode', target: '', kind: 'podcast', impact: '全程没分歧', fix: '加一处不同意' },
    ],
    hosts: spec.hosts.map(h => ({ ...h, voice_label: h.voice ? '老周的音色 · edge' : '占位 zh-CN-XiaoxiaoNeural', placeholder: !h.voice })),
    audio: {
      manifest: lines, lines,
      mix_url: `/api/podcast/${id}/artifact/audio/${id}.mp3`, mix_duration: 95,
      chapters: [{ id: 'G01', title: '冷开场', start: 0 }, { id: 'G02', title: '听友问答', start: 75 }],
      stale: true, ffmpeg: true, synthesized: 3, total: 3,
    },
    texts: { shownotes_url: `/api/podcast/${id}/artifact/text/shownotes.md`, transcript_url: `/api/podcast/${id}/artifact/text/transcript.txt` },
    draft: spec.draft, busy: { draft: null, audio: null },
    limits: { target_minutes: [1, 90], hosts: [1, 4] },
  }
}

beforeEach(() => {
  calls.length = 0
  confirmMock.mockReset(); confirmMock.mockResolvedValue(true)
  promptMock.mockReset(); promptMock.mockResolvedValue('老周要多反问')
  toastMock.mockReset()
  list = [
    { id: ID, module: 'podcast', pipeline: 'podcast', title: '为什么我们戒不掉短视频', format: 'duo', hosts: 2, minutes: 12, segments: 2, lines: 3,
      progress: { drafted: true, audio: true, mixed: false }, path: `content/podcast/${ID}.json` },
    { id: 'PC-20260916-3', module: 'podcast', pipeline: 'podcast', title: '独白一期', format: 'solo', hosts: 1, minutes: 5, segments: 0, lines: 0,
      progress: { drafted: false, audio: false, mixed: false }, path: 'content/podcast/PC-20260916-3.json' },
  ]
  detail = sampleDetail()
  formats = {
    formats: [
      { id: 'solo', name: '单人独白', english: 'Solo', hosts: [1, 1], note: '一个人讲' },
      { id: 'duo', name: '双人对谈', english: 'Duo', hosts: [2, 2], note: '两个人聊' },
      { id: 'interview', name: '访谈', english: 'Interview', hosts: [2, 4], note: '主播 + 嘉宾' },
      { id: 'roundtable', name: '圆桌', english: 'Roundtable', hosts: [3, 4], note: '三四个人' },
    ],
    kinds: ['cold_open', 'intro', 'topic', 'qa', 'outro'],
    cues: ['笑', '轻笑', '停顿', '打断', '叹气', '加快', '放慢', '低声', '强调'],
    defaults: { target_minutes: 12, pause: { line: 0.35, segment: 1.2 }, music: { bed_gain: 0.18, duck: 0.45 } },
    limits: { target_minutes: [1, 90], hosts: [1, 4] },
    examples: ['short_video_demo'], craft: ['口语不是书面语'],
  }
  voices = [{ id: 'zhou-voice', name: '老周的音色', engine: 'edge', voice: 'zh-CN-YunyangNeural' }, { id: 'v2', name: '小夏音色', engine: 'edge', voice: 'zh-CN-XiaoyiNeural' }]
  taskResult = { status: 'succeeded', result: { segments: 2, lines: 3 } }
  store.podcasts = []; store.podcastsLoaded = false
  clearPodcast(); store.gen = null; store.voices = null
  store.modules = [
    { id: 'drama', name: '短剧', english: 'Short drama', route: '/drama', status: 'ready', available: true, project_count: 1 },
    { id: 'podcast', name: 'AI 播客', english: 'AI Podcast', icon: '🎙', route: '/podcast', status: 'ready', available: true, project_count: 2 },
  ]
  store.modulesLoaded = true
  store.episodes = []; store.episodesLoaded = true
  store.books = []; store.booksLoaded = true
  store.clips = []; store.clipsLoaded = true
  store.musics = []; store.musicsLoaded = true
  store.sets = { sticker: [], portrait: [], image: [] }; store.setsLoaded = { sticker: true, portrait: true, image: true }
  store.health = { roles: { text: { configured: true }, voice: { configured: true } } }
})

describe('podcast · 首页与新建', () => {
  it('首页：标题 / 形式 / 主播数 / 预计分钟 / 进度药丸；「复制示例」POST 示例并进脚本页', async () => {
    const { w, r } = await mountAt(PodcastHome, '/podcast')
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/podcast')).toBe(true)
    const rows = w.findAll('[data-test="podcast-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe(`/podcast/p/${ID}/script`)
    expect(rows[0].text()).toContain('为什么我们戒不掉短视频')
    expect(rows[0].find('[data-test="podcast-format"]').text()).toBe('双人对谈')
    expect(rows[0].text()).toContain('2 位主播')
    expect(rows[0].text()).toContain('预计 12 分钟')
    const pills = rows[0].findAll('[data-test="podcast-progress"] .pill')
    expect(pills.map(p => p.text())).toEqual(['已起草', '已配音', '已混音'])
    expect(pills.map(p => p.classes().includes('ok'))).toEqual([true, true, false])
    expect(rows[1].findAll('[data-test="podcast-progress"] .pill.ok').length).toBe(0)
    await w.find('[data-test="podcast-example"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === '/api/podcast/examples/create')[2]).toEqual({ name: 'short_video_demo' })
    expect(r.currentRoute.value.path).toBe('/podcast/p/PC-20260916-9/script')
  })

  it('首页空状态与「+ 新建播客」', async () => {
    list = []
    const { w, r } = await mountAt(PodcastHome, '/podcast')
    expect(w.find('[data-test="podcast-empty"]').exists()).toBe(true)
    await w.find('[data-test="podcast-new"]').trigger('click'); await flushPromises()
    expect(r.currentRoute.value.path).toBe('/podcast/new')
  })

  it('新建：形式下拉来自 /formats，切换按人数预置主播行；音色来自 /api/voices；没标题不建；POST 正确的 body 后进脚本页', async () => {
    const { w, r } = await mountAt(PodcastNew, '/podcast/new')
    // 形式表是模块级缓存（首页那条测试已经拉过），这里只看音色是否拉了
    expect(calls.some(c => c[0] === 'get' && c[1] === '/api/voices')).toBe(true)
    expect(w.findAll('[data-test="podcast-format"] option').map(o => o.attributes('value'))).toEqual(['solo', 'duo', 'interview', 'roundtable'])
    expect(w.findAll('[data-test="host-row"]').length).toBe(2)   // 默认双人
    // 圆桌 → 3 行；单人 → 1 行；访谈 → 2 行、第二位是嘉宾
    await w.find('[data-test="podcast-format"]').setValue('roundtable')
    expect(w.findAll('[data-test="host-row"]').length).toBe(3)
    await w.find('[data-test="podcast-format"]').setValue('solo')
    expect(w.findAll('[data-test="host-row"]').length).toBe(1)
    await w.find('[data-test="podcast-format"]').setValue('interview')
    expect(w.findAll('[data-test="host-row"]').length).toBe(2)
    expect(w.findAll('[data-test="host-role"]')[1].element.value).toBe('guest')
    await w.find('[data-test="podcast-format"]').setValue('duo')
    expect(w.findAll('[data-test="host-role"]')[1].element.value).toBe('cohost')
    // 音色下拉：空 = 占位 + 音色库档案
    expect(w.findAll('[data-test="host-voice"]')[0].findAll('option').map(o => o.attributes('value'))).toEqual(['', 'zhou-voice', 'v2'])
    // 没标题不建
    await w.find('[data-test="podcast-create"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    // 填表
    await w.find('[data-test="podcast-title"]').setValue('为什么我们戒不掉短视频')
    await w.find('[data-test="brief-topic"]').setValue('刷短视频停不下来')
    await w.find('[data-test="brief-angle"]').setValue('不做道德批判')
    await w.find('[data-test="brief-audience"]').setValue('上班族')
    await w.find('[data-test="brief-materials"]').setValue('老周去年戒了三个月又复发')
    await w.find('[data-test="brief-tone"]').setValue('轻松但不水')
    const take = w.find('[data-test="brief-takeaways"]')
    await take.find('[data-test="brief-chip-input"]').setValue('无限流是怎么设计的、两个收手办法')
    await take.find('[data-test="brief-chip-add"]').trigger('click')
    expect(take.findAll('[data-test="brief-chip"]').length).toBe(2)
    await take.findAll('[data-test="brief-chip"]')[1].trigger('click')
    const avoid = w.find('[data-test="brief-avoid"]')
    await avoid.find('[data-test="brief-chip-input"]').setValue('说教')
    await avoid.find('[data-test="brief-chip-input"]').trigger('keyup.enter')
    await w.find('[data-test="podcast-minutes"]').setValue('15')
    const hosts = w.findAll('[data-test="host-row"]')
    await hosts[0].find('[data-test="host-name"]').setValue('老周')
    await hosts[0].find('[data-test="host-persona"]').setValue('做了十年产品')
    await hosts[0].find('[data-test="host-stance"]').setValue('认为是设计的锅')
    await hosts[0].find('[data-test="host-voice"]').setValue('zhou-voice')
    // 第二位没名字不建
    await w.find('[data-test="podcast-create"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    expect(w.find('.status').text()).toContain('每位主播都要有名字')
    await hosts[1].find('[data-test="host-name"]').setValue('小夏')
    await w.find('[data-test="podcast-create"]').trigger('click'); await flushPromises()
    const created = calls.find(c => c[0] === 'post' && c[1] === '/api/podcast')
    expect(created[2]).toEqual({
      title: '为什么我们戒不掉短视频', format: 'duo', language: 'zh', target_minutes: 15, segments: [],
      brief: { topic: '刷短视频停不下来', angle: '不做道德批判', audience: '上班族', takeaways: ['无限流是怎么设计的'], materials: '老周去年戒了三个月又复发', tone: '轻松但不水', avoid: ['说教'] },
      hosts: [
        { id: 'h1', name: '老周', role: 'host', persona: '做了十年产品', stance: '认为是设计的锅', voice: 'zhou-voice' },
        { id: 'h2', name: '小夏', role: 'cohost', persona: '', stance: '', voice: '' },
      ],
    })
    expect(r.currentRoute.value.path).toBe('/podcast/p/PC-20260916-2/script')
  })
})

describe('podcast · 项目页', () => {
  it('外壳：进项目拉 detail，导航三项，顶部进度 已起草 · 已配音 · 已混音', async () => {
    const { w } = await mountAt(Podcast, `/podcast/p/${ID}/script`)
    expect(calls.some(c => c[0] === 'get' && c[1] === `/api/podcast/${ID}`)).toBe(true)
    const nav = w.findAll('.ws-nav a.nav').map(a => a.attributes('href'))
    expect(nav).toEqual([`/podcast/p/${ID}/script`, `/podcast/p/${ID}/audio`, `/podcast/p/${ID}/advanced`])
    const prog = w.find('[data-test="podcast-progress"]').text()
    expect(prog).toContain('已起草 ✓')
    expect(prog).toContain('已配音 ✓')
    expect(prog).toContain('已混音 ✓')   // 详情带 mix_url → 已混音
    // 详情没有成品时是 —
    detail = sampleDetail(); detail.audio.mix_url = null
    await loadPodcast(ID); await flushPromises()
    expect(w.find('[data-test="podcast-progress"]').text()).toContain('已混音 —')
    expect(w.find('.ws-nav .head').text()).toContain('为什么我们戒不掉短视频')
    expect(w.find('.ws-nav .head').text()).toContain('双人对谈')
  })

  it('脚本页：环节与台词渲染、估算、问题卡；「起草整期」确认后 POST draft 并轮询、cost_note 弹出；拒绝就不发', async () => {
    await loadPodcast(ID)
    const { w } = await mountAt(PodcastScript, `/podcast/p/${ID}/script`)
    const segs = w.findAll('[data-test="segment"]')
    expect(segs.length).toBe(2)
    expect(segs[0].find('[data-test="segment-kind"]').element.value).toBe('cold_open')
    expect(segs[0].find('[data-test="segment-beats"]').element.value).toBe('小夏凌晨两点还在刷')
    expect(segs[0].findAll('[data-test="line-row"]').length).toBe(1)
    expect(segs[0].find('[data-test="line-who"]').element.value).toBe('xiao')
    expect(segs[0].find('[data-test="line-who"]').findAll('option').map(o => o.text())).toEqual(['老周', '小夏'])
    expect(segs[0].find('[data-test="line-cue"]').element.value).toBe('轻笑')
    expect(segs[0].find('[data-test="line-cue"]').findAll('option').map(o => o.attributes('value'))).toEqual(['', '笑', '轻笑', '停顿', '打断', '叹气', '加快', '放慢', '低声', '强调'])
    expect(segs[1].findAll('[data-test="line-row"]').length).toBe(2)
    expect(segs[1].findAll('[data-test="line-kind"]').map(s => s.element.value)).toEqual(['question', 'answer'])
    expect(segs[1].findAll('[data-test="question-row"]').length).toBe(1)
    expect(segs[1].find('[data-test="question-from"]').element.value).toBe('听友阿May')
    expect(segs[0].find('[data-test="segment-questions"]').exists()).toBe(false)
    // 估算 + 问题卡
    const est = w.find('[data-test="script-estimate"]').text()
    expect(est).toContain('62 字')
    expect(est).toContain('3 句')
    expect(est).toContain('目标 12 分钟')
    const probs = w.findAll('[data-test="script-problem"]')
    expect(probs.length).toBe(2)
    expect(probs[0].find('[data-test="problem-target"]').text()).toBe('G02')
    expect(probs[0].text()).toContain('回答复述了问题')
    expect(probs[0].text()).toContain('先给结论再讲原因')
    expect(probs[1].find('[data-test="problem-target"]').text()).toBe('全期')
    expect(probs[0].text()).toContain('播客')
    // 起草整期
    await w.find('[data-test="draft-note"]').setValue('冷开场从凌晨两点开始')
    calls.length = 0
    await w.find('[data-test="draft-btn"]').trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('1 次文本模型请求')
    expect(confirmMock.mock.calls.at(-1)[1]).toMatchObject({ localRole: 'text' })
    const draft = calls.find(c => c[0] === 'post' && c[1] === `/api/podcast/${ID}/draft`)
    expect(draft[2]).toEqual({ note: '冷开场从凌晨两点开始' })
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-draft')).toBe(true)
    expect(toastMock.mock.calls.some(c => String(c[0]).includes('1 次文本模型请求'))).toBe(true)
    expect(calls.some(c => c[0] === 'get' && c[1] === `/api/podcast/${ID}`)).toBe(true)   // 任务完了刷新
    expect(store.gen).toBeNull()
    // 拒绝确认就不发
    calls.length = 0
    confirmMock.mockResolvedValueOnce(false)
    await w.find('[data-test="draft-btn"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
  })

  it('脚本页：「重写这一段」问一句要求 → POST redraft 带 note；取消就不发', async () => {
    await loadPodcast(ID)
    const { w } = await mountAt(PodcastScript, `/podcast/p/${ID}/script`)
    const segs = w.findAll('[data-test="segment"]')
    calls.length = 0
    await segs[1].find('[data-test="segment-redraft"]').trigger('click'); await flushPromises()
    expect(promptMock).toHaveBeenCalled()
    const re = calls.find(c => c[0] === 'post' && c[1] === `/api/podcast/${ID}/segments/G02/redraft`)
    expect(re[2]).toEqual({ note: '老周要多反问' })
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-draft')).toBe(true)
    calls.length = 0
    promptMock.mockResolvedValueOnce(null)
    await segs[0].find('[data-test="segment-redraft"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
  })

  it('脚本页：改台词 / 加句 / 加环节 / 移动 → 保存 PUT 整份（节拍拆行、空 cue 与 talk 不落盘、去掉目录字段）', async () => {
    await loadPodcast(ID)
    const { w } = await mountAt(PodcastScript, `/podcast/p/${ID}/script`)
    expect(w.find('[data-test="script-save"]').attributes('disabled')).toBeDefined()
    const segs = w.findAll('[data-test="segment"]')
    await segs[0].find('[data-test="line-text"]').setValue('……两点十七了。')
    expect(w.find('[data-test="script-save"]').attributes('disabled')).toBeUndefined()
    await segs[0].find('[data-test="segment-beats"]').setValue('小夏凌晨两点还在刷\n老周打断')
    // 加一句：默认换个人说
    await segs[0].find('[data-test="line-add"]').trigger('click')
    const added = segs[0].findAll('[data-test="line-row"]')
    expect(added.length).toBe(2)
    expect(added[1].find('[data-test="line-who"]').element.value).toBe('zhou')
    await added[1].find('[data-test="line-text"]').setValue('几点了？')
    await added[1].find('[data-test="line-cue"]').setValue('停顿')
    // 删掉问答的第二句
    await segs[1].findAll('[data-test="line-remove"]')[1].trigger('click')
    // 加一个环节（G03），再把它上移到问答前面
    await w.find('[data-test="segment-add"]').trigger('click')
    const all = w.findAll('[data-test="segment"]')
    expect(all.length).toBe(3)
    expect(all[2].text()).toContain('G03')
    await all[2].find('[data-test="segment-title"]').setValue('无限流')
    await all[2].find('[data-test="segment-up"]').trigger('click')
    await w.find('[data-test="script-save"]').trigger('click'); await flushPromises()
    const saved = calls.find(c => c[0] === 'put' && c[1] === `/api/podcast/${ID}`)
    expect(saved[2].title).toBe('为什么我们戒不掉短视频')
    expect(saved[2].output_root).toBeUndefined()
    expect(saved[2].project_root).toBeUndefined()
    expect(saved[2].hosts.length).toBe(2)
    expect(saved[2].segments.map(s => s.id)).toEqual(['G01', 'G03', 'G02'])
    expect(saved[2].segments[0].beats).toEqual(['小夏凌晨两点还在刷', '老周打断'])
    expect(saved[2].segments[0].lines).toEqual([
      { who: 'xiao', text: '……两点十七了。', cue: '轻笑' },
      { who: 'zhou', text: '几点了？', cue: '停顿' },
    ])
    expect(saved[2].segments[1]).toEqual({ id: 'G03', kind: 'topic', title: '无限流', goal: '', beats: [], questions: [], lines: [] })
    expect(saved[2].segments[2].lines).toEqual([{ who: 'zhou', text: '阿May 问：把手机设成黑白有用吗？', kind: 'question' }])
    expect(saved[2].segments[2].questions).toEqual([{ from: '听友阿May', text: '把手机设成黑白真的有用吗？' }])
    expect(toastMock.mock.calls.some(c => String(c[0]).includes('已保存'))).toBe(true)
  })

  it('配音页：稿子改过提示、成品播放器与章节、逐句 <audio>、下载链接；「合成整期」确认后 POST synthesize 并轮询；全部重合成带 force', async () => {
    await loadPodcast(ID)
    const { w } = await mountAt(PodcastAudio, `/podcast/p/${ID}/audio`)
    expect(w.find('[data-test="audio-stale"]').text()).toContain('稿子改过，重新合成')
    expect(w.find('[data-test="audio-no-ffmpeg"]').exists()).toBe(false)
    expect(w.find('[data-test="mix-player"]').attributes('src')).toBe(`/api/podcast/${ID}/artifact/audio/${ID}.mp3`)
    const chapters = w.findAll('[data-test="audio-chapter"]')
    expect(chapters.map(c => c.text())).toEqual(['00:00 冷开场', '01:15 听友问答'])
    const lines = w.findAll('[data-test="audio-line"]')
    expect(lines.length).toBe(3)
    expect(lines[0].text()).toContain('小夏')
    expect(lines[0].find('[data-test="line-audio"]').attributes('src')).toBe(`/api/podcast/${ID}/artifact/audio/G01_00.mp3`)
    expect(lines[1].find('[data-test="audio-line-stale"]').exists()).toBe(true)
    expect(lines[0].find('[data-test="audio-line-stale"]').exists()).toBe(false)
    expect(w.find('[data-test="download-shownotes"]').attributes('href')).toBe(`/api/podcast/${ID}/artifact/text/shownotes.md`)
    expect(w.find('[data-test="download-transcript"]').attributes('href')).toBe(`/api/podcast/${ID}/artifact/text/transcript.txt`)
    expect(w.find('[data-test="download-mix"]').attributes('href')).toBe(`/api/podcast/${ID}/artifact/audio/${ID}.mp3`)
    // 主播音色：老周绑了档案（有试听链接），小夏占位
    const hosts = w.findAll('[data-test="audio-host"]')
    expect(hosts[0].find('[data-test="audio-host-voice"]').element.value).toBe('zhou-voice')
    expect(hosts[0].find('[data-test="audio-host-listen"]').attributes('href')).toBe('/voices/zhou-voice')
    expect(hosts[1].find('[data-test="audio-host-placeholder"]').exists()).toBe(true)
    expect(hosts[1].find('[data-test="audio-host-label"]').text()).toContain('占位')
    // 合成整期
    calls.length = 0
    await w.find('[data-test="synth-btn"]').trigger('click'); await flushPromises()
    expect(confirmMock.mock.calls.at(-1)[1]).toMatchObject({ localRole: 'voice' })
    expect(calls.find(c => c[0] === 'post' && c[1] === `/api/podcast/${ID}/synthesize`)[2]).toEqual({})
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-audio')).toBe(true)
    expect(toastMock.mock.calls.some(c => String(c[0]).includes('edge 免费'))).toBe(true)
    expect(store.gen).toBeNull()
    // 拒绝就不发
    calls.length = 0
    confirmMock.mockResolvedValueOnce(false)
    await w.find('[data-test="synth-force"]').trigger('click'); await flushPromises()
    expect(calls.some(c => c[0] === 'post')).toBe(false)
    await w.find('[data-test="synth-force"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1].endsWith('/synthesize'))[2]).toEqual({ force: true })
  })

  // 2026-09-22 播客修复（PC-04 / PC-06）
  it('配音页：成品按旧停顿 / 旧音乐混的（audio.mix_stale）单独提示；稿子改过的提示优先', async () => {
    detail = sampleDetail(); detail.audio.stale = false; detail.audio.mix_stale = true
    await loadPodcast(ID)
    const { w } = await mountAt(PodcastAudio, `/podcast/p/${ID}/audio`)
    expect(w.find('[data-test="audio-stale"]').exists()).toBe(false)
    expect(w.find('[data-test="audio-mix-stale"]').text()).toContain('停顿 / 音乐改过')
    expect(w.find('[data-test="mix-player"]').exists()).toBe(true)      // 旧成品仍可试听
    detail = sampleDetail(); detail.audio.stale = true; detail.audio.mix_stale = true
    await loadPodcast(ID); await flushPromises()
    expect(w.find('[data-test="audio-stale"]').exists()).toBe(true)
    expect(w.find('[data-test="audio-mix-stale"]').exists()).toBe(false)
    detail = sampleDetail(); detail.audio.stale = false; detail.audio.mix_stale = false
    await loadPodcast(ID); await flushPromises()
    expect(w.find('[data-test="audio-mix-stale"]').exists()).toBe(false)
  })

  it('配音页：绑的档案不存在 / 只有描述各有标签；只有描述的档案在下拉里禁选并标出来', async () => {
    voices = [...voices, { id: 'desc', name: '只有描述', engine: 'edge', voice: '' },
              { id: 'cloned', name: '克隆好的', engine: 'minimax', voice: '', clone: { status: 'ready', voice_id: 'mm-1' } }]
    detail = sampleDetail()
    detail.hosts[0] = { ...detail.hosts[0], voice_label: '档案「gone」已不存在，暂用占位音色 zh-CN-YunxiNeural', placeholder: true, missing: true, no_tts: false }
    detail.hosts[1] = { ...detail.hosts[1], voice_label: '档案「只有描述」只有描述，暂用占位音色 zh-CN-XiaoxiaoNeural', placeholder: true, missing: false, no_tts: true }
    await loadPodcast(ID)
    const { w } = await mountAt(PodcastAudio, `/podcast/p/${ID}/audio`)
    const hosts = w.findAll('[data-test="audio-host"]')
    expect(hosts[0].find('[data-test="audio-host-missing"]').text()).toBe('档案已不存在')
    expect(hosts[0].find('[data-test="audio-host-placeholder"]').exists()).toBe(false)
    expect(hosts[1].find('[data-test="audio-host-no-tts"]').text()).toBe('档案只有描述')
    const options = hosts[0].findAll('[data-test="audio-host-voice"] option')
    const desc = options.find(o => o.attributes('value') === 'desc')
    expect(desc.attributes('disabled')).toBeDefined()
    expect(desc.text()).toContain('只有描述，不能出声')
    expect(options.find(o => o.attributes('value') === 'cloned').attributes('disabled')).toBeUndefined()
    expect(options.find(o => o.attributes('value') === 'v2').attributes('disabled')).toBeUndefined()
  })

  it('高级：有任务在跑时删除按钮禁用并说明（服务端同时 409）', async () => {
    detail = sampleDetail(); detail.busy = { draft: null, audio: 't-audio', review: null, export: null }
    await loadPodcast(ID)
    const { w } = await mountAt(PodcastAdvanced, `/podcast/p/${ID}/advanced`)
    expect(w.find('[data-test="podcast-archive"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="podcast-archive-busy"]').text()).toContain('等它结束再删')
    detail = sampleDetail()
    await loadPodcast(ID); await flushPromises()
    expect(w.find('[data-test="podcast-archive"]').attributes('disabled')).toBeUndefined()
    expect(w.find('[data-test="podcast-archive-busy"]').exists()).toBe(false)
  })

  it('配音页：换音色 / 停顿 → 保存 PUT hosts 与 pause；上传垫乐 → POST assets 带 slot；没 ffmpeg 时提示只出了逐句', async () => {
    await loadPodcast(ID)
    const { w } = await mountAt(PodcastAudio, `/podcast/p/${ID}/audio`)
    expect(w.find('[data-test="audio-save"]').attributes('disabled')).toBeDefined()
    const hosts = w.findAll('[data-test="audio-host"]')
    await hosts[1].find('[data-test="audio-host-voice"]').setValue('v2')
    await w.find('[data-test="pause-line"]').setValue('0.5')
    await w.find('[data-test="bed-gain"]').setValue('0.25')
    expect(w.find('[data-test="audio-save"]').attributes('disabled')).toBeUndefined()
    await w.find('[data-test="audio-save"]').trigger('click'); await flushPromises()
    const saved = calls.find(c => c[0] === 'put' && c[1] === `/api/podcast/${ID}`)
    expect(saved[2].hosts[1].voice).toBe('v2')
    expect(saved[2].hosts[0].voice).toBe('zhou-voice')
    expect(saved[2].pause).toEqual({ line: 0.5, segment: 1.2 })
    expect(saved[2].music).toEqual({ intro: '', bed: `content/assets/${ID}/bed.mp3`, outro: '', bed_gain: 0.25, duck: 0.45 })
    expect(saved[2].segments.length).toBe(2)
    expect(saved[2].output_root).toBeUndefined()
    // 上传垫乐
    expect(w.find('[data-test="music-bed"] [data-test="music-file"]').text()).toBe('bed.mp3')
    expect(w.find('[data-test="music-intro"] [data-test="music-file"]').text()).toBe('—')
    const input = w.find('[data-test="music-bed"] [data-test="music-upload"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['x'], 'bed2.mp3', { type: 'audio/mpeg' })] })
    calls.length = 0
    await input.trigger('change'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === `/api/podcast/${ID}/assets`)[2]).toEqual({ filename: 'bed2.mp3', data_base64: 'QUJD', slot: 'bed' })
    expect(calls.some(c => c[0] === 'get' && c[1] === `/api/podcast/${ID}`)).toBe(true)
    // 没 ffmpeg：只出了逐句
    detail = sampleDetail(); detail.audio.ffmpeg = false; detail.audio.mix_url = null; detail.audio.stale = false
    await loadPodcast(ID); await flushPromises()
    expect(w.find('[data-test="audio-no-ffmpeg"]').exists()).toBe(true)
    expect(w.find('[data-test="audio-stale"]').exists()).toBe(false)
    expect(w.find('[data-test="audio-mix"]').exists()).toBe(false)
    expect(w.findAll('[data-test="audio-line"]').length).toBe(3)
  })

  it('高级：JSON 直改 PUT（非法 JSON 就地报错）；删除 = 归档后回模块首页', async () => {
    await loadPodcast(ID)
    const { w, r } = await mountAt(PodcastAdvanced, `/podcast/p/${ID}/advanced`)
    const ta = w.find('[data-test="json-text"]')
    const shown = JSON.parse(ta.element.value)
    expect(shown.title).toBe('为什么我们戒不掉短视频')
    expect(shown.output_root).toBeUndefined()
    await ta.setValue('{ not json')
    await w.find('[data-test="json-save"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="json-error"]').text()).toContain('不是合法的 JSON')
    expect(calls.some(c => c[0] === 'put')).toBe(false)
    await ta.setValue(JSON.stringify({ ...specBody(sampleSpec()), title: '戒不掉短视频 2' }))
    await w.find('[data-test="json-save"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'put')[2].title).toBe('戒不掉短视频 2')
    await w.find('[data-test="podcast-archive"]').trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('「回收站」里恢复')
    expect(calls.some(c => c[0] === 'del' && c[1] === `/api/podcast/${ID}`)).toBe(true)
    expect(store.podcast).toBeNull()
    expect(r.currentRoute.value.path).toBe('/podcast')
  })
})

describe('podcast · 侧栏、任务中心、路由与 helpers', () => {
  it('模块可用时侧栏多一块：列表指向 /podcast/p/…/script；删除 ✕ 归档；不可用时没有', async () => {
    store.podcasts = list; store.podcastsLoaded = true
    const { w, r } = await mountAt(Sidebar, '/')
    const block = w.find('[data-test="sidebar-module-podcast"]')
    expect(block.exists()).toBe(true)
    expect(block.find('[data-test="module-header"]').text()).toContain('AI 播客')
    expect(block.find('[data-test="module-header"]').text()).toContain('2')
    expect(block.findAll('a')[0].attributes('href')).toBe('/podcast')
    expect(block.html()).not.toContain('/podcast/new')
    await block.find('[data-test="module-header"]').trigger('click')
    expect(block.classes()).toContain('open')
    const rows = w.findAll('[data-test="podcast-list"] .proj')
    expect(rows.length).toBe(2)
    expect(rows[0].attributes('href')).toBe(`/podcast/p/${ID}/script`)
    expect(rows[0].text()).toContain('已配音')
    expect(rows[0].text()).toContain('2 位主播')
    expect(rows[1].text()).toContain('还没起草')
    expect(block.find('[data-test="podcast-search"]').exists()).toBe(false)
    await w.findAll('[data-test="podcast-list"] .del')[0].trigger('click'); await flushPromises()
    expect(String(confirmMock.mock.calls.at(-1)[0])).toContain('「回收站」里恢复')
    expect(calls.some(c => c[0] === 'del' && c[1] === `/api/podcast/${ID}`)).toBe(true)
    expect(r.currentRoute.value.path).toBe('/')
    store.modules = store.modules.filter(m => m.id !== 'podcast')
    await w.vm.$nextTick()
    expect(w.find('[data-test="sidebar-module-podcast"]').exists()).toBe(false)
  })

  it('任务中心：种类名与项目地址', () => {
    expect(KIND_LABELS['pc-draft']).toBe('播客起草')
    expect(KIND_LABELS['pc-audio']).toBe('播客配音')
    expect(PROJECT_ROUTES.podcast('PC-1')).toBe('/podcast/p/PC-1/script')
    expect(taskTarget({ kind: 'pc-audio', module: 'podcast', episode_id: 'PC-1' })).toEqual({ id: 'PC-1', url: '/podcast/p/PC-1/script', kind: 'project' })
  })

  it('路由：/podcast 下的路径直接命中；/p/:id 落到 script', async () => {
    const r = createRouter({ history: createMemoryHistory(), routes })
    await r.push('/'); await r.isReady()
    for (const [from, to] of [
      ['/podcast', '/podcast'], ['/podcast/new', '/podcast/new'],
      ['/podcast/p/PC-1', '/podcast/p/PC-1/script'], ['/podcast/p/PC-1/script', '/podcast/p/PC-1/script'],
      ['/podcast/p/PC-1/audio', '/podcast/p/PC-1/audio'], ['/podcast/p/PC-1/advanced', '/podcast/p/PC-1/advanced'],
    ]) {
      await r.push(from)
      expect(r.currentRoute.value.path, from).toBe(to)
      expect(r.currentRoute.value.matched.length, from).toBeGreaterThan(0)
    }
    expect(r.currentRoute.value.params.id).toBe('PC-1')
  })

  it('helpers：进度形状兜底、环节编号、时间码、地址拼接', () => {
    expect(progressOf(list[0])).toEqual({ drafted: true, audio: true, mixed: false, minutes: 12, hosts: 2, segments: 2, lines: 3 })
    // 详情形状：没有 progress 就从 spec / audio 推
    const d = sampleDetail()
    expect(progressOf(d)).toMatchObject({ drafted: true, audio: true, mixed: true, hosts: 2, segments: 2, lines: 3 })
    expect(progressOf(null)).toEqual({ drafted: false, audio: false, mixed: false, minutes: 0, hosts: 0, segments: 0, lines: 0 })
    expect(progressText(list[1])).toBe('PC-20260916-3'.length ? '还没起草 · 1 位主播 · 预计 5 分钟' : '')
    expect(nextSegmentId([{ id: 'G01' }, { id: 'G07' }, { id: 'x' }])).toBe('G08')
    expect(nextSegmentId([])).toBe('G01')
    expect(fmtClock(75)).toBe('01:15')
    expect(fmtClock(0)).toBe('00:00')
    expect(fmtClock(3599.6)).toBe('60:00')
    expect(splitList('说教、念数据不给来源, 排比')).toEqual(['说教', '念数据不给来源', '排比'])
    expect(emptyBrief()).toEqual({ topic: '', angle: '', audience: '', takeaways: [], materials: '', tone: '', avoid: [] })
    expect(podcastRoute('PC-1')).toBe('/podcast/p/PC-1/script')
    expect(podcastRoute('PC-1', 'audio')).toBe('/podcast/p/PC-1/audio')
    expect(audioUrl('PC-1', 'G01_00.mp3')).toBe('/api/podcast/PC-1/artifact/audio/G01_00.mp3')
    expect(audioUrl('PC-1', '')).toBeNull()
    expect(textUrl('PC-1', 'shownotes.md')).toBe('/api/podcast/PC-1/artifact/text/shownotes.md')
    expect(specBody({ id: 'x', output_root: 'o', project_root: 'p', title: 't' })).toEqual({ id: 'x', title: 't' })
  })
})
