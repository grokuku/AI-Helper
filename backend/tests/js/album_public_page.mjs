// ─────────────────────────────────────────────────────────────────────────
// Harnais jsdom de la PAGE PUBLIQUE D'ALBUM (backend/public_web/album.js).
//
// Exécuté par pytest (backend/tests/test_albums_public.py) via `node`.
// Codes de sortie : 0 = PASS · 2 = SKIP (jsdom absent) · 1 = FAIL.
//
// Il prouve SANS navigateur que album.js :
//   1. lit la clé dans l'URL et FETCHE /a/<key>/manifest.json ;
//   2. pose titre / description / compte en TEXT CONTENT (aucune injection
//      HTML : un titre malveillant reste du texte, zéro enfant DOM) ;
//   3. construit la grille depuis les items du manifest (URLs t/ et f/);
//   4. CLIC SIMPLE (kind 'click') → lightbox.openFullscreen — le double-clic
//      n'ouvre rien ; le renderMedia produit un simple <img src=…/f/0001.jpg> ;
//   5. configure la visionneuse 0.2.0 : slideshow {duration,transition,random,
//      loop,keyboard}, transition (crossfade), chrome {icons,autoHide,
//      idleDelay:3000} et libellés i18n passés via `labels` ;
//   6. injecte le bouton diaporama ▶/❚❚ (classes .holaf-lightbox-nav
//      .album-slideshow-btn) dans l'overlay créé par la brique et le pilote
//      par les événements slideshowstart/pause/resume/stop ;
//   7. transmet keydown à lightbox.handleKey (flèches ←/→ dans les deux sens,
//      Espace) UNIQUEMENT quand la visionneuse est ouverte (contrôle négatif) ;
//   8. lit les réglages persistés en localStorage (validés), les applique à
//      l'ouverture, et persiste tout changement du panneau ; une visionneuse
//      fermée est détruite pour reprendre les nouveaux réglages ;
//   9. bascule les textes de la page FR → EN (dictionnaire) + `lang` du
//      document, le panneau restant masqué par défaut ;
//  10. gère les états 404 (album inconnu/révoqué) et erreur réseau.
// ─────────────────────────────────────────────────────────────────────────
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const ALBUM_JS = resolve(HERE, "..", "..", "public_web", "album.js");
const INDEX_HTML = resolve(HERE, "..", "..", "public_web", "index.html");
const KEY = "A".repeat(43);
const STORAGE_KEY = "aih-album-settings-v1";

// ── Résolution de jsdom (mêmes sources que frontend/js/test_helpers) ──────
const require = createRequire(import.meta.url);
function resolveJsdom() {
    const paths = [];
    if (process.env.JSDOM_DIR) paths.push(process.env.JSDOM_DIR);
    paths.push(resolve(HERE, "..", "..", "..", "holaf-lib", "node_modules"));
    paths.push("/projects/holaf-lib/node_modules");
    for (const p of paths) {
        try { return require.resolve("jsdom", { paths: [p] }); } catch { /* suivant */ }
    }
    return null;
}
const jsdomPath = resolveJsdom();
if (!jsdomPath) {
    console.warn("⚠️  test_album_public : jsdom introuvable → SKIP (exit 2). JSDOM_DIR=… pour l'exécuter.");
    process.exit(2);
}
const { JSDOM } = await import(jsdomPath);

const ALBUM_SRC = readFileSync(ALBUM_JS, "utf8");
const INDEX_SRC = readFileSync(INDEX_HTML, "utf8");
assert.ok(ALBUM_JS && existsSync(ALBUM_JS), "album.js introuvable");

/** Monte la page et évalue album.js avec un fetch simulé. */
function mount(fetchImpl, options = {}) {
    const dom = new JSDOM(INDEX_SRC, {
        url: "http://localhost/a/" + KEY,
        runScripts: "outside-only",
        pretendToBeVisual: true,
    });
    const { window } = dom;
    const rec = {
        fetchUrls: [], grid: [], lightbox: [], gridItems: [], lbItems: [],
        opened: [], keys: [], destroyed: 0, slideStartCfg: [], lbState: null,
    };
    if (options.disableStorage) {
        Object.defineProperty(window, "localStorage", {
            configurable: true,
            get() { throw new Error("localStorage indisponible"); },
        });
    } else if (options.storage !== undefined) {
        window.localStorage.setItem(STORAGE_KEY, options.storage);
    }
    window.fetch = (url, opts) => { rec.fetchUrls.push({ url, opts }); return fetchImpl(url, opts); };
    window.HolafViewport = { create: () => ({}) };
    window.HolafGrid = {
        create: (el, opts) => {
            rec.grid.push({ el, opts });
            return { getColumnCount: () => 3, setItems: (items) => { rec.gridItems = items; }, destroy() {} };
        },
    };
    window.HolafLightbox = {
        create: (opts) => {
            rec.lightbox.push(opts);
            const st = { open: false, active: false, paused: false };
            const overlays = [];
            rec.lbState = st;
            const ensureOverlay = () => {
                const overlay = window.document.createElement("div");
                overlay.className = "holaf-lightbox-overlay";
                window.document.body.appendChild(overlay);
                overlays.push(overlay);
                return overlay;
            };
            const api = {
                setItems: (items) => { rec.lbItems = items; },
                openFullscreen: (item) => {
                    st.open = true;
                    ensureOverlay();
                    rec.opened.push({ kind: "fullscreen", item });
                    if (opts.onOpen) opts.onOpen("fullscreen", item);
                },
                isOpen: () => st.open,
                isSlideshow: () => st.active,
                isSlideshowPaused: () => st.paused,
                toggleSlideshow: (cfg) => {
                    if (!st.active) {
                        st.active = true;
                        st.paused = false;
                        rec.slideStartCfg.push(cfg);
                        if (opts.onSlideshowStart) opts.onSlideshowStart(cfg);
                        return true;
                    }
                    if (st.paused) {
                        st.paused = false;
                        if (opts.onSlideshowResume) opts.onSlideshowResume({});
                        return true;
                    }
                    st.paused = true;
                    if (opts.onSlideshowPause) opts.onSlideshowPause({});
                    return true;
                },
                stopSlideshow: () => {
                    st.active = false;
                    st.paused = false;
                    if (opts.onSlideshowStop) opts.onSlideshowStop({ reason: "manual" });
                },
                handleKey: (e) => {
                    rec.keys.push(e.key);
                    if (e.key === "ArrowLeft" || e.key === "ArrowRight") return true;
                    if (e.key === " " || e.key === "Spacebar") {
                        if (st.open && opts.slideshow) { api.toggleSlideshow(opts.slideshow); return true; }
                        return false;
                    }
                    if (e.key === "Escape") { st.open = false; return true; }
                    return false;
                },
                destroy: () => {
                    rec.destroyed += 1;
                    while (overlays.length) {
                        const overlay = overlays.pop();
                        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                    }
                },
            };
            return api;
        },
    };
    window.eval(ALBUM_SRC);
    return { dom, window, rec };
}

const tick = () => new Promise((r) => setTimeout(r, 40));

/** Normalise un objet créé dans la realm jsdom pour deepStrictEqual Node. */
const plain = (value) => JSON.parse(JSON.stringify(value));

function manifestResponse(body, status = 200) {
    return Promise.resolve({
        status,
        ok: status >= 200 && status < 300,
        json: () => Promise.resolve(body),
    });
}

const ITEMS = [
    { i: 1, w: 100, h: 80, ext: ".jpg" },
    { i: 2, w: 90, h: 90, ext: ".png" },
    { i: 3, w: 50, h: 50, ext: ".jpg" },
];

function keydown(window, key) {
    const ev = new window.KeyboardEvent("keydown", { key, cancelable: true, bubbles: true });
    window.document.dispatchEvent(ev);
    return ev;
}

// ── Scénario 1 : album nominal + XSS + plein écran au clic simple ────────
{
    const XSS = '<img src=x onerror="window.__pwned=1">';
    const manifest = {
        title: XSS,
        description: "Une <b>description</b> " + XSS,
        count: 3,
        items: ITEMS,
    };
    const { window, rec } = mount(() => manifestResponse(manifest));
    await tick();

    // 1. Clé lue dans l'URL + fetch du manifest.
    assert.equal(rec.fetchUrls.length, 1, "un seul fetch attendu");
    assert.equal(rec.fetchUrls[0].url, "/a/" + KEY + "/manifest.json");
    assert.equal(rec.fetchUrls[0].opts.cache, "no-store");
    assert.equal(rec.fetchUrls[0].opts.credentials, "omit");

    // 2. Titre/description/compte en TEXT CONTENT (pas d'innerHTML).
    const titleEl = window.document.getElementById("album-title");
    const descEl = window.document.getElementById("album-description");
    const countEl = window.document.getElementById("album-count");
    assert.equal(titleEl.textContent, manifest.title);
    assert.equal(descEl.textContent, manifest.description);
    assert.match(countEl.textContent, /3/);
    // Contrôle NÉGATIF : le HTML malveillant n'a créé AUCUN élément.
    assert.equal(titleEl.children.length, 0, "le titre ne doit créer aucun enfant DOM");
    assert.equal(descEl.querySelector("img"), null, "aucun <img> injecté via description");
    assert.equal(window.__pwned, undefined, "aucun script déclenché");
    assert.equal(window.document.getElementById("album-status").textContent, "");

    // 3. Grille construite depuis le manifest, en clic-simple → activation.
    assert.equal(rec.grid.length, 1, "la grille doit être créée");
    assert.equal(rec.gridItems.length, 3);
    assert.equal(rec.gridItems[0].thumbUrl, "/a/" + KEY + "/t/0001.jpg");
    assert.equal(rec.gridItems[0].fullUrl, "/a/" + KEY + "/f/0001.jpg");
    assert.equal(rec.gridItems[1].fullUrl, "/a/" + KEY + "/f/0002.png");
    assert.equal(rec.gridItems[2].thumbUrl, "/a/" + KEY + "/t/0003.jpg");
    assert.equal(rec.gridItems[0].i, 1);
    assert.equal(rec.grid[0].opts.activateOnClick, true, "clic simple = activation");
    assert.equal(rec.grid[0].opts.selectable, false);
    assert.equal(window.document.getElementById("album-grid").hidden, false);

    // 4. Clic simple (kind 'click') → PLEIN ÉCRAN.
    rec.grid[0].opts.onActivate(rec.gridItems[1], 1, "click");
    assert.equal(rec.opened.length, 1, "un seul openFullscreen attendu");
    assert.equal(rec.opened[0].kind, "fullscreen");
    assert.equal(rec.opened[0].item, rec.gridItems[1]);
    // Contrôle NÉGATIF : un double-clic n'ouvre RIEN.
    rec.grid[0].opts.onActivate(rec.gridItems[2], 2, "dblclick");
    assert.equal(rec.opened.length, 1, "le double-clic ne doit pas ouvrir la visionneuse");

    // 5. Options de la brique 0.2.0 (slideshow / crossfade / chrome / i18n).
    const opts = rec.lightbox[0];
    assert.equal(typeof opts.renderMedia, "function");
    assert.ok(opts.viewport, "le viewport holaf est injecté");
    assert.deepEqual(plain(opts.slideshow), { duration: 4000, transition: 400, random: false, loop: false, keyboard: true });
    assert.equal(opts.transition, 400, "crossfade par défaut");
    assert.deepEqual(plain(opts.chrome), { icons: true, autoHide: true, idleDelay: 3000, fadeDuration: 300 });
    assert.deepEqual(plain(opts.labels), {
        prev: "Image précédente", next: "Image suivante", close: "Fermer", region: "Visionneuse",
    });

    // 6. Bouton diaporama présent (icône seule, a11y par title/aria-label).
    const button = window.document.querySelector(".holaf-lightbox-overlay .album-slideshow-btn");
    assert.ok(button, "le bouton diaporama doit être injecté dans l'overlay");
    assert.ok(button.classList.contains("holaf-lightbox-nav"), "participe au chrome discret");
    assert.equal(button.textContent, "▶");
    assert.equal(button.getAttribute("aria-label"), "Démarrer le diaporama");
    assert.equal(window.document.querySelectorAll(".album-slideshow-btn").length, 1);
    // ▶ → ❚❚ (démarrage) → ▶ (pause) → ❚❚ (reprise).
    button.click();
    assert.equal(rec.lbState.active, true);
    assert.equal(button.textContent, "❚❚");
    assert.equal(button.getAttribute("aria-label"), "Mettre le diaporama en pause");
    button.click();
    assert.equal(rec.lbState.paused, true);
    assert.equal(button.textContent, "▶");
    assert.equal(button.getAttribute("aria-label"), "Reprendre le diaporama");
    button.click();
    assert.equal(rec.lbState.paused, false);
    assert.equal(button.textContent, "❚❚");
    // La session reçoit une copie des réglages courants.
    assert.deepEqual(plain(rec.slideStartCfg[0]), { duration: 4000, transition: 400, random: false, loop: false, keyboard: true });

    // 7. Clavier : flèches transmises à la brique (les DEUX sens) + Espace.
    rec.keys.length = 0;
    assert.equal(keydown(window, "ArrowRight").defaultPrevented, true, "flèche droite consommée");
    assert.equal(keydown(window, "ArrowLeft").defaultPrevented, true, "flèche gauche consommée");
    assert.equal(keydown(window, " ").defaultPrevented, true, "espace consommé (diaporama configuré)");
    assert.deepEqual(rec.keys, ["ArrowRight", "ArrowLeft", " "]);

    // 8. renderMedia = simple <img src=…/f/…>.
    const container = window.document.createElement("div");
    const res = opts.renderMedia({ container, item: rec.gridItems[0], signal: { aborted: false }, onReady: () => {} });
    assert.equal(res.el.tagName, "IMG");
    assert.equal(res.el.getAttribute("src"), rec.gridItems[0].fullUrl);
    assert.equal(container.firstChild, res.el);
    res.destroy();
    assert.equal(container.firstChild, null);

    // 9. Le panneau est MASQUÉ par défaut ; l'engrenage l'ouvre/le ferme.
    const panel = window.document.getElementById("album-config");
    const toggle = window.document.getElementById("album-config-toggle");
    assert.equal(panel.hidden, true, "panneau masqué au chargement");
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    toggle.click();
    assert.equal(panel.hidden, false);
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    // Échap (visionneuse fermée ici ? non : ouverte → transmis à la brique).
    rec.lbState.open = false; // simule la fermeture de la visionneuse
    assert.equal(keydown(window, "Escape").defaultPrevented, true, "Échap ferme le panneau");
    assert.equal(panel.hidden, true);
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
}

// ── Scénario 2 : réglages persistés (EN) relus et appliqués ──────────────
{
    const STORED = JSON.stringify({ lang: "en", duration: 2000, transition: 100, random: true, loop: true });
    const manifest = { title: "Album", description: "", count: 3, items: ITEMS };
    const { window, rec } = mount(() => manifestResponse(manifest), { storage: STORED });
    await tick();

    // Textes de page en anglais + lang du document mis à jour.
    assert.equal(window.document.documentElement.getAttribute("lang"), "en");
    assert.equal(window.document.getElementById("album-config-title").textContent, "Settings");
    assert.equal(window.document.getElementById("album-config-close").textContent, "Close");
    assert.equal(window.document.getElementById("album-config-toggle").getAttribute("aria-label"), "Settings");
    // Formulaire pré-rempli depuis le stockage.
    assert.equal(window.document.getElementById("album-config-lang").value, "en");
    assert.equal(window.document.getElementById("album-config-duration").value, "2000");
    assert.equal(window.document.getElementById("album-config-transition").value, "100");
    assert.equal(window.document.getElementById("album-config-random").checked, true);
    assert.equal(window.document.getElementById("album-config-loop").checked, true);
    assert.equal(window.document.getElementById("album-config-duration-value").textContent, "2.0 s");
    // Réglages appliqués à l'ouverture de la visionneuse.
    rec.grid[0].opts.onActivate(rec.gridItems[0], 0, "click");
    const opts = rec.lightbox[0];
    assert.deepEqual(plain(opts.slideshow), { duration: 2000, transition: 100, random: true, loop: true, keyboard: true });
    assert.equal(opts.transition, 100);
    assert.deepEqual(plain(opts.labels), {
        prev: "Previous image", next: "Next image", close: "Close", region: "Viewer",
    });
    assert.equal(window.document.querySelector(".album-slideshow-btn").getAttribute("aria-label"), "Start slideshow");
    // Le compte singulier/pluriel suit la langue (texte de page, pas le manifest).
    assert.match(window.document.getElementById("album-count").textContent, /3 photos/);
}

// ── Scénario 3 : changement de réglages → persisté + visionneuse réarmée ─
{
    const manifest = { title: "Album", description: "", count: 3, items: ITEMS };
    const { window, rec } = mount(() => manifestResponse(manifest));
    await tick();

    // Ouverture 1 (réglages par défaut FR).
    rec.grid[0].opts.onActivate(rec.gridItems[0], 0, "click");
    assert.equal(rec.lightbox.length, 1);
    assert.equal(rec.lightbox[0].transition, 400);

    // Changement via le panneau (un seul 'change' : le formulaire est relu en entier).
    window.document.getElementById("album-config-lang").value = "en";
    window.document.getElementById("album-config-duration").value = "6000";
    window.document.getElementById("album-config-transition").value = "0";
    window.document.getElementById("album-config-random").checked = true;
    window.document.getElementById("album-config-loop").checked = true;
    window.document.getElementById("album-config-lang")
        .dispatchEvent(new window.Event("change", { bubbles: true }));

    // Persisté + textes basculés + sortie de transition lisible.
    const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY));
    assert.deepEqual(saved, { lang: "en", duration: 6000, transition: 0, random: true, loop: true });
    assert.equal(window.document.documentElement.getAttribute("lang"), "en");
    assert.equal(window.document.getElementById("album-config-transition-value").textContent, "Cut (no fade)");
    assert.equal(window.document.getElementById("album-config-duration-value").textContent, "6.0 s");

    // Vue OUVERTE au moment du changement → pas de destruction de la brique…
    assert.equal(rec.destroyed, 0, "pas de destruction tant que la vue est ouverte");

    // … puis visionneuse FERMÉE → détruite pour reprendre les réglages à la réouverture.
    rec.lbState.open = false;
    window.document.getElementById("album-config-lang")
        .dispatchEvent(new window.Event("change", { bubbles: true }));
    assert.equal(rec.destroyed, 1, "visionneuse fermée détruite au changement de réglages");

    // Ouverture 2 : nouvelles options de brique.
    rec.grid[0].opts.onActivate(rec.gridItems[1], 1, "click");
    assert.equal(rec.lightbox.length, 2);
    const opts = rec.lightbox[1];
    assert.deepEqual(plain(opts.slideshow), { duration: 6000, transition: 0, random: true, loop: true, keyboard: true });
    assert.equal(opts.transition, 0);
    assert.equal(opts.labels.next, "Next image");
}

// ── Scénario 4 : contrôle négatif clavier (visionneuse fermée) ───────────
{
    const manifest = { title: "Album", description: "", count: 3, items: ITEMS };
    const { window, rec } = mount(() => manifestResponse(manifest));
    await tick();

    // NÉGATIF : visionneuse fermée → aucune touche n'est consommée ni transmise.
    assert.equal(keydown(window, "ArrowRight").defaultPrevented, false);
    assert.equal(rec.keys.length, 0, "aucun handleKey sans visionneuse ouverte");
    assert.equal(rec.lightbox.length, 0, "la brique n'est créée qu'à la première ouverture");
}

// ── Scénario 5 : localStorage corrompu/hors bornes → défauts sûrs ────────
{
    const manifest = { title: "Album", description: "", count: 3, items: ITEMS };
    const cases = [
        // JSON illisible → défauts complets.
        { stored: "{pas du json", duration: "4000", transition: "400" },
        // Valeurs hors bornes / types douteux → bornées, langue inconnue → fr.
        {
            stored: JSON.stringify({ lang: "de", duration: 999999, transition: -5, random: "oui", loop: 1 }),
            duration: "20000", transition: "0",
        },
    ];
    for (const cas of cases) {
        const { window } = mount(() => manifestResponse(manifest), { storage: cas.stored });
        await tick();
        assert.equal(window.document.getElementById("album-config-lang").value, "fr", "langue inconnue → fr");
        assert.equal(window.document.getElementById("album-config-duration").value, cas.duration, "durée bornée/par défaut");
        assert.equal(window.document.getElementById("album-config-transition").value, cas.transition, "transition bornée/par défaut");
        assert.equal(window.document.getElementById("album-config-random").checked, false, "booléen strict");
        assert.equal(window.document.getElementById("album-config-loop").checked, false, "booléen strict");
    }
}

// ── Scénario 5bis : localStorage indisponible → défauts, aucun crash ─────
{
    const manifest = { title: "Album", description: "", count: 3, items: ITEMS };
    const { window } = mount(() => manifestResponse(manifest), { disableStorage: true });
    await tick();
    assert.equal(window.document.getElementById("album-config-lang").value, "fr");
    // Un changement doit s'appliquer à la session même sans stockage.
    window.document.getElementById("album-config-duration").value = "6000";
    window.document.getElementById("album-config-duration")
        .dispatchEvent(new window.Event("change", { bubbles: true }));
    assert.equal(window.document.getElementById("album-config-duration-value").textContent, "6,0 s");
}

// ── Scénario 6 : manifest 404 → état « album introuvable » ───────────────
{
    const { window } = mount(() => manifestResponse({}, 404));
    await tick();
    const status = window.document.getElementById("album-status");
    assert.match(status.textContent, /introuvable/i);
    assert.equal(window.document.getElementById("album-grid").hidden, true);
}

// ── Scénario 7 : erreur réseau → état « erreur de chargement » ───────────
{
    const { window } = mount(() => Promise.reject(new Error("network")));
    await tick();
    assert.match(window.document.getElementById("album-status").textContent, /erreur/i);
}

// ── Scénario 8 : album vide → état dédié ─────────────────────────────────
{
    const manifest = { title: "Vide", description: "", count: 0, items: [] };
    const { window, rec } = mount(() => manifestResponse(manifest));
    await tick();
    assert.match(window.document.getElementById("album-status").textContent, /vide/i);
    assert.equal(rec.grid.length, 0, "aucune grille pour un album vide");
}

console.log("✅ test_album_public : album.js — titre/description en textContent, clic simple → plein écran, "
    + "flèches/espace transmis, diaporama ▶/❚❚, crossfade/chrome, réglages localStorage FR/EN, états 404/vide/erreur OK.");
process.exit(0);
