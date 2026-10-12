/* HireScope application-tracker logic, shared by the dashboard (window.HSCore) and the tests (Node).
   Mirrors src/jobsearch/tracker/applications.py; tests/test_tracker.py runs both on the same DEMO data. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.HSCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const STAGES = ["applied", "oa", "interviewing", "offer", "rejected", "ghosted", "withdrawn"];
  const STAGE_LABELS = { applied: "Applied", oa: "Online Assessment", interviewing: "Interviewing", offer: "Offer",
    rejected: "Rejected", ghosted: "Ghosted", withdrawn: "Withdrawn" };
  const ACTIVE = ["applied", "oa", "interviewing", "offer"];
  const RESPONSES = ["oa", "interviewing", "offer", "rejected"];
  const NEAR_DUPLICATE = 85;   // default only; the page passes preferences applications.near_duplicate_ratio
  const CSV_COLUMNS = ["company", "title", "track", "stage", "applied_date", "days_since_applied", "next_step",
    "next_step_date", "follow_up", "contact", "referral", "source", "link", "package", "notes"];

  // dates are YYYY-MM-DD strings; arithmetic in UTC days so DST never shifts a date
  const day = (s) => { if (!s || !/^\d{4}-\d{2}-\d{2}/.test(s)) return null; const [y, m, d] = s.slice(0, 10).split("-").map(Number); return Date.UTC(y, m - 1, d) / 864e5; };
  const iso = (n) => new Date(n * 864e5).toISOString().slice(0, 10);

  function followUp(app, today, windowDays) {
    const w = windowDays || [7, 10];
    if (app.deleted || app.stage !== "applied") return null;
    const base = day(app.followed_up) ?? day(app.applied_date);
    if (base == null) return null;
    const t = day(today), due = base + w[0], late = base + w[1];
    return { due: iso(due), state: t < due ? "upcoming" : t <= late ? "due" : "overdue", days: t - base };
  }
  function lastActivity(app) {
    const ds = [day(app.applied_date), day(app.followed_up),
      ...(app.events || []).filter((e) => ["stage", "note", "follow_up"].includes(e.kind)).map((e) => day(e.at))].filter((d) => d != null);
    return ds.length ? Math.max(...ds) : null;
  }
  function ghostSuggestion(app, today, afterDays) {
    if (app.deleted || !["applied", "oa"].includes(app.stage)) return false;
    const last = lastActivity(app);
    return last != null && day(today) - last >= (afterDays ?? 30);
  }

  // rapidfuzz-compatible token_sort_ratio (Indel similarity of sorted tokens)
  const normTitle = (t) => (t || "").toLowerCase().replace(/[^a-z0-9+#]+/g, " ").trim().split(/\s+/).filter(Boolean).join(" ");
  function ratio(a, b) {
    if (!a.length && !b.length) return 100;
    const m = a.length, n = b.length; let prev = new Array(n + 1).fill(0);
    for (let i = 1; i <= m; i++) {
      const cur = new Array(n + 1).fill(0);
      for (let j = 1; j <= n; j++) cur[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
      prev = cur;
    }
    return (200 * prev[n]) / (m + n);
  }
  const tokenSortRatio = (a, b) => ratio(a.split(" ").filter(Boolean).sort().join(" "), b.split(" ").filter(Boolean).sort().join(" "));
  const ckey = (s) => (s || "").toLowerCase().split(/\s+/).filter(Boolean).join(" ");

  function duplicateWarnings(company, title, jobKey, apps, today, windowDays, excludeId, nearRatio) {
    const out = []; const recent = []; const ck = ckey(company); const w = windowDays ?? 30; const near = nearRatio ?? NEAR_DUPLICATE;
    for (const a of apps) {
      if (a.deleted || a.id === excludeId) continue;
      if (jobKey && a.job_key === jobKey) { out.push({ kind: "same_job", app_id: a.id, message: `You already applied to this posting on ${a.applied_date || "?"}` }); continue; }
      if (ckey(a.company) !== ck) continue;
      if (tokenSortRatio(normTitle(a.title), normTitle(title)) >= near) {
        out.push({ kind: "near_duplicate", app_id: a.id, message: `Similar role at ${a.company}: “${a.title}” (applied ${a.applied_date || "?"})` }); continue;
      }
      const d = day(a.applied_date);
      if (d != null && day(today) - d <= w) recent.push(a);
    }
    if (recent.length) out.push({ kind: "same_company", app_id: recent[0].id,
      message: `${recent.length} other application${recent.length > 1 ? "s" : ""} at ${company} in the last ${w} days: ` + recent.slice(0, 3).map((a) => `“${a.title}”`).join("; ") });
    return out;
  }

  function firstResponse(app) {
    const evs = [...(app.events || [])].sort((x, y) => String(x.at || "").localeCompare(String(y.at || "")));
    for (const e of evs) if (e.kind === "stage" && RESPONSES.includes(e.to) && day(e.at) != null) return [e.to, day(e.at)];
    if (RESPONSES.includes(app.stage)) return [app.stage, null];
    return null;
  }
  function furthest(app) {
    const reached = new Set([...(app.events || []).filter((e) => e.kind === "stage").map((e) => e.to), app.stage]);
    if (reached.has("offer")) return "offer";
    if (reached.has("interviewing")) return "interview";
    if (reached.has("oa")) return "OA";
    return reached.has("rejected") ? "rejected" : "no response";
  }
  const median = (xs) => { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b); const h = s.length >> 1; return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2; };
  const r1 = (x) => Math.round(x * 10) / 10;

  function trackStats(apps, today, weeks) {
    const W = weeks || 12;
    const live = apps.filter((a) => !a.deleted);
    const t = day(today); const dow = (new Date(t * 864e5).getUTCDay() + 6) % 7;   // Monday = 0
    const start = t - dow - 7 * (W - 1);
    const perWeek = new Array(W).fill(0);
    for (const a of live) { const d = day(a.applied_date); if (d != null && d >= start) perWeek[Math.min(W - 1, Math.floor((d - start) / 7))]++; }
    const responded = live.filter((a) => firstResponse(a));
    const outcomes = live.map(furthest);
    const days = [];
    for (const a of responded) { const fr = firstResponse(a), ap = day(a.applied_date); if (fr && fr[1] != null && ap != null) days.push(Math.max(0, fr[1] - ap)); }
    const n = live.length; const rate = (k) => (n ? r1((100 * k) / n) : null);
    return { total: n, active: live.filter((a) => ACTIVE.includes(a.stage)).length, per_week: perWeek, week_start: iso(start),
      response_rate: rate(responded.length), oa_rate: rate(outcomes.filter((o) => ["OA", "interview", "offer"].includes(o)).length),
      interview_rate: rate(outcomes.filter((o) => ["interview", "offer"].includes(o)).length),
      offers: outcomes.filter((o) => o === "offer").length, median_days_to_response: median(days) };
  }
  function outcomeBreakdown(apps, dim) {
    const groups = new Map();
    for (const a of apps) if (!a.deleted) { const k = String(dim(a) ?? "Unknown") || "Unknown"; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(furthest(a)); }
    return [...groups.entries()].sort((x, y) => y[1].length - x[1].length).map(([k, outs]) => {
      const n = outs.length, resp = outs.filter((o) => o !== "no response").length;
      return { group: k, n, responses: resp, response_rate: r1((100 * resp) / n), interviews: outs.filter((o) => o === "interview" || o === "offer").length, offers: outs.filter((o) => o === "offer").length };
    });
  }

  function csvCell(v) {
    let s = v == null ? "" : String(v);
    if (/^[=+\-@]/.test(s)) s = "'" + s;                          // never let a spreadsheet run a formula
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }
  function toCsv(apps, today, windowDays) {
    const rows = [CSV_COLUMNS.join(",")];
    for (const a of [...apps].filter((x) => !x.deleted).sort((x, y) => String(x.applied_date || "").localeCompare(String(y.applied_date || "")))) {
      const d = day(a.applied_date); const fu = followUp(a, today, windowDays);
      const row = Object.assign({}, a, { stage: STAGE_LABELS[a.stage] || a.stage, days_since_applied: d != null ? day(today) - d : "", follow_up: fu ? `${fu.state} ${fu.due}` : "" });
      rows.push(CSV_COLUMNS.map((c) => csvCell(row[c])).join(","));
    }
    return rows.join("\r\n") + "\r\n";
  }

  // ---- mutations (return new objects; the caller persists) ----
  const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  const newId = () => "a" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  function newApp(fields, at) {
    const t = at || nowIso();
    const a = Object.assign({ id: newId(), job_key: "", company: "", title: "", track: "full_time", link: "", source: "", applied_date: t.slice(0, 10),
      stage: "applied", next_step: "", next_step_date: "", contact: "", referral: "", notes: "", package: "", followed_up: "", jd: "", job: {},
      deleted: false, created_at: t, updated_at: t, events: [] }, fields || {});
    a.events = [{ at: t, kind: "created", from: null, to: a.stage, note: null }];
    return a;
  }
  function moveStage(app, to, at) {
    if (!STAGES.includes(to)) throw new Error(`unknown stage ${to}`);
    if (app.stage === to) return app;
    const t = at || nowIso();
    return Object.assign({}, app, { stage: to, updated_at: t, events: [...(app.events || []), { at: t, kind: "stage", from: app.stage, to, note: null }] });
  }
  function logFollowUp(app, dateStr, at) {
    const t = at || nowIso();
    return Object.assign({}, app, { followed_up: dateStr, updated_at: t, events: [...(app.events || []), { at: t, kind: "follow_up", from: null, to: null, note: `Followed up ${dateStr}` }] });
  }
  function edit(app, patch, at) {
    const t = at || nowIso();
    return Object.assign({}, app, patch, { updated_at: t });
  }
  // newer updated_at wins; timeline events are append-only, so they are unioned
  function mergeApp(a, b) {
    if (!a) return b; if (!b) return a;
    const win = String(b.updated_at || "") > String(a.updated_at || "") ? b : a;
    const seen = new Set(); const events = [];
    for (const e of [...(a.events || []), ...(b.events || [])]) {
      const k = `${e.at}|${e.kind}|${e.to || ""}|${e.note || ""}`;
      if (!seen.has(k)) { seen.add(k); events.push(e); }
    }
    events.sort((x, y) => String(x.at).localeCompare(String(y.at)));
    return Object.assign({}, win, { events, jd: win.jd || (win === a ? b.jd : a.jd) || "" });
  }

  return { STAGES, STAGE_LABELS, ACTIVE, CSV_COLUMNS, followUp, lastActivity, ghostSuggestion, duplicateWarnings, tokenSortRatio, normTitle,
    firstResponse, furthest, trackStats, outcomeBreakdown, toCsv, newApp, moveStage, logFollowUp, edit, mergeApp, day, iso };
});
