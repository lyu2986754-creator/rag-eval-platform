// 只依赖 fetch，不引入 axios（project.md 第 8 节：不为省几行代码引第三方库）

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  })
  const text = await response.text()
  let body = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = { detail: text }
  }
  if (!response.ok) {
    const detail = body?.detail || `HTTP ${response.status}`
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return body
}

export const api = {
  health: () => request('/api/health'),
  datasets: () => request('/api/datasets'),
  dataset: (name) => request(`/api/datasets/${encodeURIComponent(name)}`),
  validate: (name) =>
    request(`/api/datasets/${encodeURIComponent(name)}/validate`, { method: 'POST' }),
  runs: () => request('/api/runs'),
  run: (id) => request(`/api/runs/${encodeURIComponent(id)}`),
  report: (id) => request(`/api/runs/${encodeURIComponent(id)}/report`),
  diff: (a, b) =>
    request(`/api/runs/${encodeURIComponent(a)}/diff/${encodeURIComponent(b)}`),
  startJob: (payload) =>
    request('/api/jobs', { method: 'POST', body: JSON.stringify(payload) }),
  job: (id) => request(`/api/jobs/${encodeURIComponent(id)}`)
}

export const CATEGORY_LABELS = {
  direct_extraction: '直接抽取',
  cross_article: '跨条文综合',
  conditional: '条件与例外',
  terminology: '术语辨析',
  refuse: '应拒答'
}

export const DIFFICULTY_LABELS = { easy: '易', medium: '中', hard: '难' }

