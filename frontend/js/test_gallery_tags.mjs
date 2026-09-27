// Test headless (jsdom) — Onglet « Galerie » : TAGS MANUELS.
// Usage : node frontend/js/test_gallery_tags.mjs   (via ./js/run_js_tests.sh)
//
// Suite dédiée aux TAGS (manuel aujourd'hui, source `ai` préparée) :
//   1. construction PURE de la requête GET /api/media?tags=… (paramètre répété) ;
//   2. état de filtre tags : applyTags → bouton + reload avec `tags=` ;
//   3. modale de filtrage (#gallery-tags-modal) : alimentée par GET /api/media/tags,
//      comptes, Tout/Aucun/Inverser, Appliquer (query tags=… + reload) /
//      Annuler (aucun effet) ;
//   4. chips du panneau d'infos : rendu (manuel + IA distinguée), retrait (croix →
//      POST /api/media/<id>/tags {remove}) et ajout (champ + Entrée → {add}) ;
//   5. normalisation client (miroir léger du backend) ;
//   6. action GROUPÉE : modale → POST /api/media/tags {ids, add|remove} + récap ;
//   7. non-régression (favoris, filtres existants).
//
// Contrôles négatifs : un filtre absent n'ajoute jamais `tags=` ; une action de
// retrait doit produire le BON payload (sinon le mock rejette).
import assert from "node:assert";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import { loadJsdomOrSkip } from "./test_helpers/jsdom_loader.mjs";

const JSDOM = await loadJsdomOrSkip("test_gallery_tags");
const HERE = dirname(fileURLToPath(import.meta.url));

/* ── DOM de l'onglet Galerie (toolbar + chips + modale) ──────────────────── */
const dom = new JSDOM(`<!doctype html><html><body>
  <div id="tab-gallery">
    <input type="range" id="gallery-thumb-size" min="80" max="300" step="10" value="150">
    <span id="gallery-thumb-size-value"></span>
    <span id="gallery-thumb-resolution"></span>
    <span id="gallery-selected" class="hidden"></span>
    <span id="gallery-count"></span>
    <select id="gallery-filter-kind"><option value="">Tous</option><option value="image">Images</option></select>
    <button type="button" id="gallery-filter-folders" aria-haspopup="dialog">Dossiers : tous</button>
    <button type="button" id="gallery-filter-tags" aria-haspopup="dialog">Tags : tous</button>
    <button type="button" id="gallery-filter-favorite" aria-pressed="false">★ Favoris</button>
    <input id="gallery-search" type="search">
    <input id="gallery-filter-from" type="date">
    <input id="gallery-filter-to" type="date">
    <select id="gallery-filter-sort"><option value="created_at_desc">Récents</option><option value="name_asc">Nom</option></select>
    <div id="gallery-grid"></div>
    <div id="gallery-loading"></div>
    <div id="gallery-empty" class="hidden"></div>
    <div id="gallery-error" class="hidden"></div>
    <div id="gallery-side">
      <aside id="gallery-info"></aside>
      <div id="gallery-actions" class="hidden">
        <span id="gallery-actions-label"></span>
        <button id="gallery-action-download" data-gallery-action>⤓</button>
        <button id="gallery-action-favorite" data-gallery-action>★</button>
        <button id="gallery-action-tags" data-gallery-action>🏷 Tag</button>
        <button id="gallery-action-delete" data-gallery-action>🗑</button>
        <button id="gallery-action-restore" data-gallery-action class="hidden">♻</button>
        <button id="gallery-action-purge" data-gallery-action class="hidden">✖</button>
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

const gridEl = window.document.getElementById("gallery-grid");
let cw = 600, ch = 400, st = 0;
Object.defineProperty(gridEl, "clientWidth", { configurable: true, get: () => cw });
Object.defineProperty(gridEl, "clientHeight", { configurable: true, get: () => ch });
Object.defineProperty(gridEl, "scrollTop", { configurable: true, get: () => st, set: (v) => { st = Math.max(0, v); } });
gridEl.getBoundingClientRect = () => ({ left: 0, top: 0, right: cw, bottom: ch, width: cw, height: ch, x: 0, y: 0, toJSON() {} });

/* ── Jeu de données : tags manuels + un tag IA ───────────────────────────── */
function detail(list) { return list.map((t) => ({ tag: t.tag, source: t.source || "manual" })); }
let items = [
  { id: 1, filename: "a.png", kind: "image", size: 100, created_at: "2025-01-01T00:00:00Z", subfolder: "s1", status: "active", trashed: false, thumb_available: true, favorite: false, tags: ["ciel", "mer"], tags_detail: detail([{ tag: "ciel" }, { tag: "mer" }]) },
  { id: 2, filename: "b.png", kind: "image", size: 200, created_at: "2025-01-02T00:00:00Z", subfolder: "s2", status: "active", trashed: false, thumb_available: true, favorite: true, tags: ["mer"], tags_detail: detail([{ tag: "mer" }]) },
  { id: 3, filename: "c.png", kind: "image", size: 300, created_at: "2025-01-03T00:00:00Z", subfolder: "s3", status: "active", trashed: false, thumb_available: true, favorite: false, tags: ["montagne"], tags_detail: detail([{ tag: "montagne", source: "ai" }]) },
  { id: 4, filename: "d.png", kind: "image", size: 400, created_at: "2025-01-04T00:00:00Z", subfolder: "s4", status: "active", trashed: false, thumb_available: true, favorite: false, tags: [], tags_detail: [] },
];

const calls = [];
let failNextTags = false;
function makeRes(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, text: async () => JSON.stringify(body) };
}
function findItem(id) { return items.find((x) => x.id === id); }
function applyTags(item, add, remove) {
  (add || []).forEach((t) => {
    const exists = item.tags.some((x) => x.toLowerCase() === t.toLowerCase());
    if (!exists) { item.tags.push(t); item.tags_detail.push({ tag: t, source: "manual" }); }
  });
  (remove || []).forEach((t) => {
    const idx = item.tags.findIndex((x) => x.toLowerCase() === t.toLowerCase());
    if (idx !== -1) { item.tags.splice(idx, 1); item.tags_detail.splice(idx, 1); }
  });
  item.tags.sort((a, b) => a.localeCompare(b));
}
function metadataFor(id) {
  const it = findItem(id) || {};
  return {
    id, filename: it.filename, subfolder: it.subfolder, kind: it.kind, ext: "", size: it.size,
    created_at: it.created_at, width: 512, height: 512, ratio: 1, duration: null, duration_ms: null,
    codec: null, prompt: "p", workflow: "", has_prompt: true, has_workflow: false,
    favorite: !!it.favorite, tags: it.tags.slice(), tags_detail: it.tags_detail.map((t) => ({ tag: t.tag, source: t.source })),
  };
}

globalThis.fetch = (url, opts = {}) => {
  const method = (opts.method || "GET").toUpperCase();
  const rawUrl = String(url);
  const body = opts.body ? JSON.parse(opts.body) : null;
  calls.push({ method, url: rawUrl, body });

  // Metadata.
  let m = rawUrl.match(/\/api\/media\/(\d+)\/metadata/);
  if (m) return Promise.resolve(makeRes(200, metadataFor(parseInt(m[1], 10))));

  // Tags unitaires : POST /api/media/<id>/tags.
  m = rawUrl.match(/\/api\/media\/(\d+)\/tags$/);
  if (m && method === "POST") {
    if (failNextTags) { failNextTags = false; return Promise.resolve(makeRes(500, { error: "boom" })); }
    const it = findItem(parseInt(m[1], 10));
    if (it) applyTags(it, body && body.add, body && body.remove);
    return Promise.resolve(makeRes(200, it ? metadataFor(it.id) : {}));
  }

  // Liste des tags (modale de filtre).
  if (rawUrl.indexOf("/api/media/tags") !== -1 && method === "GET") {
    const counts = {};
    for (const it of items) { if (it.status === "trashed") continue; for (const t of it.tags) counts[t] = (counts[t] || 0) + 1; }
    const tags = Object.keys(counts).sort((a, b) => a.localeCompare(b)).map((t) => ({ tag: t, count: counts[t] }));
    return Promise.resolve(makeRes(200, { tags, total: tags.length }));
  }

  // Tags GROUPÉS : POST /api/media/tags.
  if (rawUrl.indexOf("/api/media/tags") !== -1 && method === "POST") {
    if (failNextTags) { failNextTags = false; return Promise.resolve(makeRes(500, { error: "boom" })); }
    const ids = (body && Array.isArray(body.ids)) ? body.ids : [];
    const updated = []; const skipped = [];
    for (const id of ids) {
      const it = findItem(id);
      if (!it) { skipped.push(id); continue; }
      applyTags(it, body && body.add, body && body.remove);
      updated.push(id);
    }
    return Promise.resolve(makeRes(200, { updated: updated.length, skipped }));
  }

  // Favori unitaire (non-régression).
  m = rawUrl.match(/\/api\/media\/(\d+)\/favorite$/);
  if (m && method === "POST") {
    const it = findItem(parseInt(m[1], 10));
    if (it) it.favorite = !!(body && body.favorite);
    return Promise.resolve(makeRes(200, metadataFor(it ? it.id : 0)));
  }

  // Liste paginée.
  if (rawUrl.indexOf("/api/media?") !== -1) {
    const u = new URL(rawUrl, "http://localhost");
    const page = parseInt(u.searchParams.get("page"), 10) || 1;
    const limit = parseInt(u.searchParams.get("limit"), 10) || 60;
    const kind = u.searchParams.get("kind") || "";
    const favorite = u.searchParams.get("favorite") || "";
    const wantTags = [];
    for (const raw of u.searchParams.getAll("tags")) for (const p of raw.split(",")) if (p) wantTags.push(p.toLowerCase());
    let list = items.filter((i) => i.status !== "trashed");
    if (kind) list = list.filter((i) => i.kind === kind);
    if (favorite === "1") list = list.filter((i) => !!i.favorite);
    if (wantTags.length) list = list.filter((i) => i.tags.some((t) => wantTags.indexOf(t.toLowerCase()) !== -1));
    const total = list.length;
    const start = (page - 1) * limit;
    return Promise.resolve(makeRes(200, { items: list.slice(start, start + limit), total, page, limit }));
  }
  return Promise.resolve(makeRes(404, { error: "not found" }));
};

/* ── Briques + adaptateur ────────────────────────────────────────────────── */
for (const b of ["collection", "thumbcache", "virtual-grid", "viewport", "lightbox", "infopane", "toast", "modal"]) {
  await import("../vendor/holaf/holaf-" + b + ".js");
}
if (window.HolafToast && window.HolafToast.configure) {
  window.HolafToast.configure({ duration: 0, position: "bottom-right" });
}
await import("./app-gallery.js");

const AppGallery = window.AppGallery;
const state = AppGallery.state;

let n = 0;
function ok(cond, msg) { if (!cond) throw new Error("ASSERT FAIL: " + msg); n++; }
function eq(a, b, msg) { ok(a === b, msg + " (got " + JSON.stringify(a) + ", want " + JSON.stringify(b) + ")"); }
const settle = async (times = 12) => { for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0)); };
const click = (el, mods = {}) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, ctrlKey: !!mods.ctrl }));
const cellEl = (i) => window.document.querySelector('.holaf-grid-surface [data-holaf-index="' + i + '"]');
const keyDown = (el, k, mods = {}) => el.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true, ctrlKey: !!mods.ctrl }));
const listCalls = () => calls.filter((c) => c.method === "GET" && c.url.indexOf("/api/media?") !== -1);
const lastListCall = () => listCalls()[listCalls().length - 1];
const unitTagCalls = () => calls.filter((c) => /\/api\/media\/\d+\/tags$/.test(c.url));
const bulkTagCalls = () => calls.filter((c) => c.method === "POST" && /\/api\/media\/tags$/.test(c.url));
const byId = (id) => window.document.getElementById(id);
const modalRoot = (id) => window.document.getElementById(id);

/* ═══ 1. Requête : paramètre tags répété (construction PURE) ══════════════ */
console.log("1. Construction de la requête tags");
eq(AppGallery.mediaUrl(1, 60, { tags: ["a", "b/c"] }, "created_at_desc"),
   "/api/media?page=1&limit=60&sort=created_at_desc&tags=a&tags=b%2Fc",
   "tags : paramètre répété + encodé");
eq(AppGallery.mediaUrl(1, 60, { tags: [] }, "created_at_desc").indexOf("tags="), -1,
   "[négatif] liste vide → aucun paramètre tags");
eq(AppGallery.mediaUrl(1, 60, {}, "created_at_desc").indexOf("tags="), -1,
   "[négatif] absence → aucun paramètre tags");
eq(AppGallery.constants.TAG_MAX_LEN, 50, "TAG_MAX_LEN exposé");

/* ═══ 2. Normalisation client ═════════════════════════════════════════════ */
console.log("2. Normalisation client du tag");
eq(AppGallery.normalizeTagInput("  Sunset  "), "Sunset", "trim");
eq(AppGallery.normalizeTagInput("a   b"), "a b", "espaces réduits");
eq(AppGallery.normalizeTagInput(""), "", "[négatif] vide invalide");
eq(AppGallery.normalizeTagInput("a,b"), "", "[négatif] virgule invalide");
eq(AppGallery.normalizeTagInput("x".repeat(51)), "", "[négatif] trop long invalide");
eq(AppGallery.normalizeTagInput("éclair"), "éclair", "accent conservé");

/* ═══ 3. Démarrage + filtre tags (état) ═══════════════════════════════════ */
console.log("3. Filtre tags : bouton + applyTags");
AppGallery.start();
await settle();
eq(state.collection.total, 4, "4 médias en vue normale");
{
  const btn = byId("gallery-filter-tags");
  ok(!!btn, "bouton Tags présent");
  eq(btn.textContent, "Tags : tous", "état initial « Tags : tous »");
}
await AppGallery.applyTags(["mer"]);
await settle();
ok(lastListCall().url.indexOf("tags=mer") !== -1, "applyTags → tags=mer dans la requête");
eq(state.selectedTags.length, 1, "state.selectedTags = 1");
eq(byId("gallery-filter-tags").textContent, "Tags (1)", "libellé du bouton mis à jour");
ok(lastListCall().url.indexOf("tags=mer") !== -1 && state.collection.total === 2, "2 médias portent « mer »");
// Négatif : reset → plus de tags=.
AppGallery.resetFilters();
await settle();
ok(lastListCall().url.indexOf("tags=") === -1, "[négatif] reset → plus aucun tags=");
eq(byId("gallery-filter-tags").textContent, "Tags : tous", "reset → bouton réinitialisé");

/* ═══ 4. Modale de filtrage par tags ══════════════════════════════════════ */
console.log("4. Modale de filtrage par tags");
ok(typeof AppGallery.openTagsModal === "function", "openTagsModal exposé");
eq(AppGallery.tagsUrl(), "/api/media/tags", "URL liste des tags (vue normale)");
AppGallery.openTagsModal();
await settle(10);
ok(!!modalRoot("gallery-tags-modal"), "modale #gallery-tags-modal ouverte");
{
  const rows = modalRoot("gallery-tags-modal").querySelectorAll(".gallery-folder-item");
  eq(rows.length, 3, "3 tags listés (ciel, mer, montagne)");
  const names = Array.from(rows).map((r) => r.querySelector(".gallery-folder-name").textContent);
  ok(names.indexOf("ciel") !== -1 && names.indexOf("mer") !== -1, "noms des tags affichés");
  // Comptes.
  const merRow = Array.from(rows).find((r) => r.querySelector(".gallery-folder-name").textContent === "mer");
  eq(merRow.querySelector(".gallery-folder-count").textContent, "(2)", "compte du tag mer = 2");
  // Tout → coche tout ; Inverser → décoche tout.
  const actions = modalRoot("gallery-tags-modal").querySelectorAll(".gallery-folders-action");
  const btnTout = Array.from(actions).find((b) => b.textContent === "Tout");
  click(btnTout);
  const checks = modalRoot("gallery-tags-modal").querySelectorAll(".gallery-folder-check");
  ok(Array.from(checks).every((c) => c.checked), "Tout coche tous les tags");
  const status = modalRoot("gallery-tags-modal").querySelector(".gallery-folders-status").textContent;
  ok(status.indexOf("3") !== -1, "statut = 3 sélectionnés");
}
// Appliquer avec « ciel » coché → reload avec tags=ciel.
{
  // Aucun (décocher tout), puis cocher « ciel ».
  const actions = modalRoot("gallery-tags-modal").querySelectorAll(".gallery-folders-action");
  click(Array.from(actions).find((b) => b.textContent === "Aucun"));
  const rows = modalRoot("gallery-tags-modal").querySelectorAll(".gallery-folder-item");
  const cielRow = Array.from(rows).find((r) => r.querySelector(".gallery-folder-name").textContent === "ciel");
  const cb = cielRow.querySelector(".gallery-folder-check");
  cb.checked = true;
  cb.dispatchEvent(new window.Event("change", { bubbles: true }));
}
const before = listCalls().length;
{
  const btns = Array.from(modalRoot("gallery-tags-modal").querySelectorAll("button"));
  const applyBtn = btns.find((b) => b.textContent === "Appliquer");
  click(applyBtn);
}
await settle();
ok(listCalls().length > before, "Appliquer relance la liste");
ok(lastListCall().url.indexOf("tags=ciel") !== -1, "Appliquer → query tags=ciel");
eq(state.selectedTags.join(","), "ciel", "state.selectedTags = [ciel]");
// Annuler sans effet : rouvrir, cocher, Annuler → état inchangé.
AppGallery.resetFilters();
await settle();
AppGallery.openTagsModal();
await settle(10);
{
  const rows = modalRoot("gallery-tags-modal").querySelectorAll(".gallery-folder-item");
  const merRow = Array.from(rows).find((r) => r.querySelector(".gallery-folder-name").textContent === "mer");
  const cb = merRow.querySelector(".gallery-folder-check");
  cb.checked = true;
  cb.dispatchEvent(new window.Event("change", { bubbles: true }));
  const btns = Array.from(modalRoot("gallery-tags-modal").querySelectorAll("button"));
  click(btns.find((b) => b.textContent === "Annuler"));
}
await settle();
eq(state.selectedTags.length, 0, "[négatif] Annuler → aucun tag appliqué");

/* ═══ 5. Chips du panneau d'infos : rendu + retrait + ajout ═══════════════ */
console.log("5. Chips du panneau d'infos");
{
  const item = state.collection.at(0); // le plus récent trié (created_at_desc) → id 4 (sans tag)
  const withTags = findItem(1);
  state.infoPane.show(withTags);
  await settle(15);
  const infoEl = byId("gallery-info");
  // Le champ « Tags » doit être rendu via raw HTML.
  const fields = AppGallery.infoFields(metadataFor(1));
  const tagField = fields.find((f) => f.label === "Tags");
  ok(tagField && typeof tagField.raw === "string" && tagField.raw.indexOf("gallery-tag-chip") !== -1,
     "infoFields expose un champ Tags en HTML");
  const chips = infoEl.querySelectorAll(".gallery-tag-chip");
  ok(chips.length === 2, "2 chips rendues pour le média 1");
  ok(infoEl.querySelector(".gallery-tag-input"), "champ d'ajout de tag présent");
  // Distinction IA : le média 3 a un tag source 'ai'.
  const html3 = AppGallery.tagsFieldHtml([{ tag: "montagne", source: "ai" }, { tag: "x", source: "manual" }]);
  ok(html3.indexOf("gallery-tag-chip--ai") !== -1, "chip IA a la classe --ai");
  ok(html3.indexOf("gallery-tag-chip--manual") !== -1, "chip manuelle a la classe --manual");

  // Retrait via la croix → POST /api/media/1/tags {remove:[…]}.
  const before = unitTagCalls().length;
  const removeBtn = infoEl.querySelector('[data-gallery-tag-remove="mer"]');
  ok(!!removeBtn, "bouton croix pour « mer »");
  click(removeBtn);
  await settle();
  const call = unitTagCalls()[unitTagCalls().length - 1];
  ok(unitTagCalls().length > before, "la croix déclenche un appel tags");
  ok(/\/api\/media\/1\/tags$/.test(call.url), "appel sur le média 1");
  eq(JSON.stringify(call.body), JSON.stringify({ add: [], remove: ["mer"] }), "payload remove correct");
  eq(findItem(1).tags.join(","), "ciel", "tag « mer » retiré de la mémoire");

  // Ajout via le champ + Entrée → POST {add:[…]}.
  await settle(10);
  const input = byId("gallery-info").querySelector(".gallery-tag-input");
  ok(!!input, "champ d'ajout re-rendu après retrait");
  input.value = "nouveau";
  keyDown(input, "Enter");
  await settle();
  const call2 = unitTagCalls()[unitTagCalls().length - 1];
  eq(JSON.stringify(call2.body), JSON.stringify({ add: ["nouveau"], remove: [] }), "payload add correct");
  ok(findItem(1).tags.indexOf("nouveau") !== -1, "tag « nouveau » ajouté en mémoire");
}

/* ═══ 6. Action GROUPÉE : tag sur la sélection ════════════════════════════ */
console.log("6. Action groupée : tag");
ok(typeof AppGallery.bulkTags === "function", "bulkTags exposé");
ok(!!byId("gallery-action-tags"), "bouton d'action groupée présent");
{
  AppGallery.clearSelection();
  await settle();
  click(cellEl(0));
  await settle();
  click(cellEl(1), { ctrl: true });
  await settle();
  eq(state.selectionIds.length, 2, "2 médias sélectionnés");
  const sel = state.selectionIds.slice();
  await AppGallery.bulkTags();
  await settle(3);
  ok(!!modalRoot("gallery-tags-action-modal"), "modale d'action groupée ouverte");
  const input = modalRoot("gallery-tags-action-modal").querySelector(".gallery-tags-action-input");
  ok(!!input, "champ de tag présent dans la modale groupée");
  input.value = "lot";
  const before = bulkTagCalls().length;
  const btns = Array.from(modalRoot("gallery-tags-action-modal").querySelectorAll("button"));
  click(btns.find((b) => b.textContent === "Ajouter"));
  await settle(10);
  const call = bulkTagCalls()[bulkTagCalls().length - 1];
  ok(bulkTagCalls().length > before, "le bouton Ajouter émet POST /api/media/tags");
  eq(JSON.stringify(call.body), JSON.stringify({ ids: sel, add: ["lot"] }), "payload {ids, add} correct");
  ok(sel.every((id) => findItem(id).tags.indexOf("lot") !== -1), "tag appliqué aux 2 médias");
}
// Tag invalide → la modale ne ferme pas et aucun appel.
{
  AppGallery.clearSelection();
  await settle();
  click(cellEl(0));
  await settle();
  AppGallery.bulkTags();
  await settle(3);
  ok(!!modalRoot("gallery-tags-action-modal"), "modale groupée rouverte");
  const input = modalRoot("gallery-tags-action-modal").querySelector(".gallery-tags-action-input");
  input.value = "a,b";
  const before = bulkTagCalls().length;
  const btns = Array.from(modalRoot("gallery-tags-action-modal").querySelectorAll("button"));
  click(btns.find((b) => b.textContent === "Ajouter"));
  await settle(5);
  eq(bulkTagCalls().length, before, "[négatif] tag invalide → aucun appel");
  ok(!!modalRoot("gallery-tags-action-modal"), "[négatif] modale toujours ouverte");
  const cancel = Array.from(modalRoot("gallery-tags-action-modal").querySelectorAll("button")).find((b) => b.textContent === "Annuler");
  click(cancel);
  await settle(3);
}

/* ═══ 7. Non-régression : favoris + filtres existants ════════════════════ */
console.log("7. Non-régression");
{
  AppGallery.resetFilters();
  await settle();
  const item = findItem(2);
  const prev = item.favorite;
  await AppGallery.toggleFavorite(item);
  await settle();
  eq(findItem(2).favorite, !prev, "bascule favori opérationnelle");
  ok(state.favoriteOnly === false, "le filtre tags n'a pas activé le filtre favoris");
}
{
  // Un média SANS le tag demandé ne ressort pas.
  await AppGallery.applyTags(["montagne"]);
  await settle();
  eq(state.collection.total, 1, "filtre tags=montagne → 1 média");
  let allHave = true;
  state.collection.forEachLoaded((x) => { if (x.tags.indexOf("montagne") === -1) allHave = false; });
  ok(allHave, "tous les items rendus portent « montagne »");
  AppGallery.resetFilters();
  await settle();
  eq(state.collection.total, 4, "reset → 4 médias");
}

/* ═══ 8. Échec réseau : pas de corruption d'état ═════════════════════════ */
console.log("8. Échec réseau");
{
  failNextTags = true;
  const item = findItem(2);
  const before = item.tags.slice();
  const r = await AppGallery.applyItemTags(item, ["boom"], []);
  await settle();
  eq(r, false, "échec → false");
  eq(item.tags.join(","), before.join(","), "aucune mutation locale en cas d'échec");
  ok(!state.busy, "verrou libéré après échec");
}

/* ═══ 9. Briques vendor non modifiées ════════════════════════════════════ */
console.log("9. Briques holaf non modifiées");
{
  const { readFileSync, existsSync } = await import("node:fs");
  const { resolve } = await import("node:path");
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

console.log("\n✅ test_gallery_tags : " + n + " assertions PASSENT");
