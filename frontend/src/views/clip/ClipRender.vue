<script setup>
// 出片（docs/clip-spec.md §6）：首帧模式（不用 / 上传 / 先出一张）→ 出片（确认花钱）→ 进度 →
// 历次片子播放与「采用」→ 下载。一次请求 = 一条片子，从不覆盖。
import { computed, ref } from 'vue'
import { fileToBase64, post } from '../../api.js'
import { store, roleOk, withBusy } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { clipUrl, refreshClip, runClipTask, attempts, currentUrl, frameUrl, progressOf, FIRST_FRAME_MODES } from '../../clip.js'
import LocalTip from '../../components/LocalTip.vue'

const c = computed(() => store.clip)
const d = computed(() => store.clipDetail)
const stamp = ref(0)
const busy = computed(() => !!store.gen)
const prog = computed(() => progressOf(d.value))
const takes = computed(() => attempts(d.value))
const video = computed(() => currentUrl(d.value, stamp.value))
const frame = computed(() => frameUrl(d.value, stamp.value))
const mode = computed(() => d.value?.first_frame?.mode || c.value?.first_frame?.mode || 'none')
const frameError = computed(() => d.value?.first_frame?.error || '')
const costNote = computed(() => d.value?.cost_note || '')
const fmtBytes = (n) => (n == null ? '' : n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.round(n / 1024) + ' KB')
const stamped = (a) => (a.url ? `${a.url}${a.url.includes('?') ? '&' : '?'}t=${stamp.value}` : null)
const when = (a) => (a.created_at ? new Date(Number(a.created_at) * 1000).toLocaleString() : '')

async function setMode(next) {
  if (next === mode.value) return
  if (next === 'generate') { await generateFrame(); return }
  await withBusy(async () => {
    try {
      await post(`${clipUrl(c.value.id)}/first-frame`, { mode: next })
      await refreshClip()
    } catch (e) { await notice(e.message) }
  })
}

async function generateFrame(force = false) {
  const ok = await confirm(t('出一张首帧？会发 1 次生图请求（花钱）。'), { confirmLabel: t('出首帧'), localRole: 'image' })
  if (!ok) return
  await runClipTask(`${clipUrl(c.value.id)}/first-frame`, { mode: 'generate', force }, t('出首帧'))
  stamp.value = Date.now()
}

async function upload(event) {
  const file = event.target.files?.[0]
  if (!file) return
  await withBusy(async () => {
    try {
      const data_base64 = await fileToBase64(file)
      await post(`${clipUrl(c.value.id)}/first-frame`, { mode: 'upload', filename: file.name, data_base64 })
      await refreshClip()
      stamp.value = Date.now()
      toast(t('首帧已上传'))
    } catch (e) { await notice(e.message) }
  })
  event.target.value = ''
}

async function render(force = false) {
  const note = costNote.value || t('1 次视频请求')
  const ok = await confirm(t('开始出片？{note}', { note }), { confirmLabel: t('出片'), localRole: 'video' })
  if (!ok) return
  await runClipTask(`${clipUrl(c.value.id)}/generate`, { force }, t('出片'))
  stamp.value = Date.now()
}

async function pick(a) {
  await withBusy(async () => {
    try {
      await post(`${clipUrl(c.value.id)}/pick`, { attempt: a.file })
      await refreshClip()
      stamp.value = Date.now()
      toast(t('已采用 {name}', { name: a.file }))
    } catch (e) { await notice(e.message) }
  })
}
</script>

<template>
  <div class="ws-content" v-if="c">
    <div class="ws-center" style="max-width:900px" data-test="clip-render">
      <div class="row between" style="margin-bottom:8px">
        <div>
          <h3 style="font-size:16px">{{ $t('出片') }}</h3>
          <p class="hint">{{ $t('一次请求 = 一条片子，从不覆盖：每次出片都留一条，挑一条「采用」。请求没变且片子已在的会跳过。') }}</p>
        </div>
        <div class="row">
          <button class="btn spend" :disabled="busy" data-test="render-go" @click="render(false)">{{ $t('出片') }}</button>
          <button class="btn" :disabled="busy || !prog.attempts" data-test="render-force" @click="render(true)">{{ $t('再出一条') }}</button>
        </div>
      </div>
      <div v-if="!roleOk('video')" class="note" data-test="render-no-model">{{ $t('还没配置视频模型——') }}<router-link to="/settings">{{ $t('去「模型设置」填好') }}</router-link><LocalTip role="video" /></div>
      <p class="hint" data-test="render-cost">{{ costNote }}</p>
      <div v-if="store.gen" class="status" data-test="render-status">{{ store.gen.text }}</div>

      <!-- 首帧 -->
      <div class="card" data-test="frame-card">
        <h3>{{ $t('首帧') }}</h3>
        <p class="hint">{{ $t('不用首帧 = 文生视频；用首帧 = 图生视频，第一帧长什么样你说了算。') }}</p>
        <div class="row" style="gap:10px;flex-wrap:wrap">
          <label v-for="m in FIRST_FRAME_MODES" :key="m.id" class="row" style="gap:5px;align-items:center;cursor:pointer">
            <input type="radio" :value="m.id" :checked="mode === m.id" :data-test="`frame-mode-${m.id}`" @change="setMode(m.id)" />
            <span>{{ $t(m.label) }}</span>
          </label>
        </div>
        <div v-if="mode === 'upload'" class="row" style="margin-top:8px">
          <input type="file" accept="image/png,image/jpeg,image/webp" data-test="frame-upload" @change="upload" />
        </div>
        <div v-if="mode === 'generate'" class="row" style="margin-top:8px">
          <button class="btn spend sm" :disabled="busy" data-test="frame-generate" @click="generateFrame(true)">{{ $t('重出首帧') }}</button>
        </div>
        <div v-if="frameError" class="note bad" data-test="frame-error">{{ frameError }}</div>
        <img v-if="frame" :src="frame" alt="" data-test="frame-image" style="max-width:320px;margin-top:8px;border-radius:var(--r-sm)" />
      </div>

      <!-- 当前采用的片子 -->
      <div v-if="video" class="card" data-test="take-current">
        <div class="row between">
          <h3>{{ $t('当前采用') }}</h3>
          <a class="btn sm" :href="video" download data-test="take-download" style="text-decoration:none">{{ $t('下载') }}</a>
        </div>
        <video :src="video" controls data-test="take-video" style="width:100%;max-height:420px;border-radius:var(--r-sm);background:#000"></video>
        <div class="hint mono small">{{ d?.spec?.current }}</div>
      </div>
      <p v-else class="hint" data-test="take-none">{{ $t('还没出过片。') }}</p>

      <!-- 历次 -->
      <div class="card" data-test="take-history">
        <h3>{{ $t('历次片子') }} <span class="pill" v-if="takes.length">{{ takes.length }}</span></h3>
        <p v-if="!takes.length" class="hint">{{ $t('每次出片都会在这里留一条，旧的不删。') }}</p>
        <div v-for="a in takes" :key="a.key" class="card" data-test="take-row" style="padding:8px 10px;margin-top:6px">
          <div class="row between">
            <span class="mono small">{{ a.file }}<span class="hint" v-if="when(a)"> · {{ when(a) }}</span><span class="hint" v-if="a.model"> · {{ a.model }}</span></span>
            <span class="row" style="gap:6px">
              <span class="pill ok" v-if="a.current" data-test="take-current-pill">{{ $t('已采用') }}</span>
              <button class="btn sm" v-else-if="a.url" data-test="take-pick" @click="pick(a)">{{ $t('采用这条') }}</button>
              <a class="btn sm" v-if="a.url" :href="stamped(a)" download style="text-decoration:none">{{ $t('下载') }}</a>
            </span>
          </div>
          <div v-if="a.error" class="note bad" data-test="take-error">{{ a.error }}</div>
          <video v-else-if="a.url" :src="stamped(a)" controls data-test="take-player" style="width:100%;max-height:260px;margin-top:6px;border-radius:var(--r-sm);background:#000"></video>
          <div v-else class="hint" data-test="take-inflight">{{ $t('提交了还没回来——服务重启后也会接着等。') }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
