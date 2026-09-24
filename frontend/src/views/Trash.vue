<script setup>
// 回收站（全局页，/trash）：各模块删除的项目都先收在这里（后端 .webui_deleted/<模块>/，连同产物、上传的素材一起），
// 按模块分组列出标题 / 删除时间 / 大小；「恢复」原样放回并跳到那个项目，「彻底删除」二次确认后才真的从磁盘上删掉。
// ?module=storyvideo（侧栏短剧分组里的入口）只看一个模块；界面模块名 drama 也认。
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store } from '../store.js'
import { t, isEn } from '../i18n.js'
import { confirm, notice, toast } from '../ui.js'
import {
  fetchTrash, fmtBytes, fmtDeletedAt, groupTrash, moduleMeta, projectRoute, purgeEntry, restoreEntry, trashModuleKey,
} from '../trash.js'

const route = useRoute()
const router = useRouter()

const rows = ref([])
const loaded = ref(false)
const err = ref('')
const busy = ref(null)          // 正在恢复 / 删除的那一条（entry）：按钮先禁掉，双击不会发两次
const filter = ref(trashModuleKey(route.query.module))

const groups = computed(() => groupTrash(filter.value ? rows.value.filter(x => x.module === filter.value) : rows.value))
const presentModules = computed(() => groupTrash(rows.value).map(g => ({ module: g.module, count: g.items.length })))
const totalBytes = computed(() => groups.value.reduce((s, g) => s + g.bytes, 0))
const totalCount = computed(() => groups.value.reduce((s, g) => s + g.items.length, 0))

// 模块名：注册表（/api/modules）里有就用它的中英文名，没有就用本地表
function moduleLabel(m) {
  const meta = moduleMeta(m)
  const reg = (store.modules || []).find(x => x.id === meta.ui)
  if (reg && isEn() && reg.english) return reg.english
  return t(meta.label)
}
const moduleIcon = (m) => moduleMeta(m).icon

function rowSub(x) {
  return t('{id} · 删除于 {time} · {size}', { id: x.id, time: fmtDeletedAt(x), size: fmtBytes(x.bytes) })
}
// 这一行为什么恢复不了 / 恢复前要知道的事：写在行上，不用点了才知道
function rowNote(x) {
  if (!x.restorable) return t('这一条不能从界面恢复（不认识的模块，或归档里缺项目文件）；不需要了可以彻底删除')
  if (x.ledger_only) return t('只剩账本记录（文件已搬回原位）：恢复会把账本记录放回去')
  if (x.id_taken) return t('同 id 的项目现在就在：先删除现在那个，才能恢复这一份')
  if (x.spec_broken) return t('项目文件本身读不出来（删除前就坏了）：恢复会原样放回去，列表里标成出错，用文本编辑器修好才能打开')
  return ''
}

async function load() {
  try {
    rows.value = await fetchTrash()
    err.value = ''
  } catch (e) { err.value = e.message }
  loaded.value = true
}

function pick(m) {
  filter.value = m
  const query = { ...route.query }
  if (m) query.module = m; else delete query.module
  router.replace({ path: route.path, query }).catch(() => {})
}
watch(() => route.query.module, (v) => { filter.value = trashModuleKey(v) })

// 停在回收站页上时从侧栏删了一个项目：各模块的删除都会重拉自己的列表，哪个列表条数一变就重拉回收站，
// 不然刚删的那条要刷新页面才出现。恢复时自己会重拉（busy 期间跳过，免得重复请求）
const listSignature = computed(() => [store.episodes, store.books, store.clips, store.musics, store.podcasts, store.scripts,
  store.sets?.sticker, store.sets?.portrait, store.sets?.image].map(x => (x || []).length).join(','))
watch(listSignature, () => { if (!busy.value) load().catch(() => {}) })

async function restore(x) {
  if (busy.value) return
  busy.value = x.entry
  try {
    const r = await restoreEntry(x)
    await load()
    toast(t('已恢复「{name}」', { name: x.title || x.id }))
    if (r?.warning) await notice(r.warning)
    // 放回去的项目文件本身读不出来（删除前就坏了）：跳进项目页只会看到一个打不开的页面，留在这里告诉人去修哪个文件
    if (r?.spec_broken) {
      await notice(t('「{name}」已放回原处，但项目文件本身读不出来（删除前就是坏的），列表里会标成出错。用文本编辑器修好数据目录里的 {path} 再打开。',
        { name: x.title || x.id, path: r.path || '' }))
      return
    }
    const to = projectRoute(x.module, r?.id || x.id)
    if (to) router.push(to)
  } catch (e) {
    await notice(e.message)
    await load()
  } finally { busy.value = null }
}

async function purge(x) {
  if (busy.value) return
  const ok = await confirm(t('彻底删除「{name}」？\n\n项目文件和生成过的产物（{size}）会从磁盘上删掉，不可恢复。', { name: x.title || x.id, size: fmtBytes(x.bytes) }),
    { confirmLabel: t('彻底删除'), danger: true })
  if (!ok) return
  busy.value = x.entry
  try {
    await purgeEntry(x)
    toast(t('已彻底删除「{name}」', { name: x.title || x.id }))
  } catch (e) { await notice(e.message) } finally {
    busy.value = null
    await load()
  }
}

onMounted(() => { load().catch(() => {}) })
</script>

<template>
  <div class="page narrow">
    <div class="row between" style="margin-bottom:12px;align-items:flex-start;gap:12px;flex-wrap:wrap">
      <div>
        <h2 style="font-size:20px">🗑 {{ t('回收站') }}</h2>
        <p class="hint">{{ t('删除的项目先收在这里，连同生成过的产物和上传的素材；点「恢复」原样放回去。「彻底删除」才会真的从磁盘上删掉，删了就找不回来。') }}</p>
      </div>
      <div v-if="totalCount" class="hint" data-test="trash-total">{{ t('{n} 项 · 共 {size}', { n: totalCount, size: fmtBytes(totalBytes) }) }}</div>
    </div>

    <div v-if="presentModules.length > 1 || filter" class="card" style="margin-bottom:12px" data-test="trash-filters">
      <div class="row" style="gap:6px;flex-wrap:wrap;align-items:center">
        <span class="hint">{{ t('模块') }}</span>
        <button class="btn sm" :class="{ primary: !filter }" data-test="trash-filter-all" @click="pick('')">{{ t('全部') }}</button>
        <button v-for="m in presentModules" :key="m.module" class="btn sm" :class="{ primary: filter === m.module }"
                :data-test="`trash-filter-${m.module}`" @click="pick(m.module)">{{ moduleIcon(m.module) }} {{ moduleLabel(m.module) }} · {{ m.count }}</button>
      </div>
    </div>

    <div v-if="err" class="card" style="color:var(--bad)" data-test="trash-error">{{ err }}</div>

    <section v-for="g in groups" :key="g.module" class="card" data-test="trash-group" :data-module="g.module">
      <h3>{{ moduleIcon(g.module) }} {{ moduleLabel(g.module) }} <span class="hint" style="font-weight:400">{{ t('{n} 项 · 共 {size}', { n: g.items.length, size: fmtBytes(g.bytes) }) }}</span></h3>
      <div v-for="x in g.items" :key="x.entry" class="trash-row" data-test="trash-row" :data-entry="x.entry">
        <div class="meta">
          <div class="name">{{ x.title || x.id }}</div>
          <div class="hint small" data-test="trash-sub">{{ rowSub(x) }}</div>
          <div v-if="rowNote(x)" class="hint small warn" data-test="trash-note">{{ rowNote(x) }}</div>
        </div>
        <div class="actions">
          <button class="btn sm primary" data-test="trash-restore" :disabled="!!busy || !x.restorable" @click="restore(x)">{{ t('恢复') }}</button>
          <button class="btn sm danger" data-test="trash-purge" :disabled="!!busy" @click="purge(x)">{{ t('彻底删除') }}</button>
        </div>
      </div>
    </section>

    <div v-if="loaded && !err && !totalCount" class="empty" data-test="trash-empty">
      {{ filter ? t('这个模块没有删除过的项目。') : t('回收站是空的。删除的项目会先收在这里，随时可以恢复。') }}
    </div>
    <div v-else-if="!loaded" class="empty">{{ t('读取回收站…') }}</div>
  </div>
</template>

<style scoped>
.trash-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-top: 1px solid var(--border-soft); }
.trash-row:first-of-type { border-top: none; }
.trash-row .meta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.trash-row .name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
.trash-row .actions { display: flex; gap: 6px; flex-shrink: 0; }
.small { font-size: 12px; }
</style>
