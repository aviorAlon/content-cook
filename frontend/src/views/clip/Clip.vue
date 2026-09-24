<script setup>
// 片子外壳（docs/clip-spec.md §6）：左栏导航（写法 / 出片 / 高级）+ 顶部状态条（拍数 · 时长 · 出片状态 · 问题数）；
// 正文是子路由。进页拉一次 detail（里面已经带 lint 与提示词预览，不用再单独拉）。
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { store } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import { loadClip, clipRoute, progressOf, toCards } from '../../clip.js'

const route = useRoute()
watch(() => route.params.id, (id) => {
  if (id && route.path.startsWith('/clip/c/')) loadClip(id).catch(e => notice(t('打开片子失败：{msg}', { msg: e.message })))
}, { immediate: true })

const c = computed(() => store.clip)
const d = computed(() => store.clipDetail)
const prog = computed(() => progressOf(d.value))
const problems = computed(() => toCards(d.value).length)
const beats = computed(() => c.value?.beats?.length || 0)
const covered = computed(() => d.value?.stats?.covered ?? 0)
const statusText = computed(() => ({ done: t('已有成片'), running: t('出片中'), failed: t('上次失败'), todo: t('还没出片') }[prog.value.status] || t('还没出片')))
</script>

<template>
  <div v-if="!c" class="page"><div class="empty">{{ $t('打开片子…') }}</div></div>
  <div v-else class="ws" data-test="clip-shell">
    <nav class="ws-nav">
      <div class="head">
        <h2>{{ c.title || c.id }}</h2>
        <div class="id">{{ c.id }} · {{ d?.aspect_ratio || c.global?.aspect_ratio }} · {{ c.video_params?.duration }}s</div>
      </div>
      <router-link class="nav" :to="clipRoute(c.id, 'write')">{{ $t('写法') }} <span class="n" v-if="problems">{{ problems }}</span></router-link>
      <router-link class="nav" :to="clipRoute(c.id, 'render')">{{ $t('出片') }} <span class="n">{{ prog.attempts || '' }}</span></router-link>
      <router-link class="nav" :to="clipRoute(c.id, 'advanced')">{{ $t('高级') }}</router-link>
    </nav>
    <div class="ws-main">
      <div class="ws-top">
        <div class="prog" data-test="clip-progress">
          <span>{{ $t('节拍') }} <b>{{ beats }}</b></span>
          <span>{{ $t('覆盖') }} <b>{{ covered }}/{{ c.video_params?.duration }}s</b></span>
          <span>{{ $t('出片') }} <b>{{ statusText }}</b></span>
        </div>
        <div class="next">
          <span v-if="store.gen" class="status">{{ store.gen.text }}</span>
          <span v-else-if="problems" class="hint" data-test="clip-next">{{ $t('{n} 条检查提醒——不拦出片，但花钱前最好清掉', { n: problems }) }}</span>
        </div>
      </div>
      <router-view />
    </div>
  </div>
</template>
