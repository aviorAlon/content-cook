import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const calls = []
const responses = {}
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => { calls.push(['GET', url]); if (responses[url] instanceof Error) throw responses[url]; return responses[url] }),
  put: vi.fn(async (url, body) => { calls.push(['PUT', url, body]); if (responses['PUT ' + url] instanceof Error) throw responses['PUT ' + url]; return responses['PUT ' + url] }),
  post: vi.fn(async (url, body) => { calls.push(['POST', url, body]); if (responses['POST ' + url] instanceof Error) throw responses['POST ' + url]; return responses['POST ' + url] }),
  pollTask: vi.fn(),
}))
vi.mock('../src/store.js', () => ({ store: { health: null }, loadHealth: vi.fn() }))

import Settings from '../src/views/Settings.vue'

const DOC = {
  roles: ['text', 'image', 'voice', 'video'],
  adapters: {
    text: { openai_chat: { label: 'OpenAI 兼容', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
    image: { openrouter_images: { label: 'OpenRouter 生图', fields: ['api_key', 'model'], defaults: {} }, ark_images: { label: '火山', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
    voice: { edge_tts: { label: 'Edge', fields: ['voice'], defaults: {} } },
    video: { openrouter_video: { label: 'OpenRouter 视频', fields: ['api_key', 'model'], defaults: {} } },
  },
  settings: { roles: { image: { adapter: 'openrouter_images', model: 'x' }, video: { adapter: 'openrouter_video' }, text: { adapter: 'openai_chat', base_url: 'https://openrouter.ai/api/v1' } }, update_url: '' },
  status: { text: [], image: [], voice: ['未配置'], video: [] },
}
const CATALOG = {
  source: 'openrouter', label: 'OpenRouter', fetched_at: 1_800_000_000, count: 3,
  roles: {
    image: [{ id: 'bytedance-seed/seedream-4.5', name: 'Seedream', input: ['text', 'image'], caps: { aspect_ratios: ['1:1', '16:9'], reference_images: true, max_reference_images: 5 } }],
    video: [{ id: 'text-only/video', name: 'T2V', input: ['text'], caps: { durations: { choices: [5, 10] }, resolutions: ['720p'], first_frame: false, audio: false } }],
    voice: [], text: [{ id: 'anthropic/claude', name: 'Claude', input: ['text'] }],
  },
}

beforeEach(() => {
  calls.length = 0
  for (const k of Object.keys(responses)) delete responses[k]
  responses['/api/settings'] = DOC
  responses['/api/system/asr'] = { installed: false, frozen: false, models: [], default_model: 'small', install_hint: '' }
})

// 发第一个 tag 前的设置修复（ADP-03 / 04 / 05）：手填地址只填主机端口、清除密钥按钮、设置文件坏了也能打开设置页
describe('Settings · 发 tag 前的设置修复', () => {
  const PRESETS = [
    { id: 'ollama', name: 'Ollama', roles: ['text'], base_url: 'http://127.0.0.1:11434/v1', adapter: { text: 'openai_chat' }, needs_key: false, doc: '' },
    { id: 'comfyui', name: 'ComfyUI', roles: ['image', 'video'], base_url: 'http://127.0.0.1:8188', adapter: {}, needs_key: false, doc: '' },
  ]

  it('ADP-03：手填框按选中的预设只提示主机和端口，说明里写清路径会自动接上', async () => {
    responses['/api/system/model-catalog'] = { source: 'openrouter', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
    responses['/api/system/local-scan'] = { services: [], alive: [], recommend: {}, presets: PRESETS, roles: ['text', 'image', 'voice', 'video'] }
    const w = mount(Settings)
    await flushPromises()
    await w.find('[data-test="local-scan"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="local-addr"]').attributes('placeholder')).toBe('http://127.0.0.1:8188')
    await w.find('[data-test="local-addr-for"]').setValue('ollama')
    expect(w.find('[data-test="local-addr"]').attributes('placeholder')).toBe('http://127.0.0.1:11434')   // 不带 /v1：路径由后端按预设接上
    expect(w.find('[data-test="local-card"]').text()).toContain('只填主机和端口')
  })

  it('ADP-04：存着密钥的角色有「清除密钥」，点了发 api_key: null 并按响应清空表单；没有密钥字段的来源没有这个按钮', async () => {
    responses['/api/system/model-catalog'] = { source: 'openrouter', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
    responses['/api/settings'] = { ...DOC, settings: { ...DOC.settings, roles: { ...DOC.settings.roles, image: { adapter: 'openrouter_images', model: 'x', api_key: 'sk-1***cdef' } } } }
    responses['PUT /api/settings'] = { settings: { ...DOC.settings, roles: { ...DOC.settings.roles, image: { adapter: 'openrouter_images', model: 'x', api_key: '' } } }, status: DOC.status }
    const w = mount(Settings)
    await flushPromises()
    expect(w.find('[data-test="clear-key-image"]').exists()).toBe(true)
    expect(w.find('[data-test="clear-key-voice"]').exists()).toBe(false)     // edge_tts 没有 api_key 字段
    expect(w.find('[data-test="clear-key-text"]').exists()).toBe(false)      // text 有字段但没存密钥
    const card = w.findAll('.card').find((c) => c.find('[data-test="clear-key-image"]').exists())
    const [keyInput, modelInput] = card.findAll('input')
    await modelInput.setValue('y-unsaved')                                  // 同一张卡里还没保存的改动
    await w.find('[data-test="clear-key-image"]').trigger('click')
    await flushPromises()
    const put = calls.find((c) => c[0] === 'PUT' && c[1] === '/api/settings')
    expect(put[2]).toEqual({ roles: { image: { api_key: null } } })
    expect(w.find('[data-test="clear-key-image"]').exists()).toBe(false)
    expect(w.text()).toContain('密钥已清除')
    expect(keyInput.element.value).toBe('')
    expect(modelInput.element.value).toBe('y-unsaved')                     // 清密钥不把别的格子冲掉
  })

  it('ADP-05：后端带 warning（设置文件损坏）时红字显示原因，表单照常渲染', async () => {
    responses['/api/system/model-catalog'] = { source: 'openrouter', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
    responses['/api/settings'] = { ...DOC, warning: '设置文件损坏：C:\\x\\settings.json：Expecting value: line 1 column 3 (char 2)。保存一次会自动备份坏文件并重建' }
    const w = mount(Settings)
    await flushPromises()
    expect(w.find('[data-test="settings-warning"]').text()).toContain('设置文件损坏')
    expect(w.findAll('select').length).toBeGreaterThan(0)       // 角色卡还在，能改能存
  })

  it('ADP-05：没有 warning 时不显示', async () => {
    responses['/api/system/model-catalog'] = { source: 'openrouter', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
    const w = mount(Settings)
    await flushPromises()
    expect(w.find('[data-test="settings-warning"]').exists()).toBe(false)
  })

  // 审查复现：照提示「保存一次」后后端已备份坏文件并重建，红字却一直留着，要刷新页面才消失
  it('ADP-05：保存失败时「设置文件损坏」红字留着，保存成功后消失', async () => {
    responses['/api/system/model-catalog'] = { source: 'openrouter', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
    responses['/api/settings'] = { ...DOC, warning: '设置文件损坏：C:\\x\\settings.json：Expecting value: line 1 column 3 (char 2)。保存一次会自动备份坏文件并重建' }
    const w = mount(Settings)
    await flushPromises()
    const card = w.findAll('.card').find((c) => c.findAll('option').some((o) => o.attributes('value') === 'openrouter_images'))
    const saveBtn = card.findAll('button').find((b) => b.text() === '保存')
    responses['PUT /api/settings'] = new Error('网络断了')
    await saveBtn.trigger('click')
    await flushPromises()
    expect(w.find('[data-test="settings-warning"]').exists()).toBe(true)     // 没写成，坏文件还在，提示得留着
    responses['PUT /api/settings'] = { settings: DOC.settings, status: DOC.status }
    await saveBtn.trigger('click')
    await flushPromises()
    expect(w.find('[data-test="settings-warning"]').exists()).toBe(false)
    expect(card.text()).toContain('已保存')
  })

  // 审查复现：本机 SD WebUI 挡在反向代理后面时手配的密钥，表单里没有密钥栏——得看得见、能清掉（换来源漏过来的旧 key 也一样）
  it('ADP-04：表单里没有密钥栏的来源存着密钥时单独提示并可清除；下拉换到别的来源（未保存）时不提示', async () => {
    responses['/api/system/model-catalog'] = { source: 'openrouter', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
    const adapters = { ...DOC.adapters, image: { ...DOC.adapters.image, sdwebui: { label: 'SD WebUI', fields: ['base_url', 'model'], defaults: {} } } }
    const image = { adapter: 'sdwebui', base_url: 'http://192.168.1.50:7860', model: 'a.safetensors', api_key: 'pr*****89' }
    responses['/api/settings'] = { ...DOC, adapters, settings: { ...DOC.settings, roles: { ...DOC.settings.roles, image } } }
    responses['PUT /api/settings'] = { settings: { ...DOC.settings, roles: { ...DOC.settings.roles, image: { ...image, api_key: '' } } }, status: DOC.status }
    const w = mount(Settings)
    await flushPromises()
    const hint = w.find('[data-test="stored-key-image"]')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('pr*****89')
    expect(w.find('[data-test="stored-key-text"]').exists()).toBe(false)     // 有密钥栏的来源不走这条
    const select = w.findAll('select').find((s) => s.findAll('option').some((o) => o.attributes('value') === 'sdwebui'))
    await select.setValue('openrouter_images')
    expect(w.find('[data-test="stored-key-image"]').exists()).toBe(false)
    await select.setValue('sdwebui')
    await w.find('[data-test="stored-key-image"] [data-test="clear-key-image"]').trigger('click')
    await flushPromises()
    expect(calls.find((c) => c[0] === 'PUT')[2]).toEqual({ roles: { image: { api_key: null } } })
    expect(w.find('[data-test="stored-key-image"]').exists()).toBe(false)
  })
})

describe('Settings · 模型目录', () => {
  it('没拉过目录：卡片提示"还没拉取过"，模型 id 输入框没有补全列表', async () => {
    responses['/api/system/model-catalog'] = { source: 'openrouter', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
    const w = mount(Settings)
    await flushPromises()
    expect(w.find('[data-test="catalog-card"]').text()).toContain('还没拉取过')
    expect(w.findAll('datalist').length).toBe(0)
  })

  it('拉过目录：OpenRouter 来源的角色有补全，火山来源没有；不吃图的模型有标注', async () => {
    responses['/api/system/model-catalog'] = CATALOG
    const w = mount(Settings)
    await flushPromises()
    const lists = w.findAll('datalist')
    const ids = lists.map((d) => d.attributes('id')).sort()
    // image / video 走 openrouter；text 的 base_url 指向 openrouter.ai 也算
    expect(ids).toEqual(['catalog-image', 'catalog-text', 'catalog-video'])
    expect(w.find('#catalog-video').text()).toContain('不吃图')
    expect(w.find('#catalog-image').text()).not.toContain('不吃图')
    expect(w.find('[data-test="catalog-card"]').text()).toContain('3 个模型')
    // 切到火山来源后，补全消失
    const imageSelect = w.findAll('select').find((s) => s.findAll('option').some((o) => o.attributes('value') === 'ark_images'))
    await imageSelect.setValue('ark_images')
    expect(w.find('#catalog-image').exists()).toBe(false)
  })

  it('点「拉取」调刷新接口，失败时把错误显示出来', async () => {
    responses['/api/system/model-catalog'] = { source: 'openrouter', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
    responses['POST /api/system/model-catalog/refresh'] = CATALOG
    const w = mount(Settings)
    await flushPromises()
    const btn = w.findAll('button').find((b) => b.text().includes('拉取 / 更新目录'))
    await btn.trigger('click')
    await flushPromises()
    expect(calls.some((c) => c[0] === 'POST' && c[1] === '/api/system/model-catalog/refresh')).toBe(true)
    expect(w.find('[data-test="catalog-card"]').text()).toContain('已更新，3 个模型')
    expect(w.find('#catalog-image').exists()).toBe(true)

    responses['POST /api/system/model-catalog/refresh'] = new Error('拉取 OpenRouter 模型列表失败：network down')
    await btn.trigger('click')
    await flushPromises()
    expect(w.find('[data-test="catalog-card"]').text()).toContain('network down')
  })

  it('模型 id 在目录里：给参数区间摘要；不在：提示 id 写错', async () => {
    responses['/api/system/model-catalog'] = CATALOG
    const w = mount(Settings)
    await flushPromises()
    const hints = () => w.findAll('[data-test="model-hint"]').map((h) => h.text())
    // image 里填的是 'x'，目录里没有
    expect(hints().some((t) => t.includes('目录里没有 x'))).toBe(true)
    const imageInput = w.find('input[list="catalog-image"]')
    await imageInput.setValue('bytedance-seed/seedream-4.5')
    expect(hints().some((t) => t.includes('画幅 1:1 16:9') && t.includes('参考图最多 5 张'))).toBe(true)
    const videoInput = w.find('input[list="catalog-video"]')
    await videoInput.setValue('text-only/video')
    expect(hints().some((t) => t.includes('时长 5/10 秒') && t.includes('不接受首帧') && t.includes('不出原生音频'))).toBe(true)
  })
})

describe('Settings · 台词识别闸（桌面版）', () => {
  it('打包版也能点「一键安装」（接口借本机同版本 Python 装进外部 site 目录）；接口的可执行提示原样显示', async () => {
    responses['/api/system/model-catalog'] = { source: 'openrouter', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
    const hint = '桌面版没有随包带 faster-whisper（体积原因）。点「一键安装」会用本机的 Python 3.11 把它装进 C:\\x\\site-packages；没有 Python 3.11 就先装一个，或自己执行：py -3.11 -m pip install --target "C:\\x\\site-packages" faster-whisper，装完重启本程序。'
    responses['/api/system/asr'] = { installed: false, frozen: true, python_version: '3.11', extra_site: 'C:\\x\\site-packages', models: [], default_model: 'small', install_hint: hint }
    responses['POST /api/system/asr/install'] = new Error('本机没找到 Python 3.11（打包版借它来装）：先装一个 Python 3.11，或自己执行：py -3.11 -m pip install --target "C:\\x\\site-packages" faster-whisper，装完重启本程序。')
    const w = mount(Settings)
    await flushPromises()
    const btn = w.find('[data-test="asr-install"]')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeUndefined()
    expect(w.text()).toContain('pip install --target')
    expect(w.text()).not.toContain('PYTHONPATH')
    await btn.trigger('click')
    await flushPromises()
    expect(w.text()).toContain('本机没找到 Python 3.11')
  })

  it('打包版装好之后（外部 site 目录已接上）：不再显示安装提示，换成模型预下载', async () => {
    responses['/api/system/model-catalog'] = { source: 'openrouter', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
    responses['/api/system/asr'] = { installed: true, frozen: true, python_version: '3.11', extra_site: 'C:\\x\\site-packages',
      models: [{ name: 'small', cached: false }], default_model: 'small', install_hint: '桌面版没有随包带 faster-whisper（体积原因）。' }
    const w = mount(Settings)
    await flushPromises()
    expect(w.find('[data-test="asr-install"]').exists()).toBe(false)
    expect(w.text()).not.toContain('桌面版没有随包带')
    expect(w.text()).toContain('预下载模型')
  })
})
