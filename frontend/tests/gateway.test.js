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

const ADAPTERS = {
  text: { openai_chat: { label: 'OpenAI 兼容', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
  image: { openai_images: { label: 'OpenAI 图', fields: ['base_url', 'api_key', 'model'], defaults: {} }, openrouter_images: { label: 'OR', fields: ['api_key', 'model'], defaults: {} } },
  voice: { openai_speech: { label: 'OpenAI 语音', fields: ['base_url', 'api_key', 'model', 'voice'], defaults: {} } },
  video: { openai_video: { label: 'OpenAI 视频', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
}
function doc(gateway = { base_url: '', api_key: '' }, roles = {}) {
  return { roles: ['text', 'image', 'voice', 'video'], adapters: ADAPTERS, settings: { roles, update_url: '', gateway }, status: { text: [], image: [], voice: [], video: [] } }
}
const EMPTY_CATALOG = { source: 'x', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
const GW_CATALOG = { source: 'gateway', fetched_at: 1, count: 3, roles: { text: [{ id: 'gpt-4o', name: 'gpt-4o', input: ['text'] }], image: [{ id: 'gpt-image-1', name: 'gpt-image-1', input: ['text', 'image'] }], voice: [], video: [{ id: 'veo-3.1', name: 'veo-3.1', input: ['text', 'image'] }] } }

beforeEach(() => {
  calls.length = 0
  for (const k of Object.keys(responses)) delete responses[k]
  responses['/api/system/asr'] = { installed: false, frozen: false, models: [], default_model: 'small', install_hint: '' }
  responses['/api/system/model-catalog'] = EMPTY_CATALOG
  responses['/api/system/model-catalog?source=gateway'] = EMPTY_CATALOG
})

describe('Settings · 统一网关', () => {
  it('测试连接：先保存网关，再测，成功后拉网关目录并给应用面板补全', async () => {
    responses['/api/settings'] = doc()
    responses['PUT /api/settings'] = { settings: { roles: {}, gateway: { base_url: 'http://gw/v1', api_key: '****' } }, status: {} }
    responses['POST /api/settings/gateway/test'] = { ok: true, message: '通了，网关列出 3 个模型', count: 3 }
    responses['POST /api/system/model-catalog/refresh'] = GW_CATALOG
    const w = mount(Settings)
    await flushPromises()
    const card = w.find('[data-test="gateway-card"]')
    await card.find('input[placeholder="http://127.0.0.1:4000/v1"]').setValue('http://gw/v1')
    await card.find('input[type="password"]').setValue('sk-gw')
    await card.findAll('button').find((b) => b.text().includes('测试连接')).trigger('click')
    await flushPromises()
    expect(calls.find((c) => c[0] === 'PUT')[2]).toEqual({ gateway: { base_url: 'http://gw/v1', api_key: 'sk-gw' } })
    expect(calls.some((c) => c[0] === 'POST' && c[1] === '/api/settings/gateway/test')).toBe(true)
    expect(calls.some((c) => c[0] === 'POST' && c[1] === '/api/system/model-catalog/refresh' && c[2].source === 'gateway')).toBe(true)
    expect(card.text()).toContain('通了，网关列出 3 个模型')
    expect(w.find('#gw-video option').attributes('value')).toBe('veo-3.1')
  })

  it('应用：只发勾选的角色，语音带音色；应用后角色表单跟着变', async () => {
    responses['/api/settings'] = doc({ base_url: 'http://gw/v1', api_key: '****' })
    responses['PUT /api/settings'] = { settings: { roles: {}, gateway: { base_url: 'http://gw/v1', api_key: '****' } }, status: {} }
    responses['POST /api/settings/gateway/apply'] = {
      changed: ['text', 'voice'],
      settings: { roles: { text: { adapter: 'openai_chat', base_url: 'http://gw/v1', model: 'gpt-4o' }, voice: { adapter: 'openai_speech', base_url: 'http://gw/v1', model: 'tts-1', voice: 'nova' } }, gateway: { base_url: 'http://gw/v1', api_key: '****' } },
      status: { text: [], image: ['未配置'], voice: [], video: ['未配置'] },
    }
    const w = mount(Settings)
    await flushPromises()
    const card = w.find('[data-test="gateway-card"]')
    const boxes = card.findAll('input[type="checkbox"]')
    await boxes[1].setValue(false)   // image 不勾
    const modelInputs = card.findAll('input[placeholder="模型 id"]')
    await modelInputs[0].setValue('gpt-4o')
    await modelInputs[2].setValue('tts-1')
    await card.find('input[placeholder="音色 id，如 alloy"]').setValue('nova')
    await card.findAll('button').find((b) => b.text().includes('应用到勾选')).trigger('click')
    await flushPromises()
    const apply = calls.find((c) => c[1] === '/api/settings/gateway/apply')
    expect(apply[2]).toEqual({ roles: { text: { model: 'gpt-4o' }, voice: { model: 'tts-1', voice: 'nova' } } })
    expect(card.text()).toContain('已应用到：剧本文本模型、语音模型（旁白）')
    // 角色卡里的来源已经切成网关那一档
    const selects = w.findAll('select').filter((s) => s.findAll('option').some((o) => o.attributes('value') === 'openai_chat'))
    expect(selects[0].element.value).toBe('openai_chat')
  })

  it('没填地址就应用：不发请求，提示', async () => {
    responses['/api/settings'] = doc()
    responses['PUT /api/settings'] = { settings: { roles: {}, gateway: { base_url: '', api_key: '' } }, status: {} }
    const w = mount(Settings)
    await flushPromises()
    const card = w.find('[data-test="gateway-card"]')
    for (const box of card.findAll('input[type="checkbox"]')) await box.setValue(false)
    await card.findAll('button').find((b) => b.text().includes('应用到勾选')).trigger('click')
    await flushPromises()
    expect(calls.some((c) => c[1] === '/api/settings/gateway/apply')).toBe(false)
    expect(card.text()).toContain('至少勾一个角色')
  })

  it('旧后端不返回 gateway 字段：提示重启，不抛异常', async () => {
    responses['/api/settings'] = { roles: ['text', 'image', 'voice', 'video'], adapters: ADAPTERS, settings: { roles: {}, update_url: '' }, status: { text: [], image: [], voice: [], video: [] } }
    responses['PUT /api/settings'] = { settings: { roles: {} }, status: {} }
    const w = mount(Settings)
    await flushPromises()
    const card = w.find('[data-test="gateway-card"]')
    await card.find('input[placeholder="http://127.0.0.1:4000/v1"]').setValue('http://gw/v1')
    await card.findAll('button').find((b) => b.text() === '保存').trigger('click')
    await flushPromises()
    expect(card.text()).toContain('重启一次服务')
  })
})
