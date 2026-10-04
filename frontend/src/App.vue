<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <n-message-provider>
      <n-layout has-sider style="height: 100vh">
        <n-layout-sider bordered :width="228" :native-scrollbar="false">
          <div class="brand">
            <div class="brand-title">RAG 评测平台</div>
            <div class="brand-sub">量化 · 归因 · 验证</div>
          </div>
          <n-menu :value="activeKey" :options="menuOptions" @update:value="onSelect" />
        </n-layout-sider>

        <n-layout :native-scrollbar="false">
          <n-layout-header bordered class="page-header">
            <span class="page-title">{{ currentTitle }}</span>
          </n-layout-header>
          <n-layout-content content-style="padding: 20px 24px 48px;">
            <router-view />
          </n-layout-content>
        </n-layout>
      </n-layout>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { computed, h } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import {
  NConfigProvider,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NLayoutSider,
  NMenu,
  NMessageProvider
} from 'naive-ui'

const route = useRoute()
const router = useRouter()

const themeOverrides = { common: { primaryColor: '#18a058', primaryColorHover: '#36ad6a' } }

const menuOptions = [
  { label: () => h(RouterLink, { to: '/runs' }, { default: () => '评测运行' }), key: 'runs' },
  { label: () => h(RouterLink, { to: '/compare' }, { default: () => 'Run 对比' }), key: 'compare' },
  { label: () => h(RouterLink, { to: '/datasets' }, { default: () => '评测集' }), key: 'datasets' }
]

const activeKey = computed(() => {
  if (route.path.startsWith('/compare')) return 'compare'
  if (route.path.startsWith('/datasets')) return 'datasets'
  return 'runs'
})

const currentTitle = computed(() => route.meta.title || '评测运行')

function onSelect(key) {
  const paths = { runs: '/runs', compare: '/compare', datasets: '/datasets' }
  router.push(paths[key])
}
</script>

<style>
body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
.brand { padding: 20px 20px 12px; }
.brand-title { font-size: 17px; font-weight: 600; }
.brand-sub { font-size: 12px; color: #999; margin-top: 4px; letter-spacing: 1px; }
.page-header { padding: 0 24px; height: 56px; display: flex; align-items: center; }
.page-title { font-size: 16px; font-weight: 600; }
.muted { color: #888; }
code { background: #f2f2f2; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
.section { margin-bottom: 24px; }
.section-title { font-size: 15px; font-weight: 600; margin: 0 0 12px; }
</style>

