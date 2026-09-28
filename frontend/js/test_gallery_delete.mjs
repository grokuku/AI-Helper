// Test headless (jsdom) — Onglet « Galerie » : SUPPRESSION INCRÉMENTALE +
// FAVORI en PLEIN ÉCRAN.
// Usage : node frontend/js/test_gallery_delete.mjs   (via ./js/run_js_tests.sh)
//
// Suite frère de test_gallery_manage.mjs (gestion), test_gallery_poll.mjs
// (rafraîchissement auto), etc. Elle prouve, SANS modifier aucune brique holaf :
//   1. une suppression UNITAIRE ne recharge PAS la galerie : aucun vidage du
//      pool de cellules (_resetCells), aucune réassignation du src des <img>
//      des médias restants, cellules conservées (mêmes nœuds DOM), scroll et
//      sélection restante préservés ;
//   2. idem pour une suppression GROUPÉE (le backend renvoie les `skipped`,
//      seuls les ids réellement traités sont retirés localement) ;
//   3. idem pour une suppression depuis la VISIONNEUSE (touche Suppr), qui
//      reste ouverte sur l'image suivante ;
//   4. NON-RÉGRESSION : une suppression HORS-BANDE (faite ailleurs, ex.
//      ComfyUI) est toujours détectée par l'auto-refresh et reflétée (reload
//      complet différé) ;
//   5. COURSE poll/suppression : une réponse de tête PÉRIMÉE (partie avant
//      notre suppression) n'est PAS interprétée comme un changement hors-bande
//      (garde `mutationSeq`) → pas de reload complet injustifié ;
//   6. icône FAVORI ★/☆ dans la visionneuse : présente à côté de ⤓, état à
//      l'ouverture ET après navigation, bascule optimiste (bon payload,
//      cellule de grille mise à jour, visionneuse jamais fermée), rollback +
//      toast sur erreur ;
//   7. contrôles négatifs : sans le marquage des mutations locales, la réponse
//      périmée déclenche bien un reload complet (rouge) ; l'icône de
//      navigation est vérifiée par état attendu à chaque image ;
//   8. briques holaf byte-identiques à holaf-lib (aucune modification).
import assert from "node:assert";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { loadJsdomOrSkip } from "./test_helpers/jsdom_loader.mjs";

const JSDOM = await loadJsdomOrSkip("test_gallery_delete");
const HERE = dirname(fileURLToPath(import.meta.url));

/* ── DOM de l'onglet Galerie (toolbar complète) ──────────────────────────── */
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
    <select id="gallery-filter-kind"><option value="">Tous</option><option value="image">Images</option></select>
    <button type="button" id="gallery-filter-folders" aria-haspopup="dialog">Dossiers : tous</button>
    <button type="button" id="gallery-filter-favorite" aria-pressed="false">★ Favoris</button>
    <input id="gallery-search" type="search">
    <input id="gallery-filter-from" type="date">
    <input id="gallery-filter-to" type="date">
    <select id="gallery-filter-sort">
      <option value="created_at_desc">Récents</option>
      <option value="created_at_asc">Anciens</option>
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
// Pas de showConfirm global → galleryConfirm retombe sur window.confirm.

/* ── Géométrie de la grille (viewport volontairement court) ──────────────── */
const gridEl = window.document.getElementById("gallery-grid");
let cw = 600, ch = 400, st = 0;
Object.defineProperty(gridEl, "clientWidth", { configurable: true, get: () => cw });
Object.defineProperty(gridEl, "clientHeight", { configurable: true, get: () => ch });
Object.defineProperty(gridEl, "scrollTop", { configurable: true, get: () => st, set: (v) => { st = Math.max(0, v); } });
gridEl.getBoundingClientRect = () => ({ left: 0, top: 0, right: cw, bottom: ch, width: cw, height: ch, x: 0, y: 0, toJSON() {} });

/* ── Jeu de données (130 médias = 3 fenêtres de 60) + fetch simulé ───────── */
let skipIds = [];  // ids réellement ignorés par le backend simulé (choisis par test)
function ts(id) { return new Date(Date.UTC(2025, 0, 1) + id * 1000).toISOString(); }
function makeItem(id) {
  return {
    id,
    filename: "media_" + id + ".png",
    kind: "image",
    size: 1000 * id,
    created_at: ts(id),
    subfolder: "shot" + (id % 3),
    status: "active",
    trashed: false,
    trashed_at: null,
    thumb_available: true,
    has_prompt: true,
    has_workflow: false,
    favorite: (id % 4 === 0),
  };
}
let items = [];
for (let i = 1; i <= 130; i++) items.push(makeItem(i));

const calls = []; // { method, url, body }
let failNextDelete = false;
let failNextFavorite = false;
let deferredResponse = null;  // promesse pilotée pour simuler une réponse en vol

function makeRes(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, text: async () => JSON.stringify(body) };
}
function sortCmp(sort) {
  if (sort === "created_at_asc") return (a, b) => (a.created_at < b.created_at ? -1 : 1);
  return (a, b) => (a.created_at < b.created_at ? 1 : -1); // created_at_desc
}
function listPayload(u) {
  const page = parseInt(u.searchParams.get("page"), 10) || 1;
  const limit = parseInt(u.searchParams.get("limit"), 10) || 60;
  const status = u.searchParams.get("status") || "";
  const favorite = u.searchParams.get("favorite") || "";
  let list = items.slice();
  list = (status === "trashed") ? list.filter((i) => i.status === "trashed") : list.filter((i) => i.status !== "trashed");
  if (favorite === "1" || favorite === "true") list = list.filter((i) => !!i.favorite);
  list.sort(sortCmp(u.searchParams.get("sort") || "created_at_desc"));
  return { items: list.slice((page - 1) * limit, page * limit), total: list.length, page, limit };
}
function metadataFor(id) {
  const it = items.find((x) => x.id === id) || makeItem(id);
  return Object.assign({}, it, { ext: ".png", width: 512, height: 512, ratio: 1, prompt: "p", workflow: "{}" });
}

globalThis.fetch = (url, opts = {}) => {
  const method = (opts.method || "GET").toUpperCase();
  const rawUrl = String(url);
  const body = opts.body ? JSON.parse(opts.body) : null;
  calls.push({ method, url: rawUrl, body });

  let m = rawUrl.match(/\/api\/media\/(\d+)\/metadata/);
  if (m) return Promise.resolve(makeRes(200, metadataFor(parseInt(m[1], 10))));

  // Suppression groupée.
  m = rawUrl.match(/\/api\/media\/(delete)$/);
  if (m && method === "POST") {
    const ids = (body && Array.isArray(body.ids)) ? body.ids : [];
    const skipped = ids.filter((id) => skipIds.indexOf(id) !== -1);
    const done = ids.filter((id) => skipIds.indexOf(id) === -1);
    for (const id of done) {
      const it = items.find((x) => x.id === id);
      if (it) { it.status = "trashed"; it.trashed = true; }
    }
    return Promise.resolve(makeRes(200, { trashed: done.length, skipped }));
  }

  // Favori unitaire.
  m = rawUrl.match(/\/api\/media\/(\d+)\/favorite$/);
  if (m && method === "POST") {
    if (failNextFavorite) { failNextFavorite = false; return Promise.resolve(makeRes(500, { error: "boom" })); }
    const it = items.find((x) => x.id === parseInt(m[1], 10));
    if (it) it.favorite = !!(body && body.favorite);
    return Promise.resolve(makeRes(200, it ? Object.assign({}, it) : { id: parseInt(m[1], 10) }));
  }

  // Suppression unitaire.
  m = rawUrl.match(/\/api\/media\/(\d+)$/);
  if (m && method === "DELETE") {
    if (failNextDelete) { failNextDelete = false; return Promise.resolve(makeRes(500, { error: "boom" })); }
    const it = items.find((x) => x.id === parseInt(m[1], 10));
    if (it) { it.status = "trashed"; it.trashed = true; }
    return Promise.resolve(makeRes(200, { ok: true }));
  }

  // Liste paginée (+ interception d'une réponse en vol pour le test de course).
  if (rawUrl.indexOf("/api/media?") !== -1) {
    const u = new URL(rawUrl, "http://localhost");
    if (deferredResponse) {
      const d = deferredResponse;
      deferredResponse = null;
      // Le payload est figé À L'INSTANT DE LA REQUÊTE (comme le ferait le
      // serveur : la réponse décrit l'état d'avant notre suppression).
      const payload = d.snapshot ? makeRes(200, d.snapshot(u)) : makeRes(200, listPayload(u));
      return d.promise.then(() => payload);
    }
    return Promise.resolve(makeRes(200, listPayload(u)));
  }
  return Promise.resolve(makeRes(404, { error: "not found" }));
};

/* ── Chargement des briques + de l'adaptateur ────────────────────────────── */
for (const b of ["collection", "thumbcache", "virtual-grid", "viewport", "lightbox", "infopane", "toast", "modal"]) {
  await import("../vendor/holaf/holaf-" + b + ".js");
}
if (window.HolafToast && window.HolafToast.configure) {
  window.HolafToast.configure({ duration: 0, position: "bottom-right" });
}
await import("./app-gallery.js");

const AppGallery = window.AppGallery;
const state = AppGallery.state;

/* ── Compteurs / helpers ─────────────────────────────────────────────────── */
let n = 0;
function ok(cond, msg) { if (!cond) throw new Error("ASSERT FAIL: " + msg); n++; }
function eq(a, b, msg) { ok(a === b, msg + " (got " + JSON.stringify(a) + ", want " + JSON.stringify(b) + ")"); }
const settle = async (times = 12) => { for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0)); };

const listCalls = () => calls.filter((c) => c.method === "GET" && c.url.indexOf("/api/media?") !== -1);
const pageCalls = () => listCalls().filter((c) => c.url.indexOf("limit=60") !== -1);
const lastRequest = () => state.lastRequest;
const collectionHasId = (id) => {
  let found = false;
  state.collection.forEachLoaded((it) => { if (String(it.id) === String(id)) found = true; });
  return found;
};
const keyDown = (el, k) => el.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
const dbl = (el) => el.dispatchEvent(new window.MouseEvent("dblclick", { bubbles: true, cancelable: true }));
const cellFor = (id) => window.document.querySelector('.holaf-grid-surface [data-holaf-id="' + id + '"]');

/* ── Espions : rechargement complet + vidage du pool + src des vignettes ── */
let setFiltersCalls = 0;
let resetCellsCalls = 0;
// Compteur d'affectations `img.src` par élément <img> (proxy des requêtes de
// vignette : une cellule gardée ne doit JAMAIS réassigner son src).
const imgSrcCounts = new WeakMap();
{
  const proto = window.HTMLImageElement.prototype;
  const desc = Object.getOwnPropertyDescriptor(proto, "src");
  Object.defineProperty(proto, "src", {
    configurable: true,
    enumerable: desc.enumerable,
    get: desc.get,
    set(v) { imgSrcCounts.set(this, (imgSrcCounts.get(this) || 0) + 1); desc.set.call(this, v); },
  });
}
function snapCells() {
  const map = new Map();
  window.document.querySelectorAll(".holaf-grid-surface [data-holaf-id]").forEach((el) => {
    map.set(String(el.dataset.holafId), el);
  });
  return map;
}
function snapSrcs(map) {
  const out = new Map();
  map.forEach((el, id) => out.set(id, el._gImg ? (imgSrcCounts.get(el._gImg) || 0) : 0));
  return out;
}
/**
 * Vérifie que les cellules d'AVANT sont conservées (mêmes nœuds DOM) et
 * qu'aucune n'a réassigné son `img.src` (aucune vignette rechargée).
 * @returns {number} nombre de cellules conservées.
 */
function assertCellsKept(mapBefore, srcBefore, removedIds, label) {
  const mapAfter = snapCells();
  let kept = 0, recreated = 0, reloaded = 0;
  mapBefore.forEach((el, id) => {
    if (removedIds.indexOf(Number(id)) !== -1 || removedIds.indexOf(id) !== -1) return; // cellule partie : normal
    if (mapAfter.get(id) === el) kept++; else recreated++;
    if (el._gImg && (imgSrcCounts.get(el._gImg) || 0) !== srcBefore.get(id)) reloaded++;
  });
  eq(recreated, 0, "[" + label + "] AUCUNE cellule existante recréée (nœuds DOM conservés)");
  eq(reloaded, 0, "[" + label + "] AUCUNE vignette réassignée pour les médias restants");
  ok(kept >= 15, "[" + label + "] cellules conservées : " + kept);
  return { mapAfter, kept };
}

/* ═══ 0. Chargement initial ══════════════════════════════════════════════ */
console.log("0. Chargement initial (2 fenêtres)");
window.confirm = () => true;
await AppGallery.start();
await settle();
// Espions installés APRÈS la création des briques (galleryInit sur start).
{
  const col = state.collection;
  const origSetFilters = col.setFilters.bind(col);
  col.setFilters = function (f) { setFiltersCalls++; return origSetFilters(f); };
  const origReset = state.grid._resetCells.bind(state.grid);
  state.grid._resetCells = function () { resetCellsCalls++; return origReset(); };
}
eq(state.collection.total, 130, "130 médias chargés");
await state.collection.ensureRange(0, 119); // 2 fenêtres chargées (0..119)
await settle();
ok(state.collection.isWindowLoaded(0) && state.collection.isWindowLoaded(60), "fenêtres 0 et 60 chargées");
ok(snapCells().size > 0, "cellules rendues");

/* ═══ 1. Suppression UNITAIRE : incrémentale, sans rechargement ══════════ */
console.log("1. Suppression unitaire incrémentale");
{
  const col = state.collection;
  const totalBefore = col.total;
  const otherId = col.at(7).id;                 // un AUTRE média (sélection restante)
  state.grid.selection.set([otherId]);
  await settle();
  eq(state.selectionIds.length, 1, "prérequis : un autre média est sélectionné");
  st = 130;                                     // position de scroll non nulle
  const cellsBefore = snapCells();
  const srcBefore = snapSrcs(cellsBefore);
  const sfBefore = setFiltersCalls;
  const resetBefore = resetCellsCalls;
  const pagesBefore = pageCalls().length;
  const item = col.at(3);
  const id = item.id;

  const res = await AppGallery.deleteItem(item);
  await settle();
  eq(res, true, "DELETE réussi");
  eq(lastRequest().method, "DELETE", "méthode DELETE");
  ok(lastRequest().url.endsWith("/media/" + id), "URL /api/media/<id>");

  // Retrait local immédiat + total cohérent.
  eq(col.total, totalBefore - 1, "total décrémenté de 1");
  ok(!collectionHasId(id), "média retiré de la collection locale");
  eq(window.document.getElementById("gallery-count").textContent, (totalBefore - 1) + " médias", "compteur mis à jour");

  // AUCUN rechargement complet NI vidage du pool.
  eq(setFiltersCalls, sfBefore, "[négatif] AUCUN reload complet (setFilters) après notre suppression");
  eq(resetCellsCalls, resetBefore, "[négatif] AUCUN vidage du pool de cellules (_resetCells) → pas de rechargement global");
  assertCellsKept(cellsBefore, srcBefore, [id], "suppression unitaire");

  // Scroll et sélection restante préservés.
  eq(st, 130, "position de scroll préservée");
  eq(state.selectionIds.length, 1, "sélection restante préservée (nombre)");
  eq(state.selectionIds[0], otherId, "sélection restante préservée (id)");

  // La fenêtre du bord est réparée : aucun trou dans la zone rendue (le média
  // refetché porte le même id → la cellule est réutilisée, pas de vignette).
  let holes = 0;
  snapCells().forEach((el) => { if (el.dataset.holafId && !col.has(parseInt(el.dataset.holafIndex, 10))) holes++; });
  eq(holes, 0, "aucun trou (squelette) dans la zone rendue après retrait");
  eq(pageCalls().length - pagesBefore, 1, "1 refetch de PAGE (réparation du bord), PAS un reload complet");

  // Toast de succès (aucune erreur).
  ok(!!window.document.querySelector(".holaf-toast"), "toast de succès affiché");
  state.grid.selection.clear();
  await settle();
}

/* ═══ 2. Suppression GROUPÉE : incrémentale (ids traités seulement) ══════ */
console.log("2. Suppression groupée incrémentale");
{
  await AppGallery.reload();
  await settle();
  await state.collection.ensureRange(0, 119);
  await settle();
  const col = state.collection;
  const totalBefore = col.total;
  const idA = col.at(2).id;
  const idB = col.at(9).id;
  const idSkip = col.at(13).id;
  skipIds = [idSkip];
  state.grid.selection.set([idA, idB, idSkip]);
  await settle();
  eq(state.selectionIds.length, 3, "prérequis : 3 médias sélectionnés (dont un ignoré)");
  st = 120;
  const cellsBefore = snapCells();
  const srcBefore = snapSrcs(cellsBefore);
  const sfBefore = setFiltersCalls;
  const resetBefore = resetCellsCalls;
  const pagesBefore = pageCalls().length;

  const res = await AppGallery.bulkDelete();
  await settle();
  eq(res, true, "suppression groupée réussie");
  eq(lastRequest().method, "POST", "suppression groupée = POST");
  ok(lastRequest().url.endsWith("/media/delete"), "URL /api/media/delete");
  eq(JSON.stringify(lastRequest().body), JSON.stringify({ ids: [idA, idB, idSkip] }), "payload { ids }");

  eq(col.total, totalBefore - 2, "seuls les 2 ids TRAITÉS sont retirés (total -2)");
  ok(!collectionHasId(idA) && !collectionHasId(idB), "ids traités retirés localement");
  ok(collectionHasId(idSkip), "id IGNORÉ conservé dans la vue");
  eq(setFiltersCalls, sfBefore, "[négatif] AUCUN reload complet après la suppression groupée");
  eq(resetCellsCalls, resetBefore, "[négatif] AUCUN vidage du pool de cellules");
  assertCellsKept(cellsBefore, srcBefore, [idA, idB], "suppression groupée");
  eq(st, 120, "position de scroll préservée");
  eq(state.selectionIds.length, 1, "sélection PRUNÉE : seul l'id ignoré reste sélectionné");
  eq(state.selectionIds[0], idSkip, "l'id ignoré garde sa sélection");
  ok(pageCalls().length - pagesBefore <= 1, "au plus 1 refetch de PAGE (réparation du bord)");
  ok(!!window.document.querySelector(".holaf-toast--warning"), "toast warning (ids ignorés)");
  state.grid.selection.clear();
  await settle();
}

/* ═══ 2bis. Fallback : id SÉLECTIONNÉ hors mémoire → reload sûr ═══════════ */
console.log("2bis. Fallback hors mémoire (reload de sécurité)");
{
  const col = state.collection;
  // Dernier média du tri (le plus ancien) : hors des fenêtres chargées.
  const active = items.filter((x) => x.status !== "trashed");
  active.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  const farId = active[active.length - 1].id;
  const farIndex = col.total - 1;
  ok(!col.has(farIndex), "prérequis : le dernier média n'est PAS en mémoire");
  state.grid.selection.set([farId]);
  await settle();
  const sfBefore = setFiltersCalls;
  const totalBefore = col.total;
  const res = await AppGallery.bulkDelete();
  await settle(20);
  eq(res, true, "suppression groupée réussie");
  ok(setFiltersCalls > sfBefore, "ré-ancrage impossible → reload de sécurité (setFilters)");
  eq(state.selectionIds.length, 0, "reload → sélection vidée (état resynchronisé)");
  eq(col.total, totalBefore - 1, "total correct après resynchronisation");
  ok(!collectionHasId(farId), "média hors mémoire bien retiré de la vue après reload");
  await state.collection.ensureRange(0, 119);
  await settle();
}

/* ═══ 3. Suppression depuis la VISIONNEUSE (touche Suppr) ════════════════ */
console.log("3. Suppression depuis la visionneuse");
{
  await AppGallery.reload();
  await settle();
  const col = state.collection;
  const otherId = col.at(6).id;
  state.grid.selection.set([otherId]);
  await settle();
  st = 90;
  const idx = 3;
  const cur = col.at(idx);
  const nextItem = col.at(idx + 1);
  state.lightboxIndex = -1;
  await state.lightbox.openZoom(cur);
  await settle();
  ok(state.lightbox.isOpen(), "prérequis : visionneuse ouverte");
  const cellsBefore = snapCells();
  const srcBefore = snapSrcs(cellsBefore);
  const sfBefore = setFiltersCalls;
  const resetBefore = resetCellsCalls;
  const totalBefore = col.total;

  keyDown(window.document.body, "Delete");
  await settle(20);
  eq(col.total, totalBefore - 1, "média supprimé (total -1)");
  ok(!collectionHasId(cur.id), "média retiré de la collection locale");
  ok(state.lightbox.isOpen(), "visionneuse TOUJOURS ouverte");
  eq(state.lightboxItem && state.lightboxItem.id, nextItem.id, "navigation sur l'image suivante");
  eq(setFiltersCalls, sfBefore, "[négatif] AUCUN reload complet depuis la visionneuse");
  eq(resetCellsCalls, resetBefore, "[négatif] AUCUN vidage du pool de cellules");
  assertCellsKept(cellsBefore, srcBefore, [cur.id], "suppression visionneuse");
  eq(st, 90, "scroll de la grille conservé");
  eq(state.selectionIds.length, 1, "sélection restante conservée");
  eq(state.selectionIds[0], otherId, "sélection restante : même id");
  state.lightbox.close();
  await settle();
  state.grid.selection.clear();
  await settle();
}

/* ═══ 4. Suppression HORS-BANDE : toujours détectée ══════════════════════ */
console.log("4. Suppression hors-bande (non-régression)");
{
  await AppGallery.reload();
  await settle();
  const victim = state.collection.at(0);        // disparaît SANS action UI
  const totalBefore = state.collection.total;
  const keepId = state.collection.at(5).id;
  state.grid.selection.set([keepId]);           // → reload différé
  await settle();
  items = items.filter((x) => x.id !== victim.id);
  await settle();
  await AppGallery.pollNow();
  await settle();
  ok(state.pollPendingReload, "baisse de total HORS-BANDE → resynchronisation demandée");
  const sf1 = setFiltersCalls;
  await AppGallery.pollNow();                   // resynchronisation encore différée (sélection)
  await settle();
  eq(setFiltersCalls, sf1, "[négatif] sélection active → resynchronisation NON exécutée");
  state.grid.selection.clear();
  await settle();
  st = 0;
  await AppGallery.pollNow();                   // plus rien ne perturbe → reload
  await settle();
  eq(state.pollPendingReload, false, "resynchronisation exécutée");
  ok(setFiltersCalls > sf1, "reload complet (setFilters) — assumé pour l'hors-bande");
  ok(!collectionHasId(victim.id), "le média supprimé hors-bande a disparu de la vue");
  eq(state.collection.total, totalBefore - 1, "total reflète la vérité serveur (suppression hors-bande intégrée)");
}

/* ═══ 5. Course poll/notre suppression : réponse périmée ignorée ═════════ */
console.log("5. Course poll / notre suppression");
{
  await AppGallery.reload();
  await settle();
  const totalBefore = state.collection.total;
  // (a) La réponse de tête part AVANT la suppression, arrive APRÈS.
  let resolveHead = null;
  deferredResponse = {
    snapshot: (u) => listPayload(u),
    promise: new Promise((r) => { resolveHead = r; }),
  };
  AppGallery.pollNow();                         // requête de tête en vol
  await settle();
  ok(state.pollInFlight, "prérequis : une requête de tête est EN VOL");
  const item = state.collection.at(4);
  const sfBefore = setFiltersCalls;
  await AppGallery.deleteItem(item);            // notre suppression pendant le vol
  await settle();
  resolveHead();                                // la réponse PÉRIMÉE arrive
  await settle();
  eq(state.pollPendingReload, false, "[garde mutationSeq] réponse périmée NON interprétée comme hors-bande");
  await AppGallery.pollNow();                   // tick frais : total = serveur
  await settle();
  eq(setFiltersCalls, sfBefore, "[négatif] AUCUN reload complet après notre propre suppression (course incluse)");
  eq(state.collection.total, totalBefore - 1, "total local = serveur");
  ok(!collectionHasId(item.id), "le média supprimé n'est PAS réinséré");

  // (b) CONTRÔLE NÉGATIF : sans le marquage des mutations locales (séquence
  //     remise à sa valeur d'avant la suppression), la MÊME réponse périmée
  //     déclenche bien la resynchronisation complète → c'est le garde qui
  //     l'empêche, pas autre chose.
  let resolveHead2 = null;
  deferredResponse = {
    snapshot: (u) => listPayload(u),
    promise: new Promise((r) => { resolveHead2 = r; }),
  };
  const seq2 = state.mutationSeq;
  AppGallery.pollNow();
  await settle();
  const item2 = state.collection.at(4);
  await AppGallery.deleteItem(item2);
  await settle();
  state.mutationSeq = seq2;                     // ⚠ simulateur : garde neutralisé
  resolveHead2();
  await settle();
  ok(state.pollPendingReload, "[contrôle négatif] sans le garde, la réponse périmée déclenche la resynchronisation");
  state.grid.selection.clear();
  st = 0;
  const sf2 = setFiltersCalls;
  await AppGallery.pollNow();
  await settle();
  ok(setFiltersCalls > sf2, "[contrôle négatif] …et un RELOAD COMPLET s'exécute (bug reproduit)");
  ok(!collectionHasId(item2.id), "après resynchronisation, l'état reste correct");
}

/* ═══ 6. Favori en PLEIN ÉCRAN (★/☆) ═════════════════════════════════════ */
console.log("6. Icône favori dans la visionneuse");
{
  await AppGallery.reload();
  await settle();
  const col = state.collection;
  // Indices garantis : on cherche une paire (i, i+1) dont l'état favori diffère.
  let i = 0;
  while (i < col.total - 1 && !!col.at(i).favorite === !!col.at(i + 1).favorite) i++;
  ok(i < col.total - 1, "prérequis : deux médias voisins d'état favori DIFFÉRENT");
  const first = col.at(i);
  const second = col.at(i + 1);
  state.lightboxIndex = i; // comme galleryOnActivate (double-clic sur la cellule)
  await state.lightbox.openZoom(first);
  await settle(8);

  // (a) Présence + position + état initial.
  const star = window.document.querySelector(".gallery-lightbox-favorite");
  const dl = window.document.querySelector(".gallery-lightbox-download");
  ok(!!star, "bouton ★/☆ injecté dans l'overlay de la visionneuse");
  ok(!!star && !!dl, "bouton ⤓ toujours présent à côté");
  ok(!!star && star.classList.contains("holaf-lightbox-nav"), "réutilise la classe de nav de la brique (aucune modif)");
  eq(star.style.top, "20px", "étoile alignée en haut (même chrome que ⤓)");
  eq(star.style.right, "120px", "étoile à gauche du bouton ⤓ (70px) — pas de chevauchement");
  eq(star.textContent, first.favorite ? "★" : "☆", "icône = état favori de l'image AFFICHÉE à l'ouverture");
  eq(star.getAttribute("aria-pressed"), first.favorite ? "true" : "false", "aria-pressed reflète l'état");
  eq(star.getAttribute("aria-label"), first.favorite ? "Retirer des favoris" : "Marquer comme favori", "aria-label explicite");

  // (b) Navigation : l'icône SUIT l'image affichée (contrôle négatif naturel :
  //     sans mise à jour sur navigate, l'assertion rougit).
  await state.lightbox.navigate(1);
  await settle(8);
  eq(state.lightboxItem && state.lightboxItem.id, second.id, "prérequis : navigation vers l'image suivante");
  eq(star.textContent, second.favorite ? "★" : "☆", "navigation → l'icône suit l'image (état " + (second.favorite ? "★" : "☆") + ")");
  eq(star.getAttribute("aria-pressed"), second.favorite ? "true" : "false", "navigation → aria-pressed à jour");
  await state.lightbox.navigate(-1);
  await settle(8);
  eq(state.lightboxItem && state.lightboxItem.id, first.id, "retour à la première image");
  eq(star.textContent, first.favorite ? "★" : "☆", "retour → l'icône suit de nouveau");

  // (c) Bascule au clic : bon payload + optimiste + cellule + visionneuse ouverte.
  const prevFav = !!first.favorite;
  const id = first.id;
  const idxBefore = state.lightboxIndex;
  click(star);
  // Optimiste SYNC : l'étoile bascule avant la réponse.
  eq(star.textContent, prevFav ? "☆" : "★", "bascule OPTIMISTE de l'icône");
  await settle();
  eq(lastRequest().method, "POST", "bascule → POST");
  ok(lastRequest().url.endsWith("/media/" + id + "/favorite"), "endpoint /api/media/<id>/favorite (même que la grille)");
  eq(JSON.stringify(lastRequest().body), JSON.stringify({ favorite: !prevFav }), "payload { favorite: " + (!prevFav) + " }");
  eq(first.favorite, !prevFav, "drapeau mémoire inversé");
  eq(star.textContent, prevFav ? "☆" : "★", "icône finale cohérente");
  ok(state.lightbox.isOpen(), "visionneuse JAMAIS fermée par la bascule");
  eq(state.lightboxIndex, idxBefore, "navigation NON perturbée (même index)");
  const cell = cellFor(id);
  ok(!!cell, "cellule correspondante chargée dans la grille");
  const cellFav = cell && cell.querySelector(".gallery-cell-fav");
  ok(!!cellFav && cellFav.textContent === (first.favorite ? "★" : "☆"), "cellule de grille mise à jour (★/☆)");
  ok(!!cell && cell.classList.contains("gallery-cell--favorite") === !!first.favorite, "classe --favorite de la cellule synchronisée");

  // (d) Re-bascule : retour à l'état précédent + payload inverse.
  click(star);
  await settle();
  eq(JSON.stringify(lastRequest().body), JSON.stringify({ favorite: prevFav }), "2e clic → payload inverse");
  eq(first.favorite, prevFav, "état revenu à la valeur d'origine");

  // (e) Échec → rollback (icône + cellule) + toast, visionneuse ouverte.
  failNextFavorite = true;
  const errBefore = window.document.querySelectorAll(".holaf-toast--error").length;
  click(star);
  await settle();
  eq(first.favorite, prevFav, "échec → ROLLBACK du drapeau");
  eq(star.textContent, prevFav ? "★" : "☆", "échec → icône restaurée");
  ok(!!cellFav && cellFav.textContent === (prevFav ? "★" : "☆"), "échec → cellule restaurée");
  ok(window.document.querySelectorAll(".holaf-toast--error").length > errBefore, "échec → toast d'erreur");
  ok(state.lightbox.isOpen(), "échec → visionneuse toujours ouverte");
  eq(state.lightboxItem && state.lightboxItem.id, id, "échec → toujours sur la même image");

  // (f) Busy : un 2e clic immédiat ne double pas l'appel.
  const callsBefore = calls.filter((c) => c.url.indexOf("/favorite") !== -1).length;
  click(star);
  click(star);
  await settle();
  eq(calls.filter((c) => c.url.indexOf("/favorite") !== -1).length, callsBefore + 1, "verrou busy : un seul appel pour deux clics");
  state.lightbox.close();
  await settle();
}

/* ═══ 7. Briques holaf non modifiées ═════════════════════════════════════ */
console.log("7. Briques holaf byte-identiques");
{
  const LIB = "/projects/holaf-lib/js/";
  const pairs = [
    ["holaf-collection.js", "holaf-collection.js"],
    ["holaf-virtual-grid.js", "holaf-virtual-grid.js"],
    ["holaf-lightbox.js", "holaf-lightbox.js"],
    ["holaf-thumbcache.js", "holaf-thumbcache.js"],
  ];
  if (existsSync(LIB)) {
    let identical = 0;
    for (const [local, lib] of pairs) {
      const a = readFileSync(resolve(HERE, "..", "vendor", "holaf", local), "utf8");
      const b = readFileSync(LIB + lib, "utf8");
      if (a === b) identical++;
    }
    eq(identical, pairs.length, "les briques clés sont byte-identiques à holaf-lib");
  } else {
    console.log("  (holaf-lib introuvable → preuve d'identité ignorée)");
  }
}

AppGallery.stop();
console.log("");
console.log("✅ test_gallery_delete : " + n + " assertions PASSENT");
process.exit(0);
