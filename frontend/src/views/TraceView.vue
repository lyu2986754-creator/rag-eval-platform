<template>
  <n-space vertical size="large" v-if="record">
    <n-card :bordered="false" size="small">
      <n-space align="center" size="small">
        <n-button size="small" @click="$router.push(`/runs/${runId}`)">返回报告</n-button>
        <code>{{ record.case_id }}</code>
        <n-tag size="small" :bordered="false">{{ CATEGORY_LABELS[record.category] || record.category }}</n-tag>
        <n-tag size="small" :bordered="false">{{ DIFFICULTY_LABELS[record.difficulty] || record.difficulty }}</n-tag>
        <n-tag size="small" :bordered="false" :type="record.status === 'ok' ? 'success' : 'error'">{{ record.status }}</n-tag>
        <span class="muted">{{ record.latency_ms }}ms</span>
      </n-space>
    </n-card>

    <n-card :bordered="false" :title="record.prior_turns?.length ? '问题（多轮，被评分的是最后一轮）' : '问题'" size="small">
      <n-space v-if="record.prior_turns?.length" vertical size="small" style="margin-bottom: 10px">
        <div v-for="(t, i) in record.prior_turns" :key="i" class="muted">
          第 {{ i + 1 }} 轮　{{ t }}
        </div>
      </n-space>
      <div>{{ record.question }}</div>
    </n-card>

    <n-card :bordered="false" size="small">
      <template #header>
        <span>评分</span>
        <span class="muted" style="margin-left: 8px; font-size: 12px">
          过程层 = 检索环节的问题 · 结果层 = 最终答案的问题 ·「不适用」= 该指标对这一条无从判定（不计入通过率）
        </span>
      </template>
      <n-table :bordered="false" size="small">
        <thead>
          <tr><th>评分器</th><th>层</th><th>得分</th><th>结论</th></tr>
        </thead>
        <tbody>
          <tr v-for="s in record.scores" :key="s.name">
            <td><code>{{ s.name }}</code></td>
            <td>{{ s.layer === 'process' ? '过程层' : '结果层' }}</td>
            <td>
              <n-tag v-if="s.applicable === false" size="small" :bordered="false">不适用</n-tag>
              <n-tag v-else size="small" :bordered="false" :type="s.passed ? 'success' : 'error'">
                {{ s.value.toFixed(2) }}
              </n-tag>
            </td>
            <td>{{ s.reason }}</td>
          </tr>
        </tbody>
      </n-table>
    </n-card>

    <n-card :bordered="false" title="运行诊断" size="small">
      <n-grid :cols="4" :x-gap="16">
        <n-gi>
          <n-statistic label="召回片段来源" :value="record.retrieval_source === 'diagnostics' ? '服务实测' : record.retrieval_source === 'probe' ? '独立探针' : '无'" />
        </n-gi>
        <n-gi>
          <n-statistic label="输入 tokens" :value="record.input_tokens ?? '—'" />
        </n-gi>
        <n-gi>
          <n-statistic label="输出 tokens" :value="record.output_tokens ?? '—'" />
        </n-gi>
        <n-gi>
          <n-statistic label="成本" :value="record.cost_amount == null ? '未配置单价' : `¥${record.cost_amount}`" />
        </n-gi>
      </n-grid>
      <n-alert
        v-if="record.retrieval_source === 'probe'"
        type="warning"
        :bordered="false"
        style="margin-top: 12px"
      >
        该条未收到服务端诊断事件，召回片段来自评测平台的独立探针（代理数据）。
      </n-alert>
    </n-card>

    <n-card :bordered="false" title="模型回答" size="small">
      <pre class="answer">{{ record.answer || '（空）' }}</pre>
      <n-alert v-if="record.error" type="error" :bordered="false" style="margin-top: 12px">{{ record.error }}</n-alert>
    </n-card>

    <n-card :bordered="false" size="small">
      <template #header>
        <span>召回片段</span>
        <span class="muted" style="margin-left: 8px; font-size: 12px">
          {{ record.retrieval_source === 'probe'
              ? '来自评测平台的独立探针（代理数据）'
              : '被评测服务在 diagnostics 事件中推送的真实召回结果' }}
        </span>
      </template>
      <n-empty v-if="!chunks.length" description="无召回数据" />
      <n-space v-else vertical size="small">
        <n-card v-for="(c, i) in chunks" :key="i" size="small" embedded>
          <n-space align="center" size="small" style="margin-bottom: 6px">
            <n-tag size="small" :bordered="false" type="info">#{{ i + 1 }}</n-tag>
            <span class="muted" style="font-size: 12px">{{ sourceLabel(c.source) }}</span>
          </n-space>
          <div class="chunk">{{ c.content }}</div>
        </n-card>
      </n-space>
    </n-card>
  </n-space>

  <n-spin v-else :show="true" style="width: 100%; margin-top: 80px" />
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, CATEGORY_LABELS, DIFFICULTY_LABELS } from '@/api/client'

const route = useRoute()
const runId = computed(() => route.params.runId)
const record = ref(null)

const chunks = computed(() => {
  const rec = record.value
  return rec?.retrieved_chunks || []
})

function sourceLabel(url) {
  const name = url.split('/').pop() || url
  return name
}

onMounted(async () => {
  const data = await api.run(runId.value)
  record.value = data.records.find((r) => r.case_id === route.params.caseId) || null
})
</script>

<style scoped>
.answer { white-space: pre-wrap; word-break: break-word; font-family: inherit; margin: 0; line-height: 1.7; }
.chunk { white-space: pre-wrap; word-break: break-word; line-height: 1.7; font-size: 13px; color: #333; }
</style>
