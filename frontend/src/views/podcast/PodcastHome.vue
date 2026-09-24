<script setup>
// AI 播客首页（docs/podcast-spec.md §6）：列表（标题 / 形式 / 主播数 / 预计分钟 / 进度：已起草 · 已配音 · 已混音）+ 新建 + 复制示例。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { store, withBusy } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import { loadPodcasts, loadFormats, createFromExample, progressOf, podcastRoute, formatName } from '../../podcast.js'

const router = useRouter()
const status = ref('')
const formatsReady = ref(false)   // 形式名来自模块级缓存；拉到后让列表重渲染
onMounted(async () => {
  if (!store.podcastsLoaded) loadPodcasts()
  await loadFormats(); formatsReady.value = true
})
const rows = computed(() => store.podcasts || [])
const prog = (r) => progressOf(r)
const fmt = (r) => { void formatsReady.value; return formatName(r.format) }

async function copyExample() {
  status.value = t('复制中…')
  await withBusy(async () => {
    try {
      const r = await createFromExample()
      status.value = ''
      router.push(podcastRoute(r.id))
    } catch (e) { status.value = t('失败：{msg}', { msg: e.message }); await notice(e.message) }
  })
}
</script>

<template>
  <div class="page narrow" data-test="podcast-home">
    <div class="row between" style="margin-bottom:12px">
      <div>
        <h2 style="font-size:20px">🎙 {{ $t('AI 播客') }}</h2>
        <p class="hint">{{ $t('写清选题、角度和素材，起草一份像真人在聊的对谈稿，每位主播一个音色，逐句配音混成一期，附节目单。') }}</p>
      </div>
      <div class="row">
        <button class="btn primary" data-test="podcast-new" @click="router.push('/podcast/new')">{{ $t('+ 新建播客') }}</button>
        <button class="btn" data-test="podcast-example" @click="copyExample">{{ $t('复制示例（不花钱）') }}</button>
      </div>
    </div>
    <div v-if="status" class="status" style="margin-bottom:8px">{{ status }}</div>
    <div v-if="rows.length" class="stack" data-test="podcast-list">
      <router-link v-for="r in rows" :key="r.id" class="card" :to="podcastRoute(r.id)" style="display:flex;gap:14px;align-items:center;text-decoration:none;color:inherit;padding:12px 14px" data-test="podcast-row">
        <span style="width:52px;height:52px;flex:none;border-radius:var(--r-sm);background:var(--panel3);display:flex;align-items:center;justify-content:center;font-size:22px">🎙</span>
        <span style="flex:1;min-width:0">
          <b>{{ r.title || r.id }}</b> <span class="pill" v-if="r.format" data-test="podcast-format">{{ fmt(r) }}</span>
          <div class="hint mono">{{ r.id }} · {{ $t('{n} 位主播', { n: prog(r).hosts }) }} · {{ $t('预计 {m} 分钟', { m: prog(r).minutes }) }}<span v-if="prog(r).segments"> · {{ $t('{n} 个环节', { n: prog(r).segments }) }}</span></div>
          <div class="chips" style="margin-top:4px" data-test="podcast-progress">
            <span class="pill" :class="{ ok: prog(r).drafted }">{{ $t('已起草') }}</span>
            <span class="pill" :class="{ ok: prog(r).audio }">{{ $t('已配音') }}</span>
            <span class="pill" :class="{ ok: prog(r).mixed }">{{ $t('已混音') }}</span>
          </div>
        </span>
      </router-link>
    </div>
    <div v-else-if="store.podcastsLoaded" class="card" data-test="podcast-empty">
      <h3>{{ $t('还没有播客') }}</h3>
      <p class="hint">{{ $t('两条路：「新建播客」填好选题与主播再起草（起草会发 1 次文本模型请求）；或者「复制示例」拿一份现成的稿子直接看形状、试合成（不花钱）。') }}</p>
    </div>
    <div v-else class="empty">{{ $t('读取列表…') }}</div>
  </div>
</template>
