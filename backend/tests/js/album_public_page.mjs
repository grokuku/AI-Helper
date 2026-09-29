// ─────────────────────────────────────────────────────────────────────────
// Harnais jsdom de la PAGE PUBLIQUE D'ALBUM (backend/public_web/album.js).
//
// Exécuté par pytest (backend/tests/test_albums_public.py) via `node`.
// Codes de sortie : 0 = PASS · 2 = SKIP (jsdom absent) · 1 = FAIL.
//
// Il prouve SANS navigateur que album.js :
//   1. lit la clé dans l'URL et FETCHE /a/<key>/manifest.json ;
//   2. pose titre / description / compte en TEXT CONTENT (aucune injection
//      HTML : un titre malveillant reste du texte, zéro enfant DOM) ;
//   3. construit la grille depuis les items du manifest (URLs t/ et f/);
//   4. ouvre la vue grande au clic (onActivate → openZoom) et le renderMedia
//      produit un simple <img src=…/f/0001.jpg> ;
//   5. gère les états 404 (album inconnu/révoqué) et erreur réseau.
// ─────────────────────────────────────────────────────────────────────────
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const ALBUM_JS = resolve(HERE, "..", "..", "public_web", "album.js");
const INDEX_HTML = resolve(HERE, "..", "..", "public_web", "index.html");
const KEY = "A".repeat(43);

// ── Résolution de jsdom (mêmes sources que frontend/js/test_helpers) ──────
const require = createRequire(import.meta.url);
function resolveJsdom() {
    const paths = [];
    if (process.env.JSDOM_DIR) paths.push(process.env.JSDOM_DIR);
    paths.push(resolve(HERE, "..", "..", "..", "holaf-lib", "node_modules"));
    paths.push("/projects/holaf-lib/node_modules");
    for (const p of paths) {
        try { return require.resolve("jsdom", { paths: [p] }); } catch { /* suivant */ }
    }
    return null;
}
const jsdomPath = resolveJsdom();
if (!jsdomPath) {
    console.warn("⚠️  test_album_public : jsdom introuvable → SKIP (exit 2). JSDOM_DIR=… pour l'exécuter.");
    process.exit(2);
}
const { JSDOM } = await import(jsdomPath);

const ALBUM_SRC = readFileSync(ALBUM_JS, "utf8");
const INDEX_SRC = readFileSync(INDEX_HTML, "utf8");
assert.ok(ALBUM_JS && existsSync(ALBUM_JS), "album.js introuvable");

/** Monte la page et évalue album.js avec un fetch simulé. */
function mount(fetchImpl) {
    const dom = new JSDOM(INDEX_SRC, {
        url: "http://localhost/a/" + KEY,
        runScripts: "outside-only",
        pretendToBeVisual: true,
    });
    const { window } = dom;
    const rec = { fetchUrls: [], grid: [], lightbox: [], gridItems: [], lbItems: [], opened: [] };
    window.fetch = (url, opts) => { rec.fetchUrls.push({ url, opts }); return fetchImpl(url, opts); };
    window.HolafViewport = { create: () => ({}) };
    window.HolafGrid = {
        create: (el, opts) => {
            rec.grid.push({ el, opts });
            return { getColumnCount: () => 3, setItems: (items) => { rec.gridItems = items; }, destroy() {} };
        },
    };
    window.HolafLightbox = {
        create: (opts) => {
            rec.lightbox.push(opts);
            return {
                setItems: (items) => { rec.lbItems = items; },
                openZoom: (item) => { rec.opened.push(item); },
            };
        },
    };
    window.eval(ALBUM_SRC);
    return { dom, window, rec };
}

const tick = () => new Promise((r) => setTimeout(r, 40));

function manifestResponse(body, status = 200) {
    return Promise.resolve({
        status,
        ok: status >= 200 && status < 300,
        json: () => Promise.resolve(body),
    });
}

// ── Scénario 1 : album nominal + XSS ────────────────────────────────────
{
    const XSS = '<img src=x onerror="window.__pwned=1">';
    const manifest = {
        title: XSS,
        description: "Une <b>description</b> " + XSS,
        count: 3,
        items: [
            { i: 1, w: 100, h: 80, ext: ".jpg" },
            { i: 2, w: 90, h: 90, ext: ".png" },
            { i: 3, w: 50, h: 50, ext: ".jpg" },
        ],
    };
    const { window, rec } = mount(() => manifestResponse(manifest));
    await tick();

    // 1. Clé lue dans l'URL + fetch du manifest.
    assert.equal(rec.fetchUrls.length, 1, "un seul fetch attendu");
    assert.equal(rec.fetchUrls[0].url, "/a/" + KEY + "/manifest.json");
    assert.equal(rec.fetchUrls[0].opts.cache, "no-store");
    assert.equal(rec.fetchUrls[0].opts.credentials, "omit");

    // 2. Titre/description/compte en TEXT CONTENT (pas d'innerHTML).
    const titleEl = window.document.getElementById("album-title");
    const descEl = window.document.getElementById("album-description");
    const countEl = window.document.getElementById("album-count");
    assert.equal(titleEl.textContent, manifest.title);
    assert.equal(descEl.textContent, manifest.description);
    assert.match(countEl.textContent, /3/);
    // Contrôle NÉGATIF : le HTML malveillant n'a créé AUCUN élément.
    assert.equal(titleEl.children.length, 0, "le titre ne doit créer aucun enfant DOM");
    assert.equal(descEl.querySelector("img"), null, "aucun <img> injecté via description");
    assert.equal(window.__pwned, undefined, "aucun script déclenché");
    // Le statut de chargement a été remplacé (plus de message).
    assert.equal(window.document.getElementById("album-status").textContent, "");

    // 3. Grille construite depuis le manifest.
    assert.equal(rec.grid.length, 1, "la grille doit être créée");
    assert.equal(rec.gridItems.length, 3);
    assert.equal(rec.gridItems[0].thumbUrl, "/a/" + KEY + "/t/0001.jpg");
    assert.equal(rec.gridItems[0].fullUrl, "/a/" + KEY + "/f/0001.jpg");
    assert.equal(rec.gridItems[1].fullUrl, "/a/" + KEY + "/f/0002.png");
    assert.equal(rec.gridItems[2].thumbUrl, "/a/" + KEY + "/t/0003.jpg");
    assert.equal(rec.gridItems[0].i, 1);
    // Grille visible.
    assert.equal(window.document.getElementById("album-grid").hidden, false);

    // 4. Clic → vue grande (onActivate → openZoom).
    rec.grid[0].opts.onActivate(rec.gridItems[1], 1);
    assert.equal(rec.opened.length, 1);
    assert.equal(rec.opened[0], rec.gridItems[1]);

    // 5. renderMedia = simple <img src=…/f/…>.
    const opts = rec.lightbox[0];
    assert.equal(typeof opts.renderMedia, "function");
    assert.ok(opts.viewport, "le viewport holaf est injecté");
    const container = window.document.createElement("div");
    const res = opts.renderMedia({ container, item: rec.gridItems[0], signal: { aborted: false }, onReady: () => {} });
    assert.equal(res.el.tagName, "IMG");
    assert.equal(res.el.getAttribute("src"), rec.gridItems[0].fullUrl);
    assert.equal(container.firstChild, res.el);
    res.destroy();
    assert.equal(container.firstChild, null);
}

// ── Scénario 2 : manifest 404 → état « album introuvable » ───────────────
{
    const { window } = mount(() => manifestResponse({}, 404));
    await tick();
    const status = window.document.getElementById("album-status");
    assert.match(status.textContent, /introuvable/i);
    assert.equal(window.document.getElementById("album-grid").hidden, true);
}

// ── Scénario 3 : erreur réseau → état « erreur de chargement » ───────────
{
    const { window } = mount(() => Promise.reject(new Error("network")));
    await tick();
    const status = window.document.getElementById("album-status");
    assert.match(status.textContent, /erreur/i);
}

// ── Scénario 4 : album vide → état dédié ─────────────────────────────────
{
    const manifest = { title: "Vide", description: "", count: 0, items: [] };
    const { window, rec } = mount(() => manifestResponse(manifest));
    await tick();
    assert.match(window.document.getElementById("album-status").textContent, /vide/i);
    assert.equal(rec.grid.length, 0, "aucune grille pour un album vide");
}

console.log("✅ test_album_public : album.js — titre/description en textContent, grille depuis le manifest, états 404/vide/erreur OK.");
process.exit(0);
