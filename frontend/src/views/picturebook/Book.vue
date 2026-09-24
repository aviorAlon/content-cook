<script setup>
// 书页外壳（docs/picturebook-spec.md §7）：左栏导航（概览 / 分页表 / 角色与风格 / 朗读 / 成片 / 高级），
// 顶部进度条（插画 n/N · 朗读 n/N · 成片）+ 概览给的下一步；正文是子路由。
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { store } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import { loadBook, progressOf, pageImageUrl, hasPageImage } from '../../picturebook.js'

const route = useRoute()
watch(() => route.params.id, (id) => { if (id && route.path.startsWith('/picturebook/b/')) loadBook(id).catch(e => notice(t('打开绘本失败：{msg}', { msg: e.message }))) }, { immediate: true })

const b = computed(() => store.book)
const s = computed(() => store.bookSummary)
const prog = computed(() => progressOf(s.value, b.value?.pages?.length))
const total = computed(() => prog.value.pages.total || b.value?.pages?.length || 0)
const chars = computed(() => Object.keys(b.value?.characters || {}).length)
const scenes = computed(() => Object.keys(b.value?.scenes || {}).length)
const problems = computed(() => s.value?.problem_count ?? (Array.isArray(s.value?.problems) ? s.value.problems.length : 0) ?? (store.bookLint?.warnings?.length || 0))
const nextHint = computed(() => { const n = s.value?.next || s.value?.next_step; return typeof n === 'string' ? n : (n?.hint || n?.label || '') })
const stamp = computed(() => s.value?.updated_at || b.value?.updated_at || 0)
</script>

<template>
  <div v-if="!b" class="page"><div class="empty">{{ $t('打开绘本…') }}</div></div>
  <div v-else class="ws" data-test="book-shell">
    <nav class="ws-nav">
      <div class="head">
        <h2>{{ b.title_zh || b.title_en || b.id }}</h2>
        <div class="id">{{ b.id }} · {{ b.age_band }} {{ $t('岁') }}</div>
      </div>
      <router-link class="nav" :to="`/picturebook/b/${b.id}/overview`">{{ $t('概览') }} <span class="n" v-if="problems">{{ problems }}</span></router-link>
      <router-link class="nav" :to="`/picturebook/b/${b.id}/pages`">{{ $t('分页表') }} <span class="n">{{ b.pages.length }}</span></router-link>
      <router-link class="nav" :to="`/picturebook/b/${b.id}/characters`">{{ $t('角色与风格') }} <span class="n">{{ chars }}+{{ scenes }}</span></router-link>
      <router-link class="nav" :to="`/picturebook/b/${b.id}/narration`">{{ $t('朗读') }} <span class="n">{{ prog.narration.done }}/{{ total }}</span></router-link>
      <router-link class="nav" :to="`/picturebook/b/${b.id}/final`">{{ $t('成片') }} <span class="n">{{ prog.final ? '✓' : '' }}</span></router-link>
      <router-link class="nav" :to="`/picturebook/b/${b.id}/advanced`">{{ $t('高级') }}</router-link>
      <div class="strip">
        <router-link v-for="p in b.pages" :key="p.id" :to="`/picturebook/b/${b.id}/pages`" :title="p.id" style="aspect-ratio:3/4">
          <img v-if="hasPageImage(p, s)" :src="pageImageUrl(b.id, p, stamp)" alt="" />
          <span class="lab">{{ p.id }}</span>
        </router-link>
      </div>
    </nav>
    <div class="ws-main">
      <div class="ws-top">
        <div class="prog" data-test="book-progress">
          <span>{{ $t('插画') }} <b>{{ prog.pages.done }}/{{ total }}</b></span>
          <span>{{ $t('朗读') }} <b>{{ prog.narration.done }}/{{ total }}</b></span>
          <span>{{ $t('成片') }} <b>{{ prog.final ? '✓' : '—' }}</b></span>
        </div>
        <div class="next">
          <span v-if="store.gen" class="status">{{ store.gen.text }}</span>
          <span v-else-if="nextHint" class="hint" data-test="book-next">{{ nextHint }}</span>
        </div>
      </div>
      <router-view />
    </div>
  </div>
</template>
