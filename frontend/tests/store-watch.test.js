// 生成轮询（DESIGN §34）：2026-09-08 之前 runGenerate 在 pending 为 0 时就收工——pending 不含已提交还没回来的镜，
// 所以提交一返回循环就结束；再点只弹「不用再点」。现在：在飞的镜由服务端 generate-watch 任务等，前端盯到「没有待生成、
// 也没有在飞的」为止；概览说后台没在等就补发一次 generate-resume。
import { describe, it, expect, vi, beforeEach } from 'vitest'

const calls = []
// estimate 里放 Error 对象 = 那一次请求失败（带 status）；summaryErrors 同理，是刷新项目时 /summary 依次抛的错
const responses = { estimate: [], fallback: null, summary: { watching: true, failures: [] }, summaryErrors: [] }
vi.mock('../src/api.js', async (importOriginal) => {
  // 「哪些错算瞬时」、重试几次用真的（纯函数 / 常量）；请求与等待是替身
  const actual = await importOriginal()
  return {
    get: vi.fn(async (url) => {
      calls.push(['get', url])
      if (url.includes('/generate-estimate')) {
        const next = responses.estimate.length ? responses.estimate.shift() : responses.fallback
        if (next instanceof Error) throw next
        return next
      }
      if (url.endsWith('/summary')) {
        const error = responses.summaryErrors.shift()
        if (error) throw error
        return responses.summary
      }
      if (url === '/api/episodes') return []
      return {}
    }),
    post: vi.fn(async (url, body) => { calls.push(['post', url, body]); return {} }),
    put: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    ep: (id) => `/api/episodes/storyvideo/${id}`,
    sleep: vi.fn(async () => {}),
    isTransientError: actual.isTransientError,
    POLL_RETRIES: actual.POLL_RETRIES,
  }
})
vi.mock('../src/ui.js', () => ({
  custom: vi.fn(async () => true),
  notice: vi.fn(async () => {}),
  toast: vi.fn(),
}))

import { store, runGenerate } from '../src/store.js'
import { toast, notice } from '../src/ui.js'

const posts = (suffix) => calls.filter(([kind, url]) => kind === 'post' && url.endsWith(suffix))

beforeEach(() => {
  calls.length = 0
  vi.clearAllMocks()
  responses.estimate = []
  responses.fallback = null
  responses.summary = { watching: true, failures: [] }
  responses.summaryErrors = []
  store.projectId = 'EP1'
  store.gen = null
})

describe('runGenerate · 上次提交的还在飞', () => {
  it('pending 为 0 但有在飞的：补发 generate-resume、提示后台在等，并盯到全部回来', async () => {
    responses.estimate = [
      { pending_count: 0, in_progress: ['S1', 'S2'], failed: [], plan_token: 't0' },
      { pending_count: 0, in_progress: ['S2'], failed: [], plan_token: 't1' },
      { pending_count: 0, in_progress: [], failed: [], plan_token: 't2' },
    ]
    const ok = await runGenerate('videos')
    expect(ok).toBe(true)
    expect(posts('/generate-resume')).toHaveLength(1)
    expect(posts('/generate')).toHaveLength(0)
    expect(notice).not.toHaveBeenCalled()
    expect(toast).toHaveBeenCalledWith('上次提交的还在生成（S1、S2），后台在等结果')
    expect(toast).toHaveBeenLastCalledWith('这一批生成完了')
    expect(store.gen).toBe(null)
  })

  it('概览说后台没有在等（watching: false）：轮询里再补发一次 generate-resume，只补一次', async () => {
    responses.summary = { watching: false, failures: [] }
    responses.estimate = [
      { pending_count: 0, in_progress: ['S1'], failed: [], plan_token: 't0' },
      { pending_count: 0, in_progress: ['S1'], failed: [], plan_token: 't1' },
      { pending_count: 0, in_progress: ['S1'], failed: [], plan_token: 't2' },
      { pending_count: 0, in_progress: [], failed: [], plan_token: 't3' },
    ]
    await runGenerate('videos')
    expect(posts('/generate-resume')).toHaveLength(2)   // 入口一次 + 轮询里一次
  })
})

describe('runGenerate · 正常提交', () => {
  it('提交后在飞的镜不算完成；没有待生成也没有在飞的才收工，中途不重复提交', async () => {
    responses.estimate = [
      { pending_count: 2, in_progress: [], failed: [], plan_token: 'tok' },
      { pending_count: 0, in_progress: ['S1', 'S2'], failed: [], plan_token: 't1' },
      { pending_count: 0, in_progress: [], failed: [], plan_token: 't2' },
    ]
    const ok = await runGenerate('videos', ['S1', 'S2'])
    expect(ok).toBe(true)
    const gen = posts('/generate')
    expect(gen).toHaveLength(1)
    expect(gen[0][2]).toEqual({ through: 'videos', only: ['S1', 'S2'], plan_token: 'tok', wait: false })
    expect(posts('/generate-resume')).toHaveLength(0)
    expect(toast).toHaveBeenLastCalledWith('这一批生成完了')
  })

  it('下一波还有待生成、又没有在飞的：按新 token 再提交一次（依赖上一波首帧的镜）', async () => {
    responses.estimate = [
      { pending_count: 1, in_progress: [], failed: [], plan_token: 'tok' },
      { pending_count: 1, in_progress: [], failed: [], plan_token: 'next' },
      { pending_count: 0, in_progress: [], failed: [], plan_token: 'done' },
    ]
    await runGenerate('frames')
    const gen = posts('/generate')
    expect(gen.map((c) => c[2].plan_token)).toEqual(['tok', 'next'])
  })

  it('超过轮询上限：提示后台会继续等，清掉进度条', async () => {
    responses.estimate = [{ pending_count: 1, in_progress: [], failed: [], plan_token: 'tok' }]
    responses.fallback = { pending_count: 0, in_progress: ['S1'], failed: [], plan_token: 'x' }
    const ok = await runGenerate('videos')
    expect(ok).toBe(true)
    expect(toast).toHaveBeenCalledWith('服务商还没返回，后台会继续等；回到项目页会看到最新状态', expect.objectContaining({ kind: 'warn' }))
    expect(store.gen).toBe(null)
    expect(posts('/generate')).toHaveLength(1)
  })

  it('全部失败且没有在飞的：停下来把原因摆出来', async () => {
    responses.summary = { watching: false, failures: [{ shot_id: 'S1', stage: 'videos', label: '余额或配额不足', advice: '充值后重提' }] }
    responses.estimate = [
      { pending_count: 1, in_progress: [], failed: [], plan_token: 'tok' },
      { pending_count: 1, in_progress: [], failed: ['S1'], plan_token: 't1' },
    ]
    const ok = await runGenerate('videos')
    expect(ok).toBe(false)
    expect(notice).toHaveBeenCalledWith(expect.stringContaining('余额或配额不足'))
    expect(posts('/generate')).toHaveLength(1)
  })
})

// 发布前加固（审查意见）：盯生成是最主要的花钱流程，以前轮询预估 / 刷新项目时一次 5xx（后端读记录那一下文件正被占用）
// 或连不上就停掉盯梢、弹「生成过程中出错」——后台其实还在等服务商。现在跟任务 / 起草轮询一样：瞬时错误重试 3 次再报错。
describe('runGenerate · 轮询遇到瞬时错误', () => {
  const failure = (status, message = `HTTP ${status}`) => Object.assign(new Error(message), { status })
  const estimates = (url) => calls.filter(([kind, u]) => kind === 'get' && u === url)
  const ESTIMATE = '/api/episodes/storyvideo/EP1/generate-estimate?through=videos'

  it('中途一次 500、一次连不上都不打断，接着盯到全部回来', async () => {
    responses.estimate = [
      { pending_count: 1, in_progress: [], failed: [], plan_token: 'tok' },
      failure(500, '任务记录正被占用'),
      { pending_count: 0, in_progress: ['S1'], failed: [], plan_token: 't1' },
      failure(0, '连不上本地服务'),
      { pending_count: 0, in_progress: [], failed: [], plan_token: 't2' },
    ]
    const ok = await runGenerate('videos')
    expect(ok).toBe(true)
    expect(notice).not.toHaveBeenCalled()
    expect(toast).toHaveBeenLastCalledWith('这一批生成完了')
    expect(posts('/generate')).toHaveLength(1)                 // 重试的是读，提交没有被重复发
    expect(estimates(ESTIMATE)).toHaveLength(5)
  })

  it('刷新项目时一次 503 也不打断', async () => {
    responses.estimate = [
      { pending_count: 0, in_progress: ['S1'], failed: [], plan_token: 't0' },
      { pending_count: 0, in_progress: ['S1'], failed: [], plan_token: 't1' },
      { pending_count: 0, in_progress: [], failed: [], plan_token: 't2' },
    ]
    responses.summaryErrors = [failure(503)]
    const ok = await runGenerate('videos')
    expect(ok).toBe(true)
    expect(notice).not.toHaveBeenCalled()
    expect(toast).toHaveBeenLastCalledWith('这一批生成完了')
  })

  it('连续 4 次（1 次 + 3 次重试）都失败才停下来报错，报的是最后那一次的原因', async () => {
    responses.estimate = [
      { pending_count: 1, in_progress: [], failed: [], plan_token: 'tok' },
      failure(500, '第一次'), failure(502, '第二次'), failure(0, '第三次'), failure(500, '第四次还是被占用'),
      { pending_count: 0, in_progress: [], failed: [], plan_token: 'never' },
    ]
    const ok = await runGenerate('videos')
    expect(ok).toBe(true)
    expect(notice).toHaveBeenCalledWith('生成过程中出错：第四次还是被占用')
    expect(estimates(ESTIMATE)).toHaveLength(5)                // 入口 1 次 + 轮询里 4 次，第 6 个回复没被请求
    expect(store.gen).toBe(null)
  })

  it('404 这类真的错不重试，立刻停下来报错', async () => {
    responses.estimate = [
      { pending_count: 1, in_progress: [], failed: [], plan_token: 'tok' },
      failure(404, '没有这个项目：EP1'),
      { pending_count: 0, in_progress: [], failed: [], plan_token: 'never' },
    ]
    await runGenerate('videos')
    expect(notice).toHaveBeenCalledWith('生成过程中出错：没有这个项目：EP1')
    expect(estimates(ESTIMATE)).toHaveLength(2)
  })
})
