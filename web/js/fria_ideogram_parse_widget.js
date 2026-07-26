/**
 * FR.IA Ideogram Parse — Widget ComfyUI
 *
 * Widgets natifs : llm_response, context (STRING, forceInput)
 * Widget piloté par le DOM : validation_template_id (INT)
 */
(function waitForApp() {
    const app = window.app || window.comfyAPI?.app?.app;
    if (!app) { setTimeout(waitForApp, 100); return; }

    app.registerExtension({
        name: "FR.IA.IdeogramParse",
        async beforeRegisterNodeDef(nodeType, nodeData) {
            if (nodeData.name !== "FRIAIdeogramParseNode") return;

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

                hideWidget(node, "validation_template_id");

                const slot = node.findInputSlot?.("validation_template_id");
                if (slot !== undefined && slot !== -1) {
                    node.removeInput(slot);
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

                const container = document.createElement("div");
                Object.assign(container.style, {
                    width: "100%", padding: "6px 8px", boxSizing: "border-box",
                    background: "#2a2a2e", borderRadius: "6px",
                    display: "flex", flexDirection: "column", gap: "4px",
                    fontSize: "12px", color: "#ccc",
                });

                const label = document.createElement("label");
                label.textContent = "Validation Template (optionnel)";
                label.style.cssText = "font-size:10px;color:#888;display:block;";

                const select = document.createElement("select");
                Object.assign(select.style, {
                    width: "100%", padding: "3px 6px", borderRadius: "4px",
                    border: "1px solid #555", background: "#3a3a3e",
                    color: "#ccc", fontSize: "11px", cursor: "pointer",
                });

                const note = document.createElement("div");
                note.style.cssText = "font-size:10px;color:#666;font-style:italic;";
                note.textContent = "Laisse sur 0 pour désactiver la validation.";

                container.appendChild(label);
                container.appendChild(select);
                container.appendChild(note);

                const domWidget = node.addDOMWidget("ideogram_parse_ui", "custom", container, {
                    getValue: () => "",
                    setValue: (v) => {},
                });

                async function populate() {
                    select.innerHTML = '<option value="0">-- Pas de validation --</option>';
                    try {
                        const resp = await fetch(`${getApiUrl()}/prompts/templates`, { headers: apiHeaders() });
                        if (!resp.ok) return;
                        const items = await resp.json();
                        if (!Array.isArray(items)) return;
                        items.forEach(t => {
                            const o = document.createElement("option");
                            o.value = t.id;
                            o.textContent = t.name || `Template ${t.id}`;
                            select.appendChild(o);
                        });
                    } catch {}
                    // Restaurer la valeur du widget natif apres le chargement
                    const vw = node.widgets?.find(x => x.name === "validation_template_id");
                    if (vw) {
                        const vid = parseInt(vw.value) || 0;
                        if ([...select.options].some(o => o.value === String(vid))) {
                            select.value = String(vid);
                        }
                        _friaRestored = true;
                    }
                }

                function syncNative() {
                    if (!_friaRestored) return;
                    const w = node.widgets?.find(x => x.name === "validation_template_id");
                    if (!w) return;
                    w.value = parseInt(select.value) || 0;
                    if (w.callback) w.callback(w.value);
                }

                select.onchange = syncNative;

                populate();

                const MIN_WIDTH = 300;
                const origOnResize = node.onResize;
                node.onResize = function (size) {
                    if (origOnResize) origOnResize.call(this, size);
                    if (size[0] < MIN_WIDTH) size[0] = MIN_WIDTH;
                    container.style.width = (size[0] - 16) + "px";
                };
                requestAnimationFrame(() => {
                    if (node.size && node.size[0] < MIN_WIDTH) node.setSize([MIN_WIDTH, node.size[1]]);
                    if (node.size) container.style.width = (node.size[0] - 16) + "px";
                });

                return r;
            };
        },

        async loadedGraphNode(node) {
            if (node.type === "FRIAIdeogramParseNode") {
                setTimeout(() => {
                    const vw = node.widgets?.find(x => x.name === "validation_template_id");
                    const sel = node.widgets?.find(x => x.name === "ideogram_parse_ui");
                    if (vw && sel && sel.options?.element) {
                        const selectEl = sel.options.element.querySelector("select");
                        if (selectEl) {
                            const vid = parseInt(vw.value) || 0;
                            if ([...selectEl.options].some(o => o.value === String(vid))) {
                                selectEl.value = String(vid);
                            }
                        }
                    }
                }, 500);
            }
        },
    });
})();
