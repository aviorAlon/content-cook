<script setup>
// 绘本模块首页（docs/picturebook-spec.md §7）：书的列表（封面 / 标题 / 年龄段 / 页数 / 进度）+ 新建 + 复制示例
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { store, withBusy } from '../../store.js'
import { notice } from '../../ui.js'
import { t } from '../../i18n.js'
import { loadBooks, createFromExample, progressOf } from '../../picturebook.js'

const router = useRouter()
const status = ref('')
onMounted(() => { if (!store.booksLoaded) loadBooks() })
const books = computed(() => store.books || [])
const title = (b) => b.title || b.title_zh || b.id
const pageCount = (b) => b.page_count ?? (Array.isArray(b.pages) ? b.pages.length : b.pages) ?? progressOf(b).pages.total ?? 0
const prog = (b) => progressOf(b, pageCount(b))
// 「复制示例」：POST /api/picturebook/examples/create（随程序附带的 examples/picturebook/*.json，不花钱）
async function copyExample() {
  status.value = t('复制中…')
  await withBusy(async () => {
    try {
      const r = await createFromExample()
      status.value = ''
      router.push(`/picturebook/b/${r.id}/overview`)
    } catch (e) { status.value = t('失败：{msg}', { msg: e.message }); await notice(e.message) }
  })
}
</script>

<template>
  <div class="page narrow" data-test="pb-home">
    <div class="row between" style="margin-bottom:12px">
      <div>
        <h2 style="font-size:20px">📖 {{ $t('绘本 / 有声书') }}</h2>
        <p class="hint">{{ $t('插画 + 朗读 + 双语字幕的有声绘本：每页停多久由朗读长度决定。') }}</p>
      </div>
      <div class="row">
        <button class="btn primary" data-test="pb-new" @click="router.push('/picturebook/new')">{{ $t('+ 新建绘本') }}</button>
        <button class="btn" data-test="pb-example" @click="copyExample">{{ $t('复制示例（不花钱）') }}</button>
      </div>
    </div>
    <div v-if="status" class="status" style="margin-bottom:8px">{{ status }}</div>
    <div v-if="books.length" class="stack" data-test="pb-list">
      <router-link v-for="b in books" :key="b.id" class="card" :to="`/picturebook/b/${b.id}/overview`" style="display:flex;gap:14px;align-items:center;text-decoration:none;color:inherit;padding:12px 14px" data-test="pb-row">
        <span style="width:54px;height:72px;flex:none;border-radius:var(--r-sm);background:var(--panel3);overflow:hidden;display:flex;align-items:center;justify-content:center">
          <img v-if="b.cover_url" :src="b.cover_url" alt="" style="width:100%;height:100%;object-fit:cover;display:block" />
          <span v-else class="hint">📖</span>
        </span>
        <span style="flex:1;min-width:0">
          <b>{{ title(b) }}</b> <span class="pill">{{ b.age_band || '—' }} {{ $t('岁') }}</span>
          <div class="hint mono">{{ b.id }} · {{ $t('{n} 页', { n: pageCount(b) }) }}</div>
          <div class="chips" style="margin-top:4px">
            <span class="pill" :class="{ ok: prog(b).pages.done && prog(b).pages.done >= prog(b).pages.total }">{{ $t('插画 {a}/{n}', { a: prog(b).pages.done, n: prog(b).pages.total || pageCount(b) }) }}</span>
            <span class="pill" :class="{ ok: prog(b).narration.done && prog(b).narration.done >= prog(b).narration.total }">{{ $t('朗读 {a}/{n}', { a: prog(b).narration.done, n: prog(b).narration.total || pageCount(b) }) }}</span>
            <span class="pill" :class="{ ok: prog(b).final }">{{ $t('成片') }} {{ prog(b).final ? '✓' : '—' }}</span>
          </div>
        </span>
      </router-link>
    </div>
    <div v-else-if="store.booksLoaded" class="card" data-test="pb-empty">
      <h3>{{ $t('还没有绘本') }}</h3>
      <p class="hint">{{ $t('两条路：「新建绘本」用一句话方向让文本模型起草分页表（会花钱），或者「复制示例」拿一本 10 页的《小狐狸的三只袜子》直接改（不花钱）。') }}</p>
    </div>
    <div v-else class="empty">{{ $t('读取绘本列表…') }}</div>
  </div>
</template>
