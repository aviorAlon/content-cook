<script setup>
// 剧本工作台首页（docs/scriptlab-spec.md §9）：列表（标题 / 目标形态 / 段数 / 版本数 / 采用的是第几版 / 已投放几处）
// +「+ 新建」+「从现有项目导入」（选播客期 / 短剧集 → 先建一个工作台项目，再把那边现有的台词导进来当第一版）。
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { del, post } from '../../api.js'
import { store, withBusy } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import {
  SCRIPT_API, sc, loadScripts, loadOptions, loadTargets, progressOf, scriptRoute, targetName,
  emptyBrief, USE_MODULES, targetRows, useModule,
} from '../../scriptlab.js'

const router = useRouter()
const status = ref('')
const optionsReady = ref(false)   // 形态名来自模块级缓存；拉到后让列表重渲染
const targets = ref({ podcast: [], drama: [] })
const imp = reactive({ module: 'podcast', key: '' })

onMounted(async () => {
  if (!store.scriptsLoaded) loadScripts()
  await loadOptions(); optionsReady.value = true
  targets.value = await loadTargets()
})

const rows = computed(() => store.scripts || [])
const prog = (r) => progressOf(r)
const tname = (r) => { void optionsReady.value; return targetName(r.target) }
const importRows = computed(() => targetRows(targets.value, imp.module))

// 从现有项目导入：接口是「先有工作台项目，再往里导」（§7），所以这里两步走：POST /api/script 建壳 → POST /{id}/import。
// 建壳时角色表是空的（那边的主播 / 人物由 import 带进来，§7 说得很清楚），所以 POST 的归一化对 speakers 的下限
// 要放行空表——这条写进了 docs 的「给后端的接口约定补充」。第二步没成就把刚建出来的空壳归档掉，不在列表里留一个
// 没角色没台词的空项目。
async function importFrom() {
  const row = importRows.value.find(x => x.id === imp.key)
  if (!row) { status.value = t('先选一个要导入的项目'); return }
  status.value = t('导入中…')
  await withBusy(async () => {
    let created = null
    try {
      created = await post(SCRIPT_API, {
        title: row.title, target: imp.module, language: 'zh', brief: emptyBrief(), speakers: [], blocks: [], versions: [],
      })
      await post(`${sc(created.id)}/import`, { module: imp.module, target_id: row.id })
    } catch (e) {
      if (created?.id) { try { await del(sc(created.id)) } catch { /* 壳删不掉就留着，首页的 ✕ 能删 */ } }
      status.value = t('失败：{msg}', { msg: e.message })
      await loadScripts().catch(() => {})
      await notice(e.message)
      return
    }
    await loadScripts()
    status.value = ''
    router.push(scriptRoute(created.id))
  })
}
</script>

<template>
  <div class="page narrow" data-test="script-home">
    <div class="row between" style="margin-bottom:12px">
      <div>
        <h2 style="font-size:20px">✍️ {{ $t('剧本工作台') }}</h2>
        <p class="hint">{{ $t('台词单独拿出来调：一段换几种写法，每次生成、重写、手改都另存一版，从不覆盖；比过挑一版，再投放到某一期播客的某个环节、某一集短剧的某一场或某一镜。这个模块不出图、不出声，只管文字。') }}</p>
      </div>
      <div class="row">
        <button class="btn primary" data-test="script-new" @click="router.push('/script/new')">{{ $t('+ 新建剧本') }}</button>
      </div>
    </div>
    <div v-if="status" class="status" style="margin-bottom:8px">{{ status }}</div>

    <div v-if="rows.length" class="stack" data-test="script-list">
      <router-link v-for="r in rows" :key="r.id" class="card" :to="scriptRoute(r.id)" style="display:flex;gap:14px;align-items:center;text-decoration:none;color:inherit;padding:12px 14px" data-test="script-row">
        <span style="width:52px;height:52px;flex:none;border-radius:var(--r-sm);background:var(--panel3);display:flex;align-items:center;justify-content:center;font-size:22px">✍️</span>
        <span style="flex:1;min-width:0">
          <b>{{ r.title || r.id }}</b> <span class="pill" v-if="r.target" data-test="script-target">{{ tname(r) }}</span>
          <div class="hint mono">{{ r.id }} · {{ $t('{n} 段', { n: prog(r).blocks }) }} · {{ $t('{n} 版', { n: prog(r).versions }) }}<span v-if="prog(r).currentIndex"> · {{ $t('采用第 {k} 版', { k: prog(r).currentIndex }) }}</span><span v-if="prog(r).used"> · {{ $t('已投放 {n} 处', { n: prog(r).used }) }}</span></div>
        </span>
      </router-link>
    </div>
    <div v-else-if="store.scriptsLoaded" class="card" data-test="script-empty">
      <h3>{{ $t('还没有剧本') }}</h3>
      <p class="hint">{{ $t('两条路：「+ 新建剧本」从选题、角色和段落骨架开始写；或者「从现有项目导入」，把某一期播客、某一集短剧现在的台词拿过来调，调好了再放回去。') }}</p>
    </div>
    <div v-else class="empty">{{ $t('读取列表…') }}</div>

    <!-- 从现有项目导入 -->
    <div class="card" data-test="script-import">
      <h3>{{ $t('从现有项目导入') }}</h3>
      <p class="hint">{{ $t('把那边现有的台词原样拿过来当第一版，角色 / 主播也一起带进来（人设留空）。原项目不动，在这里调好了再投放回去。') }}</p>
      <div class="row" style="margin-top:6px;align-items:flex-end">
        <div class="field">
          <label>{{ $t('从哪来') }}</label>
          <select v-model="imp.module" data-test="import-module" @change="imp.key = ''">
            <option v-for="m in USE_MODULES" :key="m.id" :value="m.id">{{ $t(m.label) }}</option>
          </select>
        </div>
        <div class="field" style="flex:1">
          <label>{{ $t('哪个项目') }}</label>
          <select v-model="imp.key" data-test="import-target">
            <option value="">{{ $t('（选一个项目）') }}</option>
            <option v-for="x in importRows" :key="x.id" :value="x.id">{{ x.title }}（{{ x.id }}）</option>
          </select>
        </div>
        <button class="btn" :disabled="!imp.key" data-test="import-submit" @click="importFrom">{{ $t('导入并新建') }}</button>
      </div>
      <p class="hint small" v-if="!importRows.length">{{ $t('这边还没有可导入的项目：{name}里先建一个。', { name: $t(useModule(imp.module).label) }) }}</p>
    </div>
  </div>
</template>
