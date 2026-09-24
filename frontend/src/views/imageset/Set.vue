<script setup>
// 图片集外壳（docs/imageset-spec.md §5）：左栏导航（清单 / 产物 / 高级）+ 缩略条，顶部进度（出图 n/N · 挑选 n · 导出）；正文是子路由。
// 模块来自路由 meta.module，两个模块共用。
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { store } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import { moduleInfo, loadSet, progressOf, hasCurrent, itemImageUrl, setRoute, templateName, loadTemplates } from '../../imageset.js'

const route = useRoute()
const m = computed(() => route.meta.module || 'sticker')
const info = computed(() => moduleInfo(m.value))
const tplTick = ref(0)
watch(() => [route.params.id, m.value], ([id, mod]) => {
  if (!id || !route.path.startsWith(`/${mod}/s/`)) return
  loadTemplates(mod).then(() => { tplTick.value++ }).catch(() => {})
  loadSet(mod, id).catch(e => notice(t('打开失败：{msg}', { msg: e.message })))
}, { immediate: true })

const s = computed(() => store.set)
const d = computed(() => store.setDetail)
const items = computed(() => s.value?.items || [])
const prog = computed(() => {
  const p = progressOf(d.value?.progress ? d.value : { items: items.value, picks: s.value?.picks, exports: d.value?.exports || s.value?.exports })
  if (!p.total) p.total = items.value.length
  return p
})
const stamp = computed(() => d.value?.updated_at || s.value?.updated_at || 0)
// 模板名：英文界面要模板列表里的 english（详情接口的 template_name 是中文），列表拉回来之前先用 template_name
const tpl = computed(() => { void tplTick.value; return templateName(m.value, s.value?.template, d.value?.template_name) })
</script>

<template>
  <div v-if="!s" class="page"><div class="empty">{{ $t('打开中…') }}</div></div>
  <div v-else class="ws" data-test="set-shell" :data-module="m">
    <nav class="ws-nav">
      <div class="head">
        <h2>{{ s.title || s.id }}</h2>
        <div class="id">{{ s.id }} · {{ tpl }}</div>
      </div>
      <router-link class="nav" :to="setRoute(m, s.id, 'items')">{{ $t('清单') }} <span class="n">{{ items.length }}</span></router-link>
      <router-link class="nav" :to="setRoute(m, s.id, 'outputs')">{{ $t('产物') }} <span class="n">{{ prog.exported ? '✓' : '' }}</span></router-link>
      <router-link class="nav" :to="setRoute(m, s.id, 'advanced')">{{ $t('高级') }}</router-link>
      <div class="strip">
        <router-link v-for="it in items" :key="it.id" :to="setRoute(m, s.id, 'items')" :title="it.label || it.id" style="aspect-ratio:1">
          <img v-if="hasCurrent(it)" :src="itemImageUrl(m, s.id, it, stamp)" alt="" />
          <span class="lab">{{ it.id }}</span>
        </router-link>
      </div>
    </nav>
    <div class="ws-main">
      <div class="ws-top">
        <div class="prog" data-test="set-progress">
          <span>{{ $t('出图') }} <b>{{ prog.done }}/{{ prog.total }}</b></span>
          <span>{{ $t('挑选') }} <b>{{ prog.picked || $t('全部') }}</b></span>
          <span>{{ $t('导出') }} <b>{{ prog.exported ? '✓' : '—' }}</b></span>
        </div>
        <div class="next">
          <span v-if="store.gen" class="status">{{ store.gen.text }}</span>
          <span v-else class="hint">{{ info.icon }} {{ $t(info.name) }}</span>
        </div>
      </div>
      <router-view />
    </div>
  </div>
</template>
