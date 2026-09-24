<script setup>
// 播客项目外壳（docs/podcast-spec.md §6）：左栏导航（脚本 / 配音 / 高级），顶部进度（已起草 · 已配音 · 已混音）；正文是子路由。
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { store } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import { loadPodcast, progressOf, podcastRoute, formatName } from '../../podcast.js'

const route = useRoute()
watch(() => route.params.id, (id) => {
  if (!id || !route.path.startsWith('/podcast/p/')) return
  loadPodcast(id).catch(e => notice(t('打开失败：{msg}', { msg: e.message })))
}, { immediate: true })

const s = computed(() => store.podcast)
const d = computed(() => store.podcastDetail)
const prog = computed(() => progressOf(d.value || s.value))
</script>

<template>
  <div v-if="!s" class="page"><div class="empty">{{ $t('打开中…') }}</div></div>
  <div v-else class="ws" data-test="podcast-shell">
    <nav class="ws-nav">
      <div class="head">
        <h2>{{ s.title || s.id }}</h2>
        <div class="id">{{ s.id }}<span v-if="s.format"> · {{ formatName(s.format) }}</span></div>
      </div>
      <router-link class="nav" :to="podcastRoute(s.id, 'script')">{{ $t('脚本') }} <span class="n">{{ prog.segments }}</span></router-link>
      <router-link class="nav" :to="podcastRoute(s.id, 'audio')">{{ $t('配音') }}</router-link>
      <router-link class="nav" :to="podcastRoute(s.id, 'advanced')">{{ $t('高级') }}</router-link>
    </nav>
    <div class="ws-main">
      <div class="ws-top">
        <div class="prog" data-test="podcast-progress">
          <span>{{ $t('已起草') }} <b>{{ prog.drafted ? '✓' : '—' }}</b></span>
          <span>{{ $t('已配音') }} <b>{{ prog.audio ? '✓' : '—' }}</b></span>
          <span>{{ $t('已混音') }} <b>{{ prog.mixed ? '✓' : '—' }}</b></span>
        </div>
        <div class="next">
          <span v-if="store.gen" class="status">{{ store.gen.text }}</span>
          <span v-else class="hint">🎙 {{ $t('AI 播客') }}</span>
        </div>
      </div>
      <router-view />
    </div>
  </div>
</template>
