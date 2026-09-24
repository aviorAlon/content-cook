<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { store, loadEpisodes, withBusy } from '../../store.js'
import { post, del, get, ep } from '../../api.js'
import { confirm, notice, prompt, toast } from '../../ui.js'
import { t } from '../../i18n.js'

const router = useRouter()
const d = computed(() => store.detail)
const graph = ref(null)
async function loadGraph() { graph.value = await get(`${ep(store.projectId)}/graph`) }
async function revise() {
  const id = await prompt(t('新版本的项目 id（还不存在）'), { multiline: false, defaultValue: store.projectId + '-v2', confirmLabel: t('复制') })
  if (!id?.trim()) return
  await withBusy(async () => {
    try { const r = await post(`${ep(store.projectId)}/revise`, { new_episode_id: id.trim() }); await loadEpisodes(); router.push(`/drama/p/${r.id}/overview`) }
    catch (e) { await notice(e.message) }
  })
}
async function archive() {
  if (!await confirm(t('删除「{id}」？\n\n项目文件、剧本、角色锚图、已生成的首帧/视频和账本记录会一起收进回收站，删除后可在「回收站」里恢复。', { id: store.projectId }), { confirmLabel: t('删除'), danger: true })) return
  await withBusy(async () => {
    try {
      const r = await del(ep(store.projectId)); await loadEpisodes()
      toast(t('已删除（可在「回收站」里恢复）')); router.push('/drama')
      // 归档了、但有东西没搬（输出目录在数据目录外 / 账本记录没能一起归档）：说清楚，别只当成功
      if (r?.warning) await notice(r.warning)
    } catch (e) { await notice(e.message) }
  })
}
</script>

<template>
  <div class="ws-content">
    <div class="ws-center" style="max-width:820px">
      <h3 style="font-size:16px;margin-bottom:8px">{{ $t('高级') }}</h3>
      <div class="card">
        <h3>{{ $t('版本') }}</h3>
        <p class="hint">{{ $t('制作中的项目要改角色、镜头画面、台词这类会进提示词的内容，就复制为新版本，在新版本里改再重新生成；旧版本和它的产物原样保留。') }}</p>
        <div class="row" style="margin-top:8px"><button class="btn" @click="revise">{{ $t('复制为新版本') }}</button></div>
      </div>
      <div class="card">
        <h3>{{ $t('引用关系图') }}</h3>
        <p class="hint">{{ $t('谁引用了谁：镜 → 角色/场景、镜 → 参考镜、旁白/画面文字 → 镜、插卡 → 镜。') }}</p>
        <button class="btn sm" style="margin-top:6px" @click="loadGraph">{{ $t('读取') }}</button>
        <table v-if="graph" class="tbl" style="margin-top:8px">
          <tr v-for="(e, i) in graph.edges" :key="i"><td class="mono">{{ e.from }}</td><td class="hint">{{ e.type }}</td><td class="mono">{{ e.to }}</td></tr>
        </table>
      </div>
      <div class="card">
        <h3>{{ $t('镜头库与风格库') }}</h3>
        <p class="hint">{{ $t('所有项目共用的镜头语言库和风格骨架库：') }}<router-link to="/libraries">{{ $t('逐条编辑') }}</router-link>{{ $t('（旧版页面仍在 ') }}<a href="/legacy" target="_blank">/legacy</a>{{ $t('）。') }}</p>
      </div>
      <div class="card">
        <h3>{{ $t('命令行') }}</h3>
        <p class="hint mono small">storyvideo prepare {{ d.id }} · run {{ d.id }} --through frames · asr-qc {{ d.id }} --record-retakes · post {{ d.id }}</p>
      </div>
      <div class="card">
        <h3>{{ $t('删除项目（归档）') }}</h3>
        <p class="hint">{{ $t('从项目列表移走，文件保留。') }}</p>
        <div class="row" style="margin-top:8px"><button class="btn danger" @click="archive">{{ $t('删除这个项目') }}</button></div>
      </div>
    </div>
  </div>
</template>
