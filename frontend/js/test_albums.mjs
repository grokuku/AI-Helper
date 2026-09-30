// Test headless (jsdom) — Onglet « Galerie » : ALBUMS PUBLICS (phase 3).
// Usage : node frontend/js/test_albums.mjs   (via ./js/run_js_tests.sh)
//
// Suite frère de test_gallery_manage.mjs (gestion) : elle prouve l'intégration
// du front PRIVÉ avec l'API albums (backend/routes/albums.py), SANS modifier
// aucune brique holaf :
//   1. câblage statique : script classique chargé APRÈS app-gallery.js, bouton
//      « Créer un album » dans la barre d'actions, bouton « Albums » en-tête,
//      purge branchée sur l'avertissement albums ;
//   2. bouton « Créer un album » : présent, actif dès ≥1 sélection, MASQUÉ en
//      vue corbeille (contrôle négatif), non-régression des autres boutons ;
//   3. création : payload EXACT { ids, title, description } (titre/description
//      optionnels), modale de résultat, rapport ajoutés/ignorés avec raisons ;
//      contrôle négatif : sans ids → aucun POST ;
//   4. modale de résultat : URL publique affichée/copiable + « Ouvrir » quand
//      public_url est présente ; CLÉ + message d'aide + « Ouvrir » désactivé
//      quand public_url est null (contrôle négatif : aucun window.open) ;
//   5. POLLING de préparation : ticks building → ready, PROGRESSION affichée,
//      arrêt du suivi (timer annulé, aucune requête après l'arrêt), erreurs
//      réseau bornées, dépassement du nombre de ticks, fermeture de modale,
//      boucle automatique setTimeout ;
//   6. modale de gestion : liste (titre/compteur/date/statut) + chaque action
//      (Charger → sélection grille avec items hors vue, Ajouter la sélection,
//      Renommer → PATCH, Révoquer/Supprimer → confirmations, Copier l'URL) ;
//   7. avertissement AVANT purge : GET /api/albums/for-media → message
//      « figure dans N album(s) public(s) », contrôle négatif sans album,
//      purge groupée, câblage de l'action Purger d'une cellule ;
//   8. non-régression : verrou busy de la barre d'actions, purge directe
//      (sans avertissement) toujours synchrone, briques vendor intactes.
import assert from "node:assert";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { loadJsdomOrSkip } from "./test_helpers/jsdom_loader.mjs";

const JSDOM = await loadJsdomOrSkip("test_albums");
const HERE = dirname(fileURLToPath(import.meta.url));

/* ── DOM de l'onglet Galerie (toolbar + barre d'actions + en-tête) ───────── */
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
    <button type="button" id="gallery-open-albums">🖼 Albums</button>
    <select id="gallery-filter-kind"><option value="">Tous</option><option value="image">Images</option><option value="video">Vidéos</option><option value="audio">Audio</option></select>
    <button type="button" id="gallery-filter-folders" aria-haspopup="dialog">Dossiers : tous</button>
    <button type="button" id="gallery-filter-tags" aria-haspopup="dialog">Tags : tous</button>
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
        <div id="gallery-actions" class="hidden">
          <span id="gallery-actions-label"></span>
          <button id="gallery-action-download" data-gallery-action>⤓ Télécharger</button>
          <button id="gallery-action-create-album" data-gallery-action class="hidden">🖼 Créer un album</button>
          <button id="gallery-action-favorite" data-gallery-action>★ Favori</button>
          <button id="gallery-action-tags" data-gallery-action>🏷 Tag</button>
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
// Pas de showConfirm global → galleryConfirm retombe sur window.confirm (piloté ici).
let confirmAnswer = false;
let lastConfirm = "";
window.confirm = (msg) => { lastConfirm = String(msg); return confirmAnswer; };
// window.open espionné (bouton « Ouvrir »).
let openedUrls = [];
window.open = (url) => { openedUrls.push(url); return null; };
// Presse-papiers simulé (l'API Clipboard n'existe pas dans jsdom).
let clipboardWrites = [];
let clipboardBroken = false;
Object.defineProperty(window.navigator, "clipboard", {
  configurable: true,
  value: {
    writeText: (t) => {
      if (clipboardBroken) return Promise.reject(new Error("refusé"));
      clipboardWrites.push(t);
      return Promise.resolve();
    },
  },
});

/* ── Géométrie du conteneur de grille ────────────────────────────────────── */
const gridEl = window.document.getElementById("gallery-grid");
let cw = 600, ch = 400, st = 0;
Object.defineProperty(gridEl, "clientWidth", { configurable: true, get: () => cw });
Object.defineProperty(gridEl, "clientHeight", { configurable: true, get: () => ch });
Object.defineProperty(gridEl, "scrollTop", { configurable: true, get: () => st, set: (v) => { st = Math.max(0, v); } });
gridEl.getBoundingClientRect = () => ({ left: 0, top: 0, right: cw, bottom: ch, width: cw, height: ch, x: 0, y: 0, toJSON() {} });

/* ── Jeu de données média + fetch simulé ─────────────────────────────────── */
function ts(id) { return new Date(Date.UTC(2025, 0, 1) + id * 1000).toISOString(); }
function makeMedia(id) {
  const trashed = (id === 2 || id === 4);
  return {
    id,
    filename: "media_" + id + (id === 5 ? ".mp4" : ".png"),
    kind: (id === 5) ? "video" : "image",
    size: 1000 * id,
    created_at: ts(id),
    subfolder: "shot" + (id % 3),
    status: trashed ? "trashed" : "active",
    trashed,
    trashed_at: trashed ? "2025-02-01T00:00:00Z" : null,
    thumb_available: true, has_prompt: true, has_workflow: false, favorite: false,
  };
}
let mediaItems = [];
for (let i = 1; i <= 12; i++) mediaItems.push(makeMedia(i));

/* ── Albums simulés (stub mutable) ───────────────────────────────────────── */
let albums = [];
let albumSeq = 100;
let publicUrlEnabled = true;
function albumItem(mediaId, status) {
  return {
    id: mediaId * 10, media_id: mediaId, item_no: mediaId,
    ext: ".jpg", width: 100, height: 100,
    status: status || "pending", error: "", added_at: "2025-09-29T12:00:00Z",
  };
}
function countsOf(a) {
  const c = { total: a.items.length, ok: 0, failed: 0, pending: 0 };
  a.items.forEach((it) => {
    if (it.status === "ok") c.ok++;
    else if (it.status === "failed") c.failed++;
    else if (it.status === "pending") c.pending++;
  });
  return c;
}
function makeAlbum(over) {
  albumSeq += 1;
  const id = (over && over.id) ? over.id : albumSeq;
  const items = (over && over.items) || [];
  const al = {
    id,
    key: "KEY" + id,
    public_url: publicUrlEnabled ? ("https://albums.test/a/KEY" + id) : null,
    title: "", description: "",
    status: "building",
    progress: { total: items.length, done: 0 },
    created_at: "2025-09-29T12:00:00Z",
    updated_at: "2025-09-29T12:00:00Z",
    revoked_at: "",
    items,
  };
  Object.assign(al, over || {});
  return al;
}
function serializeAlbum(a, detail) {
  const out = {
    id: a.id, key: a.key, public_url: a.public_url,
    title: a.title, description: a.description,
    status: a.status,
    progress: { total: a.progress.total, done: a.progress.done },
    created_at: a.created_at, updated_at: a.updated_at, revoked_at: a.revoked_at || "",
    counts: countsOf(a),
  };
  if (detail) out.items = a.items.map((it) => Object.assign({}, it));
  return out;
}
function findAlbum(id) { return albums.find((a) => a.id === id) || null; }

/* Fetch : journal + routage. Les réponses « deferred » simulent un appel long. */
const calls = [];
let deferredItemsResp = null; // { albumId, d } → POST /items suspendu
let albumDetailFailureFor = null; // id → GET détail rejeté (erreur réseau)
let albumListFailure = false; // true → GET /api/albums (liste) rejeté (erreur réseau)
function makeRes(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, text: async () => JSON.stringify(body) };
}
globalThis.fetch = (url, opts = {}) => {
  const method = (opts.method || "GET").toUpperCase();
  const rawUrl = String(url);
  const body = opts.body ? JSON.parse(opts.body) : null;
  calls.push({ method, url: rawUrl, body });

  let m;
  // 1. Avertissement avant purge.
  m = rawUrl.match(/\/api\/albums\/for-media\/(\d+)$/);
  if (m) {
    const mid = parseInt(m[1], 10);
    const owners = albums.filter((a) => a.status !== "revoked" && a.items.some((it) => it.media_id === mid));
    return Promise.resolve(makeRes(200, { count: owners.length, albums: owners.map((a) => serializeAlbum(a)) }));
  }
  // 2. Ajout d'items (POST) — peut être suspendu pour tester le verrou busy.
  m = rawUrl.match(/\/api\/albums\/(\d+)\/items$/);
  if (m && method === "POST") {
    const al = findAlbum(parseInt(m[1], 10));
    if (!al) return Promise.resolve(makeRes(404, { error: "Album introuvable" }));
    if (deferredItemsResp && deferredItemsResp.albumId === al.id) {
      return deferredItemsResp.d.promise;
    }
    const existing = new Set(al.items.map((it) => it.media_id));
    const skipped = [];
    let added = 0;
    (body.ids || []).forEach((id) => {
      if (existing.has(id)) { skipped.push({ id: id, reason: "duplicate" }); return; }
      existing.add(id);
      al.items.push(albumItem(id, "pending"));
      added++;
    });
    al.progress.total = al.items.length;
    al.status = "building";
    return Promise.resolve(makeRes(200, { album: serializeAlbum(al, true), added: added, skipped: skipped }));
  }
  // 3. Révocation.
  m = rawUrl.match(/\/api\/albums\/(\d+)\/revoke$/);
  if (m && method === "POST") {
    const al = findAlbum(parseInt(m[1], 10));
    if (!al) return Promise.resolve(makeRes(404, { error: "Album introuvable" }));
    al.status = "revoked";
    al.revoked_at = "2025-09-29T13:00:00Z";
    return Promise.resolve(makeRes(200, serializeAlbum(al)));
  }
  // 4. Détail / PATCH / DELETE d'un album.
  m = rawUrl.match(/\/api\/albums\/(\d+)$/);
  if (m) {
    const id = parseInt(m[1], 10);
    const al = findAlbum(id);
    if (!al) return Promise.resolve(makeRes(404, { error: "Album introuvable" }));
    if (method === "GET") {
      if (albumDetailFailureFor === id) return Promise.reject(new Error("réseau indisponible"));
      return Promise.resolve(makeRes(200, serializeAlbum(al, true)));
    }
    if (method === "PATCH") {
      if (typeof body.title === "string") al.title = body.title;
      if (typeof body.description === "string") al.description = body.description;
      return Promise.resolve(makeRes(200, serializeAlbum(al)));
    }
    if (method === "DELETE") {
      albums = albums.filter((a) => a.id !== id);
      return Promise.resolve(makeRes(200, { deleted: true, id: id }));
    }
  }
  // 5. Liste + création.
  if (/\/api\/albums$/.test(rawUrl)) {
    if (method === "POST") {
      const ids = Array.isArray(body.ids) ? body.ids : [];
      const okIds = [];
      const skipped = [];
      ids.forEach((id) => {
        const it = mediaItems.find((x) => x.id === id);
        if (!it) skipped.push({ id: id, reason: "not_found" });
        else if (it.kind !== "image") skipped.push({ id: id, reason: "unsupported_kind" });
        else okIds.push(id);
      });
      const al = makeAlbum({
        title: body.title || "", description: body.description || "",
        status: "building",
        items: okIds.map((id) => albumItem(id, "pending")),
      });
      al.progress = { total: okIds.length, done: 0 };
      albums.unshift(al);
      return Promise.resolve(makeRes(201, { album: serializeAlbum(al), skipped: skipped }));
    }
    if (albumListFailure) return Promise.reject(new Error("réseau indisponible"));
    return Promise.resolve(makeRes(200, { items: albums.map((a) => serializeAlbum(a)), total: albums.length }));
  }
  // 6. Médias : liste paginée + métadonnées.
  if (rawUrl.indexOf("/api/media?") !== -1) {
    const u = new URL(rawUrl, "http://localhost");
    const page = parseInt(u.searchParams.get("page"), 10) || 1;
    const limit = parseInt(u.searchParams.get("limit"), 10) || 60;
    const status = u.searchParams.get("status") || "";
    const kind = u.searchParams.get("kind") || "";
    let list = mediaItems.slice();
    if (status === "trashed") list = list.filter((i) => i.trashed);
    else if (status && status !== "all") list = list.filter((i) => !i.trashed && i.status === status);
    else if (!status) list = list.filter((i) => !i.trashed);
    if (kind) list = list.filter((i) => i.kind === kind);
    const start = (page - 1) * limit;
    return Promise.resolve(makeRes(200, { items: list.slice(start, start + limit), total: list.length, page, limit }));
  }
  m = rawUrl.match(/\/api\/media\/(\d+)\/metadata$/);
  if (m) {
    const it = mediaItems.find((x) => x.id === parseInt(m[1], 10)) || makeMedia(parseInt(m[1], 10));
    return Promise.resolve(makeRes(200, Object.assign({}, it, { ext: "", width: 512, height: 512, ratio: 1 })));
  }
  m = rawUrl.match(/\/api\/media\/(\d+)\/purge$/);
  if (m && method === "DELETE") {
    mediaItems = mediaItems.filter((x) => x.id !== parseInt(m[1], 10));
    return Promise.resolve(makeRes(200, { purged: 1 }));
  }
  if (/\/api\/media\/purge$/.test(rawUrl) && method === "POST") {
    const ids = (body && body.ids) || [];
    mediaItems = mediaItems.filter((x) => ids.indexOf(x.id) === -1);
    return Promise.resolve(makeRes(200, { purged: ids.length, skipped: [] }));
  }
  if (rawUrl.indexOf("/api/presets") !== -1) return Promise.resolve(makeRes(200, []));
  if (rawUrl.indexOf("/api/media/folders") !== -1) return Promise.resolve(makeRes(200, { folders: [], total: 0 }));
  return Promise.resolve(makeRes(404, { error: "not found" }));
};

/* ── Chargement des briques + adaptateurs (ordre de index.html) ──────────── */
for (const b of ["collection", "thumbcache", "virtual-grid", "viewport", "lightbox", "infopane", "toast", "modal", "qrcode"]) {
  await import("../vendor/holaf/holaf-" + b + ".js");
}
if (window.HolafToast && window.HolafToast.configure) {
  window.HolafToast.configure({ duration: 0, position: "bottom-right" });
}
// Espion du rendu QR : app-albums lit window.HolafQrcode à CHAQUE appel
// (câblage tardif) — on enveloppe la fonction pour compter les appels réels.
let qrRenderCalls = [];
{
  const realRender = window.HolafQrcode.render;
  window.HolafQrcode.render = (opts) => { qrRenderCalls.push(opts); return realRender(opts); };
}
await import("./app-gallery.js");
await import("./app-albums.js");

const AppGallery = window.AppGallery;
const AppAlbums = window.AppAlbums;
const state = AppGallery.state;

/* ── Helpers d'assertion ─────────────────────────────────────────────────── */
let n = 0;
function ok(cond, msg) { if (!cond) throw new Error("ASSERT FAIL: " + msg); n++; }
function safeJsonish(v) { try { return JSON.stringify(v); } catch (e) { return String(v); } }
function eq(a, b, msg) { ok(a === b, msg + " (got " + safeJsonish(a) + ", want " + safeJsonish(b) + ")"); }
const settle = async (times = 20) => { for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0)); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const byId = (id) => window.document.getElementById(id);
const actionBtn = (id) => byId(id);
const modalRoot = (id) => byId(id);
function modalBtn(rootId, text) {
  const root = modalRoot(rootId);
  if (!root) return null;
  const btns = Array.prototype.slice.call(root.querySelectorAll(".holaf-modal-btn"));
  return btns.find((b) => b.textContent.trim() === text) || null;
}
function albumRowBtn(albumId, action) {
  const root = modalRoot("gallery-album-manage-modal");
  if (!root) return null;
  return root.querySelector('[data-album-action="' + action + '"][data-album-id="' + albumId + '"]');
}
const listCalls = () => calls.filter((c) => c.method === "GET" && /\/api\/albums$/.test(c.url) && c.url.indexOf("for-media") === -1);
const detailCalls = (albumId) => calls.filter((c) => c.method === "GET" && c.url.endsWith("/api/albums/" + albumId));
const forMediaCalls = (mediaId) => calls.filter((c) => c.method === "GET" && c.url.endsWith("/api/albums/for-media/" + mediaId));
function anyToast(re) {
  return Array.prototype.some.call(window.document.querySelectorAll(".holaf-toast"), (el) => re.test(el.textContent));
}
function defer() { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; }

/* Démarrage de l'onglet : brique grille + collection chargées. */
AppGallery.start();
await settle();
ok(!!state.grid, "grille initialisée au démarrage de l'onglet");
ok(state.collection && state.collection.total > 0, "collection chargée");

/* ═══ 1. Câblage statique (index.html + scripts classiques + CSS) ═════════ */
console.log("1. Câblage statique");
{
  const js = readFileSync(resolve(HERE, "app-albums.js"), "utf8");
  ok(/^function galleryAlbumsOpenCreate\(/m.test(js),
    "galleryAlbumsOpenCreate déclaré au niveau du fichier (global des scripts classiques)");
  ok(/^function galleryAlbumsOpenManage\(/m.test(js), "galleryAlbumsOpenManage global");
  ok(/^function galleryBulkPurgeWarned\(/m.test(js), "galleryBulkPurgeWarned global (purge avec avertissement)");
  ok(/^function galleryPurgeItemWarned\(/m.test(js), "galleryPurgeItemWarned global (purge unitaire avec avertissement)");
  ok(typeof window.AppAlbums === "object" && typeof AppAlbums.openCreate === "function",
    "window.AppAlbums exposé (tests + câblage tardif)");
  ok(typeof AppAlbums.openQr === "function" && typeof AppAlbums.renderResultQr === "function",
    "window.AppAlbums.openQr / renderResultQr exposés (QR phase 4)");

  const html = readFileSync(resolve(HERE, "..", "index.html"), "utf8");
  const albumsTag = (html.match(/<script[^>]*src="\/js\/app-albums\.js"[^>]*>/) || [""])[0];
  ok(albumsTag.length > 0, "app-albums.js chargé par index.html");
  ok(!/type="module"/.test(albumsTag), "[négatif] script classique (pas de type=module)");
  ok(html.indexOf("/js/app-albums.js") > html.indexOf("/js/app-gallery.js"),
    "app-albums.js chargé APRÈS app-gallery.js (accès tardif à window.AppGallery)");
  ok(/id="gallery-action-create-album"[^>]*onclick="galleryAlbumsOpenCreate\(\)"/.test(html),
    "bouton « Créer un album » branché dans la barre d'actions");
  ok(/id="gallery-open-albums"[^>]*onclick="galleryAlbumsOpenManage\(\)"/.test(html),
    "bouton « Albums » branché dans l'en-tête de la galerie");
  ok(/id="gallery-action-purge"[^>]*onclick="galleryBulkPurgeWarned\(\)"/.test(html),
    "purge groupée branchée sur l'avertissement albums");
  ok(html.indexOf("/vendor/holaf/holaf-qrcode.js") !== -1,
    "brique HolafQrcode (QR) chargée par index.html");
  ok(html.indexOf("/vendor/holaf/holaf-qrcode.js") < html.indexOf("/js/app-gallery.js"),
    "brique QR chargée AVANT l'adaptateur galerie (global prêt à l'interaction)");
  ok(!/type="module"[^>]*src="\/js\/app-albums\.js"/.test(html) &&
    /HolafQrcode|albumsQrcode/.test(js),
    "app-albums.js consulte window.HolafQrcode (brique vendue, jamais modifiée)");

  const css = readFileSync(resolve(HERE, "..", "css", "app.css"), "utf8");
  ok(css.indexOf(".album-create") !== -1 && css.indexOf(".album-result") !== -1 && css.indexOf(".album-manage") !== -1,
    "styles des modales albums présents dans app.css");
  ok(css.indexOf(".album-result-qr") !== -1 && css.indexOf(".album-qr-box") !== -1 && css.indexOf(".album-qr-hint") !== -1,
    "styles QR (zone résultat + modale QR) présents dans app.css");

  // Purge de la cellule : galleryOnAction route vers AppAlbums (et non l'ancien
  // chemin direct) — vérifié au comportement en section 7.
  const galleryJs = readFileSync(resolve(HERE, "app-gallery.js"), "utf8");
  ok(/window\.AppAlbums/.test(galleryJs) && /purgeItemWarned/.test(galleryJs),
    "galleryOnAction('purge') délègue à AppAlbums.purgeItemWarned");
}

/* ═══ 2. Bouton « Créer un album » dans la barre d'actions ════════════════ */
console.log("2. Bouton « Créer un album » (barre d'actions)");
{
  const bar = byId("gallery-actions");
  const btn = actionBtn("gallery-action-create-album");
  ok(!!btn, "bouton présent dans le DOM");
  eq(btn.getAttribute("data-gallery-action") !== null, true, "bouton verrouillable par gallerySetBusy");

  state.grid.selection.clear();
  await settle();
  ok(bar.classList.contains("hidden"), "0 sélection → barre d'actions masquée");

  state.grid.selection.set([1]);
  await settle();
  ok(!bar.classList.contains("hidden"), "1 sélection → barre d'actions visible");
  ok(!btn.classList.contains("hidden"), "vue Médias → bouton créer VISIBLE");
  eq(btn.disabled, false, "bouton créer ACTIF dès ≥1 sélection");

  // Vue corbeille : sélection maintenue, bouton créer masqué.
  AppGallery.setView("trash");
  await settle();
  state.grid.selection.set([2]);
  await settle();
  ok(!bar.classList.contains("hidden"), "corbeille + sélection → barre visible");
  ok(btn.classList.contains("hidden"), "vue Corbeille → bouton créer MASQUÉ (contrôle négatif)");
  ok(!actionBtn("gallery-action-restore").classList.contains("hidden"), "[non-régression] Restaurer visible en corbeille");
  ok(!actionBtn("gallery-action-purge").classList.contains("hidden"), "[non-régression] Purger visible en corbeille");
  ok(actionBtn("gallery-action-download").classList.contains("hidden") === false, "[non-régression] Télécharger visible");

  AppGallery.setView("normal");
  await settle();
  state.grid.selection.set([1]);
  await settle();
  ok(!btn.classList.contains("hidden"), "retour vue Médias → bouton créer re-visible");
  ok(!actionBtn("gallery-action-delete").classList.contains("hidden"), "[non-régression] Supprimer visible en vue Médias");
}

/* ═══ 3. Création : payload exact + modale de résultat ════════════════════ */
console.log("3. Création (payload + résultat)");
{
  state.grid.selection.set([1, 2, 5]);
  await settle();
  const ctrl = AppAlbums.openCreate();
  ok(!!ctrl, "modale de création ouverte");
  ok(!!modalRoot("gallery-album-create-modal"), "racine #gallery-album-create-modal (brique holaf-modal)");
  const hint = byId("gallery-album-create-count");
  ok(hint && /3 média/.test(hint.textContent), "compteur de sélection affiché (3 médias)");

  byId("gallery-album-title").value = "Mes vacances";
  byId("gallery-album-description").value = "Plage et soleil";
  const btn = modalBtn("gallery-album-create-modal", "Créer");
  ok(!!btn, "bouton Créer présent");
  btn.click();
  await settle();

  const post = calls.find((c) => c.method === "POST" && /\/api\/albums$/.test(c.url));
  ok(!!post, "POST /api/albums envoyé");
  eq(JSON.stringify(post.body.ids), JSON.stringify([1, 2, 5]), "payload ids EXACTS");
  eq(post.body.title, "Mes vacances", "payload titre");
  eq(post.body.description, "Plage et soleil", "payload description");
  eq(state.lastRequest.method, "POST", "le dernier appel galerie est la création");
  ok(state.lastRequest.url.endsWith("/api/albums"), "URL de création /api/albums");
  ok(!modalRoot("gallery-album-create-modal"), "modale de création refermée après Créer");

  ok(!!modalRoot("gallery-album-result-modal"), "modale de résultat ouverte");
  ok(/\d+ média/.test(byId("gallery-album-result-count").textContent), "rapport : compteur ajoutés/en préparation");
  const skipped = byId("gallery-album-result-skipped");
  ok(!!skipped, "rapport : liste des ignorés présente");
  ok(/média #5/.test(skipped.textContent), "le média vidéo #5 est listé comme ignoré");
  ok(/type non supporté/.test(skipped.textContent), "raison lisible du skip (vidéo/audio)");
  const created = AppAlbums.state.lastCreated;
  ok(created && created.title === "Mes vacances", "dernier album créé mémorisé");
  eq(created.public_url, "https://albums.test/a/" + created.key, "public_url construit côté serveur");

  // QR de l'URL publique (phase 4) : SVG généré par la brique vendue.
  const qrBox = byId("gallery-album-result-qr");
  ok(!!qrBox, "zone QR présente dans la modale de résultat");
  const qrSvg = qrBox ? qrBox.querySelector("svg.holaf-qrcode") : null;
  ok(!!qrSvg, "QR rendu en SVG (brique HolafQrcode) quand public_url est présente");
  ok(!!qrSvg && qrSvg.getAttribute("data-holaf-qr-version"), "le SVG porte data-holaf-qr-version (diagnostic)");
  ok(qrRenderCalls.some((o) => o.text === created.public_url),
    "HolafQrcode.render appelé avec l'URL publique de l'album (spy)");
  ok(qrRenderCalls.some((o) => o.text === created.public_url && typeof o.size === "number" && o.size > 0),
    "le QR est demandé avec une taille raisonnable");
}

/* Contrôle négatif : sans ids, AUCUN POST n'est émis. */
{
  state.grid.selection.clear();
  await settle();
  const before = calls.filter((c) => c.method === "POST" && /\/api\/albums$/.test(c.url)).length;
  const r = AppAlbums.openCreate();
  eq(r, null, "[négatif] pas de sélection → pas de modale");
  eq(calls.filter((c) => c.method === "POST" && /\/api\/albums$/.test(c.url)).length, before,
    "[négatif] payload sans ids → AUCUN POST /api/albums");
  const r2 = AppAlbums.create([], "Vide", "");
  eq(r2, false, "[négatif] albumsCreateSubmit([], …) refusé");
  eq(calls.filter((c) => c.method === "POST" && /\/api\/albums$/.test(c.url)).length, before,
    "[négatif] toujours aucun POST");
}

/* ═══ 4. Modale de résultat : URL présente, puis clé + aide si null ═══════ */
console.log("4. Résultat : URL publique vs clé + aide");
{
  // (a) public_url présente : input + Copier + Ouvrir.
  const album = AppAlbums.state.lastCreated;
  const urlInput = byId("gallery-album-result-url");
  ok(!!urlInput, "input URL présent quand public_url est fournie");
  eq(urlInput.value, album.public_url, "URL affichée");
  eq(urlInput.readOnly, true, "URL en lecture seule");
  const openBtn = byId("gallery-album-result-open");
  eq(openBtn.disabled, false, "bouton Ouvrir actif");
  clipboardWrites = [];
  byId("gallery-album-result-copy").click();
  await settle();
  eq(clipboardWrites.length, 1, "Copier → presse-papiers appelé");
  eq(clipboardWrites[0], album.public_url, "URL copiée");
  eq(AppAlbums.state.lastCopied, album.public_url, "state.lastCopied mémorisé");
  openedUrls = [];
  openBtn.click();
  eq(openedUrls.length, 1, "Ouvrir → window.open appelé");
  eq(openedUrls[0], album.public_url, "même URL ouverte");
  eq(AppAlbums.state.lastOpened, album.public_url, "state.lastOpened mémorisé");
  // Ferme la modale de résultat (fin du suivi).
  AppAlbums.state.resultModal.close();
  await settle();
}

{
  // (b) public_url null : clé affichée + message d'aide, Ouvrir DÉSACTIVÉ.
  publicUrlEnabled = false;
  state.grid.selection.set([1, 2]);
  await settle();
  AppAlbums.openCreate();
  byId("gallery-album-title").value = "Sans URL";
  modalBtn("gallery-album-create-modal", "Créer").click();
  await settle();

  const created = AppAlbums.state.lastCreated;
  eq(created.public_url, null, "public_url null sans AIH_ALBUM_PUBLIC_BASE_URL");
  ok(!byId("gallery-album-result-url"), "[négatif] pas d'input URL");
  const help = byId("gallery-album-result-help");
  ok(!!help && help.textContent.indexOf("AIH_ALBUM_PUBLIC_BASE_URL") !== -1,
    "message d'aide « définis AIH_ALBUM_PUBLIC_BASE_URL »");
  // QR : message d'aide À LA PLACE du SVG, aucun appel de la brique.
  const qrCallsBefore = qrRenderCalls.length;
  const qrBoxNull = byId("gallery-album-result-qr");
  ok(!!qrBoxNull, "zone QR présente même sans URL publique");
  ok(qrBoxNull && !qrBoxNull.querySelector("svg"), "[négatif] AUCUN SVG QR quand public_url est null");
  const qrHelp = byId("gallery-album-result-qr-help");
  ok(!!qrHelp && qrHelp.textContent.indexOf("AIH_ALBUM_PUBLIC_BASE_URL") !== -1,
    "message d'aide QR (AIH_ALBUM_PUBLIC_BASE_URL) à la place du QR");
  eq(qrRenderCalls.length, qrCallsBefore, "[négatif] HolafQrcode.render NON appelé sans URL publique");
  eq(byId("gallery-album-result-key").textContent, created.key, "CLÉ de l'album affichée");
  const openBtn = byId("gallery-album-result-open");
  eq(openBtn.disabled, true, "[négatif] Ouvrir désactivé sans URL publique");
  openedUrls = [];
  openBtn.click();
  eq(openedUrls.length, 0, "[négatif] aucun window.open sans URL");
  clipboardWrites = [];
  byId("gallery-album-result-copy").click();
  await settle();
  eq(clipboardWrites[0], created.key, "Copier → la CLÉ est copiée");
  AppAlbums.state.resultModal.close();
  await settle();
  publicUrlEnabled = true;
}

{
  // (c) brique QR absente (vendor non chargé) → message d'aide, aucun crash.
  const savedQr = window.HolafQrcode;
  try {
    delete window.HolafQrcode;
    const al = makeAlbum({ id: 301, title: "Sans brique", status: "ready", items: [albumItem(1, "ok")] });
    al.progress = { total: 1, done: 1 };
    AppAlbums.openResult(serializeAlbum(al), []);
    const box = byId("gallery-album-result-qr");
    ok(box && !box.querySelector("svg"), "[négatif] brique absente → PAS de SVG QR");
    const qrHelp = byId("gallery-album-result-qr-help");
    ok(!!qrHelp && /HolafQrcode/.test(qrHelp.textContent), "message d'aide « brique HolafQrcode absente »");
    AppAlbums.state.resultModal.close();
    await settle();
    eq(AppAlbums.openQr(al), null, "[négatif] openQr sans brique → null");
    ok(anyToast(/HolafQrcode/), "toast « brique HolafQrcode absente (vendor/holaf) »");
    ok(!modalRoot("gallery-album-qr-modal"), "[négatif] aucune modale QR sans brique");
  } finally {
    window.HolafQrcode = savedQr;
  }
}

/* ═══ 5. Polling de préparation ═══════════════════════════════════════════ */
console.log("5. Polling (ticks, progression, arrêt, erreurs)");
AppAlbums.state.pollMs = 100000; // ticks MANUELS pour des assertions déterministes
{
  // (a) building → ready : progression affichée puis arrêt COMPLET du suivi.
  const al = makeAlbum({ title: "Suivi", status: "building", items: [albumItem(1, "pending"), albumItem(2, "pending")] });
  al.progress = { total: 2, done: 0 };
  albums.unshift(al);
  AppAlbums.openResult(serializeAlbum(al, true), []);
  eq(AppAlbums.state.poll.active, true, "suivi actif à l'ouverture");
  ok(!!AppAlbums.state.poll.timer, "timer de suivi armé");

  al.progress.done = 1;
  await AppAlbums.pollNow();
  ok(/1\/2/.test(byId("gallery-album-result-status").textContent), "progression 1/2 affichée");
  eq(AppAlbums.state.poll.active, true, "toujours en suivi tant que building");

  al.progress.done = 2;
  al.status = "ready";
  al.items = [albumItem(1, "ok"), albumItem(2, "ok")];
  await AppAlbums.pollNow();
  ok(/prêt/i.test(byId("gallery-album-result-status").textContent), "statut final « Album prêt. »");
  eq(AppAlbums.state.poll.active, false, "ARRÊT du suivi sur ready");
  eq(AppAlbums.state.poll.timer, null, "timer annulé sur ready");
  ok(/2 média/.test(byId("gallery-album-result-count").textContent), "rapport : 2 médias ajoutés");
  const detailBefore = detailCalls(al.id).length;
  await sleep(20);
  eq(detailCalls(al.id).length, detailBefore, "[négatif] AUCUNE requête après l'arrêt du suivi");
  AppAlbums.state.resultModal.close();
  await settle();
}
{
  // (b) erreur serveur (status error) → arrêt immédiat.
  const al = makeAlbum({ title: "KO", status: "building", items: [albumItem(7, "pending")] });
  al.progress = { total: 1, done: 0 };
  albums.unshift(al);
  AppAlbums.openResult(serializeAlbum(al, true), []);
  al.status = "error";
  await AppAlbums.pollNow();
  ok(/échoué/i.test(byId("gallery-album-result-status").textContent), "statut d'échec affiché");
  eq(AppAlbums.state.poll.active, false, "ARRÊT du suivi sur error");
  AppAlbums.state.resultModal.close();
  await settle();
}
{
  // (c) erreurs réseau bornées : on n'arrête qu'après pollErrorsMax.
  AppAlbums.state.pollErrorsMax = 2;
  const al = makeAlbum({ title: "Réseau", status: "building", items: [albumItem(8, "pending")] });
  al.progress = { total: 1, done: 0 };
  albums.unshift(al);
  albumDetailFailureFor = al.id;
  AppAlbums.openResult(serializeAlbum(al, true), []);
  await AppAlbums.pollNow();
  eq(AppAlbums.state.poll.active, true, "1re erreur réseau : le suivi survit");
  await AppAlbums.pollNow();
  eq(AppAlbums.state.poll.active, false, "2e erreur réseau : suivi arrêté (borne)");
  ok(/Suivi interrompu/.test(byId("gallery-album-result-status").textContent), "message d'interruption affiché");
  albumDetailFailureFor = null;
  AppAlbums.state.resultModal.close();
  await settle();
  AppAlbums.state.pollErrorsMax = 8;
}
{
  // (d) dépassement du nombre de ticks : arrêt avec message.
  AppAlbums.state.pollMax = 2;
  const al = makeAlbum({ title: "Lent", status: "building", items: [albumItem(9, "pending")] });
  al.progress = { total: 1, done: 0 };
  albums.unshift(al);
  AppAlbums.openResult(serializeAlbum(al, true), []);
  await AppAlbums.pollNow();
  eq(AppAlbums.state.poll.active, true, "tick 1/2 : toujours actif");
  await AppAlbums.pollNow();
  eq(AppAlbums.state.poll.active, false, "tick 2/2 : suivi arrêté (borne de temps)");
  ok(/trop de temps/.test(byId("gallery-album-result-status").textContent), "message « trop de temps »");
  AppAlbums.state.resultModal.close();
  await settle();
  AppAlbums.state.pollMax = 240;
}
{
  // (e) fermeture de la modale → arrêt immédiat du suivi.
  const al = makeAlbum({ title: "Fermé", status: "building", items: [albumItem(10, "pending")] });
  al.progress = { total: 1, done: 0 };
  albums.unshift(al);
  AppAlbums.openResult(serializeAlbum(al, true), []);
  eq(AppAlbums.state.poll.active, true, "suivi actif");
  AppAlbums.state.resultModal.close();
  await settle();
  eq(AppAlbums.state.poll.active, false, "fermeture de modale → suivi arrêté");
  eq(AppAlbums.state.poll.timer, null, "timer annulé à la fermeture");
}
{
  // (f) boucle AUTOMATIQUE (setTimeout) : plusieurs ticks puis arrêt sur ready.
  AppAlbums.state.pollMs = 5;
  const al = makeAlbum({ title: "Auto", status: "building", items: [albumItem(11, "pending")] });
  al.progress = { total: 1, done: 0 };
  albums.unshift(al);
  AppAlbums.openResult(serializeAlbum(al, true), []);
  await sleep(40);
  ok(detailCalls(al.id).length >= 2, "la boucle automatique a émis au moins 2 ticks");
  al.status = "ready";
  al.items = [albumItem(11, "ok")];
  al.progress.done = 1;
  await sleep(40);
  eq(AppAlbums.state.poll.active, false, "boucle automatique arrêtée sur ready");
  const after = detailCalls(al.id).length;
  await sleep(20);
  eq(detailCalls(al.id).length, after, "[négatif] la boucle ne repart pas après l'arrêt");
  AppAlbums.state.resultModal.close();
  await settle();
  AppAlbums.state.pollMs = 100000;
}

/* ═══ 6. Modale de gestion : liste + actions ══════════════════════════════ */
console.log("6. Modale de gestion (liste, actions, confirmations)");
// Ticks de liste MANUELS (déterministes) — le suivi automatique est testé en 9.
AppAlbums.state.managePollMs = 100000;
// Fixture : 3 albums (prêt / en préparation / révoqué).
const albumReady = makeAlbum({
  id: 101, title: "Vacances", description: "Été", status: "ready",
  items: [albumItem(1, "ok"), albumItem(3, "ok"), albumItem(999999, "ok"), albumItem(5, "failed")],
});
albumReady.items[3].error = "source illisible";
albumReady.progress = { total: 4, done: 4 };
const albumBuilding = makeAlbum({
  id: 102, title: "Chantier", description: "", status: "building",
  items: [albumItem(7, "pending")],
});
albumBuilding.progress = { total: 1, done: 0 };
const albumRevoked = makeAlbum({
  id: 103, title: "Ancien", description: "", status: "revoked",
  items: [albumItem(3, "ok")],
});
albumRevoked.progress = { total: 1, done: 1 };
albums = [albumReady, albumBuilding, albumRevoked];

{
  AppAlbums.openManage();
  ok(!!modalRoot("gallery-album-manage-modal"), "modale de gestion ouverte");
  await settle();
  const rows = window.document.querySelectorAll("#gallery-album-manage-modal .album-manage-item");
  eq(rows.length, 3, "3 albums listés");
  const titles = Array.prototype.map.call(rows, (r) => r.querySelector(".album-manage-title").textContent);
  ok(titles.indexOf("Vacances") !== -1 && titles.indexOf("Chantier") !== -1 && titles.indexOf("Ancien") !== -1, "titres affichés");
  ok(/4 média/.test(rows[0].textContent), "compteur de l'album affiché");
  ok(/prêt/i.test(rows[0].querySelector(".album-manage-badge").textContent), "statut « Prêt » affiché");
  ok(/préparation/i.test(rows[1].querySelector(".album-manage-badge").textContent), "statut « En préparation »");
  ok(/révoqué/i.test(rows[2].querySelector(".album-manage-badge").textContent), "statut « Révoqué »");

  // Actions désactivées sur un album révoqué (contrôle négatif).
  eq(albumRowBtn(103, "rename").disabled, true, "[négatif] Renommer désactivé sur album révoqué");
  eq(albumRowBtn(103, "revoke").disabled, true, "[négatif] Révoquer désactivé sur album révoqué");
  eq(albumRowBtn(103, "add").disabled, true, "[négatif] Ajouter désactivé sur album révoqué");
  eq(albumRowBtn(103, "qr").disabled, true, "[négatif] QR désactivé sur album révoqué");
  eq(albumRowBtn(101, "qr").disabled, false, "QR actif sur album prêt (URL publique)");
  eq(albumRowBtn(102, "qr").disabled, false, "QR actif sur album en préparation");
  eq(albumRowBtn(103, "load").disabled, false, "Charger reste possible sur album révoqué");
  eq(albumRowBtn(103, "delete").disabled, false, "Supprimer reste possible sur album révoqué");
}

{
  // (a) « Charger » → grid.selection.set(ids ok) + toast « hors vue ».
  state.grid.selection.set([6]);
  await settle();
  eq(state.selectionIds.length, 1, "sélection précédente posée");
  albumRowBtn(101, "load").click();
  await settle();
  eq(AppAlbums.state.lastLoaded.albumId, 101, "dernier chargement = album 101");
  eq(JSON.stringify(state.selectionIds.slice().sort()), JSON.stringify([1, 3, 999999]), "3 ids sélectionnés (items ok)");
  eq(state.selectionIds.indexOf(999999) !== -1, true, "id NON chargé inclus dans la sélection");
  eq(AppAlbums.state.lastLoaded.outOfView, 1, "1 item hors vue compté");
  ok(anyToast(/hors vue/), "toast signale « hors vue »");
  ok(!modalRoot("gallery-album-manage-modal"), "modale de gestion fermée après Charger (sélection visible)");
}

{
  // (b) « Ajouter la sélection » : POST /items { ids } + récap added/skipped.
  state.grid.selection.set([7, 8]);
  await settle();
  AppAlbums.openManage();
  await settle();
  const listsBefore = listCalls().length;
  albumRowBtn(102, "add").click();
  await settle();
  const post = calls.find((c) => c.method === "POST" && /\/api\/albums\/102\/items$/.test(c.url));
  ok(!!post, "POST /api/albums/102/items envoyé");
  eq(JSON.stringify(post.body.ids), JSON.stringify([7, 8]), "payload = ids de la sélection");
  eq(AppAlbums.state.lastAdd.added, 1, "1 ajouté");
  eq(AppAlbums.state.lastAdd.skipped.length, 1, "1 ignoré (doublon)");
  ok(anyToast(/déjà présent/), "toast récapitule la raison du skip");
  ok(listCalls().length > listsBefore, "liste de gestion rafraîchie après ajout");
}

{
  // (b2) « QR » : modale du QR de l'URL publique (brique HolafQrcode).
  AppAlbums.openManage();
  await settle();
  const qrCallsBefore = qrRenderCalls.length;
  albumRowBtn(101, "qr").click();
  await settle();
  ok(!!modalRoot("gallery-album-qr-modal"), "action QR → modale #gallery-album-qr-modal ouverte");
  const qrSvg = byId("gallery-album-qr-code");
  ok(!!qrSvg && !!qrSvg.querySelector("svg.holaf-qrcode"), "modale QR : SVG rendu par la brique");
  ok(qrRenderCalls.length > qrCallsBefore &&
    qrRenderCalls[qrRenderCalls.length - 1].text === albumReady.public_url,
    "HolafQrcode.render appelé avec l'URL publique de l'album 101 (spy)");
  eq(AppAlbums.state.lastQr.albumId, 101, "lastQr = album 101");
  eq(AppAlbums.state.lastQr.text, albumReady.public_url, "lastQr = URL publique");
  ok(!!byId("gallery-album-qr-hint"), "libellé d'aide au scan présent (FR)");
  modalBtn("gallery-album-qr-modal", "Fermer").click();
  await settle();
  ok(!modalRoot("gallery-album-qr-modal"), "modale QR fermée par Fermer");
  eq(AppAlbums.state.qrModal, null, "state.qrModal nettoyé à la fermeture");

  // Contrôle négatif : album sans URL publique → toast, AUCUNE modale/QR.
  const beforeNull = qrRenderCalls.length;
  const r = AppAlbums.openQr({ id: 999, title: "Sans URL", public_url: null, status: "ready" });
  eq(r, null, "[négatif] openQr refuse un album sans URL publique");
  ok(anyToast(/AIH_ALBUM_PUBLIC_BASE_URL/), "[négatif] toast d'aide sans URL publique");
  ok(!modalRoot("gallery-album-qr-modal"), "[négatif] aucune modale QR sans URL");
  eq(qrRenderCalls.length, beforeNull, "[négatif] HolafQrcode.render NON appelé sans URL");
  eq(AppAlbums.openQr(null), null, "[négatif] openQr(null) sans effet");

  // Contrôle négatif : album révoqué → bouton QR désactivé, clic sans effet.
  AppAlbums.openManage();
  await settle();
  albumRowBtn(103, "qr").click();
  await settle();
  ok(!modalRoot("gallery-album-qr-modal"), "[négatif] album révoqué → clic QR sans effet");
  AppAlbums.state.manageModal.close();
  await settle();
}

{
  // (c) « Renommer » : modale préremplie → PATCH titre + description.
  AppAlbums.openManage();
  await settle();
  albumRowBtn(101, "rename").click();
  ok(!!modalRoot("gallery-album-rename-modal"), "modale de renommage ouverte");
  eq(byId("gallery-album-rename-title").value, "Vacances", "titre prérempli");
  eq(byId("gallery-album-rename-desc").value, "Été", "description préremplie");
  byId("gallery-album-rename-title").value = "Vacances 2025";
  modalBtn("gallery-album-rename-modal", "Enregistrer").click();
  await settle();
  const patch = calls.find((c) => c.method === "PATCH" && c.url.endsWith("/api/albums/101"));
  ok(!!patch, "PATCH /api/albums/101 envoyé");
  eq(patch.body.title, "Vacances 2025", "PATCH title = nouveau titre");
  eq(patch.body.description, "Été", "PATCH description conservée");
  eq(AppAlbums.state.lastRename.album.title, "Vacances 2025", "réponse de renommage mémorisée");
}

{
  // (d) « Révoquer » : confirmation ; refus → rien, acceptation → POST.
  AppAlbums.openManage();
  await settle();
  const before = calls.filter((c) => c.method === "POST" && /\/revoke$/.test(c.url)).length;
  confirmAnswer = false;
  albumRowBtn(101, "revoke").click();
  await settle();
  ok(/Révoquer/.test(lastConfirm), "confirmation de révocation affichée");
  eq(calls.filter((c) => c.method === "POST" && /\/revoke$/.test(c.url)).length, before,
    "[négatif] refus → aucun appel de révocation");
  confirmAnswer = true;
  albumRowBtn(101, "revoke").click();
  await settle();
  const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/api/albums/101/revoke"));
  ok(!!post, "acceptation → POST revoke envoyé");
  eq(findAlbum(101).status, "revoked", "album révoqué côté serveur simulé");
  confirmAnswer = false;
}

{
  // (e) « Supprimer » : confirmation EXPLICITE (irréversible) → DELETE.
  AppAlbums.openManage();
  await settle();
  confirmAnswer = false;
  albumRowBtn(103, "delete").click();
  await settle();
  ok(/IRRÉVERSIBLE/.test(lastConfirm), "confirmation explicite « IRRÉVERSIBLE »");
  ok(!calls.some((c) => c.method === "DELETE" && c.url.endsWith("/api/albums/103")), "[négatif] refus → aucun DELETE");
  confirmAnswer = true;
  albumRowBtn(103, "delete").click();
  await settle();
  ok(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/api/albums/103")), "acceptation → DELETE envoyé");
  ok(!findAlbum(103), "album supprimé côté serveur simulé");
  await settle();
  const rows = window.document.querySelectorAll("#gallery-album-manage-modal .album-manage-item");
  eq(rows.length, 2, "liste rafraîchie après suppression (2 albums)");
  confirmAnswer = false;
}

{
  // (f) « Copier l'URL » depuis la liste.
  AppAlbums.openManage();
  await settle();
  clipboardWrites = [];
  albumRowBtn(102, "copy").click();
  await settle();
  eq(clipboardWrites[0], albumBuilding.public_url, "URL publique copiée depuis la liste");
  AppAlbums.state.manageModal.close();
  await settle();
}

/* ═══ 7. Avertissement avant purge définitive ═════════════════════════════ */
console.log("7. Avertissement « figure dans N album(s) public(s) » avant purge");
// Fixture : le média 1 figure dans DEUX albums actifs ; le média 8 dans aucun.
albums = [
  makeAlbum({ id: 201, title: "A", status: "ready", items: [albumItem(1, "ok")] }),
  makeAlbum({ id: 202, title: "B", status: "ready", items: [albumItem(1, "ok")] }),
];
{
  confirmAnswer = false;
  const p = AppAlbums.purgeItemWarned({ id: 1, filename: "a.png" });
  await settle();
  ok(forMediaCalls(1).length === 1, "GET /api/albums/for-media/1 appelé AVANT la confirmation");
  ok(/figure dans 2 album/.test(lastConfirm), "message : « figure dans 2 album(s) public(s) »");
  ok(/IRRÉVERSIBLE/.test(lastConfirm), "message de purge conservé");
  await p;
  eq(calls.filter((c) => c.method === "DELETE" && /\/api\/media\/1\/purge$/.test(c.url)).length, 0,
    "[négatif] refus → aucune purge");

  confirmAnswer = true;
  await AppAlbums.purgeItemWarned({ id: 1, filename: "a.png" });
  await settle();
  ok(calls.some((c) => c.method === "DELETE" && /\/api\/media\/1\/purge$/.test(c.url)),
    "acceptation → DELETE /api/media/1/purge");
  confirmAnswer = false;
}
{
  // Média dans AUCUN album : pas d'avertissement (contrôle négatif).
  lastConfirm = "";
  await AppAlbums.purgeItemWarned({ id: 8, filename: "h.png" });
  await settle();
  eq(forMediaCalls(8).length, 1, "l'API est quand même interrogée pour le média 8");
  ok(!/figure dans/.test(lastConfirm), "[négatif] aucun avertissement quand 0 album");
}
{
  // Purge GROUPÉE : agrégation multi-médias (1 média concerné sur 2).
  state.grid.selection.set([1, 8]);
  await settle();
  confirmAnswer = false;
  const p = AppAlbums.bulkPurgeWarned();
  await settle();
  ok(/figure dans/.test(lastConfirm), "avertissement groupé présent");
  ok(/1 média figure dans 2 album/.test(lastConfirm), "agrégat : 1 média concerné, 2 albums");
  await p;
  eq(calls.filter((c) => c.method === "POST" && /\/api\/media\/purge$/.test(c.url)).length, 0,
    "[négatif] refus → aucune purge groupée");
  confirmAnswer = true;
  await AppAlbums.bulkPurgeWarned();
  await settle();
  const post = calls.find((c) => c.method === "POST" && /\/api\/media\/purge$/.test(c.url));
  ok(!!post, "acceptation → POST /api/media/purge");
  eq(JSON.stringify(post.body.ids), JSON.stringify([1, 8]), "payload de purge groupée intact");
  confirmAnswer = false;
}
{
  // Câblage de l'action Purger d'une CELLULE (vue corbeille) : elle passe bien
  // par l'avertissement (GET for-media) — et non par l'ancien chemin direct.
  AppGallery.setView("trash");
  await settle();
  state.grid.selection.clear();
  await settle();
  lastConfirm = "";
  const before = forMediaCalls(2).length;
  const cell = window.document.querySelector('.holaf-grid-surface [data-holaf-index="0"]');
  const purgeBtn = cell && cell.querySelector('[data-holaf-action="purge"]');
  ok(!!purgeBtn, "action Purger présente sur la cellule corbeille");
  purgeBtn.click();
  await settle();
  ok(forMediaCalls(2).length > before, "clic Purger → GET /api/albums/for-media/2 (avertissement)");
  ok(/IRRÉVERSIBLE/.test(lastConfirm), "confirmation de purge affichée (refusée → rien)");
  AppGallery.setView("normal");
  await settle();
}

/* ═══ 8. Non-régression (busy, purge directe, briques) ════════════════════ */
console.log("8. Non-régression");
{
  // Verrou busy pendant un appel réseau : le bouton créer est désactivé.
  AppAlbums.openManage();
  await settle();
  state.grid.selection.set([6]);
  await settle();
  const d = defer();
  deferredItemsResp = { albumId: 201, d };
  const p = AppAlbums.addSelection(findAlbum(201));
  await settle();
  eq(state.busy, true, "state.busy vrai pendant l'ajout");
  eq(actionBtn("gallery-action-create-album").disabled, true, "bouton créer désactivé pendant l'appel");
  d.resolve(makeRes(200, { album: serializeAlbum(findAlbum(201), true), added: 1, skipped: [] }));
  deferredItemsResp = null;
  await p;
  await settle();
  eq(state.busy, false, "verrou relâché après l'appel");
  eq(actionBtn("gallery-action-create-album").disabled, false, "bouton créer réactivé");
  if (AppAlbums.state.manageModal) { AppAlbums.state.manageModal.close(); await settle(); }
}
{
  // Purge DIRECTE (sans passer par app-albums) : contrat historique SYNCHRONE
  // conservé — DELETE posé dans la foulée de la confirmation.
  AppGallery.setView("trash");
  await settle();
  await settle();
  const beforeForMedia = calls.filter((c) => /for-media/.test(c.url)).length;
  const item = { id: 4, filename: "d.png" };
  confirmAnswer = true;
  const p = AppGallery.purgeItem(item);
  eq(state.lastRequest.method, "DELETE", "[non-régression] purge directe toujours synchrone");
  ok(state.lastRequest.url.endsWith("/media/4/purge"), "[non-régression] DELETE /api/media/4/purge");
  await p;
  await settle();
  eq(calls.filter((c) => /for-media/.test(c.url)).length, beforeForMedia,
    "[négatif] purge directe → AUCUN appel for-media");
  AppGallery.setView("normal");
  await settle();
  confirmAnswer = false;
}
{
  // Briques vendor byte-identiques à holaf-lib (aucune brique modifiée).
  const LIB = resolve(HERE, "..", "..", "..", "holaf-lib", "js");
  if (existsSync(LIB)) {
    const pairs = [
      ["holaf-collection.js", "holaf-collection.js"],
      ["holaf-thumbcache.js", "holaf-thumbcache.js"],
      ["holaf-virtual-grid.js", "holaf-virtual-grid.js"],
      ["holaf-viewport.js", "holaf-viewport.js"],
      ["holaf-lightbox.js", "holaf-lightbox.js"],
      ["holaf-infopane.js", "holaf-infopane.js"],
      ["holaf-toast.js", "holaf-toast.js"],
      ["holaf-modal.js", "holaf-modal.js"],
      ["holaf-qrcode.js", "holaf-qrcode.js"],
    ];
    let identical = 0;
    for (const [local, lib] of pairs) {
      const a = readFileSync(resolve(HERE, "..", "vendor", "holaf", local), "utf8");
      const b = readFileSync(LIB + "/" + lib, "utf8");
      if (a === b) identical++;
    }
    eq(identical, pairs.length, "les " + pairs.length + " briques vendor sont byte-identiques à holaf-lib");
  } else {
    console.log("  (holaf-lib introuvable → preuve d'identité ignorée)");
  }
}

/* ═══ 9. Polling automatique de la modale de GESTION ══════════════════════ */
console.log("9. Gestion : rafraîchissement automatique pendant la préparation");
AppAlbums.state.managePollMs = 100000; // ticks de liste MANUELS (déterministes)
{
  // (a) building → progression « x/y » visible, puis ARRÊT automatique.
  const b = makeAlbum({ id: 401, title: "En cours", status: "building", items: [albumItem(1, "pending"), albumItem(3, "pending")] });
  b.progress = { total: 2, done: 0 };
  const r = makeAlbum({ id: 402, title: "Fini", status: "ready", items: [albumItem(2, "ok")] });
  r.progress = { total: 1, done: 1 };
  albums = [b, r];
  AppAlbums.openManage();
  await settle();
  eq(AppAlbums.state.managePoll.active, true, "suivi actif dès qu'un album est en préparation");
  ok(!!AppAlbums.state.managePoll.timer, "timer de gestion armé");
  ok(/Préparation en cours… 0\/2/.test(byId("gallery-albums-manage-status").textContent),
    "état + progression agrégée « 0/2 » affichés");
  ok(/0\/2 prêts/.test(modalRoot("gallery-album-manage-modal").textContent), "ligne de l'album : progression 0/2");

  const listsBefore = listCalls().length;
  b.progress.done = 1;
  await AppAlbums.managePollNow();
  ok(listCalls().length > listsBefore, "un tick émet GET /api/albums (rafraîchissement automatique)");
  ok(/Préparation en cours… 1\/2/.test(byId("gallery-albums-manage-status").textContent), "tick : progression « 1/2 »");
  eq(AppAlbums.state.managePoll.active, true, "toujours actif tant qu'un album prépare");

  b.progress.done = 2;
  b.status = "ready";
  b.items = [albumItem(1, "ok"), albumItem(3, "ok")];
  await AppAlbums.managePollNow();
  ok(/2 albums/.test(byId("gallery-albums-manage-status").textContent), "tick final : « 2 albums »");
  eq(AppAlbums.state.managePoll.active, false, "ARRÊT dès qu'aucun album n'est en préparation");
  eq(AppAlbums.state.managePoll.timer, null, "timer annulé à l'arrêt");
  const listAfterStop = listCalls().length;
  await sleep(25);
  eq(listCalls().length, listAfterStop, "[négatif] aucune requête de liste après l'arrêt");
  AppAlbums.state.manageModal.close();
  await settle();
}
{
  // (b) Fermeture de la modale → arrêt immédiat (aucun timer fantôme).
  const b = makeAlbum({ id: 403, title: "Ferme", status: "building", items: [albumItem(4, "pending")] });
  b.progress = { total: 1, done: 0 };
  albums = [b];
  AppAlbums.openManage();
  await settle();
  eq(AppAlbums.state.managePoll.active, true, "suivi actif à l'ouverture");
  AppAlbums.state.manageModal.close();
  await settle();
  eq(AppAlbums.state.managePoll.active, false, "fermeture → suivi arrêté");
  eq(AppAlbums.state.managePoll.timer, null, "fermeture → timer annulé");
  const before = listCalls().length;
  await sleep(25);
  eq(listCalls().length, before, "[négatif] fermée → plus aucun tick");
}
{
  // (c) Idempotence : relancer le suivi ne crée JAMAIS un 2e timer.
  const b = makeAlbum({ id: 404, title: "Idem", status: "building", items: [albumItem(6, "pending")] });
  b.progress = { total: 1, done: 0 };
  albums = [b];
  AppAlbums.openManage();
  await settle();
  const timer1 = AppAlbums.state.managePoll.timer;
  AppAlbums.managePollStart();
  AppAlbums.managePollStart();
  eq(AppAlbums.state.managePoll.timer, timer1, "[négatif] pas d'empilement de timers");
  eq(AppAlbums.state.managePoll.attempts, 0, "aucun tick consommé par les relances");
  AppAlbums.state.manageModal.close();
  await settle();
}
{
  // (d) Erreurs réseau bornées : on n'arrête qu'après managePollErrorsMax.
  AppAlbums.state.managePollErrorsMax = 2;
  const b = makeAlbum({ id: 405, title: "Réseau", status: "building", items: [albumItem(7, "pending")] });
  b.progress = { total: 1, done: 0 };
  albums = [b];
  AppAlbums.openManage();
  await settle();
  albumListFailure = true;
  await AppAlbums.managePollNow();
  eq(AppAlbums.state.managePoll.active, true, "1re erreur réseau : le suivi survit");
  await AppAlbums.managePollNow();
  eq(AppAlbums.state.managePoll.active, false, "2e erreur réseau : suivi arrêté (borne)");
  ok(/Suivi interrompu/.test(byId("gallery-albums-manage-status").textContent), "message d'interruption affiché");
  albumListFailure = false;
  AppAlbums.state.managePollErrorsMax = 5;
  AppAlbums.state.manageModal.close();
  await settle();
}
{
  // (e) Le tick ne vole pas le focus et ne casse pas les modales ouvertes.
  const b = makeAlbum({ id: 406, title: "Focus", status: "building", items: [albumItem(8, "pending")] });
  b.progress = { total: 1, done: 0 };
  const r = makeAlbum({ id: 407, title: "Prêt", status: "ready", items: [albumItem(1, "ok")] });
  r.progress = { total: 1, done: 1 };
  albums = [b, r];
  AppAlbums.openManage();
  await settle();

  // (e1) Focus dans la LISTE : rendu au bouton équivalent après reconstruction.
  albumRowBtn(407, "load").focus();
  b.progress.done = 1;
  await AppAlbums.managePollNow();
  eq(window.document.activeElement, albumRowBtn(407, "load"), "focus préservé dans la liste après re-rendu");

  // (e2) Modale de renommage ouverte : le tick la laisse vivre et ne lui vole
  // pas le focus (même quand la liste change et est reconstruite).
  AppAlbums.openRename(findAlbum(406));
  await settle();
  const renameModal = modalRoot("gallery-album-rename-modal");
  ok(!!renameModal, "modale de renommage ouverte");
  ok(renameModal.contains(window.document.activeElement), "focus dans la modale de renommage");
  b.items.push(albumItem(9, "pending"));
  b.progress.total = 2;
  await AppAlbums.managePollNow();
  eq(modalRoot("gallery-album-rename-modal"), renameModal, "[négatif] le tick ne remplace PAS la modale ouverte");
  ok(renameModal.contains(window.document.activeElement), "le tick ne vole PAS le focus de la modale");
  modalBtn("gallery-album-rename-modal", "Annuler").click();
  await settle();
  AppAlbums.state.manageModal.close();
  await settle();
}
{
  // (f) Création pendant que la GESTION est ouverte → la liste repart en suivi.
  albums = [makeAlbum({ id: 408, title: "Terminé", status: "ready", items: [albumItem(1, "ok")] })];
  AppAlbums.openManage();
  await settle();
  eq(AppAlbums.state.managePoll.active, false, "aucun album en préparation → pas de suivi");
  await AppAlbums.create([1], "Nouveau", "");
  await settle();
  eq(AppAlbums.state.managePoll.active, true, "création suivie par la modale de gestion ouverte");
  if (AppAlbums.state.resultModal) {
    AppAlbums.state.resultModal.close();
    await settle();
  }
  AppAlbums.state.manageModal.close();
  await settle();
  eq(AppAlbums.state.managePoll.active, false, "tout est fermé → suivi arrêté");
}

/* ── Récapitulatif ──────────────────────────────────────────────────────── */
AppGallery.stop();
console.log("");
console.log("✅ test_albums : " + n + " assertions PASSENT");
process.exit(0);
