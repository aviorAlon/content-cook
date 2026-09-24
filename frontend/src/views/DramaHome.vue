<script setup>
// 短剧模块首页（DESIGN §38）：头部一眼可见的「新建项目」「复制示例」，下面是项目列表；
// 没项目时是三步引导（只给一行小字里的「新建一个」链接，入口太不清晰）。
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { store, roleOk } from '../store.js'
import { T } from '../ui.js'
import { t } from '../i18n.js'
import Examples from '../components/Examples.vue'
import LocalTip from '../components/LocalTip.vue'

const router = useRouter()
const roles = ['text', 'image', 'video', 'voice']
const coreReady = computed(() => roleOk('image') && roleOk('video'))
const projects = computed(() => store.episodes.filter(e => !e.module || e.module === 'drama'))
const showExamples = ref(false)
// 第二行永远带 id：同一个示例复制出来的项目标题相同，只靠标题分不清
function sub(e) {
  if (e.status === 'error') return t('{id} · 文件解析失败', { id: e.id })
  const p = e.progress
  if (!p) return t('{id} · {n} 镜', { id: e.id, n: e.shot_count })
  if (p.final) return t('{id} · 已有成片', { id: e.id })
  if (!e.locked) return t('{id} · 草稿', { id: e.id })
  return t('{id} · 首帧 {a}/{n} · 视频 {b}/{n}', { id: e.id, a: p.frames, b: p.videos, n: p.total })
}
</script>

<template>
  <div class="drama-home" data-test="drama-home">
    <div class="row between" style="margin-bottom:12px;align-items:flex-start;gap:12px;flex-wrap:wrap">
      <div>
        <h2 style="font-size:20px">🎬 {{ t('短剧') }}</h2>
        <p class="hint">{{ t('每个项目都是：角色与场景 → 镜头 → 首帧 → 视频 → 审片 → 后期 → 成片 → 海报。') }}</p>
      </div>
      <div class="row" style="gap:8px">
        <button class="btn primary" data-test="drama-new" @click="router.push('/drama/new')">{{ t('+ 新建项目（AI 起草）') }}</button>
        <button class="btn" data-test="drama-example" @click="showExamples = !showExamples">{{ t('复制示例剧集（不花钱）') }}</button>
      </div>
    </div>
    <div v-if="showExamples" class="card" style="margin-bottom:12px" data-test="drama-examples">
      <h3>{{ t('示例剧集') }}</h3>
      <p class="hint">{{ t('5 镜、有台词、有旁白、有一张插卡，复制成你的项目后可以随便改。') }}</p>
      <Examples />
    </div>

    <div v-if="store.episodesLoaded && projects.length" class="stack" data-test="drama-list">
      <router-link v-for="e in projects" :key="e.id" class="card" :to="`/drama/p/${e.id}/overview`" style="display:flex;gap:14px;align-items:center;text-decoration:none;color:inherit;padding:12px 14px" data-test="drama-row">
        <span style="width:54px;height:96px;flex:none;border-radius:var(--r-sm);background:var(--panel3);overflow:hidden;display:flex;align-items:center;justify-content:center">
          <img v-if="e.cover_url" :src="e.cover_url" alt="" style="width:100%;height:100%;object-fit:cover;display:block" />
          <span v-else class="hint">—</span>
        </span>
        <span style="flex:1;min-width:0">
          <b>{{ e.title || e.id }}</b>
          <div class="hint mono">{{ sub(e) }}</div>
        </span>
      </router-link>
    </div>

    <div v-else-if="store.episodesLoaded" class="onboard">
      <h2 style="font-size:20px;margin-bottom:4px">{{ t('三步出第一条首帧') }}</h2>
      <p class="hint" style="margin-bottom:16px">{{ t('所有数据留在本机；生成模型用你自己的账号，密钥只存在这台电脑上。') }}</p>
      <div class="step" :class="{ done: coreReady }">
        <div class="n">1</div>
        <div class="body"><b>{{ t('配好模型') }}</b>
          <p>{{ t('四类角色各选一个来源、填自己的密钥。只有一个 OpenRouter key 也够：文本、生图、视频、语音都能走它。语音可以先用免费的 Edge TTS。') }}</p>
          <div class="chips" style="margin-bottom:10px">
            <span v-for="r in roles" :key="r" class="pill" :class="{ ok: roleOk(r) }">{{ roleOk(r) ? '✓ ' : '' }}{{ t(T.role[r]) }}</span>
          </div>
          <button class="btn" @click="router.push('/settings')">{{ t('🔌 打开模型设置') }}</button>
          <LocalTip role="image" />
        </div>
      </div>
      <div class="step">
        <div class="n">2</div>
        <div class="body"><b>{{ t('拿到一个项目') }}</b>
          <p>{{ t('最快的路是从示例剧集开始：5 镜、有台词、有旁白、有一张插卡，复制成你的项目后可以随便改。也可以用一句话让文本模型起草整集。') }}</p>
          <Examples />
          <button class="btn" style="margin-top:8px" @click="router.push('/drama/new')">{{ t('+ 用 AI 起草一集') }}</button>
        </div>
      </div>
      <div class="step">
        <div class="n">3</div>
        <div class="body"><b>{{ t('出首帧') }}</b>
          <p>{{ t('打开项目 → 「开始制作」→ 先给一镜「生成首帧」看风格，满意再整批生成。每次生成前都先告诉你要发几次请求，确认了才花钱。') }}</p>
        </div>
      </div>
    </div>
    <div v-else class="empty">{{ t('读取项目列表…') }}</div>
  </div>
</template>
