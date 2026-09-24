<script setup>
// 新建绘本（docs/picturebook-spec.md §7）：方向一句话、年龄段与双语策略、要教的三件事、风格预设（可改文案）；
// 「AI 起草」= POST /api/picturebook/jobs/draft 后台任务 → 轮询 → POST /api/picturebook 建书；「空白开始」直接建一本没有页的书。
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { post, pollTask } from '../../api.js'
import { store, roleOk, withBusy } from '../../store.js'
import { notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { PB_API, AGE_BANDS, AGE_RULES, BILINGUAL_MODES, STYLE_PRESETS, loadBooks, pageCountHint } from '../../picturebook.js'
import LocalTip from '../../components/LocalTip.vue'

const router = useRouter()
const form = reactive({
  title: '', direction: '', age_band: '2-4', bilingual_mode: 'sentence_alternate',
  cognition: '', vocab: '', emotion: '',
  preset: STYLE_PRESETS[0].id, style_anchor: STYLE_PRESETS[0].anchor, negative: STYLE_PRESETS[0].negative,
  page_count: null,
})
const status = ref('')
const hint = computed(() => pageCountHint(form.age_band))
const defaultPages = computed(() => AGE_RULES[form.age_band]?.pages[0] || 8)
// 换预设时把文案换成预设的；之后用户随便改
watch(() => form.preset, (id) => { const p = STYLE_PRESETS.find(x => x.id === id); if (p) { form.style_anchor = p.anchor; form.negative = p.negative } })

function education() {
  return { cognition: form.cognition.trim(), vocab: form.vocab.split(/[,，;；\n]/).map(s => s.trim()).filter(Boolean), emotion: form.emotion.trim() }
}
function base() {
  return { age_band: form.age_band, bilingual_mode: form.bilingual_mode, education: education(), style_anchor: form.style_anchor.trim(), negative: form.negative.trim() }
}
async function create(body) {
  const r = await post(PB_API, body)
  await loadBooks()
  router.push(`/picturebook/b/${r.id}/overview`)
  return r
}
async function blank() {
  if (!form.title.trim()) { await notice(t('空白开始要先起个书名')); return }
  await withBusy(async () => {
    try { await create({ ...base(), title_zh: form.title.trim(), pages: [] }); toast(t('已新建空白绘本，去「分页表」加页')) }
    catch (e) { await notice(t('新建失败：{err}', { err: e.message })) }
  })
}
async function draft() {
  if (!form.direction.trim()) { await notice(t('方向一句话要填')); return }
  status.value = t('提交中…')
  let created
  try {
    created = await post(`${PB_API}/jobs/draft`, { direction: form.direction.trim(), age_band: form.age_band, bilingual_mode: form.bilingual_mode, education: education(), page_count: form.page_count ? Number(form.page_count) : null, title: form.title.trim() || null })
  } catch (e) { status.value = ''; await notice(t('提交失败：{err}', { err: e.message })); return }
  const label = t('起草中')
  store.gen = { text: `${label}…` }
  let task
  try {
    task = await pollTask(created.task_id, (_t, sec) => { status.value = t('{label}…已经 {s} 秒（可以切走）', { label, s: sec }); store.gen = { text: status.value } })
  } catch (e) { status.value = ''; store.gen = null; await notice(t('轮询失败：{err}', { err: e.message })); return }
  store.gen = null
  status.value = ''
  if (task.status === 'failed') { await notice(t('起草失败：\n\n{err}', { err: task.error || '' })); return }
  // 后端任务结果 {id, draft: <项目文件>, warnings, notes, cost_usd, tokens, backend, saved}（老形状 spec/book 也认）；
  // 起草只给内容，风格锚与负面词以这页填的为准；id 不带，让服务端编号
  const raw = task.result || {}
  const { id: _id, warnings: _w, notes: _n, cost_usd: _c, tokens: _t, usage_tokens: _u, backend: _b, saved: _s, ...rest } = raw.draft || raw.spec || raw.book || raw
  const spec = rest
  const body = { ...spec, ...base(), title_zh: form.title.trim() || spec.title_zh || form.direction.trim().slice(0, 20) }
  if (raw.warnings?.length) toast(t('起草有 {n} 条提醒，进书后在概览看问题卡', { n: raw.warnings.length }), { kind: 'warn', ttl: 6000 })
  await withBusy(async () => {
    try { await create(body); toast(t('已起草并建好绘本')) }
    catch (e) { await notice(t('起草结果保存失败：{err}', { err: e.message })) }
  })
}
</script>

<template>
  <div class="page narrow" data-test="pb-new">
    <div class="card">
      <h3>{{ $t('新建绘本') }}</h3>
      <p class="hint" style="margin-bottom:10px">{{ $t('给一句故事方向，再选年龄段和双语策略、写下想教的三件事，让文本模型起草分页表（会花钱，和图片 / 语音不是一个量级）；或者空白开始，自己在分页表里一页页写。') }}</p>
      <div v-if="!roleOk('text')" class="note" style="margin-bottom:10px">{{ $t('还没配置剧本文本模型——') }}<router-link to="/settings">{{ $t('去「模型设置」填好') }}</router-link><LocalTip role="text" /></div>
      <div class="field"><label>{{ $t('书名（起草时可不填，用 AI 起的）') }}</label><input v-model="form.title" data-test="pb-title" :placeholder="$t('如「小狐狸的三只袜子」')" /></div>
      <div class="field"><label>{{ $t('方向一句话') }}</label><textarea v-model="form.direction" data-test="pb-direction" :placeholder="$t('主角是谁、要做什么、想让孩子学到什么、想要的调性……')"></textarea></div>
      <div class="grid2">
        <div class="field">
          <label>{{ $t('年龄段') }}</label>
          <select v-model="form.age_band" data-test="pb-age"><option v-for="a in AGE_BANDS" :key="a" :value="a">{{ a }} {{ $t('岁') }}</option></select>
          <div class="hint" data-test="pb-page-hint">{{ hint }}</div>
        </div>
        <div class="field">
          <label>{{ $t('双语策略') }}</label>
          <select v-model="form.bilingual_mode" data-test="pb-mode"><option v-for="m in BILINGUAL_MODES" :key="m.id" :value="m.id">{{ $t(m.label) }}</option></select>
        </div>
        <div class="field"><label>{{ $t('页数（可选，不填按年龄段）') }}</label><input v-model="form.page_count" type="number" min="4" max="24" :placeholder="String(defaultPages)" data-test="pb-page-count" /></div>
      </div>
      <div class="grid2" data-test="pb-education">
        <div class="field"><label>{{ $t('教育点 · 认知') }}</label><input v-model="form.cognition" :placeholder="$t('如「认颜色：红、黄、蓝」')" /></div>
        <div class="field"><label>{{ $t('教育点 · 词汇（逗号分开，写成 中文/english）') }}</label><input v-model="form.vocab" :placeholder="$t('袜子/sock, 风/wind')" /></div>
        <div class="field span2"><label>{{ $t('教育点 · 情感') }}</label><input v-model="form.emotion" :placeholder="$t('如「东西飞走了，也可以分给朋友」')" /></div>
      </div>
      <div class="field">
        <label>{{ $t('风格预设') }}</label>
        <select v-model="form.preset" data-test="pb-preset"><option v-for="p in STYLE_PRESETS" :key="p.id" :value="p.id">{{ $t(p.name) }}</option></select>
      </div>
      <div class="field"><label>{{ $t('风格锚（会进每一页的插画提示词，可改）') }}</label><textarea v-model="form.style_anchor" data-test="pb-anchor"></textarea></div>
      <div class="field"><label>{{ $t('负面词') }}</label><input v-model="form.negative" data-test="pb-negative" /></div>
      <div class="row">
        <button class="btn spend" :disabled="!!status" data-test="pb-draft" @click="draft">{{ $t('AI 起草') }}</button>
        <button class="btn" :disabled="!!status" data-test="pb-blank" @click="blank">{{ $t('空白开始') }}</button>
        <span class="status">{{ status }}</span>
      </div>
    </div>
  </div>
</template>
