// 两张模型审稿清单的组名，全前端出这一份：播客脚本页的真人感审稿、短剧项目总览的剧本工艺审稿、剧本工作台「试」页的
// 「审一审」挂的是同一张卡（components/ModelReviewCard.vue），清单本身也是同两份
// （skills/ai-podcast/scripts/humanlike_checklist.json 与 skills/ai-short-film/scripts/craft_checklist.json）。
// 各页各抄一份的话，清单改了组名这边会静默不同步。值保持中文（同时是字典 key），渲染时 t() 翻译。
//
// 真人感清单的第三组在清单与告警句里叫「内容」，卡上写成「内容（实质）」——「内容」这个键在 en.js 里已经是交付包的
// Contents，单独一个键才能跟告警句里的 substance 译成同一个词。
export const HUMANLIKE_GROUPS = { speech: '口语', dynamics: '对谈动态', substance: '内容（实质）' }
export const CRAFT_GROUPS = { tension: '张力', emotion: '情绪可见', rhythm: '节奏', compliance: '合规' }
