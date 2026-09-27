// Test headless (jsdom) — Onglet « Galerie » du front AI-Helper.
// Usage : node frontend/js/test_gallery.mjs   (via ./js/run_js_tests.sh)
//
// Prouve le CONTRAT D'ADAPTATION de app-gallery.js sur les briques holaf
// VENDUES (vendor/holaf/), SANS jamais en modifier une :
//   1. briques exposées en globals + constantes du slider alignées sur ComfyUI ;
//   2. mapping taille d'affichage -> taille de vignette serveur {128,256,512} ;
//   3. démarrage : grille virtualisée + 1re page /api/media (mock) + compteur ;
//   4. vignettes : URL .../thumbnail?size=… (stratégie 'url', aucun revoke) ;
//   5. slider : change la taille de grille ET la taille demandée au serveur ;
//   6. sélection → panneau d'informations (prompt + workflow copiables) ;
//   7. double-clic → visionneuse (img/vidéo/audio) + navigation ‹/› ;
//   8. états vide / erreur ;
//   9. preuve : les fichiers vendor/holaf/*.js sont BYTE-IDENTIQUES à holaf-lib.
import assert from "node:assert";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { loadJsdomOrSkip } from "./test_helpers/jsdom_loader.mjs";

const JSDOM = await loadJsdomOrSkip("test_gallery");
const HERE = dirname(fileURLToPath(import.meta.url));

/* ── DOM de l'onglet Galerie (structure minimale attendue par l'adaptateur) ── */
const dom = new JSDOM(`<!doctype html><html><body>
  <div id="tab-gallery">
    <input type="range" id="gallery-thumb-size" min="80" max="300" step="10" value="150">
    <span id="gallery-thumb-size-value"></span>
    <span id="gallery-thumb-resolution"></span>
    <span id="gallery-selected" class="hidden"></span>
    <span id="gallery-count"></span>
    <div id="gallery-grid"></div>
    <div id="gallery-loading"></div>
    <div id="gallery-empty" class="hidden"></div>
    <div id="gallery-error" class="hidden"></div>
    <aside id="gallery-info"></aside>
  </div>
</body></html>`, { pretendToBeVisual: true, url: "http://localhost/" });

const { window } = dom;
globalThis.window = window;
globalThis.document = window.document;
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
globalThis.MouseEvent = window.MouseEvent;
globalThis.KeyboardEvent = window.KeyboardEvent;
globalThis.localStorage = window.localStorage;

// Globals fournis par app-core.js en production (ici substitués/stubbés).
globalThis.API = "/api";
globalThis.LOCAL_MODE = false;
globalThis.$ = (id) => window.document.getElementById(id);
globalThis.safeJson = async (res) => { try { return await res.json(); } catch { return { error: "Erreur serveur " + res.status }; } };

/* ── Géométrie du conteneur de grille (jsdom renvoie 0 par défaut) ────────── */
const gridEl = window.document.getElementById("gallery-grid");
let cw = 600, ch = 400, st = 0;
Object.defineProperty(gridEl, "clientWidth", { configurable: true, get: () => cw });
Object.defineProperty(gridEl, "clientHeight", { configurable: true, get: () => ch });
Object.defineProperty(gridEl, "scrollTop", { configurable: true, get: () => st, set: (v) => { st = Math.max(0, v); } });
gridEl.getBoundingClientRect = () => ({ left: 0, top: 0, right: cw, bottom: ch, width: cw, height: ch, x: 0, y: 0, toJSON() {} });

/* ── Jeu de données mocké + fetch simulé (cookie = même-origine implicite) ── */
function makeItem(id) {
  const kind = (id === 3) ? "audio" : (id === 6) ? "video" : "image";
  return {
    id,
    filename: "f" + id + (kind === "audio" ? ".mp3" : kind === "video" ? ".mp4" : ".png"),
    kind,
    size: 1024 * id,
    created_at: "2025-01-02T03:" + String(id % 60).padStart(2, "0") + ":00Z",
    subfolder: "sub",
    thumb_available: kind !== "audio",
    url: "/api/media/" + id + "/download",
    thumb: "/api/media/" + id + "/thumbnail",
  };
}
let mediaItems = [];
for (let i = 1; i <= 120; i++) mediaItems.push(makeItem(i));
let mediaTotal = 120;
let failNext = false;
const fetchCalls = [];

function makeRes(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}
function metadataFor(id) {
  const it = makeItem(id);
  return {
    id, filename: it.filename, subfolder: "sub", kind: it.kind, ext: "", size: it.size,
    created_at: it.created_at, width: 512, height: 512, ratio: 1,
    duration: it.kind === "audio" ? 12.5 : null, duration_ms: it.kind === "audio" ? 12500 : null,
    codec: it.kind === "video" ? "h264" : null,
    prompt: "a happy cat", workflow: '{"prompt":{"1":{"class_type":"KSampler"}}}',
    has_prompt: true, has_workflow: true,
  };
}
globalThis.fetch = async (url, opts) => {
  url = String(url);
  fetchCalls.push(url);
  if (failNext) { failNext = false; return makeRes(500, { error: "boom" }); }
  const m = url.match(/\/api\/media\/(\d+)\/metadata/);
  if (m) return makeRes(200, metadataFor(parseInt(m[1], 10)));
  if (url.indexOf("/api/media?") !== -1) {
    const u = new URL(url, "http://localhost");
    const page = parseInt(u.searchParams.get("page"), 10) || 1;
    const limit = parseInt(u.searchParams.get("limit"), 10) || 60;
    const start = (page - 1) * limit;
    return makeRes(200, { items: mediaItems.slice(start, start + limit), total: mediaTotal, page, limit });
  }
  return makeRes(404, { error: "not found" });
};

/* ── Chargement des briques (modules) + de l'adaptateur (script classique) ── */
for (const b of ["collection", "thumbcache", "virtual-grid", "viewport", "lightbox", "infopane"]) {
  await import("../vendor/holaf/holaf-" + b + ".js");
}
await import("./app-gallery.js");

const AppGallery = window.AppGallery;

/* ── Compteur d'assertions ────────────────────────────────────────────────── */
let n = 0;
function ok(cond, msg) { if (!cond) throw new Error("ASSERT FAIL: " + msg); n++; }
function eq(a, b, msg) { ok(a === b, msg + " (got " + JSON.stringify(a) + ", want " + JSON.stringify(b) + ")"); }
const settle = async (times = 8) => { for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0)); };

const surface = () => window.document.querySelector(".holaf-grid-surface");
const cellEl = (i) => window.document.querySelector('.holaf-grid-surface [data-holaf-index="' + i + '"]');
const click = (el, mods = {}) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, shiftKey: !!mods.shift, ctrlKey: !!mods.ctrl }));
const dbl = (el) => el.dispatchEvent(new window.MouseEvent("dblclick", { bubbles: true }));

/* ═══ 1. Adaptateur + constantes alignées sur ComfyUI ═════════════════════ */
console.log("1. Adaptateur & convention de taille (ComfyUI)");
ok(AppGallery && typeof AppGallery.start === "function", "window.AppGallery.start exposé");
const C = AppGallery.constants;
eq(C.DISPLAY_MIN, 80, "display min = 80 (image_viewer_ui.js)");
eq(C.DISPLAY_MAX, 300, "display max = 300");
eq(C.DISPLAY_STEP, 10, "display step = 10");
eq(C.DISPLAY_DEFAULT, 150, "display défaut = 150");
eq(C.THUMB_SIZES.join(","), "128,256,512", "tailles serveur backend (THUMB_SIZES)");

console.log("2. Mapping taille d'affichage -> taille serveur");
eq(AppGallery.serverThumbSize(128), 128, "128 -> 128");
eq(AppGallery.serverThumbSize(80), 128, "80 -> 128");
eq(AppGallery.serverThumbSize(129), 256, "129 -> 256");
eq(AppGallery.serverThumbSize(150), 256, "150 -> 256");
eq(AppGallery.serverThumbSize(256), 256, "256 -> 256");
eq(AppGallery.serverThumbSize(257), 512, "257 -> 512");
eq(AppGallery.serverThumbSize(300), 512, "300 -> 512");
eq(AppGallery.serverThumbSize(9999), 512, "borne haute -> 512");
ok(AppGallery.thumbUrl({ id: 7 }, 256) === "/api/media/7/thumbnail?size=256", "URL vignette");
ok(AppGallery.downloadUrl({ id: 7 }) === "/api/media/7/download", "URL download");

/* ═══ 3. Démarrage : grille + 1re page ════════════════════════════════════ */
console.log("3. Démarrage & première page");
AppGallery.start();
await settle();
ok(!!window.document.querySelector(".holaf-grid-root"), "racine de grille créée");
eq(AppGallery.state.serverSize, 256, "taille serveur initiale = 256 (défaut 150)");
eq(AppGallery.state.collection.total, 120, "total collection = 120");
await settle();
const cells = surface().children.length;
ok(cells > 0 && cells < 120, "rendu virtualisé borné (" + cells + " cellules)");
eq(window.document.getElementById("gallery-count").textContent, "120 médias", "compteur de médias");
ok(window.document.getElementById("gallery-loading").classList.contains("hidden"), "loader masqué après 1re page");
ok(fetchCalls.some((u) => u.indexOf("/api/media?page=1&limit=60") !== -1), "1re page demandée (page=1&limit=60)");

/* ═══ 4. Vignettes (stratégie URL) ════════════════════════════════════════ */
console.log("4. Vignettes");
const img0 = cellEl(0).querySelector(".gallery-cell-img");
eq(img0.getAttribute("src"), "/api/media/1/thumbnail?size=256", "src vignette = size 256 (défaut)");
eq(cellEl(0).querySelector(".gallery-cell-badge").textContent, "🖼️", "badge type image");
const audioCell = cellEl(2);
ok(!!audioCell, "cellule audio (index 2) rendue");
eq(audioCell.querySelector(".gallery-cell-ph").textContent, "🎵", "audio : icône (pas de vignette)");
ok(audioCell.querySelector(".gallery-cell-img").classList.contains("gallery-cell-img--hidden"), "audio : <img> masquée");
ok(!audioCell.querySelector(".gallery-cell-ph").classList.contains("gallery-cell-ph--error"),
  "audio : icône de type, PAS un état d'erreur");

// Échec de chargement d'une vignette → état d'erreur DISTINCT du placeholder.
const failImg = cellEl(0).querySelector(".gallery-cell-img");
const failPh = cellEl(0).querySelector(".gallery-cell-ph");
ok(!failPh.classList.contains("gallery-cell-ph--error"), "placeholder d'attente : aucun marqueur d'erreur");
failImg.dispatchEvent(new window.Event("error"));
ok(failPh.classList.contains("gallery-cell-ph--error"), "échec vignette → classe d'erreur distincte");
eq(failPh.textContent, "⚠", "échec vignette → glyphe ⚠");
eq(failPh.title, "Vignette indisponible", "échec vignette → title explicite");

// Reprise BORNÉE après échec : un <img> en erreur ne se recharge pas seul →
// l'adaptateur retente UNE fois (cache-buster) puis renonce (pas de boucle).
{
  AppGallery.state.grid.render(true);
  await settle();
  const rc = cellEl(1);
  const rim = rc.querySelector(".gallery-cell-img");
  const rph = rc.querySelector(".gallery-cell-ph");
  const base = rim.getAttribute("src");
  ok(!!base && base.indexOf("_retry=") === -1, "URL de vignette initiale sans cache-buster");
  eq(AppGallery.thumbRetryUrl(base, 1), base + "&_retry=1", "cache-buster calculé (URL avec query)");
  eq(AppGallery.thumbRetryUrl("/x", 1), "/x?_retry=1", "cache-buster calculé (URL sans query)");
  rim.dispatchEvent(new window.Event("error"));
  ok(rph.classList.contains("gallery-cell-ph--error"), "échec → état d'erreur affiché");
  await new Promise((r) => setTimeout(r, AppGallery.constants.THUMB_RETRY_MS + 200));
  eq(rim.getAttribute("src"), base + "&_retry=1", "1re erreur → rechargement UNE fois (cache-buster)");
  // 2e échec : borne atteinte, AUCUN nouveau retry.
  rim.dispatchEvent(new window.Event("error"));
  await new Promise((r) => setTimeout(r, AppGallery.constants.THUMB_RETRY_MS + 200));
  eq(rim.getAttribute("src"), base + "&_retry=1", "[négatif] 2e échec → aucun nouveau retry (borné)");
  AppGallery.state.grid.render(true);
  await settle();
}

// Chaque cellule image/vidéo pointe l'URL de SON item avec la taille courante
// (id 3 = audio : pas de src ; id 6 = vidéo : vignette).
for (const idx of [0, 1, 3, 4, 5]) {
  eq(cellEl(idx).querySelector(".gallery-cell-img").getAttribute("src"),
    "/api/media/" + (idx + 1) + "/thumbnail?size=256", "URL vignette de l'item id " + (idx + 1));
}

// Contrôle NÉGATIF du retry borné : un chargement RÉUSSI ne doit déclencher
// AUCUNE reprise (pas de cache-buster parasite, pas d'état d'erreur affiché).
{
  const sc = cellEl(3);
  const sim = sc.querySelector(".gallery-cell-img");
  const sph = sc.querySelector(".gallery-cell-ph");
  const sbase = sim.getAttribute("src");
  eq(sbase, "/api/media/4/thumbnail?size=256", "URL par item avant succès (id 4)");
  sim.dispatchEvent(new window.Event("load"));
  ok(!sim.classList.contains("gallery-cell-img--hidden"), "succès → <img> visible");
  ok(sph.classList.contains("is-hidden"), "succès → placeholder masqué");
  ok(!sph.classList.contains("gallery-cell-ph--error"), "succès → aucun état d'erreur");
  await new Promise((r) => setTimeout(r, AppGallery.constants.THUMB_RETRY_MS + 200));
  eq(sim.getAttribute("src"), sbase, "[négatif] succès → aucun retry déclenché");
  ok(!sph.classList.contains("gallery-cell-ph--error"), "[négatif] succès → toujours pas d'erreur après attente");
}

// Échec TRANSITOIRE puis succès au retry : l'état d'erreur se résorbe.
{
  const tc = cellEl(4);
  const tim = tc.querySelector(".gallery-cell-img");
  const tph = tc.querySelector(".gallery-cell-ph");
  const tbase = tim.getAttribute("src");
  tim.dispatchEvent(new window.Event("error"));
  ok(tph.classList.contains("gallery-cell-ph--error"), "échec transitoire → état d'erreur affiché");
  await new Promise((r) => setTimeout(r, AppGallery.constants.THUMB_RETRY_MS + 200));
  eq(tim.getAttribute("src"), tbase + "&_retry=1", "retry déclenché une fois (cache-buster)");
  tim.dispatchEvent(new window.Event("load"));
  ok(!tim.classList.contains("gallery-cell-img--hidden"), "succès au retry → <img> visible");
  ok(tph.classList.contains("is-hidden"), "succès au retry → placeholder masqué (erreur résorbée)");
}

// Dégradation backend (ex. Pillow absent) : thumb_available=false sur une image
// → état d'erreur distinct (jamais un placeholder neutre silencieux).
mediaItems[0].thumb_available = false;
AppGallery.state.grid.render(true);
await settle();
ok(cellEl(0).querySelector(".gallery-cell-ph").classList.contains("gallery-cell-ph--error"),
  "image sans vignette dispo → état d'erreur distinct");
mediaItems[0].thumb_available = true;
AppGallery.state.grid.render(true);
await settle();

/* ═══ 5. Slider : grille + taille serveur ═════════════════════════════════ */
console.log("5. Slider de taille");
AppGallery.setDisplaySize(100);
eq(AppGallery.state.displaySize, 100, "displaySize = 100");
eq(AppGallery.state.serverSize, 128, "serverSize = 128");
eq(window.document.getElementById("gallery-thumb-size-value").textContent, "100 px", "libellé taille");
eq(window.document.getElementById("gallery-thumb-resolution").textContent, "vignettes 128 px", "badge résolution");
await settle();
eq(cellEl(0).querySelector(".gallery-cell-img").getAttribute("src"), "/api/media/1/thumbnail?size=128", "vignette re-demandée en 128");
eq(window.document.getElementById("gallery-thumb-size").value, "100", "slider synchronisé à 100");

AppGallery.setDisplaySize(300);
eq(AppGallery.state.serverSize, 512, "300 -> serverSize 512");
await settle();
eq(cellEl(0).querySelector(".gallery-cell-img").getAttribute("src"), "/api/media/1/thumbnail?size=512", "vignette re-demandée en 512");
AppGallery.setDisplaySize(1000);
eq(AppGallery.state.displaySize, 300, "clamp haut à 300");
AppGallery.setDisplaySize(0);
eq(AppGallery.state.displaySize, 80, "clamp bas à 80");
AppGallery.setDisplaySize(150);
await settle();

/* ═══ 6. Sélection → panneau d'informations ══════════════════════════════ */
console.log("6. Sélection & panneau d'informations");
click(cellEl(0));
await settle();
eq(AppGallery.state.selectionIds.length, 1, "1 média sélectionné");
eq(window.document.getElementById("gallery-selected").textContent, "1 sélectionné", "libellé sélection");
await settle();
const paneRoot = window.document.querySelector("#gallery-info .holaf-infopane");
ok(!!paneRoot, "panneau d'infos monté");
const textareas = paneRoot.querySelectorAll("textarea.holaf-infopane-text");
ok(textareas.length >= 2, "blocs prompt + workflow présents (" + textareas.length + ")");
const blockTexts = Array.prototype.map.call(textareas, (t) => t.value);
ok(blockTexts.indexOf("a happy cat") !== -1, "prompt copiable affiché");
ok(blockTexts.some((v) => v.indexOf('"class_type": "KSampler"') !== -1), "workflow JSON pretty affiché");
eq(paneRoot.querySelectorAll(".holaf-infopane-copy-button").length, 2, "2 boutons copier (prompt + workflow)");
ok(!paneRoot.textContent.match(/Load workflow|Charger le workflow/i), "AUCUN bouton « Load workflow »");
ok(fetchCalls.some((u) => u.indexOf("/api/media/1/metadata") !== -1), "metadata demandée pour le média 1");
const fieldValues = Array.prototype.map.call(paneRoot.querySelectorAll(".holaf-infopane-field-value"), (e) => e.textContent).join(" | ");
ok(fieldValues.indexOf("512 × 512 px") !== -1, "résolution 512 × 512 px");
ok(fieldValues.indexOf("Image") !== -1, "type Image");

/* ═══ 7. Double-clic → visionneuse ═══════════════════════════════════════ */
console.log("7. Visionneuse (lightbox)");
dbl(cellEl(1));
await settle(12);
ok(AppGallery.state.lightbox.isOpen(), "visionneuse ouverte au double-clic");
eq(AppGallery.state.lightbox.current().id, 2, "média courant = 2");
const overlay = window.document.querySelector(".holaf-lightbox-overlay");
ok(!!overlay && overlay.style.display !== "none", "overlay plein écran visible");
const bigImg = overlay.querySelector("img.gallery-lightbox-img");
ok(!!bigImg, "média <img> rendu par renderMedia");
eq(bigImg.getAttribute("src"), "/api/media/2/download", "URL download dans la visionneuse");
await AppGallery.state.lightbox.navigate(1);
await settle(4);
eq(AppGallery.state.lightbox.current().id, 3, "navigation › = média suivant (3)");
await AppGallery.state.lightbox.navigate(-1);
await settle(4);
eq(AppGallery.state.lightbox.current().id, 2, "navigation ‹ = média précédent (2)");

AppGallery.state.lightbox.close();
await settle(2);
await AppGallery.state.lightbox.openZoom(mediaItems[5]); // id 6 (vidéo)
await settle(6);
const vid = window.document.querySelector("video.gallery-lightbox-video");
ok(!!vid, "média <video> rendu");
eq(vid.controls, true, "<video controls>");
eq(vid.getAttribute("src"), "/api/media/6/download", "src vidéo = download");
AppGallery.state.lightbox.close();
await settle(2);
await AppGallery.state.lightbox.openZoom(mediaItems[2]); // id 3 (audio)
await settle(6);
const aud = window.document.querySelector("audio.gallery-lightbox-audio");
ok(!!aud, "média <audio> rendu");
eq(aud.controls, true, "<audio controls>");
AppGallery.state.lightbox.close();
await settle(2);

/* ═══ 8. États vide / erreur ═════════════════════════════════════════════ */
console.log("8. États vide & erreur");
mediaItems = []; mediaTotal = 0;
AppGallery.refresh();
await settle(12);
ok(!window.document.getElementById("gallery-empty").classList.contains("hidden"), "état VIDE affiché (total 0)");
ok(window.document.getElementById("gallery-loading").classList.contains("hidden"), "loader masqué en état vide");

mediaItems = [];
for (let i = 1; i <= 120; i++) mediaItems.push(makeItem(i));
mediaTotal = 120;
failNext = true;
AppGallery.refresh();
await settle(12);
ok(!window.document.getElementById("gallery-error").classList.contains("hidden"), "état ERREUR affiché (HTTP 500)");
ok(window.document.getElementById("gallery-error").textContent.indexOf("Erreur") !== -1, "message d'erreur présent");

/* ═══ 8bis. Auto-rafraîchissement (poll léger) ════════════════════════════ */
console.log("8bis. Auto-rafraîchissement (poll)");
ok(typeof AppGallery.pollNow === "function", "AppGallery.pollNow exposé");
eq(AppGallery.constants.POLL_MS, 15000, "intervalle de poll = 15 s");
// Retour à un état sain (la section 8 a laissé la liste vide/erreur).
mediaItems = [];
for (let i = 1; i <= 120; i++) mediaItems.push(makeItem(i));
mediaTotal = 120;
AppGallery.refresh();
await settle(12);
eq(AppGallery.state.collection.total, 120, "état sain rétabli avant poll");
const fetchesBeforePoll = fetchCalls.length;
// Un média arrive de l'extérieur (node ComfyUI) : tête + total changent.
mediaItems.unshift(makeItem(999));
mediaTotal = 121;
AppGallery.pollNow();
await settle(15);
eq(AppGallery.state.collection.total, 121, "poll a détecté le nouveau média et rechargé");
ok(fetchCalls.length > fetchesBeforePoll, "poll a bien interrogé le serveur");
// Contrôle négatif : sans changement, le poll ne relance PAS la liste.
const fetchesBeforeNoop = fetchCalls.length;
AppGallery.pollNow();
await settle(15);
eq(AppGallery.state.collection.total, 121, "[négatif] aucun changement → total stable");
ok(fetchCalls.length === fetchesBeforeNoop + 1, "[négatif] poll = 1 seule requête de tête (pas de reload)");
AppGallery.pollStop();

/* ═══ 9. Preuve : AUCUNE brique modifiée ════════════════════════════════ */
console.log("9. Briques non modifiées (vendor == holaf-lib)");
const LIB = "/projects/holaf-lib/js/";
const pairs = [
  ["holaf-collection.js", "holaf-collection.js"],
  ["holaf-thumbcache.js", "holaf-thumbcache.js"],
  ["holaf-virtual-grid.js", "holaf-virtual-grid.js"],
  ["holaf-viewport.js", "holaf-viewport.js"],
  ["holaf-lightbox.js", "holaf-lightbox.js"],
  ["holaf-infopane.js", "holaf-infopane.js"],
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

console.log("\n✅ test_gallery : " + n + " assertions PASSENT");
