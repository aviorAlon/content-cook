<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { get, put, post, pollTask } from '../api.js'
import { store, loadHealth } from '../store.js'
import { isLocalUrl } from '../local.js'
import { forgetLocalHints } from '../localhint.js'
import { t } from '../i18n.js'

// 中文原文留在表里，渲染时再 $t()，语言切换后才能跟着变
const ROLE = {
  text: { title: '剧本文本模型', desc: '「新建项目 · AI 起草」和重写单镜用它。' },
  image: { title: '生图模型（首帧 / 定妆）', desc: '每一镜的首帧和角色定妆图。角色锚图、参考镜画面随请求发送。' },
  voice: { title: '语音模型（旁白）', desc: '后期层的旁白轨。Edge TTS 免费；画内台词由视频模型原生生成，不走这里。' },
  video: { title: '视频模型', desc: '按首帧出每一镜的视频（含原生对白/环境音，取决于模型）。' },
  music: { title: '配乐模型', desc: '「配乐」模块按意图出成段音乐，产物投放到短剧的配乐层或绘本的 BGM。统一网关不覆盖这一档，单独配。' },
}
const FIELD = { base_url: '接口地址（base_url）', api_key: '密钥（api_key）', model: '模型 id', voice: '音色（voice）' }
// 页头说明带内联标签，放脚本里免得模板属性里转义引号；渲染时 $t(INTRO_HTML) 走 v-html（纯常量，无用户输入）
const INTRO_HTML = '密钥只存在本机，不进任何仓库，也不会上传到本产品之外的地方。四类模型各选一个来源；只有一个 <b>OpenRouter</b> key 的话：文本选「OpenAI 兼容对话」并把接口地址填 <code class="mono">https://openrouter.ai/api/v1</code>，生图/视频/语音都选 OpenRouter 那一项。'
const doc = ref(null)
const form = reactive({})     // role → { adapter, fields: {…} }
const result = reactive({})
const err = ref('')

// ---- 可选组件：台词识别闸（faster-whisper）安装与模型预下载
const asr = ref(null)
const asrStatus = ref('')
const asrModel = ref('small')
async function loadAsr() { try { asr.value = await get('/api/system/asr'); asrModel.value = asr.value.default_model } catch { asr.value = null } }
async function installAsr() {
  asrStatus.value = t('安装中…（几分钟，取决于网络）')
  try {
    const r = await post('/api/system/asr/install')
    if (!r.task_id) { asrStatus.value = r.note; return }
    const task = await pollTask(r.task_id, (_t, s) => { asrStatus.value = t('安装中…已经 {s} 秒', { s }) })
    asrStatus.value = task.status === 'succeeded' ? t('安装完成，重启程序后生效') : t('安装失败：{err}', { err: task.error })
    await loadAsr(); loadHealth()
  } catch (e) { asrStatus.value = e.message }
}
async function downloadModel() {
  asrStatus.value = t('下载 {model} 模型…', { model: asrModel.value })
  try {
    const r = await post('/api/system/asr/model', { model: asrModel.value })
    if (!r.task_id) { asrStatus.value = r.note; return }
    const task = await pollTask(r.task_id, (_t, s) => { asrStatus.value = t('下载 {model} 模型…已经 {s} 秒（small 约 500MB）', { model: asrModel.value, s }) })
    asrStatus.value = task.status === 'succeeded' ? t('{model} 已缓存到本机', { model: asrModel.value }) : t('下载失败：{err}', { err: task.error })
    await loadAsr()
  } catch (e) { asrStatus.value = e.message }
}

// ---- 模型目录（OpenRouter 公开列表，用来给模型 id 补全和能力检查）
const catalog = ref(null)
const catalogStatus = ref('')
async function loadCatalog() {
  try { catalog.value = await get('/api/system/model-catalog') } catch { catalog.value = null }
  try { gwCatalog.value = await get('/api/system/model-catalog?source=gateway') } catch { gwCatalog.value = null }
}

// ---- 本地模型（DESIGN §45）：扫本机常见端口 → 一键应用到角色。本机来源不要 key、不花钱、不出网。
const localScan = ref(null)          // { services, alive, presets, roles }
const localStatus = ref('')
const localErr = ref(false)
const localPick = reactive({})       // 服务 id → 选中的模型
const localAddr = ref('')            // 手填地址
const localAddrFor = ref('comfyui')  // 手填地址扫哪个服务
// 手填框的示例按选中的预设给：只填主机和端口（路径 /v1 之类由后端按预设接上，用户不用记）
const localPlaceholder = computed(() => {
  const preset = (localScan.value?.presets || []).find((p) => p.id === localAddrFor.value)
  const m = /^(https?:\/\/[^/]+)/.exec(preset?.base_url || '')
  return m ? m[1] : 'http://127.0.0.1:8188'
})
const workflows = ref(null)          // { workflows, user_dir, note }
const workflowPick = ref('')
const workflowStatus = ref('')
// 推荐（不是要求）：后端按角色点名一个服务（生图优先 SD WebUI，视频只点 ComfyUI），这里把它排到前面并标「推荐」
const recommended = computed(() => new Set(Object.values(localScan.value?.recommend || {})))
const localRows = computed(() => {
  const rows = [...(localScan.value?.services || [])]
  const score = (s) => (s.alive ? 2 : 0) + (recommended.value.has(s.id) ? 1 : 0)
  return rows.sort((a, b) => score(b) - score(a))
})
function recommendFor(s) {
  const map = localScan.value?.recommend || {}
  return Object.keys(map).filter((role) => map[role] === s.id)
}
// 「不再提示」的复位入口（关掉的提示不会自己回来，只有这里能重新打开）
const hintsOff = computed(() => (doc.value?.settings?.local_hints_dismissed || []).length > 0)
// 「设置文件损坏」的红字：任何一次写盘成功，后端都已把坏文件备份并重建，提示得跟着消失——
// 不然用户照提示保存了一次，看到的还是「损坏」，会以为没修好
function settingsWritten() { if (doc.value) doc.value.warning = '' }
async function resetHints() {
  setLocalStatus(t('开启中…'))
  try {
    const r = await post('/api/system/local-hint/reset')
    settingsWritten()
    if (doc.value?.settings) doc.value.settings.local_hints_dismissed = r.dismissed || []
    forgetLocalHints()
    setLocalStatus(t('本地提示已重新开启'))
  } catch (e) { setLocalStatus(e.message, true) }
}
function setLocalStatus(msg, bad = false) { localStatus.value = msg; localErr.value = bad }
function roleTitle(role) { return ROLE[role] ? t(ROLE[role].title) : role }
// 插件（genkit.plugins）：健康检查里每个插件一行；来源表里插件登记的那几条带 plugin 字段
const pluginRows = computed(() => store.health?.plugins || doc.value?.plugins || [])
function pluginStatus(p) { return p.status === 'loaded' ? t('已加载') : p.status === 'skipped' ? t('跳过') : t('加载失败') }
// 存着的来源现在不在表里（提供它的插件没装上）：下拉框里显示成选中的「X（未加载）」，保存时原样发回去，不会被清掉
function unavailableOf(role) { return doc.value?.unavailable?.[role] || null }
function noSources(role) { return !Object.keys(doc.value?.adapters?.[role] || {}).length }
// 角色卡的 key 输入框：地址（或适配器默认地址）是本机时提示「本机服务通常不需要」，并允许留空
function roleLocal(role) {
  const filled = (form[role]?.fields?.base_url || '').trim()
  return isLocalUrl(filled || (adapterOf(role)?.defaults || {}).base_url || '')
}
async function scanLocal(ids = '', addr = '') {
  setLocalStatus(t('扫描中…'))
  const query = [ids ? `ids=${encodeURIComponent(ids)}` : '', addr ? `base_url=${encodeURIComponent(addr)}` : ''].filter(Boolean).join('&')
  try {
    const r = await get('/api/system/local-scan' + (query ? `?${query}` : ''))
    // 手填地址只扫了一个服务：把它并进上一次的结果，别把整张表冲掉
    localScan.value = ids && localScan.value
      ? { ...r, services: localScan.value.services.map((s) => r.services.find((x) => x.id === s.id) || s) }
      : r
    localScan.value.alive = localScan.value.services.filter((s) => s.alive).map((s) => s.id)
    for (const s of localScan.value.services) if (!localPick[s.id] && s.models.length) localPick[s.id] = s.models[0]
    setLocalStatus(localScan.value.alive.length ? t('探到 {n} 个在跑的服务', { n: localScan.value.alive.length }) : t('没探到在跑的服务'))
    if (localScan.value.alive.includes('comfyui')) loadWorkflows()
  } catch (e) { setLocalStatus(e.message, true) }
}
async function applyLocal(svc, role) {
  setLocalStatus(t('应用中…'))
  try {
    const r = await post('/api/settings/local/apply', { service: svc.id, role, model: localPick[svc.id] || '', base_url: svc.base_url })
    settingsWritten()
    doc.value.settings = r.settings; doc.value.status = r.status
    refillRoles(r.changed || [role])
    setLocalStatus(t('已应用到：{roles}', { roles: roleTitle(role) }))
    forgetLocalHints()      // 这个角色已经是本机了：别处那一行提示该自己消失
    loadHealth()
  } catch (e) { setLocalStatus(e.message, true) }
}
async function loadWorkflows() {
  try {
    workflows.value = await get('/api/system/local-workflows')
    if (!workflowPick.value && workflows.value.workflows.length) workflowPick.value = workflows.value.workflows[0].name
  } catch { workflows.value = null }
}
async function checkWorkflow() {
  if (!workflowPick.value) { workflowStatus.value = t('先选一份工作流'); return }
  workflowStatus.value = t('检查中…')
  try {
    const r = await post('/api/system/local-workflows/check', { name: workflowPick.value })
    workflowStatus.value = r.ok ? t('✓ 映射对得上（{keys}）', { keys: (r.keys || []).join(' / ') })
      : t('✗ 这些指不到：{missing}', { missing: (r.missing || []).map((m) => `${m.key}（${m.reason}）`).join('；') || r.error })
  } catch (e) { workflowStatus.value = '✗ ' + e.message }
}

// ---- 统一网关（LiteLLM / new-api / one-api 这类 OpenAI 兼容网关）：填一次，一键应用到四个角色
const gw = reactive({ base_url: '', api_key: '' })
const gwStatus = ref('')
const gwErr = ref(false)
const gwCatalog = ref(null)
const gwPick = reactive({ text: { on: true, model: '' }, image: { on: true, model: '' }, voice: { on: true, model: '', voice: '' }, video: { on: false, model: '' } })
function setGwStatus(msg, err = false) { gwStatus.value = msg; gwErr.value = err }
function gwModels(role) { return (gwCatalog.value?.roles?.[role]) || [] }
async function saveGateway() {
  setGwStatus(t('保存中…'))
  const hadKey = !!doc.value?.settings?.gateway?.api_key
  try {
    const saved = await put('/api/settings', { gateway: { base_url: gw.base_url.trim(), api_key: gw.api_key } })
    settingsWritten()
    doc.value.settings = saved.settings
    const g = saved.settings?.gateway
    if (!g) { setGwStatus(t('后端还是旧版本，不认识网关设置——重启一次服务再试'), true); return }
    gw.api_key = g.api_key; gw.base_url = g.base_url
    // 网关换了地址：旧网关密钥不跟过去，后端已清掉
    if (hadKey && !g.api_key) { setGwStatus(t('换了地址，旧密钥不会发到新地址——请重新填写密钥'), true); return }
    setGwStatus(t('已保存'))
  } catch (e) { setGwStatus(e.message, true) }
}
async function testGateway() {
  await saveGateway()
  if (gwErr.value) return
  setGwStatus(t('测试中…'))
  try {
    const r = await post('/api/settings/gateway/test')
    setGwStatus((r.ok ? '✓ ' : '✗ ') + r.message, !r.ok)
    if (r.ok) { gwCatalog.value = await post('/api/system/model-catalog/refresh', { source: 'gateway' }) }
  } catch (e) { setGwStatus('✗ ' + e.message, true) }
}
async function applyGateway() {
  const roles = {}
  for (const role of ['text', 'image', 'voice', 'video']) {
    if (!gwPick[role].on) continue
    roles[role] = { model: gwPick[role].model.trim() }
    if (role === 'voice') roles[role].voice = gwPick[role].voice.trim()
  }
  if (!Object.keys(roles).length) { setGwStatus(t('至少勾一个角色'), true); return }
  await saveGateway()
  if (gwErr.value) return
  try {
    const r = await post('/api/settings/gateway/apply', { roles })
    settingsWritten()
    doc.value.settings = r.settings; doc.value.status = r.status
    refillRoles(r.changed || [])
    setGwStatus(t('已应用到：{roles}', { roles: r.changed.map((x) => t(ROLE[x].title)).join(t('、')) }))
    loadHealth()
  } catch (e) { setGwStatus(e.message, true) }
}
async function refreshCatalog() {
  catalogStatus.value = t('拉取中…')
  try { catalog.value = await post('/api/system/model-catalog/refresh', { source: 'openrouter' }); catalogStatus.value = t('已更新，{n} 个模型', { n: catalog.value.count }) }
  catch (e) { catalogStatus.value = e.message }
}
function catalogFor(role) {
  const adapter = form[role]?.adapter || ''
  const base = (form[role]?.fields?.base_url || '').replace(/\/+$/, '')
  const viaOpenRouter = adapter.startsWith('openrouter') || (adapter === 'openai_chat' && /openrouter\.ai/.test(base))
  if (viaOpenRouter) return catalog.value?.count ? (catalog.value.roles[role] || []) : []
  const gwBase = (doc.value?.settings?.gateway?.base_url || '').replace(/\/+$/, '')
  if (adapter.startsWith('openai_') && gwBase && base === gwBase) return gwModels(role)
  return []
}
function fetchedText(ts) { return ts ? new Date(ts * 1000).toLocaleString() : t('还没拉取过') }
function catalogSummary() {
  const c = catalog.value
  if (!c?.count) return t('还没拉取过')
  return t('{n} 个模型（生图 {image} · 视频 {video} · 语音 {voice} · 文本 {text}），{time}', {
    n: c.count, image: c.roles.image.length, video: c.roles.video.length, voice: c.roles.voice.length, text: c.roles.text.length, time: fetchedText(c.fetched_at),
  })
}
// 输入框里的模型 id 在目录里有参数区间时，给一行人话摘要（来自服务商的专用接口，不是我们的静态表）
function capsText(entry) {
  if (!entry) return ''
  const c = entry.caps || {}
  const parts = []
  if (c.durations?.choices) parts.push(t('时长 {v} 秒', { v: c.durations.choices.join('/') }))
  else if (c.durations) parts.push(t('时长 {v} 秒', { v: `${c.durations.min}–${c.durations.max}` }))
  if (c.resolutions) parts.push(c.resolutions.join('/'))
  if (c.aspect_ratios) parts.push(t('画幅 {v}', { v: c.aspect_ratios.join(' ') }))
  if ('first_frame' in c) parts.push(c.first_frame ? t('接受首帧') : t('不接受首帧（只能文生视频）'))
  if ('audio' in c) parts.push(c.audio ? t('出原生音频') : t('不出原生音频'))
  if ('max_reference_images' in c) parts.push(c.max_reference_images ? t('参考图最多 {n} 张', { n: c.max_reference_images }) : t('不接受参考图'))
  else if ('reference_images' in c) parts.push(c.reference_images ? t('接受参考图') : t('不接受参考图'))
  return parts.join(' · ')
}
function catalogEntry(role) {
  const id = (form[role]?.fields?.model || '').trim().toLowerCase()
  return id ? catalogFor(role).find((m) => m.id.toLowerCase() === id) : null
}
// 填了 id、目录里却没有：提示要标黄
function catalogMissing(role) {
  return !!(catalogFor(role).length && (form[role]?.fields?.model || '').trim() && !catalogEntry(role))
}
function modelHint(role) {
  if (!catalogFor(role).length) return ''
  const id = (form[role]?.fields?.model || '').trim()
  if (!id) return ''
  const entry = catalogEntry(role)
  if (!entry) return t('目录里没有 {id}——id 写错或已下线', { id })
  return capsText(entry) || t('{name}（目录里没给参数区间）', { name: entry.name })
}

// ---- 版本更新检查
const updateUrl = ref('')
const updateInfo = ref(null)
const updateStatus = ref('')
async function saveUpdateUrl() {
  try { await put('/api/settings', { update_url: updateUrl.value.trim() }); settingsWritten(); updateStatus.value = t('已保存') } catch (e) { updateStatus.value = e.message }
}
async function checkUpdate() {
  updateStatus.value = t('检查中…')
  try { updateInfo.value = await get('/api/system/update-check'); updateStatus.value = '' } catch (e) { updateStatus.value = e.message }
}

// 把已保存的配置灌进角色卡。「用它配 …」/「应用到勾选的角色」成功后只灌响应里 changed 的那几张：后端按设计把全部角色的
// 已保存值都带回来，拿整表去灌会把别的卡里敲了还没点「保存」的 model / key 静默盖掉（单卡「保存」也只动自己那张）
function refillRoles(names) {
  for (const role of names) {
    const cfg = doc.value.settings.roles[role] || {}
    form[role] = { adapter: cfg.adapter || '', fields: { ...cfg } }
  }
}
onMounted(async () => {
  try { doc.value = await get('/api/settings') } catch (e) { err.value = e.message; return }
  refillRoles(doc.value.roles)
  updateUrl.value = doc.value.settings.update_url || ''
  Object.assign(gw, doc.value.settings.gateway || { base_url: '', api_key: '' })
  for (const role of doc.value.roles) {
    const cfg = doc.value.settings.roles[role] || {}
    if (cfg.model) gwPick[role].model = cfg.model
    if (role === 'voice' && cfg.voice) gwPick.voice.voice = cfg.voice
  }
  loadAsr()
  loadCatalog()
})
function adapterOf(role) { return (doc.value.adapters[role] || {})[form[role].adapter] }
async function save(role) {
  result[role] = t('保存中…')
  const values = { adapter: form[role].adapter }
  for (const f of adapterOf(role)?.fields || []) values[f] = form[role].fields[f] || ''
  const before = doc.value.settings.roles[role] || {}
  try {
    const saved = await put('/api/settings', { roles: { [role]: values } })
    settingsWritten()
    doc.value.settings = saved.settings; doc.value.status = saved.status
    if (saved.unavailable) doc.value.unavailable = saved.unavailable
    form[role].fields = { ...doc.value.settings.roles[role] }
    // 同一个来源只改了地址（换了主机 / 端口）：后端不会把旧密钥发到新地址，已经清掉了——说清楚，免得以为密钥还在
    const after = doc.value.settings.roles[role] || {}
    const keyDropped = before.api_key && !after.api_key && before.adapter === after.adapter && values.api_key !== undefined
    result[role] = keyDropped ? t('换了地址，旧密钥不会发到新地址——请重新填写密钥') : t('已保存')
    loadHealth()
  } catch (e) { result[role] = t('保存失败：{err}', { err: e.message }) }
}
// 清掉这个角色存着的密钥：表单回传的是打码值、空串又等于「保留」，没有这个按钮用户就没法把 key 删掉
async function clearKey(role) {
  result[role] = t('清除中…')
  try {
    const saved = await put('/api/settings', { roles: { [role]: { api_key: null } } })
    settingsWritten()
    doc.value.settings = saved.settings; doc.value.status = saved.status
    // 只动密钥这一格：同一张卡里还没保存的别的改动（模型名之类）不能被清掉
    form[role].fields.api_key = doc.value.settings.roles[role]?.api_key || ''
    result[role] = t('密钥已清除')
    loadHealth()
  } catch (e) { result[role] = t('保存失败：{err}', { err: e.message }) }
}
// 表单里没有密钥栏的来源（本机 SD WebUI 等）设置里却存着密钥：可能是自己给反向代理配的（要留着，后端同来源保存不动它），
// 也可能是换来源时从别的服务商漏过来的旧 key——输入框看不见，得单独摆出来、能清掉。
// 只看已保存的来源：下拉框刚换成别的、还没保存时不提示（保存换来源时后端本来就会清）
function hiddenStoredKey(role) {
  const saved = doc.value.settings.roles[role] || {}
  if (!saved.api_key || saved.adapter !== form[role].adapter) return ''
  return (adapterOf(role)?.fields || []).includes('api_key') ? '' : saved.api_key
}
async function test(role) {
  result[role] = t('测试中…（测的是已保存的配置，改了先保存）')
  const t0 = performance.now()
  try {
    const r = await post(`/api/settings/test/${role}`)
    const ms = Math.round(performance.now() - t0)
    result[role] = t('{mark} {msg}（{time}）', { mark: r.ok ? '✓' : '✗', msg: r.message, time: ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : ms + 'ms' })
  } catch (e) { result[role] = '✗ ' + e.message }
}
</script>

<template>
  <div class="page narrow">
    <h2 style="font-size:20px;margin-bottom:6px">{{ $t('模型设置') }}</h2>
    <p class="hint" style="margin-bottom:12px" v-html="$t(INTRO_HTML)"></p>
    <div v-if="store.health" class="card small" style="margin-bottom:14px;display:grid;grid-template-columns:auto 1fr;gap:3px 12px;color:var(--dim)">
      <span>{{ $t('版本') }}</span><code class="mono">v{{ store.health.version }}{{ store.health.frozen ? $t('（桌面版）') : $t('（开发态）') }}</code>
      <span>{{ $t('设置文件') }}</span><code class="mono">{{ store.health.settings_path }}</code>
      <span>{{ $t('项目目录') }}</span><code class="mono">{{ store.health.project_root }}</code>
      <span>ffmpeg</span><code class="mono">{{ store.health.ffmpeg_ok ? store.health.ffmpeg : $t('未找到（合成成片、后期、台词识别需要它）') }}</code>
      <span>{{ $t('台词识别') }}</span><code class="mono">{{ store.health.asr_available ? $t('faster-whisper 可用') : $t('未安装 faster-whisper（可选）') }}</code>
      <span>{{ $t('插件') }}</span>
      <code class="mono" data-test="health-plugins">
        <template v-if="pluginRows.length">
          <span v-for="p in pluginRows" :key="`${p.name}:${p.path}`" style="display:block">{{ p.name }}{{ p.version ? ' ' + p.version : '' }} · {{ pluginStatus(p) }}<template v-if="p.status !== 'loaded' && p.message">：{{ p.message }}</template></span>
        </template>
        <template v-else>{{ $t('没有装插件') }}</template>
      </code>
    </div>
    <div v-if="err" class="note bad">{{ err }}</div>
    <!-- 设置文件坏了：后端退回空配置照常给表单，这里把原因摆出来；保存一次就会备份坏文件并重建 -->
    <div v-if="doc?.warning" class="note bad" data-test="settings-warning">{{ doc.warning }}</div>
    <template v-if="doc">
      <div class="card" data-test="local-card">
        <h3>{{ $t('本地模型') }}</h3>
        <p class="hint">{{ $t('有显卡的话可以全程本地跑，不花钱、不出网——这是一条可选的路，云端来源什么都不改也照常能用。「扫描本机」看看有没有在跑的服务；本机服务通常不要密钥，key 留空就行。') }}</p>
        <div class="row" style="margin-top:8px">
          <button class="btn sm" data-test="local-scan" @click="scanLocal()">{{ $t('扫描本机') }}</button>
          <button v-if="hintsOff" class="btn sm" data-test="local-hints-reset" @click="resetHints">{{ $t('重新开启本地提示') }}</button>
          <span class="status" :class="{ bad: localErr }">{{ localStatus }}</span>
        </div>
        <table v-if="localScan" class="small" data-test="local-table" style="width:100%;margin-top:8px;border-collapse:collapse">
          <thead><tr style="text-align:left;color:var(--dim)">
            <th>{{ $t('服务') }}</th><th>{{ $t('地址') }}</th><th>{{ $t('状态') }}</th><th>{{ $t('模型') }}</th><th>{{ $t('应用到角色') }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="s in localRows" :key="s.id" :data-test="`local-row-${s.id}`">
              <td>{{ s.name }}
                <div v-if="s.alive && recommendFor(s).length" class="hint" :data-test="`local-reason-${s.id}`">
                  <span class="pill ok">{{ $t('推荐') }}</span> {{ $t(s.reason || '') }}
                </div>
              </td>
              <td><code class="mono">{{ s.base_url }}</code></td>
              <td><span class="pill" :class="s.alive ? 'ok' : 'warn'">{{ s.alive ? $t('在跑') : $t('没响应') }}</span></td>
              <td>
                <select v-if="s.models.length" v-model="localPick[s.id]" :data-test="`local-model-${s.id}`">
                  <option v-for="m in s.models" :key="m" :value="m">{{ m }}</option>
                </select>
                <span v-else class="hint">{{ s.alive ? $t('列不出模型') : '—' }}</span>
              </td>
              <td>
                <button v-for="r in s.roles" :key="r" class="btn sm" style="margin-right:4px" :disabled="!s.alive"
                        :data-test="`local-apply-${s.id}-${r}`" @click="applyLocal(s, r)">{{ $t('用它配 {role}', { role: roleTitle(r) }) }}</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="localScan && !localScan.alive.length" class="hint" data-test="local-none">{{ $t('常见端口都没有响应——服务没起，或者换了端口。可以在下面手填地址再扫一次。') }}</p>
        <div class="row" style="margin-top:8px">
          <select v-model="localAddrFor" data-test="local-addr-for">
            <option v-for="p in (localScan?.presets || [])" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
          <input v-model="localAddr" :placeholder="localPlaceholder" autocomplete="off" data-test="local-addr"
                 style="flex:1;min-width:200px;padding:5px 8px;border:1px solid var(--border);border-radius:var(--r-sm);background:var(--panel2)" />
          <button class="btn sm" data-test="local-scan-manual" @click="scanLocal(localAddrFor, localAddr)">{{ $t('用这个地址扫') }}</button>
          <span class="hint">{{ $t('只接受本机地址（localhost / 127.0.0.1 / 私有网段）。只填主机和端口即可，路径（如 /v1）按预设自动接上。') }}</span>
        </div>
        <div class="row" style="margin-top:8px" data-test="local-workflow-row">
          <span class="hint" style="margin:0">{{ $t('ComfyUI 工作流') }}</span>
          <select v-model="workflowPick" data-test="local-workflow">
            <option v-for="w in (workflows?.workflows || [])" :key="w.name" :value="w.name">{{ w.title || w.name }}{{ w.ok ? '' : $t('（映射有问题）') }}</option>
          </select>
          <button class="btn sm" data-test="local-workflow-check" @click="checkWorkflow">{{ $t('检查映射') }}</button>
          <button class="btn sm" @click="loadWorkflows">{{ $t('刷新工作流') }}</button>
          <span class="status">{{ workflowStatus }}</span>
        </div>
        <p class="hint" v-if="workflows">{{ $t('自己在 ComfyUI 里「导出（API）」的工作流放进 {dir}，配一份同名 .map.json（哪个节点的哪个字段是提示词 / 负面 / 宽高 / 步数 / 种子 / 输入图 / 输出）。', { dir: workflows.user_dir }) }}<template v-if="workflows.note"> {{ workflows.note }}</template></p>
      </div>
      <div class="card" data-test="gateway-card">
        <h3>{{ $t('统一网关（可选）') }}</h3>
        <p class="hint">{{ $t('如果你用 LiteLLM、new-api、one-api 这类 OpenAI 兼容网关（自己部署、各家 key 填在网关里，按官方价计费），这里填一次地址和网关的 key，一键应用到四个角色，不用逐个来源填。') }}</p>
        <div class="grid2" style="margin-top:8px">
          <div class="field"><label>{{ $t('网关地址（base_url）') }}</label><input v-model="gw.base_url" placeholder="http://127.0.0.1:4000/v1" autocomplete="off" /></div>
          <div class="field"><label>{{ $t('网关密钥（api_key）') }}</label><input v-model="gw.api_key" type="password" autocomplete="off" /></div>
        </div>
        <div class="row" style="margin-bottom:8px">
          <button class="btn sm" @click="saveGateway">{{ $t('保存') }}</button>
          <button class="btn sm" @click="testGateway">{{ $t('测试连接并拉取模型列表') }}</button>
          <span class="status" :class="{ bad: gwErr }">{{ gwStatus }}</span>
        </div>
        <details class="fold" :open="!!gwCatalog?.count">
          <summary>{{ $t('应用到角色（选模型后点「应用」，会覆盖这些角色现在的来源）') }}{{ gwCatalog?.count ? $t('，网关列出 {n} 个模型', { n: gwCatalog.count }) : '' }}</summary>
          <div class="grid2" style="margin-top:8px">
            <div v-for="role in ['text', 'image', 'voice', 'video']" :key="role" class="field">
              <label><input type="checkbox" v-model="gwPick[role].on" /> {{ $t(ROLE[role].title) }}</label>
              <input v-model="gwPick[role].model" :placeholder="$t('模型 id')" :list="gwModels(role).length ? `gw-${role}` : undefined" autocomplete="off" />
              <datalist v-if="gwModels(role).length" :id="`gw-${role}`"><option v-for="m in gwModels(role)" :key="m.id" :value="m.id"></option></datalist>
              <input v-if="role === 'voice'" v-model="gwPick.voice.voice" :placeholder="$t('音色 id，如 alloy')" autocomplete="off" style="margin-top:4px" />
            </div>
          </div>
          <div class="row" style="margin-top:6px">
            <button class="btn primary sm" @click="applyGateway">{{ $t('应用到勾选的角色') }}</button>
            <span class="hint">{{ $t('视频角色走 OpenAI Videos 形状（/v1/videos），只有网关按这个形状转发视频模型时才勾。') }}</span>
          </div>
        </details>
      </div>
      <div class="card">
        <h3>{{ $t('台词识别闸（可选）') }}</h3>
        <p class="hint">{{ $t('用本机的 faster-whisper 逐镜识别语音、跟台词比对。不装也能用其他所有功能。') }}</p>
        <div v-if="asr" class="row" style="margin-top:8px">
          <span class="pill" :class="asr.installed ? 'ok' : ''">{{ asr.installed ? $t('已安装') : $t('未安装') }}</span>
          <!-- 桌面版也能装：接口借本机同版本 Python 装进外部 site 目录（找不到 Python 时 425 的 detail 会显示在状态里） -->
          <button v-if="!asr.installed" class="btn sm" data-test="asr-install" @click="installAsr">{{ $t('一键安装 faster-whisper') }}</button>
          <template v-else>
            <select v-model="asrModel" style="padding:5px 8px;border:1px solid var(--border);border-radius:var(--r-sm);background:var(--panel2)">
              <option v-for="m in asr.models" :key="m.name" :value="m.name">{{ m.name }}{{ m.cached ? $t('（已缓存）') : '' }}</option>
            </select>
            <button class="btn sm" @click="downloadModel">{{ $t('预下载模型') }}</button>
          </template>
          <span class="status">{{ asrStatus }}</span>
        </div>
        <!-- 装上之后就不用再看安装提示了（打包版以前永远装不上所以常显，现在外部 site 目录装好也算装上） -->
        <p v-if="asr && !asr.installed" class="hint" style="margin-top:6px">{{ asr.install_hint }}</p>
      </div>
      <div class="card" data-test="catalog-card">
        <h3>{{ $t('模型目录（OpenRouter）') }}</h3>
        <p class="hint">{{ $t('从 OpenRouter 拉一份公开的模型列表（不需要密钥）：模型 id 输入框会有补全，开始制作前的能力检查会指出 id 不存在、模型不出图/不出视频、不吃参考图这几种坑。列表不会自动刷新。') }}</p>
        <div class="row" style="margin-top:8px">
          <button class="btn sm" @click="refreshCatalog">{{ $t('拉取 / 更新目录') }}</button>
          <span class="hint" v-if="catalog">{{ catalogSummary() }}</span>
          <span class="status">{{ catalogStatus }}</span>
        </div>
      </div>
      <div class="card">
        <h3>{{ $t('版本更新') }}</h3>
        <p class="hint">{{ $t('填一个版本清单地址（JSON：version / url / notes），启动后可以手动检查有没有新版本。不自动下载、不自动安装。') }}</p>
        <div class="row" style="margin-top:8px">
          <input v-model="updateUrl" placeholder="https://…/storystudio-latest.json" style="flex:1;min-width:260px;padding:7px 10px;border:1px solid var(--border);border-radius:var(--r-sm);background:var(--panel2)" />
          <button class="btn sm" @click="saveUpdateUrl">{{ $t('保存地址') }}</button>
          <button class="btn sm" :disabled="!updateUrl.trim()" @click="checkUpdate">{{ $t('检查更新') }}</button>
          <span class="status">{{ updateStatus }}</span>
        </div>
        <div v-if="updateInfo" class="small" style="margin-top:8px">
          <template v-if="updateInfo.error"><span class="bad">{{ updateInfo.error }}</span></template>
          <template v-else-if="updateInfo.update_available">{{ $t('有新版本') }} <b>{{ updateInfo.latest }}</b>{{ $t('（当前 {v}）：', { v: updateInfo.current }) }}<a :href="updateInfo.url" target="_blank" rel="noopener">{{ updateInfo.url }}</a><div class="hint" style="white-space:pre-line">{{ updateInfo.notes }}</div></template>
          <template v-else>{{ updateInfo.latest ? $t('当前 {v} 已是最新（清单：{latest}）。', { v: updateInfo.current, latest: updateInfo.latest }) : $t('当前 {v} 已是最新。', { v: updateInfo.current }) }}</template>
        </div>
      </div>
      <div v-for="role in doc.roles" :key="role" class="card">
        <div class="row between" style="margin-bottom:8px">
          <div><h3 style="margin:0">{{ $t(ROLE[role].title) }}</h3><div class="hint">{{ $t(ROLE[role].desc) }}</div></div>
          <span class="pill" :class="(doc.status[role] || []).length ? 'warn' : 'ok'">{{ (doc.status[role] || []).length ? $t('未配置') : $t('已配置') }}</span>
        </div>
        <div class="grid2">
          <div class="field span2"><label>{{ $t('来源') }}</label>
            <select v-model="form[role].adapter" :data-test="`adapter-${role}`">
              <option value="">{{ $t('（未选择）') }}</option>
              <option v-for="(a, id) in doc.adapters[role]" :key="id" :value="id">{{ a.label }}{{ a.plugin ? $t('（插件）') : '' }}</option>
              <option v-if="unavailableOf(role)" :value="unavailableOf(role).adapter" disabled :data-test="`adapter-unavailable-${role}`">{{ unavailableOf(role).adapter }}{{ $t('（未加载）') }}</option>
            </select>
            <div v-if="unavailableOf(role) && form[role].adapter === unavailableOf(role).adapter" class="note" style="margin-top:6px" :data-test="`unavailable-${role}`">{{ unavailableOf(role).message }}</div>
            <div v-else-if="noSources(role)" class="hint" style="margin-top:6px" :data-test="`no-sources-${role}`">{{ $t('目前没有可选的来源（内置来源不覆盖这一档，可以装一个提供它的插件）') }}</div>
          </div>
          <div v-for="f in adapterOf(role)?.fields || []" :key="f" class="field">
            <label>{{ FIELD[f] ? $t(FIELD[f]) : f }}<span v-if="f === 'model' && catalogFor(role).length" class="hint" style="margin-left:6px">{{ $t('目录里 {n} 个可选，输入时补全', { n: catalogFor(role).length }) }}</span><span
                v-if="f === 'api_key' && roleLocal(role)" class="hint" style="margin-left:6px" :data-test="`local-key-hint-${role}`">{{ $t('本机服务通常不需要，可以留空') }}</span></label>
            <input v-model="form[role].fields[f]" :type="f === 'api_key' ? 'password' : 'text'" :placeholder="(adapterOf(role).defaults || {})[f] || ''" autocomplete="off"
                   :list="f === 'model' && catalogFor(role).length ? `catalog-${role}` : undefined" />
            <button v-if="f === 'api_key' && doc.settings.roles[role]?.api_key" type="button" class="btn sm" style="margin-top:4px"
                    :data-test="`clear-key-${role}`" @click="clearKey(role)">{{ $t('清除密钥') }}</button>
            <datalist v-if="f === 'model' && catalogFor(role).length" :id="`catalog-${role}`">
              <option v-for="m in catalogFor(role)" :key="m.id" :value="m.id">{{ m.name }}{{ m.input.includes('image') ? '' : $t('（不吃图）') }}</option>
            </datalist>
            <div v-if="f === 'model' && modelHint(role)" class="hint" data-test="model-hint" :class="{ warn: catalogMissing(role) }">{{ modelHint(role) }}</div>
          </div>
        </div>
        <div v-if="adapterOf(role)" class="hint" style="margin-bottom:8px">{{ adapterOf(role).hint }}</div>
        <div v-if="hiddenStoredKey(role)" class="hint warn" style="margin-bottom:8px" :data-test="`stored-key-${role}`">
          {{ $t('这个来源的表单里没有密钥栏，设置里却存着一个（{key}），请求时可能会带上——自己给反向代理配的就留着，不认识的清掉', { key: hiddenStoredKey(role) }) }}
          <button type="button" class="btn sm" style="margin-left:6px" :data-test="`clear-key-${role}`" @click="clearKey(role)">{{ $t('清除密钥') }}</button>
        </div>
        <div class="row">
          <button class="btn primary sm" @click="save(role)">{{ $t('保存') }}</button>
          <button class="btn sm" @click="test(role)">{{ $t('测试连接') }}</button>
          <span class="status">{{ result[role] }}</span>
        </div>
      </div>
    </template>
  </div>
</template>
