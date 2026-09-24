<script setup>
// 工作台首页 = 启动台（DESIGN §38，2026-09-10 拆分）：产品名 + 一句定位、模块卡（来自 /api/modules）、
// 跨模块的「最近」（/api/recent）、在跑的任务（/api/tasks）、第一次用的三步。
// 这里**不再嵌短剧首页**——短剧只是七个模块里的一个，它的首页在 /drama。
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { store, productName } from '../store.js'
import { t, isEn } from '../i18n.js'
import { activeCount, fetchRecent, fetchTasks, relTime } from '../tasks.js'

const modules = computed(() => store.modules || [])
const ready = computed(() => modules.value.filter(m => m.available))
const name = (m) => (isEn() && m.english) ? m.english : m.name
const desc = (m) => (isEn() && m.description_en) ? m.description_en : m.description

// 「最近」：跨模块按 mtime 倒序，点一行直接进那个项目的页面
const recent = ref([])
const moduleName = (r) => (isEn() && r.module_name_en) ? r.module_name_en : (r.module_name || r.module)

// 「正在跑的任务」：只在真有 pending/running 时出现，点进任务中心
const running = ref(0)
let timer = null

async function refresh() {
  const [rows, tasks] = await Promise.all([fetchRecent(12), fetchTasks().catch(() => [])])
  recent.value = rows
  running.value = activeCount(tasks)
}

onMounted(() => {
  refresh().catch(() => {})
  // 首页停留期间每 15 秒对一次在跑的任务数（只读，不花钱）
  timer = setInterval(() => { refresh().catch(() => {}) }, 15000)
})
onUnmounted(() => { if (timer) clearInterval(timer); timer = null })

// 第一次用：所有可用模块都还没有项目（也没有最近记录）时，给三步模块无关的引导
const firstRun = computed(() => store.modulesLoaded && ready.value.length > 0
  && ready.value.every(m => !m.project_count) && !recent.value.length)
</script>

<template>
  <div class="page narrow">
    <header class="launcher-head" data-test="launcher-head">
      <h2>{{ productName() }}</h2>
      <p class="hint">{{ t('一台电脑上的内容创作工作台：挑一个模块开始，数据留在本机，每次花钱前都先告诉你要发几次请求。') }}</p>
    </header>

    <div class="modules" data-test="module-cards">
      <template v-for="m in modules" :key="m.id">
        <router-link v-if="m.available" class="module-card ready" :to="m.route" :data-test="`module-${m.id}`">
          <span class="icon">{{ m.icon }}</span>
          <span class="body">
            <b>{{ name(m) }}</b>
            <span class="desc">{{ desc(m) }}</span>
            <span class="count">{{ t('{n} 个项目', { n: m.project_count }) }}</span>
          </span>
        </router-link>
        <div v-else class="module-card planned" :data-test="`module-${m.id}`" :title="desc(m)">
          <span class="icon">{{ m.icon }}</span>
          <span class="body">
            <b>{{ name(m) }} <span class="pill small">{{ t('规划中') }}</span></b>
            <span class="desc">{{ desc(m) }}</span>
          </span>
        </div>
      </template>
      <div v-if="store.modulesLoaded && !modules.length" class="hint">{{ t('没有读到模块列表——刷新一下，或看后端日志。') }}</div>
    </div>

    <section v-if="recent.length" class="recent" data-test="recent">
      <div class="group">{{ t('最近') }}</div>
      <div class="stack">
        <router-link v-for="r in recent" :key="`${r.module}/${r.id}`" class="card recent-row" :to="r.url" data-test="recent-row">
          <span class="ico">{{ r.icon || '▪' }}</span>
          <span class="meta">
            <b>{{ r.title || r.id }}</b>
            <span class="hint mono">{{ moduleName(r) }} · {{ r.id }}</span>
          </span>
          <span class="hint when">{{ relTime(r.mtime) }}</span>
        </router-link>
      </div>
    </section>

    <router-link v-if="running" class="card running-line" to="/tasks" data-test="running-tasks">
      <span class="ico">⏳</span>
      <span>{{ t('{n} 个任务在跑', { n: running }) }}</span>
      <span class="hint">{{ t('去任务中心看看 →') }}</span>
    </router-link>

    <div v-if="firstRun" class="onboard" data-test="first-run">
      <h2 style="font-size:20px;margin-bottom:4px">{{ t('第一次用') }}</h2>
      <p class="hint" style="margin-bottom:16px">{{ t('所有数据留在本机；生成模型用你自己的账号，密钥只存在这台电脑上。') }}</p>
      <div class="step">
        <div class="n">1</div>
        <div class="body"><b>{{ t('配好模型') }}</b>
          <p>{{ t('每类角色选一个来源、填自己的密钥。只有一个 OpenRouter key 也够。有显卡的话可以全程本地跑，不花钱、不出网。') }}</p>
          <router-link class="btn" to="/settings" data-test="first-run-settings">{{ t('🔌 打开模型设置') }}</router-link>
          <router-link class="btn" to="/settings" data-test="first-run-local">{{ t('→ 有显卡就本地跑（扫描本机）') }}</router-link>
        </div>
      </div>
      <div class="step">
        <div class="n">2</div>
        <div class="body"><b>{{ t('选一个模块开始') }}</b>
          <p>{{ t('上面每张卡就是一条产线：短剧、绘本、图片、单条视频、配乐……每个模块都能从示例项目复制一份，不花钱。') }}</p>
        </div>
      </div>
      <div class="step">
        <div class="n">3</div>
        <div class="body"><b>{{ t('花钱前都会先问你') }}</b>
          <p>{{ t('每次生成前都先告诉你这一批要发几次请求，确认了才发；跑起来的任务都记在任务中心。') }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.launcher-head { margin-bottom: 18px; }
.launcher-head h2 { font-size: 20px; margin-bottom: 4px; }
.recent { margin-bottom: 18px; }
.recent-row { display: flex; gap: 12px; align-items: center; text-decoration: none; color: inherit; padding: 10px 14px; }
.recent-row .ico { font-size: 18px; flex: none; }
.recent-row .meta { flex: 1; min-width: 0; }
.recent-row .meta b { display: block; font-size: 13.5px; }
.recent-row .when { flex: none; font-size: 12px; }
.running-line { display: flex; gap: 10px; align-items: center; text-decoration: none; color: inherit; padding: 10px 14px; margin-bottom: 18px; }
</style>
