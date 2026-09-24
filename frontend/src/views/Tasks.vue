<script setup>
// 任务中心（DESIGN §38，2026-09-10）：跨模块列 GET /api/tasks——生成、装配、导出这类耗时的活都记在这里。
// 只读，不花钱：这一页从不提交任何生成，只显示服务端已经落盘的任务记录。
// 有任务在跑时每 3 秒自动刷一次，全部结束就停（不给后端做无用轮询）。
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { store } from '../store.js'
import { t, isEn } from '../i18n.js'
import {
  STATUS_FILTERS, absTime, activeCount, durationText, failureDetail, failureLine, fetchTasks,
  isActive, kindLabel, matchesStatus, relTime, statusLabel, taskTarget,
} from '../tasks.js'

const REFRESH_MS = 3000

const rows = ref([])
const loaded = ref(false)
const err = ref('')
const moduleFilter = ref('')
const statusFilter = ref('')
const expanded = ref(null)      // 展开看失败原文的那一条 task_id
let timer = null
let disposed = false            // 已卸载：在飞的 load 回来后不能再排下一轮，不然离开这一页后还一直打后端

const readyModules = computed(() => (store.modules || []).filter(m => m.available))
const moduleName = (id) => {
  const m = (store.modules || []).find(x => x.id === id)
  if (!m) return id || '—'
  return (isEn() && m.english) ? m.english : m.name
}
const visible = computed(() => rows.value.filter(r => matchesStatus(r, statusFilter.value)))
const runningNow = computed(() => activeCount(rows.value))

async function load() {
  try {
    rows.value = await fetchTasks({ module: moduleFilter.value })
    err.value = ''
  } catch (e) { err.value = e.message }
  if (disposed) return
  loaded.value = true
  schedule()
}

// 在跑就接着轮，全停（或页面已卸载）就把定时器撤掉
function schedule() {
  if (timer) { clearTimeout(timer); timer = null }
  if (disposed) return
  if (runningNow.value > 0) timer = setTimeout(() => { load().catch(() => {}) }, REFRESH_MS)
}

function pickModule(id) { moduleFilter.value = id; load().catch(() => {}) }

onMounted(() => { load().catch(() => {}) })
onUnmounted(() => { disposed = true; if (timer) clearTimeout(timer); timer = null })
</script>

<template>
  <div class="page narrow">
    <div class="row between" style="margin-bottom:12px;align-items:flex-start;gap:12px;flex-wrap:wrap">
      <div>
        <h2 style="font-size:20px">📋 {{ t('任务中心') }}</h2>
        <p class="hint">{{ t('生成、装配、导出这类耗时的活在后台跑，进度和失败原因都记在这里。这一页只看，不发起任何生成。') }}</p>
      </div>
      <div class="hint" data-test="tasks-auto">{{ runningNow ? t('{n} 个在跑 · 每 3 秒自动刷新', { n: runningNow }) : t('没有在跑的任务') }}</div>
    </div>

    <div class="card" style="margin-bottom:12px" data-test="task-filters">
      <div class="row" style="gap:6px;flex-wrap:wrap;align-items:center">
        <span class="hint">{{ t('模块') }}</span>
        <button class="btn sm" :class="{ primary: moduleFilter === '' }" data-test="module-filter-all" @click="pickModule('')">{{ t('全部') }}</button>
        <button v-for="m in readyModules" :key="m.id" class="btn sm" :class="{ primary: moduleFilter === m.id }"
                :data-test="`module-filter-${m.id}`" @click="pickModule(m.id)">{{ m.icon }} {{ moduleName(m.id) }}</button>
      </div>
      <div class="row" style="gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px">
        <span class="hint">{{ t('状态') }}</span>
        <button v-for="s in STATUS_FILTERS" :key="s.id || 'all'" class="btn sm" :class="{ primary: statusFilter === s.id }"
                :data-test="`status-filter-${s.id || 'all'}`" @click="statusFilter = s.id">{{ t(s.label) }}</button>
      </div>
    </div>

    <div v-if="err" class="card" style="color:var(--bad)" data-test="tasks-error">{{ err }}</div>

    <table v-if="visible.length" class="tbl" data-test="task-table">
      <thead>
        <tr>
          <th>{{ t('模块') }}</th><th>{{ t('种类') }}</th><th>{{ t('项目') }}</th>
          <th>{{ t('状态') }}</th><th>{{ t('开始时间') }}</th><th>{{ t('用时') }}</th><th>{{ t('失败原因') }}</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="r in visible" :key="r.task_id">
          <tr data-test="task-row" :data-kind="r.kind" :data-status="r.status">
            <td>{{ moduleName(r.module || 'drama') }}</td>
            <td>{{ kindLabel(r.kind) }}</td>
            <td class="mono">
              <router-link v-if="taskTarget(r)?.url" :to="taskTarget(r).url" data-test="task-project">{{ taskTarget(r).id }}</router-link>
              <span v-else class="hint">{{ taskTarget(r)?.id || '—' }}</span>
            </td>
            <td><span class="pill" :class="{ ok: r.status === 'succeeded', bad: r.status === 'failed', warn: isActive(r) }">{{ statusLabel(r.status) }}</span></td>
            <td class="hint" :title="absTime(r.started_at || r.created_at)">{{ relTime(r.started_at || r.created_at) }}</td>
            <td class="hint mono">{{ durationText(r) }}</td>
            <td class="fail">
              <template v-if="r.status === 'failed'">
                <span data-test="task-error">{{ failureLine(r) }}</span>
                <a href="#" class="hint" style="margin-left:6px" data-test="task-error-toggle"
                   @click.prevent="expanded = expanded === r.task_id ? null : r.task_id">{{ expanded === r.task_id ? t('收起') : t('展开') }}</a>
              </template>
              <span v-else class="hint">—</span>
            </td>
          </tr>
          <tr v-if="expanded === r.task_id" data-test="task-error-full">
            <td colspan="7"><pre class="mono" style="white-space:pre-wrap;margin:0">{{ failureDetail(r) }}</pre></td>
          </tr>
        </template>
      </tbody>
    </table>

    <div v-else-if="loaded" class="empty" data-test="tasks-empty">{{ t('还没有任务。生成、装配、导出这类耗时的活会记在这里。') }}</div>
    <div v-else class="empty">{{ t('读取任务…') }}</div>
  </div>
</template>

<style scoped>
.fail { max-width: 320px; }
table.tbl td, table.tbl th { vertical-align: middle; }
</style>
