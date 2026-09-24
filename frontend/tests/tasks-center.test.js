// 任务中心 /tasks（DESIGN §38）：跨模块列后台任务——模块 / 状态过滤、种类的人话、项目链接、
// 在跑时每 3 秒自动刷新（全停就不再刷）、空状态。只读页面：断言它从不发 POST。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()
vi.mock('../src/api.js', async (orig) => ({ ...(await orig()), get: (...a) => get(...a), post: (...a) => post(...a) }))
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import Tasks from '../src/views/Tasks.vue'
import { store } from '../src/store.js'
import { setLang } from '../src/i18n.js'
import { durationText, failureDetail, failureLine, kindLabel, relTime, taskTarget } from '../src/tasks.js'

const MODULES = [
  { id: 'drama', name: '短剧', english: 'Short drama', icon: '🎬', route: '/drama', available: true, project_count: 1 },
  { id: 'picturebook', name: '绘本 / 有声书', english: 'Picture book', icon: '📖', route: '/picturebook', available: true, project_count: 1 },
  { id: 'sticker', name: '表情与头像', english: 'Stickers', icon: '🙂', route: '/sticker', available: true, project_count: 1 },
  { id: 'planned_demo', name: '规划中的模块', english: 'Planned module', icon: '🧩', route: null, available: false, project_count: 0 },
]

const NOW = Date.now()
const S = (msAgo) => (NOW - msAgo) / 1000        // 后端时间戳是秒

const ROWS = [
  { task_id: 't1', kind: 'pb-pages', module: 'picturebook', episode_id: 'PB-1', status: 'running',
    created_at: S(120_000), started_at: S(120_000) },
  { task_id: 't2', kind: 'set-generate', module: 'sticker', episode_id: 'ST-1', status: 'failed',
    created_at: S(3600_000), started_at: S(3600_000), finished_at: S(3595_000),
    // 后端现在的形状：error 只放人话，异常类型名另存 error_type
    error: '生图来源没配置\n  第二行细节', error_type: 'ProviderError' },
  { task_id: 't3', kind: 'generate-watch', module: 'drama', episode_id: 'A-1', status: 'succeeded',
    created_at: S(7200_000), started_at: S(7200_000), finished_at: S(7100_000), result: { rounds: 3 } },
  { task_id: 't4', kind: 'install-asr', module: 'drama', episode_id: '-', status: 'succeeded',
    created_at: S(9000_000), started_at: S(9000_000), finished_at: S(8990_000) },
]

let rows = []
let urls = []

function stubRouter() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div/>' } }, { path: '/tasks', component: { template: '<div/>' } },
    { path: '/drama/p/:id/overview', component: { template: '<div/>' } },
    { path: '/picturebook/b/:id/overview', component: { template: '<div/>' } },
    { path: '/sticker/s/:id/items', component: { template: '<div/>' } },
    { path: '/voices/:voiceId?', component: { template: '<div/>' } },
  ] })
}

async function mountTasks() {
  const r = stubRouter(); await r.push('/tasks'); await r.isReady()
  const w = mount(Tasks, { global: { plugins: [r] } })
  await flushPromises()
  return w
}

beforeEach(() => {
  setLang('zh')
  store.modules = MODULES; store.modulesLoaded = true
  rows = ROWS; urls = []
  get.mockImplementation(async (url) => { urls.push(String(url)); return rows })
  post.mockReset()
})
afterEach(() => { get.mockReset(); vi.useRealTimers() })

describe('任务中心 · 表格', () => {
  it('一行一条任务：模块 / 种类人话 / 项目链接 / 状态 / 开始时间 / 用时', async () => {
    const w = await mountTasks()
    expect(urls[0]).toBe('/api/tasks')
    const trs = w.findAll('[data-test="task-row"]')
    expect(trs.length).toBe(4)
    const first = trs[0]
    expect(first.text()).toContain('绘本 / 有声书')
    expect(first.text()).toContain('绘本插画')          // pb-pages 的人话
    expect(first.text()).toContain('在跑')
    expect(first.text()).toContain('2 分钟前')
    expect(first.find('[data-test="task-project"]').attributes('href')).toBe('/picturebook/b/PB-1/overview')
    expect(trs[1].text()).toContain('出图')             // set-generate
    expect(trs[1].find('[data-test="task-project"]').attributes('href')).toBe('/sticker/s/ST-1/items')
    expect(trs[2].text()).toContain('等服务商返回')      // generate-watch
    expect(trs[2].find('[data-test="task-project"]').attributes('href')).toBe('/drama/p/A-1/overview')
    // 安装依赖这类任务的 episode_id 不是项目，不给一个打不开的链接
    expect(trs[3].text()).toContain('安装语音识别')
    expect(trs[3].find('[data-test="task-project"]').exists()).toBe(false)
    expect(post).not.toHaveBeenCalled()
  })

  it('失败原因一行人话，点「展开」看原文', async () => {
    const w = await mountTasks()
    const failed = w.findAll('[data-test="task-row"]')[1]
    expect(failed.find('[data-test="task-error"]').text()).toBe('生图来源没配置')   // 去掉异常类型名与第二行
    expect(w.find('[data-test="task-error-full"]').exists()).toBe(false)
    await failed.find('[data-test="task-error-toggle"]').trigger('click')
    expect(w.find('[data-test="task-error-full"]').text()).toContain('ProviderError')
    expect(w.find('[data-test="task-error-full"]').text()).toContain('第二行细节')
    await failed.find('[data-test="task-error-toggle"]').trigger('click')
    expect(w.find('[data-test="task-error-full"]').exists()).toBe(false)
  })

  it('老记录（类型名还在 error 里、没有 error_type）照旧：一行人话 + 展开看原文，类型名不重复', async () => {
    rows = [{ ...ROWS[1], error: 'ProviderError: 生图来源没配置\n  第二行细节', error_type: undefined }]
    const w = await mountTasks()
    const failed = w.find('[data-test="task-row"]')
    expect(failed.find('[data-test="task-error"]').text()).toBe('生图来源没配置')
    await failed.find('[data-test="task-error-toggle"]').trigger('click')
    const full = w.find('[data-test="task-error-full"]').text()
    expect(full.startsWith('ProviderError: 生图来源没配置')).toBe(true)
    expect(full.match(/ProviderError/g).length).toBe(1)
  })

  it('空状态说清楚这一页会记什么', async () => {
    rows = []
    const w = await mountTasks()
    expect(w.find('[data-test="task-table"]').exists()).toBe(false)
    expect(w.find('[data-test="tasks-empty"]').text()).toBe('还没有任务。生成、装配、导出这类耗时的活会记在这里。')
  })
})

describe('任务中心 · 过滤', () => {
  it('模块过滤走服务端（?module=），只列可用模块 + 全部', async () => {
    const w = await mountTasks()
    const filters = w.find('[data-test="task-filters"]')
    expect(filters.find('[data-test="module-filter-drama"]').exists()).toBe(true)
    expect(filters.find('[data-test="module-filter-picturebook"]').exists()).toBe(true)
    expect(filters.find('[data-test="module-filter-planned_demo"]').exists()).toBe(false)   // 规划中的模块不给按钮
    rows = [ROWS[1]]
    await filters.find('[data-test="module-filter-sticker"]').trigger('click')
    await flushPromises()
    expect(urls.at(-1)).toBe('/api/tasks?module=sticker')
    expect(w.findAll('[data-test="task-row"]').length).toBe(1)
    rows = ROWS
    await filters.find('[data-test="module-filter-all"]').trigger('click')
    await flushPromises()
    expect(urls.at(-1)).toBe('/api/tasks')
    expect(w.findAll('[data-test="task-row"]').length).toBe(4)
  })

  it('状态过滤在本地筛：在跑 = pending + running', async () => {
    const w = await mountTasks()
    const before = urls.length
    await w.find('[data-test="status-filter-active"]').trigger('click')
    expect(w.findAll('[data-test="task-row"]').map(r => r.attributes('data-status'))).toEqual(['running'])
    expect(urls.length).toBe(before)         // 不用再问一次服务端
    await w.find('[data-test="status-filter-failed"]').trigger('click')
    expect(w.findAll('[data-test="task-row"]').map(r => r.attributes('data-status'))).toEqual(['failed'])
    await w.find('[data-test="status-filter-succeeded"]').trigger('click')
    expect(w.findAll('[data-test="task-row"]').length).toBe(2)
    await w.find('[data-test="status-filter-all"]').trigger('click')
    expect(w.findAll('[data-test="task-row"]').length).toBe(4)
  })
})

describe('任务中心 · 自动刷新', () => {
  it('有任务在跑就每 3 秒刷一次，全停之后不再刷', async () => {
    vi.useFakeTimers()
    const r = stubRouter(); await r.push('/tasks'); await r.isReady()
    const w = mount(Tasks, { global: { plugins: [r] } })
    await flushPromises()
    expect(urls.length).toBe(1)
    expect(w.find('[data-test="tasks-auto"]').text()).toContain('1 个在跑')

    await vi.advanceTimersByTimeAsync(3000)
    expect(urls.length).toBe(2)
    await vi.advanceTimersByTimeAsync(3000)
    expect(urls.length).toBe(3)

    // 最后一条也跑完了：这一轮之后不再起新的定时器
    rows = ROWS.map(t => ({ ...t, status: t.status === 'running' ? 'succeeded' : t.status }))
    await vi.advanceTimersByTimeAsync(3000)
    expect(urls.length).toBe(4)
    expect(w.find('[data-test="tasks-auto"]').text()).toContain('没有在跑的任务')
    await vi.advanceTimersByTimeAsync(30000)
    expect(urls.length).toBe(4)
    w.unmount()
  })

  it('一开始就没有在跑的任务时压根不轮询', async () => {
    vi.useFakeTimers()
    rows = [ROWS[2]]
    const r = stubRouter(); await r.push('/tasks'); await r.isReady()
    const w = mount(Tasks, { global: { plugins: [r] } })
    await flushPromises()
    expect(urls.length).toBe(1)
    await vi.advanceTimersByTimeAsync(30000)
    expect(urls.length).toBe(1)
    w.unmount()
  })

  // 卸载时若有一次 load 在飞（发 tag 前审计 FE-03）：回来后不能再排下一轮——不然只要有任务在跑，
  // 离开这一页之后还会每 3 秒打一次后端，反复进出还会叠加
  function holdNext() {
    let release
    get.mockImplementationOnce((url) => { urls.push(String(url)); return new Promise((res) => { release = () => res(rows) }) })
    return () => release()
  }

  it('首次加载还没回来用户就切走：回来后不再轮询', async () => {
    vi.useFakeTimers()
    const release = holdNext()
    const r = stubRouter(); await r.push('/tasks'); await r.isReady()
    const w = mount(Tasks, { global: { plugins: [r] } })
    await flushPromises()
    expect(urls.length).toBe(1)
    w.unmount()
    release(); await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    expect(urls.length).toBe(1)
  })

  it('定时刷新那一次在飞时切走：同样不再轮询', async () => {
    vi.useFakeTimers()
    const w = await mountTasks()
    expect(urls.length).toBe(1)
    const release = holdNext()
    await vi.advanceTimersByTimeAsync(3000)
    expect(urls.length).toBe(2)
    w.unmount()
    release(); await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    expect(urls.length).toBe(2)
  })

  it('反复进出（每次都在飞时切走）：不会留下叠加的轮询', async () => {
    vi.useFakeTimers()
    const releases = []
    for (let i = 0; i < 3; i++) {
      releases.push(holdNext())
      const r = stubRouter(); await r.push('/tasks'); await r.isReady()
      const w = mount(Tasks, { global: { plugins: [r] } })
      await flushPromises()
      w.unmount()
    }
    expect(urls.length).toBe(3)
    for (const release of releases) release()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    expect(urls.length).toBe(3)
  })
})

describe('tasks.js · 纯函数', () => {
  it('种类人话认不出就原样显示 kind', () => {
    expect(kindLabel('music-generate')).toBe('配乐')
    expect(kindLabel('poster')).toBe('海报')
    expect(kindLabel('voice-clone')).toBe('音色克隆')
    expect(kindLabel('clip-generate')).toBe('出片')
    expect(kindLabel('zzz-unknown')).toBe('zzz-unknown')
  })

  it('任务 → 项目地址：对比任务取剧集、音色克隆去音色库、安装类没有链接', () => {
    expect(taskTarget({ kind: 'compare', module: 'drama', episode_id: 'A-1:S2' }).url).toBe('/drama/p/A-1/overview')
    expect(taskTarget({ kind: 'voice-clone', module: 'drama', episode_id: 'voice:v1' }).url).toBe('/voices/v1')
    expect(taskTarget({ kind: 'music-generate', module: 'music', episode_id: 'MU-1' }).url).toBe('/music/m/MU-1/generate')
    expect(taskTarget({ kind: 'clip-generate', module: 'clip', episode_id: 'CL-1' }).url).toBe('/clip/c/CL-1/write')
    expect(taskTarget({ kind: 'install-asr', module: 'drama', episode_id: '-' })).toBe(null)
    expect(taskTarget({ kind: 'asr-model', module: 'drama', episode_id: 'medium' })).toBe(null)
    expect(taskTarget({ kind: 'post', module: 'nope', episode_id: 'X' }).url).toBe(null)
  })

  it('相对时间与用时', () => {
    const now = Date.now()
    expect(relTime(now / 1000, now)).toBe('刚刚')
    expect(relTime((now - 180_000) / 1000, now)).toBe('3 分钟前')
    expect(relTime((now - 7200_000) / 1000, now)).toBe('2 小时前')
    expect(relTime((now - 30 * 3600_000) / 1000, now)).toBe('昨天')
    expect(relTime((now - 5 * 86400_000) / 1000, now)).toBe('5 天前')
    expect(relTime(0)).toBe('—')
    expect(durationText({ started_at: 100, finished_at: 145, status: 'succeeded' })).toBe('45 秒')
    expect(durationText({ started_at: 100, finished_at: 100 + 125, status: 'succeeded' })).toBe('2 分 5 秒')
    expect(durationText({ started_at: 100, status: 'running' }, 100_000 + 60_000)).toBe('1 分 0 秒')
  })

  it('失败原因：去掉异常类型名，只留第一行；没有 error 返回空串', () => {
    expect(failureLine({ error: 'RuntimeError: 服务商拒绝了这次提交' })).toBe('服务商拒绝了这次提交')
    expect(failureLine({ error: 'TaskBusy: 已经在跑\n细节' })).toBe('已经在跑')
    expect(failureLine({ error: '直接一句中文' })).toBe('直接一句中文')
    expect(failureLine({})).toBe('')
  })

  it('展开的原文：error_type 拼回类型名；老记录原样；消息为空时后端拿类型名兜底，不拼成「X: X」', () => {
    expect(failureDetail({ error: "'shots'", error_type: 'KeyError' })).toBe("KeyError: 'shots'")
    // 程序 bug 后端照旧把类型名留在 error 里（「KeyError: 'shots'」），不再拼第二遍
    expect(failureDetail({ error: "KeyError: 'shots'", error_type: 'KeyError' })).toBe("KeyError: 'shots'")
    expect(failureDetail({ error: '已经在跑\n细节', error_type: 'TaskBusy' })).toBe('TaskBusy: 已经在跑\n细节')
    expect(failureDetail({ error: 'RuntimeError: 服务商拒绝了这次提交' })).toBe('RuntimeError: 服务商拒绝了这次提交')
    expect(failureDetail({ error: 'RuntimeError: 服务商拒绝了这次提交', error_type: 'RuntimeError' }))
      .toBe('RuntimeError: 服务商拒绝了这次提交')
    expect(failureDetail({ error: 'ValueError', error_type: 'ValueError' })).toBe('ValueError')
    expect(failureDetail({})).toBe('')
  })
})
