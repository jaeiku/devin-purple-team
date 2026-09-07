const $ = (id) => document.getElementById(id);

const STATUS_PROGRESS = {
  queued: 10,
  refused_budget: 0,
  running: 55,
  blocked: 45,
  completed: 100,
  failed: 100,
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function json(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function renderTiles(m) {
  $("t-issues").textContent = m.issues_created;
  $("t-issues-sub").textContent = `${m.injections_total} injected`;
  $("t-active").textContent = m.sessions_active;
  $("t-active-sub").textContent =
    `${m.sessions_total} spawned, ${m.sessions_queued} queued`;
  $("t-completed").textContent = m.sessions_completed;
  $("t-completed-sub").textContent = `${m.remediated} findings remediated`;
  $("t-failed").textContent = m.sessions_failed;
  $("t-failed-sub").textContent =
    `${m.sessions_refused_budget} refused on budget`;
  $("t-prs").textContent = m.prs_opened;
  $("t-success").textContent = `${m.success_rate_pct}%`;
}

function renderBudget(m, cfg) {
  $("budget-text").textContent =
    `${m.acu_spend} spent / ${m.acu_committed} committed of ${m.acu_ceiling} ACU ceiling`;
  const pct = Math.min(100, (m.acu_spend / (m.acu_ceiling || 1)) * 100);
  const committedPct = Math.min(100, (m.acu_committed / (m.acu_ceiling || 1)) * 100);
  $("budget-bar").style.width = `${pct}%`;
  $("budget-committed").style.width = `${committedPct}%`;
  $("guardrail-text").textContent =
    `caps: ${cfg.max_acu_per_session} ACU/session · ${cfg.max_concurrent_sessions} concurrent sessions`;
}

function renderBars(elementId, counts, labels, severityColors) {
  const el = $(elementId);
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  el.innerHTML = entries.length
    ? entries
        .map(([key, n]) => {
          const cls = severityColors ? key : "";
          return `<div class="bar-row">
            <span>${escapeHtml(labels[key] || key)}</span>
            <div class="bar-track"><div class="bar-fill ${cls}" style="width:${(n / max) * 100}%"></div></div>
            <span class="count">${n}</span>
          </div>`;
        })
        .join("")
    : '<span class="muted">No findings yet.</span>';
}

function renderFeed(data) {
  const sessionsByIssue = {};
  data.sessions.forEach((s) => {
    sessionsByIssue[s.issue_number] = s;
  });
  const rows = data.injections.map((inj) => {
    const s = sessionsByIssue[inj.issue_number] || null;
    const status = s ? s.status : inj.status;
    const progress = s ? STATUS_PROGRESS[s.status] ?? 0 : inj.issue_number ? 20 : 0;
    return `<tr class="sev-${inj.severity}">
      <td><span class="sev ${inj.severity}">${inj.severity}</span></td>
      <td>${escapeHtml(inj.title)}<br><span class="muted mono">${escapeHtml(inj.file_path)}</span></td>
      <td>${escapeHtml(data.category_labels[inj.category] || inj.category)}</td>
      <td>${inj.issue_number ? `<a href="${escapeHtml(inj.issue_url)}" target="_blank" rel="noopener">#${inj.issue_number}</a>` : "&mdash;"}</td>
      <td>${s && s.session_id ? `<a href="${escapeHtml(s.session_url)}" target="_blank" rel="noopener" class="mono">${escapeHtml(s.session_id)}</a>` : '<span class="muted">not started</span>'}</td>
      <td><span class="pill ${status}">${escapeHtml(status)}</span></td>
      <td class="mono">${s ? `${s.acu_consumed} / ${s.acu_limit}` : "&mdash;"}</td>
      <td>${s && s.pr_url ? `<a href="${escapeHtml(s.pr_url)}" target="_blank" rel="noopener">PR</a>` : "&mdash;"}</td>
      <td><div class="progress"><div style="width:${progress}%"></div></div></td>
    </tr>`;
  });
  $("feed-body").innerHTML = rows.length
    ? rows.join("")
    : '<tr><td colspan="9" class="muted">No injections yet — trigger one from the red team console.</td></tr>';
}

function renderLog(events) {
  $("log").innerHTML = events
    .map((e) => {
      const service = e.service.replace("-team", "");
      const ts = (e.ts || "").replace("T", " ").slice(0, 19);
      return `<div class="log-line ${service} ${e.level}">
        <span class="ts">${escapeHtml(ts)}</span>
        <span class="svc">${escapeHtml(e.service)}</span>
        <span class="ev">${escapeHtml(e.event)}</span>
        <span class="msg">${escapeHtml(e.message)}</span>
      </div>`;
    })
    .join("");
}

function renderSummary(summary) {
  $("verdict-badge").textContent = summary.verdict.replace("_", " ");
  $("verdict-badge").className = `verdict-badge ${summary.verdict}`;
  $("verdict-headline").textContent = summary.headline;
  $("s-coverage").textContent = `${summary.detection_to_pr_coverage_pct}%`;
  $("s-ttp").textContent = `${summary.median_time_to_pr_minutes}m`;
  $("s-acu").textContent = summary.mean_acu_per_remediation;
  $("s-budget").textContent = `${summary.budget_used_pct}%`;
  $("s-budget-sub").textContent = `${summary.budget_remaining_acu} ACU remaining`;
  $("answers").innerHTML = summary.answers
    .map(
      (a) => `<div class="answer"><h3>${escapeHtml(a.question)}</h3><p>${escapeHtml(a.answer)}</p></div>`
    )
    .join("");
  $("guardrails").innerHTML = Object.entries(summary.guardrails)
    .map(([k, v]) => `<span class="chip">${escapeHtml(k)} = ${escapeHtml(v)}</span>`)
    .join("");
}

async function refresh() {
  const [data, log] = await Promise.all([json("/api/overview"), json("/api/events?limit=80")]);
  $("target-repo").textContent = data.config.target_repo;
  $("t-issues-link").href =
    `https://github.com/${data.config.target_repo}/issues?q=label%3Ared-team`;
  const badge = $("mode-badge");
  badge.textContent = data.config.demo_mode ? "DEMO MODE" : "LIVE";
  badge.className = `badge ${data.config.demo_mode ? "demo" : "live"}`;

  renderTiles(data.metrics);
  renderBudget(data.metrics, data.config);
  renderBars("category-chart", data.metrics.injections_by_category, data.category_labels, false);
  renderBars("severity-chart", data.metrics.injections_by_severity, {}, true);
  renderFeed(data);
  renderSummary(data.summary);
  renderLog(log.events);
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    const leader = tab.dataset.view === "leader";
    $("view-ops").classList.toggle("hidden", leader);
    $("view-leader").classList.toggle("hidden", !leader);
  });
});

setInterval(() => {
  $("clock").textContent = new Date().toISOString().replace("T", " ").slice(0, 19) + "Z";
}, 1000);

refresh().catch((err) => console.error(err));
setInterval(() => refresh().catch((err) => console.error(err)), 5000);
