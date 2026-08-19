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

                        // ---- Container ----
                        const container = document.createElement("div");
                        Object.assign(container.style, {
                            width: "100%", height: "100%", padding: "8px", boxSizing: "border-box",
                            background: "#2a2a2e", borderRadius: "8px",
                            display: "flex", flexDirection: "column", gap: "6px",
                            fontSize: "12px", color: "#ccc", overflow: "auto",
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

                        // ---- Test Générer (mode cloud) ----
                        genBtn.onclick = async () => {
                            const get = (name) => node.widgets?.find(w => w.name === name);
                            const musique = (get("musique")?.value || "").trim();
                            const lyrics = (get("lyrics")?.value || "").trim();
                            const seedWidget = get("seed");
                            const seed = seedWidget ? (parseInt(seedWidget.value) || 0) : 0;

                            if (!musique && !lyrics) {
                                captionTa.value = "Connecte au moins 'musique' (ou 'lyrics') pour tester.";
                                return;
                            }
                            const payload = { musique, lyrics };
                            if (seed > 0) payload.seed = seed;
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
                                captionTa.value = "Erreur: " + err.message;
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
