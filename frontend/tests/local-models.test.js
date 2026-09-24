// 本地模型卡（DESIGN §45）：扫描 → 结果表 → 按角色应用；手填地址；ComfyUI 工作流行；
// 角色卡在本机地址下给「key 可以留空」的提示。api 全 mock，不碰真后端。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const calls = []
const responses = {}
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => { calls.push(['GET', url]); if (responses[url] instanceof Error) throw responses[url]; return responses[url] }),
  put: vi.fn(async (url, body) => { calls.push(['PUT', url, body]); return responses['PUT ' + url] }),
  post: vi.fn(async (url, body) => { calls.push(['POST', url, body]); if (responses['POST ' + url] instanceof Error) throw responses['POST ' + url]; return responses['POST ' + url] }),
  pollTask: vi.fn(),
}))
vi.mock('../src/store.js', () => ({ store: { health: null }, loadHealth: vi.fn() }))

import Settings from '../src/views/Settings.vue'
import { isLocalUrl } from '../src/local.js'

const ADAPTERS = {
  text: { openai_chat: { label: 'OpenAI 兼容', fields: ['base_url', 'api_key', 'model'], defaults: { base_url: 'https://api.openai.com/v1' } } },
  image: {
    openai_images: { label: 'OpenAI 图', fields: ['base_url', 'api_key', 'model'], defaults: { base_url: 'https://api.openai.com/v1' } },
    comfyui: { label: '本机 ComfyUI', fields: ['base_url', 'model'], defaults: { base_url: 'http://127.0.0.1:8188' } },
  },
  voice: { openai_speech: { label: 'OpenAI 语音', fields: ['base_url', 'api_key', 'model', 'voice'], defaults: { base_url: 'https://api.openai.com/v1' } } },
  video: { openai_video: { label: 'OpenAI 视频', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
}
const PRESETS = [
  { id: 'ollama', name: 'Ollama', roles: ['text'], base_url: 'http://127.0.0.1:11434/v1', adapter: { text: 'openai_chat' }, needs_key: false, doc: '' },
  { id: 'comfyui', name: 'ComfyUI', roles: ['image', 'video'], base_url: 'http://127.0.0.1:8188', adapter: { image: 'comfyui', video: 'comfyui' }, needs_key: false, doc: '' },
]
function scanBody(alive = true) {
  return {
    services: [
      { id: 'ollama', name: 'Ollama', roles: ['text'], base_url: 'http://127.0.0.1:11434/v1', alive, models: alive ? ['qwen2.5:7b', 'llama3:8b'] : [], error: alive ? '' : 'OSError' },
      { id: 'comfyui', name: 'ComfyUI', roles: ['image', 'video'], base_url: 'http://127.0.0.1:8188', alive: false, models: [], error: 'OSError' },
    ],
    alive: alive ? ['ollama'] : [],
    presets: PRESETS,
    roles: ['text', 'image', 'voice', 'video', 'music'],
  }
}
function doc(roles = {}) {
  return {
    roles: ['text', 'image', 'voice', 'video'], adapters: ADAPTERS,
    settings: { roles, update_url: '', gateway: { base_url: '', api_key: '' } },
    status: { text: [], image: [], voice: [], video: [] },
  }
}
const EMPTY_CATALOG = { source: 'x', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }

beforeEach(() => {
  calls.length = 0
  for (const k of Object.keys(responses)) delete responses[k]
  responses['/api/settings'] = doc()
  responses['/api/system/asr'] = { installed: false, frozen: false, models: [], default_model: 'small', install_hint: '' }
  responses['/api/system/model-catalog'] = EMPTY_CATALOG
  responses['/api/system/model-catalog?source=gateway'] = EMPTY_CATALOG
  responses['/api/system/local-workflows'] = {
    workflows: [{ name: 'txt2img_sdxl', title: 'SDXL 文生图', source: 'builtin', task: 'image', ok: true, keys: ['prompt', 'seed'], missing: [] }],
    user_dir: 'D:/proj/local_workflows', note: '',
  }
})

describe('isLocalUrl', () => {
  it('本机与私有网段放行，公网和「长得像本机」的域名拒绝', () => {
    for (const u of ['http://127.0.0.1:11434/v1', 'http://localhost:7860', 'http://[::1]:8188', 'http://192.168.1.9:7860',
      'http://10.1.2.3', 'http://172.16.5.5', '127.0.0.1:11434'])
      expect(isLocalUrl(u), u).toBe(true)
    for (const u of ['', 'https://api.openai.com/v1', 'http://8.8.8.8', 'http://172.32.0.1', 'http://127.0.0.1.evil.com',
      'http://127.0.0.1@evil.com', 'ftp://127.0.0.1', 'http://0.0.0.0:8188'])
      expect(isLocalUrl(u), u).toBe(false)
  })
})

describe('Settings · 本地模型', () => {
  it('扫描：结果表列出每个服务、状态与模型下拉；没探到时给手填提示', async () => {
    responses['/api/system/local-scan'] = scanBody(false)
    const w = mount(Settings)
    await flushPromises()
    const card = w.find('[data-test="local-card"]')
    expect(card.exists()).toBe(true)
    await card.find('[data-test="local-scan"]').trigger('click')
    await flushPromises()
    expect(calls.some((c) => c[1] === '/api/system/local-scan')).toBe(true)
    expect(card.find('[data-test="local-table"]').text()).toContain('Ollama')
    expect(card.text()).toContain('没探到在跑的服务')
    expect(card.find('[data-test="local-none"]').exists()).toBe(true)
    // 都没在跑：应用按钮全禁用
    expect(card.find('[data-test="local-apply-ollama-text"]').attributes('disabled')).toBeDefined()
  })

  it('探到服务：模型下拉默认选第一个，「用它配 <角色>」把服务 + 角色 + 模型发给后端并刷新角色卡', async () => {
    responses['/api/system/local-scan'] = scanBody(true)
    responses['POST /api/settings/local/apply'] = {
      changed: ['text'],
      settings: { roles: { text: { adapter: 'openai_chat', base_url: 'http://127.0.0.1:11434/v1', api_key: '', model: 'qwen2.5:7b', local: true } }, gateway: { base_url: '', api_key: '' } },
      status: { text: [], image: ['image: 没选适配器'], voice: [], video: [] },
    }
    const w = mount(Settings)
    await flushPromises()
    const card = w.find('[data-test="local-card"]')
    await card.find('[data-test="local-scan"]').trigger('click')
    await flushPromises()
    expect(card.text()).toContain('探到 1 个在跑的服务')
    expect(card.find('[data-test="local-model-ollama"]').element.value).toBe('qwen2.5:7b')
    // ComfyUI 是多角色服务：两个按钮
    expect(card.find('[data-test="local-apply-comfyui-image"]').exists()).toBe(true)
    expect(card.find('[data-test="local-apply-comfyui-video"]').exists()).toBe(true)
    await card.find('[data-test="local-apply-ollama-text"]').trigger('click')
    await flushPromises()
    const apply = calls.find((c) => c[1] === '/api/settings/local/apply')
    expect(apply[2]).toEqual({ service: 'ollama', role: 'text', model: 'qwen2.5:7b', base_url: 'http://127.0.0.1:11434/v1' })
    expect(card.text()).toContain('已应用到：剧本文本模型')
    // 角色卡跟着变：来源切成 openai_chat，key 提示变成「可以留空」
    expect(w.find('[data-test="local-key-hint-text"]').exists()).toBe(true)
  })

  it('手填地址：只扫那一个服务，结果并进原表而不是把整表冲掉', async () => {
    responses['/api/system/local-scan'] = scanBody(false)
    responses['/api/system/local-scan?ids=comfyui&base_url=http%3A%2F%2F127.0.0.1%3A9188'] = {
      ...scanBody(false),
      services: [{ id: 'comfyui', name: 'ComfyUI', roles: ['image', 'video'], base_url: 'http://127.0.0.1:9188', alive: true, models: ['sd_xl.safetensors'], error: '' }],
      alive: ['comfyui'],
    }
    const w = mount(Settings)
    await flushPromises()
    const card = w.find('[data-test="local-card"]')
    await card.find('[data-test="local-scan"]').trigger('click')
    await flushPromises()
    await card.find('input[placeholder="http://127.0.0.1:8188"]').setValue('http://127.0.0.1:9188')
    await card.find('[data-test="local-scan-manual"]').trigger('click')
    await flushPromises()
    expect(calls.some((c) => c[1].includes('ids=comfyui') && c[1].includes('base_url='))).toBe(true)
    // 两行都还在：ollama 保留上一次的结果，comfyui 换成手填地址那一次
    expect(card.find('[data-test="local-row-ollama"]').exists()).toBe(true)
    expect(card.find('[data-test="local-row-comfyui"]').text()).toContain('http://127.0.0.1:9188')
    expect(card.text()).toContain('探到 1 个在跑的服务')
  })

  it('ComfyUI 工作流行：列出工作流，「检查映射」把缺失路径摆出来', async () => {
    responses['/api/system/local-scan'] = scanBody(true)
    responses['POST /api/system/local-workflows/check'] = {
      name: 'txt2img_sdxl', ok: false, error: '', keys: ['prompt'],
      missing: [{ key: 'seed', path: '3/inputs/seed', reason: '工作流里没有 3 号节点' }],
    }
    const w = mount(Settings)
    await flushPromises()
    const card = w.find('[data-test="local-card"]')
    expect(card.find('[data-test="local-workflow-row"]').exists()).toBe(true)
    await card.find('[data-test="local-scan"]').trigger('click')
    await flushPromises()
    // 工作流清单在扫描时按需加载（这次没探到 comfyui，所以显式刷新一次）
    await card.findAll('button').find((b) => b.text().includes('刷新工作流')).trigger('click')
    await flushPromises()
    expect(card.find('[data-test="local-workflow"]').text()).toContain('SDXL 文生图')
    await card.find('[data-test="local-workflow-check"]').trigger('click')
    await flushPromises()
    expect(calls.find((c) => c[1] === '/api/system/local-workflows/check')[2]).toEqual({ name: 'txt2img_sdxl' })
    expect(card.text()).toContain('seed')
    expect(card.text()).toContain('D:/proj/local_workflows')
  })

  it('角色卡：本机地址下 key 输入框有「可以留空」提示，远端没有', async () => {
    responses['/api/settings'] = doc({
      text: { adapter: 'openai_chat', base_url: 'http://127.0.0.1:11434/v1', api_key: '', model: 'qwen', local: true },
      voice: { adapter: 'openai_speech', base_url: 'https://api.openai.com/v1', api_key: '****', model: 'tts-1', voice: 'alloy' },
    })
    const w = mount(Settings)
    await flushPromises()
    expect(w.find('[data-test="local-key-hint-text"]').exists()).toBe(true)
    expect(w.find('[data-test="local-key-hint-voice"]').exists()).toBe(false)
  })

  it('扫描失败：报错不炸页面', async () => {
    responses['/api/system/local-scan'] = new Error('连不上本地服务（可能正在重启）')
    const w = mount(Settings)
    await flushPromises()
    const card = w.find('[data-test="local-card"]')
    await card.find('[data-test="local-scan"]').trigger('click')
    await flushPromises()
    expect(card.text()).toContain('连不上本地服务')
    expect(card.find('[data-test="local-table"]').exists()).toBe(false)
  })
})
