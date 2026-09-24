<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { store, refreshProject, withBusy, loadVoices, reviseEdit } from '../../store.js'
import { useRouter } from 'vue-router'
import { put, post, ep, pollTask, fileToBase64 } from '../../api.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import OverlayRegionsEditor from '../../components/OverlayRegionsEditor.vue'
import { START_ANCHOR, contentStamp, dubbingBlock, edgeBlock, insertIdProblem, insertLinesFromText, musicRows, subtitleBlock, subtitleFontOf, withGraphics } from '../../postlayers.js'

// 配音 / 字幕样式 / 配乐三块各自记「有没保存的改动」：refreshProject 每次都把 store.postLayers 换成新对象——保存任何一块、
// 绑旁白音色、生成期间每 8 秒一次都会刷——有改动的块不能被服务端的值冲掉；保存成功（已落盘）后才清掉标记跟着服务端走
const dubDirty = ref(false)
const subDirty = ref(false)
const musicDirty = ref(false)

const voices = computed(() => store.voices || [])
const narrId = ref('')
onMounted(async () => { if (!store.voices) await loadVoices(); narrId.value = store.postLayers?.voice_over?.voice_ref || ''; syncDub() })
watch(() => store.postLayers, () => { if (!dubDirty.value) syncDub() })
async function bindNarration() {
  setStatus(t('绑定中…'))
  try {
    const r = await post(`${ep(store.projectId)}/voice-over/voice`, { voice_id: narrId.value || null })
    await refreshProject(); setStatus(r.status === 'amended' ? t('已绑定（制作中的项目：后期块修订已记入）') : narrId.value ? t('已绑定') : t('已解绑'))
  } catch (e) { setStatus(e.message, true) }
}

const pl = computed(() => store.postLayers)
const shotIds = computed(() => pl.value?.shot_ids || [])
const vo = computed(() => pl.value?.voice_over || {})
const pg = computed(() => pl.value?.post_graphics || {})
const lines = computed(() => Array.isArray(vo.value.lines) ? vo.value.lines : [])
const inserts = computed(() => Array.isArray(pg.value.phone_ui_inserts) ? pg.value.phone_ui_inserts : [])
const notes = computed(() => Array.isArray(pg.value.notebook_text) ? pg.value.notebook_text : [])
const r = computed(() => pl.value?.readiness || {})
const status = ref('')
const statusErr = ref(false)   // 状态文案是否是错误（不再靠「已」开头判断，英文下也成立）
function setStatus(text, err = false) { status.value = text; statusErr.value = err }
const sep = () => t('、')   // 列表连接符：中文顿号，英文逗号

// ---- 台词配音（TTS 兜底）：post_graphics.dubbing = { mode: off|all|shots, shots: [...], duck: 0..1 }
const dub = reactive({ mode: 'off', shots: [], duck: 0.15 })
const dubPlan = computed(() => r.value.dubbing || { shots: [], missing_tts_cast: {}, lip_risk: [] })
const dubCast = computed(() => (store.cast || []).filter(c => c.kind === 'character'))
function syncDub() {
  const d = pg.value.dubbing || {}
  Object.assign(dub, { mode: d.mode || 'off', shots: [...(d.shots || [])], duck: typeof d.duck === 'number' ? d.duck : 0.15 })
}
function toggleDubShot(id) { const i = dub.shots.indexOf(id); if (i >= 0) dub.shots.splice(i, 1); else dub.shots.push(id); dubDirty.value = true }
// 保存时一律在原对象上覆盖界面认识的字段（postlayers.js）：silence、labels 这类界面还没做编辑的键不会被冲掉
async function saveDub() {
  return saveBlocks({ post_graphics: withGraphics(pg.value, 'dubbing', dubbingBlock(pg.value.dubbing, dub)) }, () => { dubDirty.value = false })
}
const router = useRouter()
// 配音镜一键切"画外"说话方式：speech_mode 进视频提示词——草稿直接写；制作中走「保存并预告费用」
async function offscreenAll() {
  const risky = dubPlan.value.lip_risk || []
  let msg = t('把配音镜的说话方式改成"画外"（{who}）？\n\n视频提示词会改成"画外的 X 说……"，模型不再要求嘴型对上台词。', { who: risky.length ? risky.join(sep()) : t('所有配音镜') })
  if (store.detail?.locked) msg += '\n\n' + t('项目已开始制作：会先预告哪些镜要重出视频，再保存为新版本。')
  if (!await confirm(msg, { confirmLabel: t('改成画外') })) return
  if (store.detail?.locked) {
    const r = await reviseEdit({ kind: 'dubbing_offscreen' })
    if (r) router.push(`/drama/p/${r.id}/post`)
    return
  }
  setStatus(t('修改中…'))
  try {
    const r = await post(`${ep(store.projectId)}/dubbing/offscreen`, {})
    await refreshProject(); setStatus(r.changed.length ? t('已把 {shots} 改成画外说话方式', { shots: r.changed.join(sep()) }) : r.note)
  } catch (e) { setStatus(e.message, true) }
}
// ---- 字幕样式（spec.subtitle，后期块）：够做视频字幕就行——字体、字号、颜色、描边、位置、底色带
const SUB_KEYS = ['language', 'position', 'display', 'font_path', 'chinese_font_size', 'english_font_size', 'chinese_color', 'english_color', 'outline_color', 'outline_width', 'band_alpha', 'safe_bottom_percent']
const sub = reactive({ language: 'zh', position: 'bottom', display: 'speech', font_path: '', chinese_font_size: 46, english_font_size: 64,
  chinese_color: '#FFFFFF', english_color: '#FFD84D', outline_color: '#111111', outline_width: 4, band_alpha: 0.22, safe_bottom_percent: 7 })
const fonts = computed(() => pl.value?.fonts_available || [])
function hexOf(v, fallback) { const m = String(v || fallback).trim().match(/([0-9a-fA-F]{6})$/); return '#' + (m ? m[1] : fallback.slice(-6)).toUpperCase() }
function syncSub() {
  const e = pl.value?.subtitle_effective || {}
  for (const k of SUB_KEYS) if (e[k] !== undefined) sub[k] = e[k]
  sub.font_path = subtitleFontOf(e)   // 老 spec 写的是别名 fontfile：显示实际在用的那款，保存时换成 font_path
  sub.chinese_color = hexOf(e.chinese_color, '#FFFFFF'); sub.english_color = hexOf(e.english_color, '#FFD84D'); sub.outline_color = hexOf(e.outline_color, '#111111')
}
watch(() => pl.value, () => { if (!subDirty.value) syncSub() }, { immediate: true })
// 预览：按 1080×1920 等比缩到 300px 宽，只画有字幕的那一段画面（底部或顶部 200px）
const PREVIEW_SCALE = 300 / 1080
const previewStyle = computed(() => {
  const size = Number(sub.chinese_font_size || 46) * PREVIEW_SCALE
  const stroke = Number(sub.outline_width || 0) * PREVIEW_SCALE
  const margin = Math.round(1920 * Number(sub.safe_bottom_percent || 0) / 100 * PREVIEW_SCALE)
  const fontName = (fonts.value.find(f => f.path === sub.font_path) || {}).label || ''
  return {
    text: { color: sub.chinese_color, fontSize: `${size.toFixed(1)}px`, WebkitTextStroke: stroke ? `${stroke.toFixed(1)}px ${sub.outline_color}` : '0', paintOrder: 'stroke fill',
      fontFamily: fontName ? `"${fontName}", var(--ui)` : 'var(--ui)', fontWeight: 600, lineHeight: 1.25 },
    band: { background: `rgba(0,0,0,${Number(sub.band_alpha || 0)})` },
    block: sub.position === 'top' ? { top: `${Math.round(1920 * 0.12 * PREVIEW_SCALE)}px` } : { bottom: `${margin}px` },
    en: { color: sub.english_color, fontSize: `${(Number(sub.english_font_size || 64) * PREVIEW_SCALE).toFixed(1)}px`, WebkitTextStroke: stroke ? `${(stroke * 1.2).toFixed(1)}px ${sub.outline_color}` : '0', paintOrder: 'stroke fill', fontWeight: 700 },
  }
})
async function saveSub() {
  const next = subtitleBlock(pl.value?.subtitle, sub, SUB_KEYS, ['chinese_font_size', 'english_font_size', 'outline_width', 'band_alpha', 'safe_bottom_percent'])
  return saveBlocks({ subtitle: next }, () => { subDirty.value = false })
}
function resetSub() { const d = pl.value?.subtitle_defaults || {}; for (const k of SUB_KEYS) if (d[k] !== undefined) sub[k] = d[k]; sub.chinese_color = hexOf(d.chinese_color, '#FFFFFF'); sub.english_color = hexOf(d.english_color, '#FFD84D'); sub.outline_color = hexOf(d.outline_color, '#111111'); sub.font_path = ''; subDirty.value = true }

// ---- 交付面遮挡区（DESIGN §27）：delivery.overlay_preset / overlay_regions，只改这两个键，宽高帧率不动
function saveOverlays(patch) { return saveBlocks({ delivery: patch }) }

// ---- 配乐（时间线层）：post_graphics.music = [{label, file, start, end, gain, duck, fade_in, fade_out}]
const music = ref([])
function syncMusic() { music.value = (pg.value.music || []).map(m => ({ label: '', file: '', start: 0, end: null, gain: 0.6, duck: 0.3, fade_in: 1, fade_out: 1.5, ...m })) }
watch(() => pl.value, () => { if (!musicDirty.value) syncMusic() }, { immediate: true })
const musicStatus = computed(() => r.value.music || [])
function musicUrl(file) { const name = String(file || '').split('/').pop(); return `/api/episodes/storyvideo/${store.projectId}/music/${encodeURIComponent(name)}` }
async function uploadMusic(idx, ev) {
  const file = ev.target.files?.[0]; if (!file) return
  setStatus(t('上传中…'))
  try {
    const res = await post(`${ep(store.projectId)}/music`, { filename: file.name, data_base64: await fileToBase64(file) })
    music.value[idx].file = res.file
    if (!music.value[idx].label) music.value[idx].label = file.name.replace(/\.[^.]+$/, '')
    musicDirty.value = true
    setStatus(t('已上传，记得点「保存配乐」'))
  } catch (e) { setStatus(e.message, true) }
  ev.target.value = ''
}
function addMusic() { music.value.push({ label: '', file: '', start: 0, end: null, gain: 0.6, duck: 0.3, fade_in: 1, fade_out: 1.5 }); musicDirty.value = true }
function removeMusic(i) { music.value.splice(i, 1); musicDirty.value = true }
async function saveMusic() {
  return saveBlocks({ post_graphics: withGraphics(pg.value, 'music', musicRows(music.value)) }, () => { musicDirty.value = false })
}

const edge = reactive({ voice: '', rate: '', pitch: '' })
const editing = ref(null)   // { kind: 'vo'|'ins'|'note', idx, data }

function startEdge() { Object.assign(edge, { voice: vo.value.edge?.voice || '', rate: vo.value.edge?.rate || '', pitch: vo.value.edge?.pitch || '' }); editing.value = { kind: 'edge' } }
function startEdit(kind, idx) {
  const list = kind === 'vo' ? lines.value : kind === 'ins' ? inserts.value : notes.value
  const tpl = kind === 'vo' ? { shot: shotIds.value[0], at: 0.5, zh: '' }
    : kind === 'ins' ? { id: `UI_${inserts.value.length + 1}`, after: shotIds.value[0], dur: 3, layout: 'notification', app: '', title: t('通知'), time: '09:00', items: [t('第一行'), t('第二行')] }
    : { shot: shotIds.value[0], at: 0.4, text: '', y: 0.56 }
  editing.value = { kind, idx, data: JSON.parse(JSON.stringify(idx != null ? list[idx] : tpl)) }
}
function itemsText(data) { return (data.items || []).join('\n') }
// 插卡文字框：显示用户原样敲的文本（不然每敲一下都按 lines 重排，行尾的回车、字幕卡中间的空行会被当场吃掉），
// lines 按版式规则从它算出来；保存时再按最终版式算一次（中途换了版式也对）
function setInsertLines(e, text) { e.linesText = text; e.data.lines = insertLinesFromText(text, e.data.layout) }
function setItems(data, text) { data.items = text.split('\n').map(s => s.trim()).filter(Boolean) }

// onSaved：PUT 成功、刷新之前跑——这一块的「有改动」标记要在刷新把 store.postLayers 换掉之前清掉，服务端的值才会灌回表单；
// PUT 失败就不清，表单里的改动留着
async function saveBlocks(patch, onSaved) {
  setStatus(t('保存中…'))
  try {
    const res = await put(`${ep(store.projectId)}/post-layers`, patch)
    onSaved?.()
    await refreshProject()
    setStatus(res.status === 'amended' ? t('已保存（制作中的项目：后期块修订已记入）') : res.status === 'unchanged' ? t('没有变化') : t('已保存'))
    editing.value = null
  } catch (e) { setStatus(e.message, true) }
}
async function commit() {
  const e = editing.value
  if (e.kind === 'edge') {
    return saveBlocks({ voice_over: { ...vo.value, edge: edgeBlock(vo.value.edge, edge) } })
  }
  if (e.kind === 'vo') {
    const next = [...lines.value]; if (e.idx != null) next[e.idx] = e.data; else next.push(e.data)
    return saveBlocks({ voice_over: { ...vo.value, lines: next } })
  }
  if (e.kind === 'ins') {
    if (e.linesText != null) e.data.lines = insertLinesFromText(e.linesText, e.data.layout)
    const next = [...inserts.value]; if (e.idx != null) next[e.idx] = e.data; else next.push(e.data)
    return saveBlocks({ post_graphics: { ...pg.value, phone_ui_inserts: next } })
  }
  const next = [...notes.value]; if (e.idx != null) next[e.idx] = e.data; else next.push(e.data)
  return saveBlocks({ post_graphics: { ...pg.value, notebook_text: next } })
}
async function remove(kind, idx) {
  if (!await confirm(t('删除这一条？'), { confirmLabel: t('删除'), danger: true })) return
  if (kind === 'vo') return saveBlocks({ voice_over: { ...vo.value, lines: lines.value.filter((_, i) => i !== idx) } })
  if (kind === 'ins') return saveBlocks({ post_graphics: { ...pg.value, phone_ui_inserts: inserts.value.filter((_, i) => i !== idx) } })
  return saveBlocks({ post_graphics: { ...pg.value, notebook_text: notes.value.filter((_, i) => i !== idx) } })
}

// ---- 本地任务
async function runAsr() {
  const ok = await confirm(t('台词识别：用本机的 faster-whisper 逐镜识别语音，跟台词比对。字错率 ≤0.3 通过，≤0.45 待复核，其余自动记「重拍」。第一次运行要下载识别模型（约 500MB）。不花钱。'), { confirmLabel: t('开始识别') })
  if (!ok) return
  await runLocal('asr-qc', { record_retakes: !!store.detail?.locked, include_stray: false }, t('识别中'))
}
async function runPost() {
  const ok = await confirm(t('生成后期合成版：旁白 TTS + 整屏插卡 + 画面文字 + 字幕 + 混音，直接从各镜视频合成。要求所有镜都已出视频。旁白用「模型设置」里的语音模型（没配就用免费的 Edge TTS）。本地 ffmpeg，不花钱。'), { confirmLabel: t('开始合成') })
  if (!ok) return
  await runLocal('post', {}, t('合成中'))
}
async function runLocal(path, body, label) {
  let created
  try { created = await post(`${ep(store.projectId)}/${path}`, body) } catch (e) { await notice(e.message); return }
  store.gen = { text: `${label}…` }
  try {
    const task = await pollTask(created.task_id, (_t, s) => { store.gen = { text: t('{label}…已经 {s} 秒（可以切走）', { label, s }) } })
    if (task.status === 'failed') { await notice(t('{label}失败：', { label }) + '\n\n' + task.error) }
    else if (path === 'asr-qc') {
      const r = task.result
      const msg = [t('通过 {pass} · 待复核 {review} · 不符 {fail} · 无台词镜有语音 {stray} · 无视频 {missing}', { pass: r.summary.pass, review: r.summary.review || 0, fail: r.summary.fail, stray: r.summary.stray, missing: r.summary.missing_video })]
      if (r.recorded_retakes.length) msg.push(t('已自动记重拍：{shots}', { shots: r.recorded_retakes.join(sep()) }))
      await notice(t('台词识别完成') + '\n\n' + msg.join('\n'))
    } else toast(t('后期合成完成，去「成片」看'))
  } catch (e) { await notice(t('{label}失败：', { label }) + '\n\n' + e.message)   // 服务重启 / 任务记录没了：轮询抛错也要有一句提示
  } finally { store.gen = null; await refreshProject().catch(() => {}) }   // 服务还没起来时刷新也会抛，别把上面的提示盖掉
}
</script>

<template>
  <div class="ws-content">
    <div class="ws-center" style="max-width:820px">
      <div class="row between" style="margin-bottom:8px">
        <h3 style="font-size:16px">{{ $t('旁白与插卡') }}</h3>
        <span class="status" :class="{ err: statusErr }">{{ status }}</span>
      </div>
      <p class="hint" style="margin-bottom:10px">{{ $t('这三块不进付费请求，随时能改（制作中也行）；改完在「成片」页或下面重新点「生成后期合成版」。') }}</p>
      <div class="note info" style="margin-bottom:12px">
        {{ r.ffmpeg_ok ? '✓ ffmpeg' : $t('✗ ffmpeg 未找到') }}　{{ r.fonts_ok ? $t('✓ 中文字体') : $t('✗ 缺字体') }}　{{ r.missing_videos?.length ? $t('✗ 还差 {n}/{total} 镜视频', { n: r.missing_videos.length, total: r.videos_total }) : $t('✓ {total} 镜视频齐了', { total: r.videos_total }) }}　{{ r.asr_available ? $t('✓ 台词识别可用') : $t('· 台词识别未安装（可选）') }}
        <span class="row" style="margin-left:auto;display:inline-flex">
          <button class="btn sm" :disabled="!!store.gen" @click="runAsr">{{ $t('台词识别') }}</button>
          <button class="btn sm" :disabled="!!store.gen" @click="runPost">{{ $t('生成后期合成版') }}</button>
        </span>
      </div>

      <div class="card">
        <div class="row between"><h3>{{ $t('旁白') }}</h3><div class="row"><button class="btn sm" @click="startEdge">{{ $t('声线') }}</button><button class="btn sm" @click="startEdit('vo', null)">{{ $t('+ 加一句') }}</button></div></div>
        <div class="row" style="margin-bottom:6px">
          <span class="hint">{{ $t('声线：') }}{{ vo.voice_ref ? $t('音色库「{name}」', { name: voices.find(v => v.id === vo.voice_ref)?.name || vo.voice_ref }) + ' · ' : '' }}{{ vo.edge?.voice || $t('（跟随模型设置的语音角色）') }}{{ vo.edge?.rate ? ' · ' + $t('语速') + ' ' + vo.edge.rate : '' }}{{ vo.edge?.pitch ? ' · ' + $t('音调') + ' ' + vo.edge.pitch : '' }}</span>
          <select v-model="narrId" style="padding:5px 8px;border:1px solid var(--border);border-radius:var(--r-sm);background:var(--panel2);font-size:12px">
            <option value="">{{ $t('（从音色库选…）') }}</option>
            <option v-for="v in voices.filter(v => v.voice)" :key="v.id" :value="v.id">{{ v.name }} · {{ v.voice }}</option>
          </select>
          <button class="btn sm" :disabled="narrId === (vo.voice_ref || '')" @click="bindNarration">{{ narrId ? $t('用这个音色') : $t('解绑') }}</button>
        </div>
        <table class="tbl">
          <tr v-for="(l, i) in lines" :key="i">
            <td class="mono" style="width:120px"><router-link class="link" :to="`/drama/p/${store.projectId}/shots/${l.shot}`">{{ l.shot }}</router-link> @{{ Number(l.at || 0).toFixed(1) }}s</td>
            <td>{{ l.zh }}</td>
            <td style="width:80px;white-space:nowrap"><button class="btn sm" @click="startEdit('vo', i)">{{ $t('改') }}</button> <button class="btn sm" @click="remove('vo', i)">{{ $t('删') }}</button></td>
          </tr>
          <tr v-if="!lines.length"><td colspan="3" class="hint">{{ $t('没有旁白') }}</td></tr>
        </table>
      </div>
      <div class="card" @input="dubDirty = true" @change="dubDirty = true">
        <div class="row between"><h3>{{ $t('台词配音（TTS 兜底）') }}</h3><button class="btn sm primary" @click="saveDub">{{ $t('保存配音设置') }}</button></div>
        <div class="hint" style="margin-bottom:8px">{{ $t('视频模型直出的台词音色每镜都可能漂。开启后，这些镜的台词改用说话角色绑定的 TTS 音色逐句配音，混进后期合成版，原声压低；音色由此稳定。嘴型对不上的风险靠镜头写法解决：配音镜用"画外"说话方式或不露嘴的机位。') }}</div>
        <div class="grid2">
          <div class="field"><label>{{ $t('模式') }}</label>
            <select v-model="dub.mode"><option value="off">{{ $t('关') }}</option><option value="all">{{ $t('所有有台词的镜') }}</option><option value="shots">{{ $t('只配指定的镜') }}</option></select></div>
          <div class="field"><label>{{ $t('配音时原声保留比例（0 = 静音，1 = 不压）') }}</label><input v-model.number="dub.duck" type="number" step="0.05" min="0" max="1" /></div>
        </div>
        <div v-if="dub.mode === 'shots'" class="field"><label>{{ $t('要配音的镜') }}</label>
          <div class="chips"><span v-for="s in shotIds" :key="s" class="chip" :style="dub.shots.includes(s) ? 'background:var(--accent-bg);border-color:var(--accent);color:var(--accent-strong)' : ''" @click="toggleDubShot(s)">{{ dub.shots.includes(s) ? '✓ ' : '' }}{{ s }}</span></div>
        </div>
        <div v-if="dubPlan.shots.length" class="small stack" style="gap:4px;margin-top:4px">
          <div>{{ $t('当前会配音：') }}<span class="mono">{{ dubPlan.shots.join($t('、')) }}</span></div>
          <div v-if="Object.keys(dubPlan.missing_tts_cast).length" class="bad">{{ $t('✗ 这些说话角色还没绑定 TTS 音色：') }}
            <template v-for="(sids, who) in dubPlan.missing_tts_cast" :key="who"><router-link class="link" :to="`/drama/p/${store.projectId}/cast/${who}`">{{ who }}</router-link>{{ $t('（{v}）', { v: sids.join($t('、')) }) }}　</template>
            {{ $t('——后期合成会被拦下。') }}</div>
          <div v-else class="ok">{{ $t('✓ 所有说话角色都有 TTS 音色') }}</div>
          <div v-if="dubPlan.lip_risk.length" class="warn">{{ $t('⚠ 嘴型风险：{shots} 是近景/特写且不是画外说话——观众会看到嘴型和声音对不上。', { shots: dubPlan.lip_risk.join($t('、')) }) }}
            <button class="btn sm" style="margin-left:6px" @click="offscreenAll">{{ $t('一键改成画外说话方式') }}</button></div>
          <div class="hint">{{ $t('角色的 TTS 音色：') }}<template v-for="c in dubCast" :key="c.id"><router-link class="link" :to="`/drama/p/${store.projectId}/cast/${c.id}`">{{ c.name || c.id }}</router-link>{{ c.voice_tts?.voice ? $t('（{v}）', { v: c.voice_tts.voice }) : $t('（未绑定）') }}　</template></div>
        </div>
      </div>
      <div class="card" data-test="music-card" @input="musicDirty = true" @change="musicDirty = true">
        <div class="row between"><h3>{{ $t('配乐') }}</h3><div class="row"><button class="btn sm" @click="addMusic">{{ $t('+ 加一段') }}</button><button class="btn sm primary" @click="saveMusic">{{ $t('保存配乐') }}</button></div></div>
        <div class="hint" style="margin-bottom:6px">{{ $t('本地音频按时间铺进后期合成版：有台词、旁白、配音的时段自动压低，结尾按成片长度截断。单镜视频本身不带配乐。') }}</div>
        <div v-if="!music.length" class="hint">{{ $t('没有配乐') }}</div>
        <div v-for="(m, i) in music" :key="i" class="card small" style="margin:6px 0;padding:10px 12px">
          <div class="grid2">
            <div class="field"><label>{{ $t('名字') }}</label><input v-model="m.label" /></div>
            <div class="field"><label>{{ $t('音频文件') }}</label>
              <div class="row"><input type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac,.aiff,.aif" @change="uploadMusic(i, $event)" style="font-size:12px" /><span class="hint mono" v-if="m.file">{{ m.file.split('/').pop() }}</span></div>
              <audio v-if="m.file" :src="musicUrl(m.file)" controls preload="none" style="width:100%;height:28px;margin-top:4px"></audio>
              <!-- 用不了的原因（后端 readiness.music[].reason）：路径不在数据目录里的也要说清，不能一律说「文件不存在」 -->
              <div v-if="musicStatus[i]?.missing" class="bad small" data-test="music-missing">{{ musicStatus[i]?.reason && musicStatus[i].reason !== '文件不存在' && musicStatus[i].reason !== 'file not found' ? $t('用不了：{reason}——重新上传', { reason: musicStatus[i].reason }) : $t('文件不存在，重新上传') }}</div>
            </div>
            <div class="field"><label>{{ $t('从第几秒开始') }}</label><input v-model.number="m.start" type="number" min="0" step="0.5" /></div>
            <div class="field"><label>{{ $t('到第几秒结束（留空 = 到结尾）') }}</label><input v-model.number="m.end" type="number" min="0" step="0.5" /></div>
            <div class="field"><label>{{ $t('音量（0–2，1 = 原音量）') }}</label><input v-model.number="m.gain" type="number" min="0" max="2" step="0.05" /></div>
            <div class="field"><label>{{ $t('有人说话时压到（0–1）') }}</label><input v-model.number="m.duck" type="number" min="0" max="1" step="0.05" /></div>
            <div class="field"><label>{{ $t('淡入（秒）') }}</label><input v-model.number="m.fade_in" type="number" min="0" step="0.5" /></div>
            <div class="field"><label>{{ $t('淡出（秒）') }}</label><input v-model.number="m.fade_out" type="number" min="0" step="0.5" /></div>
          </div>
          <button class="btn sm" @click="removeMusic(i)">{{ $t('删') }}</button>
        </div>
      </div>
      <div class="card" data-test="subtitle-card" @input="subDirty = true" @change="subDirty = true">
        <div class="row between"><h3>{{ $t('字幕样式') }}</h3><div class="row"><button class="btn sm" @click="resetSub">{{ $t('恢复默认') }}</button><button class="btn sm primary" @click="saveSub">{{ $t('保存字幕样式') }}</button></div></div>
        <div class="hint" style="margin-bottom:8px">{{ $t('烧进成片和后期合成版的台词字幕。改完重新点「生成成片」或「生成后期合成版」才会生效。') }}</div>
        <div class="row" style="align-items:flex-start;gap:16px">
          <div style="flex:1;min-width:260px">
            <div class="grid2">
              <div class="field"><label>{{ $t('语言') }}</label><select v-model="sub.language"><option value="zh">{{ $t('中文') }}</option><option value="en">{{ $t('英文') }}</option><option value="bilingual">{{ $t('中英双语') }}</option></select></div>
              <div class="field"><label>{{ $t('位置') }}</label><select v-model="sub.position"><option value="bottom">{{ $t('底部') }}</option><option value="top">{{ $t('顶部') }}</option></select></div>
              <div class="field span2"><label>{{ $t('字体') }}</label>
                <select v-model="sub.font_path"><option value="">{{ $t('自动（系统中文字体）') }}</option><option v-for="f in fonts" :key="f.path" :value="f.path">{{ f.label }}</option><option v-if="sub.font_path && !fonts.some(f => f.path === sub.font_path)" :value="sub.font_path">{{ sub.font_path }}</option></select></div>
              <div class="field"><label>{{ $t('中文字号（以 1080 宽为基准）') }}</label><input v-model.number="sub.chinese_font_size" type="number" min="12" max="300" /></div>
              <div class="field" v-if="sub.language !== 'zh'"><label>{{ $t('英文字号') }}</label><input v-model.number="sub.english_font_size" type="number" min="12" max="300" /></div>
              <div class="field"><label>{{ $t('文字颜色') }}</label><input v-model="sub.chinese_color" type="color" /></div>
              <div class="field" v-if="sub.language !== 'zh'"><label>{{ $t('英文颜色') }}</label><input v-model="sub.english_color" type="color" /></div>
              <div class="field"><label>{{ $t('描边颜色') }}</label><input v-model="sub.outline_color" type="color" /></div>
              <div class="field"><label>{{ $t('描边粗细（0 = 不描边）') }}</label><input v-model.number="sub.outline_width" type="number" min="0" max="20" /></div>
              <div class="field"><label>{{ $t('底色带透明度（0 = 无底色）') }}</label><input v-model.number="sub.band_alpha" type="number" min="0" max="1" step="0.05" /></div>
              <div class="field"><label>{{ $t('底部安全边距（画面高度 %）') }}</label><input v-model.number="sub.safe_bottom_percent" type="number" min="0" max="40" /></div>
              <div class="field span2"><label>{{ $t('显示方式') }}</label><select v-model="sub.display"><option value="speech">{{ $t('只在说话时段显示') }}</option><option value="full_shot">{{ $t('整镜显示') }}</option></select></div>
            </div>
          </div>
          <div data-test="subtitle-preview" style="width:300px;flex-shrink:0">
            <div class="hint" style="margin-bottom:4px">{{ $t('预览（画面{where}那一段）', { where: sub.position === 'top' ? $t('顶部') : $t('底部') }) }}</div>
            <div style="position:relative;width:300px;height:200px;border-radius:6px;overflow:hidden;background:linear-gradient(160deg,#3b3a45,#1b1a20 60%,#0d0c10)">
              <div style="position:absolute;left:0;right:0;display:flex;flex-direction:column;align-items:center;gap:2px;padding:4px 8px" :style="{ ...previewStyle.block, ...previewStyle.band }">
                <span v-if="sub.language !== 'zh'" :style="previewStyle.en">Why are you so late?</span>
                <span v-if="sub.language !== 'en'" :style="previewStyle.text">你怎么才来？</span>
              </div>
            </div>
          </div>
        </div>
      </div>
      <OverlayRegionsEditor :overlays="pl?.overlays" @save="saveOverlays" />
      <div class="card">
        <div class="row between"><h3>{{ $t('整屏插卡') }}</h3><button class="btn sm" @click="startEdit('ins', null)">{{ $t('+ 加一张') }}</button></div>
        <div class="hint" style="margin-bottom:6px">{{ $t('手机通知 / 短信 / 商品页样式的整屏画面，插在某一镜之后。') }}</div>
        <table class="tbl">
          <tr v-for="(x, i) in inserts" :key="i">
            <td class="mono" style="width:120px">{{ x.id }}<br><img :src="`/api/episodes/storyvideo/${store.projectId}/post-layers/insert-preview/${encodeURIComponent(x.id)}?v=${contentStamp([x, pg.fonts?.ui])}`" alt="" style="width:54px;aspect-ratio:9/16;object-fit:cover;border-radius:3px;border:1px solid var(--border);margin-top:4px;background:#111" @error="$event.target.style.display='none'" /></td>
            <td><span v-if="x.after === START_ANCHOR" data-test="insert-at-start">{{ $t('片头（第一镜之前）') }}</span><router-link v-else class="link" :to="`/drama/p/${store.projectId}/shots/${x.after}`">{{ $t('{shot} 之后', { shot: x.after }) }}</router-link> · {{ x.dur || 4 }}s · {{ x.layout || 'plain' }} · {{ x.title || x.app || x.sender || (x.lines || []).join(' / ') || x.screen || '' }}</td>
            <td style="width:80px;white-space:nowrap"><button class="btn sm" @click="startEdit('ins', i)">{{ $t('改') }}</button> <button class="btn sm" @click="remove('ins', i)">{{ $t('删') }}</button></td>
          </tr>
          <tr v-if="!inserts.length"><td colspan="3" class="hint">{{ $t('没有插卡') }}</td></tr>
        </table>
      </div>
      <div class="card">
        <div class="row between"><h3>{{ $t('画面文字') }}</h3><button class="btn sm" @click="startEdit('note', null)">{{ $t('+ 加一条') }}</button></div>
        <div class="hint" style="margin-bottom:6px">{{ $t('叠在某一镜画面上的手写风文字。') }}</div>
        <table class="tbl">
          <tr v-for="(n, i) in notes" :key="i">
            <td class="mono" style="width:120px"><router-link class="link" :to="`/drama/p/${store.projectId}/shots/${n.shot}`">{{ n.shot }}</router-link> @{{ Number(n.at || 0.4).toFixed(1) }}s</td>
            <td>{{ n.text }}</td>
            <td style="width:80px;white-space:nowrap"><button class="btn sm" @click="startEdit('note', i)">{{ $t('改') }}</button> <button class="btn sm" @click="remove('note', i)">{{ $t('删') }}</button></td>
          </tr>
          <tr v-if="!notes.length"><td colspan="3" class="hint">{{ $t('没有画面文字') }}</td></tr>
        </table>
      </div>
    </div>

    <aside class="ws-side" v-if="editing">
      <template v-if="editing.kind === 'edge'">
        <h4>{{ $t('旁白声线') }}</h4>
        <div class="field"><label>{{ $t('音色（Edge TTS 音色名，如 zh-CN-YunxiNeural；留空跟随模型设置）') }}</label><input v-model="edge.voice" /></div>
        <div class="field"><label>{{ $t('语速（如 -4%）') }}</label><input v-model="edge.rate" /></div>
        <div class="field"><label>{{ $t('音调（如 -6Hz）') }}</label><input v-model="edge.pitch" /></div>
      </template>
      <template v-else-if="editing.kind === 'vo'">
        <h4>{{ editing.idx != null ? $t('改旁白') : $t('加一句旁白') }}</h4>
        <div class="field"><label>{{ $t('挂在哪一镜') }}</label><select v-model="editing.data.shot"><option v-for="s in shotIds" :key="s" :value="s">{{ s }}</option></select></div>
        <div class="field"><label>{{ $t('从这镜第几秒开始') }}</label><input v-model.number="editing.data.at" type="number" step="0.1" /></div>
        <div class="field"><label>{{ $t('旁白文本') }}</label><textarea v-model="editing.data.zh" rows="3"></textarea></div>
      </template>
      <template v-else-if="editing.kind === 'ins'">
        <h4>{{ editing.idx != null ? $t('改插卡') : $t('加一张插卡') }}</h4>
        <div class="field"><label>id</label><input v-model="editing.data.id" />
          <div v-if="insertIdProblem(editing.data.id)" class="hint" style="color:var(--bad)" data-test="insert-id-problem">{{ $t(insertIdProblem(editing.data.id)) }}</div></div>
        <div class="field"><label>{{ $t('插在哪一镜之后') }}</label><select v-model="editing.data.after"><option :value="START_ANCHOR">{{ $t('片头（第一镜之前）') }}</option><option v-for="s in shotIds" :key="s" :value="s">{{ s }}</option><option v-for="x in inserts.filter(x => x.id !== editing.data.id)" :key="x.id" :value="x.id">{{ $t('插卡 {id}', { id: x.id }) }}</option></select></div>
        <div class="grid2">
          <div class="field"><label>{{ $t('时长（秒）') }}</label><input v-model.number="editing.data.dur" type="number" /></div>
          <div class="field"><label>{{ $t('版式') }}</label><select v-model="editing.data.layout"><option value="notification">{{ $t('通知') }}</option><option value="sms">{{ $t('短信') }}</option><option value="shop">{{ $t('商品页') }}</option><option value="plain">{{ $t('纯文字') }}</option><option value="card">{{ $t('纯色字幕卡') }}</option></select></div>
        </div>
        <template v-if="editing.data.layout === 'notification'">
          <div class="field"><label>{{ $t('应用名') }}</label><input v-model="editing.data.app" /></div>
          <div class="field"><label>{{ $t('标题') }}</label><input v-model="editing.data.title" /></div>
          <div class="field"><label>{{ $t('时间') }}</label><input v-model="editing.data.time" /></div>
          <div class="field"><label>{{ $t('通知内容（每行一条）') }}</label><textarea :value="itemsText(editing.data)" @input="setItems(editing.data, $event.target.value)" rows="4"></textarea></div>
        </template>
        <template v-else-if="editing.data.layout === 'sms'">
          <div class="field"><label>{{ $t('发信人') }}</label><input v-model="editing.data.sender" /></div>
          <div class="field"><label>{{ $t('时间戳') }}</label><input v-model="editing.data.stamp" /></div>
          <div class="field"><label>{{ $t('短信内容') }}</label><textarea v-model="editing.data.message" rows="4"></textarea></div>
        </template>
        <template v-else-if="editing.data.layout === 'shop'">
          <div class="field"><label>{{ $t('应用名') }}</label><input v-model="editing.data.app" /></div>
          <div class="field"><label>{{ $t('商品标题') }}</label><input v-model="editing.data.title" /></div>
          <div class="field"><label>{{ $t('副标题') }}</label><input v-model="editing.data.subtitle" /></div>
          <div class="field"><label>{{ $t('价格') }}</label><input v-model="editing.data.price" /></div>
          <div class="field"><label>{{ $t('已售') }}</label><input v-model="editing.data.sold" /></div>
          <div class="field"><label>{{ $t('按钮文字') }}</label><input v-model="editing.data.button" /></div>
        </template>
        <template v-else>
          <div class="field"><label>{{ $t('文字（每行一条）') }}</label><textarea :value="editing.linesText ?? (editing.data.lines || []).join('\n')" @input="setInsertLines(editing, $event.target.value)" rows="5"></textarea></div>
        </template>
      </template>
      <template v-else>
        <h4>{{ editing.idx != null ? $t('改画面文字') : $t('加一条画面文字') }}</h4>
        <div class="field"><label>{{ $t('叠在哪一镜') }}</label><select v-model="editing.data.shot"><option v-for="s in shotIds" :key="s" :value="s">{{ s }}</option></select></div>
        <div class="grid2">
          <div class="field"><label>{{ $t('从第几秒出现') }}</label><input v-model.number="editing.data.at" type="number" step="0.1" /></div>
          <div class="field"><label>{{ $t('纵向位置（0 顶 – 1 底）') }}</label><input v-model.number="editing.data.y" type="number" step="0.05" min="0" max="1" /></div>
        </div>
        <div class="field"><label>{{ $t('文字（可换行）') }}</label><textarea v-model="editing.data.text" rows="3"></textarea></div>
      </template>
      <div class="row" style="margin-top:8px"><button class="btn primary sm" @click="commit">{{ $t('保存') }}</button><button class="btn sm" @click="editing = null">{{ $t('取消') }}</button></div>
    </aside>
  </div>
</template>
