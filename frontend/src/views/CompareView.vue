<template>
  <n-space vertical size="large">
    <n-card :bordered="false" size="small">
      <n-space align="center" size="large">
        <n-space align="center" size="small">
          <span class="muted">基准</span>
          <n-select v-model:value="leftId" :options="runOptions" style="width: 300px" placeholder="选择 run" />
        </n-space>
        <span class="muted">对比</span>
        <n-space align="center" size="small">
          <n-select v-model:value="rightId" :options="runOptions" style="width: 300px" placeholder="选择 run" />
        </n-space>
        <n-button type="primary" :disabled="!leftId || !rightId || leftId === rightId" @click="load">对比</n-button>
      </n-space>
    </n-card>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <n-card v-if="diff" :bordered="false" size="small">
      <n-space align="center" size="small">
        <span>发生变化的用例：</span>
        <n-tag type="warning" :bordered="false">{{ changedCount }} / {{ diff.rows.length }}</n-tag>
      </n-space>
    </n-card>

    <n-card v-if="diff" :bordered="false" title="逐条对比" size="small">
      <n-data-table :columns="columns" :data="diff.rows" :bordered="false" size="small" />
    </n-card>
  </n-space>
</template>

<script setup>
import { computed, h, onMounted, ref } from 'vue'
import { NTag } from 'naive-ui'
import { api } from '@/api/client'

const runs = ref([])
const leftId = ref(null)
const rightId = ref(null)
const diff = ref(null)
const error = ref('')

const runOptions = computed(() =>
  runs.value.map((r) => ({
    label: `${new Date(r.started_at).toLocaleString('zh-CN')}  ${r.passed ?? '?'}/${r.total ?? '?'}`,
    value: r.run_id
  }))
)

const changedCount = computed(() => (diff.value ? diff.value.rows.filter((r) => r.changed).length : 0))

function cell(side, field) {
  const v = side?.[field]
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') {
    return h(NTag, { size: 'small', bordered: false, type: v ? 'success' : 'error' }, { default: () => (v ? '✓' : '✗') })
  }
  return String(v)
}

const columns = [
  { title: '用例', key: 'case_id', width: 120, render: (row) => h('code', row.case_id) },
  { title: '变化', key: 'changed', width: 80,
    render: (row) => h(NTag, { size: 'small', bordered: false, type: row.changed ? 'warning' : 'default' },
      { default: () => (row.changed ? '有变化' : '不变') }) },
  { title: '基准·检索', key: 'l_retrieval', width: 100, render: (row) => cell(row.left, 'retrieval_passed') },
  { title: '对比·检索', key: 'r_retrieval', width: 100, render: (row) => cell(row.right, 'retrieval_passed') },
  { title: '基准·引用', key: 'l_citation', width: 100, render: (row) => cell(row.left, 'citation_passed') },
  { title: '对比·引用', key: 'r_citation', width: 100, render: (row) => cell(row.right, 'citation_passed') },
  { title: '基准·要点', key: 'l_points', width: 100,
    render: (row) => (row.left?.points_value == null ? '—' : `${Math.round(row.left.points_value * 100)}%`) },
  { title: '对比·要点', key: 'r_points', width: 100,
    render: (row) => (row.right?.points_value == null ? '—' : `${Math.round(row.right.points_value * 100)}%`) },
  { title: '基准·归因', key: 'l_fail', render: (row) => row.left?.failure || '' },
  { title: '对比·归因', key: 'r_fail', render: (row) => row.right?.failure || '' }
]

async function load() {
  error.value = ''
  try {
    diff.value = await api.diff(leftId.value, rightId.value)
  } catch (e) {
    error.value = e.message
  }
}

onMounted(async () => {
  runs.value = await api.runs()
  if (runs.value.length >= 2) {
    leftId.value = runs.value[1].run_id
    rightId.value = runs.value[0].run_id
    await load()
  }
})
</script>
