<script setup>
// 图片集模块首页（docs/imageset-spec.md §5）：表情与头像 / 纪念写真共用，模块来自路由 meta.module。
// 列表（封面 / 标题 / 模板名 / 进度药丸）+ 新建 + 复制示例。
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { store, withBusy } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import { moduleInfo, loadSets, loadTemplates, createFromExample, progressOf, templateName, setRoute } from '../../imageset.js'

const route = useRoute()
const router = useRouter()
const m = computed(() => route.meta.module || 'sticker')
const info = computed(() => moduleInfo(m.value))
const status = ref('')
const tplTick = ref(0)
watch(m, async (mod) => {
  if (!store.setsLoaded[mod]) loadSets(mod)
  await loadTemplates(mod); tplTick.value++
}, { immediate: true })
const rows = computed(() => store.sets[m.value] || [])
const title = (r) => r.title || r.id
const tplName = (r) => { void tplTick.value; return templateName(m.value, r.template, r.template_name) }
const prog = (r) => progressOf(r)
// 「复制示例」：POST /api/{m}/examples/create（随程序附带的 examples/<module>/*.json，不花钱）
async function copyExample() {
  status.value = t('复制中…')
  await withBusy(async () => {
    try {
      const r = await createFromExample(m.value)
      status.value = ''
      router.push(setRoute(m.value, r.id))
    } catch (e) { status.value = t('失败：{msg}', { msg: e.message }); await notice(e.message) }
  })
}
</script>

<template>
  <div class="page narrow" data-test="set-home" :data-module="m">
    <div class="row between" style="margin-bottom:12px">
      <div>
        <h2 style="font-size:20px">{{ info.icon }} {{ $t(info.name) }}</h2>
        <p class="hint">{{ $t(info.blurb) }}</p>
      </div>
      <div class="row">
        <button class="btn primary" data-test="set-new" @click="router.push(`/${m}/new`)">{{ $t('+ 新建') }}</button>
        <button class="btn" data-test="set-example" @click="copyExample">{{ $t('复制示例（不花钱）') }}</button>
      </div>
    </div>
    <div v-if="status" class="status" style="margin-bottom:8px">{{ status }}</div>
    <div v-if="rows.length" class="stack" data-test="set-list">
      <router-link v-for="r in rows" :key="r.id" class="card" :to="setRoute(m, r.id)" style="display:flex;gap:14px;align-items:center;text-decoration:none;color:inherit;padding:12px 14px" data-test="set-row">
        <span style="width:64px;height:64px;flex:none;border-radius:var(--r-sm);background:var(--panel3);overflow:hidden;display:flex;align-items:center;justify-content:center">
          <img v-if="r.cover_url" :src="r.cover_url" alt="" style="width:100%;height:100%;object-fit:cover;display:block" />
          <span v-else class="hint">{{ info.icon }}</span>
        </span>
        <span style="flex:1;min-width:0">
          <b>{{ title(r) }}</b> <span class="pill" data-test="set-template">{{ tplName(r) || '—' }}</span>
          <div class="hint mono">{{ r.id }}<span v-if="r.style"> · {{ r.style }}</span></div>
          <div class="chips" style="margin-top:4px">
            <span class="pill" :class="{ ok: prog(r).done && prog(r).done >= prog(r).total }">{{ $t('出图 {a}/{n}', { a: prog(r).done, n: prog(r).total }) }}</span>
            <span class="pill" :class="{ ok: prog(r).picked > 0 }">{{ $t('挑选 {n}', { n: prog(r).picked }) }}</span>
            <span class="pill" :class="{ ok: prog(r).exported }">{{ $t('导出') }} {{ prog(r).exported ? '✓' : '—' }}</span>
          </div>
        </span>
      </router-link>
    </div>
    <div v-else-if="store.setsLoaded[m]" class="card" data-test="set-empty">
      <h3>{{ $t('还没有图片集') }}</h3>
      <p class="hint">{{ $t('两条路：「新建」选模板、写主体、上传参考图后展开清单出图（出图会花钱）；或者「复制示例」拿一套现成的直接改（不花钱）。') }}</p>
    </div>
    <div v-else class="empty">{{ $t('读取列表…') }}</div>
  </div>
</template>
