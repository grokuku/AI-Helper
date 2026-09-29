/* ═══════════════════════════════════════════════════════════════════════════
 * Holaf UI — Brique HolafQrcode · version 0.1.0
 * ─────────────────────────────────────────────────────────────────────────────
 * Générateur de QR codes AUTO-HÉBERGÉ (zéro dépendance runtime, zéro service
 * tiers, aucune requête réseau) : encode une chaîne, construit la matrice
 * ISO/IEC 18004 et la rend en SVG (défaut), en <canvas> ou en dataURL.
 *
 * Usages typiques : partage d'un lien (URL d'album public, deeplink,
 * invitation) — la donnée ne quitte JAMAIS le poste qui affiche le code.
 *
 * CONFORMITÉ
 *   - mode byte uniquement (les URL/textes UTF-8), sans ECI (les octets sont
 *     de l'UTF-8, interprétation retenue par la quasi-totalité des lecteurs) ;
 *   - versions 1 à 40, niveaux de correction L/M/Q/H ;
 *   - comptage des caractères 8 bits (v1-9) / 16 bits (v10-40) ;
 *   - Reed-Solomon sur GF(256) (polynôme 0x11D), blocs entrelacés ;
 *   - sélection automatique du masque par ÉVALUATION DES PÉNALITÉS
 *     (ISO 18004 §8.8.2, les 4 règles) — ou masque FORCÉ (option `mask`) ;
 *   - motifs de synchronisation (finders/separators/timing/alignment), module
 *     sombre, informations de format et de version (BCH).
 *
 * VALIDATION : tests par DÉCODAGE ALLER-RETOUR (jsQR, devDependency de test
 * uniquement) sur URLs courtes/longues, UTF-8, caractères spéciaux, et sur un
 * balayage des 40 versions × 4 niveaux, + comparaison matricielle croisée avec
 * l'implémentation MIT `qrcode-generator` (oracle de test, cf. README).
 *
 * ATTRIBUTION : l'algorithme suit la norme ISO/IEC 18004. Les tables de
 * structure Reed-Solomon et de positions d'alignement sont des DONNÉES de la
 * norme, recoupées avec l'implémentation MIT « qrcode-generator » de
 * Kazuhiko Arase (https://github.com/kazuhikoarase/qrcode-generator) — aucun
 * code de cette bibliothèque n'est embarqué. La brique n'embarque AUCUN code
 * tiers : rien à ajouter aux THIRD-PARTY-NOTICES d'un projet consommateur.
 *
 * LIMITES (assumées, documentées) : pas de modes numeric/alphanumeric/Kanji,
 * pas de QR Micro, pas de structured append, pas d'ECI, une seule chaîne.
 *
 * Zéro dépendance runtime, aucun import croisé.
 *
 * CSS-INJECTANTE (minuscule) : getCss() expose le CSS ; l'option
 * { injectStyles:false, nonce } (ou HolafQrcode.configure/setStyleNonce)
 * contrôle l'injection. CSS scopé .holaf-qrcode*, JAMAIS :root.
 *
 * Fichier DUAL : export ES + global window.HolafQrcode — se charge via
 * <script type="module"> ou `import { HolafQrcode }`.
 * ═════════════════════════════════════════════════════════════════════════ */

const VERSION = "0.1.0";

const HolafQrcode = (function () {
    "use strict";

    const CSS_ID = "holaf-qrcode-style";

    // ─── CSS auto-injecté (une seule fois pour tout le module) ──────────────
    // Classes scopées .holaf-qrcode* ; aucune variable posée sur :root.
    const CSS = `
.holaf-qrcode { display: block; max-width: 100%; height: auto; }
.holaf-qrcode--inline { display: inline-block; vertical-align: middle; }
`;

    // ─── Nonce CSP (OPTIONNEL, patron des autres briques) ───────────────────
    let styleNonce = null;
    let injectStylesGlobal = true;

    function normalizeNonce(value) {
        if (value === null || value === undefined || value === "") return null;
        return String(value);
    }
    function readNonce(el) {
        if (typeof el.nonce === "string") return normalizeNonce(el.nonce);
        return normalizeNonce(el.getAttribute("nonce"));
    }
    function setStyleNonce(nonce) { styleNonce = normalizeNonce(nonce); }

    function ensureStyle(nonceOpt) {
        if (typeof document === "undefined") return;
        const effective = nonceOpt === undefined ? styleNonce : normalizeNonce(nonceOpt);
        let style = document.getElementById(CSS_ID);
        const current = style ? readNonce(style) : null;
        if (style && current === effective) return;
        if (style && style.parentNode) style.parentNode.removeChild(style);
        style = document.createElement("style");
        style.id = CSS_ID;
        if (effective) style.setAttribute("nonce", effective);
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    function getCss() { return CSS; }

    function configure(opts) {
        opts = opts || {};
        if (opts.injectStyles !== undefined) injectStylesGlobal = opts.injectStyles !== false;
        if (opts.nonce !== undefined) setStyleNonce(opts.nonce);
    }

    function resolveInjectStyles(opts) {
        if (opts && opts.injectStyles !== undefined) return opts.injectStyles !== false;
        return injectStylesGlobal;
    }

    // ─── Constantes normatives ───────────────────────────────────────────────
    const MAX_VERSION = 40;
    const MODE_BYTE = 0x4; // indicateur de mode « byte » (0100)
    const PAD_BYTES = [0xEC, 0x11];
    // Bits d'information de format (ISO 18004 §8.9) : M=00, L=01, H=10, Q=11.
    const ECC_FORMAT_BITS = { L: 0x1, M: 0x0, Q: 0x3, H: 0x2 };
    // Ordre des niveaux dans RS_BLOCK_GROUPS (4 entrées par version).
    const ECC_ORDER = ["L", "M", "Q", "H"];
    // Couleurs par défaut du rendu.
    const DEFAULT_DARK = "#111827";
    const DEFAULT_LIGHT = "#ffffff";
    const DEFAULT_MARGIN = 4; // zone silencieuse ISO (4 modules)
    const DEFAULT_SIZE = 200; // côté du SVG en px CSS
    const DEFAULT_SCALE = 8; // px par module pour <canvas>

    // ─── Tables normatives ───────────────────────────────────────────────────
    // Structures de blocs Reed-Solomon (ISO/IEC 18004, tableau 9) : pour chaque
    // version 1..40, dans l'ordre L, M, Q, H, une suite de triplets
    // [nbBlocs, totalMotsCode, motsDonnées] (deux groupes possibles).
    // Recoupées avec qrcode-generator (MIT) — cf. en-tête/README.
    const RS_BLOCK_GROUPS = [
        // v1 : L, M, Q, H
        [1, 26, 19], [1, 26, 16], [1, 26, 13], [1, 26, 9],
        // v2 : L, M, Q, H
        [1, 44, 34], [1, 44, 28], [1, 44, 22], [1, 44, 16],
        // v3 : L, M, Q, H
        [1, 70, 55], [1, 70, 44], [2, 35, 17], [2, 35, 13],
        // v4 : L, M, Q, H
        [1, 100, 80], [2, 50, 32], [2, 50, 24], [4, 25, 9],
        // v5 : L, M, Q, H
        [1, 134, 108], [2, 67, 43], [2, 33, 15, 2, 34, 16], [2, 33, 11, 2, 34, 12],
        // v6 : L, M, Q, H
        [2, 86, 68], [4, 43, 27], [4, 43, 19], [4, 43, 15],
        // v7 : L, M, Q, H
        [2, 98, 78], [4, 49, 31], [2, 32, 14, 4, 33, 15], [4, 39, 13, 1, 40, 14],
        // v8 : L, M, Q, H
        [2, 121, 97], [2, 60, 38, 2, 61, 39], [4, 40, 18, 2, 41, 19], [4, 40, 14, 2, 41, 15],
        // v9 : L, M, Q, H
        [2, 146, 116], [3, 58, 36, 2, 59, 37], [4, 36, 16, 4, 37, 17], [4, 36, 12, 4, 37, 13],
        // v10 : L, M, Q, H
        [2, 86, 68, 2, 87, 69], [4, 69, 43, 1, 70, 44], [6, 43, 19, 2, 44, 20], [6, 43, 15, 2, 44, 16],
        // v11 : L, M, Q, H
        [4, 101, 81], [1, 80, 50, 4, 81, 51], [4, 50, 22, 4, 51, 23], [3, 36, 12, 8, 37, 13],
        // v12 : L, M, Q, H
        [2, 116, 92, 2, 117, 93], [6, 58, 36, 2, 59, 37], [4, 46, 20, 6, 47, 21], [7, 42, 14, 4, 43, 15],
        // v13 : L, M, Q, H
        [4, 133, 107], [8, 59, 37, 1, 60, 38], [8, 44, 20, 4, 45, 21], [12, 33, 11, 4, 34, 12],
        // v14 : L, M, Q, H
        [3, 145, 115, 1, 146, 116], [4, 64, 40, 5, 65, 41], [11, 36, 16, 5, 37, 17], [11, 36, 12, 5, 37, 13],
        // v15 : L, M, Q, H
        [5, 109, 87, 1, 110, 88], [5, 65, 41, 5, 66, 42], [5, 54, 24, 7, 55, 25], [11, 36, 12, 7, 37, 13],
        // v16 : L, M, Q, H
        [5, 122, 98, 1, 123, 99], [7, 73, 45, 3, 74, 46], [15, 43, 19, 2, 44, 20], [3, 45, 15, 13, 46, 16],
        // v17 : L, M, Q, H
        [1, 135, 107, 5, 136, 108], [10, 74, 46, 1, 75, 47], [1, 50, 22, 15, 51, 23], [2, 42, 14, 17, 43, 15],
        // v18 : L, M, Q, H
        [5, 150, 120, 1, 151, 121], [9, 69, 43, 4, 70, 44], [17, 50, 22, 1, 51, 23], [2, 42, 14, 19, 43, 15],
        // v19 : L, M, Q, H
        [3, 141, 113, 4, 142, 114], [3, 70, 44, 11, 71, 45], [17, 47, 21, 4, 48, 22], [9, 39, 13, 16, 40, 14],
        // v20 : L, M, Q, H
        [3, 135, 107, 5, 136, 108], [3, 67, 41, 13, 68, 42], [15, 54, 24, 5, 55, 25], [15, 43, 15, 10, 44, 16],
        // v21 : L, M, Q, H
        [4, 144, 116, 4, 145, 117], [17, 68, 42], [17, 50, 22, 6, 51, 23], [19, 46, 16, 6, 47, 17],
        // v22 : L, M, Q, H
        [2, 139, 111, 7, 140, 112], [17, 74, 46], [7, 54, 24, 16, 55, 25], [34, 37, 13],
        // v23 : L, M, Q, H
        [4, 151, 121, 5, 152, 122], [4, 75, 47, 14, 76, 48], [11, 54, 24, 14, 55, 25], [16, 45, 15, 14, 46, 16],
        // v24 : L, M, Q, H
        [6, 147, 117, 4, 148, 118], [6, 73, 45, 14, 74, 46], [11, 54, 24, 16, 55, 25], [30, 46, 16, 2, 47, 17],
        // v25 : L, M, Q, H
        [8, 132, 106, 4, 133, 107], [8, 75, 47, 13, 76, 48], [7, 54, 24, 22, 55, 25], [22, 45, 15, 13, 46, 16],
        // v26 : L, M, Q, H
        [10, 142, 114, 2, 143, 115], [19, 74, 46, 4, 75, 47], [28, 50, 22, 6, 51, 23], [33, 46, 16, 4, 47, 17],
        // v27 : L, M, Q, H
        [8, 152, 122, 4, 153, 123], [22, 73, 45, 3, 74, 46], [8, 53, 23, 26, 54, 24], [12, 45, 15, 28, 46, 16],
        // v28 : L, M, Q, H
        [3, 147, 117, 10, 148, 118], [3, 73, 45, 23, 74, 46], [4, 54, 24, 31, 55, 25], [11, 45, 15, 31, 46, 16],
        // v29 : L, M, Q, H
        [7, 146, 116, 7, 147, 117], [21, 73, 45, 7, 74, 46], [1, 53, 23, 37, 54, 24], [19, 45, 15, 26, 46, 16],
        // v30 : L, M, Q, H
        [5, 145, 115, 10, 146, 116], [19, 75, 47, 10, 76, 48], [15, 54, 24, 25, 55, 25], [23, 45, 15, 25, 46, 16],
        // v31 : L, M, Q, H
        [13, 145, 115, 3, 146, 116], [2, 74, 46, 29, 75, 47], [42, 54, 24, 1, 55, 25], [23, 45, 15, 28, 46, 16],
        // v32 : L, M, Q, H
        [17, 145, 115], [10, 74, 46, 23, 75, 47], [10, 54, 24, 35, 55, 25], [19, 45, 15, 35, 46, 16],
        // v33 : L, M, Q, H
        [17, 145, 115, 1, 146, 116], [14, 74, 46, 21, 75, 47], [29, 54, 24, 19, 55, 25], [11, 45, 15, 46, 46, 16],
        // v34 : L, M, Q, H
        [13, 145, 115, 6, 146, 116], [14, 74, 46, 23, 75, 47], [44, 54, 24, 7, 55, 25], [59, 46, 16, 1, 47, 17],
        // v35 : L, M, Q, H
        [12, 151, 121, 7, 152, 122], [12, 75, 47, 26, 76, 48], [39, 54, 24, 14, 55, 25], [22, 45, 15, 41, 46, 16],
        // v36 : L, M, Q, H
        [6, 151, 121, 14, 152, 122], [6, 75, 47, 34, 76, 48], [46, 54, 24, 10, 55, 25], [2, 45, 15, 64, 46, 16],
        // v37 : L, M, Q, H
        [17, 152, 122, 4, 153, 123], [29, 74, 46, 14, 75, 47], [49, 54, 24, 10, 55, 25], [24, 45, 15, 46, 46, 16],
        // v38 : L, M, Q, H
        [4, 152, 122, 18, 153, 123], [13, 74, 46, 32, 75, 47], [48, 54, 24, 14, 55, 25], [42, 45, 15, 32, 46, 16],
        // v39 : L, M, Q, H
        [20, 147, 117, 4, 148, 118], [40, 75, 47, 7, 76, 48], [43, 54, 24, 22, 55, 25], [10, 45, 15, 67, 46, 16],
        // v40 : L, M, Q, H
        [19, 148, 118, 6, 149, 119], [18, 75, 47, 31, 76, 48], [34, 54, 24, 34, 55, 25], [20, 45, 15, 61, 46, 16],
    ]

    // Positions des motifs d'alignement (ISO/IEC 18004, annexe E), par version.
    // v1 n'a pas de motif d'alignement ; les couples formés ici excluent ceux
    // qui chevaucheraient un finder.
    const ALIGNMENT_POSITIONS = [
        [], // v1
        [6, 18], // v2
        [6, 22], // v3
        [6, 26], // v4
        [6, 30], // v5
        [6, 34], // v6
        [6, 22, 38], // v7
        [6, 24, 42], // v8
        [6, 26, 46], // v9
        [6, 28, 50], // v10
        [6, 30, 54], // v11
        [6, 32, 58], // v12
        [6, 34, 62], // v13
        [6, 26, 46, 66], // v14
        [6, 26, 48, 70], // v15
        [6, 26, 50, 74], // v16
        [6, 30, 54, 78], // v17
        [6, 30, 56, 82], // v18
        [6, 30, 58, 86], // v19
        [6, 34, 62, 90], // v20
        [6, 28, 50, 72, 94], // v21
        [6, 26, 50, 74, 98], // v22
        [6, 30, 54, 78, 102], // v23
        [6, 28, 54, 80, 106], // v24
        [6, 32, 58, 84, 110], // v25
        [6, 30, 58, 86, 114], // v26
        [6, 34, 62, 90, 118], // v27
        [6, 26, 50, 74, 98, 122], // v28
        [6, 30, 54, 78, 102, 126], // v29
        [6, 26, 52, 78, 104, 130], // v30
        [6, 30, 56, 82, 108, 134], // v31
        [6, 34, 60, 86, 112, 138], // v32
        [6, 30, 58, 86, 114, 142], // v33
        [6, 34, 62, 90, 118, 146], // v34
        [6, 30, 54, 78, 102, 126, 150], // v35
        [6, 24, 50, 76, 102, 128, 154], // v36
        [6, 28, 54, 80, 106, 132, 158], // v37
        [6, 32, 58, 84, 110, 136, 162], // v38
        [6, 26, 54, 82, 110, 138, 166], // v39
        [6, 30, 58, 86, 114, 142, 170], // v40
    ]

    // ─── GF(256) / Reed-Solomon ──────────────────────────────────────────────
    const GF_EXP = new Uint8Array(512);
    const GF_LOG = new Uint8Array(256);
    (function initGf() {
        let x = 1;
        for (let i = 0; i < 255; i++) {
            GF_EXP[i] = x;
            GF_LOG[x] = i;
            x <<= 1;
            if (x & 0x100) x ^= 0x11d;
        }
        for (let i = 255; i < 512; i++) GF_EXP[i] = GF_EXP[i - 255];
    })();

    function gfMul(a, b) {
        if (a === 0 || b === 0) return 0;
        return GF_EXP[GF_LOG[a] + GF_LOG[b]];
    }

    const generatorCache = Object.create(null);

    /** Polynôme générateur RS de degré `ecLen` : g(x) = ∏ (x − α^i). */
    function rsGenerator(ecLen) {
        if (generatorCache[ecLen]) return generatorCache[ecLen];
        let poly = [1];
        for (let i = 0; i < ecLen; i++) {
            const next = new Array(poly.length + 1).fill(0);
            for (let j = 0; j < poly.length; j++) {
                next[j] ^= poly[j];
                next[j + 1] ^= gfMul(poly[j], GF_EXP[i % 255]);
            }
            poly = next;
        }
        generatorCache[ecLen] = poly;
        return poly;
    }

    /** Reste de la division du message par le générateur (mots de correction). */
    function rsEncode(data, ecLen) {
        const gen = rsGenerator(ecLen);
        const rem = new Uint8Array(ecLen);
        for (let i = 0; i < data.length; i++) {
            const factor = data[i] ^ rem[0];
            rem.copyWithin(0, 1);
            rem[ecLen - 1] = 0;
            if (factor !== 0) {
                for (let j = 0; j < ecLen; j++) rem[j] ^= gfMul(gen[j + 1], factor);
            }
        }
        return rem;
    }

    // ─── Validation des options ──────────────────────────────────────────────
    function fail(Type, message) {
        throw new Type("[HolafQrcode] " + message);
    }

    /** Niveau de correction normalisé ("L"|"M"|"Q"|"H") ; défaut "M". */
    function normalizeEcc(value) {
        if (value === undefined || value === null || value === "") return "M";
        const ecc = String(value).toUpperCase();
        if (ECC_ORDER.indexOf(ecc) === -1) {
            fail(TypeError, "niveau de correction inconnu : \"" + value +
                "\" (attendu : L, M, Q ou H).");
        }
        return ecc;
    }

    function normalizeVersion(value) {
        if (value === undefined || value === null || value === 0) return 0;
        const v = Number(value);
        if (!Number.isInteger(v) || v < 1 || v > MAX_VERSION) {
            fail(TypeError, "version invalide : \"" + value + "\" (entier 1 à " + MAX_VERSION +
                ", ou 0/absent pour automatique).");
        }
        return v;
    }

    function normalizeMask(value) {
        if (value === undefined || value === null) return null;
        const m = Number(value);
        if (!Number.isInteger(m) || m < 0 || m > 7) {
            fail(TypeError, "masque invalide : \"" + value + "\" (entier 0 à 7, ou null pour automatique).");
        }
        return m;
    }

    /**
     * Encode une chaîne en octets UTF-8 (TextEncoder si présent, repli manuel).
     * L'UTF-8 est le jeu d'octets standard des URL ; aucun ECI n'est émis.
     */
    function utf8Bytes(text) {
        if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(text);
        const out = [];
        for (let i = 0; i < text.length; i++) {
            let code = text.charCodeAt(i);
            if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
                const low = text.charCodeAt(i + 1);
                if (low >= 0xdc00 && low <= 0xdfff) {
                    code = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
                    i++;
                }
            }
            if (code < 0x80) out.push(code);
            else if (code < 0x800) out.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
            else if (code < 0x10000) {
                out.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
            } else {
                out.push(0xf0 | (code >> 18), 0x80 | ((code >> 12) & 0x3f),
                    0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
            }
        }
        return Uint8Array.from(out);
    }

    // ─── Structures de blocs / capacités ─────────────────────────────────────
    /** Blocs { total, data } d'une version/niveau (ordre L, M, Q, H). */
    function rsBlocks(version, ecc) {
        const groups = RS_BLOCK_GROUPS[(version - 1) * 4 + ECC_ORDER.indexOf(ecc)];
        const blocks = [];
        for (let i = 0; i < groups.length; i += 3) {
            const count = groups[i];
            const total = groups[i + 1];
            const data = groups[i + 2];
            for (let b = 0; b < count; b++) blocks.push({ total: total, data: data });
        }
        return blocks;
    }

    /** Nombre de mots de données (avant correction) d'une version/niveau. */
    function dataCodewords(version, ecc) {
        return rsBlocks(version, ecc).reduce(function (sum, b) { return sum + b.data; }, 0);
    }

    /** Bits de l'indicateur de nombre de caractères : 8 (v1-9) ou 16 (v10-40). */
    function countIndicatorBits(version) {
        return version < 10 ? 8 : 16;
    }

    /**
     * Capacité utile d'une version/niveau, en OCTETS de texte (mode byte,
     * en-tête compté). Utile pour choisir une version ou vérifier une limite.
     * @returns {number} nombre max d'octets encodables (0 si hors norme).
     */
    function capacity(opts) {
        const o = opts || {};
        const version = normalizeVersion(o.version);
        const ecc = normalizeEcc(o.ecc);
        const v = version || MAX_VERSION;
        const cci = countIndicatorBits(v);
        const bits = dataCodewords(v, ecc) * 8 - 4 - cci;
        return Math.max(0, Math.floor(bits / 8));
    }

    /** Version minimale acceptant `byteLen` octets, ou 0 si aucune (v40 = max). */
    function minimalVersion(byteLen, ecc) {
        for (let v = 1; v <= MAX_VERSION; v++) {
            if (byteLen <= capacity({ version: v, ecc: ecc })) return v;
        }
        return 0;
    }

    // ─── Construction des mots de code (données + correction) ────────────────
    function buildCodewords(bytes, version, ecc) {
        const blocks = rsBlocks(version, ecc);
        const totalData = blocks.reduce(function (sum, b) { return sum + b.data; }, 0);
        const capacityBits = totalData * 8;
        const cci = countIndicatorBits(version);
        const neededBits = 4 + cci + bytes.length * 8;
        if (neededBits > capacityBits) {
            fail(RangeError, bytes.length + " octet(s) ne tiennent pas en version " + version +
                " niveau " + ecc + " (capacité " + Math.floor((capacityBits - 4 - cci) / 8) + " octets).");
        }

        const bits = [];
        function push(value, length) {
            for (let i = length - 1; i >= 0; i--) bits.push((value >> i) & 1);
        }
        push(MODE_BYTE, 4);
        push(bytes.length, cci);
        for (let i = 0; i < bytes.length; i++) push(bytes[i], 8);

        // Terminateur (≤ 4 zéros), alignement octet, remplissage 0xEC/0x11.
        const terminator = Math.min(4, capacityBits - bits.length);
        for (let i = 0; i < terminator; i++) bits.push(0);
        while (bits.length % 8 !== 0) bits.push(0);

        const dataBytes = [];
        for (let i = 0; i < bits.length; i += 8) {
            let byte = 0;
            for (let j = 0; j < 8; j++) byte = (byte << 1) | bits[i + j];
            dataBytes.push(byte);
        }
        let padIndex = 0;
        while (dataBytes.length < totalData) {
            dataBytes.push(PAD_BYTES[padIndex % 2]);
            padIndex++;
        }

        // Correction d'erreur par bloc, puis ENTRELACEMENT (données puis EC).
        const dataBlocks = [];
        const eccBlocks = [];
        let offset = 0;
        for (let i = 0; i < blocks.length; i++) {
            const data = Uint8Array.from(dataBytes.slice(offset, offset + blocks[i].data));
            offset += blocks[i].data;
            dataBlocks.push(data);
            eccBlocks.push(rsEncode(data, blocks[i].total - blocks[i].data));
        }
        const out = [];
        let maxData = 0;
        for (let i = 0; i < dataBlocks.length; i++) maxData = Math.max(maxData, dataBlocks[i].length);
        for (let i = 0; i < maxData; i++) {
            for (let b = 0; b < dataBlocks.length; b++) {
                if (i < dataBlocks[b].length) out.push(dataBlocks[b][i]);
            }
        }
        const ecLen = eccBlocks.length ? eccBlocks[0].length : 0;
        for (let i = 0; i < ecLen; i++) {
            for (let b = 0; b < eccBlocks.length; b++) out.push(eccBlocks[b][i]);
        }
        return { blocks: blocks, codewords: Uint8Array.from(out) };
    }

    // ─── Matrice : motifs fonctionnels, placement, masques, format ───────────
    function bitLength(n) {
        let len = 0;
        while (n !== 0) { len++; n >>>= 1; }
        return len;
    }

    const G15 = 0x537; // x^10+x^8+x^5+x^4+x^2+x+1
    const G15_MASK = 0x5412;
    const G18 = 0x1f25; // x^12+x^11+x^10+x^9+x^8+x^5+x^2+1

    /** 15 bits d'information de format (BCH(15,5), masque 0x5412). */
    function formatBits(eccBits, mask) {
        const data = ((eccBits & 0x3) << 3) | (mask & 0x7);
        let d = data << 10;
        while (bitLength(d) - bitLength(G15) >= 0) d ^= G15 << (bitLength(d) - bitLength(G15));
        return ((data << 10) | d) ^ G15_MASK;
    }

    /** 18 bits d'information de version (BCH(18,6)) — versions 7+. */
    function versionBits(version) {
        let d = version << 12;
        while (bitLength(d) - bitLength(G18) >= 0) d ^= G18 << (bitLength(d) - bitLength(G18));
        return (version << 12) | d;
    }

    /** Le module (row, col) porte-t-il le masque n° `mask` ? */
    function maskAt(mask, row, col) {
        switch (mask) {
            case 0: return (row + col) % 2 === 0;
            case 1: return row % 2 === 0;
            case 2: return col % 3 === 0;
            case 3: return (row + col) % 3 === 0;
            case 4: return (Math.floor(row / 2) + Math.floor(col / 3)) % 2 === 0;
            case 5: return ((row * col) % 2) + ((row * col) % 3) === 0;
            case 6: return (((row * col) % 2) + ((row * col) % 3)) % 2 === 0;
            case 7: return (((row + col) % 2) + ((row * col) % 3)) % 2 === 0;
            default: return false;
        }
    }

    /** Matrice de base : motifs fonctionnels posés, emplacements réservés. */
    function buildBaseMatrix(version) {
        const size = version * 4 + 17;
        const modules = new Uint8Array(size * size);
        const reserved = new Uint8Array(size * size);

        function set(row, col, dark) { modules[row * size + col] = dark ? 1 : 0; }
        function reserve(row, col) { reserved[row * size + col] = 1; }

        function placeFinder(top, left) {
            for (let r = -1; r <= 7; r++) {
                for (let c = -1; c <= 7; c++) {
                    const row = top + r;
                    const col = left + c;
                    if (row < 0 || row >= size || col < 0 || col >= size) continue;
                    const onBorder = (r >= 0 && r <= 6 && (c === 0 || c === 6)) ||
                        (c >= 0 && c <= 6 && (r === 0 || r === 6));
                    const inCore = r >= 2 && r <= 4 && c >= 2 && c <= 4;
                    set(row, col, onBorder || inCore);
                    reserve(row, col);
                }
            }
        }

        // Synchronisation : finders + séparateurs.
        placeFinder(0, 0);
        placeFinder(0, size - 7);
        placeFinder(size - 7, 0);

        // Alignement (v2+) : 5×5, contour sombre, anneau clair, centre sombre.
        // AVANT le timing : un motif d'alignement INTERROMPT le timing à
        // l'endroit où il le chevauche (ISO 18004 §8.7.2) — l'inverse laisserait
        // des trous et un codage faux. On saute ceux qui chevauchent un finder
        // (leur centre est déjà réservé).
        const positions = ALIGNMENT_POSITIONS[version - 1];
        for (let a = 0; a < positions.length; a++) {
            for (let b = 0; b < positions.length; b++) {
                const row = positions[a];
                const col = positions[b];
                if (reserved[row * size + col]) continue; // chevauche un finder
                for (let r = -2; r <= 2; r++) {
                    for (let c = -2; c <= 2; c++) {
                        const dark = Math.max(Math.abs(r), Math.abs(c)) !== 1;
                        set(row + r, col + c, dark);
                        reserve(row + r, col + c);
                    }
                }
            }
        }

        // Timing (lignes/colonnes alternées) : ne réécrit JAMAIS un module
        // déjà posé (alignement).
        for (let i = 8; i < size - 8; i++) {
            if (!reserved[6 * size + i]) {
                set(6, i, i % 2 === 0);
                reserve(6, i);
            }
            if (!reserved[i * size + 6]) {
                set(i, 6, i % 2 === 0);
                reserve(i, 6);
            }
        }

        // Informations de format : zones réservées (valeurs posées par masque).
        for (let i = 0; i < 9; i++) reserve(8, i);
        for (let i = 0; i < 8; i++) reserve(i, 8);
        for (let i = 0; i < 8; i++) {
            reserve(8, size - 1 - i);
            reserve(size - 1 - i, 8);
        }
        // Module sombre fixe.
        set(size - 8, 8, true);
        reserve(size - 8, 8);

        // Informations de version (v7+), deux blocs 3×6.
        if (version >= 7) {
            const bits = versionBits(version);
            for (let i = 0; i < 18; i++) {
                const dark = ((bits >> i) & 1) === 1;
                const row = Math.floor(i / 3);
                const col = i % 3 + size - 11;
                set(row, col, dark);
                reserve(row, col);
                const row2 = i % 3 + size - 11;
                const col2 = Math.floor(i / 3);
                set(row2, col2, dark);
                reserve(row2, col2);
            }
        }
        return { size: size, modules: modules, reserved: reserved };
    }

    /**
     * Pose les informations de format (15 bits) autour des finders.
     * Convention : bit i = (bits >> i) & 1, placement ISO §8.9.
     */
    function placeFormat(modules, size, eccBits, mask) {
        const bits = formatBits(eccBits, mask);
        for (let i = 0; i < 15; i++) {
            const dark = ((bits >> i) & 1) === 1 ? 1 : 0;
            // Vertical (colonne 8).
            if (i < 6) modules[i * size + 8] = dark;
            else if (i < 8) modules[(i + 1) * size + 8] = dark;
            else modules[(size - 15 + i) * size + 8] = dark;
            // Horizontal (ligne 8).
            if (i < 8) modules[8 * size + (size - 1 - i)] = dark;
            else if (i < 9) modules[8 * size + 7] = dark;
            else modules[8 * size + (14 - i)] = dark;
        }
    }

    /** Parcours en zigzag (2 colonnes, sens alterné, colonne 6 sautée). */
    function placeCodewords(base, codewords) {
        const size = base.size;
        const modules = base.modules;
        const reserved = base.reserved;
        let bitIndex = 0;
        const totalBits = codewords.length * 8;
        let upward = true;
        for (let col = size - 1; col > 0; col -= 2) {
            if (col === 6) col--; // la colonne de timing n'accueille pas de donnée
            for (let i = 0; i < size; i++) {
                const row = upward ? size - 1 - i : i;
                for (let c = col; c > col - 2; c--) {
                    const idx = row * size + c;
                    if (reserved[idx]) continue;
                    let bit = 0;
                    if (bitIndex < totalBits) {
                        bit = (codewords[bitIndex >> 3] >> (7 - (bitIndex & 7))) & 1;
                    }
                    modules[idx] = bit;
                    bitIndex++;
                }
            }
            upward = !upward;
        }
        return base;
    }

    /** Pénalités ISO §8.8.2 (4 règles) d'une matrice complète (format posé). */
    function penalty(modules, size) {
        let lost = 0;
        const N = size * size;

        // Règle 1 : suites de ≥ 5 modules de même couleur (lignes puis colonnes).
        for (let row = 0; row < size; row++) {
            const baseIdx = row * size;
            let run = modules[baseIdx];
            let runLen = 1;
            for (let col = 1; col < size; col++) {
                const v = modules[baseIdx + col];
                if (v === run) runLen++;
                else {
                    if (runLen >= 5) lost += 3 + (runLen - 5);
                    run = v;
                    runLen = 1;
                }
            }
            if (runLen >= 5) lost += 3 + (runLen - 5);
        }
        for (let col = 0; col < size; col++) {
            let run = modules[col];
            let runLen = 1;
            for (let row = 1; row < size; row++) {
                const v = modules[row * size + col];
                if (v === run) runLen++;
                else {
                    if (runLen >= 5) lost += 3 + (runLen - 5);
                    run = v;
                    runLen = 1;
                }
            }
            if (runLen >= 5) lost += 3 + (runLen - 5);
        }

        // Règle 2 : blocs 2×2 monochromes.
        for (let row = 0; row < size - 1; row++) {
            const baseIdx = row * size;
            for (let col = 0; col < size - 1; col++) {
                const v = modules[baseIdx + col];
                if (v === modules[baseIdx + col + 1] &&
                    v === modules[baseIdx + size + col] &&
                    v === modules[baseIdx + size + col + 1]) {
                    lost += 3;
                }
            }
        }

        // Règle 3 : motif de type finder (1:1:3:1:1) bordé de 4 clairs.
        function hasFinderPattern(seq, at) {
            // 1011101 suivi de 0000, ou précédé de 0000.
            if (seq[at] && !seq[at + 1] && seq[at + 2] && seq[at + 3] && seq[at + 4] &&
                !seq[at + 5] && seq[at + 6]) {
                if (!seq[at + 7] && !seq[at + 8] && !seq[at + 9] && !seq[at + 10]) return true;
                if (at >= 4 && !seq[at - 1] && !seq[at - 2] && !seq[at - 3] && !seq[at - 4]) return true;
            }
            return false;
        }
        for (let row = 0; row < size; row++) {
            const seq = modules.subarray(row * size, row * size + size);
            for (let col = 0; col + 10 < size; col++) {
                if (hasFinderPattern(seq, col)) lost += 40;
            }
        }
        const column = new Uint8Array(size);
        for (let col = 0; col < size; col++) {
            for (let row = 0; row < size; row++) column[row] = modules[row * size + col];
            for (let row = 0; row + 10 < size; row++) {
                if (hasFinderPattern(column, row)) lost += 40;
            }
        }

        // Règle 4 : proportion de modules sombres (écart à 50 % par pas de 5 %).
        let dark = 0;
        for (let i = 0; i < N; i++) dark += modules[i];
        const ratio = (dark * 100) / N;
        lost += 10 * Math.floor(Math.abs(ratio - 50) / 5);
        return lost;
    }

    /** Matrice complète pour une version/niveau/masque donnés. */
    function buildMatrix(version, ecc, codewords, mask) {
        const base = placeCodewords(buildBaseMatrix(version), codewords);
        const size = base.size;
        const eccBits = ECC_FORMAT_BITS[ecc];

        const candidates = mask === null ? [0, 1, 2, 3, 4, 5, 6, 7] : [mask];
        let best = null;
        let bestMask = candidates[0];
        let bestScore = Infinity;
        for (let i = 0; i < candidates.length; i++) {
            const m = candidates[i];
            const modules = Uint8Array.from(base.modules);
            for (let row = 0; row < size; row++) {
                for (let col = 0; col < size; col++) {
                    const idx = row * size + col;
                    if (base.reserved[idx]) continue;
                    if (maskAt(m, row, col)) modules[idx] = modules[idx] ? 0 : 1;
                }
            }
            placeFormat(modules, size, eccBits, m);
            const score = penalty(modules, size);
            if (score < bestScore) {
                bestScore = score;
                best = modules;
                bestMask = m;
            }
        }
        return { size: size, modules: best, mask: bestMask, penalty: bestScore };
    }

    // ─── API d'encodage ──────────────────────────────────────────────────────
    /**
     * Encode `text` et renvoie le modèle matriciel.
     * @param {string} text chaîne à encoder (UTF-8, mode byte).
     * @param {object} [options] { ecc:"M", version:0 (auto), mask:null (auto) }
     * @returns {{text:string, bytes:Uint8Array, version:number, ecc:string,
     *            mask:number, size:number, modules:Uint8Array,
     *            darkAt(row:number,col:number):boolean}}
     */
    function encode(text, options) {
        const o = options || {};
        if (typeof text !== "string") {
            fail(TypeError, "texte attendu (chaîne), reçu : " + (text === null ? "null" : typeof text) + ".");
        }
        if (text.length === 0) fail(TypeError, "texte vide : rien à encoder.");
        if (o.mode !== undefined && o.mode !== null && String(o.mode).toLowerCase() !== "byte") {
            fail(TypeError, "mode \"" + o.mode + "\" non supporté (seul le mode \"byte\" l'est).");
        }
        const ecc = normalizeEcc(o.ecc);
        const mask = normalizeMask(o.mask);
        const bytes = utf8Bytes(text);
        let version = normalizeVersion(o.version);
        if (version === 0) {
            version = minimalVersion(bytes.length, ecc);
            if (version === 0) {
                fail(RangeError, "texte trop long : " + bytes.length + " octet(s) UTF-8, maximum " +
                    capacity({ version: MAX_VERSION, ecc: ecc }) + " en version " + MAX_VERSION +
                    " niveau " + ecc + ".");
            }
        }
        const built = buildCodewords(bytes, version, ecc);
        const matrix = buildMatrix(version, ecc, built.codewords, mask);
        return {
            text: text,
            bytes: bytes,
            version: version,
            ecc: ecc,
            mask: matrix.mask,
            size: matrix.size,
            modules: matrix.modules,
            darkAt: function (row, col) {
                if (row < 0 || row >= matrix.size || col < 0 || col >= matrix.size) return false;
                return matrix.modules[row * matrix.size + col] === 1;
            },
        };
    }

    /** Alias lisible de encode() : getMatrix({ text, ecc, version, mask }). */
    function getMatrix(options) {
        const o = options || {};
        return encode(o.text, o);
    }

    // ─── Rendu SVG ───────────────────────────────────────────────────────────
    function normalizeMargin(value) {
        if (value === undefined || value === null) return DEFAULT_MARGIN;
        const m = Number(value);
        if (!isFinite(m) || m < 0) return 0;
        return Math.floor(m);
    }

    function normalizeSize(value) {
        if (value === undefined || value === null) return DEFAULT_SIZE;
        const s = Number(value);
        if (!isFinite(s) || s <= 0) return DEFAULT_SIZE;
        return s;
    }

    function normalizeScale(value) {
        if (value === undefined || value === null) return DEFAULT_SCALE;
        const s = Number(value);
        if (!isFinite(s) || s < 1) return DEFAULT_SCALE;
        return Math.floor(s);
    }

    function normalizeColor(value, fallback) {
        return (typeof value === "string" && value !== "") ? value : fallback;
    }

    /** Chemin SVG (un sous-chemin carré par module sombre). */
    function modulesPath(modules, size, margin) {
        const parts = [];
        for (let row = 0; row < size; row++) {
            for (let col = 0; col < size; col++) {
                if (modules[row * size + col]) {
                    parts.push("M" + (col + margin) + " " + (row + margin) + "h1v1h-1z");
                }
            }
        }
        return parts.join("");
    }

    /**
     * Rend le QR en SVG autonome.
     * @param {object} options { text (requis), ecc, version, mask, margin=4,
     *   size=200, dark, light, background=true, className, alt/title, inline,
     *   injectStyles, nonce }
     * @returns {SVGElement}
     */
    function render(options) {
        const o = options || {};
        if (typeof document === "undefined") {
            fail(Error, "render() nécessite un DOM (document absent dans cet environnement).");
        }
        const model = encode(o.text, o);
        const margin = normalizeMargin(o.margin);
        const total = model.size + margin * 2;
        const px = normalizeSize(o.size);
        const dark = normalizeColor(o.dark, DEFAULT_DARK);
        const light = normalizeColor(o.light, DEFAULT_LIGHT);
        const ns = "http://www.w3.org/2000/svg";

        const svg = document.createElementNS(ns, "svg");
        svg.setAttribute("xmlns", ns);
        svg.setAttribute("viewBox", "0 0 " + total + " " + total);
        svg.setAttribute("width", String(px));
        svg.setAttribute("height", String(px));
        svg.setAttribute("role", "img");
        svg.setAttribute("shape-rendering", "crispEdges");
        svg.setAttribute("data-holaf-qr-version", String(model.version));
        svg.setAttribute("data-holaf-qr-ecc", model.ecc);
        svg.setAttribute("data-holaf-qr-mask", String(model.mask));
        svg.setAttribute("data-holaf-qr-modules", String(model.size));
        let className = "holaf-qrcode";
        if (o.className) className += " " + String(o.className);
        if (o.inline) className += " holaf-qrcode--inline";
        svg.setAttribute("class", className);
        const label = o.alt || o.title || null;
        if (label) svg.setAttribute("aria-label", String(label));

        if (o.background !== false) {
            const bg = document.createElementNS(ns, "rect");
            bg.setAttribute("width", String(total));
            bg.setAttribute("height", String(total));
            bg.setAttribute("fill", light);
            svg.appendChild(bg);
        }
        const path = document.createElementNS(ns, "path");
        path.setAttribute("fill", dark);
        path.setAttribute("d", modulesPath(model.modules, model.size, margin));
        svg.appendChild(path);
        if (o.title) {
            const title = document.createElementNS(ns, "title");
            title.textContent = String(o.title);
            svg.appendChild(title);
        }
        if (resolveInjectStyles(o)) ensureStyle(o.nonce);
        return svg;
    }

    // ─── Rendu canvas / dataURL ──────────────────────────────────────────────
    /** "#rgb" | "#rrggbb" | "#rrggbbaa" → [r, g, b, a] (a 0..255). */
    function parseColor(value, what) {
        const hex = String(value).trim();
        const m = hex.match(/^#([0-9a-f]{3,8})$/i);
        if (!m) {
            fail(TypeError, what + " : couleur canvas non supportée \"" + value +
                "\" (hex #rgb, #rrggbb ou #rrggbbaa attendu).");
        }
        let h = m[1];
        if (h.length === 3 || h.length === 4) {
            h = h.split("").map(function (c) { return c + c; }).join("");
        }
        if (h.length === 6) h += "ff";
        if (h.length !== 8) fail(TypeError, what + " : couleur invalide \"" + value + "\".");
        return [
            parseInt(h.slice(0, 2), 16),
            parseInt(h.slice(2, 4), 16),
            parseInt(h.slice(4, 6), 16),
            parseInt(h.slice(6, 8), 16),
        ];
    }

    /**
     * Rend le QR en <canvas> (raster, 1 module = `scale` pixels, défaut 8).
     * @param {object} options { text (requis), ecc, version, mask, margin=4,
     *   scale=8, dark, light }
     * @returns {HTMLCanvasElement}
     */
    function renderCanvas(options) {
        const o = options || {};
        if (typeof document === "undefined" || typeof document.createElement !== "function") {
            fail(Error, "renderCanvas() nécessite un DOM (document absent dans cet environnement).");
        }
        const model = encode(o.text, o);
        const margin = normalizeMargin(o.margin);
        const scale = normalizeScale(o.scale);
        const total = model.size + margin * 2;
        const pixels = total * scale;
        const darkRgba = parseColor(normalizeColor(o.dark, DEFAULT_DARK), "dark");
        const lightRgba = parseColor(normalizeColor(o.light, DEFAULT_LIGHT), "light");

        const canvas = document.createElement("canvas");
        canvas.width = pixels;
        canvas.height = pixels;
        const ctx = (typeof canvas.getContext === "function") ? canvas.getContext("2d") : null;
        if (!ctx || typeof ctx.putImageData !== "function") {
            fail(Error, "canvas 2D indisponible dans cet environnement (renderCanvas/toDataURL).");
        }
        const image = (typeof ctx.createImageData === "function")
            ? ctx.createImageData(pixels, pixels)
            : { data: new Uint8ClampedArray(pixels * pixels * 4) };
        const data = image.data;
        for (let py = 0; py < pixels; py++) {
            const mrow = Math.floor(py / scale) - margin;
            const inRow = mrow >= 0 && mrow < model.size;
            for (let px = 0; px < pixels; px++) {
                const mcol = Math.floor(px / scale) - margin;
                const isDark = inRow && mcol >= 0 && mcol < model.size &&
                    model.modules[mrow * model.size + mcol] === 1;
                const rgba = isDark ? darkRgba : lightRgba;
                const idx = (py * pixels + px) * 4;
                data[idx] = rgba[0];
                data[idx + 1] = rgba[1];
                data[idx + 2] = rgba[2];
                data[idx + 3] = rgba[3];
            }
        }
        ctx.putImageData(image, 0, 0);
        canvas.setAttribute("data-holaf-qr-version", String(model.version));
        canvas.setAttribute("data-holaf-qr-ecc", model.ecc);
        canvas.setAttribute("data-holaf-qr-mask", String(model.mask));
        canvas.setAttribute("data-holaf-qr-modules", String(model.size));
        canvas.setAttribute("class", "holaf-qrcode holaf-qrcode--canvas");
        return canvas;
    }

    /**
     * dataURL PNG du QR (via renderCanvas). Nécessite un canvas 2D.
     * @param {object} options options de renderCanvas + { type, quality }
     */
    function toDataURL(options) {
        const o = options || {};
        const canvas = renderCanvas(o);
        if (typeof canvas.toDataURL !== "function") {
            fail(Error, "toDataURL() indisponible sur ce canvas.");
        }
        const type = o.type ? String(o.type) : "image/png";
        if (o.quality === undefined || o.quality === null) return canvas.toDataURL(type);
        return canvas.toDataURL(type, Number(o.quality));
    }

    return {
        version: VERSION,
        encode: encode,
        getMatrix: getMatrix,
        capacity: capacity,
        minimalVersion: minimalVersion,
        render: render,
        renderCanvas: renderCanvas,
        toDataURL: toDataURL,
        getCss: getCss,
        configure: configure,
        setStyleNonce: setStyleNonce,
    };
})();

// Exposition globale (scripts classiques de la page).
if (typeof window !== "undefined") {
    window.HolafQrcode = HolafQrcode;
}

// Export ESM (import { HolafQrcode } from "./holaf-qrcode.js").
export { HolafQrcode, VERSION };
