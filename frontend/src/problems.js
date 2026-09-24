// 问题卡的分类名：src/webui/problems.py 的 KIND_LABELS 在前端的镜像，全前端出这一份
// （tests/test_problem_kind_labels.py 守着两边同步，后端加了新分类而这里没跟上就失败）。
// kind 是后端按关键词猜出来的内部键（screenplay / pairing…），界面上一个英文键名都不许露出去：
// 各页原先各抄几个键，后端分类器落到没抄的那个键时，pill 上就直接显示 screenplay 这种字面量。
// 值保持中文（同时是字典 key），渲染时 t() 翻译。
export const PROBLEM_KINDS = {
  craft: '工艺', genre: '题材', pairing: '搭配', form: '形态', book: '绘本', image: '出图',
  clip: '单条视频', podcast: '播客', withhold: '揭示', contract: '契约', screenplay: '剧本',
  dialogue: '台词', continuity: '连续性', performance: '表演', camera: '镜头', timing: '时长',
  text: '正文', overlay: '遮挡区', reference: '参考图', other: '其他',
}
// 表里没有的一律落到「其他」：后端将来再加新分类，界面上也只会少一个准确的名字，不会漏英文术语出去
export const problemKind = (kind) => PROBLEM_KINDS[kind] || '其他'
