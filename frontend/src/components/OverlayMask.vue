<script setup>
// 交付面遮挡区（DESIGN §27）：叠在首帧预览上的半透明斜纹矩形，比例坐标（0–1）直接换算成百分比。
// 父元素要是 position:relative，图片占满父元素。
defineProps({ regions: { type: Array, default: () => [] }, show: { type: Boolean, default: true } })
function style(r) {
  return { left: (r.x * 100) + '%', top: (r.y * 100) + '%', width: (r.w * 100) + '%', height: (r.h * 100) + '%' }
}
</script>

<template>
  <template v-if="show">
    <div v-for="(r, i) in regions" :key="i" class="overlay-region" data-test="overlay-region" :title="r.label" :style="style(r)">
      <span class="overlay-label">{{ r.label }}</span>
    </div>
  </template>
</template>

<style>
.overlay-region { position: absolute; box-sizing: border-box; pointer-events: none; border: 1px dashed rgba(255, 96, 96, .9);
  background: repeating-linear-gradient(45deg, rgba(255, 96, 96, .32) 0 5px, rgba(255, 96, 96, .06) 5px 11px); overflow: hidden }
.overlay-label { position: absolute; left: 3px; top: 1px; font-size: 10px; line-height: 1.2; color: #fff; text-shadow: 0 0 3px #000, 0 0 6px #000; white-space: nowrap }
</style>
