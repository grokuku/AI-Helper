/**
 * FR.IA Shared Helpers — Fonctions partagées entre les widgets ComfyUI.
 *
 * Chargé automatiquement par ComfyUI (WEB_DIRECTORY = "web").
 * Pas d'ESM : attache les helpers à l'objet global window.FRIA.
 *
 * Les fichiers widget délèguent à ces helpers pour éviter la duplication.
 */
(function () {
    "use strict";

    const FRIA = (window.FRIA = window.FRIA || {});

    /**
     * Récupère la clé API FR.IA depuis localStorage ("FRIA_config").
     * @returns {string} La clé API, ou "" si absente / illisible.
     */
    FRIA.getApiKey = function getApiKey() {
        try {
            return JSON.parse(localStorage.getItem("FRIA_config") || "{}").apiKey || "";
        } catch {
            return "";
        }
    };
})();