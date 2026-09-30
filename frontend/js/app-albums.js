/**
 * app-albums.js — Albums publics (phase 3) : intégration dans la galerie privée.
 *
 * L'API PRIVÉE (backend/routes/albums.py) reste la seule source de vérité :
 *   - POST   /api/albums                → création (snapshot ASYNC, statut building) ;
 *   - GET    /api/albums                → liste + compteurs + statut/avancement ;
 *   - GET    /api/albums/<id>           → détail (polling de préparation + « Charger ») ;
 *   - PATCH  /api/albums/<id>           → titre/description (renommage) ;
 *   - POST   /api/albums/<id>/items     → ajout de la sélection courante ;
 *   - POST   /api/albums/<id>/revoke    → révocation (le lien public est coupé) ;
 *   - DELETE /api/albums/<id>           → suppression définitive ;
 *   - GET    /api/albums/for-media/<id> → avertissement AVANT purge définitive.
 *
 * RÉUTILISE l'adaptateur galerie (window.AppGallery : apiRequest, toast,
 * confirm, setBusy, selectedIds, state.grid) et la brique VENDUE HolafModal.
 * AUCUNE brique frontend/vendor/holaf/* n'est modifiée (cf. `holaf check`).
 *
 * POLLING DE PRÉPARATION (modale de RÉSULTAT) : la création rend la main en
 * statut ``building`` ; la modale de résultat suit l'avancement par GET détail.
 * POLLING DE GESTION (modale « Albums publics ») : tant qu'au moins un album
 * de la liste est ``building``, la liste se rafraîchit toute seule (cadence
 * légère, ``ALBUMS_MANAGE_POLL_MS``) en affichant « Préparation en cours… x/y ».
 * Les deux suivis sont indépendants. Ré-armement par setTimeout (jamais
 * setInterval → aucune requête concurrente, aucun empilement de timers) ; le
 * suivi de gestion s'ARRÊTE dès qu'aucun album n'est en préparation, à la
 * fermeture de la modale, après ``state.managePollMax`` ticks ou
 * ``state.managePollErrorsMax`` erreurs réseau consécutives. Le DOM n'est
 * reconstruit que si la liste a réellement changé (focus/survol préservés).
 * Le suivi de RÉSULTAT s'ARRÊTE sur ready/error/revoked, à la fermeture de la
 * modale, après ``state.pollMax`` ticks ou ``state.pollErrorsMax`` erreurs
 * réseau consécutives.
 *
 * Script CLASSIQUE (comme les autres app-*.js) : les points d'entrée des
 * boutons (galleryAlbumsOpenCreate / galleryAlbumsOpenManage /
 * galleryBulkPurgeWarned) sont des fonctions globales de niveau fichier.
 */

/* ── Constantes ──────────────────────────────────────────────────────────── */

// Cadence du suivi de préparation (surchargeable en test : state.pollMs).
var ALBUMS_POLL_MS = 1500;
// Garde-fous : 240 ticks ≈ 6 min de préparation, 8 erreurs réseau consécutives.
var ALBUMS_POLL_MAX = 240;
var ALBUMS_POLL_ERRORS_MAX = 8;
// Rafraîchissement automatique de la modale de GESTION tant qu'un album est en
// préparation (cadence légère ; surchargeable en test : state.managePollMs).
var ALBUMS_MANAGE_POLL_MS = 3000;
// Garde-fous : 480 ticks ≈ 24 min, 5 erreurs réseau consécutives.
var ALBUMS_MANAGE_POLL_MAX = 480;
var ALBUMS_MANAGE_POLL_ERRORS_MAX = 5;
// Requêtes parallèles pour l'avertissement avant purge (sélection potentiellement large).
var ALBUMS_FOR_MEDIA_CONCURRENCY = 4;

// Raisons stables renvoyées par le backend (skipped[].reason) → libellé FR.
var ALBUMS_SKIP_REASONS = {
  not_found: 'introuvable',
  not_owned: 'non autorisé',
  trashed: 'en corbeille',
  unsupported_kind: 'type non supporté (vidéo/audio)',
  duplicate: 'déjà présent',
};

// Libellés de statut d'album (contrat backend : ALBUM_STATUSES).
var ALBUMS_STATUS_LABELS = {
  building: 'En préparation',
  ready: 'Prêt',
  error: 'Erreur',
  revoked: 'Révoqué',
};

/* ── État (observable de test) ───────────────────────────────────────────── */

var albumsState = {
  createModal: null,
  resultModal: null,
  manageModal: null,
  renameModal: null,
  // Modale QR (action « QR » de la gestion) — une seule à la fois.
  qrModal: null,
  // Dernier album créé (réponse POST), dernier chargement, dernier ajout.
  lastCreated: null,
  lastLoaded: null,
  lastAdd: null,
  lastRename: null,
  lastList: null,
  // Dernier QR affiché (modale de gestion) : { albumId, text, size }.
  lastQr: null,
  // Dernier texte rendu dans la zone QR de la modale de résultat (garde
  // anti-re-rendu : le polling remplace l'objet album à chaque tick).
  resultQrKey: undefined,
  // Dernier texte copié (URL publique ou clé).
  lastCopied: null,
  // Dernière URL publique ouverte (« Ouvrir »).
  lastOpened: null,
  // Modale de résultat : { album, skipped[] } — album remplacé à chaque tick.
  result: null,
  // Suivi de préparation : unique album suivi à la fois.
  poll: { active: false, albumId: null, timer: null, attempts: 0, errors: 0, status: null },
  // Suivi de la modale de GESTION : la LISTE est rafraîchie tant qu'au moins
  // un album est ``building`` (aucun albumId : un seul suivi de liste à la
  // fois). ``inflight`` évite tout chevauchement de requêtes.
  managePoll: { active: false, timer: null, attempts: 0, errors: 0, inflight: false },
  // Dernière empreinte de liste rendue (évite de reconstruire le DOM à chaque
  // tick quand rien n'a changé → focus et survol préservés).
  manageRenderKey: null,
  // Surcharges de test.
  pollMs: ALBUMS_POLL_MS,
  pollMax: ALBUMS_POLL_MAX,
  pollErrorsMax: ALBUMS_POLL_ERRORS_MAX,
  managePollMs: ALBUMS_MANAGE_POLL_MS,
  managePollMax: ALBUMS_MANAGE_POLL_MAX,
  managePollErrorsMax: ALBUMS_MANAGE_POLL_ERRORS_MAX,
};

/* ── Accès à l'adaptateur galerie (late binding : l'ordre des scripts classiques
 *    n'est pas un prérequis, les appels n'ont lieu qu'à l'interaction). ───── */

function albumsGallery() {
  return window.AppGallery || null;
}

function albumsById(id) {
  return window.document.getElementById(id);
}

function albumsErrorMessage(err) {
  if (!err) return 'erreur inconnue';
  var msg = err.message || String(err);
  if (/HTTP 401/.test(msg)) return 'connexion requise';
  if (/HTTP 403/.test(msg)) return 'accès refusé';
  if (/HTTP 404/.test(msg)) return 'album introuvable';
  if (/HTTP 409/.test(msg)) return 'album révoqué';
  return msg;
}

/** Requête JSON API privée : déléguée à l'adaptateur galerie (état lastRequest
 *  mémorisé pour les tests) ; repli autonome si l'adaptateur n'est pas chargé. */
function albumsRequest(method, path, body) {
  var g = albumsGallery();
  if (g && typeof g.apiRequest === 'function') return g.apiRequest(method, path, body);
  var base = (typeof API !== 'undefined') ? API : (window.API || '/api');
  var opts = { method: method, credentials: 'same-origin' };
  if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  return fetch(base + path, opts).then(function (res) {
    return res.json().then(function (data) {
      if (!res.ok || (data && data.error)) {
        throw new Error((data && data.error) || ('HTTP ' + res.status));
      }
      return data || {};
    });
  });
}

function albumsToast(message, type) {
  var g = albumsGallery();
  if (g && typeof g.toast === 'function') return g.toast(message, type);
  return null;
}

function albumsConfirm(title, message, cb) {
  var g = albumsGallery();
  if (g && typeof g.confirm === 'function') {
    g.confirm(title, message, cb);
    return;
  }
  var okv = true;
  try { okv = window.confirm(title + '\n\n' + message); } catch (e) { okv = true; }
  cb(!!okv);
}

function albumsSetBusy(flag) {
  var g = albumsGallery();
  if (g && typeof g.setBusy === 'function') g.setBusy(flag);
}

function albumsSelectedIds() {
  var g = albumsGallery();
  var ids = (g && typeof g.selectedIds === 'function') ? g.selectedIds() : [];
  return Array.isArray(ids) ? ids : [];
}

function albumsGrid() {
  var g = albumsGallery();
  return (g && g.state && g.state.grid) ? g.state.grid : null;
}

function albumsBusy() {
  var g = albumsGallery();
  return !!(g && g.state && g.state.busy);
}

/* ── Helpers de présentation ─────────────────────────────────────────────── */

function albumsReasonLabel(reason) {
  return ALBUMS_SKIP_REASONS[reason] || reason || 'ignoré';
}

function albumsStatusLabel(status) {
  return ALBUMS_STATUS_LABELS[status] || status || '';
}

function albumsFormatDate(iso) {
  var g = albumsGallery();
  if (g && typeof g.formatDate === 'function') return g.formatDate(iso);
  if (!iso) return '';
  try { return new Date(iso).toLocaleString(); } catch (e) { return String(iso); }
}

function albumsCountLabel(n) {
  n = Number(n) || 0;
  return n + ' média' + (n > 1 ? 's' : '');
}

/** Copie un texte dans le presse-papiers (brique navigateur OU repli textarea). */
function albumsCopyText(text) {
  albumsState.lastCopied = text;
  var nav = window.navigator || {};
  if (nav.clipboard && typeof nav.clipboard.writeText === 'function') {
    return nav.clipboard.writeText(text).then(function () { return true; }).catch(function () {
      return albumsCopyFallback(text);
    });
  }
  return Promise.resolve(albumsCopyFallback(text));
}

function albumsCopyFallback(text) {
  try {
    var area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', 'readonly');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    if (document.body) document.body.appendChild(area);
    area.select();
    var okv = (typeof document.execCommand === 'function') ? document.execCommand('copy') : false;
    if (area.parentNode) area.parentNode.removeChild(area);
    return !!okv;
  } catch (e) {
    return false;
  }
}

/** « Copier l'URL » : URL publique si configurée, sinon la CLÉ + message d'aide. */
function albumsCopyUrl(album) {
  if (!album) return Promise.resolve(false);
  if (album.public_url) {
    return albumsCopyText(album.public_url).then(function (okv) {
      albumsToast(okv ? 'URL publique copiée.' : 'Copie impossible (presse-papiers indisponible).',
        okv ? 'success' : 'error');
      return okv;
    });
  }
  return albumsCopyText(album.key || '').then(function (okv) {
    albumsToast(okv
      ? 'URL publique non configurée : définis AIH_ALBUM_PUBLIC_BASE_URL côté serveur. La clé de l\'album a été copiée.'
      : 'Copie impossible (presse-papiers indisponible).',
    okv ? 'warning' : 'error');
    return okv;
  });
}

/** « Ouvrir » la page publique (nouvel onglet) — refuse si pas d'URL configurée. */
function albumsOpenPublic() {
  var album = albumsState.result ? albumsState.result.album : null;
  var url = album ? album.public_url : null;
  if (!url) {
    albumsToast('Aucune URL publique : définis AIH_ALBUM_PUBLIC_BASE_URL côté serveur.', 'warning');
    return null;
  }
  albumsState.lastOpened = url;
  try { window.open(url, '_blank', 'noopener'); } catch (e) { /* bloqueur de popup */ }
  return url;
}

/* ── QR code de l'URL publique (brique VENDUE HolafQrcode) ────────────────
 * Le QR est généré CÔTÉ CLIENT (jamais un service tiers : l'URL contient une
 * clé secrète d'album). La brique est chargée par index.html ; si elle manque
 * (ou si public_url est null), on affiche un message d'aide au lieu du QR. ── */

function albumsQrcode() {
  return window.HolafQrcode || null;
}

/** Texte d'aide affiché à la place du QR (URL absente ou brique absente). */
function albumsQrHelpText(album) {
  if (!album || !album.public_url) {
    return 'QR indisponible : l\'URL publique n\'est pas configurée (AIH_ALBUM_PUBLIC_BASE_URL).';
  }
  return 'QR indisponible : brique HolafQrcode absente (vendor/holaf).';
}

/**
 * Remplit `box` : QR (SVG) si l'album a une URL publique ET que la brique
 * est chargée, sinon un message d'aide. Renvoie le SVG ou null.
 */
function albumsRenderQrInto(box, album, opts) {
  if (!box) return null;
  while (box.firstChild) box.removeChild(box.firstChild);
  var qr = albumsQrcode();
  if (!album || !album.public_url || !qr || typeof qr.render !== 'function') {
    var help = document.createElement('p');
    help.id = (opts && opts.helpId) || 'gallery-album-qr-help';
    help.className = 'album-qr-help';
    help.textContent = albumsQrHelpText(album);
    box.appendChild(help);
    return null;
  }
  var size = (opts && opts.size) || 180;
  var svg = qr.render({
    text: album.public_url,
    ecc: (opts && opts.ecc) || 'M',
    size: size,
    margin: (opts && opts.margin) || 3,
    className: 'album-qr-svg',
    alt: 'QR de l\'album ' + (album.title ? '« ' + album.title + ' »' : 'sans titre') + ' (URL publique)',
  });
  box.appendChild(svg);
  return svg;
}

/** Zone QR de la modale de résultat (mise à jour à l'ouverture et au polling). */
function albumsRenderResultQr() {
  var box = albumsById('gallery-album-result-qr');
  var album = albumsState.result ? albumsState.result.album : null;
  if (!box || !album) return;
  var key = album.public_url || '';
  if (albumsState.resultQrKey === key && box.firstChild) return; // déjà à jour
  albumsRenderQrInto(box, album, { size: 168, helpId: 'gallery-album-result-qr-help' });
  albumsState.resultQrKey = key;
}

/** « QR » (gestion) : modale du QR de l'URL publique d'un album. */
function albumsOpenQr(album) {
  if (!album) return null;
  if (!album.public_url) {
    albumsToast('QR indisponible : URL publique non configurée (AIH_ALBUM_PUBLIC_BASE_URL).', 'warning');
    return null;
  }
  var qr = albumsQrcode();
  if (!qr || typeof qr.render !== 'function') {
    albumsToast('QR indisponible : brique HolafQrcode absente (vendor/holaf).', 'error');
    return null;
  }
  if (!window.HolafModal || typeof window.HolafModal.open !== 'function') {
    albumsToast('Modale indisponible (vendor/holaf).', 'error');
    return null;
  }
  if (albumsState.qrModal) {
    try { albumsState.qrModal.close(); } catch (e) { /* ignore */ }
    albumsState.qrModal = null;
  }

  var wrap = document.createElement('div');
  wrap.className = 'album-qr-modal';
  var box = document.createElement('div');
  box.id = 'gallery-album-qr-code';
  box.className = 'album-qr-box';
  wrap.appendChild(box);
  var svg = albumsRenderQrInto(box, album, { size: 240, helpId: 'gallery-album-qr-help' });
  var hint = document.createElement('p');
  hint.id = 'gallery-album-qr-hint';
  hint.className = 'album-qr-hint';
  hint.textContent = 'Scanne ce code pour ouvrir la page publique de l\'album.';
  wrap.appendChild(hint);
  albumsState.lastQr = {
    albumId: album.id,
    text: album.public_url,
    size: svg ? 240 : 0,
  };

  var ctrl = window.HolafModal.open({
    id: 'gallery-album-qr-modal',
    title: album.title ? ('QR — ' + album.title) : 'QR de l\'album',
    size: 'sm',
    width: 340,
    draggable: true,
    resizable: true,
    minWidth: 280,
    minHeight: 260,
    storageKey: 'gallery-album-qr-modal',
    content: wrap,
    buttons: [{ text: 'Fermer', value: false, type: 'cancel' }],
    onClose: function () { albumsState.qrModal = null; },
  });
  albumsState.qrModal = ctrl;
  return ctrl;
}

/* ── Liste des albums (GET /api/albums) ──────────────────────────────────── */

function albumsFetchList() {
  return albumsRequest('GET', '/albums').then(function (data) {
    var items = Array.isArray(data.items) ? data.items : [];
    albumsState.lastList = items;
    return items;
  });
}

/** Compteur affiché : `counts.total` (liste/détail) sinon `progress.total`. */
function albumsTotalOf(album) {
  if (album.counts && typeof album.counts.total === 'number') return album.counts.total;
  if (album.progress && typeof album.progress.total === 'number') return album.progress.total;
  return 0;
}

/** Au moins un album de la liste est-il en préparation (``building``) ? */
function albumsAnyBuilding(items) {
  return (items || []).some(function (a) { return a && a.status === 'building'; });
}

/** État de la barre de statut de la gestion (progression agrégée si besoin). */
function albumsManageStatusText(items) {
  if (!items || !items.length) return 'Aucun album';
  var building = items.filter(function (a) { return a && a.status === 'building'; });
  if (building.length) {
    var done = 0, total = 0;
    building.forEach(function (a) {
      var p = a.progress || {};
      done += Number(p.done) || 0;
      total += Number(p.total) || 0;
    });
    return 'Préparation en cours… ' + done + '/' + total;
  }
  return items.length + ' album' + (items.length > 1 ? 's' : '');
}

/** Empreinte légère de la liste (rendu seulement si elle change réellement). */
function albumsManageRenderKeyOf(items) {
  return JSON.stringify((items || []).map(function (a) {
    var p = a.progress || {};
    var c = a.counts || {};
    return [a.id, a.title, a.description, a.status, p.done, p.total, c.ok, c.failed, c.total];
  }));
}

function albumsManageSetStatus(text) {
  var status = albumsById('gallery-albums-manage-status');
  if (status) status.textContent = text;
}

/** Applique une liste reçue : rendu (si changé), statut, mémorisation. */
function albumsApplyList(items, opts) {
  items = items || [];
  var key = albumsManageRenderKeyOf(items);
  var list = albumsById('gallery-albums-list');
  var empty = !list || !list.firstChild;
  albumsState.lastList = items;
  if (!(opts && opts.keepIfUnchanged) || key !== albumsState.manageRenderKey || empty) {
    albumsRenderList(items);
    albumsState.manageRenderKey = key;
  }
  albumsManageSetStatus(albumsManageStatusText(items));
  return items;
}

function albumsRefreshList(opts) {
  var silent = !!(opts && opts.silent);
  var ctrl = albumsState.manageModal;
  if (!silent) {
    albumsManageSetStatus('Chargement…');
    if (ctrl && typeof ctrl.setBusy === 'function') ctrl.setBusy(true, 'Chargement des albums…');
  }
  return albumsFetchList().then(function (items) {
    albumsApplyList(items, { keepIfUnchanged: silent });
    albumsSyncManagePolling(items);
    return items;
  }).catch(function (err) {
    if (!silent) {
      albumsApplyList([], { keepIfUnchanged: false });
      albumsManageSetStatus('Erreur : ' + albumsErrorMessage(err));
    }
    return [];
  }).then(function (items) {
    if (!silent && ctrl && typeof ctrl.setBusy === 'function') ctrl.setBusy(false);
    return items;
  });
}

/* ── Suivi automatique de la modale de GESTION (albums en préparation) ──── */

function albumsStopManagePolling() {
  var p = albumsState.managePoll;
  p.active = false;
  p.inflight = false;
  if (p.timer) {
    clearTimeout(p.timer);
    p.timer = null;
  }
}

function albumsScheduleManagePoll() {
  var p = albumsState.managePoll;
  if (!p.active || p.timer) return;
  p.timer = setTimeout(function () {
    p.timer = null;
    albumsManagePollTick();
  }, albumsState.managePollMs);
}

/** Démarre (ou relance) le suivi de liste sans jamais empiler de timer. */
function albumsStartManagePolling() {
  var p = albumsState.managePoll;
  if (p.active) {
    albumsScheduleManagePoll();
    return;
  }
  p.active = true;
  p.attempts = 0;
  p.errors = 0;
  albumsScheduleManagePoll();
}

/** Démarre le suivi si un album est en préparation, l'arrête sinon. */
function albumsSyncManagePolling(items) {
  if (!albumsState.manageModal || !albumsById('gallery-albums-list')) {
    albumsStopManagePolling();
    return false;
  }
  if (albumsAnyBuilding(items)) {
    albumsStartManagePolling();
    return true;
  }
  albumsStopManagePolling();
  return false;
}

/** Un tick de suivi de la GESTION (testable sans attendre le timer). */
function albumsManagePollNow() {
  return albumsManagePollTick();
}

function albumsManagePollTick() {
  var p = albumsState.managePoll;
  if (!p.active || p.inflight) return Promise.resolve(null);
  if (!albumsState.manageModal || !albumsById('gallery-albums-list')) {
    albumsStopManagePolling();
    return Promise.resolve(null);
  }
  p.inflight = true;
  p.attempts += 1;
  return albumsFetchList().then(function (items) {
    p.inflight = false;
    if (!p.active) return null;
    p.errors = 0;
    albumsApplyList(items, { keepIfUnchanged: true });
    if (!albumsAnyBuilding(items)) {
      albumsStopManagePolling();
      return items;
    }
    if (p.attempts >= albumsState.managePollMax) {
      albumsManageSetStatus('Suivi interrompu (préparation trop longue) — clique « Rafraîchir ».');
      albumsStopManagePolling();
      return items;
    }
    albumsScheduleManagePoll();
    return items;
  }).catch(function (err) {
    p.inflight = false;
    if (!p.active) return null;
    p.errors += 1;
    if (p.errors >= albumsState.managePollErrorsMax) {
      albumsManageSetStatus('Suivi interrompu (erreur réseau) : ' + albumsErrorMessage(err));
      albumsStopManagePolling();
      return null;
    }
    albumsScheduleManagePoll();
    return null;
  });
}

/* ── Modale de GESTION ───────────────────────────────────────────────────── */

function albumsBuildManageContent() {
  var wrap = document.createElement('div');
  wrap.className = 'album-manage';

  var bar = document.createElement('div');
  bar.className = 'album-manage-toolbar';
  var refresh = document.createElement('button');
  refresh.type = 'button';
  refresh.id = 'gallery-albums-refresh';
  refresh.className = 'album-manage-btn';
  refresh.textContent = '↻ Rafraîchir';
  refresh.addEventListener('click', function () { albumsRefreshList(); });
  bar.appendChild(refresh);
  var status = document.createElement('span');
  status.id = 'gallery-albums-manage-status';
  status.className = 'album-manage-status-text';
  status.setAttribute('role', 'status');
  bar.appendChild(status);
  wrap.appendChild(bar);

  var list = document.createElement('div');
  list.id = 'gallery-albums-list';
  list.className = 'album-manage-list';
  wrap.appendChild(list);
  return wrap;
}

function albumsRenderList(albums) {
  var list = albumsById('gallery-albums-list');
  if (!list) return;
  // Focus préservé : un tick peut reconstruire la liste sous les doigts de
  // l'utilisateur (Tab) ; on rend le focus au bouton équivalent après rendu.
  var active = document.activeElement;
  var focused = null;
  if (active && typeof list.contains === 'function' && list.contains(active)
      && active.getAttribute && active.getAttribute('data-album-action')) {
    focused = {
      action: active.getAttribute('data-album-action'),
      albumId: active.getAttribute('data-album-id'),
    };
  }
  while (list.firstChild) list.removeChild(list.firstChild);

  if (!albums || !albums.length) {
    var empty = document.createElement('div');
    empty.className = 'album-manage-empty';
    empty.textContent = 'Aucun album pour l\'instant.';
    list.appendChild(empty);
    return;
  }

  albums.forEach(function (album) {
    var row = document.createElement('div');
    row.className = 'album-manage-item';
    row.setAttribute('data-album-id', String(album.id));
    row.setAttribute('data-album-status', album.status || '');

    var head = document.createElement('div');
    head.className = 'album-manage-head';
    var title = document.createElement('span');
    title.className = 'album-manage-title';
    title.textContent = album.title || 'Sans titre';
    head.appendChild(title);
    var badge = document.createElement('span');
    badge.className = 'album-manage-badge album-manage-badge--' + (album.status || 'unknown');
    badge.textContent = albumsStatusLabel(album.status);
    head.appendChild(badge);
    row.appendChild(head);

    var meta = document.createElement('div');
    meta.className = 'album-manage-meta';
    var total = albumsTotalOf(album);
    var progress = album.progress || {};
    var bits = [albumsCountLabel(total), albumsFormatDate(album.created_at)];
    if (album.status === 'building') bits.push(progress.done + '/' + progress.total + ' prêts');
    if (album.counts && album.counts.failed) bits.push(album.counts.failed + ' en échec');
    meta.textContent = bits.filter(Boolean).join(' • ');
    row.appendChild(meta);

    var actions = document.createElement('div');
    actions.className = 'album-manage-actions';
    var defs = [
      { action: 'load', label: 'Charger', enabled: true },
      { action: 'add', label: 'Ajouter la sélection', enabled: album.status !== 'revoked' },
      { action: 'rename', label: 'Renommer', enabled: album.status !== 'revoked' },
      { action: 'qr', label: 'QR', enabled: album.status !== 'revoked' },
      { action: 'revoke', label: 'Révoquer', enabled: album.status !== 'revoked' },
      { action: 'delete', label: 'Supprimer', enabled: true, danger: true },
      { action: 'copy', label: 'Copier l\'URL', enabled: true },
    ];
    defs.forEach(function (def) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'album-manage-action' + (def.danger ? ' album-manage-action--danger' : '');
      b.setAttribute('data-album-action', def.action);
      b.setAttribute('data-album-id', String(album.id));
      b.textContent = def.label;
      b.disabled = !def.enabled;
      b.addEventListener('click', function () { albumsManageAction(def.action, album); });
      actions.appendChild(b);
    });
    row.appendChild(actions);
    list.appendChild(row);
  });

  if (focused) {
    var next = list.querySelector('[data-album-action="' + focused.action
      + '"][data-album-id="' + focused.albumId + '"]');
    if (next && typeof next.focus === 'function') next.focus();
  }
}

function albumsManageAction(action, album) {
  if (!album) return;
  if (action === 'load') albumsLoadIntoGrid(album);
  else if (action === 'add') albumsAddSelection(album);
  else if (action === 'rename') albumsOpenRename(album);
  else if (action === 'qr') albumsOpenQr(album);
  else if (action === 'revoke') albumsRevoke(album);
  else if (action === 'delete') albumsDelete(album);
  else if (action === 'copy') albumsCopyUrl(album);
}

/** « Charger » : GET détail → sélection dans la grille (ids NON chargés inclus). */
function albumsLoadIntoGrid(album) {
  if (!album) return Promise.resolve(null);
  return albumsRequest('GET', '/albums/' + encodeURIComponent(album.id)).then(function (detail) {
    var ids = (detail.items || [])
      .filter(function (it) { return it.status === 'ok'; })
      .map(function (it) { return it.media_id; });
    if (!ids.length) {
      albumsToast('Aucun média prêt dans cet album.', 'warning');
      return null;
    }
    var grid = albumsGrid();
    if (!grid || !grid.selection || typeof grid.selection.set !== 'function') {
      albumsToast('Galerie indisponible : ouvre l\'onglet Galerie.', 'error');
      return null;
    }
    grid.selection.set(ids);
    var loaded = 0;
    try { loaded = grid.selection.items().length; } catch (e) { loaded = 0; }
    var outOfView = Math.max(0, ids.length - loaded);
    albumsState.lastLoaded = { albumId: album.id, ids: ids, outOfView: outOfView };
    albumsToast(ids.length + ' sélectionné' + (ids.length > 1 ? 's' : '') + ', ' + outOfView + ' hors vue',
      outOfView ? 'warning' : 'success');
    // Ferme la gestion pour rendre la sélection visible dans la galerie.
    if (albumsState.manageModal) {
      try { albumsState.manageModal.close(); } catch (e) { /* ignore */ }
    }
    return ids;
  }).catch(function (err) {
    albumsToast('Chargement de l\'album impossible : ' + albumsErrorMessage(err), 'error');
    return null;
  });
}

/** « Ajouter la sélection » : POST /items puis rafraîchit la liste de gestion. */
function albumsAddSelection(album) {
  if (!album) return Promise.resolve(false);
  var ids = albumsSelectedIds();
  if (!ids.length) {
    albumsToast('Sélectionne au moins un média à ajouter.', 'warning');
    return Promise.resolve(false);
  }
  albumsSetBusy(true);
  return albumsRequest('POST', '/albums/' + encodeURIComponent(album.id) + '/items', { ids: ids })
    .then(function (data) {
      var added = Number(data.added) || 0;
      var skipped = Array.isArray(data.skipped) ? data.skipped : [];
      albumsState.lastAdd = { albumId: album.id, ids: ids, added: added, skipped: skipped };
      var msg = added + ' média' + (added > 1 ? 's' : '') + ' ajouté' + (added > 1 ? 's' : '') + ' à l\'album';
      if (skipped.length) {
        var reasons = {};
        skipped.forEach(function (s) { reasons[s.reason] = (reasons[s.reason] || 0) + 1; });
        var parts = Object.keys(reasons).map(function (r) {
          return reasons[r] + ' ' + albumsReasonLabel(r);
        });
        msg += ' • ' + skipped.length + ' ignoré' + (skipped.length > 1 ? 's' : '') + ' (' + parts.join(', ') + ')';
      }
      albumsToast(msg, skipped.length ? 'warning' : 'success');
      albumsRefreshList();
      return true;
    })
    .catch(function (err) {
      albumsToast('Ajout impossible : ' + albumsErrorMessage(err), 'error');
      return false;
    })
    .then(function (r) { albumsSetBusy(false); return r; });
}

/** « Renommer » : modale titre/description → PATCH (les deux champs). */
function albumsOpenRename(album) {
  if (!album) return null;
  if (!window.HolafModal || typeof window.HolafModal.open !== 'function') {
    albumsToast('Modale indisponible (vendor/holaf).', 'error');
    return null;
  }
  if (albumsState.renameModal) {
    try { albumsState.renameModal.close(); } catch (e) { /* ignore */ }
    albumsState.renameModal = null;
  }

  var wrap = document.createElement('div');
  wrap.className = 'album-create';
  var titleLabel = document.createElement('label');
  titleLabel.className = 'album-create-label';
  titleLabel.setAttribute('for', 'gallery-album-rename-title');
  titleLabel.textContent = 'Titre';
  var title = document.createElement('input');
  title.type = 'text';
  title.id = 'gallery-album-rename-title';
  title.className = 'album-create-input';
  title.maxLength = 200;
  title.value = album.title || '';
  var descLabel = document.createElement('label');
  descLabel.className = 'album-create-label';
  descLabel.setAttribute('for', 'gallery-album-rename-desc');
  descLabel.textContent = 'Description (optionnelle)';
  var desc = document.createElement('textarea');
  desc.id = 'gallery-album-rename-desc';
  desc.className = 'album-create-input';
  desc.rows = 3;
  desc.maxLength = 2000;
  desc.value = album.description || '';
  wrap.appendChild(titleLabel);
  wrap.appendChild(title);
  wrap.appendChild(descLabel);
  wrap.appendChild(desc);

  var ctrl = window.HolafModal.open({
    id: 'gallery-album-rename-modal',
    title: 'Renommer l\'album',
    size: 'sm',
    width: 400,
    draggable: true,
    resizable: true,
    minWidth: 300,
    minHeight: 220,
    storageKey: 'gallery-album-rename-modal',
    content: wrap,
    buttons: [
      { text: 'Annuler', value: false, type: 'cancel' },
      {
        text: 'Enregistrer', value: true, type: 'primary', autoFocus: true,
        onClick: function () { return albumsRenameSubmit(album, title.value, desc.value); },
      },
    ],
    onClose: function () { albumsState.renameModal = null; },
  });
  albumsState.renameModal = ctrl;
  return ctrl;
}

function albumsRenameSubmit(album, title, description) {
  if (!album) return false;
  albumsSetBusy(true);
  albumsRequest('PATCH', '/albums/' + encodeURIComponent(album.id), {
    title: (title || '').trim(),
    description: (description || '').trim(),
  })
    .then(function (updated) {
      albumsState.lastRename = { albumId: album.id, album: updated };
      albumsToast('Album renommé.', 'success');
      albumsRefreshList();
      return true;
    })
    .catch(function (err) {
      albumsToast('Renommage impossible : ' + albumsErrorMessage(err), 'error');
      return false;
    })
    .then(function (r) { albumsSetBusy(false); return r; });
  return true; // ferme la modale de renommage ; l'erreur éventuelle est toastée
}

/** « Révoquer » (confirmation) : le lien public cesse de fonctionner. */
function albumsRevoke(album) {
  if (!album) return Promise.resolve(false);
  var d = albumsDeferred();
  albumsConfirm('Révoquer l\'album', 'Révoquer « ' + (album.title || 'Sans titre') + ' » ?\n\n'
    + 'Le lien public cessera de fonctionner immédiatement. Les fichiers restent sur le disque '
    + 'mais l\'album ne sera plus servi.',
  function (okv) {
    if (!okv) { d.resolve(false); return; }
    albumsSetBusy(true);
    albumsRequest('POST', '/albums/' + encodeURIComponent(album.id) + '/revoke')
      .then(function () {
        albumsToast('Album révoqué. Le lien public ne fonctionne plus.', 'warning');
        albumsRefreshList();
        d.resolve(true);
      })
      .catch(function (err) {
        albumsToast('Révocation impossible : ' + albumsErrorMessage(err), 'error');
        d.resolve(false);
      })
      .then(function () { albumsSetBusy(false); });
  });
  return d.promise;
}

// Petite fabrique de promesse pour les confirmations asynchrones.
function albumsDeferred() {
  var resolve;
  var promise = new Promise(function (r) { resolve = r; });
  return { promise: promise, resolve: resolve };
}

/** « Supprimer » (confirmation EXPLICITE : irréversible) : lignes + dossier. */
function albumsDelete(album) {
  if (!album) return Promise.resolve(false);
  var d = albumsDeferred();
  albumsConfirm('Supprimer l\'album', 'Supprimer définitivement « ' + (album.title || 'Sans titre') + ' » ?\n\n'
    + 'Cette action est IRRÉVERSIBLE : le dossier public de l\'album sera effacé.',
  function (okv) {
    if (!okv) { d.resolve(false); return; }
    albumsSetBusy(true);
    albumsRequest('DELETE', '/albums/' + encodeURIComponent(album.id))
      .then(function () {
        albumsToast('Album supprimé.', 'success');
        albumsRefreshList();
        d.resolve(true);
      })
      .catch(function (err) {
        albumsToast('Suppression impossible : ' + albumsErrorMessage(err), 'error');
        d.resolve(false);
      })
      .then(function () { albumsSetBusy(false); });
  });
  return d.promise;
}

/** Ouvre la modale de gestion (bouton « Albums » de l'en-tête de galerie). */
function galleryAlbumsOpenManage() {
  if (!window.HolafModal || typeof window.HolafModal.open !== 'function') {
    albumsToast('Modale indisponible (vendor/holaf).', 'error');
    return null;
  }
  if (albumsState.manageModal) {
    try { albumsState.manageModal.close(); } catch (e) { /* ignore */ }
    albumsState.manageModal = null;
  }
  var ctrl = window.HolafModal.open({
    id: 'gallery-album-manage-modal',
    title: 'Albums publics',
    size: 'lg',
    width: 640,
    draggable: true,
    resizable: true,
    minWidth: 420,
    minHeight: 300,
    storageKey: 'gallery-album-manage-modal',
    content: albumsBuildManageContent(),
    buttons: [{ text: 'Fermer', value: false, type: 'cancel' }],
    onClose: function () {
      albumsState.manageModal = null;
      albumsStopManagePolling();
    },
  });
  albumsState.manageModal = ctrl;
  albumsRefreshList();
  return ctrl;
}

/* ── Modale de CRÉATION ──────────────────────────────────────────────────── */

/** Ouvre la modale de création (bouton « Créer un album » de la barre d'actions). */
function galleryAlbumsOpenCreate() {
  if (!window.HolafModal || typeof window.HolafModal.open !== 'function') {
    albumsToast('Modale indisponible (vendor/holaf).', 'error');
    return null;
  }
  var ids = albumsSelectedIds();
  if (!ids.length) {
    albumsToast('Sélectionne au moins un média pour créer un album.', 'warning');
    return null;
  }
  if (albumsState.createModal) {
    try { albumsState.createModal.close(); } catch (e) { /* ignore */ }
    albumsState.createModal = null;
  }

  var wrap = document.createElement('div');
  wrap.className = 'album-create';
  var hint = document.createElement('p');
  hint.id = 'gallery-album-create-count';
  hint.className = 'album-create-hint';
  hint.textContent = ids.length + ' média' + (ids.length > 1 ? 's' : '') + ' sélectionné' + (ids.length > 1 ? 's' : '')
    + ' — les vidéos et l\'audio seront ignorés (phase 1 : images uniquement).';
  wrap.appendChild(hint);

  var titleLabel = document.createElement('label');
  titleLabel.className = 'album-create-label';
  titleLabel.setAttribute('for', 'gallery-album-title');
  titleLabel.textContent = 'Titre (optionnel)';
  var title = document.createElement('input');
  title.type = 'text';
  title.id = 'gallery-album-title';
  title.className = 'album-create-input';
  title.maxLength = 200;
  title.placeholder = 'Mon album…';
  wrap.appendChild(titleLabel);
  wrap.appendChild(title);

  var descLabel = document.createElement('label');
  descLabel.className = 'album-create-label';
  descLabel.setAttribute('for', 'gallery-album-description');
  descLabel.textContent = 'Description (optionnelle)';
  var desc = document.createElement('textarea');
  desc.id = 'gallery-album-description';
  desc.className = 'album-create-input';
  desc.rows = 3;
  desc.maxLength = 2000;
  desc.placeholder = 'À quoi correspond cet album ?';
  wrap.appendChild(descLabel);
  wrap.appendChild(desc);

  var ctrl = window.HolafModal.open({
    id: 'gallery-album-create-modal',
    title: 'Créer un album public',
    size: 'sm',
    width: 420,
    draggable: true,
    resizable: true,
    minWidth: 320,
    minHeight: 240,
    storageKey: 'gallery-album-create-modal',
    content: wrap,
    buttons: [
      { text: 'Annuler', value: false, type: 'cancel' },
      {
        text: 'Créer', value: true, type: 'primary', autoFocus: true,
        onClick: function () { return albumsCreateSubmit(ids, title.value, desc.value); },
      },
    ],
    onClose: function () { albumsState.createModal = null; },
  });
  albumsState.createModal = ctrl;
  return ctrl;
}

/** POST /api/albums { ids, title, description } → modale de résultat + polling. */
function albumsCreateSubmit(ids, title, description) {
  if (!ids || !ids.length) {
    albumsToast('Sélectionne au moins un média.', 'warning');
    return false;
  }
  var payload = { ids: ids.slice(), title: (title || '').trim(), description: (description || '').trim() };
  albumsState.lastCreate = payload;
  albumsSetBusy(true);
  albumsRequest('POST', '/albums', payload)
    .then(function (data) {
      albumsState.lastCreated = data.album || null;
      var skipped = Array.isArray(data.skipped) ? data.skipped : [];
      if (!data.album) {
        albumsToast('Réponse serveur invalide (album manquant).', 'error');
        return false;
      }
      if (albumsState.resultModal) {
        try { albumsState.resultModal.close(); } catch (e) { /* ignore */ }
        albumsState.resultModal = null;
      }
      albumsOpenResult(data.album, skipped);
      // Si la modale de GESTION est ouverte, la liste doit refléter le nouvel
      // album (et son suivi automatique démarrer s'il est en préparation).
      if (albumsState.manageModal) albumsRefreshList();
      albumsToast('Album créé' + (skipped.length
        ? ' • ' + skipped.length + ' média' + (skipped.length > 1 ? 's' : '') + ' ignoré' + (skipped.length > 1 ? 's' : '')
        : '') + '.', skipped.length ? 'warning' : 'success');
      return true;
    })
    .catch(function (err) {
      albumsToast('Création de l\'album impossible : ' + albumsErrorMessage(err), 'error');
      return false;
    })
    .then(function (r) { albumsSetBusy(false); return r; });
  return true; // la modale de création se ferme ; le résultat vit dans SA modale
}

/* ── Modale de RÉSULTAT + polling de préparation ─────────────────────────── */

function albumsBuildResultContent() {
  var wrap = document.createElement('div');
  wrap.className = 'album-result';

  var status = document.createElement('div');
  status.id = 'gallery-album-result-status';
  status.className = 'album-result-status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  wrap.appendChild(status);

  var progress = document.createElement('div');
  progress.id = 'gallery-album-result-progress';
  progress.className = 'album-result-progress';
  wrap.appendChild(progress);

  var urlBox = document.createElement('div');
  urlBox.id = 'gallery-album-result-urlbox';
  urlBox.className = 'album-result-url';
  wrap.appendChild(urlBox);

  // QR de l'URL publique (SVG généré côté client) ou message d'aide.
  var qrBox = document.createElement('div');
  qrBox.id = 'gallery-album-result-qr';
  qrBox.className = 'album-result-qr';
  wrap.appendChild(qrBox);

  var report = document.createElement('div');
  report.id = 'gallery-album-result-report';
  report.className = 'album-result-report';
  wrap.appendChild(report);
  return wrap;
}

/**
 * Ouvre la modale de résultat puis démarre le suivi si l'album est ``building``.
 * @param {object} album album sérialisé (POST ou GET détail)
 * @param {Array} skipped ids refusés à la création (raison stable)
 */
function albumsOpenResult(album, skipped) {
  if (!album) return null;
  albumsState.result = { album: album, skipped: skipped || [] };
  albumsState.resultQrKey = undefined; // nouvelle modale → QR à (re)rendre
  var ctrl = window.HolafModal.open({
    id: 'gallery-album-result-modal',
    title: album.title ? ('Album « ' + album.title + ' »') : 'Album créé',
    size: 'sm',
    width: 460,
    draggable: true,
    resizable: true,
    minWidth: 340,
    minHeight: 260,
    storageKey: 'gallery-album-result-modal',
    content: albumsBuildResultContent(),
    buttons: [{ text: 'Fermer', value: false, type: 'cancel' }],
    onClose: function () {
      albumsState.resultModal = null;
      albumsStopPolling();
    },
  });
  albumsState.resultModal = ctrl;
  albumsRenderResultUrl();
  albumsRenderResult();
  if (album.status === 'building') albumsStartPolling(album.id);
  return ctrl;
}

/** Zone URL : input readonly + Copier/Ouvrir, ou clé + message d'aide si null. */
function albumsRenderResultUrl() {
  var box = albumsById('gallery-album-result-urlbox');
  if (!box) return;
  while (box.firstChild) box.removeChild(box.firstChild);
  var album = albumsState.result ? albumsState.result.album : null;
  if (!album) return;

  var row = document.createElement('div');
  row.className = 'album-result-url-row';
  if (album.public_url) {
    var input = document.createElement('input');
    input.type = 'text';
    input.id = 'gallery-album-result-url';
    input.className = 'album-result-url-input';
    input.readOnly = true;
    input.value = album.public_url;
    input.setAttribute('aria-label', 'URL publique de l\'album');
    row.appendChild(input);
  } else {
    var help = document.createElement('p');
    help.id = 'gallery-album-result-help';
    help.className = 'album-result-help';
    help.textContent = 'URL publique non configurée : définis AIH_ALBUM_PUBLIC_BASE_URL côté serveur. '
      + 'En attendant, la CLÉ de l\'album est copiable :';
    row.appendChild(help);
    var key = document.createElement('code');
    key.id = 'gallery-album-result-key';
    key.className = 'album-result-key';
    key.textContent = album.key || '';
    row.appendChild(key);
  }
  var copy = document.createElement('button');
  copy.type = 'button';
  copy.id = 'gallery-album-result-copy';
  copy.className = 'album-manage-btn';
  copy.textContent = album.public_url ? '📋 Copier l\'URL' : '📋 Copier la clé';
  copy.addEventListener('click', function () { albumsCopyUrl(album); });
  row.appendChild(copy);
  var open = document.createElement('button');
  open.type = 'button';
  open.id = 'gallery-album-result-open';
  open.className = 'album-manage-btn';
  open.textContent = '↗ Ouvrir';
  open.disabled = !album.public_url;
  open.title = album.public_url ? 'Ouvrir la page publique' : 'URL publique non configurée (AIH_ALBUM_PUBLIC_BASE_URL)';
  open.addEventListener('click', function () { albumsOpenPublic(); });
  row.appendChild(open);
  box.appendChild(row);
}

function albumsResultStatusText(album) {
  var p = album.progress || {};
  if (album.status === 'building') return 'Préparation en cours : ' + (p.done || 0) + '/' + (p.total || 0) + '…';
  if (album.status === 'ready') return 'Album prêt.';
  if (album.status === 'error') return 'La préparation a échoué côté serveur.';
  if (album.status === 'revoked') return 'Album révoqué : le lien public ne fonctionne plus.';
  return album.status || '';
}

/** Met à jour (à chaque tick) statut, avancement et rapport de la modale. */
function albumsRenderResult() {
  var st = albumsState.result;
  var album = st ? st.album : null;
  if (!album) return;

  albumsRenderResultQr();

  var status = albumsById('gallery-album-result-status');
  if (status) status.textContent = albumsResultStatusText(album);
  if (status) status.setAttribute('data-status', album.status || '');
  var progress = albumsById('gallery-album-result-progress');
  if (progress) {
    var counts = album.counts;
    var p = album.progress || {};
    var bits = [];
    if (album.status === 'building') bits.push('Avancement : ' + (p.done || 0) + ' / ' + (p.total || 0));
    if (counts) bits.push(counts.ok + ' prêt' + (counts.ok > 1 ? 's' : '') + ' • ' + counts.failed + ' en échec');
    progress.textContent = bits.join(' — ');
  }

  var box = albumsById('gallery-album-result-report');
  if (!box) return;
  while (box.firstChild) box.removeChild(box.firstChild);
  var items = Array.isArray(album.items) ? album.items : [];
  var okv, failed = [], pending;
  if (items.length) {
    okv = items.filter(function (it) { return it.status === 'ok'; }).length;
    failed = items.filter(function (it) { return it.status === 'failed'; });
    pending = items.filter(function (it) { return it.status === 'pending'; }).length;
  } else {
    var prog = album.progress || {};
    okv = prog.done || 0;
    pending = Math.max(0, (prog.total || 0) - (prog.done || 0));
  }
  var line = document.createElement('p');
  line.id = 'gallery-album-result-count';
  line.className = 'album-result-count';
  line.textContent = okv + ' média' + (okv > 1 ? 's' : '') + ' ajouté' + (okv > 1 ? 's' : '')
    + (pending ? ' • ' + pending + ' en préparation' : '')
    + (failed.length ? ' • ' + failed.length + ' en échec' : '');
  box.appendChild(line);

  var skipped = st.skipped || [];
  if (skipped.length) {
    var t = document.createElement('p');
    t.className = 'album-result-subtitle';
    t.textContent = 'Ignorés (' + skipped.length + ') :';
    box.appendChild(t);
    var ul = document.createElement('ul');
    ul.id = 'gallery-album-result-skipped';
    ul.className = 'album-result-list';
    skipped.forEach(function (s) {
      var li = document.createElement('li');
      li.textContent = 'média #' + s.id + ' — ' + albumsReasonLabel(s.reason);
      ul.appendChild(li);
    });
    box.appendChild(ul);
  }
  if (failed.length) {
    var t2 = document.createElement('p');
    t2.className = 'album-result-subtitle';
    t2.textContent = 'Échecs (' + failed.length + ') :';
    box.appendChild(t2);
    var ul2 = document.createElement('ul');
    ul2.id = 'gallery-album-result-failed';
    ul2.className = 'album-result-list';
    failed.forEach(function (it) {
      var li = document.createElement('li');
      li.textContent = 'média #' + it.media_id + ' — ' + (it.error || 'échec de génération');
      ul2.appendChild(li);
    });
    box.appendChild(ul2);
  }
}

function albumsResultSetError(message) {
  var status = albumsById('gallery-album-result-status');
  if (status) {
    status.textContent = message;
    status.setAttribute('data-status', 'error');
  }
}

/* ── Polling (setTimeout ré-armé : jamais deux requêtes en vol) ──────────── */

function albumsStopPolling() {
  var p = albumsState.poll;
  p.active = false;
  p.albumId = null;
  if (p.timer) {
    clearTimeout(p.timer);
    p.timer = null;
  }
}

function albumsStartPolling(albumId) {
  var p = albumsState.poll;
  albumsStopPolling();
  p.active = true;
  p.albumId = albumId;
  p.attempts = 0;
  p.errors = 0;
  p.status = 'building';
  albumsSchedulePoll();
}

function albumsSchedulePoll() {
  var p = albumsState.poll;
  if (!p.active || p.timer) return;
  p.timer = setTimeout(function () {
    p.timer = null;
    albumsPollTick();
  }, albumsState.pollMs);
}

/** Un tick de suivi (testable sans attendre le timer). */
function albumsPollNow() {
  return albumsPollTick();
}

function albumsPollTick() {
  var p = albumsState.poll;
  if (!p.active || p.albumId === null) return Promise.resolve(null);
  var albumId = p.albumId;
  p.attempts += 1;
  return albumsRequest('GET', '/albums/' + encodeURIComponent(albumId)).then(function (album) {
    if (!p.active || p.albumId !== albumId) return null; // annulé entre-temps
    p.errors = 0;
    p.status = album.status;
    if (albumsState.result) albumsState.result.album = album;
    albumsRenderResult();
    if (album.status === 'building') {
      if (p.attempts >= albumsState.pollMax) {
        albumsResultSetError('La préparation prend trop de temps. Ouvre « Albums » pour suivre l\'avancement.');
        albumsStopPolling();
        return null;
      }
      albumsSchedulePoll();
      return album;
    }
    albumsStopPolling(); // ready / error / revoked : fin du suivi
    if (album.status === 'ready') albumsToast('Album prêt.', 'success');
    else if (album.status === 'error') albumsToast('La préparation de l\'album a échoué.', 'error');
    return album;
  }).catch(function (err) {
    if (!p.active || p.albumId !== albumId) return null;
    p.errors += 1;
    if (p.errors >= albumsState.pollErrorsMax) {
      albumsResultSetError('Suivi interrompu (erreur réseau) : ' + albumsErrorMessage(err));
      albumsStopPolling();
      return null;
    }
    albumsSchedulePoll();
    return null;
  });
}

/* ── Avertissement AVANT purge définitive (GET /for-media) ───────────────── */

/** Comptage best-effort (concurrence bornée) : { media, albums }. */
function albumsCountsForMedia(ids, concurrency) {
  var queue = (ids || []).slice();
  var size = Math.max(1, concurrency || ALBUMS_FOR_MEDIA_CONCURRENCY);
  var media = 0;
  var seenAlbums = {};
  function runBatch() {
    if (!queue.length) return Promise.resolve();
    var batch = queue.splice(0, size);
    return Promise.all(batch.map(function (id) {
      return albumsRequest('GET', '/albums/for-media/' + encodeURIComponent(id))
        .then(function (data) {
          var count = (data && typeof data.count === 'number')
            ? data.count
            : ((data && Array.isArray(data.albums)) ? data.albums.length : 0);
          if (count > 0) media += 1;
          ((data && data.albums) || []).forEach(function (a) {
            if (a && a.id !== undefined) seenAlbums[a.id] = true;
          });
        })
        .catch(function () { /* best-effort : une erreur n'empêche pas la purge */ });
    })).then(runBatch);
  }
  return runBatch().then(function () {
    return { media: media, albums: Object.keys(seenAlbums).length };
  });
}

/** Message d'avertissement ('' si aucun album concerné ou API indisponible). */
function albumsPurgeWarningText(ids) {
  ids = (ids || []).slice();
  if (!ids.length) return Promise.resolve('');
  return albumsCountsForMedia(ids).then(function (agg) {
    if (!agg.albums) return '';
    // Unitaire : « Ce média… » ; groupé : « N média(s) figurent… » (seuls les
    // médias CONCERNÉS sont comptés, pas toute la sélection).
    var who = (ids.length === 1)
      ? 'Ce média figure'
      : agg.media + ' média' + (agg.media > 1 ? 's' : '') + ' ' + (agg.media > 1 ? 'figurent' : 'figure');
    var tail = (agg.media === 1) ? 'la purge l\'en retirera.' : 'la purge les en retirera.';
    return '⚠ ' + who + ' dans ' + agg.albums + ' album' + (agg.albums > 1 ? 's' : '')
      + ' public' + (agg.albums > 1 ? 's' : '') + ' : ' + tail;
  }).catch(function () { return ''; });
}

/** Purge UNITAIRE passée par l'avertissement (sélection/action bar). */
function galleryPurgeItemWarned(item) {
  if (!item || albumsBusy()) return Promise.resolve(false);
  return albumsPurgeWarningText([item.id]).then(function (warningText) {
    var g = albumsGallery();
    if (!g || typeof g.purgeItem !== 'function') return false;
    return g.purgeItem(item, warningText);
  });
}

/** Purge GROUPÉE passée par l'avertissement (bouton « Supprimer définitivement »). */
function galleryBulkPurgeWarned() {
  var ids = albumsSelectedIds();
  if (!ids.length || albumsBusy()) return Promise.resolve(false);
  return albumsPurgeWarningText(ids).then(function (warningText) {
    var g = albumsGallery();
    if (!g || typeof g.bulkPurge !== 'function') return false;
    return g.bulkPurge(warningText);
  });
}

/* ── Export (tests headless + câblage tardif) ────────────────────────────── */

window.AppAlbums = {
  state: albumsState,
  // Boutons (aussi globaux via les déclarations de niveau fichier).
  openCreate: galleryAlbumsOpenCreate,
  openManage: galleryAlbumsOpenManage,
  // Création + résultat + polling.
  create: albumsCreateSubmit,
  openResult: albumsOpenResult,
  renderResultQr: albumsRenderResultQr,
  openPublic: albumsOpenPublic,
  openQr: albumsOpenQr,
  pollNow: albumsPollNow,
  pollStop: albumsStopPolling,
  // Gestion.
  fetchList: albumsFetchList,
  refreshList: albumsRefreshList,
  managePollNow: albumsManagePollNow,
  managePollStart: albumsStartManagePolling,
  managePollStop: albumsStopManagePolling,
  loadIntoGrid: albumsLoadIntoGrid,
  addSelection: albumsAddSelection,
  openRename: albumsOpenRename,
  rename: albumsRenameSubmit,
  revoke: albumsRevoke,
  remove: albumsDelete,
  copyUrl: albumsCopyUrl,
  copyText: albumsCopyText,
  reasonLabel: albumsReasonLabel,
  statusLabel: albumsStatusLabel,
  // Avertissement avant purge.
  purgeWarningText: albumsPurgeWarningText,
  purgeItemWarned: galleryPurgeItemWarned,
  bulkPurgeWarned: galleryBulkPurgeWarned,
  constants: {
    POLL_MS: ALBUMS_POLL_MS,
    POLL_MAX: ALBUMS_POLL_MAX,
    POLL_ERRORS_MAX: ALBUMS_POLL_ERRORS_MAX,
    MANAGE_POLL_MS: ALBUMS_MANAGE_POLL_MS,
    MANAGE_POLL_MAX: ALBUMS_MANAGE_POLL_MAX,
    MANAGE_POLL_ERRORS_MAX: ALBUMS_MANAGE_POLL_ERRORS_MAX,
    FOR_MEDIA_CONCURRENCY: ALBUMS_FOR_MEDIA_CONCURRENCY,
    SKIP_REASONS: ALBUMS_SKIP_REASONS,
    STATUS_LABELS: ALBUMS_STATUS_LABELS,
  },
};
