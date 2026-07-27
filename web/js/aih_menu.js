/**
 * AIH — Menu & Settings extension for ComfyUI.
 * Adds a [AIH] button to the menu bar with dropdown options.
 * Modales : draggable, ✕ pour fermer, pas de fermeture au clic extérieur.
 */

const STORAGE_KEY = "AIH_config";

function getConfig() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
    catch { return {}; }
}

function setConfig(cfg) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg));
}

// ── Modale draggable (style AIH, pas de fermeture au clic extérieur) ──

// Utilise aihOpenModalV2 (01_aih_modal_v2.js) pour toutes les fenêtres flottantes.

// Wait for ComfyUI app to be available
(function waitForApp() {
    const app = window.app || window.comfyAPI?.app?.app;
    if (!app) {
        setTimeout(waitForApp, 100);
        return;
    }

    app.registerExtension({
        name: "AIH.Menu",
        async setup() {
            setTimeout(() => initMenu(app), 50);
        }
    });
})();

function initMenu(appInstance) {
    const settingsButton = appInstance?.menu?.settingsGroup?.element;
    if (!settingsButton) {
        setTimeout(() => initMenu(appInstance), 300);
        return;
    }

    const wrapper = document.createElement("div");
    wrapper.style.position = "relative";
    wrapper.style.display = "inline-block";
    wrapper.style.margin = "0 4px";

    const btn = document.createElement("button");
    btn.textContent = "AIH ▾";
    Object.assign(btn.style, {
        background: "#6366f1", color: "white", border: "none",
        padding: "4px 12px", borderRadius: "6px", cursor: "pointer",
        fontSize: "13px", fontWeight: "600",
    });

    const dd = document.createElement("div");
    dd.id = "aih-dropdown-menu";
    dd.style.display = "none";
    dd.style.zIndex = "10005";
    Object.assign(dd.style, {
        position: "absolute", top: "100%", left: "0",
        background: "#2a2a2e", border: "1px solid #444", borderRadius: "8px",
        minWidth: "200px", boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
    });

    const mkItem = (txt, icon, cb) => {
        const el = document.createElement("div");
        el.textContent = `${icon}  ${txt}`;
        Object.assign(el.style, {
            padding: "10px 16px", cursor: "pointer", fontSize: "13px",
            borderBottom: "1px solid #444",
        });
        el.onmouseenter = () => el.style.background = "#3a3a3e";
        el.onmouseleave = () => el.style.background = "";
        el.onclick = async () => { await cb(); dd.style.display = "none"; };
        return el;
    };

    dd.appendChild(mkItem("Open Webpage", "🌐", () => {
        const cfg = getConfig();
        window.open(cfg.serverUrl || "https://kw.holaf.fr", "_blank");
    }));
    dd.appendChild(mkItem("Workflows", "📤", () => {
        if (window.openWorkflowManager) window.openWorkflowManager();
        else aihShowAlert("Info", "Workflow Sharing pas encore chargé. Réessaie.", "info");
    }));
    dd.appendChild(mkItem("📦 Models", "📦", () => {
        if (window.openModelBrowser) window.openModelBrowser();
        else aihShowAlert("Info", "Model Browser pas encore chargé.", "info");
    }));
    dd.appendChild(mkItem("Terminal", "💻", () => {
        if (window.aihTerminal) {
            window.aihTerminal.toggle();
        } else {
            console.warn("[AIH] Terminal widget pas encore charge.");
            aihShowAlert("Info", "AIH Terminal: widget pas encore chargé. Réessaie dans une seconde.", "info");
        }
    }));
    dd.appendChild(mkItem("Membres", "👥", () => openMembers()));
    dd.appendChild(mkItem("Paramètres", "⚙️", () => openSettings()));
    dd.appendChild(mkItem("Update", "🔄", () => openUpdate()));

    // ── Blobby Companion toggle ──
    const blobbyDiv = document.createElement("div");
    Object.assign(blobbyDiv.style, {
        padding: "10px 16px", cursor: "pointer", fontSize: "13px",
        borderBottom: "1px solid #444",
        display: "flex", alignItems: "center", gap: "8px",
    });
    blobbyDiv.onmouseenter = () => blobbyDiv.style.background = "#3a3a3e";
    blobbyDiv.onmouseleave = () => blobbyDiv.style.background = "";

    const blobbyIcon = document.createElement("span");
    blobbyIcon.textContent = "🧡";
    const blobbyLabel = document.createElement("span");
    blobbyLabel.style.flex = "1";
    const blobbyStatus = document.createElement("span");
    Object.assign(blobbyStatus.style, {
        fontSize: "10px", padding: "1px 6px", borderRadius: "4px",
        fontWeight: "600",
    });

    // Chat menu item (doit etre declare avant updateBlobbyUI qui l'utilise)
    var chatDiv = document.createElement("div");

    function updateBlobbyUI() {
        const active = window.BlobbyCompanion && window.BlobbyCompanion.isActive();
        blobbyLabel.textContent = active ? "Blobby (test)" : "Activer Blobby";
        blobbyStatus.textContent = active ? "ON" : "OFF";
        blobbyStatus.style.background = active ? "#166534" : "#555";
        blobbyStatus.style.color = active ? "#86efac" : "#aaa";
        // Afficher/cacher le chat selon l'etat de Blobby
        if (chatDiv) chatDiv.style.display = active ? "flex" : "none";
    }
    updateBlobbyUI();
    // Rattraper si Blobby s'active apres l'init du menu
    setTimeout(updateBlobbyUI, 1000);

    blobbyDiv.onclick = () => {
        if (window.BlobbyCompanion && window.BlobbyCompanion.toggle) {
            window.BlobbyCompanion.toggle();
            updateBlobbyUI();
        }
        // Ne pas fermer le menu
    };

    blobbyDiv.appendChild(blobbyIcon);
    blobbyDiv.appendChild(blobbyLabel);
    blobbyDiv.appendChild(blobbyStatus);
    dd.appendChild(blobbyDiv);

    // ── Blobby Chat (visible seulement quand Blobby est actif) ──
    chatDiv.textContent = "💬  Chat";
    Object.assign(chatDiv.style, {
        padding: "10px 16px", cursor: "pointer", fontSize: "13px",
        borderBottom: "1px solid #444", display: "none",
    });
    chatDiv.onmouseenter = () => { if (chatDiv.style.display !== 'none') chatDiv.style.background = "#3a3a3e"; };
    chatDiv.onmouseleave = () => chatDiv.style.background = "";
    chatDiv.onclick = () => {
        if (window.BlobbyCompanion && window.BlobbyCompanion.openChat) {
            window.BlobbyCompanion.openChat();
        }
    };
    dd.appendChild(chatDiv);

    // Statut serveur
    const statusDiv = document.createElement("div");
    statusDiv.id = "aih-server-status";
    Object.assign(statusDiv.style, {
        padding: "8px 16px", fontSize: "11px", color: "#888",
        borderTop: "1px solid #444", cursor: "default",
    });
    statusDiv.textContent = "Statut : vérification...";
    dd.appendChild(statusDiv);

    wrapper.appendChild(btn);
    wrapper.appendChild(dd);

    btn.onclick = (e) => {
        e.stopPropagation();
        const opening = dd.style.display !== "block";
        dd.style.display = opening ? "block" : "none";
        if (opening) checkServerStatus(statusDiv);
    };

    document.addEventListener("click", (e) => {
        if (dd.style.display === "block" && !wrapper.contains(e.target)) {
            dd.style.display = "none";
        }
    });

    settingsButton.parentNode.insertBefore(wrapper, settingsButton);
    console.log("[AIH] Menu initialized");
}

// ── Membres ──────────────────────────────────────────────────────────

async function openMembers() {
    const cfg = getConfig();
    const baseUrl = (cfg.serverUrl || "https://kw.holaf.fr").replace(/\/+$/, "");
    const apiKey = cfg.apiKey || "";

    const modal = aihOpenModalV2({
        title: "👥 Membres",
        content: "<p style='color:#888;font-size:12px;'>Chargement...</p>",
        width: "560px",
        height: "auto",
        minHeight: "200px",
        storageKey: "aih-modal-members",
        persistSize: true,
        persistPos: true
    });

    try {
        const headers = {};
        if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;
        const resp = await fetch(`${baseUrl}/api/members`, { method: "GET", headers });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const members = await resp.json();

        // Trier : admin en premier, puis kw_editor, puis par nom
        members.sort((a, b) => {
            const rank = (r) => r === "admin" ? 0 : r === "kw_editor" ? 1 : 2;
            const diff = rank(a.role) - rank(b.role);
            if (diff !== 0) return diff;
            return (a.display_name || a.username || "").localeCompare(b.display_name || b.username || "");
        });

        let html = `<table style="width:100%;border-collapse:collapse;font-size:12px;color:#ccc;">
            <thead>
                <tr style="border-bottom:1px solid #555;">
                    <th style="text-align:left;padding:6px 8px;color:#888;font-weight:600;">Membre</th>
                    <th style="text-align:center;padding:6px 4px;color:#888;font-weight:600;">Rôle</th>
                    <th style="text-align:center;padding:6px 4px;color:#888;font-weight:600;">Filtres</th>
                    <th style="text-align:center;padding:6px 4px;color:#888;font-weight:600;">Prompts</th>
                </tr>
            </thead><tbody>`;

        for (const m of members) {
            const name = m.display_name || m.username || m.id?.substring(0, 8) || "?";
            const avatarUrl = m.avatar_url || (m.avatar && m.id ? `https://cdn.discordapp.com/avatars/${m.id}/${m.avatar}.png?size=32` : null);
            const roleBadge = m.role === "admin"
                ? '<span style="background:#6366f1;color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;">admin</span>'
                : m.role === "kw_editor"
                  ? '<span style="background:#d97706;color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;">kw_editor</span>'
                  : '<span style="color:#888;font-size:11px;">membre</span>';

            html += `<tr style="border-bottom:1px solid #333;">
                <td style="padding:6px 8px;display:flex;align-items:center;gap:8px;">
                    ${avatarUrl ? `<img src="${avatarUrl}" style="width:24px;height:24px;border-radius:50;flex-shrink:0;">` : '<span style="width:24px;height:24px;display:inline-flex;align-items:center;justify-content:center;background:#444;border-radius:50%;font-size:11px;flex-shrink:0;">👤</span>'}
                    <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${name}</span>
                </td>
                <td style="text-align:center;padding:6px 4px;">${roleBadge}</td>
                <td style="text-align:center;padding:6px 4px;">${m.filter_count ?? 0}</td>
                <td style="text-align:center;padding:6px 4px;">${m.prompt_count ?? 0}</td>
            </tr>`;
        }

        html += "</tbody></table>";
        modal.body.innerHTML = html;
    } catch (err) {
        modal.body.innerHTML = `<p style="color:#f87171;font-size:12px;">Erreur : ${err.message}</p>`;
    }
}

// ── Statut serveur ────────────────────────────────────────────────────

async function checkServerStatus(el) {
    const cfg = getConfig();
    const baseUrl = (cfg.serverUrl || "https://kw.holaf.fr").replace(/\/+$/, "");
    const apiKey = cfg.apiKey || "";
    try {
        const headers = {};
        if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;

        // Compat : AbortSignal.timeout() n'existe pas dans tous les navigateurs
        const makeTimeoutSignal = (ms) => {
            if (typeof AbortSignal !== "undefined" && AbortSignal.timeout) {
                return AbortSignal.timeout(ms);
            }
            const ctrl = new AbortController();
            setTimeout(() => ctrl.abort(), ms);
            return ctrl.signal;
        };
        const timeoutSignal = makeTimeoutSignal(5000);

        const [statsResp, meResp] = await Promise.all([
            fetch(`${baseUrl}/api/stats`, { method: "GET", headers, signal: timeoutSignal }).catch(() => null),
            fetch(`${baseUrl}/api/auth/me`, { method: "GET", headers, signal: timeoutSignal }).catch(() => null),
        ]);

        const serverOk = statsResp && statsResp.ok;
        let user = null;
        if (meResp && meResp.ok) {
            try { user = await meResp.json(); } catch {}
        }

        el.innerHTML = "";
        el.style.display = "flex";
        el.style.alignItems = "center";
        el.style.gap = "6px";

        if (user && (user.display_name || user.username)) {
            const name = user.display_name || user.username || "?";
            const avatarUrl = user.avatar_url || (user.avatar && user.id ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png?size=64` : null);

            const dot = document.createElement("span");
            dot.textContent = "🟢";
            dot.style.cssText = "font-size:11px;line-height:1;flex-shrink:0;";
            el.appendChild(dot);

            const nameSpan = document.createElement("span");
            nameSpan.textContent = name;
            nameSpan.style.cssText = "flex:1;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
            el.appendChild(nameSpan);

            if (avatarUrl) {
                const img = document.createElement("img");
                img.src = avatarUrl;
                img.style.cssText = "width:22px;height:22px;border-radius:50%;flex-shrink:0;";
                el.appendChild(img);
            }

            el.style.color = "#4ade80";
        } else if (serverOk) {
            el.textContent = "🟢  Serveur en ligne (non connecté)";
            el.style.color = "#4ade80";
        } else if (statsResp) {
            el.textContent = "🟡  Serveur répond (HTTP " + statsResp.status + ")";
            el.style.color = "#facc15";
        } else {
            el.textContent = "🔴  Serveur hors ligne";
            el.style.color = "#f87171";
        }
    } catch {
        el.textContent = "🔴  Serveur hors ligne";
        el.style.color = "#f87171";
    }
}

// ── Paramètres (modale a 2 onglets : Provider / Compte) ──

function openSettings() {
    const cfg = getConfig();
    const modal = aihOpenModalV2({
        title: "⚙️ Paramètres AIH",
        width: "720px",
        height: "600px",
        minWidth: "480px",
        minHeight: "350px",
        storageKey: "aih-modal-settings",
        persistSize: true,
        persistPos: true
    });

    // Tabs
    const tabsBar = document.createElement("div");
    Object.assign(tabsBar.style, {
        display: "flex", borderBottom: "1px solid #444", background: "#1a1a1e",
    });
    const tabContent = document.createElement("div");
    Object.assign(tabContent.style, { padding: "16px", overflowY: "auto", maxHeight: "calc(80vh - 100px)" });

    modal.body.innerHTML = "";
    modal.body.appendChild(tabsBar);
    modal.body.appendChild(tabContent);

    const tabs = [
        { id: "providers", label: "Provider LLM", render: renderProvidersTab },
        { id: "compte", label: "Compte", render: renderCompteTab },
    ];
    const activeTabs = new Set(["providers"]);

    const renderTabsBar = () => {
        tabsBar.innerHTML = "";
        tabs.forEach(t => {
            const btn = document.createElement("button");
            btn.textContent = t.label;
            const isActive = activeTabs.has(t.id);
            Object.assign(btn.style, {
                flex: "1", padding: "10px 12px", border: "none", cursor: "pointer",
                background: "transparent", fontSize: "13px", fontWeight: isActive ? "600" : "400",
                color: isActive ? "#fff" : "#888",
                borderBottom: isActive ? "2px solid #6366f1" : "2px solid transparent",
                transition: "all 0.15s",
            });
            btn.onclick = () => {
                activeTabs.clear();
                activeTabs.add(t.id);
                renderTabsBar();
                renderActiveTab();
            };
            tabsBar.appendChild(btn);
        });
    };

    const renderActiveTab = async () => {
        tabContent.innerHTML = "<p style='color:#888;font-size:12px;'>Chargement...</p>";
        const t = tabs.find(x => activeTabs.has(x.id));
        try {
            await t.render(tabContent, cfg);
        } catch (e) {
            tabContent.innerHTML = `<p style='color:#f87171;font-size:12px;'>Erreur : ${e.message || e}</p>`;
        }
    };

    renderTabsBar();
    renderActiveTab();
}

// ── Helpers partages ──

const _aihStyle = {
    input: "width:100%; padding:6px 10px; border-radius:4px; border:1px solid #555; background:#1a1a1e; color:#fff; font-size:12px; box-sizing:border-box;",
    label: "display:block; margin-bottom:3px; font-size:11px; color:#aaa;",
    btn: (bg = "#6366f1") => `padding:6px 12px; border-radius:4px; border:none; background:${bg}; color:white; cursor:pointer; font-size:12px; font-weight:600;`,
    btnSecondary: "padding:6px 12px; border-radius:4px; border:1px solid #555; background:transparent; color:#ccc; cursor:pointer; font-size:12px;",
    section: "padding:12px; background:#1a1a1e; border:1px solid #333; border-radius:6px; margin-bottom:12px;",
};

async function _aihFetchApi(path, opts = {}) {
    const cfg = getConfig();
    // baseUrl pointe vers le backend AIH (par defaut https://kw.holaf.fr)
    // /api/* est prefixe automatiquement
    const baseUrl = (cfg.serverUrl || "https://kw.holaf.fr").replace(/\/+$/, "");
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) headers["Authorization"] = `Bearer ${cfg.apiKey}`;
    // path peut deja commencer par /api/ (auquel cas on l'utilise tel quel) ou non
    const cleanPath = path.replace(/^\/+/, "");
    const finalPath = cleanPath.startsWith("api/") ? cleanPath : "api/" + cleanPath;
    const resp = await fetch(`${baseUrl}/${finalPath}`, {
        ...opts,
        headers: { ...headers, ...(opts.headers || {}) },
        body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!resp.ok) {
        const t = await resp.text().catch(() => "");
        let msg = `HTTP ${resp.status}`;
        try { const j = JSON.parse(t); if (j.error) msg = j.error; } catch {}
        throw new Error(msg);
    }
    return resp.json().catch(() => ({}));
}

// ── Onglet Provider LLM ─────────────────────────────────────────────

async function renderProvidersTab(container, cfg) {
    container.innerHTML = "";

    // Section : liste des presets existants
    const listSection = document.createElement("div");
    listSection.style.cssText = _aihStyle.section;
    const listTitle = document.createElement("h3");
    listTitle.textContent = "MES PRESETS";
    Object.assign(listTitle.style, { margin: "0 0 8px", fontSize: "11px", color: "#888", fontWeight: "600" });
    listSection.appendChild(listTitle);
    container.appendChild(listSection);

    async function reloadPresets() {
        const presets = await _aihFetchApi("presets");
        listSection.innerHTML = "";
        const t = document.createElement("h3");
        t.textContent = "MES PRESETS";
        Object.assign(t.style, { margin: "0 0 8px", fontSize: "11px", color: "#888", fontWeight: "600" });
        listSection.appendChild(t);
        if (presets.length === 0) {
            const empty = document.createElement("p");
            empty.textContent = "Aucun preset. Créez-en un ci-dessous.";
            Object.assign(empty.style, { color: "#888", fontSize: "12px", margin: "0" });
            listSection.appendChild(empty);
            return;
        }
        presets.forEach(p => {
            const row = document.createElement("div");
            Object.assign(row.style, {
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "6px 8px", borderBottom: "1px solid #2a2a2e", fontSize: "12px",
            });
            const left = document.createElement("div");
            const scope = p.is_global ? "global" : (p.owner_name ? `(${p.owner_name})` : "personnel");
            const clientBadge = p.is_client_side ? " <span style='color:#f59e0b;'>🖥️</span>" : "";
            left.innerHTML = `<strong style='color:#fff;'>${p.name}</strong> <span style='color:#888;'>[${scope}]</span> ${clientBadge}<br><span style='color:#888;font-size:10px;'>${p.model} @ ${p.base_url}</span>`;
            const actions = document.createElement("div");
            Object.assign(actions.style, { display: "flex", gap: "4px" });
            const editBtn = mkBtn("Edit", _aihStyle.btnSecondary, () => fillForm(p));
            const dupBtn = mkBtn("Dup", _aihStyle.btnSecondary, async () => {
                const body = { ...p };
                delete body.id;
                body.name = p.name + " (copie)";
                try {
                    await _aihFetchApi("presets", { method: "POST", body });
                    reloadPresets();
                } catch (e) { await aihShowAlert("Erreur", "Erreur duplication : " + e.message, "error"); }
            });
            const delBtn = mkBtn("Del", "padding:6px 12px;border-radius:4px;border:none;background:#7f1d1d;color:white;cursor:pointer;font-size:12px;", async () => {
                var ok = await aihShowConfirm("Supprimer", "Supprimer " + p.name + " ?"); if (!ok) return;
                try {
                    await _aihFetchApi(`presets/${p.id}`, { method: "DELETE" });
                    reloadPresets();
                } catch (e) { await aihShowAlert("Erreur", "Erreur suppression : " + e.message, "error"); }
            });
            actions.append(editBtn, dupBtn, delBtn);
            row.append(left, actions);
            listSection.appendChild(row);
        });
    }
    await reloadPresets();

    // Section : formulaire create/edit
    const form = document.createElement("div");
    form.style.cssText = _aihStyle.section;

    const editingId = { value: null };

    const formTitle = document.createElement("h3");
    formTitle.id = "aih-preset-form-title";
    formTitle.textContent = "Nouveau preset";
    Object.assign(formTitle.style, { margin: "0 0 10px", fontSize: "11px", color: "#888", fontWeight: "600" });
    form.appendChild(formTitle);

    const mkField = (label, type = "text", value = "", placeholder = "") => {
        const wrap = document.createElement("div");
        wrap.style.cssText = "margin-bottom:8px;";
        const l = document.createElement("label");
        l.textContent = label;
        l.style.cssText = _aihStyle.label;
        wrap.appendChild(l);
        const input = document.createElement("input");
        input.type = type;
        input.value = value;
        input.placeholder = placeholder;
        input.style.cssText = _aihStyle.input;
        wrap.appendChild(input);
        return { wrap, input };
    };

    const fName = mkField("Nom du preset", "text", "", "");
    form.appendChild(fName.wrap);

    const fUrl = mkField("URL du serveur", "url", "", "http://localhost:11434/v1");
    form.appendChild(fUrl.wrap);

    const fKey = mkField("API Key (optionnel)", "password", "", "");
    form.appendChild(fKey.wrap);

    // Modele + bouton Lister
    const modelRow = document.createElement("div");
    modelRow.style.cssText = "display:flex; gap:6px; margin-bottom:8px; align-items:flex-end;";
    const fModelWrap = document.createElement("div");
    fModelWrap.style.cssText = "flex:1;";
    const lblModel = document.createElement("label");
    lblModel.textContent = "Modele";
    lblModel.style.cssText = _aihStyle.label;
    fModelWrap.appendChild(lblModel);
    const fModel = document.createElement("input");
    fModel.type = "text";
    fModel.placeholder = "llama3";
    fModel.style.cssText = _aihStyle.input;
    fModelWrap.appendChild(fModel);
    modelRow.appendChild(fModelWrap);

    const listModelsBtn = mkBtn("Lister", "padding:6px 10px;border-radius:4px;border:1px solid #6366f1;background:transparent;color:#6366f1;cursor:pointer;font-size:11px;flex:0 0 auto;height:28px;");
    listModelsBtn.onclick = async () => {
        const url = fUrl.input.value.trim();
        if (!url) { await aihShowAlert("Info", "Saisis l'URL d'abord", "info"); return; }
        try {
            let models;
            const isClient = fClientInput.checked;
            if (isClient) {
                // Appel direct navigateur → serveur LLM
                const headers = { "Content-Type": "application/json" };
                const k = fKey.input.value.trim();
                if (k) headers["Authorization"] = "Bearer " + k;
                const r = await fetch(url.replace(/\/+$/, "") + "/models", { headers });
                if (!r.ok) throw new Error("HTTP " + r.status);
                const data = await r.json();
                const raw = (data && data.data) || (data && data.models) || [];
                models = raw.map(m => typeof m === "string" ? { id: m } : { id: m.id || m.name || "" });
            } else {
                // Backend proxy
                const resp = await _aihFetchApi("presets/list-models", {
                    method: "POST",
                    body: { base_url: url, api_key: fKey.input.value.trim() },
                });
                models = resp;
            }
            // Proposer un select inline pour choisir
            var choice = await aihShowPrompt("Choisir un modèle", "Modèles trouvés :\n" + models.map(m => "- " + m.id).join("\n") + "\n\nColle l'ID du modèle désiré :", models[0]?.id || "");
            if (choice) fModel.value = choice.trim();
        } catch (e) {
            await aihShowAlert("Erreur", "Erreur listage modèles : " + e.message, "error");
        }
    };
    modelRow.appendChild(listModelsBtn);
    form.appendChild(modelRow);

    // Checkboxes
    const checks = document.createElement("div");
    checks.style.cssText = "display:flex;gap:14px;margin-bottom:10px;";
    const mkCheck = (label, initial = false) => {
        const w = document.createElement("label");
        w.style.cssText = "display:flex;align-items:center;gap:5px;font-size:12px;color:#ccc;cursor:pointer;";
        const i = document.createElement("input");
        i.type = "checkbox";
        i.checked = initial;
        w.appendChild(i);
        w.appendChild(document.createTextNode(label));
        return { wrap: w, input: i };
    };
    const fGlobal = mkCheck("Global (visible par tous)", false);
    const fClient = mkCheck("Client-side", false);
    const fClientInput = fClient.input;
    checks.append(fGlobal.wrap, fClient.wrap);
    form.appendChild(checks);

    // Boutons
    const btnRow = document.createElement("div");
    btnRow.style.cssText = "display:flex;gap:8px;justify-content:flex-end;";
    const cancelBtn = mkBtn("Annuler", _aihStyle.btnSecondary, () => {
        editingId.value = null;
        fName.input.value = ""; fUrl.input.value = ""; fKey.input.value = "";
        fModel.value = ""; fGlobal.input.checked = false; fClientInput.checked = false;
        formTitle.textContent = "Nouveau preset";
    });
    const saveBtn = mkBtn("Sauvegarder", _aihStyle.btn(), async () => {
        const body = {
            name: fName.input.value.trim(),
            base_url: fUrl.input.value.trim(),
            api_key: fKey.input.value.trim(),
            model: fModel.value.trim(),
            is_global: fGlobal.input.checked ? 1 : 0,
            is_client_side: fClientInput.checked ? 1 : 0,
        };
        if (!body.name || !body.base_url || !body.model) {
            await aihShowAlert("Info", "Nom, URL et modèle sont requis", "info"); return;
        }
        try {
            if (editingId.value) {
                await _aihFetchApi(`presets/${editingId.value}`, { method: "PUT", body });
            } else {
                await _aihFetchApi("presets", { method: "POST", body });
            }
            editingId.value = null;
            formTitle.textContent = "Nouveau preset";
            fName.input.value = ""; fUrl.input.value = ""; fKey.input.value = "";
            fModel.value = ""; fGlobal.input.checked = false; fClientInput.checked = false;
            reloadPresets();
        } catch (e) {
            await aihShowAlert("Erreur", "Erreur sauvegarde : " + e.message, "error");
        }
    });
    btnRow.append(cancelBtn, saveBtn);
    form.appendChild(btnRow);
    container.appendChild(form);

    function fillForm(p) {
        editingId.value = p.id;
        fName.input.value = p.name || "";
        fUrl.input.value = p.base_url || "";
        fKey.input.value = ""; // On ne pré-remplit pas la clé pour la sécurité
        fModel.value = p.model || "";
        fGlobal.input.checked = !!p.is_global;
        fClientInput.checked = !!p.is_client_side;
        formTitle.textContent = "Modifier preset";
    }
}

function mkBtn(text, css, onClick) {
    const b = document.createElement("button");
    b.textContent = text;
    b.style.cssText = typeof css === "string" ? css : "";
    b.onclick = onClick;
    return b;
}

// ── Onglet Compte (URL serveur + clé API) ───────────────────────────

// ── Onglet Compte (URL serveur + clé API) ───────────────────────────

function renderCompteTab(container, cfg) {
    container.innerHTML = "";
    const section = document.createElement("div");
    section.style.cssText = _aihStyle.section;

    // Status du fichier de credentials
    const status = document.createElement("p");
    status.style.cssText = "margin:0 0 12px; font-size:11px; color:#888;";
    status.textContent = "Chargement...";
    section.appendChild(status);

    const lbl1 = document.createElement("label");
    lbl1.textContent = "URL du serveur";
    lbl1.style.cssText = _aihStyle.label;
    section.appendChild(lbl1);

    const inputUrl = document.createElement("input");
    inputUrl.type = "url";
    inputUrl.value = cfg.serverUrl || "https://kw.holaf.fr";
    inputUrl.style.cssText = _aihStyle.input;
    inputUrl.style.marginBottom = "12px";
    section.appendChild(inputUrl);

    const lbl2 = document.createElement("label");
    lbl2.textContent = "Clé API";
    lbl2.style.cssText = _aihStyle.label;
    section.appendChild(lbl2);

    const inputKey = document.createElement("input");
    inputKey.type = "password";
    inputKey.value = cfg.apiKey || "";
    inputKey.style.cssText = _aihStyle.input;
    inputKey.style.marginBottom = "4px";
    section.appendChild(inputKey);

    const hint = document.createElement("p");
    hint.textContent = "Générez votre clé sur le site web → Settings → Clé API";
    Object.assign(hint.style, { margin: "0 0 12px", fontSize: "11px", color: "#888" });
    section.appendChild(hint);

    const saveBtn = mkBtn("Sauvegarder", _aihStyle.btn(), async () => {
        saveBtn.disabled = true;
        saveBtn.textContent = "...";
        try {
            const resp = await fetch("/aih/credentials", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    api_key: inputKey.value.trim(),
                    server_url: inputUrl.value.trim(),
                }),
            });
            const data = await resp.json();
            if (data.status === "ok") {
                // Mettre a jour aussi localStorage pour le cache UI
                setConfig({
                    serverUrl: inputUrl.value.trim(),
                    apiKey: inputKey.value.trim(),
                });
                status.textContent = `✓ Sauvegardé dans ${data.path}`;
                status.style.color = "#4ade80";
                saveBtn.textContent = "✓ Sauvegardé";
            } else {
                status.textContent = `✗ Erreur : ${data.message || "inconnue"}`;
                status.style.color = "#ef4444";
                saveBtn.textContent = "Erreur";
            }
        } catch (err) {
            status.textContent = `✗ Erreur réseau : ${err.message}`;
            status.style.color = "#ef4444";
            saveBtn.textContent = "Erreur";
        } finally {
            saveBtn.disabled = false;
            setTimeout(() => { saveBtn.textContent = "Sauvegarder"; }, 2000);
        }
    });
    section.appendChild(saveBtn);
    container.appendChild(section);

    // Charger les credentials depuis le fichier (au cas ou localStorage est vide)
    fetch("/aih/credentials")
        .then(r => r.json())
        .then(data => {
            if (data.status === "ok") {
                if (data.server_url) inputUrl.value = data.server_url;
                if (data.api_key) inputKey.value = data.api_key;
                if (data.path) {
                    status.textContent = `Fichier : ${data.path}`;
                    status.style.color = "#888";
                }
                // Auto-migration : si le fichier n'existe pas mais que
                // localStorage a une cle, on migre silencieusement.
                if (!data.exists && cfg.apiKey) {
                    status.textContent = "⟳ Migration depuis localStorage...";
                    status.style.color = "#facc15";
                    return fetch("/aih/credentials", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            api_key: cfg.apiKey,
                            server_url: cfg.serverUrl || "https://kw.holaf.fr",
                        }),
                    }).then(r => r.json()).then(saveData => {
                        if (saveData.status === "ok") {
                            status.textContent = `✓ Migré depuis localStorage vers ${saveData.path}`;
                            status.style.color = "#4ade80";
                        } else {
                            status.textContent = `✗ Échec migration : ${saveData.message || "inconnu"}`;
                            status.style.color = "#ef4444";
                        }
                    });
                }
            } else {
                status.textContent = `✗ Impossible de lire le fichier : ${data.message || "inconnu"}`;
                status.style.color = "#ef4444";
            }
        })
        .catch(err => {
            status.textContent = `✗ Erreur de chargement : ${err.message}`;
            status.style.color = "#ef4444";
        });
}

// ── Update (git pull sur le repo local) ────────────────────────────

async function openUpdate() {
    // Modale d'attente
    const modal = aihOpenModalV2({
        title: "🔄 Update AIH",
        content: `
        <div style="padding:8px 0;">
            <p style="color:#ccc; font-size:13px; margin:0 0 12px;">
                Mise à jour du repo <code style="background:#1a1a1e; padding:1px 5px; border-radius:3px;">AIH_Tools</code> en cours...
            </p>
            <div id="aih-update-spinner" style="text-align:center; padding:20px;">
                <span style="display:inline-block; width:32px; height:32px; border:3px solid #444; border-top-color:#6366f1; border-radius:50%; animation:aih-spin 1s linear infinite;"></span>
            </div>
            <div id="aih-update-log" style="background:#1a1a1e; border:1px solid #333; border-radius:6px; padding:10px; font-family:monospace; font-size:11px; color:#aaa; max-height:280px; overflow-y:auto; white-space:pre-wrap; display:none;"></div>
        </div>
        <style>@keyframes aih-spin { to { transform: rotate(360deg); } }</style>
        `,
        width: "520px",
        height: "auto",
        minHeight: "250px",
        storageKey: null
    });

    const logEl = modal.body.querySelector("#aih-update-log");
    const spinnerEl = modal.body.querySelector("#aih-update-spinner");

    try {
        const resp = await fetch("/aih/update", { method: "POST" });
        const data = await resp.json();
        spinnerEl.style.display = "none";
        logEl.style.display = "block";
        logEl.textContent = data.log || "(pas de log)";

        if (data.status === "ok" && data.updated) {
            // Mise à jour effectuée : proposer de redémarrer
            const restartSection = document.createElement("div");
            restartSection.style.cssText = "margin-top:14px; padding:12px; background:#1a2e1a; border:1px solid #2d5a2d; border-radius:6px;";
            restartSection.innerHTML = `
                <p style="color:#4ade80; font-size:13px; margin:0 0 8px;">
                    ✓ Mise à jour installée. Un redémarrage de ComfyUI est nécessaire pour charger les nouveaux fichiers.
                </p>
                <div style="display:flex; gap:8px; justify-content:flex-end;">
                    <button id="aih-update-later" style="padding:6px 14px; border-radius:6px; border:1px solid #555; background:transparent; color:#ccc; cursor:pointer; font-size:12px;">Plus tard</button>
                    <button id="aih-update-restart" style="padding:6px 14px; border-radius:6px; border:none; background:#6366f1; color:white; cursor:pointer; font-size:12px; font-weight:600;">Redémarrer ComfyUI</button>
                </div>
            `;
            modal.body.appendChild(restartSection);

            modal.body.querySelector("#aih-update-later").onclick = () => modal.close();
            modal.body.querySelector("#aih-update-restart").onclick = async () => {
                const btn = modal.body.querySelector("#aih-update-restart");
                btn.disabled = true;
                btn.textContent = "Redémarrage...";
                // Remplacer le contenu de la modale par un message d'attente
                modal.body.innerHTML = `
                    <div style="text-align:center; padding:40px 20px;">
                        <div style="font-size:32px; margin-bottom:12px;">🔄</div>
                        <p style="color:#fff; font-size:14px; margin:0 0 6px; font-weight:600;">ComfyUI redémarre...</p>
                        <p style="color:#888; font-size:12px; margin:0;">Cette page se reconnectera automatiquement dans quelques secondes.</p>
                    </div>
                `;
                try {
                    await fetch("/aih/restart", { method: "POST" });
                } catch (e) {
                    // Normal : la connexion est coupée pendant le restart
                }
                // Tenter de reconnecter toutes les 2s
                let attempts = 0;
                const reconnectInterval = setInterval(() => {
                    attempts++;
                    if (attempts > 30) {
                        clearInterval(reconnectInterval);
                        modal.body.innerHTML = `
                            <div style="text-align:center; padding:40px 20px;">
                                <p style="color:#f87171; font-size:14px;">Le redémarrage prend plus longtemps que prévu.</p>
                                <p style="color:#888; font-size:12px;">Rechargez manuellement la page ComfyUI.</p>
                            </div>
                        `;
                        return;
                    }
                    fetch("/aih/update", { method: "POST" })
                        .then(r => {
                            if (r.ok || r.status === 400) {
                                clearInterval(reconnectInterval);
                                location.reload();
                            }
                        })
                        .catch(() => {});
                }, 2000);
            };
        } else if (data.status === "ok" && !data.updated) {
            // Déjà à jour
            const okSection = document.createElement("div");
            okSection.style.cssText = "margin-top:14px; padding:12px; background:#1a2e1a; border:1px solid #2d5a2d; border-radius:6px; text-align:center;";
            okSection.innerHTML = `<p style="color:#4ade80; font-size:13px; margin:0 0 8px;">✓ Vous êtes déjà à jour.</p>`;
            const closeBtn = document.createElement("button");
            closeBtn.textContent = "Fermer";
            Object.assign(closeBtn.style, { padding: "6px 14px", borderRadius: "6px", border: "1px solid #555", background: "transparent", color: "#ccc", cursor: "pointer", fontSize: "12px" });
            closeBtn.onclick = () => modal.close();
            okSection.appendChild(closeBtn);
            modal.body.appendChild(okSection);
        } else {
            // Erreur
            const errSection = document.createElement("div");
            errSection.style.cssText = "margin-top:14px; padding:12px; background:#2e1a1a; border:1px solid #5a2d2d; border-radius:6px; text-align:center;";
            errSection.innerHTML = `<p style="color:#f87171; font-size:13px; margin:0 0 8px;">✗ ${data.message || "Erreur inconnue"}</p>`;
            const closeBtn = document.createElement("button");
            closeBtn.textContent = "Fermer";
            Object.assign(closeBtn.style, { padding: "6px 14px", borderRadius: "6px", border: "1px solid #555", background: "transparent", color: "#ccc", cursor: "pointer", fontSize: "12px" });
            closeBtn.onclick = () => modal.close();
            errSection.appendChild(closeBtn);
            modal.body.appendChild(errSection);
        }
    } catch (err) {
        spinnerEl.style.display = "none";
        logEl.style.display = "block";
        logEl.textContent = "Erreur réseau : " + err.message;
    }
}