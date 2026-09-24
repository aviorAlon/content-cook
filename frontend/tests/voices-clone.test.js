// 音色库 · 参考音频与克隆（DESIGN §33）：目录接口按密钥给 engines → 引擎下拉多出 MiniMax；选文件 → base64 上传 → 播放器；
// 「克隆音色」→ 后台任务 → 轮档案的 clone.status → 状态药丸。api 全部 mock，不联网。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const { calls, state } = vi.hoisted(() => ({ calls: [], state: { engines: ['edge', 'settings', 'minimax'], cloneOutcome: 'ready' } }))
const girl = () => ({ id: 'girl_a', name: '小雨', engine: 'edge', voice: 'zh-CN-XiaoxiaoNeural', description: '', traits: {}, has_sample: false, updated_at: 1 })

vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  const store = reactive({ voices: null })
  return {
    store,
    withBusy: vi.fn(async (fn) => fn()),
    loadVoices: vi.fn(async () => {
      calls.push(['loadVoices'])
      if (store.voices === null) store.voices = [{ id: 'girl_a', name: '小雨', engine: 'edge', voice: 'zh-CN-XiaoxiaoNeural', description: '', traits: {}, has_sample: false, updated_at: 1 }]
      // 克隆中 → 轮询一次后按 state.cloneOutcome 落定
      const v = store.voices[0]
      if (v.clone?.status === 'cloning') {
        const done = state.cloneOutcome === 'ready'
          ? { ...v.clone, status: 'ready', cloned_at: 3 }
          : { ...v.clone, status: 'failed', error: '克隆失败：余额或配额不足（minimax 1008）' }
        store.voices = [{ ...v, clone: done, engine: done.status === 'ready' ? 'minimax' : v.engine, voice: done.status === 'ready' ? done.voice_id : v.voice }]
      }
      return store.voices
    }),
  }
})
vi.mock('../src/api.js', async () => {
  const { store } = await import('../src/store.js')
  return {
    get: vi.fn(async (url) => {
      calls.push(['get', url])
      if (url.startsWith('/api/voices/catalog')) return { adapter: 'edge_tts', voices: [], note: '', engines: state.engines }
      return {}
    }),
    post: vi.fn(async (url, body) => {
      calls.push(['post', url, body])
      if (url.endsWith('/reference-audio')) {
        store.voices = [{ ...girl(), reference_audio: 'reference/girl_a-abcd1234-1.wav', has_reference_audio: true, clone: { provider: '', voice_id: '', status: 'none', cloned_at: null, error: '' }, updated_at: 2 }]
        return { ...store.voices[0], bytes: 3, reference_url: '/api/voices/girl_a/reference-audio?t=2' }
      }
      if (url.endsWith('/clone')) {
        store.voices = [{ ...store.voices[0], clone: { ...store.voices[0].clone, status: 'cloning', provider: 'minimax', voice_id: 'ss_girl_a_abcd1234' } }]
        return { task_id: 't1', id: 'girl_a', clone: store.voices[0].clone }
      }
      return {}
    }),
    put: vi.fn(async (url, body) => { calls.push(['put', url, body]); return {} }),
    del: vi.fn(),
    fileToBase64: vi.fn(async () => 'QUJD'),
  }
})
vi.mock('../src/ui.js', () => ({ confirm: vi.fn(async () => true), notice: vi.fn(async () => {}), prompt: vi.fn(async () => '') }))

import { store } from '../src/store.js'
import Voices from '../src/views/Voices.vue'

async function mountVoices() {
  const r = createRouter({ history: createMemoryHistory(), routes: [{ path: '/voices/:voiceId?', component: Voices }] })
  r.push('/voices/girl_a'); await r.isReady()
  const w = mount(Voices, { global: { plugins: [r] } })
  await flushPromises()
  return w
}

function pickFile(input, name) {
  const file = new File(['abc'], name, { type: 'audio/wav' })
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  return input.trigger('change')
}

beforeEach(() => { calls.length = 0; store.voices = null; state.engines = ['edge', 'settings', 'minimax']; state.cloneOutcome = 'ready' })

describe('Voices · 参考音频与克隆', () => {
  it('配了 MiniMax 密钥：引擎下拉有 MiniMax；没参考音频时不能克隆、药丸是「未克隆」', async () => {
    const w = await mountVoices()
    const options = w.find('[data-test="engine-select"]').findAll('option').map(o => o.attributes('value'))
    expect(options).toEqual(['edge', 'settings', 'minimax'])
    const card = w.find('[data-test="voice-clone"]')
    expect(card.exists()).toBe(true)
    expect(card.find('[data-test="clone-run"]').attributes('disabled')).toBeDefined()
    expect(card.find('[data-test="clone-status"]').text()).toBe('未克隆')
    expect(card.find('[data-test="ref-audio-player"]').exists()).toBe(false)
    expect(card.find('[data-test="clone-no-key"]').exists()).toBe(false)
  })

  it('没配密钥：下拉没有 MiniMax，卡片提示去配 key，按钮禁用', async () => {
    state.engines = ['edge', 'settings']
    const w = await mountVoices()
    const options = w.find('[data-test="engine-select"]').findAll('option').map(o => o.attributes('value'))
    expect(options).toEqual(['edge', 'settings'])
    expect(w.find('[data-test="clone-no-key"]').exists()).toBe(true)
    expect(w.find('[data-test="clone-run"]').attributes('disabled')).toBeDefined()
  })

  it('选文件 → base64 上传 → 显示文件名与播放器；克隆 → 轮询 → 已克隆并切到 MiniMax 引擎', async () => {
    const w = await mountVoices()
    await pickFile(w.find('[data-test="ref-audio-file"]'), '我的声音.wav')
    await flushPromises()
    const upload = calls.find(c => c[0] === 'post' && c[1] === '/api/voices/girl_a/reference-audio')
    expect(upload[2]).toEqual({ filename: '我的声音.wav', data_base64: 'QUJD' })
    expect(w.find('[data-test="ref-audio-name"]').text()).toBe('girl_a-abcd1234-1.wav')
    expect(w.find('[data-test="ref-audio-player"]').attributes('src')).toContain('/api/voices/girl_a/reference-audio')
    expect(w.find('[data-test="clone-run"]').attributes('disabled')).toBeUndefined()
    await w.find('[data-test="clone-run"]').trigger('click')
    await flushPromises()
    expect(calls.some(c => c[0] === 'put' && c[1] === '/api/voices/girl_a')).toBe(true)   // 先保存表单
    expect(calls.some(c => c[0] === 'post' && c[1] === '/api/voices/girl_a/clone')).toBe(true)
    expect(w.find('[data-test="clone-status"]').text()).toBe('已克隆：ss_girl_a_abcd1234')
    expect(w.find('[data-test="clone-status"]').classes()).toContain('ok')
    expect(w.find('[data-test="engine-select"]').element.value).toBe('minimax')
    expect(w.text()).toContain('已克隆')
  })

  it('克隆失败：药丸显示原因', async () => {
    state.cloneOutcome = 'failed'
    const w = await mountVoices()
    await pickFile(w.find('[data-test="ref-audio-file"]'), 'a.mp3')
    await flushPromises()
    await w.find('[data-test="clone-run"]').trigger('click')
    await flushPromises()
    const pill = w.find('[data-test="clone-status"]')
    expect(pill.text()).toBe('失败：克隆失败：余额或配额不足（minimax 1008）')
    expect(pill.classes()).toContain('bad')
    expect(w.find('[data-test="clone-run"]').attributes('disabled')).toBeUndefined()   // 可以再试
  })

  it('上传接口报错显示在状态条，不吞掉', async () => {
    const { post } = await import('../src/api.js')
    post.mockImplementationOnce(async () => { throw new Error('参考音频只接受 mp3 / wav / m4a，收到的是 .ogg') })
    const w = await mountVoices()
    await pickFile(w.find('[data-test="ref-audio-file"]'), 'a.ogg')
    await flushPromises()
    expect(w.find('.status').text()).toBe('参考音频只接受 mp3 / wav / m4a，收到的是 .ogg')
  })

  it('轮询克隆进度时读不到（重试完仍失败）：状态条说清楚，不再悄悄停在「克隆中」', async () => {
    const { loadVoices } = await import('../src/store.js')
    const w = await mountVoices()
    await pickFile(w.find('[data-test="ref-audio-file"]'), 'a.mp3')
    await flushPromises()
    // 轮询那一次（带 retry）失败；以前 loadVoices 吞掉错误、清空列表，轮询悄悄停掉
    loadVoices.mockImplementationOnce(async (opts) => {
      expect(opts).toEqual({ retry: true })
      throw Object.assign(new Error('连不上本机服务'), { status: 0 })
    })
    await w.find('[data-test="clone-run"]').trigger('click')
    await flushPromises()
    expect(w.find('.status').text()).toBe('读不到克隆进度：连不上本机服务——克隆还在后台进行，稍后刷新页面再看')
    expect(w.find('.status').classes()).toContain('err')
    // 最后读到的状态仍是克隆中：药丸照实显示，克隆按钮保持禁用（后端对进行中的克隆也回 409）
    expect(w.find('[data-test="clone-status"]').text()).toBe('克隆中…')
    expect(w.find('[data-test="clone-run"]').attributes('disabled')).toBeDefined()
  })
})
