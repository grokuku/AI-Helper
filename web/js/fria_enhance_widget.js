/**
 * FR.IA Prompt Enhancer — Custom DOM widget for ComfyUI node FRIAEnhanceNode.
 *
 * Le DOM widget pilote les widgets natifs (template_id, preset_id, style_id)
 * qui sont serialises par ComfyUI. L'api_key est lue depuis
 * ComfyUI/user/default/fria_credentials.json cote Python.
 *
 * Le DOM widget :
 *   - Dropdown Preset IA (peuple depuis /api/presets) -> set widget preset_id
 *   - Dropdown Template (peuple depuis /api/prompts/templates) -> set widget template_id
 *   - Dropdown Style (peuple depuis /api/styles) -> set widget style_id
 *   - Bouton "Test enhance"
 *   - Textarea resultat
 */
(function() {
    FRIA.waitForApp(function(app) {
        app.registerExtension({
        name: "FR.IA.Enhance",
        // TODO: Refactor to use FRIA.registerWidget (see fria_widget_base.js)
        async beforeRegisterNodeDef(nodeType, nodeData) {
            if (nodeData.name !== "FRIAEnhanceNode") return; // TODO: this guard could be part of FRIA.registerWidget config

            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = onNodeCreated?.apply(this, arguments);
                const node = this;
                let _friaRestored = false;

                // ---- Cacher les widgets natifs pilotés par le DOM ----
                const hideWidget = (n, name) => {
                    const w = n.widgets?.find(x => x.name === name);
                    if (w) {
                        w.hidden = true;
                        if (w.inputEl) w.inputEl.style.display = "none";
                        if (w.parentEl) w.parentEl.style.display = "none";
                    }
                };
                ["template_id", "preset_id", "style_id", "style_shortlist"].forEach(n => hideWidget(node, n));

                for (const inputName of ["template_id", "preset_id", "style_id"]) {
                    const slot = node.findInputSlot?.(inputName);
                    if (slot !== undefined && slot !== -1) {
                        node.removeInput(slot);
                    }
                }

                // Refs vers les widgets natifs
                const templateIdWidget = node.widgets?.find(x => x.name === "template_id");
                const presetIdWidget = node.widgets?.find(x => x.name === "preset_id");
                const styleIdWidget = node.widgets?.find(x => x.name === "style_id");

                // ---- Helpers API ----
                const getApiUrl = () => {
                    try {
                        const cfg = JSON.parse(localStorage.getItem("FRIA_config") || "{}");
                        const base = (cfg.serverUrl || "https://kw.holaf.fr").replace(/\/+$/, "");
                        return base + "/api";
                    } catch {
                        return "https://kw.holaf.fr/api";
                    }
                };
                const getApiKey = () => window.FRIA.getApiKey();
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

                const _cache = (window.__FRIA_cache = window.__FRIA_cache || { presets: 0, styles: 0, tmpl: 0 });
                const CACHE_TTL = 15000;

                // ---- Populate generique ----
                async function populateSelect(select, apiPath, placeholder, cacheKey, valKey) {
                    select.innerHTML = `<option value="0">${placeholder}</option>`;
                    try {
                        const items = await apiGet(apiPath);
                        if (!Array.isArray(items)) return;
                        if (apiPath === "presets") {
                            try { node._friaPresets = items; } catch {}
                        }
                        items.forEach(item => {
                            const o = document.createElement("option");
                            o.value = item[valKey || "id"];
                            o.textContent = item.name;
                            select.appendChild(o);
                        });
                    } catch {}
                }
                async function refreshIfStale(select, apiPath, cacheKey, placeholder, valKey) {
                    const now = Date.now();
                    if (now - (_cache[cacheKey] || 0) < CACHE_TTL) return;
                    _cache[cacheKey] = now;
                    const oldVal = select.value;
                    await populateSelect(select, apiPath, placeholder, cacheKey, valKey);
                    if ([...select.options].some(o => o.value === oldVal)) select.value = oldVal;
                }

                // ---- Container ----
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

                // ---- Grille Template + Style (ligne 1) ----
                const tsRow = document.createElement("div");
                Object.assign(tsRow.style, { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px" });

                const templateDiv = document.createElement("div");
                const templateSelect = document.createElement("select");
                Object.assign(templateSelect.style, { width: "100%", padding: "3px 6px", borderRadius: "4px", border: "1px solid #555", background: "#3a3a3e", color: "#ccc", fontSize: "11px", cursor: "pointer" });
                templateDiv.appendChild(mkLabel("Template"));
                templateDiv.appendChild(templateSelect);
                tsRow.appendChild(templateDiv);

                const styleDiv = document.createElement("div");
                const styleRow = document.createElement("div");
                Object.assign(styleRow.style, { display: "flex", gap: "4px", alignItems: "center", width: "100%" });
                const styleSelect = document.createElement("select");
                Object.assign(styleSelect.style, { width: "100%", padding: "3px 6px", borderRadius: "4px", border: "1px solid #555", background: "#3a3a3e", color: "#ccc", fontSize: "11px", cursor: "pointer" });
                styleDiv.appendChild(mkLabel("Style"));
                styleRow.appendChild(styleSelect);
                styleDiv.appendChild(styleRow);
                tsRow.appendChild(styleDiv);
                // Style picker avec config modal
                var stylePicker = FRIA.PickerConfig.setup({
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
                container.appendChild(tsRow);

                // ---- Preset IA (ligne 2, full width) ----
                const presetDiv = document.createElement("div");
                const presetRow = document.createElement("div");
                Object.assign(presetRow.style, { display: "flex", gap: "4px", alignItems: "center" });
                const presetSelect = document.createElement("select");
                Object.assign(presetSelect.style, { width: "100%", padding: "3px 6px", borderRadius: "4px", border: "1px solid #555", background: "#3a3a3e", color: "#ccc", fontSize: "11px", cursor: "pointer" });
                presetSelect.style.flex = "1";
                presetDiv.appendChild(mkLabel("Preset IA"));
                presetRow.appendChild(presetSelect);
                presetDiv.appendChild(presetRow);
                container.appendChild(presetDiv);

                // ---- Bouton Test Enhance ----
                const enhanceBtn = document.createElement("button");
                enhanceBtn.textContent = "🔄  Test enhance";
                Object.assign(enhanceBtn.style, {
                    width: "100%", padding: "6px", borderRadius: "4px",
                    border: "none", background: "#6366f1", color: "white",
                    fontSize: "11px", fontWeight: "600", cursor: "pointer",
                });
                enhanceBtn.onmouseenter = () => enhanceBtn.style.background = "#5558e8";
                enhanceBtn.onmouseleave = () => enhanceBtn.style.background = "#6366f1";
                container.appendChild(enhanceBtn);

                // ---- Textarea resultat ----
                const resultTextarea = document.createElement("textarea");
                Object.assign(resultTextarea.style, {
                    width: "100%",
                    height: "120px", minHeight: "80px", maxHeight: "260px",
                    borderRadius: "4px", border: "1px solid #555",
                    padding: "4px", background: "#1a1a1e", color: "#fff",
                    fontSize: "11px", resize: "vertical", boxSizing: "border-box",
                });
                resultTextarea.placeholder = "Résultat de l'enhance...";
                resultTextarea.readOnly = true;
                container.appendChild(resultTextarea);

                // ---- Ajout au node ----
                const widget = node.addDOMWidget("FRIA_Enhance", "div", container, {
                    serialize: false,
                    hideOnZoom: false,
                });
                widget.computeSize = () => [node.size[0] - 20, 240];

                // ---- Sync des widgets natifs ----
                function syncNativeWidgets(force) {
                    if (!_friaRestored && !force) return;
                    const set = (name, val) => {
                        const w = node.widgets?.find(x => x.name === name);
                        if (!w) return;
                        w.value = val;
                        if (w.callback) w.callback(val);
                    };
                    set("template_id", parseInt(templateSelect.value) || 0);
                    set("preset_id", parseInt(presetSelect.value) || 0);
                    set("style_id", parseInt(styleSelect.value) || 0);
                }

                templateSelect.onchange = syncNativeWidgets;
                presetSelect.onchange = syncNativeWidgets;
                styleSelect.onchange = syncNativeWidgets;

                // ---- Restoration depuis widgets natifs ----
                function restoreFromNativeWidgets() {
                    let restored = false;
                    if (templateIdWidget) {
                        const tid = parseInt(templateIdWidget.value) || 0;
                        if (tid > 0 && [...templateSelect.options].some(o => o.value === String(tid))) {
                            templateSelect.value = String(tid);
                            restored = true;
                        } else if (tid === 0) {
                            templateSelect.value = "0";
                        }
                    }
                    if (presetIdWidget) {
                        const pid = parseInt(presetIdWidget.value) || 0;
                        if (pid > 0 && [...presetSelect.options].some(o => o.value === String(pid))) {
                            presetSelect.value = String(pid);
                            restored = true;
                        } else if (pid === 0) {
                            presetSelect.value = "0";
                        }
                    }
                    if (styleIdWidget) {
                        const sid = parseInt(styleIdWidget.value) || 0;
                        if (sid > 0 && [...styleSelect.options].some(o => o.value === String(sid))) {
                            styleSelect.value = String(sid);
                            restored = true;
                        } else if (sid === 0) {
                            styleSelect.value = "0";
                        }
                    }
                    _friaRestored = true;
                    return restored;
                }

                node._friaRestore = function () {
                    let ra = 0;
                    const retry = () => {
                        const restored = restoreFromNativeWidgets();
                        if (restored) syncNativeWidgets();
                        if (!restored && ++ra < 20) setTimeout(retry, 300);
                    };
                    retry();
                };

                // ---- Initialisation ----
                Promise.all([
                    stylePicker.init(),
                    populateSelect(templateSelect, "prompts/templates", "-- Template --", "tmpl"),
                    populateSelect(presetSelect, "presets", "-- Preset IA --", "presets"),
                ]).then(() => {
                    const restored = restoreFromNativeWidgets();
                    if (restored) syncNativeWidgets();
                    else {
                        templateSelect.value = String(parseInt(templateIdWidget?.value) || 0);
                        presetSelect.value = String(parseInt(presetIdWidget?.value) || 0);
                        styleSelect.value = String(parseInt(styleIdWidget?.value) || 0);
                        syncNativeWidgets();
                    }
                    let ra = 0;
                    function delayedRestore() {
                        const r = restoreFromNativeWidgets();
                        if (r) syncNativeWidgets();
                        if (++ra < 20) setTimeout(delayedRestore, 300);
                    }
                    setTimeout(delayedRestore, 100);
                });

                // ---- mousedown : refresh cache ----
                templateSelect.addEventListener("mousedown", () => refreshIfStale(templateSelect, "prompts/templates", "tmpl", "-- Template --"));
                presetSelect.addEventListener("mousedown", () => refreshIfStale(presetSelect, "presets", "presets", "-- Preset IA --"));

                // ---- Resize : pas de ResizeObserver (voir fria_prep_widget.js) ----
                const onResize = node.onResize;
                node.onResize = function (size) {
                    const r = onResize?.apply(this, arguments);
                    widget.computeSize = () => [size[0] - 20, 240];
                    container.style.width = (size[0] - 20) + "px";
                    tsRow.style.gridTemplateColumns = "1fr 1fr";
                    return r;
                };
                tsRow.style.gridTemplateColumns = "1fr 1fr";

                // ---- Test Enhance ----
                enhanceBtn.onclick = async () => {
                    const get = (name) => node.widgets?.find(w => w.name === name);
                    const basePrompt = (get("base_prompt")?.value || "").trim();
                    const specialInstructions = (get("special_instructions")?.value || "").trim();
                    const seedW = get("seed")?.value;
                    const elementsRaw = get("elements")?.value || "[]";

                    let elements = [];
                    try {
                        const parsed = JSON.parse(elementsRaw);
                        if (Array.isArray(parsed)) elements = parsed;
                        else if (parsed?.elements) elements = parsed.elements;
                    } catch {
                        // Texte brut
                    }

                    const payload = {
                        text: basePrompt,
                        seed: seedW > 0 ? seedW : null,
                        template_id: parseInt(templateSelect.value) || 0,
                        preset_id: parseInt(presetSelect.value) || null,
                        style_id: parseInt(styleSelect.value) || null,
                        special_instructions: specialInstructions,
                    };
                    if (elements.length > 0) {
                        payload.ep_elements = elements;
                    } else if (elementsRaw.trim() && elementsRaw !== "[]") {
                        payload.text = (basePrompt + "\n\n" + elementsRaw).trim();
                    }

                    if (!payload.text && (!payload.ep_elements || payload.ep_elements.length === 0)) {
                        resultTextarea.value = "Saisis au moins le prompt de base ou des éléments.";
                        return;
                    }

                    resultTextarea.value = "Enhancement en cours...";
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
                        let neg = "";
                        for (const line of text.split("\n")) {
                            if (!line.trim()) continue;
                            try {
                                const chunk = JSON.parse(line);
                                if (chunk.status === "done") {
                                    output = chunk.output || "";
                                    neg = chunk.negative_prompt || "";
                                } else if (chunk.status === "error") {
                                    throw new Error(chunk.error || "Erreur inconnue");
                                }
                            } catch (e) {
                                if (e instanceof SyntaxError) continue;
                                throw e;
                            }
                        }
                        const sep = neg ? "\n\n--- Negative prompt ---\n" + neg : "";
                        resultTextarea.value = output + sep;
                        syncNativeWidgets();
                    } catch (err) {
                        resultTextarea.value = "Erreur: " + err.message;
                    }
                };

                // ---- onExecuted ----
                const origExec = node.onExecuted;
                node.onExecuted = function (output) {
                    if (origExec) origExec.call(this, output);
                    const arr = output?.prompt;
                    if (Array.isArray(arr) && arr.length > 0) {
                        const neg = output?.negative_prompt;
                        const out = String(arr[0]);
                        const sep = Array.isArray(neg) && neg[0] ? "\n\n--- Negative prompt ---\n" + neg[0] : "";
                        resultTextarea.value = out + sep;
                    }
                };

                return r;
            };
        },

        async loadedGraphNode(node) {
            if (node._friaRestore) {
                setTimeout(() => node._friaRestore(), 0);
            }
        },
    });
    });
})();
