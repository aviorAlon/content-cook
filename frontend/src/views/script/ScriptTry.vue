<script setup>
// 试（docs/scriptlab-spec.md §9）：「生成一版」（整篇，1 次文本模型请求）、「只重写这几段」（勾选段 + 一句要求，
// 其余段从基准版原样抄过来）、「跑检查」（本地即时，不花钱）、「审一审」（复用已有的两张清单，1 次请求）；
// 下面是版本列表：标签 / 来源 / 模型 / 时间 / 字数 / 分钟 / 告警数 / 审稿结论 / 采用 / 删除。
// 项目还没采用任何一版时新出的那一版自动采用（后端 adopt_if_first），之后生成的版本不自动顶替。
import { computed, reactive, ref } from 'vue'
import { del, post } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { relTime } from '../../tasks.js'
import ModelReviewCard from '../../components/ModelReviewCard.vue'
import { sc, scriptRoute, refreshScript, runScriptTask, sourceLabel, verdictText, targetName } from '../../scriptlab.js'
// 审稿卡：目标形态是播客就按真人感清单审，其余按短剧工艺清单审（契约 §5，两张清单都不是这个模块新写的）；
// 组名跟播客脚本页、短剧总览共用一份（checklists.js），别各抄各的
import { HUMANLIKE_GROUPS, CRAFT_GROUPS } from '../../checklists.js'
// 问题卡分类名：全前端一份（problems.js，镜像 webui/problems.py 的 KIND_LABELS），别再各抄几个键
import { problemKind } from '../../problems.js'

const EVIDENCE_LABELS = { quote_not_found: '引文在台词里找不到', unknown_ids: '引用了不存在的段号' }

const s = computed(() => store.script)
const d = computed(() => store.scriptDetail)
const isPodcast = computed(() => s.value?.target === 'podcast')
const blocks = computed(() => s.value?.blocks || [])
const versions = computed(() => d.value?.versions || s.value?.versions || [])
const current = computed(() => String(d.value?.spec?.current ?? s.value?.current ?? ''))
const busyDraft = computed(() => !!d.value?.busy?.draft)
// 界面上只摆解析好的问题卡：原始告警字符串带着 block B01: 这种内部前缀，不露给用户看（跟 webui/problems.py 一个规矩）
const problems = computed(() => d.value?.problems || [])

const draftNote = ref('')
const redraftNote = ref('')
const picked = reactive({})     // 段 id → 勾没勾
const linting = ref('')
const checked = computed(() => blocks.value.map(b => b.id).filter(id => picked[id]))

// 生成一版：整篇，新版本追加在最后。项目还没采用任何一版时后端会直接采用它（adopt_if_first，契约 §3），
// 否则不自动顶替——确认文案按这两种情况分开说，别对着空项目说「不会自动采用」
async function generate() {
  const msg = current.value
    ? t('这会发 1 次文本模型请求，出一整版新的台词；现有版本一句不动，新的一版也不会自动采用。继续？')
    : t('这会发 1 次文本模型请求，出一整版新的台词；项目还没有采用任何一版，这一版会直接采用（之后再生成的版本要自己挑）。继续？')
  const ok = await confirm(msg, { confirmLabel: t('生成'), localRole: 'text' })
  if (!ok) return
  const body = {}
  const note = draftNote.value.trim()
  if (note) body.note = note
  await runScriptTask(`${sc(s.value.id)}/generate`, body, t('生成一版'))
}

// 只重写这几段：其余段从基准版（默认当前采用的那一版）原样抄过来
async function redraft() {
  if (!checked.value.length) { linting.value = t('先勾上要重写的段'); return }
  const ok = await confirm(t('只重写勾上的 {n} 段，其余段从当前采用的那一版原样抄过来。这会发 1 次文本模型请求。继续？', { n: checked.value.length }), { confirmLabel: t('重写'), localRole: 'text' })
  if (!ok) return
  const body = { blocks: [...checked.value] }
  const note = redraftNote.value.trim()
  if (note) body.note = note
  if (current.value) body.from_version = current.value
  await runScriptTask(`${sc(s.value.id)}/generate`, body, t('重写这几段'))
}

// 跑检查：静态判据在后端算好挂在详情里，这里只是重新拉一次详情（不花钱）
async function lint() {
  linting.value = t('检查中…')
  await withBusy(async () => {
    try { await refreshScript(); linting.value = problems.value.length ? t('检查完了，{n} 条提醒', { n: problems.value.length }) : t('检查完了，没有提醒') }
    catch (e) { linting.value = ''; await notice(e.message) }
  })
}

async function pick(v) {
  await withBusy(async () => {
    try { await post(`${sc(s.value.id)}/pick`, { version: v.id }); await refreshScript(); toast(t('已采用 {v}', { v: v.label || v.id })) }
    catch (e) { await notice(e.message) }
  })
}
async function removeVersion(v) {
  const ok = await confirm(t('删掉 {v}？这一版的台词就没了，找不回来。', { v: v.label || v.id }), { confirmLabel: t('删除'), danger: true })
  if (!ok) return
  await withBusy(async () => {
    try { await del(`${sc(s.value.id)}/versions/${encodeURIComponent(v.id)}`); await refreshScript(); toast(t('已删掉 {v}', { v: v.label || v.id })) }
    catch (e) { await notice(e.message) }
  })
}

const blockIds = computed(() => new Set(blocks.value.map(b => b.id)))
const blockLink = (id) => (blockIds.value.has(id) && s.value ? { path: scriptRoute(s.value.id, 'write'), hash: `#block-${id}` } : null)
</script>

<template>
  <div class="ws-content" v-if="s">
    <div class="ws-center" style="max-width:980px" data-test="script-try">
      <!-- 生成 / 重写 / 检查 -->
      <div class="card">
        <h3>{{ $t('试一版') }} <span class="pill" data-test="try-target">{{ targetName(s.target) }}</span></h3>
        <p class="hint">{{ $t('写法按目标形态选，全部复用已有模块的经验规则，这个模块不新造判据。每生成一次就多一版，从不覆盖；要不要采用，比过再说。') }}</p>
        <div class="row" style="margin-top:6px;align-items:flex-end">
          <div class="field" style="flex:1;margin-bottom:0">
            <label>{{ $t('给这一版的一句话要求（可空）') }}</label>
            <input v-model="draftNote" data-test="draft-note" :placeholder="$t('例如：开头先说一个周六晚上的具体场景')" />
          </div>
          <button class="btn primary" :disabled="busyDraft" data-test="draft-btn" @click="generate">{{ $t('生成一版') }}</button>
          <button class="btn" data-test="lint-btn" @click="lint">{{ $t('跑检查（不花钱）') }}</button>
        </div>
        <div v-if="busyDraft" class="status" data-test="try-busy">{{ $t('正在生成…') }}</div>
        <div v-if="linting" class="status" data-test="lint-status">{{ linting }}</div>
        <!-- 问题卡：段号 + 分类 + 影响 → 改法，跟「写」页同一套布局 -->
        <div v-if="problems.length" class="note" style="margin-top:8px" data-test="try-problems">
          <div v-for="(c, i) in problems" :key="i" class="small" style="margin:4px 0;display:flex;gap:6px;align-items:flex-start" data-test="try-problem">
            <router-link v-if="blockLink(c.target)" :to="blockLink(c.target)" class="pill mono" style="flex-shrink:0" data-test="try-problem-target">{{ c.target }}</router-link>
            <span v-else class="pill mono" style="flex-shrink:0" data-test="try-problem-target">{{ c.scope === 'block' && c.target ? c.target : $t('全篇') }}</span>
            <span class="pill" style="flex-shrink:0">{{ $t(problemKind(c.kind)) }}</span>
            <span><b>{{ c.impact }}</b><template v-if="c.fix"> <span class="hint">→ {{ c.fix }}</span></template></span>
          </div>
        </div>
      </div>

      <!-- 只重写这几段 -->
      <div class="card" data-test="redraft-card">
        <h3>{{ $t('只重写这几段') }}</h3>
        <p class="hint">{{ $t('勾上不满意的段，其余段从当前采用的那一版原样抄过来，一样是 1 次文本模型请求、一样另出一版。') }}</p>
        <div class="chips" style="margin-top:6px">
          <label v-for="b in blocks" :key="b.id" class="pill" style="cursor:pointer" data-test="redraft-block">
            <input type="checkbox" v-model="picked[b.id]" :data-test="`redraft-check-${b.id}`" /> {{ b.id }} {{ b.title || '' }}
          </label>
        </div>
        <div class="row" style="margin-top:8px;align-items:flex-end">
          <div class="field" style="flex:1;margin-bottom:0">
            <label>{{ $t('这几段要怎么改（可空）') }}</label>
            <input v-model="redraftNote" data-test="redraft-note" :placeholder="$t('例如：分歧再明显一点，别各说各的')" />
          </div>
          <button class="btn primary" :disabled="busyDraft || !checked.length" data-test="redraft-btn" @click="redraft">{{ $t('重写勾上的 {n} 段', { n: checked.length }) }}</button>
        </div>
      </div>

      <!-- 审一审：播客形态按真人感清单，其余按短剧工艺清单（两张清单都复用现成的） -->
      <ModelReviewCard v-if="isPodcast" key="humanlike" title="审一审（真人感清单）" path="review" :base="sc(s.id)" :group-labels="HUMANLIKE_GROUPS" test-prefix="script-review"
        :name="$t('真人感清单')" hint="用文本模型按口语 / 对谈动态 / 内容三组真人感清单逐条对照当前采用的那一版台词；每条问题都要引用其中一句（或相邻两句），引文对不上的不算"
        confirm-text="按真人感清单审一遍当前这版台词：{note}。" footer="这是模型按真人感清单的判断，不是保证"
        missing-text="模型漏答了 {n} 条清单条目" dropped-text="丢掉 {n} 条编号对不上清单的回答"
        :ref-link="blockLink" :refresh="refreshScript" :evidence-labels="EVIDENCE_LABELS" />
      <ModelReviewCard v-else key="craft" title="审一审（剧本工艺清单）" path="review" :base="sc(s.id)" :group-labels="CRAFT_GROUPS" test-prefix="script-review"
        :name="$t('剧本工艺清单')" hint="用文本模型按张力 / 情绪可见 / 节奏 / 合规四组工艺清单逐条对照当前采用的那一版台词；每条问题都要引用其中一句，引文对不上的不算"
        confirm-text="按工艺清单审一遍当前这版台词：{note}。" footer="这是模型按工艺清单的判断，不是保证"
        missing-text="模型漏答了 {n} 条清单条目" dropped-text="丢掉 {n} 条编号对不上清单的回答"
        :ref-link="blockLink" :refresh="refreshScript" :evidence-labels="EVIDENCE_LABELS" />

      <!-- 版本列表 -->
      <div class="card" data-test="version-list">
        <h3>{{ $t('版本') }} <span class="hint">{{ versions.length }}</span></h3>
        <p v-if="!versions.length" class="hint" data-test="version-empty">{{ $t('还没有任何一版。点上面的「生成一版」，或者在「写」页手写完另存。') }}</p>
        <div v-for="v in versions" :key="v.id" class="row between" style="align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--border)" data-test="version-row">
          <div style="flex:1;min-width:0">
            <div>
              <span class="pill mono">{{ v.id }}</span>
              <b style="margin-left:6px">{{ v.label || v.id }}</b>
              <span v-if="v.id === current" class="pill ok" style="margin-left:6px" data-test="version-current">{{ $t('采用中') }}</span>
            </div>
            <div class="hint small" data-test="version-meta">
              {{ sourceLabel(v.source) }} · {{ v.model || v.backend || '—' }} · {{ relTime(v.created_at) }}
              · {{ $t('{c} 字', { c: v.estimate?.chars || 0 }) }} · {{ $t('{m} 分钟', { m: Number(v.estimate?.minutes || 0).toFixed(1) }) }}
              · {{ $t('{n} 条提醒', { n: v.warnings_count || 0 }) }}
              · <span data-test="version-verdict">{{ v.review ? verdictText(v.review) : $t('还没审过') }}</span>
            </div>
            <div v-if="v.note" class="hint small" data-test="version-note">{{ v.note }}</div>
          </div>
          <div class="row" style="flex:none">
            <button class="btn sm" :disabled="v.id === current" data-test="version-pick" @click="pick(v)">{{ $t('采用') }}</button>
            <button class="btn sm danger" :disabled="v.id === current || versions.length <= 1" data-test="version-remove" @click="removeVersion(v)">{{ $t('删除') }}</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
