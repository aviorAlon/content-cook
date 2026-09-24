import { reactive } from 'vue'
import { t } from './i18n.js'

// 页面内弹窗服务：confirm / prompt / notice / 自定义（预估确认）。原生 confirm/prompt
// 在桌面壳里样式脱节且会吞键盘输入，一律不用。ModalHost.vue 渲染队列头部那一个。
export const modal = reactive({ current: null })

function open(spec) {
  return new Promise(resolve => {
    modal.current = { ...spec, resolve: (v) => { modal.current = null; resolve(v) } }
  })
}

export const confirm = (message, opts = {}) =>
  open({ kind: 'confirm', message, confirmLabel: t('确定'), cancelLabel: t('取消'), danger: false, ...opts })

export const prompt = (message, opts = {}) =>
  open({ kind: 'prompt', message, defaultValue: '', placeholder: '', multiline: true, confirmLabel: t('确定'), cancelLabel: t('取消'), ...opts })

export const notice = (message, opts = {}) =>
  open({ kind: 'notice', message, okLabel: t('知道了'), ...opts })

export const custom = (spec) => open({ kind: 'custom', ...spec })

// 右下角 toast（非阻塞提醒）
export const toasts = reactive([])
export function toast(message, { ttl = 4000, kind = 'info' } = {}) {
  const item = { id: Math.random().toString(36).slice(2), message, kind }
  toasts.push(item)
  if (ttl) setTimeout(() => { const i = toasts.indexOf(item); if (i >= 0) toasts.splice(i, 1) }, ttl)
}

// 术语与状态文案集中在这里，界面不再出现工程词（DESIGN §11.1）
// 值保持中文（它们同时是字典 key）；使用处用 t(T.qc[d]) 在渲染时翻译。
export const T = {
  qc: { pass: '已通过', waive: '已跳过', retake: '待重拍', none: '待审' },
  qcMark: { pass: '可', waive: '跳', retake: '退' },
  asr: {
    pass: '识别通过', review: '待复核', fail: '识别不符', silent: '没识别到语音',
    stray: '无台词镜里有语音', no_lines: '无台词', missing_video: '无视频',
  },
  role: { text: '剧本文本', image: '生图', voice: '语音', video: '视频' },
  castKind: { character: '角色', scene: '场景', prop: '道具' },
  // 重拍处置（DESIGN §24）：缺陷是看到了什么，处置是打算怎么办。value 发给后端，label 渲染时翻译
  defect: {
    identity_drift: '身份漂移', seam: '接缝断裂', dialogue: '台词错', text_residue: '文字残留',
    motion: '动作不对', moderation: '审核拦截', quality: '画质', other: '其他',
  },
  disposition: { keep: '保留', post: '后期处理', edit: '改文字重出', resubmit: '原样重提', model: '换模型对比' },
}

export function fmtSeconds(s) {
  const n = Number(s || 0)
  return n >= 60
    ? t('{m} 分 {s} 秒', { m: Math.floor(n / 60), s: Math.round(n % 60) })
    : t('{s} 秒', { s: Math.round(n) })
}
