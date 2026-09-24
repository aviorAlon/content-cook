import { describe, it, expect, beforeEach, vi } from 'vitest'

vi.mock('../src/api.js', async (orig) => ({ ...(await orig()), del: vi.fn().mockResolvedValue({ archived_to: '.webui_deleted/storyvideo/A-1__x' }) }))
vi.mock('../src/ui.js', async (orig) => ({ ...(await orig()), confirm: vi.fn().mockResolvedValue(true) }))
import { del as delMock } from '../src/api.js'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import QcBadge from '../src/components/QcBadge.vue'
import ModalHost from '../src/components/ModalHost.vue'
import Sidebar from '../src/components/Sidebar.vue'
import { modal, custom } from '../src/ui.js'
import { store } from '../src/store.js'

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<div/>' } }, { path: '/drama', component: { template: '<div/>' } },
    { path: '/drama/new', component: { template: '<div/>' } }, { path: '/drama/libraries', component: { template: '<div/>' } },
    { path: '/voices', component: { template: '<div/>' } },
    { path: '/settings', component: { template: '<div/>' } }, { path: '/drama/p/:id/overview', component: { template: '<div/>' } },
  ] })
}

describe('QcBadge', () => {
  it('印章语义：通过/跳过/重拍都是盖章，待审是空框', () => {
    expect(mount(QcBadge, { props: { decision: 'pass' } }).text()).toContain('已通过')
    expect(mount(QcBadge, { props: { decision: 'retake' } }).text()).toContain('待重拍')
    const none = mount(QcBadge, { props: { decision: null } })
    expect(none.text()).toContain('待审')
    expect(none.find('.qc').classes()).toContain('none')
  })
})

describe('ModalHost', () => {
  beforeEach(() => { modal.current = null })
  it('预估弹窗：列出请求次数、跳过数与逐镜状态', async () => {
    const w = mount(ModalHost)
    const p = custom({ view: 'estimate', through: 'frames', only: null, estimate: {
      pending_count: 2, already_have_count: 1, missing_upstream_frames: [],
      items: [{ shot_id: 'S1', already_have: true, model: 'm' }, { shot_id: 'S2' }, { shot_id: 'S3', in_progress: true }],
    } })
    await w.vm.$nextTick()
    expect(w.text()).toContain('本次会发起')
    expect(w.text()).toContain('已有，跳过')
    expect(w.text()).toContain('生成中，跳过')
    expect(w.text()).toContain('确认生成，开始花钱')
    await w.find('button.btn.spend').trigger('click')
    await expect(p).resolves.toBe(true)
  })
  it('revise 弹窗：预填建议 id，列出要重出的镜', async () => {
    const w = mount(ModalHost)
    custom({ view: 'revise', preview: { requests: { frames: 1, videos: 2 }, affected_frames: ['S3'], affected_videos: ['S3', 'S4'],
      reusable_frames: ['S1', 'S2'], reusable_videos: [], removed_shots: [], reasons: { S3: '首帧请求变了：prompt' }, suggested_id: 'EP-v2' } })
    await w.vm.$nextTick()
    expect(w.text()).toContain('保存并预告费用')
    expect(w.find('input[type=text]').element.value).toBe('EP-v2')
    expect(w.text()).toContain('S3、S4')
    modal.current.resolve(null)
  })
  it('json 弹窗：格式错误不提交', async () => {
    const w = mount(ModalHost)
    const p = custom({ view: 'json', title: 't', value: { id: 'x' } })
    await w.vm.$nextTick()
    const ta = w.find('textarea')
    expect(ta.element.value).toContain('"id": "x"')
    await ta.setValue('{bad')
    await w.find('button.btn.primary').trigger('click')
    expect(w.text()).toContain('JSON 格式不对')
    expect(modal.current).not.toBeNull()
    await ta.setValue('{"id":"y"}')
    await w.find('button.btn.primary').trigger('click')
    await expect(p).resolves.toEqual({ id: 'y' })
  })
})

describe('ModalHost · 重拍处置弹窗（DESIGN §24）', () => {
  beforeEach(() => { modal.current = null })
  it('缺陷/处置两个下拉都在，默认原样重提，确认返回 { defect, disposition, reason }', async () => {
    const w = mount(ModalHost)
    const p = custom({ view: 'retake', gate: 'frames', shotId: 'S2', attempts: { total: 1, same_request: 1 }, last: null })
    await w.vm.$nextTick()
    expect(w.text()).toContain('重拍首帧：S2')
    expect(w.find('[data-test="retake-attempts"]').text()).toContain('已经出过 1 次（同一份提示词 1 次）')
    const defect = w.find('[data-test="retake-defect"]')
    const disposition = w.find('[data-test="retake-disposition"]')
    expect(defect.findAll('option').map(o => o.text())).toContain('身份漂移')
    expect(disposition.findAll('option').map(o => o.text())).toEqual(['保留', '后期处理', '改文字重出', '原样重提', '换模型对比'])
    expect(disposition.element.value).toBe('resubmit')
    expect(w.find('[data-test="retake-warn"]').exists()).toBe(false)
    // 没写原因不能提交
    const confirmBtn = w.find('[data-test="retake-confirm"]')
    expect(confirmBtn.attributes('disabled')).toBeDefined()
    expect(confirmBtn.text()).toBe('记录并重新生成')
    await defect.setValue('identity_drift')
    await w.find('[data-test="retake-reason"]').setValue('  脸漂了 ')
    expect(w.find('[data-test="retake-confirm"]').attributes('disabled')).toBeUndefined()
    await w.find('[data-test="retake-confirm"]').trigger('click')
    await expect(p).resolves.toEqual({ defect: 'identity_drift', disposition: 'resubmit', reason: '脸漂了' })
    expect(modal.current).toBeNull()
  })
  it('同一份提示词已出过 2 次：默认处置改成「改文字重出」，切回原样重提会有提醒；上次缺陷带回来', async () => {
    const w = mount(ModalHost)
    custom({ view: 'retake', gate: 'videos', shotId: 'S3', attempts: { total: 2, same_request: 2 }, last: { decision: 'retake', defect: 'seam', disposition: 'resubmit' } })
    await w.vm.$nextTick()
    expect(w.find('[data-test="retake-defect"]').element.value).toBe('seam')
    expect(w.find('[data-test="retake-disposition"]').element.value).toBe('edit')
    expect(w.text()).toContain('上次退回：接缝断裂（原样重提）')
    expect(w.find('[data-test="retake-confirm"]').text()).toBe('记录，去改文字')
    await w.find('[data-test="retake-disposition"]').setValue('resubmit')
    expect(w.find('[data-test="retake-warn"]').text()).toContain('原样重提第 3 次通常还是一样')
    // 保留不用写原因，确认文案随处置变
    await w.find('[data-test="retake-disposition"]').setValue('keep')
    expect(w.find('[data-test="retake-confirm"]').attributes('disabled')).toBeUndefined()
    expect(w.find('[data-test="retake-confirm"]').text()).toBe('记为通过')
    modal.current.resolve(null)
  })
})

describe('Sidebar', () => {
  it('项目行第二行永远带 id，状态按进度显示', async () => {
    store.episodes = [
      { id: 'A-1', title: '加班夜', status: 'ok', locked: true, shot_count: 5, progress: { frames: 2, videos: 0, total: 5, final: null } },
      { id: 'A-2', title: '加班夜', status: 'ok', locked: false, shot_count: 5, progress: { frames: 0, videos: 0, total: 5, final: null } },
      { id: 'A-3', title: '', status: 'ok', locked: true, shot_count: 5, progress: { frames: 5, videos: 5, total: 5, final: 'post' } },
      { id: 'B-9', title: 'bad', status: 'error', shot_count: 0 },
    ]
    store.episodesLoaded = true
    const r = router(); await r.push('/'); await r.isReady()
    const w = mount(Sidebar, { global: { plugins: [r] } })
    const text = w.text()
    expect(text).toContain('A-1 · 首帧 2/5 · 视频 0/5')
    expect(text).toContain('A-2 · 草稿')
    expect(text).toContain('A-3 · 已有成片')
    expect(text).toContain('B-9 · 文件解析失败')
    // 四个项目还不到出搜索框的条数（SEARCH_MIN=8）
    expect(w.find('[data-test="project-search"]').exists()).toBe(false)
    // 满 8 条才出现，出现后按 id 或标题过滤
    store.episodes = [...store.episodes, ...Array.from({ length: 5 }, (_, i) => ({ id: `C-${i}`, title: 'x', status: 'ok', locked: false, shot_count: 1, progress: null }))]
    await w.vm.$nextTick()
    await w.find('[data-test="project-search"]').setValue('B-9')
    expect(w.findAll('.proj').length).toBe(1)
  })

  it('项目行有删除按钮：确认后调 DELETE 并刷新列表', async () => {
    store.episodes = [{ id: 'A-1', title: '加班夜', status: 'ok', locked: false, shot_count: 5, progress: null }]
    store.episodesLoaded = true
    const r = router(); await r.push('/'); await r.isReady()
    const w = mount(Sidebar, { global: { plugins: [r] } })
    const btn = w.find('.proj .del')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    await new Promise((res) => setTimeout(res, 0))
    expect(delMock).toHaveBeenCalledWith('/api/episodes/storyvideo/A-1')
  })
})
