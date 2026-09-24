<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { get, post, put, del, fileToBase64 } from '../api.js'
import { store, loadVoices, withBusy } from '../store.js'
import { confirm, notice, prompt } from '../ui.js'
import { t } from '../i18n.js'

const route = useRoute(); const router = useRouter()
const voices = computed(() => store.voices || [])
const cur = computed(() => voices.value.find(v => v.id === route.params.voiceId) || null)
const form = reactive({ name: '', description: '', description_mode: 'traits', engine: 'edge', voice: '', rate: '', pitch: '', sample_text: '' })
const traits = reactive({ gender: '', age: '', timbre: '', pace: 'medium', volume: 'normal', accent: '', mood: '' })
const preview = reactive({ zh: '', en: '' })
const status = ref('')
const statusErr = ref(false)   // 状态条是不是错误（不靠文案判断，语言切换后也对）
const catalog = ref({ voices: [], note: '' })
const locale = ref('zh')
const sampleUrl = ref('')
const catalogLoading = ref(false)
const engines = ref(['edge', 'settings'])   // 目录接口按密钥给：配了 MiniMax 才有 minimax
const refUrl = ref('')                       // 参考音频播放地址（DESIGN §33）
const cloneBusy = ref(false)
let previewTimer = null
let clonePollTimer = null
const ENGINE_LABEL = { settings: '模型设置的语音', minimax: 'MiniMax', edge: 'Edge' }

// 中文原文留在表里（value 是发给后端的 id），渲染时 $t(label)
const OPT = {
  gender: [['', '（未定）'], ['female', '女声'], ['male', '男声'], ['neutral', '中性声']],
  age: [['', '（未定）'], ['child', '六到十岁'], ['teen', '十几岁'], ['young', '二十多岁'], ['adult', '三十多岁'], ['middle', '四五十岁'], ['senior', '六十岁以上']],
  pace: [['', '（未定）'], ['slow', '慢'], ['medium', '中等'], ['fast', '偏快']],
  volume: [['', '（未定）'], ['soft', '偏小但清楚'], ['normal', '正常'], ['loud', '偏大']],
}
// 带内联标签的说明，v-html 渲染（纯常量）
const INTRO_HTML = '一条音色 = 决定音色的 TTS 参数 + 一段固定的音色提示词。绑给<b>旁白</b>用 TTS 合成，音色稳定；绑给<b>角色</b>时提示词进每一镜的视频提示词，让台词声音"像同一个人"。'

function setStatus(msg, isErr = false) { status.value = msg; statusErr.value = isErr }

onMounted(async () => { await loadVoices(); await loadCatalog() })
watch(cur, (v) => {
  if (!v) return
  Object.assign(form, { name: v.name, description: v.description || '', description_mode: v.description_mode || (v.traits && Object.keys(v.traits).length ? 'traits' : 'custom'),
    engine: v.engine || 'edge', voice: v.voice || '', rate: v.rate || '', pitch: v.pitch || '', sample_text: v.sample_text || '' })
  const tr = v.traits || {}
  Object.assign(traits, { gender: tr.gender || '', age: tr.age || '', timbre: (tr.timbre || []).join('、'), pace: tr.pace || '', volume: tr.volume || '', accent: tr.accent || '', mood: tr.mood || '' })
  preview.zh = v.description || ''; preview.en = v.description_en || ''
  sampleUrl.value = v.has_sample ? `/api/voices/${v.id}/sample?t=${v.updated_at || 0}` : ''
  refUrl.value = v.has_reference_audio ? `/api/voices/${v.id}/reference-audio?t=${v.updated_at || 0}` : ''
  setStatus('')
}, { immediate: true })
onUnmounted(() => clearTimeout(clonePollTimer))
watch(() => [form.engine, locale.value], loadCatalog)
watch(traits, () => { if (form.description_mode !== 'traits') return; clearTimeout(previewTimer); previewTimer = setTimeout(refreshPreview, 250) }, { deep: true })

async function refreshPreview() {
  try { const r = await post('/api/voices/preview-description', { traits: { ...traits } }); preview.zh = r.zh; preview.en = r.en }
  catch (e) { preview.zh = e.message; preview.en = '' }
}
async function loadCatalog() {
  catalogLoading.value = true
  try {
    catalog.value = await get(`/api/voices/catalog?engine=${form.engine}&locale=${locale.value}`)
    if (Array.isArray(catalog.value.engines) && catalog.value.engines.length) engines.value = catalog.value.engines
  } catch (e) { catalog.value = { voices: [], note: e.message } }
  finally { catalogLoading.value = false }
}
const voiceInCatalog = computed(() => catalog.value.voices.find(v => v.voice === form.voice))

async function draftFromVoice() {
  if (!form.voice) { await notice(t('先选一个 TTS 音色')); return }
  try {
    const r = await get(`/api/voices/draft-traits?engine=${form.engine}&voice=${encodeURIComponent(form.voice)}`)
    if (!r.found) { await notice(t('目录里没有这个音色的信息，维度只能手填')); return }
    const tr = r.traits
    Object.assign(traits, { gender: tr.gender, timbre: (tr.timbre || []).join('、'), pace: tr.pace, volume: tr.volume, accent: tr.accent, age: traits.age, mood: traits.mood })
    form.description_mode = 'traits'
    setStatus(t('已按音色填了初稿，补上年龄和音色关键词更准'))
  } catch (e) { setStatus(e.message, true) }
}
function payload() {
  const body = { name: form.name, description_mode: form.description_mode, engine: form.engine, voice: form.voice, rate: form.rate, pitch: form.pitch, sample_text: form.sample_text }
  if (form.description_mode === 'traits') body.traits = { ...traits }
  else body.description = form.description
  return body
}
async function create() {
  const name = await prompt(t('新音色的名字（比如「旁白男声」「主角女声」）'), { multiline: false, confirmLabel: t('创建') })
  if (!name?.trim()) return
  await withBusy(async () => {
    try {
      const v = await post('/api/voices', { name: name.trim(), engine: 'edge', voice: 'zh-CN-XiaoxiaoNeural', description_mode: 'traits', traits: { gender: 'female', pace: 'medium', volume: 'normal', accent: '标准普通话' } })
      await loadVoices(); router.push(`/voices/${v.id}`)
    } catch (e) { await notice(e.message) }
  })
}
async function save() {
  setStatus(t('保存中…'))
  try { await put(`/api/voices/${cur.value.id}`, payload()); await loadVoices(); setStatus(t('已保存')) }
  catch (e) { setStatus(e.message, true) }
}
async function remove() {
  if (!await confirm(t('删除音色「{name}」？已经绑定它的项目不受影响（绑定时已拷贝）。', { name: cur.value.name }), { confirmLabel: t('删除'), danger: true })) return
  try { await del(`/api/voices/${cur.value.id}`); await loadVoices(); router.push('/voices') } catch (e) { await notice(e.message) }
}
// ---- 参考音频与克隆（DESIGN §33）：文件 → base64 上传（永不覆盖）；克隆是后台任务，轮档案的 clone.status
const cloneStatus = computed(() => cur.value?.clone?.status || (cur.value?.reference_audio ? 'none' : ''))
const refName = computed(() => String(cur.value?.reference_audio || '').split(/[\\/]/).pop())
const canClone = computed(() => engines.value.includes('minimax'))
const cloneLabel = computed(() => {
  const c = cur.value?.clone || {}
  if (cloneStatus.value === 'cloning') return t('克隆中…')
  if (cloneStatus.value === 'ready') return t('已克隆：{id}', { id: c.voice_id || '' })
  if (cloneStatus.value === 'failed') return t('失败：{err}', { err: c.error || '' })
  return t('未克隆')
})
async function uploadReference(ev) {
  const file = ev.target.files?.[0]
  if (!file) return
  setStatus(t('上传参考音频…'))
  try {
    const r = await post(`/api/voices/${cur.value.id}/reference-audio`, { filename: file.name, data_base64: await fileToBase64(file) })
    await loadVoices()
    refUrl.value = r.reference_url || refUrl.value
    setStatus(t('参考音频已上传，可以克隆了'))
  } catch (e) { setStatus(e.message, true) }
  finally { ev.target.value = '' }
}
async function pollClone(id, tries = 0) {
  // 瞬时错误（服务抖一下、5xx）先重试：以前 loadVoices 吞掉错误、把列表清空，一次抖动轮询就停了，界面一直停在「克隆中」
  try { await loadVoices({ retry: true }) } catch (e) {
    cloneBusy.value = false
    setStatus(t('读不到克隆进度：{msg}——克隆还在后台进行，稍后刷新页面再看', { msg: e.message }), true)
    return
  }
  const v = (store.voices || []).find(x => x.id === id)
  if (!v || v.clone?.status !== 'cloning') { cloneBusy.value = false; return }
  if (tries >= 120) {
    cloneBusy.value = false
    setStatus(t('克隆还没结束，已停止自动刷新——稍后回到这里看结果'))
    return
  }
  clonePollTimer = setTimeout(() => pollClone(id, tries + 1), 1500)
}
async function runClone() {
  const id = cur.value.id
  cloneBusy.value = true
  setStatus(t('提交克隆…'))
  try {
    await put(`/api/voices/${id}`, payload())
    await post(`/api/voices/${id}/clone`, {})
    setStatus(t('克隆已提交，完成后这条音色自动切到 MiniMax 引擎'))
    await pollClone(id)
  } catch (e) { cloneBusy.value = false; setStatus(e.message, true) }
}
async function audition() {
  setStatus(t('先保存再合成试听…'))
  try {
    await put(`/api/voices/${cur.value.id}`, payload())
    const r = await post(`/api/voices/${cur.value.id}/sample`, { text: form.sample_text || null })
    sampleUrl.value = r.sample_url
    await loadVoices(); setStatus(t('试听已生成'))
  } catch (e) { setStatus(e.message, true) }
}
</script>

<template>
  <div class="ws">
    <div class="ws-center" style="max-width:440px;border-right:1px solid var(--border)">
      <div class="row between" style="margin-bottom:8px">
        <h3 style="font-size:16px">{{ $t('音色库') }}</h3>
        <button class="btn sm" @click="create">{{ $t('+ 新增') }}</button>
      </div>
      <p class="hint" style="margin-bottom:10px" v-html="$t(INTRO_HTML)"></p>
      <div class="item-list">
        <router-link v-for="v in voices" :key="v.id" class="item" :class="{ cur: v.id === cur?.id }" :to="`/voices/${v.id}`">
          <span class="thumb sq">🎙</span>
          <span class="body">
            <span class="title">{{ v.name }} <span class="pill">{{ $t(ENGINE_LABEL[v.engine] || 'Edge') }}</span><span v-if="v.clone?.status === 'ready'" class="pill">{{ $t('已克隆') }}</span></span>
            <span class="sub">{{ v.voice || $t('（无 TTS 音色，只有提示词）') }}{{ v.rate ? ' · ' + v.rate : '' }}{{ v.pitch ? ' · ' + v.pitch : '' }}</span>
            <span class="sub">{{ v.description || $t('（没有音色提示词）') }}</span>
          </span>
        </router-link>
      </div>
      <div v-if="!voices.length" class="empty">{{ $t('还没有音色。点「新增」建第一个。') }}</div>
    </div>
    <div class="ws-center" v-if="cur" style="max-width:720px">
      <div class="row between" style="margin-bottom:10px">
        <h3 style="font-size:16px">{{ cur.name }} <span class="mono hint">{{ cur.id }}</span></h3>
        <button class="btn sm danger" @click="remove">{{ $t('删除') }}</button>
      </div>
      <div class="field"><label>{{ $t('名字') }}</label><input v-model="form.name" /></div>

      <div class="card" style="margin:6px 0 12px">
        <h3>{{ $t('TTS 音色（决定真实音色：旁白、配音、试听）') }}</h3>
        <div class="grid2">
          <div class="field"><label>{{ $t('引擎') }}</label>
            <select v-model="form.engine" data-test="engine-select"><option value="edge">{{ $t('Edge TTS（免费，微软在线音色）') }}</option><option value="settings">{{ $t('模型设置里的语音模型') }}</option><option v-if="canClone || form.engine === 'minimax'" value="minimax">{{ $t('MiniMax（系统音色或克隆音色）') }}</option></select></div>
          <div class="field" v-if="form.engine === 'edge'"><label>{{ $t('语言') }}</label>
            <select v-model="locale"><option value="zh">{{ $t('中文') }}</option><option value="en">{{ $t('英文') }}</option><option value="zh,en">{{ $t('中英文') }}</option><option value="ja">{{ $t('日文') }}</option></select></div>
        </div>
        <div class="field"><label>{{ $t('音色') }} <span v-if="catalogLoading">{{ $t('（读取目录…）') }}</span></label>
          <select v-if="catalog.voices.length" v-model="form.voice">
            <option value="">{{ $t('（不用 TTS，只用提示词）') }}</option>
            <option v-for="v in catalog.voices" :key="v.voice" :value="v.voice">{{ v.voice }}{{ v.gender ? ' · ' + (v.gender === 'Female' ? $t('女') : v.gender === 'Male' ? $t('男') : v.gender) : '' }}{{ v.personalities?.length ? ' · ' + v.personalities.join('/') : '' }}</option>
          </select>
          <input v-else v-model="form.voice" :placeholder="$t('音色 id（按服务商文档填）')" />
          <span v-if="catalog.note" class="hint">{{ catalog.note }}</span>
          <span v-else-if="voiceInCatalog" class="hint">{{ voiceInCatalog.label }}</span>
        </div>
        <div class="grid2" v-if="form.engine === 'edge' || form.engine === 'minimax'">
          <div class="field"><label>{{ $t('语速（如 -4%、+10%）') }}</label><input v-model="form.rate" /></div>
          <div class="field" v-if="form.engine === 'edge'"><label>{{ $t('音调（如 -6Hz、+2Hz）') }}</label><input v-model="form.pitch" /></div>
        </div>
        <div class="field"><label>{{ $t('试听文本') }}</label><input v-model="form.sample_text" :placeholder="$t('留空用默认句子')" /></div>
        <div class="row">
          <button class="btn sm" :disabled="!form.voice" @click="audition">{{ $t('试听') }}</button>
          <audio v-if="sampleUrl" :src="sampleUrl" controls style="height:32px"></audio>
        </div>
      </div>

      <div class="card" style="margin:0 0 12px" data-test="voice-clone">
        <h3>{{ $t('参考音频与克隆（MiniMax）') }}</h3>
        <p class="hint">{{ $t('上传 10 秒到 5 分钟的清晰人声（wav / mp3 / m4a，20MB 以内），点「克隆音色」后这条音色用 MiniMax 合成，声音就是参考音频里的那个人；克隆成功会自动把引擎切到 MiniMax。这条路径还没经过充分实测。') }}</p>
        <div class="row">
          <!-- 后端按内容认：别的音频格式（AIFF、手机录音…）读得出就转成 wav，所以选文件时别把它们藏起来 -->
          <input type="file" accept="audio/*,.wav,.mp3,.m4a,.aiff,.aif" data-test="ref-audio-file" @change="uploadReference" />
          <span v-if="cur.reference_audio" class="mono hint" data-test="ref-audio-name">{{ refName }}</span>
        </div>
        <audio v-if="refUrl" :src="refUrl" controls style="height:32px;margin-top:6px" data-test="ref-audio-player"></audio>
        <div class="row" style="margin-top:8px">
          <button class="btn sm" data-test="clone-run" :disabled="!cur.reference_audio || !canClone || cloneBusy || cloneStatus === 'cloning'" @click="runClone">{{ $t('克隆音色') }}</button>
          <span class="pill" data-test="clone-status" :class="{ bad: cloneStatus === 'failed', ok: cloneStatus === 'ready' }">{{ cloneLabel }}</span>
        </div>
        <span v-if="!canClone" class="hint" data-test="clone-no-key">{{ $t('还没配 MiniMax 密钥：去模型设置，把语音（或视频）来源选 MiniMax 并填 key。') }}</span>
      </div>

      <div class="card" style="margin:0 0 12px">
        <div class="row between"><h3>{{ $t('音色提示词（进视频提示词：让各镜台词声音像同一个人）') }}</h3>
          <div class="row" style="gap:4px">
            <button class="btn sm" :class="{ primary: form.description_mode === 'traits' }" @click="form.description_mode = 'traits'; refreshPreview()">{{ $t('按维度生成') }}</button>
            <button class="btn sm" :class="{ primary: form.description_mode === 'custom' }" @click="form.description_mode = 'custom'">{{ $t('手写') }}</button>
          </div>
        </div>
        <template v-if="form.description_mode === 'traits'">
          <div class="row" style="margin:6px 0 8px"><button class="btn sm" :disabled="!form.voice" @click="draftFromVoice">{{ $t('从上面的音色填初稿') }}</button><span class="hint">{{ $t('目录里带性别、口音、性格标签；年龄和音色关键词要自己补。') }}</span></div>
          <div class="grid2">
            <div class="field"><label>{{ $t('性别') }}</label><select v-model="traits.gender"><option v-for="[v, l] in OPT.gender" :key="v" :value="v">{{ $t(l) }}</option></select></div>
            <div class="field"><label>{{ $t('年龄段') }}</label><select v-model="traits.age"><option v-for="[v, l] in OPT.age" :key="v" :value="v">{{ $t(l) }}</option></select></div>
            <div class="field"><label>{{ $t('语速') }}</label><select v-model="traits.pace"><option v-for="[v, l] in OPT.pace" :key="v" :value="v">{{ $t(l) }}</option></select></div>
            <div class="field"><label>{{ $t('音量') }}</label><select v-model="traits.volume"><option v-for="[v, l] in OPT.volume" :key="v" :value="v">{{ $t(l) }}</option></select></div>
            <div class="field"><label>{{ $t('音色关键词（顿号或逗号分隔：清亮、略带沙哑、低沉、温暖…）') }}</label><input v-model="traits.timbre" /></div>
            <div class="field"><label>{{ $t('口音 / 语言（标准普通话、台湾腔、美式英语…）') }}</label><input v-model="traits.accent" /></div>
            <div class="field"><label>{{ $t('情绪基调（平静、明快、疲惫…）') }}</label><input v-model="traits.mood" /></div>
          </div>
          <div class="field"><label>{{ $t('中文提示词（自动生成，保存后进项目）') }}</label><div class="note info small">{{ preview.zh || '—' }}</div></div>
          <div class="field"><label>{{ $t('英文提示词（英文项目用）') }}</label><div class="note info small">{{ preview.en || '—' }}</div></div>
        </template>
        <template v-else>
          <div class="field"><label>{{ $t('手写描述（性别、年龄、音色、语速、音量；例：二十六七岁的女声，音量正常，语速中等，口齿清楚）') }}</label><textarea v-model="form.description" rows="3"></textarea></div>
        </template>
      </div>

      <div class="row"><button class="btn primary sm" @click="save">{{ $t('保存') }}</button><span class="status" :class="{ err: statusErr }">{{ status }}</span></div>
      <p class="hint" style="margin-top:14px">{{ $t('绑定是拷贝：改这里不会改已经绑过的项目；要同步，去项目的角色页重新绑一次。') }}</p>
    </div>
    <div class="ws-center" v-else><div class="empty">{{ $t('从左边选一个音色，或新增') }}</div></div>
  </div>
</template>
