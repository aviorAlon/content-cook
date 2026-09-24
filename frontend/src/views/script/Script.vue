<script setup>
// 剧本工作台项目外壳（docs/scriptlab-spec.md §9）：左栏导航（写 / 试 / 比 / 用），顶部摘要（目标形态 · 段数 · 版本数 ·
// 采用第几版 · 预计分钟 · 已投放几处）；正文是子路由。
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { store } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import { loadScript, progressOf, scriptRoute, targetName } from '../../scriptlab.js'

const route = useRoute()
watch(() => route.params.id, (id) => {
  if (!id || !route.path.startsWith('/script/p/')) return
  loadScript(id).catch(e => notice(t('打开失败：{msg}', { msg: e.message })))
}, { immediate: true })

const s = computed(() => store.script)
const d = computed(() => store.scriptDetail)
const prog = computed(() => progressOf(d.value || s.value))
const minutes = computed(() => Number(d.value?.estimate?.minutes ?? prog.value.minutes ?? 0))
</script>

<template>
  <div v-if="!s" class="page"><div class="empty">{{ $t('打开中…') }}</div></div>
  <div v-else class="ws" data-test="script-shell">
    <nav class="ws-nav">
      <div class="head">
        <h2>{{ s.title || s.id }}</h2>
        <div class="id">{{ s.id }}<span v-if="s.target"> · {{ targetName(s.target) }}</span></div>
      </div>
      <router-link class="nav" :to="scriptRoute(s.id, 'write')">{{ $t('写') }} <span class="n">{{ prog.blocks }}</span></router-link>
      <router-link class="nav" :to="scriptRoute(s.id, 'try')">{{ $t('试') }} <span class="n">{{ prog.versions }}</span></router-link>
      <router-link class="nav" :to="scriptRoute(s.id, 'compare')">{{ $t('比') }}</router-link>
      <router-link class="nav" :to="scriptRoute(s.id, 'use')">{{ $t('用') }} <span class="n">{{ prog.used }}</span></router-link>
    </nav>
    <div class="ws-main">
      <div class="ws-top">
        <div class="prog" data-test="script-summary">
          <span>{{ $t('段') }} <b>{{ prog.blocks }}</b></span>
          <span>{{ $t('版本') }} <b>{{ prog.versions }}</b></span>
          <span>{{ $t('采用') }} <b>{{ prog.currentIndex ? $t('第 {k} 版', { k: prog.currentIndex }) : '—' }}</b></span>
          <span>{{ $t('预计') }} <b>{{ minutes.toFixed(1) }}</b> {{ $t('分钟') }}</span>
          <span>{{ $t('已投放') }} <b>{{ prog.used }}</b></span>
        </div>
        <div class="next">
          <span v-if="store.gen" class="status">{{ store.gen.text }}</span>
          <span v-else class="hint">✍️ {{ $t('剧本工作台') }}</span>
        </div>
      </div>
      <router-view />
    </div>
  </div>
</template>
