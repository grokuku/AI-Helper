/* ─────────────────────────────────────────────────────────────────────────
 * Album public — logique de la page (coquille HTML servie par public_app.py).
 *
 * Chargée en <script type="module"> : elle lit window.HolafViewport /
 * window.HolafGrid / window.HolafLightbox posés par les briques VENDUES
 * (vendor/holaf/, jamais éditées). AUCUN script inline (CSP script-src 'self'),
 * AUCUN style inline : tout le CSS de la page est dans /assets/album.css.
 *
 * Sécurité XSS : le titre, la description et le compte proviennent du manifest
 * et sont posés EXCLUSIVEMENT via `textContent` (jamais innerHTML). La grille,
 * la vue grande et les réglages ne construisent que des éléments DOM via
 * createElement.
 *
 * Confort de visionnage (phase B) :
 *   - clic simple sur une vignette → PLEIN ÉCRAN (grille activateOnClick,
 *     onActivate(item, index, 'click') → lightbox.openFullscreen) ;
 *   - flèches clavier dans la visionneuse : la brique les gère dans handleKey,
 *     l'hôte se contente de lui transmettre keydown (aucun listener dans la brique) ;
 *   - diaporama : bouton ▶/❚❚ (contrôle HÔTE, classe .holaf-lightbox-nav),
 *     piloté par les événements slideshowstart/stop/pause/resume ;
 *   - crossfade entre images via l'option `transition` (ms) ;
 *   - Espace : en plein écran, démarre le diaporama puis play/pause (géré par
 *     la brique, en opt-in `slideshow`) ;
 *   - chrome épuré : icônes seules (chrome.icons), masquage après 3 s
 *     d'immobilité de la souris (chrome.autoHide/idleDelay) et réapparition
 *     au moindre mouvement, en fondu (brique 0.2.0).
 *
 * Réglages du VISITEUR (langue + diaporama) : panneau DANS la page, ouvert par
 * l'engrenage de l'en-tête (hors plein écran), persisté en localStorage
 * (service public en lecture seule, sans compte) et appliqués à l'ouverture de
 * la visionneuse. Choix page/panneau : un panneau évite une page (et un asset
 * HTML) supplémentaire, ne fait pas perdre l'album de vue et n'expose la clé
 * dans AUCUNE URL secondaire (Referrer-Policy: no-referrer rendrait un retour
 * vers /a/<clé> impossible à reconstruire sans la remettre en query).
 *
 * Fonctionnement : la clé de l'album est lue dans l'URL (/a/<key>) ; le
 * manifest est récupéré (no-store) ; un 404 → état « album introuvable » ;
 * toute autre erreur → état « erreur de chargement ». Aucune écriture.
 * ───────────────────────────────────────────────────────────────────────── */
(function () {
    'use strict';

    var KEY_RE = /^\/a\/([A-Za-z0-9_-]{20,64})(?:\/|$)/;

    // Clé localStorage des réglages visiteur (versionnée : un futur schéma
    // incompatible pourra ignorer l'ancienne au lieu de la mal interpréter).
    var STORAGE_KEY = 'aih-album-settings-v1';

    // Bornes des réglages numériques (validation des valeurs relues).
    var DURATION_MIN = 1000;
    var DURATION_MAX = 20000;
    var TRANSITION_MIN = 0;
    var TRANSITION_MAX = 2000;

    var DEFAULT_SETTINGS = {
        lang: 'fr',
        duration: 4000,
        transition: 400,
        random: false,
        loop: false
    };

    // Chrome du plein écran : icônes seules + masquage doux après 3 s
    // d'immobilité (même curseur posé sur un icône), réapparition au mouvement.
    var CHROME = { icons: true, autoHide: true, idleDelay: 3000, fadeDuration: 300 };

    // Dictionnaire FR/EN de la page. Les libellés de la visionneuse sont
    // passés à la brique via son option `labels`.
    var I18N = {
        fr: {
            loading: 'Chargement…',
            notFound: 'Album introuvable ou révoqué.',
            loadError: 'Erreur de chargement.',
            empty: 'Cet album est vide.',
            countOne: '{n} photo',
            countMany: '{n} photos',
            photoLabel: 'Photo {n}',
            configToggle: 'Réglages',
            configTitle: 'Réglages',
            configLang: 'Langue',
            configDuration: 'Durée d’affichage',
            configTransition: 'Vitesse de transition',
            configRandom: 'Ordre aléatoire',
            configLoop: 'Boucle',
            configClose: 'Fermer',
            configSeconds: '{s} s',
            configTransitionOff: 'Coupe franche',
            slideshowStart: 'Démarrer le diaporama',
            slideshowPause: 'Mettre le diaporama en pause',
            slideshowResume: 'Reprendre le diaporama',
            prev: 'Image précédente',
            next: 'Image suivante',
            close: 'Fermer',
            region: 'Visionneuse'
        },
        en: {
            loading: 'Loading…',
            notFound: 'Album not found or revoked.',
            loadError: 'Loading error.',
            empty: 'This album is empty.',
            countOne: '{n} photo',
            countMany: '{n} photos',
            photoLabel: 'Photo {n}',
            configToggle: 'Settings',
            configTitle: 'Settings',
            configLang: 'Language',
            configDuration: 'Slide duration',
            configTransition: 'Transition speed',
            configRandom: 'Random order',
            configLoop: 'Loop',
            configClose: 'Close',
            configSeconds: '{s} s',
            configTransitionOff: 'Cut (no fade)',
            slideshowStart: 'Start slideshow',
            slideshowPause: 'Pause slideshow',
            slideshowResume: 'Resume slideshow',
            prev: 'Previous image',
            next: 'Next image',
            close: 'Close',
            region: 'Viewer'
        }
    };

    var ui = { index: 0, grid: null, lightbox: null, items: [], settings: null, statusKey: null, count: null };

    function byId(id) { return document.getElementById(id); }

    /** Interpolation minimale « {n} » → params.n (aucun HTML : texte seul). */
    function t(key, params) {
        var lang = (ui.settings && ui.settings.lang) || 'fr';
        var dict = I18N[lang] || I18N.fr;
        var text = Object.prototype.hasOwnProperty.call(dict, key) ? dict[key] : (I18N.fr[key] || key);
        if (params) {
            text = text.replace(/\{(\w+)\}/g, function (match, name) {
                return Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match;
            });
        }
        return text;
    }

    /** Statut par CLÉ i18n (repeint tel quel au changement de langue). */
    function setStatus(key) {
        ui.statusKey = key || null;
        paintStatus();
    }

    function paintStatus() {
        var el = byId('album-status');
        if (!el) return;
        if (ui.statusKey) {
            el.textContent = t(ui.statusKey);
            el.hidden = false;
        } else {
            el.textContent = '';
            el.hidden = true;
        }
    }

    /** Compte de photos par CLÉ i18n (singulier/pluriel selon la langue). */
    function setCount(count) {
        ui.count = (typeof count === 'number' && isFinite(count)) ? count : null;
        paintCount();
    }

    function paintCount() {
        var el = byId('album-count');
        if (!el) return;
        if (!ui.count) {
            el.textContent = '';
            el.hidden = true;
            return;
        }
        el.textContent = t(ui.count > 1 ? 'countMany' : 'countOne', { n: ui.count });
        el.hidden = false;
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

    // ── Réglages visiteur (localStorage, validés) ──────────────────────────

    function clampInt(value, min, max, fallback) {
        var n = Number(value);
        if (!isFinite(n)) return fallback;
        n = Math.round(n);
        if (n < min) return min;
        if (n > max) return max;
        return n;
    }

    /** Valide/normalise un objet de réglages (JSON corrompu ou hors bornes inclus). */
    function normalizeSettings(raw) {
        var out = {
            lang: (raw && raw.lang === 'en') ? 'en' : 'fr',
            duration: DEFAULT_SETTINGS.duration,
            transition: DEFAULT_SETTINGS.transition,
            random: false,
            loop: false
        };
        if (raw) {
            out.duration = clampInt(raw.duration, DURATION_MIN, DURATION_MAX, DEFAULT_SETTINGS.duration);
            out.transition = clampInt(raw.transition, TRANSITION_MIN, TRANSITION_MAX, DEFAULT_SETTINGS.transition);
            out.random = raw.random === true;
            out.loop = raw.loop === true;
        }
        return out;
    }

    /** localStorage peut être coupé (navigation privée) : repli silencieux. */
    function readStorage() {
        try {
            return window.localStorage || null;
        } catch (e) {
            return null;
        }
    }

    function loadSettings() {
        var storage = readStorage();
        if (!storage) return normalizeSettings(null);
        try {
            var raw = storage.getItem(STORAGE_KEY);
            return normalizeSettings(raw ? JSON.parse(raw) : null);
        } catch (e) {
            return normalizeSettings(null);
        }
    }

    function saveSettings() {
        var storage = readStorage();
        if (!storage) return;
        try {
            storage.setItem(STORAGE_KEY, JSON.stringify(ui.settings));
        } catch (e) {
            /* quota refusé : les réglages restent appliqués pour la session */
        }
    }

    function formatSeconds(ms) {
        var s = (ms / 1000).toFixed(1);
        return (ui.settings.lang === 'fr') ? s.replace('.', ',') : s;
    }

    function slideshowConfig() {
        var s = ui.settings;
        return {
            duration: s.duration,
            transition: s.transition,
            random: s.random,
            loop: s.loop,
            keyboard: true
        };
    }

    // ── i18n de la page ────────────────────────────────────────────────────

    function paintConfigValues() {
        setText('album-config-duration-value', t('configSeconds', { s: formatSeconds(ui.settings.duration) }));
        if (ui.settings.transition > 0) {
            setText('album-config-transition-value', t('configSeconds', { s: formatSeconds(ui.settings.transition) }));
        } else {
            setText('album-config-transition-value', t('configTransitionOff'));
        }
    }

    /** Met à jour les aria-label des vignettes déjà rendues. */
    function refreshCellLabels() {
        var cells = document.querySelectorAll('.album-cell');
        for (var i = 0; i < cells.length; i++) {
            var number = cells[i].getAttribute('data-album-i');
            if (number) cells[i].setAttribute('aria-label', t('photoLabel', { n: number }));
        }
    }

    /** Applique la langue aux textes de la page (jamais au contenu du manifest). */
    function applyTranslations() {
        if (ui.settings) document.documentElement.setAttribute('lang', ui.settings.lang);

        var toggle = byId('album-config-toggle');
        if (toggle) {
            toggle.title = t('configToggle');
            toggle.setAttribute('aria-label', t('configToggle'));
        }
        setText('album-config-title', t('configTitle'));
        setText('album-config-lang-label', t('configLang'));
        setText('album-config-duration-label', t('configDuration'));
        setText('album-config-transition-label', t('configTransition'));
        setText('album-config-random-label', t('configRandom'));
        setText('album-config-loop-label', t('configLoop'));
        setText('album-config-close', t('configClose'));
        paintConfigValues();
        paintStatus();
        paintCount();
        refreshCellLabels();
        updateSlideshowButton();
    }

    // ── Visionneuse ────────────────────────────────────────────────────────

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
        setConfigOpen(false);
        var lightbox = ensureLightbox();
        if (!lightbox) return;
        if (typeof lightbox.openFullscreen === 'function') lightbox.openFullscreen(ui.items[index]);
    }

    /** ▶/❚❚ selon l'état courant du diaporama (brique tenue à jour). */
    function slideshowGlyph() {
        var lightbox = ui.lightbox;
        var playing = lightbox
            && typeof lightbox.isSlideshow === 'function' && lightbox.isSlideshow()
            && typeof lightbox.isSlideshowPaused === 'function' && !lightbox.isSlideshowPaused();
        return playing ? '❚❚' : '▶';
    }

    function slideshowLabel() {
        var lightbox = ui.lightbox;
        var active = lightbox && typeof lightbox.isSlideshow === 'function' && lightbox.isSlideshow();
        if (active && typeof lightbox.isSlideshowPaused === 'function' && lightbox.isSlideshowPaused()) {
            return t('slideshowResume');
        }
        if (active) return t('slideshowPause');
        return t('slideshowStart');
    }

    /** Refraîchit tous les boutons diaporama présents (aucun : sans effet). */
    function updateSlideshowButton() {
        var buttons = document.querySelectorAll('.album-slideshow-btn');
        for (var i = 0; i < buttons.length; i++) {
            buttons[i].textContent = slideshowGlyph();
            buttons[i].title = slideshowLabel();
            buttons[i].setAttribute('aria-label', buttons[i].title);
        }
    }

    /**
     * Bouton diaporama HÔTE : la brique n'en fournit aucun. La classe
     * `.holaf-lightbox-nav` le fait participer au chrome discret de la brique
     * (masquage/réapparition doux) ; `.album-slideshow-btn` le positionne.
     */
    function makeSlideshowButton() {
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'holaf-lightbox-nav album-slideshow-btn';
        button.textContent = slideshowGlyph();
        button.title = slideshowLabel();
        button.setAttribute('aria-label', button.title);
        button.addEventListener('click', function (event) {
            event.stopPropagation();
            var lightbox = ui.lightbox;
            if (lightbox && typeof lightbox.toggleSlideshow === 'function') {
                // Réglages courants en surcharge de session : même si la
                // brique a été créée avant un changement de réglages.
                lightbox.toggleSlideshow(slideshowConfig());
            }
            updateSlideshowButton();
        });
        return button;
    }

    /**
     * Pose le bouton diaporama dans chaque overlay créé par la brique (elle
     * les crée paresseusement au premier open). Appelée sur l'événement `open`
     * de la visionneuse : l'overlay existe alors déjà.
     */
    function injectSlideshowButton() {
        var overlays = document.querySelectorAll('.holaf-lightbox-overlay');
        for (var i = 0; i < overlays.length; i++) {
            var overlay = overlays[i];
            overlay.classList.add('album-lightbox');
            if (!overlay.querySelector('.album-slideshow-btn')) {
                overlay.appendChild(makeSlideshowButton());
            }
        }
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
            },
            // ── Confort de visionnage (opt-in brique 0.2.0) ──
            slideshow: slideshowConfig(),
            transition: ui.settings.transition,
            chrome: CHROME,
            labels: {
                prev: t('prev'),
                next: t('next'),
                close: t('close'),
                region: t('region')
            },
            onOpen: function () { injectSlideshowButton(); },
            onSlideshowStart: function () { updateSlideshowButton(); },
            onSlideshowStop: function () { updateSlideshowButton(); },
            onSlideshowPause: function () { updateSlideshowButton(); },
            onSlideshowResume: function () { updateSlideshowButton(); }
        };
        if (window.HolafViewport) opts.viewport = window.HolafViewport;
        ui.lightbox = window.HolafLightbox.create(opts);
        ui.lightbox.setItems(ui.items);
        return ui.lightbox;
    }

    /**
     * Après un changement de réglages : détruit la visionneuse FERMÉE pour que
     * la prochaine ouverture reprenne transition/slideshow/labels. Si une vue
     * est ouverte (impossible en pratique : le panneau est sous l'overlay),
     * elle reste intacte ; le bouton diaporama, lui, surcharge déjà la session.
     */
    function resetLightbox() {
        var lightbox = ui.lightbox;
        if (!lightbox) return;
        var open = (typeof lightbox.isOpen === 'function') && lightbox.isOpen();
        if (open) return;
        if (typeof lightbox.destroy === 'function') {
            try { lightbox.destroy(); } catch (e) { /* déjà détruite */ }
        }
        ui.lightbox = null;
    }

    // ── Panneau de réglages ────────────────────────────────────────────────

    function isConfigOpen() {
        var panel = byId('album-config');
        return !!(panel && !panel.hidden);
    }

    function setConfigOpen(open) {
        var panel = byId('album-config');
        var toggle = byId('album-config-toggle');
        if (!panel || !toggle) return;
        panel.hidden = !open;
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
            var lang = byId('album-config-lang');
            if (lang && typeof lang.focus === 'function') lang.focus();
        }
    }

    function applySettingsToForm() {
        var values = {
            'album-config-lang': ui.settings.lang,
            'album-config-duration': String(ui.settings.duration),
            'album-config-transition': String(ui.settings.transition)
        };
        for (var id in values) {
            var el = byId(id);
            if (el) el.value = values[id];
        }
        var random = byId('album-config-random');
        if (random) random.checked = ui.settings.random;
        var loop = byId('album-config-loop');
        if (loop) loop.checked = ui.settings.loop;
        paintConfigValues();
    }

    function settingsFromForm() {
        var lang = byId('album-config-lang');
        var duration = byId('album-config-duration');
        var transition = byId('album-config-transition');
        var random = byId('album-config-random');
        var loop = byId('album-config-loop');
        return normalizeSettings({
            lang: lang ? lang.value : ui.settings.lang,
            duration: duration ? duration.value : ui.settings.duration,
            transition: transition ? transition.value : ui.settings.transition,
            random: random ? random.checked : ui.settings.random,
            loop: loop ? loop.checked : ui.settings.loop
        });
    }

    function onConfigChange() {
        ui.settings = settingsFromForm();
        saveSettings();
        applyTranslations();
        resetLightbox();
    }

    function initConfig() {
        ui.settings = loadSettings();
        applySettingsToForm();
        var toggle = byId('album-config-toggle');
        if (toggle) {
            toggle.addEventListener('click', function () { setConfigOpen(!isConfigOpen()); });
        }
        var close = byId('album-config-close');
        if (close) close.addEventListener('click', function () { setConfigOpen(false); });
        var ids = [
            'album-config-lang',
            'album-config-duration',
            'album-config-transition',
            'album-config-random',
            'album-config-loop'
        ];
        for (var i = 0; i < ids.length; i++) {
            var el = byId(ids[i]);
            if (el) el.addEventListener('change', onConfigChange);
        }
        applyTranslations();
    }

    // ── Clavier ────────────────────────────────────────────────────────────

    /**
     * La brique 0.2.0 n'attache AUCUN listener clavier : l'hôte lui transmet
     * keydown. Flèches ‹/› (les deux sens), Espace (démarre puis play/pause en
     * plein écran), Échap, +/−, 0 sont ainsi actifs dans la visionneuse.
     */
    function onKeyDown(event) {
        if (!event || typeof event.key !== 'string') return;
        var lightbox = ui.lightbox;
        var lightboxOpen = !!(lightbox && typeof lightbox.isOpen === 'function' && lightbox.isOpen());
        if (lightboxOpen && typeof lightbox.handleKey === 'function') {
            var target = event.target;
            var onSlideshowButton = !!(target && typeof target.closest === 'function'
                && target.closest('.album-slideshow-btn'));
            if (onSlideshowButton && (event.key === 'Enter' || event.key === ' ')) {
                // Activation NATIVE du bouton focalisé : ne pas la doubler.
                return;
            }
            if (lightbox.handleKey(event)) event.preventDefault();
            return;
        }
        if (isConfigOpen() && event.key === 'Escape') {
            setConfigOpen(false);
            event.preventDefault();
        }
    }

    // ── Grille / page ──────────────────────────────────────────────────────

    function buildGallery(key, items) {
        var gridEl = byId('album-grid');
        if (!gridEl) return;
        if (!window.HolafGrid || typeof window.HolafGrid.create !== 'function') {
            setStatus('loadError');
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
                    el.setAttribute('data-album-i', String(item.i));
                    el.setAttribute('aria-label', t('photoLabel', { n: item.i }));
                },
                release: function (el) {
                    var img = el.querySelector('img');
                    if (img) img.removeAttribute('src');
                }
            },
            // Clic simple = PLEIN ÉCRAN (double-clic ignoré : pas de double ouverture).
            activateOnClick: true,
            onActivate: function (item, index, kind) {
                if (kind !== 'click') return;
                openViewer(index);
            }
        });
        ui.grid.setItems(items);
        gridEl.hidden = false;
    }

    function render(key, manifest) {
        var rawItems = (manifest && Array.isArray(manifest.items)) ? manifest.items : [];
        var items = buildItems(key, rawItems);
        setText('album-title', manifest && manifest.title ? manifest.title : 'Album');
        setText('album-description', manifest ? manifest.description : '');
        setCount(items.length);
        if (!items.length) {
            setStatus('empty');
            return;
        }
        setStatus(null);
        buildGallery(key, items);
    }

    function start() {
        initConfig();
        document.addEventListener('keydown', onKeyDown);

        var key = albumKeyFromPath(window.location && window.location.pathname);
        if (!key) {
            setStatus('notFound');
            return;
        }
        setStatus('loading');
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
                if (error && error.code === 404) setStatus('notFound');
                else setStatus('loadError');
            });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
