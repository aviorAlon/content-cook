// 模型设置 · 同一个来源只改了地址（换了主机 / 端口）：后端不再把旧密钥发到新地址、直接清掉（2026-09-23 发布前自查 SEC-3）。
// 前端要说清楚「请重新填写密钥」，不能只报「已保存」让人以为密钥还在。角色卡和统一网关两处。api 全 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const responses = {}
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => responses[url]),
  put: vi.fn(async (url) => responses['PUT ' + url]),
  post: vi.fn(async (url) => responses['POST ' + url]),
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
  text: { adapter: 'openai_chat', base_url: 'https://api.openai.com/v1', api_key: 'sk-p***mnop', model: 'gpt-4o-mini' },
  image: { adapter: 'openai_images', base_url: 'https://i.example/v1', api_key: '****', model: 'img' },
  voice: { adapter: 'openai_speech', base_url: 'https://v.example/v1', api_key: '****', model: 'tts-1', voice: 'nova' },
  video: { adapter: 'openai_video', base_url: 'https://vid.example/v1', api_key: '****', model: 'vid' },
}
const clone = (x) => JSON.parse(JSON.stringify(x))
const STATUS = { text: [], image: [], voice: [], video: [] }
const EMPTY_CATALOG = { source: 'x', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }

const roleCard = (w, title) => w.findAll('.card').find((c) => c.find('h3').exists() && c.find('h3').text() === title)

beforeEach(() => {
  for (const k of Object.keys(responses)) delete responses[k]
  responses['/api/settings'] = { roles: ['text', 'image', 'voice', 'video'], adapters: ADAPTERS,
    settings: { roles: clone(SAVED), update_url: '', gateway: { base_url: 'http://127.0.0.1:4000/v1', api_key: 'sk-g***wxyz' } }, status: STATUS }
  responses['/api/system/asr'] = { installed: false, frozen: false, models: [], default_model: 'small', install_hint: '' }
  responses['/api/system/model-catalog'] = EMPTY_CATALOG
  responses['/api/system/model-catalog?source=gateway'] = EMPTY_CATALOG
  responses['/api/system/local-workflows'] = { workflows: [], user_dir: '', note: '' }
})

describe('Settings · 换了地址，旧密钥被清掉时要说清', () => {
  it('角色卡：保存后密钥没了（来源没换）→ 提示重新填写密钥', async () => {
    responses['PUT /api/settings'] = { settings: { roles: { ...clone(SAVED), text: { ...SAVED.text, base_url: 'https://other.example/v1', api_key: '' } },
      gateway: responses['/api/settings'].settings.gateway }, status: STATUS }
    const w = mount(Settings); await flushPromises()
    const card = roleCard(w, '剧本文本模型')
    await card.findAll('input[type="text"]')[0].setValue('https://other.example/v1')
    await card.findAll('button').find((b) => b.text() === '保存').trigger('click'); await flushPromises()
    expect(card.find('.status').text()).toContain('换了地址，旧密钥不会发到新地址——请重新填写密钥')
  })

  it('对照组：密钥还在就只报「已保存」', async () => {
    responses['PUT /api/settings'] = { settings: { roles: clone(SAVED), gateway: responses['/api/settings'].settings.gateway }, status: STATUS }
    const w = mount(Settings); await flushPromises()
    const card = roleCard(w, '剧本文本模型')
    await card.findAll('button').find((b) => b.text() === '保存').trigger('click'); await flushPromises()
    expect(card.find('.status').text()).toBe('已保存')
  })

  it('统一网关：换了地址、密钥被清掉 → 标红提示', async () => {
    responses['PUT /api/settings'] = { settings: { roles: clone(SAVED), gateway: { base_url: 'https://evil.example/v1', api_key: '' } }, status: STATUS }
    const w = mount(Settings); await flushPromises()
    const card = w.find('[data-test="gateway-card"]')
    await card.find('input').setValue('https://evil.example/v1')
    await card.findAll('button').find((b) => b.text() === '保存').trigger('click'); await flushPromises()
    const status = card.find('.status')
    expect(status.text()).toContain('请重新填写密钥')
    expect(status.classes()).toContain('bad')
  })
})
