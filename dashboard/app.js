/* Job Search dashboard. Data comes from data.js (window.JOBS / window.META).
   All job text is rendered with textContent: JD content is data, never markup. */
(function () {
  "use strict";

  const JOBS = Array.isArray(window.JOBS) ? window.JOBS : [];
  const META = window.META || {};
  const STATUS_KEY = "jobsearch.statuses.v1";
  const THEME_KEY = "jobsearch.theme";
  const STATUSES = ["", "interested", "shortlisted", "package exported", "applied", "interviewing", "offer", "rejected", "skipped"];
  const SKIP_REASONS = ["", "not interested in role", "location", "too senior", "missing skills", "company", "compensation", "visa/sponsorship", "other"];

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, props, ...kids) => {
    const n = document.createElement(tag);
    if (props) for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "style") n.style.cssText = v;  // CSSOM: allowed under the page CSP
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat()) if (k != null && k !== false) n.append(k instanceof Node ? k : document.createTextNode(String(k)));
    return n;
  };
  const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);

  // ---------- storage ----------
  const store = {
    get(key, dflt) { try { const v = localStorage.getItem(key); return v == null ? dflt : JSON.parse(v); } catch (e) { return dflt; } },
    set(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) { /* private mode: ignore */ } },
  };
  let statuses = store.get(STATUS_KEY, {});   // key -> {status, skip_reason, updated_at}
  const jobKey = (j) => `${j.company}::${j.source}::${j.job_id}`;
  function setStatus(j, patch) {
    const k = jobKey(j);
    statuses[k] = Object.assign({ company: j.company, title: j.title, job_id: j.job_id, source: j.source },
      statuses[k] || {}, patch, { updated_at: new Date().toISOString() });
    store.set(STATUS_KEY, statuses);
  }

  // ---------- theme ----------
  function applyTheme(t) { if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme"); }
  applyTheme(store.get(THEME_KEY, null));
  $("#theme-toggle").addEventListener("click", () => {
    const dark = document.documentElement.getAttribute("data-theme") === "dark" ||
      (!document.documentElement.getAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
    const next = dark ? "light" : "dark";
    store.set(THEME_KEY, next); applyTheme(next);
  });

  // ---------- helpers ----------
  function toast(msg) {
    const t = el("div", { class: "toast", role: "status", text: msg });
    document.body.append(t); setTimeout(() => t.remove(), 2200);
  }
  function download(filename, text, type) {
    const url = URL.createObjectURL(new Blob([text], { type: type || "text/plain;charset=utf-8" }));
    const a = el("a", { href: url, download: filename });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fall back */ }
    const ta = el("textarea", { style: "position:fixed;opacity:0" }); ta.value = text;
    document.body.append(ta); ta.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove(); return ok;
  }
  const fmtTime = (iso) => {
    try {
      const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: META.timezone || undefined, year: "numeric", month: "2-digit",
        day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
      return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
    } catch (e) { return iso; }
  };
  const todayInTz = () => { try { return new Intl.DateTimeFormat("en-CA", { timeZone: META.timezone || undefined }).format(new Date()); } catch (e) { return new Date().toISOString().slice(0, 10); } };
  const RED_FLAG = { sponsorship: "No sponsorship", citizenship: "US citizenship required", clearance: "Clearance required" };
  const redFlags = (j) => Object.entries(RED_FLAG).filter(([k, v]) => (j.flags || {})[k] === v).map(([, v]) => v);

  // ---------- header ----------
  function renderHeader() {
    const s = META.summary || {};
    const meta = $("#run-meta");
    if (!META.last_run) { meta.append("No run yet — run ", el("code", { text: "scripts/run.sh" })); return; }
    meta.append(
      el("span", null, "Last run ", el("b", { text: fmtTime(META.last_run) })),
      el("span", null, el("b", { text: `${s.sources_ok} of ${s.sources_checked}` }), " companies/sources checked successfully this run",
        s.sources_skipped ? el("span", { class: "muted", text: ` (${s.sources_skipped} not due)` }) : null),
      el("span", null, "Jobs found ", el("b", { text: s.jobs_found })),
      el("span", null, "New ", el("b", { text: s.new }), " · Updated ", el("b", { text: s.updated }), " · Closed ", el("b", { text: s.closed })),
      el("span", null, "Strong ", el("b", { text: s.strong })),
      el("span", null, el("a", { href: "file://" + encodeURI(META.log_path || ""), text: "run log" })),
      el("span", { class: "muted", text: `resume ${META.resume_version_hash || "?"}` }),
    );
    $("#disclaimer").textContent = META.disclaimer || "";
    const w = $("#warnings");
    const fails = s.failures || [], manual = s.manual || [], partial = s.partial || [];
    if (fails.length || manual.length || partial.length) {
      const det = el("details", { class: "warn" }, el("summary", { text:
        `Coverage incomplete: ${fails.length} failed, ${manual.length} need a manual check, ${partial.length} partial — see the Coverage tab` }));
      const ul = el("ul");
      for (const f of fails) ul.append(el("li", { text: `${f.company} (${f.source}) failed — ${f.error}` }));
      for (const m of manual) ul.append(el("li", null, `${m.company}: manual check needed `, safeUrl(m.url) ? el("a", { href: m.url, target: "_blank", rel: "noopener noreferrer", text: m.url }) : ""));
      for (const p of partial) ul.append(el("li", { text: `${p.company} (${p.source}) partial — ${p.why}` }));
      det.append(ul); w.append(det);
    }
    if (META.partial_run) w.append(el("div", { class: "warn", text: "Last run was limited to one source/company; other companies were not re-checked." }));
    if (/example/i.test(META.resume_path || "")) w.append(el("div", { class: "warn", text: `Scored against ${META.resume_path} (DEMO resume). Add inputs/master_resume.md for real matches.` }));
  }

  // ---------- track switch ----------
  const TRACK_KEY = "jobsearch.track";
  const TRACK_LABEL = { coop_intern: "Co-op/Intern", full_time: "Full-time", unclear: "Unclear" };
  let activeTrack = (location.hash === "#coop" && "coop_intern") || (location.hash === "#fulltime" && "full_time") || store.get(TRACK_KEY, "both");
  const trackOk = (j) => activeTrack === "both" || j.track === activeTrack || j.track === "unclear";
  function trackBadge(j) {
    const t = j.track || "full_time";
    return el("span", { class: `badge track ${t}`, title: j.track_evidence || "", text: t === "unclear" ? "? Unclear" : TRACK_LABEL[t] });
  }
  function renderTrackBar() {
    for (const b of document.querySelectorAll(".track-btn")) {
      const t = b.dataset.track;
      b.setAttribute("aria-checked", String(t === activeTrack));
      const n = JOBS.filter((j) => !j.filtered && (t === "both" || j.track === t || j.track === "unclear")).length;
      b.querySelector(".n").textContent = n;
    }
  }
  for (const b of document.querySelectorAll(".track-btn")) b.addEventListener("click", () => {
    activeTrack = b.dataset.track; store.set(TRACK_KEY, activeTrack);
    history.replaceState(null, "", activeTrack === "coop_intern" ? "#coop" : activeTrack === "full_time" ? "#fulltime" : "#");
    renderTrackBar(); renderTabs(); render();
  });

  // ---------- add job by URL ----------
  $("#add-job").addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = $("#add-url").value.trim();
    if (!/^https?:\/\//i.test(url)) { toast("Enter a full http(s) URL"); return; }
    const cmd = `cd ${META.repo_root || "<repo>"} && ./scripts/add_url.sh ${JSON.stringify(url)}`;
    const ok = await copyText(cmd);
    toast(ok ? "Command copied — paste it in Terminal" : cmd);
  });

  // ---------- coverage ----------
  function renderCoverage() {
    const box = $("#coverage"); box.textContent = "";
    const counts = { ok: 0, failed: 0, manual: 0, skipped: 0 };
    for (const r of COVERAGE) counts[r.status] = (counts[r.status] || 0) + 1;
    box.append(el("p", null, el("b", { text: `${counts.ok} ok` }), ` · ${counts.failed} failed · ${counts.manual} manual check · ${counts.skipped} not due this run (checked on their tier schedule)`));
    const t = el("table", { class: "jobs coverage" });
    t.append(el("thead", null, el("tr", null, ...["Company", "Status", "Source", "Detected via", "Last checked", "Open roles", "Notes"].map((h) => el("th", { text: h })))));
    const tb = el("tbody");
    const order = { failed: 0, manual: 1, ok: 2, skipped: 3 };
    for (const r of [...COVERAGE].sort((a, b) => (order[a.status] - order[b.status]) || a.company.localeCompare(b.company))) {
      const st = el("span", { class: `badge cov-${r.status}`, text: { ok: "OK", failed: "Failed", manual: "Manual check", skipped: "Not due" }[r.status] || r.status });
      const link = safeUrl(r.url);
      tb.append(el("tr", null,
        el("td", null, r.company, r.my_company ? el("span", { class: "badge", text: "My Company" }) : null, r.tier ? el("span", { class: "muted", text: ` · tier ${r.tier}` }) : null),
        el("td", null, st, r.partial ? el("div", { class: "muted", text: `partial: ${r.partial}` }) : null),
        el("td", { text: r.source }),
        el("td", { text: r.detected_via || (r.origin === "seed" ? "seed list" : r.origin || "") }),
        el("td", { text: r.last_checked ? fmtTime(r.last_checked) : "—" }),
        el("td", { text: r.open_roles ?? "—" }),
        el("td", null, r.error ? el("div", { class: "err", text: r.error }) : null, r.notes ? el("div", { class: "muted", text: r.notes }) : null,
          link ? el("a", { href: link, target: "_blank", rel: "noopener noreferrer", text: r.status === "manual" ? "Check manually" : "link" }) : null),
      ));
    }
    t.append(tb); box.append(t);
  }

  function render() {
    const cov = activeTab === "coverage";
    $("#coverage").hidden = !cov;
    document.querySelector("table.jobs:not(.coverage)").hidden = cov;
    document.querySelector(".controls").hidden = cov;
    if (cov) { $("#empty").hidden = true; $("#filtered-summary").hidden = true; renderCoverage(); return; }
    const fs = $("#filtered-summary");
    fs.hidden = activeTab !== "filtered";
    if (!fs.hidden) {
      fs.textContent = `${META.filtered_count || 0} jobs filtered; ${META.filtered_listed || 0} listed here (software-relevant first). By reason: ` +
        Object.entries(META.filtered_reasons || {}).map(([k, v]) => `${k} (${v})`).join(" · ");
    }
    renderRows();
  }

  // ---------- tabs ----------
  const days = [...new Set(JOBS.filter((j) => !j.filtered).map((j) => j.day))].sort().reverse();
  const tabs = [{ id: "all", label: "All open" }, ...days.map((d) => ({ id: "day:" + d, label: d })),
    { id: "filtered", label: "Filtered" }, { id: "coverage", label: "Coverage" }];
  let activeTab = "all";
  const COVERAGE = Array.isArray(META.coverage) ? META.coverage : [];
  function tabCount(id) {
    if (id === "coverage") return COVERAGE.length;
    const inTrack = JOBS.filter(trackOk);
    if (id === "all") return inTrack.filter((j) => !j.filtered).length;
    if (id === "filtered") return inTrack.filter((j) => j.filtered).length;
    return inTrack.filter((j) => !j.filtered && "day:" + j.day === id).length;
  }
  function renderTabs() {
    const nav = $("#tabs"); nav.textContent = "";
    for (const t of tabs) {
      nav.append(el("button", { role: "tab", "aria-selected": String(t.id === activeTab), onclick: () => { activeTab = t.id; renderTabs(); render(); } },
        t.label, el("span", { class: "n", text: tabCount(t.id) })));
    }
  }

  // ---------- filters ----------
  const uniq = (xs) => [...new Set(xs.filter((x) => x != null && x !== ""))].sort();
  function fillSelect(id, label, values, fmt) {
    const s = $(id); s.append(el("option", { value: "", text: label }));
    for (const v of values) s.append(el("option", { value: String(v), text: fmt ? fmt(v) : String(v) }));
    s.addEventListener("change", renderRows);
  }
  fillSelect("#f-company", "All companies", uniq(JOBS.map((j) => j.company)));
  fillSelect("#f-tier", "All tiers", uniq(JOBS.map((j) => j.tier)), (t) => `Tier ${t}`);
  fillSelect("#f-label", "All labels", ["Strong", "Stretch", "Weak"]);
  fillSelect("#f-type", "All types", uniq(JOBS.map((j) => j.employment_type)));
  fillSelect("#f-location", "Any location/mode", ["remote", "hybrid", "onsite", "unknown"]);
  fillSelect("#f-status", "Any status", ["(none)", ...STATUSES.filter(Boolean)]);
  fillSelect("#f-flag", "Any flags", ["Has red flag", "No red flags", "Broken link", ...Object.values(RED_FLAG)]);
  fillSelect("#f-mine", "All companies (any list)", ["My Companies only"]);
  fillSelect("#f-level", "Any level", ["intern", "entry", "mid", "senior", "unknown"]);
  $("#search").addEventListener("input", renderRows);

  function matches(j) {
    if (!trackOk(j)) return false;
    if (activeTab === "filtered" ? !j.filtered : j.filtered) return false;
    if (activeTab.startsWith("day:") && "day:" + j.day !== activeTab) return false;
    const v = (id) => $(id).value;
    if (v("#f-company") && j.company !== v("#f-company")) return false;
    if (v("#f-tier") && String(j.tier) !== v("#f-tier")) return false;
    if (v("#f-label") && j.label !== v("#f-label")) return false;
    if (v("#f-type") && j.employment_type !== v("#f-type")) return false;
    if (v("#f-location") && j.work_mode !== v("#f-location")) return false;
    if (v("#f-mine") && !j.my_company) return false;
    if (v("#f-level") && j.level !== v("#f-level")) return false;
    const st = (statuses[jobKey(j)] || {}).status || "";
    if (v("#f-status") === "(none)" ? st !== "" : v("#f-status") && st !== v("#f-status")) return false;
    const rf = redFlags(j), fv = v("#f-flag");
    if (fv === "Has red flag" && !rf.length) return false;
    if (fv === "No red flags" && rf.length) return false;
    if (fv === "Broken link" && j.link_status !== "broken") return false;
    if (fv && !["Has red flag", "No red flags", "Broken link"].includes(fv) && !rf.includes(fv)) return false;
    const q = $("#search").value.trim().toLowerCase();
    if (q) {
      const hay = [j.title, j.company, j.location, (j.skills || []).join(" "), (j.filter_reasons || []).join(" ")].join(" ").toLowerCase();
      if (!q.split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    return true;
  }

  // ---------- rows ----------
  const expanded = new Set();
  const latestDay = days[0] || todayInTz();

  function badges(j) {
    const out = [];
    if (j.job_status === "updated") out.push(el("span", { class: "badge updated", text: "Updated" }));
    else if (j.day === latestDay) out.push(el("span", { class: "badge new", text: "New" }));  // first seen on the latest run day
    else out.push(el("span", { class: "badge", text: "Seen" }));
    for (const f of redFlags(j)) out.push(el("span", { class: "badge flag", text: f }));
    if (j.link_status === "broken") out.push(el("span", { class: "badge flag", title: "Apply link returned 404/closed when last checked", text: "Broken link" }));
    if (j.my_company) out.push(el("span", { class: "badge", text: "My Company" }));
    return out;
  }

  function statusCell(j) {
    const cur = statuses[jobKey(j)] || {};
    const sel = el("select", { "aria-label": "My status", onclick: (e) => e.stopPropagation() });
    for (const s of STATUSES) sel.append(el("option", { value: s, text: s || "—", selected: s === (cur.status || "") }));
    const reason = el("select", { "aria-label": "Skip reason", hidden: cur.status !== "skipped", onclick: (e) => e.stopPropagation() });
    for (const r of SKIP_REASONS) reason.append(el("option", { value: r, text: r || "skip reason…", selected: r === (cur.skip_reason || "") }));
    sel.addEventListener("change", () => {
      setStatus(j, { status: sel.value, skip_reason: sel.value === "skipped" ? reason.value : "" });
      reason.hidden = sel.value !== "skipped";
    });
    reason.addEventListener("change", () => setStatus(j, { skip_reason: reason.value }));
    return el("td", { class: "status-cell" }, sel, reason);
  }

  function actions(j) {
    const box = el("div", { class: "actions" });
    if (j.package) {
      box.append(el("button", { type: "button", class: "primary", title: "Download job package (.md) and copy it to the clipboard", onclick: async (e) => {
        e.stopPropagation();
        download(j.package_filename, j.package, "text/markdown;charset=utf-8");
        const ok = await copyText(j.package);
        setStatus(j, { status: "package exported" });
        renderRows();
        toast(ok ? "Package downloaded and copied" : "Package downloaded (clipboard unavailable)");
      } }, "Generate Resume"));
    }
    const apply = safeUrl(j.apply_url);
    if (apply) box.append(el("a", { href: apply, target: "_blank", rel: "noopener noreferrer", onclick: (e) => e.stopPropagation() }, "Open Application"));
    const li = safeUrl(j.linkedin_url);
    if (li) box.append(el("a", { href: li, target: "_blank", rel: "noopener noreferrer", onclick: (e) => e.stopPropagation() }, "LinkedIn search"));
    return el("td", null, box);
  }

  function bar(v) { return el("div", { class: "bar" }, el("span", { style: `width:${Math.max(0, Math.min(100, v || 0))}%` })); }
  function partsTable(title, block) {
    const t = el("table", { class: "bd" });
    t.append(el("tr", null, el("td", { class: "k" }, el("b", { text: title })), el("td", { class: "v", text: block.score == null ? "?" : Math.round(block.score) }), el("td", null, bar(block.score)), el("td", { class: "muted", text: `weight ${block.weight}` })));
    for (const [k, p] of Object.entries(block.parts || {})) {
      t.append(el("tr", null, el("td", { class: "k", text: k.replace("_", " ") }), el("td", { class: "v", text: p.score == null ? "?" : Math.round(p.score) }), el("td", null, bar(p.score)), el("td", { class: "muted", text: p.detail })));
    }
    return t;
  }
  const list = (items, empty) => items && items.length ? el("ul", null, items.map((x) => el("li", { text: x }))) : el("p", { class: "muted", text: empty || "None stated." });

  function detail(j) {
    const td = el("td", { colspan: "6" });
    if (j.filtered) {
      td.append(el("h3", { text: "Why this job is hidden" }), list(j.filter_reasons),
        el("p", { class: "muted", text: "Hard filters are set in inputs/preferences.yaml (rules: hard|soft)." }));
      return el("tr", { class: "detail" }, td);
    }
    const bd = j.breakdown || {};
    const grid = el("div", { class: "detail-grid" });

    const scoreBox = el("div", null, el("h3", { text: `Score breakdown — ${j.score} (${j.label})` }));
    if (bd.qualification) scoreBox.append(partsTable("Qualification", bd.qualification));
    if (bd.preference) scoreBox.append(partsTable("Preference", bd.preference));
    if ((bd.penalties || []).length) scoreBox.append(el("h3", { text: "Penalties" }), list(bd.penalties.map((p) => `−${p.points}: ${p.reason}`)));
    if ((bd.uncertain || []).length) scoreBox.append(el("p", { class: "muted", text: `Uncertain (no data, excluded from average): ${bd.uncertain.join(", ")}` }));
    scoreBox.append(el("p", { class: "muted", text: META.disclaimer }));
    grid.append(scoreBox);

    const ev = el("div", null, el("h3", { text: "Evidence from your resume" }));
    if (j.explanation) ev.append(el("p", { text: j.explanation }));
    for (const e of bd.evidence || []) ev.append(el("div", { class: "evidence" }, el("div", { class: "muted", text: `JD: ${e.jd}` }), el("div", null, `${e.section}: `, el("q", { text: e.resume }), el("span", { class: "muted", text: ` (sim ${e.similarity})` }))));
    ev.append(el("h3", { text: "Gaps" }), list(j.gaps, "None identified."));
    ev.append(el("h3", { text: "Skills in JD" }), el("p", null, (j.skills || []).map((s) => el("span", { class: "badge" + ((bd.matched_skills || []).includes(s) ? " new" : ""), text: s }))));
    grid.append(ev);

    const reqs = el("div", null,
      el("h3", { text: "Minimum requirements" }), list(j.min_reqs),
      el("h3", { text: "Preferred requirements" }), list(j.pref_reqs));
    grid.append(reqs);

    const flags = el("div", null, el("h3", { text: "Flags (only when explicitly stated)" }));
    for (const k of ["sponsorship", "citizenship", "clearance"]) {
      const ev2 = (j.flag_evidence || {})[k];
      flags.append(el("div", null, el("b", { text: `${k}: ` }), (j.flags || {})[k] || "Not stated", ev2 ? el("div", { class: "muted evidence", text: `“${ev2}”` }) : null));
    }
    const facts = [
      `Years required: ${j.yoe_required == null ? "Not stated" : j.yoe_required + "+"}` + (j.yoe_preferred != null ? ` (preferred: ${j.yoe_preferred}+)` : ""),
      `Degree: ${j.degree || "Not stated"}`,
      `Graduation years: ${(j.grad_years || []).join(", ") || "Not stated"}`,
      `Salary: ${j.salary ? `${j.salary.currency || ""} ${j.salary.min.toLocaleString()}–${j.salary.max.toLocaleString()}` : "Not stated"}`,
      `Track: ${TRACK_LABEL[j.track] || j.track} — ${j.track_evidence || ""}`,
      `Level: ${j.level} — ${j.level_evidence || ""}`,
      `Terms: ${(j.terms || []).join(", ") || "Not stated"}`,
      `Employment type: ${j.employment_type}`,
      `Apply link: ${j.link_status || "not checked"}`,
      `Source: ${j.source} · Job ID ${j.job_id} · first seen ${fmtTime(j.first_seen)}`,
    ];
    flags.append(el("h3", { text: "Facts" }), list(facts));
    grid.append(flags);

    td.append(grid);
    const jdText = (j.package || "").split("\n## Full JD\n")[1];
    if (jdText) {
      const det = el("details", null, el("summary", { text: "Full job description" }), el("pre", { class: "jd", text: jdText.split("\n## Minimum requirements\n")[0].trim() }));
      td.append(det);
    }
    return el("tr", { class: "detail" }, td);
  }

  function renderRows() {
    const tbody = $("#rows"); tbody.textContent = "";
    const rows = JOBS.filter(matches).sort((a, b) => (b.score ?? -1) - (a.score ?? -1) || a.company.localeCompare(b.company));
    $("#count").textContent = `${rows.length} job${rows.length === 1 ? "" : "s"}`;
    $("#empty").hidden = rows.length > 0;
    const frag = document.createDocumentFragment();
    for (const j of rows.slice(0, 1000)) {
      const id = jobKey(j);
      const tr = el("tr", { class: "job", onclick: () => { expanded.has(id) ? expanded.delete(id) : expanded.add(id); renderRows(); } },
        el("td", null, j.filtered ? el("span", { class: "muted", text: "—" }) : [el("div", { class: "score", text: Math.round(j.score) }), el("span", { class: `label ${j.label}`, text: j.label })]),
        el("td", null, el("div", { class: "role-title", text: j.title }), el("div", { class: "role-sub" }, trackBadge(j), ` ${j.company}${j.tier ? ` · tier ${j.tier}` : ""} · ${j.level !== "unknown" ? j.level : "level not stated"}${(j.terms || []).length ? " · " + j.terms.join(", ") : ""}`, ...badges(j)),
          j.filtered ? el("div", { class: "role-sub", text: (j.filter_reasons || []).join("; ") }) : null),
        el("td", null, j.location, el("div", { class: "role-sub", text: j.work_mode })),
        el("td", { text: j.posted_date }),
        statusCell(j),
        actions(j),
      );
      frag.append(tr);
      if (expanded.has(id)) frag.append(detail(j));
    }
    tbody.append(frag);
    if (rows.length > 1000) tbody.append(el("tr", null, el("td", { colspan: "6", class: "muted", text: `Showing first 1000 of ${rows.length}. Narrow the filters.` })));
  }

  // ---------- export statuses ----------
  $("#export-statuses").addEventListener("click", () => {
    const payload = { exported_at: new Date().toISOString(), format: 1, statuses };
    download("jobsearch-statuses.json", JSON.stringify(payload, null, 2), "application/json");
  });

  renderHeader();
  renderTrackBar();
  renderTabs();
  render();
})();
