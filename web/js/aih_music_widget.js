/**
 * AIH Music — Custom DOM widget for ComfyUI node AIHMusicNode.
 *
 * Toutes les entrées sont wires-only (musique, lyrics, llm_config), donc pas
 * de paramètres configurables. Le DOM widget affiche :
 *   - un bouton "🎵 Générer" (test cloud POST /api/music3/generate)
 *   - 3 zones read-only : caption, lyrics, duration_seconds
 *
 * Le mode (local/cloud) est décidé côté Python selon que llm_config est
 * connecté. Le bouton de test envoie toujours en mode cloud.
 */
(function() {
    // Cacher un widget ComfyUI : reste dans node.widgets (sérialisé) mais invisible dans l'UI.
    // Pattern identique à aih_elements_widget.js / aih_keywords_widget.js.
    // hidden=true est sûr : serialize() ne vérifie que serialize===false (pas hidden),
    // donc le widget reste sérialisé et restauré normalement. Ne PAS mettre serialize=false.
    function hideWidget(node, name) {
        const w = node.widgets?.find(x => x.name === name);
        if (w) {
            w.hidden = true;
            w.computeSize = () => [0, -4];
            if (w.element) w.element.style.display = "none";
            if (w.inputEl) w.inputEl.style.display = "none";
            if (w.parentEl) w.parentEl.style.display = "none";
            return w;
        }
        return null;
    }

    function aihBoot() {
        // Attendre que 03_aih_shared.js / 04_aih_widget_base.js aient défini
        // window.AIH avant d'enregistrer l'extension.
        if (!window.AIH || !window.AIH.waitForApp) { setTimeout(aihBoot, 50); return; }

        window.AIH.waitForApp(function(app) {
            app.registerExtension({
                name: "AIH.Music",

                async beforeRegisterNodeDef(nodeType, nodeData) {
                    if (nodeData.name !== "AIHMusicNode") return;

                    const onNodeCreated = nodeType.prototype.onNodeCreated;
                    nodeType.prototype.onNodeCreated = function () {
                        const r = onNodeCreated?.apply(this, arguments);
                        const node = this;

                        // ---- Helpers API (copiés depuis aih_enhance_widget.js) ----
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
                            try {
                                const resp = await fetch(`${getApiUrl()}/${path.replace(/^\//, "")}`, { headers: apiHeaders() });
                                if (!resp.ok) return [];
                                return resp.json().catch(() => []);
                            } catch { return []; }
                        };

                        // ---- Dropdown Preset IA : cache 15s + refresh sur mousedown ----
                        // (pattern identique à aih_enhance_widget.js)
                        const _cache = (window.__AIH_cache = window.__AIH_cache || { presets: 0, styles: 0, tmpl: 0 });
                        const PRESET_CACHE_TTL = 15000;

                        async function populatePresets(select) {
                            try {
                                const items = await apiGet("presets");
                                if (!Array.isArray(items)) return;
                                const oldVal = select.value;
                                select.innerHTML = '<option value="0">-- Preset IA --</option>';
                                // Trier par ordre alphabétique
                                items.sort(function (a, b) {
                                    var nameA = (a.name || a.title || a.text || "").toString().toLowerCase();
                                    var nameB = (b.name || b.title || b.text || "").toString().toLowerCase();
                                    return nameA.localeCompare(nameB);
                                });
                                items.forEach(function (item) {
                                    const o = document.createElement("option");
                                    o.value = item.id;
                                    o.textContent = item.name;
                                    select.appendChild(o);
                                });
                                if ([...select.options].some(o => o.value === oldVal)) select.value = oldVal;
                            } catch {}
                        }
                        async function refreshPresetsIfStale(select) {
                            const now = Date.now();
                            if (now - (_cache.presets || 0) < PRESET_CACHE_TTL) return;
                            _cache.presets = now;
                            await populatePresets(select);
                        }

                        // ---- Container ----
                        const container = document.createElement("div");
                        Object.assign(container.style, {
                            width: "100%", height: "100%", padding: "8px", boxSizing: "border-box",
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
                        const mkReadonly = () => {
                            const ta = document.createElement("textarea");
                            Object.assign(ta.style, {
                                width: "100%", minHeight: "30px", flex: "1",
                                borderRadius: "4px", border: "1px solid #555",
                                padding: "4px", background: "#1a1a1e", color: "#fff",
                                fontSize: "11px", resize: "none", boxSizing: "border-box",
                            });
                            ta.readOnly = true;
                            return ta;
                        };

                        // ---- Bouton Générer ----
                        const genBtn = document.createElement("button");
                        genBtn.textContent = "🎵  Générer";
                        Object.assign(genBtn.style, {
                            width: "100%", padding: "6px", borderRadius: "4px",
                            border: "none", background: "#6366f1", color: "white",
                            fontSize: "11px", fontWeight: "600", cursor: "pointer",
                        });
                        genBtn.onmouseenter = () => genBtn.style.background = "#5558e8";
                        genBtn.onmouseleave = () => genBtn.style.background = "#6366f1";

                        // ---- Dropdown Preset IA (select le model backend cloud) ----
                        const presetRow = document.createElement("div");
                        const presetSelect = document.createElement("select");
                        Object.assign(presetSelect.style, {
                            width: "100%", padding: "3px 6px", borderRadius: "4px",
                            border: "1px solid #555", background: "#3a3a3e",
                            color: "#ccc", fontSize: "11px", cursor: "pointer",
                        });
                        presetSelect.innerHTML = '<option value="0">-- Preset IA --</option>';
                        presetRow.appendChild(mkLabel("Preset IA"));
                        presetRow.appendChild(presetSelect);

                        // Le widget natif preset_id (côté Python) est la source de vérité
                        // sérialisée par ComfyUI. Le dropdown DOM le pilote.
                        const getPresetIdWidget = () => node.widgets?.find(w => w.name === "preset_id");
                        function syncPresetWidget() {
                            const w = getPresetIdWidget();
                            if (!w) return;
                            const val = parseInt(presetSelect.value) || 0;
                            if (w.value !== val) {
                                w.value = val;
                                if (w.callback) w.callback(val);
                            }
                        }
                        function restorePresetFromNative() {
                            const w = getPresetIdWidget();
                            if (!w) return;
                            const pid = parseInt(w.value) || 0;
                            const current = parseInt(presetSelect.value) || 0;
                            if (current > 0 && current !== pid) {
                                // L'utilisateur a choisi une valeur : on respecte son choix.
                                w.value = current;
                                if (w.callback) w.callback(current);
                            } else if (pid > 0 && [...presetSelect.options].some(o => o.value === String(pid))) {
                                presetSelect.value = String(pid);
                            } else if (pid === 0) {
                                presetSelect.value = "0";
                            }
                        }
                        presetSelect.onchange = syncPresetWidget;
                        presetSelect.addEventListener("mousedown", () => refreshPresetsIfStale(presetSelect));
                        populatePresets(presetSelect).then(restorePresetFromNative);

                        // ---- Zones read-only ----
                        const captionTa = mkReadonly();
                        captionTa.placeholder = "Caption...";
                        const lyricsTa = mkReadonly();
                        lyricsTa.placeholder = "Lyrics...";
                        const durationDiv = document.createElement("div");
                        Object.assign(durationDiv.style, {
                            fontSize: "11px", color: "#fff", padding: "2px 0",
                        });
                        durationDiv.textContent = "Durée: —";

                        container.appendChild(presetRow);
                        container.appendChild(genBtn);
                        container.appendChild(mkLabel("Caption"));
                        container.appendChild(captionTa);
                        container.appendChild(mkLabel("Lyrics"));
                        container.appendChild(lyricsTa);
                        container.appendChild(mkLabel("Duration"));
                        container.appendChild(durationDiv);

                        // ---- Ajout au node ----
                        const widget = node.addDOMWidget("AIH_Music", "div", container, {
                            serialize: false,
                            hideOnZoom: false,
                            getValue: () => "",
                            setValue: () => {},
                            getMinHeight: () => 180,
                        });
                        // Sérialisation : poser LES DEUX flags (cf. enhance_widget)
                        widget.serialize = false;
                        widget.options.serialize = false;
                        node.widgets_start_y = 52;

                        // ---- Masquer le widget natif preset_id ----
                        // Le dropdown DOM "Preset IA" (presetSelect) le pilote déjà et
                        // reste la seule interface visible. hidden=true garde la
                        // sérialisation (seul serialize===false l'empêche). Le widget
                        // natif seed, lui, reste visible (géré par ComfyUI).
                        hideWidget(node, "preset_id");

                        // ---- Largeur minimale (fix layout seed vs sockets) ----
                        // Sans largeur plancher, la node pouvait être trop étroite : le
                        // widget natif seed (label à gauche + valeur/control à droite)
                        // s'étirait jusqu'aux bords et chevauchait la socket d'entrée
                        // llm_config (gauche) et la sortie duration_seconds (droite).
                        // Pattern identique à aih_elements_widget.js / aih_keywords_widget.js.
                        const MIN_WIDTH = 360;
                        const origResize = node.onResize;
                        node.onResize = function (size) {
                            const r = origResize?.apply(this, arguments);
                            if (size[0] < MIN_WIDTH) size[0] = MIN_WIDTH;
                            container.style.width = (size[0] - 20) + "px";
                            return r;
                        };
                        requestAnimationFrame(() => {
                            if (node.size && node.size[0] < MIN_WIDTH) {
                                node.setSize([MIN_WIDTH, node.size[1]]);
                            }
                            container.style.width = (node.size[0] - 20) + "px";
                        });

                        // ---- Test Générer (mode cloud) ----
                        genBtn.onclick = async () => {
                            const get = (name) => node.widgets?.find(w => w.name === name);
                            const musique = (get("musique")?.value || "").trim();
                            const lyrics = (get("lyrics")?.value || "").trim();
                            const seedWidget = get("seed");
                            const seed = seedWidget ? (parseInt(seedWidget.value) || 0) : 0;
                            const presetIdNative = parseInt(get("preset_id")?.value) || 0;
                            let presetId = parseInt(presetSelect.value) || 0;
                            if (presetId === 0) presetId = presetIdNative;

                            if (!musique && !lyrics) {
                                captionTa.value = "Connecte au moins 'musique' (ou 'lyrics') pour tester.";
                                return;
                            }
                            const payload = { musique, lyrics };
                            if (seed > 0) payload.seed = seed;
                            if (presetId > 0) payload.preset_id = presetId;
                            captionTa.value = "Génération en cours...";
                            lyricsTa.value = "";
                            durationDiv.textContent = "Durée: —";
                            try {
                                const resp = await fetch(`${getApiUrl()}/music3/generate`, {
                                    method: "POST", headers: apiHeaders(), body: JSON.stringify(payload),
                                });
                                if (!resp.ok) {
                                    const t = await resp.text().catch(() => "");
                                    throw new Error(`HTTP ${resp.status}: ${t.substring(0, 200)}`);
                                }
                                const data = await resp.json();
                                captionTa.value = data.caption || "";
                                lyricsTa.value = data.lyrics || "";
                                const d = parseInt(data.duration_seconds) || 0;
                                durationDiv.textContent = `Durée: ${d} s`;
                            } catch (err) {
                                // Fallback local : si le backend cloud est injoignable,
                                // vérifier que le mode LOCAL de la node est disponible
                                // (références syncées / store local peuplé).
                                try {
                                    const lresp = await fetch("/aih/local/api/music3/manifest");
                                    if (lresp.ok) {
                                        const manifest = await lresp.json().catch(() => null);
                                        const hasRefs = manifest && Array.isArray(manifest.files) && manifest.files.length > 0;
                                        captionTa.value = hasRefs
                                            ? "Backend indisponible — le test button cloud ne marche pas offline. Utilisez le mode LOCAL : connectez llm_config sur la node (références locales prêtes)."
                                            : "Backend indisponible — utilisez le mode LOCAL (connectez llm_config) ou vérifiez /aih/local/status.";
                                        return;
                                    }
                                } catch {}
                                captionTa.value = "Backend indisponible — utilisez le mode LOCAL (connectez llm_config) ou vérifiez /aih/local/status.";
                            }
                        };

                        // ---- onExecuted (sur l'instance, pas le prototype) ----
                        const origExec = node.onExecuted;
                        node.onExecuted = function (output) {
                            if (origExec) origExec.call(this, output);
                            const capArr = output?.caption;
                            const lyrArr = output?.lyrics;
                            const durArr = output?.duration;
                            if (Array.isArray(capArr) && capArr.length > 0) {
                                captionTa.value = String(capArr[0]);
                            }
                            if (Array.isArray(lyrArr) && lyrArr.length > 0) {
                                lyricsTa.value = String(lyrArr[0]);
                            }
                            if (Array.isArray(durArr) && durArr.length > 0) {
                                const d = parseInt(durArr[0]);
                                durationDiv.textContent = `Durée: ${isNaN(d) ? '—' : d + ' s'}`;
                            }
                        };

                        return r;
                    };
                },
            });
        });
    }
    aihBoot();
})();
