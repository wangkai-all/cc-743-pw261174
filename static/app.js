/* Admissions matching dashboard — vanilla JS, no external dependencies. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  let state = null;
  let roundRecord = null;
  let selectedCandidate = null;
  let lastAction = null;
  let previewData = null;
  let eventOutcomeFilter = "all";
  let eventCandidateFilter = "all";
  let explainRequestId = 0;

  const POLICY_LABELS = {
    full_recompute: "整体重算",
    protect_promises: "保留承诺",
  };
  const POOL_LABELS = { normal: "普通池", special: "专项池", joint: "共享专项" };
  const TIER_LABELS = {
    sibling: "兄弟姐妹在读",
    legacy: "教职工子女",
    diversity: "就近/多元",
    none: "普通/无优先",
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function toast(message, isError = false) {
    const el = $("#toast");
    el.textContent = message;
    el.className = `toast show${isError ? " error" : ""}`;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => (el.className = "toast"), 3600);
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `请求失败 (${res.status})`);
    return data;
  }

  function candidatesById() {
    return Object.fromEntries((state?.candidates || []).map((c) => [c.id, c]));
  }
  function acceptersById() {
    const map = {};
    (state?.programs || []).forEach((p) => (map[p.id] = p));
    (state?.joint_programs || []).forEach((j) => (map[j.id] = j));
    return map;
  }
  function cname(cid) {
    if (!cid) return "—";
    const c = candidatesById()[cid];
    return c ? `${c.name} · ${cid}` : cid;
  }
  function aname(aid) {
    if (!aid) return "未录取";
    const a = acceptersById()[aid];
    return a ? a.name : aid;
  }
  function shortName(aid) {
    if (!aid) return "未录取";
    const a = acceptersById()[aid];
    if (!a) return aid;
    return `${a.name}（${aid}）`;
  }
  function activeResult() {
    return roundRecord?.result || state?.current_result;
  }

  // -------------------------------------------------------------------------
  // Theme and tabs
  // -------------------------------------------------------------------------
  function initTheme() {
    const saved = localStorage.getItem("admissions-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const theme = saved || (prefersDark ? "dark" : "light");
    document.documentElement.dataset.theme = theme;
    const btn = $("#themeToggle");
    const sync = () => {
      const dark = document.documentElement.dataset.theme === "dark";
      btn.textContent = dark ? "☀️ 浅色" : "🌙 深色";
      btn.setAttribute("aria-pressed", String(dark));
    };
    sync();
    btn.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("admissions-theme", next);
      sync();
    });
  }

  function initTabs() {
    $$(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        $$(".tab").forEach((t) => {
          const active = t === tab;
          t.classList.toggle("active", active);
          t.setAttribute("aria-selected", String(active));
        });
        const name = tab.dataset.tab;
        $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
        $$(".tab-panel").forEach((p) => {
          if (p.id === `tab-${name}`) p.removeAttribute("hidden");
          else p.setAttribute("hidden", "");
        });
        if (name === "verify" && !window._verifyLoaded) loadVerification();
      });
    });
  }

  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  function renderAll() {
    if (!state) return;
    $("#seedText").textContent = state.seed_text;
    $("#roundLabel").textContent = `第 ${state.current_round} 轮 · ${POLICY_LABELS[state.current_policy] || state.current_policy || "—"}`;
    const isCoalition = state.scenario === "promise_coalition";
    $("#scenarioTitle").textContent = isCoalition ? "承诺导致的三人轮换联盟" : "清河学区 2026 虚构招生匹配";
    $("#scenarioNotice").textContent = state.institution_notice || "所有人物、校区、成绩与名额均为虚构。";
    populateAccepterOptions();
    populateCandidateSelects();
    renderKpisAndAlerts();
    renderAssignmentTable();
    renderExplainSelect();
    renderCapacity();
    renderStability();
    renderRoundList();
    renderTieTable();
  }

  function populateAccepterOptions() {
    const accepters = [
      ...(state.programs || []).map((a) => ({ ...a, kind: "program" })),
      ...(state.joint_programs || []).map((a) => ({ ...a, kind: "joint" })),
    ];
    const options = accepters
      .map((a) => `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name)}（${a.id}${a.kind === "joint" ? " · 共享专项" : ""}）</option>`)
      .join("");
    $$(".accepter-options").forEach((sel) => {
      const blank = sel.name?.startsWith("choice") ? `<option value="">— 空志愿 —</option>` : "";
      sel.innerHTML = blank + options;
    });
    $("#capacityAccepter").innerHTML = options;
    updateCapacityPoolOptions();
    $("#capacityAccepter").onchange = updateCapacityPoolOptions;
  }

  function updateCapacityPoolOptions() {
    const aid = $("#capacityAccepter").value;
    const joint = (state.joint_programs || []).some((j) => j.id === aid);
    const poolSel = $("#capacityPool");
    if (joint) {
      poolSel.innerHTML = `<option value="joint">跨校共享总容量</option>`;
      $("#capacityForm [name=new_capacity]").value = state.joint_programs.find((j) => j.id === aid).capacity;
    } else {
      const p = state.programs.find((x) => x.id === aid);
      poolSel.innerHTML = `<option value="normal">普通池容量</option><option value="special">专项池容量</option>`;
      $("#capacityForm [name=new_capacity]").value = p?.quotas?.normal ?? 0;
      poolSel.onchange = () => {
        const pool = poolSel.value;
        $("#capacityForm [name=new_capacity]").value = p?.quotas?.[pool] ?? 0;
      };
    }
  }

  function activeCandidates() {
    const withdrawn = new Set((state.withdrawals || []).map((w) => w.candidate_id));
    return (state.candidates || []).filter((c) => !withdrawn.has(c.id));
  }

  function populateCandidateSelects() {
    const opts = activeCandidates()
      .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}（${c.id}）</option>`)
      .join("");
    $("#withdrawCandidate").innerHTML = opts;
    $("#explainCandidate").innerHTML = (state.candidates || [])
      .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}（${c.id}）</option>`)
      .join("");
    if (!selectedCandidate || !state.candidates.some((c) => c.id === selectedCandidate)) {
      selectedCandidate = state.candidates?.[0]?.id;
    }
    $("#explainCandidate").value = selectedCandidate;
  }

  function renderKpisAndAlerts() {
    const r = activeResult();
    if (!r) return;
    const s = r.stats || {};
    const st = r.stability || {};
    const cap = r.capacity_audit || {};
    const kpis = [
      { label: "活跃学生", value: s.active ?? "—", sub: `已撤回 ${(state.withdrawals || []).length} 人` },
      { label: "已分配", value: s.assigned ?? "—", sub: `保留承诺 ${s.promises ?? 0}` },
      { label: "未录取", value: s.unmatched ?? "—", sub: "志愿全部用完", cls: s.unmatched ? "alert" : "good" },
      { label: "可阻挡配对", value: s.blocking_pairs ?? 0, sub: `承诺导致 ${s.promise_blocking_pairs ?? 0}`, cls: s.blocking_pairs ? "alert" : "good" },
      { label: "交换联盟", value: s.blocking_coalitions ?? 0, sub: `承诺联盟 ${st.promise_blocking_coalitions?.length || 0}`, cls: s.blocking_coalitions ? "alert" : "good" },
      { label: "容量/规则冲突", value: s.conflicts ?? r.conflicts?.length ?? 0, sub: cap.all_within_capacity ? "容量核对通过" : "容量超额", cls: s.conflicts ? "alert" : "good" },
    ];
    $("#kpiGrid").innerHTML = kpis
      .map(
        (k) => `<article class="kpi ${k.cls || ""}">
          <div class="label">${escapeHtml(k.label)}</div>
          <div class="value">${escapeHtml(k.value)}</div>
          <div class="sub">${escapeHtml(k.sub)}</div>
        </article>`
      )
      .join("");

    const alerts = [];
    (r.conflicts || []).forEach((c) =>
      alerts.push({ type: "critical", icon: "⛔", title: c.code, text: c.message })
    );
    if (st.stable === false && !(r.conflicts || []).length) {
      alerts.push({
        type: "warning",
        icon: "⚠️",
        title: "结果不是完全稳定匹配",
        text: `存在 ${st.blocking_pairs?.length || 0} 个可阻挡配对和 ${st.blocking_coalitions?.length || 0} 个短周期联盟；其中多数由必须保留的承诺造成。`,
      });
    }
    if (st.constrained_stable && !st.stable) {
      alerts.push({
        type: "warning",
        icon: "🔒",
        title: "承诺约束下无额外结构性阻挡",
        text: "除去必须打破既有承诺才能消除的组合后，没有发现新的结构性 blocking pair / coalition。",
      });
    }
    if (st.stable && cap.all_within_capacity && !(r.conflicts || []).length) {
      alerts.push({ type: "good", icon: "✅", title: "稳定性与容量核对通过", text: "未发现 blocking pair、短周期联盟或容量超额。" });
    }
    $("#alertStack").innerHTML = alerts
      .map(
        (a) => `<div class="alert ${a.type}" role="alert"><span class="icon" aria-hidden="true">${a.icon}</span><div><strong>${escapeHtml(a.title)}</strong><span>${escapeHtml(a.text)}</span></div></div>`
      )
      .join("");
  }

  function renderAssignmentTable() {
    const r = activeResult();
    const q = ($("#studentSearch").value || "").trim().toLowerCase();
    const tbody = $("#assignmentTable tbody");
    if (!r) return;
    const rows = (state.candidates || [])
      .filter((c) => !q || c.id.toLowerCase().includes(q) || c.name.toLowerCase().includes(q))
      .map((c) => {
        const a = r.assignments?.[c.id];
        const withdrawn = (state.withdrawals || []).some((w) => w.candidate_id === c.id);
        const choiceCount = c.choices?.length || 0;
        let resultCell = `<span class="tag critical">未录取</span>`;
        if (withdrawn) resultCell = `<span class="tag">已撤回</span>`;
        if (a) {
          const host = a.host_program_id ? `<br><small class="muted">宿主：${escapeHtml(shortName(a.host_program_id))}</small>` : "";
          resultCell = `<strong>${escapeHtml(shortName(a.accepter_id))}</strong>${host}`;
        }
        const source = a?.source === "promise" ? `<span class="tag orange">🔒 保留承诺</span>` : a ? `<span class="tag blue">DA 新匹配</span>` : "—";
        const choiceIdx = a?.choice_index;
        return `<tr data-candidate="${c.id}" style="cursor:pointer">
          <td class="student-cell"><strong>${escapeHtml(c.name)}</strong><span>${c.id}</span></td>
          <td><span class="tag ${c.pool === "special" ? "orange" : "blue"}">${POOL_LABELS[c.pool] || c.pool}</span></td>
          <td>${resultCell}</td>
          <td>${a && Number.isInteger(choiceIdx) ? `第 ${choiceIdx + 1}/${choiceCount} 志愿` : `共 ${choiceCount} 个`}</td>
          <td>${source}</td>
        </tr>`;
      })
      .join("");
    tbody.innerHTML = rows || `<tr><td colspan="5" class="empty">没有匹配学生</td></tr>`;
    $$("#assignmentTable tbody tr").forEach((tr) =>
      tr.addEventListener("click", () => {
        selectedCandidate = tr.dataset.candidate;
        $("#explainCandidate").value = selectedCandidate;
        loadExplanation();
        $('.tab[data-tab="assignments"]').click();
      })
    );
  }

  function renderExplainSelect() {
    $("#explainCandidate").value = selectedCandidate;
    loadExplanation();
  }

  async function loadExplanation() {
    selectedCandidate = $("#explainCandidate").value;
    const body = $("#explanationBody");
    if (!selectedCandidate) return;
    const requestId = ++explainRequestId;
    body.innerHTML = `<p class="muted">正在生成解释…</p>`;
    try {
      const roundNo = roundRecord?.round_no || state.current_round;
      const e = await api(`/api/explain?candidate_id=${encodeURIComponent(selectedCandidate)}&round=${roundNo}`);
      if (requestId !== explainRequestId) return;
      const statusIcon = { admitted: "✅", rejected: "⛔", not_considered: "⏭️" };
      body.innerHTML = `
        <div class="expl-summary">${escapeHtml(e.summary)}</div>
        ${e.rows
          .map((row) => {
            const rank = row.my_rank
              ? `<div class="rank-mini"><span class="tag">你的层级：${escapeHtml(row.my_rank.tier_label)}</span><span class="tag">抽签：${Number(row.my_rank.draw).toFixed(4)}</span>${row.cutoff_id ? `<span class="tag warning">末位：${escapeHtml(row.cutoff_id)}</span>` : ""}${row.capacity != null ? `<span class="tag">容量 ${row.capacity} · 已用 ${row.used}</span>` : ""}</div>`
              : "";
            const host = row.host_name ? `<p>宿主校区：${escapeHtml(row.host_name)}</p>` : "";
            return `<article class="choice-step ${row.status}">
              <div class="step-head">
                <div class="step-title">${statusIcon[row.status] || "•"} 第 ${row.choice_index + 1} 志愿 · ${escapeHtml(row.accepter_name)} ${row.is_joint ? '<span class="tag orange">跨校共享</span>' : ""}</div>
                <span class="tag ${row.status === "admitted" ? "good" : row.status === "rejected" ? "critical" : ""}">${escapeHtml(row.status_label)}</span>
              </div>
              ${host}
              ${row.explanation ? `<p>${escapeHtml(row.explanation)}</p>` : row.status === "admitted" ? "<p>DA 最终持有该志愿席位。</p>" : ""}
              ${rank}
            </article>`;
          })
          .join("")}
      `;
    } catch (err) {
      if (requestId !== explainRequestId) return;
      body.innerHTML = `<div class="alert critical"><span class="icon">⛔</span><div>${escapeHtml(err.message)}</div></div>`;
    }
  }

  // -------------------------------------------------------------------------
  // Capacity
  // -------------------------------------------------------------------------
  function renderCapacity() {
    const r = activeResult();
    if (!r) return;
    const rows = r.capacity_audit?.rows || [];
    $("#capacityStatusPill").innerHTML = r.capacity_audit.all_within_capacity
      ? `<span class="pill good">✅ 全部在容量内</span>`
      : `<span class="pill bad">⛔ 存在超额</span>`;

    $("#capacityCards").innerHTML = rows
      .map((row) => {
        const normalWidth = row.normal_effective_capacity ? Math.min(100, (row.normal_used / row.normal_effective_capacity) * 100) : 0;
        const specialWidth = row.special_effective_capacity ? Math.min(100, (row.special_used / row.special_effective_capacity) * 100) : 0;
        return `<article class="cap-card ${row.over_capacity ? "over" : ""}">
          <h4><span>${escapeHtml(row.name)} <small class="muted">${row.accepter_id}</small></span>${row.kind === "joint" ? '<span class="tag orange">共享专项</span>' : ""}</h4>
          <div class="meter-row"><span>普通</span><div class="meter"><span class="normal" style="width:${normalWidth}%"></span></div><b>${row.normal_used}/${row.normal_effective_capacity}</b></div>
          <div class="meter-row"><span>专项</span><div class="meter"><span class="special" style="width:${specialWidth}%"></span></div><b>${row.special_used}/${row.special_effective_capacity}</b></div>
          <div class="cap-legend">
            <span><i class="dot blue"></i>普通已用</span><span><i class="dot orange"></i>专项已用</span>
            ${row.converted_in ? `<span class="tag blue">回流 +${row.converted_in}</span>` : ""}
            ${row.reserved_unused ? `<span class="tag">保留空专项 ${row.reserved_unused}</span>` : ""}
            ${row.over_capacity ? `<span class="tag critical">超额</span>` : `<span class="tag good">核对通过</span>`}
          </div>
          ${row.host_placement ? renderHostPlacement(row) : ""}
        </article>`;
      })
      .join("");

    $("#capacityTable tbody").innerHTML = rows
      .map(
        (row) => `<tr class="${row.over_capacity ? "over-row" : ""}">
        <td><strong>${escapeHtml(row.name)}</strong><br><span class="muted">${row.accepter_id}${row.kind === "joint" ? " · J" : ""}</span></td>
        <td class="num">${row.normal_base}</td>
        <td class="num">${row.normal_effective_capacity}${row.converted_in ? ` <span class="tag blue">+${row.converted_in}</span>` : ""}</td>
        <td class="num">${row.normal_used}${row.normal_promised ? ` <small class="muted">(${row.normal_promised} 承诺)</small>` : ""}</td>
        <td class="num">${row.special_base}</td>
        <td class="num">${row.special_effective_capacity}</td>
        <td class="num">${row.special_used}${row.special_promised ? ` <small class="muted">(${row.special_promised} 承诺)</small>` : ""}</td>
        <td class="num">${row.converted_in}</td>
        <td>${row.reserved_unused ? `保留 ${row.reserved_unused}` : `普通空位 ${row.normal_free}`}</td>
        <td>${row.over_capacity ? '<span class="tag critical">⛔ 超额</span>' : '<span class="tag good">✅ 通过</span>'}</td>
      </tr>`
      )
      .join("");

    const conv = [
      ...(r.conversion || []).map((x) => ({ id: x.accepter_id, ...x, kind: "校区专项" })),
      ...(r.joint_conversion || []).map((x) => ({ id: x.joint_id, accepter_id: x.joint_id, ...x, kind: "跨校专项" })),
    ];
    $("#conversionBody").innerHTML = `<table class="table"><thead><tr><th>项目</th><th>类型</th><th>专项容量</th><th>已用</th><th>未用</th><th>配置</th><th>回流</th><th>目标</th></tr></thead><tbody>
      ${conv
        .map((x) => {
          const convert = x.convert ?? true;
          return `<tr><td>${escapeHtml(shortName(x.accepter_id || x.id))}</td><td>${x.kind}</td><td class="num">${x.special_capacity ?? x.capacity}</td><td class="num">${x.special_used ?? x.used}</td><td class="num">${x.unused}</td><td>${convert ? "允许回流" : "配置为保留"}</td><td><b>${x.converted_to_normal || 0}</b></td><td>${x.target_program_id ? escapeHtml(shortName(x.target_program_id)) : "本校普通池/—"}</td></tr>`;
        })
        .join("")}
    </tbody></table>`;
  }

  function renderHostPlacement(row) {
    const entries = Object.entries(row.host_placement || {});
    if (!entries.length) return "";
    return `<div class="hint">宿主安置：${entries.map(([cid, pid]) => `${escapeHtml(cid)}→${escapeHtml(pid)}`).join("，")}</div>`;
  }

  // -------------------------------------------------------------------------
  // Stability
  // -------------------------------------------------------------------------
  function renderStability() {
    const r = activeResult();
    if (!r) return;
    const st = r.stability || {};
    const pairs = st.blocking_pairs || [];
    $("#pairCountPill").innerHTML = pairs.length
      ? `<span class="pill bad">${pairs.length} 个</span>`
      : `<span class="pill good">✅ 0 个</span>`;
    $("#blockingPairsBody").innerHTML = pairs.length
      ? `<table class="table"><thead><tr><th>学生</th><th>更偏好项目</th><th>类型</th><th>当前录取</th><th>优先序比较</th><th>承诺归因</th></tr></thead><tbody>
        ${pairs
          .map((p) => {
            const cr = p.candidate_rank || {};
            const xr = p.cutoff_rank || {};
            return `<tr>
              <td><strong>${escapeHtml(cname(p.candidate_id))}</strong></td>
              <td>${escapeHtml(p.accepter_name || aname(p.accepter_id))}<br><small class="muted">第 ${(p.choice_index ?? 0) + 1} 志愿 · ${POOL_LABELS[p.pool] || p.pool}</small></td>
              <td>${p.kind === "vacancy" ? '<span class="tag warning">有空位</span>' : '<span class="tag critical">优先序更高</span>'}</td>
              <td>${escapeHtml(aname(p.current_accepter_id))}</td>
              <td><small>${escapeHtml(cr.tier_label || "")} ${cr.draw != null ? Number(cr.draw).toFixed(4) : ""}</small><br>${p.worst_holder_id ? `<small class="muted">末位 ${escapeHtml(cname(p.worst_holder_id))}：${escapeHtml(xr.tier_label || "")} ${xr.draw != null ? Number(xr.draw).toFixed(4) : ""}</small>` : ""}</td>
              <td>${p.promise_caused ? `<span class="tag orange">🔒 必须打破承诺</span><br><small>${(p.involved_promise_candidates || []).map(escapeHtml).join(", ")}</small>` : '<span class="tag good">结构性</span>'}</td>
            </tr>`;
          })
          .join("")}
      </tbody></table>`
      : `<div class="empty">✅ 没有 blocking pair。</div>`;

    const coals = st.blocking_coalitions || [];
    $("#coalitionCountPill").innerHTML = coals.length ? `<span class="pill bad">${coals.length} 个</span>` : `<span class="pill good">0 个</span>`;
    $("#coalitionsBody").innerHTML = coals.length
      ? coals
          .map(
            (k) => `<article class="coalition-card ${k.promise_caused ? "promise" : ""}">
              <h4>${k.promise_caused ? "🔒 承诺导致" : "结构性"} · ${k.length} 人周期</h4>
              <div class="coalition-members">${k.members.map((m) => `<span class="member-chip">${escapeHtml(m)}</span>`).join("")}</div>
              <p class="hint">${escapeHtml(k.message)}</p>
              <ul class="diff-list">${(k.steps || []).map((s) => `<li>${escapeHtml(s.candidate_id)}：${escapeHtml(aname(s.current_accepter_id))} → <strong>${escapeHtml(s.target_accepter_name || s.target_accepter_id)}</strong>，替换 ${escapeHtml(s.would_replace)}</li>`).join("")}</ul>
            </article>`
          )
          .join("")
      : `<div class="empty">没有检测到长度 2–4 的短周期交换联盟。</div>`;

    const conflicts = r.conflicts || [];
    $("#conflictCountPill").innerHTML = conflicts.length ? `<span class="pill bad">${conflicts.length} 项</span>` : `<span class="pill good">✅ 0 项</span>`;
    $("#conflictsBody").innerHTML = conflicts.length
      ? conflicts.map((c) => `<div class="conflict-card"><strong>⛔ ${escapeHtml(c.code)}</strong><p>${escapeHtml(c.message)}</p>${c.candidate_ids ? `<p class="hint">${c.candidate_ids.map(escapeHtml).join(", ")}</p>` : ""}</div>`).join("")
      : `<div class="empty">✅ 没有容量、资格或承诺冲突。规则可以同时满足。</div>`;
  }

  // -------------------------------------------------------------------------
  // Rounds / event log
  // -------------------------------------------------------------------------
  function renderRoundList() {
    const rounds = state.rounds || [];
    $("#roundList").innerHTML = rounds
      .slice()
      .reverse()
      .map(
        (r) => `<button class="round-item ${roundRecord?.round_no === r.round_no ? "active" : ""}" data-round="${r.round_no}">
          <strong><span>第 ${r.round_no} 轮</span><span class="tag ${r.status === "conflict" ? "critical" : "good"}">${r.status === "conflict" ? "冲突" : "OK"}</span></strong>
          <span>${POLICY_LABELS[r.policy] || r.policy} · ${r.action?.type || "—"}</span>
          <span>${new Date(r.created_at).toLocaleString("zh-CN")}</span>
        </button>`
      )
      .join("");
    $$(".round-item").forEach((btn) =>
      btn.addEventListener("click", () => loadRound(Number(btn.dataset.round)))
    );
  }

  async function loadRound(no) {
    try {
      roundRecord = await api(`/api/rounds/${no}`);
      selectedCandidate = null;
      renderRoundList();
      renderRoundDetail();
      renderKpisAndAlerts();
      renderAssignmentTable();
      renderExplainSelect();
      renderCapacity();
      renderStability();
    } catch (err) {
      toast(err.message, true);
    }
  }

  function renderRoundDetail() {
    if (!roundRecord) return;
    const r = roundRecord.result;
    $("#roundDetailTitle").textContent = `第 ${roundRecord.round_no} 轮 · ${POLICY_LABELS[roundRecord.policy] || roundRecord.policy}`;
    $("#roundMeta").innerHTML = [
      `<span class="pill">${new Date(roundRecord.created_at).toLocaleString("zh-CN")}</span>`,
      `<span class="pill">操作：${escapeHtml(roundRecord.action?.type || "—")}</span>`,
      `<span class="pill ${r.status === "conflict" ? "bad" : "good"}">${r.status === "conflict" ? "存在冲突" : "计算完成"}</span>`,
      `<span class="pill">申请/处理事件 ${r.events?.length || 0}</span>`,
    ].join("");

    const candidateIds = Array.from(new Set((r.events || []).map((e) => e.candidate_id))).sort();
    $("#roundFilters").innerHTML = `
      <select class="input" id="eventOutcomeFilter">
        <option value="all">全部事件</option>
        <option value="rejected">仅拒绝</option>
        <option value="held">暂时/最终持有</option>
        <option value="displaced">被挤出</option>
        <option value="host_assigned">宿主安置</option>
      </select>
      <select class="input" id="eventCandidateFilter"><option value="all">全部学生</option>${candidateIds.map((id) => `<option value="${id}">${escapeHtml(cname(id))}</option>`).join("")}</select>
    `;
    $("#eventOutcomeFilter").value = eventOutcomeFilter;
    $("#eventCandidateFilter").value = eventCandidateFilter;
    $("#eventOutcomeFilter").onchange = (e) => {
      eventOutcomeFilter = e.target.value;
      renderEvents();
    };
    $("#eventCandidateFilter").onchange = (e) => {
      eventCandidateFilter = e.target.value;
      renderEvents();
    };
    renderEvents();
  }

  function renderEvents() {
    const r = roundRecord?.result;
    if (!r) return;
    const events = (r.events || []).filter((e) => {
      if (eventOutcomeFilter !== "all" && e.outcome !== eventOutcomeFilter) return false;
      if (eventCandidateFilter !== "all" && e.candidate_id !== eventCandidateFilter) return false;
      return true;
    });
    $("#eventTable tbody").innerHTML = events.length
      ? events
          .map(
            (e) => `<tr>
        <td><strong>${escapeHtml(cname(e.candidate_id))}</strong></td>
        <td>${escapeHtml(shortName(e.accepter_id))}${e.choice_index != null ? `<br><small class="muted">第 ${e.choice_index + 1} 志愿</small>` : ""}</td>
        <td><span class="tag">${escapeHtml(e.stage || "")}</span></td>
        <td class="event-outcome ${escapeHtml(e.outcome)}">${escapeHtml(outcomeLabel(e.outcome))}</td>
        <td>${escapeHtml(e.reason_text || e.reason_code || "")}</td>
      </tr>`
          )
          .join("")
      : `<tr><td colspan="5" class="empty">当前筛选没有事件。</td></tr>`;
  }

  function outcomeLabel(o) {
    return { held: "暂留/录取", rejected: "拒绝", displaced: "被替代", host_assigned: "宿主安置" }[o] || o;
  }

  // -------------------------------------------------------------------------
  // Preview / actions
  // -------------------------------------------------------------------------
  function initForms() {
    $("#studentSearch").addEventListener("input", renderAssignmentTable);
    $("#explainCandidate").addEventListener("change", loadExplanation);

    $("#addCandidateForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(e.currentTarget);
      const choice_ids = ["choice_1", "choice_2", "choice_3", "choice_4"]
        .map((n) => fd.get(n))
        .filter(Boolean);
      doPreview({
        type: "add_candidate",
        name: fd.get("name"),
        pool: fd.get("pool"),
        maths: fd.get("maths"),
        science: fd.get("science"),
        priority_tier: fd.get("priority_tier"),
        priority_accepter_id: fd.get("priority_accepter_id"),
        choice_ids,
      });
    });

    $("#fillPromiseExample").addEventListener("click", () => {
      const form = $("#addCandidateForm");
      const field = (name) => form.querySelector(`[name="${name}"]`);
      field("name").value = "陆星回";
      field("pool").value = "normal";
      field("maths").value = "90";
      field("science").value = "88";
      field("priority_tier").value = "sibling";
      field("priority_accepter_id").value = "P2";
      field("choice_1").value = "P2";
      field("choice_2").value = "P1";
      field("choice_3").value = "P3";
      field("choice_4").value = "";
      toast("已填入：P2 兄弟姐妹优先示例。提交预览后，整体重算会替换末位多元优先者，保留承诺会标记 promise block。");
    });

    $("#fillChainExample").addEventListener("click", () => {
      const form = $("#withdrawForm");
      form.querySelector('[name="candidate_id"]').value = "C02";
      form.querySelector('[name="reason"]').value = "家庭搬迁，主动撤回";
      toast("已选择 C02。整体重算会出现 C01→P1、C18→P3 的两步链；保留承诺会让 C08 补入 P1，并产生 C01 的承诺阻挡配对。");
    });

    $("#capacityForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(e.currentTarget);
      doPreview({
        type: "edit_capacity",
        accepter_id: fd.get("accepter_id"),
        pool: fd.get("pool"),
        new_capacity: Number(fd.get("new_capacity")),
      });
    });

    $("#withdrawForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(e.currentTarget);
      doPreview({ type: "withdraw", candidate_id: fd.get("candidate_id"), reason: fd.get("reason") });
    });

    $("#commitProtect").addEventListener("click", () => commitPreview("protect_promises"));
    $("#commitFull").addEventListener("click", () => commitPreview("full_recompute"));
    $("#compareCurrent").addEventListener("click", () => doPreview({ type: "compare_only" }));
    $("#resetDemo").addEventListener("click", () => resetScenario("demo"));
    $("#resetCoalition").addEventListener("click", () => resetScenario("coalition"));
  }

  async function doPreview(action) {
    try {
      toast("正在计算两种政策…");
      previewData = await api("/api/preview", { method: "POST", body: JSON.stringify({ action }) });
      lastAction = action;
      renderPreview();
      $("#previewPanel").hidden = false;
      $("#previewPanel").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      toast(err.message, true);
    }
  }

  function renderPreview() {
    if (!previewData) return;
    const { full_recompute: full, protect_promises: prot } = previewData.policies;
    const compareOnly = previewData.compare_only || lastAction?.type === "compare_only";
    $(".commit-actions").hidden = compareOnly;
    $("#previewTitle").textContent = compareOnly
      ? `当前第 ${previewData.round} 轮状态 · 仅比较，不创建新轮次`
      : `预计第 ${previewData.round} 轮 · ${actionDescription(lastAction)}`;
    $("#previewContent").innerHTML = `
      <div class="compare-grid">
        ${policyColumn("full_recompute", full)}
        ${policyColumn("protect_promises", prot, true)}
      </div>
      <div class="grid two-one">
        <div><h4>政策分配差异</h4>${renderPolicyDiff(full, prot)}</div>
        <div><h4>补录链（保留承诺）</h4>${renderChains(prot.chains)}</div>
      </div>
    `;
  }

  function actionDescription(a) {
    if (!a) return "";
    if (a.type === "compare_only") return "当前状态双政策比较";
    if (a.type === "withdraw") return `撤回 ${cname(a.candidate_id)}`;
    if (a.type === "edit_capacity") return `修改 ${a.accepter_id} ${a.pool} 容量为 ${a.new_capacity}`;
    if (a.type === "add_candidate") return `新增学生 ${a.name || ""}`;
    return a.type;
  }

  function policyColumn(policy, r, recommended = false) {
    const st = r.stability || {};
    return `<article class="policy-col ${recommended ? "recommended" : ""}">
      <h4>${POLICY_LABELS[policy]} ${recommended ? '<span class="tag blue">增量政策</span>' : ""}
        <span class="pill ${r.status === "conflict" ? "bad" : st.stable ? "good" : "warn"}">${r.status === "conflict" ? "冲突" : st.stable ? "完全稳定" : st.constrained_stable ? "约束稳定" : "有阻挡"}</span>
      </h4>
      <div class="mini-stats">
        <div class="mini-stat"><b>${r.stats.assigned}</b><span>录取</span></div>
        <div class="mini-stat"><b>${r.stats.unmatched}</b><span>未录取</span></div>
        <div class="mini-stat"><b>${r.stats.blocking_pairs}</b><span>阻挡配对</span></div>
        <div class="mini-stat"><b>${r.stats.conflicts}</b><span>冲突</span></div>
      </div>
      <p class="hint">承诺 ${r.stats.promises}；承诺导致配对 ${r.stats.promise_blocking_pairs}；联盟 ${r.stats.blocking_coalitions}。</p>
      ${renderChains(r.chains, true)}
    </article>`;
  }

  function renderPolicyDiff(full, prot) {
    const ids = new Set([...Object.keys(full.assignments), ...Object.keys(prot.assignments)]);
    const diffs = [];
    ids.forEach((id) => {
      const fa = full.assignments[id]?.accepter_id || null;
      const pa = prot.assignments[id]?.accepter_id || null;
      if (fa !== pa) diffs.push({ id, fa, pa });
    });
    if (!diffs.length) return `<div class="empty">两种政策的最终录取项目完全相同。</div>`;
    return `<ul class="diff-list">${diffs
      .map((d) => `<li><strong>${escapeHtml(cname(d.id))}</strong>：保留承诺为 <b>${escapeHtml(aname(d.pa))}</b>；整体重算为 <b>${escapeHtml(aname(d.fa))}</b></li>`)
      .join("")}</ul>`;
  }

  function renderChains(chainsData, compact = false) {
    const chains = chainsData?.chains || [];
    const moves = chainsData?.moves || [];
    if (!chains.length && !moves.length) return `<div class="empty">没有席位移动。</div>`;
    const chainHtml = chains
      .map(
        (ch) => `<div class="chain">
          <strong>${escapeHtml(ch.root?.label || "补录链")}</strong>
          ${ch.steps
            .map((s) => `<div class="chain-step">${s.admitted_candidate_id ? `<b>${escapeHtml(cname(s.admitted_candidate_id))}</b> 补入 ${escapeHtml(s.program_name)}` : `${escapeHtml(s.program_name)}：${escapeHtml(s.note)}`}${s.previous_program_name ? `<br><small class="muted">${escapeHtml(s.note)}</small>` : s.admitted_candidate_id ? `<br><small class="muted">${escapeHtml(s.note)}</small>` : ""}</div>`)
            .join("")}
        </div>`
      )
      .join("");
    const moveHtml =
      compact || !moves.length
        ? ""
        : `<ul class="diff-list">${moves
            .slice(0, 12)
            .map((m) => `<li>${escapeHtml(cname(m.candidate_id))}：${m.from_name ? escapeHtml(m.from_name) : "未录取"} → <strong>${m.to_name ? escapeHtml(m.to_name) : "未录取/退出"}</strong></li>`)
            .join("")}</ul>`;
    return chainHtml + moveHtml;
  }

  async function commitPreview(policy) {
    if (!lastAction || lastAction.type === "compare_only") return;
    try {
      const rec = await api("/api/commit", { method: "POST", body: JSON.stringify({ action: lastAction, policy }) });
      toast(`已保存第 ${rec.round_no} 轮（${POLICY_LABELS[policy]}）`);
      $("#previewPanel").hidden = true;
      previewData = null;
      lastAction = null;
      await loadState();
      await loadRound(rec.round_no);
      $('.tab[data-tab="rounds"]').click();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function resetScenario(scenario) {
    try {
      state = await api("/api/reset", { method: "POST", body: JSON.stringify({ scenario }) });
      roundRecord = null;
      selectedCandidate = null;
      eventOutcomeFilter = "all";
      eventCandidateFilter = "all";
      toast(scenario === "coalition" ? "已载入承诺联盟构造示例" : "已重置主演示数据");
      renderAll();
      await loadRound(state.current_round);
    } catch (err) {
      toast(err.message, true);
    }
  }

  // -------------------------------------------------------------------------
  // Verification
  // -------------------------------------------------------------------------
  function initVerify() {
    $("#runVerify").addEventListener("click", async () => {
      window._verifyLoaded = true;
      $("#verifySummary").innerHTML = `<div class="empty">正在穷举…</div>`;
      try {
        const report = await api("/api/verify/run", { method: "POST", body: JSON.stringify({ cases: 24 }) });
        renderVerify(report);
      } catch (err) {
        toast(err.message, true);
      }
    });
  }

  async function loadVerification() {
    if (window._verifyLoaded) return;
    window._verifyLoaded = true;
    try {
      const report = await api("/api/verify");
      renderVerify(report);
    } catch (err) {
      $("#verifySummary").innerHTML = `<div class="alert critical">${escapeHtml(err.message)}</div>`;
    }
  }

  function renderVerify(report) {
    $("#verifySummary").innerHTML = `
      <span class="pill ${report.all_passed ? "good" : "bad"}">${report.all_passed ? "✅ 全部通过" : "⛔ 存在失败"}</span>
      <strong>${report.passed}/${report.total} 个用例通过</strong>
      <span class="muted">${escapeHtml(report.note || "")}</span>`;
    $("#verifyCases").innerHTML = report.cases
      .map((c) => {
        const metrics = Object.entries(c)
          .filter(([k]) => !["name", "kind", "pass"].includes(k) && typeof c[k] !== "object")
          .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd>`)
          .join("");
        return `<article class="verify-case ${c.pass ? "" : "fail"}">
          <h4><span>${escapeHtml(c.name || c.kind)}</span><span>${c.pass ? "✅" : "⛔"}</span></h4>
          <dl>${metrics}</dl>
        </article>`;
      })
      .join("");
  }

  // -------------------------------------------------------------------------
  // Tie table
  // -------------------------------------------------------------------------
  function candidateTier(c, a) {
    const order = a.priority_order || ["sibling", "legacy", "diversity"];
    for (const tier of order) {
      if ((c.priorities?.[tier] || []).includes(a.id)) return tier;
    }
    return "none";
  }

  function renderTieTable() {
    if (!state) return;
    const accepters = acceptersById();
    const cmap = candidatesById();
    const rows = [];
    Object.entries(state.tie_vectors || {}).forEach(([aid, table]) => {
      const a = accepters[aid];
      if (!a) return;
      const ranked = Object.keys(table)
        .map((cid) => {
          const c = cmap[cid];
          if (!c) return null;
          const tier = candidateTier(c, a);
          const order = a.priority_order || ["sibling", "legacy", "diversity"];
          const rank = tier === "none" ? order.length : order.indexOf(tier);
          return { aid, cid, tier, rank, draw: table[cid] };
        })
        .filter(Boolean)
        .sort((x, y) => x.rank - y.rank || x.draw - y.draw || x.cid.localeCompare(y.cid));
      const tierGroups = {};
      ranked.forEach((x) => (tierGroups[`${x.rank}:${x.tier}`] = (tierGroups[`${x.rank}:${x.tier}`] || 0) + 1));
      ranked.forEach((x) => rows.push({ ...x, tiedGroup: tierGroups[`${x.rank}:${x.tier}`] > 1 }));
    });
    $("#tieTable tbody").innerHTML = rows
      .map(
        (x) => `<tr>
        <td>${escapeHtml(shortName(x.aid))}</td>
        <td>${escapeHtml(cname(x.cid))}</td>
        <td>${TIER_LABELS[x.tier] || x.tier}${x.tiedGroup ? ' <span class="tag warning">同层并列</span>' : ""}</td>
        <td class="num"><code>${Number(x.draw).toFixed(8)}</code></td>
        <td><code>(${x.rank}, ${Number(x.draw).toFixed(6)}, ${x.cid})</code></td>
      </tr>`
      )
      .join("");
  }

  async function copySeed() {
    try {
      await navigator.clipboard.writeText(state.seed_text);
      toast("种子已复制");
    } catch {
      toast("复制失败，请手动选择", true);
    }
  }

  async function loadState() {
    state = await api("/api/state");
    renderAll();
    try {
      await loadRound(state.current_round);
    } catch {
      /* current_result still renders */
    }
  }

  function init() {
    initTheme();
    initTabs();
    initForms();
    initVerify();
    $("#copySeed").addEventListener("click", copySeed);
    loadState().catch((err) => toast(`初始化失败：${err.message}`, true));
  }

  document.addEventListener("DOMContentLoaded", init);
})();
