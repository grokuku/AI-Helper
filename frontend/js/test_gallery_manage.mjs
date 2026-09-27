// Test headless (jsdom) — Onglet « Galerie » du front AI-Helper : GESTION.
// Usage : node frontend/js/test_gallery_manage.mjs   (via ./js/run_js_tests.sh)
//
// Suite frère de test_gallery.mjs (CONSULTATION). Elle prouve l'adaptation de
// app-gallery.js pour la GESTION, SANS modifier aucune brique holaf :
//   1. construction PURE de la requête GET /api/media (filtres + tri + statut) ;
//   2. filtres branchés sur la toolbar (type / sous-dossier / nom / dates / tri) ;
//   3. bascule vue Médias ⇄ Corbeille (status=trashed) + UI distincte ;
//   4. suppression optimiste + ROLLBACK sur échec (contrôle négatif : pas de
//      rollback si l'API réussit) ;
//   5. restauration / purge (avec confirmation) ;
//   6. actions groupées : payload { ids }, récap { n, skipped } ;
//   7. téléchargement (URL appelée) unitaire / groupé / depuis la lightbox ;
//   8. boutons désactivés pendant un appel réseau ;
//   9. toast brique chargée (succès/erreur) + preuve vendor == holaf-lib ;
//  10. correctifs UX sélection : clic dans le VIDE → désélection, case à
//      cocher (toggle unitaire), non-régressions clic/ctrl/shift/action/dblclick ;
//  11. touche Suppr en visionneuse : DELETE de l'ITEM AFFICHÉ (corbeille),
//      navigation vers la suivante (ou précédente si dernière / fermeture
//      si plus rien), erreurs sans navigation, négatifs (champ, modificateur,
//      répétition, visionneuse fermée).
import assert from "node:assert";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { loadJsdomOrSkip } from "./test_helpers/jsdom_loader.mjs";

const JSDOM = await loadJsdomOrSkip("test_gallery_manage");
const HERE = dirname(fileURLToPath(import.meta.url));

/* ── DOM de l'onglet Galerie (avec toolbar GESTION) ──────────────────────── */
const dom = new JSDOM(`<!doctype html><html><body>
  <div id="tab-gallery">
    <input type="range" id="gallery-thumb-size" min="80" max="300" step="10" value="150">
    <span id="gallery-thumb-size-value"></span>
    <span id="gallery-thumb-resolution"></span>
    <div class="gallery-view-toggle">
      <button id="gallery-view-normal" aria-selected="true" class="is-active">Médias</button>
      <button id="gallery-view-trash" aria-selected="false">Corbeille</button>
    </div>
    <span id="gallery-selected" class="hidden"></span>
    <span id="gallery-count"></span>
    <select id="gallery-filter-kind"><option value="">Tous</option><option value="image">Images</option><option value="video">Vidéos</option><option value="audio">Audio</option></select>
    <button type="button" id="gallery-filter-folders" aria-haspopup="dialog">Dossiers : tous</button>
    <button type="button" id="gallery-filter-favorite" aria-pressed="false">★ Favoris</button>
    <input id="gallery-search" type="search">
    <input id="gallery-filter-from" type="date">
    <input id="gallery-filter-to" type="date">
    <select id="gallery-filter-sort">
      <option value="created_at_desc">Récents</option>
      <option value="created_at_asc">Anciens</option>
      <option value="name_asc">Nom (A→Z)</option>
      <option value="size_desc">Taille (↓)</option>
    </select>
    <div class="gallery-body">
      <div id="gallery-main">
        <div id="gallery-grid"></div>
        <div id="gallery-loading"></div>
        <div id="gallery-empty" class="hidden"></div>
        <div id="gallery-error" class="hidden"></div>
      </div>
      <!-- Colonne de droite : infos + barre d'actions EN PIED (reflète index.html) -->
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
// Pas de showConfirm global → galleryConfirm retombe sur window.confirm (contrôlé ci-dessous).

/* ── Géométrie du conteneur de grille ────────────────────────────────────── */
const gridEl = window.document.getElementById("gallery-grid");
let cw = 600, ch = 400, st = 0;
Object.defineProperty(gridEl, "clientWidth", { configurable: true, get: () => cw });
Object.defineProperty(gridEl, "clientHeight", { configurable: true, get: () => ch });
Object.defineProperty(gridEl, "scrollTop", { configurable: true, get: () => st, set: (v) => { st = Math.max(0, v); } });
gridEl.getBoundingClientRect = () => ({ left: 0, top: 0, right: cw, bottom: ch, width: cw, height: ch, x: 0, y: 0, toJSON() {} });

/* ── Jeu de données + fetch simulé ───────────────────────────────────────── */
const SKIP_ID = 3; // id « ignoré » par le backend simulé (déjà disparu)
function makeItem(id) {
  const kind = (id % 5 === 0) ? "video" : (id % 7 === 0) ? "audio" : "image";
  const trashed = (id === 2 || id === 4);
  return {
    id,
    filename: "media_" + id + (kind === "audio" ? ".mp3" : kind === "video" ? ".mp4" : ".png"),
    kind,
    size: 1000 * id,
    created_at: "2025-01-" + String(id).padStart(2, "0") + "T00:00:00Z",
    subfolder: "shot" + (id % 3),
    status: trashed ? "trashed" : "active",
    trashed,
    trashed_at: trashed ? "2025-02-01T00:00:00Z" : null,
    thumb_available: kind !== "audio",
    has_prompt: true, has_workflow: false,
    width: 512, height: 512,
    favorite: (id % 4 === 0),
  };
}
let items = [];
for (let i = 1; i <= 24; i++) items.push(makeItem(i));
const ACTIVE_TOTAL = items.filter((i) => i.status !== "trashed").length;
const TRASH_TOTAL = items.filter((i) => i.status === "trashed").length;

const calls = []; // { method, url, body }
let failNextDelete = false;
let failNextFavorite = false;
let deferred = null;
function defer() { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; }

function makeRes(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, text: async () => JSON.stringify(body) };
}
function metadataFor(id) {
  const it = items.find((x) => x.id === id) || makeItem(id);
  return {
    id, filename: it.filename, subfolder: it.subfolder, kind: it.kind, ext: "", size: it.size,
    created_at: it.created_at, width: 512, height: 512, ratio: 1,
    duration: it.kind === "audio" ? 12.5 : null, duration_ms: it.kind === "audio" ? 12500 : null,
    codec: it.kind === "video" ? "h264" : null,
    prompt: "prompt " + id, workflow: '{"a":1}', has_prompt: true, has_workflow: true,
    favorite: !!it.favorite,
  };
}
function sortCmp(sort) {
  if (sort === "created_at_asc") return (a, b) => (a.created_at < b.created_at ? -1 : 1);
  if (sort === "name_asc") return (a, b) => (a.filename < b.filename ? -1 : 1);
  if (sort === "size_desc") return (a, b) => b.size - a.size;
  return (a, b) => (a.created_at < b.created_at ? 1 : -1); // created_at_desc
}

globalThis.fetch = (url, opts = {}) => {
  const method = (opts.method || "GET").toUpperCase();
  const rawUrl = String(url);
  const body = opts.body ? JSON.parse(opts.body) : null;
  calls.push({ method, url: rawUrl, body });
  if (deferred) { const d = deferred; deferred = null; return d.promise; }

  let m = rawUrl.match(/\/api\/media\/(\d+)\/metadata/);
  if (m) return Promise.resolve(makeRes(200, metadataFor(parseInt(m[1], 10))));

  // Actions groupées (pas d'id dans l'URL).
  m = rawUrl.match(/\/api\/media\/(delete|restore|purge)$/);
  if (m) {
    const op = m[1];
    const ids = (body && Array.isArray(body.ids)) ? body.ids : [];
    const done = ids.filter((id) => id !== SKIP_ID);
    const skipped = ids.filter((id) => id === SKIP_ID);
    for (const id of done) {
      const it = items.find((x) => x.id === id);
      if (!it) continue;
      if (op === "delete") { it.status = "trashed"; it.trashed = true; }
      else if (op === "restore") { it.status = "active"; it.trashed = false; }
      else if (op === "purge") items = items.filter((x) => x.id !== id);
    }
    const key = op === "delete" ? "trashed" : op === "restore" ? "restored" : "purged";
    const out = {}; out[key] = done.length; out.skipped = skipped;
    return Promise.resolve(makeRes(200, out));
  }

  // Favori : actions unitaire et groupée (avant les handlers « id + DELETE »).
  m = rawUrl.match(/\/api\/media\/(\d+)\/favorite$/);
  if (m && method === "POST") {
    if (failNextFavorite) { failNextFavorite = false; return Promise.resolve(makeRes(500, { error: "boom" })); }
    const it = items.find((x) => x.id === parseInt(m[1], 10));
    if (it) it.favorite = !!(body && body.favorite);
    return Promise.resolve(makeRes(200, it ? Object.assign({}, it) : { id: parseInt(m[1], 10), favorite: !!(body && body.favorite) }));
  }
  if (rawUrl.match(/\/api\/media\/favorite$/) && method === "POST") {
    const ids = (body && Array.isArray(body.ids)) ? body.ids : [];
    const val = !!(body && body.favorite);
    const updated = []; const skipped = [];
    for (const id of ids) {
      if (id === SKIP_ID || !items.find((x) => x.id === id)) { skipped.push(id); continue; }
      items.find((x) => x.id === id).favorite = val;
      updated.push(id);
    }
    return Promise.resolve(makeRes(200, { updated: updated.length, skipped }));
  }

  // Actions unitaires (id dans l'URL).
  m = rawUrl.match(/\/api\/media\/(\d+)$/);
  if (m && method === "DELETE") {
    if (failNextDelete) { failNextDelete = false; return Promise.resolve(makeRes(500, { error: "boom" })); }
    const it = items.find((x) => x.id === parseInt(m[1], 10));
    if (it) { it.status = "trashed"; it.trashed = true; }
    return Promise.resolve(makeRes(200, { ok: true }));
  }
  m = rawUrl.match(/\/api\/media\/(\d+)\/(restore|purge)$/);
  if (m) {
    const id = parseInt(m[1], 10);
    if (m[2] === "purge") {
      if (failNextDelete) { failNextDelete = false; return Promise.resolve(makeRes(500, { error: "boom" })); }
      items = items.filter((x) => x.id !== id);
    } else {
      const it = items.find((x) => x.id === id);
      if (it) { it.status = "active"; it.trashed = false; }
    }
    return Promise.resolve(makeRes(200, {}));
  }

  // Liste.
  // Liste des DOSSIERS (modale multi-dossiers) — AVANT la liste, car
  // `/api/media/folders` ne contient pas « /api/media? ».
  if (rawUrl.indexOf("/api/media/folders") !== -1) {
    const u = new URL(rawUrl, "http://localhost");
    const status = u.searchParams.get("status") || "";
    const pool = (status === "trashed")
      ? items.filter((i) => i.status === "trashed")
      : items.filter((i) => i.status !== "trashed");
    const counts = {};
    for (const it of pool) { const sf = it.subfolder || ""; counts[sf] = (counts[sf] || 0) + 1; }
    const folders = Object.keys(counts).sort((a, b) => a.localeCompare(b))
      .map((sf) => ({ subfolder: sf, count: counts[sf] }));
    const total = folders.reduce((a, f) => a + f.count, 0);
    return Promise.resolve(makeRes(200, { folders, total }));
  }

  if (rawUrl.indexOf("/api/media?") !== -1) {
    const u = new URL(rawUrl, "http://localhost");
    const page = parseInt(u.searchParams.get("page"), 10) || 1;
    const limit = parseInt(u.searchParams.get("limit"), 10) || 60;
    const status = u.searchParams.get("status") || "";
    const kind = u.searchParams.get("kind") || "";
    const subfolder = u.searchParams.get("subfolder") || "";
    const subfolders = u.searchParams.getAll("subfolders");
    const q = u.searchParams.get("q") || "";
    const from = u.searchParams.get("from") || "";
    const to = u.searchParams.get("to") || "";
    const sort = u.searchParams.get("sort") || "created_at_desc";
    const favorite = u.searchParams.get("favorite") || "";
    let list = items.slice();
    list = (status === "trashed") ? list.filter((i) => i.status === "trashed") : list.filter((i) => i.status !== "trashed");
    if (kind) list = list.filter((i) => i.kind === kind);
    if (favorite === "1" || favorite === "true") list = list.filter((i) => !!i.favorite);
    else if (favorite === "0" || favorite === "false") list = list.filter((i) => !i.favorite);
    if (subfolder) list = list.filter((i) => i.subfolder === subfolder);
    if (subfolders.length) {
      const wanted = [];
      for (const raw of subfolders) for (const p of raw.split(",")) wanted.push(p);
      list = list.filter((i) => wanted.indexOf(i.subfolder || "") !== -1);
    }
    if (q) list = list.filter((i) => i.filename.toLowerCase().indexOf(q.toLowerCase()) !== -1);
    if (from) list = list.filter((i) => i.created_at >= from);
    if (to) list = list.filter((i) => i.created_at <= to + "T23:59:59Z");
    list.sort(sortCmp(sort));
    const total = list.length;
    const start = (page - 1) * limit;
    return Promise.resolve(makeRes(200, { items: list.slice(start, start + limit), total, page, limit }));
  }
  return Promise.resolve(makeRes(404, { error: "not found" }));
};

/* ── Chargement des briques + de l'adaptateur ────────────────────────────── */
for (const b of ["collection", "thumbcache", "virtual-grid", "viewport", "lightbox", "infopane", "toast", "modal"]) {
  await import("../vendor/holaf/holaf-" + b + ".js");
}
if (window.HolafToast && window.HolafToast.configure) {
  window.HolafToast.configure({ duration: 0, position: "bottom-right" }); // toasts persistants (pas de timer)
}
await import("./app-gallery.js");

const AppGallery = window.AppGallery;
const state = AppGallery.state;

/* ── Compteurs / helpers ─────────────────────────────────────────────────── */
let n = 0;
function ok(cond, msg) { if (!cond) throw new Error("ASSERT FAIL: " + msg); n++; }
function eq(a, b, msg) { ok(a === b, msg + " (got " + JSON.stringify(a) + ", want " + JSON.stringify(b) + ")"); }
const settle = async (times = 10) => { for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0)); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const listCalls = () => calls.filter((c) => c.method === "GET" && c.url.indexOf("/api/media?") !== -1);
const lastListCall = () => listCalls()[listCalls().length - 1];
const setSelect = (id, v) => { const el = window.document.getElementById(id); el.value = v; el.dispatchEvent(new window.Event("change", { bubbles: true })); };
const click = (el, mods = {}) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, shiftKey: !!mods.shift, ctrlKey: !!mods.ctrl }));
const dbl = (el) => el.dispatchEvent(new window.MouseEvent("dblclick", { bubbles: true }));
const keyDown = (el, k, mods = {}) => el.dispatchEvent(new window.KeyboardEvent("keydown", {
  key: k, bubbles: true, ctrlKey: !!mods.ctrl, metaKey: !!mods.meta, altKey: !!mods.alt, repeat: !!mods.repeat,
}));
const cellEl = (i) => window.document.querySelector('.holaf-grid-surface [data-holaf-index="' + i + '"]');
const surfaceEl = () => window.document.querySelector(".holaf-grid-surface");
const actionsBar = () => window.document.getElementById("gallery-actions");
function collectionHasId(id) {
  let found = false;
  state.collection.forEachLoaded((x) => { if (String(x.id) === String(id)) found = true; });
  return found;
}

/* ═══ 1. Requête GET /api/media : construction PURE ═══════════════════════ */
console.log("1. Construction de la requête (filtres + tri + statut)");
ok(typeof AppGallery.mediaUrl === "function", "AppGallery.mediaUrl exposé");
eq(AppGallery.constants.SEARCH_DEBOUNCE, 350, "debounce de recherche = 350 ms");
eq(AppGallery.constants.SORTS.join(","), "created_at_desc,created_at_asc,name_asc,size_desc", "tris exposés");
eq(AppGallery.mediaUrl(1, 60, {}, "created_at_desc"), "/api/media?page=1&limit=60&sort=created_at_desc", "requête sans filtre");
eq(AppGallery.mediaUrl(2, 30, { kind: "video" }, "name_asc"), "/api/media?page=2&limit=30&sort=name_asc&kind=video", "kind + tri");
{
  const url = AppGallery.mediaUrl(1, 60, { kind: "video", subfolder: "shot1", q: "a b", from: "2025-01-01", to: "2025-02-01", status: "trashed" }, "size_desc");
  ok(url.indexOf("kind=video") !== -1, "filtre kind présent");
  ok(url.indexOf("subfolder=shot1") !== -1, "filtre subfolder présent");
  ok(url.indexOf("q=a%20b") !== -1, "filtre q encodé présent");
  ok(url.indexOf("from=2025-01-01") !== -1, "filtre from présent");
  ok(url.indexOf("to=2025-02-01") !== -1, "filtre to présent");
  ok(url.indexOf("status=trashed") !== -1, "filtre status présent");
  ok(url.indexOf("sort=size_desc") !== -1, "tri size_desc présent");
}
// Contrôles négatifs : un filtre vide NE DOIT PAS apparaître.
{
  const url = AppGallery.mediaUrl(1, 60, {}, "created_at_desc");
  ok(url.indexOf("kind=") === -1, "[négatif] aucun kind= sans filtre");
  ok(url.indexOf("subfolder=") === -1, "[négatif] aucun subfolder= sans filtre");
  ok(url.indexOf("q=") === -1, "[négatif] aucun q= sans filtre");
  ok(url.indexOf("status=") === -1, "[négatif] aucun status= en vue normale");
  ok(AppGallery.mediaUrl(1, 60, {}, "").indexOf("sort=created_at_desc") !== -1, "[négatif] tri vide → défaut created_at_desc");
}
// Contrôle négatif pur : un paramètre omis n'est jamais fabriqué.
eq(AppGallery.mediaUrl(1, 60, { kind: "audio" }, "created_at_desc").indexOf("kind=image"), -1, "[négatif] ne fabrique pas kind=image");

/* ═══ 2. Démarrage : vue normale, sans status= ════════════════════════════ */
console.log("2. Démarrage (vue Médias)");
AppGallery.start();
await settle();
eq(state.collection.total, ACTIVE_TOTAL, "total vue Médias = médias actifs");
{
  const first = listCalls()[0];
  ok(first.url.indexOf("kind=") === -1 && first.url.indexOf("status=") === -1, "1re page : aucun filtre");
}
eq(window.document.getElementById("gallery-view-normal").getAttribute("aria-selected"), "true", "vue Médias active");

/* ═══ 3. Filtres branchés sur la toolbar ══════════════════════════════════ */
console.log("3. Filtres (type / sous-dossier / nom / dates / tri)");
setSelect("gallery-filter-kind", "video");
await settle();
ok(lastListCall().url.indexOf("kind=video") !== -1, "filtre TYPE → kind=video dans la requête");
eq(state.filters.kind, "video", "state.filters.kind = video");
{
  let allVideo = true;
  state.collection.forEachLoaded((x) => { if (x.kind !== "video") allVideo = false; });
  ok(allVideo, "tous les items rendus sont des vidéos");
}
setSelect("gallery-filter-sort", "size_desc");
await settle();
ok(lastListCall().url.indexOf("sort=size_desc") !== -1, "filtre TRI → sort=size_desc");

// Dates.
const fromEl = window.document.getElementById("gallery-filter-from");
fromEl.value = "2025-01-10";
fromEl.dispatchEvent(new window.Event("change", { bubbles: true }));
await settle();
ok(lastListCall().url.indexOf("from=2025-01-10") !== -1, "filtre DATE (du) → from=");

// Recherche avec debounce.
AppGallery.resetFilters();
await settle();
{
  const search = window.document.getElementById("gallery-search");
  const before = listCalls().length;
  search.value = "media_1";
  search.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(120);
  eq(listCalls().length, before, "[debounce] aucun fetch avant 350 ms");
  await sleep(400);
  await settle();
  ok(lastListCall().url.indexOf("q=media_1") !== -1, "recherche NOM → q=media_1 après debounce");
}
AppGallery.resetFilters();
await settle();
ok(lastListCall().url.indexOf("q=") === -1 && lastListCall().url.indexOf("kind=") === -1, "[négatif] reset → plus aucun filtre");
eq(window.document.getElementById("gallery-filter-sort").value, "created_at_desc", "reset → tri revenu au défaut");

/* ═══ 3bis. Filtre DOSSIERS : bouton + modale (liste + comptes) ══════════ */
console.log("3bis. Filtre dossiers : bouton + modale");
// (a) Le BOUTON remplace la text box. Contrôle négatif : plus d'input.
ok(!window.document.getElementById("gallery-filter-subfolder"), "[négatif] plus d'input « Sous-dossier »");
ok(!window.document.getElementById("gallery-subfolders"), "[négatif] plus de datalist sous-dossiers");
{
  const btn = window.document.getElementById("gallery-filter-folders");
  ok(!!btn, "bouton « Dossiers » présent");
  eq(btn.tagName, "BUTTON", "le filtre dossiers est un <button>");
  eq(btn.textContent, "Dossiers : tous", "état initial : « Dossiers : tous »");
}
// (b) Construction PURE de l'URL multi-dossiers (répétée, encodée).
eq(AppGallery.mediaUrl(1, 60, { subfolders: ["a", "b/c"] }, "created_at_desc"),
   "/api/media?page=1&limit=60&sort=created_at_desc&subfolders=a&subfolders=b%2Fc",
   "URL multi-dossiers : paramètre répété (encodé)");
eq(AppGallery.mediaUrl(1, 60, { subfolders: [] }, "created_at_desc").indexOf("subfolders="), -1,
   "[négatif] liste vide → aucun paramètre subfolders");
eq(AppGallery.foldersUrl(), "/api/media/folders", "URL de la liste des dossiers (vue normale)");

// (c) Ouverture : liste alimentée par GET /api/media/folders + comptes affichés.
AppGallery.resetFilters();
await settle();
AppGallery.openFoldersModal();
await settle(8);
const modalRoot = () => window.document.getElementById("gallery-folders-modal");
ok(!!modalRoot(), "modale ouverte (#gallery-folders-modal)");
ok(modalRoot().classList.contains("holaf-modal-root"), "racine de la brique holaf-modal (vendor)");
// DÉPLAÇABLE + REDIMENSIONNABLE : 8 poignées + curseur « move » sur le header.
eq(modalRoot().querySelectorAll(".holaf-modal-resize").length, 8, "8 poignées de redimensionnement (resizable)");
eq(modalRoot().querySelector(".holaf-modal-header").style.cursor, "move", "header déplaçable (draggable)");
ok(calls.some((c) => c.url.indexOf("/api/media/folders") !== -1), "GET /api/media/folders appelé (pas la liste des médias)");
{
  const rows = Array.prototype.map.call(modalRoot().querySelectorAll(".gallery-folder-item"), (r) => ({
    name: r.querySelector(".gallery-folder-name").textContent,
    count: r.querySelector(".gallery-folder-count").textContent,
    checked: r.querySelector(".gallery-folder-check").checked,
  }));
  ok(rows.length >= 3, "au moins 3 dossiers listés (shot0/1/2)");
  const shot1 = rows.find((r) => r.name === "shot1");
  ok(!!shot1, "dossier shot1 listé");
  const expect = items.filter((i) => i.status !== "trashed" && i.subfolder === "shot1").length;
  eq(shot1 && shot1.count, "(" + expect + ")", "compte EXACT du dossier affiché entre parenthèses");
  ok(rows.every((r) => !r.checked), "aucune case cochée à l'ouverture (aucune sélection)");
}

// (d) Tout / Aucun / Inverser.
const clickFolderAction = (label) => {
  const b = Array.prototype.slice.call(modalRoot().querySelectorAll(".gallery-folders-action"))
    .find((x) => x.textContent === label);
  b.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
};
const allBoxes = () => Array.prototype.slice.call(modalRoot().querySelectorAll(".gallery-folder-check"));
clickFolderAction("Tout");
await settle(2);
ok(allBoxes().every((b) => b.checked), "Tout → toutes les cases cochées");
clickFolderAction("Aucun");
await settle(2);
ok(allBoxes().every((b) => !b.checked), "Aucun → aucune case cochée");
clickFolderAction("Inverser");
await settle(2);
ok(allBoxes().every((b) => b.checked), "Inverser (0 coché) → toutes cochées");
clickFolderAction("Inverser");
await settle(2);
ok(allBoxes().every((b) => !b.checked), "Inverser (toutes) → aucune cochée");

// (e) Cocher 2 dossiers puis APPLIQUER → query multi + reload + état du bouton.
{
  const toggle = (name) => {
    const row = Array.prototype.slice.call(modalRoot().querySelectorAll(".gallery-folder-item"))
      .find((r) => r.querySelector(".gallery-folder-name").textContent === name);
    row.querySelector(".gallery-folder-check").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  };
  toggle("shot0");
  toggle("shot2");
  await settle(2);
  const before = listCalls().length;
  const applyBtn = Array.prototype.slice.call(modalRoot().querySelectorAll(".holaf-modal-btn"))
    .find((b) => b.textContent === "Appliquer");
  applyBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle(8);
  ok(!modalRoot(), "modale fermée après Appliquer");
  ok(listCalls().length > before, "Appliquer → reload de la galerie");
  const url = lastListCall().url;
  ok(url.indexOf("subfolders=shot0") !== -1 && url.indexOf("subfolders=shot2") !== -1,
     "la requête contient les 2 dossiers sélectionnés (paramètre multi)");
  ok(url.indexOf("subfolders=shot1") === -1, "[négatif] le dossier NON coché n'est pas dans la requête");
  eq(state.selectedFolders.join(","), "shot0,shot2", "état = dossiers sélectionnés (triés)");
  eq(window.document.getElementById("gallery-filter-folders").textContent, "Dossiers (2)", "bouton affiche « Dossiers (2) »");
  let onlyChosen = true;
  state.collection.forEachLoaded((x) => { if (x.subfolder !== "shot0" && x.subfolder !== "shot2") onlyChosen = false; });
  ok(onlyChosen, "grille filtrée sur les dossiers choisis");
  eq(state.selectionIds.length, 0, "sélection vidée après application");
}

// (f) ANNULER → aucun changement (contrôle négatif).
{
  AppGallery.resetFilters();
  await settle();
  AppGallery.openFoldersModal();
  await settle(8);
  const before = listCalls().length;
  modalRoot().querySelector(".gallery-folder-item .gallery-folder-check")
    .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle(2);
  const cancelBtn = Array.prototype.slice.call(modalRoot().querySelectorAll(".holaf-modal-btn"))
    .find((b) => b.textContent === "Annuler");
  cancelBtn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle(6);
  ok(!modalRoot(), "modale fermée après Annuler");
  eq(state.selectedFolders.length, 0, "[négatif] Annuler → aucune sélection");
  eq(window.document.getElementById("gallery-filter-folders").textContent, "Dossiers : tous", "[négatif] Annuler → bouton inchangé");
  eq(listCalls().length, before, "[négatif] Annuler → AUCUN reload");
}

// (g) Réinitialiser vide aussi les dossiers + non-régression du bouton.
{
  AppGallery.applyFolders(["shot1"]);
  await settle();
  eq(window.document.getElementById("gallery-filter-folders").textContent, "Dossiers (1)", "1 dossier → « Dossiers (1) »");
  ok(lastListCall().url.indexOf("subfolders=shot1") !== -1, "applyFolders → subfolders=shot1");
  AppGallery.resetFilters();
  await settle();
  eq(state.selectedFolders.length, 0, "reset → dossiers vidés");
  eq(window.document.getElementById("gallery-filter-folders").textContent, "Dossiers : tous", "reset → bouton « tous »");
  ok(lastListCall().url.indexOf("subfolders=") === -1, "[négatif] reset → aucun paramètre subfolders");
}

/* ═══ 4. Sélection → barre d'actions, vidée au changement de filtre ═══════ */
console.log("4. Barre d'actions contextuelle");
click(cellEl(0));
await settle();
eq(state.selectionIds.length, 1, "1 media sélectionné");
ok(!actionsBar().classList.contains("hidden"), "barre d'actions visible");
ok(!window.document.getElementById("gallery-action-delete").classList.contains("hidden"), "vue Médias : bouton Supprimer visible");
ok(window.document.getElementById("gallery-action-restore").classList.contains("hidden"), "vue Médias : Restaurer masqué");
ok(window.document.getElementById("gallery-action-purge").classList.contains("hidden"), "vue Médias : Purger masqué");
// EMPLACEMENT : la barre est EN PIED DE LA COLONNE DE DROITE (#gallery-side),
// pas un bandeau au-dessus de la grille → son apparition ne décale pas la grille.
{
  const sideEl = window.document.getElementById("gallery-side");
  const mainEl = window.document.getElementById("gallery-main");
  ok(!!sideEl, "colonne de droite (#gallery-side) présente dans le DOM");
  eq(actionsBar().parentElement, sideEl, "barre d'actions enfant direct de #gallery-side (colonne droite)");
  ok(sideEl.contains(window.document.getElementById("gallery-info")), "même colonne que #gallery-info");
  ok(!mainEl.contains(actionsBar()), "[négatif] la barre n'est PAS dans la zone de la grille → aucun reflow");
  ok(!mainEl.contains(sideEl), "[négatif] la colonne droite est hors de la zone de la grille");
  ok(!actionsBar().classList.contains("hidden"), "barre visible avec 1 sélection");
}

// Contrôle négatif : un changement de filtre recharge ET vide la sélection.
setSelect("gallery-filter-kind", "image");
await settle();
eq(state.selectionIds.length, 0, "[négatif] filtre → sélection vidée");
ok(actionsBar().classList.contains("hidden"), "[négatif] filtre → barre d'actions masquée");
AppGallery.resetFilters();
await settle();

/* ═══ 4bis. Correctifs UX sélection (fond / ctrl / shift / case à cocher) ══ */
console.log("4bis. UX sélection : clic dans le vide, ctrl/shift, case à cocher");
const idAt = (i) => state.collection.at(i).id;

// (2) Clic dans la ZONE VIDE de la grille → sélection vidée + barre masquée.
{
  click(cellEl(0));
  await settle();
  eq(state.selectionIds.length, 1, "prérequis : 1 média sélectionné");
  ok(!actionsBar().classList.contains("hidden"), "prérequis : barre d'actions visible");
  surfaceEl().dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle();
  eq(state.selectionIds.length, 0, "[point 2] clic dans le vide → sélection VIDÉE");
  ok(actionsBar().classList.contains("hidden"), "[point 2] clic dans le vide → barre d'actions MASQUÉE");
  eq(window.document.getElementById("gallery-selected").textContent, "", "[point 2] libellé de sélection vidé");
}
// Contrôles négatifs : cellule et action rapide NE SONT PAS « le vide ».
{
  click(cellEl(0));
  await settle();
  eq(state.selectionIds.length, 1, "[négatif] clic sur une cellule → sélection (pas un clic dans le vide)");
  click(cellEl(1));
  await settle();
  eq(state.selectionIds.length, 1, "clic cellule → remplace la sélection (comportement conservé)");
  const idsBefore = state.selectionIds.slice().map(String).sort().join(",");
  state.lastDownload = null;
  cellEl(0).querySelector('[data-holaf-action="download"]').dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle();
  eq(state.lastDownload, AppGallery.downloadUrl(state.collection.at(0)), "clic action rapide → téléchargement déclenché");
  eq(state.selectionIds.slice().map(String).sort().join(","), idsBefore, "[négatif] clic action rapide → sélection conservée");
}
// Non-régressions : ctrl = toggle, shift = plage.
{
  AppGallery.clearSelection();
  await settle();
  click(cellEl(0));
  await settle();
  click(cellEl(1), { ctrl: true });
  await settle();
  eq(state.selectionIds.length, 2, "Ctrl-clic AJOUTE sans vider (non-régression)");
  click(cellEl(1), { ctrl: true });
  await settle();
  eq(state.selectionIds.length, 1, "Ctrl-clic sur un sélectionné RETIRE (toggle)");
  click(cellEl(0));
  await settle();
  click(cellEl(2), { shift: true });
  await settle();
  ok(state.selectionIds.length === 3, "Shift-clic → PLAGE de 3 items (non-régression)");
  eq(state.selectionIds.slice().map(String).sort().join(","),
     [idAt(0), idAt(1), idAt(2)].map(String).sort().join(","), "plage = items 0→2");
}
// (3) CASE À COCHER : toggle unitaire sans vider les autres.
{
  AppGallery.clearSelection();
  await settle();
  const c0 = cellEl(0).querySelector(".gallery-cell-check");
  ok(!!c0, "[point 3] case à cocher présente dans la cellule (haut gauche)");
  eq(c0 && c0.type, "checkbox", "[point 3] case = <input type=checkbox>");
  eq(c0 && c0.checked, false, "[point 3] case VIDE quand non sélectionné");
  c0.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle();
  eq(state.selectionIds.length, 1, "[point 3] clic case → image AJOUTÉE à la sélection");
  eq(c0.checked, true, "[point 3] case PLEINE (cochée) quand sélectionnée");
  const c1 = cellEl(1).querySelector(".gallery-cell-check");
  c1.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle();
  eq(state.selectionIds.length, 2, "[point 3] case 2 → 2 sélectionnés SANS vider le 1er (toggle unitaire)");
  eq(c0.checked, true, "[point 3] la 1re case reste cochée");
  eq(c1.checked, true, "[point 3] la 2e case est cochée");
  c1.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle();
  eq(state.selectionIds.length, 1, "[point 3] 2e clic sur la case → image RETIRÉE");
  eq(c1.checked, false, "[point 3] case redevenue vide");
  eq(c0.checked, true, "[point 3] les autres sélections sont intactes");
  ok(!actionsBar().classList.contains("hidden"), "barre d'actions toujours visible (1 restant)");
}
// Contrôle négatif : double-clic sur la CASE → aucune visionneuse, aucune action.
{
  const c0 = cellEl(0).querySelector(".gallery-cell-check");
  state.lastDownload = null;
  dbl(c0);
  await settle(8);
  ok(!state.lightbox.isOpen(), "[négatif] double-clic sur la case → visionneuse NON ouverte");
  eq(state.lastDownload, null, "[négatif] clic case → aucune action rapide déclenchée");
}
// Non-régression : double-clic sur une CELLULE → visionneuse.
{
  const target = state.collection.at(0);
  dbl(cellEl(0));
  await settle(15);
  ok(state.lightbox.isOpen(), "double-clic cellule → visionneuse ouverte (non-régression)");
  eq(state.lightboxItem && state.lightboxItem.id, target.id, "visionneuse sur l'item double-cliqué");
  state.lightbox.close();
  await settle(2);
}
// Contrôle négatif : clic dans le vide SANS sélection → aucun état corrompu.
{
  AppGallery.clearSelection();
  await settle();
  surfaceEl().dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle();
  eq(state.selectionIds.length, 0, "[négatif] clic dans le vide sans sélection → rien à faire");
  ok(actionsBar().classList.contains("hidden"), "barre toujours masquée");
}
// Non-régressions CLAVIER : la case focalisée ne bloque pas les flèches, et
// « Espace » n'est pas doublé (activation native unique du navigateur).
{
  AppGallery.clearSelection();
  await settle();
  const cb0 = cellEl(0).querySelector(".gallery-cell-check");
  click(cellEl(0)); // activeIndex = 0, sélection = item 0
  await settle();
  const anchorBefore = state.grid.selection.anchor();
  cb0.focus();
  keyDown(cb0, "ArrowRight");
  await settle();
  eq(state.grid.selection.anchor(), anchorBefore + 1, "navigation clavier (flèches) active même case focalisée");
  AppGallery.clearSelection();
  await settle();
  const selBefore = state.selectionIds.length;
  keyDown(cb0, " ");
  await settle();
  eq(state.selectionIds.length, selBefore, "[négatif] Espace n'est PAS traité deux fois (hôte + natif)");
  cb0.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle();
  eq(state.selectionIds.length, selBefore + 1, "un SEUL toggle pour Espace (via l'activation native)");
  AppGallery.clearSelection();
  await settle();
  cb0.blur();
}

/* ═══ 5. Vue Corbeille (status=trashed) ═══════════════════════════════════ */
console.log("5. Vue Corbeille");
AppGallery.setView("trash");
await settle();
eq(state.view, "trash", "state.view = trash");
ok(lastListCall().url.indexOf("status=trashed") !== -1, "vue Corbeille → status=trashed");
eq(state.collection.total, TRASH_TOTAL, "total corbeille = items corbeillés");
eq(window.document.getElementById("gallery-view-trash").getAttribute("aria-selected"), "true", "onglet Corbeille actif");
{
  let allTrashed = true;
  state.collection.forEachLoaded((x) => { if (!(x.status === "trashed" || x.trashed)) allTrashed = false; });
  ok(allTrashed, "tous les items rendus sont corbeillés");
}
{
  const cell = cellEl(0);
  ok(cell.classList.contains("gallery-cell--trashed"), "cellule distinguée (opacité) en corbeille");
  ok(!cell.querySelector(".gallery-cell-trash").classList.contains("is-hidden"), "marqueur 🗑 visible");
  ok(cell.querySelector('[data-holaf-action="restore"]') && !cell.querySelector('[data-holaf-action="restore"]').classList.contains("is-hidden"), "action Restaurer visible sur la cellule");
  ok(cell.querySelector('[data-holaf-action="purge"]') && !cell.querySelector('[data-holaf-action="purge"]').classList.contains("is-hidden"), "action Purger visible sur la cellule");
  ok(cell.querySelector('[data-holaf-action="delete"]').classList.contains("is-hidden"), "action Supprimer masquée en corbeille");
}
AppGallery.setView("normal");
await settle();
ok(lastListCall().url.indexOf("status=") === -1, "[négatif] retour vue Médias → plus de status=");

/* ═══ 6. Suppression optimiste + ROLLBACK ═════════════════════════════════ */
console.log("6. Suppression optimiste & rollback");
{
  const item = state.collection.at(0);
  const totalBefore = state.collection.total;
  const listBefore = listCalls().length;
  const p = AppGallery.deleteItem(item);
  // Retrait OPTIMISTE immédiat (avant la réponse).
  eq(state.collection.total, totalBefore - 1, "retrait optimiste immédiat (total -1)");
  ok(!collectionHasId(item.id), "item retiré de la collection avant la réponse");
  const res = await p;
  await settle();
  eq(res, true, "appel DELETE réussi");
  eq(state.collection.total, totalBefore - 1, "après succès : toujours retiré");
  eq(listCalls().length, listBefore, "[négatif] AUCUN reload (rollback) déclenché si l'API réussit");
  ok(state.lastRequest.method === "DELETE" && state.lastRequest.url.endsWith("/media/" + item.id), "DELETE /api/media/<id> appelé");
}
{
  // Échec → ROLLBACK par reload complet.
  AppGallery.reload();
  await settle();
  const item = state.collection.at(0);
  const listBefore = listCalls().length;
  failNextDelete = true;
  const res = await AppGallery.deleteItem(item);
  await settle();
  eq(res, false, "appel DELETE en échec (HTTP 500)");
  ok(listCalls().length > listBefore, "ROLLBACK : reload complet déclenché sur échec");
  await settle();
  ok(collectionHasId(item.id), "item présent de nouveau dans la collection après rollback");
  ok(!!window.document.querySelector(".holaf-toast--error"), "toast d'erreur affiché après échec");
}

/* ═══ 7. Restauration unitaire ════════════════════════════════════════════ */
console.log("7. Restauration unitaire");
AppGallery.setView("trash");
await settle();
{
  const item = state.collection.at(0);
  const p = AppGallery.restoreItem(item);
  eq(state.lastRequest.method, "POST", "restauration = POST");
  ok(state.lastRequest.url.endsWith("/media/" + item.id + "/restore"), "POST /api/media/<id>/restore");
  const res = await p;
  await settle();
  eq(res, true, "restauration réussie");
  ok(!collectionHasId(item.id), "item retiré de la vue Corbeille après restauration");
}

/* ═══ 8. Purge unitaire AVEC confirmation ═════════════════════════════════ */
console.log("8. Purge unitaire (confirmation)");
{
  const item = state.collection.at(0);
  const before = calls.length;
  window.confirm = () => false; // refus
  const r1 = await AppGallery.purgeItem(item);
  eq(r1, false, "purge annulée si confirmation refusée");
  eq(calls.length, before, "[négatif] AUCUN appel réseau si l'utilisateur refuse");
  window.confirm = () => true; // acceptation
  const p2 = AppGallery.purgeItem(item);
  eq(state.lastRequest.method, "DELETE", "purge = DELETE");
  ok(state.lastRequest.url.endsWith("/media/" + item.id + "/purge"), "DELETE /api/media/<id>/purge");
  const r2 = await p2;
  await settle();
  eq(r2, true, "purge confirmée et exécutée");
  ok(!items.some((x) => x.id === item.id), "item supprimé définitivement côté serveur simulé");
}

/* ═══ 9. Actions groupées (payload { ids } + récap skipped) ═══════════════ */
console.log("9. Actions groupées");
AppGallery.setView("normal");
await settle();
{
  state.grid.selection.set([1, SKIP_ID]);
  await settle();
  eq(state.selectionIds.length, 2, "2 items sélectionnés");
  window.confirm = () => true;
  const p = AppGallery.bulkDelete();
  eq(state.lastRequest.method, "POST", "suppression groupée = POST");
  ok(state.lastRequest.url.endsWith("/media/delete"), "POST /api/media/delete");
  eq(JSON.stringify(state.lastRequest.body), JSON.stringify({ ids: [1, SKIP_ID] }), "payload groupé = { ids: [...] }");
  const res = await p;
  await settle();
  eq(res, true, "suppression groupée réussie");
  ok(!collectionHasId(1), "id traité retiré de la vue");
  ok(collectionHasId(SKIP_ID), "id non traité (« skipped ») laissé en place");
  const warn = window.document.querySelector(".holaf-toast--warning");
  ok(!!warn, "toast WARNING quand des ids sont ignorés");
  ok(warn && warn.textContent.indexOf("ignor") !== -1, "toast signale les ignorés");
}
{
  // Suppression groupée annulée : aucun appel.
  state.grid.selection.set([5, 6]);
  await settle();
  const before = calls.length;
  window.confirm = () => false;
  const r = await AppGallery.bulkDelete();
  eq(r, false, "suppression groupée annulée si refus");
  eq(calls.length, before, "[négatif] pas d'appel si confirmation refusée");
}
{
  // Restaurer groupé (vue corbeille)
  AppGallery.setView("trash");
  await settle();
  state.grid.selection.set([2, 4]);
  await settle();
  const p = AppGallery.bulkRestore();
  eq(state.lastRequest.method, "POST", "restauration groupée = POST");
  ok(state.lastRequest.url.endsWith("/media/restore"), "POST /api/media/restore");
  eq(JSON.stringify(state.lastRequest.body), JSON.stringify({ ids: [2, 4] }), "payload restauration = { ids }");
  const res = await p;
  await settle();
  eq(res, true, "restauration groupée réussie");
}
{
  // Purger groupé (vue corbeille)
  AppGallery.setView("trash");
  await settle();
  state.grid.selection.set([SKIP_ID]);
  await settle();
  window.confirm = () => true;
  const p = AppGallery.bulkPurge();
  eq(state.lastRequest.method, "POST", "purge groupée = POST");
  ok(state.lastRequest.url.endsWith("/media/purge"), "POST /api/media/purge");
  eq(JSON.stringify(state.lastRequest.body), JSON.stringify({ ids: [SKIP_ID] }), "payload purge = { ids }");
  const res = await p;
  await settle();
  eq(res, true, "purge groupée réussie");
}

/* ═══ 10. Boutons désactivés pendant un appel réseau ══════════════════════ */
console.log("10. Verrou (busy) pendant l'appel");
AppGallery.setView("normal");
await settle();
{
  state.grid.selection.set([7, 8]);
  await settle();
  window.confirm = () => true;
  const d = defer();
  deferred = d;
  const p = AppGallery.bulkDelete();
  eq(state.busy, true, "state.busy = true pendant l'appel");
  ok(window.document.getElementById("gallery-action-download").disabled === true, "boutons désactivés pendant l'appel");
  ok(window.document.getElementById("gallery-action-delete").disabled === true, "bouton Supprimer désactivé");
  d.resolve(makeRes(200, { trashed: 2, skipped: [] }));
  await p;
  await settle();
  eq(state.busy, false, "state.busy relâché après l'appel");
  ok(window.document.getElementById("gallery-action-download").disabled === false, "boutons réactivés après l'appel");
}

/* ═══ 11. Téléchargement (URL) ════════════════════════════════════════════ */
console.log("11. Téléchargement de l'original");
{
  const url = AppGallery.downloadItem({ id: 10, filename: "a.png" });
  eq(url, "/api/media/10/download", "URL de téléchargement unitaire");
  eq(state.lastDownload, "/api/media/10/download", "state.lastDownload mémorisé");
}
{
  const urls = AppGallery.downloadItems([{ id: 10, filename: "a.png" }, { id: 11, filename: "b.png" }]);
  eq(urls.join(","), "/api/media/10/download,/api/media/11/download", "séquence d'URL groupée");
  eq(state.lastDownloadBatch.join(","), "/api/media/10/download,/api/media/11/download", "state.lastDownloadBatch mémorisé");
  eq(state.lastDownload, "/api/media/10/download", "1er téléchargement déclenché immédiatement");
  await sleep(450);
  await settle();
  eq(state.lastDownload, "/api/media/11/download", "2e téléchargement déclenché en séquence (400 ms)");
}
{
  // Contrôle négatif : téléchargement d'une sélection vide = aucune URL.
  state.grid.selection.clear();
  await settle();
  eq(AppGallery.bulkDownload().length, 0, "[négatif] sélection vide → aucun téléchargement");
}
{
  // Depuis la barre d'actions avec une sélection.
  state.grid.selection.set([9, 10]);
  await settle();
  const urls = AppGallery.bulkDownload();
  eq(urls.length, 2, "téléchargement groupé depuis la sélection");
  ok(urls.indexOf("/api/media/9/download") !== -1 && urls.indexOf("/api/media/10/download") !== -1,
     "URLs des 2 médias sélectionnés présentes");
}

/* ═══ 12. Téléchargement depuis la lightbox (sans modifier la brique) ══════ */
console.log("12. Bouton ⤓ dans la lightbox");
AppGallery.setView("normal");
await settle();
{
  const item = state.collection.at(1);
  await state.lightbox.openZoom(item);
  await settle(12);
  const btn = window.document.querySelector(".gallery-lightbox-download");
  ok(!!btn, "bouton ⤓ injecté dans l'overlay de la lightbox");
  ok(!!btn && btn.classList.contains("holaf-lightbox-nav"), "réutilise la classe de nav de la brique (pas de modif)");
  state.lastDownload = null;
  btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  eq(state.lastDownload, AppGallery.downloadUrl(item), "le bouton télécharge l'item courant");
  state.lightbox.close();
  await settle(4);
}

/* ═══ 12bis. Touche Suppr en visionneuse (corbeille + navigation) ═════════ */
console.log("12bis. Touche Suppr dans la visionneuse");
items = [];
for (let i = 1; i <= 24; i++) items.push(makeItem(i));
AppGallery.setView("normal");
await settle();
await AppGallery.reload();
await settle();
const pressDelete = async () => { keyDown(window.document.body, "Delete"); await settle(20); };

// Item du MILIEU → suppression + passage AUTOMATIQUE au suivant.
{
  const idx = 1;
  const cur = state.collection.at(idx);
  const nextItem = state.collection.at(idx + 1);
  state.lightboxIndex = -1;
  await state.lightbox.openZoom(cur);
  await settle(12);
  ok(state.lightbox.isOpen(), "prérequis : visionneuse ouverte");
  eq(state.lightboxItem && state.lightboxItem.id, cur.id, "prérequis : item courant mémorisé (open)");
  const callsBefore = calls.length;
  await pressDelete();
  ok(calls.length > callsBefore, "[point 4] Suppr → appel réseau");
  eq(state.lastRequest.method, "DELETE", "[point 4] Suppr → méthode DELETE (suppression douce)");
  ok(state.lastRequest.url.endsWith("/media/" + cur.id), "[point 4] DELETE /api/media/<id> de l'ITEM AFFICHÉ (pas la sélection)");
  ok(!collectionHasId(cur.id), "[point 4] item supprimé retiré de la grille (retrait local)");
  ok(state.lightbox.isOpen(), "visionneuse toujours ouverte (il reste des médias)");
  eq(state.lightboxItem && state.lightboxItem.id, nextItem.id, "[point 4] passage AUTOMATIQUE à l'image suivante");
  eq(state.lightboxIndex, idx, "index positionné sur la suivante (elle a glissé à cet index)");
  state.lightbox.close();
  await settle(2);
}
// DERNIÈRE image → suppression + retour à la PRÉCÉDENTE (choix documenté).
{
  await AppGallery.reload();
  await settle();
  const total = state.collection.total;
  const lastItem = state.collection.at(total - 1);
  const prevItem = state.collection.at(total - 2);
  state.lightboxIndex = -1;
  await state.lightbox.openZoom(lastItem);
  await settle(12);
  await pressDelete();
  eq(state.collection.total, total - 1, "[point 4] 1 média retiré");
  ok(state.lightbox.isOpen(), "[choix] dernière image supprimée → visionneuse TOUJOURS ouverte");
  eq(state.lightboxItem && state.lightboxItem.id, prevItem.id, "[choix] dernière image → retour à la PRÉCÉDENTE");
  eq(state.lightboxIndex, total - 2, "index reculé d'un cran");
  state.lightbox.close();
  await settle(2);
}
// SEUL média → suppression puis FERMETURE propre de la visionneuse.
{
  items = [makeItem(1)];
  await AppGallery.reload();
  await settle(12);
  eq(state.collection.total, 1, "prérequis : 1 seul média");
  state.lightboxIndex = -1;
  await state.lightbox.openZoom(state.collection.at(0));
  await settle(12);
  await pressDelete();
  eq(state.collection.total, 0, "[choix] seul média supprimé → total 0");
  eq(state.lightbox.isOpen(), false, "[choix] plus aucun média → visionneuse FERMÉE proprement");
  eq(state.lightboxIndex, -1, "index visionneuse réinitialisé");
}
// ÉCHEC du DELETE → toast d'erreur, AUCUNE navigation, rollback.
{
  items = [];
  for (let i = 1; i <= 6; i++) items.push(makeItem(i));
  await AppGallery.reload();
  await settle(12);
  const cur = state.collection.at(0);
  state.lightboxIndex = -1;
  await state.lightbox.openZoom(cur);
  await settle(12);
  failNextDelete = true;
  const errBefore = window.document.querySelectorAll(".holaf-toast--error").length;
  await pressDelete();
  await settle(12);
  ok(state.lightbox.isOpen(), "[point 4] échec : visionneuse toujours ouverte");
  eq(state.lightboxItem && state.lightboxItem.id, cur.id, "[point 4] échec → AUCUNE navigation (toujours le même item)");
  ok(collectionHasId(cur.id), "échec : ROLLBACK → item toujours dans la grille");
  ok(window.document.querySelectorAll(".holaf-toast--error").length > errBefore, "échec : toast d'erreur affiché");
  state.lightbox.close();
  await settle(2);
}
// Contrôles négatifs de la touche Suppr.
{
  await AppGallery.reload();
  await settle();
  // (a) Visionneuse FERMÉE → aucun appel.
  const before = calls.length;
  keyDown(window.document.body, "Delete");
  await settle(10);
  eq(calls.length, before, "[négatif] Suppr hors visionneuse → AUCUN appel réseau");
  // (b) Focus dans un CHAMP DE SAISIE → ignoré.
  state.lightboxIndex = -1;
  await state.lightbox.openZoom(state.collection.at(0));
  await settle(12);
  const before2 = calls.length;
  keyDown(window.document.getElementById("gallery-search"), "Delete");
  await settle(10);
  eq(calls.length, before2, "[négatif] Suppr depuis un champ de saisie → ignoré");
  // (c) Touche avec MODIFICATEUR → non interceptée (raccourci navigateur).
  const before3 = calls.length;
  keyDown(window.document.body, "Delete", { ctrl: true });
  await settle(10);
  eq(calls.length, before3, "[négatif] Ctrl+Suppr → non intercepté");
  // (d) AUTO-RÉPÉTITION → aucune suppression en chaîne.
  const before4 = calls.length;
  keyDown(window.document.body, "Delete", { repeat: true });
  await settle(10);
  eq(calls.length, before4, "[négatif] touche maintenue (repeat) → aucune suppression");
  state.lightbox.close();
  await settle(2);
}

/* ═══ 12ter. FAVORIS : étoile, groupé, filtre, rollback ══════════════════ */
console.log("12ter. Favoris (étoile + groupé + filtre + rollback)");
AppGallery.setView("normal");
AppGallery.resetFilters();
await settle();
await AppGallery.reload();
await settle();

// (a) Construction PURE de l'URL + contrôle négatif (filtre absent).
eq(AppGallery.mediaUrl(1, 60, { favorite: "1" }, "created_at_desc"),
   "/api/media?page=1&limit=60&sort=created_at_desc&favorite=1", "URL favorite=1");
ok(AppGallery.mediaUrl(1, 60, {}, "created_at_desc").indexOf("favorite=") === -1,
   "[négatif] aucun favorite= sans filtre");
ok(AppGallery.mediaUrl(1, 60, { favorite: "0" }, "created_at_desc").indexOf("favorite=0") !== -1,
   "URL favorite=0");

// (b) ÉTOILE de cellule : présente, bascule SANS toucher à la sélection ni
// ouvrir la visionneuse (bouton `data-holaf-action`).
let favId, favCell;
{
  favCell = cellEl(0);
  favId = state.collection.at(0).id;
  eq(state.collection.at(0).favorite, false, "prérequis : item 0 non favori");
  const star = favCell.querySelector(".gallery-cell-fav");
  ok(!!star, "étoile présente dans la cellule");
  eq(star.getAttribute("data-holaf-action"), "favorite", "étoile = action hôte data-holaf-action");
  eq(star.textContent, "☆", "étoile vide quand non favori");
  const selBefore = state.selectionIds.length;
  const listBefore = listCalls().length;
  state.lastDownload = null;
  star.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  // Optimiste : drapeau posé avant la réponse.
  eq(state.collection.at(0).favorite, true, "optimiste : favori posé immédiatement");
  eq(state.lastRequest.method, "POST", "favorite = POST");
  ok(state.lastRequest.url.endsWith("/media/" + favId + "/favorite"), "POST /api/media/<id>/favorite");
  eq(JSON.stringify(state.lastRequest.body), JSON.stringify({ favorite: true }), "payload unitaire = { favorite: true }");
  await settle();
  eq(state.selectionIds.length, selBefore, "[négatif] clic étoile → sélection INCHANGÉE");
  ok(!state.lightbox.isOpen(), "[négatif] clic étoile → visionneuse NON ouverte");
  eq(state.lastDownload, null, "[négatif] clic étoile → aucune action rapide déclenchée");
  eq(listCalls().length, listBefore, "[négatif] succès → AUCUN reload");
  const starAfter = cellEl(0).querySelector(".gallery-cell-fav");
  eq(starAfter.textContent, "★", "étoile pleine après marquage");
  ok(starAfter.classList.contains("is-fav"), "classe is-fav posée");
  ok(cellEl(0).classList.contains("gallery-cell--favorite"), "cellule marquée favorite (état visuel)");
}
// Bascule inverse (démarquage).
{
  cellEl(0).querySelector(".gallery-cell-fav").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await settle();
  eq(state.collection.at(0).favorite, false, "2e clic → retiré des favoris");
  eq(cellEl(0).querySelector(".gallery-cell-fav").textContent, "☆", "étoile redevenue vide");
}

// (c) Action GROUPÉE : payload { ids, favorite } + bascule marquer/démarquer.
{
  state.grid.selection.set([1, 2]);
  await settle();
  const p = AppGallery.bulkFavorite();
  eq(state.lastRequest.method, "POST", "favori groupé = POST");
  ok(state.lastRequest.url.endsWith("/media/favorite"), "POST /api/media/favorite");
  eq(JSON.stringify(state.lastRequest.body), JSON.stringify({ ids: [1, 2], favorite: true }),
     "payload groupé = { ids, favorite: true } (aucun favori dans la sélection)");
  const r = await p;
  await settle();
  eq(r, true, "favori groupé réussi");
  ok(items.find((x) => x.id === 1).favorite && items.find((x) => x.id === 2).favorite, "items marqués favoris");
}
{
  // Re-clic : TOUS déjà favoris → on DÉMARQUE.
  state.grid.selection.set([1, 2]);
  await settle();
  const p = AppGallery.bulkFavorite();
  eq(JSON.stringify(state.lastRequest.body), JSON.stringify({ ids: [1, 2], favorite: false }),
     "re-clic → favorite:false (démarquer)");
  await p;
  await settle();
}
{
  // Récap avec id ignoré → toast warning.
  await AppGallery.reload();
  await settle();
  state.grid.selection.set([SKIP_ID, 5]);
  await settle();
  const beforeWarn = window.document.querySelectorAll(".holaf-toast--warning").length;
  const p = AppGallery.bulkFavorite();
  await p;
  await settle();
  ok(window.document.querySelectorAll(".holaf-toast--warning").length > beforeWarn,
     "toast warning quand un id est ignoré (skipped)");
  eq(items.find((x) => x.id === 5).favorite, true, "id traité marqué favori");
}
{
  // Contrôle négatif : sélection vide → aucun appel.
  AppGallery.clearSelection();
  await settle();
  const before = calls.length;
  const r = await AppGallery.bulkFavorite();
  eq(r, false, "[négatif] sélection vide → false");
  eq(calls.length, before, "[négatif] sélection vide → AUCUN appel réseau");
}

// (d) FILTRE « Favoris » (bascule toolbar).
{
  AppGallery.resetFilters();
  await settle();
  await AppGallery.reload();
  await settle();
  items = [];
  for (let i = 1; i <= 8; i++) items.push(makeItem(i)); // favoris = id 4, 8
  await AppGallery.reload();
  await settle();
  const favBtn = window.document.getElementById("gallery-filter-favorite");
  ok(!!favBtn, "bouton filtre « Favoris » présent");
  eq(favBtn.getAttribute("aria-pressed"), "false", "bouton filtre inactif par défaut");
  AppGallery.toggleFavoriteFilter();
  await settle();
  ok(lastListCall().url.indexOf("favorite=1") !== -1, "filtre Favoris → favorite=1 dans la requête");
  ok(favBtn.classList.contains("is-active"), "bouton filtre marqué actif");
  eq(favBtn.getAttribute("aria-pressed"), "true", "aria-pressed = true");
  eq(state.filters.favorite, "1", "state.filters.favorite = 1");
  eq(state.collection.total, items.filter((i) => i.favorite && i.status !== "trashed").length,
     "total = nombre de favoris vivants");
  {
    let onlyFav = true;
    state.collection.forEachLoaded((x) => { if (!x.favorite) onlyFav = false; });
    ok(onlyFav, "seuls les favoris sont rendus");
  }
  // Non-régression : un favori n'est PAS favori dans la vue d'un autre filtre.
  ok(lastListCall().url.indexOf("kind=") === -1, "le filtre Favoris n'ajoute aucun kind=" );
  AppGallery.toggleFavoriteFilter();
  await settle();
  ok(lastListCall().url.indexOf("favorite=") === -1, "[négatif] re-toggle → plus de favorite=");
  eq(favBtn.getAttribute("aria-pressed"), "false", "bouton filtre redevenu inactif");
  // Réinitialiser efface aussi le filtre favori.
  AppGallery.toggleFavoriteFilter();
  await settle();
  AppGallery.resetFilters();
  await settle();
  eq(state.favoriteOnly, false, "reset → filtre favori désactivé");
  ok(lastListCall().url.indexOf("favorite=") === -1, "reset → aucun favorite= dans la requête");
}

// (e) ROLLBACK sur échec de l'appel unitaire.
{
  AppGallery.resetFilters();
  await settle();
  await AppGallery.reload();
  await settle();
  const item = state.collection.at(0);
  const wasFav = !!item.favorite;
  failNextFavorite = true;
  const beforeErr = window.document.querySelectorAll(".holaf-toast--error").length;
  const r = await AppGallery.toggleFavorite(item);
  eq(r, false, "appel favori en échec (HTTP 500)");
  await settle();
  eq(state.collection.at(0).favorite, wasFav, "ROLLBACK : drapeau restauré à sa valeur d'origine");
  ok(window.document.querySelectorAll(".holaf-toast--error").length > beforeErr, "toast d'erreur affiché");
}

/* ═══ 13. Toast brique + preuve de non-modification ══════════════════════ */
console.log("13. Toast & briques non modifiées");
ok(!!window.HolafToast && typeof window.HolafToast.show === "function", "window.HolafToast disponible");
ok(!!window.document.querySelector(".holaf-toast"), "des toasts ont été rendus par la brique");
{
  const LIB = "/projects/holaf-lib/js/";
  const pairs = [
    ["holaf-collection.js", "holaf-collection.js"],
    ["holaf-thumbcache.js", "holaf-thumbcache.js"],
    ["holaf-virtual-grid.js", "holaf-virtual-grid.js"],
    ["holaf-viewport.js", "holaf-viewport.js"],
    ["holaf-lightbox.js", "holaf-lightbox.js"],
    ["holaf-infopane.js", "holaf-infopane.js"],
    ["holaf-toast.js", "holaf-toast.js"],
    ["holaf-modal.js", "holaf-modal.js"],
  ];
  if (existsSync(LIB)) {
    let identical = 0;
    for (const [local, lib] of pairs) {
      const a = readFileSync(resolve(HERE, "..", "vendor", "holaf", local), "utf8");
      const b = readFileSync(LIB + lib, "utf8");
      if (a === b) identical++;
    }
    eq(identical, pairs.length, "les " + pairs.length + " briques vendor sont byte-identiques à holaf-lib");
  } else {
    console.log("  (holaf-lib introuvable → preuve d'identité ignorée)");
  }
}

console.log("\n✅ test_gallery_manage : " + n + " assertions PASSENT");
