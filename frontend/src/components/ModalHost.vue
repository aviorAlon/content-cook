<script setup>
import { ref, reactive, computed, watch, nextTick } from 'vue'
import { modal, T } from '../ui.js'
import { t } from '../i18n.js'
import { loadLocalHint, localSuggestion, dismissLocalHint } from '../localhint.js'

// 花钱之前那一行（DESIGN §45「推荐而不强制」）：弹窗打开时**异步**问一句本机有没有在跑的服务，
// 问到了就多一行，问不到 / 没有就跟以前一模一样——这一条从不拦住弹窗，也从不改按钮。
const localTip = computed(() => localSuggestion(modal.current?.localRole || ''))
async function dismissLocal() { await dismissLocalHint(modal.current?.localRole || '') }

const value = ref('')
const inputEl = ref(null)
const jsonError = ref('')
// 重拍处置弹窗（DESIGN §24）：缺陷 + 处置 + 原因；resolve 给 { defect, disposition, reason }
const retakeForm = reactive({ defect: 'other', disposition: 'resubmit', reason: '' })
const RETAKE_CONFIRM = { keep: '记为通过', post: '记为跳过（交后期）', edit: '记录，去改文字', resubmit: '记录并重新生成', model: '去多模型对比' }
const retakeSame = computed(() => Number(modal.current?.attempts?.same_request || 0))
const retakeWarn = computed(() => modal.current?.view === 'retake' && retakeForm.disposition === 'resubmit' && retakeSame.value >= 2)
// 保留（通过）可以不写原因；其他处置都要写清楚哪里不对
const retakeReady = computed(() => retakeForm.disposition === 'keep' || retakeForm.disposition === 'model' || !!retakeForm.reason.trim())
function submitRetake() {
  if (!retakeReady.value) return
  modal.current.resolve({ defect: retakeForm.defect, disposition: retakeForm.disposition, reason: retakeForm.reason.trim() })
}
function submitJson() {
  try { const parsed = JSON.parse(value.value); jsonError.value = ''; modal.current.resolve(parsed) }
  catch (e) { jsonError.value = t('JSON 格式不对：{msg}', { msg: e.message }) }
}
watch(() => modal.current, async (m) => {
  if (!m) return
  if (m.localRole) loadLocalHint(m.localRole)      // 不 await：拿到了就多一行，拿不到弹窗照旧
  jsonError.value = ''
  value.value = m.view === 'json' ? JSON.stringify(m.value ?? {}, null, 2)
    : (m.defaultValue || (m.view === 'revise' ? (m.preview?.suggested_id || '') : ''))
  if (m.view === 'retake') {
    // 上次退回时记的缺陷带回来；同一缺陷第二次出现时默认处置改成「改文字重出」（不要用原样重提回应重复缺陷）
    const last = m.last || null
    retakeForm.defect = last?.defect || 'other'
    retakeForm.disposition = (last?.decision === 'retake' && last?.defect) || Number(m.attempts?.same_request || 0) >= 2 ? 'edit' : 'resubmit'
    retakeForm.reason = ''
  }
  await nextTick()
  inputEl.value?.focus?.()
})
function key(e) {
  const m = modal.current
  if (!m) return
  if (e.key === 'Escape') m.resolve(m.kind === 'prompt' || m.view === 'json' || m.view === 'revise' || m.view === 'retake' ? null : false)
  if (m.view === 'json') return
  if (e.key === 'Enter' && m.kind === 'prompt' && (!m.multiline || e.ctrlKey || e.metaKey)) { e.preventDefault(); m.resolve(value.value) }
}
function estimateRows(m) {
  return (m.estimate.items || []).map(i => ({
    id: i.shot_id,
    state: i.waiting_tail ? t('等 {id} 视频过检后抽尾帧', { id: i.waiting_tail.source }) : i.in_progress ? t('生成中，跳过') : i.already_have ? t('已有，跳过') : t('待生成'),
    pending: !i.in_progress && !i.already_have && !i.waiting_tail,
    model: i.model || '',
    duration: i.duration,
  }))
}
</script>

<template>
  <div v-if="modal.current" class="modal-overlay" @keydown="key">
    <div class="modal-box">
      <template v-if="modal.current.kind === 'confirm'">
        <p>{{ modal.current.message }}</p>
        <p v-if="localTip" class="hint small" data-test="modal-local-hint">
          {{ t('本机有 {name} 在跑，切过去这次不花钱', { name: localTip.name }) }}
          <a href="#/settings" data-test="modal-local-go" @click="modal.current.resolve(false)">{{ t('去切换') }}</a>
          <a href="#" data-test="modal-local-off" style="margin-left:8px" @click.prevent="dismissLocal">{{ t('不再提示') }}</a>
        </p>
        <div class="actions">
          <button class="btn" @click="modal.current.resolve(false)">{{ modal.current.cancelLabel }}</button>
          <button class="btn" :class="modal.current.danger ? 'danger' : 'primary'" @click="modal.current.resolve(true)">{{ modal.current.confirmLabel }}</button>
        </div>
      </template>
      <template v-else-if="modal.current.kind === 'prompt'">
        <p>{{ modal.current.message }}</p>
        <textarea v-if="modal.current.multiline" ref="inputEl" v-model="value" rows="3" :placeholder="modal.current.placeholder"></textarea>
        <input v-else ref="inputEl" type="text" v-model="value" :placeholder="modal.current.placeholder" />
        <div class="actions">
          <button class="btn" @click="modal.current.resolve(null)">{{ modal.current.cancelLabel }}</button>
          <button class="btn primary" @click="modal.current.resolve(value)">{{ modal.current.confirmLabel }}</button>
        </div>
      </template>
      <template v-else-if="modal.current.kind === 'notice'">
        <p>{{ modal.current.message }}</p>
        <div class="actions"><button class="btn primary" ref="inputEl" @click="modal.current.resolve(true)">{{ modal.current.okLabel }}</button></div>
      </template>
      <template v-else-if="modal.current.view === 'json'">
        <h3>{{ modal.current.title }}</h3>
        <textarea ref="inputEl" v-model="value" :rows="modal.current.tall ? 26 : 14" class="mono" style="font-size:12px;width:100%"></textarea>
        <p v-if="jsonError" class="bad small">{{ jsonError }}</p>
        <div class="actions">
          <button class="btn" @click="modal.current.resolve(null)">{{ t('取消') }}</button>
          <button class="btn primary" @click="submitJson">{{ t('保存') }}</button>
        </div>
      </template>
      <template v-else-if="modal.current.view === 'revise'">
        <h3>{{ t('保存并预告费用') }}</h3>
        <p class="hint">{{ t('项目已开始制作。这次改动会保存为一个新版本；没受影响的首帧和视频（连同审片裁决）原样带过去，只有下面这些镜需要重新生成。旧版本原样保留。') }}</p>
        <table class="tbl"><tbody>
          <tr><td>{{ t('需要重出首帧') }}</td><td><b>{{ modal.current.preview.requests.frames }}</b> {{ t('镜') }} <span class="hint mono">{{ modal.current.preview.affected_frames.join('、') }}</span></td></tr>
          <tr><td>{{ t('需要重出视频') }}</td><td><b>{{ modal.current.preview.requests.videos }}</b> {{ t('镜') }} <span class="hint mono">{{ modal.current.preview.affected_videos.join('、') }}</span></td></tr>
          <tr><td>{{ t('直接复用') }}</td><td>{{ t('首帧 {a} · 视频 {b}', { a: modal.current.preview.reusable_frames.length, b: modal.current.preview.reusable_videos.length }) }}</td></tr>
          <tr v-if="modal.current.preview.removed_shots.length"><td>{{ t('删除的镜') }}</td><td class="mono">{{ modal.current.preview.removed_shots.join('、') }}</td></tr>
        </tbody></table>
        <details v-if="Object.keys(modal.current.preview.reasons || {}).length" class="fold"><summary>{{ t('为什么') }}</summary>
          <div v-for="(why, sid) in modal.current.preview.reasons" :key="sid" class="small"><span class="mono">{{ sid }}</span>：{{ why }}</div>
        </details>
        <p v-if="modal.current.preview.requests.frames + modal.current.preview.requests.videos" class="hint" style="margin-top:8px">{{ t('保存后回到项目点「生成首帧」/「生成视频」才会真正花钱；这一步本身不花钱。') }}</p>
        <div class="field" style="margin-top:8px"><label>{{ t('新版本 id') }}</label><input ref="inputEl" type="text" v-model="value" /></div>
        <div class="actions">
          <button class="btn" @click="modal.current.resolve(null)">{{ t('取消') }}</button>
          <button class="btn primary" :disabled="!value.trim()" @click="modal.current.resolve(value.trim())">{{ t('保存为新版本') }}</button>
        </div>
      </template>
      <template v-else-if="modal.current.view === 'retake'">
        <h3>{{ t('重拍{what}：{id}', { what: modal.current.gate === 'frames' ? t('首帧') : t('视频'), id: modal.current.shotId }) }}</h3>
        <p class="hint small" data-test="retake-attempts">
          {{ t('这一镜的{what}已经出过 {n} 次（同一份提示词 {m} 次）。', { what: modal.current.gate === 'frames' ? t('首帧') : t('视频'), n: modal.current.attempts?.total || 0, m: retakeSame }) }}
          <template v-if="modal.current.last?.decision === 'retake'">{{ t('上次退回：{defect}（{disposition}）', { defect: t(T.defect[modal.current.last.defect] || '未标缺陷'), disposition: t(T.disposition[modal.current.last.disposition] || '未标处置') }) }}</template>
        </p>
        <div class="field"><label>{{ t('哪里不对') }}</label>
          <select v-model="retakeForm.defect" data-test="retake-defect"><option v-for="(label, k) in T.defect" :key="k" :value="k">{{ t(label) }}</option></select>
        </div>
        <div class="field"><label>{{ t('怎么办') }}</label>
          <select v-model="retakeForm.disposition" data-test="retake-disposition"><option v-for="(label, k) in T.disposition" :key="k" :value="k">{{ t(label) }}</option></select>
        </div>
        <p class="hint small" style="margin:2px 0 6px">{{ t({
          keep: '镜头目的已达成，瑕疵不影响理解——记为通过，缺陷写进记录，不改文字。',
          post: '缺陷在文字之外（渲染瑕疵、边缘帧、混音）——记为跳过，交给后期，不改提示词。',
          edit: '只有一层错（画面/动作/台词）——记重拍，回编辑器只改那一层，保存后再重出。',
          resubmit: '文字核对过没问题，失败像随机——原样再出一次；同一份提示词最多两次。',
          model: '同一套提示词换几个模型各出一张比一比，不进账本；满意的再采用。',
        }[retakeForm.disposition]) }}</p>
        <p v-if="retakeWarn" class="warn small" data-test="retake-warn">{{ t('同一份提示词已经出过 {m} 次——原样重提第 {k} 次通常还是一样。叠加「高质量、保持一致」这类形容词也不算改文字；改画面/动作，或换模型对比。', { m: retakeSame, k: retakeSame + 1 }) }}</p>
        <div class="field" v-if="retakeForm.disposition !== 'model'"><label>{{ t('原因（写清楚哪里不对，方便以后查）') }}</label>
          <textarea ref="inputEl" v-model="retakeForm.reason" rows="3" :placeholder="t('例如：脸漂了 / 出现文字 / 动作不对')" data-test="retake-reason"></textarea>
        </div>
        <div class="actions">
          <button class="btn" @click="modal.current.resolve(null)">{{ t('取消') }}</button>
          <button class="btn" :class="retakeForm.disposition === 'resubmit' ? 'spend' : 'primary'" :disabled="!retakeReady" data-test="retake-confirm" @click="submitRetake">{{ t(RETAKE_CONFIRM[retakeForm.disposition]) }}</button>
        </div>
      </template>
      <template v-else-if="modal.current.view === 'capability'">
        <h3>{{ t('开始制作前的检查') }}</h3>
        <div v-for="(i, k) in modal.current.check.issues" :key="k" class="small" :class="i.level === 'error' ? 'bad' : i.level === 'warn' ? 'warn' : 'hint'" style="margin:4px 0">
          {{ i.level === 'error' ? '✗' : i.level === 'warn' ? '⚠' : '·' }} {{ i.message }}
        </div>
        <p class="hint" style="margin-top:8px">{{ modal.current.check.errors ? t('标 ✗ 的是实测过的模型组合下必定被拒的参数，建议先改；也可以强制开始。') : t('没有会被拒的参数；标 ⚠ 的按文档判断，实际以服务商为准。') }}</p>
        <div class="actions">
          <button class="btn" @click="modal.current.resolve(false)">{{ t('先去改') }}</button>
          <button class="btn" :class="modal.current.check.errors ? 'danger' : 'primary'" @click="modal.current.resolve(true)">{{ modal.current.check.errors ? t('强制开始制作') : t('开始制作') }}</button>
        </div>
      </template>
      <template v-else-if="modal.current.view === 'estimate'">
        <h3>{{ modal.current.through === 'final' ? t('合成成片') : modal.current.through === 'frames' ? t('生成首帧') : t('生成视频') }}</h3>
        <p v-if="modal.current.only?.length" class="hint">{{ t('只针对：{list}', { list: modal.current.only.join('、') }) }}</p>
        <template v-if="modal.current.through === 'final'">
          <p class="hint">{{ t('本地 ffmpeg 拼接，不调用付费接口。要求所有镜的首帧和视频都已审过。') }}</p>
        </template>
        <template v-else>
          <p>{{ t('本次会发起') }} <b>{{ modal.current.estimate.pending_count }}</b> {{ modal.current.through === 'frames' ? t('次生图请求，已有产物跳过') : t('次视频生成请求，已有产物跳过') }} <b>{{ modal.current.estimate.already_have_count }}</b> {{ t('个。') }}</p>
          <p v-if="modal.current.estimate.missing_upstream_frames?.length" class="bad small">{{ t('⚠ 这些镜还没有首帧，视频会卡住：{list}', { list: modal.current.estimate.missing_upstream_frames.join('、') }) }}</p>
          <p v-if="modal.current.estimate.tail_pending?.length" class="hint small">{{ t('这些镜在等源镜的视频过检后抽尾帧，这一轮不提交，下一轮自动接上：{list}', { list: modal.current.estimate.tail_pending.map(x => `${x.shot_id}←${x.source}`).join('、') }) }}</p>
          <table class="tbl"><tbody>
            <tr v-for="r in estimateRows(modal.current)" :key="r.id">
              <td class="mono">{{ r.id }}</td><td :class="{ warn: r.pending }">{{ r.state }}</td><td class="hint">{{ r.model }}{{ r.duration ? ` · ${r.duration}s` : '' }}</td>
            </tr>
          </tbody></table>
          <p class="hint">{{ t('确认后会真实调用付费接口。') }}</p>
        </template>
        <p v-if="localTip" class="hint small" data-test="modal-local-hint">
          {{ t('本机有 {name} 在跑，切过去这次不花钱', { name: localTip.name }) }}
          <a href="#/settings" data-test="modal-local-go" @click="modal.current.resolve(false)">{{ t('去切换') }}</a>
          <a href="#" data-test="modal-local-off" style="margin-left:8px" @click.prevent="dismissLocal">{{ t('不再提示') }}</a>
        </p>
        <div class="actions">
          <button class="btn" @click="modal.current.resolve(false)">{{ t('取消') }}</button>
          <button class="btn" :class="modal.current.through === 'final' ? 'primary' : 'spend'" @click="modal.current.resolve(true)">
            {{ modal.current.through === 'final' ? t('开始合成') : t('确认生成，开始花钱') }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>
