// 播客交付包（配音页底部的「导出交付包」卡）：已有交付包列表来自 GET /export（名字 / 大小 / 时间 / 下载链接 / 没混音标记），
// 拉不到时退回 detail.exports；按钮起 POST /export 并轮询任务、完成后重拉列表；没合成过按钮禁用并提示；在跑时禁用；
// 任务中心种类名 pc-export；podcast.js 的 exportUrl / normalizeExports / fmtBytes / fmtWhen。api 全部 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let detail, exportsResponse, exportsFail, taskResult
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/voices') return [{ id: 'zhou-voice', name: '老周的音色', engine: 'edge' }]
    if (url.startsWith('/api/tasks/')) return taskResult
    if (url.endsWith('/export')) { if (exportsFail) throw new Error('boom'); return exportsResponse }
    if (url.startsWith('/api/podcast/')) return detail
    return {}
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    if (url.endsWith('/export')) return { task_id: 't-export', name: 'PC-20260916-1-20260916-1300.zip' }
    if (url.endsWith('/synthesize')) return { task_id: 't-audio' }
    return { ok: true }
  }),
  put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return { spec: body } }),
  del: vi.fn(async () => ({})),
  ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(async (id) => { calls.push(['poll', id]); return taskResult }),
  pollJob: vi.fn(), sleep: vi.fn(async () => {}), fileToBase64: vi.fn(async () => 'QUJD'),
}))
const toastMock = vi.fn()
const noticeMock = vi.fn()
vi.mock('../src/ui.js', () => ({
  prompt: vi.fn(async () => ''), confirm: vi.fn(async () => true), notice: (...a) => noticeMock(...a), toast: (...a) => toastMock(...a),
  custom: vi.fn(), toasts: [],
  T: { role: { text: '剧本文本', image: '生图', voice: '语音', video: '视频', music: '配乐' } },
}))

import { store } from '../src/store.js'
import { KIND_LABELS, kindLabel, taskTarget } from '../src/tasks.js'
import { loadPodcast, clearPodcast, exportUrl, normalizeExports, loadExports, fmtBytes, fmtWhen } from '../src/podcast.js'
import PodcastAudio from '../src/views/podcast/PodcastAudio.vue'

const stub = { template: '<div/>' }
async function mountAudio() {
  const r = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: stub }, { path: '/voices/:voiceId?', component: stub }, { path: '/podcast/p/:id/:sub', component: stub },
  ] })
  await r.push(`/podcast/p/${ID}/audio`); await r.isReady()
  const w = mount(PodcastAudio, { global: { plugins: [r] } })
  await flushPromises()
  return w
}

const ID = 'PC-20260916-1'
function sampleDetail() {
  const spec = {
    id: ID, title: '为什么我们戒不掉短视频', format: 'duo', language: 'zh', brief: { topic: 't' }, target_minutes: 12,
    hosts: [{ id: 'zhou', name: '老周', role: 'host', voice: 'zhou-voice' }, { id: 'xiao', name: '小夏', role: 'cohost', voice: '' }],
    segments: [{ id: 'G01', kind: 'cold_open', title: '冷开场', lines: [{ who: 'xiao', text: '两点十七。' }] }],
    music: { intro: '', bed: '', outro: '', bed_gain: 0.18, duck: 0.45 }, pause: { line: 0.35, segment: 1.2 }, draft: {},
  }
  const lines = [{ segment: 'G01', index: 1, who: 'xiao', text: '两点十七。', file: 'G01_01.mp3', duration: 3.2, stale: false, url: `/api/podcast/${ID}/artifact/audio/G01_01.mp3` }]
  return {
    id: ID, module: 'podcast', title: spec.title, spec, estimate: { lines: 1, minutes: 0.1 }, warnings: [], problems: [],
    hosts: spec.hosts.map(h => ({ ...h, voice_label: h.voice ? '老周的音色' : '占位', placeholder: !h.voice })),
    audio: { lines, mix_url: `/api/podcast/${ID}/artifact/audio/${ID}.mp3`, mix_duration: 95, chapters: [], stale: false, ffmpeg: true, synthesized: 1, total: 1 },
    texts: { shownotes_url: null, transcript_url: null }, draft: {},
    exports: [{ name: 'PC-20260916-1-20260915-0900.zip', size: 2048, created: '2026-09-15T09:00:12+08:00', files: 9, bytes: 1900, mixed: true, lines: 1 }],
    busy: { draft: null, audio: null, export: null }, limits: { target_minutes: [1, 90], hosts: [1, 4] },
  }
}
const PACKAGES = [
  { name: 'PC-20260916-1-20260916-1200.zip', size: 3 * 1024 * 1024 + 200000, created: '2026-09-16T12:00:30+08:00', files: 9, bytes: 3000000, mixed: true, lines: 1,
    url: `/api/podcast/${ID}/export/PC-20260916-1-20260916-1200.zip` },
  { name: 'PC-20260916-1-20260916-1130.zip', size: 40 * 1024, created: '2026-09-16T11:30:05+08:00', files: 5, bytes: 39000, mixed: false, lines: 1 },
]

beforeEach(() => {
  calls.length = 0
  toastMock.mockReset(); noticeMock.mockReset()
  detail = sampleDetail()
  exportsResponse = { id: ID, packages: PACKAGES, export_dir: `output/podcast/${ID}/09_export` }
  exportsFail = false
  taskResult = { status: 'succeeded', result: { name: 'PC-20260916-1-20260916-1300.zip', files: 9, mixed: true, lines: 1 } }
  clearPodcast(); store.gen = null; store.voices = null
})

describe('podcast · 导出交付包', () => {
  it('列表来自 GET /export：名字 / 大小 / 时间 / 下载链接，没混音的带标记；说明写清包里有什么', async () => {
    await loadPodcast(ID)
    const w = await mountAudio()
    expect(calls.some(c => c[0] === 'get' && c[1] === `/api/podcast/${ID}/export`)).toBe(true)
    const card = w.find('[data-test="audio-export"]')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('AI 生成说明与工具清单')
    expect(card.text()).toContain(`output/podcast/${ID}/09_export/`)
    expect(card.find('[data-test="export-empty"]').exists()).toBe(false)
    const rows = card.findAll('[data-test="export-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].text()).toContain('PC-20260916-1-20260916-1200.zip')
    expect(rows[0].text()).toContain('3.2 MB')
    expect(rows[0].text()).toContain('2026-09-16 12:00')
    expect(rows[0].find('[data-test="export-download"]').attributes('href')).toBe(`/api/podcast/${ID}/export/PC-20260916-1-20260916-1200.zip`)
    expect(rows[0].find('[data-test="export-no-mix"]').exists()).toBe(false)
    // 后端没给 url 的行也能下载（前端拼）；没混音的带标记
    expect(rows[1].find('[data-test="export-download"]').attributes('href')).toBe(`/api/podcast/${ID}/export/PC-20260916-1-20260916-1130.zip`)
    expect(rows[1].text()).toContain('40 KB')
    expect(rows[1].find('[data-test="export-no-mix"]').text()).toBe('没混音')
    // 按钮可用（合成过）
    const btn = card.find('[data-test="export-btn"]')
    expect(btn.attributes('disabled')).toBeUndefined()
    expect(card.find('[data-test="export-need-audio"]').exists()).toBe(false)
  })

  it('点「导出交付包」→ POST /export（不问确认）并轮询任务，完成后提示并重拉列表；失败弹 notice', async () => {
    await loadPodcast(ID)
    const w = await mountAudio()
    calls.length = 0
    exportsResponse = { id: ID, packages: [PACKAGES[0], { name: 'PC-20260916-1-20260916-1300.zip', size: 10, created: '2026-09-16T13:00:00+08:00', mixed: true }] }
    await w.find('[data-test="export-btn"]').trigger('click'); await flushPromises()
    expect(calls.find(c => c[0] === 'post' && c[1] === `/api/podcast/${ID}/export`)[2]).toEqual({})
    expect(calls.some(c => c[0] === 'poll' && c[1] === 't-export')).toBe(true)
    expect(toastMock.mock.calls.some(c => String(c[0]).includes('导出交付包完成'))).toBe(true)
    expect(calls.filter(c => c[0] === 'get' && c[1] === `/api/podcast/${ID}/export`).length).toBeGreaterThanOrEqual(1)
    expect(store.gen).toBeNull()
    expect(w.findAll('[data-test="export-row"]').map(r => r.text())).toEqual(expect.arrayContaining([expect.stringContaining('PC-20260916-1-20260916-1300.zip')]))
    // 失败
    taskResult = { status: 'failed', error: '这一分钟已经导出过一个交付包了' }
    await w.find('[data-test="export-btn"]').trigger('click'); await flushPromises()
    expect(noticeMock.mock.calls.some(c => String(c[0]).includes('导出交付包失败'))).toBe(true)
  })

  it('没合成过：按钮禁用并提示先合成；在跑：禁用并显示正在打包；GET /export 挂了退回 detail.exports；空列表提示', async () => {
    detail = sampleDetail()
    detail.audio.synthesized = 0; detail.audio.mix_url = null; detail.audio.lines = []; detail.exports = []
    exportsResponse = { id: ID, packages: [] }
    await loadPodcast(ID)
    const w = await mountAudio()
    expect(w.find('[data-test="export-btn"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="export-need-audio"]').text()).toBe('先合成，才有东西可导。')
    expect(w.find('[data-test="export-empty"]').text()).toBe('还没有导出过。')
    // 在跑
    detail = sampleDetail(); detail.busy.export = 't-export'
    exportsFail = true
    await loadPodcast(ID); await flushPromises()
    expect(w.find('[data-test="export-btn"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="audio-export"]').text()).toContain('正在打包…')
    // 接口挂了：用 detail.exports（第一次 mount 时的 GET /export 成功过但返回空，切项目重拉失败 → 退回 detail）
    detail = sampleDetail(); detail.id = 'PC-20260916-2'; detail.spec.id = 'PC-20260916-2'
    detail.exports = [{ name: 'PC-20260916-2-20260916-1000.zip', size: 1024, created: '2026-09-16T10:00:00+08:00', mixed: true }]
    await loadPodcast('PC-20260916-2'); await flushPromises()
    const rows = w.findAll('[data-test="export-row"]')
    expect(rows.length).toBe(1)
    expect(rows[0].find('[data-test="export-download"]').attributes('href')).toBe('/api/podcast/PC-20260916-2/export/PC-20260916-2-20260916-1000.zip')
  })

  it('任务中心种类名与 helpers', async () => {
    expect(KIND_LABELS['pc-export']).toBe('播客导出')
    expect(kindLabel('pc-export')).toBe('播客导出')
    expect(taskTarget({ kind: 'pc-export', module: 'podcast', episode_id: 'PC-1' })).toEqual({ id: 'PC-1', url: '/podcast/p/PC-1/script', kind: 'project' })
    expect(exportUrl('PC-1', 'a b.zip')).toBe('/api/podcast/PC-1/export/a%20b.zip')
    expect(exportUrl('PC-1', '')).toBeNull()
    expect(normalizeExports('PC-1', { packages: [{ name: 'x.zip' }, { name: '' }, null] })).toEqual([{ name: 'x.zip', url: '/api/podcast/PC-1/export/x.zip' }])
    expect(normalizeExports('PC-1', [{ name: 'y.zip', url: '/u' }])).toEqual([{ name: 'y.zip', url: '/u' }])
    expect(normalizeExports('PC-1', null)).toEqual([])
    exportsResponse = { packages: [{ name: 'z.zip' }] }
    expect(await loadExports(ID)).toEqual([{ name: 'z.zip', url: `/api/podcast/${ID}/export/z.zip` }])
    exportsFail = true
    expect(await loadExports(ID)).toBeNull()
    expect(fmtBytes(0)).toBe('—'); expect(fmtBytes(500)).toBe('1 KB'); expect(fmtBytes(40 * 1024)).toBe('40 KB'); expect(fmtBytes(3.25 * 1024 * 1024)).toBe('3.3 MB')
    expect(fmtWhen('2026-09-16T12:00:30+08:00')).toBe('2026-09-16 12:00'); expect(fmtWhen('')).toBe('')
  })
})
