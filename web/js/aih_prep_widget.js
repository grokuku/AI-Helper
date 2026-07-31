/**
 * AIH Prompt Prep — Custom DOM widget for ComfyUI node AIHPromptPrepNode.
 *
 * Ce node est une version "découplée" du AIH Prompt Enhancer :
 *   - Il NE fait PAS d'appel LLM
 *   - Il sort 3 strings (llm_prompt, system_prompt, neg_prompt)
 *   - L'utilisateur branche son propre node LLM (LM Studio, Ollama, etc.)
 *
 * DOM widget simplifié :
 *   - Grille 2 colonnes : Type (gauche) + Style (droite)
 *   - 1 bouton "↻" pour rafraichir la liste des styles
 *   - Pas de bouton "Test enhance", pas de textarea, pas de dropdown "Preset IA"
 *
 * Les widgets natifs ComfyUI (seed, base_prompt, special_instructions,
 * elements) sont restaurés automatiquement par ComfyUI au rechargement
 * de la page. Les widgets template_id et style_id sont natifs mais caches
 * (le DOM les pilote via leur .value).
 *
 * api_key et server_url sont lus depuis ComfyUI/user/default/aih_credentials.json
 * (helper Python _credentials). Plus de widget STRING _api_config.
 */
(function() {
    function aihBoot() {
        // Les fichiers d'extension ComfyUI ne sont pas chargés dans un ordre
        // garanti : attendre que 03_aih_shared.js / 04_aih_widget_base.js aient
        // défini window.AIH avant d'enregistrer l'extension.
        if (!window.AIH || !window.AIH.waitForApp) { setTimeout(aihBoot, 50); return; }
    window.AIH.waitForApp(function(app) {
        app.registerExtension({
            name: "AIH.PromptPrep",
            async beforeRegisterNodeDef(nodeType, nodeData) {
                if (nodeData.name !== "AIHPromptPrepNode") return;

                AIH.registerWidget(nodeType, {
                    onCreated: function() {
                        const node = this;
                        let _aihRestored = false;

                // ---- Cacher les widgets natifs pilotes par le DOM ----
                // Ne PAS utiliser w.hidden = true (empêche la sérialisation dans ComfyUI récent).
                // On cache visuellement via display:none sur les éléments DOM.
                const hideWidget = (n, name) => {
                    const w = n.widgets?.find(x => x.name === name);
                    if (w) {
                        // Forcer serializeValue pour garantir la sauvegarde dans le workflow
                        w.serializeValue = function() { return this.value; };
                        w.computeSize = () => [0, 0]; // 0 place visuellement, pas de hauteur négative
                        // Ne pas masquer les éléments DOM (display:none peut empêcher la sérialisation)
                    }
                };
                ["template_id", "style_id", "style_shortlist"].forEach(n => hideWidget(node, n));

                // ---- Supprimer les sockets d'entrée ----
                for (const inputName of ["template_id", "style_id", "style_shortlist"]) {
                    const slot = node.findInputSlot?.(inputName);
                    if (slot !== undefined && slot !== -1) {
                        node.removeInput(slot);
                    }
                }

                // Refs vers les widgets natifs (utiles pour le sync)
                const templateIdWidget = node.widgets?.find(x => x.name === "template_id");
                const styleWidget = node.widgets?.find(x => x.name === "style_id");

                // ---- Fixer la taille du textarea natif base_prompt ----
                const FIXED_TA_HEIGHT = 90;
                const basePromptWidget = node.widgets?.find(w => w.name === "base_prompt");
                if (basePromptWidget) {
                    const fixBasePrompt = () => {
                        if (basePromptWidget.inputEl) {
                            basePromptWidget.inputEl.style.height = FIXED_TA_HEIGHT + "px";
                            basePromptWidget.inputEl.style.minHeight = FIXED_TA_HEIGHT + "px";
                            basePromptWidget.inputEl.style.maxHeight = FIXED_TA_HEIGHT + "px";
                            basePromptWidget.inputEl.style.resize = "none";
                            basePromptWidget.inputEl.style.boxSizing = "border-box";
                        }
                    };
                    fixBasePrompt();
                    // Hauteur du wrapper = textarea (90px) + label (~20px) + padding (~8px) ≈ 118px
                    // Calcul explicite pour eviter les problemes de mesure asynchrone.
                    var textareaWrapperHeight = FIXED_TA_HEIGHT + 20;
                    basePromptWidget.computeSize = function() {
                        return [0, textareaWrapperHeight];
                    };
                }

                // ---- Cache de rafraîchissement ----
                const _cache = (window.__AIH_cache = window.__AIH_cache || {});
                const CACHE_TTL = 15000;

                // URL API pour recuperer la liste des styles
                const getApiUrl = () => {
                    try {
                        const cfg = JSON.parse(localStorage.getItem("AIH_config") || "{}");
                        const base = (cfg.serverUrl || "https://kw.holaf.fr").replace(/\/+$/, "");
                        return base + "/api";
                    } catch {
                        return "https://kw.holaf.fr/api";
                    }
                };
                const getApiKey = () => window.AIH.getApiKey();
                const apiHeaders = () => {
                    const h = { "Content-Type": "application/json" };
                    const key = getApiKey();
                    if (key) h["Authorization"] = `Bearer ${key}`;
                    return h;
                };
                const apiGet = async (path) => {
                    const resp = await fetch(`${getApiUrl()}/${path.replace(/^\//, "")}`, { headers: apiHeaders() });
                    if (!resp.ok) return [];
                    return resp.json().catch(() => []);
                };

                function syncNativeWidgets(force) {
                    if (!_aihRestored && !force) return;
                    if (templateIdWidget) {
                        const val = parseInt(typeSelect.value) || 0;
                        templateIdWidget.value = val;
                        if (templateIdWidget.callback) templateIdWidget.callback(val);
                    }
                    if (styleWidget) {
                        var val;
                        if (styleSelect.value === '_random') {
                            val = -1;  // sentinelle : random persistant
                        } else {
                            val = parseInt(styleSelect.value) || 0;
                        }
                        styleWidget.value = val;
                        if (styleWidget.callback) styleWidget.callback(val);
                    }
                }

                // Restaurer la selection des dropdowns depuis les widgets natifs
                // (restaures par ComfyUI au rechargement de la page)
                function restoreFromNativeWidgets() {
                    let restored = false;
                    if (templateIdWidget) {
                        const tid = parseInt(templateIdWidget.value) || 0;
                        if (tid > 0 && [...typeSelect.options].some(o => o.value === String(tid))) {
                            typeSelect.value = String(tid);
                            restored = true;
                        } else if (tid === 0) {
                            typeSelect.value = "0";
                        }
                    }
                    if (styleWidget) {
                        const sid = parseInt(styleWidget.value);
                        if (sid === -1 && [...styleSelect.options].some(o => o.value === '_random')) {
                            styleSelect.value = '_random';
                            restored = true;
                        } else if (sid > 0 && [...styleSelect.options].some(o => o.value === String(sid))) {
                            styleSelect.value = String(sid);
                            restored = true;
                        } else if (sid === 0 || isNaN(sid)) {
                            styleSelect.value = "0";
                        }
                    }
                    _aihRestored = true;
                    return restored;
                }

                // populateStyleSelect et refreshStylesIfStale remplacés par AIH.PickerConfig ci-dessous

                // ---- Container (flex column) ----
                const container = document.createElement("div");
                Object.assign(container.style, {
                    width: "100%", padding: "8px", boxSizing: "border-box",
                    background: "#2a2a2e", borderRadius: "8px",
                    display: "flex", flexDirection: "column", gap: "6px",
                    fontSize: "12px", color: "#ccc", overflow: "hidden",
                });

                const mkLabel = (text) => {
                    const l = document.createElement("label");
                    l.textContent = text;
                    l.style.cssText = "font-size:10px;color:#888;display:block;margin-bottom:2px;";
                    return l;
                };

                // ---- Grille 2 colonnes (Type + Style) ----
                const grid = document.createElement("div");
                Object.assign(grid.style, {
                    display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px",
                });

                const selectStyle = {
                    width: "100%", padding: "3px 6px", borderRadius: "4px",
                    border: "1px solid #555", background: "#3a3a3e",
                    color: "#ccc", fontSize: "11px", cursor: "pointer",
                };

                // Type (gauche) — peuplé depuis /api/prompts/templates (meme pattern que Style)
                const typeDiv = document.createElement("div");
                const typeSelect = document.createElement("select");
                Object.assign(typeSelect.style, selectStyle);
                typeSelect.onchange = syncNativeWidgets;
                typeSelect.addEventListener("mousedown", refreshTemplatesIfStale);
                typeDiv.appendChild(mkLabel("Template"));
                typeDiv.appendChild(typeSelect);
                grid.appendChild(typeDiv);

                async function populateTemplateSelect() {
                    typeSelect.innerHTML = `<option value="0">-- Chargement --</option>`;
                    try {
                        const items = await apiGet("prompts/templates");
                        typeSelect.innerHTML = `<option value="0">-- Template --</option>`;
                        if (!Array.isArray(items)) return;
                        // Trier par ordre alphabétique
                        items.sort(function(a, b) {
                            var nameA = (a.name || a.title || a.text || a).toString().toLowerCase();
                            var nameB = (b.name || b.title || b.text || b).toString().toLowerCase();
                            return nameA.localeCompare(nameB);
                        });
                        items.forEach(item => {
                            const o = document.createElement("option");
                            o.value = item.id;
                            o.textContent = item.name || (`Template ${item.id}`);
                            typeSelect.appendChild(o);
                        });
                    } catch {
                        typeSelect.innerHTML = `<option value="0">-- Template --</option>`;
                    }
                }
                async function refreshTemplatesIfStale() {
                    const now = Date.now();
                    if (now - (_cache.tmpl || 0) < CACHE_TTL) return;
                    _cache.tmpl = now;
                    const oldVal = typeSelect.value;
                    await populateTemplateSelect();
                    if (oldVal !== "0" && [...typeSelect.options].some(o => o.value === oldVal)) typeSelect.value = oldVal;
                }

                // Style (droite) — picker configurable avec modale
                const styleDiv = document.createElement("div");
                const styleRow = document.createElement("div");
                Object.assign(styleRow.style, { display: "flex", gap: "4px", alignItems: "center", width: "100%" });
                const styleSelect = document.createElement("select");
                Object.assign(styleSelect.style, selectStyle);
                styleSelect.onchange = syncNativeWidgets;
                styleDiv.appendChild(mkLabel("Style"));
                styleRow.appendChild(styleSelect);
                styleDiv.appendChild(styleRow);
                grid.appendChild(styleDiv);
                // Style picker avec config modal
                var stylePicker = AIH.PickerConfig.setup({
                    select: styleSelect,
                    node: node,
                    widgetName: 'style_id',
                    listWidgetName: 'style_shortlist',
                    apiPath: 'styles',
                    label: 'Style',
                    placeholder: '-- Style --',
                    idField: 'id',
                    nameField: 'name',
                    authorField: 'owner_name',
                    descField: 'style_text',
                    fetchItems: apiGet,
                });

                container.appendChild(grid);

                // Mini-explication
                const help = document.createElement("div");
                help.style.cssText = "font-size:10px;color:#777;margin-top:4px;line-height:1.4;";
                help.innerHTML = "Sort 3 strings : <b>llm_prompt</b>, <b>system_prompt</b>, <b>neg_prompt</b>.<br>Branchez votre node LLM sur les 2 premiers.";
                container.appendChild(help);

                // ---- Ajout au node ----
                const widget = node.addDOMWidget("AIH_PromptPrep", "div", container, {
                    serialize: false,
                    hideOnZoom: false,
                });
                // ---- Calcul dynamique de la hauteur du DOM widget ----
                // Le DOM widget remplit l'espace restant après les widgets natifs.
                // Les widgets natifs (base_prompt, seed, etc.) ont une taille fixe,
                // et le DOM widget s'agrandit quand on resize la node verticalement.
                const DOM_WIDGET_HEIGHT = 128; // -112px: JS height + minimum node height reduction
                const CHROME = 70; // titre node + padding
                // Somme des hauteurs des widgets natifs visibles (utilise computeSize de chaque widget)
                function fixedWidgetsHeight() {
                    let h = 0;
                    for (const w of node.widgets) {
                        if (w === widget) continue;
                        if (w.hidden) continue;
                        let wh = 26;
                        if (w.computeSize) {
                            try {
                                const s = w.computeSize();
                                if (Array.isArray(s) && s[1]) wh = s[1];
                            } catch {}
                        }
                        h += wh;
                    }
                    return h;
                }
                // computeSize reste CONSTANT (PAS de feedback loop avec node.size[1])
                widget.computeSize = () => [node.size[0] - 20, DOM_WIDGET_HEIGHT];

                // ---- Initialisation ----
                Promise.all([populateTemplateSelect(), stylePicker.init()]).then(() => {
                    const restored = restoreFromNativeWidgets();
                    if (restored) syncNativeWidgets(true);
                    else {
                        // Premier chargement : forcer les selects sur les widgets natifs
                        typeSelect.value = String(parseInt(templateIdWidget?.value) || 0);
                        // Gérer la sentinelle -1 (random persistant)
                        var sval = parseInt(styleWidget?.value);
                        if (sval === -1 && [...styleSelect.options].some(o => o.value === '_random')) {
                            styleSelect.value = '_random';
                        } else {
                            styleSelect.value = String(sval || 0);
                        }
                        syncNativeWidgets(true);
                    }
                    let ra = 0;
                    function delayedRestore() {
                        const r = restoreFromNativeWidgets();
                        if (r) syncNativeWidgets(true);
                        if (++ra < 20) setTimeout(delayedRestore, 300);
                    }
                    setTimeout(delayedRestore, 100);
                });

                // ---- Resize au resize du node ----
                // NOTE : pas de ResizeObserver sur le container : il observe la
                // grille 2 colonnes et, apres le release de la souris, sa
                // contentRect.width peut refleter une largeur effondree
                // (grid 1fr 1fr qui passe a 1 colonne), ce qui ecrase la
                // largeur fixee par onResize avec une valeur trop petite.
                // On se fie uniquement a onResize ci-dessous.
                const onResize = node.onResize;
                node.onResize = function (size) {
                    const r = onResize?.apply(this, arguments);
                    // Hauteur VISUELLE dynamique : le DOM widget remplit l'espace restant
                    // apres les widgets natifs, sans modifier computeSize (pas de feedback loop).
                    if (container) {
                        var remainingHeight = node.size[1] - fixedWidgetsHeight() - CHROME;
                        container.style.height = Math.max(remainingHeight, DOM_WIDGET_HEIGHT) + "px";
                        container.style.width = (size[0] - 20) + "px";
                    }
                    // Forcer la grille 2 colonnes a rester en 2 colonnes
                    // (evite que le grid s'effondre si la largeur devient
                    // trop petite).
                    if (grid) {
                        grid.style.gridTemplateColumns = "1fr 1fr";
                    }
                    return r;
                };
                // Forcer la grille 2 colonnes au demarrage.
                if (grid) {
                    grid.style.gridTemplateColumns = "1fr 1fr";
                }
                // Set initial container height based on available space
                requestAnimationFrame(() => {
                    if (container) {
                        var remH = node.size[1] - fixedWidgetsHeight() - CHROME;
                        container.style.height = Math.max(remH, DOM_WIDGET_HEIGHT) + "px";
                    }
                });

                node._aihRestore = function () {
                    let ra = 0;
                    const retry = () => {
                        const restored = restoreFromNativeWidgets();
                        if (!restored && ++ra < 20) setTimeout(retry, 300);
                    };
                    retry();
                };
                    }
                });
            },

            async loadedGraphNode(node) {
                if (node._aihRestore) {
                    setTimeout(() => node._aihRestore(), 0);
                }
            },
        });
    });
    }
    aihBoot();
})();
