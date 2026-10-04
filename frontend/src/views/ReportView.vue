<template>
  <n-space vertical size="large" v-if="data">
    <n-card :bordered="false" size="small">
      <n-space align="center" justify="space-between">
        <n-space align="center" size="small">
          <n-button size="small" @click="$router.push('/runs')">返回</n-button>
          <code>{{ data.run_id }}</code>
          <n-tag size="small" :bordered="false">{{ data.dataset || '—' }}</n-tag>
          <n-tag v-if="summary.multi_turn_total" size="small" :bordered="false" type="info">
            多轮 {{ summary.multi_turn_total }} 条
          </n-tag>
        </n-space>
        <span class="muted">{{ new Date(data.started_at).toLocaleString('zh-CN') }}</span>
      </n-space>
    </n-card>

    <n-grid :cols="3" :x-gap="16" :y-gap="16">
      <n-gi v-for="s in stats" :key="s.key">
        <n-card :bordered="false" size="small">
          <div class="stat-label">
            <span>{{ s.label }}</span>
            <n-tooltip trigger="hover" :style="{ maxWidth: '380px' }">
              <template #trigger><span class="help">?</span></template>
              <div class="tip">{{ s.help }}</div>
            </n-tooltip>
          </div>
          <div class="stat-value">{{ s.value }}</div>
          <div v-if="s.sub" class="stat-sub">{{ s.sub }}</div>
        </n-card>
      </n-gi>
    </n-grid>

    <n-collapse>
      <n-collapse-item title="这些指标是什么意思？（点开看完整说明）" name="glossary">
        <div class="glossary">
          <p>
            <b>为什么要分「过程层」和「结果层」</b>：只看最终答案，无法区分
            「检索没找到那条法条」和「找到了但模型没答对」——这两者的修法完全不同
            （前者改检索，后者改 prompt 或模型）。所以指标分成两层。
          </p>
          <table>
            <thead>
              <tr><th>层</th><th>指标（内部名）</th><th>说人话</th></tr>
            </thead>
            <tbody>
              <tr>
                <td rowspan="2">过程层</td>
                <td><code>retrieval_hit</code></td>
                <td>该找到的法条<b>找到了吗、排第几</b>。得分 = 1 ÷ 最优排名，所以它其实是"平均排名质量"：每次都排第 1 名就是 1.00，平均排第 5 名是 0.20。</td>
              </tr>
              <tr>
                <td><code>citation_support</code></td>
                <td>答案里写的法条编号，<b>能不能在召回的原文里找到</b>。这一项低 = 答案在编法条号，是幻觉最直接的证据。</td>
              </tr>
              <tr>
                <td rowspan="3">结果层</td>
                <td><code>points_hit</code></td>
                <td>答案答到了<b>几个必须覆盖的关键要点</b>。每题都预先写好了几个关键短语，命中比例就是得分。</td>
              </tr>
              <tr>
                <td><code>semantic_similarity</code></td>
                <td>答案和标准答案<b>意思上</b>有多接近（0～1）。换个说法但意思对，分依然高。</td>
              </tr>
              <tr>
                <td><code>refusal</code></td>
                <td><b>该拒答的时候拒答了吗</b>；以及不该拒答的时候，有没有莫名其妙地说"我答不了"。</td>
              </tr>
            </tbody>
          </table>
          <p>
            <b>「适用样本」是什么</b>：有些指标对某些题<b>天生没法判</b>——比如拒答题谈不上"检索命中"，
            答案没引用条文号时"引用是否有据"也无从判定。这些情况标为「不适用」，
            并从通过率的分母里剔除。<b>「不适用」不等于「做对了」</b>——
            把它们算成通过，等于白送分数。
          </p>
          <p>
            <b>「全部通过」的分母为什么不等于总条数</b>：除了「不适用」，
            还会剔除**调用失败**——那是平台自己的问题（网络超时、上游 5xx、流被掐断），
            与被测系统能力无关，不能算到它头上。
          </p>
          <p><b>失败归因的几种说法</b>：</p>
          <ul>
            <li><b>没召回到</b> —— 该找到的法条根本没进检索结果，答案自然答不出。</li>
            <li><b>召回到了但没答对</b> —— 证据在手上，但答案没把关键要点说全。</li>
            <li><b>幻觉（答案无召回依据）</b> —— 答案里出现了召回内容里根本没有的东西（最常见的是编法条号）。</li>
            <li><b>该拒答没拒答</b> —— 问的东西知识库里没有，它却照样编了一个答案。</li>
            <li><b>无端拒答</b> —— 知识库里明明有，它却说"我答不了"。</li>
            <li><b>调用失败</b> —— 平台侧的故障，不计入通过率，但会单独列出来。</li>
          </ul>
        </div>
      </n-collapse-item>
    </n-collapse>

    <n-alert v-if="summary.layers.points_hit.average > summary.layers.retrieval_hit.average + 0.1"
             type="warning" :bordered="false">
      结果层明显高于过程层：答案看起来不错，但相当一部分回答并没有拿到权威条文依据。
      只看最终答案发现不了这一点。
    </n-alert>

    <n-alert v-if="summary.call_errors" type="warning" :bordered="false">
      本轮的 {{ summary.call_errors }} 条调用失败属于平台侧故障（网络超时 / 上游 5xx / 流被掐断），
      与模型能力无关，已从通过率、分层得分和题型统计中全部剔除。
    </n-alert>

    <n-grid :cols="2" :x-gap="16" :y-gap="16">
      <n-gi>
        <n-card :bordered="false" title="按题型分解" size="small">
          <n-data-table :columns="categoryColumns" :data="categoryRows" :bordered="false" size="small" />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card :bordered="false" title="失败归因" size="small">
          <n-empty v-if="!failureRows.length" description="全部通过" />
          <n-data-table v-else :columns="failureColumns" :data="failureRows" :bordered="false" size="small" />
        </n-card>
      </n-gi>
    </n-grid>

    <n-card :bordered="false" title="逐条结果" size="small">
      <n-data-table
        :columns="recordColumns"
        :data="data.records"
        :bordered="false"
        size="small"
        :row-props="recordRowProps"
      />
    </n-card>
  </n-space>

  <n-spin v-else :show="true" style="width: 100%; margin-top: 80px" />
</template>

<script setup>
import { computed, h, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NTag } from 'naive-ui'
import { api, CATEGORY_LABELS, DIFFICULTY_LABELS } from '@/api/client'

const route = useRoute()
const router = useRouter()
const data = ref(null)

const summary = computed(() => data.value?.summary || { layers: {}, by_category: {}, failures: {} })

// 通过率的分母用有效样本（已剔除平台侧调用失败），与后端 summarize 口径一致
const passLabel = computed(() => {
  const s = summary.value
  if (s.passed == null) return '—'
  return `${s.passed}/${s.effective_total ?? s.total}`
})

// 每个卡片都带一句"说人话"的解释。指标名对写代码的人清楚，对看报告的人不清楚——
// 而这个页面的用途恰恰是给人看结论，所以解释必须跟数字贴在同一个位置。
const stats = computed(() => {
  const s = summary.value
  const layer = (name) => s.layers?.[name] || { average: 0, pass_rate: 0, count: 0 }
  const count = (name) => `适用 ${layer(name).count} 条`
  return [
    {
      key: 'passed',
      label: '全部通过',
      value: passLabel.value,
      sub: '',
      help: '该拿分的地方都拿满了的条数。分母已剔除两类不该算的：调用失败（平台自己出问题）与「不适用」（该指标对这条无从判定）。'
    },
    {
      key: 'retrieval_hit',
      label: '过程层 · 检索命中',
      value: layer('retrieval_hit').average.toFixed(2),
      sub: count('retrieval_hit'),
      help: '该找到的法条，有没有被检索召回、排在第几位。得分 = 1 ÷ 最优排名：每次都在第 1 名召回就是 1.00，平均排在第 5 名是 0.20。它只衡量"检索"这一步，与答案写了什么无关。'
    },
    {
      key: 'citation_support',
      label: '过程层 · 引用有据',
      value: layer('citation_support').average.toFixed(2),
      sub: count('citation_support'),
      help: '答案里引用的法条编号，能不能在被召回的原文里找到出处。这一项低，说明答案在编造法条号——是幻觉最直接的证据。答案没引用条文号时无从判定，不计入。'
    },
    {
      key: 'points_hit',
      label: '结果层 · 要点命中',
      value: layer('points_hit').average.toFixed(2),
      sub: count('points_hit'),
      help: '答案覆盖了多少个标准答案里的关键要点。每题都预先写好了必须答到的几个关键短语，0.95 表示平均答到了 95%。'
    },
    {
      key: 'semantic_similarity',
      label: '结果层 · 语义相似度',
      value: layer('semantic_similarity').average.toFixed(2),
      sub: count('semantic_similarity'),
      help: '答案与标准答案在"意思上"有多接近（0～1，越高越好）。换个说法但意思对，分依然高——逐字比对做不到这一点。'
    },
    {
      key: 'latency',
      label: '平均耗时',
      value: `${(s.average_latency_ms / 1000).toFixed(1)}s`,
      sub: '',
      help: '从发问到拿到完整回答的平均时间。多轮用例记的是所有轮次之和——那才是它真实的成本。'
    }
  ]
})

const categoryRows = computed(() =>
  Object.entries(summary.value.by_category || {}).map(([k, v]) => {
    const m = v.layers?.retrieval_hit
    return {
      category: CATEGORY_LABELS[k] || k,
      total: v.total,
      passed: v.passed,
      // 只有"适用"的条目才参与——拒答题上的检索命中率无从判定，计进去等于白送分数
      retrieval: m && m.count ? Math.round(m.pass_rate * 100) + '%' : '—'
    }
  })
)

const categoryColumns = [
  { title: '题型', key: 'category' },
  { title: '有效样本', key: 'total', width: 90 },
  { title: '通过', key: 'passed', width: 70 },
  { title: '检索命中率', key: 'retrieval', width: 110 }
]

const failureRows = computed(() =>
  Object.entries(summary.value.failures || {}).map(([id, reason]) => ({ case_id: id, reason }))
)

const failureColumns = [
  { title: '用例', key: 'case_id', render: (row) => h('code', row.case_id) },
  { title: '归因', key: 'reason' }
]

function scoreOf(record, name) {
  return (record.scores || []).find((s) => s.name === name)
}

const recordColumns = [
  { title: '用例', key: 'case_id', render: (row) => h('code', row.case_id) },
  { title: '题型', key: 'category', width: 110, render: (row) => CATEGORY_LABELS[row.category] || row.category },
  { title: '难度', key: 'difficulty', width: 70, render: (row) => DIFFICULTY_LABELS[row.difficulty] || row.difficulty },
  { title: '检索', key: 'retrieval', width: 70, render: (row) => {
      const s = scoreOf(row, 'retrieval_hit')
      if (!s || s.applicable === false) return '—'
      return h(NTag, { size: 'small', bordered: false, type: s.passed ? 'success' : 'error' },
        { default: () => (s.passed ? '命中' : '未命中') })
    } },
  { title: '引用', key: 'citation', width: 60, render: (row) => {
      const s = scoreOf(row, 'citation_support')
      if (!s || s.applicable === false) return '—'
      return h(NTag, { size: 'small', bordered: false, type: s.passed ? 'success' : 'error' },
        { default: () => (s.passed ? '有据' : '无据') })
    } },
  { title: '要点', key: 'points', width: 80, render: (row) => {
      const s = scoreOf(row, 'points_hit')
      if (!s || s.applicable === false) return '—'
      return `${Math.round(s.value * 100)}%`
    } },
  { title: '耗时', key: 'latency_ms', width: 90, render: (row) => `${row.latency_ms}ms` },
  { title: '归因', key: 'failure', render: (row) => summary.value.failures[row.case_id] || '' }
]

function recordRowProps(row) {
  return { style: 'cursor: pointer', onClick: () => router.push(`/runs/${data.value.run_id}/trace/${row.case_id}`) }
}

onMounted(async () => {
  data.value = await api.run(route.params.runId)
})
</script>

<style scoped>
.stat-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #6b7280;
}
.help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 1px solid #c9ccd3;
  color: #8a8f98;
  font-size: 10px;
  cursor: help;
  user-select: none;
}
.help:hover {
  border-color: #2080f0;
  color: #2080f0;
}
.stat-value {
  margin-top: 4px;
  font-size: 25px;
  font-weight: 600;
  line-height: 1.2;
}
.stat-sub {
  margin-top: 2px;
  font-size: 12px;
  color: #9aa0a6;
}
.tip {
  line-height: 1.7;
  font-size: 13px;
}
.glossary {
  line-height: 1.8;
  font-size: 13px;
  color: #3f4550;
}
.glossary table {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0 14px;
}
.glossary th,
.glossary td {
  border: 1px solid #e8e8e8;
  padding: 6px 10px;
  text-align: left;
  vertical-align: top;
}
.glossary th {
  background: #fafafa;
  font-weight: 600;
}
.glossary ul {
  margin: 6px 0;
  padding-left: 20px;
}
.glossary li {
  margin: 3px 0;
}
</style>
