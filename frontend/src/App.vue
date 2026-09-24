<script setup>
import { computed, onMounted, watchEffect } from 'vue'
import Sidebar from './components/Sidebar.vue'
import ModalHost from './components/ModalHost.vue'
import { store, loadHealth, loadEpisodes, loadModules, productName, moduleById } from './store.js'
import { loadBooks } from './picturebook.js'
import { loadSets, IMAGESET_IDS } from './imageset.js'
import { loadClips } from './clip.js'
import { loadMusics } from './music.js'
import { loadPodcasts } from './podcast.js'
import { loadScripts } from './scriptlab.js'
import { toasts } from './ui.js'
import { t } from './i18n.js'

// 产品名唯一来源在后端 webui/branding.py（DESIGN §38）：/api/health 和 /api/modules 都带；还没拉到时用工作名兜底
const title = computed(() => productName())
watchEffect(() => { if (typeof document !== 'undefined') document.title = title.value })

onMounted(async () => {
  await loadHealth()
  loadModules().then(() => {
    if (moduleById('picturebook')?.available) loadBooks()
    // 图片集模块（表情与头像 / 纪念写真）：注册表说可用才拉列表
    for (const m of IMAGESET_IDS) if (moduleById(m)?.available) loadSets(m)
    // 单条视频（docs/clip-spec.md §6）：同上
    if (moduleById('clip')?.available) loadClips()
    // 配乐（docs/music-spec.md §5）：同上
    if (moduleById('music')?.available) loadMusics()
    // AI 播客（docs/podcast-spec.md §6）：同上
    if (moduleById('podcast')?.available) loadPodcasts()
    // 剧本工作台（docs/scriptlab-spec.md §9）：同上
    if (moduleById('script')?.available) loadScripts()
  }).catch(() => {})
  await loadEpisodes().catch(() => {})
})
</script>

<template>
  <div class="app">
    <header class="app-header">
      <span class="dot"></span>
      <h1 data-test="product-name">{{ title }}</h1>
      <span class="tag">{{ store.health ? `v${store.health.version} · ` : '' }}{{ t('本地版 · 数据不出本机') }}</span>
    </header>
    <div class="busy-bar" :class="{ on: store.busy > 0 || store.gen }"><i></i></div>
    <div v-if="store.health && !store.health.ffmpeg_ok" class="sys-banner">
      ⚠ {{ t('没在 PATH 里找到') }} <code>ffmpeg</code> / <code>ffprobe</code>{{ t('——首帧和视频生成不受影响，但「合成成片」「后期合成」「台词识别」都需要它。') }}
      <a :href="store.health.ffmpeg_download_url" target="_blank" rel="noopener">{{ t('去下载') }}</a>
      <span v-if="store.health.frozen">{{ t('，把 ffmpeg.exe 和 ffprobe.exe 放进程序所在目录后重启。') }}</span>
    </div>
    <div class="app-body">
      <Sidebar />
      <div class="main">
        <router-view />
      </div>
    </div>
    <ModalHost />
    <div class="toasts">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.kind">{{ t.message }}</div>
    </div>
  </div>
</template>
