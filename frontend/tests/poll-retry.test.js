// 发布前加固：任务 / 起草 job 的轮询遇到瞬时错误（后端读任务记录那一下文件正被占用返回 5xx、网络抖一下 / 服务在重启）
// 先重试，连续 3 次重试都失败才报错；404 / 400 这类真的错立刻抛。用真的 api.js，只替换 fetch。
import { describe, it, expect, vi } from 'vitest'
import { pollTask, pollJob, getRetrying, isTransientError, POLL_RETRIES } from '../src/api.js'

function mockFetch(sequence) {
  const calls = []
  globalThis.fetch = vi.fn(async (url) => {
    calls.push(url)
    const next = sequence.shift()
    if (next === 'offline') throw new TypeError('Failed to fetch')
    return { ok: next.ok ?? true, status: next.status ?? 200, statusText: next.statusText || 'OK', json: async () => next.body ?? null }
  })
  return calls
}
const busy = (detail = '任务记录正被占用') => ({ ok: false, status: 500, statusText: 'Internal Server Error', body: { detail } })

describe('轮询的瞬时错误先重试', () => {
  it('pollTask：中途一次 500、一次连不上、一次 503 都不报错，接着轮到结束', async () => {
    const calls = mockFetch([{ body: { status: 'running' } }, busy(), 'offline', { ok: false, status: 503, statusText: 'Service Unavailable' },
      { body: { status: 'succeeded', result: 1 } }])
    const ticks = []
    const task = await pollTask('t1', (t) => ticks.push(t.status), 1)
    expect(task).toEqual({ status: 'succeeded', result: 1 })
    expect(ticks).toEqual(['running', 'succeeded'])      // 失败的那几次不回调、不打断
    expect(calls.length).toBe(5)
  })

  it('连续 3 次重试都失败才报错，报的是最后那一次的原因', async () => {
    expect(POLL_RETRIES).toBe(3)
    const calls = mockFetch([busy('第一次'), 'offline', busy('第三次'), busy('第四次还是被占用'), { body: { status: 'succeeded' } }])
    await expect(pollTask('t1', null, 1)).rejects.toThrow('第四次还是被占用')
    expect(calls.length).toBe(4)                          // 1 次 + 3 次重试，第 5 个回复没被请求
  })

  it('404（任务记录没了）/ 400 是真的错：不重试，立刻抛', async () => {
    let calls = mockFetch([{ ok: false, status: 404, body: { detail: '没有这个任务：t9' } }, { body: { status: 'succeeded' } }])
    await expect(pollTask('t9', null, 1)).rejects.toThrow('没有这个任务：t9')
    expect(calls.length).toBe(1)
    calls = mockFetch([{ ok: false, status: 400, body: { detail: '坏请求' } }, { body: { status: 'succeeded' } }])
    await expect(pollJob('j9', null, null, 1)).rejects.toThrow('坏请求')
    expect(calls.length).toBe(1)
  })

  it('pollJob 同样重试；stop 令牌照旧能停', async () => {
    const calls = mockFetch([busy(), { body: { status: 'running' } }, 'offline', { body: { status: 'succeeded', job_id: 'j1' } }])
    const job = await pollJob('j1', null, { stopped: false }, 1)
    expect(job).toEqual({ status: 'succeeded', job_id: 'j1' })
    expect(calls.every(u => u === '/api/scripts/jobs/j1')).toBe(true)
    mockFetch([{ body: { status: 'running' } }])
    const stop = { stopped: false }
    await expect(pollJob('j2', () => { stop.stopped = true }, stop, 1)).resolves.toEqual({ status: 'watch_stopped', job_id: 'j2' })
  })

  it('哪些算瞬时：连不上 / 408 / 429 / 5xx；其余不算', async () => {
    expect([0, 408, 429, 500, 502, 503, 504].every(status => isTransientError({ status }))).toBe(true)
    expect([400, 401, 403, 404, 409, 422, undefined].some(status => isTransientError({ status }))).toBe(false)
    expect(isTransientError(null)).toBe(false)
    mockFetch([busy(), { body: { ok: 1 } }])
    await expect(getRetrying('/api/x', 1)).resolves.toEqual({ ok: 1 })
  })
})
