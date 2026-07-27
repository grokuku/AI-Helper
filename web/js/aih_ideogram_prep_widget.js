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
    AIH.waitForApp(function(app) {
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
                if (styleWidget) {
                    styleWidget.hidden = true;
                    if (styleWidget.inputEl) styleWidget.inputEl.style.display = "none";
                    if (styleWidget.parentEl) styleWidget.parentEl.style.display = "none";
                }
                if (templateIdWidget) {
                    templateIdWidget.hidden = true;
                    if (templateIdWidget.inputEl) templateIdWidget.inputEl.style.display = "none";
                    if (templateIdWidget.parentEl) templateIdWidget.parentEl.style.display = "none";
                }
                for (const inputName of ["style_id", "template_id"]) {
                    const slot = node.findInputSlot?.(inputName);
                    if (slot !== undefined && slot !== -1) {
                        node.removeInput(slot);
                    }
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

                function syncStyleWidget() {
                    if (!_aihRestored) return;
                    if (styleWidget) {
                        const val = parseInt(styleSelect.value) || 0;
                        styleWidget.value = val;
                        if (styleWidget.callback) styleWidget.callback(val);
                    }
                    if (templateIdWidget) {
                        const val = parseInt(typeSelect.value) || 0;
                        templateIdWidget.value = val;
                        if (templateIdWidget.callback) templateIdWidget.callback(val);
                    }
                }

                function restoreFromNativeWidget() {
                    let restored = false;
                    if (styleWidget) {
                        const sid = parseInt(styleWidget.value) || 0;
                        if (sid > 0 && [...styleSelect.options].some(o => o.value === String(sid))) {
                            styleSelect.value = String(sid);
                            restored = true;
                        } else if (sid === 0) {
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

                async function populateStyleSelect() {
                    styleSelect.innerHTML = `<option value="0">-- Style --</option>`;
                    try {
                        const items = await apiGet("styles");
                        if (!Array.isArray(items)) return;
                        items.forEach(item => {
                            const o = document.createElement("option");
                            o.value = item.id;
                            o.textContent = item.name;
                            styleSelect.appendChild(o);
                        });
                    } catch {}
                }

                async function refreshStylesIfStale() {
                    const now = Date.now();
                    if (now - (_cache.styles || 0) < CACHE_TTL) return;
                    _cache.styles = now;
                    const oldVal = styleSelect.value;
                    await populateStyleSelect();
                    if ([...styleSelect.options].some(o => o.value === oldVal)) {
                        styleSelect.value = oldVal;
                    }
                }

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

                const styleDiv = document.createElement("div");
                const styleRow = document.createElement("div");
                Object.assign(styleRow.style, { display: "flex", gap: "4px", alignItems: "center" });
                const styleSelect = document.createElement("select");
                Object.assign(styleSelect.style, selectStyle);
                styleSelect.style.flex = "1";
                typeSelect.onchange = syncStyleWidget;
                styleSelect.onchange = syncStyleWidget;
                styleSelect.addEventListener("mousedown", refreshStylesIfStale);
                const styleRefreshBtn = document.createElement("button");
                styleRefreshBtn.textContent = "↻";
                Object.assign(styleRefreshBtn.style, {
                    padding: "2px 5px", fontSize: "10px", cursor: "pointer",
                    border: "1px solid #555", borderRadius: "3px",
                    background: "#3a3a3e", color: "#aaa", flex: "0 0 auto",
                });
                styleRefreshBtn.title = "Rafraîchir la liste des styles";
                styleRefreshBtn.onclick = () => { _cache.styles = 0; refreshStylesIfStale(); };
                styleDiv.appendChild(mkLabel("Style"));
                styleRow.appendChild(styleSelect);
                styleRow.appendChild(styleRefreshBtn);
                styleDiv.appendChild(styleRow);
                grid.appendChild(styleDiv);

                container.appendChild(grid);

                const help = document.createElement("div");
                help.style.cssText = "font-size:10px;color:#777;margin-top:4px;line-height:1.4;";
                help.innerHTML = "Sort 3 strings : <b>llm_prompt</b>, <b>system_prompt</b>, <b>context</b>.<br>Branchez un LLM puis la node Parse.";
                container.appendChild(help);

                const widget = node.addDOMWidget("AIH_IdeogramPrep", "div", container, {
                    serialize: false,
                    hideOnZoom: false,
                });
                widget.computeSize = () => [node.size[0] - 20, 105];

                Promise.all([populateTemplateSelect(), populateStyleSelect()]).then(() => {
                    const restored = restoreFromNativeWidget();
                    if (restored) syncStyleWidget();
                    else {
                        typeSelect.value = String(parseInt(templateIdWidget?.value) || 0);
                        styleSelect.value = String(parseInt(styleWidget?.value) || 0);
                        syncStyleWidget();
                    }
                    let ra = 0;
                    function delayedRestore() {
                        const r = restoreFromNativeWidget();
                        if (r) syncStyleWidget();
                        if (++ra < 20) setTimeout(delayedRestore, 300);
                    }
                    setTimeout(delayedRestore, 100);
                });

                const onResize = node.onResize;
                node.onResize = function (size) {
                    const r = onResize?.apply(this, arguments);
                    widget.computeSize = () => [size[0] - 20, 105];
                    container.style.width = (size[0] - 20) + "px";
                    // Forcer la grille 2 colonnes pour eviter l'effondrement
                    if (grid) grid.style.gridTemplateColumns = "1fr 1fr";
                    return r;
                };
                if (grid) grid.style.gridTemplateColumns = "1fr 1fr";

                node._aihRestore = function () {
                    let ra = 0;
                    const retry = () => {
                        const restored = restoreFromNativeWidget();
                        if (restored) syncStyleWidget();
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
})();
