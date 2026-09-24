<script setup>
// 角色还没配好的地方那**一行**（DESIGN §45「推荐而不强制」）：只说本地是一条可选的路，
// 不写安装教程、不给下载链接、不拦任何操作。点过「不再提示」就再也不出现（服务端记着）。
import { onMounted } from 'vue'
import { loadLocalHint, localTipVisible, dismissLocalHint } from '../localhint.js'

const props = defineProps({ role: { type: String, required: true } })
onMounted(() => loadLocalHint(props.role))
</script>

<template>
  <p v-if="localTipVisible(role)" class="hint" data-test="local-tip" style="margin-top:6px">
    {{ $t('有显卡的话可以本地跑，不花钱、不出网') }}
    <router-link to="/settings" data-test="local-tip-go">{{ $t('→ 扫描本机') }}</router-link>
    <a href="#" data-test="local-tip-off" style="margin-left:8px" @click.prevent="dismissLocalHint(role)">{{ $t('不再提示') }}</a>
  </p>
</template>
