// Test headless (jsdom) — Onglet « Galerie » : PRÉ-CHARGEMENT des vignettes.
// Usage : node frontend/js/test_gallery_preload.mjs   (via ./js/run_js_tests.sh)
//
// Suite frère de test_gallery.mjs (CONSULTATION) et test_gallery_manage.mjs
// (GESTION). Elle prouve, SANS modifier aucune brique holaf :
//   1. les OPTIONS de pré-charge passées par l'hôte : bufferFactor ≥ 2 (zone
//      rendue élargie), gap, capacity et concurrency du thumbcache ;
//   2. la ZONE RENDUE (bufferFactor) couvre des rangées AU-DESSUS et EN
//      DESSOUS de la zone strictement visible, plus qu'avec l'ancien 1,5 ;
//   3. la PRÉ-CHARGE EXPLICITE des rangées AU-DELÀ du rendu, des DEUX côtés,
//      en PRIORITÉ BASSE (prefetch → request PRIORITY_LOW) ;
//   4. les DONNÉES chargées d'avance (ensureRange étendu, borné par total) ;
//   5. la DÉDUPLICATION (aucune nouvelle demande pour une vignette connue) ;
//   6. les BORNES : total, budget par émission, concurrence ;
//   7. visibilitychange : onglet masqué → file vidée et plus aucune demande ;
//      retour → reprise de la pré-charge de la dernière plage ;
//   8. contrôles négatifs : rien au-dessus en haut de liste, média sans
//      vignette jamais pré-chargé, pas de dépassement du total, stop() vide
//      la file.
import assert from "node:assert";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { loadJsdomOrSkip } from "./test_helpers/jsdom_loader.mjs";

const JSDOM = await loadJsdomOrSkip("test_gallery_preload");
const HERE = dirname(fileURLToPath(import.meta.url));

/* ── DOM de l'onglet Galerie (structure attendue par l'adaptateur) ───────── */
const dom = new JSDOM(`<!doctype html><html><body>
  <div id="tab-gallery">
    <input type="range" id="gallery-thumb-size" min="80" max="300" step="10" value="150">
    <span id="gallery-thumb-size-value"></span>
    <span id="gallery-thumb-resolution"></span>
    <div class="gallery-view-toggle">
      <button id="gallery-fit-cover" aria-pressed="true" class="is-active">Remplir</button>
      <button id="gallery-fit-contain" aria-pressed="false">Entière</button>
    </div>
    <div class="gallery-view-toggle">
      <button id="gallery-view-normal" aria-selected="true" class="is-active">Médias</button>
      <button id="gallery-view-trash" aria-selected="false">Corbeille</button>
    </div>
    <span id="gallery-selected" class="hidden"></span>
    <span id="gallery-count"></span>
    <select id="gallery-filter-kind"><option value="">Tous</option></select>
    <button type="button" id="gallery-filter-folders" aria-haspopup="dialog">Dossiers : tous</button>
    <button type="button" id="gallery-filter-favorite" aria-pressed="false">★ Favoris</button>
    <input id="gallery-search" type="search">
    <input id="gallery-filter-from" type="date">
    <input id="gallery-filter-to" type="date">
    <select id="gallery-filter-sort"><option value="created_at_desc">Récents</option></select>
    <div class="gallery-body">
      <div id="gallery-main">
        <div id="gallery-grid"></div>
        <div id="gallery-loading"></div>
        <div id="gallery-empty" class="hidden"></div>
        <div id="gallery-error" class="hidden"></div>
      </div>
      <div id="gallery-side">
        <aside id="gallery-info"></aside>
        <div id="gallery-actions" class="hidden">
          <span id="gallery-actions-label"></span>
          <button id="gallery-action-download" data-gallery-action>⤓ Télécharger</button>
          <button id="gallery-action-favorite" data-gallery-action>★ Favori</button>
          <button id="gallery-action-delete" data-gallery-action>🗑 Supprimer</button>
          <button id="gallery-action-restore" data-gallery-action class="hidden">♻ Restaurer</button>
          <button id="gallery-action-purge" data-gallery-action class="hidden">✖ Purger</button>
        </div>
      </div>
    </div>
  </div>
</body></html>`, { pretendToBeVisual: true, url: "http://localhost/" });

const { window } = dom;
globalThis.window = window;
globalThis.document = window.document;
globalThis.Event = window.Event;
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
globalThis.MouseEvent = window.MouseEvent;
globalThis.KeyboardEvent = window.KeyboardEvent;
globalThis.localStorage = window.localStorage;

globalThis.API = "/api";
globalThis.LOCAL_MODE = false;
globalThis.$ = (id) => window.document.getElementById(id);
globalThis.safeJson = async (res) => { try { return await res.json(); } catch { return { error: "Erreur serveur " + res.status }; } };

/* ── Géométrie du conteneur de grille (jsdom renvoie 0 par défaut) ───────── */
const gridEl = window.document.getElementById("gallery-grid");
let cw = 600, ch = 400, st = 0;
Object.defineProperty(gridEl, "clientWidth", { configurable: true, get: () => cw });
Object.defineProperty(gridEl, "clientHeight", { configurable: true, get: () => ch });
Object.defineProperty(gridEl, "scrollTop", { configurable: true, get: () => st, set: (v) => { st = Math.max(0, v); } });
gridEl.getBoundingClientRect = () => ({ left: 0, top: 0, right: cw, bottom: ch, width: cw, height: ch, x: 0, y: 0, toJSON() {} });

/* ── Jeu de données + fetch simulé (2 pages de 60) ───────────────────────── */
const TOTAL = 120;
const NO_THUMB_ID = 76; // média sans vignette (audio) — contrôle négatif
function makeItem(id) {
  const kind = (id === NO_THUMB_ID) ? "audio" : "image";
  return {
    id,
    filename: "media_" + id + (kind === "audio" ? ".mp3" : ".png"),
    kind,
    size: 1000 * id,
    created_at: "2025-01-01T" + String(id % 24).padStart(2, "0") + ":00:00Z",
    subfolder: "shot" + (id % 3),
    status: "active", trashed: false, trashed_at: null,
    thumb_available: kind !== "audio",
    url: "/api/media/" + id + "/download",
    thumb: "/api/media/" + id + "/thumbnail",
    has_prompt: true, has_workflow: false, favorite: false,
    tags: [], tags_detail: [],
  };
}
const ITEMS = [];
for (let i = 1; i <= TOTAL; i++) ITEMS.push(makeItem(i));

function makeRes(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, text: async () => JSON.stringify(body) };
}
function metadataFor(id) {
  const it = ITEMS.find((x) => x.id === id) || makeItem(id);
  return {
    id, filename: it.filename, subfolder: it.subfolder, kind: it.kind, ext: "", size: it.size,
    created_at: it.created_at, width: 512, height: 512, ratio: 1,
    duration: it.kind === "audio" ? 12.5 : null, duration_ms: it.kind === "audio" ? 12500 : null,
    codec: null, prompt: "prompt " + id, workflow: '{"a":1}', has_prompt: true, has_workflow: true,
  };
}
globalThis.fetch = (url) => {
  const raw = String(url);
  let m = raw.match(/\/api\/media\/(\d+)\/metadata/);
  if (m) return Promise.resolve(makeRes(200, metadataFor(parseInt(m[1], 10))));
  if (raw.indexOf("/api/media/folders") !== -1) return Promise.resolve(makeRes(200, { folders: [], total: 0 }));
  if (raw.indexOf("/api/media/tags") !== -1) return Promise.resolve(makeRes(200, { tags: [] }));
  if (raw.indexOf("/api/media?") !== -1) {
    const u = new URL(raw, "http://localhost");
    const page = parseInt(u.searchParams.get("page") || "1", 10);
    const limit = parseInt(u.searchParams.get("limit") || "60", 10);
    const start = (page - 1) * limit;
    return Promise.resolve(makeRes(200, { items: ITEMS.slice(start, start + limit), total: TOTAL, page, limit }));
  }
  return Promise.resolve(makeRes(404, { error: "not found" }));
};

/* ── <img> factices : jsdom ne charge pas les sous-ressources, on simule un
 *    load asynchrone pour que la file de pré-charge se draine vraiment. ──── */
const origCreateElement = window.document.createElement.bind(window.document);
window.document.createElement = function (tag, opts) {
  const el = origCreateElement(tag, opts);
  if (String(tag).toLowerCase() === "img") {
    let src = "";
    Object.defineProperty(el, "src", {
      configurable: true,
      get() { return src; },
      set(v) {
        src = String(v);
        setTimeout(() => { try { el.dispatchEvent(new window.Event("load")); } catch (e) { /* ignore */ } }, 0);
      },
    });
  }
  return el;
};

/* ── Chargement des briques + de l'adaptateur (aucune brique modifiée) ───── */
for (const b of ["collection", "thumbcache", "virtual-grid", "viewport", "lightbox", "infopane", "toast", "modal"]) {
  await import("../vendor/holaf/holaf-" + b + ".js");
}
if (window.HolafToast && window.HolafToast.configure) {
  window.HolafToast.configure({ duration: 0, position: "bottom-right" });
}

/* Capture des OPTIONS passées par l'hôte à la grille (preuve du bufferFactor). */
let gridOptions = null;
let gridCreates = 0;
const origGridCreate = window.HolafGrid.create.bind(window.HolafGrid);
window.HolafGrid.create = function (el, opts) {
  gridCreates++;
  gridOptions = opts;
  return origGridCreate(el, opts);
};

await import("./app-gallery.js");

const AppGallery = window.AppGallery;
const state = AppGallery.state;
const C = AppGallery.constants;

/* ── Compteurs / helpers ─────────────────────────────────────────────────── */
let n = 0;
function ok(cond, msg) { if (!cond) throw new Error("ASSERT FAIL: " + msg); n++; }
function eq(a, b, msg) { ok(a === b, msg + " (got " + JSON.stringify(a) + ", want " + JSON.stringify(b) + ")"); }
const flush = async (times = 25) => { for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0)); };
async function waitFor(fn, timeout = 8000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    try { if (fn()) return true; } catch (e) { /* ignore */ }
    await new Promise((r) => setTimeout(r, 10));
  }
  return false;
}

/* Géométrie attendue (mêmes entrées que la brique : itemSize=150, gap=12). */
const VH = ch;
let cols = 3, rowH = 162, buffer = 0;
function renderedWindow() {
  const lastRow = Math.ceil(state.collection.total / cols) - 1;
  const first = Math.max(0, Math.floor(Math.max(0, st - buffer) / rowH));
  const last = Math.min(lastRow, Math.ceil((st + VH + buffer) / rowH));
  return { first, last, lastRow };
}
function visibleStartRow() { return Math.floor(st / rowH); }
function visibleEndIndex() {
  const endRow = Math.ceil((st + VH) / rowH);
  return Math.min(state.collection.total - 1, endRow * cols + cols - 1);
}
function expectedExtraRows() {
  const w = renderedWindow();
  const above = [], below = [];
  for (let d = 1; d <= C.PRELOAD_EXTRA_ROWS; d++) {
    if (w.first - d >= 0) above.push(w.first - d);
    if (w.last + d <= w.lastRow) below.push(w.last + d);
  }
  // MÊME ordre que l'adaptateur : le côté vers lequel on défile d'abord
  // (sens déduit du déplacement de la plage visible), sinon alternance.
  const dir = state.preload ? state.preload.dir : 0;
  if (dir > 0) return below.concat(above);
  if (dir < 0) return above.concat(below);
  const rows = [];
  for (let i = 0; i < Math.max(above.length, below.length); i++) {
    if (i < above.length) rows.push(above[i]);
    if (i < below.length) rows.push(below[i]);
  }
  return rows;
}
/** Vraie si `queued` (rangées) suit l'ORDRE RELATIF de `expected`. */
function rowsFollow(queued, expected) {
  const pos = new Map();
  expected.forEach((r, i) => pos.set(r, i));
  const seq = queued.filter((r) => pos.has(r));
  for (let i = 1; i < seq.length; i++) {
    if (pos.get(seq[i]) < pos.get(seq[i - 1])) return false;
  }
  return true;
}
/** Bande de pré-charge courante (mêmes entrées que l'adaptateur). */
function band() {
  const w = renderedWindow();
  return {
    first: Math.max(0, w.first - C.PRELOAD_EXTRA_ROWS),
    last: Math.min(w.lastRow, w.last + C.PRELOAD_EXTRA_ROWS),
  };
}
/** Bande qu'AURAIT la rangée `row`, sans y déplacer la vue. */
function bandAt(row) {
  const saved = st;
  st = Math.max(0, row * rowH);
  const b = band();
  st = saved;
  return b;
}
function rowIds(rows) {
  const out = [];
  for (const r of rows) {
    for (let i = r * cols; i < (r + 1) * cols && i < state.collection.total; i++) out.push(i + 1);
  }
  return out;
}
function setScroll(row) {
  st = Math.max(0, row * rowH);
  gridEl.dispatchEvent(new window.Event("scroll"));
  state.grid.render(true); // rendu synchrone (émet onVisibleRange)
}
const pendingIds = () => Object.keys(state.preload.pending).map(Number);
const queuedIds = () => state.preload.queue.map((e) => e.item.id);
const queuedRows = () => state.preload.queue.map((e) => e.row);
const renderedIds = () => Array.from(window.document.querySelectorAll("#gallery-grid .holaf-grid-cell"))
  .map((c) => parseInt(c.dataset.holafIndex, 10)).filter((x) => isFinite(x));
const lruHas = (id) => !!state.thumbCache.peek(id);

/* ── Flag document.hidden pilotable (visibilitychange) ───────────────────── */
let hiddenFlag = false;
Object.defineProperty(window.document, "hidden", { configurable: true, get: () => hiddenFlag });
const setHidden = (v) => {
  hiddenFlag = v;
  window.document.dispatchEvent(new window.Event("visibilitychange"));
};

/* ═══ 1. Options de pré-charge passées aux briques ═══════════════════════ */
console.log("1. Options de pré-charge (grille + thumbcache)");
eq(typeof AppGallery.prefetchAround, "function", "AppGallery.prefetchAround exposé (testable)");
ok(C.GRID_BUFFER_FACTOR >= 2, "GRID_BUFFER_FACTOR ≥ 2 (zone rendue élargie, " + C.GRID_BUFFER_FACTOR + ")");
eq(C.GRID_GAP, 12, "GRID_GAP = 12");
ok(C.PRELOAD_EXTRA_ROWS >= 2, "PRELOAD_EXTRA_ROWS ≥ 2 (" + C.PRELOAD_EXTRA_ROWS + ")");
ok(C.PRELOAD_CONCURRENCY >= 1 && C.PRELOAD_CONCURRENCY <= 6, "concurrence de pré-charge bornée 1..6");
ok(C.PRELOAD_MAX_ITEMS >= 24, "budget de pré-charge ≥ 24 items (" + C.PRELOAD_MAX_ITEMS + ")");
ok(C.THUMB_CACHE_CAPACITY >= 3000, "capacité du LRU ≥ 3000 (" + C.THUMB_CACHE_CAPACITY + ")");
// Priorisation directionnelle + purge (adaptation du pack ComfyUI) : réglages
// bornés et nommés, valeurs par défaut vérifiées.
ok(Number.isInteger(C.PRELOAD_STALE_GRACE_ROWS) && C.PRELOAD_STALE_GRACE_ROWS >= 0
  && C.PRELOAD_STALE_GRACE_ROWS <= 2, "marge d'abandon de file bornée (" + C.PRELOAD_STALE_GRACE_ROWS + ")");
eq(C.PRELOAD_CANCEL_STALE_ROWS, 0, "annulation des pré-charges en vol DÉSACTIVÉE par défaut (sans gain mesuré)");

AppGallery.start();
await waitFor(() => state.collection && state.collection.total === TOTAL && renderedIds().length > 0);
await flush();

eq(gridCreates, 1, "une seule grille créée");
eq(gridOptions.bufferFactor, C.GRID_BUFFER_FACTOR, "bufferFactor passé à la grille = constante");
ok(gridOptions.bufferFactor >= 2, "bufferFactor ≥ 2 (contrat hôte → brique)");
eq(gridOptions.gap, C.GRID_GAP, "gap passé à la grille = constante");
eq(state.thumbCache.capacity, C.THUMB_CACHE_CAPACITY, "capacity demandée au thumbcache");
eq(state.thumbCache.concurrency, C.THUMB_CONCURRENCY, "concurrency demandée au thumbcache");
eq(state.thumbCache.stats().strategy, "url", "stratégie 'url' conservée (cache navigateur)");
eq(typeof state.visibilityHandler, "function", "listener de visibilité branché (poll d'onglet)");

cols = state.grid.getColumnCount();
rowH = state.grid.getMetrics().itemHeight + state.grid.getMetrics().gap;
buffer = VH * C.GRID_BUFFER_FACTOR;
eq(cols, 3, "3 colonnes (600 px / items 150 px)");
eq(rowH, state.grid.getMetrics().itemHeight + C.GRID_GAP, "hauteur de rangée = celle de la brique (métriques)");
ok(rowH > state.displaySize, "la hauteur de rangée suit la largeur de colonne (aspect 1), pas itemSize");

/* ═══ 2. Zone RENDUE élargie + données chargées d'avance ════════════════ */
console.log("2. Zone rendue élargie (bufferFactor) + ensureRange étendu");
const origEnsure = state.collection.ensureRange.bind(state.collection);
const ensureEnds = [];
state.collection.ensureRange = function (s, e) { ensureEnds.push(e); return origEnsure(s, e); };
setScroll(15);
state.collection.ensureRange = origEnsure;
const w15 = renderedWindow();
const leadAbove = visibleStartRow() - Math.floor(Math.min(...renderedIds()) / cols);
const oldLead = Math.ceil((VH * 1.5) / rowH); // ancien bufferFactor 1,5
ok(leadAbove >= oldLead, "rangées rendues AU-DESSUS du visible (" + leadAbove + " ≥ ancien " + oldLead + ")");
ok(w15.last - w15.first >= oldLead + 2,
  "amplitude rendue " + (w15.last - w15.first) + " rangées ≥ ancien + 2 (" + (oldLead + 2) + ")");
ok(renderedIds().length >= 24, "cellules rendues suffisantes pour la fenêtre visible");
eq(Math.min(...renderedIds()), w15.first * cols, "la fenêtre rendue commence à la rangée calculée (" + w15.first + ")");
ok(ensureEnds.some((e) => e > visibleEndIndex()),
  "ensureRange étendu AU-DELÀ du strict visible (rangées de pré-charge)");
ok(ensureEnds.every((e) => e <= TOTAL - 1), "ensureRange BORNÉ par total (" + TOTAL + ")");
ok(ensureEnds.every((e) => e >= 0), "ensureRange jamais négatif");
await waitFor(() => state.collection.isWindowLoaded(60));
await flush();
ok(state.collection.at(60) !== undefined, "page 2 chargée d'avance (données disponibles)");

/* ═══ 3. Pré-charge EXPLICITE au-delà du rendu, des DEUX côtés ═══════════ */
console.log("3. Pré-charge explicite (au-delà du rendu, priorité basse)");
const w = renderedWindow();
const extraRows = expectedExtraRows();
const extraIds = rowIds(extraRows);
ok(extraIds.length > 0, "rangées extra à pré-charger calculées (" + extraIds.length + " items)");
ok(extraRows.every((r) => r <= w.lastRow), "rangées extra bornées par le total");
ok(extraIds.every((id) => {
  const row = Math.floor((id - 1) / cols);
  return row < w.first || row > w.last;
}), "rangées extra STRICTEMENT au-delà de la fenêtre rendue");
ok(extraIds.indexOf(NO_THUMB_ID) !== -1, "le média sans vignette est bien DANS la zone extra (contrôle)");
await flush(40);
ok(extraIds.every((id) => id === NO_THUMB_ID ? !lruHas(id) : lruHas(id)),
  "rangées extra pré-chargées (URL en LRU), média sans vignette EXCLU");

// Spy de priorité : au-delà du rendu, `prefetch` doit demander en PRIORITÉ BASSE.
const origRequest = state.thumbCache.request.bind(state.thumbCache);
const reqLog = [];
state.thumbCache.request = function (item, priority) { reqLog.push({ id: item.id, p: priority }); return origRequest(item, priority); };
setScroll(20); // nouvelle zone fraîche (rangées 28..31 jamais demandées)
const extraIdsNow = rowIds(expectedExtraRows()); // rangées extra APRÈS le scroll
ok(state.preload.active <= C.PRELOAD_CONCURRENCY, "concurrence respectée dès la mise en file");
ok(pendingIds().length + queuedIds().length + state.preload.active <= C.PRELOAD_MAX_ITEMS,
  "budget par émission respecté");
await flush(40);
delete state.thumbCache.request;

const extraPrio = reqLog.filter((r) => extraIdsNow.indexOf(r.id) !== -1);
ok(extraPrio.length > 0, "la pré-charge a bien demandé les rangées extra (" + extraPrio.length + " demandes)");
ok(extraPrio.every((r) => r.p === window.HolafThumbCache.PRIORITY_LOW),
  "toutes les demandes de pré-charge sont en PRIORITÉ BASSE (prefetch)");
eq(reqLog.filter((r) => extraIdsNow.indexOf(r.id) !== -1 && r.p !== window.HolafThumbCache.PRIORITY_LOW).length,
  0, "aucune rangée extra demandée en priorité HAUTE");
ok(extraIdsNow.filter((id) => id !== NO_THUMB_ID).every((id) => lruHas(id)),
  "URL des rangées extra mémorisées en LRU (hits pour les cellules)");
ok(state.thumbCache.stats().size <= C.THUMB_CACHE_CAPACITY, "LRU ≤ capacité");

/* ═══ 3bis. Sens du défilement (ordre de file) + purge des sautées ══════ */
console.log("3bis. Ordre directionnel de la file + abandon des rangées périmées");
// Descente (14 → 18) : le côté BAS doit entrer en file EN PREMIER.
state.thumbCache.clear();
setScroll(14);
setScroll(18);
eq(state.preload.dir, 1, "scroll vers le bas → dir=+1");
ok(queuedIds().length > 0, "file non vide après déplacement (contrôle)");
ok(rowsFollow(queuedRows(), expectedExtraRows()), "file ordonnée : rangées SOUS le rendu d'abord (sens du défilement)");
ok(queuedRows().some((r) => r > renderedWindow().last), "des rangées sous la zone rendue sont en file");
await flush(40); // draine la file
// Remontée (18 → 10) : le côté HAUT doit repasser en premier.
state.thumbCache.clear();
setScroll(10);
eq(state.preload.dir, -1, "scroll vers le haut → dir=-1");
ok(rowsFollow(queuedRows(), expectedExtraRows()), "file ordonnée : rangées AU-DESSUS du rendu d'abord (sens inversé)");
await flush(40);
// PURGE : file garnie à la rangée 18, puis SAUT à la rangée 34 → toutes les
// rangées sorties de la bande cible (± marge) doivent être abandonnées.
state.thumbCache.clear();
setScroll(18);
await flush(2); // laisse la file se garnir SANS la drainer
const bTarget = bandAt(34);
const purgeLo = bTarget.first - C.PRELOAD_STALE_GRACE_ROWS;
const purgeHi = bTarget.last + C.PRELOAD_STALE_GRACE_ROWS;
ok(queuedRows().some((r) => r < purgeLo),
  "avant le saut, des rangées qui SORTIRONT de la bande cible sont en file (contrôle)");
setScroll(34); // saut brusque vers le bas
ok(queuedRows().every((r) => r >= purgeLo && r <= purgeHi),
  "après un saut, SEULES les rangées de la bande courante (± marge) restent en file");
ok(!queuedRows().some((r) => r < purgeLo), "rangées SAUTÉES / dépassées abandonnées avant téléchargement");
ok(queuedRows().length <= C.PRELOAD_MAX_ITEMS, "file toujours bornée après purge (" + queuedRows().length + ")");
await flush(40);

/* ═══ 4. Déduplication ═══════════════════════════════════════════════════ */
console.log("4. Déduplication (aucune vignette connue re-demandée)");
const missesBefore = state.thumbCache.stats().misses;
state.grid.render(true); // même position → même plage
await flush(30);
eq(state.thumbCache.stats().misses, missesBefore, "2e rendu identique → ZÉRO nouvelle demande");
eq(pendingIds().length + queuedIds().length, 0, "2e rendu identique → aucune nouvelle mise en file");
AppGallery.prefetchAround(w.first * cols, (w.last + 1) * cols - 1); // appel direct répété
await flush(10);
eq(state.thumbCache.stats().misses, missesBefore, "appel direct répété → ZÉRO nouvelle demande");
const renderedNow = renderedIds();
ok(pendingIds().every((id) => renderedNow.indexOf(id - 1) === -1), "aucun id rendu dans la file de pré-charge");

/* ═══ 5. visibilitychange : pause puis reprise ══════════════════════════ */
console.log("5. Onglet masqué (pause) / retour (reprise)");
state.thumbCache.clear(); // zone « jamais vue » CONTRÔLÉE (cache vidé)
setScroll(38);            // bas de liste : seules des rangées AU-DESSUS sont en file
await flush(2);
const freshRows = queuedRows().slice();
ok(freshRows.length > 0, "zone extra fraîche en file avant masquage (" + freshRows.length + " rangées)");
ok(freshRows.every((r) => r >= band().first && r <= band().last), "rangées en file dans la bande courante");
setHidden(true);
eq(state.preload.queue.length, 0, "onglet masqué → file de pré-charge VIDÉE");
ok(state.preload.active <= C.PRELOAD_CONCURRENCY, "onglet masqué → seuls les chargements en vol subsistent");
await flush(10);
eq(pendingIds().length, 0, "chargements en vol terminés → plus rien en attente (onglet masqué)");
setHidden(false); // retour → reprise de lastVisibleRange
await flush(40);
ok(rowIds(freshRows).every((id) => lruHas(id)), "retour d'onglet → pré-charge REPRISE (zone fraîche complète)");
// Contrôle négatif : masqué, un appel direct ne déclenche AUCUNE demande.
setHidden(true);
const missesHidden = state.thumbCache.stats().misses;
AppGallery.prefetchAround(0, cols - 1);
await flush(10);
eq(state.thumbCache.stats().misses, missesHidden, "onglet masqué → ZÉRO nouvelle demande (même en appel direct)");
setHidden(false);

/* ═══ 6. Bornes : bas de liste ══════════════════════════════════════════ */
console.log("6. Bornes en bas de liste");
setScroll(38);
await flush(40);
const wBottom = renderedWindow();
ok(wBottom.last <= wBottom.lastRow, "dernière rangée rendue ≤ dernière rangée du total");
ok(pendingIds().every((id) => id >= 1 && id <= TOTAL), "aucune pré-charge hors [1, total]");
ok(queuedIds().every((id) => id >= 1 && id <= TOTAL), "file de pré-charge bornée par le total");
ok(pendingIds().concat(queuedIds()).every((id) => Math.ceil(id / cols) <= Math.ceil(TOTAL / cols)),
  "aucune pré-charge au-delà de la dernière rangée");

/* ═══ 7. Contrôles négatifs de bord ══════════════════════════════════════ */
console.log("7. Contrôles négatifs de bord");
setScroll(0); // haut de liste : rien au-dessus
await flush(20);
const wTop = renderedWindow();
eq(wTop.first, 0, "en haut, la fenêtre rendue part de la rangée 0");
ok(pendingIds().every((id) => Math.floor((id - 1) / cols) >= wTop.first),
  "en haut, AUCUNE rangée pré-chargée AU-DESSUS (rien n'existe)");
ok(!lruHas(NO_THUMB_ID), "média sans vignette JAMAIS mis en cache (toutes phases)");

/* ═══ 7bis. Reload : file vidée + sens réinitialisé ═════════════════════ */
console.log("7bis. Reload complet (filtres) → file vidée, sens réinitialisé");
state.thumbCache.clear();
setScroll(20);
ok(queuedIds().length + state.preload.active > 0, "pré-charge programmée avant reload (contrôle)");
AppGallery.reload();
eq(state.preload.queue.length, 0, "reload → file de pré-charge vidée (items de l'ancien jeu)");
eq(state.preload.dir, 0, "reload → sens du défilement réinitialisé");
eq(state.preload.lastStart, null, "reload → dernière plage visible oubliée");
await waitFor(() => state.collection && state.collection.total === TOTAL && state.collection.isWindowLoaded(0));
await flush(10);

/* ═══ 8. stop() vide la file de pré-charge ══════════════════════════════ */
console.log("8. Sortie d'onglet → file vidée");
state.thumbCache.clear(); // force une zone « inconnue » (preuve que la file se remplit)
setScroll(20);
ok(pendingIds().length + queuedIds().length + state.preload.active > 0, "pré-charge de nouveau programmée");
AppGallery.stop();
eq(state.preload.queue.length, 0, "stop() → file de pré-charge vidée");
AppGallery.start();
await flush(10);

/* ═══ 9. Briques vendor non modifiées (preuve d'identité) ═══════════════ */
console.log("9. Briques holaf non modifiées");
{
  const LIB = "/projects/holaf-lib/js/";
  const pairs = ["collection", "thumbcache", "virtual-grid", "viewport", "lightbox", "infopane", "toast", "modal"];
  if (existsSync(LIB)) {
    let identical = 0;
    for (const b of pairs) {
      const a = readFileSync(resolve(HERE, "..", "vendor", "holaf", "holaf-" + b + ".js"), "utf8");
      const lib = readFileSync(LIB + "holaf-" + b + ".js", "utf8");
      if (a === lib) identical++;
    }
    eq(identical, pairs.length, "les " + pairs.length + " briques vendor sont byte-identiques à holaf-lib");
  } else {
    console.log("  (holaf-lib introuvable → preuve d'identité ignorée)");
  }
}

console.log("\n✅ test_gallery_preload : " + n + " assertions PASSENT");
