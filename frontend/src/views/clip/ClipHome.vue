<script setup>
// 单条视频模块首页（docs/clip-spec.md §6）：片子列表（标题 / 画幅 / 时长 / 拍数 / 进度）+ 新建 + 复制示例
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { store, withBusy } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import { loadClips, createFromExample, progressOf, clipRoute } from '../../clip.js'

const router = useRouter()
const status = ref('')
onMounted(() => { if (!store.clipsLoaded) loadClips() })
const clips = computed(() => store.clips || [])
const prog = (c) => progressOf(c)
// 「复制示例」：POST /api/clip/examples/create（随程序附带的 examples/clip/*.json，不花钱）
async function copyExample() {
  status.value = t('复制中…')
  await withBusy(async () => {
    try {
      const r = await createFromExample()
      status.value = ''
      router.push(clipRoute(r.id))
    } catch (e) { status.value = t('失败：{msg}', { msg: e.message }); await notice(e.message) }
  })
}
</script>

<template>
  <div class="page narrow" data-test="clip-home">
    <div class="row between" style="margin-bottom:12px">
      <div>
        <h2 style="font-size:20px">🎞 {{ $t('单条视频') }}</h2>
        <p class="hint">{{ $t('一条提示词出一段视频：广告 / 预告 / 单镜素材。写法是「全片设定 / 分段时间线 / 衔接与收尾」三段式——不拆镜、不进账本，一个项目就是一条片子。') }}</p>
      </div>
      <div class="row">
        <button class="btn primary" data-test="clip-new" @click="router.push('/clip/new')">{{ $t('+ 新建片子') }}</button>
        <button class="btn" data-test="clip-example" @click="copyExample">{{ $t('复制示例（不花钱）') }}</button>
      </div>
    </div>
    <div v-if="status" class="status" style="margin-bottom:8px">{{ status }}</div>
    <div v-if="clips.length" class="stack" data-test="clip-list">
      <router-link v-for="c in clips" :key="c.id" class="card" :to="clipRoute(c.id)" style="display:flex;gap:14px;align-items:center;text-decoration:none;color:inherit;padding:12px 14px" data-test="clip-row">
        <span style="width:88px;height:50px;flex:none;border-radius:var(--r-sm);background:var(--panel3);overflow:hidden;display:flex;align-items:center;justify-content:center">
          <video v-if="c.video_url" :src="c.video_url" muted style="width:100%;height:100%;object-fit:cover;display:block"></video>
          <span v-else class="hint">🎞</span>
        </span>
        <span style="flex:1;min-width:0">
          <b>{{ c.title || c.id }}</b> <span class="pill">{{ c.aspect_ratio || '16:9' }}</span>
          <div class="hint mono">{{ c.id }} · {{ $t('{n} 秒', { n: c.duration ?? 0 }) }} · {{ $t('{n} 拍', { n: c.beats ?? 0 }) }}</div>
          <div class="chips" style="margin-top:4px">
            <span class="pill" :class="{ ok: prog(c).done }">{{ prog(c).done ? $t('已有成片') : $t('还没出片') }}</span>
            <span class="pill" v-if="prog(c).attempts">{{ $t('出过 {n} 次', { n: prog(c).attempts }) }}</span>
            <span class="pill warn" v-if="c.status === 'error'">{{ $t('文件解析失败') }}</span>
          </div>
        </span>
      </router-link>
    </div>
    <div v-else-if="store.clipsLoaded" class="card" data-test="clip-empty">
      <h3>{{ $t('还没有片子') }}</h3>
      <p class="hint">{{ $t('两条路：「新建片子」自己写三段式（写法页有实时提示词预览），或者「复制示例」拿一条 30 秒示例短片直接改（不花钱）。') }}</p>
    </div>
    <div v-else class="empty">{{ $t('读取列表…') }}</div>
  </div>
</template>
