<script setup>
// 脚本页（docs/podcast-spec.md §6）：顶部「起草整期」（确认框写清 1 次文本模型请求）+ 估算（字数 / 分钟 vs 目标）+
// 「真人感审稿」卡（跟短剧的题材 / 工艺卡共用 ModelReviewCard，永远显示）+ 问题卡；
// 环节按顺序一张卡：种类 / 标题 / 目标 / 节拍（一行一条）/ 听众问题（qa）/ 台词表（说话人 · 台词 · cue · 种类），
// 每张卡有「重写这一段」（带一句要求）与增删移动；整页一个「保存」（PUT 整份）。
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { put } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { confirm, notice, prompt, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import ModelReviewCard from '../../components/ModelReviewCard.vue'
import { HUMANLIKE_GROUPS } from '../../checklists.js'
// 问题卡分类名：全前端一份（problems.js，镜像 webui/problems.py 的 KIND_LABELS）。
// 这里原先只抄了「播客」「其他」两个键——播客的台词判据也会落到 dialogue / timing 上，落不到表里就显示英文键名
import { problemKind } from '../../problems.js'
import {
  pc, podcastRoute, specBody, refreshPodcast, runPodcastTask, loadFormats, nextSegmentId, emptySegment, emptyLine,
  SEGMENT_KINDS, LINE_KINDS, CUES,
} from '../../podcast.js'

// 真人感审稿卡：接口前缀是播客的，引用号（环节号）链到本页对应的环节卡（hash 锚点 #segment-G03），审完刷新播客详情。
// 组名跟短剧总览、剧本工作台共用一份（checklists.js）
const HUMANLIKE_EVIDENCE_LABELS = { quote_not_found: '引文在稿子里找不到', unknown_ids: '引用了不存在的环节号' }

const s = computed(() => store.podcast)
const d = computed(() => store.podcastDetail)
const form = reactive({ segments: null })
const dirty = ref(false)
const saving = ref('')
const draftNote = ref('')
const cues = ref(CUES)
const kinds = ref(Object.keys(SEGMENT_KINDS))

// 环节 → 表单行：节拍数组变成「一行一条」的文本框，保存时再拆回去
function toRow(seg) {
  return { ...JSON.parse(JSON.stringify(seg)), beatsText: (seg.beats || []).join('\n'), questions: [...(seg.questions || [])], lines: [...(seg.lines || [])].map(l => ({ cue: '', kind: 'talk', ...l })) }
}
function fromRow(row) {
  const { beatsText, ...rest } = row
  return {
    ...rest,
    beats: String(beatsText || '').split('\n').map(x => x.trim()).filter(Boolean),
    questions: (rest.kind === 'qa' ? rest.questions : []).filter(q => String(q.text || '').trim()),
    lines: (rest.lines || []).map(l => {
      const out = { who: l.who, text: String(l.text || '') }
      if (l.cue) out.cue = l.cue
      if (l.kind && l.kind !== 'talk') out.kind = l.kind
      return out
    }),
  }
}
function sync() {
  if (!s.value) return
  form.segments = (s.value.segments || []).map(toRow)
  dirty.value = false
}
watch(s, () => { if (!dirty.value) sync() }, { immediate: true })
onMounted(async () => { const f = await loadFormats(); cues.value = f.cues; kinds.value = f.kinds })

// 审稿卡里的环节号 → 本页的环节卡；不存在的环节号不链。点了之后按 hash 把那张卡滚进视野（hash 路由下浏览器不会自己跳）
const route = useRoute()
const segmentIds = computed(() => new Set((form.segments || []).map(seg => seg.id)))
const segmentLink = (id) => (segmentIds.value.has(id) && s.value ? { path: podcastRoute(s.value.id, 'script'), hash: `#segment-${id}` } : null)
function scrollToHash(hash) {
  if (!hash?.startsWith('#segment-') || typeof document === 'undefined') return
  document.getElementById(hash.slice(1))?.scrollIntoView?.({ block: 'start' })
}
// 带 #segment-G03 直接打开脚本页也要滚：挂载之后才找得到元素（immediate 的回调是在 setup 里同步跑的，那时候环节卡还没渲染）
onMounted(() => nextTick(() => scrollToHash(route.hash)))
watch(() => route.hash, scrollToHash, { flush: 'post' })

const hosts = computed(() => s.value?.hosts || [])
const hostName = (id) => hosts.value.find(h => h.id === id)?.name || id
const estimate = computed(() => d.value?.estimate || null)
const problems = computed(() => d.value?.problems || [])
const busy = computed(() => !!d.value?.busy?.draft)
const drafted = computed(() => (form.segments || []).some(seg => seg.lines?.length))

function touch() { dirty.value = true }

async function save() {
  saving.value = t('保存中…')
  const body = { ...specBody(s.value), segments: form.segments.map(fromRow) }
  await withBusy(async () => {
    try { await put(pc(s.value.id), body); dirty.value = false; await refreshPodcast(); saving.value = ''; toast(t('已保存')) }
    catch (e) { saving.value = ''; await notice(e.message) }
  })
  return !dirty.value
}

// 起草整期：1 次文本模型请求，覆盖所有环节的台词
async function draft() {
  if (dirty.value && !(await save())) return
  const ok = await confirm(t('这会发 1 次文本模型请求，覆盖现有台词。继续？'), { confirmLabel: t('起草'), localRole: 'text' })
  if (!ok) return
  await runPodcastTask(`${pc(s.value.id)}/draft`, { note: draftNote.value.trim() }, t('起草'))
}

// 重写这一段：带一句要求，只重写这一个环节
async function redraft(seg) {
  if (dirty.value && !(await save())) return
  const note = await prompt(t('这一段要怎么改？写一句要求（例如：分歧再明显一点、加一个亲历的例子）。会发 1 次文本模型请求。'), { confirmLabel: t('重写'), placeholder: t('例如：老周要多反问'), multiline: false })
  if (note === null || note === undefined || note === false) return
  await runPodcastTask(`${pc(s.value.id)}/segments/${encodeURIComponent(seg.id)}/redraft`, { note: String(note).trim() }, t('重写这一段'))
}

// ---- 环节增删移动
function addSegment() {
  form.segments.push(toRow(emptySegment(nextSegmentId(form.segments), 'topic')))
  touch()
}
async function removeSegment(i) {
  const seg = form.segments[i]
  if (seg.lines?.length && !(await confirm(t('删掉「{title}」？这一段的 {n} 句台词会一起没了。', { title: seg.title || seg.id, n: seg.lines.length }), { confirmLabel: t('删除'), danger: true }))) return
  form.segments.splice(i, 1)
  touch()
}
function moveSegment(i, delta) {
  const j = i + delta
  if (j < 0 || j >= form.segments.length) return
  const [seg] = form.segments.splice(i, 1)
  form.segments.splice(j, 0, seg)
  touch()
}
function addQuestion(seg) { seg.questions.push({ from: '', text: '' }); touch() }
function removeQuestion(seg, i) { seg.questions.splice(i, 1); touch() }
function addLine(seg, after = null) {
  const prev = after === null ? seg.lines[seg.lines.length - 1] : seg.lines[after]
  // 默认换一个人说：真人对谈不会一个人连着念
  const ids = hosts.value.map(h => h.id)
  const next = prev && ids.length > 1 ? ids[(ids.indexOf(prev.who) + 1) % ids.length] : (ids[0] || '')
  const line = emptyLine(next)
  if (after === null) seg.lines.push(line); else seg.lines.splice(after + 1, 0, line)
  touch()
}
function removeLine(seg, i) { seg.lines.splice(i, 1); touch() }
</script>

<template>
  <div class="ws-content" v-if="s && form.segments">
    <div class="ws-center" style="max-width:960px" data-test="podcast-script">
      <!-- 起草 + 估算 -->
      <div class="card">
        <div class="row between">
          <h3>{{ $t('脚本') }}</h3>
          <div class="row">
            <span v-if="saving" class="status">{{ saving }}</span>
            <button class="btn primary sm" :disabled="!dirty" data-test="script-save" @click="save">{{ $t('保存') }}</button>
          </div>
        </div>
        <p class="hint">{{ $t('起草会按选题、素材和每位主播的人设写满所有环节的台词（1 次文本模型请求）；已有环节骨架就只填台词，没有就连骨架一起出。之后可以逐句改，或者只重写某一段。') }}</p>
        <div class="row" style="margin-top:6px;align-items:flex-end">
          <div class="field" style="flex:1">
            <label>{{ $t('给起草的一句话要求（可空）') }}</label>
            <input v-model="draftNote" data-test="draft-note" :placeholder="$t('例如：冷开场从小夏凌晨两点还在刷开始')" />
          </div>
          <button class="btn primary" :disabled="busy" data-test="draft-btn" @click="draft">{{ drafted ? $t('重新起草整期') : $t('起草整期') }}</button>
          <span v-if="busy" class="status">{{ $t('正在起草…') }}</span>
        </div>
        <div v-if="estimate" class="hint" style="margin-top:8px" data-test="script-estimate">
          {{ $t('预计 {minutes} 分钟（{chars} 字，{lines} 句）· 目标 {target} 分钟', { minutes: Number(estimate.minutes || 0).toFixed(1), chars: estimate.chars || 0, lines: estimate.lines || 0, target: s.target_minutes }) }}
        </div>
        <div v-if="d?.draft?.created_at" class="hint small" data-test="script-draft-info">{{ $t('最近一次起草：{model}', { model: d.draft.model || d.draft.backend || '—' }) }}<span v-if="d.draft.note"> · {{ d.draft.note }}</span></div>
      </div>

      <!-- 真人感审稿（永远显示；引文由后端逐字核对，对不上的降级、不计入） -->
      <ModelReviewCard title="真人感审稿" path="humanlike-review" :base="pc(s.id)" :group-labels="HUMANLIKE_GROUPS" test-prefix="humanlike-review"
        :name="$t('真人感清单')" hint="用文本模型按口语 / 对谈动态 / 内容三组真人感清单逐条对照整期稿子，判断像不像真人在聊；每条问题都要引用稿子里的一句（或相邻两句）台词或一条节拍，引文对不上的不算"
        confirm-text="按真人感清单审一遍稿子：{note}。" footer="这是模型按真人感清单的判断，不是保证"
        missing-text="模型漏答了 {n} 条清单条目" dropped-text="丢掉 {n} 条编号对不上清单的回答"
        :ref-link="segmentLink" :refresh="refreshPodcast" :evidence-labels="HUMANLIKE_EVIDENCE_LABELS" />

      <!-- 问题卡 -->
      <div v-if="problems.length" class="card" data-test="script-problems">
        <h3>{{ $t('{n} 条检查提醒（不阻止合成，但通常值得看一眼）', { n: problems.length }) }}</h3>
        <div v-for="(c, i) in problems" :key="i" class="small" style="margin:4px 0;display:flex;gap:6px;align-items:flex-start" data-test="script-problem">
          <span class="pill mono" style="flex-shrink:0" data-test="problem-target">{{ c.scope === 'segment' && c.target ? c.target : $t('全期') }}</span>
          <span class="pill" style="flex-shrink:0">{{ $t(problemKind(c.kind)) }}</span>
          <span><b>{{ c.impact }}</b><template v-if="c.fix"> <span class="hint">→ {{ c.fix }}</span></template></span>
        </div>
      </div>

      <!-- 环节 -->
      <div v-for="(seg, i) in form.segments" :key="seg.id" :id="`segment-${seg.id}`" class="card" data-test="segment">
        <div class="row between">
          <h3><span class="mono hint">{{ seg.id }}</span> {{ seg.title || $t(SEGMENT_KINDS[seg.kind] || seg.kind) }}</h3>
          <div class="row">
            <button class="btn sm" :disabled="i === 0" data-test="segment-up" :title="$t('上移')" @click="moveSegment(i, -1)">↑</button>
            <button class="btn sm" :disabled="i === form.segments.length - 1" data-test="segment-down" :title="$t('下移')" @click="moveSegment(i, 1)">↓</button>
            <button class="btn sm" :disabled="busy" data-test="segment-redraft" @click="redraft(seg)">{{ $t('重写这一段') }}</button>
            <button class="btn sm danger" data-test="segment-remove" @click="removeSegment(i)">{{ $t('删除') }}</button>
          </div>
        </div>
        <div class="row">
          <div class="field">
            <label>{{ $t('种类') }}</label>
            <select v-model="seg.kind" data-test="segment-kind" @change="touch">
              <option v-for="k in kinds" :key="k" :value="k">{{ $t(SEGMENT_KINDS[k] || k) }}</option>
            </select>
          </div>
          <div class="field" style="flex:1">
            <label>{{ $t('标题') }}</label>
            <input v-model="seg.title" data-test="segment-title" @input="touch" />
          </div>
          <div class="field" style="flex:2">
            <label>{{ $t('这一段要达到什么') }}</label>
            <input v-model="seg.goal" data-test="segment-goal" @input="touch" :placeholder="$t('例如：一个具体场景把人拽进来')" />
          </div>
        </div>
        <div class="field">
          <label>{{ $t('节拍（一行一条）') }}</label>
          <textarea v-model="seg.beatsText" data-test="segment-beats" rows="2" @input="touch"></textarea>
        </div>
        <div v-if="seg.kind === 'qa'" class="field" data-test="segment-questions">
          <label>{{ $t('听众问题') }}</label>
          <div v-for="(q, qi) in seg.questions" :key="qi" class="row" style="margin-top:4px" data-test="question-row">
            <input v-model="q.from" data-test="question-from" style="width:140px" :placeholder="$t('谁问的')" @input="touch" />
            <input v-model="q.text" data-test="question-text" style="flex:1" :placeholder="$t('问题原话')" @input="touch" />
            <button class="btn sm" data-test="question-remove" @click="removeQuestion(seg, qi)">✕</button>
          </div>
          <button class="btn sm" style="margin-top:4px" data-test="question-add" @click="addQuestion(seg)">{{ $t('+ 加一个问题') }}</button>
        </div>
        <div class="field">
          <label>{{ $t('台词') }} <span class="hint">{{ seg.lines.length }}</span></label>
          <div v-if="!seg.lines.length" class="hint">{{ $t('还没有台词。点「起草整期」让模型写，或者「+ 加一句」手写。') }}</div>
          <div v-for="(l, li) in seg.lines" :key="li" class="row" style="margin-top:4px;align-items:center" data-test="line-row">
            <select v-model="l.who" data-test="line-who" style="width:110px" @change="touch">
              <option v-for="h in hosts" :key="h.id" :value="h.id">{{ h.name || h.id }}</option>
            </select>
            <input v-model="l.text" data-test="line-text" style="flex:1" @input="touch" />
            <select v-model="l.cue" data-test="line-cue" style="width:90px" :title="$t('cue：只标 TTS 与剪辑能用的')" @change="touch">
              <option value="">{{ $t('（无）') }}</option>
              <option v-for="c in cues" :key="c" :value="c">{{ $t(c) }}</option>
            </select>
            <select v-model="l.kind" data-test="line-kind" style="width:80px" @change="touch">
              <option v-for="(label, k) in LINE_KINDS" :key="k" :value="k">{{ $t(label) }}</option>
            </select>
            <button class="btn sm" data-test="line-insert" :title="$t('在这句后面插一句')" @click="addLine(seg, li)">+</button>
            <button class="btn sm" data-test="line-remove" :title="$t('删掉这句')" @click="removeLine(seg, li)">✕</button>
          </div>
          <button class="btn sm" style="margin-top:6px" data-test="line-add" @click="addLine(seg)">{{ $t('+ 加一句') }}</button>
        </div>
      </div>

      <div class="row" style="margin-top:6px">
        <button class="btn" data-test="segment-add" @click="addSegment">{{ $t('+ 加一个环节') }}</button>
        <button class="btn primary" :disabled="!dirty" data-test="script-save-bottom" @click="save">{{ $t('保存') }}</button>
      </div>
      <p class="hint" v-if="!form.segments.length">{{ $t('还没有环节。直接「起草整期」会连骨架一起生成（冷开场 → 开场 → 话题 → 听友问答 → 收尾）；也可以先手动加环节写好节拍再起草。') }}</p>
    </div>
  </div>
</template>
