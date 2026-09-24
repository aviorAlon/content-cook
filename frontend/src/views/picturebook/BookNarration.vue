<script setup>
// 朗读：中 / 英音色从音色库选、英文语速、停顿；「合成朗读」起后台任务；逐页试听（/artifact/audio/{pid}_{lang}）与实测时长。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { put } from '../../api.js'
import { store, withBusy, loadVoices } from '../../store.js'
import { confirm, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { pb, audioUrl, audioInfo, hasPageAudio, durationOf, timelinePage, refreshBook, runBookTask } from '../../picturebook.js'

const b = computed(() => store.book)
const s = computed(() => store.bookSummary)
// 音色库：/api/voices（共享服务）；拉不到时退回 detail 里带的档案列表
const voices = computed(() => { const v = (store.voices || []).filter(x => x.id); return v.length ? v : (store.bookDetail?.voices || []) })
const form = reactive({ zh_voice: '', en_voice: '', en_rate: '-8%', lead: 0.4, between: 0.6, tail: 0.8 })
const dirty = ref(false)
const error = ref('')
function sync() {
  const n = b.value?.narration || {}
  form.zh_voice = n.zh_voice || ''; form.en_voice = n.en_voice || ''; form.en_rate = n.en_rate || '-8%'
  form.lead = n.pause?.lead ?? 0.4; form.between = n.pause?.between ?? 0.6; form.tail = n.pause?.tail ?? 0.8
  dirty.value = false
}
watch(() => b.value?.id, sync, { immediate: true })
watch(() => b.value?.narration, () => { if (!dirty.value) sync() })
onMounted(async () => { if (!store.voices) await loadVoices() })
const touch = () => { dirty.value = true }
const langs = computed(() => { const m = b.value?.bilingual_mode; return m === 'zh_only' ? ['zh'] : m === 'en_only' ? ['en'] : ['zh', 'en'] })
const stamp = computed(() => s.value?.updated_at || b.value?.updated_at || 0)
const fmt = (sec) => sec == null ? '—' : `${Number(sec).toFixed(1)}s`
// 页时长：detail.timeline 给的（实测音频 + 停顿；没音频的页是估算）；没有时间线时按 spec §5 自己算
function pageSeconds(p) {
  const tl = timelinePage(p.id)
  if (tl && tl.duration != null) return Number(tl.duration)
  const zh = durationOf(p, 'zh', s.value), en = durationOf(p, 'en', s.value)
  if (zh == null && en == null) return null
  const extra = b.value?.age_band === '2-4' ? 0.2 : 0
  return (form.lead + extra) + (zh || 0) + (langs.value.length > 1 ? form.between + extra : 0) + (en || 0) + (form.tail + extra)
}
const totalSeconds = computed(() => {
  const tl = store.bookDetail?.timeline
  if (tl && tl.total != null) return Number(tl.total)
  let sum = 0, any = false
  for (const p of b.value?.pages || []) { const v = pageSeconds(p); if (v != null) { sum += v; any = true } }
  return any ? sum : null
})
const estimated = computed(() => !!store.bookDetail?.timeline?.estimated)

function body() {
  const { output_root: _o, project_root: _r, ...spec } = b.value
  return { ...spec, narration: { ...(spec.narration || {}), zh_voice: form.zh_voice || '', en_voice: form.en_voice || '', en_rate: form.en_rate.trim(), pause: { lead: Number(form.lead), between: Number(form.between), tail: Number(form.tail) } } }
}
async function save() {
  error.value = ''
  return withBusy(async () => {
    try { await put(pb(b.value.id), body()); dirty.value = false; await refreshBook(); toast(t('已保存')); return true }
    catch (e) { error.value = e.message; return false }
  })
}
async function generate(only = null) {
  if (dirty.value && !(await save())) return
  const n = only ? 1 : (b.value?.pages?.length || 0)
  if (!await confirm(t('合成朗读：{n} 页 × {l} 种语言（同文本同音色不重复合成）。继续？', { n, l: langs.value.length }), { confirmLabel: t('合成朗读') })) return
  await runBookTask(`${pb(b.value.id)}/narration/generate`, only ? { only, languages: langs.value } : { languages: langs.value }, t('合成朗读'))
}
</script>

<template>
  <div class="ws-content" v-if="b">
    <div class="ws-center" style="max-width:900px" data-test="book-narration">
      <div class="row between" style="margin-bottom:8px">
        <h3 style="font-size:16px">{{ $t('朗读') }}</h3>
        <div class="row">
          <button class="btn" :disabled="!dirty" data-test="narration-save" @click="save">{{ $t('保存设置') }}</button>
          <button class="btn spend" :disabled="!!store.gen || !b.pages.length" data-test="narration-generate" @click="generate()">{{ $t('合成朗读') }}</button>
        </div>
      </div>
      <div v-if="error" class="note bad" style="margin-bottom:8px">{{ error }}</div>
      <div class="card">
        <p class="hint">{{ $t('页时长跟着朗读走：先合成每页每种语言的音频，量出来的时长决定这一页停多久（前导 + 中文 + 间隔 + 英文 + 收尾；2–4 岁各停顿再 +0.2 秒）。音色来自音色库。') }}</p>
        <div class="grid2" style="margin-top:8px">
          <div class="field">
            <label>{{ $t('中文音色') }}</label>
            <select v-model="form.zh_voice" @change="touch" data-test="zh-voice">
              <option value="">{{ $t('（跟随模型设置的语音角色）') }}</option>
              <option v-for="v in voices" :key="v.id" :value="v.id">{{ v.name || v.id }}{{ v.voice ? ' · ' + v.voice : '' }}</option>
            </select>
          </div>
          <div class="field">
            <label>{{ $t('英文音色') }}</label>
            <select v-model="form.en_voice" @change="touch" data-test="en-voice">
              <option value="">{{ $t('（跟随模型设置的语音角色）') }}</option>
              <option v-for="v in voices" :key="v.id" :value="v.id">{{ v.name || v.id }}{{ v.voice ? ' · ' + v.voice : '' }}</option>
            </select>
          </div>
          <div class="field"><label>{{ $t('英文语速（叠加在音色档案之上，如 -8%）') }}</label><input v-model="form.en_rate" @input="touch" data-test="en-rate" /></div>
          <div class="field">
            <label>{{ $t('停顿（秒）：前导 / 中英间隔 / 收尾') }}</label>
            <div class="row" style="flex-wrap:nowrap">
              <input v-model.number="form.lead" @input="touch" type="number" step="0.1" min="0" style="width:33%" />
              <input v-model.number="form.between" @input="touch" type="number" step="0.1" min="0" style="width:33%" />
              <input v-model.number="form.tail" @input="touch" type="number" step="0.1" min="0" style="width:33%" />
            </div>
          </div>
        </div>
        <p class="hint" v-if="!voices.length">{{ $t('音色库是空的——') }}<router-link to="/voices">{{ $t('去音色库建一个档案') }}</router-link></p>
      </div>
      <div class="card">
        <div class="row between"><h3>{{ $t('逐页试听') }}</h3><span class="hint" v-if="totalSeconds != null">{{ estimated ? $t('估算全书 {s}', { s: fmt(totalSeconds) }) : $t('全书 {s}', { s: fmt(totalSeconds) }) }}</span></div>
        <table class="tbl" style="margin-top:6px" data-test="narration-table">
          <thead><tr><th style="width:60px">#</th><th>{{ $t('文本') }}</th><th v-for="l in langs" :key="l" style="width:260px">{{ l === 'zh' ? $t('中文') : 'English' }}</th><th style="width:70px">{{ $t('页时长') }}</th><th style="width:70px"></th></tr></thead>
          <tbody>
            <tr v-for="p in b.pages" :key="p.id" data-test="narration-row">
              <td class="mono"><b>{{ p.id }}</b></td>
              <td class="small"><div v-if="langs.includes('zh')">{{ p.zh }}</div><div v-if="langs.includes('en')" class="hint">{{ p.en }}</div></td>
              <td v-for="l in langs" :key="l">
                <template v-if="hasPageAudio(p, l, s)">
                  <audio :src="audioUrl(b.id, p, l, stamp)" controls preload="none" style="width:100%;height:30px" :data-test="`audio-${l}`"></audio>
                  <div class="hint mono">{{ fmt(durationOf(p, l, s)) }}<span v-if="audioInfo(p, l)?.placeholder" class="pill warn" style="margin-left:6px">{{ $t('占位音色') }}</span></div>
                </template>
                <span v-else class="hint">—</span>
              </td>
              <td class="mono hint">{{ fmt(pageSeconds(p)) }}</td>
              <td><button class="btn sm spend" :disabled="!!store.gen" @click="generate([p.id])">{{ $t('只合成这页') }}</button></td>
            </tr>
          </tbody>
        </table>
        <p v-if="!b.pages.length" class="hint" style="margin-top:6px">{{ $t('还没有页。') }}</p>
      </div>
    </div>
  </div>
</template>
