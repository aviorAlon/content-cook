// 新建项目页 · 形态（可选）下拉（DESIGN §31）：拉 /api/scripts/forms、选中后显示卡的摘要、提交时把 form 带给起草接口；不选就发 null。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
vi.mock('../src/api.js', () => ({
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url.startsWith('/api/episodes/next-id')) return { id: 'EP-1' }
    if (url === '/api/camera-options') return {
      profiles: ['short_drama_vertical', 'suspense_thriller', 'tv_drama'],
      profile_details: {
        short_drama_vertical: { english: 'Vertical short drama', refs: ['《无双》', '《闪婚》'], basis: '按参考作品的公认风格人工设定（2026-09-08），未做逐镜分析', confidence: '低' },
        suspense_thriller: { english: 'Suspense thriller', refs: ['《七宗罪》'], basis: '按参考作品的公认风格人工设定（2026-09-08），未做逐镜分析', confidence: '低' },
        tv_drama: { english: 'TV series drama', refs: ['《琅琊榜》'], basis: '按参考作品的公认风格人工设定（2026-09-08），未做逐镜分析', confidence: '低' },
      },
    }
    if (url === '/api/scripts/genres') return [
      { id: 'zhichang-xiju', name: '职场喜剧', aliases: [], core: '核心一句', confidence: '高', basis: '套件既有工艺文件与短剧写作经验归纳', source_file: '职场喜剧.md',
        pairing: { recommended: ['short_drama_vertical', 'tv_drama'], avoid: ['suspense_thriller'], why: '笑点靠落差，惊悚镜头会吃掉它' } },
      // 老后端：没有出处字段、没有 pairing——页面照常，只是不显示这两行
      { id: 'xuanyi-guize', name: '悬疑规则', aliases: [], core: '另一句' },
    ]
    if (url === '/api/scripts/forms') return [
      { id: 'vertical_drama', name: '竖屏短剧', aliases: ['短剧'], defaults: { aspect_ratio: '9:16', shot_seconds: [3, 8], subtitles: true, max_shots_per_minute: 12 }, must_have: ['前 3 秒有钩子'], summary: '9:16 · 单镜 3–8 秒 · 每分钟不超过 12 镜 · 字幕常开' },
      { id: 'horizontal_short', name: '横屏短片', aliases: [], defaults: { aspect_ratio: '16:9', shot_seconds: [4, 12], subtitles: false, max_shots_per_minute: 10 }, must_have: [], summary: '' },
    ]
    if (url === '/api/scripts/jobs') return []
    if (url === '/api/examples') return []
    return {}
  }),
  post: vi.fn(async (url, body) => { calls.push(['post', url, body]); return { job_id: 'j1', episode_id: 'EP-1' } }),
  pollJob: vi.fn(async () => ({ status: 'failed', error: '没配文本模型' })),
  put: vi.fn(), del: vi.fn(), ep: (id) => `/api/episodes/storyvideo/${id}`,
}))
vi.mock('../src/store.js', () => {
  const { reactive } = require('vue')
  return { store: reactive({ episodes: [] }), roleOk: () => true, loadEpisodes: vi.fn(async () => {}), withBusy: vi.fn(async (fn) => fn()) }
})
vi.mock('../src/ui.js', () => ({ notice: vi.fn(async () => {}), prompt: vi.fn(), confirm: vi.fn(async () => true) }))

import NewProject from '../src/views/NewProject.vue'

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div/>' } }, { path: '/settings', component: { template: '<div/>' } }] })
}

async function mountPage() {
  const w = mount(NewProject, { global: { plugins: [router()] } })
  await flushPromises()
  return w
}

beforeEach(() => { calls.length = 0 })

describe('NewProject · 形态（可选）', () => {
  it('拉形态卡列表，下拉首项「（不指定）」，选中后显示摘要', async () => {
    const w = await mountPage()
    expect(calls.some(([m, u]) => m === 'get' && u === '/api/scripts/forms')).toBe(true)
    const select = w.find('[data-test="form-select"]')
    expect(select.exists()).toBe(true)
    const options = select.findAll('option').map((o) => o.text())
    expect(options).toEqual(['（不指定）', '竖屏短剧', '横屏短片'])
    expect(w.find('[data-test="form-summary"]').exists()).toBe(false)
    await select.setValue('vertical_drama')
    const summary = w.find('[data-test="form-summary"]').text()
    expect(summary).toContain('9:16')
    expect(summary).toContain('单镜 3–8 秒')
    expect(summary).toContain('每分钟不超过 12 镜')
    expect(summary).toContain('字幕常开')
    await select.setValue('horizontal_short')
    expect(w.find('[data-test="form-summary"]').text()).toContain('字幕可选')
  })

  it('提交时把 form 带给起草接口；不选就是 null', async () => {
    const w = await mountPage()
    await w.find('textarea').setValue('一个人加班')
    await w.find('[data-test="form-select"]').setValue('vertical_drama')
    await w.find('button.spend').trigger('click')
    await flushPromises()
    const draft = calls.find(([m, u]) => m === 'post' && u === '/api/scripts/jobs/draft')
    expect(draft).toBeTruthy()
    expect(draft[2].form).toBe('vertical_drama')
    expect(draft[2].genre).toBeNull()

    calls.length = 0
    const w2 = await mountPage()
    await w2.find('textarea').setValue('一个人加班')
    await w2.find('button.spend').trigger('click')
    await flushPromises()
    const draft2 = calls.find(([m, u]) => m === 'post' && u === '/api/scripts/jobs/draft')
    expect(draft2[2].form).toBeNull()
  })

  it('出处行按接口数据拼：镜头风格一行、选了题材后题材一行；老后端没字段就不显示', async () => {
    const w = await mountPage()
    const prof = w.find('[data-test="profile-provenance"]')
    expect(prof.exists()).toBe(true)
    expect(prof.text()).toBe('按《无双》《闪婚》等作品的公认风格人工设定 · 置信度 低 · 未做逐镜分析')
    expect(prof.attributes('title')).toContain('未做逐镜分析')
    await w.find('[data-test="camera-profile-select"]').setValue('tv_drama')
    expect(w.find('[data-test="profile-provenance"]').text()).toContain('《琅琊榜》')

    expect(w.find('[data-test="genre-provenance"]').exists()).toBe(false)
    await w.find('[data-test="genre-select"]').setValue('zhichang-xiju')
    const gp = w.find('[data-test="genre-provenance"]')
    expect(gp.text()).toBe('出处：drama-skills 题材卡 · 置信度 高 · 例子为合成虚构，非数据统计')
    expect(gp.attributes('title')).toBe('套件既有工艺文件与短剧写作经验归纳 · 职场喜剧.md')
    await w.find('[data-test="genre-select"]').setValue('xuanyi-guize')
    expect(w.find('[data-test="genre-provenance"]').exists()).toBe(false)
  })

  it('题材 × 镜头风格：常配给一句肯定，会打架给理由和可点的常配档，其余不出声', async () => {
    const w = await mountPage()
    const hints = () => w.findAll('[data-test^="pairing-"]').filter((e) => !e.attributes('data-test').startsWith('pairing-option-'))
    expect(hints()).toHaveLength(0)                                   // 没选题材
    const genre = w.find('[data-test="genre-select"]')
    const camera = w.find('[data-test="camera-profile-select"]')
    await genre.setValue('zhichang-xiju')                              // 默认 short_drama_vertical：常配
    expect(w.find('[data-test="pairing-recommended"]').text()).toBe('这个题材常配这档镜头风格')
    await camera.setValue('suspense_thriller')                         // 会打架
    expect(w.find('[data-test="pairing-recommended"]').exists()).toBe(false)
    const avoid = w.find('[data-test="pairing-avoid"]')
    expect(avoid.text()).toContain('这个组合容易打架：笑点靠落差，惊悚镜头会吃掉它')
    expect(avoid.findAll('button').map((b) => b.text())).toEqual(['short_drama_vertical', 'tv_drama'])
    await w.find('[data-test="pairing-option-tv_drama"]').trigger('click')
    expect(camera.element.value).toBe('tv_drama')
    expect(w.find('[data-test="pairing-recommended"]').exists()).toBe(true)
    await genre.setValue('xuanyi-guize')                               // 没有搭配数据 / 既不推荐也不回避：不出声
    expect(hints()).toHaveLength(0)
    await genre.setValue('')
    expect(hints()).toHaveLength(0)
  })

  it('形态接口挂了也不影响页面，下拉只剩「（不指定）」', async () => {
    const api = await import('../src/api.js')
    const orig = api.get.getMockImplementation()
    api.get.mockImplementation(async (url) => { if (url === '/api/scripts/forms') throw new Error('boom'); return orig(url) })
    const w = await mountPage()
    expect(w.find('[data-test="form-select"]').findAll('option').map((o) => o.text())).toEqual(['（不指定）'])
    api.get.mockImplementation(orig)
  })
})
