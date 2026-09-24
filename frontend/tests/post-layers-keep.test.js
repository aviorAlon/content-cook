// 后期层页（Post.vue）保存时别把界面还不会编辑的字段冲掉：
// PUT post-layers 整块替换 post_graphics / voice_over / subtitle，前端要「展开原对象再覆盖已知字段」。
// 钉住：silence、labels、逐句旁白声线 lines[i].edge、voice_over.subtitles、字幕卡的 background / motion、
// 以及各块里不认识的键，在保存配音 / 配乐 / 字幕样式 / 旁白声线 / 改一句旁白 / 删插卡之后都还在。api 全部 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const puts = []
vi.mock('../src/api.js', () => ({
  get: vi.fn(async () => ({})),
  post: vi.fn(async () => ({})),
  put: vi.fn(async (url, body) => { puts.push([url, JSON.parse(JSON.stringify(body))]); return { status: 'saved' } }),
  del: vi.fn(), ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(), fileToBase64: vi.fn(async () => 'QUJD'),
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({ projectId: 'EP1', detail: { id: 'EP1', locked: false }, postLayers: null, cast: [], voices: [], gen: null })
  return { store, refreshProject: vi.fn(async () => {}), withBusy: vi.fn(async (fn) => fn()), loadVoices: vi.fn(async () => []), reviseEdit: vi.fn() }
})
vi.mock('../src/ui.js', () => ({ confirm: vi.fn(async () => true), notice: vi.fn(), toast: vi.fn(), prompt: vi.fn(), T: {} }))

import { store } from '../src/store.js'
import Post from '../src/views/project/Post.vue'
import { overlay, dubbingBlock, musicRows, withGraphics, subtitleBlock, edgeBlock, START_ANCHOR, insertLinesFromText, subtitleFontOf } from '../src/postlayers.js'

function postLayers() {
  return {
    id: 'EP1', locked: false, shot_ids: ['S1', 'S2'],
    voice_over: {
      voice_ref: 'v1', engine: 'edge', subtitles: true,
      edge: { voice: 'zh-CN-YunxiNeural', rate: '-6%', pitch: '-8Hz', style: 'calm' },
      lines: [
        { shot: 'S1', at: 0.5, zh: '凌晨一点。' },
        { shot: 'S2', at: 0.2, zh: '旁白：外面起风了。', edge: { voice: 'zh-CN-XiaoxiaoNeural', rate: '+4%' } },
      ],
    },
    post_graphics: {
      dubbing: { mode: 'shots', shots: ['S2'], duck: 0.2, note: '只配 S2' },
      music: [{ label: 'BGM-A', file: 'content/assets/EP1/music/a.mp3', start: 0, end: 35, gain: 0.35, duck: 1, fade_in: 2, fade_out: 2, used_in: 'A' }],
      phone_ui_inserts: [
        { id: 'OPEN', after: START_ANCHOR, dur: 5, layout: 'card', background: '#000000', color: '#FFFFFF', motion: 'push', lines: ['本片由 AI 生成'] },
        { id: 'UI_1', after: 'S1', dur: 3, layout: 'plain', lines: ['x'] },
      ],
      notebook_text: [{ shot: 'S2', text: '你好' }],
      silence: [{ shot: 'S2', at: 1.2, dur: 0.8, label: '片中静音' }],
      labels: [{ text: 'AI 生成', position: 'top_right', opacity: 0.9 }],
      fonts: { ui: 'C:/Windows/Fonts/msyh.ttc' },
    },
    subtitle: { language: 'zh', chinese_font_size: 46, top_margin_ratio: 0.1, chinese_color: '#FFFFFF' },
    subtitle_effective: { language: 'zh', position: 'bottom', display: 'speech', chinese_font_size: 46, chinese_color: '#FFFFFF', outline_color: '#111111', outline_width: 4, band_alpha: 0.22, safe_bottom_percent: 7 },
    subtitle_defaults: {}, fonts_available: [], overlays: { preset: 'none', custom: false, regions: [], presets: [] },
    readiness: { dubbing: { shots: ['S2'], missing_tts_cast: {}, lip_risk: [] }, music: [] },
  }
}

async function mountPost() {
  const r = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div/>' } }, { path: '/drama/p/:id/:rest(.*)*', component: { template: '<div/>' } }] })
  await r.push('/'); await r.isReady()
  const w = mount(Post, { global: { plugins: [r] } })
  await flushPromises()
  return w
}

const button = (w, text) => w.findAll('button').find(b => b.text() === text)
const lastBody = () => puts[puts.length - 1][1]

beforeEach(() => { puts.length = 0; store.postLayers = postLayers() })

describe('postlayers.js：展开原对象再覆盖', () => {
  it('overlay 保留不认识的键，undefined = 删键；不是对象的底当空对象', () => {
    expect(overlay({ a: 1, keep: true }, { a: 2, gone: undefined })).toEqual({ a: 2, keep: true })
    expect(overlay({ a: 1, gone: 1 }, { gone: undefined })).toEqual({ a: 1 })
    expect(overlay(null, { a: 1 })).toEqual({ a: 1 })
    expect(overlay(['x'], { a: 1 })).toEqual({ a: 1 })
  })
  it('配音：换成 all 时旧的 shots 清掉，别的键保留', () => {
    expect(dubbingBlock({ mode: 'shots', shots: ['S2'], note: 'n' }, { mode: 'all', shots: ['S2'], duck: '0.3' })).toEqual({ mode: 'all', duck: 0.3, note: 'n' })
    expect(dubbingBlock(undefined, { mode: 'shots', shots: ['S1'], duck: 0.15 })).toEqual({ mode: 'shots', shots: ['S1'], duck: 0.15 })
  })
  it('配乐：条目上不认识的键保留；空文件行不存；空 label 删掉；空 end = null', () => {
    const rows = musicRows([{ label: '', file: 'a.mp3', start: '', end: '', gain: 0.6, duck: 0.3, fade_in: 1, fade_out: 1.5, used_in: 'A' }, { file: '' }])
    expect(rows).toEqual([{ file: 'a.mp3', start: 0, end: null, gain: 0.6, duck: 0.3, fade_in: 1, fade_out: 1.5, used_in: 'A' }])
    expect(withGraphics({ labels: [1], music: [2] }, 'music', [])).toEqual({ labels: [1] })
    expect(withGraphics({ labels: [1] }, 'music', rows)).toEqual({ labels: [1], music: rows })
  })
  it('字幕 / 旁白声线：清空的已知键删掉，不认识的键保留', () => {
    expect(subtitleBlock({ top_margin_ratio: 0.1, font_path: 'old.ttc' }, { font_path: '', chinese_font_size: '50' }, ['font_path', 'chinese_font_size'], ['chinese_font_size']))
      .toEqual({ top_margin_ratio: 0.1, chinese_font_size: 50 })
    expect(edgeBlock({ voice: 'a', rate: '-6%', style: 'calm' }, { voice: 'b', rate: ' ', pitch: '' })).toEqual({ voice: 'b', style: 'calm' })
  })
})

describe('Post.vue：保存一块，别的新字段都还在', () => {
  it('保存配音设置：silence / labels / 插卡 / 配乐原样带回，dubbing 里的未知键也在', async () => {
    const w = await mountPost()
    await button(w, '保存配音设置').trigger('click'); await flushPromises()
    const [url, body] = puts[0]
    expect(url).toBe('/api/episodes/storyvideo/EP1/post-layers')
    const before = postLayers().post_graphics
    expect(body.post_graphics.silence).toEqual(before.silence)
    expect(body.post_graphics.labels).toEqual(before.labels)
    expect(body.post_graphics.phone_ui_inserts).toEqual(before.phone_ui_inserts)
    expect(body.post_graphics.music).toEqual(before.music)
    expect(body.post_graphics.fonts).toEqual(before.fonts)
    expect(body.post_graphics.dubbing).toEqual({ mode: 'shots', shots: ['S2'], duck: 0.2, note: '只配 S2' })
    expect(body.voice_over).toBeUndefined()   // 只提交改动的那一块
  })

  it('保存配乐：条目上的 used_in 与整块的 silence / labels 都在', async () => {
    const w = await mountPost()
    await button(w, '保存配乐').trigger('click'); await flushPromises()
    const pg = lastBody().post_graphics
    expect(pg.music[0]).toMatchObject({ label: 'BGM-A', used_in: 'A', end: 35, gain: 0.35 })
    expect(pg.silence).toHaveLength(1)
    expect(pg.labels[0].text).toBe('AI 生成')
  })

  it('保存字幕样式：spec.subtitle 里表单不认识的键（top_margin_ratio）保留', async () => {
    const w = await mountPost()
    await button(w, '保存字幕样式').trigger('click'); await flushPromises()
    expect(lastBody().subtitle).toMatchObject({ top_margin_ratio: 0.1, language: 'zh', chinese_font_size: 46 })
  })

  it('改旁白声线 / 改一句旁白：subtitles 开关与逐句 edge 都在', async () => {
    const w = await mountPost()
    await button(w, '声线').trigger('click'); await flushPromises()
    await button(w, '保存').trigger('click'); await flushPromises()
    let vo = lastBody().voice_over
    expect(vo.subtitles).toBe(true)
    expect(vo.edge).toEqual({ voice: 'zh-CN-YunxiNeural', rate: '-6%', pitch: '-8Hz', style: 'calm' })
    expect(vo.lines[1].edge).toEqual({ voice: 'zh-CN-XiaoxiaoNeural', rate: '+4%' })

    const editButtons = w.findAll('button').filter(b => b.text() === '改')
    await editButtons[0].trigger('click'); await flushPromises()     // 第一句旁白
    await button(w, '保存').trigger('click'); await flushPromises()
    vo = lastBody().voice_over
    expect(vo.subtitles).toBe(true)
    expect(vo.lines[1].edge).toEqual({ voice: 'zh-CN-XiaoxiaoNeural', rate: '+4%' })
  })

  it('改片头字幕卡并保存：background / motion / after=__start__ 都在，silence / labels 也在', async () => {
    const w = await mountPost()
    const rows = w.findAll('tr').filter(tr => tr.text().includes('OPEN'))
    expect(rows[0].find('[data-test="insert-at-start"]').text()).toBe('片头（第一镜之前）')
    await rows[0].findAll('button').find(b => b.text() === '改').trigger('click'); await flushPromises()
    const afterSelect = w.findAll('select').find(s => s.findAll('option').some(o => o.element.value === START_ANCHOR))
    expect(afterSelect.element.value).toBe(START_ANCHOR)
    await button(w, '保存').trigger('click'); await flushPromises()
    const pg = lastBody().post_graphics
    expect(pg.phone_ui_inserts[0]).toEqual(postLayers().post_graphics.phone_ui_inserts[0])
    expect(pg.silence).toHaveLength(1)
    expect(pg.labels).toHaveLength(1)
  })

  it('删一张插卡：别的块原样带回', async () => {
    const w = await mountPost()
    const row = w.findAll('tr').find(tr => tr.text().includes('UI_1'))
    await row.findAll('button').find(b => b.text() === '删').trigger('click'); await flushPromises()
    const pg = lastBody().post_graphics
    expect(pg.phone_ui_inserts.map(x => x.id)).toEqual(['OPEN'])
    expect(pg.silence).toHaveLength(1)
    expect(pg.labels).toHaveLength(1)
    expect(pg.dubbing.note).toBe('只配 S2')
  })
})

describe('字幕卡文字框：中间的空行是行距，别吃掉', () => {
  it('insertLinesFromText：card 只去首尾空行，别的版式照旧丢空行', () => {
    expect(insertLinesFromText('声音由 AI 合成\n\n夜行（完）', 'card')).toEqual(['声音由 AI 合成', '', '夜行（完）'])
    expect(insertLinesFromText('\n \na\r\n\r\nb\n\n', 'card')).toEqual(['a', '', 'b'])
    expect(insertLinesFromText('', 'card')).toEqual([])
    expect(insertLinesFromText('a\n\nb\n', 'plain')).toEqual(['a', 'b'])
    expect(insertLinesFromText('a\n\nb', undefined)).toEqual(['a', 'b'])
  })

  const endCard = { id: 'END', after: 'S2', dur: 4, layout: 'card', lines: ['声音由 AI 合成', '', '夜行'] }
  async function editInsert(w, id) {
    const row = w.findAll('tr').find(tr => tr.text().includes(id))
    await row.findAll('button').find(b => b.text() === '改').trigger('click'); await flushPromises()
    return w.findAll('textarea').find(t => t.element.closest('.field')?.textContent.includes('文字（每行一条）'))
  }

  it('改字幕卡的一行字：中间的空行保存后还在', async () => {
    store.postLayers.post_graphics.phone_ui_inserts.push(JSON.parse(JSON.stringify(endCard)))
    const w = await mountPost()
    const area = await editInsert(w, 'END')
    expect(area.element.value).toBe('声音由 AI 合成\n\n夜行')
    await area.setValue('声音由 AI 合成\n\n夜行（完）')
    await button(w, '保存').trigger('click'); await flushPromises()
    const saved = lastBody().post_graphics.phone_ui_inserts.find(x => x.id === 'END')
    expect(saved.lines).toEqual(['声音由 AI 合成', '', '夜行（完）'])
    expect(lastBody().post_graphics.silence).toHaveLength(1)
  })

  it('在文字框末尾敲回车：回车留在框里（不被当场按 lines 重排吃掉），接着敲的字成为新的一行', async () => {
    store.postLayers.post_graphics.phone_ui_inserts.push(JSON.parse(JSON.stringify(endCard)))
    const w = await mountPost()
    const area = await editInsert(w, 'END')
    await area.setValue('声音由 AI 合成\n\n夜行\n'); await flushPromises()
    expect(area.element.value).toBe('声音由 AI 合成\n\n夜行\n')
    await area.setValue('声音由 AI 合成\n\n夜行\n\n导演'); await flushPromises()
    await button(w, '保存').trigger('click'); await flushPromises()
    expect(lastBody().post_graphics.phone_ui_inserts.find(x => x.id === 'END').lines).toEqual(['声音由 AI 合成', '', '夜行', '', '导演'])
  })

  it('没碰文字框：只写了 screen 的字幕卡保存后不会多出空的 lines（后端会当成「没有文字」）', async () => {
    store.postLayers.post_graphics.phone_ui_inserts.push({ id: 'END', after: 'S2', dur: 4, layout: 'card', screen: '声音由 AI 合成' })
    const w = await mountPost()
    await editInsert(w, 'END')
    await button(w, '保存').trigger('click'); await flushPromises()
    const saved = lastBody().post_graphics.phone_ui_inserts.find(x => x.id === 'END')
    expect(saved).toEqual({ id: 'END', after: 'S2', dur: 4, layout: 'card', screen: '声音由 AI 合成' })
  })

  it('纯文字版式照旧：空行不存', async () => {
    const w = await mountPost()
    const area = await editInsert(w, 'UI_1')
    await area.setValue('x\n\ny')
    await button(w, '保存').trigger('click'); await flushPromises()
    expect(lastBody().post_graphics.phone_ui_inserts.find(x => x.id === 'UI_1').lines).toEqual(['x', 'y'])
  })
})

describe('字幕字体的旧别名 fontfile', () => {
  const KAI = 'C:/Windows/Fonts/simkai.ttf'
  function withFontfile() {
    store.postLayers.subtitle = { language: 'zh', fontfile: KAI }
    store.postLayers.subtitle_effective = { ...store.postLayers.subtitle_effective, font_path: '', fontfile: KAI }
  }
  const fontSelect = (w) => w.findAll('select').find(s => s.findAll('option').some(o => o.text() === '自动（系统中文字体）'))

  it('subtitleFontOf / subtitleBlock：显示取 font_path || fontfile；保存时别名删掉', () => {
    expect(subtitleFontOf({ fontfile: KAI })).toBe(KAI)
    expect(subtitleFontOf({ font_path: 'a.ttc', fontfile: KAI })).toBe('a.ttc')
    expect(subtitleFontOf(null)).toBe('')
    expect(subtitleBlock({ fontfile: KAI, top_margin_ratio: 0.1 }, { font_path: '' }, ['font_path'])).toEqual({ top_margin_ratio: 0.1 })
    expect(subtitleBlock({ fontfile: KAI }, { font_path: KAI }, ['font_path'])).toEqual({ font_path: KAI })
    // 表单不管 font_path 时别名不动
    expect(subtitleBlock({ fontfile: KAI }, { language: 'zh' }, ['language'])).toEqual({ fontfile: KAI, language: 'zh' })
  })

  it('表单显示实际在用的 fontfile；原样保存 = 换成 font_path', async () => {
    withFontfile()
    const w = await mountPost()
    expect(fontSelect(w).element.value).toBe(KAI)
    await button(w, '保存字幕样式').trigger('click'); await flushPromises()
    expect(lastBody().subtitle.font_path).toBe(KAI)
    expect(lastBody().subtitle.fontfile).toBeUndefined()
  })

  it('字体选「自动」保存：font_path 与 fontfile 都删掉，真的回到默认字体', async () => {
    withFontfile()
    const w = await mountPost()
    await fontSelect(w).setValue('')
    await button(w, '保存字幕样式').trigger('click'); await flushPromises()
    expect(lastBody().subtitle.font_path).toBeUndefined()
    expect(lastBody().subtitle.fontfile).toBeUndefined()
    expect(lastBody().subtitle.language).toBe('zh')
  })
})
