/* ══════════════════════════════════════════════════════════════════════════
 * app-gallery.js — Onglet « Galerie » (version CONSULTATION) du front AI-Helper.
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

function galleryDownloadUrl(item) {
  return galleryApiBase() + '/media/' + encodeURIComponent(item.id) + '/download';
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
  loadedOnce: false,
};

var galleryKeyHandler = null;

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
  var col = galleryState.collection;
  if (!col) return;
  col.reset();
  galleryState.loadedOnce = false;
  galleryHideStates();
  galleryShowLoading();
  col.ensureRange(0, GALLERY_PAGE_SIZE - 1);
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
  var url = galleryApiBase() + '/media?page=' + page + '&limit=' + limit + '&sort=created_at_desc';
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

function galleryCellRenderer() {
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
      el._gImg = img;
      el._gPh = ph;
      el._gBadge = badge;
      el._gToken = 0;
      img.addEventListener('load', function () {
        el._gPh.classList.add('is-hidden');
        img.classList.remove('gallery-cell-img--hidden');
      });
      img.addEventListener('error', function () {
        el._gPh.textContent = '⚠';
        el._gPh.classList.remove('is-hidden');
        img.classList.add('gallery-cell-img--hidden');
      });
      return el;
    },

    update: function (el, item, ctx) {
      var token = ++el._gToken;
      el.dataset.mediaId = String(item.id);
      el._gBadge.textContent = galleryKindIcon(item.kind);
      el._gBadge.dataset.kind = item.kind || '';

      var img = el._gImg;
      var ph = el._gPh;

      // L'audio n'a pas de vignette produisible → icône seule.
      if (!item.thumb_available) {
        img.classList.add('gallery-cell-img--hidden');
        img.removeAttribute('src');
        ph.textContent = galleryKindIcon(item.kind);
        ph.classList.remove('is-hidden');
        return;
      }

      ph.textContent = '';
      ph.classList.remove('is-hidden');
      img.classList.add('gallery-cell-img--hidden');

      // Le cache 'url' est synchrone en pratique (aucun fetch de la brique).
      var cache = galleryState.thumbCache;
      var cached = cache.peek(item.id);
      if (cached) {
        img.src = cached;
        return;
      }
      cache.request(item, window.HolafThumbCache.PRIORITY_HIGH).then(function (url) {
        if (el._gToken !== token || !url) return; // cellule recyclée entre-temps
        img.src = url;
      }).catch(function () {
        if (el._gToken !== token) return;
        ph.textContent = '⚠';
        ph.classList.remove('is-hidden');
      });
    },

    release: function (el) {
      el._gToken++;
      if (el._gImg) { el._gImg.removeAttribute('src'); el._gImg.classList.add('gallery-cell-img--hidden'); }
      if (el._gPh) { el._gPh.textContent = ''; el._gPh.classList.add('is-hidden'); }
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
  lb.on('navigate', function (p) { if (p && typeof p.index === 'number') galleryState.lightboxIndex = p.index; });
  lb.on('open', function (p) { if (p && p.item) galleryState.lightboxIndex = galleryState.lightboxIndex; });
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
  setDisplaySize: gallerySetDisplaySize,
  serverThumbSize: galleryServerThumbSize,
  thumbUrl: galleryThumbUrl,
  downloadUrl: galleryDownloadUrl,
  infoFields: galleryInfoFields,
  infoBlocks: galleryInfoBlocks,
  previewFields: galleryPreviewFields,
  formatBytes: galleryFormatBytes,
  formatDate: galleryFormatDate,
  formatDuration: galleryFormatDuration,
  prettyJson: galleryPrettyJson,
  kindLabel: galleryKindLabel,
  kindIcon: galleryKindIcon,
  constants: {
    PAGE_SIZE: GALLERY_PAGE_SIZE,
    PREFETCH: GALLERY_PREFETCH,
    DISPLAY_MIN: GALLERY_DISPLAY_MIN,
    DISPLAY_MAX: GALLERY_DISPLAY_MAX,
    DISPLAY_STEP: GALLERY_DISPLAY_STEP,
    DISPLAY_DEFAULT: GALLERY_DISPLAY_DEFAULT,
    THUMB_SIZES: GALLERY_THUMB_SIZES.slice(),
  },
  state: galleryState,
};
