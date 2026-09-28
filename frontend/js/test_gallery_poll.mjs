// Test headless (jsdom) — Onglet « Galerie » : RAFRAÎCHISSEMENT AUTOMATIQUE.
// Usage : node frontend/js/test_gallery_poll.mjs   (via ./js/run_js_tests.sh)
//
// Suite frère de test_gallery.mjs (consultation), test_gallery_manage.mjs
// (gestion), test_gallery_preload.mjs (pré-charge) et test_gallery_tags.mjs.
// Elle prouve le correctif du bug « la galerie ne se met pas à jour toute
// seule » SANS modifier aucune brique holaf :
//   1. cycle de vie : le poll DÉMARRE à l'ouverture de l'onglet (timer +
//      listener de visibilité) et S'ARRÊTE à la fermeture ;
//   2. détection d'une nouvelle image SANS filtre → insertion incrémentale
//      (chemin delta) et non un reload complet ;
//   3. détection AVEC filtre (type / dossiers) : un média hors filtre ne
//      déclenche rien (les filtres de la vue courante sont respectés) ;
//   4. comportement NON PERTURBANT : sélection conservée, scroll compensé
//      (jamais remonté), visionneuse ouverte NON fermée et réalignée ;
//   5. reprise après erreur réseau : le poll ne meurt pas ;
//   6. retour d'onglet (visibilitychange) : masqué = rien, visible = refresh ;
//   7. dossiers : la modale recharge la liste à l'ouverture ET se met à jour
//      si un nouveau dossier apparaît pendant qu'elle est ouverte (comptes à
//      jour), sans perdre les cases cochées ;
//   8. contrôles négatifs : poll non démarré → aucun rafraîchissement ;
//      le delta préserve l'identité des objets chargés (un reload complet la
//      perdrait) et n'émet PAS de requête de page complète.
import assert from "node:assert";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import { loadJsdomOrSkip } from "./test_helpers/jsdom_loader.mjs";

const JSDOM = await loadJsdomOrSkip("test_gallery_poll");

/* ── DOM de l'onglet Galerie (toolbar complète + modale dossiers) ────────── */
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
      <div id="gallery-side">
        <aside id="gallery-info"></aside>
        <div id="gallery-actions" class="hidden"><span id="gallery-actions-label"></span></div>
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

/* ── Géométrie de la grille ──────────────────────────────────────────────── */
const gridEl = window.document.getElementById("gallery-grid");
let cw = 600, ch = 400, st = 0;
Object.defineProperty(gridEl, "clientWidth", { configurable: true, get: () => cw });
Object.defineProperty(gridEl, "clientHeight", { configurable: true, get: () => ch });
Object.defineProperty(gridEl, "scrollTop", { configurable: true, get: () => st, set: (v) => { st = Math.max(0, v); } });
gridEl.getBoundingClientRect = () => ({ left: 0, top: 0, right: cw, bottom: ch, width: cw, height: ch, x: 0, y: 0, toJSON() {} });

/* ── Jeu de données + fetch simulé ───────────────────────────────────────── */
function ts(id) { return new Date(Date.UTC(2025, 0, 1) + id * 1000).toISOString(); }
function makeItem(id, kind, subfolder) {
  kind = kind || "image";
  return {
    id,
    filename: "media_" + id + (kind === "audio" ? ".mp3" : kind === "video" ? ".mp4" : ".png"),
    kind,
    size: 1000 * id,
    created_at: ts(id),
    subfolder: subfolder || "shot" + (id % 3),
    status: "active", trashed: false,
    thumb_available: kind !== "audio", has_prompt: true, has_workflow: false,
    favorite: false,
  };
}
let items = [];
for (let i = 1; i <= 130; i++) items.push(makeItem(i));
const BASE_TOTAL = items.length;
let failNext = false;
const calls = []; // { method, url, body }

function makeRes(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, text: async () => JSON.stringify(body) };
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
  calls.push({ method, url: rawUrl });
  if (failNext) { failNext = false; return Promise.reject(new Error("réseau indisponible")); }

  let m = rawUrl.match(/\/api\/media\/(\d+)\/metadata/);
  if (m) {
    const it = items.find((x) => x.id === parseInt(m[1], 10)) || makeItem(parseInt(m[1], 10));
    return Promise.resolve(makeRes(200, Object.assign({}, it, { ext: "", width: 512, height: 512, ratio: 1 })));
  }
  if (rawUrl.indexOf("/api/media/folders") !== -1) {
    const counts = {};
    for (const it of items) { const sf = it.subfolder || ""; counts[sf] = (counts[sf] || 0) + 1; }
    const folders = Object.keys(counts).sort((a, b) => a.localeCompare(b)).map((sf) => ({ subfolder: sf, count: counts[sf] }));
    return Promise.resolve(makeRes(200, { folders, total: folders.reduce((a, f) => a + f.count, 0) }));
  }
  if (rawUrl.indexOf("/api/media?") !== -1) {
    const u = new URL(rawUrl, "http://localhost");
    const page = parseInt(u.searchParams.get("page"), 10) || 1;
    const limit = parseInt(u.searchParams.get("limit"), 10) || 60;
    const kind = u.searchParams.get("kind") || "";
    const subfolders = u.searchParams.getAll("subfolders");
    const sort = u.searchParams.get("sort") || "created_at_desc";
    let list = items.slice();
    if (kind) list = list.filter((i) => i.kind === kind);
    if (subfolders.length) list = list.filter((i) => subfolders.indexOf(i.subfolder || "") !== -1);
    list.sort(sortCmp(sort));
    const start = (page - 1) * limit;
    return Promise.resolve(makeRes(200, { items: list.slice(start, start + limit), total: list.length, page, limit }));
  }
  return Promise.resolve(makeRes(404, { error: "not found" }));
};

/* ── Espionnage du listener de visibilité ────────────────────────────────── */
let visAdd = 0, visRemove = 0;
const origAdd = window.document.addEventListener.bind(window.document);
const origRemove = window.document.removeEventListener.bind(window.document);
window.document.addEventListener = function (type, fn, opts) {
  if (type === "visibilitychange") visAdd++;
  return origAdd(type, fn, opts);
};
window.document.removeEventListener = function (type, fn, opts) {
  if (type === "visibilitychange") visRemove++;
  return origRemove(type, fn, opts);
};
// document.hidden pilotable (retour d'onglet).
let hiddenFlag = false;
Object.defineProperty(window.document, "hidden", { configurable: true, get: () => hiddenFlag });
const setHidden = (v) => { hiddenFlag = !!v; window.document.dispatchEvent(new window.Event("visibilitychange")); };

/* ── Chargement des briques + de l'adaptateur ────────────────────────────── */
for (const b of ["collection", "thumbcache", "virtual-grid", "viewport", "lightbox", "infopane", "toast", "modal"]) {
  await import("../vendor/holaf/holaf-" + b + ".js");
}
if (window.HolafToast && window.HolafToast.configure) {
  window.HolafToast.configure({ duration: 0, position: "bottom-right" }); // toasts persistants
}
await import("./app-gallery.js");

const AppGallery = window.AppGallery;
const state = AppGallery.state;

/* ── Helpers d'assertion ─────────────────────────────────────────────────── */
let n = 0;
function ok(cond, msg) { if (!cond) throw new Error("ASSERT FAIL: " + msg); n++; }
function eq(a, b, msg) { ok(a === b, msg + " (got " + JSON.stringify(a) + ", want " + JSON.stringify(b) + ")"); }
const settle = async (times = 20) => { for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0)); };
const listCalls = () => calls.filter((c) => c.method === "GET" && c.url.indexOf("/api/media?") !== -1);
const lastListCall = () => listCalls()[listCalls().length - 1];
const setSelect = (id, v) => { const el = window.document.getElementById(id); el.value = v; el.dispatchEvent(new window.Event("change", { bubbles: true })); };
const dbl = (el) => el.dispatchEvent(new window.MouseEvent("dblclick", { bubbles: true }));
const cellEl = (i) => window.document.querySelector('.holaf-grid-surface [data-holaf-index="' + i + '"]');
const foldersRoot = () => window.document.getElementById("gallery-folders-modal");
async function poll() { AppGallery.pollNow(); await settle(); }
/** Ajoute un média côté backend (tête de liste) et renvoie son id. */
let seq = 100000;
function addItem(id, kind, subfolder) { const it = makeItem(id, kind, subfolder); it.created_at = ts(++seq); items.unshift(it); return id; }

/* ═══ 1. Cycle de vie du poll ═════════════════════════════════════════════ */
console.log("1. Cycle de vie (démarrage / arrêt)");
ok(typeof AppGallery.pollNow === "function", "AppGallery.pollNow exposé");
ok(typeof AppGallery.pollCheck === "function", "AppGallery.pollCheck exposé");
eq(AppGallery.constants.POLL_MS, 10000, "intervalle de poll = 10 s");
eq(AppGallery.constants.POLL_LIMIT, 30, "requête de tête bornée à 30 items");

eq(state.pollTimer, null, "avant start : aucun timer");
AppGallery.start();
await settle();
ok(!!state.pollTimer, "start → timer de poll armé");

/* Espions : DISTINGUER le chemin delta (insertTop) d'un reload complet
 * (setFilters = reset + refetch). */
let insertTopCalls = 0, setFiltersCalls = 0;
{
  const origInsertTop = state.collection.insertTop.bind(state.collection);
  state.collection.insertTop = function (x) { insertTopCalls++; return origInsertTop(x); };
  const origSetFilters = state.collection.setFilters.bind(state.collection);
  state.collection.setFilters = function (f) { setFiltersCalls++; return origSetFilters(f); };
}
eq(typeof state.visibilityHandler, "function", "start → listener de visibilité branché");
eq(visAdd, 1, "un seul listener visibilitychange ajouté");
eq(state.collection.total, BASE_TOTAL, "collection chargée");
ok(state.loadedOnce, "chargement initial terminé");

// Idempotence : un 2e start REMPLACE le listener (retrait puis ajout),
// donc jamais deux listeners actifs simultanément.
AppGallery.start();
await settle();
eq(visAdd, 2, "2e start : listener rebranché");
eq(visRemove, 1, "2e start : l'ancien listener a été retiré (pas de doublon actif)");
eq(typeof state.visibilityHandler, "function", "2e start : un seul handler actif");

AppGallery.stop();
eq(state.pollTimer, null, "stop → timer annulé");
eq(state.visibilityHandler, null, "stop → listener retiré");
eq(visRemove, 2, "stop → removeEventListener appelé");
AppGallery.start();
await settle();
ok(!!state.pollTimer, "restart → timer réarmé");
eq(visAdd, 3, "restart → listener rebranché");

/* ═══ 2. Nouvelle image SANS filtre → insertion incrémentale ══════════════ */
console.log("2. Nouvelle image (sans filtre) — chemin DELTA");
{
  // Charge explicitement la page 2 AVANT tout insert pour prouver que le delta
  // RÉ-ANCRE les fenêtres (préserve les objets) au lieu de tout recharger.
  await state.collection.ensureRange(0, 119);
  await settle();
  const obj60 = state.collection.at(60);
  ok(!!obj60, "page 2 chargée (index 60 présent)");
  const beforeId60 = obj60.id;
  const before = state.collection.total;
  const newId = addItem(9001);
  const itBefore = insertTopCalls, sfBefore = setFiltersCalls;
  const callsBefore = listCalls().length;
  state.lastPollInserted = 0;
  await poll();
  eq(state.collection.total, before + 1, "total +1");
  eq(state.collection.at(0).id, newId, "nouveau média inséré EN TÊTE");
  eq(state.lastPollInserted, 1, "1 item inséré (chemin delta)");
  eq(insertTopCalls, itBefore + 1, "poll → insertTop() appelé (delta)");
  eq(setFiltersCalls, sfBefore, "[négatif] poll → AUCUN reload complet (setFilters non appelé)");
  eq(state.collection.at(61), obj60, "l'objet qui était à l'index 60 passe à 61 — MÊME référence (pas de reset)");
  eq(state.collection.at(61).id, beforeId60, "…et c'est bien le même média");
  const last = lastListCall();
  ok(last.url.indexOf("limit=" + AppGallery.constants.POLL_LIMIT) !== -1, "la tête est interrogée avec limit=" + AppGallery.constants.POLL_LIMIT + " (pas une page complète)");
  ok(last.url.indexOf("page=1") !== -1, "la tête est page=1");
  ok(last.url.indexOf("limit=60") === -1, "[négatif] aucune requête de page complète (limit=60)");
  eq(listCalls().length, callsBefore + 1, "1 seule requête de tête");
}

// Contrôle négatif : sans changement, aucune insertion.
{
  const before = state.collection.total;
  state.lastPollInserted = 0;
  await poll();
  eq(state.collection.total, before, "[négatif] aucun changement → total stable");
  eq(state.lastPollInserted, 0, "[négatif] aucune insertion");
}

// Plusieurs médias d'un coup.
{
  const before = state.collection.total;
  addItem(9002); addItem(9003); addItem(9004);
  state.lastPollInserted = 0;
  await poll();
  eq(state.collection.total, before + 3, "3 nouveaux médias intégrés");
  eq(state.lastPollInserted, 3, "lastPollInserted = 3");
}

/* ═══ 3. Filtres respectés ════════════════════════════════════════════════ */
console.log("3. Filtres respectés (type / dossier)");
{
  setSelect("gallery-filter-kind", "image");
  await settle();
  const before = state.collection.total;
  addItem(9101, "video"); // hors filtre « image »
  await poll();
  eq(state.collection.total, before, "[négatif] média hors filtre → pas d'insertion");
  addItem(9102, "image");
  await poll();
  eq(state.collection.total, before + 1, "média DANS le filtre → inséré");
  eq(state.collection.at(0).id, 9102, "inséré en tête");
  setSelect("gallery-filter-kind", "");
  await settle();
}
{
  AppGallery.applyFolders(["shot0"]);
  await settle();
  const before = state.collection.total;
  addItem(9201, "image", "shot1"); // hors dossier sélectionné
  await poll();
  eq(state.collection.total, before, "[négatif] dossier non sélectionné → pas d'insertion");
  addItem(9202, "image", "shot0");
  await poll();
  eq(state.collection.total, before + 1, "média du dossier sélectionné → inséré");
  AppGallery.applyFolders([]);
  await settle();
}

/* ═══ 4. Comportement NON perturbant ══════════════════════════════════════ */
console.log("4. Non perturbant (sélection / scroll / visionneuse)");
{
  // (a) Sélection conservée à travers une INSERTION.
  const selId = state.collection.at(1).id;
  state.grid.selection.set([selId]);
  await settle();
  eq(state.selectionIds.length, 1, "sélection active avant poll");
  addItem(9051);
  await poll();
  eq(state.selectionIds.length, 1, "poll → sélection TOUJOURS active");
  eq(state.selectionIds[0], selId, "poll → même id sélectionné");
  state.grid.selection.clear();
  await settle();
}
{
  // (b) Scroll compensé (jamais remonté), contenu stable.
  const topId = state.collection.at(0).id;
  st = 120;
  addItem(9061);
  await poll();
  ok(st > 120, "poll → scroll NON remonté (scrollTop " + st + " > 120)");
  const m = state.grid.getMetrics();
  const cols = Math.max(1, state.grid.getColumnCount());
  const rowH = (m.itemHeight || 150) + (m.gap != null ? m.gap : 12);
  eq(st, 120 + Math.ceil(state.lastPollInserted / cols) * rowH, "scroll compensé d'autant de rangées que d'items insérés");
  // Le contenu d'avant reste à sa place visuelle : l'ancien top a glissé de n.
  eq(state.collection.at(state.lastPollInserted).id, topId, "l'ancien 1er média a glissé de n (contenu préservé)");
  st = 0;
}
{
  // (c) Visionneuse ouverte : jamais fermée, index réaligné.
  st = 0;
  await settle();
  const cell0 = cellEl(0);
  ok(!!cell0, "cellule 0 rendue");
  dbl(cell0);
  await settle();
  ok(state.lightbox && state.lightbox.isOpen(), "visionneuse ouverte (double-clic)");
  eq(state.lightboxIndex, 0, "index visionneuse = 0");
  addItem(9301); addItem(9302);
  await poll();
  ok(state.lightbox.isOpen(), "poll → visionneuse TOUJOURS ouverte");
  eq(state.lightboxIndex, 2, "poll → index visionneuse réaligné (+2)");
  state.lightbox.close();
  await settle();
}

/* ═══ 5. Reprise après erreur réseau ═════════════════════════════════════ */
console.log("5. Reprise après erreur réseau");
{
  const before = state.collection.total;
  addItem(9401);
  failNext = true;
  await poll();
  eq(state.collection.total, before, "erreur réseau → aucun changement (pas de crash)");
  eq(state.pollInFlight, false, "erreur réseau → pollInFlight relâché");
  // Le poll n'est PAS mort : le tick suivant intègre le média.
  await poll();
  eq(state.collection.total, before + 1, "reprise : le média est intégré au tick suivant");
  ok(!!state.pollTimer, "le timer de poll est toujours armé après une erreur");
}

/* ═══ 6. Retour d'onglet (visibilitychange) ══════════════════════════════ */
console.log("6. Retour d'onglet");
{
  const before = state.collection.total;
  addItem(9501);
  const callsBefore = listCalls().length;
  setHidden(true);
  await settle();
  eq(listCalls().length, callsBefore, "[négatif] onglet masqué → aucune requête de poll");
  eq(state.collection.total, before, "[négatif] onglet masqué → pas de mise à jour");
  setHidden(false); // retour d'onglet → galleryPollNow()
  await settle();
  eq(state.collection.total, before + 1, "retour d'onglet → mise à jour immédiate");
}

/* ═══ 7. Dossiers & compteurs ════════════════════════════════════════════ */
console.log("7. Dossiers (modale) & compteurs");
{
  // (a) Ouverture : liste rechargée depuis /api/media/folders.
  const callsBefore = calls.filter((c) => c.url.indexOf("/api/media/folders") !== -1).length;
  AppGallery.openFoldersModal();
  await settle();
  ok(!!foldersRoot(), "modale dossiers ouverte");
  ok(calls.filter((c) => c.url.indexOf("/api/media/folders") !== -1).length > callsBefore, "GET /api/media/folders appelé à l'ouverture");
  const names0 = Array.prototype.slice.call(foldersRoot().querySelectorAll(".gallery-folder-name")).map((e) => e.textContent);
  ok(names0.indexOf("shot0") !== -1, "dossier existant listé");

  // (b) Un NOUVEAU dossier apparaît pendant que la modale est ouverte.
  addItem(9601, "image", "NOUVEAU");
  await poll();
  const names1 = Array.prototype.slice.call(foldersRoot().querySelectorAll(".gallery-folder-name")).map((e) => e.textContent);
  ok(names1.indexOf("NOUVEAU") !== -1, "nouveau dossier visible SANS fermer la modale");

  // (c) Compteur à jour pour ce dossier.
  const row = Array.prototype.slice.call(foldersRoot().querySelectorAll(".gallery-folder-item"))
    .find((r) => r.querySelector(".gallery-folder-name").textContent === "NOUVEAU");
  ok(!!row, "ligne du nouveau dossier présente");
  eq(row.querySelector(".gallery-folder-count").textContent, "(1)", "compteur du nouveau dossier = (1)");

  // (d) Les cases cochées survivent au rafraîchissement.
  const cb = foldersRoot().querySelector('.gallery-folder-check[value="shot0"]');
  cb.checked = true;
  cb.dispatchEvent(new window.Event("change", { bubbles: true }));
  addItem(9602, "image", "ENCORE");
  await poll();
  eq(foldersRoot().querySelector('.gallery-folder-check[value="shot0"]').checked, true, "case cochée conservée après rafraîchissement");
  ok(Array.prototype.slice.call(foldersRoot().querySelectorAll(".gallery-folder-name")).map((e) => e.textContent).indexOf("ENCORE") !== -1, "2e nouveau dossier visible");
  try { foldersRoot().querySelector(".holaf-modal-close") && foldersRoot().querySelector(".holaf-modal-close").click(); } catch (e) { /* ignore */ }
  if (state.foldersModal) { try { state.foldersModal.close(); } catch (e) { /* ignore */ } state.foldersModal = null; }
  await settle();
}

/* ═══ 8. Contrôles négatifs ══════════════════════════════════════════════ */
console.log("8. Contrôles négatifs");
{
  // (a) Poll NON démarré → aucun rafraîchissement.
  const before = state.collection.total;
  addItem(9701);
  const started = state.started;
  state.started = false;
  await poll();
  eq(state.collection.total, before, "[négatif] poll non démarré → aucun rafraîchissement");
  state.started = started;
  await poll();
  eq(state.collection.total, before + 1, "poll relancé → rafraîchissement de nouveau effectif");
}
{
  // (b) Chemin DELTA confirmé par espion : aucune insertion/rafraîchissement
  //     ne passe par un reload (setFilters).
  const sfBefore = setFiltersCalls;
  addItem(9801);
  await poll();
  eq(setFiltersCalls, sfBefore, "[négatif] insertion via poll → setFilters NON appelé (delta, pas de reload)");
}
{
  // (c) Suppression (total en baisse) → resynchronisation complète DIFFÉRÉE
  //     tant qu'elle perturberait (sélection active ici).
  const selId = state.collection.at(0).id;
  state.grid.selection.set([selId]);
  await settle();
  items = items.filter((x) => x.id !== selId); // disparu côté backend
  await poll();
  ok(state.pollPendingReload, "suppression → resynchronisation en attente");
  eq(state.collection.at(0).id, selId, "[négatif] sélection active → reload NON exécuté (pas de perturbation)");
  state.grid.selection.clear();
  await settle();
  st = 0;
  await poll();
  eq(state.pollPendingReload, false, "sélection levée + haut de liste → resynchronisation exécutée");
  ok(state.collection.at(0).id !== selId, "la liste ne contient plus le média supprimé");
}
{
  // (d) Tri NON descendant : un nouveau média n'est pas forcément en tête →
  //     resynchronisation complète (différée) et non insertion incrémentale.
  setSelect("gallery-filter-sort", "created_at_asc");
  await settle();
  const before = state.collection.total;
  const sfBefore = setFiltersCalls;
  addItem(9901);
  await poll();
  eq(state.collection.total, before, "[négatif] tri ascendant → pas d'insertion incrémentale immédiate");
  ok(state.pollPendingReload, "tri ascendant → resynchronisation en attente");
  await poll();
  eq(state.pollPendingReload, false, "resynchronisation rejouée au tick suivant");
  eq(state.collection.total, before + 1, "le nouveau média est bien présent après resynchronisation");
  ok(setFiltersCalls > sfBefore, "resynchronisation → reload complet (setFilters) assumé");
  setSelect("gallery-filter-sort", "created_at_desc");
  await settle();
}

/* ── Récapitulatif ──────────────────────────────────────────────────────── */
AppGallery.stop();
console.log("");
console.log("✅ test_gallery_poll : " + n + " assertions PASSENT");
process.exit(0);
