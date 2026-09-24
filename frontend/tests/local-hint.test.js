// 「推荐而不强制」的三处提示（DESIGN §45）：角色没配好那一行、花钱之前那一行、设置页的「推荐」标记。
// 每一处都是一行、都能关掉、都不拦路；没有 suggestion 时界面跟以前一模一样。api 全 mock，不碰真后端。
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
vi.mock('../src/store.js', () => ({ store: { health: null }, loadHealth: vi.fn(), withBusy: (fn) => fn() }))

import LocalTip from '../src/components/LocalTip.vue'
import ModalHost from '../src/components/ModalHost.vue'
import Settings from '../src/views/Settings.vue'
import { confirm, custom, modal } from '../src/ui.js'
import { forgetLocalHints, localHints } from '../src/localhint.js'

const HINT_IMAGE = '/api/system/local-hint?role=image'
const SUGGESTION = { service: 'sdwebui', name: 'Stable Diffusion WebUI / Forge', reason: '出图推荐：接口固定、装好就能用' }

function hint(extra = {}) {
  return {
    role: 'image',
    available: [{ service: 'sdwebui', name: 'Stable Diffusion WebUI / Forge', base_url: 'http://127.0.0.1:7860', models: ['sd_xl'], roles: ['image'] }],
    current_is_local: false,
    suggestion: SUGGESTION,
    dismissed: false,
    scanned: true,
    ...extra,
  }
}

beforeEach(() => {
  calls.length = 0
  for (const k of Object.keys(responses)) delete responses[k]
  forgetLocalHints()
  modal.current = null
})

describe('角色没配好那一行', () => {
  it('探到在跑的服务：出现一行，指向设置页，不写安装教程', async () => {
    responses[HINT_IMAGE] = hint()
    const w = mount(LocalTip, { props: { role: 'image' } })
    await flushPromises()
    const line = w.find('[data-test="local-tip"]')
    expect(line.exists()).toBe(true)
    expect(line.text()).toContain('有显卡的话可以本地跑')
    expect(line.text()).not.toContain('安装')
    // 只声称「可以本地跑」，不点名说什么在跑（这一处的文案对「没扫过」的人也要成立）
    expect(line.text()).not.toContain('Stable Diffusion')
    expect(w.find('[data-test="local-tip-go"]').attributes('to')).toBe('/settings')
  })

  it('从来没扫过本机：也给这一行（文案不声称本机有什么在跑）', async () => {
    responses[HINT_IMAGE] = hint({ available: [], suggestion: null, scanned: false })
    const w = mount(LocalTip, { props: { role: 'image' } })
    await flushPromises()
    expect(w.find('[data-test="local-tip"]').exists()).toBe(true)
  })

  it('扫过、什么都没在跑 / 这个角色已经是本机 / 关过提示：一个字都不出现', async () => {
    for (const extra of [{ available: [], suggestion: null, scanned: true },
      { current_is_local: true }, { dismissed: true, suggestion: null }]) {
      forgetLocalHints()
      responses[HINT_IMAGE] = hint(extra)
      const w = mount(LocalTip, { props: { role: 'image' } })
      await flushPromises()
      expect(w.find('[data-test="local-tip"]').exists(), JSON.stringify(extra)).toBe(false)
    }
  })

  it('接口问不到（旧后端 / 服务在重启）：不显示，也不报错', async () => {
    responses[HINT_IMAGE] = new Error('连不上本地服务（可能正在重启）')
    const w = mount(LocalTip, { props: { role: 'image' } })
    await flushPromises()
    expect(w.find('[data-test="local-tip"]').exists()).toBe(false)
  })

  it('「不再提示」调用后端并当场收起来；同一个角色不会被问第二遍', async () => {
    responses[HINT_IMAGE] = hint()
    responses['POST /api/system/local-hint/dismiss'] = { dismissed: ['image'] }
    const w = mount(LocalTip, { props: { role: 'image' } })
    await flushPromises()
    await w.find('[data-test="local-tip-off"]').trigger('click')
    await flushPromises()
    expect(calls.find((c) => c[1] === '/api/system/local-hint/dismiss')[2]).toEqual({ role: 'image' })
    expect(w.find('[data-test="local-tip"]').exists()).toBe(false)
    // 第二个组件挂上来：用缓存，不再问一次
    const before = calls.filter((c) => c[1] === HINT_IMAGE).length
    mount(LocalTip, { props: { role: 'image' } })
    await flushPromises()
    expect(calls.filter((c) => c[1] === HINT_IMAGE).length).toBe(before)
  })
})

describe('花钱之前那一行', () => {
  it('确认框：探到了就多一行「本机有 X 在跑」，带「去切换」与「不再提示」', async () => {
    responses[HINT_IMAGE] = hint()
    responses['POST /api/system/local-hint/dismiss'] = { dismissed: ['image'] }
    const w = mount(ModalHost)
    const ask = confirm('将发出 3 次生图请求。继续？', { confirmLabel: '生成缺的', localRole: 'image' })
    await flushPromises()
    const line = w.find('[data-test="modal-local-hint"]')
    expect(line.exists()).toBe(true)
    expect(line.text()).toContain('本机有 Stable Diffusion WebUI / Forge 在跑')
    expect(line.text()).toContain('这次不花钱')
    expect(w.find('[data-test="modal-local-go"]').attributes('href')).toBe('#/settings')
    // 按钮一个没变：原来的「取消 / 确认」还在，提示不接管这次操作
    expect(w.text()).toContain('将发出 3 次生图请求')
    expect(w.text()).toContain('生成缺的')
    await w.find('[data-test="modal-local-off"]').trigger('click')
    await flushPromises()
    expect(calls.some((c) => c[1] === '/api/system/local-hint/dismiss')).toBe(true)
    expect(w.find('[data-test="modal-local-hint"]').exists()).toBe(false)
    // 提示关掉了，弹窗本身还开着（从不替用户做决定）
    expect(modal.current).not.toBe(null)
    modal.current.resolve(true)
    expect(await ask).toBe(true)
  })

  it('没有 suggestion / 没给角色：确认框跟以前一模一样', async () => {
    responses[HINT_IMAGE] = hint({ available: [], suggestion: null })
    const w = mount(ModalHost)
    confirm('将发出 3 次生图请求。继续？', { confirmLabel: '生成缺的', localRole: 'image' })
    await flushPromises()
    expect(w.find('[data-test="modal-local-hint"]').exists()).toBe(false)
    modal.current.resolve(false)
    await flushPromises()
    // 连问都不问：没给 localRole 的弹窗一个请求都不发
    calls.length = 0
    confirm('删掉？')
    await flushPromises()
    expect(calls).toEqual([])
    expect(w.find('[data-test="modal-local-hint"]').exists()).toBe(false)
    modal.current.resolve(false)
  })

  it('预估弹窗（首帧 / 视频）：同一行挂在确认按钮上面，问不到就当没有', async () => {
    responses[HINT_IMAGE] = hint()
    const w = mount(ModalHost)
    custom({
      view: 'estimate', through: 'frames', only: null, localRole: 'image',
      estimate: { pending_count: 2, already_have_count: 0, items: [{ shot_id: 'S1', model: 'x' }] },
    })
    await flushPromises()
    expect(w.find('[data-test="modal-local-hint"]').text()).toContain('本机有 Stable Diffusion WebUI / Forge 在跑')
    expect(w.text()).toContain('确认生成，开始花钱')
    modal.current.resolve(false)
  })
})

describe('设置页的本地模型卡', () => {
  const ADAPTERS = {
    text: { openai_chat: { label: 'OpenAI 兼容', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
    image: { openai_images: { label: 'OpenAI 图', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
    voice: { openai_speech: { label: 'OpenAI 语音', fields: ['base_url', 'api_key', 'model', 'voice'], defaults: {} } },
    video: { openai_video: { label: 'OpenAI 视频', fields: ['base_url', 'api_key', 'model'], defaults: {} } },
  }
  function doc(dismissed = []) {
    return {
      roles: ['text', 'image', 'voice', 'video'], adapters: ADAPTERS,
      settings: { roles: {}, update_url: '', gateway: { base_url: '', api_key: '' }, local_hints_dismissed: dismissed },
      status: { text: [], image: [], voice: [], video: [] },
    }
  }
  const EMPTY_CATALOG = { source: 'x', fetched_at: null, count: 0, roles: { image: [], video: [], voice: [], text: [] } }
  const SCAN = {
    services: [
      { id: 'ollama', name: 'Ollama', roles: ['text'], base_url: 'http://127.0.0.1:11434/v1', alive: false, models: [], error: 'OSError', reason: '本地文本推荐：接口就是 OpenAI 那套' },
      { id: 'comfyui', name: 'ComfyUI', roles: ['image', 'video'], base_url: 'http://127.0.0.1:8188', alive: true, models: ['sd_xl'], error: '', reason: '本地生视频 / Flux 走它' },
      { id: 'sdwebui', name: 'SD WebUI', roles: ['image'], base_url: 'http://127.0.0.1:7860', alive: true, models: ['sd_xl'], error: '', reason: '出图推荐：接口固定、装好就能用' },
    ],
    alive: ['comfyui', 'sdwebui'],
    recommend: { image: 'sdwebui', video: 'comfyui' },
    presets: [], roles: ['text', 'image', 'voice', 'video', 'music'],
  }

  beforeEach(() => {
    responses['/api/settings'] = doc()
    responses['/api/system/asr'] = { installed: false, frozen: false, models: [], default_model: 'small', install_hint: '' }
    responses['/api/system/model-catalog'] = EMPTY_CATALOG
    responses['/api/system/model-catalog?source=gateway'] = EMPTY_CATALOG
    responses['/api/system/local-workflows'] = { workflows: [], user_dir: 'D:/proj/local_workflows', note: '' }
    responses['/api/system/local-scan'] = SCAN
  })

  it('被点名的那个排在前面并标「推荐」，理由一行；没被点名的没有标记', async () => {
    const w = mount(Settings)
    await flushPromises()
    const card = w.find('[data-test="local-card"]')
    await card.find('[data-test="local-scan"]').trigger('click')
    await flushPromises()
    const ids = card.findAll('tbody tr').map((r) => r.attributes('data-test'))
    // 被点名的（在跑 + 推荐）排在前面，没在跑的排最后
    expect(ids.slice(0, 2).sort()).toEqual(['local-row-comfyui', 'local-row-sdwebui'])
    expect(ids[ids.length - 1]).toBe('local-row-ollama')
    expect(card.find('[data-test="local-reason-sdwebui"]').text()).toContain('推荐')
    expect(card.find('[data-test="local-reason-sdwebui"]').text()).toContain('接口固定')
    expect(card.find('[data-test="local-reason-comfyui"]').text()).toContain('本地生视频')
    expect(card.find('[data-test="local-reason-ollama"]').exists()).toBe(false)
  })

  it('一个都没探到：只有一行，不给安装教程也不给下载链接', async () => {
    responses['/api/system/local-scan'] = { ...SCAN, services: SCAN.services.map((s) => ({ ...s, alive: false })), alive: [], recommend: {} }
    const w = mount(Settings)
    await flushPromises()
    const card = w.find('[data-test="local-card"]')
    await card.find('[data-test="local-scan"]').trigger('click')
    await flushPromises()
    const none = card.find('[data-test="local-none"]')
    expect(none.exists()).toBe(true)
    expect(none.text().split('\n').filter((x) => x.trim()).length).toBe(1)
    expect(card.findAll('[data-test^="local-reason-"]').length).toBe(0)
    expect(card.html()).not.toContain('http://github.com')
    expect(card.html()).not.toContain('下载')
  })

  it('关过提示时才有「重新开启本地提示」，点了就清空', async () => {
    const plain = mount(Settings)
    await flushPromises()
    expect(plain.find('[data-test="local-hints-reset"]').exists()).toBe(false)
    responses['/api/settings'] = doc(['image'])
    responses['POST /api/system/local-hint/reset'] = { dismissed: [] }
    const w = mount(Settings)
    await flushPromises()
    const btn = w.find('[data-test="local-hints-reset"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    await flushPromises()
    expect(calls.some((c) => c[1] === '/api/system/local-hint/reset')).toBe(true)
    expect(w.find('[data-test="local-card"]').text()).toContain('本地提示已重新开启')
    expect(w.find('[data-test="local-hints-reset"]').exists()).toBe(false)
    expect(Object.keys(localHints)).toEqual([])
  })

  it('卡片开头那句只说「可以」，不写「先装好再来」', async () => {
    const w = mount(Settings)
    await flushPromises()
    const intro = w.find('[data-test="local-card"]').find('p.hint').text()
    expect(intro).toContain('可选的路')
    expect(intro).not.toContain('先在本机')
    expect(intro).not.toContain('请')
  })
})
