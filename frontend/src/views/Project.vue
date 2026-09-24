<script setup>
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store, loadProject, refreshProject, runGenerate, withBusy, capabilityCheck } from '../store.js'
import { post, ep } from '../api.js'
import { confirm, notice, toast, custom } from '../ui.js'
import { t } from '../i18n.js'

const route = useRoute()
const router = useRouter()
watch(() => route.params.id, (id) => { if (id) loadProject(id).catch(e => notice(t('打开项目失败：{msg}', { msg: e.message }))) }, { immediate: true })

const d = computed(() => store.detail)
const s = computed(() => store.summary)
const chars = computed(() => (store.cast || []).filter(c => c.kind === 'character').length)
const scenes = computed(() => (store.cast || []).filter(c => c.kind !== 'character').length)
const postCount = computed(() => {
  const p = store.postLayers; if (!p) return 0
  return (p.voice_over?.lines?.length || 0) + (p.post_graphics?.phone_ui_inserts?.length || 0) + (p.post_graphics?.notebook_text?.length || 0)
})
const curShot = computed(() => route.params.shotId || null)

// 顶部主按钮：永远指向 summary.next_step
async function doNext() {
  const n = s.value?.next_step; if (!n) return
  const id = store.projectId
  switch (n.key) {
    case 'start': return start()
    case 'failed': return router.push(`/drama/p/${id}/overview`)
    case 'frames': return withBusy(() => runGenerate('frames', n.shots?.length ? n.shots : null))
    case 'videos': return withBusy(() => runGenerate('videos', n.shots?.length ? n.shots : null))
    case 'review_frames':
    case 'review_videos': return router.push(`/drama/p/${id}/shots/${n.shots?.[0] || ''}`)
    case 'post': return router.push(`/drama/p/${id}/post`)
    case 'final':
    case 'retake_final': return withBusy(() => runGenerate('final'))
    case 'review_final': return router.push(`/drama/p/${id}/final`)
    // 项目文件 / 账本 / 冻结快照对不上（summary.freeze）：这时生成、审片都会被拦，别当成生成动作；
    // 概览页的问题卡写着是哪一层对不上、怎么处理
    case 'freeze': return router.push(`/drama/p/${id}/overview`)
    // 快照和产物都在、账本没有记录（从归档手动搬回来）：重新登记一遍，本地校验、不花钱
    case 'import_legacy': return withBusy(async () => {
      try { await post(`${ep(id)}/import-legacy`); await refreshProject(); toast(t('已重新登记已有产物')) }
      catch (e) { await notice(typeof e.message === 'string' ? e.message : JSON.stringify(e.message)) }
    })
    default: return router.push(`/drama/p/${id}/final`)
  }
}
async function start() {
  // 先过模型能力表：实测过的组合下参数越界会被拦，可强制；文档级的只提醒
  const check = await capabilityCheck()
  let force = false
  if (check && check.issues?.length) {
    const go = await custom({ view: 'capability', check })
    if (!go) return
    force = check.errors > 0
  } else {
    const ok = await confirm(
      t('开始制作？\n\n之后每一笔生成都会记账，可以逐镜重拍、随时合成。开始后改角色、镜头画面、台词这类会进提示词的内容，系统会预告哪些镜要重出，确认后保存为新版本（旁白、插卡、画面文字随时能改）。'),
      { confirmLabel: t('开始制作') })
    if (!ok) return
  }
  await withBusy(async () => {
    try {
      if (d.value?.needs_import_legacy) await post(`${ep(store.projectId)}/import-legacy`)
      await post(`${ep(store.projectId)}/prepare${force ? '?force=true' : ''}`)
      await refreshProject(); toast(t('已开始制作'))
    } catch (e) { await notice(typeof e.message === 'string' ? e.message : JSON.stringify(e.message)) }
  })
}
</script>

<template>
  <div v-if="!d" class="page"><div class="empty">{{ t('打开项目…') }}</div></div>
  <div v-else class="ws">
    <nav class="ws-nav">
      <div class="head">
        <h2>{{ d.title || d.id }}</h2>
        <div class="id">{{ d.id }} · {{ d.locked ? t('制作中') : t('草稿') }}</div>
      </div>
      <router-link class="nav" :to="`/drama/p/${d.id}/overview`">{{ t('概览') }}</router-link>
      <router-link class="nav" :to="`/drama/p/${d.id}/cast`">{{ t('角色与场景') }} <span class="n">{{ chars }}+{{ scenes }}</span></router-link>
      <router-link class="nav" :to="`/drama/p/${d.id}/screenplay`">{{ t('剧本') }} <span class="n">{{ d.has_screenplay ? '✓' : '' }}</span></router-link>
      <router-link class="nav" :to="`/drama/p/${d.id}/shots`">{{ t('镜头') }} <span class="n">{{ d.shots.length }}</span></router-link>
      <router-link class="nav" :to="`/drama/p/${d.id}/post`">{{ t('旁白与插卡') }} <span class="n">{{ postCount }}</span></router-link>
      <router-link class="nav" :to="`/drama/p/${d.id}/final`">{{ t('成片') }} <span class="n">{{ d.final_url ? '✓' : '' }}</span></router-link>
      <router-link class="nav" :to="`/drama/p/${d.id}/advanced`">{{ t('高级') }}</router-link>
      <div class="strip">
        <router-link v-for="sh in d.shots" :key="sh.id" :to="`/drama/p/${d.id}/shots/${sh.id}`" :class="{ cur: sh.id === curShot }" :title="sh.id">
          <img v-if="sh.frame_url" :src="sh.frame_url" alt="" />
          <span class="lab">{{ sh.id }}</span>
          <span v-if="sh.frame_url" class="d" :class="sh.frame_qc_decision || 'none'"></span>
        </router-link>
      </div>
    </nav>
    <div class="ws-main">
      <div class="ws-top">
        <div class="prog" v-if="s">
          <span>{{ t('首帧') }} <b>{{ s.frames.done }}/{{ s.frames.total }}</b></span>
          <span>{{ t('视频') }} <b>{{ s.videos.done }}/{{ s.videos.total }}</b></span>
          <span>{{ t('审片') }} <b>{{ s.frames.passed.length + s.videos.passed.length }}</b>{{ s.frames.retake.length + s.videos.retake.length ? t(' · 待重拍 {n}', { n: s.frames.retake.length + s.videos.retake.length }) : '' }}</span>
          <span>{{ t('成片') }} <b>{{ s.final.exists ? (s.final.kind === 'post' ? t('后期版') : t('装配版')) : '—' }}</b></span>
        </div>
        <div class="next" v-if="s">
          <span v-if="store.gen" class="status">{{ store.gen.text }}</span>
          <span v-else class="hint">{{ s.next_step.hint }}</span>
          <button class="btn" :class="['frames', 'videos'].includes(s.next_step.key) ? 'spend' : 'primary'" :disabled="!!store.gen || s.next_step.key === 'done'" @click="doNext">{{ s.next_step.label }}</button>
        </div>
      </div>
      <router-view />
    </div>
  </div>
</template>
