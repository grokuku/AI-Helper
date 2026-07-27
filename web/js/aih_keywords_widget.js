/**
 * AIH Keywords Picker — Custom widget for ComfyUI node.
 *
 * UI de filtrage de mots-clés :
 *   - Section / Subsection / NSFW / Confidence / Include / Exclude / Semantic
 *   - Auto-update via debounce (500ms) → GET /api/keywords
 *   - Sauvegarde/Chargement de filtres (GET/POST /api/filters)
 *   - Liste scrollable des mots-clés résultants
 *
 * Widget caché _keywords_config : sérialisé dans le workflow pour persistance.
 */

// ========================
// Helpers partagés
// ========================

function getApiUrl() {
    const base = (window.AIH.getServerUrl() || "https://kw.holaf.fr").replace(/\/+$/, "");
    return base + "/api";
}

function getApiKey() { return window.AIH.getApiKey(); }

function apiHeaders() {
    const h = { "Content-Type": "application/json" };
    const key = getApiKey();
    if (key) h["Authorization"] = `Bearer ${key}`;
    return h;
}

async function apiCall(method, path, body) {
    const opts = { method, headers: apiHeaders() };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(`${getApiUrl()}/${path.replace(/^\//, "")}`, opts);
    if (!resp.ok) {
        const txt = await resp.text().catch(() => "");
        throw new Error(`HTTP ${resp.status}: ${txt.substring(0, 200)}`);
    }
    return resp.json();
}

// ========================
// Cacher un widget ComfyUI
// ========================
function hideWidget(node, name) {
    const w = node.widgets?.find(x => x.name === name);
    if (w) {
        w.hidden = true;
        w.computeSize = () => [0, -4];
        return w;
    }
    return null;
}

// ========================
// Helpers UI
// ========================

/** Escaping HTML pour injection sécurisée dans innerHTML */
function esc(str) {
    if (typeof str !== "string") return "";
    var d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
}

function showToast(title, msg) {
    const overlay = document.createElement("div");
    Object.assign(overlay.style, {
        position: "fixed", bottom: "20px", right: "20px", zIndex: "99999",
        background: "#2a2a2e", borderRadius: "8px", padding: "12px 16px",
        border: "1px solid #555", maxWidth: "350px",
        boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
    });
    overlay.innerHTML = `
        <strong style="font-size:12px; color:#f87171;">${title}</strong>
        <p style="margin:4px 0 0; font-size:11px; color:#ccc;">${msg}</p>
    `;
    document.body.appendChild(overlay);
    setTimeout(() => overlay.remove(), 4000);
}

// ========================
// Debounce utilitaire
// ========================
function debounce(fn, delay) {
    let timer = null;
    return function () {
        const ctx = this, args = arguments;
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
            timer = null;
            fn.apply(ctx, args);
        }, delay);
    };
}

// ========================
// Enregistrement du widget
// ========================
AIH.waitForApp(function(app) {

    app.registerExtension({
        name: "AIH.Keywords",
        async beforeRegisterNodeDef(nodeType, nodeData) {
            if (nodeData.name !== "AIHKeywordsNode") return;

            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = onNodeCreated?.apply(this, arguments);
                const node = this;

                // ---- Masquer les widgets sérialisés _keywords_config et seed ----
                hideWidget(node, "_keywords_config");
                hideWidget(node, "seed");

                // ---- Supprimer la socket d'entrée de _keywords_config ----
                {
                    const slot = node.findInputSlot?.("_keywords_config");
                    if (slot !== undefined && slot !== -1) {
                        node.removeInput(slot);
                    }
                }

                // ---- État local ----
                if (!node._aihKeywords) node._aihKeywords = [];
                if (!node._aihTotal) node._aihTotal = 0;

                // Config courante (pour le payload _keywords_config et les appels API)
                const config = {
                    section: "",
                    subsection: "",
                    include: "",
                    exclude: "",
                    semantic: "",
                    nsfw: "",
                    min_confidence: 0.0,
                };

                // ---- Sync _keywords_config ----
                function syncKeywordsConfig() {
                    const w = node.widgets?.find(x => x.name === "_keywords_config");
                    if (!w) return;
                    w.value = JSON.stringify({
                        keywords_text: (node._aihKeywords || []).map(k => k.keyword || k).join(", "),
                        keywords: node._aihKeywords || [],
                        total: node._aihTotal || 0,
                        config: { ...config },
                    });
                }

                // ---- Appel API keywords avec debounce ----
                async function fetchKeywords() {
                    const params = new URLSearchParams();
                    if (config.section) params.set("section", config.section);
                    if (config.subsection) params.set("subsection", config.subsection);
                    if (config.include) params.set("q", config.include);
                    if (config.exclude) params.set("q_neg", config.exclude);
                    if (config.semantic) params.set("semantic", config.semantic);
                    if (config.nsfw) params.set("nsfw", config.nsfw);
                    if (config.min_confidence > 0) params.set("min_confidence", String(config.min_confidence));

                    const queryStr = params.toString();
                    const url = queryStr ? `keywords?${queryStr}` : "keywords";

                    try {
                        const data = await apiCall("GET", url);
                        const list = data.keywords || data.results || data.data || (Array.isArray(data) ? data : []);
                        node._aihKeywords = list.map(k => {
                            if (typeof k === "string") return { id: null, keyword: k, description: "" };
                            return k;
                        });
                        node._aihTotal = data.total !== undefined ? data.total : node._aihKeywords.length;
                        renderKeywords();
                        syncKeywordsConfig();
                    } catch (err) {
                        console.warn("[AIH.Keywords] fetch error:", err.message);
                        node._aihKeywords = [];
                        node._aihTotal = 0;
                        renderKeywords();
                        syncKeywordsConfig();
                    }
                }

                const debouncedFetch = debounce(fetchKeywords, 500);

                // ---- Met à jour la config et déclenche l'auto-update ----
                function updateConfigAndFetch(partial) {
                    Object.assign(config, partial);
                    debouncedFetch();
                }

                // ---- UI Container ----
                const container = document.createElement("div");
                Object.assign(container.style, {
                    width: "100%",
                    height: "100%",
                    minHeight: "320px",
                    background: "#2a2a2e",
                    borderRadius: "8px",
                    padding: "8px",
                    boxSizing: "border-box",
                    fontSize: "12px",
                    color: "#ccc",
                    display: "flex",
                    flexDirection: "column",
                    overflow: "hidden",
                });

                // ========================================
                // Row 1 : Section + Subsection
                // ========================================
                const row1 = document.createElement("div");
                Object.assign(row1.style, {
                    display: "flex", gap: "4px", marginBottom: "6px",
                    flex: "0 0 auto",
                });

                // Section dropdown
                const sectionSel = document.createElement("select");
                Object.assign(sectionSel.style, {
                    flex: "1", padding: "4px 6px", borderRadius: "4px",
                    border: "1px solid #555", background: "#1a1a1e",
                    color: "#fff", fontSize: "11px", cursor: "pointer",
                });
                sectionSel.innerHTML = '<option value="">Section...</option>';
                sectionSel.onchange = function () {
                    config.section = this.value;
                    config.subsection = ""; // Reset subsection quand section change
                    subsectionSel.innerHTML = '<option value="">Sous-section...</option>';
                    updateConfigAndFetch({ section: this.value, subsection: "" });
                    // Charger les sous-sections
                    if (this.value) loadSubsections(this.value);
                };

                // Subsection dropdown
                const subsectionSel = document.createElement("select");
                Object.assign(subsectionSel.style, {
                    flex: "1", padding: "4px 6px", borderRadius: "4px",
                    border: "1px solid #555", background: "#1a1a1e",
                    color: "#fff", fontSize: "11px", cursor: "pointer",
                });
                subsectionSel.innerHTML = '<option value="">Sous-section...</option>';
                subsectionSel.onchange = function () {
                    updateConfigAndFetch({ subsection: this.value });
                };

                row1.appendChild(sectionSel);
                row1.appendChild(subsectionSel);

                // ---- Chargement des sections ----
                async function loadSections() {
                    try {
                        const data = await apiCall("GET", "sections");
                        const sections = Array.isArray(data) ? data : (data.sections || data.data || []);
                        sectionSel.innerHTML = '<option value="">Section...</option>';
                        sections.forEach(function (s) {
                            const name = typeof s === "string" ? s : (s.section_title || s.title || s.name || String(s.section_id ?? s.id ?? ""));
                            const val = typeof s === "string" ? s : (s.section_id ?? s.id ?? s.name ?? s.section_title ?? s.title ?? "");
                            const opt = document.createElement("option");
                            opt.value = val;
                            opt.textContent = name;
                            sectionSel.appendChild(opt);
                        });
                    } catch (err) {
                        console.warn("[AIH.Keywords] loadSections error:", err.message);
                    }
                }

                async function loadSubsections(section) {
                    try {
                        const data = await apiCall("GET", `subsections?section=${encodeURIComponent(section)}`);
                        const subs = Array.isArray(data) ? data : (data.subsections || data.data || []);
                        subsectionSel.innerHTML = '<option value="">Sous-section...</option>';
                        subs.forEach(function (s) {
                            const name = typeof s === "string" ? s : (s.subsection_title || s.title || s.name || String(s.subsection_id ?? s.id ?? ""));
                            const val = typeof s === "string" ? s : (s.subsection_id ?? s.id ?? s.name ?? s.subsection_title ?? s.title ?? "");
                            const opt = document.createElement("option");
                            opt.value = val;
                            opt.textContent = name;
                            subsectionSel.appendChild(opt);
                        });
                    } catch (err) {
                        console.warn("[AIH.Keywords] loadSubsections error:", err.message);
                    }
                }

                loadSections();

                // ========================================
                // Row 2 : NSFW + Confidence slider
                // ========================================
                const row2 = document.createElement("div");
                Object.assign(row2.style, {
                    display: "flex", gap: "6px", marginBottom: "6px",
                    alignItems: "center", flex: "0 0 auto",
                });

                // NSFW dropdown
                const nsfwSel = document.createElement("select");
                Object.assign(nsfwSel.style, {
                    flex: "0 0 auto", width: "80px", padding: "4px 6px", borderRadius: "4px",
                    border: "1px solid #555", background: "#1a1a1e",
                    color: "#fff", fontSize: "11px", cursor: "pointer",
                });
                nsfwSel.innerHTML = '<option value="">Tout</option><option value="0">SFW</option><option value="1">NSFW</option>';
                nsfwSel.onchange = function () {
                    updateConfigAndFetch({ nsfw: this.value });
                };

                // Confidence label
                const confLabel = document.createElement("span");
                confLabel.textContent = "Confiance:";
                confLabel.style.cssText = "font-size:11px;color:#aaa;white-space:nowrap;";

                // Confidence slider
                const confSlider = document.createElement("input");
                confSlider.type = "range";
                confSlider.min = "0";
                confSlider.max = "100";
                confSlider.value = "0";
                confSlider.step = "1";
                Object.assign(confSlider.style, {
                    flex: "1", minWidth: "60px", height: "16px",
                    cursor: "pointer", accentColor: "#6366f1",
                });

                // Confidence value display
                const confVal = document.createElement("span");
                confVal.textContent = "0%";
                confVal.style.cssText = "font-size:11px;color:#ccc;min-width:32px;text-align:right;";

                confSlider.oninput = function () {
                    const pct = parseInt(this.value) || 0;
                    confVal.textContent = pct + "%";
                };
                confSlider.onchange = function () {
                    const pct = parseInt(this.value) || 0;
                    updateConfigAndFetch({ min_confidence: pct / 100 });
                };

                row2.appendChild(nsfwSel);
                row2.appendChild(confLabel);
                row2.appendChild(confSlider);
                row2.appendChild(confVal);

                // ========================================
                // Row 3 : Include input
                // ========================================
                const row3 = document.createElement("div");
                Object.assign(row3.style, {
                    display: "flex", gap: "4px", marginBottom: "6px",
                    alignItems: "center", flex: "0 0 auto",
                });

                const includeLabel = document.createElement("span");
                includeLabel.textContent = "Include:";
                includeLabel.style.cssText = "font-size:11px;color:#aaa;white-space:nowrap;flex-shrink:0;";

                const includeInput = document.createElement("input");
                includeInput.type = "text";
                includeInput.placeholder = "Mots-clés à inclure...";
                Object.assign(includeInput.style, {
                    flex: "1", padding: "4px 6px", borderRadius: "4px",
                    border: "1px solid #555", background: "#1a1a1e",
                    color: "#fff", fontSize: "11px", minWidth: "0",
                });
                includeInput.oninput = function () {
                    updateConfigAndFetch({ include: this.value });
                };

                row3.appendChild(includeLabel);
                row3.appendChild(includeInput);

                // ========================================
                // Row 4 : Exclude input
                // ========================================
                const row4 = document.createElement("div");
                Object.assign(row4.style, {
                    display: "flex", gap: "4px", marginBottom: "6px",
                    alignItems: "center", flex: "0 0 auto",
                });

                const excludeLabel = document.createElement("span");
                excludeLabel.textContent = "Exclude:";
                excludeLabel.style.cssText = "font-size:11px;color:#aaa;white-space:nowrap;flex-shrink:0;";

                const excludeInput = document.createElement("input");
                excludeInput.type = "text";
                excludeInput.placeholder = "Mots-clés à exclure...";
                Object.assign(excludeInput.style, {
                    flex: "1", padding: "4px 6px", borderRadius: "4px",
                    border: "1px solid #555", background: "#1a1a1e",
                    color: "#fff", fontSize: "11px", minWidth: "0",
                });
                excludeInput.oninput = function () {
                    updateConfigAndFetch({ exclude: this.value });
                };

                row4.appendChild(excludeLabel);
                row4.appendChild(excludeInput);

                // ========================================
                // Row 5 : Semantic input
                // ========================================
                const row5 = document.createElement("div");
                Object.assign(row5.style, {
                    display: "flex", gap: "4px", marginBottom: "6px",
                    alignItems: "center", flex: "0 0 auto",
                });

                const semanticLabel = document.createElement("span");
                semanticLabel.textContent = "Semantic:";
                semanticLabel.style.cssText = "font-size:11px;color:#aaa;white-space:nowrap;flex-shrink:0;";

                const semanticInput = document.createElement("input");
                semanticInput.type = "text";
                semanticInput.placeholder = "Recherche sémantique...";
                Object.assign(semanticInput.style, {
                    flex: "1", padding: "4px 6px", borderRadius: "4px",
                    border: "1px solid #555", background: "#1a1a1e",
                    color: "#fff", fontSize: "11px", minWidth: "0",
                });
                semanticInput.oninput = function () {
                    updateConfigAndFetch({ semantic: this.value });
                };

                row5.appendChild(semanticLabel);
                row5.appendChild(semanticInput);

                // ========================================
                // Row 6 : Load / Save buttons
                // ========================================
                const row6 = document.createElement("div");
                Object.assign(row6.style, {
                    display: "flex", gap: "4px", marginBottom: "6px",
                    flex: "0 0 auto",
                });

                const mkBtn = (text, primary) => {
                    const b = document.createElement("button");
                    b.textContent = text;
                    Object.assign(b.style, {
                        flex: "1", padding: "4px 8px", borderRadius: "4px",
                        border: primary ? "none" : "1px solid #555",
                        fontSize: "11px", cursor: "pointer",
                        background: primary ? "#6366f1" : "#3a3a3e",
                        color: primary ? "white" : "#ccc",
                        fontWeight: primary ? "600" : "normal",
                    });
                    b.onmouseenter = () => {
                        if (primary) b.style.background = "#5558e8";
                        else b.style.background = "#4a4a4e";
                    };
                    b.onmouseleave = () => {
                        if (primary) b.style.background = "#6366f1";
                        else b.style.background = "#3a3a3e";
                    };
                    return b;
                };

                const loadBtn = mkBtn("📂 Load");
                const resetBtn = mkBtn("🔁 Reset");
                const saveBtn = mkBtn("💾 Save", true);

                row6.appendChild(loadBtn);
                row6.appendChild(resetBtn);
                row6.appendChild(saveBtn);

                // ---- Load button : liste des filtres ----
                loadBtn.onclick = async () => {
                    try {
                        const filters = await apiCall("GET", "filters");
                        showFilterPicker(filters, (filter) => {
                            loadFilter(filter.id);
                        });
                    } catch (err) {
                        showToast("Erreur", "Impossible de charger les filtres : " + err.message);
                    }
                };

                async function loadFilter(filterId) {
                    try {
                        const data = await apiCall("GET", `filters/${filterId}/preview`);
                        // data contient la config du filtre
                        const cfg = data.config || data.filter?.config || data;
                        if (cfg) {
                            if (cfg.section !== undefined) {
                                config.section = cfg.section || "";
                                sectionSel.value = config.section;
                                if (config.section) loadSubsections(config.section);
                            }
                            if (cfg.subsection !== undefined) {
                                config.subsection = cfg.subsection || "";
                                subsectionSel.value = config.subsection;
                            }
                            if (cfg.include !== undefined) {
                                config.include = cfg.include || "";
                                includeInput.value = config.include;
                            }
                            if (cfg.exclude !== undefined) {
                                config.exclude = cfg.exclude || "";
                                excludeInput.value = config.exclude;
                            }
                            if (cfg.semantic !== undefined) {
                                config.semantic = cfg.semantic || "";
                                semanticInput.value = config.semantic;
                            }
                            if (cfg.nsfw !== undefined) {
                                config.nsfw = String(cfg.nsfw) || "";
                                nsfwSel.value = config.nsfw;
                            }
                            if (cfg.min_confidence !== undefined) {
                                const pct = Math.round((cfg.min_confidence || 0) * 100);
                                config.min_confidence = pct / 100;
                                confSlider.value = String(pct);
                                confVal.textContent = pct + "%";
                            }
                            // Forcer un fetch immédiat
                            debouncedFetch();
                        }
                    } catch (err) {
                        showToast("Erreur", "Impossible de charger le filtre : " + err.message);
                    }
                }

                // ---- Save button ----
                saveBtn.onclick = () => {
                    aihShowPrompt("Sauvegarder le filtre", "Nom du filtre :", "").then(function (name) {
                        if (!name) return;
                        const payload = {
                            name: name,
                            config: { ...config },
                        };
                        apiCall("POST", "filters", payload).then(() => {
                            showToast("Succès", "Filtre \"" + name + "\" sauvegardé !");
                        }).catch(err => {
                            showToast("Erreur", "Impossible de sauvegarder : " + err.message);
                        });
                    });
                };

                // ---- Reset button ----
                resetBtn.onclick = function () {
                    // Reset Section
                    config.section = "";
                    sectionSel.value = "";

                    // Reset Subsection
                    config.subsection = "";
                    subsectionSel.innerHTML = '<option value="">Sous-section...</option>';

                    // Reset NSFW
                    config.nsfw = "";
                    nsfwSel.value = "";

                    // Reset Confidence
                    const pct = 0;
                    config.min_confidence = 0.0;
                    confSlider.value = "0";
                    confVal.textContent = "0%";

                    // Reset Include
                    config.include = "";
                    includeInput.value = "";

                    // Reset Exclude
                    config.exclude = "";
                    excludeInput.value = "";

                    // Reset Semantic
                    config.semantic = "";
                    semanticInput.value = "";

                    // Vider la liste des mots-clés
                    node._aihKeywords = [];
                    node._aihTotal = 0;

                    // Relancer le fetch (retournera une liste vide ou par defaults)
                    fetchKeywords();
                };

                // ========================================
                // Keywords list (scrollable, flex grow)
                // ========================================
                const keywordsList = document.createElement("div");
                Object.assign(keywordsList.style, {
                    flex: "1 1 0",
                    minHeight: "40px",
                    overflowY: "auto",
                    border: "1px dashed #555",
                    borderRadius: "4px",
                    padding: "4px",
                    fontSize: "11px",
                    color: "#666",
                    marginBottom: "4px",
                });

                function renderKeywords() {
                    const items = node._aihKeywords || [];
                    if (items.length === 0) {
                        keywordsList.innerHTML = "Aucun mot-clé. Modifiez les filtres ci-dessus.";
                        keywordsList.style.color = "#666";
                        return;
                    }
                    keywordsList.style.color = "#ccc";
                    keywordsList.innerHTML = "";

                    // En-tête : nombre de résultats
                    const header = document.createElement("div");
                    Object.assign(header.style, {
                        padding: "2px 4px", marginBottom: "4px",
                        fontSize: "10px", color: "#888",
                        borderBottom: "1px solid #444",
                    });
                    header.textContent = node._aihTotal + " mot(s)-clé(s)";
                    keywordsList.appendChild(header);

                    items.forEach(function (kw) {
                        const row = document.createElement("div");
                        Object.assign(row.style, {
                            display: "flex", alignItems: "center", gap: "4px",
                            padding: "3px 4px", borderRadius: "3px", marginBottom: "2px",
                            background: "#2d3748",
                            border: "1px solid #555",
                        });

                        // Icône
                        const icon = document.createElement("span");
                        icon.textContent = "🔑";
                        icon.style.cssText = "flex-shrink:0;font-size:10px;";

                        // Mot-clé
                        const kwText = document.createElement("span");
                        kwText.textContent = kw.keyword || (typeof kw === "string" ? kw : "");
                        kwText.style.cssText = "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500;";

                        // Description si présente
                        const desc = document.createElement("span");
                        if (kw.description) {
                            desc.textContent = kw.description;
                            desc.style.cssText = "font-size:10px;color:#999;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:120px;flex-shrink:1;";
                        } else {
                            desc.style.display = "none";
                        }

                        row.appendChild(icon);
                        row.appendChild(kwText);
                        if (kw.description) row.appendChild(desc);
                        keywordsList.appendChild(row);
                    });
                }

                // Initial render
                renderKeywords();

                // ========================================
                // Assemble le container
                // ========================================
                container.appendChild(row1);
                container.appendChild(row2);
                container.appendChild(row3);
                container.appendChild(row4);
                container.appendChild(row5);
                container.appendChild(row6);
                container.appendChild(keywordsList);

                // ---- Intégration DOM widget ----
                const domWidget = node.addDOMWidget("keywords_ui", "custom", container, {
                    getValue: () => "",
                    setValue: (v) => {},
                });
                domWidget.options = domWidget.options || {};
                domWidget.options.height = 340;

                // ---- Taille minimum ----
                const MIN_WIDTH = 340;
                const origOnResize = node.onResize;
                node.onResize = function (size) {
                    if (origOnResize) origOnResize.call(this, size);
                    if (size[0] < MIN_WIDTH) size[0] = MIN_WIDTH;
                };
                requestAnimationFrame(() => {
                    if (node.size && node.size[0] < MIN_WIDTH) {
                        node.setSize([MIN_WIDTH, node.size[1]]);
                    }
                });

                // ---- Persistance workflow (restauration) ----
                function restoreFromWidgets(n) {
                    const kwc = n.widgets?.find(w => w.name === "_keywords_config");
                    if (!kwc || !kwc.value || kwc.value === "{}" || kwc.value === "") return false;
                    try {
                        const data = JSON.parse(kwc.value);
                        if (data.config) {
                            const cfg = data.config;
                            if (cfg.section !== undefined) {
                                config.section = cfg.section || "";
                                sectionSel.value = config.section;
                                if (config.section) loadSubsections(config.section);
                            }
                            if (cfg.subsection !== undefined) {
                                config.subsection = cfg.subsection || "";
                                subsectionSel.value = config.subsection;
                            }
                            if (cfg.include !== undefined) {
                                config.include = cfg.include || "";
                                includeInput.value = config.include;
                            }
                            if (cfg.exclude !== undefined) {
                                config.exclude = cfg.exclude || "";
                                excludeInput.value = config.exclude;
                            }
                            if (cfg.semantic !== undefined) {
                                config.semantic = cfg.semantic || "";
                                semanticInput.value = config.semantic;
                            }
                            if (cfg.nsfw !== undefined) {
                                config.nsfw = String(cfg.nsfw) || "";
                                nsfwSel.value = config.nsfw;
                            }
                            if (cfg.min_confidence !== undefined) {
                                const pct = Math.round((cfg.min_confidence || 0) * 100);
                                config.min_confidence = pct / 100;
                                confSlider.value = String(pct);
                                confVal.textContent = pct + "%";
                            }
                        }
                        if (data.keywords && Array.isArray(data.keywords)) {
                            node._aihKeywords = data.keywords;
                            node._aihTotal = data.total || data.keywords.length;
                            renderKeywords();
                        }
                        // Forcer un fetch si les champs sont remplis
                        if (data.config) {
                            debouncedFetch();
                        }
                        return true;
                    } catch (err) {
                        console.warn("[AIH.Keywords] restore error:", err);
                        return false;
                    }
                }

                node._aihKeywordsRestore = restoreFromWidgets.bind(null, node);

                // Fallback : tentative périodique de restauration
                let restoreAttempts = 0;
                function delayedRestore() {
                    if (restoreFromWidgets(node)) return;
                    restoreAttempts++;
                    if (restoreAttempts < 20) {
                        setTimeout(delayedRestore, 300);
                    }
                }
                setTimeout(delayedRestore, 100);

                // ---- Stockage refs ----
                node._keywordsList = keywordsList;
                node._domWidget = domWidget;

                // Sync initial
                syncKeywordsConfig();

                return r;
            };
        },

        // Hook appelé APRÈS que ComfyUI a restauré les widgets
        async loadedGraphNode(node) {
            if (node._aihKeywordsRestore) {
                setTimeout(() => node._aihKeywordsRestore(), 0);
            }
        },
    });
});

// ========================
// Filter picker modal
// ========================

function showFilterPicker(filters, onSelect) {
    var html = '<div style="max-height:50vh;overflow-y:auto;">';

    if (filters.length > 0) {
        filters.forEach(function(f) {
            html += '<div class="aih-filter-item" data-id="' + f.id + '" style="padding:6px 8px;cursor:pointer;border-radius:4px;font-size:12px;color:#ccc;background:#3a3a3e;margin-bottom:2px;">' +
                esc(f.name || f.filter_name || "Filtre #" + f.id) + (f.nsfw ? ' 🔞' : '') +
                ' <span style="color:#888;font-size:10px;">' + (f.owner_name || f.user_id?.substring(0,6) || "") + '</span></div>';
        });
    } else {
        html += '<p style="font-size:12px;color:#666;">Aucun filtre disponible.</p>';
    }
    html += '</div>';

    var m = aihOpenModalV2({
        title: "Charger un filtre",
        content: html,
        width: "380px",
        height: "auto",
        minHeight: "150px",
        maxHeight: "70vh",
        resizable: false,
        storageKey: null,
    });

    m.modal.querySelectorAll(".aih-filter-item").forEach(function(el) {
        el.onclick = function() {
            var id = parseInt(el.dataset.id);
            var f = filters.find(function(x) { return x.id === id; });
            if (f && onSelect) onSelect(f);
            m.close();
        };
        el.onmouseenter = function() { el.style.background = '#4a4a4e'; };
        el.onmouseleave = function() { el.style.background = '#3a3a3e'; };
    });
}
