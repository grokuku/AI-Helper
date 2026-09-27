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

// Rafraîchissement AUTOMATIQUE de la galerie (ms). Poll léger : on interroge
// seulement la tête de liste (page 1, limit 1) et on relance la liste
// uniquement si le média le plus récent ou le total a changé. Détecte les
// médias poussés par l'extérieur (node ComfyUI, autre onglet) sans action
// manuelle. Assez long pour ne pas matraquer le serveur.
var GALLERY_POLL_MS = 15000;

// Reprise UNIQUE et bornée d'une vignette après un échec de chargement <img>
// (un <img> en erreur ne se recharge pas tout seul, la cellule resterait
// bloquée sur l'état d'erreur). Délai court (ms) : laisse passer un aléa
// réseau transitoire, puis on renonce et on affiche l'état d'erreur.
var GALLERY_THUMB_RETRY_MS = 1500;

// Délai MAXIMAL d'attente d'une vignette avant de basculer sur un état
// d'erreur EXPLICITE. Un <img> dont la requête reste bloquée (réseau figé,
// serveur/proxy bloqué, stockage qui ne répond pas) ne déclenche NI `load`
// NI `error` : sans cette borne, la cellule resterait sur un placeholder
// neutre — visuellement « vide » et impossible à diagnostiquer.
// Surchargeable via `AppGallery.state.thumbPendingMs` (tests headless).
var GALLERY_THUMB_PENDING_MS = 20000;

// Glyphes des états de vignette : VISIBLES et DISTINCTS les uns des autres.
//   ⏳ = en attente de chargement ; ⚠ = échec de chargement ;
//   🚫 = aucune vignette produisible (décision backend).
// Le glyphe de TYPE (🖼️/▶/🎵) reste réservé au badge et à l'audio.
var GALLERY_PH_PENDING = '⏳';
var GALLERY_PH_ERROR = '⚠';
var GALLERY_PH_UNAVAILABLE = '🚫';

// Valeurs de tri exposées par le backend (GET /api/media?sort=).
var GALLERY_SORTS = ['created_at_desc', 'created_at_asc', 'name_asc', 'size_desc'];

// Longueur maximale d'un tag (miroir de TAG_MAX_LEN côté backend).
var GALLERY_TAG_MAX_LEN = 50;

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
 * @param {object} filters { kind, subfolder, subfolders, q, from, to, status, favorite, tags }
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
  if (filters.subfolders && filters.subfolders.length) {
    for (var i = 0; i < filters.subfolders.length; i++) {
      parts.push('subfolders=' + encodeURIComponent(filters.subfolders[i]));
    }
  }
  if (filters.q) parts.push('q=' + encodeURIComponent(filters.q));
  if (filters.from) parts.push('from=' + encodeURIComponent(filters.from));
  if (filters.to) parts.push('to=' + encodeURIComponent(filters.to));
  if (filters.status) parts.push('status=' + encodeURIComponent(filters.status));
  if (filters.favorite) parts.push('favorite=' + encodeURIComponent(filters.favorite));
  // Filtre MULTI-tags : paramètre RÉPÉTÉ (sémantique OU côté backend).
  if (filters.tags && filters.tags.length) {
    for (var t = 0; t < filters.tags.length; t++) {
      parts.push('tags=' + encodeURIComponent(filters.tags[t]));
    }
  }
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
 * HTML des chips de tags (rendu par la brique infoPane via `html: true` et
 * `field.raw`). Chaque chip porte un bouton croix (attribut
 * `data-gallery-tag-remove`) ; un champ d'ajout est TOUJOURS présent.
 *
 * L'`innerHTML` reçoit du contenu ÉCHAPPÉ (`galleryEsc`) : aucun tag ne peut
 * injecter de HTML. La source (`manual`/`ai`) pilote une classe distincte —
 * le terrain est prêt pour l'auto-tagging IA (chips IA visuellement différenciées).
 */
function galleryTagsFieldHtml(detail) {
  detail = Array.isArray(detail) ? detail : [];
  var html = '<span class="gallery-tags">';
  for (var i = 0; i < detail.length; i++) {
    var t = detail[i] || {};
    var name = (t.tag === undefined || t.tag === null) ? '' : String(t.tag);
    var src = (t.source === 'ai') ? 'ai' : 'manual';
    html += '<span class="gallery-tag-chip gallery-tag-chip--' + src + '" data-gallery-tag-source="' + src + '">'
      + '<span class="gallery-tag-name">' + galleryEsc(name) + '</span>'
      + '<button type="button" class="gallery-tag-remove" data-gallery-tag-remove="' + galleryEsc(name) + '"'
      + ' title="Retirer ce tag" aria-label="Retirer le tag ' + galleryEsc(name) + '">&#215;</button>'
      + '</span>';
  }
  html += '</span>';
  html += '<input type="text" class="gallery-tag-input" maxlength="' + GALLERY_TAG_MAX_LEN + '"'
    + ' placeholder="Ajouter un tag…" aria-label="Ajouter un tag (valider par Entrée)">';
  return html;
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
    { label: 'Favori', value: data.favorite ? '★ Oui' : '' },
    // Tags : chips interactives (ajout/retrait) — rendu HTML dédié.
    { label: 'Tags', stacked: true, raw: galleryTagsFieldHtml(data.tags_detail), value: '' },
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
    { label: 'Favori', value: item.favorite ? '★ Oui' : '' },
    { label: 'Tags', stacked: true, raw: galleryTagsFieldHtml(item.tags_detail), value: '' },
    { label: 'Ajouté le', value: galleryFormatDate(item.created_at) },
  ];
}

/**
 * Focus dans un champ de SAISIE ? Le clavier global doit l'ignorer.
 * Les CONTRÔLES de choix (checkbox/radio/button) ne sont PAS de la saisie :
 * les flèches doivent continuer à naviguer dans la grille quand la case à
 * cocher a le focus (l'hôte neutralise Spécifiquement « Espace » plus bas
 * pour ne pas doubler l'activation native du navigateur).
 */
function galleryIsInputFocused(e) {
  var t = e && e.target;
  if (!t || !t.tagName) return false;
  var tag = t.tagName.toLowerCase();
  if (tag === 'textarea' || tag === 'select' || t.isContentEditable === true) return true;
  if (tag === 'input') {
    var type = String(t.type || 'text').toLowerCase();
    return type !== 'checkbox' && type !== 'radio' && type !== 'button';
  }
  return false;
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
  // Filtre DOSSIERS : dossiers sélectionnés (multi, OU) + contrôleur de modale.
  selectedFolders: [],
  foldersModal: null,
  // Filtre TAGS : tags sélectionnés (multi, OU) + contrôleur de modale.
  selectedTags: [],
  tagsModal: null,
  // Filtre FAVORIS : bascule simple (true = n'afficher que les favoris).
  favoriteOnly: false,
  // Verrou d'actions pendant un appel réseau (désactive les boutons).
  busy: false,
  // Rafraîchissement automatique : timer du poll + listener de visibilité.
  pollTimer: null,
  visibilityHandler: null,
  // Observables de test : dernier appel réseau et derniers téléchargements.
  lastRequest: null,
  lastDownload: null,
  lastDownloadBatch: [],
  // Observable de test : dernier changement de tags d'un média (add/remove).
  lastTagsRequest: null,
  // Presets compatibles vision (cache de session) pour l'auto-tag IA.
  visionPresets: null,
  // Demande d'annulation de la boucle d'auto-tag (lue entre deux médias).
  autoTagCancel: false,
  // Observable de test : dernier lot d'auto-tag lancé (ids + preset_id).
  lastAutoTagRequest: null,
  // Borne d'attente d'une vignette (surchargeable en test).
  thumbPendingMs: GALLERY_THUMB_PENDING_MS,
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
  galleryStartPolling();
}

function galleryStop() {
  // Ferme la visionneuse si elle est ouverte (l'onglet quitté ne doit pas
  // garder un overlay plein écran). Les données sont conservées.
  if (galleryState.lightbox && galleryState.lightbox.isOpen()) {
    try { galleryState.lightbox.close(); } catch (e) { /* ignore */ }
  }
  galleryStopPolling();
}

/* ── Rafraîchissement automatique (poll léger + retour d'onglet) ─────────── */

/**
 * Démarre le rafraîchissement automatique : un poll périodique ET un
 * rafraîchissement quand l'onglet redevient visible (retour du navigateur).
 * Idempotent : un 2e appel ne crée pas de doublon (listener + timer uniques).
 */
function galleryStartPolling() {
  galleryStopPolling();
  if (typeof document !== 'undefined' && !galleryState.visibilityHandler) {
    galleryState.visibilityHandler = function () {
      if (!document.hidden) galleryPollNow();
    };
    document.addEventListener('visibilitychange', galleryState.visibilityHandler);
  }
  galleryPollSchedule();
}

/** Arrête le poll et retire le listener de visibilité. */
function galleryStopPolling() {
  if (galleryState.pollTimer) {
    clearTimeout(galleryState.pollTimer);
    galleryState.pollTimer = null;
  }
  if (galleryState.visibilityHandler && typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', galleryState.visibilityHandler);
    galleryState.visibilityHandler = null;
  }
}

/** (Re)programme le prochain tick de poll. */
function galleryPollSchedule() {
  if (!galleryState.started) return;
  galleryState.pollTimer = setTimeout(galleryPollTick, GALLERY_POLL_MS);
  // En Node (tests headless), setTimeout renvoie un objet Timeout : unref()
  // évite de maintenir la boucle d'événements vivante. En navigateur, c'est
  // un nombre (pas de unref) → aucun effet.
  if (galleryState.pollTimer && typeof galleryState.pollTimer.unref === 'function') {
    galleryState.pollTimer.unref();
  }
}

function galleryPollTick() {
  galleryState.pollTimer = null;
  galleryPollCheck();
  galleryPollSchedule();
}

/** Rafraîchissement immédiat (retour d'onglet), si la page est visible. */
function galleryPollNow() {
  if (typeof document !== 'undefined' && document.hidden) return;
  galleryPollCheck();
}

/**
 * Vérifie si la liste doit être rechargée, SANS perturber l'utilisateur :
 * on ne touche à rien pendant une action réseau, une sélection multiple, la
 * visionneuse ouverte, ou un scroll profond (on ne « saute » pas en tête).
 */
function galleryPollCheck() {
  if (!galleryState.started) return;
  if (galleryState.busy) return;
  if (galleryState.lightbox && galleryState.lightbox.isOpen()) return;
  if (gallerySelectedIds().length) return;
  var col = galleryState.collection;
  if (!col) return;
  var gridEl = galleryById('gallery-grid');
  if (gridEl && gridEl.scrollTop > 0) return; // l'utilisateur lit la suite
  galleryPollFetchTop(col);
}

/**
 * Interroge la tête de liste (page 1, limit 1) et relance la liste seulement
 * si le média le plus récent ou le total a changé. Silencieux en cas d'erreur
 * réseau (le prochain tick réessaiera).
 */
function galleryPollFetchTop(col) {
  var url = galleryMediaUrl(1, 1, galleryReadFilterInputs(), galleryState.sort);
  return fetch(url, { credentials: 'same-origin' }).then(function (res) {
    if (!res.ok) return null;
    return galleryJson(res).then(function (data) {
      var items = (data && Array.isArray(data.items)) ? data.items : [];
      var topId = items.length ? items[0].id : null;
      var total = (data && typeof data.total === 'number') ? data.total : null;
      var first = (col.length > 0) ? col.at(0) : null;
      var curTop = (first && first.id != null) ? first.id : null;
      var changed = false;
      if (total !== null && col.total !== total) changed = true;
      if (topId !== null && curTop !== null && topId !== curTop) changed = true;
      if (changed) galleryReload();
      return data;
    });
  }).catch(function () { return null; });
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
    // `html: true` autorise le rendu `field.raw` en innerHTML : SEUL le champ
    // « Tags » l'utilise (contenu ÉCHAPPÉ par `galleryEsc`), les autres
    // champs restent en textContent (comportement inchangé).
    html: true,
    actions: [
      {
        id: 'download',
        label: 'Télécharger',
        isEnabled: function (item) { return !!item; },
        run: function (item) { galleryDownloadItem(item); },
      },
      {
        id: 'favorite',
        label: '★ Favori',
        isEnabled: function (item) { return !!item; },
        run: function (item) {
          return galleryToggleFavorite(item).then(function () {
            // Re-rend le panneau pour refléter le champ « Favori » à jour.
            if (galleryState.infoPane && typeof galleryState.infoPane.show === 'function') {
              galleryState.infoPane.show(item);
            }
          });
        },
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
  // Clic dans la ZONE VIDE de la grille → désélection (correctif UX : la
  // brique ne consomme que les clics sur une cellule, l'hôte peut traiter le
  // fond sans conflit).
  gridEl.addEventListener('click', galleryOnGridClick);
  galleryBindCollectionEvents();
  galleryBindKeyHandler();
  galleryBindLightboxEvents();
  galleryBindFilterControls();
  galleryUpdateViewToggle();
  galleryUpdateActionBar();
  galleryUpdateFoldersButton();
  galleryUpdateTagsButton();
  galleryBindInfoPaneEvents();

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

/* États VISIBLES et DISTINCTS d'une vignette de cellule :
     - ⏳ `--pending`     : chargement en attente (placeholder ACTIF, non vide) ;
     - ⚠ `--error`       : échec de chargement (HTTP non-2xx, onerror, délai) ;
     - 🚫 `--unavailable` : aucune vignette produisible (`thumb_available=false`).
   Objectif : ne JAMAIS laisser une cellule muette/vide — un échec doit être
   lisible au premier coup d'œil, sans ouvrir la console. */
function galleryCellSetPh(el, glyph, stateClass, title) {
  var ph = el._gPh;
  if (!ph) return;
  ph.textContent = glyph;
  ph.classList.remove('is-hidden');
  ph.classList.remove('gallery-cell-ph--error', 'gallery-cell-ph--pending', 'gallery-cell-ph--unavailable');
  if (stateClass) ph.classList.add(stateClass);
  if (title) ph.title = title; else ph.removeAttribute('title');
}

function galleryCellShowThumbPending(el) {
  galleryCellSetPh(el, GALLERY_PH_PENDING, 'gallery-cell-ph--pending',
    'Chargement de la vignette…');
}

/* État d'ERREUR : distinct de l'attente et de l'indisponibilité. */
function galleryCellShowThumbError(el, url) {
  galleryCellClearThumbTimer(el);
  if (el._gImg) el._gImg.classList.add('gallery-cell-img--hidden');
  galleryCellSetPh(el, GALLERY_PH_ERROR, 'gallery-cell-ph--error',
    'Échec du chargement de la vignette');
  if (url && galleryThumbDebugEnabled()) galleryThumbProbe(url);
}

/* État d'INDISPONIBILITÉ : aucune vignette produisible dans cet environnement
   (décision backend `thumb_available=false`), distinct d'un échec réseau. */
function galleryCellShowThumbUnavailable(el, kind) {
  galleryCellClearThumbTimer(el);
  if (el._gImg) el._gImg.classList.add('gallery-cell-img--hidden');
  var title = 'Aucune vignette disponible pour ce média';
  if (kind && kind !== 'audio') title += ' (outil backend absent : Pillow/ffmpeg ?)';
  galleryCellSetPh(el, GALLERY_PH_UNAVAILABLE, 'gallery-cell-ph--unavailable', title);
}

/* Retire tout marqueur d'état du placeholder (icône de type normale, release). */
function galleryCellResetPh(el) {
  var ph = el._gPh;
  if (!ph) return;
  ph.classList.remove('gallery-cell-ph--error', 'gallery-cell-ph--pending', 'gallery-cell-ph--unavailable');
  ph.removeAttribute('title');
}

/* Borne d'attente par cellule (nettoyée au load/error/release). */
function galleryCellClearThumbTimer(el) {
  if (el._gPendingTimer) {
    clearTimeout(el._gPendingTimer);
    el._gPendingTimer = null;
  }
}

function galleryCellStartThumbTimer(el, item) {
  galleryCellClearThumbTimer(el);
  var ms = Number(galleryState.thumbPendingMs);
  if (!isFinite(ms) || ms <= 0) return; // borne désactivée (0 = pas de timeout)
  el._gPendingTimer = setTimeout(function () {
    el._gPendingTimer = null;
    // Ne bascule que si la cellule attend encore (sinon load/error a déjà statué).
    var ph = el._gPh;
    if (!ph || !ph.classList.contains('gallery-cell-ph--pending')) return;
    galleryCellShowThumbError(el,
      el._gExpectedUrl || galleryThumbUrl(item, galleryState.serverSize));
  }, ms);
  // En Node (tests headless), le timer est un objet Timeout : unref() évite de
  // maintenir la boucle d'événements vivante. En navigateur : un nombre.
  if (el._gPendingTimer && typeof el._gPendingTimer.unref === 'function') {
    el._gPendingTimer.unref();
  }
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
      // `eager` : la grille est VIRTUALISÉE (seules les cellules proches de la
      // vue existent dans le DOM) → le lazy n'apporte aucun gain et, combiné au
      // masquage `display:none` jusqu'au load, risque de ne jamais déclencher le
      // chargement sur certains moteurs (cellule vide, sans load ni error).
      img.loading = 'eager';
      img.draggable = false;
      el.appendChild(img);
      var ph = document.createElement('div');
      ph.className = 'gallery-cell-ph';
      el.appendChild(ph);
      var badge = document.createElement('span');
      badge.className = 'gallery-cell-badge';
      el.appendChild(badge);
      // CASE À COCHER de sélection (correction UX : remplace l'ancienne icône
      // en haut à gauche). La brique traite tout `<input>` cliqué comme un
      // TOGGLE unitaire (elle ne touche pas aux autres) et synchronise elle-
      // même `checked` sur l'état de sélection — aucun besoin de la modifier.
      var check = document.createElement('input');
      check.type = 'checkbox';
      check.className = 'gallery-cell-check';
      check.title = 'Sélectionner ce média (Ctrl+clic : ajouter à la sélection)';
      check.setAttribute('aria-label', 'Sélectionner ce média');
      el.appendChild(check);
      var trash = document.createElement('span');
      trash.className = 'gallery-cell-trash is-hidden';
      trash.textContent = '🗑';
      el.appendChild(trash);
      // ÉTOILE de FAVORI (« à exposer ») : bouton marqué `data-holaf-action` →
      // la brique émet onAction() SANS changer la sélection ni ouvrir la
      // visionneuse. Toujours visible quand le média est favori, sinon au
      // survol (comme la case à cocher) ; posée en bas-droite (créneau libre,
      // pas de chevauchement avec case/badge/actions).
      var fav = document.createElement('button');
      fav.type = 'button';
      fav.className = 'gallery-cell-fav';
      fav.dataset.holafAction = 'favorite';
      fav.textContent = '☆';
      fav.title = 'Marquer comme favori';
      fav.setAttribute('aria-label', 'Marquer comme favori');
      el.appendChild(fav);
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
      el._gCheck = check;
      el._gTrash = trash;
      el._gFav = fav;
      el._gActions = actions;
      el._gToken = 0;
      // Reprise de vignette : URL attendue + drapeau « déjà retenté » (borné).
      el._gExpectedUrl = '';
      el._gThumbRetried = false;
      el._gPendingTimer = null;
      img.addEventListener('load', function () {
        galleryCellClearThumbTimer(el);
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
      // Case à cocher : état reflété (la brique le re-synchronise aussi après
      // update/sélection, mais on le pose ici pour rester correct même isolé).
      if (el._gCheck) {
        el._gCheck.checked = !!(ctx && ctx.selected);
        el._gCheck.setAttribute('aria-label',
          'Sélectionner ' + (item.filename || 'ce média'));
      }
      // Étoile de favori : étoile pleine + classe d'état quand le média est
      // favori (l'état visuel de la cellule est posé plus bas).
      if (el._gFav) {
        var isFav = !!item.favorite;
        el._gFav.classList.toggle('is-fav', isFav);
        el._gFav.textContent = isFav ? '★' : '☆';
        el._gFav.title = isFav ? 'Retirer des favoris' : 'Marquer comme favori';
        el._gFav.setAttribute('aria-label',
          isFav ? 'Retirer des favoris' : 'Marquer comme favori');
      }
      el.classList.toggle('gallery-cell--favorite', !!item.favorite);

      // UI distincte en corbeille : marqueur + opacité + actions Restaurer/Purger.
      var trashed = galleryIsTrashed(item);
      el.classList.toggle('gallery-cell--trashed', trashed);
      el._gTrash.classList.toggle('is-hidden', !trashed);
      gallerySetActionVisibility(el, 'delete', !trashed);
      gallerySetActionVisibility(el, 'restore', trashed);
      gallerySetActionVisibility(el, 'purge', trashed);

      var img = el._gImg;

      // Pas de vignette produisible (décision backend) : l'audio affiche SON
      // icône de type (état normal) ; pour image/vidéo c'est une DÉGRADATION
      // (outil backend absent : Pillow/ffmpeg) → indicateur EXPLICITE distinct
      // de l'échec de chargement, pour qu'une cellule ne reste jamais muette.
      if (!item.thumb_available) {
        el._gExpectedUrl = '';
        el._gThumbRetried = false;
        galleryCellClearThumbTimer(el);
        img.classList.add('gallery-cell-img--hidden');
        img.removeAttribute('src');
        if (item.kind === 'audio') {
          galleryCellSetPh(el, galleryKindIcon(item.kind), null, null);
        } else {
          galleryCellShowThumbUnavailable(el, item.kind);
          if (galleryThumbDebugEnabled()) {
            console.warn('[gallery] thumb_available=false (média ' + item.kind + ', id=' + item.id + ')');
          }
        }
        return;
      }

      galleryCellShowThumbPending(el);
      img.classList.add('gallery-cell-img--hidden');

      // Borne d'attente : un chargement qui ne se conclut jamais (ni load ni
      // error) devient un ÉTAT D'ERREUR visible au lieu d'une cellule vide.
      galleryCellStartThumbTimer(el, item);

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
      if (el._gCheck) el._gCheck.checked = false;
      if (el._gFav) { el._gFav.classList.remove('is-fav'); el._gFav.textContent = '☆'; }
      el.classList.remove('gallery-cell--favorite');
      galleryCellClearThumbTimer(el);
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

/**
 * Clic dans la ZONE VIDE de la grille (fond, padding, zone sous les
 * vignettes) → vide la sélection et masque la barre d'actions.
 *
 * La brique HolafGrid ne consomme QUE les clics sur une cellule
 * (`[data-holaf-index]`) : un clic sur le fond ne fait rien chez elle, l'hôte
 * peut donc le traiter sans conflit. On ignore les clics sur une cellule, une
 * action rapide ou la case à cocher (gérés par la brique/l'hôte), les clics
 * non primaires, et un clic dans la barre de défilement du conteneur.
 */
function galleryOnGridClick(e) {
  if (!e) return;
  if (typeof e.button === 'number' && e.button !== 0) return; // clic droit/molette
  var target = e.target;
  if (!target || typeof target.closest !== 'function') return;
  // Ni cellule (même en squelette de chargement), ni action rapide, ni case.
  if (target.closest('.holaf-grid-cell, [data-holaf-index], [data-holaf-action], input, button')) return;
  var gridEl = galleryById('gallery-grid');
  // Clic sur la barre de défilement (bord droit du conteneur) : pas « le vide ».
  if (gridEl && target === gridEl && typeof e.offsetX === 'number'
      && gridEl.clientWidth > 0 && e.offsetX >= gridEl.clientWidth) return;
  if (!gallerySelectedIds().length) return; // rien à vider → aucun travail inutile
  galleryClearSelection();
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
  else if (actionId === 'favorite') galleryToggleFavorite(item);
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

/**
 * Index (dans la collection) de l'item AFFICHÉ par la visionneuse. En temps
 * normal, l'index de l'hôte (`lightboxIndex`, mis à jour par open/navigate)
 * est la référence ; s'il est incohérent (ex. openZoom direct sans passer par
 * la grille), on retrouve l'item parmi les items CHARGÉS de la collection.
 */
function galleryLightboxIndexOf(item) {
  var index = galleryState.lightboxIndex;
  var col = galleryState.collection;
  if (!col || !item) return index;
  var at = (index >= 0 && index < (col.total || 0)) ? col.at(index) : null;
  if (at && String(at.id) === String(item.id)) return index;
  var found = -1;
  col.forEachLoaded(function (it, i) {
    if (found < 0 && it && String(it.id) === String(item.id)) found = i;
  });
  return found >= 0 ? found : index;
}

/**
 * Touche « Suppr » en plein écran : envoie l'item AFFICHÉ par la visionneuse à
 * la corbeille (suppression DOUCE, réversible → donc SANS confirmation, comme
 * la suppression unitaire de la grille), puis enchaîne sur l'image SUIVANTE.
 *
 * Choix documentés :
 *   - la cible est l'item COURANT de la visionneuse, jamais la sélection ;
 *   - si l'item supprimé était le DERNIER, on recule sur le précédent ;
 *   - si c'était le SEUL média, la visionneuse se ferme proprement ;
 *   - en cas d'échec : toast d'erreur (par galleryDeleteItem) et AUCUNE
 *     navigation — la visionneuse reste sur l'image courante ;
 *   - la touche est gérée par l'HÔTE (la brique lightbox ne connaît pas
 *     « Suppr » et n'est PAS modifiée). L'auto-répétition est ignorée : pas de
 *     suppressions en chaîne si la touche reste enfoncée.
 */
function galleryLightboxDeleteCurrent() {
  var lb = galleryState.lightbox;
  if (!lb || !lb.isOpen()) return Promise.resolve(false);
  var item = galleryState.lightboxItem ||
    ((typeof lb.current === 'function') ? lb.current() : null);
  if (!item || galleryState.busy) return Promise.resolve(false);
  var col = galleryState.collection;
  var totalBefore = col ? (col.total || 0) : 0;
  // Index cible APRÈS retrait : les items suivants glissent d'un cran, l'index
  // courant pointe donc naturellement le suivant ; si l'on supprimait le
  // dernier, l'index serait hors bornes → on recule d'un cran.
  var index = galleryLightboxIndexOf(item);
  var target;
  if (index < 0) target = 0;
  else if (index >= totalBefore - 1) target = index - 1;
  else target = index;
  return galleryDeleteItem(item).then(function (ok) {
    if (!ok) return false; // échec : toast déjà affiché, on NE navigue pas
    galleryLightboxResyncAfterRemoval(target);
    return true;
  });
}

/**
 * Re-synchronise la visionneuse après le retrait de l'item courant : re-rend
 * l'item de l'index cible via l'API PUBLIQUE de la brique (`navigate(0)` relit
 * notre `getIndex()` et re-rend la vue), ou ferme la visionneuse s'il ne reste
 * plus rien. Aucune brique modifiée.
 */
function galleryLightboxResyncAfterRemoval(target) {
  var lb = galleryState.lightbox;
  if (!lb || !lb.isOpen()) return;
  var col = galleryState.collection;
  var total = col ? (col.total || 0) : 0;
  if (!total) {
    galleryState.lightboxIndex = -1;
    galleryState.lightboxItem = null;
    try { lb.close(); } catch (e) { /* ignore */ }
    return;
  }
  if (target < 0) target = 0;
  if (target > total - 1) target = total - 1;
  galleryState.lightboxItem = null;
  galleryState.lightboxIndex = target;
  try {
    var r = lb.navigate(0); // dir 0 = « reste sur l'index courant, re-rend »
    if (r && typeof r.catch === 'function') r.catch(function () { /* ignore */ });
  } catch (e) { /* ignore */ }
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
  var q = galleryInputValue('gallery-search');
  var from = galleryInputValue('gallery-filter-from');
  var to = galleryInputValue('gallery-filter-to');
  var sort = galleryInputValue('gallery-filter-sort');
  galleryState.sort = sort || 'created_at_desc';

  var filters = {};
  if (kind) filters.kind = kind;
  // Dossiers sélectionnés via la MODALE (multi, sémantique OU).
  var folders = galleryState.selectedFolders || [];
  if (folders.length) filters.subfolders = folders.slice();
  if (q) filters.q = q;
  if (from) filters.from = from;
  if (to) filters.to = to;
  if (galleryState.view === 'trash') filters.status = 'trashed';
  // Filtre FAVORIS (bascule toolbar) : « 1 » = n'afficher que les favoris.
  if (galleryState.favoriteOnly) filters.favorite = '1';
  // Filtre TAGS (modale, multi-sélection, sémantique OU).
  var tags = galleryState.selectedTags || [];
  if (tags.length) filters.tags = tags.slice();
  galleryState.filters = filters;
  return filters;
}

/** Un changement de filtre = RELOAD COMPLET de la liste (reset + page 1). */
function galleryApplyFilterChange() {
  galleryReload();
}

/** Branche les champs de filtre (selects/dates : 'change', nom : 'input'). */
function galleryBindFilterControls() {
  var selectIds = [
    'gallery-filter-kind', 'gallery-filter-sort',
    'gallery-filter-from', 'gallery-filter-to',
  ];
  for (var i = 0; i < selectIds.length; i++) {
    var el = galleryById(selectIds[i]);
    if (el) el.addEventListener('change', galleryApplyFilterChange);
  }
  var foldersBtn = galleryById('gallery-filter-folders');
  if (foldersBtn) foldersBtn.addEventListener('click', galleryOpenFoldersModal);
  var favBtn = galleryById('gallery-filter-favorite');
  if (favBtn) favBtn.addEventListener('click', galleryToggleFavoriteFilter);
  var tagsBtn = galleryById('gallery-filter-tags');
  if (tagsBtn) tagsBtn.addEventListener('click', galleryOpenTagsModal);
  var search = galleryById('gallery-search');
  if (search) {
    search.addEventListener('input', function () {
      if (gallerySearchTimer) clearTimeout(gallerySearchTimer);
      gallerySearchTimer = setTimeout(galleryApplyFilterChange, GALLERY_SEARCH_DEBOUNCE);
    });
  }
}

/* ── Filtre DOSSIERS : bouton + modale (liste + comptes) ─────────────────── */

/** Libellé d'un dossier (chaîne vide = racine). */
function galleryFolderLabel(subfolder) {
  return subfolder ? subfolder : '(sans dossier)';
}

/** URL de GET /api/media/folders (statut aligné sur la vue courante). */
function galleryFoldersUrl() {
  var base = galleryApiBase() + '/media/folders';
  return (galleryState.view === 'trash') ? (base + '?status=trashed') : base;
}

/** Récupère les sous-dossiers ({subfolder,count}) depuis le backend. */
function galleryFetchFolders() {
  return fetch(galleryFoldersUrl(), { credentials: 'same-origin' }).then(function (res) {
    return galleryJson(res).then(function (data) {
      if (!res.ok || !data || data.error) {
        throw new Error((data && data.error) || ('HTTP ' + res.status));
      }
      return Array.isArray(data.folders) ? data.folders : [];
    });
  });
}

/** Met à jour le libellé du bouton selon la sélection courante. */
function galleryUpdateFoldersButton() {
  var btn = galleryById('gallery-filter-folders');
  if (!btn) return;
  var n = (galleryState.selectedFolders || []).length;
  if (n > 0) {
    btn.textContent = 'Dossiers (' + n + ')';
    btn.title = n + ' dossier(s) sélectionné(s)';
    btn.setAttribute('aria-label', 'Filtrer par dossiers : ' + n + ' sélectionné(s)');
  } else {
    btn.textContent = 'Dossiers : tous';
    btn.title = 'Tous les dossiers';
    btn.setAttribute('aria-label', 'Filtrer par dossiers : tous');
  }
  btn.classList.toggle('is-active', n > 0);
}

/** Applique une sélection de dossiers (liste) : état + bouton + RELOAD complet. */
function galleryApplyFolders(list) {
  var clean = [];
  var seen = {};
  (list || []).forEach(function (sf) {
    var v = (sf === undefined || sf === null) ? '' : String(sf);
    if (!Object.prototype.hasOwnProperty.call(seen, v)) { seen[v] = true; clean.push(v); }
  });
  clean.sort();
  galleryState.selectedFolders = clean;
  galleryUpdateFoldersButton();
  return galleryReload();
}

/**
 * Filtre FAVORIS (bascule simple) : n'affiche QUE les médias favoris.
 * L'état vit dans `galleryState.favoriteOnly` et se traduit par `favorite=1`
 * dans la requête ; la bascule relance la liste (reset + page 1).
 */
function galleryToggleFavoriteFilter() {
  galleryState.favoriteOnly = !galleryState.favoriteOnly;
  galleryUpdateFavoriteButton();
  return galleryReload();
}

/** Synchronise l'apparence du bouton « Favoris » (actif/inactif). */
function galleryUpdateFavoriteButton() {
  var btn = galleryById('gallery-filter-favorite');
  if (!btn) return;
  var on = !!galleryState.favoriteOnly;
  btn.classList.toggle('is-active', on);
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  btn.title = on ? 'Afficher tous les médias' : 'Afficher uniquement les favoris';
}

/**
 * Ouvre la MODALE de sélection des dossiers (déplaçable + redimensionnable via
 * la brique holaf-modal : options `draggable`/`resizable`). La liste et les
 * comptes viennent de GET /api/media/folders (PAS des items chargés).
 */
function galleryOpenFoldersModal() {
  if (!window.HolafModal || typeof window.HolafModal.open !== 'function') {
    galleryToast('Modale indisponible (vendor/holaf).', 'error');
    return null;
  }
  if (galleryState.foldersModal) {
    try { galleryState.foldersModal.close(); } catch (e) { /* ignore */ }
    galleryState.foldersModal = null;
  }

  var selected = {};
  (galleryState.selectedFolders || []).forEach(function (sf) { selected[sf] = true; });
  var folders = [];

  var wrap = document.createElement('div');
  wrap.className = 'gallery-folders';

  var bar = document.createElement('div');
  bar.className = 'gallery-folders-bar';
  var search = document.createElement('input');
  search.type = 'search';
  search.className = 'gallery-folders-search';
  search.placeholder = 'Rechercher un dossier…';
  search.setAttribute('aria-label', 'Rechercher un dossier');
  bar.appendChild(search);
  var actions = document.createElement('div');
  actions.className = 'gallery-folders-actions';
  function mkAction(label, fn) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'gallery-folders-action';
    b.textContent = label;
    b.addEventListener('click', fn);
    return b;
  }
  actions.appendChild(mkAction('Tout', function () { setAll(true); }));
  actions.appendChild(mkAction('Aucun', function () { setAll(false); }));
  actions.appendChild(mkAction('Inverser', function () { invertAll(); }));
  bar.appendChild(actions);
  wrap.appendChild(bar);

  var status = document.createElement('div');
  status.className = 'gallery-folders-status';
  status.setAttribute('role', 'status');
  wrap.appendChild(status);

  var list = document.createElement('div');
  list.className = 'gallery-folders-list';
  wrap.appendChild(list);

  function updateStatus() {
    var n = Object.keys(selected).length;
    status.textContent = n === 0 ? 'Tous les dossiers' : (n + ' dossier(s) sélectionné(s)');
  }

  function render() {
    while (list.firstChild) list.removeChild(list.firstChild);
    var q = (search.value || '').trim().toLowerCase();
    var shown = folders.filter(function (f) {
      return !q || galleryFolderLabel(f.subfolder).toLowerCase().indexOf(q) !== -1;
    });
    if (shown.length === 0) {
      var empty = document.createElement('div');
      empty.className = 'gallery-folders-empty';
      empty.textContent = folders.length === 0 ? 'Aucun dossier.' : 'Aucun résultat.';
      list.appendChild(empty);
    }
    shown.forEach(function (f) {
      var row = document.createElement('label');
      row.className = 'gallery-folder-item';
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'gallery-folder-check';
      cb.value = f.subfolder;
      cb.checked = !!selected[f.subfolder];
      cb.addEventListener('change', function () {
        if (cb.checked) selected[f.subfolder] = true;
        else delete selected[f.subfolder];
        updateStatus();
      });
      var name = document.createElement('span');
      name.className = 'gallery-folder-name';
      name.textContent = galleryFolderLabel(f.subfolder);
      var count = document.createElement('span');
      count.className = 'gallery-folder-count';
      count.textContent = '(' + f.count + ')';
      row.appendChild(cb);
      row.appendChild(name);
      row.appendChild(count);
      list.appendChild(row);
    });
    updateStatus();
  }

  function setAll(on) {
    folders.forEach(function (f) { if (on) selected[f.subfolder] = true; else delete selected[f.subfolder]; });
    render();
  }
  function invertAll() {
    folders.forEach(function (f) {
      if (selected[f.subfolder]) delete selected[f.subfolder];
      else selected[f.subfolder] = true;
    });
    render();
  }
  search.addEventListener('input', render);

  var ctrl = window.HolafModal.open({
    id: 'gallery-folders-modal',
    title: 'Filtrer par dossiers',
    size: 'md',
    width: 420,
    draggable: true,
    resizable: true,
    minWidth: 320,
    minHeight: 240,
    storageKey: 'gallery-folders-modal',
    content: wrap,
    buttons: [
      { text: 'Annuler', value: false, type: 'cancel' },
      {
        text: 'Appliquer', value: true, type: 'primary', autoFocus: true,
        onClick: function () { galleryApplyFolders(Object.keys(selected)); },
      },
    ],
    onClose: function () { galleryState.foldersModal = null; },
  });
  galleryState.foldersModal = ctrl;

  ctrl.setBusy(true, 'Chargement des dossiers…');
  galleryFetchFolders().then(function (rows) {
    folders = rows;
    render();
    ctrl.setBusy(false);
  }).catch(function (err) {
    folders = [];
    render();
    status.textContent = 'Erreur de chargement : ' + ((err && err.message) ? err.message : err);
    ctrl.setBusy(false);
  });
  return ctrl;
}

/* ── Filtre TAGS : bouton + modale (liste + comptes) ─────────────────────── */

/** Libellé d'un tag (jamais vide côté backend, défensif ici). */
function galleryTagLabel(tag) {
  return tag ? String(tag) : '(sans nom)';
}

/** URL de GET /api/media/tags (statut aligné sur la vue courante). */
function galleryTagsUrl() {
  var base = galleryApiBase() + '/media/tags';
  return (galleryState.view === 'trash') ? (base + '?status=trashed') : base;
}

/** Récupère les tags ({tag,count}) depuis le backend. */
function galleryFetchTags() {
  return fetch(galleryTagsUrl(), { credentials: 'same-origin' }).then(function (res) {
    return galleryJson(res).then(function (data) {
      if (!res.ok || !data || data.error) {
        throw new Error((data && data.error) || ('HTTP ' + res.status));
      }
      return Array.isArray(data.tags) ? data.tags : [];
    });
  });
}

/** Met à jour le libellé du bouton selon la sélection courante. */
function galleryUpdateTagsButton() {
  var btn = galleryById('gallery-filter-tags');
  if (!btn) return;
  var n = (galleryState.selectedTags || []).length;
  if (n > 0) {
    btn.textContent = 'Tags (' + n + ')';
    btn.title = n + ' tag(s) sélectionné(s)';
    btn.setAttribute('aria-label', 'Filtrer par tags : ' + n + ' sélectionné(s)');
  } else {
    btn.textContent = 'Tags : tous';
    btn.title = 'Tous les tags';
    btn.setAttribute('aria-label', 'Filtrer par tags : tous');
  }
  btn.classList.toggle('is-active', n > 0);
}

/** Applique une sélection de tags (liste) : état + bouton + RELOAD complet. */
function galleryApplyTags(list) {
  var clean = [];
  var seen = {};
  (list || []).forEach(function (tag) {
    var v = (tag === undefined || tag === null) ? '' : String(tag);
    var key = v.toLowerCase();
    if (v && !Object.prototype.hasOwnProperty.call(seen, key)) { seen[key] = true; clean.push(v); }
  });
  clean.sort();
  galleryState.selectedTags = clean;
  galleryUpdateTagsButton();
  return galleryReload();
}

/**
 * Ouvre la MODALE de filtrage par tags (déplaçable + redimensionnable via la
 * brique holaf-modal). Liste et comptes viennent de GET /api/media/tags.
 * Réutilise le markup/CSS de la modale de dossiers (.gallery-folders*).
 */
function galleryOpenTagsModal() {
  if (!window.HolafModal || typeof window.HolafModal.open !== 'function') {
    galleryToast('Modale indisponible (vendor/holaf).', 'error');
    return null;
  }
  if (galleryState.tagsModal) {
    try { galleryState.tagsModal.close(); } catch (e) { /* ignore */ }
    galleryState.tagsModal = null;
  }

  var selected = {};
  (galleryState.selectedTags || []).forEach(function (tag) { selected[tag] = true; });
  var tags = [];

  var wrap = document.createElement('div');
  wrap.className = 'gallery-folders';

  var bar = document.createElement('div');
  bar.className = 'gallery-folders-bar';
  var search = document.createElement('input');
  search.type = 'search';
  search.className = 'gallery-folders-search';
  search.placeholder = 'Rechercher un tag…';
  search.setAttribute('aria-label', 'Rechercher un tag');
  bar.appendChild(search);
  var actions = document.createElement('div');
  actions.className = 'gallery-folders-actions';
  function mkAction(label, fn) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'gallery-folders-action';
    b.textContent = label;
    b.addEventListener('click', fn);
    return b;
  }
  actions.appendChild(mkAction('Tout', function () { setAll(true); }));
  actions.appendChild(mkAction('Aucun', function () { setAll(false); }));
  actions.appendChild(mkAction('Inverser', function () { invertAll(); }));
  bar.appendChild(actions);
  wrap.appendChild(bar);

  var status = document.createElement('div');
  status.className = 'gallery-folders-status';
  status.setAttribute('role', 'status');
  wrap.appendChild(status);

  var list = document.createElement('div');
  list.className = 'gallery-folders-list';
  wrap.appendChild(list);

  function updateStatus() {
    var n = Object.keys(selected).length;
    status.textContent = n === 0 ? 'Tous les tags' : (n + ' tag(s) sélectionné(s)');
  }

  function render() {
    while (list.firstChild) list.removeChild(list.firstChild);
    var q = (search.value || '').trim().toLowerCase();
    var shown = tags.filter(function (f) {
      return !q || galleryTagLabel(f.tag).toLowerCase().indexOf(q) !== -1;
    });
    if (shown.length === 0) {
      var empty = document.createElement('div');
      empty.className = 'gallery-folders-empty';
      empty.textContent = tags.length === 0 ? 'Aucun tag.' : 'Aucun résultat.';
      list.appendChild(empty);
    }
    shown.forEach(function (f) {
      var row = document.createElement('label');
      row.className = 'gallery-folder-item';
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'gallery-folder-check';
      cb.value = f.tag;
      cb.checked = !!selected[f.tag];
      cb.addEventListener('change', function () {
        if (cb.checked) selected[f.tag] = true;
        else delete selected[f.tag];
        updateStatus();
      });
      var name = document.createElement('span');
      name.className = 'gallery-folder-name gallery-tag-name-cell';
      name.textContent = galleryTagLabel(f.tag);
      var count = document.createElement('span');
      count.className = 'gallery-folder-count';
      count.textContent = '(' + f.count + ')';
      row.appendChild(cb);
      row.appendChild(name);
      row.appendChild(count);
      list.appendChild(row);
    });
    updateStatus();
  }

  function setAll(on) {
    tags.forEach(function (f) { if (on) selected[f.tag] = true; else delete selected[f.tag]; });
    render();
  }
  function invertAll() {
    tags.forEach(function (f) {
      if (selected[f.tag]) delete selected[f.tag];
      else selected[f.tag] = true;
    });
    render();
  }
  search.addEventListener('input', render);

  var ctrl = window.HolafModal.open({
    id: 'gallery-tags-modal',
    title: 'Filtrer par tags',
    size: 'md',
    width: 420,
    draggable: true,
    resizable: true,
    minWidth: 320,
    minHeight: 240,
    storageKey: 'gallery-tags-modal',
    content: wrap,
    buttons: [
      { text: 'Annuler', value: false, type: 'cancel' },
      {
        text: 'Appliquer', value: true, type: 'primary', autoFocus: true,
        onClick: function () { galleryApplyTags(Object.keys(selected)); },
      },
    ],
    onClose: function () { galleryState.tagsModal = null; },
  });
  galleryState.tagsModal = ctrl;

  ctrl.setBusy(true, 'Chargement des tags…');
  galleryFetchTags().then(function (rows) {
    tags = rows;
    render();
    ctrl.setBusy(false);
  }).catch(function (err) {
    tags = [];
    render();
    status.textContent = 'Erreur de chargement : ' + ((err && err.message) ? err.message : err);
    ctrl.setBusy(false);
  });
  return ctrl;
}

/** Réinitialise tous les filtres + tri et relance la liste. */
function galleryResetFilters() {
  var ids = ['gallery-filter-kind', 'gallery-search', 'gallery-filter-from', 'gallery-filter-to'];
  for (var i = 0; i < ids.length; i++) {
    var el = galleryById(ids[i]);
    if (el) el.value = '';
  }
  var sort = galleryById('gallery-filter-sort');
  if (sort) sort.value = 'created_at_desc';
  galleryState.sort = 'created_at_desc';
  galleryState.selectedFolders = [];
  galleryUpdateFoldersButton();
  galleryState.selectedTags = [];
  galleryUpdateTagsButton();
  galleryState.favoriteOnly = false;
  galleryUpdateFavoriteButton();
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
  galleryShowButton('gallery-action-favorite', !trash);
  // Le tag est disponible dans les DEUX vues (une corbeille conserve ses tags).
  galleryShowButton('gallery-action-tags', true);
  galleryShowButton('gallery-action-restore', trash);
  galleryShowButton('gallery-action-purge', trash);
  // Auto-tag IA : uniquement hors corbeille (on ne tagge pas la corbeille).
  galleryShowButton('gallery-action-auto-tag', !trash);
  var presetSel = galleryById('gallery-auto-tag-preset');
  if (presetSel) presetSel.classList.toggle('hidden', trash);
  if (!trash) {
    // Presets vision mis en cache (pas de refetch à chaque clic de sélection).
    galleryEnsureVisionPresets(false).then(galleryPopulateAutoTagSelectFrom);
  }
  // Libellé du bouton favori selon l'état de la sélection (marquer/démarquer).
  var favBtn = galleryById('gallery-action-favorite');
  if (favBtn && !trash) {
    var items = gallerySelectedItems();
    var allFav = items.length > 0 && items.every(function (it) { return !!it.favorite; });
    favBtn.textContent = allFav ? '★ Retirer favori' : '★ Favori';
    favBtn.title = allFav ? 'Retirer des favoris' : 'Marquer comme favori';
  }
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

/** Re-rend UNE cellule (item modifié sur place : bascule favori optimiste). */
function galleryRefreshItem(item) {
  var g = galleryState.grid;
  if (!g || !item || typeof g.refresh !== 'function') return;
  try { g.refresh(item.id); } catch (e) { /* ignore */ }
}

/** Le média doit-il sortir de la vue courante après ce changement de favori ? */
function galleryFavoriteFilterMismatch(isFav) {
  return !!galleryState.favoriteOnly && !isFav;
}

/**
 * Bascule le FAVORI d'UN média (POST /api/media/<id>/favorite).
 *
 * OPTIMISTE : le drapeau est inversé en mémoire puis la cellule re-rendue
 * immédiatement ; ROLLBACK = restauration de l'ancien drapeau + re-rendu si
 * l'appel échoue (comme la suppression). Ne touche NI à la sélection NI à la
 * visionneuse (l'action arrive par `data-holaf-action`). Si un filtre
 * « Favoris » est actif et que le média cesse d'être favori, un reload complet
 * le retire de la vue (la vérité serveur est rechargée).
 */
function galleryToggleFavorite(item) {
  if (!item || galleryState.busy) return Promise.resolve(false);
  var next = !item.favorite;
  var prev = !!item.favorite;
  item.favorite = next;
  galleryRefreshItem(item);
  gallerySetBusy(true);
  return galleryApiRequest('POST', '/media/' + encodeURIComponent(item.id) + '/favorite', { favorite: next })
    .then(function () {
      galleryToast(next ? 'Ajouté aux favoris.' : 'Retiré des favoris.', 'success');
      if (galleryFavoriteFilterMismatch(next)) galleryReload();
      return true;
    })
    .catch(function (err) {
      item.favorite = prev; // ROLLBACK
      galleryRefreshItem(item);
      galleryToast('Favori impossible : ' + galleryErrorMessage(err), 'error');
      return false;
    })
    .then(function (r) { gallerySetBusy(false); return r; });
}

/* ── Tags d'un média : chips du panneau d'infos ──────────────────────────── */

/**
 * Normalise une saisie de tag CÔTÉ CLIENT (miroir léger de `_normalize_tag`
 * backend) : trim + espaces multiples réduits, borne 1..50. Le backend RESTE
 * l'autorité (un tag refusé par le serveur produit un toast d'erreur).
 *
 * @returns {string} le tag normalisé, ou '' si invalide.
 */
function galleryNormalizeTagInput(raw) {
  if (raw === undefined || raw === null) return '';
  var tag = String(raw).replace(/\s+/g, ' ').trim();
  if (!tag || tag.length > GALLERY_TAG_MAX_LEN) return '';
  // Caractères manifestement interdits (la virgule est le séparateur du filtre).
  if (/[,;<>"\\\x00-\x1f]/.test(tag)) return '';
  return tag;
}

/**
 * Branche les ÉVÉNEMENTS des chips de tags du panneau d'infos par DÉLÉGATION
 * sur la racine de la brique (survit aux re-rendus internes de l'infoPane) :
 *   - clic sur `.gallery-tag-remove` → retrait du tag ;
 *   - Entrée dans `.gallery-tag-input` → ajout du tag.
 * L'item courant est lu via `pane.current()` (jamais capturé dans une closure
 * obsolète). Idempotent (marqueur `_gTagsBound`).
 */
function galleryBindInfoPaneEvents() {
  var pane = galleryState.infoPane;
  if (!pane || typeof pane.element !== 'function') return;
  var root = pane.element();
  if (!root || root._gTagsBound) return;
  root._gTagsBound = true;
  root.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || typeof t.closest !== 'function') return;
    var btn = t.closest('[data-gallery-tag-remove]');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    var item = pane.current();
    if (item) galleryRemoveTag(item, btn.getAttribute('data-gallery-tag-remove'));
  });
  root.addEventListener('keydown', function (e) {
    var t = e.target;
    if (!t || !t.classList || !t.classList.contains('gallery-tag-input')) return;
    if (e.key !== 'Enter') return;
    e.preventDefault();
    var item = pane.current();
    if (item) galleryAddTag(item, t.value);
  });
}

/** Un filtre TAGS est-il actif ? (une édition de tags doit rafraîchir la vue). */
function galleryTagsFilterMismatch() {
  return (galleryState.selectedTags || []).length > 0;
}

/**
 * Applique un ajout/retrait de tags sur UN média (POST /api/media/<id>/tags).
 * Met à jour l'item EN MÉMOIRE (liste) et rafraîchit le panneau d'infos à
 * partir de la réponse serveur. Si un filtre tags est actif, recharge la vue.
 * Non optimiste (la vérité serveur est reflétée) ; verrouille pendant l'appel.
 */
function galleryApplyItemTags(item, add, remove) {
  add = add || [];
  remove = remove || [];
  if (!item || galleryState.busy) return Promise.resolve(false);
  if (!add.length && !remove.length) return Promise.resolve(false);
  galleryState.lastTagsRequest = { id: item.id, add: add.slice(), remove: remove.slice() };
  gallerySetBusy(true);
  return galleryApiRequest('POST', '/media/' + encodeURIComponent(item.id) + '/tags', {
      add: add, remove: remove,
    })
    .then(function (data) {
      item.tags = Array.isArray(data.tags) ? data.tags : [];
      item.tags_detail = Array.isArray(data.tags_detail) ? data.tags_detail : [];
      var pane = galleryState.infoPane;
      if (pane && typeof pane.show === 'function' && pane.current() && pane.current().id === item.id) {
        pane.show(item);
      }
      var parts = [];
      if (add.length) parts.push(add.length + ' tag' + (add.length > 1 ? 's' : '') + ' ajouté' + (add.length > 1 ? 's' : ''));
      if (remove.length) parts.push(remove.length + ' tag' + (remove.length > 1 ? 's' : '') + ' retiré' + (remove.length > 1 ? 's' : ''));
      galleryToast(parts.join(', ') + '.', 'success');
      if (galleryTagsFilterMismatch()) galleryReload();
      return true;
    })
    .catch(function (err) {
      galleryToast('Tags impossibles : ' + galleryErrorMessage(err), 'error');
      return false;
    })
    .then(function (r) { gallerySetBusy(false); return r; });
}

/** Retire UN tag d'un média (croix sur la chip). */
function galleryRemoveTag(item, tag) {
  if (!tag) return Promise.resolve(false);
  return galleryApplyItemTags(item, [], [tag]);
}

/** Ajoute UN tag à un média (champ + Entrée). */
function galleryAddTag(item, raw) {
  var tag = galleryNormalizeTagInput(raw);
  if (!tag) {
    galleryToast('Tag invalide (1..' + GALLERY_TAG_MAX_LEN + ' caractères, sans virgule).', 'error');
    return Promise.resolve(false);
  }
  return galleryApplyItemTags(item, [tag], []);
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
  var payload = { ids: ids };
  if (opts.body && typeof opts.body === 'object') {
    for (var k in opts.body) {
      if (Object.prototype.hasOwnProperty.call(opts.body, k)) payload[k] = opts.body[k];
    }
  }
  return galleryApiRequest(method, path, payload)
    .then(function (data) {
      var n = (data.trashed !== undefined) ? data.trashed
        : (data.restored !== undefined) ? data.restored
        : (data.updated !== undefined) ? data.updated
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

/**
 * Favori GROUPÉ : POST /api/media/favorite { ids, favorite } → récap updated.
 * Cible calculée sur la sélection : si TOUS les sélectionnés sont déjà favoris
 * → on démarque ; sinon on marque (bouton unique marquer/démarquer).
 */
function galleryBulkFavorite() {
  var ids = gallerySelectedIds();
  if (!ids.length) return Promise.resolve(false);
  var items = gallerySelectedItems();
  var allFav = items.length > 0 && items.every(function (it) { return !!it.favorite; });
  var target = !allFav;
  return galleryBulkRequest('POST', '/media/favorite', ids, {
    body: { favorite: target },
    errorLabel: target ? 'Ajout aux favoris' : 'Retrait des favoris',
    message: function (done, s) {
      return done + ' média' + (done > 1 ? 's' : '') + ' '
        + (target ? 'ajouté' : 'retiré') + (done > 1 ? 's' : '') + ' '
        + (target ? 'aux favoris' : 'des favoris') + gallerySkippedSuffix(s);
    },
    after: function () { galleryReload(); },
  });
}

/**
 * Tags GROUPÉS : ouvre une petite modale (tag à ajouter/retirer) puis
 * POST /api/media/tags ``{ids, add|remove:[tag]}`` → récap ``{updated, skipped}``.
 * Utile dès maintenant et INDISPENSABLE pour l'auto-tagging IA à venir
 * (poser un lot de tags sur une sélection).
 */
function galleryBulkTags() {
  var ids = gallerySelectedIds();
  if (!ids.length) return Promise.resolve(false);
  if (!window.HolafModal || typeof window.HolafModal.open !== 'function') {
    galleryToast('Modale indisponible (vendor/holaf).', 'error');
    return Promise.resolve(false);
  }

  var wrap = document.createElement('div');
  wrap.className = 'gallery-tags-action';
  var hint = document.createElement('p');
  hint.className = 'gallery-tags-action-hint';
  hint.textContent = 'Appliquer un tag aux ' + ids.length + ' média'
    + (ids.length > 1 ? 's' : '') + ' sélectionné' + (ids.length > 1 ? 's' : '') + ' :';
  wrap.appendChild(hint);
  var input = document.createElement('input');
  input.type = 'text';
  input.className = 'gallery-tags-action-input';
  input.maxLength = GALLERY_TAG_MAX_LEN;
  input.placeholder = 'Nom du tag…';
  input.setAttribute('aria-label', 'Nom du tag à appliquer');
  wrap.appendChild(input);

  function run(mode) {
    var tag = galleryNormalizeTagInput(input.value);
    if (!tag) {
      galleryToast('Tag invalide (1..' + GALLERY_TAG_MAX_LEN + ' caractères, sans virgule).', 'error');
      return false; // garde la modale ouverte pour corriger
    }
    var removing = (mode === 'remove');
    galleryBulkRequest('POST', '/media/tags', ids, {
      body: removing ? { remove: [tag] } : { add: [tag] },
      errorLabel: removing ? 'Retrait du tag' : 'Ajout du tag',
      message: function (n, s) {
        return n + ' média' + (n > 1 ? 's' : '') + ' « ' + tag + ' » '
          + (removing ? 'retiré' : 'ajouté') + (n > 1 ? 's' : '') + gallerySkippedSuffix(s);
      },
      after: function () { galleryReload(); },
    });
    return true;
  }

  var ctrl = window.HolafModal.open({
    id: 'gallery-tags-action-modal',
    title: 'Tag des médias sélectionnés',
    size: 'sm',
    width: 380,
    draggable: true,
    resizable: true,
    minWidth: 300,
    minHeight: 170,
    storageKey: 'gallery-tags-action-modal',
    content: wrap,
    buttons: [
      { text: 'Annuler', value: false, type: 'cancel' },
      { text: 'Retirer', value: 'remove', onClick: function () { return run('remove'); } },
      { text: 'Ajouter', value: 'add', type: 'primary', autoFocus: true, onClick: function () { return run('add'); } },
    ],
  });
  // Le champ est l'action principale : on lui donne le focus d'entrée.
  setTimeout(function () { try { input.focus(); } catch (e) { /* ignore */ } }, 0);
  return Promise.resolve(ctrl);
}

/* ── Auto-tagging IA (vision) ────────────────────────────────────────────── */

/**
 * Presets marqués « compatible vision » (champ `supports_vision`). On ne
 * propose QUE ceux-là : un preset classique n'accepte pas d'image.
 */
function galleryVisionPresets(presets) {
  return (presets || []).filter(function (p) { return !!(p && p.supports_vision); });
}

/** GET /api/presets → liste brute (rejette si indisponible). */
function galleryFetchPresets() {
  return fetch(galleryApiBase() + '/presets', { credentials: 'same-origin' }).then(function (res) {
    return galleryJson(res).then(function (data) {
      if (!res.ok || !Array.isArray(data)) {
        throw new Error((data && data.error) || ('HTTP ' + res.status));
      }
      return data;
    });
  });
}

/**
 * Presets vision disponibles (cache de session). `force` refait l'appel (ex.
 * l'utilisateur vient d'activer « compatible vision » dans Paramètres).
 */
function galleryEnsureVisionPresets(force) {
  if (!force && galleryState.visionPresets) return Promise.resolve(galleryState.visionPresets);
  return galleryFetchPresets().then(function (presets) {
    galleryState.visionPresets = galleryVisionPresets(presets);
    return galleryState.visionPresets;
  }).catch(function () {
    if (!galleryState.visionPresets) galleryState.visionPresets = [];
    return galleryState.visionPresets;
  });
}

/** Remplit le sélecteur de preset vision (défaut = premier ; conserve le choix). */
function galleryPopulateAutoTagSelectFrom(vision) {
  var sel = galleryById('gallery-auto-tag-preset');
  if (!sel) return;
  vision = vision || [];
  var prev = sel.value;
  while (sel.firstChild) sel.removeChild(sel.firstChild);
  vision.forEach(function (p) {
    var opt = document.createElement('option');
    opt.value = String(p.id);
    opt.textContent = p.name || ('Preset ' + p.id);
    sel.appendChild(opt);
  });
  if (!vision.length) {
    var emptyOpt = document.createElement('option');
    emptyOpt.value = '';
    emptyOpt.textContent = 'Aucun preset vision';
    sel.appendChild(emptyOpt);
  }
  var hasPrev = vision.some(function (p) { return String(p.id) === prev; });
  sel.value = hasPrev ? prev : (vision.length ? String(vision[0].id) : '');
}

/** Affiche la progression « n/total » pendant/après la boucle d'auto-tag. */
function galleryUpdateAutoTagProgress(done, total, finished) {
  var el = galleryById('gallery-autotag-progress');
  if (!el) return;
  if (!total) { el.classList.add('hidden'); el.textContent = ''; return; }
  el.classList.remove('hidden');
  el.textContent = finished
    ? 'Auto-tag : ' + done + '/' + total + ' traité' + (done > 1 ? 's' : '')
    : 'Auto-tag en cours… ' + done + '/' + total;
}

/** Bascule l'état « exécution auto-tag » (bouton Annuler + verrou du sélecteur). */
function gallerySetAutoTagRunning(on) {
  var cancel = galleryById('gallery-action-autotag-cancel');
  if (cancel) cancel.classList.toggle('hidden', !on);
  var btn = galleryById('gallery-action-auto-tag');
  if (btn) btn.disabled = !!on;
  var sel = galleryById('gallery-auto-tag-preset');
  if (sel) sel.disabled = !!on;
}

/** Demande l'annulation : la boucle s'arrête entre deux médias. */
function galleryCancelAutoTag() {
  galleryState.autoTagCancel = true;
}

/**
 * Boucle d'auto-tag sur la SÉLECTION : UN appel par média
 * (POST /api/media/<id>/auto-tag {preset_id}) → progression + ANNULATION.
 * Récap final (toast) distinguant succès / ignorés / erreurs, puis reload.
 */
function galleryRunAutoTag(ids, presetId) {
  if (!ids || !ids.length || galleryState.busy) return Promise.resolve(false);
  var total = ids.length;
  var done = 0, tagged = 0, skipped = 0, errors = 0, addedTags = 0;
  galleryState.autoTagCancel = false;
  galleryState.lastAutoTagRequest = { ids: ids.slice(), preset_id: presetId };
  gallerySetAutoTagRunning(true);
  gallerySetBusy(true);
  galleryUpdateAutoTagProgress(0, total);
  return new Promise(function (resolve) {
    function finish(cancelled) {
      galleryState.autoTagCancel = false;
      gallerySetAutoTagRunning(false);
      gallerySetBusy(false);
      galleryUpdateAutoTagProgress(done, total, true);
      var msg = tagged + ' média' + (tagged > 1 ? 's' : '') + ' auto-taggé' + (tagged > 1 ? 's' : '')
        + ' (' + addedTags + ' tag' + (addedTags > 1 ? 's' : '') + ')';
      if (skipped) msg += ' • ' + skipped + ' ignoré' + (skipped > 1 ? 's' : '');
      if (errors) msg += ' • ' + errors + ' erreur' + (errors > 1 ? 's' : '');
      if (cancelled) msg += ' • annulé';
      galleryToast(msg, errors ? 'warning' : 'success');
      galleryReload();
      resolve(true);
    }
    function step() {
      if (galleryState.autoTagCancel) { finish(true); return; }
      if (done >= total) { finish(false); return; }
      var id = ids[done];
      galleryApiRequest('POST', '/media/' + encodeURIComponent(id) + '/auto-tag', { preset_id: presetId })
        .then(function (data) {
          var at = (data && data.auto_tag) || {};
          if (at.status === 'tagged') { tagged++; addedTags += (at.added || 0); }
          else { skipped++; }
        })
        .catch(function () { errors++; })
        .then(function () {
          done++;
          galleryUpdateAutoTagProgress(done, total);
          step();
        });
    }
    step();
  });
}

/**
 * Action « 🤖 Auto-tag (IA) » : cible la sélection avec le preset vision choisi.
 * Les presets sont RECHARGÉS à chaque clic (l'utilisateur peut avoir activé/
 * retiré « compatible vision » dans Paramètres entre-temps). Sans preset vision
 * configuré → message actionnable (aucun appel).
 */
function galleryBulkAutoTag() {
  var ids = gallerySelectedIds();
  if (!ids.length) return Promise.resolve(false);
  return galleryEnsureVisionPresets(true).then(function (vision) {
    galleryPopulateAutoTagSelectFrom(vision);
    var sel = galleryById('gallery-auto-tag-preset');
    if (!vision.length || !sel || !sel.value) {
      galleryToast('Aucun preset compatible vision. Coche « Compatible vision » sur un preset dans Paramètres > Provider LLM.', 'warning');
      return false;
    }
    return galleryRunAutoTag(ids, parseInt(sel.value, 10));
  });
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
    var t = e && e.target;
    // Espace sur la CASE À COCHER focalisée : le navigateur gère nativement le
    // toggle (il émet un click → la brique met la sélection à jour). Ne pas le
    // traiter ici, sinon double toggle (clavier hôte + activation native).
    if (e.key === ' ' && t && t.classList && t.classList.contains('gallery-cell-check')) return;
    var lb = galleryState.lightbox;
    if (lb && lb.isOpen()) {
      // « Suppr » = corbeille de l'item AFFICHÉ. Touche gérée par l'hôte : la
      // brique lightbox ne la connaît pas (on ne la modifie pas).
      if (e.key === 'Delete' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        if (!e.repeat) galleryLightboxDeleteCurrent();
        return;
      }
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
  pollNow: galleryPollNow,
  pollStop: galleryStopPolling,
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
  // FILTRE DOSSIERS (bouton + modale multi-dossiers)
  openFoldersModal: galleryOpenFoldersModal,
  applyFolders: galleryApplyFolders,
  fetchFolders: galleryFetchFolders,
  foldersUrl: galleryFoldersUrl,
  updateFoldersButton: galleryUpdateFoldersButton,
  folderLabel: galleryFolderLabel,
  // FILTRE TAGS (bouton + modale multi-tags)
  openTagsModal: galleryOpenTagsModal,
  applyTags: galleryApplyTags,
  fetchTags: galleryFetchTags,
  tagsUrl: galleryTagsUrl,
  updateTagsButton: galleryUpdateTagsButton,
  tagLabel: galleryTagLabel,
  // TAGS d'un média (chips du panneau d'infos)
  tagsFieldHtml: galleryTagsFieldHtml,
  normalizeTagInput: galleryNormalizeTagInput,
  bindInfoPaneEvents: galleryBindInfoPaneEvents,
  applyItemTags: galleryApplyItemTags,
  addTag: galleryAddTag,
  removeTag: galleryRemoveTag,
  // FILTRE FAVORIS (bascule toolbar)
  toggleFavoriteFilter: galleryToggleFavoriteFilter,
  updateFavoriteButton: galleryUpdateFavoriteButton,
  clearSelection: galleryClearSelection,
  selectedIds: gallerySelectedIds,
  selectedItems: gallerySelectedItems,
  deleteItem: galleryDeleteItem,
  deleteLightboxCurrent: galleryLightboxDeleteCurrent,
  toggleFavorite: galleryToggleFavorite,
  restoreItem: galleryRestoreItem,
  purgeItem: galleryPurgeItem,
  bulkDelete: galleryBulkDelete,
  bulkFavorite: galleryBulkFavorite,
  bulkTags: galleryBulkTags,
  bulkAutoTag: galleryBulkAutoTag,
  runAutoTag: galleryRunAutoTag,
  cancelAutoTag: galleryCancelAutoTag,
  visionPresets: galleryVisionPresets,
  ensureVisionPresets: galleryEnsureVisionPresets,
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
    THUMB_PENDING_MS: GALLERY_THUMB_PENDING_MS,
    POLL_MS: GALLERY_POLL_MS,
    SORTS: GALLERY_SORTS.slice(),
    TAG_MAX_LEN: GALLERY_TAG_MAX_LEN,
  },
  state: galleryState,
};
