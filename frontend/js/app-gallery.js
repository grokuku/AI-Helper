/* ══════════════════════════════════════════════════════════════════════════
 * app-gallery.js — Onglet « Galerie » du front AI-Helper.
 *
 * CONSULTATION (commit backend 0d5e8c6) : liste paginée + grille virtualisée +
 * visionneuse + panneau d'informations.
 * GESTION (cette étape) : FILTRES (type / sous-dossier / nom / dates / tri),
 * vue CORBEILLE (status=trashed), SUPPRESSION (optimiste + rollback) et
 * ACTIONS GROUPÉES (delete/restore/purge + téléchargement), restauration,
 * purge (avec confirmation), téléchargement de l'original (grille, infopane,
 * lightbox), toasts et verrouillage pendant les appels.
 *
 * ADAPTATEUR des briques holaf VENDUES (frontend/vendor/holaf/, jamais
 * modifiées — cf. holaf-manifest.json + `holaf check`) :
 *   - window.HolafCollection  → liste paginée virtualisée (données pures)
 *   - window.HolafThumbCache  → cache + ordonnanceur de vignettes (stratégie URL)
 *   - window.HolafGrid        → grille virtualisée DOM
 *   - window.HolafLightbox    → visionneuse (zoom/pan via HolafViewport)
 *   - window.HolafViewport    → géométrie zoom/pan injectée dans la lightbox
 *   - window.HolafInfoPane    → panneau d'informations (champs + blocs copiables)
 *
 * Ce fichier ne contient QUE le métier de l'hôte : endpoints, construction des
 * URL, formatage FR, i18n FR injectée, et le branchement des callbacks des
 * briques. AUCUNE mécanique générique n'est réimplémentée ici.
 *
 * Réglage de taille d'affichage : SLIDER IDENTIQUE À ComfyUI
 * (js/image_viewer/image_viewer_ui.js : min=80 max=300 step=10, défaut 150) ;
 * il pilote à la fois la taille des cellules de la grille ET la taille de
 * vignette demandée au serveur, « snappée » sur les tailles servies par le
 * backend AI-Helper {128, 256, 512} (backend/routes/media.py : THUMB_SIZES).
 * ────────────────────────────────────────────────────────────────────────── */

/* ── Constantes ──────────────────────────────────────────────────────────── */

// Pagination de GET /api/media (limit max backend = 200).
var GALLERY_PAGE_SIZE = 60;
// Fenêtre de préchargement autour d'un média ouvert (navigation visionneuse).
var GALLERY_PREFETCH = 10;

// Slider de taille d'affichage — MÊMES bornes que la galerie ComfyUI.
var GALLERY_DISPLAY_MIN = 80;
var GALLERY_DISPLAY_MAX = 300;
var GALLERY_DISPLAY_STEP = 10;
var GALLERY_DISPLAY_DEFAULT = 150;
var GALLERY_DISPLAY_KEY = 'gallery-display-size';

// Tailles de vignettes servies par le backend AI-Helper (THUMB_SIZES).
var GALLERY_THUMB_SIZES = [128, 256, 512];

// Debounce de la recherche par nom (ms) avant de relancer la liste.
var GALLERY_SEARCH_DEBOUNCE = 350;

// Reprise UNIQUE et bornée d'une vignette après un échec de chargement <img>
// (un <img> en erreur ne se recharge pas tout seul, la cellule resterait
// bloquée sur l'état d'erreur). Délai court (ms) : laisse passer un aléa
// réseau transitoire, puis on renonce et on affiche l'état d'erreur.
var GALLERY_THUMB_RETRY_MS = 1500;

// Valeurs de tri exposées par le backend (GET /api/media?sort=).
var GALLERY_SORTS = ['created_at_desc', 'created_at_asc', 'name_asc', 'size_desc'];

/* ── Helpers purs (testables sans DOM) ───────────────────────────────────── */

/**
 * Taille de vignette à demander au serveur pour une taille d'affichage donnée.
 *
 * On garde le SLIDER de ComfyUI tel quel (80..300) et on choisit la plus petite
 * taille serveur disponible >= l'affichage demandé, pour éviter tout upscale :
 *   0..128  -> 128
 *   129..256 -> 256
 *   257..∞  -> 512
 * Justification : le pack ComfyUI génère ses vignettes à une taille FIXE
 * (holaf_utils.py : THUMBNAIL_SIZE = (200, 200)) tandis que le backend web
 * n'expose que des paliers discrets {128, 256, 512}. On aligne donc le front
 * sur ces paliers sans casser l'UX du slider.
 */
function galleryServerThumbSize(displayPx) {
  var n = Number(displayPx);
  if (!isFinite(n) || n <= 0) n = GALLERY_DISPLAY_MIN;
  for (var i = 0; i < GALLERY_THUMB_SIZES.length; i++) {
    if (n <= GALLERY_THUMB_SIZES[i]) return GALLERY_THUMB_SIZES[i];
  }
  return GALLERY_THUMB_SIZES[GALLERY_THUMB_SIZES.length - 1];
}

function galleryApiBase() {
  return (typeof API !== 'undefined') ? API : (window.API || '/api');
}

function galleryLocalMode() {
  return (typeof LOCAL_MODE !== 'undefined') ? !!LOCAL_MODE : !!window.LOCAL_MODE;
}

function galleryById(id) {
  return (typeof $ === 'function') ? $(id) : document.getElementById(id);
}

function galleryJson(res) {
  if (typeof safeJson === 'function') return safeJson(res);
  return res.json().catch(function () { return { error: 'Erreur serveur ' + res.status }; });
}

function galleryThumbUrl(item, size) {
  return galleryApiBase() + '/media/' + encodeURIComponent(item.id) + '/thumbnail?size=' + size;
}

/**
 * Ajoute un cache-buster `_retry=` à une URL de vignette (PUR).
 *
 * Utilisé UNIQUEMENT pour la reprise après échec : force le navigateur (et un
 * éventuel cache heuristique) à redemander l'image. On ne touche PAS au param
 * `size` — le backend ignore les paramètres inconnus, le cache serveur/navigateur
 * reste indexé par (média, taille) et l'ETag est inchangé.
 */
function galleryThumbRetryUrl(url, attempt) {
  if (!url) return url;
  var sep = (url.indexOf('?') === -1) ? '?' : '&';
  return url + sep + '_retry=' + (attempt || 1);
}

function galleryDownloadUrl(item) {
  return galleryApiBase() + '/media/' + encodeURIComponent(item.id) + '/download';
}

/**
 * Construit l'URL de GET /api/media pour une page donnée et un état de filtres.
 *
 * PUR (testable hors DOM) : l'ordre des paramètres est STABLE (page, limit,
 * sort d'abord — les tests de non-régression s'appuient sur ce préfixe), puis
 * les filtres optionnels ne sont ajoutés QUE s'ils sont renseignés (un filtre
 * vide n'apparaît donc jamais dans la requête).
 *
 * @param {number} page    page 1-indexée
 * @param {number} limit   taille de page
 * @param {object} filters { kind, subfolder, q, from, to, status }
 * @param {string} sort    valeur de tri backend (déf. created_at_desc)
 */
function galleryMediaUrl(page, limit, filters, sort) {
  filters = filters || {};
  var parts = [
    'page=' + page,
    'limit=' + limit,
    'sort=' + encodeURIComponent(sort || 'created_at_desc'),
  ];
  if (filters.kind) parts.push('kind=' + encodeURIComponent(filters.kind));
  if (filters.subfolder) parts.push('subfolder=' + encodeURIComponent(filters.subfolder));
  if (filters.q) parts.push('q=' + encodeURIComponent(filters.q));
  if (filters.from) parts.push('from=' + encodeURIComponent(filters.from));
  if (filters.to) parts.push('to=' + encodeURIComponent(filters.to));
  if (filters.status) parts.push('status=' + encodeURIComponent(filters.status));
  return galleryApiBase() + '/media?' + parts.join('&');
}

function galleryEsc(s) {
  if (s === undefined || s === null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function galleryFormatBytes(n) {
  n = Number(n) || 0;
  if (n <= 0) return '0 o';
  var units = ['o', 'Ko', 'Mo', 'Go', 'To'];
  var i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  var v = (i === 0) ? String(Math.round(n)) : n.toFixed(n < 10 ? 1 : 0);
  return v + ' ' + units[i];
}

function galleryFormatDate(iso) {
  if (!iso) return '';
  try {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    var pad = function (x) { return x < 10 ? '0' + x : '' + x; };
    return pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + d.getFullYear()
      + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  } catch (e) { return String(iso); }
}

function galleryFormatDuration(seconds) {
  var s = Number(seconds);
  if (!isFinite(s) || s < 0) return '';
  var m = Math.floor(s / 60);
  var r = s - m * 60;
  return m > 0 ? (m + ' min ' + r.toFixed(1) + ' s') : (r.toFixed(2) + ' s');
}

function galleryKindLabel(kind) {
  if (kind === 'image') return 'Image';
  if (kind === 'video') return 'Vidéo';
  if (kind === 'audio') return 'Audio';
  return kind || 'Média';
}

function galleryKindIcon(kind) {
  if (kind === 'video') return '🎬';
  if (kind === 'audio') return '🎵';
  return '🖼️';
}

function galleryPrettyJson(raw) {
  if (!raw) return '';
  if (typeof raw === 'object') { try { return JSON.stringify(raw, null, 2); } catch (e) { return ''; } }
  try { return JSON.stringify(JSON.parse(raw), null, 2); } catch (e) { return String(raw); }
}

/**
 * Champs du panneau d'informations à partir des métadonnées /metadata.
 * Une valeur vide ('') est omise par la brique (champ masqué).
 */
function galleryInfoFields(data) {
  data = data || {};
  var resolution = (data.width && data.height) ? (data.width + ' × ' + data.height + ' px') : '';
  return [
    { label: 'Nom', value: data.filename || '', stacked: true },
    { label: 'Type', value: galleryKindLabel(data.kind) },
    { label: 'Taille', value: galleryFormatBytes(data.size) },
    { label: 'Résolution', value: resolution },
    { label: 'Ratio', value: data.ratio ? String(data.ratio) : '' },
    { label: 'Durée', value: (data.duration !== null && data.duration !== undefined) ? galleryFormatDuration(data.duration) : '' },
    { label: 'Codec', value: data.codec || '' },
    { label: 'Sous-dossier', value: data.subfolder || '' },
    { label: 'Ajouté le', value: galleryFormatDate(data.created_at) },
  ];
}

/** Blocs copiables (prompt + workflow JSON). AUCUNE action « charger ». */
function galleryInfoBlocks(data) {
  data = data || {};
  return [
    {
      id: 'prompt',
      label: 'Prompt',
      text: data.prompt || '',
      copyable: true,
      copyLabel: 'Copier le prompt',
      copyPlacement: 'before',
      copyDisabled: !data.prompt,
      empty: 'Aucun prompt pour ce média',
    },
    {
      id: 'workflow',
      label: 'Workflow',
      text: galleryPrettyJson(data.workflow),
      copyable: true,
      copyLabel: 'Copier le workflow',
      copyPlacement: 'before',
      copyDisabled: !data.workflow,
      empty: 'Aucun workflow pour ce média',
    },
  ];
}

/** Champs « preview » synchrones (aucun réseau) affichés avant /metadata. */
function galleryPreviewFields(item) {
  item = item || {};
  return [
    { label: 'Nom', value: item.filename || '', stacked: true },
    { label: 'Type', value: galleryKindLabel(item.kind) },
    { label: 'Taille', value: galleryFormatBytes(item.size) },
    { label: 'Sous-dossier', value: item.subfolder || '' },
    { label: 'Ajouté le', value: galleryFormatDate(item.created_at) },
  ];
}

function galleryIsInputFocused(e) {
  var t = e && e.target;
  if (!t || !t.tagName) return false;
  var tag = t.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || t.isContentEditable === true;
}

/* ── État de l'onglet ────────────────────────────────────────────────────── */

var galleryState = {
  started: false,
  displaySize: GALLERY_DISPLAY_DEFAULT,
  serverSize: 0,
  collection: null,
  thumbCache: null,
  grid: null,
  lightbox: null,
  infoPane: null,
  selectionIds: [],
  lightboxIndex: -1,
  lightboxItem: null,
  loadedOnce: false,
  // GESTION : vue courante + filtres + tri (relancent la liste).
  view: 'normal',
  filters: {},
  sort: 'created_at_desc',
  // Verrou d'actions pendant un appel réseau (désactive les boutons).
  busy: false,
  // Observables de test : dernier appel réseau et derniers téléchargements.
  lastRequest: null,
  lastDownload: null,
  lastDownloadBatch: [],
};

var galleryKeyHandler = null;
var gallerySearchTimer = null;

/* ── Cycle de vie de l'onglet ────────────────────────────────────────────── */

function galleryStart() {
  if (galleryLocalMode()) return;
  if (!galleryState.started) {
    if (!galleryInit()) return;
    galleryState.started = true;
  }
}

function galleryStop() {
  // Ferme la visionneuse si elle est ouverte (l'onglet quitté ne doit pas
  // garder un overlay plein écran). Les données sont conservées.
  if (galleryState.lightbox && galleryState.lightbox.isOpen()) {
    try { galleryState.lightbox.close(); } catch (e) { /* ignore */ }
  }
}

/** Relance le chargement depuis la première page (bouton Rafraîchir). */
function galleryRefresh() {
  if (!galleryState.started) { galleryStart(); return; }
  galleryReload();
}

/**
 * RELOAD COMPLET de la liste : lit les filtres de la barre d'outils, réinitialise
 * la collection (annule les fetch en vol), vide la sélection, remonte en haut et
 * recharge la première page.
 *
 * ⚠️ On n'utilise JAMAIS le chemin « delta » (insertTop/applyDelta) pour un
 * changement de filtre : la sémantique d'un filtre est un NOUVEAU JEU de
 * données, pas un patch — c'est la conception de la brique (reset + refetch).
 * Utilisé aussi comme ROLLBACK après un échec d'action optimiste.
 */
function galleryReload() {
  var col = galleryState.collection;
  if (!col) return Promise.resolve();
  var filters = galleryReadFilterInputs();
  col.setFilters(filters); // = reset() + remplace les filtres
  galleryState.loadedOnce = false;
  galleryClearSelection();
  var gridEl = galleryById('gallery-grid');
  if (gridEl) gridEl.scrollTop = 0;
  galleryHideStates();
  galleryShowLoading();
  return col.ensureRange(0, GALLERY_PAGE_SIZE - 1);
}

/* ── Initialisation : création des briques + branchements ────────────────── */

function galleryInit() {
  var gridEl = galleryById('gallery-grid');
  var infoEl = galleryById('gallery-info');
  if (!gridEl || !infoEl) return false;

  if (!window.HolafCollection || !window.HolafThumbCache || !window.HolafGrid
      || !window.HolafLightbox || !window.HolafInfoPane) {
    galleryShowError('Briques de galerie indisponibles (vendor/holaf).');
    return false;
  }

  // 1) Taille d'affichage : reprise de la dernière valeur (comme ComfyUI) sinon défaut.
  var saved = 0;
  try { saved = parseInt(localStorage.getItem(GALLERY_DISPLAY_KEY), 10); } catch (e) { saved = 0; }
  var initial = isFinite(saved) && saved >= GALLERY_DISPLAY_MIN && saved <= GALLERY_DISPLAY_MAX
    ? saved : GALLERY_DISPLAY_DEFAULT;
  galleryState.displaySize = initial;
  galleryState.serverSize = galleryServerThumbSize(initial);

  var slider = galleryById('gallery-thumb-size');
  if (slider) {
    slider.min = String(GALLERY_DISPLAY_MIN);
    slider.max = String(GALLERY_DISPLAY_MAX);
    slider.step = String(GALLERY_DISPLAY_STEP);
    slider.value = String(initial);
    slider.addEventListener('input', function () {
      gallerySetDisplaySize(parseInt(slider.value, 10));
    });
  }
  galleryRenderSizeLabels();

  // 2) Collection (données paginées) — mode 'append' = scroll infini.
  galleryState.collection = window.HolafCollection.create({
    pageSize: GALLERY_PAGE_SIZE,
    mode: 'append',
    getId: function (m) { return m.id; },
    sortKey: function (m) { return m.created_at; },
    fetchPage: galleryFetchPage,
  });

  // 3) Cache de vignettes — stratégie 'url' (le navigateur cache via ETag).
  galleryState.thumbCache = window.HolafThumbCache.create({
    capacity: 1200,
    concurrency: 6,
    strategy: 'url',
    getId: function (m) { return m.id; },
    load: function (m) { return galleryThumbUrl(m, galleryState.serverSize); },
  });

  // 4) Grille virtualisée DOM.
  galleryState.grid = window.HolafGrid.create(gridEl, {
    itemSize: function () { return galleryState.displaySize; },
    gap: 12,
    aspect: 1,
    bufferFactor: 1.5,
    getId: function (m) { return m.id; },
    keyboard: false, // clavier piloté globalement via galleryOnKeyDown
    activateOnClick: false,
    labels: { grid: 'Galerie des médias', cell: 'Média' },
    cell: galleryCellRenderer(),
    onVisibleRange: galleryOnVisibleRange,
    onSelectionChange: galleryOnSelectionChange,
    onActivate: galleryOnActivate,
    onAction: galleryOnAction,
  });
  galleryState.grid.setSource(galleryState.collection);

  // 5) Visionneuse (zoom/pan via HolafViewport injecté).
  galleryState.lightbox = window.HolafLightbox.create({
    host: document.body,
    getId: function (m) { return m.id; },
    urlFor: function (m) { return galleryDownloadUrl(m); },
    renderMedia: galleryRenderMedia,
    viewport: window.HolafViewport,
    viewportOptions: { maxZoom: 12, doubleClickZoom: true, wheel: true, drag: true },
    preload: GALLERY_PREFETCH,
    modes: ['zoom', 'fullscreen'],
    getColumnCount: function () { return galleryState.grid ? galleryState.grid.getColumnCount() : 1; },
    getIndex: function () { return galleryState.lightboxIndex; },
    shouldHandleKey: function (e) { return !galleryIsInputFocused(e); },
    shouldPreload: function (m) { return m && m.kind === 'image'; },
    labels: { prev: '‹', next: '›', close: '✕', region: 'Visionneuse média' },
    onNavigate: function (dir, item, index) { galleryState.lightboxIndex = index; },
  });
  galleryState.lightbox.setSource({
    total: function () { return galleryState.collection ? galleryState.collection.total : 0; },
    getAt: galleryEnsureItem,
    getAtSync: function (i) { return galleryState.collection ? (galleryState.collection.at(i) || null) : null; },
  });

  // 6) Panneau d'informations (champs + blocs copiables FR, sans édition).
  galleryState.infoPane = window.HolafInfoPane.create(infoEl, {
    preview: galleryPreviewFields,
    resolve: galleryResolveMetadata,
    actions: [
      {
        id: 'download',
        label: 'Télécharger',
        isEnabled: function (item) { return !!item; },
        run: function (item) { galleryDownloadItem(item); },
      },
    ],
    labels: {
      copy: 'Copier',
      copied: 'Copié !',
      copyFailed: 'Échec de la copie',
      loading: 'Chargement…',
      selectItem: 'Sélectionnez un média pour afficher ses informations.',
      notAvailable: 'Non disponible',
      error: 'Erreur',
    },
  });

  // 7) Branchements globaux.
  galleryBindCollectionEvents();
  galleryBindKeyHandler();
  galleryBindLightboxEvents();
  galleryBindFilterControls();
  galleryUpdateViewToggle();
  galleryUpdateActionBar();

  // 8) Premier chargement.
  galleryHideStates();
  galleryShowLoading();
  galleryState.collection.ensureRange(0, GALLERY_PAGE_SIZE - 1);
  return true;
}

/* ── Collection : chargement d'une page via GET /api/media ───────────────── */

function galleryFetchPage(ctx) {
  var page = (ctx.page || 0) + 1; // l'API est 1-indexée
  var limit = ctx.limit || GALLERY_PAGE_SIZE;
  var url = galleryMediaUrl(page, limit, ctx.filters, galleryState.sort);
  return fetch(url, { signal: ctx.signal, credentials: 'same-origin' }).then(function (res) {
    return galleryJson(res).then(function (data) {
      if (!res.ok || !data || data.error) {
        throw new Error((data && data.error) || ('HTTP ' + res.status));
      }
      var items = Array.isArray(data.items) ? data.items : [];
      var total = (typeof data.total === 'number') ? data.total : items.length;
      return { items: items, total: total };
    });
  });
}

function galleryBindCollectionEvents() {
  var col = galleryState.collection;
  col.on('load', function () {
    galleryState.loadedOnce = true;
    galleryHideStates();
    if (!col.total) galleryShowEmpty();
    // relayout() recalcule le sizer (total) et re-rend la fenêtre SANS vider le
    // pool de cellules (efficace en scroll infini 'append').
    if (galleryState.grid) galleryState.grid.relayout();
    galleryUpdateCount();
    galleryPopulateSubfolders();
  });
  col.on('total', galleryUpdateCount);
  col.on('error', function (p) {
    if (galleryState.loadedOnce && col.length > 0) return; // erreur d'une page suivante
    galleryShowError(galleryErrorMessage(p && p.error));
  });
}

function galleryErrorMessage(err) {
  if (!err) return 'Erreur de chargement.';
  var msg = err.message || String(err);
  if (/HTTP 401/.test(msg)) return 'Connexion requise pour accéder à la galerie.';
  if (/HTTP 403/.test(msg)) return 'Accès refusé.';
  return 'Erreur de chargement : ' + msg;
}

/* ── Grille : renderer de cellule + callbacks ────────────────────────────── */

/* Debug vignettes (OPT-IN) : trace l'URL + le statut HTTP réel d'un échec.
   Activé par window.AIH_GALLERY_DEBUG === true, localStorage 'aihGalleryDebug'
   = '1', ou l'URL ?galleryDebug=1. JAMAIS actif par défaut (aucun bruit). */
function galleryThumbDebugEnabled() {
  try {
    if (window.AIH_GALLERY_DEBUG === true) return true;
    if (window.localStorage && window.localStorage.getItem('aihGalleryDebug') === '1') return true;
    return /(?:[?&])galleryDebug=1(?:&|$)/.test(window.location.search || '');
  } catch (e) { return false; }
}

/* Sonde de diagnostic : rejoue un GET sur l'URL de vignette en échec pour
   exposer le statut ET le corps JSON (code/reason renvoyés par le backend),
   puis trace le tout. Uniquement appelé en debug. */
function galleryThumbProbe(url) {
  try {
    fetch(url, { credentials: 'same-origin' }).then(function (res) {
      if (!res) { console.warn('[gallery] vignette échec', url, 'réponse vide'); return null; }
      if (res.status >= 400 && typeof res.json === 'function') {
        return res.json().then(function (j) {
          console.warn('[gallery] vignette échec', url, 'HTTP', res.status, j);
        }).catch(function () {
          console.warn('[gallery] vignette échec', url, 'HTTP', res.status);
        });
      }
      console.warn('[gallery] vignette échec', url, 'HTTP', res.status);
      return null;
    }).catch(function (e) {
      console.warn('[gallery] vignette échec', url, 'fetch_error', e && e.message);
    });
  } catch (e) { /* ignore */ }
}

/* État d'ERREUR d'une cellule : DISTINCT du placeholder d'attente (classe
   `--error`, glyphe ⚠ + title), pour qu'un échec ne soit plus silencieux. */
function galleryCellShowThumbError(el, url) {
  if (el._gImg) el._gImg.classList.add('gallery-cell-img--hidden');
  var ph = el._gPh;
  if (ph) {
    ph.textContent = '⚠';
    ph.classList.remove('is-hidden');
    ph.classList.add('gallery-cell-ph--error');
    ph.title = 'Vignette indisponible';
  }
  if (url && galleryThumbDebugEnabled()) galleryThumbProbe(url);
}

function galleryCellResetPh(el) {
  var ph = el._gPh;
  if (!ph) return;
  ph.classList.remove('gallery-cell-ph--error');
  ph.removeAttribute('title');
}

function galleryCellRenderer() {
  // Icône ⤓ / 🗑 / ♻ / ✖ : actions rapides au survol, marquées
  // `data-holaf-action` → la brique émet onAction() SANS changer la sélection.
  function makeAction(action, label, title) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'gallery-cell-action';
    b.dataset.holafAction = action;
    b.textContent = label;
    b.title = title;
    b.setAttribute('aria-label', title);
    return b;
  }

  return {
    create: function () {
      var el = document.createElement('div');
      el.className = 'gallery-cell';
      var img = document.createElement('img');
      img.className = 'gallery-cell-img gallery-cell-img--hidden';
      img.alt = '';
      img.loading = 'lazy';
      img.draggable = false;
      el.appendChild(img);
      var ph = document.createElement('div');
      ph.className = 'gallery-cell-ph';
      el.appendChild(ph);
      var badge = document.createElement('span');
      badge.className = 'gallery-cell-badge';
      el.appendChild(badge);
      var trash = document.createElement('span');
      trash.className = 'gallery-cell-trash is-hidden';
      trash.textContent = '🗑';
      el.appendChild(trash);
      var actions = document.createElement('div');
      actions.className = 'gallery-cell-actions';
      actions.appendChild(makeAction('download', '⤓', "Télécharger l'original"));
      actions.appendChild(makeAction('delete', '🗑', 'Mettre à la corbeille'));
      actions.appendChild(makeAction('restore', '♻', 'Restaurer'));
      actions.appendChild(makeAction('purge', '✖', 'Supprimer définitivement'));
      el.appendChild(actions);
      el._gImg = img;
      el._gPh = ph;
      el._gBadge = badge;
      el._gTrash = trash;
      el._gActions = actions;
      el._gToken = 0;
      // Reprise de vignette : URL attendue + drapeau « déjà retenté » (borné).
      el._gExpectedUrl = '';
      el._gThumbRetried = false;
      img.addEventListener('load', function () {
        el._gPh.classList.add('is-hidden');
        img.classList.remove('gallery-cell-img--hidden');
      });
      img.addEventListener('error', function () {
        // Reprise UNIQUE et bornée : un <img> en erreur ne se recharge pas
        // seul → la cellule resterait sur l'état d'erreur même après
        // correction côté serveur (ex. Pillow installé). On retente UNE fois
        // avec un cache-buster, puis on affiche l'état d'erreur.
        var base = el._gExpectedUrl;
        if (base && !el._gThumbRetried) {
          el._gThumbRetried = true;
          setTimeout(function () {
            // Cellule recyclée / autre média entre-temps → ne rien écraser.
            if (el._gExpectedUrl !== base) return;
            // L'erreur a masqué l'<img> (`display:none`) : on lui redonne une
            // boîte et on force un chargement EAGER (sinon `loading=lazy`
            // pourrait ne jamais déclencher le rechargement).
            img.classList.remove('gallery-cell-img--hidden');
            img.loading = 'eager';
            img.src = galleryThumbRetryUrl(base, 1);
          }, GALLERY_THUMB_RETRY_MS);
        }
        galleryCellShowThumbError(el, base || img.getAttribute('src'));
      });
      return el;
    },

    update: function (el, item, ctx) {
      var token = ++el._gToken;
      el.dataset.mediaId = String(item.id);
      el._gBadge.textContent = galleryKindIcon(item.kind);
      el._gBadge.dataset.kind = item.kind || '';

      // UI distincte en corbeille : marqueur + opacité + actions Restaurer/Purger.
      var trashed = galleryIsTrashed(item);
      el.classList.toggle('gallery-cell--trashed', trashed);
      el._gTrash.classList.toggle('is-hidden', !trashed);
      gallerySetActionVisibility(el, 'delete', !trashed);
      gallerySetActionVisibility(el, 'restore', trashed);
      gallerySetActionVisibility(el, 'purge', trashed);

      var img = el._gImg;
      var ph = el._gPh;

      // Pas de vignette produisible (décision backend) : l'audio affiche SON
      // icône de type (état normal) ; pour image/vidéo c'est une DÉGRADATION
      // (outil backend absent : Pillow/ffmpeg) → état d'erreur DISTINCT + trace
      // en debug, pour qu'un placeholder ne masque plus un problème réel.
      if (!item.thumb_available) {
        el._gExpectedUrl = '';
        el._gThumbRetried = false;
        img.classList.add('gallery-cell-img--hidden');
        img.removeAttribute('src');
        ph.textContent = galleryKindIcon(item.kind);
        ph.classList.remove('is-hidden');
        if (item.kind === 'audio') {
          galleryCellResetPh(el);
        } else {
          ph.classList.add('gallery-cell-ph--error');
          ph.title = 'Vignette indisponible (outil backend absent ?)';
          if (galleryThumbDebugEnabled()) {
            console.warn('[gallery] thumb_available=false (média ' + item.kind + ', id=' + item.id + ')');
          }
        }
        return;
      }

      ph.textContent = '';
      ph.classList.remove('is-hidden');
      galleryCellResetPh(el);
      img.classList.add('gallery-cell-img--hidden');

      // Le cache 'url' est synchrone en pratique (aucun fetch de la brique).
      var cache = galleryState.thumbCache;
      var cached = cache.peek(item.id);
      if (cached) {
        el._gExpectedUrl = cached;
        el._gThumbRetried = false;
        img.src = cached;
        return;
      }
      cache.request(item, window.HolafThumbCache.PRIORITY_HIGH).then(function (url) {
        if (el._gToken !== token || !url) return; // cellule recyclée entre-temps
        el._gExpectedUrl = url;
        el._gThumbRetried = false;
        img.src = url;
      }).catch(function () {
        if (el._gToken !== token) return;
        galleryCellShowThumbError(el, galleryThumbUrl(item, galleryState.serverSize));
      });
    },

    release: function (el) {
      el._gToken++;
      el._gExpectedUrl = '';
      el._gThumbRetried = false;
      if (el._gImg) { el._gImg.removeAttribute('src'); el._gImg.classList.add('gallery-cell-img--hidden'); }
      if (el._gPh) { el._gPh.textContent = ''; el._gPh.classList.add('is-hidden'); }
      galleryCellResetPh(el);
    },
  };
}

function galleryOnVisibleRange(start, end, ids) {
  if (!galleryState.started && !galleryState.grid) return;
  var col = galleryState.collection;
  var cache = galleryState.thumbCache;
  if (cache && ids && ids.length) cache.onVisible(ids);
  if (col && end >= start) col.ensureRange(start, end);
}

function galleryOnSelectionChange(ids, items) {
  galleryState.selectionIds = ids.slice();
  galleryUpdateSelectionLabel(ids.length);
  galleryUpdateActionBar();
  var pane = galleryState.infoPane;
  if (!pane) return;
  if (ids.length === 1 && items && items.length === 1) {
    pane.show(items[0]);
  } else {
    // 0 ou multi-sélection : on revient à l'état « sélectionnez un média ».
    pane.clear();
  }
}

function galleryOnActivate(item, index, kind) {
  if (kind !== 'dblclick' || !item) return;
  galleryState.lightboxIndex = index;
  // Garantit la présence de l'item (et de ses voisins) avant d'ouvrir.
  galleryEnsureItem(index).then(function () {
    if (galleryState.collection) {
      galleryState.collection.ensureRange(Math.max(0, index - 1), index + GALLERY_PREFETCH);
    }
    if (galleryState.lightbox) galleryState.lightbox.openZoom(item);
  });
}

/** Clic sur une action rapide d'une cellule (`data-holaf-action`). */
function galleryOnAction(actionId, item, index) {
  if (!item) return;
  if (actionId === 'download') galleryDownloadItem(item);
  else if (actionId === 'delete') galleryDeleteItem(item);
  else if (actionId === 'restore') galleryRestoreItem(item);
  else if (actionId === 'purge') galleryPurgeItem(item);
}

/* ── Visionneuse : renderer média (img/vidéo/audio, PAS d'éditeur) ───────── */

function galleryRenderMedia(ctx) {
  var item = ctx.item;
  var container = ctx.container;
  var kind = item.kind;

  if (kind === 'video') {
    var video = document.createElement('video');
    video.className = 'gallery-lightbox-video';
    video.controls = true;
    video.autoplay = true;
    video.playsInline = true;
    video.src = galleryDownloadUrl(item);
    video.onloadedmetadata = function () { ctx.onReady({ width: video.videoWidth, height: video.videoHeight }); };
    video.onerror = function () { ctx.onReady({}); };
    container.appendChild(video);
    return {
      el: video,
      destroy: function () {
        try { video.pause(); } catch (e) { /* ignore */ }
        video.onloadedmetadata = null; video.onerror = null;
        video.removeAttribute('src');
        if (video.load) { try { video.load(); } catch (e) { /* ignore */ } }
        if (video.parentNode) video.parentNode.removeChild(video);
      },
    };
  }

  if (kind === 'audio') {
    var audio = document.createElement('audio');
    audio.className = 'gallery-lightbox-audio';
    audio.controls = true;
    audio.autoplay = true;
    audio.src = galleryDownloadUrl(item);
    audio.onloadedmetadata = function () { ctx.onReady({}); };
    audio.onerror = function () { ctx.onReady({}); };
    container.appendChild(audio);
    return {
      el: audio,
      destroy: function () {
        try { audio.pause(); } catch (e) { /* ignore */ }
        audio.onloadedmetadata = null; audio.onerror = null;
        audio.removeAttribute('src');
        if (audio.load) { try { audio.load(); } catch (e) { /* ignore */ } }
        if (audio.parentNode) audio.parentNode.removeChild(audio);
      },
    };
  }

  // Image (défaut). La vignette peut servir de placeholder flou le temps du
  // chargement pleine résolution — ici on va droit au download (simple).
  var img = document.createElement('img');
  img.className = 'gallery-lightbox-img';
  img.draggable = false;
  img.alt = item.filename || '';
  img.onload = function () { ctx.onReady({ width: img.naturalWidth, height: img.naturalHeight }); };
  img.onerror = function () { ctx.onReady({}); };
  img.src = galleryDownloadUrl(item);
  container.appendChild(img);
  return {
    el: img,
    destroy: function () {
      img.onload = null; img.onerror = null;
      img.removeAttribute('src');
      if (img.parentNode) img.parentNode.removeChild(img);
    },
  };
}

function galleryBindLightboxEvents() {
  var lb = galleryState.lightbox;
  if (!lb) return;
  lb.on('navigate', function (p) {
    if (p && typeof p.index === 'number') galleryState.lightboxIndex = p.index;
    if (p && p.item) galleryState.lightboxItem = p.item;
  });
  lb.on('open', function (p) {
    // On mémorise l'item courant (le bouton ⤓ ne dépend PAS de lightbox.current()
    // qui, chez nous, s'appuie sur getIndex() — l'hôte fait autorité sur l'index).
    if (p && p.item) galleryState.lightboxItem = p.item;
    // ⤓ Télécharger : injecté dans l'overlay créé par la brique (on NE modifie
    // PAS la brique — on ajoute un bouton hôte dans son overlay).
    galleryInjectLightboxDownload();
  });
}

/**
 * Ajoute un bouton « télécharger l'original » à l'overlay de la visionneuse.
 *
 * La brique HolafLightbox ne propose pas de slot de contrôles personnalisés :
 * on se contente d'appendre un bouton hôte dans l'overlay (`.holaf-lightbox-
 * overlay`) créé par la brique, en réutilisant sa classe `.holaf-lightbox-nav`
 * pour l'apparence. Le bouton lit `galleryState.lightboxItem` (mémorisé sur les
 * events open/navigate) plutôt que `lightbox.current()` — car notre option
 * `getIndex()` fait autorité sur l'index et peut renvoyer null. Besoin futur de
 * brique : un vrai slot `controls` dans le chrome de la lightbox.
 */
function galleryInjectLightboxDownload() {
  var overlays;
  try { overlays = document.querySelectorAll('.holaf-lightbox-overlay'); } catch (e) { overlays = null; }
  if (!overlays) return;
  for (var i = 0; i < overlays.length; i++) {
    var ov = overlays[i];
    if (!ov || ov.querySelector('.gallery-lightbox-download')) continue;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'holaf-lightbox-nav gallery-lightbox-download';
    btn.textContent = '⤓';
    btn.title = "Télécharger l'original";
    btn.setAttribute('aria-label', "Télécharger l'original");
    btn.style.top = '20px';
    btn.style.right = '70px';
    btn.style.width = '40px';
    btn.style.height = '40px';
    btn.style.borderRadius = '50%';
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      var cur = galleryState.lightboxItem ||
        ((galleryState.lightbox && galleryState.lightbox.current) ? galleryState.lightbox.current() : null);
      if (cur) galleryDownloadItem(cur);
    });
    ov.appendChild(btn);
  }
}

/* ── Infos : resolve via GET /api/media/<id>/metadata ────────────────────── */

function galleryResolveMetadata(item, ctx) {
  var url = galleryApiBase() + '/media/' + encodeURIComponent(item.id) + '/metadata';
  return fetch(url, { signal: ctx.signal, credentials: 'same-origin' }).then(function (res) {
    return galleryJson(res).then(function (data) {
      if (!res.ok || !data || data.error) {
        throw new Error((data && data.error) || ('HTTP ' + res.status));
      }
      return {
        fields: galleryInfoFields(data),
        blocks: galleryInfoBlocks(data),
      };
    });
  });
}

/* ── GESTION : filtres, vue (média/corbeille), actions, téléchargement ───── */

/** Valeur texte d'un champ de filtre ('' si absent), rognée. */
function galleryInputValue(id) {
  var el = galleryById(id);
  if (!el || el.value === undefined || el.value === null) return '';
  return String(el.value).trim();
}

/** Un item est-il en corbeille ? (le backend expose status ET trashed). */
function galleryIsTrashed(item) {
  return !!(item && (item.status === 'trashed' || item.trashed === true));
}

/**
 * Lit l'état des filtres depuis la barre d'outils et met à jour
 * `galleryState.filters` + `galleryState.sort`. La vue corbeille force
 * `status=trashed` (le backend exclut les corbeillés par défaut).
 */
function galleryReadFilterInputs() {
  var kind = galleryInputValue('gallery-filter-kind');
  var subfolder = galleryInputValue('gallery-filter-subfolder');
  var q = galleryInputValue('gallery-search');
  var from = galleryInputValue('gallery-filter-from');
  var to = galleryInputValue('gallery-filter-to');
  var sort = galleryInputValue('gallery-filter-sort');
  galleryState.sort = sort || 'created_at_desc';

  var filters = {};
  if (kind) filters.kind = kind;
  if (subfolder) filters.subfolder = subfolder;
  if (q) filters.q = q;
  if (from) filters.from = from;
  if (to) filters.to = to;
  if (galleryState.view === 'trash') filters.status = 'trashed';
  galleryState.filters = filters;
  return filters;
}

/** Un changement de filtre = RELOAD COMPLET de la liste (reset + page 1). */
function galleryApplyFilterChange() {
  galleryReload();
}

/** Branche les champs de filtre (selects/dates : 'change', nom : 'input') */
function galleryBindFilterControls() {
  var selectIds = [
    'gallery-filter-kind', 'gallery-filter-sort',
    'gallery-filter-subfolder', 'gallery-filter-from', 'gallery-filter-to',
  ];
  for (var i = 0; i < selectIds.length; i++) {
    var el = galleryById(selectIds[i]);
    if (el) el.addEventListener('change', galleryApplyFilterChange);
  }
  var search = galleryById('gallery-search');
  if (search) {
    search.addEventListener('input', function () {
      if (gallerySearchTimer) clearTimeout(gallerySearchTimer);
      gallerySearchTimer = setTimeout(galleryApplyFilterChange, GALLERY_SEARCH_DEBOUNCE);
    });
  }
}

/** Remplit le datalist des sous-dossiers depuis les items DÉJÀ chargés. */
function galleryPopulateSubfolders() {
  var list = galleryById('gallery-subfolders');
  var col = galleryState.collection;
  if (!list || !col || typeof col.forEachLoaded !== 'function') return;
  var seen = {};
  col.forEachLoaded(function (it) {
    var sf = it && it.subfolder;
    if (sf) seen[sf] = true;
  });
  var names = Object.keys(seen).sort();
  while (list.firstChild) list.removeChild(list.firstChild);
  for (var i = 0; i < names.length; i++) {
    var opt = document.createElement('option');
    opt.value = names[i];
    list.appendChild(opt);
  }
}

/** Réinitialise tous les filtres + tri et relance la liste. */
function galleryResetFilters() {
  var ids = ['gallery-filter-kind', 'gallery-filter-subfolder', 'gallery-search', 'gallery-filter-from', 'gallery-filter-to'];
  for (var i = 0; i < ids.length; i++) {
    var el = galleryById(ids[i]);
    if (el) el.value = '';
  }
  var sort = galleryById('gallery-filter-sort');
  if (sort) sort.value = 'created_at_desc';
  galleryState.sort = 'created_at_desc';
  galleryReload();
}

/** Bascule vue médias ⇄ corbeille (status=trashed) et relance la liste. */
function gallerySetView(view) {
  view = (view === 'trash') ? 'trash' : 'normal';
  if (galleryState.view === view && galleryState.started) return;
  galleryState.view = view;
  galleryUpdateViewToggle();
  galleryReload();
}

/** Synchronise l'apparence de la bascule de vue (aria-selected + classe). */
function galleryUpdateViewToggle() {
  var trash = galleryState.view === 'trash';
  var normalBtn = galleryById('gallery-view-normal');
  var trashBtn = galleryById('gallery-view-trash');
  if (normalBtn) {
    normalBtn.setAttribute('aria-selected', trash ? 'false' : 'true');
    normalBtn.classList.toggle('is-active', !trash);
  }
  if (trashBtn) {
    trashBtn.setAttribute('aria-selected', trash ? 'true' : 'false');
    trashBtn.classList.toggle('is-active', trash);
  }
  galleryUpdateActionBar();
}

/** Vide la sélection de la grille (si la brique l'expose). */
function galleryClearSelection() {
  var g = galleryState.grid;
  if (g && g.selection && typeof g.selection.clear === 'function') {
    try { g.selection.clear(); } catch (e) { /* ignore */ }
  }
}

function gallerySelectedIds() {
  return galleryState.selectionIds.slice();
}

function gallerySelectedItems() {
  var g = galleryState.grid;
  if (g && g.selection && typeof g.selection.items === 'function') {
    try { return g.selection.items() || []; } catch (e) { /* ignore */ }
  }
  return [];
}

/** Affiche/masque un bouton de la barre d'actions. */
function galleryShowButton(id, show) {
  var el = galleryById(id);
  if (!el) return;
  el.classList.toggle('hidden', !show);
}

/**
 * Met à jour la barre d'actions contextuelle (visible dès 1 item sélectionné).
 * En vue normale : Télécharger + Supprimer (corbeille). En vue corbeille :
 * Télécharger + Restaurer + Purger.
 */
function galleryUpdateActionBar() {
  var bar = galleryById('gallery-actions');
  if (!bar) return;
  var n = galleryState.selectionIds.length;
  if (n === 0) { bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');
  var label = galleryById('gallery-actions-label');
  if (label) label.textContent = n + ' média' + (n > 1 ? 's' : '') + ' sélectionné' + (n > 1 ? 's' : '');
  var trash = galleryState.view === 'trash';
  galleryShowButton('gallery-action-delete', !trash);
  galleryShowButton('gallery-action-restore', trash);
  galleryShowButton('gallery-action-purge', trash);
}

/** (Interne renderer) masque/affiche une action rapide selon le statut de l'item. */
function gallerySetActionVisibility(el, action, visible) {
  if (!el || !el._gActions) return;
  var btn = el._gActions.querySelector('[data-holaf-action="' + action + '"]');
  if (btn) btn.classList.toggle('is-hidden', !visible);
}

/** Désactive les boutons d'action pendant un appel réseau. */
function gallerySetBusy(flag) {
  galleryState.busy = !!flag;
  var buttons = document.querySelectorAll('[data-gallery-action]');
  for (var i = 0; i < buttons.length; i++) buttons[i].disabled = !!flag;
  var gridEl = galleryById('gallery-grid');
  if (gridEl) gridEl.setAttribute('aria-busy', flag ? 'true' : 'false');
}

/**
 * Appel API générique (JSON), en mémorisant le dernier appel pour les tests.
 * Rejette sur HTTP hors 2xx ou sur `{ error }`.
 */
function galleryApiRequest(method, path, body) {
  var url = galleryApiBase() + path;
  var opts = { method: method, credentials: 'same-origin' };
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  galleryState.lastRequest = { method: method, url: url, body: (body === undefined ? null : body) };
  return fetch(url, opts).then(function (res) {
    return galleryJson(res).then(function (data) {
      if (!res.ok || (data && data.error)) {
        throw new Error((data && data.error) || ('HTTP ' + res.status));
      }
      return data || {};
    });
  });
}

/** Thème des toasts aligné sur le mode clair/sombre de la page. */
function galleryToastTheme() {
  var dark = false;
  try {
    var root = document.documentElement;
    dark = root.classList.contains('dark') || root.getAttribute('data-theme') === 'dark';
  } catch (e) { /* ignore */ }
  return dark ? 'indigo-dark' : 'indigo-light';
}

/** Toast (brique HolafToast vendue). Sans brique : no-op silencieux. */
function galleryToast(message, type, opts) {
  var t = window.HolafToast;
  if (!t || typeof t.show !== 'function') return null;
  var o = { message: message, type: type || 'info', theme: galleryToastTheme() };
  if (opts) { for (var k in opts) { if (Object.prototype.hasOwnProperty.call(opts, k)) o[k] = opts[k]; } }
  try { return t.show(o); } catch (e) { return null; }
}

/**
 * Confirmation de destructif : réutilise `showConfirm` du front (app-admin.js)
 * si présent, sinon `window.confirm` (surchargeable en test). Le callback
 * reçoit `true` uniquement si l'utilisateur confirme.
 */
function galleryConfirm(title, message, cb) {
  if (typeof showConfirm === 'function') {
    showConfirm(title, message, function (ok) { cb(!!ok); });
    return;
  }
  var res = true;
  try { res = window.confirm(title + '\n\n' + message); } catch (e) { res = true; }
  cb(!!res);
}

function gallerySkippedSuffix(skipped) {
  if (!skipped) return '';
  return ' • ' + skipped + ' ignoré' + (skipped > 1 ? 's' : '');
}

/** Après un retrait local optimiste : compteur + re-rendu de la grille. */
function galleryAfterLocalRemoval() {
  galleryUpdateCount();
  if (galleryState.grid) galleryState.grid.render(true);
  if (galleryState.collection && galleryState.collection.total === 0) galleryShowEmpty();
}

/* ── Suppression / restauration / purge d'un item ────────────────────────── */

/**
 * Envoie UN média à la corbeille (DELETE /api/media/<id>).
 *
 * OPTIMISTE : l'item est retiré de la collection AVANT la réponse (retrait
 * local via la brique), puis ROLLBACK = reload complet si l'appel échoue.
 * Pas de confirmation : l'action est RÉVERSIBLE (le média part en corbeille).
 */
function galleryDeleteItem(item) {
  if (!item || galleryState.busy) return Promise.resolve(false);
  var id = item.id;
  var col = galleryState.collection;
  var removed = col ? col.removeByIds([id]) : false;
  var optimistic = (removed !== false);
  if (optimistic) galleryAfterLocalRemoval();

  gallerySetBusy(true);
  return galleryApiRequest('DELETE', '/media/' + encodeURIComponent(id))
    .then(function () {
      galleryToast('Média envoyé à la corbeille.', 'success');
      if (!optimistic) galleryReload(); // retrait local impossible → on resynchronise
      else galleryClearSelection();
      return true;
    })
    .catch(function (err) {
      if (optimistic) galleryReload(); // ROLLBACK : on recharge la vérité serveur
      galleryToast('Suppression impossible : ' + galleryErrorMessage(err), 'error');
      return false;
    })
    .then(function (r) { gallerySetBusy(false); return r; });
}

/** Restaure UN média corbeillé (POST /api/media/<id>/restore). */
function galleryRestoreItem(item) {
  if (!item || galleryState.busy) return Promise.resolve(false);
  var id = item.id;
  gallerySetBusy(true);
  return galleryApiRequest('POST', '/media/' + encodeURIComponent(id) + '/restore')
    .then(function () {
      galleryToast('Média restauré.', 'success');
      galleryReload();
      return true;
    })
    .catch(function (err) {
      galleryToast('Restauration impossible : ' + galleryErrorMessage(err), 'error');
      return false;
    })
    .then(function (r) { gallerySetBusy(false); return r; });
}

/** Purge DÉFINITIVEMENT UN média (DELETE /api/media/<id>/purge) — confirmation. */
function galleryPurgeItem(item) {
  if (!item || galleryState.busy) return Promise.resolve(false);
  var id = item.id;
  var name = item.filename || ('média #' + id);
  return new Promise(function (resolve) {
    galleryConfirm('Supprimer définitivement',
      'Supprimer définitivement « ' + name + ' » ? Cette action est IRRÉVERSIBLE.',
      function (ok) {
        if (!ok) { resolve(false); return; }
        gallerySetBusy(true);
        galleryApiRequest('DELETE', '/media/' + encodeURIComponent(id) + '/purge')
          .then(function () {
            galleryToast('Média supprimé définitivement.', 'success');
            galleryReload();
            resolve(true);
          })
          .catch(function (err) {
            galleryToast('Purge impossible : ' + galleryErrorMessage(err), 'error');
            resolve(false);
          })
          .then(function () { gallerySetBusy(false); });
      });
  });
}

/* ── Actions groupées ────────────────────────────────────────────────────── */

/**
 * Action groupée : POST /api/media/<op> { ids } → récap { n, skipped }.
 * `opts.message(n, skipped)` construit le libellé du toast ; `opts.after`
 * rafraîchit la liste. Toute erreur réseau est toastée et la liste n'est PAS
 * patchée (les actions groupées rechargent, elles ne sont pas optimistes).
 */
function galleryBulkRequest(method, path, ids, opts) {
  opts = opts || {};
  if (!ids || !ids.length || galleryState.busy) return Promise.resolve(false);
  gallerySetBusy(true);
  return galleryApiRequest(method, path, { ids: ids })
    .then(function (data) {
      var n = (data.trashed !== undefined) ? data.trashed
        : (data.restored !== undefined) ? data.restored
        : (data.purged !== undefined) ? data.purged : 0;
      var skipped = Array.isArray(data.skipped) ? data.skipped.length : 0;
      var msg = (typeof opts.message === 'function') ? opts.message(n, skipped)
        : (n + ' média' + (n > 1 ? 's' : '') + ' traité' + (n > 1 ? 's' : ''));
      galleryToast(msg, skipped ? 'warning' : 'success');
      if (typeof opts.after === 'function') opts.after(n, skipped);
      return true;
    })
    .catch(function (err) {
      galleryToast((opts.errorLabel || 'Opération') + ' impossible : ' + galleryErrorMessage(err), 'error');
      return false;
    })
    .then(function (r) { gallerySetBusy(false); return r; });
}

/** Suppression groupée (corbeille) : POST /api/media/delete { ids }. */
function galleryBulkDelete() {
  var ids = gallerySelectedIds();
  if (!ids.length) return Promise.resolve(false);
  var label = ids.length + ' média' + (ids.length > 1 ? 's' : '');
  return new Promise(function (resolve) {
    galleryConfirm('Mettre à la corbeille', 'Mettre ' + label + ' à la corbeille ?', function (ok) {
      if (!ok) { resolve(false); return; }
      resolve(galleryBulkRequest('POST', '/media/delete', ids, {
        errorLabel: 'Suppression',
        message: function (n, s) { return n + ' média' + (n > 1 ? 's' : '') + ' mis à la corbeille' + gallerySkippedSuffix(s); },
        after: function () { galleryReload(); },
      }));
    });
  });
}

/** Restauration groupée : POST /api/media/restore { ids } (pas de confirmation). */
function galleryBulkRestore() {
  var ids = gallerySelectedIds();
  return galleryBulkRequest('POST', '/media/restore', ids, {
    errorLabel: 'Restauration',
    message: function (n, s) { return n + ' média' + (n > 1 ? 's' : '') + ' restauré' + (n > 1 ? 's' : '') + gallerySkippedSuffix(s); },
    after: function () { galleryReload(); },
  });
}

/** Purge groupée : POST /api/media/purge { ids } — confirmation explicite. */
function galleryBulkPurge() {
  var ids = gallerySelectedIds();
  if (!ids.length) return Promise.resolve(false);
  var label = ids.length + ' média' + (ids.length > 1 ? 's' : '');
  return new Promise(function (resolve) {
    galleryConfirm('Supprimer définitivement',
      'Supprimer définitivement ' + label + ' ? Cette action est IRRÉVERSIBLE.',
      function (ok) {
        if (!ok) { resolve(false); return; }
        resolve(galleryBulkRequest('POST', '/media/purge', ids, {
          errorLabel: 'Purge',
          message: function (n, s) { return n + ' média' + (n > 1 ? 's' : '') + ' supprimé' + (n > 1 ? 's' : '') + ' définitivement' + gallerySkippedSuffix(s); },
          after: function () { galleryReload(); },
        }));
      });
  });
}

/* ── Téléchargement de l'original ────────────────────────────────────────── */

/**
 * Télécharge l'original d'UN média via l'ancre `download` (même origine).
 * Mémorise l'URL dans `state.lastDownload` (observable de test).
 */
function galleryDownloadItem(item) {
  if (!item) return null;
  var url = galleryDownloadUrl(item);
  galleryState.lastDownload = url;
  try {
    var a = document.createElement('a');
    a.href = url;
    a.download = item.filename || '';
    a.rel = 'noopener';
    a.style.display = 'none';
    if (document.body) document.body.appendChild(a);
    a.click();
    if (a.parentNode) a.parentNode.removeChild(a);
  } catch (e) { /* environnement sans DOM complet */ }
  return url;
}

/**
 * Téléchargement MULTI : le navigateur ne sait pas produire un zip sans
 * backend. On enchaîne les téléchargements (1er immédiat — geste utilisateur,
 * suivants décalés de 400 ms) pour ne pas se faire bloquer. Limite documentée :
 * certains navigateurs exigent un geste par fichier / peuvent en bloquer.
 * Mémorise la séquence dans `state.lastDownloadBatch` (observable de test).
 */
function galleryDownloadItems(items) {
  items = (items || []).filter(Boolean);
  var urls = [];
  for (var i = 0; i < items.length; i++) urls.push(galleryDownloadUrl(items[i]));
  galleryState.lastDownloadBatch = urls.slice();
  if (!items.length) return urls;
  galleryDownloadItem(items[0]);
  for (var j = 1; j < items.length; j++) {
    (function (it, delay) {
      setTimeout(function () { galleryDownloadItem(it); }, delay);
    })(items[j], j * 400);
  }
  return urls;
}

/** Bouton « Télécharger » de la barre d'actions (sélection courante). */
function galleryBulkDownload() {
  var items = gallerySelectedItems();
  if (!items.length) return [];
  var urls = galleryDownloadItems(items);
  galleryToast(items.length + ' téléchargement' + (items.length > 1 ? 's' : '') + ' lancé' + (items.length > 1 ? 's' : ''), 'info');
  return urls;
}

/* ── Taille d'affichage (slider) ─────────────────────────────────────────── */

function gallerySetDisplaySize(px) {
  var n = parseInt(px, 10);
  if (!isFinite(n)) n = GALLERY_DISPLAY_DEFAULT;
  n = Math.max(GALLERY_DISPLAY_MIN, Math.min(GALLERY_DISPLAY_MAX, n));
  galleryState.displaySize = n;
  try { localStorage.setItem(GALLERY_DISPLAY_KEY, String(n)); } catch (e) { /* ignore */ }

  var slider = galleryById('gallery-thumb-size');
  if (slider && slider.value !== String(n)) slider.value = String(n);
  galleryRenderSizeLabels();

  var server = galleryServerThumbSize(n);
  var serverChanged = server !== galleryState.serverSize;
  galleryState.serverSize = server;

  if (!galleryState.grid) return;
  if (serverChanged && galleryState.thumbCache) {
    // Les URL de vignettes dépendent de la taille : on vide le cache (clé = id,
    // pas id+taille) et on force une reconstruction des cellules.
    galleryState.thumbCache.clear();
    galleryState.grid.render(true);
  } else {
    galleryState.grid.relayout();
  }
}

function galleryRenderSizeLabels() {
  var valueEl = galleryById('gallery-thumb-size-value');
  if (valueEl) valueEl.textContent = galleryState.displaySize + ' px';
  var resEl = galleryById('gallery-thumb-resolution');
  if (resEl) resEl.textContent = 'vignettes ' + galleryServerThumbSize(galleryState.displaySize) + ' px';
}

/* ── Clavier global (grille + visionneuse) ───────────────────────────────── */

function galleryBindKeyHandler() {
  if (galleryKeyHandler) return;
  galleryKeyHandler = function (e) {
    if (!galleryState.started || galleryLocalMode()) return;
    if (galleryIsInputFocused(e)) return;
    var lb = galleryState.lightbox;
    if (lb && lb.isOpen()) {
      if (lb.handleKey(e)) e.preventDefault();
      return;
    }
    var grid = galleryState.grid;
    if (grid && grid.selection && grid.selection.handleKey(e)) e.preventDefault();
  };
  document.addEventListener('keydown', galleryKeyHandler);
}

/* ── États visuels ───────────────────────────────────────────────────────── */

function galleryShowLoading() {
  var el = galleryById('gallery-loading');
  if (el) el.classList.remove('hidden');
  galleryHide('gallery-empty');
  galleryHide('gallery-error');
}

function galleryShowEmpty() {
  galleryHide('gallery-loading');
  galleryHide('gallery-error');
  var el = galleryById('gallery-empty');
  if (el) el.classList.remove('hidden');
}

function galleryShowError(msg) {
  galleryHide('gallery-loading');
  galleryHide('gallery-empty');
  var el = galleryById('gallery-error');
  if (!el) return;
  el.textContent = msg || 'Erreur.';
  el.classList.remove('hidden');
}

function galleryHideStates() {
  galleryHide('gallery-loading');
  galleryHide('gallery-empty');
  galleryHide('gallery-error');
}

function galleryHide(id) {
  var el = galleryById(id);
  if (el) el.classList.add('hidden');
}

function galleryUpdateCount() {
  var el = galleryById('gallery-count');
  if (!el || !galleryState.collection) return;
  var total = galleryState.collection.total || 0;
  el.textContent = total + ' média' + (total > 1 ? 's' : '');
}

function galleryUpdateSelectionLabel(count) {
  var el = galleryById('gallery-selected');
  if (!el) return;
  if (count > 0) {
    el.textContent = count + ' sélectionné' + (count > 1 ? 's' : '');
    el.classList.remove('hidden');
  } else {
    el.textContent = '';
    el.classList.add('hidden');
  }
}

/* ── Résolution d'un item par index (pour la navigation visionneuse) ─────── */

function galleryEnsureItem(index) {
  var col = galleryState.collection;
  if (!col || index < 0 || index >= col.total) return Promise.resolve(null);
  var existing = col.at(index);
  if (existing) return Promise.resolve(existing);
  return new Promise(function (resolve) {
    var settled = false;
    var timer = null, offLoad = null, offErr = null;
    function finish() {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (offLoad) offLoad();
      if (offErr) offErr();
      resolve(col.at(index) || null);
    }
    timer = setTimeout(finish, 8000);
    offLoad = col.on('load', function () { if (col.at(index)) finish(); });
    offErr = col.on('error', finish);
    col.ensureIndex(index);
  });
}

/* ── Export (tests headless + accès interne) ─────────────────────────────── */

window.AppGallery = {
  start: galleryStart,
  stop: galleryStop,
  refresh: galleryRefresh,
  reload: galleryReload,
  setDisplaySize: gallerySetDisplaySize,
  serverThumbSize: galleryServerThumbSize,
  thumbUrl: galleryThumbUrl,
  thumbRetryUrl: galleryThumbRetryUrl,
  downloadUrl: galleryDownloadUrl,
  mediaUrl: galleryMediaUrl,
  infoFields: galleryInfoFields,
  infoBlocks: galleryInfoBlocks,
  previewFields: galleryPreviewFields,
  formatBytes: galleryFormatBytes,
  formatDate: galleryFormatDate,
  formatDuration: galleryFormatDuration,
  prettyJson: galleryPrettyJson,
  kindLabel: galleryKindLabel,
  kindIcon: galleryKindIcon,
  // GESTION
  setView: gallerySetView,
  resetFilters: galleryResetFilters,
  readFilters: galleryReadFilterInputs,
  clearSelection: galleryClearSelection,
  selectedIds: gallerySelectedIds,
  selectedItems: gallerySelectedItems,
  deleteItem: galleryDeleteItem,
  restoreItem: galleryRestoreItem,
  purgeItem: galleryPurgeItem,
  bulkDelete: galleryBulkDelete,
  bulkRestore: galleryBulkRestore,
  bulkPurge: galleryBulkPurge,
  bulkDownload: galleryBulkDownload,
  downloadItem: galleryDownloadItem,
  downloadItems: galleryDownloadItems,
  toast: galleryToast,
  constants: {
    PAGE_SIZE: GALLERY_PAGE_SIZE,
    PREFETCH: GALLERY_PREFETCH,
    DISPLAY_MIN: GALLERY_DISPLAY_MIN,
    DISPLAY_MAX: GALLERY_DISPLAY_MAX,
    DISPLAY_STEP: GALLERY_DISPLAY_STEP,
    DISPLAY_DEFAULT: GALLERY_DISPLAY_DEFAULT,
    THUMB_SIZES: GALLERY_THUMB_SIZES.slice(),
    SEARCH_DEBOUNCE: GALLERY_SEARCH_DEBOUNCE,
    THUMB_RETRY_MS: GALLERY_THUMB_RETRY_MS,
    SORTS: GALLERY_SORTS.slice(),
  },
  state: galleryState,
};
