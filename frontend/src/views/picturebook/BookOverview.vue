<script setup>
// 概览：进度、问题卡（/lint，按页分组）、下一步提示（/summary）、三个起任务的按钮（生成插画 / 合成朗读 / 装配成片）
import { computed } from 'vue'
import { store } from '../../store.js'
import { confirm } from '../../ui.js'
import { t } from '../../i18n.js'
import { pb, progressOf, toCards, runBookTask } from '../../picturebook.js'

// 问题卡 kind 标签（后端四要素卡的 kind；前端自拆的都是 text）与双语策略标签，渲染时翻译
const KIND_LABELS = { text: '正文', hook: '翻页钩', kb: '运镜', grading: '分级', education: '教育点', continuity: '连续性', other: '其他' }
const MODE_LABELS = { sentence_alternate: '逐句交替', page_zh_then_en: '整页先中后英', zh_only: '只有中文', en_only: '只有英文' }
const modeLabel = (mode) => MODE_LABELS[mode] || mode || ''
const b = computed(() => store.book)
const s = computed(() => store.bookSummary)
const prog = computed(() => progressOf(s.value, b.value?.pages?.length))
const total = computed(() => prog.value.pages.total || b.value?.pages?.length || 0)
const cards = computed(() => toCards(store.bookLint))
const notes = computed(() => store.bookLint?.notes || [])
const groups = computed(() => {
  const out = {}
  for (const c of cards.value) { const key = c.target ? c.target : '__book__'; (out[key] ||= []).push(c) }
  return out
})
const next = computed(() => { const n = s.value?.next || s.value?.next_step; return typeof n === 'string' ? { hint: n } : (n || null) })
const busy = computed(() => !!store.gen)

async function pages() {
  const n = total.value - prog.value.pages.done
  if (!await confirm(t('生成插画：会发 {n} 次生图请求（已有插画且提示词没变的页会跳过）。继续？', { n: Math.max(n, 0) || total.value }), { confirmLabel: t('生成插画') })) return
  await runBookTask(`${pb(b.value.id)}/pages/generate`, {}, t('生成插画'))
}
async function narration() {
  if (!await confirm(t('合成朗读：每页每种语言各一段 TTS（同文本同音色不重复合成）。继续？'), { confirmLabel: t('合成朗读') })) return
  await runBookTask(`${pb(b.value.id)}/narration/generate`, {}, t('合成朗读'))
}
async function assemble() {
  await runBookTask(`${pb(b.value.id)}/assemble`, {}, t('装配成片'))
}
</script>

<template>
  <div class="ws-content" v-if="b">
    <div class="ws-center" style="max-width:860px" data-test="book-overview">
      <h3 style="font-size:16px;margin-bottom:8px">{{ $t('概览') }}</h3>
      <div class="card">
        <div class="row between">
          <div>
            <b>{{ b.title_zh || b.title_en }}</b> <span class="hint">{{ b.title_en }}</span>
            <div class="hint">{{ $t('{n} 页 · {age} 岁 · {mode}', { n: b.pages.length, age: b.age_band, mode: $t(modeLabel(b.bilingual_mode)) }) }}</div>
          </div>
          <div class="chips">
            <span class="pill" :class="{ ok: prog.pages.done >= total && total }">{{ $t('插画 {a}/{n}', { a: prog.pages.done, n: total }) }}</span>
            <span class="pill" :class="{ ok: prog.narration.done >= total && total }">{{ $t('朗读 {a}/{n}', { a: prog.narration.done, n: total }) }}</span>
            <span class="pill" :class="{ ok: prog.final }">{{ $t('成片') }} {{ prog.final ? '✓' : '—' }}</span>
          </div>
        </div>
        <p class="hint" v-if="next?.hint || next?.label" style="margin-top:8px" data-test="next-step">→ {{ next.hint || next.label }}</p>
        <div class="row" style="margin-top:10px">
          <button class="btn spend" :disabled="busy || !b.pages.length" data-test="gen-pages" @click="pages">{{ $t('生成插画') }}</button>
          <button class="btn spend" :disabled="busy || !b.pages.length" data-test="gen-narration" @click="narration">{{ $t('合成朗读') }}</button>
          <button class="btn primary" :disabled="busy || !b.pages.length" data-test="gen-assemble" @click="assemble">{{ $t('装配成片') }}</button>
          <span v-if="store.gen" class="status">{{ store.gen.text }}</span>
        </div>
        <p class="hint" v-if="!b.pages.length" style="margin-top:6px">{{ $t('还没有页——先去「分页表」加页，或回到新建页用 AI 起草。') }}</p>
      </div>

      <div class="card" data-test="problems">
        <h3>{{ cards.length ? $t('{n} 条检查提醒（不阻止生成，但通常值得看一眼）', { n: cards.length }) : $t('文本检查没有提醒') }}</h3>
        <p class="hint" v-if="!cards.length">{{ $t('字数、翻页钩、KB 方向、拟声词、一页一个动作、插画无字、群像点名、结尾安稳、目标词次数都过了。') }}</p>
        <div v-for="(group, key) in groups" :key="key" style="margin:8px 0 4px">
          <div class="small" style="font-weight:600;margin-bottom:3px">
            <router-link v-if="key !== '__book__'" :to="`/picturebook/b/${b.id}/pages`" class="link mono">{{ key }}</router-link>
            <span v-else>{{ $t('全书') }}</span>
          </div>
          <div v-for="(c, i) in group" :key="i" class="small" style="margin:2px 0 2px 8px;display:flex;gap:6px;align-items:flex-start" data-test="problem-card">
            <span class="pill" style="flex-shrink:0">{{ $t(KIND_LABELS[c.kind] || c.kind) }}</span>
            <span>{{ c.impact }}<template v-if="c.fix"> <span class="hint">→ {{ c.fix }}</span></template></span>
          </div>
        </div>
        <div v-if="notes.length" style="margin-top:8px">
          <div v-for="(n, i) in notes" :key="i" class="hint">ℹ {{ n }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

