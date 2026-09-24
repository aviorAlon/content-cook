// 模型设置 · 「用它配 …」/「应用到勾选的角色」只重灌被应用的角色卡（发 tag 前审计 FE-08）：
// 后端按设计把全部角色的已保存值都带回来，前端以前拿这份整表覆盖五张卡，别的卡里没保存的输入静默丢掉。
// 钉住：只有响应 changed 里的角色卡重灌，其余卡的输入原样保留；对照组「保存」本来就只动自己那张卡。api 全 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const calls = []
const responses = {}
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => { calls.push(['GET', url]); return responses[url] }),
  put: vi.fn(async (url, body) => { calls.push(['PUT', url, body]); return responses['PUT ' + url] }),
  post: vi.fn(async (url, body) => { calls.push(['POST', url, body]); return responses['POST ' + url] }),
  pollTask: vi.fn(),
}))
vi.mock('../src/store.js', () => ({ store: { health: null }, loadHealth: vi.fn() }))

import Settings from '../src/views/Settings.vue'

const ADAPTERS = {
  text: { openai_chat: { label: 'OpenAI 兼容', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
  image: { openai_images: { label: 'OpenAI 图', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
  voice: { openai_speech: { label: 'OpenAI 语音', fields: ['base_url', 'api_key', 'model', 'voice'], defaults: {} } },
  video: { openai_video: { label: 'OpenAI 视频', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
}
const SAVED = {
  text: { adapter: 'openai_chat', base_url: 'https://t.example/v1', api_key: '****', model: 'saved-text-model' },
  image: { adapter: 'openai_images', base_url: 'https://i.example/v1', api_key: '****', model: 'saved-image-model' },
  voice: { adapter: 'openai_speech', base_url: 'https://v.example/v1', api_key: '****', model: 'tts-1', voice: 'nova' },
  video: { adapter: 'openai_video', base_url: 'https://vid.example/v1', api_key: '****', model: 'saved-video-model' },
}
const GATEWAY = { base_url: 'http://gw/v1', api_key: '****' }
const clone = (x) => JSON.parse(JSON.stringify(x))
function doc(roles = SAVED) {
  return { roles: ['text', 'image', 'voice', 'video'], adapters: ADAPTERS, settings: { roles: clone(roles), update_url: '', gateway: GATEWAY }, status: { text: [], image: [], voice: [], video: [] } }
}
const EMPTY_CATALOG = { source: 'x', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
const SCAN = {
  services: [{ id: 'ollama', name: 'Ollama', roles: ['text'], base_url: 'http://127.0.0.1:11434/v1', alive: true, models: ['qwen2.5:7b'], error: '' }],
  alive: ['ollama'], presets: [], roles: ['text', 'image', 'voice', 'video'],
}

// 角色卡：按标题找；模型 id 是卡里最后一个文本框（base_url / model 是 text，api_key 是 password）
const roleCard = (w, title) => w.findAll('.card').find((c) => c.find('h3').exists() && c.find('h3').text() === title)
const modelInput = (w, title) => roleCard(w, title).findAll('input[type="text"]').at(-1)
const keyInput = (w, title) => roleCard(w, title).find('input[type="password"]')

beforeEach(() => {
  calls.length = 0
  for (const k of Object.keys(responses)) delete responses[k]
  responses['/api/settings'] = doc()
  responses['/api/system/asr'] = { installed: false, frozen: false, models: [], default_model: 'small', install_hint: '' }
  responses['/api/system/model-catalog'] = EMPTY_CATALOG
  responses['/api/system/model-catalog?source=gateway'] = EMPTY_CATALOG
  responses['/api/system/local-scan'] = SCAN
  responses['/api/system/local-workflows'] = { workflows: [], user_dir: '', note: '' }
})

describe('Settings · 应用只重灌被应用的角色卡', () => {
  it('对照组：视频卡「保存」只重灌视频卡，生图卡里没保存的输入还在', async () => {
    responses['PUT /api/settings'] = { settings: { roles: { ...clone(SAVED), video: { ...SAVED.video, model: 'new-video-model' } }, gateway: GATEWAY }, status: doc().status }
    const w = mount(Settings); await flushPromises()
    await modelInput(w, '生图模型（首帧 / 定妆）').setValue('my-unsaved-image-model')
    await modelInput(w, '视频模型').setValue('new-video-model')
    await roleCard(w, '视频模型').findAll('button').find((b) => b.text() === '保存').trigger('click'); await flushPromises()
    expect(modelInput(w, '视频模型').element.value).toBe('new-video-model')
    expect(modelInput(w, '生图模型（首帧 / 定妆）').element.value).toBe('my-unsaved-image-model')
  })

  it('「用它配 剧本文本模型」：文本卡跟着变，视频卡里没保存的 model / key 原样保留', async () => {
    responses['POST /api/settings/local/apply'] = {
      changed: ['text'],
      settings: { roles: { ...clone(SAVED), text: { adapter: 'openai_chat', base_url: 'http://127.0.0.1:11434/v1', api_key: '', model: 'qwen2.5:7b', local: true } }, gateway: GATEWAY },
      status: doc().status,
    }
    const w = mount(Settings); await flushPromises()
    await w.find('[data-test="local-scan"]').trigger('click'); await flushPromises()
    await modelInput(w, '视频模型').setValue('my-unsaved-video-model')
    await keyInput(w, '视频模型').setValue('sk-typed-not-saved')
    await w.find('[data-test="local-apply-ollama-text"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="local-card"]').text()).toContain('已应用到：剧本文本模型')
    expect(modelInput(w, '剧本文本模型').element.value).toBe('qwen2.5:7b')
    expect(modelInput(w, '视频模型').element.value).toBe('my-unsaved-video-model')
    expect(keyInput(w, '视频模型').element.value).toBe('sk-typed-not-saved')
  })

  it('「应用到勾选的角色」只勾 text：生图卡里没保存的 model 原样保留，文本卡跟着变', async () => {
    responses['PUT /api/settings'] = { settings: { roles: clone(SAVED), gateway: GATEWAY }, status: doc().status }
    responses['POST /api/settings/gateway/apply'] = {
      changed: ['text'],
      settings: { roles: { ...clone(SAVED), text: { adapter: 'openai_chat', base_url: 'http://gw/v1', api_key: '****', model: 'gpt-4o' } }, gateway: GATEWAY },
      status: doc().status,
    }
    const w = mount(Settings); await flushPromises()
    const card = w.find('[data-test="gateway-card"]')
    const boxes = card.findAll('input[type="checkbox"]')
    await boxes[1].setValue(false); await boxes[2].setValue(false); await boxes[3].setValue(false)
    await card.findAll('input[placeholder="模型 id"]')[0].setValue('gpt-4o')
    await modelInput(w, '生图模型（首帧 / 定妆）').setValue('my-unsaved-image-model')
    await card.findAll('button').find((b) => b.text().includes('应用到勾选')).trigger('click'); await flushPromises()
    expect(calls.find((c) => c[1] === '/api/settings/gateway/apply')[2]).toEqual({ roles: { text: { model: 'gpt-4o' } } })
    expect(card.text()).toContain('已应用到：剧本文本模型')
    expect(modelInput(w, '剧本文本模型').element.value).toBe('gpt-4o')
    expect(modelInput(w, '生图模型（首帧 / 定妆）').element.value).toBe('my-unsaved-image-model')
  })
})
