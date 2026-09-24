<script setup>
// 配乐模块首页（docs/music-spec.md §5）：列表（标题 / 体裁 / 时长 / 进度 / 投放去向）+ 新建 + 复制示例。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { store, withBusy } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import { loadMusics, createFromExample, progressOf, musicRoute } from '../../music.js'

const router = useRouter()
const status = ref('')
onMounted(() => { if (!store.musicsLoaded) loadMusics() })
const rows = computed(() => store.musics || [])
const prog = (r) => progressOf(r)

async function copyExample() {
  status.value = t('复制中…')
  await withBusy(async () => {
    try {
      const r = await createFromExample()
      status.value = ''
      router.push(musicRoute(r.id))
    } catch (e) { status.value = t('失败：{msg}', { msg: e.message }); await notice(e.message) }
  })
}
</script>

<template>
  <div class="page narrow" data-test="music-home">
    <div class="row between" style="margin-bottom:12px">
      <div>
        <h2 style="font-size:20px">🎵 {{ $t('配乐') }}</h2>
        <p class="hint">{{ $t('写清用途、情绪、乐器和结构，一次出几条候选，挑一条放进短剧的配乐层或绘本的 BGM。') }}</p>
      </div>
      <div class="row">
        <button class="btn primary" data-test="music-new" @click="router.push('/music/new')">{{ $t('+ 新建配乐') }}</button>
        <button class="btn" data-test="music-example" @click="copyExample">{{ $t('复制示例（不花钱）') }}</button>
      </div>
    </div>
    <div v-if="status" class="status" style="margin-bottom:8px">{{ status }}</div>
    <div v-if="rows.length" class="stack" data-test="music-list">
      <router-link v-for="r in rows" :key="r.id" class="card" :to="musicRoute(r.id)" style="display:flex;gap:14px;align-items:center;text-decoration:none;color:inherit;padding:12px 14px" data-test="music-row">
        <span style="width:52px;height:52px;flex:none;border-radius:var(--r-sm);background:var(--panel3);display:flex;align-items:center;justify-content:center;font-size:22px">🎵</span>
        <span style="flex:1;min-width:0">
          <b>{{ r.title || r.id }}</b> <span class="pill" v-if="r.genre" data-test="music-genre">{{ r.genre }}</span>
          <div class="hint mono">{{ r.id }}<span v-if="r.duration"> · {{ r.duration }}s</span><span v-if="r.count"> · ×{{ r.count }}</span></div>
          <div class="chips" style="margin-top:4px">
            <span class="pill" :class="{ ok: prog(r).done }" data-test="music-progress">{{ prog(r).done ? $t('已采用 1 条') : $t('还没出') }}</span>
            <span class="pill">{{ $t('历次 {n}', { n: prog(r).attempts }) }}</span>
            <span class="pill" :class="{ ok: prog(r).used > 0 }" data-test="music-used">{{ $t('已投放 {n}', { n: prog(r).used }) }}</span>
          </div>
        </span>
      </router-link>
    </div>
    <div v-else-if="store.musicsLoaded" class="card" data-test="music-empty">
      <h3>{{ $t('还没有配乐') }}</h3>
      <p class="hint">{{ $t('两条路：「新建配乐」挑一张预设 brief 再改（出配乐会花钱）；或者「复制示例」拿一份现成的直接看形状（不花钱）。') }}</p>
    </div>
    <div v-else class="empty">{{ $t('读取列表…') }}</div>
  </div>
</template>
