<script setup>
// 配乐项目外壳（docs/music-spec.md §5）：左栏导航（生成 / 高级），顶部进度（历次 n · 已采用 · 已投放 n）；正文是子路由。
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { store } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import { loadMusic, progressOf, musicRoute } from '../../music.js'

const route = useRoute()
watch(() => route.params.id, (id) => {
  if (!id || !route.path.startsWith('/music/m/')) return
  loadMusic(id).catch(e => notice(t('打开失败：{msg}', { msg: e.message })))
}, { immediate: true })

const s = computed(() => store.music)
const d = computed(() => store.musicDetail)
const prog = computed(() => progressOf(d.value || s.value))
</script>

<template>
  <div v-if="!s" class="page"><div class="empty">{{ $t('打开中…') }}</div></div>
  <div v-else class="ws" data-test="music-shell">
    <nav class="ws-nav">
      <div class="head">
        <h2>{{ s.title || s.id }}</h2>
        <div class="id">{{ s.id }}<span v-if="s.brief?.genre"> · {{ s.brief.genre }}</span></div>
      </div>
      <router-link class="nav" :to="musicRoute(s.id, 'generate')">{{ $t('生成') }} <span class="n">{{ prog.attempts }}</span></router-link>
      <router-link class="nav" :to="musicRoute(s.id, 'advanced')">{{ $t('高级') }}</router-link>
    </nav>
    <div class="ws-main">
      <div class="ws-top">
        <div class="prog" data-test="music-progress">
          <span>{{ $t('历次') }} <b>{{ prog.attempts }}</b></span>
          <span>{{ $t('采用') }} <b>{{ prog.done ? '✓' : '—' }}</b></span>
          <span>{{ $t('投放') }} <b>{{ prog.used }}</b></span>
        </div>
        <div class="next">
          <span v-if="store.gen" class="status">{{ store.gen.text }}</span>
          <span v-else class="hint">🎵 {{ $t('配乐') }}</span>
        </div>
      </div>
      <router-view />
    </div>
  </div>
</template>
