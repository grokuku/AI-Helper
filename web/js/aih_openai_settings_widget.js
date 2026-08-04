/**
 * AIH OpenAI Settings — Enhancements for the native AIHOpenAISettingsNode.
 *
 * This file does NOT replace the native ComfyUI widgets; it augments them
 * in-place:
 *
 *   1. "model" widget → editable combo (input + <datalist>)
 *      On focus/mousedown, fetches GET {base_url}/models and populates the
 *      datalist with model IDs.  The input remains fully editable so the user
 *      can type a model name manually.
 *
 *   2. "api_key" widget → visually masked WITHOUT type="password"
 *      Uses -webkit-text-security: disc so the browser password manager
 *      does not pop up, while still hiding the value behind dots.
 *
 * Pattern: self-contained polling for window.app (same as other AIH widgets)
 * to avoid load-order dependencies.
 */

(function () {
    "use strict";

    // ── self-contained boot (mirrors aih_keywords_widget.js etc.) ──
    function aihBoot() {
        var app = window.app || (window.comfyAPI && window.comfyAPI.app && window.comfyAPI.app.app);
        if (!app || !app.graph) {
            setTimeout(aihBoot, 100);
            return;
        }

        app.registerExtension({
            name: "AIH.OpenAISettings",

            async beforeRegisterNodeDef(nodeType, nodeData) {
                if (nodeData.name !== "AIHOpenAISettingsNode") return;

                const onNodeCreated = nodeType.prototype.onNodeCreated;
                nodeType.prototype.onNodeCreated = function () {
                    const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
                    const node = this;
                    try {
                        setupOpenAISettingsNode(node);
                        setupNumCtxField(node);
                    } catch (err) {
                        console.error("[AIH.OpenAISettings] setup error:", err);
                    }
                    return r;
                };

                // Re-sync le champ DOM num_ctx après chargement d'un workflow :
                // configure() restaure les widgets natifs APRÈS onNodeCreated, donc
                // on rafraîchit le champ DOM ici pour refléter la valeur chargée.
                const onConfigure = nodeType.prototype.onConfigure;
                nodeType.prototype.onConfigure = function () {
                    const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
                    try {
                        const node = this;
                        const numCtxWidget = node.widgets && node.widgets.find(function (w) { return w.name === "num_ctx"; });
                        const domInput = node._aihNumCtxInput;
                        if (numCtxWidget && domInput) {
                            var v = parseInt(numCtxWidget.value, 10);
                            if (isNaN(v) || v < 0) v = 0;
                            domInput.value = v;
                        }
                    } catch (err) {
                        console.error("[AIH.OpenAISettings] num_ctx sync error:", err);
                    }
                    return r;
                };
            },
        });
    }

    // ────────────────────────────────────────────────────────────────
    // Main setup, called once per created node instance.
    // ────────────────────────────────────────────────────────────────
    function setupOpenAISettingsNode(node) {
        // The native STRING widgets may not have their inputEl created yet at
        // onNodeCreated time (ComfyUI lazily draws them).  Poll briefly until
        // both input elements are available, then apply the enhancements.
        var attempts = 0;
        var MAX_ATTEMPTS = 50; // 50 × 50 ms = 2.5 s max

        function trySetup() {
            var modelWidget = node.widgets && node.widgets.find(function (w) { return w.name === "model"; });
            var apiKeyWidget = node.widgets && node.widgets.find(function (w) { return w.name === "api_key"; });
            var baseUrlWidget = node.widgets && node.widgets.find(function (w) { return w.name === "base_url"; });

            var modelReady = modelWidget && modelWidget.inputEl;
            var apiKeyReady = apiKeyWidget && apiKeyWidget.inputEl;

            // api_key masking can be applied even if model isn't ready yet, but
            // for simplicity we wait until both are present (or timeout).
            if ((!modelReady || !apiKeyReady) && attempts < MAX_ATTEMPTS) {
                attempts++;
                setTimeout(trySetup, 50);
                return;
            }
            if (!modelReady || !apiKeyReady) {
            }

            if (modelReady) {
                enhanceModelWidget(node, modelWidget);
            } else if (modelWidget) {
                // Fallback: the widget exists but has no inputEl (shouldn't
                // normally happen).  Try again once more after a longer delay.
                setTimeout(function () {
                    if (modelWidget.inputEl) enhanceModelWidget(node, modelWidget);
                }, 500);
            }

            if (apiKeyReady) {
                maskApiKey(apiKeyWidget);
            }

            // Auto-save / auto-load API key from local file (per base_url).
            // Requires both apiKey and baseUrl widgets to be present.
            if (apiKeyWidget && baseUrlWidget) {
                setupApiKeyPersistence(node, apiKeyWidget, baseUrlWidget);
            }
        }

        trySetup();
    }

    // ────────────────────────────────────────────────────────────────
    // 1. Model widget → editable combo (input + datalist)
    // ────────────────────────────────────────────────────────────────
    function enhanceModelWidget(node, modelWidget) {
        var input = modelWidget.inputEl;
        if (!input) return;

        // Guard against double-setup (onNodeCreated may fire on reload)
        if (input.dataset.aihDatalist) return;
        input.dataset.aihDatalist = "1";

        // Unique datalist id per node to avoid collisions when several
        // AIHOpenAISettingsNode instances coexist on the canvas.
        var datalistId = "aih-models-" + (node.id || Math.random().toString(36).slice(2));

        var datalist = document.createElement("datalist");
        datalist.id = datalistId;
        // Datalists are invisible by nature but must be in the DOM to work.
        document.body.appendChild(datalist);

        input.setAttribute("list", datalistId);
        // Make the input clearly editable / autocomplete-friendly.
        input.setAttribute("autocomplete", "off");

        // Fetch guard: avoid overlapping fetches and stale responses.
        var fetchInProgress = false;

        function getBaseUrl() {
            var w = node.widgets && node.widgets.find(function (x) { return x.name === "base_url"; });
            if (!w) return "";
            var v = (typeof w.value === "string" ? w.value : "").trim();
            return v;
        }

        function populateDatalist(models) {
            // Clear existing options
            datalist.innerHTML = "";
            var seen = {};
            models.forEach(function (id) {
                var key = String(id);
                if (seen[key]) return;
                seen[key] = true;
                var opt = document.createElement("option");
                opt.value = key;
                datalist.appendChild(opt);
            });
        }

        function fetchModels() {
            if (fetchInProgress) return;
            var baseUrl = getBaseUrl();
            if (!baseUrl) {
                // Nothing to fetch — leave datalist empty (user can still type)
                return;
            }

            // Build the /models URL.  We append "/models" to base_url as-is
            // (base_url is typically "http://localhost:11434/v1" or
            // "https://api.openai.com/v1").
            var url = baseUrl.replace(/\/+$/, "") + "/models";

            fetchInProgress = true;
            fetch(url, {
                method: "GET",
                headers: { "Content-Type": "application/json" },
                // signal: none — we let it complete or fail naturally
            })
                .then(function (resp) {
                    if (!resp.ok) {
                        // e.g. 401 for OpenAI without a key — silent, non-fatal
                        return null;
                    }
                    return resp.json();
                })
                .then(function (data) {
                    if (!data) return;
                    // OpenAI format: { "data": [ { "id": "gpt-4o" }, ... ] }
                    var items = data.data || data.models || (Array.isArray(data) ? data : []);
                    var ids = items
                        .map(function (m) {
                            if (!m) return null;
                            if (typeof m === "string") return m;
                            return m.id || m.name || m.model || null;
                        })
                        .filter(function (id) { return id; });
                    populateDatalist(ids);
                })
                .catch(function (err) {
                    // Network/CORS/parse error — silent, non-fatal.
                    // The datalist simply stays empty; the user can still type.
                    console.warn("[AIH.OpenAISettings] fetch models failed (" + url + "):", err.message || err);
                })
                .then(function () {
                    // .finally() equivalent
                    fetchInProgress = false;
                });
        }

        // Re-fetch on every focus / mousedown (no permanent cache, as
        // requested).  mousedown fires before focus and before the native
        // dropdown opens, giving the datalist time to populate.
        input.addEventListener("mousedown", function () {
            fetchModels();
        });
        input.addEventListener("focus", function () {
            fetchModels();
        });

        // If the base_url widget changes, we don't pre-fetch (lazy: the next
        // focus on model will trigger a fresh fetch with the new base_url).
    }

    // ────────────────────────────────────────────────────────────────
    // 3. API key persistence — auto-save (debounced) + auto-load on
    //    base_url change, via the local /aih/openai/keys route.
    //    The key is kept in-memory in the widget for the session but is
    //    NOT serialised into the workflow (serializeValue → "").
    // ────────────────────────────────────────────────────────────────
    function setupApiKeyPersistence(node, apiKeyWidget, baseUrlWidget) {
        // Guard against double-setup (reload / duplicate node).
        if (apiKeyWidget._aihKeyPersistence) return;
        apiKeyWidget._aihKeyPersistence = true;

        function loadApiKeyForBaseUrl(baseUrl) {
            if (!baseUrl || !baseUrl.trim()) return;
            fetch("/aih/openai/keys?base_url=" + encodeURIComponent(baseUrl.trim().replace(/\/+$/, "")))
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.status === "ok" && data.key) {
                        apiKeyWidget.value = data.key;
                    }
                })
                .catch(function (e) { /* silencieux */ });
        }

        function saveApiKey(baseUrl, apiKey) {
            if (!baseUrl || !baseUrl.trim()) return;
            fetch("/aih/openai/keys", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    base_url: baseUrl.trim().replace(/\/+$/, ""),
                    api_key: apiKey || ""
                })
            }).catch(function (e) { console.warn("[AIH OpenAI] Failed to save API key:", e); });
        }

        // --- Auto-load quand base_url change ---
        var origBaseUrlOnChange = baseUrlWidget.callback;
        baseUrlWidget.callback = function () {
            loadApiKeyForBaseUrl(baseUrlWidget.value);
            if (origBaseUrlOnChange) origBaseUrlOnChange.apply(this, arguments);
        };

        // Au chargement de la node, charger la clé pour le base_url actuel.
        loadApiKeyForBaseUrl(baseUrlWidget.value);

        // --- Auto-save de la clé (debounced) ---
        var saveTimeout = null;
        var origApiKeyOnChange = apiKeyWidget.callback;
        apiKeyWidget.callback = function () {
            if (saveTimeout) clearTimeout(saveTimeout);
            saveTimeout = setTimeout(function () {
                saveApiKey(baseUrlWidget.value, apiKeyWidget.value);
            }, 500);
            if (origApiKeyOnChange) origApiKeyOnChange.apply(this, arguments);
        };

        // --- Ne pas sérialiser api_key dans le workflow ---
        apiKeyWidget.serializeValue = function () { return ""; };
    }

    // ────────────────────────────────────────────────────────────────
    // 2. api_key widget → visual masking without type="password"
    // ────────────────────────────────────────────────────────────────
    function maskApiKey(apiKeyWidget) {
        var input = apiKeyWidget.inputEl;
        if (!input) return;

        // Guard against double-setup
        if (input.dataset.aihMasked) return;
        input.dataset.aihMasked = "1";

        // Ensure it's type="text" (ComfyUI default for STRING widgets), NOT
        // "password", so the browser password manager does not interfere.
        input.type = "text";
        input.setAttribute("autocomplete", "off");
        // Prevent some browsers from treating it as a password field.
        input.setAttribute("data-lpignore", "true");

        // -webkit-text-security: disc renders dots like a password field,
        // but the browser does NOT recognise the input as type="password",
        // so no password-manager popup appears.
        input.style.webkitTextSecurity = "disc";
        input.style.setProperty("-webkit-text-security", "disc", "important");
        // Fallback for non-webkit browsers: colour the text to blend with
        // the background so it's not trivially readable.
        // (This is a secondary visual measure; -webkit-text-security is
        //  widely supported in Chrome/Edge/Safari which are the typical
        //  ComfyUI environments.)
    }

    // ────────────────────────────────────────────────────────────────
    // 4. num_ctx widget → labelled DOM field "Context (num_ctx)"
    //    The native INT widget stays the source of truth (serialized in the
    //    workflow).  We hide it and render a labelled number input that
    //    syncs both ways: on load (native → DOM) and on change (DOM → native).
    // ────────────────────────────────────────────────────────────────
    function setupNumCtxField(node) {
        var numCtxWidget = node.widgets && node.widgets.find(function (w) { return w.name === "num_ctx"; });
        if (!numCtxWidget) return;

        // Guard against double-setup (reload / duplicate node).
        if (node._aihNumCtxSetup) return;
        node._aihNumCtxSetup = true;

        // Hide the native INT widget; the labelled DOM field below is the
        // visible control (same pattern as aih_enhance_widget.js).
        numCtxWidget.hidden = true;
        numCtxWidget.computeSize = function () { return [0, -4]; };
        if (numCtxWidget.element) numCtxWidget.element.style.display = "none";
        if (numCtxWidget.inputEl) numCtxWidget.inputEl.style.display = "none";
        if (numCtxWidget.parentEl) numCtxWidget.parentEl.style.display = "none";

        var container = document.createElement("div");
        Object.assign(container.style, {
            display: "flex",
            gap: "4px",
            alignItems: "center",
            width: "100%",
            padding: "2px 6px",
        });

        var label = document.createElement("label");
        label.textContent = "Context (num_ctx) · 0 = auto";
        Object.assign(label.style, {
            fontSize: "11px",
            color: "#aaa",
            whiteSpace: "nowrap",
        });

        var input = document.createElement("input");
        input.type = "number";
        input.min = "0";
        input.step = "1024";
        input.value = 0; // 0 = auto-détection depuis l'API (cache 1h) ; >0 = override manuel
        Object.assign(input.style, {
            flex: "1",
            minWidth: "0",
            padding: "3px 6px",
            borderRadius: "4px",
            border: "1px solid #555",
            background: "#3a3a3e",
            color: "#ccc",
            fontSize: "11px",
            boxSizing: "border-box",
        });
        input.title = "Fenêtre de contexte (spécifique Ollama). 0 = auto-détection depuis l'API (recommandé) ; >0 = override manuel (ex: 131072/262144 pour Gemma 4).";

        container.appendChild(label);
        container.appendChild(input);

        var domWidget = node.addDOMWidget("AIH_NumCtx", "div", container, {
            serialize: false,
            hideOnZoom: false,
        });
        domWidget.serialize = false;
        domWidget.options.serialize = false;

        // Keep a ref so onConfigure can re-sync after workflow load.
        node._aihNumCtxInput = input;

        // Sync: native widget -> DOM field (initial value / default 0 = auto).
        function syncFromNative() {
            var v = parseInt(numCtxWidget.value, 10);
            if (isNaN(v) || v < 0) v = 0;
            input.value = v;
        }
        syncFromNative();

        // Sync: DOM field -> native widget (user typing / change).
        function writeToNative(v) {
            numCtxWidget.value = v;
            if (typeof numCtxWidget.callback === "function") {
                try { numCtxWidget.callback(v); } catch (e) { console.warn("[AIH.OpenAISettings] num_ctx callback error:", e); }
            }
        }

        input.addEventListener("input", function () {
            var v = parseInt(input.value, 10);
            if (isNaN(v) || v < 0) v = 0;
            numCtxWidget.value = v;
        });

        input.addEventListener("change", function () {
            var v = parseInt(input.value, 10);
            if (isNaN(v) || v < 0) v = 0;
            input.value = v;
            writeToNative(v);
        });
    }

    // Kick things off
    aihBoot();
})();