/**
 * FR.IA Elements Picker — Custom widget for ComfyUI node.
 *
 * Flux de données :
 *   - "Test generation" : JS appelle l'API → aperçu instantané dans le textarea
 *   - "Run" (workflow) : Python lit _elements_json + _api_config, appelle l'API
 *     lui-même avec le seed → résultat déterministe affiché via onExecuted
 *
 * Les widgets _elements_json et _api_config sont masqués dans l'UI ComfyUI
 * mais sérialisés dans le workflow pour que Python y ait accès.
 */

const STORAGE_KEY = "FRIA_config";

function getConfig() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
    catch { return {}; }
}

function getApiUrl() {
    try {
        const cfg = JSON.parse(localStorage.getItem("FRIA_config") || "{}");
        const base = (cfg.serverUrl || "https://kw.holaf.fr").replace(/\/+$/, "");
        return base + "/api";
    } catch {
        return "https://kw.holaf.fr/api";
    }
}

function getApiKey() {
    return getConfig().apiKey || "";
}

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

// Cacher un widget ComfyUI : reste dans node.widgets (sérialisé) mais invisible dans l'UI.
// IMPORTANT : ne PAS changer widget.type en "hidden" car ComfyUI le
// désérialiserait à vide. On utilise uniquement widget.hidden = true
// et on réduit sa hauteur à 0.
function hideWidget(node, name) {
    const w = node.widgets?.find(x => x.name === name);
    if (w) {
        w.hidden = true;
        w.computeSize = () => [0, -4]; // hauteur négative = ligne compressée
        return w;
    }
    return null;
}

// Attendre que l'app ComfyUI soit disponible
(function waitForApp() {
    const app = window.app || window.comfyAPI?.app?.app;
    if (!app) { setTimeout(waitForApp, 100); return; }

    app.registerExtension({
        name: "FR.IA.Elements",
        async beforeRegisterNodeDef(nodeType, nodeData) {
            if (nodeData.name !== "FRIAElementsNode") return;

            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = onNodeCreated?.apply(this, arguments);
                const node = this;

                // ---- Masquer le widget sérialisé _elements_json ----
                // (plus de _api_config : api_key/url sont lus cote Python depuis
                // le fichier de credentials)
                hideWidget(node, "_elements_json");

                // ---- Supprimer la socket d'entrée de _elements_json ----
                {
                    const slot = node.findInputSlot?.("_elements_json");
                    if (slot !== undefined && slot !== -1) {
                        node.removeInput(slot);
                    }
                }

                // Le widget seed est géré nativement par ComfyUI.

                // Stockage local des éléments
                if (!node._friaElements) node._friaElements = [];

                // ---- Sync les widgets sérialisés ----
                function syncElementsWidget() {
                    const w = node.widgets?.find(x => x.name === "_elements_json");
                    if (!w) return;
                    w.value = JSON.stringify({
                        elements: node._friaElements.map(e => {
                            const base = { visible: e.visible !== false };
                            if (e.type === "filter") {
                                return { ...base, type: "filter", id: e.id, name: e.name || "", author: e.author || "", is_public: !!e.is_public };
                            }
                            if (e.type === "text") {
                                // "raw" est le format attendu par le backend /api/generate
                                return { ...base, type: "raw", text: e.text };
                            }
                            return { ...base, ...e };
                        }),
                        random_count: randCb.checked ? (parseInt(randN.value) || 3) : 0,
                        random_sfw: sfwCb.checked,
                        random_nsfw: nsfwCb.checked,
                    });
                }

                function syncApiConfigWidget() {
                    // No-op: _api_config supprime, api_key/url lus depuis le
                    // fichier de credentials cote Python.
                }

                // Sync initial
                syncApiConfigWidget();

                // ========================================
                // LAYOUT : flex column, la liste s'étend,
                // le résultat est fixé en bas
                // ========================================

                const container = document.createElement("div");
                Object.assign(container.style, {
                    width: "100%",
                    height: "100%",         // Remplit l'espace alloué par ComfyUI
                    minHeight: "280px",
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

                // ---- Toolbar (hauteur fixe) ----
                const tb = document.createElement("div");
                Object.assign(tb.style, {
                    display: "flex", gap: "4px", marginBottom: "8px",
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

                const addFilterBtn = mkBtn("+ Add saved filter");
                const addTextBtn = mkBtn("+ Add custom text");

                tb.appendChild(addFilterBtn);
                tb.appendChild(addTextBtn);

                // ---- Liste des éléments (flex: grow, absorbe l'espace) ----
                const listEl = document.createElement("div");
                Object.assign(listEl.style, {
                    flex: "1 1 0",           // Prend tout l'espace dispo
                    minHeight: "40px",       // Hauteur mini pour être utilisable
                    overflowY: "auto",
                    marginBottom: "8px",
                    border: "1px dashed #555",
                    borderRadius: "4px",
                    padding: "4px",
                    fontSize: "11px",
                    color: "#666",
                });

                function renderList() {
                    const items = node._friaElements || [];
                    if (items.length === 0) {
                        listEl.innerHTML = "Aucun élément. Ajoutez des filtres ou du texte custom.";
                        listEl.style.color = "#666";
                        return;
                    }
                    listEl.style.color = "#ccc";
                    listEl.innerHTML = "";
                    items.forEach((item, idx) => {
                        const row = document.createElement("div");
                        Object.assign(row.style, {
                            display: "flex", alignItems: "center", gap: "4px",
                            padding: "3px 4px", borderRadius: "3px", marginBottom: "2px",
                            background: item.type === "filter" ? "#2d3748" : "#1a365d",
                            border: "1px solid #555",
                        });

                        row.className = "fria-element-row";
                        const isHidden = item.visible === false;
                        if (isHidden) {
                            row.style.opacity = "0.45";
                            row.style.background = "#252836";
                            row.style.border = "1px solid #444";
                        }

                        // Poignée de drag (⠿)
                        const grip = document.createElement("span");
                        grip.textContent = "⠿";
                        Object.assign(grip.style, {
                            cursor: "grab", color: "#666", fontSize: "10px", flexShrink: "0",
                            userSelect: "none", marginRight: "2px", touchAction: "none",
                        });
                        grip.title = "Glisser-déposer pour réorganiser";
                        grip.onpointerdown = (e) => startDrag(e, idx, row);

                        // Icône œil (visible / masqué)
                        // Meme pattern que la croix de suppression (del.onclick) :
                        // un simple <button> avec onclick direct. Les tentatives
                        // avec mousedown/pointerdown empechaient le click de se
                        // synthetiser correctement dans le DOM widget ComfyUI.
                        const eyeBtn = document.createElement("button");
                        eyeBtn.type = "button";
                        eyeBtn.textContent = isHidden ? "🙈" : "👁";
                        Object.assign(eyeBtn.style, {
                            background: "none", border: "none", color: "#ccc",
                            cursor: "pointer", fontSize: "12px", padding: "0 2px", flexShrink: "0",
                        });
                        eyeBtn.title = isHidden ? "Activer cette entrée" : "Masquer cette entrée";
                        eyeBtn.onclick = () => {
                            // Toggle : visible (true ou undefined) → false, false → true
                            if (item.visible === false) {
                                item.visible = true;
                            } else {
                                item.visible = false;
                            }
                            renderList();
                            syncElementsWidget();
                        };

                        row.appendChild(grip);
                        row.appendChild(eyeBtn);
                        const iconSpan = document.createElement("span");
                        iconSpan.style.cssText = "flex-shrink:0;";
                        iconSpan.textContent = item.type === "filter" ? "🔽" : "📝";
                        row.appendChild(iconSpan);

                        // Contenu : input texte pour "text", label pour "filter"
                        if (item.type === "text") {
                            const textInput = document.createElement("input");
                            textInput.type = "text";
                            textInput.value = item.text || "";
                            textInput.placeholder = "Texte... ou A::B::C pour alternatives au hasard";
                            Object.assign(textInput.style, {
                                flex: "1", minWidth: "0",
                                padding: "2px 6px", borderRadius: "3px",
                                border: "1px solid #555", background: "#1a1a1e",
                                color: "#fff", fontSize: "11px",
                            });
                            textInput.oninput = () => {
                                item.text = textInput.value;
                                syncElementsWidget();
                            };
                            row.appendChild(textInput);
                        } else {
                            // Filtre : nom + meta
                            const label = document.createElement("span");
                            label.style.cssText = "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
                            label.textContent = item.name || `Filtre #${item.id}`;
                            row.appendChild(label);

                            if (item.author) {
                                const meta = document.createElement("span");
                                meta.style.cssText = "font-size:10px;color:#999;white-space:nowrap;flex-shrink:0;";
                                meta.textContent = `${item.author} ${item.is_public ? "🌐" : "🔒"}`;
                                row.appendChild(meta);
                            } else if (item.is_public !== undefined) {
                                const vis = document.createElement("span");
                                vis.style.cssText = "flex-shrink:0;";
                                vis.textContent = item.is_public ? "🌐" : "🔒";
                                row.appendChild(vis);
                            }
                        }

                        // Bouton supprimer
                        const del = document.createElement("button");
                        del.textContent = "✕";
                        Object.assign(del.style, {
                            background: "none", border: "none", color: "#f87171",
                            cursor: "pointer", fontSize: "11px", padding: "0 2px", flexShrink: "0",
                        });
                        del.onclick = () => {
                            items.splice(idx, 1);
                            renderList();
                            syncElementsWidget();
                        };
                        row.appendChild(del);
                        listEl.appendChild(row);
                    });
                }

                // ---- Drag & drop reorder (pointer events) ----
                // Le handle ⠿ de chaque ligne declenche un drag fluide :
                // - clone fantome qui suit le curseur, a taille identique aux autres lignes
                // - placeholder reduit (fine barre) indiquant l'emplacement de drop
                // - les elements glissent avec une petite animation FLIP quand le
                //   placeholder change de position
                // - reordonnancement deterministe de node._friaElements
                let dragState = null;

                function startDrag(e, startIdx, row) {
                    e.preventDefault();
                    e.stopPropagation();

                    const items = node._friaElements || [];
                    if (items.length < 2) return;

                    const rect = row.getBoundingClientRect();
                    const ghost = row.cloneNode(true);
                    Object.assign(ghost.style, {
                        position: "fixed", left: `${rect.left}px`, top: `${rect.top}px`,
                        width: `${rect.width}px`, opacity: "0.92", pointerEvents: "none",
                        zIndex: "10000",
                        // Ombre tres legere, pas de scale : le fantome a exactement
                        // la meme taille que les autres elements.
                        boxShadow: "0 2px 8px rgba(0,0,0,0.25)",
                    });
                    document.body.appendChild(ghost);

                    // Extraire la ligne reelle du DOM.

                    // Placeholder en cadre pointille bleu, hauteur reduite (moitie
                    // d'une ligne), pour bien marquer l'emplacement de drop.
                    const rowHeight = row.getBoundingClientRect().height;
                    const placeholder = document.createElement("div");
                    placeholder.className = "fria-drag-placeholder";
                    Object.assign(placeholder.style, {
                        height: `${rowHeight / 2}px`,
                        border: "2px dashed #60a5fa", borderRadius: "4px",
                        background: "rgba(96,165,250,0.10)",
                        marginBottom: "2px", pointerEvents: "none",
                        transition: "transform 0.12s ease-out",
                    });

                    listEl.insertBefore(placeholder, row.nextSibling);
                    row.remove();

                    // Activer les transitions sur toutes les lignes restantes.
                    listEl.querySelectorAll(".fria-element-row").forEach(r => {
                        r.style.transition = "transform 0.15s ease-out";
                    });

                    const gripInGhost = ghost.querySelector("span");
                    if (gripInGhost) gripInGhost.style.color = "#fff";

                    dragState = {
                        startIdx,
                        currentIdx: startIdx,
                        ghost,
                        row,
                        draggedEls: { row },
                        offsetY: e.clientY - rect.top,
                    };

                    document.addEventListener("pointermove", onPointerMove);
                    document.addEventListener("pointerup", onPointerUp);
                    document.addEventListener("pointercancel", onPointerUp);
                    try {
                        grip.setPointerCapture(e.pointerId);
                    } catch (_) {}
                }

                // Drapeau anti-clignotement : pendant qu'une animation FLIP est
                // en cours, les getBoundingClientRect() des lignes renvoient des
                // positions intermediaires, ce qui peut faire osciller le calcul
                // de targetIdx. On bloque la detection jusqu'a la fin de la
                // transition.
                let isAnimating = false;

                function onPointerMove(e) {
                    if (!dragState) return;
                    e.preventDefault();
                    const { ghost, offsetY } = dragState;
                    ghost.style.top = `${e.clientY - offsetY}px`;

                    // Pendant l'animation, ne pas recalculer la position cible.
                    if (isAnimating) return;

                    const rows = Array.from(listEl.querySelectorAll(".fria-element-row"));
                    let targetIdx = rows.length;
                    for (let i = 0; i < rows.length; i++) {
                        const rc = rows[i].getBoundingClientRect();
                        const mid = rc.top + rc.height / 2;
                        if (e.clientY < mid) {
                            targetIdx = i;
                            break;
                        }
                    }

                    if (targetIdx !== dragState.currentIdx) {
                        const placeholder = listEl.querySelector(".fria-drag-placeholder");
                        if (placeholder) {
                            flipMovePlaceholder(targetIdx, placeholder);
                        }
                        dragState.currentIdx = targetIdx;
                    }
                }

                // Deplace le placeholder dans le DOM en animant les autres lignes
                // via la technique FLIP (First/Last/Invert/Play).
                function flipMovePlaceholder(targetIdx, placeholder) {
                    isAnimating = true;
                    const rows = Array.from(listEl.querySelectorAll(".fria-element-row"));

                    // 1. First : positions avant le deplacement.
                    const before = rows.map(r => r.getBoundingClientRect().top);

                    // 2. Last : deplacer le placeholder dans le DOM.
                    if (targetIdx >= rows.length) {
                        listEl.appendChild(placeholder);
                    } else {
                        listEl.insertBefore(placeholder, rows[targetIdx]);
                    }

                    // 3. Invert : calculer le delta pour chaque ligne et appliquer
                    //    un transform qui l'amene a sa position d'origine.
                    rows.forEach((r, i) => {
                        const after = r.getBoundingClientRect().top;
                        const delta = before[i] - after;
                        if (delta !== 0) {
                            r.style.transition = "none";
                            r.style.transform = `translateY(${delta}px)`;
                        }
                    });

                    // 4. Play : forcer un reflow puis retirer le transform pour
                    //    declencher la transition.
                    listEl.offsetHeight; // force reflow
                    rows.forEach((r) => {
                        r.style.transition = "transform 0.15s ease-out";
                        r.style.transform = "";
                    });

                    // Liberer le drapeau quand la transition est terminee.
                    // On ecoute la derniere ligne (forcement animee) ; fallback
                    // timeout au cas ou transitionend ne fire pas.
                    let released = false;
                    const release = () => {
                        if (released) return;
                        released = true;
                        isAnimating = false;
                    };
                    const lastRow = rows[rows.length - 1];
                    if (lastRow) {
                        lastRow.addEventListener("transitionend", release, { once: true });
                    }
                    setTimeout(release, 180);
                }

                function onPointerUp(e) {
                    if (!dragState) return;
                    document.removeEventListener("pointermove", onPointerMove);
                    document.removeEventListener("pointerup", onPointerUp);
                    document.removeEventListener("pointercancel", onPointerUp);

                    const { startIdx, currentIdx, ghost, row, draggedEls } = dragState;
                    ghost.remove();
                    const placeholder = listEl.querySelector(".fria-drag-placeholder");

                    const items = node._friaElements || [];
                    if (currentIdx !== startIdx && currentIdx >= 0 && currentIdx <= items.length) {
                        let insertIdx = currentIdx;
                        if (currentIdx > startIdx) insertIdx--;
                        const [moved] = items.splice(startIdx, 1);
                        items.splice(insertIdx, 0, moved);
                    }

                    // Nettoyer les transitions forcees sur les lignes restantes.
                    listEl.querySelectorAll(".fria-element-row").forEach(r => {
                        r.style.transition = "";
                        r.style.transform = "";
                    });

                    // Replacer la ligne reelle a la position du placeholder
                    if (placeholder) {
                        listEl.insertBefore(draggedEls.row, placeholder);
                        placeholder.remove();
                    } else {
                        listEl.appendChild(draggedEls.row);
                    }
                    dragState = null;
                    syncElementsWidget();
                }


                // ---- Add saved filter ----
                addFilterBtn.onclick = async () => {
                    try {
                        const [filters, me] = await Promise.all([
                            apiCall("GET", "filters"),
                            apiCall("GET", "auth/me").catch(() => null),
                        ]);
                        const currentUserId = me?.id || null;
                        showFilterPicker(filters, currentUserId, (filter) => {
                            node._friaElements.push({
                                type: "filter",
                                id: filter.id,
                                name: filter.name,
                                author: filter.user_id === currentUserId ? "vous" : (filter.owner_name || filter.user_id?.substring(0,6) || "?"),
                                is_public: !!filter.is_public,
                            });
                            renderList();
                            syncElementsWidget();
                        });
                    } catch (err) {
                        showToast("Erreur", "Impossible de charger les filtres : " + err.message);
                    }
                };

                // ---- Add custom text ----
                addTextBtn.onclick = () => {
                    node._friaElements.push({ type: "text", text: "" });
                    renderList();
                    syncElementsWidget();
                    // Focus le dernier input texte
                    setTimeout(() => {
                        const inputs = listEl.querySelectorAll("input[type='text']");
                        const last = inputs[inputs.length - 1];
                        if (last) last.focus();
                    }, 0);
                };

                // ---- Random + SFW/NSFW row (hauteur fixe) ----
                const randRow = document.createElement("div");
                Object.assign(randRow.style, {
                    display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center", marginBottom: "4px",
                    flex: "0 0 auto",
                });

                const randCb = document.createElement("input");
                randCb.type = "checkbox";
                randCb.checked = false;
                randCb.id = "fria-rand-" + node.id;

                const randN = document.createElement("input");
                randN.type = "number";
                randN.value = "3";
                Object.assign(randN.style, {
                    width: "40px", padding: "2px 4px", borderRadius: "4px",
                    border: "1px solid #555", background: "1a1a1e",
                    color: "#fff", fontSize: "11px", textAlign: "center",
                });
                randN.min = 1;
                randN.max = 20;

                const randLabel = document.createElement("label");
                randLabel.style.fontSize = "11px";
                randLabel.htmlFor = randCb.id;
                randLabel.textContent = "Add random";

                // Séparateur visuel
                const randSep = document.createElement("span");
                randSep.textContent = "|";
                Object.assign(randSep.style, { color: "#555", fontSize: "11px" });

                // ---- SFW / NSFW checkboxes ----
                const sfwCb = document.createElement("input");
                sfwCb.type = "checkbox";
                sfwCb.checked = true;
                sfwCb.id = "fria-sfw-" + node.id;
                const sfwLabel = document.createElement("label");
                sfwLabel.style.fontSize = "11px";
                sfwLabel.htmlFor = sfwCb.id;
                sfwLabel.textContent = "SFW";
                sfwLabel.style.color = "#4ade80";

                const nsfwCb = document.createElement("input");
                nsfwCb.type = "checkbox";
                nsfwCb.checked = false;
                nsfwCb.id = "fria-nsfw-" + node.id;
                const nsfwLabel = document.createElement("label");
                nsfwLabel.style.fontSize = "11px";
                nsfwLabel.htmlFor = nsfwCb.id;
                nsfwLabel.textContent = "NSFW";
                nsfwLabel.style.color = "#f87171";

                randRow.appendChild(randCb);
                randRow.appendChild(randLabel);
                randRow.appendChild(document.createTextNode(" N:"));
                randRow.appendChild(randN);
                randRow.appendChild(randSep);
                randRow.appendChild(sfwCb);
                randRow.appendChild(sfwLabel);
                randRow.appendChild(nsfwCb);
                randRow.appendChild(nsfwLabel);

                // Validation : au moins un des deux doit être coché
                function validateNsfwCheckboxes() {
                    if (!sfwCb.checked && !nsfwCb.checked) {
                        sfwCb.checked = true; // Forcer au moins SFW
                    }
                }
                sfwCb.onchange = () => { validateNsfwCheckboxes(); syncElementsWidget(); };
                nsfwCb.onchange = () => { validateNsfwCheckboxes(); syncElementsWidget(); };
                randCb.onchange = () => { syncElementsWidget(); };
                randN.onchange = () => { syncElementsWidget(); };
                randN.oninput = () => { syncElementsWidget(); };

                // ---- Test generation button (hauteur fixe) ----
                const genBtn = mkBtn("🔄  Test generation", true);
                genBtn.style.width = "100%";
                genBtn.style.padding = "6px";
                genBtn.style.marginBottom = "8px";
                genBtn.style.flex = "0 0 auto";

                genBtn.onclick = () => triggerGenerate(node);

                // ---- Hash deterministe (pour le random par seed) ----
                // 32-bit FNV-1a : simple, rapide, distribue raisonnablement.
                function hash32(str) {
                    let h = 0x811c9dc5;
                    for (let i = 0; i < str.length; i++) {
                        h ^= str.charCodeAt(i);
                        h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
                    }
                    return h >>> 0;
                }

                // Choisit 1 alternative parmi celles separees par "::".
                // Si 1 seule (ou aucune), retourne le texte tel quel.
                // Deterministe : meme (seed, index, texte) => meme choix.
                // Si seed == 0 (pas de seed), random non-deterministe.
                function pickAlternative(rawText, seed, elementIndex) {
                    if (!rawText) return "";
                    const alts = rawText.split("::").map(s => s.trim()).filter(Boolean);
                    if (alts.length < 2) return rawText;
                    if (seed <= 0) {
                        // Pas de seed : random classique
                        return alts[Math.floor(Math.random() * alts.length)];
                    }
                    // Hash du triplet (seed, index, rawText) pour eviter les
                    // collisions entre elements ayant les memes alternatives
                    const h = hash32(`${seed}|${elementIndex}|${rawText}`);
                    return alts[h % alts.length];
                }

                // ---- triggerGenerate ----
                function triggerGenerate(n) {
                    const allElements = n._friaElements || [];
                    const elements = allElements.filter(e => e.visible !== false);

                    if (elements.length === 0 && !randCb.checked) {
                        result.value = "Ajoutez au moins un élément visible ou activez Add random.";
                        return;
                    }

                    // Lire le seed depuis le widget ComfyUI
                    const sw = n.widgets?.find(w => w.name === "seed");
                    const seed = sw ? parseInt(sw.value) || 0 : 0;

                    // Construire le payload
                    const payload = { elements: [] };
                    if (seed > 0) payload.seed = seed;

                    elements.forEach((e, idx) => {
                        if (e.type === "filter") {
                            payload.elements.push({ type: "filter", id: e.id });
                        } else if (e.type === "text") {
                            // Si le texte contient "::", choisir 1 alternative au hasard
                            // (deterministe par seed pour reproductibilite du workflow).
                            const chosen = pickAlternative(e.text || "", seed, idx);
                            payload.elements.push({ type: "raw", text: chosen });
                        }
                    });

                    if (randCb.checked) {
                        payload.random_count = parseInt(randN.value) || 3;
                        payload.random_sfw = sfwCb.checked;
                        payload.random_nsfw = nsfwCb.checked;
                    }

                    result.value = "Génération en cours...";

                    apiCall("POST", "generate", payload).then(data => {
                        const prompt = data.prompt || "";
                        if (node._resultArea) node._resultArea.value = prompt;
                        syncElementsWidget();
                        syncApiConfigWidget();
                    }).catch(err => {
                        if (node._resultArea) node._resultArea.value = "Erreur : " + err.message;
                    });
                }

                // ---- Result area (hauteur fixe, calée en bas, pas de resize) ----
                const result = document.createElement("textarea");
                Object.assign(result.style, {
                    width: "100%",
                    height: "54px",            // Hauteur fixe
                    minHeight: "54px",
                    maxHeight: "54px",
                    borderRadius: "4px",
                    border: "1px solid #555",
                    padding: "4px",
                    background: "#1a1a1e",
                    color: "#fff",
                    fontSize: "11px",
                    resize: "none",            // PAS de resize
                    boxSizing: "border-box",
                    flex: "0 0 auto",          // Ne s'étend pas, fixé en bas
                });
                result.placeholder = "Résultat...";
                result.readOnly = true;

                // ---- Assemble ----
                container.appendChild(tb);        // fixe
                container.appendChild(listEl);    // flex: grow
                container.appendChild(randRow);   // fixe
                container.appendChild(genBtn);    // fixe
                container.appendChild(result);     // fixe en bas

                // Intégrer dans le layout ComfyUI via addDOMWidget
                const domWidget = node.addDOMWidget("elements_ui", "custom", container, {
                    getValue: () => "",
                    setValue: (v) => {},
                });
                domWidget.options = domWidget.options || {};
                domWidget.options.height = 300;

                // ---- Taille minimum de la node ----
                const MIN_WIDTH = 360;

                // Intercepter le resize pour imposer un minimum
                const origOnResize = node.onResize;
                node.onResize = function (size) {
                    if (origOnResize) origOnResize.call(this, size);
                    if (size[0] < MIN_WIDTH) size[0] = MIN_WIDTH;
                };

                // Appliquer la taille initiale
                requestAnimationFrame(() => {
                    if (node.size) {
                        if (node.size[0] < MIN_WIDTH) {
                            node.setSize([MIN_WIDTH, node.size[1]]);
                        }
                    }
                });

                // ---- Persistance workflow (sauvegarde/chargement) ----
                // ComfyUI charge les valeurs des widgets APRÈS onNodeCreated.
                // On stocke la fonction de restauration sur l'instance du node
                // pour pouvoir l'appeler depuis loadedGraphNode ou afterConfigureGraph.
                // En fallback, on tente aussi périodiquement.

                function restoreFromWidgets(n) {
                    const ej = n.widgets?.find(w => w.name === "_elements_json");
                    if (!ej || !ej.value || ej.value === "{}" || ej.value === "") return false;
                    try {
                        const data = JSON.parse(ej.value);
                        if (data.elements && Array.isArray(data.elements) && data.elements.length > 0 && n._friaElements.length === 0) {
                            n._friaElements = data.elements.map(e => {
                                const visible = e.visible !== false;
                                if (e.type === "filter") {
                                    return {
                                        type: "filter",
                                        id: e.id,
                                        name: e.name || `Filtre #${e.id}`,
                                        author: e.author || "?",
                                        is_public: !!e.is_public,
                                        visible,
                                    };
                                }
                                if (e.type === "text" || e.type === "raw") {
                                    // Format interne = "text" pour l'affichage
                                    return { type: "text", text: e.text || "", visible };
                                }
                                return { ...e, visible };
                            });
                            renderList();
                        }
                        if (data.random_sfw !== undefined) sfwCb.checked = !!data.random_sfw;
                        if (data.random_nsfw !== undefined) nsfwCb.checked = !!data.random_nsfw;
                        if (data.random_count > 0) {
                            randCb.checked = true;
                            randN.value = data.random_count;
                        }
                        return true; // Succès
                    } catch (err) {
                        console.warn("[FR.IA] Impossible de restaurer les éléments :", err);
                        return false;
                    }
                }

                // Stocker sur l'instance pour que les hooks d'extension puissent l'appeler
                node._friaRestore = restoreFromWidgets.bind(null, node);

                // Fallback : tente de restaurer périodiquement (pour F5 et cas où les hooks ne marchent pas)
                let restoreAttempts = 0;
                function delayedRestore() {
                    if (restoreFromWidgets(node)) return;
                    restoreAttempts++;
                    if (restoreAttempts < 20) {
                        setTimeout(delayedRestore, 300);
                    }
                }
                setTimeout(delayedRestore, 100);

                // Stocker les refs
                node._resultArea = result;
                node._domWidget = domWidget;

                // ---- onExecuted SUR L'INSTANCE (pas le prototype !) ----
                // LiteGraph met this.onExecuted = null dans son constructeur,
                // ce qui MASQUE tout override sur nodeType.prototype.
                // On doit donc écraser la propriété directement sur l'instance.
                const origExec = node.onExecuted; // null (mis par le constructeur)
                node.onExecuted = function (output) {
                    if (origExec) origExec.call(this, output);

                    // ComfyUI passe le résultat differemment selon la version :
                    //   Nouveau frontend : detail.output = { elements: ["text"] }
                    //   Ancien frontend : output = { elements: "text" } ou ["text"]
                    let text = null;
                    if (output && typeof output === 'object') {
                        if (output.output !== undefined) {
                            const out = output.output;
                            if (typeof out === 'object' && !Array.isArray(out) && out.elements !== undefined) text = out.elements;
                            else if (Array.isArray(out) && out.length > 0) text = out[0];
                            else if (typeof out === 'string') text = out;
                        }
                        if (text === null && output.elements !== undefined) text = output.elements;
                        if (text === null && Array.isArray(output) && output.length > 0) text = output[0];
                        // ComfyUI sérialise souvent les sorties avec des clés numériques : {"0": "text"}
                        if (text === null) {
                            for (const key of Object.keys(output)) {
                                if (/^\d+$/.test(key)) { text = output[key]; break; }
                            }
                        }
                    }
                    if (text === null && typeof output === 'string') text = output;

                    if (text !== null && text !== undefined) {
                        const str = Array.isArray(text) ? text.join("") : String(text);
                        console.log("[FR.IA] onExecuted result:", str.substring(0, 80));
                        if (node._resultArea) {
                            node._resultArea.value = str;
                        }
                    } else if (output) {
                        console.log("[FR.IA] onExecuted: format inconnu:", JSON.stringify(output).substring(0, 200));
                    }
                };

                // Sync initial
                syncElementsWidget();
                syncApiConfigWidget();

                return r;
            };
        },

        // Hook appelé APRÈS que ComfyUI a restauré les widgets depuis le workflow
        async loadedGraphNode(node) {
            if (node._friaRestore) {
                setTimeout(() => node._friaRestore(), 0);
            }
        },

        // Écouteur d'événements API global (la méthode la plus fiable)
        async setup() {
            // API singleton : ancien frontend (window.app.api) ou nouveau (window.comfyAPI.api)
            const api = window.app?.api || window.comfyAPI?.api;
            if (!api) return;

            api.addEventListener("executed", ({ detail }) => {
                if (!detail?.node || !detail?.output) return;
                
                // Trouver le nœud dans le graph
                const node = window.app.graph.getNodeById(detail.node);
                if (!node || node.type !== "FRIAElementsNode" || !node._resultArea) return;

                const output = detail.output;
                let text = null;

                // Extraction robuste du texte
                if (output.elements !== undefined) text = output.elements;
                else if (Array.isArray(output) && output.length > 0) text = output[0];
                else if (typeof output === 'string') text = output;
                else if (output.output) { // Format nested
                    const o = output.output;
                    if (o.elements !== undefined) text = o.elements;
                    else if (Array.isArray(o) && o.length > 0) text = o[0];
                }
                // ComfyUI sérialise souvent avec clés numériques : {"0": "text"}
                if (text === null && typeof output === 'object') {
                    for (const key of Object.keys(output)) {
                        if (/^\d+$/.test(key)) { text = output[key]; break; }
                    }
                }

                if (text !== null && text !== undefined) {
                    const str = Array.isArray(text) ? text.join("") : String(text);
                    node._resultArea.value = str;
                    console.log("[FR.IA] WebSocket executed update:", str.substring(0, 50));
                }
            });
        }
    });
})();

// ========================
// Utilitaires : modales et toots
// ========================

/** Escaping HTML pour injection sécurisée dans innerHTML */
function esc(str) {
    if (typeof str !== "string") return "";
    var d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
}

function showFilterPicker(filters, currentUserId, onSelect) {
    var mine = currentUserId
        ? filters.filter(function(f) { return f.user_id === currentUserId && !f.is_public; })
        : filters.filter(function(f) { return f.user_id && !f.is_public; });
    var pub = filters.filter(function(f) { return f.is_public; });

    var html = '<div style="max-height:50vh;overflow-y:auto;">';

    if (mine.length > 0) {
        html += '<p style="margin:8px 0 4px;font-size:11px;color:#888;">Mes filtres</p>';
        mine.forEach(function(f) {
            html += '<div class="fria-filter-item" data-id="' + f.id + '" style="padding:6px 8px;cursor:pointer;border-radius:4px;font-size:12px;color:#ccc;background:#3a3a3e;margin-bottom:2px;">' +
                esc(f.name) + (f.nsfw ? ' 🔞' : '') + '</div>';
        });
    }
    if (pub.length > 0) {
        html += '<p style="margin:8px 0 4px;font-size:11px;color:#888;">Filtres publics</p>';
        pub.forEach(function(f) {
            html += '<div class="fria-filter-item" data-id="' + f.id + '" style="padding:6px 8px;cursor:pointer;border-radius:4px;font-size:12px;color:#ccc;background:#3a3a3e;margin-bottom:2px;">' +
                esc(f.name) + (f.nsfw ? ' 🔞' : '') + ' <span style="color:#888;font-size:10px;">par ' + esc(f.user_id ? f.user_id.substring(0,6) : '?') + '</span></div>';
        });
    }
    if (filters.length === 0) {
        html += '<p style="font-size:12px;color:#666;">Aucun filtre disponible.</p>';
    }
    html += '</div>';

    var m = friaOpenModalV2({
        title: "Choisir un filtre",
        content: html,
        width: "380px",
        height: "auto",
        minHeight: "150px",
        maxHeight: "70vh",
        resizable: false,
        storageKey: null
    });

    // Attacher les événements aux items
    m.modal.querySelectorAll(".fria-filter-item").forEach(function(el) {
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

function showPrompt(title, msg, placeholder, cb) {
    friaShowPrompt(title, msg, placeholder).then(function(value) {
        if (cb) cb(value);
    });
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