<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { get, post } from '../api.js'
import { loadEpisodes, withBusy } from '../store.js'
import { t } from '../i18n.js'

const router = useRouter()
const examples = ref([])
const status = ref({})
onMounted(async () => {
  try { examples.value = await get('/api/examples') } catch { examples.value = [] }
})
async function create(ex) {
  status.value[ex.name] = t('复制中…')
  await withBusy(async () => {
    try {
      const r = await post(`/api/examples/${encodeURIComponent(ex.name)}/create`, {})   // 编号由后端生成（DEMO-1、DEMO-2…）
      await loadEpisodes()
      router.push(`/drama/p/${r.id}/overview`)
    } catch (e) { status.value[ex.name] = t('失败：{msg}', { msg: e.message }) }
  })
}
</script>

<template>
  <div class="stack">
    <div v-for="ex in examples" :key="ex.name" class="card" style="padding:12px 14px">
      <div class="row between">
        <div style="flex:1;min-width:220px">
          <b>{{ ex.title }}</b>
          <div class="hint">{{ ex.logline }}</div>
          <div class="hint">{{ t('{n} 镜 · 约 {s} 秒', { n: ex.shot_count, s: Math.round(ex.total_seconds) }) }}{{ ex.has_voice_over ? t(' · 有旁白') : '' }}{{ ex.has_inserts ? t(' · 有插卡') : '' }}</div>
        </div>
        <button class="btn primary sm" @click="create(ex)">{{ t('复制成新项目（不花钱）') }}</button>
      </div>
      <div v-if="status[ex.name]" class="status err" style="margin-top:6px">{{ status[ex.name] }}</div>
    </div>
  </div>
</template>
