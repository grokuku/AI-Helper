/**
 * AIH Ideogram 4 Caption Builder — Widget ComfyUI
 *
 * Widgets natifs ComfyUI (visibles) : seed, width, height, description, element_1..4
 * Widgets pilotés par le DOM : preset_id, style_id, template_id
 */
(function() {
    function aihBoot() {
        // Les fichiers d'extension ComfyUI ne sont pas chargés dans un ordre
        // garanti : attendre que 03_aih_shared.js / 04_aih_widget_base.js aient
        // défini window.AIH avant d'enregistrer l'extension.
        if (!window.AIH || !window.AIH.waitForApp) { setTimeout(aihBoot, 50); return; }
    window.AIH.waitForApp(function(app) {
        app.registerExtension({
        name: "AIH.Ideogram4",
        // TODO: Refactor to use AIH.registerWidget (see aih_widget_base.js)
        async beforeRegisterNodeDef(nodeType, nodeData) {
            if (nodeData.name !== "AIHIdeogram4Node") return; // TODO: this guard could be part of AIH.registerWidget config

            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = onNodeCreated?.apply(this, arguments);
                const node = this;
                let _aihRestored = false;

                const hideWidget = (n, name) => {
                    const w = n.widgets?.find(x => x.name === name);
                    if (w) {
                        // NE PAS mettre hidden=true — la nouvelle frontend Vue exclut
                        // les widgets hidden de widgets_values au chargement (données perdues)
                        w.computeSize = () => [0, -4];  // 0 place visuelle (compressé)
                        if (w.element) w.element.style.display = "none";
                        if (w.inputEl) w.inputEl.style.display = "none";
                        if (w.parentEl) w.parentEl.style.display = "none";
                        return w;
                    }
                    return null;
                };

                hideWidget(node, "preset_id");
                hideWidget(node, "style_id");
                hideWidget(node, "style_shortlist");
                hideWidget(node, "template_id");
                hideWidget(node, "validation_template_id");

                for (const inputName of ["preset_id", "style_id", "template_id", "validation_template_id"]) {
                    const slot = node.findInputSlot?.(inputName);
                    if (slot !== undefined && slot !== -1) {
                        node.removeInput(slot);
                    }
                }

                // ---- Fixer la taille du textarea natif description ----
                const FIXED_TA_HEIGHT = 90;
                const descriptionWidget = node.widgets?.find(w => w.name === "description");
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
                    // Hauteur du wrapper = textarea (90px) + label (~20px) + padding (~8px) ≈ 118px
                    // Calcul explicite pour eviter les problemes de mesure asynchrone.
                    var textareaWrapperHeight = FIXED_TA_HEIGHT + 20;
                    descriptionWidget.computeSize = function() {
                        return [0, textareaWrapperHeight];
                    };
                }

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

                const _cache = (window.__AIH_cache = window.__AIH_cache || { presets: 0, styles: 0, tmpl: 0 });
                const CACHE_TTL = 15000;

                async function populateSelect(select, apiPath, placeholder) {
                    select.innerHTML = `<option value="0">${placeholder}</option>`;
                    try {
                        const items = await apiGet(apiPath);
                        if (Array.isArray(items)) {
                            if (apiPath === "presets") {
                                try { node._aihPresets = items; } catch {}
                            }
                            // Trier par ordre alphabétique
                            items.sort(function(a, b) {
                                var nameA = (a.name || a.title || a.text || a).toString().toLowerCase();
                                var nameB = (b.name || b.title || b.text || b).toString().toLowerCase();
                                return nameA.localeCompare(nameB);
                            });
                            items.forEach(item => {
                                const o = document.createElement("option");
                                o.value = item.id;
                                o.textContent = item.name;
                                select.appendChild(o);
                            });
                        }
                    } catch {}
                }
                async function refreshIfStale(select, apiPath, cacheKey) {
                    const now = Date.now();
                    if (now - (_cache[cacheKey] || 0) < CACHE_TTL) return;
                    _cache[cacheKey] = now;
                    const oldVal = select.value;
                    await populateSelect(select, apiPath, select.options[0]?.textContent || "--");
                    if ([...select.options].some(o => o.value === oldVal)) select.value = oldVal;
                }

                const container = document.createElement("div");
                Object.assign(container.style, {
                    width: "100%",
                    padding: "8px", boxSizing: "border-box",
                    background: "#2a2a2e", borderRadius: "8px",
                    display: "flex", flexDirection: "column", gap: "6px",
                    fontSize: "12px", color: "#ccc", overflow: "hidden",
                });

                function mkLabel(text) {
                    const l = document.createElement("label");
                    l.textContent = text;
                    l.style.cssText = "font-size:10px;color:#888;display:block;margin-bottom:2px;";
                    return l;
                }

                const tsRow = document.createElement("div");
                Object.assign(tsRow.style, { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "6px" });

                const templateDiv = document.createElement("div");
                const templateSelect = document.createElement("select");
                Object.assign(templateSelect.style, { width: "100%", padding: "3px 6px", borderRadius: "4px", border: "1px solid #555", background: "#3a3a3e", color: "#ccc", fontSize: "11px", cursor: "pointer" });
                templateDiv.appendChild(mkLabel("Template"));
                templateDiv.appendChild(templateSelect);
                tsRow.appendChild(templateDiv);

                const styleDiv = document.createElement("div");
                const styleSelect = document.createElement("select");
                Object.assign(styleSelect.style, { width: "100%", padding: "3px 6px", borderRadius: "4px", border: "1px solid #555", background: "#3a3a3e", color: "#ccc", fontSize: "11px", cursor: "pointer" });
                styleDiv.appendChild(mkLabel("Style"));
                styleDiv.appendChild(styleSelect);
                tsRow.appendChild(styleDiv);
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

                const valTmplDiv = document.createElement("div");
                const valTmplSelect = document.createElement("select");
                Object.assign(valTmplSelect.style, { width: "100%", padding: "3px 6px", borderRadius: "4px", border: "1px solid #555", background: "#3a3a3e", color: "#ccc", fontSize: "11px", cursor: "pointer" });
                valTmplDiv.appendChild(mkLabel("Validation"));
                valTmplDiv.appendChild(valTmplSelect);
                valTmplSelect.addEventListener("mousedown", refreshTemplatesIfStale);
                tsRow.appendChild(valTmplDiv);
                container.appendChild(tsRow);

                const presetDiv = document.createElement("div");
                const presetSelect = document.createElement("select");
                Object.assign(presetSelect.style, { width: "100%", padding: "3px 6px", borderRadius: "4px", border: "1px solid #555", background: "#3a3a3e", color: "#ccc", fontSize: "11px", cursor: "pointer" });
                presetSelect.addEventListener("mousedown", () => refreshIfStale(presetSelect, "presets", "presets"));
                presetDiv.appendChild(mkLabel("Preset IA"));
                presetDiv.appendChild(presetSelect);
                container.appendChild(presetDiv);

                async function populateTemplateSelect() {
                    const current = templateSelect.value;
                    templateSelect.innerHTML = '<option value="0">-- Chargement --</option>';
                    try {
                        const items = await apiGet("prompts/templates");
                        templateSelect.innerHTML = '<option value="0">-- Template --</option>';
                        if (!Array.isArray(items)) return;
                        // Trier par ordre alphabétique
                        items.sort(function(a, b) {
                            var nameA = (a.name || a.title || a.text || a).toString().toLowerCase();
                            var nameB = (b.name || b.title || b.text || b).toString().toLowerCase();
                            return nameA.localeCompare(nameB);
                        });
                        items.forEach(t => {
                            const o = document.createElement("option");
                            o.value = t.id;
                            o.textContent = t.name || (`Template ${t.id}`);
                            templateSelect.appendChild(o);
                        });
                        if (current !== "0" && [...templateSelect.options].some(o => o.value === current)) {
                            templateSelect.value = current;
                        }
                    } catch {
                        templateSelect.innerHTML = '<option value="0">-- Template --</option>';
                    }
                }
                async function refreshTemplatesIfStale() {
                    const now = Date.now();
                    if (now - (_cache.tmpl || 0) < CACHE_TTL) return;
                    _cache.tmpl = now;
                    await populateTemplateSelect();
                }
                templateSelect.addEventListener("mousedown", refreshTemplatesIfStale);

                const generateBtn = document.createElement("button");
                generateBtn.textContent = "🔄  Generate Ideogram 4 caption";
                Object.assign(generateBtn.style, {
                    width: "100%", padding: "6px", borderRadius: "4px",
                    border: "none", background: "#6366f1", color: "white",
                    fontSize: "11px", fontWeight: "600", cursor: "pointer",
                });
                generateBtn.onmouseenter = () => generateBtn.style.background = "#5558e8";
                generateBtn.onmouseleave = () => generateBtn.style.background = "#6366f1";
                container.appendChild(generateBtn);

                const resultTextarea = document.createElement("textarea");
                Object.assign(resultTextarea.style, {
                    width: "100%",
                    flex: "1", minHeight: "120px",
                    borderRadius: "4px", border: "1px solid #555",
                    padding: "4px", background: "#1a1a1e", color: "#fff",
                    fontSize: "11px", resize: "none", boxSizing: "border-box",
                });
                resultTextarea.placeholder = "JSON caption Ideogram 4...";
                resultTextarea.readOnly = true;
                container.appendChild(mkLabel("Resultat"));
                container.appendChild(resultTextarea);

                const domWidget = node.addDOMWidget("ideogram4_ui", "custom", container, {
                    getValue: () => "",
                    setValue: (v) => {},
                });
                domWidget.options = domWidget.options || {};

                // ---- Hauteur FIXE pour le DOM widget (anti feedback loop) ----
                // computeSize DOIT retourner une hauteur constante, sinon LiteGraph
                // aggrandit la node à chaque frame (computeDomHeight lisait node.size[1]
                // → feedback loop). La hauteur réelle du container est pilotée
                // manuellement dans onResize / init.
                const FIXED_DOM_HEIGHT = 200; // valeur fixe retournée à LiteGraph
                domWidget.computeSize = () => [node.size[0] - 20, FIXED_DOM_HEIGHT];

                // Hauteur des widgets natifs (description + autres ~26px chacun)
                // Somme dynamique des computeSize() de tous les widgets natifs visibles
                function fixedWidgetsHeight() {
                    let h = 0;
                    for (const w of node.widgets) {
                        if (w === domWidget) continue;
                        if (w.hidden) continue;
                        let wh = 26;
                        if (w.computeSize) {
                            try {
                                const s = w.computeSize();
                                if (Array.isArray(s) && s[1] !== undefined) wh = s[1];
                            } catch {}
                        }
                        // Widgets compressés (computeSize [0,-4], sans hidden=true) : 0 px
                        if (wh <= 0) continue;
                        h += wh;
                    }
                    return h;
                }
                const CHROME = 112; // titre node + padding (+42 pour corriger le décalage de 42px)
                // La hauteur du container est pilotée dynamiquement dans onResize
                // via le calcul de l'espace restant (node.size[1] - fixedWidgetsHeight() - CHROME).
                // applyContainerHeight n'est plus utilisée ; le calcul est fait directement
                // dans onResize et requestAnimationFrame.

                const MIN_WIDTH = 340;
                const origOnResize = node.onResize;
                node.onResize = function (size) {
                    if (origOnResize) origOnResize.call(this, size);
                    if (size[0] < MIN_WIDTH) size[0] = MIN_WIDTH;
                    // Hauteur VISUELLE dynamique : le DOM widget remplit l'espace restant
                    // apres les widgets natifs, sans modifier computeSize (pas de feedback loop).
                    var remainingHeight = node.size[1] - fixedWidgetsHeight() - CHROME;
                    container.style.height = Math.max(remainingHeight, FIXED_DOM_HEIGHT) + "px";
                    container.style.width = (size[0] - 20) + "px";
                };
                requestAnimationFrame(() => {
                    if (node.size && node.size[0] < MIN_WIDTH) node.setSize([MIN_WIDTH, node.size[1]]);
                    if (node.size) {
                        container.style.width = (node.size[0] - 20) + "px";
                        var remH = node.size[1] - fixedWidgetsHeight() - CHROME;
                        container.style.height = Math.max(remH, FIXED_DOM_HEIGHT) + "px";
                    }
                });

                node._resultArea = resultTextarea;
                node._domWidget = domWidget;

                function syncNativeWidgets() {
                    if (!_aihRestored) return;
                    const set = (name, val) => {
                        const w = node.widgets?.find(x => x.name === name);
                        if (!w) return;
                        w.value = val;
                        if (w.callback) w.callback(val);
                    };
                    set("preset_id", parseInt(presetSelect.value) || 0);
                    var sval;
                    if (styleSelect.value === '_random') {
                        sval = -1;  // sentinelle : random persistant
                    } else {
                        sval = parseInt(styleSelect.value) || 0;
                    }
                    set("style_id", sval);
                    set("template_id", parseInt(templateSelect.value) || 0);
                    set("validation_template_id", parseInt(valTmplSelect.value) || 0);
                }

                presetSelect.onchange = syncNativeWidgets;
                styleSelect.onchange = syncNativeWidgets;
                templateSelect.onchange = syncNativeWidgets;
                valTmplSelect.onchange = syncNativeWidgets;

                function restoreFromWidgets(n) {
                    let restored = false;
                    const pw = n.widgets?.find(x => x.name === "preset_id");
                    const sw = n.widgets?.find(x => x.name === "style_id");
                    const tw = n.widgets?.find(x => x.name === "template_id");
                    const vw = n.widgets?.find(x => x.name === "validation_template_id");
                    try {
                        if (pw && pw.value > 0 && [...presetSelect.options].some(o => o.value === String(pw.value))) {
                            presetSelect.value = String(pw.value);
                            restored = true;
                        }
                        if (sw) {
                            const sid = parseInt(sw.value);
                            if (sid === -1 && [...styleSelect.options].some(o => o.value === '_random')) {
                                styleSelect.value = '_random';
                                restored = true;
                            } else if (sid > 0 && [...styleSelect.options].some(o => o.value === String(sid))) {
                                styleSelect.value = String(sid);
                                restored = true;
                            }
                        }
                        if (tw) {
                            const tid = parseInt(tw.value) || 0;
                            if (tid > 0 && [...templateSelect.options].some(o => o.value === String(tid))) {
                                templateSelect.value = String(tid);
                                restored = true;
                            } else if (tid === 0) {
                                templateSelect.value = "0";
                            }
                        }
                        if (vw) {
                            const vid = parseInt(vw.value) || 0;
                            if (vid > 0 && [...valTmplSelect.options].some(o => o.value === String(vid))) {
                                valTmplSelect.value = String(vid);
                                restored = true;
                            } else if (vid === 0) {
                                valTmplSelect.value = "0";
                            }
                        }
                    } catch {}
                    _aihRestored = true;
                    return restored;
                }
                node._aihRestore = function () {
                    let ra = 0;
                    const retry = () => {
                        const restored = restoreFromWidgets(node);
                        if (restored) syncNativeWidgets();
                        if (!restored && ++ra < 20) setTimeout(retry, 300);
                    };
                    retry();
                };

                Promise.all([
                    stylePicker.init(),
                    populateSelect(presetSelect, "presets", "-- Preset IA --"),
                    populateTemplateSelect(),
                    populateSelect(valTmplSelect, "prompts/templates", "-- Pas de validation --"),
                ]).then(() => {
                    const restored = restoreFromWidgets(node);
                    if (restored) syncNativeWidgets();
                    else {
                        const tw = node.widgets?.find(x => x.name === "template_id");
                        const tid = parseInt(tw?.value) || 0;
                        templateSelect.value = String(tid);
                        syncNativeWidgets();
                    }
                    let ra = 0;
                    function delayedRestore() {
                        const r = restoreFromWidgets(node);
                        if (r) syncNativeWidgets();
                        if (++ra < 20) setTimeout(delayedRestore, 300);
                    }
                    setTimeout(delayedRestore, 100);
                });

                generateBtn.onclick = async () => {
                    const get = (name) => node.widgets?.find(w => w.name === name);
                    const description = (get("description")?.value || "").trim();
                    const elTexts = ["element_1", "element_2", "element_3", "element_4"]
                        .map(n => (get(n)?.value || "").trim())
                        .filter(Boolean);
                    const seedW = get("seed")?.value;
                    const widthW = get("width")?.value;
                    const heightW = get("height")?.value;

                    // Résoudre le style aléatoire si la sentinelle -1 est active
                    let styleId = parseInt(styleSelect.value) || null;
                    if (styleSelect.value === '_random' || styleId === -1) {
                        var realOptions = Array.from(styleSelect.options)
                            .filter(o => o.value !== '0' && o.value !== '_random' && o.value !== '')
                            .map(o => parseInt(o.value));
                        if (realOptions.length > 0) {
                            styleId = realOptions[Math.floor(Math.random() * realOptions.length)];
                        }
                    }

                    const payload = {
                        text: description,
                        seed: seedW > 0 ? seedW : null,
                        template_id: parseInt(templateSelect.value) || 0,
                        validation_template_id: parseInt(valTmplSelect.value) || 0,
                        width: widthW || 1024,
                        height: heightW || 1024,
                        ep_elements: elTexts.map(t => ({ type: "text", text: t })),
                        preset_id: parseInt(presetSelect.value) || null,
                        style_id: styleId,
                    };

                    if (!description && elTexts.length === 0) {
                        resultTextarea.value = "Decris au moins la scene generale ou un element.";
                        return;
                    }

                    resultTextarea.value = "Generation en cours...";
                    try {
                        const resp = await fetch(`${getApiUrl()}/enhance`, {
                            method: "POST", headers: apiHeaders(), body: JSON.stringify(payload),
                        });
                        if (!resp.ok) {
                            const t = await resp.text().catch(() => "");
                            throw new Error(`HTTP ${resp.status}: ${t.substring(0, 200)}`);
                        }
                        const text = await resp.text();
                        let output = "";
                        for (const line of text.split("\n")) {
                            if (!line.trim()) continue;
                            try {
                                const chunk = JSON.parse(line);
                                if (chunk.status === "done") {
                                    output = chunk.output || "";
                                    if (chunk.debug_md) node._lastDebugMd = chunk.debug_md;
                                } else if (chunk.status === "error") {
                                    throw new Error(chunk.error || "Erreur inconnue");
                                }
                            } catch (e) {
                                if (e instanceof SyntaxError) continue;
                                throw e;
                            }
                        }
                        resultTextarea.value = output;
                        syncNativeWidgets();
                    } catch (err) {
                        resultTextarea.value = "Erreur: " + err.message;
                    }
                };

                const origExec = node.onExecuted;
                node.onExecuted = function (output) {
                    if (origExec) origExec.call(this, output);
                    const arr = output?.prompt;
                    if (Array.isArray(arr) && arr.length > 0) {
                        resultTextarea.value = String(arr[0]);
                    }
                };

                return r;
            };
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
