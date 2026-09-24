import { lang } from './i18n.js'

// 所有请求走这里：同源、带 cookie（本机访问令牌）、带界面语言（后端据此翻译文案），错误统一取 detail 文案
export async function api(path, opts = {}) {
  const init = { credentials: 'same-origin', ...opts }
  init.headers = { 'X-StoryStudio-Lang': lang.value, ...(init.headers || {}) }
  if (init.body && typeof init.body !== 'string') {
    init.body = JSON.stringify(init.body)
    init.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) }
  }
  let r
  try {
    r = await fetch(path, init)
  } catch (e) {
    // 网络层失败（服务没起 / 正在重启）：浏览器只给 "Failed to fetch"，翻成人话
    const err = new Error(lang.value === 'en'
      ? 'Cannot reach the local server (it may be restarting) - wait a few seconds and try again'
      : '连不上本地服务（可能正在重启）——等几秒再试')
    err.status = 0
    err.cause = e
    throw err
  }
  const body = await r.json().catch(() => null)
  if (!r.ok) {
    const detail = body && body.detail
    const err = new Error(typeof detail === 'string' ? detail : (detail ? JSON.stringify(detail) : r.statusText))
    // 结构化错误（409 重投纪律、能力检查等）：调用方按 status / detail.kind 分流，不用解析 message
    err.status = r.status
    err.detail = detail
    throw err
  }
  return body
}

export const get = (p) => api(p)
export const post = (p, body) => api(p, { method: 'POST', body: body ?? {} })
export const put = (p, body) => api(p, { method: 'PUT', body })
export const del = (p) => api(p, { method: 'DELETE' })

export const ep = (id) => `/api/episodes/storyvideo/${encodeURIComponent(id)}`

// 轮询遇到的瞬时错误：连不上（服务在重启 / 网络抖一下，status 0）、5xx（后端读任务记录那一下文件正被占用）、
// 408 / 429。这些先别报错——任务还在后台跑，一次抖动就让进度条消失、弹「生成过程中出错」只会让人以为白干了。
// 404（任务记录没了）、400 这类是真的错，立刻抛
export const POLL_RETRIES = 3
export function isTransientError(e) {
  const status = e?.status
  return status === 0 || status === 408 || status === 429 || (typeof status === 'number' && status >= 500)
}

// 轮询里的一次 GET：瞬时错误隔 intervalMs 重试，连续 POLL_RETRIES 次重试都失败才把最后那个错误抛出去
export async function getRetrying(path, intervalMs = 1000, retries = POLL_RETRIES) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await get(path)
    } catch (e) {
      if (attempt >= retries || !isTransientError(e)) throw e
      await sleep(intervalMs)
    }
  }
}

// 轮询一个本地任务（台词识别 / 后期合成）直到结束
export async function pollTask(taskId, onTick, intervalMs = 2500) {
  const started = Date.now()
  for (;;) {
    const task = await getRetrying(`/api/tasks/${taskId}`, intervalMs)
    onTick?.(task, Math.round((Date.now() - started) / 1000))
    if (task.status === 'succeeded' || task.status === 'failed') return task
    await sleep(intervalMs)
  }
}

// 轮询起草 job
export async function pollJob(jobId, onTick, stop, intervalMs = 4000) {
  const started = Date.now()
  for (;;) {
    if (stop?.stopped) return { status: 'watch_stopped', job_id: jobId }
    const job = await getRetrying(`/api/scripts/jobs/${jobId}`, intervalMs)
    onTick?.(job, Math.round((Date.now() - started) / 1000))
    if (job.status === 'succeeded' || job.status === 'failed') return job
    await sleep(intervalMs)
  }
}

export const sleep = (ms) => new Promise(r => setTimeout(r, ms))

export function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}
