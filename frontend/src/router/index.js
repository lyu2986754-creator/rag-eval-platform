import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/runs' },
  { path: '/datasets', name: 'datasets', component: () => import('@/views/DatasetsView.vue'), meta: { title: '评测集' } },
  { path: '/runs', name: 'runs', component: () => import('@/views/RunsView.vue'), meta: { title: '评测运行' } },
  { path: '/runs/:runId', name: 'report', component: () => import('@/views/ReportView.vue'), meta: { title: '评测报告' } },
  { path: '/runs/:runId/trace/:caseId', name: 'trace', component: () => import('@/views/TraceView.vue'), meta: { title: 'Trace 详情' } },
  { path: '/compare', name: 'compare', component: () => import('@/views/CompareView.vue'), meta: { title: 'Run 对比' } }
]

const router = createRouter({ history: createWebHistory(), routes })

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · RAG 评测平台` : 'RAG 评测平台'
})

export default router

