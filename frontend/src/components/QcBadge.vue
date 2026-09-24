<script setup>
import { T } from '../ui.js'
import { t } from '../i18n.js'
const props = defineProps({ decision: { type: String, default: null } })
</script>

<template>
  <span class="qc" :class="decision || 'none'"><i>{{ T.qcMark[decision] ? t(T.qcMark[decision]) : '' }}</i>{{ t(T.qc[decision] || T.qc.none) }}</span>
</template>
