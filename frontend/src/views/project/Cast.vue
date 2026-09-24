<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store, refreshProject, withBusy, loadVoices, reviseEdit } from '../../store.js'
import { get, post, put, del, ep, fileToBase64 } from '../../api.js'
import { confirm, notice, prompt, toast, T } from '../../ui.js'
import { t } from '../../i18n.js'

const route = useRoute(); const router = useRouter()
const cast = computed(() => store.cast || [])
const cur = computed(() => cast.value.find(c => c.id === route.params.castId) || null)
const form = reactive({ kind: 'character', name: '', voice: '', block: '', locks: [], variants: [] })
const status = ref('')
const statusOk = ref(false) // 状态栏文案是否为成功提示（不靠比对中文字面，切语言也不会误判成错误）
function setStatus(text, ok) { status.value = text; statusOk.value = ok }
// 连续性锁：表单里 mode 是 all / shots 的单选，保存时折回后端形状 shots: "all" | [镜号]
function lockRows(locks) {
  return (locks || []).map(l => ({ surface: l.surface || '', mode: Array.isArray(l.shots) ? 'shots' : 'all', shots: Array.isArray(l.shots) ? [...l.shots] : [], note: l.note || '' }))
}
// 造型变体（DESIGN §23）：表单里是数组行，保存时折回后端形状 variants: { key: {label, delta, asset, shots} }
function variantRows(variants) {
  return Object.entries(variants || {}).map(([key, v]) => ({ key, label: v.label || '', delta: v.delta || '', asset: v.asset || '', asset_url: v.asset_url || null, used_in: v.used_in || [], saved: true, keyTouched: true }))
}
function variantsBody(rows) {
  const out = {}
  for (const v of rows) out[v.key.trim()] = { label: v.label.trim(), delta: v.delta, ...(v.asset ? { asset: v.asset } : {}) }
  return out
}
function body() {
  return { kind: form.kind, name: form.name, voice: form.voice, block: form.block,
    locks: form.locks.map(l => ({ surface: l.surface, shots: l.mode === 'shots' ? l.shots : 'all', note: l.note })),
    variants: variantsBody(form.variants) }
}
// 变体键：和后端 id 规则一样（只留 ASCII 字母数字，其余压成下划线，40 字），压不出东西退到 v1、v2…；用户改过的键不再自动跟标题
function variantKeyFrom(label, taken) {
  const slug = String(label || '').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toLowerCase().slice(0, 40)
  const used = new Set(taken)
  if (slug) { let k = slug; for (let n = 2; used.has(k); n++) k = `${slug}-${n}`; return k }
  let n = 1; while (used.has(`v${n}`)) n++
  return `v${n}`
}
function addVariant() { form.variants.push({ key: '', label: '', delta: '', asset: '', asset_url: null, used_in: [], saved: false, keyTouched: false }) }
function onVariantLabel(v) { if (!v.keyTouched) v.key = variantKeyFrom(v.label, form.variants.filter(x => x !== v).map(x => x.key)) }
function variantProblem() {
  const keys = form.variants.map(v => v.key.trim())
  if (keys.some(k => !k)) return t('有一个造型变体没有键——填标题会自动生成')
  if (new Set(keys).size !== keys.length) return t('造型变体的键重复了')
  if (form.variants.some(v => !v.delta.trim())) return t('有一个造型变体没写差异——只写相对基底换了什么')
  return ''
}
const shotIds = computed(() => (store.detail?.shots || []).map(s => s.id))
function addLock() { form.locks.push({ surface: '', mode: 'all', shots: [], note: '' }) }
function toggleLockShot(l, sid) { const i = l.shots.indexOf(sid); if (i >= 0) l.shots.splice(i, 1); else l.shots.push(sid) }
// 表单有没有没保存的改动：refreshProject 每次都把 store.cast 换成新数组（生成期间每 8 秒一次、绑音色也刷），有改动时不能
// 拿服务端的值把它冲掉；换角色 / 换版本才重灌。锁、变体都是按钮改嵌套数组，改动靠 form 的深层同步 watch 记，灌表单本身用 filling 排除。
const dirty = ref(false)
let filling = false
let filledKey = ''   // 当前表单灌的是哪个版本的哪个角色
function fill(c) {
  filling = true
  try { Object.assign(form, { kind: c.kind, name: c.name, voice: c.voice, block: c.block, locks: lockRows(c.locks), variants: variantRows(c.variants) }) }
  finally { filling = false }
  dirty.value = false
  filledKey = `${store.projectId}/${c.id}`
}
watch(form, () => { if (!filling) dirty.value = true }, { deep: true, flush: 'sync' })
watch(cur, (c) => {
  if (!c) return
  if (`${store.projectId}/${c.id}` !== filledKey) { fill(c); setStatus('', false); return }
  if (!dirty.value) fill(c)   // 同一角色刷新：没改动才跟着服务端
}, { immediate: true })
const locked = computed(() => store.detail?.locked)
const readonly = computed(() => !!store.detail?.readonly_reason)
// 制作中的项目：动作走「预览影响 → 保存为新版本」，成功后跳到新版本的同一个角色
async function mutate(direct, mutation, castId) {
  if (!locked.value) return direct()
  const r = await reviseEdit(mutation)
  if (r) router.push(`/drama/p/${r.id}/cast/${castId || ''}`)
  return r
}
const voices = computed(() => store.voices || [])
const bindId = ref('')
onMounted(() => { if (!store.voices) loadVoices() })
// 下拉只在换角色、或服务端的绑定真变了时复位：刷新（同一个 voice_ref）不能把用户刚选还没点「绑定」的那项冲掉
watch([() => cur.value?.id, () => cur.value?.voice_ref], ([, ref]) => { bindId.value = ref || '' }, { immediate: true })
async function bindVoice() {
  const cid = cur.value.id
  if (locked.value) { await mutate(null, { kind: 'cast_voice', cast_id: cid, voice_id: bindId.value || null }, cid); return }
  setStatus(t('绑定中…'), true)
  try {
    await post(`${ep(store.projectId)}/cast/${encodeURIComponent(cid)}/voice`, { voice_id: bindId.value || null })
    const wasDirty = dirty.value
    await refreshProject(); setStatus(bindId.value ? t('已绑定，声音描述已拷进角色') : t('已解绑'), true)
    // 表单有别的改动时刷新不会重灌：只把服务端刚拷进角色的声音描述接过来，不然下次「保存」会用旧描述把它盖回去
    if (wasDirty && cur.value) form.voice = cur.value.voice
  } catch (e) { setStatus(e.message, false) }
}
const boundVoice = computed(() => voices.value.find(v => v.id === cur.value?.voice_ref) || null)

// 对比试听（DESIGN §29）：同一句台词让几个候选音色各念一遍，并排听完再绑。不改 spec，制作中也能用；「用这个」走上面的绑定。
const CMP_MIN = 2; const CMP_MAX = 6
const cmp = reactive({ picked: [], text: '', defaultText: '', running: false, error: '', runs: [] })
const ttsVoices = computed(() => voices.value.filter(v => v.voice))
async function loadCompare(cid) {
  try {
    const r = await get(`${ep(store.projectId)}/cast/${encodeURIComponent(cid)}/voice-compare`)
    cmp.runs = r.runs || []; cmp.defaultText = r.default_text || ''
  } catch { cmp.runs = []; cmp.defaultText = '' }
}
// 换角色才清空对比区：刷新（同一角色）不能把勾好的音色、填好的试听文字清掉
watch(() => cur.value?.id, () => {
  const c = cur.value
  Object.assign(cmp, { picked: [], text: '', defaultText: '', running: false, error: '', runs: [] })
  if (c && c.kind === 'character') loadCompare(c.id)
}, { immediate: true })
function togglePick(id) {
  const i = cmp.picked.indexOf(id)
  if (i >= 0) { cmp.picked.splice(i, 1); return }
  if (cmp.picked.length >= CMP_MAX) { toast(t('最多对比 {n} 个音色', { n: CMP_MAX })); return }
  cmp.picked.push(id)
}
async function runCompare() {
  const cid = cur.value.id
  if (cmp.picked.length < CMP_MIN) return
  cmp.running = true; cmp.error = ''
  try {
    const r = await post(`${ep(store.projectId)}/cast/${encodeURIComponent(cid)}/voice-compare`, { voices: [...cmp.picked], text: cmp.text.trim() || null })
    const known = new Set(voices.value.map(v => v.id))
    cmp.runs.unshift({ text: r.text, items: (r.items || []).map(i => ({ ...i, in_library: known.has(i.voice_id) })), fresh: true })
  } catch (e) { cmp.error = e.message }
  finally { cmp.running = false }
}
async function adopt(voiceId) {
  bindId.value = voiceId
  await bindVoice()
}

async function save() {
  const cid = cur.value.id
  if (form.locks.some(l => !l.surface.trim())) { setStatus(t('有一把锁的锁面是空的——填上，或删掉那一行'), false); return }
  if (form.locks.some(l => l.mode === 'shots' && !l.shots.length)) { setStatus(t('「指定镜」的锁至少要选一镜，或改成「全集」'), false); return }
  const vp = variantProblem(); if (vp) { setStatus(vp, false); return }
  if (locked.value) { await mutate(null, { kind: 'cast_edit', cast_id: cid, ...body() }, cid); return }
  setStatus(t('保存中…'), true)
  try {
    await put(`${ep(store.projectId)}/cast/${encodeURIComponent(cid)}`, body())
    dirty.value = false   // 已经落盘：接下来的刷新要把服务端的值（变体键、锚图地址）灌回来
    await refreshProject(); setStatus(t('已保存'), true)
  } catch (e) { setStatus(e.message, false) }
}
async function create() {
  const name = await prompt(t('新角色/场景的名字（比如「林小满」「办公室」）'), { multiline: false, defaultValue: '', confirmLabel: t('创建') })
  if (!name?.trim()) return
  const label = name.trim()
  // id 由后端按名字生成；block 是写进剧本的外形描述占位（数据，剧本正文是中文），不做界面翻译
  const body = { kind: 'character', name: label, block: `${label}：（外形描述待写）` }
  await mutate(() => withBusy(async () => {
    try {
      const c = await post(`${ep(store.projectId)}/cast`, body)
      await refreshProject(); router.push(`/drama/p/${store.projectId}/cast/${c.id}`)
    } catch (e) { await notice(e.message) }
  }), { kind: 'cast_create', ...body }, '')
}
async function remove() {
  const cid = cur.value.id
  if (!await confirm(t('删除「{name}」？', { name: cur.value.name || cid }), { confirmLabel: t('删除'), danger: true })) return
  await mutate(async () => {
    try { await del(`${ep(store.projectId)}/cast/${encodeURIComponent(cid)}`); await refreshProject(); router.push(`/drama/p/${store.projectId}/cast`) }
    catch (e) { await notice(e.message) }
  }, { kind: 'cast_delete', cast_id: cid }, '')
}
async function upload(ev) {
  const file = ev.target.files?.[0]; ev.target.value = ''
  if (!file) return
  const cid = cur.value.id
  setStatus(t('上传中…'), true)
  try {
    const data_base64 = await fileToBase64(file)
    if (locked.value) {
      // 制作中：先把图放好（stage），再走「保存并预告费用」把路径写进新版本
      const staged = await post(`${ep(store.projectId)}/cast/${encodeURIComponent(cid)}/asset?stage=true`, { filename: file.name, data_base64 })
      setStatus('', false)
      await mutate(null, { kind: 'cast_asset', cast_id: cid, asset: staged.asset }, cid)
      return
    }
    await post(`${ep(store.projectId)}/cast/${encodeURIComponent(cid)}/asset`, { filename: file.name, data_base64 })
    await refreshProject(); setStatus(t('锚图已更新'), true); toast(t('锚图会作为参考图随首帧请求发送'))
  } catch (e) { setStatus(e.message, false) }
}
async function uploadVariant(ev, v) {
  const file = ev.target.files?.[0]; ev.target.value = ''
  if (!file) return
  const cid = cur.value.id; const key = v.key.trim()
  setStatus(t('上传中…'), true)
  try {
    const data_base64 = await fileToBase64(file)
    if (locked.value) {
      // 制作中：先把图放好（stage），再把带路径的整张变体表走「保存并预告费用」
      const staged = await post(`${ep(store.projectId)}/cast/${encodeURIComponent(cid)}/variants/${encodeURIComponent(key)}/asset?stage=true`, { filename: file.name, data_base64 })
      setStatus('', false)
      const rows = form.variants.map(x => x === v ? { ...x, asset: staged.asset } : x)
      await mutate(null, { kind: 'cast_edit', cast_id: cid, variants: variantsBody(rows) }, cid)
      return
    }
    const res = await post(`${ep(store.projectId)}/cast/${encodeURIComponent(cid)}/variants/${encodeURIComponent(key)}/asset`, { filename: file.name, data_base64 })
    const wasDirty = dirty.value
    await refreshProject(); setStatus(t('变体锚图已更新'), true)
    // 表单有别的改动时刷新不会重灌：把服务端刚写进这个变体的锚图接过来，不然下次「保存」会拿旧变体表把它冲掉
    if (wasDirty && form.variants.includes(v)) Object.assign(v, { asset: res.asset, asset_url: res.asset_url || null })
  } catch (e) { setStatus(e.message, false) }
}
function clearVariantAsset(v) { v.asset = ''; v.asset_url = null }
async function clearAsset() {
  const cid = cur.value.id
  await mutate(async () => {
    try { await put(`${ep(store.projectId)}/cast/${encodeURIComponent(cid)}`, { asset: '' }); await refreshProject() } catch (e) { await notice(e.message) }
  }, { kind: 'cast_asset', cast_id: cid, asset: '' }, cid)
}
</script>

<template>
  <div class="ws-content">
    <div class="ws-center" style="max-width:520px;border-right:1px solid var(--border)">
      <div class="row between" style="margin-bottom:10px">
        <h3 style="font-size:16px">{{ $t('角色与场景') }}</h3>
        <button class="btn sm" :disabled="readonly" @click="create">{{ $t('+ 新增') }}</button>
      </div>
      <div v-if="locked" class="hint" style="margin-bottom:10px">{{ $t('制作中：角色外形、声音、锚图会进提示词。点「保存」时系统会先告诉你引用它的哪些镜要重出首帧，确认后保存为一个新版本。') }}</div>
      <div class="item-list">
        <router-link v-for="c in cast" :key="c.id" class="item" :class="{ cur: c.id === cur?.id }" :to="`/drama/p/${store.projectId}/cast/${c.id}`">
          <span class="thumb sq"><img v-if="c.asset_url" :src="c.asset_url" alt="" /><span v-else>{{ c.kind === 'character' ? $t('人') : $t('景') }}</span></span>
          <span class="body">
            <span class="title">{{ c.name || c.id }} <span class="pill" style="margin-left:4px">{{ $t(T.castKind[c.kind]) }}</span></span>
            <span class="sub">{{ c.block }}</span>
            <span class="sub">{{ $t('出现在 {s}', { s: c.used_in.length ? c.used_in.join($t('、')) : $t('（还没被任何镜引用）') }) }}</span>
          </span>
        </router-link>
      </div>
      <div v-if="!cast.length" class="empty">{{ $t('还没有角色或场景') }}</div>
    </div>
    <div class="ws-center" v-if="cur">
      <div class="row between" style="margin-bottom:10px">
        <h3 style="font-size:16px">{{ cur.name || cur.id }} <span class="mono hint">{{ cur.id }}</span></h3>
        <button class="btn sm danger" :disabled="readonly || cur.used_in.length > 0" :title="cur.used_in.length ? $t('还被镜头引用，先从镜头里移掉') : ''" @click="remove">{{ $t('删除') }}</button>
      </div>
      <div class="grid2">
        <div class="field"><label>{{ $t('类型') }}</label>
          <select v-model="form.kind" :disabled="readonly"><option v-for="(label, k) in T.castKind" :key="k" :value="k">{{ $t(label) }}</option></select></div>
        <div class="field"><label>{{ $t('名字（角色用；进台词提示词）') }}</label><input v-model="form.name" :disabled="readonly" /></div>
      </div>
      <div class="field"><label>{{ form.kind === 'character' ? $t('外形（进首帧提示词：发型、服装、体态、标志物；表情逐镜写，不写在这里）') : $t('场景/道具描述（进首帧提示词）') }}</label>
        <textarea v-model="form.block" :disabled="readonly" rows="4"></textarea></div>
      <div class="field" v-if="form.kind === 'character'"><label>{{ $t('声音描述（进视频提示词：音色、语速、音量）') }}{{ cur.voice_ref ? $t('——来自音色库「{name}」，可以再改', { name: cur.voice_name }) : '' }}</label><input v-model="form.voice" :disabled="readonly" /></div>
      <div class="field">
        <label>{{ $t('跨镜锁定项（锁面原样进生效镜的首帧和视频提示词；只锁颜色 + 材质/形制 + 物体这类不随剧情变的事实，一集通常只有几把）') }}</label>
        <div v-for="(l, i) in form.locks" :key="i" style="margin-bottom:6px;padding:6px 8px;border:1px solid var(--border);border-radius:var(--r-sm)">
          <div class="row" style="gap:6px">
            <input v-model="l.surface" :disabled="readonly" :placeholder="$t('锁面，用提示词语言写，如「淡蓝色粗针织羊毛毛衣」')" style="flex:2;min-width:0" />
            <select v-model="l.mode" :disabled="readonly" style="flex:0 0 auto">
              <option value="all">{{ $t('全集（出场的每一镜）') }}</option>
              <option value="shots">{{ $t('指定镜') }}</option>
            </select>
            <input v-model="l.note" :disabled="readonly" :placeholder="$t('备注（可选）')" style="flex:1;min-width:0" />
            <button class="btn sm" :disabled="readonly" :title="$t('删除这把锁')" @click="form.locks.splice(i, 1)">×</button>
          </div>
          <div v-if="l.mode === 'shots'" class="chips" style="margin-top:6px">
            <span v-for="sid in shotIds" :key="sid" class="chip" :style="l.shots.includes(sid) ? 'background:var(--accent-bg);border-color:var(--accent);color:var(--accent-strong)' : ''" @click="!readonly && toggleLockShot(l, sid)">{{ l.shots.includes(sid) ? '✓ ' : '' }}{{ sid }}</span>
            <span v-if="!shotIds.length" class="hint">{{ $t('还没有镜头') }}</span>
          </div>
        </div>
        <button class="btn sm" :disabled="readonly" @click="addLock">{{ $t('+ 加一把锁') }}</button>
      </div>
      <div class="field" v-if="form.kind === 'character'">
        <label>{{ $t('造型变体（换装、发型状态、受伤、湿污、伪装——只写相对基底的差异；脸和体态是身份，不在这里改。镜头「出场」里可把该角色切到某个造型）') }}</label>
        <div v-for="(v, i) in form.variants" :key="i" style="margin-bottom:6px;padding:6px 8px;border:1px solid var(--border);border-radius:var(--r-sm)">
          <div class="row" style="gap:6px">
            <input v-model="v.label" :disabled="readonly" :placeholder="$t('标题，如「雨夜」「受伤后」')" style="flex:1;min-width:0" @input="onVariantLabel(v)" />
            <input v-model="v.key" :disabled="readonly || v.saved" class="mono" :placeholder="$t('键')" :title="v.saved ? $t('已保存的变体键不能改（镜头里用 角色@键 引用它）') : $t('镜头里用 角色@键 引用；按标题自动生成，可改')" style="flex:0 0 120px;min-width:0" @input="v.keyTouched = true" />
            <button class="btn sm" :disabled="readonly || v.used_in.length > 0" :title="v.used_in.length ? $t('还被镜头引用，先在镜头里切回基底') : $t('删除这个变体')" @click="form.variants.splice(i, 1)">×</button>
          </div>
          <textarea v-model="v.delta" :disabled="readonly" rows="2" :placeholder="$t('差异，用提示词语言写，如「外加一件橙色旧雨衣，兜帽放下，头发湿贴额头」')" style="margin-top:6px"></textarea>
          <div class="row" style="margin-top:6px;align-items:center;gap:8px">
            <img v-if="v.asset_url" :src="v.asset_url" alt="" style="width:36px;height:48px;object-fit:cover;border-radius:4px;border:1px solid var(--border)" />
            <label v-if="v.saved" class="btn sm" :style="readonly ? 'opacity:.4;pointer-events:none' : ''">{{ v.asset_url ? $t('换一张') : $t('上传变体锚图') }}<input type="file" accept="image/png,image/jpeg,image/webp" style="display:none" @change="uploadVariant($event, v)" /></label>
            <span v-else class="hint">{{ $t('先保存，再上传这个造型的锚图') }}</span>
            <button v-if="v.asset" class="btn sm" :disabled="readonly" @click="clearVariantAsset(v)">{{ $t('移除') }}</button>
            <span class="hint">{{ v.asset_url ? '' : $t('没有锚图时用角色的锚图') }}</span>
            <span class="hint" style="margin-left:auto">{{ v.used_in.length ? $t('用于 {s}', { s: v.used_in.join($t('、')) }) : $t('还没有镜用到') }}</span>
          </div>
        </div>
        <button class="btn sm" :disabled="readonly" @click="addVariant">{{ $t('+ 加一个造型') }}</button>
      </div>
      <div class="row" style="margin-bottom:16px">
        <button class="btn primary sm" :disabled="readonly" @click="save">{{ locked ? $t('保存并预告费用') : $t('保存') }}</button>
        <span class="status" :class="{ err: status && !statusOk }">{{ status }}</span>
      </div>
      <div class="card" v-if="form.kind === 'character'">
        <h3>{{ $t('音色') }}</h3>
        <p class="hint">{{ $t('从音色库绑定：声音描述拷进上面的「声音描述」（视频模型直出台词时用），TTS 参数记在角色上（旁白式配音时用）。绑定是拷贝，库里以后改了不会自动跟着变。') }}</p>
        <div class="row" style="margin-top:8px">
          <select v-model="bindId" :disabled="readonly" style="padding:7px 10px;border:1px solid var(--border);border-radius:var(--r-sm);background:var(--panel2);min-width:220px">
            <option value="">{{ $t('（不绑定）') }}</option>
            <option v-for="v in voices" :key="v.id" :value="v.id">{{ v.name }}{{ v.voice ? ' · ' + v.voice : '' }}</option>
          </select>
          <button class="btn sm" :disabled="readonly || bindId === (cur.voice_ref || '')" @click="bindVoice">{{ bindId ? $t('绑定') : $t('解绑') }}</button>
          <router-link class="btn sm" to="/voices" style="text-decoration:none">{{ $t('管理音色库') }}</router-link>
        </div>
        <div v-if="boundVoice" class="row" style="margin-top:8px">
          <span class="pill ok">{{ $t('已绑定 {name}', { name: boundVoice.name }) }}</span>
          <audio v-if="boundVoice.has_sample" :src="`/api/voices/${boundVoice.id}/sample`" controls style="height:30px"></audio>
          <span v-else class="hint">{{ $t('这个音色还没有试听样本，去音色库点「试听」') }}</span>
        </div>
        <div v-else-if="cur.voice_ref" class="hint" style="margin-top:6px">{{ $t('绑定的音色「{name}」已不在库里；描述文字仍保留在角色上。', { name: cur.voice_name }) }}</div>
        <div class="cmp" data-test="voice-compare">
          <h4>{{ $t('对比试听') }}</h4>
          <p class="hint">{{ $t('同一句台词，让几个候选音色各念一遍，并排听完再绑。选 2 到 6 个；只有带 TTS 音色的档案能试听。') }}</p>
          <div class="chips" style="margin-top:6px">
            <label v-for="v in ttsVoices" :key="v.id" class="chip" :class="{ on: cmp.picked.includes(v.id) }" :data-test="`compare-pick-${v.id}`">
              <input type="checkbox" :checked="cmp.picked.includes(v.id)" :disabled="readonly" style="margin:0 4px 0 0;vertical-align:-1px" @change="togglePick(v.id)" />{{ v.name }}<span v-if="v.id === cur.voice_ref"> ✓</span>
            </label>
            <span v-if="!ttsVoices.length" class="hint">{{ $t('音色库里还没有带 TTS 音色的档案') }}</span>
          </div>
          <div class="row" style="margin-top:8px">
            <input v-model="cmp.text" data-test="compare-text" :disabled="readonly" style="flex:1;min-width:0"
              :placeholder="cmp.defaultText ? $t('试听文字（留空用第一句台词：{line}）', { line: cmp.defaultText }) : $t('这个角色没有台词，填一句试听文字')" />
            <button class="btn sm primary" data-test="compare-run" :disabled="readonly || cmp.running || cmp.picked.length < CMP_MIN || cmp.picked.length > CMP_MAX" @click="runCompare">{{ cmp.running ? $t('合成中…') : $t('对比试听') }}</button>
          </div>
          <div v-if="cmp.picked.length && cmp.picked.length < CMP_MIN" class="hint" style="margin-top:4px">{{ $t('再选 {n} 个', { n: CMP_MIN - cmp.picked.length }) }}</div>
          <div v-if="cmp.error" class="status err" data-test="compare-error" style="margin-top:6px">{{ cmp.error }}</div>
          <div v-for="(run, ri) in cmp.runs" :key="ri" class="cmp-run" data-test="compare-run-result">
            <div class="hint">{{ $t('台词：「{line}」', { line: run.text }) }}<span v-if="run.fresh" class="pill ok" style="margin-left:6px">{{ $t('刚出的') }}</span></div>
            <div class="cmp-cards">
              <div v-for="it in run.items" :key="it.voice_id" class="cmp-card" :class="{ bad: !!it.error, bound: it.voice_id === cur.voice_ref }" data-test="compare-card">
                <div class="cmp-name">{{ it.name }}<span v-if="!it.in_library" class="hint"> {{ $t('（音色库里已删除）') }}</span></div>
                <audio v-if="it.url" :src="it.url" controls preload="none" style="width:100%;height:30px"></audio>
                <div v-else class="status err">{{ it.error }}</div>
                <button class="btn sm" data-test="compare-adopt" :disabled="readonly || !it.url || !it.in_library || it.voice_id === cur.voice_ref" @click="adopt(it.voice_id)">{{ it.voice_id === cur.voice_ref ? $t('已绑定') : $t('用这个') }}</button>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="card" v-if="form.kind === 'character'">
        <h3>{{ $t('锚图（定妆参考）') }}</h3>
        <p class="hint">{{ $t('生成每一镜首帧时，这张图会作为参考图一起发给生图模型，保证跨镜身份一致。建议正面半身、干净背景、和外形描述一致。') }}</p>
        <div class="row" style="margin-top:8px;align-items:flex-start">
          <img v-if="cur.asset_url" :src="cur.asset_url" alt="" style="width:120px;aspect-ratio:3/4;object-fit:cover;border-radius:4px;border:1px solid var(--border)" />
          <div class="stack">
            <label class="btn sm" :style="readonly ? 'opacity:.4;pointer-events:none' : ''">{{ cur.asset_url ? $t('换一张') : $t('上传锚图') }}<input type="file" accept="image/png,image/jpeg,image/webp" style="display:none" @change="upload" /></label>
            <button v-if="cur.asset_url" class="btn sm" :disabled="readonly" @click="clearAsset">{{ $t('移除') }}</button>
            <span class="hint" v-if="cur.asset">{{ cur.asset }}</span>
          </div>
        </div>
      </div>
      <div class="card">
        <h3>{{ $t('出现在哪些镜') }}</h3>
        <div class="chips" v-if="cur.used_in.length"><router-link v-for="sid in cur.used_in" :key="sid" class="chip" :to="`/drama/p/${store.projectId}/shots/${sid}`">{{ sid }}</router-link></div>
        <div v-else class="hint">{{ $t('还没有镜引用它——在镜头编辑器的「出场」里勾上。') }}</div>
      </div>
    </div>
    <div class="ws-center" v-else><div class="empty">{{ $t('从左边选一个角色或场景') }}</div></div>
  </div>
</template>

<style scoped>
.cmp { margin-top: 14px; padding-top: 10px; border-top: 1px dashed var(--border); }
.cmp h4 { margin: 0 0 4px; font-size: 13px; }
.cmp .chip.on { background: var(--accent-bg); border-color: var(--accent); color: var(--accent-strong); }
.cmp-run { margin-top: 10px; }
.cmp-cards { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; align-items: stretch; }
.cmp-card { flex: 1 1 180px; max-width: 260px; display: flex; flex-direction: column; gap: 6px; padding: 8px; border: 1px solid var(--border); border-radius: var(--r-sm); background: var(--panel2); }
.cmp-card.bound { border-color: var(--good); }
.cmp-card.bad { border-style: dashed; }
.cmp-name { font-size: 12px; font-weight: 600; }
</style>
