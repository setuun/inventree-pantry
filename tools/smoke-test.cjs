#!/usr/bin/env node
/* Smoke test: the real page against a fake InvenTree API, in every shipped language.

     npm install --no-save playwright && npx playwright install chromium
     node tools/smoke-test.cjs [--shots <dir>] [--viewport]

   It serves app/ at /vorrat/ next to a small in-memory stand-in for the parts of the
   InvenTree API the page uses, then walks every screen and every form once per language at
   phone size (390 × 844). It fails on:
     · any JavaScript error or console error on the page
     · a translation key showing on screen instead of its text ("stock.heading")
     · a screen that does not come up at all
   With --shots it also leaves one screenshot per screen and language, which is how layout
   problems (a word that is twice as long in Russian) get seen rather than guessed. Shots are
   the whole page by default; --viewport keeps them at phone-screen size (docs/screenshots). 

   Not a replacement for trying it on a phone against a real InvenTree: the fake API answers
   what the page asks, not what InvenTree would say about it. */

"use strict";

const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const APP = path.join(__dirname, "..", "app");
const args = process.argv.slice(2);
const SHOTS = args.includes("--shots") ? args[args.indexOf("--shots") + 1] : null;
const FULL_PAGE = !args.includes("--viewport");
const API_DELAY_MS = 150;

// ---------------------------------------------------------------- fake InvenTree
function freshDb() {
  const today = new Date();
  const iso = (days) => {
    const d = new Date(today);
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  };
  const locations = [
    { pk: 1, name: "Kitchen", pathstring: "Kitchen" },
    { pk: 2, name: "Cellar", pathstring: "Cellar" },
    { pk: 3, name: "Emergency", pathstring: "Cellar/Emergency" },
  ];
  const categories = [
    { pk: 1, name: "Food", pathstring: "Food", structural: true },
    { pk: 2, name: "Grains", pathstring: "Food/Grains", structural: false },
    { pk: 3, name: "Vegetables", pathstring: "Food/Vegetables", structural: false },
    { pk: 4, name: "Energy", pathstring: "Energy", structural: false },
  ];
  const parts = [
    { pk: 10, name: "Rice", full_name: "Rice", is_template: true, units: "kg", category: 2,
      minimum_stock: 4, total_in_stock: 2, in_stock: 0, description: "Staple", image: null },
    { pk: 11, name: "Basmati rice (Brand)", full_name: "Basmati rice (Brand)", variant_of: 10,
      units: "", category: 2, minimum_stock: 0, in_stock: 2, default_expiry: 730,
      description: "Brand · 1 kg", image: null },
    { pk: 20, name: "Tomatoes (Brand)", full_name: "Tomatoes (Brand)", units: "", category: 3,
      minimum_stock: 4, in_stock: 3, total_in_stock: 3, default_expiry: 1095,
      description: "400 g tin", image: null },
    { pk: 30, name: "AA batteries", full_name: "AA batteries", is_template: true, units: "",
      category: 4, minimum_stock: 8, total_in_stock: 0, in_stock: 0, description: "Staple",
      image: null },
  ];
  const brief = (p) => ({ pk: p.pk, name: p.name, full_name: p.full_name, image: p.image,
                          thumbnail: null, minimum_stock: p.minimum_stock,
                          default_expiry: p.default_expiry || 0, description: p.description });
  const stock = [
    { pk: 100, part: 11, quantity: 2, location: 3, expiry_date: iso(20),
      creation_date: iso(-700) },
    { pk: 101, part: 20, quantity: 2, location: 1, expiry_date: iso(400),
      creation_date: iso(-10) },
    { pk: 102, part: 20, quantity: 1, location: 2, expiry_date: iso(-3),
      creation_date: iso(-1000) },
  ];
  return { locations, categories, parts, stock, brief, nextPk: 500 };
}

let DB = freshDb();

function partByPk(pk) { return DB.parts.find((p) => p.pk === pk); }

function recount() {
  for (const p of DB.parts) {
    p.in_stock = DB.stock.filter((s) => s.part === p.pk).reduce((n, s) => n + Number(s.quantity), 0);
  }
  for (const p of DB.parts.filter((x) => x.is_template)) {
    p.total_in_stock = DB.parts.filter((x) => x.variant_of === p.pk)
      .reduce((n, x) => n + x.in_stock, 0);
  }
}

function api(method, url, body) {
  const u = new URL(url, "http://x");
  const p = u.pathname.replace(/^\/api/, "");
  const q = u.searchParams;
  const list = (rows) => ({ count: rows.length, results: rows });
  let m;
  recount();

  if (p === "/user/me/token/") return [200, { token: "fake-token" }];
  if (p === "/user/me/") return [200, { pk: 1, username: "tester" }];
  if (p === "/stock/location/") return [200, list(DB.locations)];
  if (p === "/part/category/") return [200, list(DB.categories)];
  if (p === "/parameter/template/") return [200, list([{ pk: 1, name: "Pack content" }])];
  if (p === "/parameter/" && method === "GET") {
    return [200, list([{ pk: 1, template: 1, model_id: 11, data: "1", data_numeric: 1 }])];
  }
  if (p === "/parameter/") return [201, { pk: DB.nextPk++ }];
  if ((m = p.match(/^\/parameter\/(\d+)\/$/))) return [200, { pk: Number(m[1]) }];

  if (p === "/barcode/") {
    return body && body.barcode === "4000000000000" ? [200, { part: { pk: 20 } }]
                                                   : [400, { error: "No match found" }];
  }
  if (p === "/barcode/link/") return [200, { success: "linked" }];

  if (p === "/part/" && method === "GET") {
    const rows = q.get("low_stock") === "true"
      ? DB.parts.filter((x) => x.minimum_stock
                         && (x.is_template ? x.total_in_stock : x.in_stock) < x.minimum_stock)
      : DB.parts;
    return [200, list(rows)];
  }
  if (p === "/part/" && method === "POST") {
    const created = Object.assign({ pk: DB.nextPk++, in_stock: 0, total_in_stock: 0,
                                    image: null, units: "" }, body);
    created.full_name = created.name;
    DB.parts.push(created);
    return [201, created];
  }
  if ((m = p.match(/^\/part\/(\d+)\/$/))) {
    const part = partByPk(Number(m[1]));
    if (!part) return [404, { detail: "Not found." }];
    if (method === "PATCH" && body && typeof body === "object") Object.assign(part, body);
    if (method === "DELETE") DB.parts = DB.parts.filter((x) => x !== part);
    return [200, part];
  }

  if (p === "/stock/" && method === "GET") {
    let rows = DB.stock;
    if (q.get("part")) rows = rows.filter((s) => s.part === Number(q.get("part")));
    rows = rows.map((s) => Object.assign({}, s, { part_detail: DB.brief(partByPk(s.part)) }));
    return [200, list(rows)];
  }
  if (p === "/stock/" && method === "POST") {
    const row = Object.assign({ pk: DB.nextPk++, creation_date: new Date().toISOString() }, body);
    DB.stock.push(row);
    return [201, row];
  }
  if ((m = p.match(/^\/stock\/(add|remove|count|transfer)\/$/))) {
    for (const it of body.items) {
      const row = DB.stock.find((s) => s.pk === it.pk);
      const n = Number(it.quantity);
      if (m[1] === "add") row.quantity = Number(row.quantity) + n;
      if (m[1] === "remove") row.quantity = Number(row.quantity) - n;
      if (m[1] === "count") row.quantity = n;
      if (m[1] === "transfer") row.location = body.location;
    }
    DB.stock = DB.stock.filter((s) => Number(s.quantity) > 0);
    return [200, { success: true }];
  }
  if ((m = p.match(/^\/stock\/(\d+)\/$/))) {
    const row = DB.stock.find((s) => s.pk === Number(m[1]));
    if (!row) return [404, { detail: "Not found." }];
    if (method === "PATCH") Object.assign(row, body);
    if (method === "DELETE") DB.stock = DB.stock.filter((s) => s !== row);
    return [200, Object.assign({}, row, { part_detail: DB.brief(partByPk(row.part)) })];
  }
  return [404, { detail: "fake API has no " + method + " " + p }];
}

const TYPES = { ".html": "text/html", ".json": "application/json", ".svg": "image/svg+xml",
                ".webmanifest": "application/manifest+json" };
const CONFIG = JSON.stringify({ language: "en", pack_parameter: "Pack content",
                                app: { book_location: "Cellar/Emergency" },
                                openfoodfacts: { fallback_location: "Kitchen" } });

const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    if (req.url.startsWith("/api/")) {
      // The first token request of a fresh page is the "existing /web/ session" probe; the
      // test wants to see the login card, so that one probe is refused.
      if (req.url.startsWith("/api/user/me/token/") && !req.headers.authorization) {
        res.writeHead(401, { "Content-Type": "application/json" });
        return res.end('{"detail":"no session"}');
      }
      let parsed = null;
      try { parsed = body && req.headers["content-type"] === "application/json" ? JSON.parse(body) : body; }
      catch (e) { parsed = null; }
      const [status, data] = api(req.method, req.url, parsed);
      // A slow server, so that "the curtain lifts before the data" would be visible.
      return setTimeout(() => {
        res.writeHead(status, { "Content-Type": "application/json" });
        res.end(JSON.stringify(data));
      }, API_DELAY_MS);
    }
    const rel = decodeURIComponent(new URL(req.url, "http://x").pathname).replace(/^\/vorrat\/?/, "");
    if (rel === "config.json") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(CONFIG);
    }
    const file = path.join(APP, rel || "index.html");
    if (!file.startsWith(APP) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404);
      return res.end();
    }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
});

// ---------------------------------------------------------------- the walk
const KEY_ON_SCREEN = /\b(?:app|lang|common|auth|tab|nav|unit|days|date|count|field|error|expiry|scan|stock|article|requirement|detail|suggest|photo|action|book|use|batch|split|delete|buy)\.[a-zA-Z.]+\b/;

async function walk(browser, lang, base) {
  DB = freshDb();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: lang,
                                         deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const problems = [];
  page.on("pageerror", (e) => problems.push("page error: " + e.message));
  // A refused request shows up as a console error too; the 401 of the session probe and the
  // 400 of an unknown barcode are expected. Real script errors arrive as "pageerror".
  page.on("console", (m) => {
    if (m.type() === "error" && !/^Failed to load resource/.test(m.text())) {
      problems.push("console: " + m.text());
    }
  });

  let n = 0;
  const shot = async (name) => {
    const text = await page.evaluate(() => document.body.innerText);
    const hit = text.match(KEY_ON_SCREEN);
    if (hit) problems.push(name + ": raw key on screen: " + hit[0]);
    if (SHOTS) {
      fs.mkdirSync(SHOTS, { recursive: true });
      await page.screenshot({ path: path.join(SHOTS, lang + "-" + String(++n).padStart(2, "0")
                                               + "-" + name + ".png"), fullPage: FULL_PAGE });
    }
  };
  const step = async (name, fn) => {
    try { await fn(); await page.waitForTimeout(250); await shot(name); }
    catch (e) { problems.push(name + ": " + e.message.split("\n")[0]); }
  };
  const tap = (sel) => page.locator(sel).first().click({ timeout: 4000 });
  const back = () => tap("#detailback");

  await step("login", async () => {
    await page.goto(base + "/vorrat/");
    await page.waitForSelector("#u", { timeout: 5000 });
  });
  await step("stock", async () => {
    await page.fill("#u", "tester");
    await page.fill("#pw", "secret");
    await page.keyboard.press("Enter");
    await page.waitForSelector("#stocklist .rowlink", { timeout: 5000 });
  });
  // A hard reload with a stored token: the curtain must not lift before the list has its rows.
  await step("reload", async () => {
    await page.reload();
    await page.waitForFunction(() => document.body.classList.contains("ready"), null,
                               { timeout: 8000 });
    const rows = await page.locator("#stocklist .rowlink").count();
    if (!rows) throw new Error("curtain lifted before the stock list had any rows");
  });
  await step("stock-missing", () => tap("#shortbtn"));
  await step("detail-product", async () => { await tap("#shortbtn"); await tap('#stocklist [data-pk="20"]'); });
  await step("book-in", () => tap("#detail .actions .btn.primary"));
  await step("used", () => tap("#detail .actions .btn:nth-child(2)"));
  await step("count", () => tap("#detail .actions .btn.ghost"));
  // The last batch holds two, so it can be split; the first one is a single expired tin.
  await step("batch-edit", () => tap('#detail ul.stock li:last-child button[title]'));
  await step("split", () => tap("#detail .panel > .btn.ghost"));
  await step("batch-delete", () => tap('#detail ul.stock li:first-child .warnink'));
  await step("article-edit", () => tap("#detail .detailhead .iconbtn:not(.warnink)"));
  await step("article-delete", () => tap("#detail .detailhead .warnink"));
  await step("buy", async () => { await back(); await tap("#tab-buy"); await page.waitForSelector("#buylist .buyrow"); });
  await step("requirement", () => tap('#buylist [data-pk="10"]'));
  await step("book-measured", () => tap('#detail .buyrow'));
  await step("book-measured-form", () => tap("#detail .actions .btn.primary"));
  await step("buy-all", async () => { await tap("#tab-buy"); await tap("#buycontrols .btn:first-child"); });
  await step("new-requirement", () => tap("#buycontrols .btn:nth-child(2)"));
  await step("expiry", () => tap("#tab-expiry"));
  await step("scan-unknown", async () => {
    await tap("#tab-scan");
    await page.fill("#ean", "4006381333931");
    await tap("#go");
    await page.waitForSelector("#result .card .btn.primary");
  });
  await step("scan-create", () => tap("#result .card .btn.primary"));
  await step("language-switch", async () => {
    const other = lang === "en" ? "de" : "en";
    await page.selectOption("#lang", other);
    await page.waitForLoadState("load");
    await page.waitForTimeout(600);
    const now = await page.evaluate(() => document.documentElement.lang);
    if (now !== other) throw new Error("language did not switch: " + now);
  });

  await ctx.close();
  return problems;
}

(async () => {
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = "http://127.0.0.1:" + server.address().port;
  const html = fs.readFileSync(path.join(APP, "index.html"), "utf8");
  const langs = [...html.matchAll(/^\s+([a-z]{2,3}): \{ name: "/gm)].map((m) => m[1]);

  const browser = await chromium.launch();
  let failed = 0;
  for (const lang of langs) {
    const problems = await walk(browser, lang, base);
    console.log((problems.length ? "FAIL " : "ok   ") + lang
                + (problems.length ? "\n  - " + problems.join("\n  - ") : ""));
    failed += problems.length;
  }
  await browser.close();
  server.close();
  process.exit(failed ? 1 : 0);
})();
