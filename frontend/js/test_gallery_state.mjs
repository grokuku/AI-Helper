// Test headless (jsdom) — ÉTAT DE TRAVAIL de la galerie WEB (front AI-Helper)
// ET miniatures ANIMÉES des vidéos (aperçu au survol).
// Usage : node frontend/js/test_gallery_state.mjs   (via ./js/run_js_tests.sh)
//
// Verrouille la demande « garder l'état de la galerie quand on la ferme » (A)
// côté galerie WEB :
//   - ce qui est DÉJÀ persisté (taille d'affichage, mode Remplir/Entière) n'est
//     pas redoublé ; on ajoute filtres + tri + vue + dossiers/tags/favori +
//     position de défilement ;
//   - clé localStorage unique, valeurs invalides/obsolètes → défauts (aucun
//     throw) ;
//   - la PREMIÈRE requête après un rechargement repart avec le bon contexte.
// Et (B) le comportement retenu des miniatures animées : statique par défaut,
// VIDÉO muette en boucle UNIQUEMENT au survol, UNE seule à la fois, arrêt au
// mouseleave / arrêt global (onglet masqué).
//
// Contrôles négatifs par mutation (l'assertion échoue si on retire la logique) :
//   M1 retirer la normalisation → les valeurs invalides fuient ;
//   M2 ne pas appliquer la position mémorisée au load → scroll perdu ;
//   M3 retirer la borne de simultanéité → 2 vidéos présentes au lieu d'1.
import assert from "node:assert";
import { loadJsdomOrSkip } from "./test_helpers/jsdom_loader.mjs";

const JSDOM = await loadJsdomOrSkip("test_gallery_state");

const dom = new JSDOM(`<!doctype html><html><body>
  <div id="tab-gallery">
    <div class="flex items-center gap-2 ml-1">
      <input type="range" id="gallery-thumb-size" min="80" max="300" step="10" value="150">
      <span id="gallery-thumb-size-value"></span>
      <span id="gallery-thumb-resolution"></span>
      <div id="gallery-fit-toggle">
        <button type="button" id="gallery-fit-cover" class="gallery-view-btn is-active" aria-pressed="true">⛶ Remplir</button>
        <button type="button" id="gallery-fit-contain" class="gallery-view-btn" aria-pressed="false">▣ Entière</button>
      </div>
    </div>
    <div role="tablist">
      <button type="button" id="gallery-view-normal" role="tab" aria-selected="true">Médias</button>
      <button type="button" id="gallery-view-trash" role="tab" aria-selected="false">Corbeille</button>
    </div>
    <select id="gallery-filter-kind"><option value="">Tous</option><option value="image">Images</option><option value="video">Vidéos</option><option value="audio">Audio</option></select>
    <button type="button" id="gallery-filter-folders">Dossiers : tous</button>
    <button type="button" id="gallery-filter-tags">Tags : tous</button>
    <button type="button" id="gallery-filter-favorite" aria-pressed="false">★ Favoris</button>
    <input id="gallery-search" type="search">
    <input id="gallery-filter-from" type="date">
    <input id="gallery-filter-to" type="date">
    <select id="gallery-filter-sort">
      <option value="created_at_desc">Récents</option><option value="created_at_asc">Anciens</option>
      <option value="name_asc">Nom (A→Z)</option><option value="size_desc">Taille</option>
    </select>
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

/* ── Jeu de données mocké + fetch simulé ─────────────────────────────────── */
function makeItem(id) {
  const kind = (id === 3) ? "audio" : (id === 6 || id === 9) ? "video" : "image";
  return {
    id, filename: "f" + id + (kind === "audio" ? ".mp3" : kind === "video" ? ".mp4" : ".png"),
    kind, size: 1024 * id, created_at: "2025-01-02T03:00:00Z", subfolder: "sub",
    thumb_available: kind !== "audio", favorite: id === 5,
    url: "/api/media/" + id + "/download", thumb: "/api/media/" + id + "/thumbnail",
  };
}
let mediaItems = [];
for (let i = 1; i <= 40; i++) mediaItems.push(makeItem(i));
const fetchCalls = [];
function makeRes(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, text: async () => JSON.stringify(body) };
}
globalThis.fetch = async (url) => {
  url = String(url);
  fetchCalls.push(url);
  if (url.indexOf("/api/media?") !== -1) {
    const u = new URL(url, "http://localhost");
    const page = parseInt(u.searchParams.get("page"), 10) || 1;
    const limit = parseInt(u.searchParams.get("limit"), 10) || 60;
    const start = (page - 1) * limit;
    return makeRes(200, { items: mediaItems.slice(start, start + limit), total: mediaItems.length, page, limit });
  }
  if (/\/api\/media\/(\d+)\/metadata/.test(url)) return makeRes(200, { id: 1, filename: "f1.png", kind: "image" });
  if (url.indexOf("/api/media/folders") !== -1) return makeRes(200, { folders: [{ subfolder: "sub", count: 10 }] });
  if (url.indexOf("/api/media/tags") !== -1) return makeRes(200, { tags: [{ tag: "ciel", count: 3 }] });
  return makeRes(404, { error: "not found" });
};

/* ── Chargement des briques (modules) + de l'adaptateur ───────────────────── */
for (const b of ["collection", "thumbcache", "virtual-grid", "viewport", "lightbox", "infopane"]) {
  await import("../vendor/holaf/holaf-" + b + ".js");
}
await import("./app-gallery.js");
const AppGallery = window.AppGallery;

let n = 0;
function ok(cond, msg) { if (!cond) throw new Error("ASSERT FAIL: " + msg); n++; }
function eq(a, b, msg) { ok(a === b, msg + " (got " + JSON.stringify(a) + ", want " + JSON.stringify(b) + ")"); }
const settle = async (times = 10) => { for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0)); };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const lastMediaUrl = () => fetchCalls.filter((u) => u.indexOf("/api/media?") !== -1).pop() || "";
const cellByMedia = (id) => window.document.querySelector('.holaf-grid-surface [data-media-id="' + id + '"]');
const fireInput = (id, value) => {
  const el = window.document.getElementById(id);
  el.value = value;
  el.dispatchEvent(new window.Event("change", { bubbles: true }));
};

/* ═══ 1. Normalisation (PUR) ══════════════════════════════════════════════ */
console.log("1. normalizePersistedState (valeurs invalides → défauts)");
const N = AppGallery.normalizePersistedState;
assert.deepStrictEqual(N({}).view, "normal", "vue par défaut");
assert.deepStrictEqual(N({ view: "hacker" }).view, "normal", "vue invalide → normal");
eq(N({ sort: "boom" }).sort, "created_at_desc", "tri invalide → défaut (M1)");
eq(N({ kind: "exe" }).kind, "", "type invalide → '' (M1)");
eq(N({ from: "12/31/2025" }).from, "", "date invalide → '' (M1)");
eq(N({ from: "2025-05-01" }).from, "2025-05-01", "date valide conservée");
eq(N({ scrollTop: -3 }).scrollTop, 0, "scroll négatif → 0");
eq(N({ scrollTop: 512.7 }).scrollTop, 512, "scroll rogné");
eq(N({ favoriteOnly: "yes" }).favoriteOnly, false, "favori non booléen → false");
assert.deepStrictEqual(N({ selectedFolders: ["a", "a", 3, "", "b"] }).selectedFolders, ["a", "b"],
  "liste nettoyée (dédup/non-chaînes/vides)");
assert.deepStrictEqual(N(null).selectedTags, [], "entrée null tolérée");
ok(N({ selectedFolders: new Array(500).fill(0).map((_, i) => "d" + i) }).selectedFolders.length
  <= AppGallery.constants.STATE_LIST_MAX, "liste bornée (anti-payload)");
ok(AppGallery.constants.STATE_KEY === "gallery-state", "clé localStorage documentée");
ok(AppGallery.constants.VIDEO_PREVIEW_MAX === 1, "une seule vidéo animée à la fois");

/* ═══ 2. Démarrage SANS état persisté ════════════════════════════════════ */
console.log("2. Démarrage sans état → défauts, aucune fuite");
eq(AppGallery.readPersistedState(), null, "rien en localStorage au départ");
AppGallery.start();
await settle();
ok(AppGallery.state.collection.total === 40, "première page chargée");
eq(AppGallery.state.view, "normal", "vue normale par défaut");
ok(lastMediaUrl().indexOf("subfolders=") === -1 && lastMediaUrl().indexOf("tags=") === -1,
  "aucun filtre dans la première requête");

/* ═══ 3. Persistance après réglage des filtres/vue ═══════════════════════ */
console.log("3. Réglage → écriture localStorage");
fireInput("gallery-filter-kind", "image");
fireInput("gallery-search", "chat");
fireInput("gallery-filter-from", "2025-05-01");
fireInput("gallery-filter-to", "2025-06-30");
fireInput("gallery-filter-sort", "name_asc");
AppGallery.applyFolders(["sub"]);
AppGallery.applyTags(["ciel"]);
AppGallery.toggleFavoriteFilter();
AppGallery.setView("trash");
await settle();
AppGallery.persistState();

const saved = AppGallery.readPersistedState();
ok(saved, "état persisté relu");
eq(saved.kind, "image", "type persisté");
eq(saved.q, "chat", "recherche persistée");
eq(saved.from, "2025-05-01", "borne basse persistée");
eq(saved.to, "2025-06-30", "borne haute persistée");
eq(saved.sort, "name_asc", "tri persisté");
assert.deepStrictEqual(saved.selectedFolders, ["sub"], "dossiers persistés");
assert.deepStrictEqual(saved.selectedTags, ["ciel"], "tags persistés");
eq(saved.favoriteOnly, true, "favori persisté");
eq(saved.view, "trash", "vue corbeille persistée");
ok(localStorage.getItem("gallery-state"), "clé 'gallery-state' écrite");

/* ═══ 4. Simulation d'un RECHARGEMENT de page ════════════════════════════ */
console.log("4. Rechargement → restauration DOM + state, 1re requête contextuelle");
// On remet tout « à neuf » : champs vides, state par défaut (comme au boot).
for (const id of ["gallery-filter-kind", "gallery-search", "gallery-filter-from", "gallery-filter-to"]) {
  window.document.getElementById(id).value = "";
}
window.document.getElementById("gallery-filter-sort").value = "created_at_desc";
Object.assign(AppGallery.state, {
  view: "normal", sort: "created_at_desc", selectedFolders: [], selectedTags: [],
  favoriteOnly: false, pendingScrollTop: 0, restoreScrollPending: false,
});
eq(AppGallery.readPersistedState().q, "chat", "la valeur persiste indépendamment du state live");

const applied = AppGallery.restoreState();
ok(applied, "restoreState applique l'état mémorisé");
eq(window.document.getElementById("gallery-filter-kind").value, "image", "champ Type restauré");
eq(window.document.getElementById("gallery-search").value, "chat", "champ recherche restauré");
eq(window.document.getElementById("gallery-filter-from").value, "2025-05-01", "champ date début restauré");
eq(window.document.getElementById("gallery-filter-sort").value, "name_asc", "champ tri restauré");
eq(AppGallery.state.view, "trash", "vue restaurée");
assert.deepStrictEqual(AppGallery.state.selectedFolders, ["sub"], "dossiers restaurés");
assert.deepStrictEqual(AppGallery.state.selectedTags, ["ciel"], "tags restaurés");
eq(AppGallery.state.favoriteOnly, true, "favori restauré");
ok(window.document.getElementById("gallery-view-trash").getAttribute("aria-selected") === "true",
  "bascule de vue reflétée");
ok(window.document.getElementById("gallery-filter-favorite").getAttribute("aria-pressed") === "true",
  "bouton favori reflété");

// La première requête après relance part avec le contexte restauré.
await AppGallery.reload();
await settle();
const q = lastMediaUrl();
ok(q.indexOf("kind=image") !== -1, "requête: kind restauré");
ok(q.indexOf("q=chat") !== -1, "requête: recherche restaurée");
ok(q.indexOf("subfolders=sub") !== -1, "requête: dossier restauré");
ok(q.indexOf("tags=ciel") !== -1, "requête: tag restauré");
ok(q.indexOf("favorite=1") !== -1, "requête: favori restauré");
ok(q.indexOf("status=trashed") !== -1, "requête: vue corbeille restaurée");

/* ═══ 5. Position de défilement ══════════════════════════════════════════ */
console.log("5. Position de défilement restaurée au chargement");
localStorage.setItem("gallery-state", JSON.stringify({
  view: "normal", sort: "created_at_desc", kind: "", q: "", from: "", to: "",
  selectedFolders: [], selectedTags: [], favoriteOnly: false, scrollTop: 320,
}));
AppGallery.restoreState();
eq(AppGallery.state.restoreScrollPending, true, "drapeau de reprise armé");
await AppGallery.reload();
await settle();
eq(gridEl.scrollTop, 320, "scrollTop réappliqué après le load (M2)");
eq(AppGallery.state.restoreScrollPending, false, "drapeau consommé une seule fois");

/* ═══ 6. Miniatures ANIMÉES des vidéos (survol) ══════════════════════════ */
console.log("6. Aperçu vidéo au survol : statique par défaut, 1 seule, arrêt au leave");
AppGallery.state.grid.render(true);
await settle();
const v6 = cellByMedia("6");
const v9 = cellByMedia("9");
ok(v6 && v9, "cellules vidéo (id 6 et 9) rendues");
eq(v6.querySelectorAll("video").length, 0, "aucune vidéo AVANT survol (statique par défaut)");

v6.dispatchEvent(new window.MouseEvent("mouseenter"));
await wait(AppGallery.constants.VIDEO_PREVIEW_DELAY + 120);
const vid6 = v6.querySelector("video.gallery-cell-video");
ok(!!vid6, "survol → <video> superposé créé");
ok(vid6.src.indexOf("/api/media/6/download") !== -1, "src = route download (Range)");
eq(vid6.muted, true, "muet");
eq(vid6.loop, true, "en boucle");
eq(vid6.autoplay, true, "lecture automatique");
eq(AppGallery.videoPreviewCount(), 1, "une seule vidéo animée");

// Borne de simultanéité : survoler une 2e vidéo n'en ajoute pas une 2e.
v9.dispatchEvent(new window.MouseEvent("mouseenter"));
await wait(AppGallery.constants.VIDEO_PREVIEW_DELAY + 120);
eq(AppGallery.videoPreviewCount(), 1, "M3 : jamais plus d'une vidéo animée à la fois");
eq(v9.querySelectorAll("video").length, 0, "la 2e cellule n'a pas démarré (budget pris)");

// mouseleave → arrêt + retrait immédiats.
v6.dispatchEvent(new window.MouseEvent("mouseleave"));
await wait(30);
eq(v6.querySelectorAll("video").length, 0, "mouseleave → <video> retiré");
eq(AppGallery.videoPreviewCount(), 0, "plus aucune vidéo animée");
eq(v6.querySelectorAll("video.gallery-cell-video").length, 0, "aucun <video> résiduel");
eq(vid6.getAttribute("src"), null, "src libérée (arrêt du décodage/téléchargement)");

// Arrêt GLOBAL (équivalent « onglet masqué ») : plus aucune vidéo.
v9.dispatchEvent(new window.MouseEvent("mouseenter"));
await wait(AppGallery.constants.VIDEO_PREVIEW_DELAY + 120);
eq(AppGallery.videoPreviewCount(), 1, "2e survol → aperçu démarré (budget libéré)");
AppGallery.pauseAllVideoPreviews();
eq(AppGallery.videoPreviewCount(), 0, "pauseAllVideoPreviews() arrête tout");
eq(v9.querySelectorAll("video").length, 0, "aucun <video> résiduel après arrêt global");

// Contrôle négatif : une IMAGE ne déclenche AUCUN aperçu vidéo.
const img1 = cellByMedia("1");
img1.dispatchEvent(new window.MouseEvent("mouseenter"));
await wait(AppGallery.constants.VIDEO_PREVIEW_DELAY + 120);
eq(img1.querySelectorAll("video").length, 0, "[négatif] image → aucun aperçu vidéo");

console.log(`\n${n} vérifications — PASS`);
