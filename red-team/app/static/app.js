const state = { catalog: [], categories: {}, injections: {}, selected: null };

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

function banner(message, kind) {
  const el = $("banner");
  el.textContent = message;
  el.className = `banner ${kind}`;
  setTimeout(() => el.classList.add("hidden"), 8000);
}

async function loadConfig() {
  const cfg = await api("/api/config");
  $("target-repo").textContent = cfg.target_repo;
  const badge = $("mode-badge");
  badge.textContent = cfg.demo_mode ? "DEMO MODE" : "LIVE";
  badge.className = `badge ${cfg.demo_mode ? "demo" : "live"}`;
}

async function loadCatalog() {
  const data = await api("/api/catalog");
  state.catalog = data.vulnerabilities;
  state.categories = data.categories;
}

async function loadInjections() {
  const data = await api("/api/injections");
  state.injections = {};
  data.injections.forEach((i) => {
    if (!state.injections[i.vuln_id]) state.injections[i.vuln_id] = [];
    state.injections[i.vuln_id].push(i);
  });
}

function render() {
  const query = $("filter").value.trim().toLowerCase();
  const container = $("catalog");
  container.innerHTML = "";

  const visible = state.catalog.filter((v) => {
    if (!query) return true;
    return [v.title, v.category, v.cwe, v.description, v.file_path]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });

  visible.forEach((v) => {
    const instances = state.injections[v.vuln_id] || [];
    const latest = instances.reduce(
      (current, instance) =>
        !current || instance.instance > current.instance ? instance : current,
      null
    );
    const card = document.createElement("article");
    card.className = `card${instances.length ? " injected" : ""}`;
    card.innerHTML = `
      <div class="card-head">
        <h3></h3>
        <span class="sev ${v.severity}">${v.severity}</span>
      </div>
      <p class="desc"></p>
      <div class="chips">
        <span class="chip">${state.categories[v.category] || v.category}</span>
        <span class="chip">${v.cwe}</span>
        <span class="chip mono">${v.file_path}</span>
      </div>
      <div class="card-foot">
        <span class="status ${instances.length ? "done" : ""}"></span>
        <button class="btn danger">Inject</button>
      </div>`;
    card.querySelector("h3").textContent = v.title;
    card.querySelector(".desc").textContent = v.description;
    const status = card.querySelector(".status");
    if (latest) {
      status.append(`${instances.length} instance(s) · run #${latest.instance} · issue `);
      if (latest.issue_number && latest.issue_url) {
        const issueLink = document.createElement("a");
        issueLink.href = latest.issue_url;
        issueLink.target = "_blank";
        issueLink.rel = "noopener";
        issueLink.textContent = `#${latest.issue_number}`;
        issueLink.addEventListener("click", (event) => event.stopPropagation());
        status.append(issueLink);
      } else {
        status.append(`#${latest.issue_number || "?"}`);
      }
      status.append(` · ${latest.status}`);
    } else {
      status.textContent = "not injected";
    }
    card.querySelector("button").addEventListener("click", (event) => {
      event.stopPropagation();
      injectVuln(v.vuln_id);
    });
    card.addEventListener("click", () => openDrawer(v));
    container.appendChild(card);
  });

  $("stat-total").textContent = state.catalog.length;
  const injectedList = Object.values(state.injections).flat();
  $("stat-injected").textContent = injectedList.length;
  $("stat-issues").textContent = injectedList.filter((i) => i.issue_number).length;
}

function openDrawer(v) {
  state.selected = v;
  $("drawer-title").textContent = v.title;
  $("drawer-meta").innerHTML = `
    <span class="sev ${v.severity}">${v.severity}</span>
    <span class="chip">${state.categories[v.category] || v.category}</span>
    <span class="chip">${v.cwe}</span>
    ${(v.tags || []).map((t) => `<span class="chip">${t}</span>`).join("")}`;
  const instances = state.injections[v.vuln_id] || [];
  const instanceList = $("drawer-instances");
  instanceList.innerHTML = "";
  if (!instances.length) {
    instanceList.textContent = "No instances yet.";
  }
  instances
    .slice()
    .sort((a, b) => a.instance - b.instance)
    .forEach((instance) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.append(`run #${instance.instance} · issue `);
      if (instance.issue_number && instance.issue_url) {
        const issueLink = document.createElement("a");
        issueLink.href = instance.issue_url;
        issueLink.target = "_blank";
        issueLink.rel = "noopener";
        issueLink.textContent = `#${instance.issue_number}`;
        chip.append(issueLink);
      } else {
        chip.append(`#${instance.issue_number || "?"}`);
      }
      chip.append(` · ${instance.status}`);
      instanceList.append(chip);
    });
  $("drawer-desc").textContent = v.description;
  $("drawer-path").textContent = v.file_path;
  $("drawer-code").textContent = v.content;
  $("drawer-remediation").textContent = v.remediation;
  $("drawer").classList.remove("hidden");
}

async function injectVuln(vulnId) {
  try {
    banner(`Injecting ${vulnId}…`, "ok");
    const result = await api("/api/inject", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vuln_id: vulnId }),
    });
    const inj = result.injection;
    banner(
      result.created
        ? result.issue_created === false
          ? `Injected ${vulnId} run #${result.instance} → branch ${inj.branch}, reused existing issue #${inj.issue_number}`
          : `Injected ${vulnId} run #${result.instance} → branch ${inj.branch}, issue #${inj.issue_number}`
        : `${vulnId} already injected (issue #${inj.issue_number}) — no duplicate created`,
      "ok"
    );
    await loadInjections();
    render();
  } catch (err) {
    banner(`Injection failed: ${err.message}`, "err");
  }
}

async function injectAll() {
  const button = $("inject-all");
  button.disabled = true;
  button.textContent = "Injecting…";
  try {
    const result = await api("/api/inject-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const errors = result.results.filter((r) => r.error);
    banner(
      errors.length
        ? `Catalog injected with ${errors.length} error(s): ${errors
            .map((e) => e.vuln_id)
            .join(", ")}`
        : `Catalog injected: ${result.results.length} vulnerabilities processed`,
      errors.length ? "err" : "ok"
    );
    await loadInjections();
    render();
  } catch (err) {
    banner(`Bulk injection failed: ${err.message}`, "err");
  } finally {
    button.disabled = false;
    button.textContent = "Inject entire catalog";
  }
}

async function refresh() {
  await Promise.all([loadConfig(), loadCatalog(), loadInjections()]);
  render();
}

$("filter").addEventListener("input", render);
$("refresh").addEventListener("click", refresh);
$("inject-all").addEventListener("click", injectAll);
$("drawer-close").addEventListener("click", () => $("drawer").classList.add("hidden"));
$("drawer").addEventListener("click", (event) => {
  if (event.target.id === "drawer") $("drawer").classList.add("hidden");
});
$("drawer-inject").addEventListener("click", () => {
  if (state.selected) injectVuln(state.selected.vuln_id);
});

refresh().catch((err) => banner(err.message, "err"));
setInterval(() => loadInjections().then(render).catch(() => {}), 10000);
