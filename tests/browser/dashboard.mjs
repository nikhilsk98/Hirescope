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
  HTMLAnchorElement.prototype.click = function () { if (!this.hasAttribute("download")) return ok.call(this); };
  try { ${action}; } finally { URL.createObjectURL = oc; HTMLAnchorElement.prototype.click = ok; }
  return out ? out.text() : null; })()`;
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
    check("Undo removes the application", await p.eval(`Object.values(JSON.parse(localStorage.getItem("jobsearch.applications.v1") || "{}")).filter((a) => !a.deleted).length`) === 0);
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
