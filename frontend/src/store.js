import { reactive } from 'vue'
import { get, getRetrying, post, ep, sleep, isTransientError, POLL_RETRIES } from './api.js'
import { custom, notice, toast } from './ui.js'
import { t, isEn } from './i18n.js'

// 一个全局响应式状态。项目相关的东西（detail / summary / cast / postLayers）在进入
// 项目页时一起拉，之后每个动作只刷新自己碰到的那几份。
export const store = reactive({
  health: null,
  product: null,       // 产品名 {name, name_en, short}，来自 /api/health 或 /api/modules（DESIGN §38）
  modules: [],         // 模块注册表（/api/modules），启动时拉一次
  modulesLoaded: false,
  episodes: [],
  episodesLoaded: false,
  // 绘本模块（docs/picturebook-spec.md）：列表 + 当前打开的书；加载函数在 picturebook.js
  books: [],
  booksLoaded: false,
  bookId: null,
  book: null,          // 项目文件本身（detail.spec + id）
  bookDetail: null,    // 后端 detail 外壳：pages 行（image_url / audio）、timeline、final_url、busy…
  bookSummary: null,
  bookLint: null,
  // 图片集模块（docs/imageset-spec.md、docs/imagestudio-spec.md）：表情与头像 sticker / 纪念写真 portrait /
  // 图片工作台 image 共用一套页面；加载函数在 imageset.js
  sets: { sticker: [], portrait: [], image: [] },
  setsLoaded: { sticker: false, portrait: false, image: false },
  setModule: null,
  setId: null,
  set: null,           // 项目文件本身（detail.spec + id）
  setDetail: null,     // 后端 detail 外壳：items 行（status / current_url / attempts / outputs）、exports…
  // 配乐模块（docs/music-spec.md）：列表 + 当前打开的配乐；加载函数在 music.js
  musics: [],
  musicsLoaded: false,
  musicId: null,
  music: null,         // 项目文件本身（detail.spec + id）
  musicDetail: null,   // 后端 detail 外壳：prompt / attempts 行（url / current）、used_in、progress、busy…
  // AI 播客模块（docs/podcast-spec.md）：列表 + 当前打开的播客；加载函数在 podcast.js
  podcasts: [],
  podcastsLoaded: false,
  podcastId: null,
  podcast: null,       // 项目文件本身（detail.spec + id）
  podcastDetail: null, // 后端 detail 外壳：estimate / problems / hosts（voice_label）/ audio（lines、mix_url、chapters、stale）/ texts / busy…
  // 剧本工作台模块（docs/scriptlab-spec.md）：列表 + 当前打开的剧本；加载函数在 scriptlab.js
  scripts: [],
  scriptsLoaded: false,
  scriptId: null,
  script: null,        // 项目文件本身（detail.spec + id）：brief / speakers / blocks / versions / current / used_in
  scriptDetail: null,  // 后端 detail 外壳：current_version / estimate / warnings / problems / versions 摘要 / review / busy…
  // 单条视频模块（docs/clip-spec.md）：列表 + 当前打开的片子；加载函数在 clip.js
  clips: [],
  clipsLoaded: false,
  clipId: null,
  clip: null,          // 项目文件本身（detail.spec + id）
  clipDetail: null,    // 后端 detail 外壳：prompt / sections / beats 行 / warnings / status / attempts / first_frame…
  projectId: null,
  detail: null,
  summary: null,
  cast: null,
  postLayers: null,
  cameraOptions: {},   // profile → options
  styleCards: null,    // 出图风格卡库（GET /api/style-cards）：{categories, cards, byId}，全局只拉一次（stylecards.js）
  settings: null,
  voices: null,        // 音色库
  busy: 0,             // 顶部进度条计数
  gen: null,           // 正在进行的生成/任务：{ text }
})

export async function withBusy(fn) {
  store.busy++
  try { return await fn() } finally { store.busy = Math.max(0, store.busy - 1) }
}

export async function loadHealth() {
  try { store.health = await get('/api/health') } catch { store.health = null }
  if (store.health?.product) store.product = store.health.product
  return store.health
}

// 工作名兜底：后端没拉到之前头部也要有字（真正的名字只在 webui/branding.py 一处）
const FALLBACK_PRODUCT = 'Content Cook'

export function productName() {
  const p = store.product
  if (!p) return FALLBACK_PRODUCT
  return (isEn() && p.name_en) ? p.name_en : (p.name || FALLBACK_PRODUCT)
}

// 模块注册表（DESIGN §38）：启动拉一次；失败保持空列表，首页只显示短剧区
export async function loadModules() {
  try {
    const r = await get('/api/modules')
    store.modules = r.modules || []
    if (r.product) store.product = r.product
  } catch { store.modules = [] }
  store.modulesLoaded = true
  return store.modules
}

export function moduleById(id) {
  return store.modules.find(m => m.id === id) || null
}

export function roleOk(role) {
  return !!store.health?.roles?.[role]?.configured
}

// retry：轮询用（克隆进度）——瞬时错误先重试，重试完还不行就把错误抛给调用方，别悄悄停
export async function loadVoices({ retry = false } = {}) {
  try {
    store.voices = retry ? await getRetrying('/api/voices', 1500) : await get('/api/voices')
  } catch (e) {
    // 读失败时保留上一份列表：清空的话，一次网络抖动就让音色库看起来全没了、绑定下拉框也空了
    store.voices = store.voices || []
    if (retry) throw e
  }
  return store.voices
}

export async function loadEpisodes() {
  store.episodes = await get('/api/episodes')
  store.episodesLoaded = true
  return store.episodes
}

export async function loadProject(id, { keep = false } = {}) {
  if (!keep) { store.detail = null; store.summary = null; store.cast = null; store.postLayers = null }
  store.projectId = id
  const [detail, summary, cast, postLayers] = await Promise.all([
    get(ep(id)), get(`${ep(id)}/summary`), get(`${ep(id)}/cast`), get(`${ep(id)}/post-layers`),
  ])
  if (store.projectId !== id) return // 用户已经切走
  store.detail = detail; store.summary = summary; store.cast = cast; store.postLayers = postLayers
  if (detail.camera_profile && !store.cameraOptions[detail.camera_profile]) {
    try { store.cameraOptions[detail.camera_profile] = await get(`/api/camera-options?profile=${encodeURIComponent(detail.camera_profile)}`) } catch { /* 无 profile 时下面兜底 */ }
  }
  if (!store.cameraOptions['']) {
    try { store.cameraOptions[''] = await get('/api/camera-options') } catch { /* ignore */ }
  }
}

export async function refreshProject() {
  if (!store.projectId) return
  await loadProject(store.projectId, { keep: true })
  loadEpisodes().catch(() => {})
}

export function cameraOptionsFor(detail) {
  return store.cameraOptions[detail?.camera_profile || ''] || store.cameraOptions[''] || { shots: [], combos: [], profiles: [] }
}

export function shotById(id) {
  return store.detail?.shots?.find(s => s.id === id) || null
}

// ---- 生成：预估 → 确认 → 提交 → 轮询（跟旧界面同一套语义：plan_token 二次确认）
const POLL_MS = 8000
const POLL_MAX = 45
const RETRY_MS = 2000

// 盯生成时的一次读（预估 / 刷新项目）：瞬时错误（后端读记录那一下文件正被占用的 5xx、连不上、408 / 429）隔 RETRY_MS
// 重试，连续 POLL_RETRIES 次重试都失败才报错；404 这类真的错立刻抛。跟 api.js 的 getRetrying 同一个规矩——这里单写一份，
// 是因为刷新项目是四个 GET 一起发，不是一条路径。以前一次抖动就停掉盯梢、弹「生成过程中出错」，后台其实还在等服务商，
// 用户却以为这一批（花了钱的）白跑了
async function watchRead(read) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await read()
    } catch (e) {
      if (attempt >= POLL_RETRIES || !isTransientError(e)) throw e
      await sleep(RETRY_MS)
    }
  }
}

// 后台轮询（DESIGN §34）：真正去问服务商、下载、记账的是服务端的 generate-watch 任务；这里只是让它保证在跑。不花钱。
export async function resumeWatch(id = store.projectId) {
  try { return await post(`${ep(id)}/generate-resume`) } catch { return null }
}

// 盯着这一批直到「没有待生成、也没有在飞的」。2026-09-08 之前这里在 pending 为 0 时就收工——
// 而 pending 不含已提交还没回来的镜，所以提交一返回循环就结束，之后再没人看过服务商。
export async function watchGenerate(id, through, onlyQ = '', started = Date.now()) {
  const only = onlyQ ? onlyQ.replace(/^&only=/, '').split(',').filter(Boolean) : null
  let resumed = false
  try {
    for (let i = 1; i <= POLL_MAX; i++) {
      await sleep(POLL_MS)
      if (store.projectId !== id) return true
      const now = await watchRead(() => get(`${ep(id)}/generate-estimate?through=${through}${onlyQ}`))
      store.gen = { text: t('生成中，已经 {n} 秒（可以切走，回来会自动刷新）', { n: Math.round((Date.now() - started) / 1000) }) }
      const inflight = now.in_progress?.length || 0
      if (now.pending_count === 0 && !inflight) { await watchRead(refreshProject); toast(t('这一批生成完了')); return true }
      if (inflight) {
        // 服务端有后台任务在等结果；概览说没有（服务重启过、或它已经报错停了）就补发一次 generate-resume
        await watchRead(refreshProject)
        if (!resumed && store.summary?.watching === false) { resumed = true; await resumeWatch(id) }
        continue
      }
      // 明确失败的镜不会被自动重提：停下轮询，把原因摆出来（概览页有「失败任务」卡）
      if (now.failed?.length && now.failed.length >= now.pending_count) {
        await watchRead(refreshProject)
        const f = store.summary?.failures || []
        const lines = f.map((x) => t('{id}（{stage}）：{label}——{advice}', {
          id: x.shot_id, stage: t(x.stage === 'frames' ? '首帧' : '视频'), label: x.label, advice: x.advice,
        }))
        await notice(t('{n} 镜提交失败，没有自动重提：\n{lines}', { n: now.failed.length, lines: lines.join('\n') || now.failed.join('、') }))
        return false
      }
      // 还有待生成、又没有在飞的（下一波依赖上一波的首帧，或有失败要重提）：沿用旧界面的做法，按新 token 再提交一次
      // 提交本身不重试（花钱的请求，重发可能多提交一次）；之后的刷新照样按瞬时错误重试
      await post(`${ep(id)}/generate`, { through, only, plan_token: now.plan_token, wait: false })
      await watchRead(refreshProject)
    }
    toast(t('服务商还没返回，后台会继续等；回到项目页会看到最新状态'), { kind: 'warn', ttl: 8000 })
  } catch (e) {
    await notice(t('生成过程中出错：{msg}', { msg: e.message }))
  } finally {
    store.gen = null
  }
  await refreshProject()
  return true
}

export async function runGenerate(through, only = null) {
  const id = store.projectId
  const onlyQ = only?.length ? `&only=${only.join(',')}` : ''
  let est
  try {
    est = await get(`${ep(id)}/generate-estimate?through=${through}${onlyQ}`)
  } catch (e) { await notice(t('预估失败：{msg}', { msg: e.message })); return false }
  const isFinal = through === 'final' || through === 'assemble'
  if (est.pending_count === 0) {
    if (isFinal) { await refreshProject(); return true }
    if (est.in_progress?.length) {
      // 上次提交的还在服务商那边：不再只弹一句「不用再点」，而是保证后台在等，并盯着它直到回来
      await resumeWatch(id)
      toast(t('上次提交的还在生成（{list}），后台在等结果', { list: est.in_progress.join('、') }))
      store.gen = { text: t('已提交，等待服务商返回…') }
      return watchGenerate(id, through, onlyQ)
    }
    await notice(only?.length
      ? t('这一镜已经有产物，没有新东西要生成。想重新生成先点「重拍」。')
      : t('这一批已经有产物，没有新东西要生成。想重新生成先点「重拍」。'))
    return false
  }
  // localRole：花钱之前那一行「本机有 X 在跑」问的是哪个角色（DESIGN §45；没探到就什么都不显示）
  const ok = await custom({ view: 'estimate', through, only, estimate: est, localRole: through === 'frames' ? 'image' : through === 'videos' ? 'video' : '' })
  if (!ok) return false
  try {
    await post(`${ep(id)}/generate`, { through, only: only?.length ? only : null, plan_token: est.plan_token, wait: false })
  } catch (e) { await notice(e.message); return false }
  if (isFinal) { await refreshProject(); toast(t('成片已合成')); return true }
  store.gen = { text: t('已提交，等待服务商返回…') }
  return watchGenerate(id, through, onlyQ)
}

// ---- 制作中也能改：预览影响 → 用户确认新版本 id → 复制成新版本（带走可复用产物）→ 返回新 id
export async function reviseEdit(mutation) {
  const id = store.projectId
  let prev
  try { prev = await post(`${ep(id)}/revise-edit/preview`, { mutation }) }
  catch (e) { await notice(e.message); return null }
  const newId = await custom({ view: 'revise', preview: prev })
  if (!newId) return null
  try {
    const r = await post(`${ep(id)}/revise-edit/apply`, { mutation, new_episode_id: newId })
    await loadEpisodes()
    toast(t('已保存为新版本 {id}，只有受影响的镜需要重新生成', { id: r.id }))
    return r
  } catch (e) { await notice(e.message); return null }
}

// ---- 失败任务：重置回待提交（不花钱），再由用户点「生成」重新提交
export async function retryFailed(only = null, { force = false } = {}) {
  const id = store.projectId
  try {
    const r = await post(`${ep(id)}/retry-failed`, { only, force })
    await refreshProject()
    if (r.advice?.length) await notice(r.advice.join('\n'))
    if (r.reset?.length) toast(t('已重置 {n} 个任务，点「生成」会重新提交', { n: r.reset.length }))
    return r
  } catch (e) { await notice(e.message); return null }
}

// ---- 开始制作前的模型能力检查
export async function capabilityCheck() {
  try { return await get(`${ep(store.projectId)}/capability-check`) } catch { return null }
}

// ---- 重拍 = 记重拍裁决 + 重新提交这一镜
// meta：{ defect, disposition, force }（DESIGN §24）。disposition=edit 只记重拍、不马上重出（等改完文字）；
// 同一份提示词已出过 2 次且原样重提 → 后端 409（e.status / e.detail.kind === 'retry_limit'），调用方决定要不要 force
export async function retake(gate, shotId, reason, meta = {}) {
  const id = store.projectId
  const { defect = null, disposition = null, force = false, generate = disposition !== 'edit' } = meta
  await post(`${ep(id)}/review`, { gate, decision: 'retake', shots: [shotId], reason, reviewer: 'webui', defect, disposition, force })
  await refreshProject()
  if (!generate) return true
  return runGenerate(gate, [shotId])
}

export async function review(gate, decision, shots, reason = '', meta = {}) {
  const { defect = null, disposition = null } = meta
  await post(`${ep(store.projectId)}/review`, { gate, decision, shots, reason, reviewer: 'webui', defect, disposition })
  await refreshProject()
}
