/**
 * FR.IA Ideogram 4 Caption Builder — Widget ComfyUI
 *
 * Widgets natifs ComfyUI (visibles) : seed, width, height, description, element_1..4
 * Widgets pilotés par le DOM : preset_id, style_id, template_id
 */
(function() {
    FRIA.waitForApp(function(app) {
        app.registerExtension({
        name: "FR.IA.Ideogram4",
        // TODO: Refactor to use FRIA.registerWidget (see fria_widget_base.js)
        async beforeRegisterNodeDef(nodeType, nodeData) {
            if (nodeData.name !== "FRIAIdeogram4Node") return; // TODO: this guard could be part of FRIA.registerWidget config

            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = onNodeCreated?.apply(this, arguments);
                const node = this;
                let _friaRestored = false;

                const hideWidget = (n, name) => {
                    const w = n.widgets?.find(x => x.name === name);
                    if (w) {
                        w.hidden = true;
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

                async function populateSelect(select, apiPath, placeholder) {
                    select.innerHTML = `<option value="0">${placeholder}</option>`;
                    try {
                        const items = await apiGet(apiPath);
                        if (Array.isArray(items)) {
                            if (apiPath === "presets") {
                                try { node._friaPresets = items; } catch {}
                            }
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
                    height: "160px", minHeight: "120px", maxHeight: "260px",
                    borderRadius: "4px", border: "1px solid #555",
                    padding: "4px", background: "#1a1a1e", color: "#fff",
                    fontSize: "11px", resize: "vertical", boxSizing: "border-box",
                });
                resultTextarea.placeholder = "JSON caption Ideogram 4...";
                resultTextarea.readOnly = true;
                container.appendChild(mkLabel("Resultat"));
                container.appendChild(resultTextarea);

                const previewNote = document.createElement("div");
                Object.assign(previewNote.style, {
                    fontSize: "10px", color: "#888", textAlign: "center",
                    fontStyle: "italic", padding: "2px",
                });
                previewNote.textContent = "💡 Preview = sortie IMAGE | Debug = sortie STRING du node";
                container.appendChild(previewNote);

                const debugBtn = document.createElement("button");
                debugBtn.textContent = "🔍 Voir debug LLM";
                Object.assign(debugBtn.style, {
                    width: "100%", padding: "4px", borderRadius: "4px",
                    border: "1px solid #555", background: "#3a3a3e", color: "#ccc",
                    fontSize: "10px", cursor: "pointer", marginTop: "4px",
                });
                debugBtn.onclick = () => {
                    const md = node._lastDebugMd || "Aucun debug. Lance un Generate d'abord.";
                    const w = window.open("", "FR.IA Debug", "width=800,height=600");
                    w.document.write(`<html><head><title>FR.IA Debug</title><style>body{background:#1a1a1e;color:#ccc;font-family:monospace;font-size:12px;padding:16px;}pre{white-space:pre-wrap;background:#2a2a2e;padding:8px;border-radius:4px;}h1,h2,h3{color:#6366f1;}h2{border-bottom:1px solid #444;padding-bottom:4px;}code{background:#3a3a3e;padding:1px 4px;border-radius:2px;}</style></head><body></body></html>`);
                    const pre = w.document.createElement("pre");
                    pre.textContent = md;
                    w.document.body.appendChild(pre);
                    w.document.close();
                };
                container.appendChild(debugBtn);

                const domWidget = node.addDOMWidget("ideogram4_ui", "custom", container, {
                    getValue: () => "",
                    setValue: (v) => {},
                });
                domWidget.options = domWidget.options || {};
                domWidget.options.height = 320;

                const MIN_WIDTH = 340;
                const origOnResize = node.onResize;
                node.onResize = function (size) {
                    if (origOnResize) origOnResize.call(this, size);
                    if (size[0] < MIN_WIDTH) size[0] = MIN_WIDTH;
                    container.style.width = (size[0] - 20) + "px";
                };
                requestAnimationFrame(() => {
                    if (node.size && node.size[0] < MIN_WIDTH) node.setSize([MIN_WIDTH, node.size[1]]);
                    if (node.size) container.style.width = (node.size[0] - 20) + "px";
                });

                node._resultArea = resultTextarea;
                node._domWidget = domWidget;

                function syncNativeWidgets() {
                    if (!_friaRestored) return;
                    const set = (name, val) => {
                        const w = node.widgets?.find(x => x.name === name);
                        if (!w) return;
                        w.value = val;
                        if (w.callback) w.callback(val);
                    };
                    set("preset_id", parseInt(presetSelect.value) || 0);
                    set("style_id", parseInt(styleSelect.value) || 0);
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
                        if (sw && sw.value > 0 && [...styleSelect.options].some(o => o.value === String(sw.value))) {
                            styleSelect.value = String(sw.value);
                            restored = true;
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
                    _friaRestored = true;
                    return restored;
                }
                node._friaRestore = function () {
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

                    const payload = {
                        text: description,
                        seed: seedW > 0 ? seedW : null,
                        template_id: parseInt(templateSelect.value) || 0,
                        validation_template_id: parseInt(valTmplSelect.value) || 0,
                        width: widthW || 1024,
                        height: heightW || 1024,
                        ep_elements: elTexts.map(t => ({ type: "text", text: t })),
                        preset_id: parseInt(presetSelect.value) || null,
                        style_id: parseInt(styleSelect.value) || null,
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
            if (node._friaRestore) {
                setTimeout(() => node._friaRestore(), 0);
            }
        },
    });
    });
})();
