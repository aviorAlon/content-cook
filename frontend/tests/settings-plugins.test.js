// 设置页 × 插件（genkit.plugins）：插件登记的来源带「（插件）」；存着的来源没装上时显示成选中且不可再选的
// 「X（未加载）」并给出原因，保存时原样发回（不会悄悄清掉来源和密钥）；一个来源都没有的角色给出「要装插件」的提示；
// 健康检查卡片里有插件一行。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const calls = []
const responses = {}
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => { calls.push(['GET', url]); if (responses[url] instanceof Error) throw responses[url]; return responses[url] }),
  put: vi.fn(async (url, body) => { calls.push(['PUT', url, body]); if (responses['PUT ' + url] instanceof Error) throw responses['PUT ' + url]; return responses['PUT ' + url] }),
  post: vi.fn(async (url, body) => { calls.push(['POST', url, body]); return responses['POST ' + url] }),
  pollTask: vi.fn(),
}))
vi.mock('../src/store.js', () => ({ store: { health: null }, loadHealth: vi.fn() }))

import Settings from '../src/views/Settings.vue'
import { store } from '../src/store.js'

const GONE = '「生图」的来源 plug_images 现在用不了：它不是内置来源，提供它的插件没有加载——把插件放进 ~/.storystudio/plugins 后重启程序，或去「模型设置」给「生图」换一个来源'
function doc(extra = {}) {
  return {
    roles: ['image', 'video', 'music'],
    adapters: {
      image: { openai_images: { label: 'OpenAI 图片', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
      video: {
        openrouter_video: { label: 'OpenRouter 视频', fields: ['api_key', 'model'], defaults: {} },
        demo_video: { label: '示例视频', fields: ['model'], defaults: { model: 'demo' }, plugin: 'demo' },
      },
      music: {},
    },
    settings: { roles: { image: { adapter: 'plug_images', model: 'm1', api_key: 'sk-1***cdef' }, video: { adapter: 'demo_video' }, music: {} }, update_url: '' },
    status: { image: ['image: 来源 plug_images 没有装上'], video: [], music: ['music: 没选适配器'] },
    unavailable: { image: { adapter: 'plug_images', message: GONE } },
    plugins: [],
    ...extra,
  }
}

beforeEach(() => {
  calls.length = 0
  for (const k of Object.keys(responses)) delete responses[k]
  responses['/api/settings'] = doc()
  responses['/api/system/asr'] = { installed: false, frozen: false, models: [], default_model: 'small', install_hint: '' }
  responses['/api/system/model-catalog'] = { source: 'openrouter', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
})
afterEach(() => { store.health = null })

function cardOf(w, role) {
  return w.findAll('.card').find((c) => c.find(`[data-test="adapter-${role}"]`).exists())
}

describe('Settings · 插件来源', () => {
  it('存着的来源没装上：选中且不可再选的「（未加载）」选项 + 原因；保存原样发回，不会发空来源', async () => {
    responses['PUT /api/settings'] = { settings: doc().settings, status: doc().status, unavailable: doc().unavailable }
    const w = mount(Settings)
    await flushPromises()
    const select = w.find('[data-test="adapter-image"]')
    expect(select.element.value).toBe('plug_images')
    const option = w.find('[data-test="adapter-unavailable-image"]')
    expect(option.exists()).toBe(true)
    expect(option.element.disabled).toBe(true)
    expect(option.text()).toBe('plug_images（未加载）')
    expect(w.find('[data-test="unavailable-image"]').text()).toBe(GONE)
    await cardOf(w, 'image').find('button.btn.primary').trigger('click')
    await flushPromises()
    const put = calls.find((c) => c[0] === 'PUT' && c[1] === '/api/settings')
    expect(put[2]).toEqual({ roles: { image: { adapter: 'plug_images' } } })
  })

  it('换成内置来源后原因提示消失；插件登记的来源标「（插件）」', async () => {
    const w = mount(Settings)
    await flushPromises()
    await w.find('[data-test="adapter-image"]').setValue('openai_images')
    expect(w.find('[data-test="unavailable-image"]').exists()).toBe(false)
    const labels = w.find('[data-test="adapter-video"]').findAll('option').map((o) => o.text())
    expect(labels).toEqual(['（未选择）', 'OpenRouter 视频', '示例视频（插件）'])
    expect(w.find('[data-test="adapter-unavailable-video"]').exists()).toBe(false)
  })

  it('一个来源都没有的角色（配乐）：说要装插件，而不是只给一个空下拉', async () => {
    const w = mount(Settings)
    await flushPromises()
    expect(w.find('[data-test="no-sources-music"]').text()).toBe('目前没有可选的来源（内置来源不覆盖这一档，可以装一个提供它的插件）')
    expect(w.find('[data-test="no-sources-video"]').exists()).toBe(false)
  })

  it('健康检查卡片：插件逐个列出名字、版本、状态和失败原因；没装插件就说没有', async () => {
    store.health = {
      version: '0.1.0', frozen: false, settings_path: 's', project_root: 'p', ffmpeg_ok: true, ffmpeg: 'ffmpeg', asr_available: false,
      plugins: [
        { name: 'demo', version: '1.2.0', status: 'loaded', message: '', path: 'x' },
        { name: 'broken', version: '', status: 'failed', message: '插件 broken 加载失败：ModuleNotFoundError: x', path: 'y' },
      ],
    }
    const w = mount(Settings)
    await flushPromises()
    const row = w.find('[data-test="health-plugins"]').text()
    expect(row).toContain('demo 1.2.0 · 已加载')
    expect(row).toContain('broken · 加载失败：插件 broken 加载失败：ModuleNotFoundError: x')
    store.health = { ...store.health, plugins: [] }
    const empty = mount(Settings)
    await flushPromises()
    expect(empty.find('[data-test="health-plugins"]').text()).toBe('没有装插件')
  })
})
