<script setup>
// 配音页（docs/podcast-spec.md §6）：主播音色绑定（下拉 + 试听链接到音色库）、停顿两档、三段音乐上传、「合成整期」（cost_note）、
// 逐句列表（<audio>）+ 成品播放器 + 章节时间码 + 节目单 / 逐字稿下载。稿子改过显示「稿子改过，重新合成」。
// 底部「导出交付包」：起后台任务 pc-export 并轮询，已有交付包列表（名字 / 大小 / 时间 / 下载）。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { put, post, fileToBase64 } from '../../api.js'
import { store, withBusy, loadVoices } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import {
  pc, specBody, refreshPodcast, runPodcastTask, fmtClock, fmtBytes, fmtWhen, loadExports, normalizeExports, MUSIC_SLOTS,
} from '../../podcast.js'

const s = computed(() => store.podcast)
const d = computed(() => store.podcastDetail)
const form = reactive({ hosts: null, pause: { line: 0.35, segment: 1.2 }, bed_gain: 0.18, duck: 0.45 })
const dirty = ref(false)
const saving = ref('')
const voices = computed(() => (store.voices || []).filter(v => v.id))
// 只有文字描述、没有 TTS 音色（也没克隆好）的档案不能出声：下拉里标出来并禁选，不然保存 200 没告警、成片是占位音色
const descriptionOnly = (v) => typeof v.voice === 'string' && !v.voice.trim() && !(v.clone && v.clone.status === 'ready' && v.clone.voice_id)

function sync() {
  if (!s.value) return
  form.hosts = JSON.parse(JSON.stringify(s.value.hosts || []))
  form.pause = { line: 0.35, segment: 1.2, ...(s.value.pause || {}) }
  form.bed_gain = s.value.music?.bed_gain ?? 0.18
  form.duck = s.value.music?.duck ?? 0.45
  dirty.value = false
}
watch(s, () => { if (!dirty.value) sync() }, { immediate: true })
onMounted(async () => { if (!store.voices) await loadVoices() })

const audio = computed(() => d.value?.audio || {})
const lines = computed(() => audio.value.lines || [])
const chapters = computed(() => audio.value.chapters || [])
const texts = computed(() => d.value?.texts || {})
const busy = computed(() => !!d.value?.busy?.audio)
const hostInfo = (id) => (d.value?.hosts || []).find(h => h.id === id) || null
const hostName = (id) => (s.value?.hosts || []).find(h => h.id === id)?.name || id
const musicFile = (slot) => { const p = String(s.value?.music?.[slot] || ''); return p ? p.split('/').pop() : '' }
// 内联音色档案（{engine, voice}）不在下拉里：显示成一项，别把它悄悄改掉
const inlineVoice = (h) => (h.voice && typeof h.voice === 'object') ? h.voice : null
const voiceValue = (h) => (typeof h.voice === 'string' ? h.voice : '__inline__')
function setVoice(h, v) { if (v !== '__inline__') h.voice = v; touch() }

function touch() { dirty.value = true }

async function save() {
  saving.value = t('保存中…')
  const body = {
    ...specBody(s.value),
    hosts: form.hosts,
    pause: { line: Number(form.pause.line), segment: Number(form.pause.segment) },
    music: { ...(s.value.music || {}), bed_gain: Number(form.bed_gain), duck: Number(form.duck) },
  }
  await withBusy(async () => {
    try { await put(pc(s.value.id), body); dirty.value = false; await refreshPodcast(); saving.value = ''; toast(t('已保存')) }
    catch (e) { saving.value = ''; await notice(e.message) }
  })
  return !dirty.value
}

// 合成整期：逐句 TTS + 混音一次跑完。edge 免费；音色库里别的引擎按各自计费（后端 cost_note 会说）
async function synthesize({ force = false } = {}) {
  if (dirty.value && !(await save())) return
  const ok = await confirm(force
    ? t('把全部台词重新合成一遍再混音（不管有没有改过）。占位音色与 edge 免费；音色库里其他引擎按各自计费。继续？')
    : t('逐句合成没合成过或改过的台词，再混成一期。占位音色与 edge 免费；音色库里其他引擎按各自计费。继续？'),
  { confirmLabel: t('合成'), localRole: 'voice' })
  if (!ok) return
  await runPodcastTask(`${pc(s.value.id)}/synthesize`, force ? { force: true } : {}, t('合成整期'))
}

async function upload(slot, ev) {
  const file = (ev.target.files || [])[0]; ev.target.value = ''
  if (!file) return
  await withBusy(async () => {
    try {
      const data_base64 = await fileToBase64(file)
      await post(`${pc(s.value.id)}/assets`, { filename: file.name, data_base64, slot })
      toast(t('已上传 {name}', { name: file.name }))
      await refreshPodcast()
    } catch (e) { await notice(t('上传失败：{msg}', { msg: e.message })) }
  })
}

// ---- 导出交付包：不花钱、不问确认；列表以 GET /export 为准，拉不到就用 detail.exports
const exportsLoaded = ref(null)
const exportsList = computed(() => exportsLoaded.value ?? normalizeExports(s.value?.id, d.value?.exports || []))
const exportBusy = computed(() => !!d.value?.busy?.export)
const canExport = computed(() => Number(audio.value.synthesized || 0) > 0 || !!audio.value.mix_url)
async function reloadExports() { if (s.value?.id) exportsLoaded.value = await loadExports(s.value.id) }
onMounted(reloadExports)
watch(() => s.value?.id, (id, old) => { if (id && id !== old) { exportsLoaded.value = null; reloadExports() } })
async function exportPackage() {
  await runPodcastTask(`${pc(s.value.id)}/export`, {}, t('导出交付包'))
  await reloadExports()
}
</script>

<template>
  <div class="ws-content" v-if="s && form.hosts">
    <div class="ws-center" style="max-width:960px" data-test="podcast-audio">
      <!-- 音色与停顿 -->
      <div class="card">
        <div class="row between">
          <h3>{{ $t('主播音色') }}</h3>
          <div class="row">
            <span v-if="saving" class="status">{{ saving }}</span>
            <button class="btn primary sm" :disabled="!dirty" data-test="audio-save" @click="save">{{ $t('保存') }}</button>
          </div>
        </div>
        <p class="hint">{{ $t('每位主播绑一个音色库档案。留空走占位音色（免费，按语言轮着分），能出声但两个人听起来分不清；两位主播同一个音色也一样。') }}</p>
        <div v-for="h in form.hosts" :key="h.id" class="row" style="margin-top:6px;align-items:center" data-test="audio-host">
          <b style="width:110px">{{ h.name || h.id }}</b>
          <select :value="voiceValue(h)" data-test="audio-host-voice" style="flex:1" @change="setVoice(h, $event.target.value)">
            <option value="">{{ $t('占位音色（免费，可稍后换）') }}</option>
            <option v-if="inlineVoice(h)" value="__inline__">{{ $t('内联档案') }}：{{ inlineVoice(h).engine }} · {{ inlineVoice(h).voice }}</option>
            <option v-for="v in voices" :key="v.id" :value="v.id" :disabled="descriptionOnly(v)">{{ v.name || v.id }}{{ descriptionOnly(v) ? ` · ${$t('只有描述，不能出声')}` : '' }}</option>
          </select>
          <span v-if="hostInfo(h.id)?.voice_label" class="hint small" data-test="audio-host-label">{{ hostInfo(h.id).voice_label }}</span>
          <span v-if="hostInfo(h.id)?.missing" class="pill bad" data-test="audio-host-missing">{{ $t('档案已不存在') }}</span>
          <span v-else-if="hostInfo(h.id)?.no_tts" class="pill bad" data-test="audio-host-no-tts">{{ $t('档案只有描述') }}</span>
          <span v-else-if="hostInfo(h.id)?.placeholder" class="pill warn" data-test="audio-host-placeholder">{{ $t('占位') }}</span>
          <router-link v-if="typeof h.voice === 'string' && h.voice" class="link small" :to="`/voices/${h.voice}`" data-test="audio-host-listen">{{ $t('试听') }}</router-link>
        </div>
        <p class="hint" v-if="!voices.length">{{ $t('音色库是空的——') }}<router-link to="/voices">{{ $t('去音色库建一个档案') }}</router-link></p>
        <div class="row" style="margin-top:10px">
          <div class="field">
            <label>{{ $t('句间停顿（秒）') }}</label>
            <input v-model="form.pause.line" type="number" step="0.05" min="0" max="5" data-test="pause-line" @input="touch" />
          </div>
          <div class="field">
            <label>{{ $t('环节间停顿（秒）') }}</label>
            <input v-model="form.pause.segment" type="number" step="0.1" min="0" max="5" data-test="pause-segment" @input="touch" />
          </div>
        </div>
        <p class="hint small">{{ $t('cue 里标了「停顿」的句子前面再加 0.6 秒。') }}</p>
      </div>

      <!-- 音乐 -->
      <div class="card" data-test="audio-music">
        <h3>{{ $t('音乐（可空）') }}</h3>
        <p class="hint">{{ $t('片头在冷开场之后、开场之前整段播；垫乐循环铺底，说话时按 duck 压低；片尾在最后一句之后播。mp3 / wav / m4a，≤ 30MB。') }}</p>
        <div v-for="(label, slot) in MUSIC_SLOTS" :key="slot" class="row" style="margin-top:6px;align-items:center" :data-test="`music-${slot}`">
          <b style="width:70px">{{ $t(label) }}</b>
          <span class="mono small" style="flex:1" data-test="music-file">{{ musicFile(slot) || '—' }}</span>
          <label class="btn sm" style="cursor:pointer">{{ $t('上传') }}<input type="file" accept="audio/*,.mp3,.wav,.m4a,.aiff,.aif" style="display:none" data-test="music-upload" @change="upload(slot, $event)" /></label>
        </div>
        <div class="row" style="margin-top:8px">
          <div class="field">
            <label>{{ $t('垫乐音量（0–1）') }}</label>
            <input v-model="form.bed_gain" type="number" step="0.01" min="0" max="1" data-test="bed-gain" @input="touch" />
          </div>
          <div class="field">
            <label>{{ $t('说话时压到（0–1）') }}</label>
            <input v-model="form.duck" type="number" step="0.05" min="0" max="1" data-test="duck" @input="touch" />
          </div>
        </div>
      </div>

      <!-- 合成 -->
      <div class="card">
        <h3>{{ $t('合成') }}</h3>
        <div class="row" style="margin-top:6px">
          <button class="btn primary" :disabled="busy" data-test="synth-btn" @click="synthesize()">{{ $t('合成整期') }}</button>
          <button class="btn" :disabled="busy || !lines.length" data-test="synth-force" @click="synthesize({ force: true })">{{ $t('全部重合成') }}</button>
          <span v-if="busy" class="status">{{ $t('正在合成…') }}</span>
        </div>
        <div v-if="audio.stale && audio.synthesized > 0" class="note warn" style="margin-top:8px" data-test="audio-stale">{{ $t('稿子改过，重新合成') }}</div>
        <div v-else-if="audio.mix_stale && audio.mix_url" class="note warn" style="margin-top:8px" data-test="audio-mix-stale">{{ $t('停顿 / 音乐改过，成品还是按旧参数混的——再点一次「合成整期」重混') }}</div>
        <div v-if="audio.ffmpeg === false" class="note" style="margin-top:8px" data-test="audio-no-ffmpeg">{{ $t('没找到 ffmpeg：只出了逐句音频，没有混音成品。装好 ffmpeg 后再点「合成整期」。') }}</div>
      </div>

      <!-- 成品 -->
      <div class="card" v-if="audio.mix_url" data-test="audio-mix">
        <h3>{{ $t('成品') }} <span class="hint" v-if="audio.mix_duration">{{ fmtClock(audio.mix_duration) }}</span></h3>
        <audio controls preload="none" :src="audio.mix_url" data-test="mix-player" style="width:100%;margin-top:6px"></audio>
        <div class="row" style="margin-top:8px">
          <a class="btn sm" :href="audio.mix_url" download data-test="download-mix">{{ $t('下载 mp3') }}</a>
          <a v-if="texts.shownotes_url" class="btn sm" :href="texts.shownotes_url" download data-test="download-shownotes">{{ $t('下载节目单') }}</a>
          <a v-if="texts.transcript_url" class="btn sm" :href="texts.transcript_url" download data-test="download-transcript">{{ $t('下载逐字稿') }}</a>
        </div>
        <div v-if="chapters.length" style="margin-top:8px" data-test="audio-chapters">
          <div class="small" style="font-weight:600">{{ $t('章节') }}</div>
          <div v-for="c in chapters" :key="c.id" class="small" data-test="audio-chapter"><span class="mono">{{ fmtClock(c.start) }}</span> {{ c.title || c.id }}</div>
        </div>
      </div>

      <!-- 逐句 -->
      <div class="card" data-test="audio-lines">
        <h3>{{ $t('逐句') }} <span class="hint">{{ lines.length }}</span></h3>
        <div v-if="!lines.length" class="hint">{{ $t('还没合成过。点上面的「合成整期」。') }}</div>
        <div v-for="l in lines" :key="`${l.segment}-${l.index}`" style="padding:6px 0;border-bottom:1px solid var(--border)" data-test="audio-line">
          <div class="small">
            <span class="mono hint">{{ l.segment }} · {{ l.index }}</span>
            <b> {{ hostName(l.who) }}</b>：{{ l.text }}
            <span v-if="l.stale && l.file" class="pill warn" data-test="audio-line-stale">{{ $t('改过') }}</span>
            <span v-if="l.duration" class="hint"> · {{ fmtClock(l.duration) }}</span>
          </div>
          <audio v-if="l.url" controls preload="none" :src="l.url" data-test="line-audio" style="width:100%;margin-top:4px"></audio>
        </div>
      </div>

      <!-- 导出交付包 -->
      <div class="card" data-test="audio-export">
        <h3>{{ $t('导出交付包') }}</h3>
        <p class="hint">{{ $t('包里有：项目文件、成品 mp3（混过音才有）、节目单 / 逐字稿 / 章节、逐句 mp3、AI 生成说明与工具清单、manifest 与 sha256 校验；打进 output/podcast/{id}/09_export/，从不覆盖已导出的包。设置、密钥、上传的音乐一律不进包。', { id: s.id }) }}</p>
        <div class="row" style="margin-top:6px">
          <button class="btn primary" :disabled="exportBusy || !canExport" data-test="export-btn" @click="exportPackage">{{ $t('导出交付包') }}</button>
          <span v-if="exportBusy" class="status">{{ $t('正在打包…') }}</span>
          <span v-else-if="!canExport" class="hint" data-test="export-need-audio">{{ $t('先合成，才有东西可导。') }}</span>
        </div>
        <div v-if="!exportsList.length" class="hint" style="margin-top:8px" data-test="export-empty">{{ $t('还没有导出过。') }}</div>
        <div v-for="e in exportsList" :key="e.name" class="row between small" style="margin-top:6px;padding:4px 0;border-top:1px solid var(--border-soft)" data-test="export-row">
          <span class="mono">{{ e.name }}<span class="hint"> · {{ fmtBytes(e.size) }}<span v-if="e.created"> · {{ fmtWhen(e.created) }}</span></span>
            <span v-if="e.mixed === false" class="pill warn" data-test="export-no-mix">{{ $t('没混音') }}</span></span>
          <a class="btn sm" :href="e.url" download data-test="export-download">{{ $t('下载') }}</a>
        </div>
      </div>
    </div>
  </div>
</template>
