// Test headless (jsdom) — Onglet « Galerie » : AUTO-TAG IA (vision).
// Usage : node frontend/js/test_gallery_autotag.mjs   (via ./js/run_js_tests.sh)
//
// Suite dédiée à l'action « 🤖 Auto-tag (IA) » sur la sélection :
//   1. présence du bouton + du SÉLECTEUR de preset vision ;
//   2. le sélecteur ne liste QUE les presets `supports_vision` (contrôle
//      négatif : un preset classique ne doit jamais apparaître) ;
//   3. exécution : UN appel POST /api/media/<id>/auto-tag {preset_id} par média,
//      payload correct (observable lastAutoTagRequest) ;
//   4. PROGRESSION visible + récap final (toast) succès/ignorés/erreurs ;
//   5. ANNULATION : la boucle s'arrête entre deux médias ;
//   6. aucun preset vision → message actionnable, AUCUN appel ;
//   7. non-régression (favoris + tags manuels).
//
// Contrôles négatifs : retirer le filtre `supports_vision` ferait apparaître le
// preset classique dans le sélecteur → assertion rouge ; un preset vision absent
// ne doit produire AUCUN POST auto-tag.
import assert from "node:assert";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import { loadJsdomOrSkip } from "./test_helpers/jsdom_loader.mjs";

const JSDOM = await loadJsdomOrSkip("test_gallery_autotag");
const HERE = dirname(fileURLToPath(import.meta.url));

/* ── DOM de l'onglet Galerie (barre d'actions + sélecteur + progression) ─── */
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
        <select id="gallery-auto-tag-preset"></select>
        <button id="gallery-action-auto-tag" data-gallery-action>🤖 Auto-tag (IA)</button>
        <button id="gallery-action-autotag-cancel" class="hidden">✖ Annuler</button>
        <div id="gallery-autotag-progress" class="hidden"></div>
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

/* ── Jeu de données ──────────────────────────────────────────────────────── */
let items = [
  { id: 1, filename: "a.png", kind: "image", size: 100, created_at: "2025-01-01T00:00:00Z", subfolder: "s1", status: "complete", trashed: false, thumb_available: true, favorite: false, tags: [], tags_detail: [] },
  { id: 2, filename: "b.png", kind: "image", size: 200, created_at: "2025-01-02T00:00:00Z", subfolder: "s2", status: "complete", trashed: false, thumb_available: true, favorite: true, tags: [], tags_detail: [] },
  { id: 5, filename: "v.mp4", kind: "video", size: 300, created_at: "2025-01-03T00:00:00Z", subfolder: "s3", status: "complete", trashed: false, thumb_available: true, favorite: false, tags: [], tags_detail: [] },
];

// Presets : `supports_vision` distingue les presets utilisables par l'auto-tag.
let presets = [
  { id: 10, name: "Vision A", model: "gpt-4o", base_url: "https://api.example.com", is_global: false, is_client_side: false, supports_vision: true },
  { id: 11, name: "Classique", model: "llama3", base_url: "https://api.example.com", is_global: false, is_client_side: false, supports_vision: false },
  { id: 12, name: "Vision B", model: "qwen-vl", base_url: "https://api.example.com", is_global: false, is_client_side: false, supports_vision: true },
];

const calls = [];
const toasts = [];
let failIds = {};        // id → true : le POST auto-tag répond 502
let notImageIds = {};    // id → true : le POST renvoie status skipped
let autoTagDelayMs = 0;  // latence simulée du POST auto-tag (progression visible)

function makeRes(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, text: async () => JSON.stringify(body) };
}
function findItem(id) { return items.find((x) => x.id === id); }
function metadataFor(id) {
  const it = findItem(id) || {};
  return { id, filename: it.filename, subfolder: it.subfolder, kind: it.kind, ext: "", size: it.size, created_at: it.created_at, width: 512, height: 512, ratio: 1, duration: null, duration_ms: null, codec: null, prompt: "", workflow: "", has_prompt: false, has_workflow: false, favorite: !!it.favorite, tags: (it.tags || []).slice(), tags_detail: (it.tags_detail || []).map((t) => ({ tag: t.tag, source: t.source })) };
}

globalThis.fetch = (url, opts = {}) => {
  const method = (opts.method || "GET").toUpperCase();
  const rawUrl = String(url);
  const body = opts.body ? JSON.parse(opts.body) : null;
  calls.push({ method, url: rawUrl, body });

  if (rawUrl.indexOf("/api/presets") !== -1 && method === "GET") {
    return Promise.resolve(makeRes(200, presets.map((p) => ({ ...p }))));
  }

  let m = rawUrl.match(/\/api\/media\/(\d+)\/auto-tag$/);
  if (m && method === "POST") {
    const id = parseInt(m[1], 10);
    let res;
    if (failIds[id]) res = makeRes(502, { error: "Fournisseur LLM", reason: "llm_error" });
    else if (!findItem(id)) res = makeRes(404, { error: "Média introuvable" });
    else if (notImageIds[id]) res = makeRes(200, { media: metadataFor(id), auto_tag: { status: "skipped", reason: "not_image", added: 0, ai_tags: [] } });
    else {
      const it = findItem(id);
      it.tags = ["ia1", "ia2"];
      it.tags_detail = [{ tag: "ia1", source: "ai" }, { tag: "ia2", source: "ai" }];
      res = makeRes(200, { media: metadataFor(id), auto_tag: { status: "tagged", added: 2, ai_tags: ["ia1", "ia2"] } });
    }
    if (autoTagDelayMs) return new Promise((resolve) => setTimeout(() => resolve(res), autoTagDelayMs));
    return Promise.resolve(res);
  }

  m = rawUrl.match(/\/api\/media\/(\d+)\/metadata/);
  if (m) return Promise.resolve(makeRes(200, metadataFor(parseInt(m[1], 10))));

  m = rawUrl.match(/\/api\/media\/(\d+)\/tags$/);
  if (m && method === "POST") {
    const it = findItem(parseInt(m[1], 10));
    if (it) {
      (body && body.add || []).forEach((t) => { it.tags.push(t); it.tags_detail.push({ tag: t, source: "manual" }); });
      (body && body.remove || []).forEach((t) => {
        const i = it.tags.findIndex((x) => x.toLowerCase() === t.toLowerCase());
        if (i !== -1) { it.tags.splice(i, 1); it.tags_detail.splice(i, 1); }
      });
    }
    return Promise.resolve(makeRes(200, metadataFor(it ? it.id : 0)));
  }

  m = rawUrl.match(/\/api\/media\/(\d+)\/favorite$/);
  if (m && method === "POST") {
    const it = findItem(parseInt(m[1], 10));
    if (it) it.favorite = !!(body && body.favorite);
    return Promise.resolve(makeRes(200, metadataFor(it ? it.id : 0)));
  }

  if (rawUrl.indexOf("/api/media?") !== -1) {
    const u = new URL(rawUrl, "http://localhost");
    const page = parseInt(u.searchParams.get("page"), 10) || 1;
    const limit = parseInt(u.searchParams.get("limit"), 10) || 60;
    let list = items.filter((i) => i.status !== "trashed");
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

// Capture des toasts (récap).
if (window.HolafToast && typeof window.HolafToast.show === "function") {
  const realShow = window.HolafToast.show.bind(window.HolafToast);
  window.HolafToast.show = (o) => { toasts.push(o); return realShow(o); };
}

let n = 0;
function ok(cond, msg) { if (!cond) throw new Error("ASSERT FAIL: " + msg); n++; }
function eq(a, b, msg) { ok(a === b, msg + " (got " + JSON.stringify(a) + ", want " + JSON.stringify(b) + ")"); }
const settle = async (times = 15) => { for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0)); };
const click = (el, mods = {}) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, ctrlKey: !!mods.ctrl }));
const cellEl = (i) => window.document.querySelector('.holaf-grid-surface [data-holaf-index="' + i + '"]');
const byId = (id) => window.document.getElementById(id);
const sel = () => byId("gallery-auto-tag-preset");
const optionValues = () => Array.from(sel().querySelectorAll("option")).map((o) => o.value);
const optionLabels = () => Array.from(sel().querySelectorAll("option")).map((o) => o.textContent);
const autoTagCalls = () => calls.filter((c) => c.method === "POST" && /\/api\/media\/\d+\/auto-tag$/.test(c.url));
const lastToast = () => toasts[toasts.length - 1];

async function selectFirst(nItems) {
  AppGallery.clearSelection();
  await settle();
  click(cellEl(0));
  await settle();
  for (let i = 1; i < nItems; i++) { click(cellEl(i), { ctrl: true }); await settle(); }
}

/* ═══ 1. Démarrage + présence bouton/sélecteur ═══════════════════════════ */
console.log("1. Présence du bouton + sélecteur");
AppGallery.start();
await settle();
ok(!!byId("gallery-action-auto-tag"), "bouton 🤖 Auto-tag (IA) présent");
ok(!!byId("gallery-auto-tag-preset"), "sélecteur de preset présent");
ok(!!byId("gallery-autotag-progress"), "élément de progression présent");
eq(AppGallery.constants.TAG_MAX_LEN, 50, "non-régression : constantes exposées");

/* ═══ 2. Le sélecteur ne liste QUE les presets vision ════════════════════ */
console.log("2. Filtrage supports_vision du sélecteur");
await selectFirst(1);
await settle();
eq(state.selectionIds.length, 1, "1 média sélectionné → barre visible");
eq(optionValues().join(","), "10,12", "seuls les presets vision (10, 12) sont listés");
ok(optionLabels().indexOf("Classique") === -1, "[négatif] le preset sans vision n'apparaît JAMAIS");
eq(sel().value, "10", "défaut = premier preset vision");

/* ═══ 3. Exécution : payload + progression ═══════════════════════════════ */
console.log("3. Exécution sur la sélection");
await selectFirst(2); // ids 1 et 2
await settle();
eq(state.selectionIds.length, 2, "2 médias sélectionnés");
{
  const before = autoTagCalls().length;
  autoTagDelayMs = 25; // laisse la boucle « en cours » le temps d'observer l'UI
  const p = AppGallery.bulkAutoTag();
  await settle(2);
  // Pendant l'exécution : progression visible + bouton Annuler visible.
  ok(!byId("gallery-autotag-progress").classList.contains("hidden"), "progression visible pendant l'exécution");
  ok(!byId("gallery-action-autotag-cancel").classList.contains("hidden"), "bouton Annuler visible pendant l'exécution");
  eq(byId("gallery-action-auto-tag").disabled, true, "bouton auto-tag désactivé pendant l'exécution");
  await p;
  autoTagDelayMs = 0;
  await settle();
  const newCalls = autoTagCalls().slice(before);
  eq(newCalls.length, 2, "un POST par média sélectionné");
  ok(newCalls.every((c) => c.body && c.body.preset_id === 10), "payload {preset_id} correct");
  eq(state.lastAutoTagRequest.preset_id, 10, "observable lastAutoTagRequest.preset_id");
  eq(state.lastAutoTagRequest.ids.join(","), "1,2", "observable lastAutoTagRequest.ids");
  eq(byId("gallery-action-auto-tag").disabled, false, "bouton réactivé après exécution");
  ok(byId("gallery-autotag-progress").textContent.indexOf("/2") !== -1, "progression finale n/total");
}

/* ═══ 4. Récap final (toast) ═════════════════════════════════════════════ */
console.log("4. Récap final");
{
  const t = lastToast();
  ok(t && /auto-tagg/.test(t.message), "toast récap auto-tag émis");
  ok(/2 média/.test(t.message), "récap compte les succès");
  ok(/4 tag/.test(t.message), "récap additionne les tags ajoutés");
}

/* ═══ 5. Ignorés + erreurs dans le récap ═════════════════════════════════ */
console.log("5. Ignorés et erreurs");
{
  // id 5 (vidéo) → skipped ; id 2 → erreur 502.
  notImageIds = { 5: true };
  failIds = { 2: true };
  items = [
    { id: 1, filename: "a.png", kind: "image", size: 1, created_at: "2025-01-01T00:00:00Z", subfolder: "s1", status: "complete", trashed: false, thumb_available: true, favorite: false, tags: [], tags_detail: [] },
    { id: 2, filename: "b.png", kind: "image", size: 1, created_at: "2025-01-02T00:00:00Z", subfolder: "s2", status: "complete", trashed: false, thumb_available: true, favorite: false, tags: [], tags_detail: [] },
    { id: 5, filename: "v.mp4", kind: "video", size: 1, created_at: "2025-01-03T00:00:00Z", subfolder: "s3", status: "complete", trashed: false, thumb_available: true, favorite: false, tags: [], tags_detail: [] },
  ];
  await AppGallery.reload();
  await settle();
  await selectFirst(3);
  await settle();
  eq(state.selectionIds.length, 3, "3 médias sélectionnés");
  await AppGallery.bulkAutoTag();
  await settle();
  const t = lastToast();
  ok(/1 média/.test(t.message), "récap : 1 succès");
  ok(/1 ignor/.test(t.message), "récap : 1 ignoré (vidéo)");
  ok(/1 erreur/.test(t.message), "récap : 1 erreur (LLM)");
  failIds = {}; notImageIds = {};
}

/* ═══ 6. ANNULATION entre deux médias ════════════════════════════════════ */
console.log("6. Annulation");
{
  await selectFirst(2);
  await settle();
  const before = autoTagCalls().length;
  autoTagDelayMs = 20;
  const p = AppGallery.bulkAutoTag();
  // Laisse la boucle démarrer (première requête en vol), PUIS annule : la boucle
  // s'arrête avant de traiter le média suivant.
  await settle(3);
  AppGallery.cancelAutoTag();
  await p;
  autoTagDelayMs = 0;
  await settle();
  const count = autoTagCalls().length - before;
  ok(count <= 1, "annulation : au plus 1 média traité (got " + count + ")");
  ok(/annul/.test(lastToast().message), "récap signale l'annulation");
}

/* ═══ 7. Aucun preset vision → message actionnable, aucun appel ══════════ */
console.log("7. Aucun preset vision");
{
  presets = [
    { id: 21, name: "Classique", model: "llama3", base_url: "https://api.example.com", is_global: false, is_client_side: false, supports_vision: false },
  ];
  await selectFirst(1);
  await settle();
  const before = autoTagCalls().length;
  await AppGallery.bulkAutoTag();
  await settle();
  // Le clic recharge les presets : le sélecteur ne propose plus rien de valide.
  eq(optionValues().join(","), "", "[négatif] aucun preset vision → option vide");
  eq(optionLabels()[0], "Aucun preset vision", "libellé « Aucun preset vision »");
  eq(autoTagCalls().length, before, "[négatif] aucun POST auto-tag sans preset vision");
  const t = lastToast();
  ok(t && /compatible vision/.test(t.message), "message actionnable (Paramètres)");
  // Restaure.
  presets = [
    { id: 10, name: "Vision A", model: "gpt-4o", base_url: "https://api.example.com", is_global: false, is_client_side: false, supports_vision: true },
    { id: 11, name: "Classique", model: "llama3", base_url: "https://api.example.com", is_global: false, is_client_side: false, supports_vision: false },
    { id: 12, name: "Vision B", model: "qwen-vl", base_url: "https://api.example.com", is_global: false, is_client_side: false, supports_vision: true },
  ];
}

/* ═══ 8. Non-régression : favoris + tags manuels ════════════════════════ */
console.log("8. Non-régression");
{
  await AppGallery.reload();
  await settle();
  const item = findItem(1);
  const prev = item.favorite;
  await AppGallery.toggleFavorite(item);
  await settle();
  eq(findItem(1).favorite, !prev, "bascule favori opérationnelle");
  const before = autoTagCalls().length;
  await AppGallery.applyItemTags(findItem(1), ["manuel"], []);
  await settle();
  ok(findItem(1).tags.indexOf("manuel") !== -1, "ajout de tag manuel opérationnel");
  eq(autoTagCalls().length, before, "le tag manuel n'a pas déclenché d'auto-tag");
}

console.log("\n✅ test_gallery_autotag : " + n + " assertions PASSENT");
