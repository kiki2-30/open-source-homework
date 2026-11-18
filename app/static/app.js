const state = { knowledgeBases: [], kbId: null, conversationId: null, tab: "chat" };
const $ = (selector) => document.querySelector(selector);

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try { message = (await response.json()).detail || message; } catch {}
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast${error ? " error" : ""}`;
  setTimeout(() => node.classList.add("hidden"), 3500);
}

async function loadKnowledgeBases() {
  state.knowledgeBases = await api("/api/knowledge-bases");
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
  $("#kb-list").replaceChildren();
  for (const kb of state.knowledgeBases) {
    const button = document.createElement("button");
    button.className = `kb-item${kb.id === state.kbId ? " active" : ""}`;
    button.innerHTML = `<span class="kb-icon">▤</span><span class="kb-name"></span><span class="kb-count">${kb.document_count}</span>`;
    button.querySelector(".kb-name").textContent = kb.name;
    button.addEventListener("click", async () => {
      state.kbId = kb.id;
      state.conversationId = null;
      renderKnowledgeBases();
      renderWorkspace();
      await Promise.all([loadDocuments(), loadConversations()]);
    });
    $("#kb-list").append(button);
  }
}

function renderWorkspace() {
  const selected = state.knowledgeBases.find((kb) => kb.id === state.kbId);
  $("#empty-state").classList.toggle("hidden", Boolean(selected));
  $("#workspace").classList.toggle("hidden", !selected);
  $("#active-kb-name").textContent = selected ? selected.name : "选择一个知识库";
  $("#delete-kb").classList.toggle("hidden", !selected);
  if (!selected) return;
  $("#kb-count").textContent = `${selected.document_count} 份资料 · 来源可溯`;
  switchTab(state.tab);
}

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  for (const name of ["chat", "documents", "pipeline", "evaluation"]) {
    $(`#${name}-panel`).classList.toggle("hidden", name !== tab);
  }
  if (!state.kbId) return;
  if (tab === "pipeline") loadPipeline();
  if (tab === "evaluation") loadEvaluations();
}

async function createKnowledgeBase() {
  const name = $("#kb-name").value.trim();
  if (!name) return;
  const kb = await api("/api/knowledge-bases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description: $("#kb-description").value.trim() }),
  });
  $("#kb-dialog").close();
  $("#kb-form").reset();
  state.kbId = kb.id;
  state.conversationId = null;
  await loadKnowledgeBases();
  toast("知识库已创建");
}

async function deleteKnowledgeBase() {
  const selected = state.knowledgeBases.find((kb) => kb.id === state.kbId);
  if (!selected || !confirm(`删除知识库“${selected.name}”？其中的文档、索引和聊天记录都会被删除。`)) return;
  try {
    await api(`/api/knowledge-bases/${state.kbId}`, { method: "DELETE" });
    state.kbId = null;
    state.conversationId = null;
    await loadKnowledgeBases();
    toast("知识库已删除");
  } catch (error) { toast(error.message, true); }
}

async function loadDocuments() {
  if (!state.kbId) return;
  const documents = await api(`/api/knowledge-bases/${state.kbId}/documents`);
  $("#document-count").textContent = documents.filter((doc) => doc.status === "ready").length;
  const body = $("#documents-table");
  body.replaceChildren();
  if (!documents.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty-row">还没有资料，上传文档开始构建知识库。</td></tr>';
  }
  for (const doc of documents) {
    const row = document.createElement("tr");
    const status = doc.status === "ready" ? "已完成" : doc.status === "failed" ? "失败" : "处理中";
    row.innerHTML = `<td class="filename"></td><td><span class="status ${doc.status}">${status}</span></td><td>${doc.chunk_count}</td><td>${new Date(doc.created_at).toLocaleString()}</td><td><button class="delete-button" title="移除文档">移除</button></td>`;
    row.querySelector(".filename").textContent = doc.filename + (doc.error ? ` · ${doc.error}` : "");
    row.querySelector(".delete-button").addEventListener("click", async () => {
      if (!confirm(`确定移除“${doc.filename}”及其索引？`)) return;
      try {
        await api(`/api/knowledge-bases/${state.kbId}/documents/${doc.id}`, { method: "DELETE" });
        await Promise.all([loadDocuments(), loadKnowledgeBases()]);
        toast("文档已移除");
      } catch (error) { toast(error.message, true); }
    });
    body.append(row);
  }
}

async function uploadFiles(files) {
  if (!state.kbId || !files.length) return;
  const form = new FormData();
  for (const file of files) form.append("files", file);
  try {
    const result = await api(`/api/knowledge-bases/${state.kbId}/documents`, { method: "POST", body: form });
    toast(`已完成 ${result.length} 个文档的解析与向量索引`);
  } catch (error) { toast(error.message, true); }
  finally {
    $("#file-input").value = "";
    await Promise.all([loadDocuments(), loadKnowledgeBases()]);
  }
}

function appendMessage(role, text, sources = []) {
  const messages = $("#messages");
  messages.querySelector(".welcome")?.remove();
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const roleLabel = document.createElement("div");
  roleLabel.className = "message-role";
  roleLabel.textContent = role === "user" ? "你" : "知问";
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;
  article.append(roleLabel, bubble);
  if (sources.length) {
    const sourceList = document.createElement("div");
    sourceList.className = "sources";
    const title = document.createElement("div");
    title.className = "sources-title";
    title.textContent = `参考来源 · ${sources.length}`;
    sourceList.append(title);
    for (const source of sources) {
      const card = document.createElement("div");
      card.className = "source-card";
      const label = document.createElement("strong");
      label.textContent = `[${source.index}] ${source.filename}${source.page ? ` · 第 ${source.page} 页` : ""}`;
      const excerpt = document.createElement("p");
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
  appendMessage("user", question);
  $("#question").value = "";
  $("#ask-button").disabled = true;
  const loading = document.createElement("article");
  loading.className = "message assistant";
  loading.innerHTML = '<div class="message-role">知问</div><div class="message-bubble">正在检索知识库并生成回答…</div>';
  $("#messages").append(loading);
  try {
    const result = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kb_id: state.kbId, question, conversation_id: state.conversationId }),
    });
    loading.remove();
    state.conversationId = result.conversation_id;
    appendMessage("assistant", result.answer, result.sources);
    await loadConversations();
  } catch (error) {
    loading.remove();
    appendMessage("assistant", `本次请求失败：${error.message}`);
  } finally { $("#ask-button").disabled = false; }
}

async function loadConversations() {
  if (!state.kbId) return;
  const items = await api(`/api/knowledge-bases/${state.kbId}/conversations`);
  const list = $("#conversation-list");
  list.replaceChildren();
  for (const item of items) {
    const button = document.createElement("button");
    button.className = `conversation${item.id === state.conversationId ? " active" : ""}`;
    button.textContent = item.title;
    const remove = document.createElement("span");
    remove.className = "conversation-delete";
    remove.textContent = "×";
    remove.title = "删除对话";
    remove.addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        await api(`/api/conversations/${item.id}`, { method: "DELETE" });
        if (state.conversationId === item.id) {
          state.conversationId = null;
          showWelcome();
        }
        await loadConversations();
      } catch (error) { toast(error.message, true); }
    });
    button.append(remove);
    button.addEventListener("click", async () => {
      state.conversationId = item.id;
      await showConversation(item.id);
      await loadConversations();
    });
    list.append(button);
  }
}

function showWelcome() {
  $("#messages").innerHTML = '<div class="welcome"><div class="welcome-badge">✳</div><h2>你好，需要查找什么？</h2><p>我会基于当前知识库回答，并提供对应资料来源。</p></div>';
}

async function showConversation(id) {
  const messages = await api(`/api/conversations/${id}/messages`);
  $("#messages").replaceChildren();
  for (const message of messages) appendMessage(message.role, message.content, message.sources);
}

async function loadPipeline() {
  const runs = await api(`/api/knowledge-bases/${state.kbId}/pipeline`);
  const list = $("#pipeline-list");
  list.replaceChildren();
  if (!runs.length) list.innerHTML = '<div class="eval-result">还没有流水线记录。上传资料或进行问答后，这里会显示处理过程。</div>';
  for (const run of runs) {
    const row = document.createElement("div");
    row.className = "pipeline-row";
    row.innerHTML = `<span class="pipeline-stage"></span><span class="pipeline-detail"></span><span class="status ${run.status}">${run.status}</span><span class="pipeline-time">${new Date(run.created_at).toLocaleString()}</span>`;
    row.querySelector(".pipeline-stage").textContent = run.stage;
    row.querySelector(".pipeline-detail").textContent = run.details;
    list.append(row);
  }
}

async function loadEvaluations() {
  const result = await api(`/api/knowledge-bases/${state.kbId}/evaluations`);
  const summary = result.aggregate;
  $("#evaluation-result").innerHTML = summary.cases
    ? `<div class="eval-result">累计评测 <strong>${summary.cases}</strong> 题 · 答案子串命中率 <strong>${(summary.substring_hit_rate * 100).toFixed(1)}%</strong></div>`
    : "";
}

async function runEvaluation() {
  const question = $("#eval-question").value.trim();
  const expected = $("#eval-expected").value.trim();
  if (!question || !expected) return toast("请填写测试问题和期望答案", true);
  $("#run-evaluation").disabled = true;
  try {
    const result = await api("/api/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kb_id: state.kbId, question, expected }),
    });
    $("#evaluation-result").innerHTML = `<div class="eval-result">${result.exact_substring_hit ? "✅ 答案命中" : "⚠️ 未命中期望答案子串"} · 引用 ${result.citation_count} 条<br><br></div>`;
    const answer = document.createElement("div");
    answer.className = "eval-result";
    answer.textContent = result.answer;
    $("#evaluation-result").append(answer);
    await loadEvaluations();
  } catch (error) { toast(error.message, true); }
  finally { $("#run-evaluation").disabled = false; }
}

$("#new-kb").addEventListener("click", () => $("#kb-dialog").showModal());
$("#empty-create").addEventListener("click", () => $("#kb-dialog").showModal());
$("#delete-kb").addEventListener("click", deleteKnowledgeBase);
$("#kb-form").addEventListener("submit", (event) => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  createKnowledgeBase().catch((error) => toast(error.message, true));
});
document.querySelectorAll(".tab").forEach((button) => button.addEventListener("click", () => switchTab(button.dataset.tab)));
$("#ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  askQuestion($("#question").value);
});
$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("#ask-form").requestSubmit();
  }
});
$("#new-chat").addEventListener("click", () => {
  state.conversationId = null;
  showWelcome();
  loadConversations();
});
$("#file-input").addEventListener("change", (event) => uploadFiles([...event.target.files]));
$("#refresh-pipeline").addEventListener("click", () => loadPipeline().catch((error) => toast(error.message, true)));
$("#run-evaluation").addEventListener("click", runEvaluation);

api("/api/health").then((health) => {
  $("#provider-status").textContent = `${health.provider} 已连接`;
  document.querySelector(".status-dot").classList.add("online");
}).catch(() => { $("#provider-status").textContent = "后端连接失败"; });
loadKnowledgeBases().catch((error) => toast(error.message, true));
