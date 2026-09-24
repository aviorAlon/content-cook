<script setup>
// 项目页 · 剧本标签（DESIGN §32）：两步起草第一步产出的 <id>.screenplay.json，在这里逐场改。
// 保存走 PUT（后端归一化 + 校验 + 重新 lint，告警随响应回来）；「拆镜」用这份剧本起一个新项目——
// 当前项目已经有拆好的镜头（可能还花过钱），不会被覆盖。
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { get, post, put, pollJob } from '../../api.js'
import { store, loadEpisodes, withBusy } from '../../store.js'
import { confirm, notice, toast } from '../../ui.js'
import { t } from '../../i18n.js'
import ScreenplayEditor from '../../components/ScreenplayEditor.vue'

const route = useRoute()
const router = useRouter()
const doc = ref(null)          // 剧本本身（编辑器直接改它）
const warnings = ref([])
const stats = ref(null)
const missing = ref(false)     // 这个项目没有剧本文件
const dirty = ref(false)
const status = ref('')
const error = ref('')
const projectId = computed(() => route.params.id || store.projectId)

async function load(id) {
  doc.value = null; warnings.value = []; stats.value = null; missing.value = false; dirty.value = false; error.value = ''
  if (!id) return
  try {
    const r = await get(`/api/episodes/storyvideo/${encodeURIComponent(id)}/screenplay`)
    doc.value = r.screenplay; warnings.value = r.warnings || []; stats.value = r.stats || null
  } catch (e) {
    if (e.status === 404) { missing.value = true; return }
    error.value = e.message
  }
}
watch(projectId, load, { immediate: true })

async function save() {
  if (!doc.value) return null
  status.value = t('保存中…'); error.value = ''
  try {
    const r = await put(`/api/episodes/storyvideo/${encodeURIComponent(projectId.value)}/screenplay`, { screenplay: doc.value })
    doc.value = r.screenplay; warnings.value = r.warnings || []; stats.value = r.stats || null
    dirty.value = false; status.value = ''
    toast(t('剧本已保存'))
    return r
  } catch (e) { status.value = ''; error.value = e.message; return null }
}

// 拆镜：当前项目已经有镜头了，所以按这份剧本起一个新项目（剧本先复制到新编号下），完成后跳到新项目的镜头页
async function breakdown() {
  if (dirty.value && !(await save())) return
  const d = store.detail
  const seconds = Math.round((d?.shots || []).reduce((n, s) => n + (Number(s.dur) || 0), 0)) || 90
  let created
  try { created = await get('/api/episodes/next-id?prefix=EP') } catch (e) { error.value = e.message; return }
  const newId = created.id
  const ok = await confirm(
    t('按这份剧本重新拆镜？\n\n会真花一次钱，拆出来的镜头存成新项目 {id}（当前项目 {cur} 不动）。', { id: newId, cur: projectId.value }),
    { confirmLabel: t('拆镜') })
  if (!ok) return
  status.value = t('提交中…')
  try {
    await put(`/api/episodes/storyvideo/${encodeURIComponent(newId)}/screenplay`, { screenplay: doc.value })
    const job = await post('/api/scripts/jobs/draft', {
      episode_id: newId, direction: doc.value.logline || d?.logline || '', title: doc.value.title || null,
      target_seconds: seconds, camera_profile: d?.camera_profile || '', prompt_language: 'zh', use_screenplay: true,
    })
    const done = await pollJob(job.job_id, (j, s) => { status.value = t('拆镜中…已经 {s} 秒', { s }) })
    if (done.status === 'failed') { status.value = ''; await notice(t('拆镜失败：{err}', { err: done.error })); return }
    await withBusy(() => post('/api/scripts/save', { episode_id: newId, spec: done.result.spec }))
    await loadEpisodes()
    status.value = ''
    router.push(`/drama/p/${newId}/shots`)
  } catch (e) { status.value = ''; error.value = e.message }
}
</script>

<template>
  <div class="page">
    <div v-if="missing" class="empty" data-test="sp-empty">
      {{ $t('这个项目没有剧本——剧本是「两步起草」第一步的产物（场次 / 节拍 / 对白 / 单集契约）。') }}
      <div style="margin-top:8px"><router-link class="btn" to="/drama/new" data-test="sp-go-new">{{ $t('去新建页起草剧本') }}</router-link></div>
    </div>
    <div v-else-if="!doc" class="empty">{{ error || $t('打开剧本…') }}</div>
    <template v-else>
      <div v-if="error" class="note bad" style="margin-bottom:8px" data-test="sp-error">{{ error }}</div>
      <ScreenplayEditor :doc="doc" :warnings="warnings" @dirty="dirty = true">
        <template #actions>
          <span class="status">{{ status }}</span>
          <button class="btn primary" @click="save" :disabled="!dirty || !!status" data-test="sp-save">{{ dirty ? $t('保存剧本') : $t('已保存') }}</button>
          <button class="btn spend" @click="breakdown" :disabled="!!status" data-test="sp-breakdown">{{ $t('拆镜') }}</button>
        </template>
      </ScreenplayEditor>
    </template>
  </div>
</template>
