/* ─────────────────────────────────────────────────────────────────────────
 * Album public — logique de la page (coquille HTML servie par public_app.py).
 *
 * Chargée en <script type="module"> : elle lit window.HolafViewport /
 * window.HolafGrid / window.HolafLightbox posés par les briques VENDUES
 * (vendor/holaf/, jamais éditées). AUCUN script inline (CSP script-src 'self').
 *
 * Sécurité XSS : le titre, la description et le compte proviennent du manifest
 * et sont posés EXCLUSIVEMENT via `textContent` (jamais innerHTML). La grille
 * et la vue grande ne construisent que des éléments DOM via createElement.
 *
 * Fonctionnement : la clé de l'album est lue dans l'URL (/a/<key>) ; le
 * manifest est récupéré (no-store) ; un 404 → état « album introuvable » ;
 * toute autre erreur → état « erreur de chargement ». Aucune écriture.
 * ───────────────────────────────────────────────────────────────────────── */
(function () {
    'use strict';

    var KEY_RE = /^\/a\/([A-Za-z0-9_-]{20,64})(?:\/|$)/;

    var ui = { index: 0, grid: null, lightbox: null, items: [] };

    function byId(id) { return document.getElementById(id); }

    function setStatus(message) {
        var el = byId('album-status');
        if (!el) return;
        if (message) {
            el.textContent = String(message);
            el.hidden = false;
        } else {
            el.textContent = '';
            el.hidden = true;
        }
    }

    /** Écrit un texte SANS interprétation HTML (anti-XSS). */
    function setText(id, value) {
        var el = byId(id);
        if (!el) return;
        var text = (value === null || value === undefined) ? '' : String(value);
        el.textContent = text;
        el.hidden = text === '';
    }

    function albumKeyFromPath(pathname) {
        var match = KEY_RE.exec(String(pathname || ''));
        return match ? match[1] : null;
    }

    function pad4(number) {
        var s = String(number);
        while (s.length < 4) s = '0' + s;
        return s;
    }

    /** Construit les items de galerie depuis les items du manifest. */
    function buildItems(key, rawItems) {
        var out = [];
        for (var i = 0; i < rawItems.length; i++) {
            var raw = rawItems[i] || {};
            var number = parseInt(raw.i, 10);
            if (!isFinite(number)) continue;
            var name = pad4(number);
            var ext = (typeof raw.ext === 'string' && raw.ext.charAt(0) === '.')
                ? raw.ext.slice(1)
                : 'jpg';
            out.push({
                i: number,
                w: raw.w || null,
                h: raw.h || null,
                thumbUrl: '/a/' + key + '/t/' + name + '.jpg',
                fullUrl: '/a/' + key + '/f/' + name + '.' + ext
            });
        }
        out.sort(function (a, b) { return a.i - b.i; });
        return out;
    }

    /** renderer média de la lightbox : simple <img src=…/f/0001.jpg>. */
    function makeRenderMedia() {
        return function renderMedia(ctx) {
            var img = document.createElement('img');
            img.className = 'album-media';
            img.alt = '';
            img.decoding = 'async';
            var notifyReady = function () {
                if (ctx.signal && ctx.signal.aborted) return;
                if (typeof ctx.onReady === 'function') {
                    ctx.onReady({ width: img.naturalWidth, height: img.naturalHeight });
                }
            };
            img.addEventListener('load', notifyReady);
            img.addEventListener('error', function () {
                if (ctx.signal && ctx.signal.aborted) return;
                if (typeof ctx.onReady === 'function') ctx.onReady({ fit: true });
            });
            img.src = ctx.item.fullUrl;
            ctx.container.appendChild(img);
            return {
                el: img,
                destroy: function () {
                    if (img.parentNode) img.parentNode.removeChild(img);
                }
            };
        };
    }

    function openViewer(index) {
        ui.index = index;
        if (!ui.items.length) return;
        var lightbox = ensureLightbox();
        if (!lightbox) return;
        lightbox.openZoom(ui.items[index]);
    }

    function ensureLightbox() {
        if (ui.lightbox) return ui.lightbox;
        if (!window.HolafLightbox || typeof window.HolafLightbox.create !== 'function') return null;
        var opts = {
            host: document.body,
            getId: function (item) { return String(item.i); },
            urlFor: function (item) { return item.fullUrl; },
            renderMedia: makeRenderMedia(),
            shouldPreload: function () { return true; },
            preload: 4,
            preloadDebounce: 300,
            getColumnCount: function () { return ui.grid ? ui.grid.getColumnCount() : 1; },
            getIndex: function () { return ui.index; },
            onNavigate: function (dir, item, index) {
                if (typeof index === 'number') ui.index = index;
            }
        };
        if (window.HolafViewport) opts.viewport = window.HolafViewport;
        ui.lightbox = window.HolafLightbox.create(opts);
        ui.lightbox.setItems(ui.items);
        return ui.lightbox;
    }

    function buildGallery(key, items) {
        var gridEl = byId('album-grid');
        if (!gridEl) return;
        if (!window.HolafGrid || typeof window.HolafGrid.create !== 'function') {
            setStatus('Erreur de chargement.');
            return;
        }
        ui.items = items;
        ui.index = 0;
        ui.grid = window.HolafGrid.create(gridEl, {
            itemSize: ['--album-thumb-size', 160],
            gap: 'auto',
            aspect: 1,
            getId: function (item) { return String(item.i); },
            selectable: false,
            keyboard: false,
            cell: {
                create: function () {
                    var button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'album-cell';
                    var img = document.createElement('img');
                    img.loading = 'lazy';
                    img.decoding = 'async';
                    img.alt = '';
                    button.appendChild(img);
                    return button;
                },
                update: function (el, item) {
                    var img = el.querySelector('img');
                    if (img) img.src = item.thumbUrl;
                    el.setAttribute('aria-label', 'Photo ' + item.i);
                },
                release: function (el) {
                    var img = el.querySelector('img');
                    if (img) img.removeAttribute('src');
                }
            },
            onActivate: function (item, index) { openViewer(index); }
        });
        ui.grid.setItems(items);
        gridEl.hidden = false;
    }

    function render(key, manifest) {
        var rawItems = (manifest && Array.isArray(manifest.items)) ? manifest.items : [];
        var items = buildItems(key, rawItems);
        setText('album-title', manifest && manifest.title ? manifest.title : 'Album');
        setText('album-description', manifest ? manifest.description : '');
        setText('album-count', items.length ? (items.length + (items.length > 1 ? ' photos' : ' photo')) : '');
        if (!items.length) {
            setStatus('Cet album est vide.');
            return;
        }
        setStatus('');
        buildGallery(key, items);
    }

    function start() {
        var key = albumKeyFromPath(window.location && window.location.pathname);
        if (!key) {
            setStatus('Album introuvable ou révoqué.');
            return;
        }
        setStatus('Chargement…');
        fetch('/a/' + key + '/manifest.json', { credentials: 'omit', cache: 'no-store' })
            .then(function (response) {
                if (response.status === 404) {
                    var notFound = new Error('not-found');
                    notFound.code = 404;
                    throw notFound;
                }
                if (!response.ok) {
                    var httpError = new Error('http-' + response.status);
                    httpError.code = response.status;
                    throw httpError;
                }
                return response.json();
            })
            .then(function (manifest) { render(key, manifest); })
            .catch(function (error) {
                if (error && error.code === 404) setStatus('Album introuvable ou révoqué.');
                else setStatus('Erreur de chargement.');
            });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
