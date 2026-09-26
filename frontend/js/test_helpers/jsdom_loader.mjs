// ─────────────────────────────────────────────────────────────────────────
// Chargeur jsdom PARTAGÉ par les tests headless du front AI-Helper.
//
// jsdom est un BANC DE TEST, jamais une dépendance runtime du front : aucun
// package.json n'est ajouté au projet. On résout jsdom via node_modules
// externes (holaf-lib), dans cet ordre :
//   1. $JSDOM_DIR                         (surcharge explicite)
//   2. ./node_modules                     (racine frontend)
//   3. ../../holaf-lib/node_modules       (dépôt voisin)
//   4. /projects/holaf-lib/node_modules   (secours absolu)
//
// Si aucune source ne fournit jsdom : SKIP BRUYANT, code 2 — JAMAIS un PASS.
// ─────────────────────────────────────────────────────────────────────────
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const require = createRequire(import.meta.url);
// Ce fichier vit dans frontend/js/test_helpers/ : la racine du front est à deux
// niveaux ; /projects est à quatre niveaux (pour le secours holaf-lib).
const HERE = dirname(fileURLToPath(import.meta.url));

/** Chemins de recherche de jsdom, dans l'ordre (documentés ci-dessus). */
export function jsdomSearchPaths() {
    const paths = [];
    if (process.env.JSDOM_DIR) paths.push(process.env.JSDOM_DIR);
    paths.push(resolve(HERE, "..", "..", "node_modules"));                                   // 2. ./node_modules
    paths.push(resolve(HERE, "..", "..", "..", "holaf-lib", "node_modules"));                 // 3. ../holaf-lib
    paths.push(resolve(HERE, "..", "..", "..", "..", "holaf-lib", "node_modules"));           // 3bis
    paths.push("/projects/holaf-lib/node_modules");                                           // 4. secours absolu
    return paths.filter((p, i) => p && paths.indexOf(p) === i);
}

/**
 * Résout et importe jsdom.
 * @returns {Promise<typeof import("jsdom").JSDOM|null>} la classe JSDOM, ou null.
 */
export async function loadJsdom() {
    for (const dir of jsdomSearchPaths()) {
        try {
            const resolved = require.resolve("jsdom", { paths: [dir] });
            const mod = await import(resolved);
            const JSDOM = mod.JSDOM || mod.default?.JSDOM;
            if (JSDOM) return JSDOM;
        } catch { /* candidat suivant */ }
    }
    return null;
}

/**
 * Charge jsdom ou sort en SKIP bruyant (exit 2).
 * @param {string} testName nom du test (affiché dans le message).
 * @returns {Promise<typeof import("jsdom").JSDOM>}
 */
export async function loadJsdomOrSkip(testName) {
    const JSDOM = await loadJsdom();
    if (JSDOM) return JSDOM;
    console.warn(
        `⚠️  ${testName} : jsdom introuvable → test ignoré (SKIP, exit 2).\n` +
        `    Cherché dans : ${jsdomSearchPaths().join(" → ")}\n` +
        `    Fournir JSDOM_DIR=/chemin/vers/node_modules (contenant jsdom) pour l'exécuter.`
    );
    process.exit(2);
}
