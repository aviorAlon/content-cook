<script setup>
// 成片：装配（后台任务）、预览（/artifact/final）、下载、朗读稿导出（zh / en xlsx）、可选 16:9、BGM / 翻页音效上传。
import { computed, ref, watch } from 'vue'
import { put, post, fileToBase64 } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { pb, pbArtifact, progressOf, refreshBook, runBookTask } from '../../picturebook.js'

const b = computed(() => store.book)
const s = computed(() => store.bookSummary)
const prog = computed(() => progressOf(s.value, b.value?.pages?.length))
const total = computed(() => prog.value.pages.total || b.value?.pages?.length || 0)
const ready = computed(() => total.value > 0 && prog.value.pages.done >= total.value && prog.value.narration.done >= total.value)
const landscape = ref(false)
watch(() => b.value?.delivery?.landscape_variant, (v) => { landscape.value = !!v }, { immediate: true })
const d = computed(() => store.bookDetail)
const stamp = computed(() => s.value?.updated_at || 0)
const finalUrl = computed(() => d.value?.final_url || s.value?.final_url || pbArtifact.final(b.value.id, stamp.value))
const finalInfo = computed(() => {
  const f = s.value?.final ?? s.value?.progress?.final
  const obj = f && typeof f === 'object' ? f : {}
  return { path: d.value?.final_path || obj.path || '', bytes: d.value?.final_size_bytes ?? obj.bytes ?? null, landscape_url: d.value?.landscape_url || obj.landscape_url || null }
})
const fmtMB = (n) => (n == null ? '' : (n / 1048576).toFixed(1) + ' MB')

function body(patch) {
  const { output_root: _o, project_root: _r, ...spec } = b.value
  return { ...spec, ...patch }
}
async function saveLandscape() {
  await withBusy(async () => {
    try { await put(pb(b.value.id), body({ delivery: { ...(b.value.delivery || {}), landscape_variant: landscape.value } })); await refreshBook(); toast(landscape.value ? t('装配时会多出一版 16:9') : t('只出 9:16 主版')) }
    catch (e) { landscape.value = !landscape.value; await notice(e.message) }
  })
}
async function assemble() {
  await runBookTask(`${pb(b.value.id)}/assemble`, {}, t('装配成片'))
}
// BGM / 翻页音效：POST /assets/{kind}/{key}（kind = music | sfx，key 随意；后端按内容摘要存文件，不覆盖）
async function upload(ev, kind) {
  const file = ev.target.files?.[0]; ev.target.value = ''
  if (!file) return
  await withBusy(async () => {
    try {
      const data_base64 = await fileToBase64(file)
      await post(`${pb(b.value.id)}/assets/${kind}/${kind}`, { filename: file.name, data_base64 })
      await refreshBook(); toast(t('已上传 {name}', { name: file.name }))
    } catch (e) { await notice(t('上传失败：{msg}', { msg: e.message })) }
  })
}
const baseName = (v) => v ? String(v).split(/[\\/]/).pop() : ''
</script>

<template>
  <div class="ws-content" v-if="b">
    <div class="ws-center" style="max-width:820px" data-test="book-final">
      <h3 style="font-size:16px;margin-bottom:8px">{{ $t('成片') }}</h3>
      <div class="card">
        <p class="hint">{{ $t('装配在本机跑（ffmpeg，不花钱）：每页一条 Ken Burns，翻页 xfade + 翻页音效，字幕中上英下、当前朗读句暖黄高亮，朗读 + BGM + 音效混音后 loudnorm。主版 9:16。') }}</p>
        <p class="hint" v-if="!ready">{{ $t('现在：插画 {a}/{n}，朗读 {b}/{n}。都齐了再装配才是完整的书；现在装配会只用已有的页。', { a: prog.pages.done, b: prog.narration.done, n: total }) }}</p>
        <div class="row" style="margin-top:8px">
          <button class="btn primary" :disabled="!!store.gen || !b.pages.length" data-test="assemble" @click="assemble">{{ prog.final ? $t('重新装配') : $t('装配成片') }}</button>
          <label class="row small" style="gap:6px"><input type="checkbox" v-model="landscape" data-test="landscape" @change="saveLandscape" /> {{ $t('同时出 16:9 版') }}</label>
          <span v-if="store.gen" class="status">{{ store.gen.text }}</span>
        </div>
      </div>
      <div v-if="prog.final" class="card" data-test="final-card">
        <div class="row between" style="margin-bottom:8px">
          <div><b>{{ $t('成片') }}</b><div class="hint mono" v-if="finalInfo.path || finalInfo.bytes">{{ finalInfo.path }}{{ finalInfo.bytes ? ' · ' + fmtMB(finalInfo.bytes) : '' }}</div></div>
          <div class="row">
            <a class="btn" :href="finalUrl" download data-test="final-download" style="text-decoration:none">{{ $t('下载 mp4') }}</a>
            <a v-if="finalInfo.landscape_url" class="btn" :href="finalInfo.landscape_url" download style="text-decoration:none">{{ $t('下载 16:9') }}</a>
          </div>
        </div>
        <video :src="finalUrl" controls preload="metadata" data-test="final-video" style="width:100%;max-width:420px;aspect-ratio:9/16;background:#000;border-radius:var(--r-md);display:block"></video>
      </div>
      <div class="card">
        <h3>{{ $t('朗读稿') }}</h3>
        <p class="hint">{{ $t('交给真人朗读或别的 TTS 用的朗读稿（xlsx）：一行一页，列是 页 / 说话人 / 文本 / 语气提示（英文稿的表头是英文）。录好的音频按 P01_zh.mp3 这样命名放回朗读目录就能对上。') }}</p>
        <div class="row" style="margin-top:6px">
          <a class="btn sm" :href="pbArtifact.scriptSheet(b.id, 'zh')" download data-test="sheet-zh" style="text-decoration:none">{{ $t('导出中文朗读稿') }}</a>
          <a class="btn sm" :href="pbArtifact.scriptSheet(b.id, 'en')" download data-test="sheet-en" style="text-decoration:none">{{ $t('导出英文朗读稿') }}</a>
        </div>
      </div>
      <div class="card">
        <h3>{{ $t('配乐与翻页音效') }}</h3>
        <p class="hint">{{ $t('BGM 默认 -12 dB 垫在朗读下面、封底慢慢收掉；翻页音效落在每次翻页的正中间。都可空。') }}</p>
        <div class="row" style="margin-top:6px;gap:16px">
          <div><span class="hint mono">{{ baseName(b.music?.file) || $t('（无 BGM）') }}</span> <label class="btn sm" style="cursor:pointer">{{ $t('上传 BGM') }}<input type="file" accept="audio/*" style="display:none" @change="upload($event, 'music')" /></label></div>
          <div><span class="hint mono">{{ baseName(b.page_turn_sfx) || $t('（无翻页音效）') }}</span> <label class="btn sm" style="cursor:pointer">{{ $t('上传翻页音效') }}<input type="file" accept="audio/*" style="display:none" @change="upload($event, 'sfx')" /></label></div>
        </div>
      </div>
    </div>
  </div>
</template>
