<script setup>
// 模型审稿卡：短剧的「按题材审稿」（DESIGN §49）/「剧本工艺审稿」和播客的「真人感审稿」共用——三者接口形状一样
// （GET {review, stale, note, problems, cost_note, task_id}；POST 起后台任务），只差标题、接口前缀与路径、组名表、
// 引用号（镜号 / 场号 / 环节号）链到哪、审完刷新谁和几句文案，全由 props 决定；不给 base / refLink / refresh 就是短剧项目的默认。
// 跑之前弹成本确认（取消不发请求），确认后 POST → 轮询任务 → 重新拉审稿并刷新项目；结果按 违反 / 有风险 / 没问题 分组，
// 核对过的引文高亮、引用号可点；没对上原文的条目收在默认折叠的一组里；正文改过显示过时横幅（话以接口的 note 为准）+ 重新审稿按钮；
// 打开页面时已有任务在跑就接着轮询它。要换审的对象（题材换了）由父组件换 key 重挂。
import { computed, onMounted, ref } from 'vue'
import { store, refreshProject } from '../store.js'
import { get, post, ep, pollTask } from '../api.js'
import { notice, toast, confirm } from '../ui.js'
import { t } from '../i18n.js'

const props = defineProps({
  title: { type: String, required: true },            // 卡标题（中文原文，渲染时翻）
  path: { type: String, required: true },             // 接口路径尾段：genre-review / craft-review / humanlike-review
  groupLabels: { type: Object, required: true },      // 条目 source → 组名（中文原文）
  testPrefix: { type: String, required: true },       // data-test 前缀
  name: { type: String, default: '' },                // 标题旁的 pill：题材名 / 清单名（已翻译）
  hint: { type: String, default: '' },                // 标题下的一句做法说明（中文原文）
  confirmText: { type: String, required: true },      // 成本确认句，占位 {name} {note}（中文原文）
  footer: { type: String, required: true },           // 页脚（中文原文）
  missingText: { type: String, default: '模型漏答了 {n} 条卡片条目' },
  droppedText: { type: String, default: '丢掉 {n} 条编号对不上卡片的回答' },
  base: { type: String, default: '' },                // 接口前缀（如 /api/podcast/PC-1）；空 = 短剧项目 ep(store.projectId)
  refLink: { type: Function, default: null },         // 引用号 → router-link 的 to（字符串或路由对象），返回空 = 不链；不给 = 短剧镜号链到镜头页
  refresh: { type: Function, default: null },         // 审完后刷新父项目；不给 = 短剧的 refreshProject
  evidenceLabels: { type: Object, default: () => ({}) },  // 覆盖「没对上原文」的说明（播客：引文在稿子里找不到 / 不存在的环节号）
})

const VERDICT_LABELS = { violated: '违反', risk: '有风险', ok: '没问题' }
const EVIDENCE_LABELS = { no_quote: '没给引文', quote_not_found: '引文在剧本里找不到', unknown_ids: '引用了不存在的镜号', quote_too_short: '引文太短，算不上证据' }
const GROUPS = [{ key: 'violated', label: '违反' }, { key: 'risk', label: '有风险' }, { key: 'ok', label: '没问题' }]
const evidenceLabel = (problem) => t(props.evidenceLabels[problem] || EVIDENCE_LABELS[problem] || problem || '')

const data = ref(null)                 // GET 的整份回复
const running = ref(false)
const status = ref('')
const review = computed(() => data.value?.review || null)
const items = computed(() => review.value?.result?.items || [])
const groups = computed(() => ({
  violated: items.value.filter(i => i.verdict === 'violated' && i.evidence_verified !== false),
  risk: items.value.filter(i => i.verdict === 'risk' && i.evidence_verified !== false),
  ok: items.value.filter(i => i.verdict === 'ok'),
  unverified: items.value.filter(i => i.evidence_verified === false),
}))
const shotIds = computed(() => new Set((store.detail?.shots || []).map(x => x.id)))
const ready = () => !!(props.base || store.projectId)
const url = () => `${props.base || ep(store.projectId)}/${props.path}`
// 引用号链到哪：短剧默认只链真实存在的镜号（场号不链），别的模块由 refLink 决定
const linkTo = (id) => (props.refLink ? props.refLink(id) : (shotIds.value.has(id) ? `/drama/p/${store.projectId}/shots/${id}` : null))
const fmtTime = (sec) => (sec ? new Date(sec * 1000).toLocaleString() : '')
const groupLabel = (source) => t(props.groupLabels[source] || source)

async function fetchReview() {
  try { data.value = await get(url()) } catch { data.value = null }
}
async function load() {
  if (!ready()) { data.value = null; return }
  await fetchReview()
  if (data.value?.task_id && !running.value) await watchTask(data.value.task_id)
}
async function watchTask(taskId) {
  running.value = true
  try {
    const task = await pollTask(taskId, (_t, sec) => { status.value = t('审稿中…已经 {s} 秒（可以切走）', { s: sec }) })
    if (task.status === 'failed') { await notice(t('审稿失败：') + '\n\n' + (task.error || '')); return }
    toast(t('审稿完成'))
  } catch (e) { await notice(e.message) }
  finally {
    running.value = false; status.value = ''
    await fetchReview()
    await (props.refresh ? props.refresh() : refreshProject())
  }
}
async function run() {
  if (running.value) return
  const note = t(data.value?.cost_note || '发 1 次文本模型请求')
  const ok = await confirm(t(props.confirmText, { name: props.name, note }), { confirmLabel: t('开始审稿') })
  if (!ok) return
  let created
  try { created = await post(url()) } catch (e) { await notice(e.message); return }
  await watchTask(created.task_id)
}
onMounted(load)
</script>

<template>
  <div class="card" :data-test="`${testPrefix}-card`">
    <div class="row between">
      <h3>{{ $t(title) }} <span v-if="name" class="pill" :data-test="`${testPrefix}-card-name`">{{ name }}</span></h3>
      <button class="btn sm primary" @click="run" :disabled="running" :data-test="`${testPrefix}-run`">{{ review ? $t('重新审稿') : $t('开始审稿') }}</button>
    </div>
    <p v-if="hint" class="hint" style="margin:4px 0 8px">{{ $t(hint) }}</p>
    <div v-if="running" class="note" :data-test="`${testPrefix}-running`">{{ status || $t('审稿中…') }}</div>
    <div v-if="data?.stale || data?.note" class="note row between" style="border-color:var(--warn)" :data-test="`${testPrefix}-stale`">
      <!-- 横幅的话以接口的 note 为准（服务端已按语言翻好）：清单文件读不出来时说的是清单的事，不是「剧本改过」；
           没审过但清单缺失时接口只给 note 不给 stale，横幅也要出来，不然用户点「开始审稿」才拿到 400 -->
      <span>{{ data.note || $t('剧本改过，这份审稿可能过时') }}</span>
      <button class="btn sm" @click="run" :disabled="running" :data-test="`${testPrefix}-rerun`">{{ $t('重新审稿') }}</button>
    </div>
    <template v-if="review">
      <p class="hint small" style="margin:6px 0" :data-test="`${testPrefix}-meta`">
        {{ $t(review.card_name || review.checklist_name || name) }} · {{ review.model || review.backend }} · {{ fmtTime(review.created_at) }} ·
        {{ $t('核对了 {checked} 条问题：{verified} 条引文对上，{demoted} 条没对上', review.verification || { checked: 0, verified: 0, demoted: 0 }) }}
        <template v-if="review.verification?.dropped"> · {{ $t(droppedText, { n: review.verification.dropped }) }}</template>
        <template v-if="review.verification?.missing"> · {{ $t(missingText, { n: review.verification.missing }) }}</template>
      </p>
      <p v-if="review.result?.summary" class="small" style="margin:4px 0 8px">{{ review.result.summary }}</p>
      <div v-for="g in GROUPS" :key="g.key" :data-test="`${testPrefix}-group-${g.key}`" style="margin:8px 0">
        <div class="small" style="font-weight:600;margin-bottom:3px">{{ $t(g.label) }} <span class="pill" :class="g.key === 'violated' && groups[g.key].length ? 'bad' : g.key === 'risk' && groups[g.key].length ? 'warn' : ''">{{ groups[g.key].length }}</span></div>
        <div v-for="it in groups[g.key]" :key="`${it.source}-${it.index}`" class="small" style="margin:4px 0 6px 8px" :data-test="`${testPrefix}-item`">
          <div><span class="pill small">{{ groupLabel(it.source) }} {{ it.index }}</span> {{ it.item }}</div>
          <template v-if="g.key !== 'ok'">
            <div v-if="it.quote" style="margin-top:2px">{{ $t('原文：') }}<mark :data-test="`${testPrefix}-quote`">{{ it.quote }}</mark></div>
            <div v-if="it.shots?.length" class="row" style="gap:4px;margin-top:2px">
              <template v-for="id in it.shots" :key="id">
                <router-link v-if="linkTo(id)" :to="linkTo(id)" class="pill mono" :data-test="`${testPrefix}-shot`">{{ id }}</router-link>
                <span v-else class="pill mono">{{ id }}</span>
              </template>
            </div>
            <div v-if="it.why" style="margin-top:2px">{{ $t('理由：') }}{{ it.why }}</div>
            <div v-if="it.fix" class="hint" style="margin-top:2px">{{ $t('改法：') }}{{ it.fix }}</div>
          </template>
        </div>
      </div>
      <details v-if="groups.unverified.length" class="fold" :data-test="`${testPrefix}-unverified`">
        <summary class="small">{{ $t('模型说有问题但没给出能对上的原文（不计入）') }} <span class="pill">{{ groups.unverified.length }}</span></summary>
        <div v-for="it in groups.unverified" :key="`u-${it.source}-${it.index}`" class="small hint" style="margin:4px 0 6px 8px" :data-test="`${testPrefix}-unverified-item`">
          <div><span class="pill small">{{ groupLabel(it.source) }} {{ it.index }}</span> {{ it.item }}</div>
          <div>{{ $t('模型原判：{v}', { v: $t(VERDICT_LABELS[it.model_verdict] || it.model_verdict || '') }) }} · {{ evidenceLabel(it.evidence_problem) }}</div>
          <div v-if="it.quote">{{ $t('模型给的引文：') }}{{ it.quote }}<template v-if="it.shots?.length">（{{ it.shots.join('、') }}）</template></div>
          <div v-if="it.why">{{ $t('理由：') }}{{ it.why }}</div>
        </div>
      </details>
    </template>
    <p v-else-if="!running" class="hint small" :data-test="`${testPrefix}-empty`">{{ $t('还没审过') }}</p>
    <p class="hint small" style="margin:8px 0 0" :data-test="`${testPrefix}-footer`">{{ $t(footer) }}</p>
  </div>
</template>
