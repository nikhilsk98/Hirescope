// Dashboard scenarios in headless Chrome: node tests/browser/dashboard.mjs <file:///…/index.html> <scenario> [width] [dark]
// Prints one JSON object: {ok, errors, checks: [[name, pass, detail]]}. The page's hirescope:// links are captured
// through window.__hsLaunch (the real launcher is never opened from a test).
import { launch } from "./cdp.mjs";

const [, , url, scenario, width = "1440", dark = ""] = process.argv;
const checks = [];
const check = (name, pass, detail = "") => checks.push([name, !!pass, String(detail).slice(0, 300)]);

const CAPTURE = `window.__links = []; window.__hsLaunch = (u) => window.__links.push(u); true`;
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
