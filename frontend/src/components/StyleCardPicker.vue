<script setup>
// 出图风格卡挑选器（docs/image-style-cards.md §5）：一个下拉（模板自带的预设 → 按分类分组的卡 → 自写）+ 一片卡片网格
// （名字 + 一句话 + 色板小色块 + 「适合」标签），选中后把锚句全文只读摆出来，「改成自写」切到 custom 并把锚句当起点填进文本框。
// 卡库由 stylecards.js 全局缓存，挂载时保证拉过一次；后端没落地时卡片区为空，模板自带的预设照常可选。
import { computed, ref } from 'vue'
import {
  loadStyleCards, cachedStyleCards, styleCardById, groupCards, cardAnchor, cardName,
  fitLabel, swatchStyle, knownSwatch,
} from '../stylecards.js'

const props = defineProps({
  modelValue: { type: String, default: '' },   // 当前风格：模板预设 id / 卡 id / 'custom'
  presets: { type: Array, default: () => [] }, // 模板自带的预设 [{id, name, anchor}]
  fits: { type: String, default: '' },         // 如 sticker.emoji_pack / picturebook / poster
  anchor: { type: String, default: '' },       // 当前自写锚句（「改成自写」的落点）
  selectTest: { type: String, default: 'style-select' },
  showSelect: { type: Boolean, default: true },
  showGrid: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue', 'update:anchor'])

loadStyleCards()
const onlyFits = ref(true)
const lib = computed(() => cachedStyleCards())
const groups = computed(() => groupCards(props.fits, { only: onlyFits.value && !!props.fits, keep: props.modelValue }, lib.value))
const hidden = computed(() => lib.value.cards.length - groups.value.reduce((n, g) => n + g.cards.length, 0))
const card = computed(() => styleCardById(props.modelValue))
const presetOf = computed(() => props.presets.find(p => p.id === props.modelValue) || null)
const anchorText = computed(() => cardAnchor(card.value))

function pick(id) {
  if (id === props.modelValue) return
  emit('update:modelValue', id)
}
// 「改成自写」：切到 custom，并把这张卡的锚句填进文本框当起点
function toCustom() {
  const seed = anchorText.value || props.anchor
  emit('update:modelValue', 'custom')
  emit('update:anchor', seed)
}
const label = (c) => cardName(c)
</script>

<template>
  <div data-test="style-picker">
    <div class="row" style="gap:10px">
      <select v-if="showSelect" :value="modelValue" :data-test="selectTest" @change="pick($event.target.value)">
        <optgroup v-if="presets.length" :label="$t('模板自带')">
          <option v-for="p in presets" :key="p.id" :value="p.id">{{ p.name }}</option>
        </optgroup>
        <optgroup v-for="g in groups" :key="g.id" :label="g.name">
          <option v-for="c in g.cards" :key="c.id" :value="c.id">{{ label(c) }}</option>
        </optgroup>
        <option value="custom">{{ $t('自写风格锚') }}</option>
      </select>
      <label v-if="fits" class="hint" style="display:flex;align-items:center;gap:5px;cursor:pointer">
        <input type="checkbox" v-model="onlyFits" data-test="style-fits-only" /> {{ $t('只看适合本模板的') }}
      </label>
      <span v-if="fits && onlyFits && hidden > 0" class="hint" data-test="style-hidden">{{ $t('另有 {n} 张别处的卡（取消勾选可见）', { n: hidden }) }}</span>
    </div>

    <div v-if="showGrid" style="margin-top:8px">
      <div v-for="g in groups" :key="g.id" data-test="style-group" :data-group="g.id" style="margin-bottom:8px">
        <div class="hint" style="margin-bottom:4px"><b>{{ g.name }}</b></div>
        <div class="cardgrid">
          <button v-for="c in g.cards" :key="c.id" type="button" class="module-card ready pickcard"
                  :class="{ picked: c.id === modelValue }" data-test="style-card" :data-card="c.id" @click="pick(c.id)">
            <div class="body">
              <b>{{ label(c) }}</b>
              <div class="desc">{{ c.summary }}</div>
              <div class="row" style="gap:3px" data-test="style-palette" :title="c.palette.join('、')">
                <span v-for="(p, i) in c.palette.slice(0, 7)" :key="i" class="sw" :style="swatchStyle(p)"
                      :data-known="knownSwatch(p) ? '1' : '0'" :title="p"></span>
              </div>
              <div v-if="c.fits.length" class="chips">
                <span class="hint" style="font-size:11px">{{ $t('适合') }}</span>
                <span v-for="f in c.fits" :key="f" class="chip" data-test="style-fit">{{ fitLabel(f) }}</span>
              </div>
            </div>
          </button>
        </div>
      </div>
      <p v-if="!groups.length" class="hint" data-test="style-empty">{{ $t('还没有风格卡库（后端 /api/style-cards 没给内容）——用模板自带的预设或自写风格锚。') }}</p>
    </div>

    <div v-if="card" class="card" style="margin-top:8px;padding:10px 12px" data-test="style-detail">
      <div class="row between" style="margin-bottom:4px">
        <b>{{ label(card) }}<span v-if="card.english && card.english !== label(card)" class="hint"> · {{ card.english }}</span></b>
        <button class="btn sm" type="button" data-test="style-to-custom" @click="toCustom">{{ $t('改成自写') }}</button>
      </div>
      <p class="hint" style="margin-bottom:6px">{{ card.summary }}</p>
      <textarea readonly rows="3" style="width:100%" data-test="style-card-anchor" :value="anchorText"></textarea>
      <p class="hint" style="margin-top:6px">{{ $t('这句会进每一张的提示词。想改就点「改成自写」，它会填进风格锚文本框当起点。') }}</p>
      <div class="hint" v-if="card.palette.length" data-test="style-card-palette">{{ $t('色板') }}：{{ card.palette.join('、') }}</div>
      <div class="hint" v-if="card.lighting">{{ $t('布光') }}：{{ card.lighting }}</div>
      <div class="hint" v-if="card.camera">{{ $t('镜头观感') }}：{{ card.camera }}</div>
      <div class="hint" v-if="card.materials">{{ $t('材质') }}：{{ card.materials }}</div>
      <div class="hint" v-if="card.constraints.length">{{ $t('约束') }}：{{ card.constraints.join('；') }}</div>
    </div>
    <p v-else-if="presetOf" class="hint" style="margin-top:6px" data-test="style-preset-note">{{ $t('模板自带的预设「{name}」。', { name: presetOf.name }) }}</p>
    <p v-else-if="modelValue === 'custom'" class="hint" style="margin-top:6px" data-test="style-custom-note">{{ $t('自写风格锚：下面文本框里的那句就是进提示词的风格。') }}</p>
  </div>
</template>

<style scoped>
.cardgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }
.pickcard { text-align: left; font: inherit; color: inherit; width: 100%; cursor: pointer; padding: 10px 12px; }
.pickcard.picked { border-color: var(--accent); background: var(--accent-bg); }
.pickcard .body { gap: 5px; }
.sw { width: 12px; height: 12px; border-radius: 999px; display: inline-block; flex: none; }
</style>
