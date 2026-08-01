/**
 * AIH Debug — Système de logging centralisé pour l'extension AI-Helper.
 *
 * Fournit :
 *   - window.AIH.__logs   : buffer FIFO (max 500 entrées)
 *   - window.AIH.__log(scope, msg, data) : enregistre + log console
 *   - window.AIH.__clearLogs() : vide le buffer
 *   - window.AIH.__openDebug() : ouvre la fenêtre de debug (bouton "Debug" du menu AIH)
 *
 * Chargé automatiquement par ComfyUI (WEB_DIRECTORY = "web"). Les fichiers
 * widget instrumentés appellent window.AIH.__log(...) aux points critiques
 * (onNodeCreated, onConfigure, restore, sync, delayedRestore...).
 *
 * Ordre de chargement : 03_aih_shared.js → 04_aih_widget_base.js → aih_debug.js
 * → widgets (aih_elements_widget.js, aih_enhance_widget.js, ...) → aih_menu.js.
 * window.AIH.__log est donc toujours défini quand les widgets s'exécutent.
 */
(function () {
    "use strict";

    const AIH = (window.AIH = window.AIH || {});

    // ── Buffer de logs (max 500 entrées, FIFO) ──
    AIH.__logs = [];
    const MAX_LOGS = 500;
    const MAX_DATA_LEN = 4000; // troncature des données STOCKÉES (la console garde tout)

    function serializeData(data) {
        if (data === undefined) return "";
        let str;
        if (typeof data === "string") str = data;
        else if (data instanceof Error) str = data.message || String(data);
        else {
            try { str = JSON.stringify(data); }
            catch (e) { str = String(data); }
        }
        if (str.length > MAX_DATA_LEN) {
            return str.substring(0, MAX_DATA_LEN) + "… (+" + (str.length - MAX_DATA_LEN) + " chars)";
        }
        return str;
    }

    /**
     * Enregistre une entrée de log (scope + message + data optionnelle).
     * Miroir console.log : console.log reçoit la data complète, le buffer
     * garde une version tronquée pour rester léger.
     */
    AIH.__log = function (scope, msg, data) {
        const entry = {
            t: new Date().toISOString(),
            scope: scope,
            msg: msg,
            data: data !== undefined ? serializeData(data) : "",
        };
        AIH.__logs.push(entry);
        if (AIH.__logs.length > MAX_LOGS) AIH.__logs.shift();
        try {
            console.log("[AIH][" + scope + "] " + msg, data !== undefined ? data : "");
        } catch (e) { /* non bloquant */ }
    };

    /** Vide le buffer de logs. */
    AIH.__clearLogs = function () {
        AIH.__logs.length = 0;
    };

    function formatEntry(l) {
        return "[" + l.t + "][" + l.scope + "] " + l.msg + (l.data ? " | " + l.data : "");
    }

    /**
     * Ouvre la fenêtre de debug : logs hyper détaillés + bouton copier.
     * Fenêtre centrée, refresh automatique toutes les 500ms, filtre par scope,
     * bouton "Vider", fermeture par ✕ ou Échap.
     */
    AIH.__openDebug = function () {
        // Déjà ouvert → on garde la fenêtre existante
        if (document.getElementById("aih-debug-overlay")) return;

        const overlay = document.createElement("div");
        overlay.id = "aih-debug-overlay";
        Object.assign(overlay.style, {
            position: "fixed", top: "0", left: "0", right: "0", bottom: "0",
            background: "rgba(0,0,0,0.75)", zIndex: "99999",
            display: "flex", alignItems: "center", justifyContent: "center",
        });

        const box = document.createElement("div");
        Object.assign(box.style, {
            background: "#1a1a1e", color: "#ccc", borderRadius: "8px",
            border: "1px solid #333",
            padding: "12px", width: "92%", maxWidth: "1100px", height: "82%",
            display: "flex", flexDirection: "column",
            fontFamily: "monospace", fontSize: "11px",
        });

        // ── Header ──
        const header = document.createElement("div");
        Object.assign(header.style, {
            display: "flex", justifyContent: "space-between", alignItems: "center",
            gap: "8px", marginBottom: "8px", flexWrap: "wrap",
        });

        const title = document.createElement("span");
        title.textContent = "🔍 AIH Debug Logs";
        title.style.cssText = "font-size:14px;font-weight:bold;color:#fff;";

        const countBadge = document.createElement("span");
        countBadge.style.cssText = "background:#2a2a2e;color:#818cf8;border-radius:4px;padding:1px 8px;font-size:11px;";

        const titleWrap = document.createElement("span");
        titleWrap.style.cssText = "display:flex;align-items:center;gap:8px;";
        titleWrap.appendChild(title);
        titleWrap.appendChild(countBadge);

        const btnStyle = "padding:4px 10px;border-radius:4px;border:1px solid #555;background:#2a2a2e;color:#ccc;cursor:pointer;font-size:11px;font-family:inherit;";

        const copyBtn = document.createElement("button");
        copyBtn.textContent = "📋 Copier les logs";
        copyBtn.style.cssText = btnStyle;
        copyBtn.style.borderColor = "#6366f1";
        copyBtn.style.color = "#818cf8";

        const clearBtn = document.createElement("button");
        clearBtn.textContent = "🗑 Vider";
        clearBtn.style.cssText = btnStyle;

        const closeBtn = document.createElement("button");
        closeBtn.textContent = "✕ Fermer";
        closeBtn.style.cssText = btnStyle;

        const actions = document.createElement("div");
        Object.assign(actions.style, { display: "flex", gap: "6px", alignItems: "center" });
        actions.appendChild(copyBtn);
        actions.appendChild(clearBtn);
        actions.appendChild(closeBtn);

        header.appendChild(titleWrap);
        header.appendChild(actions);
        box.appendChild(header);

        // ── Filtre par scope ──
        const filterRow = document.createElement("div");
        Object.assign(filterRow.style, {
            display: "flex", gap: "6px", alignItems: "center", marginBottom: "8px",
            flex: "0 0 auto",
        });
        const filterLabel = document.createElement("span");
        filterLabel.textContent = "Filtre scope:";
        filterLabel.style.cssText = "color:#888;";
        const filterInput = document.createElement("input");
        filterInput.type = "text";
        filterInput.placeholder = "ex: Elements, Enhance, restore, onConfigure...";
        Object.assign(filterInput.style, {
            flex: "1", padding: "3px 8px", borderRadius: "4px",
            border: "1px solid #555", background: "#111", color: "#ccc", fontSize: "11px",
            fontFamily: "inherit",
        });
        filterRow.appendChild(filterLabel);
        filterRow.appendChild(filterInput);
        box.appendChild(filterRow);

        // ── Zone de logs ──
        const logArea = document.createElement("pre");
        Object.assign(logArea.style, {
            flex: "1", overflow: "auto", background: "#111", color: "#0f0",
            padding: "8px", borderRadius: "4px", margin: "0",
            whiteSpace: "pre-wrap", wordBreak: "break-all",
        });

        function currentFilter() {
            return filterInput.value.trim().toLowerCase();
        }
        function filteredLogs() {
            const f = currentFilter();
            if (!f) return AIH.__logs;
            return AIH.__logs.filter(function (l) {
                return (l.scope + " " + l.msg + " " + l.data).toLowerCase().indexOf(f) >= 0;
            });
        }
        function isNearBottom() {
            return logArea.scrollHeight - logArea.scrollTop - logArea.clientHeight < 40;
        }
        function renderLogs() {
            const keepScroll = isNearBottom();
            const arr = filteredLogs();
            countBadge.textContent = arr.length + "/" + AIH.__logs.length;
            logArea.textContent = arr.map(formatEntry).join("\n");
            if (keepScroll) logArea.scrollTop = logArea.scrollHeight;
        }

        filterInput.addEventListener("input", function () {
            logArea.scrollTop = logArea.scrollHeight;
            renderLogs();
        });

        renderLogs();
        box.appendChild(logArea);
        overlay.appendChild(box);
        document.body.appendChild(overlay);

        // ── Copier (clipboard avec fallback execCommand) ──
        function fallbackCopy(text) {
            const ta = document.createElement("textarea");
            ta.value = text;
            ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;";
            document.body.appendChild(ta);
            ta.select();
            try { document.execCommand("copy"); } catch (e) {}
            ta.remove();
        }

        copyBtn.onclick = function () {
            const text = filteredLogs().map(formatEntry).join("\n");
            const done = function () {
                copyBtn.textContent = "✅ Copié !";
                setTimeout(function () { copyBtn.textContent = "📋 Copier les logs"; }, 1500);
            };
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(done).catch(function () {
                    fallbackCopy(text);
                    done();
                });
            } else {
                fallbackCopy(text);
                done();
            }
        };

        // ── Vider ──
        clearBtn.onclick = function () {
            AIH.__clearLogs();
            renderLogs();
        };

        // ── Fermer (✕ ou Échap) ──
        const close = function () {
            overlay.remove();
        };
        closeBtn.onclick = close;
        overlay.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
        overlay.tabIndex = -1;
        overlay.focus();

        // ── Refresh automatique toutes les 500ms pendant que la fenêtre est ouverte ──
        const interval = setInterval(function () {
            if (!document.getElementById("aih-debug-overlay")) { clearInterval(interval); return; }
            renderLogs();
        }, 500);
    };
})();
