<script setup>
// 比（docs/scriptlab-spec.md §9）：左右两个版本下拉，逐段逐行并排，相同的行淡显、不同的行高亮；
// 上面摆两版的字数 / 分钟 / 告警数 / 审稿结论；右上角「用左边这版」「用右边这版」直接采用。
// 差异在前端算（scriptlab.compareVersions，按段 id + 行号对齐），后端不做 diff，也不引第三方 diff 库。
import { computed, ref, watch } from 'vue'
import { post } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import { sc, refreshScript, compareVersions, lineText, sourceLabel, verdictText } from '../../scriptlab.js'

const s = computed(() => store.script)
const d = computed(() => store.scriptDetail)
const versions = computed(() => s.value?.versions || [])
const summaries = computed(() => d.value?.versions || [])
const current = computed(() => String(d.value?.spec?.current ?? s.value?.current ?? ''))
const speakers = computed(() => s.value?.speakers || [])
const blocks = computed(() => s.value?.blocks || [])

const leftId = ref('')
const rightId = ref('')
// 默认摆「当前采用的那一版」和「最新的那一版」；只有一版就两边都是它
watch(versions, (list) => {
  if (!list.length) { leftId.value = ''; rightId.value = ''; return }
  const last = list[list.length - 1]
  const cur = list.find(v => v.id === current.value) || list[Math.max(0, list.length - 2)]
  if (!list.some(v => v.id === leftId.value)) leftId.value = cur.id
  if (!list.some(v => v.id === rightId.value)) rightId.value = last.id
}, { immediate: true })

const byId = (id) => versions.value.find(v => v.id === id) || null
const summaryOf = (id) => summaries.value.find(v => v.id === id) || byId(id) || null
const left = computed(() => byId(leftId.value))
const right = computed(() => byId(rightId.value))
const rows = computed(() => compareVersions(left.value, right.value, blocks.value))
const changed = computed(() => rows.value.reduce((n, b) => n + b.changed, 0))
const show = (line) => lineText(line, speakers.value)

function metaText(id) {
  const v = summaryOf(id)
  if (!v) return ''
  const est = v.estimate || {}
  return [
    sourceLabel(v.source),
    v.model || v.backend || '—',
    t('{c} 字', { c: est.chars || 0 }),
    t('{m} 分钟', { m: Number(est.minutes || 0).toFixed(1) }),
    t('{n} 条提醒', { n: v.warnings_count || 0 }),
    v.review ? verdictText(v.review) : t('还没审过'),
  ].join(' · ')
}

async function pick(id) {
  if (!id) return
  await withBusy(async () => {
    try { await post(`${sc(s.value.id)}/pick`, { version: id }); await refreshScript(); toast(t('已采用 {v}', { v: byId(id)?.label || id })) }
    catch (e) { await notice(e.message) }
  })
}
</script>

<template>
  <div class="ws-content" v-if="s">
    <div class="ws-center" style="max-width:1100px" data-test="script-compare">
      <div class="card">
        <div class="row between">
          <h3>{{ $t('比两版') }}</h3>
          <div class="row">
            <button class="btn sm" :disabled="!leftId || leftId === current" data-test="pick-left" @click="pick(leftId)">{{ $t('用左边这版') }}</button>
            <button class="btn sm" :disabled="!rightId || rightId === current" data-test="pick-right" @click="pick(rightId)">{{ $t('用右边这版') }}</button>
          </div>
        </div>
        <p class="hint">{{ $t('同一段按行号并排：两边一模一样的行淡显，不一样的行高亮。差异在本机算，不花钱。') }}</p>
        <div class="row" style="margin-top:6px">
          <div class="field" style="flex:1;margin-bottom:0">
            <label>{{ $t('左边') }}</label>
            <select v-model="leftId" data-test="compare-left">
              <option v-for="v in versions" :key="v.id" :value="v.id">{{ v.id }} · {{ v.label || v.id }}{{ v.id === current ? $t('（采用中）') : '' }}</option>
            </select>
            <div class="hint small" data-test="compare-left-meta">{{ metaText(leftId) }}</div>
          </div>
          <div class="field" style="flex:1;margin-bottom:0">
            <label>{{ $t('右边') }}</label>
            <select v-model="rightId" data-test="compare-right">
              <option v-for="v in versions" :key="v.id" :value="v.id">{{ v.id }} · {{ v.label || v.id }}{{ v.id === current ? $t('（采用中）') : '' }}</option>
            </select>
            <div class="hint small" data-test="compare-right-meta">{{ metaText(rightId) }}</div>
          </div>
        </div>
        <div class="hint" style="margin-top:6px" data-test="compare-count">{{ $t('{n} 行不一样', { n: changed }) }}</div>
      </div>

      <div v-if="!versions.length" class="card" data-test="compare-empty">
        <p class="hint">{{ $t('还没有任何一版可比。去「试」页生成一版。') }}</p>
      </div>
      <div v-for="b in rows" :key="b.id" class="card" data-test="compare-block">
        <h3><span class="mono hint">{{ b.id }}</span> {{ b.title || $t('（没起名的一段）') }} <span class="pill" :class="b.changed ? 'warn' : ''" data-test="compare-block-count">{{ $t('{n} 行不一样', { n: b.changed }) }}</span></h3>
        <div v-for="r in b.rows" :key="r.index" class="cmp-row" :class="r.same ? 'same' : 'diff'" data-test="compare-row">
          <div class="cmp-cell" data-test="compare-cell-left">{{ show(r.left) }}</div>
          <div class="cmp-cell" data-test="compare-cell-right">{{ show(r.right) }}</div>
        </div>
        <p v-if="!b.rows.length" class="hint small">{{ $t('两版在这一段都没有台词。') }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cmp-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px; padding: 3px 0; font-size: 13px; line-height: 1.55; }
.cmp-cell { min-width: 0; overflow-wrap: anywhere; }
/* 相同的行淡显（扫过去就行），不同的行高亮（要看的就这些） */
.cmp-row.same { color: var(--faint); }
.cmp-row.diff { background: var(--warn-bg); border-radius: var(--r-sm); }
.cmp-row.diff .cmp-cell { padding: 0 6px; }
</style>
