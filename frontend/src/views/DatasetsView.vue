<template>
  <n-space vertical size="large">
    <n-card :bordered="false" size="small">
      <n-space align="center" size="small">
        <n-select v-model:value="selected" :options="options" style="width: 280px" placeholder="选择评测集" />
        <n-button size="small" :disabled="!selected" :loading="validating" @click="doValidate">校验</n-button>
      </n-space>
    </n-card>

    <n-alert v-if="problems" :type="problems.length ? 'error' : 'success'" :bordered="false" title="校验结果">
      <template v-if="problems.length">
        <div v-for="(p, i) in problems" :key="i">{{ p }}</div>
      </template>
      <template v-else>全部通过：条文号均存在于语料，答案要点均出自所引条文。</template>
    </n-alert>

    <n-card v-if="detail" :bordered="false" :title="`${detail.name} · ${detail.case_count} 条`" size="small">
      <n-data-table :columns="columns" :data="detail.cases" :bordered="false" size="small" />
    </n-card>
  </n-space>
</template>

<script setup>
import { computed, h, onMounted, ref, watch } from 'vue'
import { NTag } from 'naive-ui'
import { api, CATEGORY_LABELS, DIFFICULTY_LABELS } from '@/api/client'

const datasets = ref([])
const selected = ref(null)
const detail = ref(null)
const problems = ref(null)
const validating = ref(false)

const options = computed(() =>
  datasets.value.map((d) => ({ label: `${d.name}（${d.case_count} 条）`, value: d.name }))
)

const columns = [
  { title: 'id', key: 'id', width: 120, render: (row) => h('code', row.id) },
  { title: '题型', key: 'category', width: 110, render: (row) => CATEGORY_LABELS[row.category] || row.category },
  { title: '难度', key: 'difficulty', width: 70, render: (row) => DIFFICULTY_LABELS[row.difficulty] || row.difficulty },
  { title: '轮次', key: 'prior_turns', width: 70,
    render: (row) => ((row.prior_turns?.length || 0) + 1) + ' 轮' },
  { title: '问题（多轮时显示被评分的那一轮）', key: 'question' },
  { title: '依据条文', key: 'expected_sources', width: 220,
    render: (row) => (row.should_refuse ? h(NTag, { size: 'small', bordered: false, type: 'warning' }, { default: () => '应拒答' })
      : (row.expected_sources || []).join('、')) }
]

async function loadDetail() {
  if (!selected.value) return
  problems.value = null
  detail.value = await api.dataset(selected.value)
}

async function doValidate() {
  validating.value = true
  try {
    const r = await api.validate(selected.value)
    problems.value = r.problems
  } finally {
    validating.value = false
  }
}

watch(selected, loadDetail)

onMounted(async () => {
  datasets.value = await api.datasets()
  if (datasets.value.length) selected.value = datasets.value[0].name
})
</script>
