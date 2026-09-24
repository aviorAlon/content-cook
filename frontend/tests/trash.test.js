// 回收站页（/trash）：各模块删除的项目按模块分组列出（标题 / 删除时间 / 大小），一键恢复后跳到那个项目，
// 彻底删除要二次确认；?module= 只看一个模块（侧栏短剧分组的入口带 storyvideo）。以前只有短剧能在侧栏恢复，
// 其它模块的确认框只能教人「手动搬回来」——这里也把所有删除确认框的文案钉住：指向回收站、不再提手动搬。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const calls = []
let archived = []
let restoreError = null
vi.mock('../src/api.js', async (orig) => ({
  ...(await orig()),
  get: vi.fn(async (url) => {
    calls.push(['get', url])
    if (url.startsWith('/api/deleted')) return archived
    return []
  }),
  post: vi.fn(async (url) => {
    calls.push(['post', url])
    if (restoreError) throw restoreError
    const entry = decodeURIComponent(url.split('/')[4])
    const hit = archived.find(x => x.entry === entry)
    archived = archived.filter(x => x.entry !== entry)
    return { module: hit.module, id: hit.id, restored: { spec: true, output: true, assets: true },
      path: `content/${hit.module}/${hit.id}.json`, spec_broken: !!hit.spec_broken }
  }),
  del: vi.fn(async (url) => {
    calls.push(['del', url])
    const entry = decodeURIComponent(url.split('/')[4])
    archived = archived.filter(x => x.entry !== entry)
    return { entry, freed_bytes: 1 }
  }),
}))
const confirmMock = vi.fn(async () => true)
const noticeMock = vi.fn(async () => {})
const toastMock = vi.fn()
vi.mock('../src/ui.js', async (orig) => ({
  ...(await orig()), confirm: (...a) => confirmMock(...a), notice: (...a) => noticeMock(...a), toast: (...a) => toastMock(...a),
}))

import Trash from '../src/views/Trash.vue'
import { store } from '../src/store.js'
import { t, setLang } from '../src/i18n.js'
import { en } from '../src/locales/en.js'
import { fmtBytes, fmtDeletedAt, groupTrash, projectRoute, trashModuleKey } from '../src/trash.js'

const stub = { template: '<div/>' }
function stubRouter() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: stub }, { path: '/trash', component: stub },
    { path: '/drama/p/:id/overview', component: stub }, { path: '/clip/c/:id/write', component: stub },
    { path: '/music/m/:id/generate', component: stub }, { path: '/picturebook/b/:id/overview', component: stub },
  ] })
}

async function mountAt(path = '/trash') {
  const r = stubRouter(); await r.push(path); await r.isReady()
  const w = mount(Trash, { global: { plugins: [r] } })
  await flushPromises()
  return { w, r }
}

const MB = 1024 * 1024
function rows() {
  return [
    { module: 'clip', entry: 'CL-1__20260922T170500Z', id: 'CL-1', title: '城市清晨短片', archived_at: '20260922T170500Z',
      deleted_at: '2026-09-22T17:05:00Z', bytes: 12 * MB, restorable: true, id_taken: true, ledger_only: false },
    { module: 'storyvideo', entry: 'A-1__20260922T170403Z', id: 'A-1', title: '旧的一集', archived_at: '20260922T170403Z',
      deleted_at: '2026-09-22T17:04:03Z', bytes: 3 * MB, restorable: true, id_taken: false, ledger_only: false },
    { module: 'oldmodule', entry: 'OLD-1__20260901T080000Z', id: 'OLD-1', title: '旧模块', archived_at: '20260901T080000Z',
      deleted_at: '2026-09-01T08:00:00Z', bytes: 100, restorable: false, id_taken: false, ledger_only: false },
    { module: 'music', entry: 'MU-1__20260922T160000Z', id: 'MU-1', title: '', archived_at: '20260922T160000Z',
      deleted_at: '', bytes: 2048, restorable: true, id_taken: false, ledger_only: false },
    { module: 'storyvideo', entry: 'A-3__20260922T150000Z', id: 'A-3', title: '只剩账本', archived_at: '20260922T150000Z',
      deleted_at: '2026-09-22T15:00:00Z', bytes: 500, restorable: true, id_taken: false, ledger_only: true },
  ]
}

beforeEach(() => {
  calls.length = 0
  archived = rows()
  restoreError = null
  confirmMock.mockReset(); confirmMock.mockResolvedValue(true)
  noticeMock.mockClear(); toastMock.mockClear()
  store.modules = [{ id: 'drama', name: '短剧', english: 'Short drama', icon: '🎬', available: true }]
})
afterEach(() => setLang('zh'))

describe('回收站页', () => {
  it('按模块分组（短剧在前、不认识的模块最后）：标题 / 删除时间 / 大小；恢复不了、同 id 已存在、只剩账本的写在行上', async () => {
    const { w } = await mountAt()
    expect(calls).toContainEqual(['get', '/api/deleted'])
    const groups = w.findAll('[data-test="trash-group"]')
    expect(groups.map(g => g.attributes('data-module'))).toEqual(['storyvideo', 'clip', 'music', 'oldmodule'])
    expect(groups[0].text()).toContain('短剧')
    expect(groups[0].text()).toContain('2 项')
    expect(w.find('[data-test="trash-total"]').text()).toContain('5 项')

    const row = (entry) => w.find(`[data-test="trash-row"][data-entry="${entry}"]`)
    const a1 = row('A-1__20260922T170403Z')
    expect(a1.text()).toContain('旧的一集')
    expect(a1.find('[data-test="trash-sub"]').text()).toMatch(/^A-1 · 删除于 2026-09-2\d \d\d:\d\d · 3\.0 MB$/)
    expect(a1.find('[data-test="trash-note"]').exists()).toBe(false)
    expect(row('MU-1__20260922T160000Z').text()).toContain('MU-1')     // 没有标题就显示 id
    expect(row('MU-1__20260922T160000Z').find('[data-test="trash-sub"]').text()).toContain('2 KB')
    expect(row('CL-1__20260922T170500Z').find('[data-test="trash-note"]').text()).toContain('同 id 的项目现在就在')
    expect(row('A-3__20260922T150000Z').find('[data-test="trash-note"]').text()).toContain('只剩账本记录')
    const odd = row('OLD-1__20260901T080000Z')
    expect(odd.find('[data-test="trash-note"]').text()).toContain('不能从界面恢复')
    expect(odd.find('[data-test="trash-restore"]').attributes('disabled')).toBeDefined()
    expect(odd.find('[data-test="trash-purge"]').attributes('disabled')).toBeUndefined()   // 恢复不了的也能彻底删除
  })

  it('点「恢复」→ POST restore（归档名编码进路径），刷新那个模块的侧栏列表和回收站，跳到恢复出来的项目', async () => {
    const { w, r } = await mountAt()
    calls.length = 0
    await w.find('[data-test="trash-row"][data-entry="CL-1__20260922T170500Z"] [data-test="trash-restore"]').trigger('click')
    await flushPromises()
    expect(calls[0]).toEqual(['post', '/api/deleted/clip/CL-1__20260922T170500Z/restore'])
    expect(calls).toContainEqual(['get', '/api/clip'])        // 单条视频的侧栏列表
    expect(calls).toContainEqual(['get', '/api/deleted'])
    expect(toastMock).toHaveBeenCalledWith('已恢复「城市清晨短片」')
    expect(r.currentRoute.value.path).toBe('/clip/c/CL-1/write')
  })

  it('恢复被拒（409 同 id 已存在）：原因弹出来，留在回收站页、列表重新拉一遍', async () => {
    restoreError = Object.assign(new Error('没有恢复：项目 CL-1 已经存在——先删除现在这个同 id 的项目（它也会进回收站），再恢复这一份'), { status: 409 })
    const { w, r } = await mountAt()
    calls.length = 0
    await w.find('[data-test="trash-row"][data-entry="CL-1__20260922T170500Z"] [data-test="trash-restore"]').trigger('click')
    await flushPromises()
    expect(noticeMock.mock.calls[0][0]).toContain('已经存在')
    expect(calls).toContainEqual(['get', '/api/deleted'])
    expect(r.currentRoute.value.path).toBe('/trash')
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('项目文件本身是坏的（删除前列表里就是出错）：行上先说一声；恢复照做，但不跳进打不开的项目页，告诉人去修哪个文件', async () => {
    archived = [...rows(), { module: 'script', entry: 'SC-2__20260923T040859Z', id: 'SC-2', title: '', archived_at: '20260923T040859Z',
      deleted_at: '2026-09-23T04:08:59Z', bytes: 40, restorable: true, id_taken: false, ledger_only: false, spec_broken: true }]
    const { w, r } = await mountAt()
    const row = w.find('[data-test="trash-row"][data-entry="SC-2__20260923T040859Z"]')
    expect(row.find('[data-test="trash-note"]').text()).toContain('项目文件本身读不出来')
    expect(row.find('[data-test="trash-restore"]').attributes('disabled')).toBeUndefined()
    // 好的那几条不带这句
    expect(w.find('[data-test="trash-row"][data-entry="A-1__20260922T170403Z"] [data-test="trash-note"]').exists()).toBe(false)

    calls.length = 0
    await row.find('[data-test="trash-restore"]').trigger('click')
    await flushPromises()
    expect(calls[0]).toEqual(['post', '/api/deleted/script/SC-2__20260923T040859Z/restore'])
    expect(toastMock).toHaveBeenCalledWith('已恢复「SC-2」')
    expect(noticeMock.mock.calls[0][0]).toContain('content/script/SC-2.json')
    expect(noticeMock.mock.calls[0][0]).toContain('读不出来')
    expect(r.currentRoute.value.path).toBe('/trash')
  })

  it('彻底删除要二次确认：取消什么都不发；确认后 DELETE 这一条，列表里没了', async () => {
    const { w } = await mountAt()
    const purge = () => w.find('[data-test="trash-row"][data-entry="A-1__20260922T170403Z"] [data-test="trash-purge"]').trigger('click')
    confirmMock.mockResolvedValueOnce(false)
    calls.length = 0
    await purge(); await flushPromises()
    const [text, opts] = confirmMock.mock.calls[0]
    expect(text).toContain('彻底删除「旧的一集」')
    expect(text).toContain('3.0 MB')
    expect(text).toContain('不可恢复')
    expect(opts).toMatchObject({ danger: true, confirmLabel: '彻底删除' })
    expect(calls.some(c => c[0] === 'del')).toBe(false)

    await purge(); await flushPromises()
    expect(calls).toContainEqual(['del', '/api/deleted/storyvideo/A-1__20260922T170403Z'])
    expect(w.find('[data-test="trash-row"][data-entry="A-1__20260922T170403Z"]').exists()).toBe(false)
    expect(toastMock).toHaveBeenCalledWith('已彻底删除「旧的一集」')
  })

  it('?module=storyvideo 只看短剧（drama 也认）；点「全部」看全部', async () => {
    const { w, r } = await mountAt('/trash?module=storyvideo')
    expect(w.findAll('[data-test="trash-group"]').map(g => g.attributes('data-module'))).toEqual(['storyvideo'])
    expect(w.find('[data-test="trash-filter-storyvideo"]').classes()).toContain('primary')
    await w.find('[data-test="trash-filter-all"]').trigger('click'); await flushPromises()
    expect(w.findAll('[data-test="trash-group"]').length).toBe(4)
    expect(r.currentRoute.value.query.module).toBeUndefined()

    const other = await mountAt('/trash?module=drama')
    expect(other.w.findAll('[data-test="trash-group"]').map(g => g.attributes('data-module'))).toEqual(['storyvideo'])
  })

  it('停在回收站页上从侧栏删了项目（那个模块的列表变了）：回收站自己重拉，刚删的那条马上出现', async () => {
    store.musics = [{ id: 'MU-9', title: '新配乐' }]
    const { w } = await mountAt()
    calls.length = 0
    archived = [...rows(), { module: 'music', entry: 'MU-9__20260923T010000Z', id: 'MU-9', title: '新配乐', archived_at: '20260923T010000Z',
      deleted_at: '2026-09-23T01:00:00Z', bytes: 10, restorable: true, id_taken: false, ledger_only: false }]
    store.musics = []          // 侧栏删除后 loadMusics() 把列表刷成空
    await flushPromises()
    expect(calls).toContainEqual(['get', '/api/deleted'])
    expect(w.find('[data-test="trash-row"][data-entry="MU-9__20260923T010000Z"]').exists()).toBe(true)
  })

  it('空的回收站有说明', async () => {
    archived = []
    const { w } = await mountAt()
    expect(w.find('[data-test="trash-empty"]').text()).toContain('回收站是空的')
    expect(w.find('[data-test="trash-group"]').exists()).toBe(false)
  })

  it('英文界面：页面文案、模块名（注册表的英文名）都翻了', async () => {
    setLang('en')
    const { w } = await mountAt()
    expect(w.text()).toContain('Trash')
    expect(w.text()).toContain('Short drama')
    expect(w.text()).toContain('Delete forever')
    expect(w.text()).not.toMatch(/回收站|彻底删除|删除于/)
  })
})

describe('回收站 helpers', () => {
  it('大小、删除时间（老归档没有 deleted_at 时从目录名推）、分组顺序、模块别名、恢复后的项目地址', () => {
    expect(fmtBytes(500)).toBe('500 B')
    expect(fmtBytes(2048)).toBe('2 KB')
    expect(fmtBytes(3 * MB)).toBe('3.0 MB')
    expect(fmtBytes(3 * 1024 * MB)).toBe('3.00 GB')
    const local = fmtDeletedAt({ deleted_at: '', archived_at: '20260922T170403Z' })
    const d = new Date(Date.UTC(2026, 8, 22, 17, 4, 3))
    expect(local).toBe(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:04`)
    expect(groupTrash(rows()).map(g => [g.module, g.items.length])).toEqual([['storyvideo', 2], ['clip', 1], ['music', 1], ['oldmodule', 1]])
    expect(trashModuleKey('drama')).toBe('storyvideo')
    expect(trashModuleKey('clip')).toBe('clip')
    expect(trashModuleKey('')).toBe('')
    expect(projectRoute('storyvideo', 'A-1')).toBe('/drama/p/A-1/overview')
    expect(projectRoute('picturebook', 'PB-1')).toBe('/picturebook/b/PB-1/overview')
    expect(projectRoute('portrait', 'PT-1')).toBe('/portrait/s/PT-1/items')
    expect(projectRoute('music', 'MU-1')).toBe('/music/m/MU-1/generate')
    expect(projectRoute('podcast', 'PC-1')).toBe('/podcast/p/PC-1/script')
    expect(projectRoute('script', 'SC-1')).toBe('/script/p/SC-1/write')
    expect(projectRoute('oldmodule', 'OLD-1')).toBeNull()
  })
})

// ---- 删除确认框文案：所有模块都指向回收站，不再教人「手动搬回来」
const sources = import.meta.glob(['../src/components/Sidebar.vue', '../src/views/**/*Advanced.vue', '../src/views/Trash.vue'],
  { query: '?raw', import: 'default', eager: true })

describe('删除文案', () => {
  it('侧栏和每个模块「高级」页的删除确认框都说「删除后可在『回收站』里恢复」，没有「手动搬回来」', () => {
    const files = Object.keys(sources)
    expect(files.length).toBeGreaterThanOrEqual(8)   // Sidebar + 6 个高级页 + Trash
    for (const [file, text] of Object.entries(sources)) {
      expect(text, file).not.toContain('想找回手动搬回来即可')
      expect(text, file).not.toContain('归档在 .webui_deleted/')
      const confirms = [...text.matchAll(/confirm\(t\('(删除「[^']*)'/g)].map(m => m[1])
      for (const c of confirms) expect(c, file).toContain('删除后可在「回收站」里恢复')
    }
    const sidebarConfirms = [...sources['../src/components/Sidebar.vue'].matchAll(/confirm\(t\('(删除「[^']*)'/g)]
    expect(sidebarConfirms.length).toBe(7)   // 配乐 / 播客 / 剧本 / 单条视频 / 图片集 / 短剧 / 绘本
  })

  it('这几页 t(\'…\') 里的中文都有英文', () => {
    const missing = []
    for (const [file, text] of Object.entries(sources)) {
      for (const m of text.matchAll(/\bt\('((?:[^'\\]|\\.)*)'/g)) {
        const key = m[1].replace(/\\n/g, '\n')
        if (/[一-鿿]/.test(key) && !Object.prototype.hasOwnProperty.call(en, key)) missing.push(`${file}: ${key}`)
      }
    }
    expect(missing).toEqual([])
    setLang('en')
    expect(t('删除「{name}」？\n\n项目文件和生成过的产物会一起收进回收站，删除后可在「回收站」里恢复。', { name: 'X' })).toContain('Trash')
  })
})
