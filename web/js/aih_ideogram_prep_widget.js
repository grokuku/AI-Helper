/**
 * AIH Ideogram Prep — Custom DOM widget for ComfyUI node AIHIdeogramPrepNode.
 *
 * Version "découplée" du AIH Ideogram 4 Builder :
 *   - Il NE fait PAS d'appel LLM
 *   - Il sort 3 strings (llm_prompt, system_prompt, context)
 *   - L'utilisateur branche son propre node LLM (LM Studio, Ollama, etc.)
 *   - Puis le AIH Ideogram Parse parse la réponse
 *
 * DOM widget : grille 2 colonnes (Template + Style).
 *
 * Les widgets natifs ComfyUI (seed, description, element_1..4, special_instructions,
 * width, height, style_id, template_id) sont restaurés automatiquement par ComfyUI
 * au rechargement. Le DOM widget pilote style_id et template_id.
 */
(function() {
    function aihBoot() {
        // Les fichiers d'extension ComfyUI ne sont pas chargés dans un ordre
        // garanti : attendre que 03_aih_shared.js / 04_aih_widget_base.js aient
        // défini window.AIH avant d'enregistrer l'extension.
        if (!window.AIH || !window.AIH.waitForApp) { setTimeout(aihBoot, 50); return; }
    window.AIH.waitForApp(function(app) {
        app.registerExtension({
            name: "AIH.IdeogramPrep",
            async beforeRegisterNodeDef(nodeType, nodeData) {
                if (nodeData.name !== "AIHIdeogramPrepNode") return;

                AIH.registerWidget(nodeType, {
                    onCreated: function() {
                        const node = this;
                        let _aihRestored = false;

                const styleWidget = node.widgets?.find(x => x.name === "style_id");
                const templateIdWidget = node.widgets?.find(x => x.name === "template_id");
                const hideWidget = (n, name) => {
                    const w = n.widgets?.find(x => x.name === name);
                    if (w) {
                        w.hidden = true;
                        if (w.inputEl) w.inputEl.style.display = "none";
                        if (w.parentEl) w.parentEl.style.display = "none";
                    }
                };
                ["style_id", "template_id", "style_shortlist"].forEach(n => hideWidget(node, n));
                for (const inputName of ["style_id", "template_id", "style_shortlist"]) {
                    const slot = node.findInputSlot?.(inputName);
                    if (slot !== undefined && slot !== -1) {
                        node.removeInput(slot);
                    }
                }

                // ---- Fixer la taille du textarea natif description ----
                const FIXED_TA_HEIGHT = 90;
                const descriptionWidget = node.widgets?.find(w => w.name === "description");
                var _aihRealTAHeight = 150; // fallback conservateur (textarea + label wrapper)
                if (descriptionWidget) {
                    const fixDescription = () => {
                        if (descriptionWidget.inputEl) {
                            descriptionWidget.inputEl.style.height = FIXED_TA_HEIGHT + "px";
                            descriptionWidget.inputEl.style.minHeight = FIXED_TA_HEIGHT + "px";
                            descriptionWidget.inputEl.style.maxHeight = FIXED_TA_HEIGHT + "px";
                            descriptionWidget.inputEl.style.resize = "none";
                            descriptionWidget.inputEl.style.boxSizing = "border-box";
                        }
                    };
                    fixDescription();
                    // Mesurer le wrapper ComfyUI (parentEl) après rendu, pas juste le textarea.
                    // Le textarea fait 120px mais ComfyUI ajoute un label + padding autour.
                    const measureWrapper = () => {
                        if (descriptionWidget.parentEl) {
                            var rectH = descriptionWidget.parentEl.getBoundingClientRect().height;
                            if (rectH && rectH > 0) { _aihRealTAHeight = rectH; return; }
                            var pOh = descriptionWidget.parentEl.offsetHeight;
                            if (pOh && pOh > 0) { _aihRealTAHeight = pOh; return; }
                        }
                        // fallback sur le textarea lui-même
                        var taOh = descriptionWidget.inputEl && descriptionWidget.inputEl.offsetHeight;
                        if (taOh && taOh > 0) _aihRealTAHeight = taOh;
                    };
                    // Première mesure immédiate (peut être 0 si pas encore rendu)
                    measureWrapper();
                    // Seconde mesure après la frame de rendu ComfyUI
                    requestAnimationFrame(function() {
                        fixDescription();
                        measureWrapper();
                    });
                    descriptionWidget.computeSize = function() {
                        return [0, _aihRealTAHeight];
                    };
                }

                const _cache = (window.__AIH_cache = window.__AIH_cache || { styles: 0, tmpl: 0 });
                const CACHE_TTL = 15000;

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

                function syncStyleWidget(force) {
                    if (!_aihRestored && !force) return;
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
                    if (templateIdWidget) {
                        const val = parseInt(typeSelect.value) || 0;
                        templateIdWidget.value = val;
                        if (templateIdWidget.callback) templateIdWidget.callback(val);
                    }
                }

                // Helper : résoudre la sentinelle -1 en un ID de style aléatoire
                function resolveRandomStyle() {
                    if (styleSelect.value !== '_random') return parseInt(styleSelect.value) || 0;
                    var realOptions = Array.from(styleSelect.options)
                        .filter(o => o.value !== '0' && o.value !== '_random' && o.value !== '')
                        .map(o => parseInt(o.value));
                    if (realOptions.length > 0) {
                        return realOptions[Math.floor(Math.random() * realOptions.length)];
                    }
                    return 0;
                }

                function restoreFromNativeWidget() {
                    let restored = false;
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
                    if (templateIdWidget) {
                        const tid = parseInt(templateIdWidget.value) || 0;
                        if (tid > 0 && [...typeSelect.options].some(o => o.value === String(tid))) {
                            typeSelect.value = String(tid);
                            restored = true;
                        } else if (tid === 0) {
                            typeSelect.value = "0";
                        }
                    }
                    _aihRestored = true;
                    return restored;
                }

                // populateStyleSelect et refreshStylesIfStale remplacés par AIH.PickerConfig ci-dessous

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

                const grid = document.createElement("div");
                Object.assign(grid.style, {
                    display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px",
                });

                const selectStyle = {
                    width: "100%", padding: "3px 6px", borderRadius: "4px",
                    border: "1px solid #555", background: "#3a3a3e",
                    color: "#ccc", fontSize: "11px", cursor: "pointer",
                };

                const typeDiv = document.createElement("div");
                const typeSelect = document.createElement("select");
                Object.assign(typeSelect.style, selectStyle);
                typeDiv.appendChild(mkLabel("Template"));
                typeDiv.appendChild(typeSelect);
                grid.appendChild(typeDiv);

                async function populateTemplateSelect() {
                    const current = typeSelect.value;
                    typeSelect.innerHTML = '<option value="0">-- Chargement --</option>';
                    try {
                        const items = await apiGet("prompts/templates");
                        typeSelect.innerHTML = '<option value="0">-- Template --</option>';
                        if (!Array.isArray(items)) return;
                        items.forEach(t => {
                            const o = document.createElement("option");
                            o.value = t.id;
                            o.textContent = t.name || (`Template ${t.id}`);
                            typeSelect.appendChild(o);
                        });
                        if (current !== "0" && [...typeSelect.options].some(o => o.value === current)) {
                            typeSelect.value = current;
                        }
                    } catch {
                        typeSelect.innerHTML = '<option value="0">-- Template --</option>';
                    }
                }
                async function refreshTemplatesIfStale() {
                    const now = Date.now();
                    if (now - (_cache.tmpl || 0) < CACHE_TTL) return;
                    _cache.tmpl = now;
                    await populateTemplateSelect();
                }
                typeSelect.addEventListener("mousedown", refreshTemplatesIfStale);

                typeSelect.onchange = syncStyleWidget;

                const styleDiv = document.createElement("div");
                const styleRow = document.createElement("div");
                Object.assign(styleRow.style, { display: "flex", gap: "4px", alignItems: "center", width: "100%" });
                const styleSelect = document.createElement("select");
                Object.assign(styleSelect.style, selectStyle);
                styleSelect.onchange = syncStyleWidget;
                styleDiv.appendChild(mkLabel("Style"));
                styleRow.appendChild(styleSelect);
                styleDiv.appendChild(styleRow);
                grid.appendChild(styleDiv);
                // Style picker avec config modal (remplace le bouton ↻ par ⚙️)
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

                const help = document.createElement("div");
                help.style.cssText = "font-size:10px;color:#777;margin-top:4px;line-height:1.4;";
                help.innerHTML = "Sort 3 strings : <b>llm_prompt</b>, <b>system_prompt</b>, <b>context</b>.<br>Branchez un LLM puis la node Parse.";
                container.appendChild(help);

                const widget = node.addDOMWidget("AIH_IdeogramPrep", "div", container, {
                    serialize: false,
                    hideOnZoom: false,
                });
                // ---- Hauteur FIXE pour le DOM widget (anti feedback loop) ----
                // computeSize DOIT retourner une hauteur constante, sinon LiteGraph
                // aggrandit la node à chaque frame (computeDomHeight lisait node.size[1]
                // → feedback loop). La hauteur réelle du container est pilotée
                // manuellement dans onResize / init.
                const FIXED_DOM_HEIGHT = 200; // valeur fixe retournée à LiteGraph
                widget.computeSize = () => [node.size[0] - 20, FIXED_DOM_HEIGHT];

                // Somme dynamique des computeSize() de tous les widgets natifs visibles
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
                const CHROME = 70;
                function applyContainerHeight() {
                    // Le container doit faire exactement la hauteur annoncée à LiteGraph
                    container.style.height = FIXED_DOM_HEIGHT + "px";
                }

                Promise.all([populateTemplateSelect(), stylePicker.init()]).then(() => {
                    const restored = restoreFromNativeWidget();
                    if (restored) syncStyleWidget(true);
                    else {
                        typeSelect.value = String(parseInt(templateIdWidget?.value) || 0);
                        // Gérer la sentinelle -1 (random persistant)
                        var sval = parseInt(styleWidget?.value);
                        if (sval === -1 && [...styleSelect.options].some(o => o.value === '_random')) {
                            styleSelect.value = '_random';
                        } else {
                            styleSelect.value = String(sval || 0);
                        }
                        syncStyleWidget(true);
                    }
                    let ra = 0;
                    function delayedRestore() {
                        const r = restoreFromNativeWidget();
                        if (r) syncStyleWidget(true);
                        if (++ra < 20) setTimeout(delayedRestore, 300);
                    }
                    setTimeout(delayedRestore, 100);
                });

                const onResize = node.onResize;
                node.onResize = function (size) {
                    const r = onResize?.apply(this, arguments);
                    // Le container doit faire exactement la hauteur annoncée à LiteGraph
                    container.style.width = (size[0] - 20) + "px";
                    applyContainerHeight();
                    // Forcer la grille 2 colonnes pour eviter l'effondrement
                    if (grid) grid.style.gridTemplateColumns = "1fr 1fr";
                    return r;
                };
                if (grid) grid.style.gridTemplateColumns = "1fr 1fr";
                requestAnimationFrame(() => {
                    if (node.size) container.style.width = (node.size[0] - 20) + "px";
                    applyContainerHeight();
                });

                node._aihRestore = function () {
                    let ra = 0;
                    const retry = () => {
                        const restored = restoreFromNativeWidget();
                        if (restored) syncStyleWidget(true);
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
