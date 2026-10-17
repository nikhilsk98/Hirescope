// UI review: screenshots of every view (light + dark, 13" laptop width + a narrow window) and an audit of each one:
// console errors, horizontal overflow, cut-off text, WCAG AA contrast of rendered text, render timings.
//   node tests/browser/ui_review.mjs <file:///…/dashboard/index.html> <out dir> [only-prefix]
// Read-only: a scratch browser profile; hirescope:// links are captured (never opened); seeded Bucket / queue state
// lives only in that profile.
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { launch } from "./cdp.mjs";

const [, , url, out, only = ""] = process.argv;
mkdirSync(out, { recursive: true });

// before any page script: capture launcher links; optional "launcher not installed"
const PRE = (installed) => `window.__links = []; window.__hsLaunch = (u) => window.__links.push(u);
  try { localStorage.setItem("jobsearch.launcherTip.v1", "true"); localStorage.setItem("jobsearch.tipDismissed.v2", "true"); } catch (e) {}
  ${installed ? "" : `Object.defineProperty(window, "HS_LAUNCHER", { get: () => ({ installed: false }), set() {} });`}`;

// rendered-text audit in the page: contrast (WCAG 2.1 AA), overflow, clipped text
const AUDIT = `(() => {
  const lum = (c) => { const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const parse = (s) => { const m = s.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const bgOf = (el) => { let layers = []; for (let n = el; n; n = n.parentElement) { const c = parse(getComputedStyle(n).backgroundColor); if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; } }
    let base = [255, 255, 255]; const root = parse(getComputedStyle(document.body).backgroundColor); if (root && root[3] >= 1) base = root.slice(0, 3);
    for (const c of layers.reverse()) base = base.map((v, i) => v * (1 - c[3]) + c[i] * c[3]); return base; };
  const fails = []; const seen = new Set();
  for (const el of document.querySelectorAll("body *")) {
    if (!el.childNodes.length || ![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height || r.bottom < 0 || r.top > innerHeight * 3) continue;
    const cs = getComputedStyle(el); if (cs.visibility === "hidden" || +cs.opacity === 0) continue;
    if (el.closest("[hidden], [inert] .skeleton, svg")) continue;
    let grad = false; for (let n = el; n && !grad; n = n.parentElement) grad = getComputedStyle(n).backgroundImage.includes("gradient");
    if (grad) continue;              // a gradient background can't be measured here (tests/test_dashboard.py checks those tokens)
    const fg = parse(cs.color); if (!fg) continue; const bg = bgOf(el);
    const f = fg.slice(0, 3).map((v, i) => v * fg[3] + bg[i] * (1 - fg[3]));
    const L1 = lum(f), L2 = lum(bg); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const size = parseFloat(cs.fontSize), bold = +cs.fontWeight >= 700; const large = size >= 24 || (bold && size >= 18.66);
    const disabled = el.closest("button[disabled], .disabled, [aria-disabled=true]");
    if (!disabled && ratio < (large ? 3 : 4.5)) {
      const key = el.className + "|" + cs.color + "|" + cs.backgroundColor; if (seen.has(key)) continue; seen.add(key);
      fails.push({ text: el.textContent.trim().slice(0, 40), cls: String(el.className).slice(0, 40), tag: el.tagName, ratio: Math.round(ratio * 100) / 100, color: cs.color, bg: "rgb(" + bg.map(Math.round).join(",") + ")" });
    }
  }
  const overflowX = document.documentElement.scrollWidth > innerWidth + 1;
  const clipped = [];
  for (const el of document.querySelectorAll(".btn, .chip, .track-tab, .view-btn, .tile, th, .kbd")) {
    const r = el.getBoundingClientRect(); if (!r.width) continue;
    if (el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflow !== "visible" && !getComputedStyle(el).textOverflow.includes("ellipsis")) clipped.push((el.textContent || "").trim().slice(0, 40));
    if (r.right > innerWidth + 1 && !el.closest(".table-wrap, .board, .popover, #panel, #drawer, .palette, dialog")) clipped.push("offscreen: " + (el.textContent || "").trim().slice(0, 40));
  }
  return { contrast: fails.slice(0, 25), overflowX, clipped: [...new Set(clipped)].slice(0, 15) };
})()`;

const SHOTS = [];   // [name, async (p) => setup]
const view = (id) => `(() => { const b = [...document.querySelectorAll(".view-btn")].find((x) => x.querySelector(".ic") && x.textContent.includes(${JSON.stringify(id)})); if (b) b.click(); return !!b; })()`;
const tab = (key) => async (p) => { await p.key(key); await p.sleep(400); };
SHOTS.push(["tab-coop", tab("1")], ["tab-fulltime", tab("2")]);
SHOTS.push(["tab-bucket", async (p) => {
  await p.eval(`(() => { const now = new Date().toISOString(); const ids = window.JOBS.filter((j) => !j.filtered).slice(0, 6); const b = {};
    ids.forEach((j, i) => { b[j.id] = { in: true, added_at: now, updated_at: now, snap: { id: j.id, company: j.company, title: j.title, track: j.track, score: j.score, deadline: j.deadline, job_status: i === 5 ? "closed" : "open" } }; });
    localStorage.setItem("jobsearch.bucket.v1", JSON.stringify(b)); })()`);
  await p.reload(); await p.key("3"); await p.sleep(400);
}]);
SHOTS.push(["tab-bucket-empty", async (p) => { await p.eval(`localStorage.removeItem("jobsearch.bucket.v1")`); await p.reload(); await p.key("3"); await p.sleep(300); }]);
SHOTS.push(["tab-resume-builder", tab("4")]);
for (const [name, label] of [["view-last24", "Last 24h"], ["view-apply-today", "Apply Today"], ["view-all-jobs", "All Jobs"], ["view-my-companies", "My Companies"],
  ["view-external", "External Jobs"], ["view-tracker", "Applied / Tracker"], ["view-shortlisted", "Shortlisted"], ["view-resumes", "Resumes"],
  ["view-questions", "Questions for you"], ["view-followups", "Follow-ups Due"], ["view-closing-soon", "Closing Soon"], ["view-skill-gaps", "Skill Gaps"],
  ["view-stats", "Stats"], ["view-seasons", "Hiring Seasons"], ["view-missed", "Missed Matches"], ["view-filtered", "Filtered Out"], ["view-coverage", "Coverage"],
  ["view-manual-checks", "Manual Checks"], ["view-run-log", "Run Log"]]) {
  SHOTS.push([name, async (p) => { await p.key("2"); await p.eval(view(label)); await p.sleep(450); }]);
}
for (const t of ["board", "table", "companies", "stats"]) {
  SHOTS.push([`view-applications-${t}`, async (p) => { await p.key("1"); await p.eval(view("My Applications")); await p.sleep(250);
    await p.eval(`[...document.querySelectorAll(".seg button")].find((b) => b.textContent.toLowerCase().startsWith(${JSON.stringify(t === "companies" ? "applied" : t)})).click()`); await p.sleep(300); }]);
}
SHOTS.push(["view-all-jobs-table", async (p) => { await p.key("2"); await p.eval(view("All Jobs")); await p.sleep(200); await p.eval(`document.querySelector("#mode-toggle").click()`); await p.sleep(400); }]);
for (const t of ["Overview", "Requirements", "Keywords", "Score", "Full JD", "Similar", "Notes & History"]) {
  SHOTS.push([`panel-${t.toLowerCase().replace(/[^a-z]+/g, "-")}`, async (p) => {
    await p.key("2"); await p.eval(view("All Jobs")); await p.sleep(200);
    await p.eval(`(document.querySelector("#list .card") || document.querySelector("#list tr[data-id]")).click()`); await p.sleep(600);
    await p.eval(`(() => { const b = [...document.querySelectorAll("#p-tabs .panel-tab")].find((x) => x.textContent === ${JSON.stringify(t)}); if (b) b.click(); })()`); await p.sleep(400);
  }]);
}
SHOTS.push(["drawer-application", async (p) => { await p.key("1"); await p.eval(view("My Applications")); await p.sleep(250);
  await p.eval(`(() => { const c = document.querySelector(".app-card"); if (c) c.click(); })()`); await p.sleep(400); }]);
SHOTS.push(["queue-panel", async (p) => {
  await p.key("2");
  const t0 = new Date(Date.now() - 245000).toISOString();
  await p.eval(`(() => { const j = window.JOBS.filter((x) => !x.filtered); window.__hsLive({ generated_at: "ui1", worker: { state: "running" }, queue: [
    { id: 1, job_pk: j[0].id, company: j[0].company, title: j[0].title, status: "generating", attempts: 1, added_at: "${t0}", started_at: "${t0}" },
    { id: 2, job_pk: j[1].id, company: j[1].company, title: j[1].title, status: "queued", attempts: 0, added_at: "${t0}" },
    { id: 3, job_pk: j[2].id, company: j[2].company, title: j[2].title, status: "queued", attempts: 0, added_at: "${t0}" },
    { id: 4, job_pk: j[3].id, company: j[3].company, title: j[3].title, status: "done", result: "ready_questions", attempts: 1, added_at: "${t0}", finished_at: "${t0}", message: "v1 ready; 2 question(s) for you" },
    { id: 5, job_pk: j[4].id, company: j[4].company, title: j[4].title, status: "failed", attempts: 2, added_at: "${t0}", finished_at: "${t0}", message: "bullet_length: bullets over 2 lines" }] }); })()`);
  await p.sleep(300); await p.eval(`document.querySelector("#queue-btn").click()`); await p.sleep(400);
}]);
SHOTS.push(["palette", async (p) => { await p.key("2"); await p.eval(`document.querySelector("#palette-btn").click()`); await p.sleep(200);
  await p.eval(`(() => { const q = document.querySelector("#palette-q"); q.value = "software engineer"; q.dispatchEvent(new Event("input")); })()`); await p.sleep(1500); }]);
SHOTS.push(["toast", async (p) => { await p.key("2"); await p.eval(view("All Jobs")); await p.sleep(200);
  await p.eval(`document.querySelector("#list .card input.bucket-cb").click()`); await p.sleep(250); }]);
SHOTS.push(["help-dialog", async (p) => { await p.key("2"); await p.eval(`document.querySelector("#help-btn").click()`); await p.sleep(250); }]);
SHOTS.push(["setup-banner", async (p) => { await p.key("2"); await p.sleep(300); }, false]);

const widths = [["laptop", 1440, 900], ["narrow", 760, 1000]];
const report = [];
for (const [wname, width, height] of widths) {
  for (const dark of [false, true]) {
    for (const [name, setup, installed = true] of SHOTS) {
      if (only && !name.startsWith(only)) continue;
      const p = await launch({ width, height, dark });
      const t0 = Date.now();
      await p.send("Page.addScriptToEvaluateOnNewDocument", { source: PRE(installed) });
      let err = null;
      try {
        await p.goto(url);
        const loadMs = Date.now() - t0;
        await setup(p);
        const audit = await p.eval(AUDIT);
        const file = join(out, `${name}--${wname}--${dark ? "dark" : "light"}.png`);
        await p.screenshot(file, !["queue-panel", "palette", "toast", "help-dialog", "drawer-application"].includes(name) && !name.startsWith("panel-"));
        report.push({ name, width: wname, theme: dark ? "dark" : "light", loadMs, errors: p.errors.slice(0, 5), ...audit, links: await p.eval("window.__links") });
      } catch (e) { err = String(e.message || e).slice(0, 300); report.push({ name, width: wname, theme: dark ? "dark" : "light", failed: err, errors: p.errors.slice(0, 5) }); }
      await p.close();
    }
  }
}
writeFileSync(join(out, "report.json"), JSON.stringify(report, null, 1));
const bad = report.filter((r) => r.failed || r.errors.length || r.overflowX || (r.contrast || []).length || (r.clipped || []).length);
console.log(JSON.stringify({ shots: report.length, with_issues: bad.length }));
