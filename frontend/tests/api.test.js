import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api, pollTask, pollJob, ep } from '../src/api.js'

function mockFetch(sequence) {
  const calls = []
  globalThis.fetch = vi.fn(async (url, init) => {
    calls.push({ url, init })
    const next = sequence.shift()
    return { ok: next.ok ?? true, statusText: next.statusText || 'OK', json: async () => next.body }
  })
  return calls
}

describe('api()', () => {
  beforeEach(() => { vi.restoreAllMocks() })
  it('对象 body 自动 JSON 化并带 Content-Type；同源带 cookie', async () => {
    const calls = mockFetch([{ body: { ok: 1 } }])
    const r = await api('/api/x', { method: 'POST', body: { a: 1 } })
    expect(r).toEqual({ ok: 1 })
    expect(calls[0].init.body).toBe('{"a":1}')
    expect(calls[0].init.headers['Content-Type']).toBe('application/json')
    expect(calls[0].init.credentials).toBe('same-origin')
  })
  it('错误从 detail 里取人话；非字符串 detail 序列化', async () => {
    mockFetch([{ ok: false, statusText: 'Bad', body: { detail: '项目已开始制作' } }])
    await expect(api('/api/x')).rejects.toThrow('项目已开始制作')
    mockFetch([{ ok: false, statusText: 'Bad', body: { detail: { message: 'm', check: {} } } }])
    await expect(api('/api/x')).rejects.toThrow('"message":"m"')
    mockFetch([{ ok: false, statusText: 'Gone', body: null }])
    await expect(api('/api/x')).rejects.toThrow('Gone')
  })
  it('ep() 对 id 做 URL 编码', () => {
    expect(ep('EP 01/x')).toBe('/api/episodes/storyvideo/EP%2001%2Fx')
  })
})

describe('polling', () => {
  it('pollTask 直到 succeeded/failed，每次 tick 回调', async () => {
    mockFetch([{ body: { status: 'running' } }, { body: { status: 'succeeded', result: 1 } }])
    const ticks = []
    const task = await pollTask('t1', (t) => ticks.push(t.status), 1)
    expect(task.status).toBe('succeeded')
    expect(ticks).toEqual(['running', 'succeeded'])
  })
  it('pollJob 可以被 stop 令牌停下（不取消服务端任务）', async () => {
    mockFetch([{ body: { status: 'running' } }, { body: { status: 'running' } }])
    const stop = { stopped: false }
    const p = pollJob('j1', () => { stop.stopped = true }, stop, 1)
    await expect(p).resolves.toEqual({ status: 'watch_stopped', job_id: 'j1' })
  })
})
