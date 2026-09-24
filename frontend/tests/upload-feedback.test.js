// 发布前加固（零碎体验）：参考图上传的反馈要说真话。api / ui 全 mock。
// * 高级页补传参考图（图片集 SetAdvanced / 配乐 MusicAdvanced）：后端按内容认出是已有的那张（existing: true）时说
//   「同一张，没有重复添加」，不再一律「已上传」——用户以为传上了，列表里却数不见。
// * 纪念写真新建页（SetNew）：传不上的照片逐张列出原因（HEIC 的「请在手机上导出为 JPG」得让人看到），
//   不再只报一句「N 张上传失败」。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const { calls, replies, toastMock, noticeMock, fixtures } = vi.hoisted(() => {
  const setSpec = {
    id: 'PT-1', module: 'portrait', title: '纪念写真', template: 'wedding',
    subject: { kind: 'photo', assets: [{ file: 'content/assets/PT-1/bride-1234abcd.jpg', who: 'bride' }], fidelity: 90 },
    style: 'film', options: {}, items: [], picks: [],
  }
  const musicSpec = { id: 'MU-1', title: '纪念日', brief: { usage: '片尾' }, duration: 60, count: 1, reference_images: ['content/assets/MU-1/a-1234abcd.png'], attempts: [] }
  return {
    calls: [], replies: { upload: () => ({ existing: false }) }, toastMock: vi.fn(), noticeMock: vi.fn(async () => {}),
    fixtures: {
      setDetail: { id: 'PT-1', module: 'portrait', spec: setSpec, items: [], assets: [] },
      musicDetail: { id: 'MU-1', spec: musicSpec, references: [{ file: musicSpec.reference_images[0], exists: true }], limits: { max_references: 10 } },
      templates: { templates: [{ id: 'wedding', name: '婚纱', description: '两人', styles: [{ id: 'film', name: '复古胶片', anchor: 'vintage film' }],
                                 options_schema: { scenes: { type: 'list', default: ['教堂'] } } }] },
    },
  }
})

vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url === '/api/portrait/templates') return fixtures.templates
    if (url.startsWith('/api/music/')) return fixtures.musicDetail
    if (/^\/api\/portrait\/[^/]+$/.test(url)) return fixtures.setDetail
    return []
  }),
  post: vi.fn(async (url, body) => {
    calls.push(['post', url, body])
    if (url === '/api/portrait') return { id: 'PT-2', spec: body }
    if (url.endsWith('/assets') || url.endsWith('/references')) {
      const reply = replies.upload(body)
      if (reply instanceof Error) throw reply
      return reply
    }
    return {}
  }),
  put: vi.fn(async () => ({})), del: vi.fn(async () => ({})), ep: (id) => `/api/episodes/storyvideo/${id}`,
  pollTask: vi.fn(), pollJob: vi.fn(), sleep: vi.fn(async () => {}), fileToBase64: vi.fn(async () => 'QUJD'),
}))
vi.mock('../src/ui.js', () => ({
  prompt: vi.fn(), confirm: vi.fn(async () => true), notice: (...a) => noticeMock(...a), toast: (...a) => toastMock(...a), custom: vi.fn(), toasts: [],
  T: { role: { text: '剧本文本', image: '生图', voice: '语音', video: '视频' } },
}))

import { store } from '../src/store.js'
import { loadSet, clearSet } from '../src/imageset.js'
import { loadMusic } from '../src/music.js'
import SetAdvanced from '../src/views/imageset/SetAdvanced.vue'
import SetNew from '../src/views/imageset/SetNew.vue'
import MusicAdvanced from '../src/views/music/MusicAdvanced.vue'

const HEIC_HINT = '这是 HEIC 格式的照片（苹果手机默认的格式），这里读不了——请在手机上导出为 JPG 再传（iPhone 可在「设置 → 相机 → 格式」里选「兼容性最佳」，之后拍的就是 JPG）'
const stub = { template: '<div/>' }

async function mountAt(Comp, path) {
  const r = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: stub }, { path: '/settings', component: stub },
    { path: '/portrait', component: stub, meta: { module: 'portrait' } }, { path: '/portrait/new', component: stub, meta: { module: 'portrait' } },
    { path: '/portrait/s/:id/:sub', component: stub, meta: { module: 'portrait' } },
    { path: '/music', component: stub }, { path: '/music/m/:id/:sub', component: stub },
  ] })
  await r.push(path); await r.isReady()
  const w = mount(Comp, { global: { plugins: [r] } })
  await flushPromises()
  return { w, r }
}
async function pick(w, selector, names) {
  const input = w.find(selector)
  Object.defineProperty(input.element, 'files', { value: names.map(n => new File(['x'], n, { type: 'image/jpeg' })), configurable: true })
  await input.trigger('change'); await flushPromises()
}
const toasts = () => toastMock.mock.calls.map(c => String(c[0]))

beforeEach(() => {
  calls.length = 0; toastMock.mockReset(); noticeMock.mockReset(); noticeMock.mockResolvedValue(undefined)
  replies.upload = () => ({ existing: false })
  clearSet()
  store.modules = [{ id: 'portrait', name: '纪念写真', english: 'Milestone portraits', icon: '📷', route: '/portrait', status: 'ready', available: true, project_count: 1 }]
  store.modulesLoaded = true
  store.health = { roles: { image: { configured: true } } }
})

describe('高级页补传参考图：重复的那张要说出来', () => {
  it('图片集 SetAdvanced：existing → 「同一张，没有重复添加」；新图 → 「已上传」', async () => {
    await loadSet('portrait', 'PT-1')
    const { w } = await mountAt(SetAdvanced, '/portrait/s/PT-1/advanced')
    replies.upload = (body) => ({ existing: body.filename === 'IMG_0001 (1).jpg' })
    await pick(w, '[data-test="asset-upload"]', ['IMG_0001 (1).jpg', 'IMG_0002.jpg'])
    expect(calls.filter(c => c[0] === 'post' && c[1] === '/api/portrait/PT-1/assets').length).toBe(2)
    expect(toasts()).toEqual(['「IMG_0001 (1).jpg」跟已有的参考图是同一张，没有重复添加', '已上传 IMG_0002.jpg'])
    expect(noticeMock).not.toHaveBeenCalled()
  })

  it('配乐 MusicAdvanced：同样按 existing 分开说', async () => {
    await loadMusic('MU-1')
    const { w } = await mountAt(MusicAdvanced, '/music/m/MU-1/advanced')
    replies.upload = () => ({ existing: true })
    await pick(w, '[data-test="reference-upload"]', ['cover-copy.png'])
    expect(toasts()).toEqual(['「cover-copy.png」跟已有的参考图是同一张，没有重复添加'])
    replies.upload = () => ({ existing: false })
    toastMock.mockReset()
    await pick(w, '[data-test="reference-upload"]', ['new.png'])
    expect(toasts()).toEqual(['已上传 new.png'])
  })

  it('上传失败照旧弹原因（HEIC 的「导出为 JPG」原样给人看）', async () => {
    await loadSet('portrait', 'PT-1')
    const { w } = await mountAt(SetAdvanced, '/portrait/s/PT-1/advanced')
    replies.upload = () => Object.assign(new Error(HEIC_HINT), { status: 400 })
    await pick(w, '[data-test="asset-upload"]', ['IMG_0003.HEIC'])
    expect(noticeMock.mock.calls.map(c => c[0])).toEqual([`上传失败：${HEIC_HINT}`])
    expect(toasts()).toEqual([])
  })
})

describe('纪念写真新建：传不上的照片逐张说原因', () => {
  it('HEIC 被拒：建完项目后列出是哪张、为什么，而不是只说「N 张上传失败」', async () => {
    const { w, r } = await mountAt(SetNew, '/portrait/new')
    await pick(w, '[data-test="asset-file"]', ['IMG_0001.jpg', 'IMG_0002.HEIC'])
    replies.upload = (body) => (body.filename.endsWith('.HEIC') ? Object.assign(new Error(HEIC_HINT), { status: 400 }) : { existing: false })
    await w.find('[data-test="set-title"]').setValue('我们的婚纱照')
    await w.find('[data-test="set-create"]').trigger('click'); await flushPromises()
    expect(calls.filter(c => c[0] === 'post' && c[1] === '/api/portrait/PT-2/assets').map(c => c[2].filename)).toEqual(['IMG_0001.jpg', 'IMG_0002.HEIC'])
    expect(noticeMock).toHaveBeenCalledTimes(1)
    const message = String(noticeMock.mock.calls[0][0])
    expect(message.startsWith('1 张参考图没传上去，进项目后可以在「高级」里补：')).toBe(true)
    expect(message).toContain(`IMG_0002.HEIC：${HEIC_HINT}`)
    expect(message).not.toContain('IMG_0001.jpg')
    expect(r.currentRoute.value.path).toBe('/portrait/s/PT-2/items')     // 项目照样建好、进清单页
  })

  it('都传上了：不弹窗', async () => {
    const { w, r } = await mountAt(SetNew, '/portrait/new')
    await pick(w, '[data-test="asset-file"]', ['IMG_0001.jpg'])
    await w.find('[data-test="set-title"]').setValue('百天')
    await w.find('[data-test="set-create"]').trigger('click'); await flushPromises()
    expect(noticeMock).not.toHaveBeenCalled()
    expect(r.currentRoute.value.path).toBe('/portrait/s/PT-2/items')
  })
})
