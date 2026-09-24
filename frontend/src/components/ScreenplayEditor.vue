<script setup>
// 剧本的逐场编辑器（DESIGN §32）：新建页的两步起草和项目页的「剧本」标签共用这一个组件。
// 它直接改传进来的那份 doc（父组件保存时整份 PUT 回去），每次改动 emit 一次 dirty，保存按钮由父组件放进 actions 插槽。
// 告警是后端 lint 给的整句（「scene S02: 影响——改法」/「全集：…」/「cast ye: …」）：顶部汇总一份，逐场的再贴在那一场里。
import { computed, watch } from 'vue'
import { t } from '../i18n.js'

const props = defineProps({
  doc: { type: Object, required: true },
  warnings: { type: Array, default: () => [] },
})
const emit = defineEmits(['dirty'])

function touch() { emit('dirty') }

// ---- 兜底：模型/老文件里这些字段可能整个没有或形状不对，模板里要能直接 v-model。
// 只在换了一份剧本时补一次（不放进 computed：在取值里改数据会把自己的依赖打翻）
function ensureShape(doc) {
  if (!doc || typeof doc !== 'object') return
  if (!doc.contract || typeof doc.contract !== 'object' || Array.isArray(doc.contract)) doc.contract = {}
  for (const k of ['premise', 'hook', 'cliffhanger']) if (typeof doc.contract[k] !== 'string') doc.contract[k] = ''
  for (const k of ['must_pay_off', 'carry_over']) if (!Array.isArray(doc.contract[k])) doc.contract[k] = []
  if (!Array.isArray(doc.cast)) doc.cast = []
  if (!Array.isArray(doc.scenes)) doc.scenes = []
  for (const s of doc.scenes) {
    if (!Array.isArray(s.beats)) s.beats = typeof s.beats === 'string' ? s.beats.split('\n').filter(x => x.trim()) : []
    if (!Array.isArray(s.dialogue)) s.dialogue = []
  }
}
watch(() => props.doc, ensureShape, { immediate: true })

const contract = computed(() => props.doc.contract || {})
const cast = computed(() => props.doc.cast || [])
const scenes = computed(() => props.doc.scenes || [])
const stats = computed(() => {
  const lines = scenes.value.reduce((n, s) => n + (s.dialogue || []).length, 0)
  const words = scenes.value.reduce((n, s) =>
    n + (s.beats || []).reduce((m, b) => m + String(b || '').trim().length, 0)
      + (s.dialogue || []).reduce((m, l) => m + String(l.zh || '').trim().length, 0), 0)
  return { scenes: scenes.value.length, dialogue_lines: lines, words }
})

// 说话人下拉：人物表 + 旁白。写在剧本里但人物表没有的说话人也列出来，不然一打开就被下拉悄悄改掉
const NARRATOR = 'narrator'
function speakerOptions(current) {
  const out = cast.value.filter(c => String(c.id || '').trim()).map(c => ({ value: c.id, label: c.name ? `${c.name}（${c.id}）` : c.id }))
  out.push({ value: NARRATOR, label: t('旁白') })
  const who = String(current || '').trim()
  if (who && !out.some(o => o.value === who)) out.unshift({ value: who, label: t('{who}（不在人物表里）', { who }) })
  return out
}

// ---- 告警：顶部汇总全部，逐场的贴在那一场里（前缀在中英文下都是 "scene <id>: "）
function sceneWarnings(id) {
  const head = `scene ${id}:`
  return props.warnings.filter(w => typeof w === 'string' && w.startsWith(head)).map(w => w.slice(head.length).trim())
}

// ---- 通用的行操作
function move(list, i, delta) {
  const j = i + delta
  if (j < 0 || j >= list.length) return
  list.splice(j, 0, list.splice(i, 1)[0])
  touch()
}
function removeAt(list, i) { list.splice(i, 1); touch() }

function nextSceneId() {
  let max = 0
  for (const s of scenes.value) {
    const m = /^S(\d+)$/.exec(String(s.id || '').trim())
    if (m) max = Math.max(max, Number(m[1]))
  }
  let n = Math.max(max + 1, scenes.value.length + 1)
  const taken = new Set(scenes.value.map(s => String(s.id || '').trim()))
  while (taken.has(`S${String(n).padStart(2, '0')}`)) n++
  return `S${String(n).padStart(2, '0')}`
}
function blankScene() { return { id: nextSceneId(), heading: '', place: '', time: '', beats: [''], dialogue: [], note: '' } }
function addScene(at = scenes.value.length) { scenes.value.splice(at, 0, blankScene()); touch() }
function delScene(i) { scenes.value.splice(i, 1); touch() }
function addBeat(scene) { scene.beats.push(''); touch() }
function addLine(scene) { scene.dialogue.push({ who: cast.value[0]?.id || NARRATOR, zh: '' }); touch() }
function addCast() { cast.value.push({ id: '', name: '', block: '' }); touch() }
function addChip(key) { contract.value[key].push(''); touch() }
</script>

<template>
  <div class="sp" data-test="screenplay-editor">
    <div class="row between wrap">
      <div class="hint" data-test="sp-stats">
        {{ $t('{n} 场', { n: stats.scenes }) }} · {{ $t('台词 {n} 句', { n: stats.dialogue_lines }) }} · {{ $t('正文 {n} 字', { n: stats.words }) }}
      </div>
      <div class="row"><slot name="actions" /></div>
    </div>

    <div v-if="warnings.length" class="note" data-test="sp-warning-summary">
      <div class="small" style="margin-bottom:4px"><b>{{ $t('{n} 条提醒', { n: warnings.length }) }}</b> {{ $t('（都是提醒，不挡保存）') }}</div>
      <div v-for="(w, i) in warnings" :key="i" class="small">⚠ {{ w }}</div>
    </div>

    <div class="card" style="padding:12px 14px">
      <div class="field"><label>{{ $t('剧名') }}</label><input v-model="doc.title" @input="touch" data-test="sp-title" /></div>
      <div class="field"><label>{{ $t('梗概') }}</label><textarea v-model="doc.logline" @input="touch" data-test="sp-logline" rows="2"></textarea></div>
    </div>

    <div class="card" style="padding:12px 14px" data-test="sp-contract">
      <b>{{ $t('单集契约') }}</b>
      <div class="hint small" style="margin:2px 0 6px">{{ $t('先立承诺再写场：写成可核对的事实，不写「关系更紧张」这类气氛词。拆镜时会照抄进项目。') }}</div>
      <div class="field"><label>{{ $t('前提') }}</label><input v-model="contract.premise" @input="touch" data-test="sp-premise" /></div>
      <div class="field"><label>{{ $t('开场钩子') }}</label><input v-model="contract.hook" @input="touch" data-test="sp-hook" /></div>
      <div class="field"><label>{{ $t('集尾压力') }}</label><input v-model="contract.cliffhanger" @input="touch" data-test="sp-cliffhanger" /></div>
      <div class="field" v-for="key in ['must_pay_off', 'carry_over']" :key="key">
        <label>{{ key === 'must_pay_off' ? $t('本集必须兑现') : $t('留给下一集') }}</label>
        <div v-for="(item, i) in contract[key]" :key="i" class="row" :data-test="`sp-chip-${key}`">
          <input v-model="contract[key][i]" @input="touch" style="flex:1" />
          <button class="btn sm" @click="removeAt(contract[key], i)" :title="$t('删掉')">✕</button>
        </div>
        <button class="btn sm" @click="addChip(key)" :data-test="`sp-chip-add-${key}`">＋ {{ $t('加一条') }}</button>
      </div>
    </div>

    <div class="card" style="padding:12px 14px" data-test="sp-cast">
      <div class="row between"><b>{{ $t('人物表') }}</b><span class="hint small">{{ $t('id 拆镜时沿用；台词的说话人只能从这里选') }}</span></div>
      <div v-for="(m, i) in cast" :key="i" class="row wrap" data-test="sp-cast-row" style="margin-top:6px">
        <input v-model="m.id" @input="touch" class="mono" :placeholder="$t('id')" style="width:110px" data-test="sp-cast-id" />
        <input v-model="m.name" @input="touch" :placeholder="$t('角色名')" style="width:120px" data-test="sp-cast-name" />
        <input v-model="m.block" @input="touch" :placeholder="$t('外形一句话（拆镜定形用）')" style="flex:1;min-width:180px" data-test="sp-cast-block" />
        <button class="btn sm" @click="removeAt(cast, i)" data-test="sp-cast-del">✕</button>
      </div>
      <button class="btn sm" style="margin-top:6px" @click="addCast" data-test="sp-cast-add">＋ {{ $t('加一个角色') }}</button>
    </div>

    <div class="card scene" v-for="(s, i) in scenes" :key="i" style="padding:12px 14px" data-test="scene-card">
      <div class="row between wrap">
        <b class="mono">{{ s.id || $t('（没有 id）') }}</b>
        <div class="row">
          <button class="btn sm" @click="move(scenes, i, -1)" :disabled="i === 0" data-test="scene-up">↑</button>
          <button class="btn sm" @click="move(scenes, i, 1)" :disabled="i === scenes.length - 1" data-test="scene-down">↓</button>
          <button class="btn sm" @click="addScene(i + 1)" data-test="scene-insert">{{ $t('在下面插一场') }}</button>
          <button class="btn sm" @click="delScene(i)" data-test="scene-del">{{ $t('删掉这一场') }}</button>
        </div>
      </div>
      <div v-if="sceneWarnings(s.id).length" class="note small" style="margin:6px 0" data-test="scene-warnings">
        <div v-for="(w, k) in sceneWarnings(s.id)" :key="k">⚠ {{ w }}</div>
      </div>
      <div class="field"><label>{{ $t('场景标题') }}</label><input v-model="s.heading" @input="touch" :placeholder="$t('内/外 · 地点 · 时间')" data-test="scene-heading" /></div>
      <div class="grid2">
        <div class="field"><label>{{ $t('地点') }}</label><input v-model="s.place" @input="touch" data-test="scene-place" /></div>
        <div class="field"><label>{{ $t('时间') }}</label><input v-model="s.time" @input="touch" data-test="scene-time" /></div>
      </div>
      <div class="field">
        <label>{{ $t('节拍（每条一件看得见的事）') }}</label>
        <div v-for="(b, k) in s.beats" :key="k" class="row" data-test="beat-row">
          <span class="hint mono small" style="width:20px">{{ k + 1 }}</span>
          <input :value="b" @input="s.beats[k] = $event.target.value; touch()" style="flex:1" data-test="beat-input" />
          <button class="btn sm" @click="move(s.beats, k, -1)" :disabled="k === 0" data-test="beat-up">↑</button>
          <button class="btn sm" @click="move(s.beats, k, 1)" :disabled="k === s.beats.length - 1" data-test="beat-down">↓</button>
          <button class="btn sm" @click="removeAt(s.beats, k)" data-test="beat-del">✕</button>
        </div>
        <button class="btn sm" @click="addBeat(s)" data-test="beat-add">＋ {{ $t('加一条节拍') }}</button>
      </div>
      <div class="field">
        <label>{{ $t('对白') }}</label>
        <div v-for="(l, k) in s.dialogue" :key="k" class="row" data-test="line-row">
          <select v-model="l.who" @change="touch" style="width:150px" data-test="line-who">
            <option v-for="o in speakerOptions(l.who)" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
          <input v-model="l.zh" @input="touch" style="flex:1" :placeholder="$t('台词原文')" data-test="line-zh" />
          <button class="btn sm" @click="move(s.dialogue, k, -1)" :disabled="k === 0" data-test="line-up">↑</button>
          <button class="btn sm" @click="move(s.dialogue, k, 1)" :disabled="k === s.dialogue.length - 1" data-test="line-down">↓</button>
          <button class="btn sm" @click="removeAt(s.dialogue, k)" data-test="line-del">✕</button>
        </div>
        <button class="btn sm" @click="addLine(s)" data-test="line-add">＋ {{ $t('加一句台词') }}</button>
      </div>
      <div class="field"><label>{{ $t('备注（给拆镜看，不进故事）') }}</label><input v-model="s.note" @input="touch" data-test="scene-note" /></div>
    </div>

    <div class="row" style="margin-top:8px">
      <button class="btn sm" @click="addScene()" data-test="scene-add">＋ {{ $t('加一场') }}</button>
    </div>
  </div>
</template>

<style scoped>
.sp .card { margin-bottom: 10px; }
.sp .row { gap: 6px; align-items: center; }
.sp .row.wrap { flex-wrap: wrap; }
.sp .field { margin-top: 6px; }
.sp .scene { border-left: 3px solid var(--line, #ddd); }
</style>
