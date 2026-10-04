<template>
  <n-space vertical size="large">
    <n-card :bordered="false" size="small">
      <n-space align="center" justify="space-between">
        <n-space align="center" size="small">
          <span class="muted">共 {{ runs.length }} 次运行</span>
          <n-tag v-if="health" size="small" :bordered="false">API 正常</n-tag>
        </n-space>
        <n-space>
          <n-button size="small" @click="load">刷新</n-button>
          <n-button size="small" type="primary" @click="openStart">发起评测</n-button>
        </n-space>
      </n-space>
    </n-card>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <n-card v-if="job" :bordered="false" size="small" title="进行中的评测">
      <n-space vertical size="small">
        <n-progress
          type="line"
          :percentage="job.total ? Math.round((job.finished / job.total) * 100) : 0"
          :height="10"
        />
        <span class="muted">
          状态 {{ job.status }} · {{ job.finished }}/{{ job.total }}
          <template v-if="job.current_case"> · 正在跑 <code>{{ job.current_case }}</code></template>
          <template v-if="job.run_id"> · run_id <code>{{ job.run_id }}</code></template>
        </span>
        <n-alert v-if="job.error" type="error" :bordered="false">{{ job.error }}</n-alert>
        <n-button v-if="job.status === 'done'" size="small" type="primary" @click="goRun(job.run_id)">
          查看报告
        </n-button>
      </n-space>
    </n-card>

    <n-card :bordered="false" title="运行历史">
      <n-data-table
        :columns="columns"
        :data="runs"
        :loading="loading"
        :row-props="rowProps"
        :bordered="false"
        size="small"
      />
    </n-card>
  </n-space>

  <n-modal v-model:show="showStart" preset="card" title="发起一轮评测" style="width: 460px">
    <n-space vertical size="medium">
      <n-select v-model:value="form.dataset" :options="datasetOptions" placeholder="选择评测集" />
      <n-input-number v-model:value="form.limit" :min="1" placeholder="只跑前 N 条（留空表示全部）" clearable style="width: 100%" />
      <n-alert type="info" :bordered="false">一轮约需 1–2 分钟，完成后自动出现在运行历史里。</n-alert>
      <n-space justify="end">
        <n-button @click="showStart = false">取消</n-button>
        <n-button type="primary" :loading="starting" @click="startJob">开始</n-button>
      </n-space>
    </n-space>
  </n-modal>
</template>

<script setup>
import { computed, h, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { NButton, NTag } from 'naive-ui'
import { api } from '@/api/client'

const router = useRouter()
const message = useMessage()

const runs = ref([])
const datasets = ref([])
const health = ref(null)
const loading = ref(false)
const error = ref('')
const showStart = ref(false)
const starting = ref(false)
const job = ref(null)
const form = ref({ dataset: null, limit: null })
let timer = null

const datasetOptions = computed(() =>
  datasets.value.map((d) => ({ label: `${d.name}（${d.case_count} 条）`, value: d.name }))
)

const columns = [
  { title: 'run_id', key: 'run_id', render: (row) => h('code', row.run_id) },
  { title: '通过', key: 'passed', width: 150, render: (row) => {
      if (row.passed == null) return '—'
      // 调用失败是平台侧故障，不算被测系统的问题，因此不计入分母
      const total = row.effective_total ?? row.total
      const label = row.call_errors ? `${row.passed}/${total}（+${row.call_errors} 调用失败）` : `${row.passed}/${total}`
      return h(NTag, { type: row.passed === total ? 'success' : 'warning', size: 'small', bordered: false },
        { default: () => label })
    } },
  { title: '用例数', key: 'case_count', width: 90 },
  { title: '开始时间', key: 'started_at', render: (row) => new Date(row.started_at).toLocaleString('zh-CN') },
  { title: '操作', key: 'ops', width: 100,
    render: (row) => h(NButton, { size: 'tiny', onClick: () => goRun(row.run_id) }, { default: () => '查看' }) }
]

function rowProps(row) {
  return { style: 'cursor: pointer', onClick: () => goRun(row.run_id) }
}

function goRun(id) {
  if (id) router.push(`/runs/${id}`)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [r, d, hl] = await Promise.all([api.runs(), api.datasets(), api.health()])
    runs.value = r
    datasets.value = d
    health.value = hl
    if (!form.value.dataset && d.length) form.value.dataset = d[0].name
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function openStart() {
  showStart.value = true
}

async function startJob() {
  if (!form.value.dataset) {
    message.warning('请先选择评测集')
    return
  }
  starting.value = true
  try {
    job.value = await api.startJob({
      dataset: form.value.dataset,
      limit: form.value.limit || null
    })
    showStart.value = false
    message.success('已开始，右侧进度会实时更新')
    poll()
  } catch (e) {
    message.error(e.message)
  } finally {
    starting.value = false
  }
}

function poll() {
  clearInterval(timer)
  timer = setInterval(async () => {
    if (!job.value) return
    try {
      job.value = await api.job(job.value.job_id)
      if (['done', 'error'].includes(job.value.status)) {
        clearInterval(timer)
        await load()
      }
    } catch {
      clearInterval(timer)
    }
  }, 2000)
}

onMounted(load)
onUnmounted(() => clearInterval(timer))
</script>
