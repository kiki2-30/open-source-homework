const state = {
  knowledgeBases: [],
  kbId: null,
  conversationId: null,
  tab: 'chat',
  datasetName: null,
};
const $ = (selector) => document.querySelector(selector);

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      message = (await response.json()).detail || message;
    } catch {}
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message, error = false) {
  const node = $('#toast');
  node.textContent = message;
  node.className = `toast${error ? ' error' : ''}`;
  setTimeout(() => node.classList.add('hidden'), 3500);
}

async function loadKnowledgeBases() {
  state.knowledgeBases = await api('/api/knowledge-bases');
  renderKnowledgeBases();
  if (state.kbId && !state.knowledgeBases.some((kb) => kb.id === state.kbId)) state.kbId = null;
  if (!state.kbId && state.knowledgeBases.length) state.kbId = state.knowledgeBases[0].id;
  renderWorkspace();
  if (state.kbId) {
    await loadDocuments();
    await loadConversations();
  }
}

function renderKnowledgeBases() {
  $('#kb-list').replaceChildren();
  for (const kb of state.knowledgeBases) {
    const button = document.createElement('button');
    button.className = `kb-item${kb.id === state.kbId ? ' active' : ''}`;
    button.innerHTML = `<span class="kb-icon">▤</span><span class="kb-name"></span><span class="kb-count">${kb.document_count}</span>`;
    button.querySelector('.kb-name').textContent = kb.name;
    button.addEventListener('click', async () => {
      state.kbId = kb.id;
      state.conversationId = null;
      renderKnowledgeBases();
      renderWorkspace();
      await Promise.all([loadDocuments(), loadConversations()]);
    });
    $('#kb-list').append(button);
  }
}

function renderWorkspace() {
  const selected = state.knowledgeBases.find((kb) => kb.id === state.kbId);
  $('#empty-state').classList.toggle('hidden', Boolean(selected));
  $('#workspace').classList.toggle('hidden', !selected);
  $('#active-kb-name').textContent = selected ? selected.name : '选择一个知识库';
  $('#delete-kb').classList.toggle('hidden', !selected);
  if (!selected) return;
  $('#kb-count').textContent = `${selected.document_count} 份资料 · 来源可溯`;
  switchTab(state.tab);
}

function switchTab(tab) {
  state.tab = tab;
  document
    .querySelectorAll('.tab')
    .forEach((button) => button.classList.toggle('active', button.dataset.tab === tab));
  for (const name of ['chat', 'documents', 'pipeline', 'evaluation']) {
    $(`#${name}-panel`).classList.toggle('hidden', name !== tab);
  }
  if (!state.kbId) return;
  if (tab === 'pipeline') loadPipeline();
  if (tab === 'evaluation') loadEvaluations();
}

async function createKnowledgeBase() {
  const name = $('#kb-name').value.trim();
  if (!name) return;
  const kb = await api('/api/knowledge-bases', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description: $('#kb-description').value.trim() }),
  });
  $('#kb-dialog').close();
  $('#kb-form').reset();
  state.kbId = kb.id;
  state.conversationId = null;
  await loadKnowledgeBases();
  toast('知识库已创建');
}

async function deleteKnowledgeBase() {
  const selected = state.knowledgeBases.find((kb) => kb.id === state.kbId);
  if (!selected || !confirm(`删除知识库“${selected.name}”？其中的文档、索引和聊天记录都会被删除。`))
    return;
  try {
    await api(`/api/knowledge-bases/${state.kbId}`, { method: 'DELETE' });
    state.kbId = null;
    state.conversationId = null;
    await loadKnowledgeBases();
    toast('知识库已删除');
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadDocuments() {
  if (!state.kbId) return;
  const documents = await api(`/api/knowledge-bases/${state.kbId}/documents`);
  $('#document-count').textContent = documents.filter((doc) => doc.status === 'ready').length;
  const body = $('#documents-table');
  body.replaceChildren();
  if (!documents.length) {
    body.innerHTML =
      '<tr><td colspan="5" class="empty-row">还没有资料，上传文档开始构建知识库。</td></tr>';
  }
  for (const doc of documents) {
    const row = document.createElement('tr');
    const status = doc.status === 'ready' ? '已完成' : doc.status === 'failed' ? '失败' : '处理中';
    row.innerHTML = `<td class="filename"></td><td><span class="status ${doc.status}">${status}</span></td><td>${doc.chunk_count}</td><td>${new Date(doc.created_at).toLocaleString()}</td><td><button class="delete-button" title="移除文档">移除</button></td>`;
    row.querySelector('.filename').textContent =
      doc.filename + (doc.error ? ` · ${doc.error}` : '');
    row.querySelector('.delete-button').addEventListener('click', async () => {
      if (!confirm(`确定移除“${doc.filename}”及其索引？`)) return;
      try {
        await api(`/api/knowledge-bases/${state.kbId}/documents/${doc.id}`, { method: 'DELETE' });
        await Promise.all([loadDocuments(), loadKnowledgeBases()]);
        toast('文档已移除');
      } catch (error) {
        toast(error.message, true);
      }
    });
    body.append(row);
  }
}

async function uploadFiles(files) {
  if (!state.kbId || !files.length) return;
  const form = new FormData();
  for (const file of files) form.append('files', file);
  try {
    const result = await api(`/api/knowledge-bases/${state.kbId}/documents`, {
      method: 'POST',
      body: form,
    });
    toast(`已完成 ${result.length} 个文档的解析与向量索引`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    $('#file-input').value = '';
    await Promise.all([loadDocuments(), loadKnowledgeBases()]);
  }
}

function appendMessage(role, text, sources = []) {
  const messages = $('#messages');
  messages.querySelector('.welcome')?.remove();
  const article = document.createElement('article');
  article.className = `message ${role}`;
  const roleLabel = document.createElement('div');
  roleLabel.className = 'message-role';
  roleLabel.textContent = role === 'user' ? '你' : '知问';
  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';
  bubble.textContent = text;
  article.append(roleLabel, bubble);
  if (sources.length) {
    const sourceList = document.createElement('div');
    sourceList.className = 'sources';
    const title = document.createElement('div');
    title.className = 'sources-title';
    title.textContent = `参考来源 · ${sources.length}`;
    sourceList.append(title);
    for (const source of sources) {
      const card = document.createElement('div');
      card.className = 'source-card';
      const label = document.createElement('strong');
      label.textContent = `[${source.index}] ${source.filename}${source.page ? ` · 第 ${source.page} 页` : ''} · 检索分数 ${Number(source.score).toFixed(3)}`;
      const excerpt = document.createElement('p');
      excerpt.textContent = source.chunk;
      card.append(label, excerpt);
      sourceList.append(card);
    }
    article.append(sourceList);
  }
  messages.append(article);
  messages.scrollTop = messages.scrollHeight;
}

async function askQuestion(question) {
  if (!question.trim() || !state.kbId) return;
  appendMessage('user', question);
  $('#question').value = '';
  $('#ask-button').disabled = true;
  const loading = document.createElement('article');
  loading.className = 'message assistant';
  loading.innerHTML =
    '<div class="message-role">知问</div><div class="message-bubble">正在检索知识库并生成回答…</div>';
  $('#messages').append(loading);
  try {
    const result = await api('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kb_id: state.kbId,
        question,
        conversation_id: state.conversationId,
        strategy: $('#chat-strategy').value,
      }),
    });
    loading.remove();
    state.conversationId = result.conversation_id;
    appendMessage('assistant', result.answer, result.sources);
    await loadConversations();
  } catch (error) {
    loading.remove();
    appendMessage('assistant', `本次请求失败：${error.message}`);
  } finally {
    $('#ask-button').disabled = false;
  }
}

async function loadConversations() {
  if (!state.kbId) return;
  const items = await api(`/api/knowledge-bases/${state.kbId}/conversations`);
  const list = $('#conversation-list');
  list.replaceChildren();
  for (const item of items) {
    const button = document.createElement('button');
    button.className = `conversation${item.id === state.conversationId ? ' active' : ''}`;
    button.textContent = item.title;
    const remove = document.createElement('span');
    remove.className = 'conversation-delete';
    remove.textContent = '×';
    remove.title = '删除对话';
    remove.addEventListener('click', async (event) => {
      event.stopPropagation();
      try {
        await api(`/api/conversations/${item.id}`, { method: 'DELETE' });
        if (state.conversationId === item.id) {
          state.conversationId = null;
          showWelcome();
        }
        await loadConversations();
      } catch (error) {
        toast(error.message, true);
      }
    });
    button.append(remove);
    button.addEventListener('click', async () => {
      state.conversationId = item.id;
      await showConversation(item.id);
      await loadConversations();
    });
    list.append(button);
  }
}

function showWelcome() {
  $('#messages').innerHTML =
    '<div class="welcome"><div class="welcome-badge">✳</div><h2>你好，需要查找什么？</h2><p>我会基于当前知识库回答，并提供对应资料来源。</p></div>';
}

async function showConversation(id) {
  const messages = await api(`/api/conversations/${id}/messages`);
  $('#messages').replaceChildren();
  for (const message of messages) appendMessage(message.role, message.content, message.sources);
}

async function loadPipeline() {
  const runs = await api(`/api/knowledge-bases/${state.kbId}/pipeline`);
  const list = $('#pipeline-list');
  list.replaceChildren();
  if (!runs.length)
    list.innerHTML =
      '<div class="eval-result">还没有流水线记录。上传资料或进行问答后，这里会显示处理过程。</div>';
  for (const run of runs) {
    const row = document.createElement('div');
    row.className = 'pipeline-row';
    row.innerHTML = `<span class="pipeline-stage"></span><span class="pipeline-detail"></span><span class="status ${run.status}">${run.status}</span><span class="pipeline-time">${new Date(run.created_at).toLocaleString()}</span>`;
    row.querySelector('.pipeline-stage').textContent = run.stage;
    row.querySelector('.pipeline-detail').textContent = run.details;
    list.append(row);
  }
}

async function loadEvaluations() {
  const [result, runs] = await Promise.all([
    api(`/api/knowledge-bases/${state.kbId}/evaluations`),
    api(`/api/knowledge-bases/${state.kbId}/evaluations/runs`),
  ]);
  const summary = result.aggregate;
  $('#single-evaluation-summary').textContent = summary.cases
    ? `单题评测累计 ${summary.cases} 题 · 答案子串命中率 ${(summary.substring_hit_rate * 100).toFixed(1)}%`
    : '';
  renderEvaluationHistory(runs);
}

async function runEvaluation() {
  const question = $('#eval-question').value.trim();
  const expected = $('#eval-expected').value.trim();
  if (!question || !expected) return toast('请填写测试问题和期望答案', true);
  $('#run-evaluation').disabled = true;
  try {
    const result = await api('/api/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kb_id: state.kbId, question, expected }),
    });
    $('#single-evaluation-summary').textContent =
      `${result.exact_substring_hit ? '✅ 答案命中' : '⚠️ 未命中期望答案子串'} · 引用 ${result.citation_count} 条`;
    $('#single-evaluation-answer').textContent = result.answer;
    await loadEvaluations();
  } catch (error) {
    toast(error.message, true);
  } finally {
    $('#run-evaluation').disabled = false;
  }
}

function parseEvaluationDataset(text) {
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = text
      .split(/\r?\n/)
      .filter((line) => line.trim())
      .map((line) => JSON.parse(line));
  }
  const cases = Array.isArray(parsed) ? parsed : parsed.cases;
  if (!Array.isArray(cases) || cases.length === 0 || cases.length > 25) {
    throw new Error('评测集必须包含 1–25 道题目。');
  }
  for (const [index, item] of cases.entries()) {
    if (
      !item ||
      typeof item.question !== 'string' ||
      typeof item.expected_answer !== 'string' ||
      !item.question.trim() ||
      !item.expected_answer.trim()
    ) {
      throw new Error(`第 ${index + 1} 题缺少 question 或 expected_answer。`);
    }
    if (item.expected_sources !== undefined && !Array.isArray(item.expected_sources)) {
      throw new Error(`第 ${index + 1} 题的 expected_sources 必须是文件名数组。`);
    }
  }
  return {
    name: parsed.dataset_name || state.datasetName || 'browser-dataset',
    cases,
  };
}

function renderBatchResult(result) {
  const root = $('#batch-result');
  root.replaceChildren();
  const metrics = result.metrics;
  const cards = document.createElement('div');
  cards.className = 'metric-grid';
  const values = [
    ['答案命中率', `${(metrics.answer_hit_rate * 100).toFixed(1)}%`],
    [
      '来源命中率',
      metrics.source_hit_rate == null ? '未评估' : `${(metrics.source_hit_rate * 100).toFixed(1)}%`,
    ],
    ['Recall@K', metrics.recall_at_k == null ? '未评估' : metrics.recall_at_k.toFixed(3)],
    ['Precision@K', metrics.precision_at_k == null ? '未评估' : metrics.precision_at_k.toFixed(3)],
    ['MRR', metrics.mrr == null ? '未评估' : metrics.mrr.toFixed(3)],
    [
      '平均延迟',
      metrics.average_latency_ms == null ? '—' : `${metrics.average_latency_ms.toFixed(0)} ms`,
    ],
  ];
  for (const [label, value] of values) {
    const card = document.createElement('div');
    card.className = 'metric-card';
    const title = document.createElement('span');
    title.textContent = label;
    const metric = document.createElement('strong');
    metric.textContent = value;
    card.append(title, metric);
    cards.append(card);
  }
  root.append(cards);
  const count = document.createElement('p');
  count.className = 'batch-status';
  count.textContent = `${result.dataset_name} · ${result.status} · ${metrics.successful_cases}/${metrics.total_cases} 成功，${metrics.failed_cases} 失败`;
  root.append(count);

  const table = document.createElement('table');
  table.className = 'batch-case-table';
  table.innerHTML =
    '<thead><tr><th>#</th><th>问题</th><th>答案</th><th>来源</th><th>延迟</th></tr></thead>';
  const body = document.createElement('tbody');
  for (const item of result.cases) {
    const row = document.createElement('tr');
    const valuesForRow = [
      String(item.position),
      item.question,
      item.status === 'failed' ? `失败：${item.error}` : item.answer_hit ? '命中' : '未命中',
      item.status === 'failed'
        ? '—'
        : item.retrieved_sources.map((source) => source.filename).join(', ') || '无引用',
      `${item.latency_ms.toFixed(0)} ms`,
    ];
    for (const value of valuesForRow) {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
  table.append(body);
  root.append(table);
}

async function runBatchEvaluation() {
  if (!state.kbId) return;
  let dataset;
  try {
    dataset = parseEvaluationDataset($('#dataset-input').value);
  } catch (error) {
    return toast(error.message, true);
  }
  const button = $('#run-batch-evaluation');
  button.disabled = true;
  button.textContent = '正在逐题评测…';
  try {
    const result = await api(`/api/knowledge-bases/${state.kbId}/evaluations/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        dataset_name: dataset.name,
        strategy: $('#batch-strategy').value,
        cases: dataset.cases,
      }),
    });
    renderBatchResult(result);
    await loadEvaluations();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = '运行批量评测';
  }
}

function renderEvaluationHistory(runs) {
  const root = $('#evaluation-history');
  root.replaceChildren();
  if (!runs.length) return;
  const heading = document.createElement('h3');
  heading.textContent = '历史批次';
  root.append(heading);
  for (const run of runs) {
    const item = document.createElement('div');
    item.className = 'history-run';
    const label = document.createElement('strong');
    label.textContent = run.dataset_name;
    const metric = document.createElement('span');
    metric.textContent = `${run.status} · 命中率 ${(run.metrics.answer_hit_rate * 100).toFixed(1)}% · ${run.metrics.total_cases} 题`;
    item.append(label, metric);
    root.append(item);
  }
}

$('#new-kb').addEventListener('click', () => $('#kb-dialog').showModal());
$('#empty-create').addEventListener('click', () => $('#kb-dialog').showModal());
$('#delete-kb').addEventListener('click', deleteKnowledgeBase);
$('#kb-form').addEventListener('submit', (event) => {
  if (event.submitter?.value === 'cancel') return;
  event.preventDefault();
  createKnowledgeBase().catch((error) => toast(error.message, true));
});
document
  .querySelectorAll('.tab')
  .forEach((button) => button.addEventListener('click', () => switchTab(button.dataset.tab)));
$('#ask-form').addEventListener('submit', (event) => {
  event.preventDefault();
  askQuestion($('#question').value);
});
$('#question').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    $('#ask-form').requestSubmit();
  }
});
$('#new-chat').addEventListener('click', () => {
  state.conversationId = null;
  showWelcome();
  loadConversations();
});
$('#file-input').addEventListener('change', (event) => uploadFiles([...event.target.files]));
$('#refresh-pipeline').addEventListener('click', () =>
  loadPipeline().catch((error) => toast(error.message, true)),
);
$('#run-evaluation').addEventListener('click', runEvaluation);
$('#run-batch-evaluation').addEventListener('click', runBatchEvaluation);
$('#dataset-file').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    state.datasetName = file.name.replace(/\.(jsonl|json)$/i, '');
    const text = await file.text();
    $('#dataset-input').value = text;
    parseEvaluationDataset(text);
    toast(`已导入 ${file.name}`);
  } catch (error) {
    toast(`无法导入评测集：${error.message}`, true);
  } finally {
    event.target.value = '';
  }
});

$('#dataset-input').value = JSON.stringify(
  {
    dataset_name: '退款政策示例集',
    cases: [
      {
        question: '退款期限是多久？',
        expected_answer: '30 天',
        expected_sources: ['refund-policy.pdf'],
      },
      {
        question: '申请退款需要准备什么？',
        expected_answer: '订单号',
        expected_sources: ['refund-policy.pdf'],
      },
    ],
  },
  null,
  2,
);

api('/api/health')
  .then((health) => {
    $('#provider-status').textContent = `${health.provider} 已连接`;
    document.querySelector('.status-dot').classList.add('online');
  })
  .catch(() => {
    $('#provider-status').textContent = '后端连接失败';
  });
loadKnowledgeBases().catch((error) => toast(error.message, true));
