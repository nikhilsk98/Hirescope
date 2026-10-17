// Dashboard scenarios in headless Chrome: node tests/browser/dashboard.mjs <file:///…/index.html> <scenario> [width] [dark]
// Prints one JSON object: {ok, errors, checks: [[name, pass, detail]]}. The page's hirescope:// links are captured
// through window.__hsLaunch (the real launcher is never opened from a test).
import { launch } from "./cdp.mjs";

const [, , url, scenario, width = "1440", dark = ""] = process.argv;
const checks = [];
const check = (name, pass, detail = "") => checks.push([name, !!pass, String(detail).slice(0, 300)]);

const CAPTURE = `window.__links = []; window.__hsLaunch = (u) => window.__links.push(u); true`;
// run `action` and return the text of the file the page would download (nothing is saved)
const DOWNLOAD = (action) => `(() => { let out = null; const oc = URL.createObjectURL, ok = HTMLAnchorElement.prototype.click;
  URL.createObjectURL = (b) => { out = b; return oc.call(URL, b); };
  HTMLAnchorElement.prototype.click = function () { if (!this.hasAttribute("download")) return ok.call(this); window.__dlName = this.getAttribute("download"); };
  try { ${action}; } finally { URL.createObjectURL = oc; HTMLAnchorElement.prototype.click = ok; }
  return out ? out.text() : null; })()`;
// keep download stubs installed (for async handlers that download after an await); downloads go to window.__dl
const STUB_DOWNLOADS = `(() => { window.__dl = []; const oc = URL.createObjectURL, ok = HTMLAnchorElement.prototype.click;
  URL.createObjectURL = (b) => { window.__dlBlob = b; return oc.call(URL, b); };
  HTMLAnchorElement.prototype.click = function () { if (!this.hasAttribute("download")) return ok.call(this);
    const name = this.getAttribute("download"); window.__dlBlob.text().then((t) => window.__dl.push({ name, text: t })); };
  return true; })()`;
const text = (sel) => `(() => { const n = document.querySelector(${JSON.stringify(sel)}); return n ? n.textContent : null; })()`;

const scenarios = {
  async smoke(p) {
    check("cards render", await p.eval(`document.querySelectorAll("#list .card").length`) > 0);
    check("no setup banner when installed", !(await p.eval(`!!document.querySelector(".setup-banner")`)));
    await p.eval(CAPTURE);
    await p.eval(`document.querySelector("#list .card").focus()`);
    await p.key("Enter");
    check("panel opens", await p.eval(`document.querySelector("#panel").classList.contains("open")`));
    const gen = await p.eval(`(() => { const b = [...document.querySelectorAll("#p-actions button")].find((x) => /Generate Resume/.test(x.textContent)); return b ? b.textContent : null; })()`);
    check("Generate Resume button (no copy-command wording)", gen === "Generate Resume", gen);
    check("Copy command is not a visible button", !(await p.eval(`[...document.querySelectorAll("#p-actions button")].some((x) => /copy command/i.test(x.textContent))`)));
    await p.eval(`[...document.querySelectorAll("#p-actions button")].find((x) => /Generate Resume/.test(x.textContent)).click()`);
    await p.sleep(150);
    check("one-time browser tip shown", await p.eval(`document.querySelector("#confirm").open && /Always allow/.test(document.querySelector("#confirm").textContent)`));
    await p.eval(`document.querySelector("#confirm-yes").click()`);
    await p.sleep(200);
    const links = await p.eval(`window.__links`);
    const id = await p.eval(`Number(document.querySelector("#list .card").dataset.id)`);
    check("Generate Resume opens hirescope://queue", links.length === 1 && links[0] === `hirescope://queue?job=${id}`, JSON.stringify(links));
    check("status Queued right away", /Resume: Queued/.test(await p.eval(`document.querySelector("#p-badges").textContent`)));
    const more = await p.eval(`(() => { const b = document.querySelector("#p-actions .more-btn"); if (!b) return null; b.click(); return [...document.querySelectorAll(".more-pop button")].map((x) => x.textContent); })()`);
    check("Copy command lives under …", Array.isArray(more) && more.some((t) => /Copy Terminal command/.test(t)), JSON.stringify(more));
    await p.key("Escape");
    // the queue moves on: live.js says generating, then done
    const now = new Date().toISOString();
    await p.eval(`window.__hsLive({generated_at: "a1", queue: [{id: 1, job_pk: ${id}, company: "X", title: "Y", status: "generating", attempts: 1, added_at: "${now}", started_at: "${now}"}]})`);
    await p.sleep(100);
    check("live: Generating", /Resume: Generating/.test(await p.eval(`document.querySelector("#list .card").textContent`)));
    await p.key("Escape");
    await p.sleep(100);
    await p.eval(`document.querySelector("#list .card").focus()`);
    await p.key("g");
    await p.sleep(150);
    const links2 = await p.eval(`window.__links`);
    check("g key queues without a second tip", links2.length === 2 && !(await p.eval(`document.querySelector("#confirm").open`)), JSON.stringify(links2));
  },
  async bucket(p) {
    await p.eval(CAPTURE + `; localStorage.setItem("jobsearch.launcherTip.v1", "true")`);
    const ids = await p.eval(`(() => { const by = {}; for (const j of window.JOBS.filter((x) => !x.filtered)) (by[j.track] = by[j.track] || []).push(j.id); return by; })()`);
    const coop = ids.coop_intern[0], ft = ids.full_time[0], sim = ids.coop_intern[1];
    // co-op job from the Co-op tab (checkbox before the title)
    await p.key("1");
    await p.eval(`document.querySelector('#list [data-id="${coop}"] input.bucket-cb').click()`);
    check("checkbox before the title adds to the Bucket", await p.eval(`document.querySelector("#tab-bucket .count").textContent`) === "1");
    // full-time job: Space on the focused card
    await p.key("2");
    await p.eval(`document.querySelector('#list [data-id="${ft}"]').focus()`);
    await p.eval(`document.querySelector('#list [data-id="${ft}"]').click()`);
    await p.key("Escape");
    await p.eval(`document.querySelector('#list [data-id="${ft}"]').focus()`);
    await p.key(" ");
    check("Space adds the focused job", await p.eval(`document.querySelector("#tab-bucket .count").textContent`) === "2");
    // from the Similar list of another job (and the panel's own checkbox stays in step)
    await p.eval(`window.JOBS.find((j) => j.id === ${coop}).similar = [${sim}]`);
    await p.key("1");
    await p.eval(`document.querySelector('#list [data-id="${coop}"]').click()`);
    await p.sleep(300);
    check("panel checkbox shows In Bucket", await p.eval(`document.querySelector('#p-bucket input.bucket-cb').checked`));
    await p.eval(`[...document.querySelectorAll("#p-tabs .panel-tab")].find((t) => t.textContent === "Similar").click()`);
    await p.sleep(150);
    await p.eval(`document.querySelector('#p-body [data-id="${sim}"] input.bucket-cb').click()`);
    check("Similar list adds to the Bucket", await p.eval(`document.querySelector("#tab-bucket .count").textContent`) === "3");
    await p.key("Escape");
    const links = await p.eval(`window.__links`);
    check("each tick sends one bucket link", JSON.stringify(links) === JSON.stringify([coop, ft, sim].map((i) => `hirescope://bucket?job=${i}&op=add`)), JSON.stringify(links));
    // the Bucket tab: both tracks, track badge, sortable, live count
    await p.key("3");
    await p.sleep(150);
    check("Bucket page lists 3 jobs", await p.eval(`document.querySelectorAll("[data-bucket-row]").length`) === 3);
    check("track badges for both tracks", await p.eval(`(() => { const t = [...document.querySelectorAll(".bucket-table .chip")].map((c) => c.textContent); return t.includes("Co-op / Intern") && t.includes("Full-time"); })()`));
    check("tiles and day tabs hidden on the Bucket page", await p.eval(`document.querySelector("#tiles").hidden && document.querySelector(".day-row").hidden`));
    check("hash #bucket", await p.eval(`location.hash`) === "#bucket");
    await p.eval(`(() => { const s = document.querySelector(".page-toolbar select"); s.value = "company"; s.dispatchEvent(new Event("change")); })()`);
    const cos = await p.eval(`[...document.querySelectorAll("[data-bucket-row] td.role > .muted")].map((n) => n.firstChild.textContent)`);
    check("sort by company", JSON.stringify(cos) === JSON.stringify([...cos].sort()), JSON.stringify(cos));
    // Mark Applied from the Bucket: leaves the Bucket, joins My Applications, one status link, Undo restores
    await p.eval(`window.__links.length = 0`);
    await p.eval(`document.querySelector('[data-bucket-row="${ft}"] .row-actions button[aria-label^="Mark"]').click()`);
    await p.sleep(250);
    check("Mark Applied removes it from the Bucket", await p.eval(`document.querySelector("#tab-bucket .count").textContent`) === "2");
    check("in My Applications", await p.eval(`Object.values(JSON.parse(localStorage.getItem("jobsearch.applications.v1"))).some((a) => !a.deleted && a.job_key && a.title)`));
    const st = await p.eval(`window.__links[0]`);
    check("status link with app id and today's date", /^hirescope:\/\/status\?job=\d+&stage=applied&app=[a-z0-9]+&date=\d{4}-\d{2}-\d{2}$/.test(st), st);
    await p.eval(`[...document.querySelectorAll("#toasts .toast button")].pop().click()`);
    await p.sleep(200);
    check("Undo puts it back", await p.eval(`document.querySelector("#tab-bucket .count").textContent`) === "3");
    check("Undo sends stage=undo", /stage=undo&app=/.test(await p.eval(`window.__links[1]`)), await p.eval(`window.__links[1]`));
    check("Undo removes the application", await p.eval(`Object.values(JSON.parse(localStorage.getItem("jobsearch.applications.v1") || "{}")).filter((a) => !a.deleted && a.stage === "applied").length`) === 0);
    // Generate resumes for all: one link for every Bucket job without a resume
    await p.eval(`window.__links.length = 0`);
    await p.eval(`[...document.querySelectorAll(".page-toolbar button")].find((b) => /Generate resumes for all/.test(b.textContent)).click()`);
    await p.sleep(200);
    const g = await p.eval(`window.__links[0] || ""`);
    check("Generate resumes for all queues the Bucket jobs", /^hirescope:\/\/queue\?(list=bucket|job=\d+(,\d+){2})$/.test(g), g);
    check("statuses show Queued", await p.eval(`document.querySelectorAll(".bucket-table .chip.rs").length`) === 3);
    // remove (untick) from the Bucket page, persistence across reload, export format 6
    await p.eval(`document.querySelector('[data-bucket-row="${sim}"] .row-actions button[aria-label^="Remove"]').click()`);
    await p.sleep(150);
    check("Remove from Bucket", await p.eval(`document.querySelector("#tab-bucket .count").textContent`) === "2");
    await p.reload();
    check("Bucket persists after a reload", await p.eval(`document.querySelector("#tab-bucket .count").textContent`) === "2" && await p.eval(`document.querySelectorAll("[data-bucket-row]").length`) === 2);
    const exp = await p.eval(DOWNLOAD(`document.querySelector("#export-statuses").click()`));
    const ej = JSON.parse(exp);
    check("Export statuses: format 6 with the Bucket", ej.format === 6 && Object.values(ej.bucket).filter((e) => e.in).length === 2);
    // keyboard: arrow keys move across the four top tabs
    await p.eval(`document.querySelector("#tab-bucket").focus()`);
    await p.key("ArrowLeft");
    check("arrow keys move between top tabs", await p.eval(`document.activeElement.id`) === "tab-ft" && await p.eval(`document.body.dataset.page`) === "jobs");
  },
  async load(p) {          // read-only: render, open the Bucket tab, open a job; no clicks that send links
    await p.eval(CAPTURE);
    check("cards render", await p.eval(`document.querySelectorAll("#list .card, #list tr[data-id]").length`) > 0);
    await p.key("3"); await p.sleep(200);
    check("Bucket tab renders", await p.eval(`document.body.dataset.page`) === "bucket");
    await p.key("1"); await p.sleep(200);
    await p.eval(`(document.querySelector("#list .card") || document.querySelector("#list tr[data-id]")).click()`); await p.sleep(400);
    check("panel opens", await p.eval(`document.querySelector("#panel").classList.contains("open")`));
    check("no links sent", (await p.eval(`window.__links.length`)) === 0);
  },
  async builder(p) {
    await p.eval(CAPTURE + `; localStorage.setItem("jobsearch.launcherTip.v1", "true"); ` + STUB_DOWNLOADS);
    await p.key("4"); await p.sleep(200);
    check("Resume Builder page", await p.eval(`document.body.dataset.page`) === "builder" && await p.eval(`location.hash`) === "#builder");
    check("tab count = external jobs", await p.eval(`document.querySelector("#tab-builder .count").textContent`) === "1");
    const row = `document.querySelector("[data-ext-row]")`;
    check("external job listed with resume status", /Resume: Ready/.test(await p.eval(`${row}.textContent`)));
    check("Ready to apply in the list", /Ready to apply/.test(await p.eval(`${row}.textContent`)));
    check("preview and download links", await p.eval(`${row}.querySelectorAll("a").length`) === 2);
    // Mark Applied: Ready to apply → Applied (today, resume v1), one status link; Undo → back
    await p.eval(`${row}.querySelector('button[aria-label^="Mark"]').click()`); await p.sleep(200);
    const link = await p.eval(`window.__links[0] || ""`);
    check("Mark Applied link records the version", /^hirescope:\/\/status\?job=\d+&stage=applied&app=ext-\d+&date=\d{4}-\d{2}-\d{2}&v=1$/.test(link), link);
    check("now Applied", /Applied ✓/.test(await p.eval(`document.querySelector("[data-ext-row]").textContent`)));
    await p.eval(`[...document.querySelectorAll("#toasts .toast button")].pop().click()`); await p.sleep(200);
    check("Undo → Ready to apply again", /Ready to apply/.test(await p.eval(`document.querySelector("[data-ext-row]").textContent`)) && /stage=undo/.test(await p.eval(`window.__links[1]`)));
    // My Applications board: Ready to apply is the first column
    await p.eval(`(() => { const b = [...document.querySelectorAll(".view-btn")].find((x) => /My Applications/.test(x.textContent)); b.click(); })()`); await p.sleep(200);
    await p.key("1"); await p.sleep(200);
    const cols = await p.eval(`[...document.querySelectorAll(".board .col h3")].map((h) => h.firstChild.textContent)`);
    check("board starts with Ready to apply", cols[0] === "Ready to apply" && cols[1] === "Applied", JSON.stringify(cols));
    check("the external job sits there", /Platform Engineer Co-op/.test(await p.eval(`document.querySelector(".board .col").textContent`)));
    // the form: validation, pasted JD → saved file + one external link, typing survives live updates
    await p.key("4"); await p.sleep(150);
    await p.eval(`window.__links.length = 0`);
    await p.eval(`document.querySelector(".builder-form button[type=submit]").click()`); await p.sleep(100);
    check("empty form: nothing sent", (await p.eval(`window.__links.length`)) === 0 && (await p.eval(`window.__dl.length`)) === 0);
    await p.eval(`(() => { const f = document.querySelector(".builder-form"); f.elements.company.value = "DemoNew"; f.elements.role.value = "Backend Co-op (DEMO)";
      f.elements.location.value = "Remote"; f.elements.jd.value = "DemoNew builds payments. Requirements: Python, SQL."; f.elements.track.value = "coop_intern"; })()`);
    await p.eval(`document.querySelector(".builder-form button[type=submit]").click()`); await p.sleep(400);
    const dl = await p.eval(`window.__dl[0]`);
    const sent = await p.eval(`window.__links[0] || ""`);
    check("saves the job file", dl && /^hirescope-external-job-DemoNew-\d{8}T\d{6}-[a-z0-9]+\.json$/.test(dl.name) && JSON.parse(dl.text).origin === "resume_builder" && JSON.parse(dl.text).jd_text.includes("payments"), dl && dl.name);
    check("opens hirescope://external for that file", dl && sent === `hirescope://external?file=${encodeURIComponent(dl.name)}`, sent);
    check("pending row shows Importing", /Importing/.test(await p.eval(`document.querySelector(".ext-table").textContent`)));
    check("status banner", /Sent to the launcher/.test(await p.eval(`document.querySelector(".ext-status").textContent`)));
    await p.eval(`document.querySelector(".builder-form").elements.company.value = "typing…"`);
    await p.eval(`window.__hsLive({generated_at: "b1", queue: [], launcher: {at: new Date().toISOString().slice(0, 19) + "+00:00", action: "external", ok: true, message: "imported DemoNew — Backend Co-op (DEMO) (job 99); resume queued"}})`);
    await p.sleep(150);
    check("launcher answer shown", /imported DemoNew/.test(await p.eval(`document.querySelector(".ext-status").textContent`)));
    check("typing survives a live update", await p.eval(`document.querySelector(".builder-form").elements.company.value`) === "typing…");
    // URL only
    await p.eval(`(() => { const f = document.querySelector(".builder-form"); f.reset(); f.elements.url.value = "https://jobs.demo.example/postings/7"; })()`);
    await p.eval(`document.querySelector(".builder-form button[type=submit]").click()`); await p.sleep(400);
    const dl2 = await p.eval(`window.__dl[1]`);
    check("URL only: saved with the URL and no JD", dl2 && JSON.parse(dl2.text).url === "https://jobs.demo.example/postings/7" && !JSON.parse(dl2.text).jd_text);
    check("URL only: one external link", (await p.eval(`window.__links.length`)) === 2);
  },
  async queue_questions(p) {
    await p.eval(CAPTURE + `; localStorage.setItem("jobsearch.launcherTip.v1", "true")`);
    await p.key("1");
    const qbtn = `[...document.querySelectorAll(".view-btn")].find((x) => /Questions for you/.test(x.textContent))`;
    check("sidebar shows 1 question due", /1/.test(await p.eval(`${qbtn}.querySelector(".n").textContent`)));
    await p.eval(`${qbtn}.click()`); await p.sleep(150);
    check("question card", /Kafka/.test(await p.eval(`document.querySelector(".question").textContent`)));
    // Yes without the confirmation: nothing sent
    await p.eval(`(() => { const f = document.querySelector(".answer-form"); f.querySelector('input[value=yes]').click();
      const sel = f.querySelectorAll("select"); sel[0].selectedIndex = 1; f.querySelector("textarea").value = "Built consumers for order events"; })()`);
    await p.eval(`document.querySelector(".answer-form button[type=submit]").click()`); await p.sleep(100);
    check("needs the confirmation tick", (await p.eval(`window.__links.length`)) === 0);
    await p.eval(`document.querySelector(".answer-form .confirm-line input").click()`);
    await p.eval(`document.querySelector(".answer-form button[type=submit]").click()`); await p.sleep(150);
    const a = await p.eval(`window.__links[0] || ""`);
    check("answer link", /^hirescope:\/\/answer\?q=\d+&a=yes&entry=[^&]+&text=Built\+consumers/.test(a), a);
    check("Regenerate offered after answering", /Regenerate this job/.test(await p.eval(`document.querySelector(".question").textContent`)));
    await p.eval(`[...document.querySelectorAll(".question button")].find((b) => /Regenerate/.test(b.textContent)).click()`); await p.sleep(150);
    check("Regenerate queues with regenerate=1", /^hirescope:\/\/queue\?job=\d+&regenerate=1$/.test(await p.eval(`window.__links[1] || ""`)), await p.eval(`window.__links[1] || ""`));
    // the queue panel: building now (elapsed time), waiting in order with Cancel, finished with the reason
    const t0 = new Date(Date.now() - 65000).toISOString(); const ids = await p.eval(`window.JOBS.filter((j) => !j.filtered).slice(0, 3).map((j) => j.id)`);
    await p.eval(`window.__hsLive({generated_at: "q1", worker: {state: "running"}, queue: [
      {id: 11, job_pk: ${ids[0]}, company: "DemoCo", title: "Gen job", status: "generating", attempts: 1, added_at: "${t0}", started_at: "${t0}"},
      {id: 12, job_pk: ${ids[1]}, company: "DemoCo", title: "Next job", status: "queued", attempts: 0, added_at: "${t0}"},
      {id: 13, job_pk: ${ids[2]}, company: "DemoCo", title: "Bad job", status: "failed", attempts: 2, added_at: "${t0}", finished_at: "${t0}", message: "bullet_length: 3 lines"}]})`);
    await p.sleep(150);
    check("Queue button counts active builds", await p.eval(`document.querySelector("#queue-count").textContent`) === "2");
    await p.eval(`document.querySelector("#queue-btn").focus()`); await p.key("Enter"); await p.sleep(200);
    check("panel opens with focus", await p.eval(`!document.querySelector("#queue-panel").hidden && document.querySelector("#queue-panel").contains(document.activeElement)`));
    const txt = await p.eval(`document.querySelector("#queue-body").textContent`);
    check("building / waiting / finished", /Building now/.test(txt) && /Waiting \(1\)/.test(txt) && /Finished today/.test(txt) && /bullet_length/.test(txt), txt);
    check("elapsed time", /^1m 0[5-9]s$|^1m 1\ds$/.test(await p.eval(`document.querySelector("[data-started]").textContent`)), await p.eval(`document.querySelector("[data-started]").textContent`));
    await p.eval(`window.__links.length = 0`);
    await p.key("Tab"); await p.key("Tab");
    const cancelFocused = await p.eval(`document.activeElement.getAttribute("aria-label") || document.activeElement.textContent`);
    check("Cancel reachable by keyboard", /Cancel/.test(cancelFocused), cancelFocused);
    await p.key("Enter"); await p.sleep(150);
    check("Cancel sends hirescope://cancel", /^hirescope:\/\/cancel\?job=\d+$/.test(await p.eval(`window.__links[0] || ""`)), await p.eval(`window.__links[0] || ""`));
    await p.eval(`document.querySelector("#queue-panel").focus()`); await p.key("Escape"); await p.sleep(100);
    check("Esc closes and returns focus", await p.eval(`document.querySelector("#queue-panel").hidden && document.activeElement.id === "queue-btn"`));
  },
  async setup_banner(p) {
    check("setup banner when the launcher isn't installed", await p.eval(`!!document.querySelector(".setup-banner")`));
    await p.eval(CAPTURE);
    await p.eval(`document.querySelector("#list .card").focus()`);
    await p.key("g");
    await p.sleep(150);
    check("no link without the launcher (copies the command instead)", (await p.eval(`window.__links.length`)) === 0);
  },
};

const page = await launch({ width: Number(width), dark: !!dark });
let ok = true;
try {
  await page.goto(url);
  await scenarios[scenario](page);
} catch (e) {
  ok = false; check("scenario ran", false, e.stack || e.message);
} finally {
  await page.close();
}
console.log(JSON.stringify({ ok: ok && checks.every((c) => c[1]), errors: page.errors, checks }));
