<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store, refreshProject, withBusy, runGenerate, retake, review, cameraOptionsFor, reviseEdit } from '../../store.js'
import { get, post, put, del, ep, pollTask } from '../../api.js'
import { confirm, notice, custom, toast, T } from '../../ui.js'
import { t } from '../../i18n.js'
import QcBadge from '../../components/QcBadge.vue'
import OverlayMask from '../../components/OverlayMask.vue'

const route = useRoute(); const router = useRouter()
// 交付面遮挡区（DESIGN §27）：后端按 delivery 算好的区域叠在首帧上；开关记在 localStorage（隐私模式下读写会抛，兜住）
const OVERLAY_KEY = 'storystudio.showOverlays'
function readOverlayPref() { try { return localStorage.getItem(OVERLAY_KEY) !== '0' } catch { return true } }
const showOverlays = ref(readOverlayPref())
watch(showOverlays, (v) => { try { localStorage.setItem(OVERLAY_KEY, v ? '1' : '0') } catch { /* ignore */ } })
const overlayRegions = computed(() => store.postLayers?.overlays?.regions || [])
const d = computed(() => store.detail)
const shots = computed(() => d.value?.shots || [])
const cur = computed(() => shots.value.find(s => s.id === route.params.shotId) || null)
const locked = computed(() => d.value?.locked)
const editable = computed(() => d.value?.editable)
// 制作中也能改：草稿直接写；制作中的项目走「预览影响 → 保存为新版本」（旧式只读格式除外）
const canEdit = computed(() => editable.value || (locked.value && !d.value?.readonly_reason))

// 统一入口：草稿态调直接接口；制作中改为 revise 流程，成功后跳到新版本的同一位置
async function mutate(direct, mutation, afterPath) {
  if (!locked.value) return direct()
  const r = await reviseEdit(mutation)
  if (r) router.push(`/drama/p/${r.id}/${afterPath || 'shots'}`)
  return r
}
const cams = computed(() => cameraOptionsFor(d.value))
const cast = computed(() => store.cast || [])
const form = reactive({})
const status = ref('')
const statusOk = ref(false) // 状态栏文案是否为成功提示（不靠比对中文字面，切语言也不会误判成错误）
// 镜头功能（相邻两镜功能别重复，后端 lint.PURPOSES 同一份、同一顺序）；value 是发给后端的 id，label 渲染时翻译
const PURPOSES = { establish: '建立（交代空间/人物）', detail: '细节/证据', power: '权力关系', pressure: '施压', impact: '冲击', shift: '转折', reaction: '反应', aftermath: '余波', exit: '出场/收束' }
// 关系场景类型（剧本工艺清单里关系戏的那几条）：标了才检查拒绝怎么挡开 / 分手有没有撤不回的举动 / 重逢凭什么认出来；value 发给后端，label 渲染时翻译
const SCENE_TYPES = { confession: '表白', reunion: '重逢', forgiveness: '原谅', rejection: '拒绝', breakup: '分手' }
// 画面文字政策（DESIGN §20）：画面里必须可读的文字；value 发给后端，label 渲染时翻译
const TEXT_LANGS = { zh: '中文', en: '英文', digits: '数字' }
// 结构化起止边界（DESIGN §22，drama-skills start/end_boundary）：六格都是"不读上一镜也能理解"的绝对事实；
// 「与上一镜相同」用勾选表达，编译器渲染成一句。key 发给后端，label / 占位在渲染时翻译。
const BOUNDARY_KEYS = { positions: '位置', facing: '朝向', gaze: '视线', hands: '手', held_props: '持物', visible_state: '可见状态' }
const BOUNDARY_HINTS = { positions: '谁在哪个锚点的哪一侧、相距多少', facing: '身体朝向谁 / 哪', gaze: '视线落在什么上', hands: '双手在做什么、放在哪', held_props: '谁手里有什么、在哪只手', visible_state: '衣服 / 道具 / 伤痕的可见状态' }
const AXIS_SIDES = { left: '左', right: '右' }
const ACTIVE_CHIP = 'background:var(--accent-bg);border-color:var(--accent);color:var(--accent-strong)'
const structured = ref(false)   // 接缝状态用结构化六格还是自由文本；镜次已有 entry/exit 对象时默认开
function boundaryOf(obj) {
  const b = { same_as_previous: [...(obj?.same_as_previous || [])] }
  for (const k of Object.keys(BOUNDARY_KEYS)) b[k] = obj?.[k] || ''
  return b
}
function axisOf(obj) { return { anchors: [obj?.anchors?.[0] || '', obj?.anchors?.[1] || ''], side: obj?.side || '', screen: { ...(obj?.screen || {}) }, break: !!obj?.break } }
// 表单有没有没保存的改动：refreshProject 每次都把 store.detail 换成新对象（生成期间每 8 秒一次），有改动时不能拿服务端的值
// 把它冲掉；换镜 / 换版本才重灌。改动靠 form 的深层同步 watch 记——台词行、参考图用途、轴线这些都是按钮改嵌套数组，
// 逐个 @input 记不全；灌表单本身的写入用 filling 排除掉。
const dirty = ref(false)
let filling = false
let filledKey = ''   // 当前表单灌的是哪个版本的哪一镜
function fill(s) {
  filling = true
  try {
    Object.assign(form, {
      shot: s.shot, art: s.art, motion: s.motion, sfx: s.sfx, entry_state: s.entry_state, exit_state: s.exit_state, purpose: s.purpose || '', scene_type: s.scene_type || '',
      dur: s.dur, camera_move: s.camera_move || '', ref_frame: s.ref_frame || '', cast: [...(s.cast || [])],
      lines: (s.lines || []).map(l => ({ who: l.who, zh: l.zh, tone: l.tone || '' })),
      readable_text: (s.readable_text || []).map(r => ({ surface: r.surface || '', text: r.text || '', language: r.language || 'zh', position: r.position || '' })),
      withhold: (s.withhold || []).map(w => ({ fact: w.fact || '', carrier: w.carrier || '', until: w.until || '' })),
      entry: boundaryOf(s.entry), exit: boundaryOf(s.exit), axis: axisOf(s.axis), boundary_change: s.boundary_change || '',
      ref_tail: { shot: s.ref_tail?.shot || '', mode: s.ref_tail?.mode || 'reference' },
      reference_roles: (s.reference_roles || []).map(r => ({ target: r.target || '', use: r.use || '', control: [...(r.control || [])], ignore: [...(r.ignore || [])], fidelity: r.fidelity ?? null })),
    })
    structured.value = !!(s.entry || s.exit)
  } finally { filling = false }
  dirty.value = false
  filledKey = `${store.projectId}/${s.id}`
}
watch(form, () => { if (!filling) dirty.value = true }, { deep: true, flush: 'sync' })
watch(structured, () => { if (!filling) dirty.value = true }, { flush: 'sync' })
watch(cur, (s) => {
  if (!s) return
  if (`${store.projectId}/${s.id}` !== filledKey) { fill(s); status.value = ''; statusOk.value = false; return }
  if (!dirty.value) fill(s)   // 同一镜刷新：没改动才跟着服务端
}, { immediate: true })
function toggleSame(key) { const l = form.entry.same_as_previous; const i = l.indexOf(key); if (i >= 0) l.splice(i, 1); else l.push(key) }
// 轴线锚点从本镜出场里选（角色和场景都能当锚点：人—人、人—门）
const axisCandidates = computed(() => form.cast.map(ref => { const id = castIdOf(ref); return cast.value.find(x => x.id === id) || { id, name: id } }))
const axisAnchors = computed(() => [...new Set((form.axis?.anchors || []).filter(Boolean))])
function castName(id) { return cast.value.find(x => x.id === id)?.name || id }
function setScreen(id, side) { if (form.axis.screen[id] === side) delete form.axis.screen[id]; else form.axis.screen[id] = side }
// 发给后端的形状：{} = 清掉；结构化关着时清掉 entry/exit 对象，让自由文本重新权威
function boundaryPayload(b, withSame) {
  const out = {}
  for (const k of Object.keys(BOUNDARY_KEYS)) if ((b[k] || '').trim()) out[k] = b[k].trim()
  if (withSame && b.same_as_previous?.length) out.same_as_previous = [...b.same_as_previous]
  return out
}
function axisPayload() {
  const anchors = axisAnchors.value
  if (!anchors.length) return {}
  const screen = {}
  for (const a of anchors) if (AXIS_SIDES[form.axis.screen[a]]) screen[a] = form.axis.screen[a]
  return { anchors, side: form.axis.side || '', screen, break: !!form.axis.break }
}

const index = computed(() => shots.value.findIndex(s => s.id === cur.value?.id))
function go(i) { const s = shots.value[i]; if (s) router.push(`/drama/p/${store.projectId}/shots/${s.id}`) }

async function save() {
  if (axisAnchors.value.length === 1) { status.value = t('轴线要选两个不同的锚点（或都留空）'); statusOk.value = false; return }
  const payload = { ...form, ref_frame: form.ref_frame || '', camera_move: form.camera_move || null,
    readable_text: (form.readable_text || []).filter(r => (r.surface || '').trim() || (r.text || '').trim()),   // 整行空白 = 没填，不发
    withhold: (form.withhold || []).filter(w => (w.fact || '').trim() || (w.carrier || '').trim()),
    entry: structured.value ? boundaryPayload(form.entry, true) : {}, exit: structured.value ? boundaryPayload(form.exit, false) : {},
    axis: axisPayload(), boundary_change: form.boundary_change || '',
    ref_tail: form.ref_tail?.shot ? { shot: form.ref_tail.shot, mode: form.ref_tail.mode || 'reference' } : '',
    reference_roles: (form.reference_roles || []).filter(r => r.target && ((r.use || '').trim() || r.control?.length || r.ignore?.length || r.fidelity)).map(r => ({ ...r, fidelity: r.fidelity ? Number(r.fidelity) : undefined })) }
  const sid = cur.value.id
  if (locked.value) { await mutate(null, { kind: 'shot_edit', shot_id: sid, ...payload }, `shots/${sid}`); return }
  status.value = t('保存中…'); statusOk.value = true
  try {
    await put(`${ep(store.projectId)}/shots/${sid}`, payload)
    dirty.value = false   // 已经落盘：接下来的刷新要把服务端规范化后的值灌回来
    await refreshProject(); status.value = t('已保存'); statusOk.value = true
  } catch (e) { status.value = e.message; statusOk.value = false }
}
function addLine() { form.lines.push({ who: form.cast.map(castIdOf).find(c => cast.value.find(x => x.id === c)?.kind === 'character') || '', zh: '', tone: '' }) }
function removeLine(i) { form.lines.splice(i, 1) }
function addText() { (form.readable_text ||= []).push({ surface: '', text: '', language: 'zh', position: '' }) }
function removeText(i) { form.readable_text.splice(i, 1) }
// 观众可见性 / 揭示保护（DESIGN §28）：先不揭示的事实、会泄露它的载体、从哪一镜起可以揭示（只能选本镜之后的镜）
function addWithhold() { (form.withhold ||= []).push({ fact: '', carrier: '', until: '' }) }
function removeWithhold(i) { form.withhold.splice(i, 1) }
const revealCandidates = computed(() => shots.value.slice(index.value + 1))
// 尾帧续接（DESIGN §30）：源镜只能是前面的镜；作参考图 = 尾帧占首帧请求的第一个参考槽，直接当首帧 = 不出图不花钱
const TAIL_MODES = { reference: '作参考图（尾帧占第一个参考槽）', direct: '直接当首帧（不出图、不花钱）' }
const tailCandidates = computed(() => shots.value.slice(0, Math.max(index.value, 0)))
// 参考图用途：target 从本镜真的会带的参考图里选——依赖槽（尾帧 / 参考镜）跟着表单里的选择走，角色定妆 / 道具沿用后端算出的槽位
const REFERENCE_ASPECTS = ['脸', '发型', '服装', '姿势', '机位', '光线', '场景', '色调']
const referenceTargets = computed(() => {
  const out = []
  if (form.ref_tail?.shot && form.ref_tail.mode !== 'direct') out.push({ target: 'tail', label: t('上一镜 {id} 尾帧', { id: form.ref_tail.shot }) })
  else if (form.ref_frame) out.push({ target: 'prev_frame', label: t('上一镜 {id} 首帧', { id: form.ref_frame }) })
  for (const slot of cur.value?.reference_slots || []) {
    if (slot.target === 'tail' || slot.target === 'prev_frame') continue
    if (slot.target.startsWith('cast:') && !form.cast.some(c => castIdOf(c) === castIdOf(slot.target.slice(5)))) continue
    out.push({ target: slot.target, label: slot.label_zh || slot.target })
  }
  return out
})
function addRole() { (form.reference_roles ||= []).push({ target: referenceTargets.value[0]?.target || '', use: '', control: [], ignore: [], fidelity: null }) }
function removeRole(i) { form.reference_roles.splice(i, 1) }
function toggleAspect(list, aspect) { const i = list.indexOf(aspect); if (i >= 0) list.splice(i, 1); else list.push(aspect) }
function addAspect(list, e) { const v = (e.target.value || '').trim(); if (v && !list.includes(v)) list.push(v); e.target.value = '' }
// 出场引用：`lin` 是基底，`lin@rain` 是角色的造型变体（DESIGN §23）；form.cast 原样存字符串
function castIdOf(ref) { return String(ref).split('@')[0] }
function castIndex(id) { return form.cast.findIndex(c => castIdOf(c) === id) }
function castVariantOf(id) { const i = castIndex(id); return i >= 0 ? (String(form.cast[i]).split('@')[1] || '') : '' }
function toggleCast(id) { const i = castIndex(id); if (i >= 0) form.cast.splice(i, 1); else form.cast.push(id) }
function setCastVariant(id, key) { const i = castIndex(id); if (i >= 0) form.cast.splice(i, 1, key ? `${id}@${key}` : id) }

// 新镜的占位内容是发给后端存进剧本的数据（剧本正文本身是中文），不做界面翻译
const NEW_SHOT = { shot: '中景（待写：人在画面哪一块、看着什么、身子朝哪、机位高低）', art: '（画面里有什么：待写）', motion: '唯一动作：（待写）', dur: 5, cast: [] }
async function addShot(after) {
  // 镜次 id 由后端按 S<序号> 生成，用户不用起名
  await mutate(() => withBusy(async () => {
    try {
      const created = await post(`${ep(store.projectId)}/shots`, { after, ...NEW_SHOT })
      await refreshProject(); router.push(`/drama/p/${store.projectId}/shots/${created.shot?.id || created.id || ''}`)
    } catch (e) { await notice(e.message) }
  }), { kind: 'shot_create', after, ...NEW_SHOT }, 'shots')
}
async function removeShot() {
  const sid = cur.value.id
  if (!await confirm(t('删除 {id}？', { id: sid }), { confirmLabel: t('删除'), danger: true })) return
  await mutate(async () => {
    try { await del(`${ep(store.projectId)}/shots/${sid}`); await refreshProject(); router.push(`/drama/p/${store.projectId}/shots`) }
    catch (e) { await notice(e.message) }
  }, { kind: 'shot_delete', shot_id: sid }, 'shots')
}
// ---- 列表拖拽排序：松手时算出新顺序，走跟上移/下移同一条路（草稿直写，制作中预告影响）
const dragging = ref(null)
const dragOver = ref(null)
function onDragStart(id, ev) { dragging.value = id; ev.dataTransfer?.setData('text/plain', id); ev.dataTransfer && (ev.dataTransfer.effectAllowed = 'move') }
async function onDrop(targetId) {
  const from = dragging.value; dragging.value = null; dragOver.value = null
  if (!from || from === targetId) return
  const order = shots.value.map(s => s.id)
  const i = order.indexOf(from), j = order.indexOf(targetId)
  order.splice(i, 1); order.splice(j, 0, from)
  await mutate(async () => {
    try { await put(`${ep(store.projectId)}/shots-order`, { order }); await refreshProject() } catch (e) { await notice(e.message) }
  }, { kind: 'shot_order', order }, 'shots')
}
async function move(delta) {
  const order = shots.value.map(s => s.id); const i = index.value; const j = i + delta
  if (j < 0 || j >= order.length) return
  ;[order[i], order[j]] = [order[j], order[i]]
  await mutate(async () => {
    try { await put(`${ep(store.projectId)}/shots-order`, { order }); await refreshProject() } catch (e) { await notice(e.message) }
  }, { kind: 'shot_order', order }, `shots/${cur.value.id}`)
}

// ---- 首帧对比试跑（不进账本的候选；采用时才回到账本）
const cmpModels = ref('')
const cmpRows = ref([])
async function loadCompare() {
  if (!cur.value || !locked.value) { cmpRows.value = []; return }
  try { cmpRows.value = (await get(`${ep(store.projectId)}/compare/frames/${cur.value.id}`)).candidates } catch { cmpRows.value = [] }
}
watch(cur, loadCompare, { immediate: true })
async function runCompare() {
  const models = cmpModels.value.split(/[,，\s]+/).map(s => s.trim()).filter(Boolean)
  if (!models.length) return
  if (!await confirm(t('对 {id} 用 {n} 个模型各出一张首帧候选（{n} 次生图请求，真花钱）？\n\n{list}', { id: cur.value.id, n: models.length, list: models.join('\n') }), { confirmLabel: t('出候选') })) return
  let created
  try { created = await post(`${ep(store.projectId)}/compare/frames`, { shot_id: cur.value.id, models }) } catch (e) { await notice(e.message); return }
  store.gen = { text: t('出候选中…') }
  try {
    const task = await pollTask(created.task_id, (_t, s) => { store.gen = { text: t('出候选中…已经 {s} 秒', { s }) } })
    if (task.status === 'failed') await notice(t('出候选失败：{err}', { err: task.error }))
  } catch (e) { await notice(t('出候选失败：{err}', { err: e.message }))   // 服务重启 / 任务记录没了：轮询抛错也要有一句提示
  } finally { store.gen = null; await loadCompare() }
}
async function adoptCandidate(r) {
  if (!await confirm(t('把这张（{model}）采用为 {id} 的首帧？\n\n会记一次重拍并重新登记，原首帧备份在对比目录里；这一镜要重新审首帧，视频（如已生成）也要重出。', { model: r.model, id: cur.value.id }), { confirmLabel: t('采用') })) return
  await withBusy(async () => {
    try { await post(`${ep(store.projectId)}/compare/frames/${cur.value.id}/adopt`, { name: r.file }); await refreshProject(); toast(t('已采用为首帧，请重新审片')) }
    catch (e) { await notice(e.message) }
  })
}

// ---- 操作：生成 / 重拍 / 审片
const gen = (through) => withBusy(() => runGenerate(through, [cur.value.id]))
const gateName = (gate) => gate === 'frames' ? t('首帧') : t('视频')
// 重拍处置（DESIGN §24）：缺陷 + 处置五分法。保留 = 通过、后期处理 = 跳过（都带缺陷记录）；改文字重出只记重拍、
// 回编辑器改那一层；原样重提才真的重新生成，同一份提示词出过 2 次后后端 409，用户确认「仍然重提」才 force。
async function doRetake(gate) {
  const sid = cur.value.id
  const r = await custom({ view: 'retake', gate, shotId: sid, attempts: cur.value.attempts?.[gate] || null, last: cur.value.qc_meta?.[gate]?.last_retake || null })
  if (!r) return
  const meta = { defect: r.defect, disposition: r.disposition }
  if (r.disposition === 'model') { focusCompare(); return }
  if (r.disposition === 'keep' || r.disposition === 'post') {
    const decision = r.disposition === 'keep' ? 'pass' : 'waive'
    await withBusy(async () => { try { await review(gate, decision, [sid], r.reason || (r.disposition === 'keep' ? '' : t('交后期处理')), meta); toast(decision === 'pass' ? t('已通过') : t('已记为跳过，交后期处理')) } catch (e) { await notice(e.message) } })
    return
  }
  await withBusy(async () => {
    try {
      await retake(gate, sid, r.reason, meta)
      if (r.disposition === 'edit') toast(t('已记重拍。在下面改画面/动作/台词文字，保存后会预告并重出这一镜'), { ttl: 7000 })
    } catch (e) {
      if (e.status === 409 && e.detail?.kind === 'retry_limit') {
        // 重投纪律：把后端的人话摆出来，明确「仍然重提」才 force
        if (await confirm(e.detail.message, { confirmLabel: t('仍然重提'), cancelLabel: t('先不重提'), danger: true })) {
          try { await retake(gate, sid, r.reason, { ...meta, force: true }) } catch (e2) { await notice(e2.message) }
        }
        return
      }
      await notice(e.message)
    }
  })
}
// 「换模型对比」不落账：滚到右侧「首帧对比试跑」区，光标放进模型框
function focusCompare() {
  const el = document.querySelector('[data-test="compare-section"]')
  el?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  el?.querySelector?.('input')?.focus?.()
  if (!el) toast(t('多模型对比在右侧「首帧对比试跑」区'))
}
const attemptsPill = (s, gate) => Number(s.attempts?.[gate]?.same_request || 0)
async function pass(gate) { await withBusy(async () => { try { await review(gate, 'pass', [cur.value.id]); toast(t('已通过')) } catch (e) { await notice(e.message) } }) }
async function passAll(gate) {
  const key = gate === 'frames' ? 'frame' : 'video'
  const pending = shots.value.filter(s => s[`${key}_url`] && !s[`${key}_qc_decision`]).map(s => s.id)
  if (!pending.length) { await notice(t('没有待审的{what}', { what: gateName(gate) })); return }
  if (!await confirm(t('把 {n} 个待审{what}一次记为通过？\n\n{list}', { n: pending.length, what: gateName(gate), list: pending.join(t('、')) }), { confirmLabel: t('全部通过（{n}）', { n: pending.length }) })) return
  // '批量通过' 是写进审片账本的备注（数据），不翻译
  await withBusy(async () => { try { await review(gate, 'pass', pending, '批量通过'); toast(t('已记录')) } catch (e) { await notice(e.message) } })
}
function failuresOf(id) { return (store.summary?.failures || []).filter(f => f.shot_id === id) }
function asrClass(a) { return a.status === 'pass' || a.status === 'no_lines' ? 'ok' : a.status === 'review' ? 'warn' : 'bad' }
</script>

<template>
  <div class="ws-content">
    <div class="ws-center" v-if="!cur">
      <div class="row between" style="margin-bottom:10px">
        <h3 style="font-size:16px">{{ $t('镜头') }} {{ shots.length }}</h3>
        <div class="row">
          <button v-if="locked" class="btn sm" @click="passAll('frames')">{{ $t('待审首帧全部通过') }}</button>
          <button v-if="locked" class="btn sm" @click="passAll('videos')">{{ $t('待审视频全部通过') }}</button>
          <button class="btn sm" :disabled="!canEdit" @click="addShot(null)">{{ $t('+ 在末尾加一镜') }}</button>
        </div>
      </div>
      <div v-if="canEdit" class="hint" style="margin-bottom:6px">{{ locked ? $t('拖动镜头卡片可以调整顺序（制作中：松手后会预告影响并保存为新版本）。') : $t('拖动镜头卡片可以调整顺序。') }}</div>
      <div class="item-list">
        <router-link v-for="(s, i) in shots" :key="s.id" class="item" :class="{ 'drag-over': dragOver === s.id }" :to="`/drama/p/${store.projectId}/shots/${s.id}`"
          :draggable="canEdit" @dragstart="onDragStart(s.id, $event)" @dragover.prevent="dragOver = s.id" @dragleave="dragOver = null" @drop.prevent="onDrop(s.id)" @dragend="dragOver = null; dragging = null">
          <span class="thumb"><img v-if="s.frame_url" :src="s.frame_url" alt="" /><span v-else>{{ $t('无首帧') }}</span></span>
          <span class="body">
            <span class="title"><span class="mono">{{ i + 1 }}. {{ s.id }}</span> <span class="hint">{{ s.dur }}s · {{ s.cast.join($t('、')) || $t('无角色') }}</span></span>
            <span class="sub">{{ s.shot }}</span>
            <span class="sub">{{ s.lines.map(l => l.zh).join(' / ') || $t('（无台词）') }}</span>
            <span class="row" style="margin-top:4px;gap:10px">
              <QcBadge v-if="s.frame_url" :decision="s.frame_qc_decision" /><span v-else class="hint small">{{ $t('首帧未生成') }}</span>
              <QcBadge v-if="s.video_url" :decision="s.video_qc_decision" /><span v-else class="hint small">{{ $t('视频未生成') }}</span>
              <span v-if="s.asr" class="small" :class="asrClass(s.asr)">{{ $t(T.asr[s.asr.status]) }}</span>
              <span v-if="attemptsPill(s, 'frames') >= 2" class="pill small" data-test="attempts-pill" :title="$t('同一份提示词已出过 {n} 次', { n: attemptsPill(s, 'frames') })">{{ $t('首帧第 {n} 次', { n: attemptsPill(s, 'frames') }) }}</span>
              <span v-if="attemptsPill(s, 'videos') >= 2" class="pill small" data-test="attempts-pill" :title="$t('同一份提示词已出过 {n} 次', { n: attemptsPill(s, 'videos') })">{{ $t('视频第 {n} 次', { n: attemptsPill(s, 'videos') }) }}</span>
              <span v-for="f in failuresOf(s.id)" :key="f.step_id" class="small bad" :title="f.error">✗ {{ f.stage === 'frames' ? $t('首帧') : $t('视频') }}{{ f.label }}</span>
            </span>
          </span>
        </router-link>
      </div>
    </div>

    <template v-else>
      <div class="ws-center">
        <div class="row between" style="margin-bottom:8px">
          <div class="row">
            <button class="btn sm" @click="router.push(`/drama/p/${store.projectId}/shots`)">{{ $t('← 列表') }}</button>
            <button class="btn sm" :disabled="index <= 0" @click="go(index - 1)">{{ $t('上一镜') }}</button>
            <button class="btn sm" :disabled="index >= shots.length - 1" @click="go(index + 1)">{{ $t('下一镜') }}</button>
            <h3 style="font-size:16px;margin-left:6px" class="mono">{{ index + 1 }}. {{ cur.id }}</h3>
          </div>
          <div class="row" v-if="canEdit">
            <button class="btn sm" :disabled="index <= 0" @click="move(-1)">{{ $t('↑ 前移') }}</button>
            <button class="btn sm" :disabled="index >= shots.length - 1" @click="move(1)">{{ $t('↓ 后移') }}</button>
            <button class="btn sm" @click="addShot(cur.id)">{{ $t('+ 在这镜后加一镜') }}</button>
            <button class="btn sm danger" @click="removeShot">{{ $t('删除') }}</button>
          </div>
        </div>

        <div v-if="cur.frame_url || cur.video_url" class="media" style="margin-bottom:14px">
          <div v-if="cur.frame_url">
            <div class="cap"><span>{{ $t('首帧') }}</span><QcBadge :decision="cur.frame_qc_decision" />
              <label v-if="overlayRegions.length" class="small" style="margin-left:auto;cursor:pointer" data-test="overlay-toggle"><input type="checkbox" v-model="showOverlays" /> {{ $t('显示遮挡区') }}</label></div>
            <div style="position:relative">
              <img :src="cur.frame_url" alt="" />
              <OverlayMask :regions="overlayRegions" :show="showOverlays" />
              <!-- 画面文字的位置预览：按 y 比例叠在首帧上，跟后期合成同一套坐标 -->
              <div v-for="(n, i) in cur.links.notebook_text" :key="i" :title="$t('第 {t}s 出现', { t: n.at ?? 0.4 })"
                   style="position:absolute;left:0;right:0;text-align:center;pointer-events:none;color:#1a1a1a;font-family:var(--display);text-shadow:0 0 3px #fff,0 0 6px #fff;white-space:pre-line"
                   :style="{ top: ((n.y ?? 0.56) * 100) + '%', fontSize: ((n.size || (n.text.replace(/\n/g, '').length <= 2 ? 88 : 46)) / 1920 * 100 * 3.2) + '%' }">{{ n.text }}</div>
              <div v-for="(sid, i) in cur.links.inserts_after" :key="'ins' + i" class="pill" style="position:absolute;right:6px;bottom:6px;background:rgba(0,0,0,.65);color:#fff;border-color:transparent">{{ $t('后接插卡 {id}', { id: sid }) }}</div>
            </div>
          </div>
          <div v-if="cur.video_url">
            <div class="cap"><span>{{ $t('视频') }}</span><QcBadge :decision="cur.video_qc_decision" /></div>
            <video :src="cur.video_url" controls preload="metadata"></video>
            <div v-if="cur.asr" class="small" :class="asrClass(cur.asr)" style="margin-top:4px" :title="$t('期望「{s}」', { s: cur.asr.expected || '' })">
              {{ $t('台词识别：') }}{{ $t(T.asr[cur.asr.status]) }}{{ typeof cur.asr.cer === 'number' ? $t(' · 字错率 {cer}', { cer: cur.asr.cer }) : '' }}{{ cur.asr.heard ? $t(' · 听到「{s}」', { s: cur.asr.heard }) : '' }}
            </div>
          </div>
        </div>

        <div v-if="locked" class="hint" style="margin-bottom:10px">{{ $t('制作中：这里改的画面、动作、台词、运镜会进提示词。点「保存」时系统会先告诉你哪些镜的首帧/视频要重出，确认后保存为一个新版本，没受影响的产物直接带过去；旁白、插卡、画面文字在「旁白与插卡」里随时能改。') }}</div>
        <div class="field"><label>{{ $t('画面（景别之外还要写清：人在画面哪一块、看着什么、身子朝哪、机位高低——不写清楚，模型就自己定）') }}</label><textarea v-model="form.shot" :disabled="!canEdit" rows="2"></textarea></div>
        <div class="field"><label>{{ $t('美术（画面里有什么、道具在哪）') }}</label><textarea v-model="form.art" :disabled="!canEdit" rows="2"></textarea></div>
        <div class="field"><label>{{ $t('动作（一镜只有一个主导动作）') }}</label><textarea v-model="form.motion" :disabled="!canEdit" rows="2"></textarea></div>
        <div class="grid2">
          <div class="field"><label>{{ $t('时长（秒，整数）') }}</label><input v-model.number="form.dur" type="number" min="1" :disabled="!canEdit" /></div>
          <div class="field"><label>{{ $t('运镜') }}</label>
            <select v-model="form.camera_move" :disabled="!canEdit">
              <option value="">{{ $t('（不声明）') }}</option>
              <option v-for="c in cams.shots" :key="c.id" :value="c.id">{{ $t('{name}（{risk}）', { name: c.cn, risk: c.risk }) }}</option>
            </select></div>
          <div class="field"><label>{{ $t('参考画面（首帧以哪一镜为参考，保证连续）') }}</label>
            <select v-model="form.ref_frame" :disabled="!canEdit">
              <option value="">{{ $t('（独立首帧）') }}</option>
              <option v-for="s in shots.filter(x => x.id !== cur.id)" :key="s.id" :value="s.id">{{ s.id }}</option>
            </select></div>
          <div class="field" data-test="tail-editor"><label>{{ $t('尾帧续接（源镜视频过检后抽它的最后一帧接到这一镜；只能接前面的镜）') }}</label>
            <select v-model="form.ref_tail.shot" :disabled="!canEdit">
              <option value="">{{ $t('（不续接）') }}</option>
              <option v-for="s in tailCandidates" :key="s.id" :value="s.id">{{ s.id }}</option>
            </select>
            <div v-if="form.ref_tail.shot" class="row" style="gap:10px;flex-wrap:wrap;margin-top:4px">
              <label v-for="(label, key) in TAIL_MODES" :key="key" class="small" style="display:inline-flex;align-items:center;gap:4px"><input type="radio" :value="key" v-model="form.ref_tail.mode" :disabled="!canEdit" />{{ $t(label) }}</label>
            </div>
            <div v-if="form.ref_tail.shot" class="hint small">{{ $t('源镜 {id} 的视频过检之前，这一镜的首帧和视频会推迟；下一轮自动接上。源镜收在大全景而这一镜是近景/特写时会提示加桥接帧。', { id: form.ref_tail.shot }) }}</div>
          </div>
          <div class="field"><label>{{ $t('音效') }}</label><input v-model="form.sfx" :disabled="!canEdit" /></div>
          <div class="field"><label>{{ $t('镜头功能（相邻镜功能相同又同景别，观众读到的是跳切）') }}</label>
            <select v-model="form.purpose" :disabled="!canEdit">
              <option value="">{{ $t('（不声明）') }}</option>
              <option v-for="(label, key) in PURPOSES" :key="key" :value="key">{{ $t(label) }}</option>
            </select></div>
          <div class="field"><label>{{ $t('场景类型（可选；标了才按这类关系戏的写法检查：拒绝怎么挡开、分手有没有撤不回的举动、重逢凭什么认出来）') }}</label>
            <select v-model="form.scene_type" :disabled="!canEdit" data-test="scene-type">
              <option value="">{{ $t('（不声明）') }}</option>
              <option v-for="(label, key) in SCENE_TYPES" :key="key" :value="key">{{ $t(label) }}</option>
            </select></div>
        </div>
        <div class="field"><label>{{ $t('出场（勾选这一镜里出现的角色和场景）') }}</label>
          <div class="chips">
            <template v-for="c in cast" :key="c.id">
              <span class="chip" style="display:inline-flex;align-items:center;gap:5px" :style="castIndex(c.id) >= 0 ? 'background:var(--accent-bg);border-color:var(--accent);color:var(--accent-strong)' : ''" @click="canEdit && toggleCast(c.id)">
                <img v-if="(castVariantOf(c.id) && c.variants?.[castVariantOf(c.id)]?.asset_url) || c.asset_url" :src="(castVariantOf(c.id) && c.variants?.[castVariantOf(c.id)]?.asset_url) || c.asset_url" alt="" style="width:18px;height:18px;border-radius:50%;object-fit:cover" :title="$t('锚图：生成首帧时作为参考图')" />{{ castIndex(c.id) >= 0 ? '✓ ' : '' }}{{ c.name || c.id }}</span>
              <select v-if="castIndex(c.id) >= 0 && Object.keys(c.variants || {}).length" :value="castVariantOf(c.id)" :disabled="!canEdit" :title="$t('造型：基底或某个变体（差异接在外形后进首帧提示词，变体锚图优先）')" style="padding:2px 4px;margin-left:-4px;font-size:12px" @change="setCastVariant(c.id, $event.target.value)">
                <option value="">{{ $t('基底') }}</option>
                <option v-for="(v, key) in c.variants" :key="key" :value="key">{{ v.label || key }}</option>
              </select>
            </template>
            <router-link class="chip" :to="`/drama/p/${store.projectId}/cast`">{{ $t('管理角色…') }}</router-link>
          </div>
        </div>
        <div class="field">
          <label>{{ $t('接缝状态（入口接上一镜的出口，出口给下一镜接）') }}</label>
          <label class="small" style="display:inline-flex;align-items:center;gap:5px;font-weight:normal;margin-bottom:4px"><input type="checkbox" v-model="structured" :disabled="!canEdit" />{{ $t('结构化填写（位置 / 朝向 / 视线 / 手 / 持物 / 可见状态，逐字段和上一镜比对）') }}</label>
          <template v-if="structured">
            <div class="hint small" style="margin-bottom:6px">{{ $t('每格写成不读上一镜也能理解的绝对事实（谁在哪、朝哪、看哪、手在做什么、拿着什么）；真的没变就勾「与上一镜相同」，不要写「同上」。') }}</div>
            <div class="grid2">
              <div v-for="side in ['entry', 'exit']" :key="side">
                <div class="small" style="font-weight:600;margin:2px 0 4px">{{ side === 'entry' ? $t('入口') : $t('出口') }}</div>
                <div v-for="(label, key) in BOUNDARY_KEYS" :key="key" class="row" style="gap:6px;margin-bottom:4px;align-items:center;flex-wrap:nowrap">
                  <span class="small" style="width:60px;flex:none">{{ $t(label) }}</span>
                  <input v-model="form[side][key]" :disabled="!canEdit" :placeholder="$t(BOUNDARY_HINTS[key])" style="flex:1;min-width:0" />
                </div>
                <div v-if="side === 'entry'" class="row small" style="gap:8px;flex-wrap:wrap;align-items:center;margin-top:4px">
                  <span class="hint">{{ $t('与上一镜相同：') }}</span>
                  <label v-for="(label, key) in BOUNDARY_KEYS" :key="key" style="display:inline-flex;align-items:center;gap:3px"><input type="checkbox" :checked="form.entry.same_as_previous.includes(key)" :disabled="!canEdit" @change="toggleSame(key)" />{{ $t(label) }}</label>
                </div>
              </div>
            </div>
            <div class="field" style="margin-top:6px"><label>{{ $t('边界变化说明（入口和上一镜出口不一样时写变化来自哪里；不写，逐字段比对会告警）') }}</label><input v-model="form.boundary_change" :disabled="!canEdit" :placeholder="$t('例如：伞在镜外已递给她 / 时间跳到十分钟后')" /></div>
          </template>
          <div v-else class="grid2">
            <div class="field"><label>{{ $t('入口状态（这镜开始时人物/道具在哪，接上一镜的出口）') }}</label><textarea v-model="form.entry_state" :disabled="!canEdit" rows="2"></textarea></div>
            <div class="field"><label>{{ $t('出口状态（这镜结束时的落位，给下一镜接）') }}</label><textarea v-model="form.exit_state" :disabled="!canEdit" rows="2"></textarea></div>
          </div>
        </div>
        <div class="field">
          <label>{{ $t('轴线（两个锚点的连线：机位常驻哪一侧、谁固定占画面哪边。正反打要互补；有意越轴就标出来，不然相邻镜画面侧翻了会告警）') }}</label>
          <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
            <select v-model="form.axis.anchors[0]" :disabled="!canEdit"><option value="">{{ $t('（锚点甲）') }}</option><option v-for="c in axisCandidates" :key="c.id" :value="c.id">{{ c.name || c.id }}</option></select>
            <span class="hint">—</span>
            <select v-model="form.axis.anchors[1]" :disabled="!canEdit"><option value="">{{ $t('（锚点乙）') }}</option><option v-for="c in axisCandidates" :key="c.id" :value="c.id">{{ c.name || c.id }}</option></select>
            <select v-model="form.axis.side" :disabled="!canEdit"><option value="">{{ $t('（机位侧未定）') }}</option><option value="left">{{ $t('机位在连线左侧') }}</option><option value="right">{{ $t('机位在连线右侧') }}</option></select>
            <label class="small" style="display:inline-flex;align-items:center;gap:4px"><input type="checkbox" v-model="form.axis.break" :disabled="!canEdit" />{{ $t('本镜有意越轴') }}</label>
          </div>
          <div v-if="axisAnchors.length" class="chips" style="margin-top:4px;align-items:center">
            <template v-for="a in axisAnchors" :key="a">
              <span class="small">{{ castName(a) }}</span>
              <span v-for="(label, side) in AXIS_SIDES" :key="side" class="chip" :style="form.axis.screen[a] === side ? ACTIVE_CHIP : ''" @click="canEdit && setScreen(a, side)">{{ $t('画面{s}', { s: $t(label) }) }}</span>
            </template>
          </div>
        </div>
        <div class="field"><label>{{ $t('台词') }}</label>
          <table class="tbl">
            <tr v-for="(l, i) in form.lines" :key="i">
              <td style="width:110px"><select v-model="l.who" :disabled="!canEdit"><option v-for="c in cast.filter(x => x.kind === 'character')" :key="c.id" :value="c.id">{{ c.name || c.id }}</option></select></td>
              <td><input v-model="l.zh" :disabled="!canEdit" :placeholder="$t('台词')" style="width:100%" />
                <input v-model="l.tone" :disabled="!canEdit" :placeholder="$t('语气 / 音量 / 语速（例：认真、语速中等、音量正常、口齿清楚）')" style="width:100%;margin-top:4px;font-size:12px;color:var(--dim)" /></td>
              <td style="width:40px"><button v-if="canEdit" class="btn sm" @click="removeLine(i)">{{ $t('删') }}</button></td>
            </tr>
          </table>
          <button v-if="canEdit" class="btn sm" style="align-self:flex-start;margin-top:4px" @click="addLine">{{ $t('+ 加一句') }}</button>
        </div>
        <div class="field"><label>{{ $t('画面里必须可读的文字（观众要读的证据：手机屏、合同、榜单。不填 = 画面无文字）') }}</label>
          <table class="tbl" v-if="form.readable_text?.length">
            <tr v-for="(r, i) in form.readable_text" :key="i">
              <td style="width:150px"><input v-model="r.surface" :disabled="!canEdit" :placeholder="$t('载体（手机屏幕 / 合同签名栏）')" style="width:100%" /></td>
              <td><input v-model="r.text" :disabled="!canEdit" :placeholder="$t('文字')" style="width:100%" /></td>
              <td style="width:80px"><select v-model="r.language" :disabled="!canEdit"><option v-for="(label, key) in TEXT_LANGS" :key="key" :value="key">{{ $t(label) }}</option></select></td>
              <td style="width:130px"><input v-model="r.position" :disabled="!canEdit" :placeholder="$t('位置（可选）')" style="width:100%" /></td>
              <td style="width:40px"><button v-if="canEdit" class="btn sm" @click="removeText(i)">{{ $t('删') }}</button></td>
            </tr>
          </table>
          <div class="hint small">{{ $t('声明后首帧提示词只允许这几处文字，其余位置仍禁止文字；超过 24 字模型容易乱码，中文字形很多模型不稳，出图后核对，不行改走后期画面文字。') }}</div>
          <button v-if="canEdit" class="btn sm" style="align-self:flex-start;margin-top:4px" @click="addText">{{ $t('+ 加一处文字') }}</button>
        </div>
        <div class="field" data-test="withhold-editor"><label>{{ $t('先不揭示（观众此刻还不能知道的事实：写它、写会泄露它的载体、写从哪一镜起可以揭示。不填 = 没有要藏的）') }}</label>
          <table class="tbl" v-if="form.withhold?.length">
            <tr v-for="(w, i) in form.withhold" :key="i">
              <td><input v-model="w.fact" :disabled="!canEdit" :placeholder="$t('事实（凶手是陈默）')" style="width:100%" /></td>
              <td style="width:160px"><input v-model="w.carrier" :disabled="!canEdit" :placeholder="$t('载体（脸 / 手上的戒指 / 台词）')" style="width:100%" /></td>
              <td style="width:150px"><select v-model="w.until" :disabled="!canEdit"><option value="">{{ $t('（整集不揭示）') }}</option><option v-for="s in revealCandidates" :key="s.id" :value="s.id">{{ $t('{id} 起可揭示', { id: s.id }) }}</option></select></td>
              <td style="width:40px"><button v-if="canEdit" class="btn sm" @click="removeWithhold(i)">{{ $t('删') }}</button></td>
            </tr>
          </table>
          <div class="hint small">{{ $t('从这一镜起到揭示点之前，首帧和视频提示词都会多一句「不得揭示：…」；这只是提示词文本，不是保证，出片后要核对载体真的没露。正文写到了事实本身会告警。') }}</div>
          <button v-if="canEdit" class="btn sm" style="align-self:flex-start;margin-top:4px" @click="addWithhold">{{ $t('+ 加一条要藏的') }}</button>
        </div>
        <div class="field" data-test="roles-editor"><label>{{ $t('参考图用途（每张参考图说清取什么、控制什么、不得控制什么；按请求里的顺序编号进首帧提示词。不填 = 不说明）') }}</label>
          <table class="tbl" v-if="form.reference_roles?.length">
            <tr v-for="(r, i) in form.reference_roles" :key="i">
              <td style="width:170px;vertical-align:top"><select v-model="r.target" :disabled="!canEdit" style="width:100%"><option value="">{{ $t('（选参考图）') }}</option><option v-for="c in referenceTargets" :key="c.target" :value="c.target">{{ c.label }}</option></select></td>
              <td style="vertical-align:top"><input v-model="r.use" :disabled="!canEdit" :placeholder="$t('取什么（只取场景与光线 / 脸和发型）')" style="width:100%" />
                <div class="chips" style="margin-top:4px;align-items:center"><span class="small">{{ $t('控制') }}</span>
                  <span v-for="a in REFERENCE_ASPECTS" :key="a" class="chip" :style="r.control.includes(a) ? ACTIVE_CHIP : ''" @click="canEdit && toggleAspect(r.control, a)">{{ $t(a) }}</span>
                  <span v-for="a in r.control.filter(x => !REFERENCE_ASPECTS.includes(x))" :key="'c' + a" class="chip" :style="ACTIVE_CHIP" @click="canEdit && toggleAspect(r.control, a)">{{ a }}</span>
                  <input v-if="canEdit" :placeholder="$t('+ 自定义，回车')" style="width:110px;font-size:12px" @keydown.enter.prevent="addAspect(r.control, $event)" /></div>
                <div class="chips" style="margin-top:4px;align-items:center"><span class="small">{{ $t('不得控制') }}</span>
                  <span v-for="a in REFERENCE_ASPECTS" :key="a" class="chip" :style="r.ignore.includes(a) ? ACTIVE_CHIP : ''" @click="canEdit && toggleAspect(r.ignore, a)">{{ $t(a) }}</span>
                  <span v-for="a in r.ignore.filter(x => !REFERENCE_ASPECTS.includes(x))" :key="'i' + a" class="chip" :style="ACTIVE_CHIP" @click="canEdit && toggleAspect(r.ignore, a)">{{ a }}</span>
                  <input v-if="canEdit" :placeholder="$t('+ 自定义，回车')" style="width:110px;font-size:12px" @keydown.enter.prevent="addAspect(r.ignore, $event)" /></div>
                <div class="chips" style="margin-top:4px;align-items:center"><span class="small">{{ $t('相似度') }}</span>
                  <input v-model.number="r.fidelity" type="number" min="1" max="100" step="5" :disabled="!canEdit" :placeholder="$t('如 80')" style="width:70px;font-size:12px" data-test="role-fidelity" /><span class="small">%</span>
                  <span class="hint small">{{ $t('写了会进提示词：「与这张参考图的相似度约 80%」；留空不写') }}</span></div></td>
              <td style="width:40px;vertical-align:top"><button v-if="canEdit" class="btn sm" @click="removeRole(i)">{{ $t('删') }}</button></td>
            </tr>
          </table>
          <div class="hint small">{{ $t('说明只是提示词里的一句话，不是一致性保证；参考图的真实顺序：上一镜尾帧/首帧 → 角色定妆 → 道具。') }}</div>
          <button v-if="canEdit" class="btn sm" style="align-self:flex-start;margin-top:4px" :disabled="!referenceTargets.length" @click="addRole">{{ $t('+ 加一条用途') }}</button>
        </div>
        <div class="row" v-if="canEdit"><button class="btn primary sm" @click="save">{{ locked ? $t('保存并预告费用') : $t('保存这一镜') }}</button><span class="status" :class="{ err: status && !statusOk }">{{ status }}</span></div>
      </div>

      <aside class="ws-side">
        <section v-if="locked">
          <h4>{{ $t('操作') }}</h4>
          <div class="stack">
            <button class="btn spend" :disabled="!!store.gen" @click="gen('frames')">{{ cur.frame_url ? $t('重新生成首帧') : $t('生成首帧') }}</button>
            <button class="btn spend" :disabled="!!store.gen || !cur.frame_url" @click="gen('videos')">{{ cur.video_url ? $t('重新生成视频') : $t('生成视频') }}</button>
            <div class="hint" v-if="!cur.frame_url">{{ $t('先出首帧，再出视频。') }}</div>
          </div>
        </section>
        <section v-if="locked" data-test="compare-section">
          <h4>{{ $t('首帧对比试跑') }}</h4>
          <div class="hint">{{ $t('同一镜、同一套提示词和参考图，换几个模型各出一张看看。候选不进账本，只放在对比目录；满意的可以「采用」为这一镜的首帧（会记一次重拍并重新登记）。') }}</div>
          <div class="row" style="margin-top:6px">
            <input v-model="cmpModels" :placeholder="$t('模型 id，逗号分隔（同一来源）')" style="flex:1;min-width:160px;padding:6px 8px;border:1px solid var(--border);border-radius:var(--r-sm);background:var(--panel2);font-size:12px" />
            <button class="btn sm spend" :disabled="!!store.gen || !cmpModels.trim()" @click="runCompare">{{ $t('各出一张') }}</button>
          </div>
          <div v-if="cmpRows.length" class="stack" style="margin-top:8px;gap:8px">
            <div v-for="r in cmpRows" :key="r.file || r.model + r.created_at" class="row" style="align-items:flex-start">
              <img v-if="r.exists" :src="`/api/episodes/storyvideo/${store.projectId}/compare/frames/${cur.id}/file/${encodeURIComponent(r.file)}`" alt="" style="width:72px;aspect-ratio:9/16;object-fit:cover;border-radius:3px;border:1px solid var(--border)" />
              <div class="small" style="flex:1;min-width:0">
                <div class="mono" style="word-break:break-all">{{ r.model }}</div>
                <div v-if="r.error" class="bad">{{ r.error }}</div>
                <button v-else-if="r.exists && cur.frame_url" class="btn sm" style="margin-top:4px" @click="adoptCandidate(r)">{{ $t('采用为首帧') }}</button>
                <span v-else-if="r.exists" class="hint">{{ $t('先生成一次首帧后才能采用') }}</span>
              </div>
            </div>
          </div>
        </section>
        <section v-if="locked && cur.frame_url">
          <h4>{{ $t('审首帧') }} <QcBadge :decision="cur.frame_qc_decision" /></h4>
          <div class="row">
            <button class="btn sm chop" :disabled="cur.frame_qc_decision === 'pass'" @click="pass('frames')">{{ $t('通过') }}</button>
            <button class="btn sm chop" :disabled="cur.frame_qc_decision === 'retake'" @click="doRetake('frames')">{{ $t('重拍') }}</button>
          </div>
          <div class="hint" style="margin-top:4px">{{ $t('看：身份、构图、道具位置、有没有可读文字。') }}</div>
        </section>
        <section v-if="locked && cur.video_url">
          <h4>{{ $t('审视频') }} <QcBadge :decision="cur.video_qc_decision" /></h4>
          <div class="row">
            <button class="btn sm chop" :disabled="cur.video_qc_decision === 'pass'" @click="pass('videos')">{{ $t('通过') }}</button>
            <button class="btn sm chop" :disabled="cur.video_qc_decision === 'retake'" @click="doRetake('videos')">{{ $t('重拍') }}</button>
          </div>
          <div class="hint" style="margin-top:4px">{{ $t('看：动作、表情、台词念对没有、接缝。') }}</div>
        </section>
        <section>
          <h4>{{ $t('关联') }}</h4>
          <div class="small stack" style="gap:5px">
            <div>{{ $t('参考画面：') }}<router-link v-if="cur.ref_frame" class="link" :to="`/drama/p/${store.projectId}/shots/${cur.ref_frame}`">{{ cur.ref_frame }}</router-link><span v-else class="hint">{{ $t('独立首帧') }}</span></div>
            <div v-if="cur.ref_tail?.shot" data-test="tail-info">{{ $t('尾帧续接：') }}<router-link class="link" :to="`/drama/p/${store.projectId}/shots/${cur.ref_tail.shot}`">{{ cur.ref_tail.shot }}</router-link> <span class="hint">{{ $t(cur.ref_tail.mode === 'direct' ? '直接当首帧' : '作参考图') }}</span></div>
            <div>{{ $t('被参考：') }}<template v-if="cur.links.referenced_by.length"><router-link v-for="r in cur.links.referenced_by" :key="r" class="link" style="margin-right:6px" :to="`/drama/p/${store.projectId}/shots/${r}`">{{ r }}</router-link></template><span v-else class="hint">{{ $t('无') }}</span></div>
            <div>{{ $t('出场：') }}<router-link v-for="c in cur.cast" :key="c" class="link" style="margin-right:6px" :to="`/drama/p/${store.projectId}/cast/${castIdOf(c)}`">{{ cast.find(x => x.id === castIdOf(c))?.name || castIdOf(c) }}{{ c.includes('@') ? '@' + (cast.find(x => x.id === castIdOf(c))?.variants?.[c.split('@')[1]]?.label || c.split('@')[1]) : '' }}</router-link><span v-if="!cur.cast.length" class="hint">{{ $t('无') }}</span></div>
            <div>{{ $t('旁白：') }}<template v-if="cur.links.voice_over.length"><router-link class="link" :to="`/drama/p/${store.projectId}/post`">{{ $t('{n} 句', { n: cur.links.voice_over.length }) }}</router-link>{{ $t('（{s}）', { s: cur.links.voice_over.map(v => v.zh).join(' / ') }) }}</template><span v-else class="hint">{{ $t('无') }}</span></div>
            <div>{{ $t('后面接插卡：') }}<router-link v-if="cur.links.inserts_after.length" class="link" :to="`/drama/p/${store.projectId}/post`">{{ cur.links.inserts_after.join($t('、')) }}</router-link><span v-else class="hint">{{ $t('无') }}</span></div>
            <div>{{ $t('画面文字：') }}<router-link v-if="cur.links.notebook_text.length" class="link" :to="`/drama/p/${store.projectId}/post`">{{ $t('{n} 条', { n: cur.links.notebook_text.length }) }}</router-link><span v-else class="hint">{{ $t('无') }}</span></div>
          </div>
        </section>
        <section v-if="cur.budget">
          <h4>{{ $t('时长核算') }}</h4>
          <div class="small" :class="cur.budget.overflow ? 'bad' : cur.budget.tight ? 'warn' : ''">{{ $t('台词 {chars} 字 ≈ {sec}s / 声明 {dur}s', { chars: cur.budget.dialogue_chars, sec: cur.budget.speech_seconds, dur: cur.budget.declared_dur }) }}{{ cur.budget.overflow ? $t('，装不下') : cur.budget.tight ? $t('，偏紧（慢语速 ≈ {slow}s，窗口 {win}s）', { slow: cur.budget.speech_seconds_slow, win: cur.budget.window_seconds }) : '' }}</div>
        </section>
        <section>
          <h4>{{ $t('提示词（只读）') }}</h4>
          <details class="fold"><summary>{{ $t('首帧提示词') }}</summary><pre class="prompt">{{ cur.frame_prompt }}</pre></details>
          <details class="fold"><summary>{{ $t('负向提示词') }}</summary><pre class="prompt">{{ cur.neg_prompt }}</pre></details>
          <details class="fold"><summary>{{ $t('视频提示词') }}</summary><pre class="prompt">{{ cur.video_prompt }}</pre></details>
        </section>
      </aside>
    </template>
  </div>
</template>
