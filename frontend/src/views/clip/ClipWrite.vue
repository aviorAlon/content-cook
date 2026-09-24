<script setup>
// 写法（docs/clip-spec.md §6）：三段式编辑器 —— 【全片设定】表单 + 【分段时间线】节拍表（时间条 + 增删排序）+
// 【衔接与收尾】表单，右侧是编译出来的提示词与检查提醒。整条提示词由后端编译（保存后刷新），
// 每一拍那一行在本地实时算，边打字边看得到。
import { computed, ref, watch } from 'vue'
import { put } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import {
  clipUrl, refreshClip, specBody, ASPECT_RATIOS, DIALECTS, PROMPT_LANGUAGES,
  newBeat, moveBeat, removeBeat, renumber, timeline, previewLine, round, toCards, beatCards, clipCards,
} from '../../clip.js'
import StyleCardPicker from '../../components/StyleCardPicker.vue'

const c = computed(() => store.clip)
const d = computed(() => store.clipDetail)
const draft = ref(null)
const dirty = ref(false)
const error = ref('')
const lint = ref(null)          // 保存后用返回的检查结果顶掉进页时那份

function sync() {
  if (!c.value) return
  draft.value = JSON.parse(JSON.stringify(specBody(c.value)))
  draft.value.global.paletteText = (draft.value.global.palette || []).join('、')
  draft.value.continuity.forbidText = (draft.value.continuity.forbid || []).join('\n')
  dirty.value = false
}
watch(() => c.value, () => { if (!dirty.value) sync() }, { immediate: true, deep: false })
// 换了片子才丢掉保存时拿到的那份检查结果（同一条片子刷新时留着：它比进页那份新）
watch(() => c.value?.id, () => { lint.value = null })

const g = computed(() => draft.value?.global || {})
const beats = computed(() => draft.value?.beats || [])
const cont = computed(() => draft.value?.continuity || {})
const zh = computed(() => (draft.value?.prompt_language || 'zh') === 'zh')
const duration = computed(() => Number(draft.value?.video_params?.duration || 0))
const bars = computed(() => timeline(beats.value, duration.value))
const covered = computed(() => (beats.value.length ? round(Number(beats.value[beats.value.length - 1].end || 0) - Number(beats.value[0].start || 0)) : 0))
const paletteCount = computed(() => String(g.value.paletteText || '').split(/[、,，;；\n]/).filter(s => s.trim()).length)
const cards = computed(() => toCards(lint.value || d.value))
const bookCards = computed(() => clipCards(cards.value))
const prompt = computed(() => (lint.value?.prompt) || d.value?.prompt || '')
const touch = () => { dirty.value = true }

function addBeat() { draft.value.beats = [...beats.value, newBeat(beats.value)]; touch() }
function drop(i) { draft.value.beats = removeBeat(beats.value, i); touch() }
function move(i, delta) { draft.value.beats = moveBeat(beats.value, i, delta); touch() }
// 改了时间就重新排序编号（后端也会排，界面先所见即所得）
function retime() { draft.value.beats = renumber(beats.value); touch() }
// 「贴住上一拍」：把这一拍的 start 顶到上一拍的 end，空洞/重叠一键消掉
function snap(i) {
  if (i === 0) return
  const previous = beats.value[i - 1]
  const row = beats.value[i]
  const span = round(Number(row.end || 0) - Number(row.start || 0))
  draft.value.beats[i] = { ...row, start: round(previous.end), end: round(Number(previous.end) + span) }
  touch()
}

async function save() {
  error.value = ''
  const body = JSON.parse(JSON.stringify(draft.value))
  body.global.palette = String(body.global.paletteText || '').split(/[、,，;；\n]/).map(s => s.trim()).filter(Boolean)
  body.continuity.forbid = String(body.continuity.forbidText || '').split(/[\n;；]/).map(s => s.trim()).filter(Boolean)
  delete body.global.paletteText
  delete body.continuity.forbidText
  body.beats = renumber(body.beats).map(b => ({ ...b, start: Number(b.start || 0), end: Number(b.end || 0) }))
  await withBusy(async () => {
    try {
      const r = await put(clipUrl(c.value.id), body)
      dirty.value = false
      lint.value = r
      await refreshClip()
      const n = (r.warnings || []).length
      toast(n ? t('已保存，{n} 条检查提醒', { n }) : t('已保存'))
    } catch (e) { error.value = e.message }
  })
}
</script>

<template>
  <div class="ws-content" v-if="draft">
    <div class="ws-center" style="max-width:1100px" data-test="clip-write">
      <div class="row between" style="margin-bottom:8px">
        <div>
          <h3 style="font-size:16px">{{ $t('写法（全片设定 / 分段时间线 / 衔接与收尾）') }}</h3>
          <p class="hint">{{ $t('三段各管一件事：全片设定定下从头到尾不变的东西，分段时间线按秒说每一拍做什么，衔接与收尾管转场、最后一帧和禁止清单。') }}</p>
        </div>
        <button class="btn primary" :disabled="!dirty" data-test="write-save" @click="save">{{ $t('保存') }}</button>
      </div>
      <div v-if="error" class="note bad" data-test="write-error">{{ error }}</div>

      <div class="cols" style="display:grid;grid-template-columns:1.35fr 1fr;gap:14px;align-items:start">
        <div class="stack">
          <!-- ---------------------------------------------------------- 全片设定 -->
          <div class="card" data-test="write-global">
            <h3>{{ $t('【全片设定】') }}</h3>
            <div class="field">
              <label>{{ $t('风格') }}</label>
              <StyleCardPicker v-model="draft.style" v-model:anchor="draft.style_anchor" :show-grid="false" select-test="write-style" @update:modelValue="touch" @update:anchor="touch" />
            </div>
            <div class="field" v-if="draft.style === 'custom'">
              <label>{{ $t('风格锚') }}</label>
              <textarea v-model="draft.style_anchor" data-test="write-anchor" @input="touch"></textarea>
            </div>
            <div class="field"><label>{{ $t('看起来像什么') }}</label><textarea v-model="draft.global.look" data-test="write-look" @input="touch"></textarea></div>
            <div class="field">
              <label>{{ $t('色板（5–7 个具体色名，顿号分开）') }}</label>
              <input v-model="draft.global.paletteText" data-test="write-palette" @input="touch" />
              <div class="hint" data-test="write-palette-count">{{ $t('现在 {n} 个', { n: paletteCount }) }}</div>
            </div>
            <div class="field"><label>{{ $t('光线 / 天气 / 大气') }}</label><input v-model="draft.global.light" data-test="write-light" @input="touch" /></div>
            <div class="grid2">
              <div class="field">
                <label>{{ $t('画幅') }}</label>
                <select v-model="draft.global.aspect_ratio" data-test="write-aspect" @change="touch"><option v-for="a in ASPECT_RATIOS" :key="a" :value="a">{{ a }}</option></select>
                <div class="hint">{{ $t('提示词自动带上排除另一种画幅的那句') }}</div>
              </div>
              <div class="field">
                <label>{{ $t('时长（秒）') }}</label>
                <input type="number" min="1" v-model.number="draft.video_params.duration" data-test="write-duration" @input="touch" />
                <div class="hint" data-test="write-covered">{{ $t('节拍覆盖 {a}/{b} 秒', { a: covered, b: duration }) }}</div>
              </div>
            </div>
            <div class="field"><label>{{ $t('主体安全区') }}</label><input v-model="draft.global.safe_area" data-test="write-safe" @input="touch" /></div>
            <div class="field"><label>{{ $t('文字政策') }}</label><input v-model="draft.global.text_policy" data-test="write-text-policy" @input="touch" /></div>
            <div class="field"><label>{{ $t('声音说明（只是说明，BGM 由配乐或后期解决）') }}</label><input v-model="draft.global.audio" data-test="write-audio" @input="touch" /></div>
            <div class="grid2">
              <div class="field">
                <label>{{ $t('提示词语言') }}</label>
                <select v-model="draft.prompt_language" data-test="write-lang" @change="touch"><option v-for="l in PROMPT_LANGUAGES" :key="l.id" :value="l.id">{{ $t(l.label) }}</option></select>
              </div>
              <div class="field">
                <label>{{ $t('提示词方言') }}</label>
                <select v-model="draft.dialect" data-test="write-dialect" @change="touch"><option v-for="x in DIALECTS" :key="x.id" :value="x.id">{{ $t(x.label) }}</option></select>
              </div>
            </div>
          </div>

          <!-- ---------------------------------------------------------- 分段时间线 -->
          <div class="card" data-test="write-timeline">
            <div class="row between">
              <h3>{{ $t('【分段时间线】') }}</h3>
              <button class="btn sm" data-test="beat-add" @click="addBeat">{{ $t('+ 加一拍') }}</button>
            </div>
            <p class="hint">{{ $t('绝对时间，可带小数。相邻两拍首尾相接：不留空洞、不重叠——一个节拍只做一件事。') }}</p>
            <div class="bar" data-test="beat-bar" style="position:relative;height:22px;margin:8px 0 12px;background:var(--panel3);border-radius:var(--r-sm);overflow:hidden">
              <span v-for="b in bars" :key="b.id" :data-test="`bar-${b.id}`" :title="b.label"
                    :style="{ position: 'absolute', left: b.left + '%', width: b.width + '%', top: 0, bottom: 0, background: 'var(--accent, #6b8afd)', opacity: 0.75, borderRight: '1px solid var(--panel)' }">
                <span class="mono" style="font-size:10px;padding-left:3px">{{ b.id }}</span>
              </span>
            </div>
            <div v-if="!beats.length" class="hint" data-test="beat-empty">{{ $t('还没有节拍。按秒把画面切成 4–10 拍，每拍写景别 + 画面 + 声音 + 画面文字。') }}</div>
            <div v-for="(b, i) in beats" :key="b.id" class="card" data-test="beat-row" style="padding:8px 10px;margin-top:6px">
              <div class="row between">
                <span class="mono"><b>{{ b.id }}</b></span>
                <span class="row" style="gap:4px">
                  <input type="number" step="0.1" min="0" v-model.number="b.start" data-test="beat-start" style="width:72px" @change="retime" />
                  <span class="hint">–</span>
                  <input type="number" step="0.1" min="0" v-model.number="b.end" data-test="beat-end" style="width:72px" @change="retime" />
                  <span class="hint">s</span>
                  <button class="btn sm" v-if="i > 0" data-test="beat-snap" :title="$t('贴住上一拍')" @click="snap(i)">⇤</button>
                  <button class="btn sm" data-test="beat-up" :disabled="i === 0" @click="move(i, -1)">↑</button>
                  <button class="btn sm" data-test="beat-down" :disabled="i === beats.length - 1" @click="move(i, 1)">↓</button>
                  <button class="btn sm danger" data-test="beat-remove" @click="drop(i)">✕</button>
                </span>
              </div>
              <div class="grid2" style="margin-top:6px">
                <div class="field"><label>{{ $t('景别 / 运镜') }}</label><input v-model="b.shot" data-test="beat-shot" @input="touch" /></div>
                <div class="field"><label>{{ $t('声音') }}</label><input v-model="b.sound" data-test="beat-sound" @input="touch" /></div>
                <div class="field span2"><label>{{ $t('画面（一拍只做一件事）') }}</label><textarea v-model="b.picture" data-test="beat-picture" @input="touch"></textarea></div>
                <div class="field span2"><label>{{ $t('画面里要出现的文字（会自动加引号）') }}</label><input v-model="b.text" data-test="beat-text" @input="touch" /></div>
              </div>
              <div class="hint mono small" data-test="beat-line">{{ previewLine(b, zh) }}</div>
              <div v-for="card in beatCards(cards, b.id)" :key="card.text" class="note" data-test="beat-lint">
                <b>{{ card.impact }}</b><span v-if="card.fix"> — {{ card.fix }}</span>
              </div>
            </div>
          </div>

          <!-- ---------------------------------------------------------- 衔接与收尾 -->
          <div class="card" data-test="write-continuity">
            <h3>{{ $t('【衔接与收尾】') }}</h3>
            <label class="row" style="gap:6px;align-items:center;margin-bottom:6px">
              <input type="checkbox" v-model="draft.continuity.one_take" data-test="write-onetake" @change="touch" />
              <span>{{ $t('一镜到底（中间没有剪辑点）') }}</span>
            </label>
            <div class="field"><label>{{ $t('转场载体（借前景里本来就有的东西挡一下镜头）') }}</label><input v-model="draft.continuity.transitions" data-test="write-transitions" @input="touch" /></div>
            <div class="field"><label>{{ $t('最后一帧停在什么上') }}</label><input v-model="draft.continuity.last_frame" data-test="write-lastframe" @input="touch" /></div>
            <div class="field"><label>{{ $t('禁止清单（一行一条）') }}</label><textarea v-model="draft.continuity.forbidText" data-test="write-forbid" @input="touch"></textarea></div>
          </div>
        </div>

        <!-- ---------------------------------------------------------- 右侧：提示词预览 + 检查 -->
        <div class="stack" style="position:sticky;top:8px">
          <div class="card" data-test="write-preview">
            <div class="row between">
              <h3>{{ $t('编译出来的提示词') }}</h3>
              <span class="hint mono small">{{ (prompt || '').length }} {{ $t('字') }}</span>
            </div>
            <p v-if="dirty" class="hint" data-test="preview-stale">{{ $t('写法改过了——点「保存」后这里更新（每一拍那行在下面实时更新）。') }}</p>
            <pre class="mono small" data-test="preview-text" style="white-space:pre-wrap;max-height:420px;overflow:auto">{{ prompt }}</pre>
          </div>
          <div class="card" data-test="write-lint">
            <h3>{{ $t('检查') }} <span class="pill" v-if="cards.length">{{ cards.length }}</span></h3>
            <p v-if="!cards.length" class="hint" data-test="lint-clean">{{ $t('没有告警。') }}</p>
            <div v-for="card in bookCards" :key="card.text" class="note" data-test="clip-lint-card">
              <b>{{ card.impact }}</b><span v-if="card.fix"> — {{ card.fix }}</span>
            </div>
            <p v-if="cards.length > bookCards.length" class="hint">{{ $t('另有 {n} 条挂在对应的节拍下面', { n: cards.length - bookCards.length }) }}</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
