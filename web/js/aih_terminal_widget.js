/**
 * AIH Terminal — Floating panel (singleton) for ComfyUI.
 *
 * Adapted from CUI-Holaf-Utils/js/holaf_terminal.js (Holaf, 2025).
 *
 * Architecture :
 *   - Singleton : un seul panel existe, accessible via le menu AIH → 💻 Terminal.
 *   - Floating panel : positionné en `position: fixed`, draggable, redimensionnable.
 *   - Persistant : taille / position / fullscreen / thème / font-size / dernière
 *     commande sont sauvegardés dans localStorage.aih_terminal_settings.
 *   - Conflit-safe avec CUI-Holaf-Utils : route `/aih/terminal` (pas /holaf/),
 *     expose `window.aihTerminal` (pas `window.holafTerminal`).
 *   - PAS DE MOT DE PASSE : usage local uniquement. Bandeau d'avertissement
 *     toujours visible.
 */
AIH.waitForApp(function(app) {

    // ════════════════════════════════════════════════════════════════════
    //  Helpers
    // ════════════════════════════════════════════════════════════════════

    function loadScript(src) {
        return new Promise((resolve, reject) => {
            const existing = document.querySelector(`script[src="${src}"]`);
            if (existing && existing.dataset.loaded) { resolve(); return; }
            if (existing) {
                existing.addEventListener('load', () => { existing.dataset.loaded = "1"; resolve(); }, { once: true });
                existing.addEventListener('error', () => reject(new Error("load failed: " + src)), { once: true });
                return;
            }
            const s = document.createElement('script');
            s.src = src;
            s.onload = () => { s.dataset.loaded = "1"; resolve(); };
            s.onerror = () => reject(new Error("load failed: " + src));
            document.head.appendChild(s);
        });
    }

    async function ensureXtermLoaded() {
        if (window.Terminal && window.FitAddon) return;
        // Try AIH first, then fall back to Holaf if not installed.
        const sources = [
            "extensions/AIH_Tools/js/xterm.js",
            "extensions/AIH_Tools/js/xterm-addon-fit.js",
            "extensions/ComfyUI-Holaf-Utilities/js/xterm.js",
            "extensions/ComfyUI-Holaf-Utilities/js/xterm-addon-fit.js",
        ];
        for (const src of sources) {
            try {
                if (src.endsWith("xterm.js") && !window.Terminal) {
                    await loadScript(src);
                } else if (src.endsWith("xterm-addon-fit.js") && !window.FitAddon) {
                    await loadScript(src);
                }
            } catch (e) { /* try next */ }
        }
        if (!window.Terminal || !window.FitAddon) {
            throw new Error("xterm.js introuvable (ni dans AIH, ni dans Holaf).");
        }
    }

    // ════════════════════════════════════════════════════════════════════
    //  Settings (localStorage)
    // ════════════════════════════════════════════════════════════════════

    const STORAGE_KEY = "aih_terminal_settings";
    const DEFAULTS = {
        fontSize: 13,
        theme: "dark",
        panel_is_fullscreen: false,
    };
    const THEMES = {
        dark: { background: "#0a0a0a", foreground: "#e0e0e0", cursor: "#7f7", selectionBackground: "#444" },
        light: { background: "#fafafa", foreground: "#1a1a1a", cursor: "#27c93f", selectionBackground: "#b4d5fe" },
        solarized: { background: "#002b36", foreground: "#839496", cursor: "#93a1a1", selectionBackground: "#073642" },
        monokai: { background: "#272822", foreground: "#f8f8f2", cursor: "#f8f8f0", selectionBackground: "#49483e" },
    };

    function loadSettings() {
        try {
            const s = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
            return { ...DEFAULTS, ...s };
        } catch { return { ...DEFAULTS }; }
    }
    let saveTimer = null;
    function saveSettings(patch) {
        aihTerminal.settings = { ...aihTerminal.settings, ...patch };
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
            try { localStorage.setItem(STORAGE_KEY, JSON.stringify(aihTerminal.settings)); } catch {}
        }, 200);
    }

    // ════════════════════════════════════════════════════════════════════
    //  Panel singleton
    // ════════════════════════════════════════════════════════════════════

    const aihTerminal = {
        panelEl: null,
        contentEl: null,
        xtermContainer: null,
        terminal: null,
        fitAddon: null,
        socket: null,
        isConnected: false,
        settings: loadSettings(),

        // ── Public API ────────────────────────────────────────────────
        toggle() { this.isOpen() ? this.hide() : this.show(); },
        isOpen() { return !!this.panelEl && this.panelEl.style.display === "flex"; },

        show() {
            if (!this.panelEl) {
                this._createPanel();
            } else {
                this.panelEl.style.display = "flex";
                if (this._m) this._m.bringToFront();
            }
            this._connectIfNeeded();
        },

        hide() {
            if (this.panelEl) this.panelEl.style.display = "none";
        },

        // ── Build DOM (once) ─────────────────────────────────────────
        _createPanel() {
            var self = this;

            // ── Créer la modale v2 ──────────────────────────────────────────
            var m = aihOpenModalV2({
                title: "💻 AIH Terminal",
                width: "700px",
                height: "480px",
                minWidth: "400px",
                minHeight: "200px",
                storageKey: "aih-modal-terminal",
                persistSize: true,
                persistPos: true,
                closeOnEscape: false,
                className: "aih-terminal-modal",
                onClose: function () {
                    panel.style.display = "none";
                },
            });

            var panel = m.modal;
            panel.id = "aih-terminal-panel";

            // ── Styliser le body v2 pour le terminal ────────────────────────
            var body = m.body;
            Object.assign(body.style, {
                padding: "0",
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
                background: "#1a1a1e",
            });

            // ── Header : ajouter les contrôles custom dans header-right ─────
            var headerRight = panel.querySelector(".aih-modal-header-right");
            if (headerRight) {
                // Theme selector
                var themeSelect = document.createElement("select");
                Object.assign(themeSelect.style, {
                    padding: "2px 4px", background: "#3a3a3e", color: "#ccc",
                    border: "1px solid #555", borderRadius: "3px", fontSize: "11px",
                });
                Object.keys(THEMES).forEach(function (t) {
                    var o = document.createElement("option");
                    o.value = t;
                    o.textContent = t;
                    if (t === self.settings.theme) o.selected = true;
                    themeSelect.appendChild(o);
                });
                themeSelect.addEventListener("change", function () {
                    self.settings.theme = this.value;
                    saveSettings({ theme: this.value });
                    self._applyTheme();
                });
                headerRight.appendChild(themeSelect);

                // Decrease font size
                var fsDown = document.createElement("button");
                fsDown.textContent = "A-";
                fsDown.title = "Réduire la police";
                Object.assign(fsDown.style, {
                    background: "none", border: "none", color: "#999",
                    cursor: "pointer", fontSize: "11px", padding: "0 4px",
                });
                fsDown.onclick = function () {
                    self.settings.fontSize = Math.max(8, self.settings.fontSize - 1);
                    saveSettings({ fontSize: self.settings.fontSize });
                    if (self.terminal) {
                        self.terminal.options.fontSize = self.settings.fontSize;
                        self._fit();
                    }
                };
                headerRight.appendChild(fsDown);

                // Increase font size
                var fsUp = document.createElement("button");
                fsUp.textContent = "A+";
                fsUp.title = "Agrandir la police";
                Object.assign(fsUp.style, {
                    background: "none", border: "none", color: "#999",
                    cursor: "pointer", fontSize: "11px", padding: "0 4px",
                });
                fsUp.onclick = function () {
                    self.settings.fontSize = Math.min(28, self.settings.fontSize + 1);
                    saveSettings({ fontSize: self.settings.fontSize });
                    if (self.terminal) {
                        self.terminal.options.fontSize = self.settings.fontSize;
                        self._fit();
                    }
                };
                headerRight.appendChild(fsUp);

                // Fullscreen toggle
                var fsBtn = document.createElement("button");
                fsBtn.textContent = "⛶";
                fsBtn.title = "Plein écran";
                Object.assign(fsBtn.style, {
                    background: "none", border: "none", color: "#999",
                    cursor: "pointer", fontSize: "11px", padding: "0 4px",
                });
                fsBtn.onclick = function () { self._toggleFullscreen(); };
                headerRight.appendChild(fsBtn);
            }

            // ── Remplacer le bouton close v2 (cacher, pas détruire) ────────
            var closeBtn = panel.querySelector(".aih-modal-close");
            if (closeBtn) {
                var newCloseBtn = closeBtn.cloneNode(true);
                closeBtn.parentNode.replaceChild(newCloseBtn, closeBtn);
                newCloseBtn.addEventListener("click", function () {
                    panel.style.display = "none";
                });
            }

            // ── Body : contenu du terminal ──────────────────────────────────
            // Warning banner
            var warning = document.createElement("div");
            Object.assign(warning.style, {
                background: "#5a1a1a", color: "#ffcccc",
                padding: "4px 8px", fontSize: "10px", lineHeight: "1.3",
                borderBottom: "1px solid #333",
            });
            warning.innerHTML = "⚠️ <b>No password.</b> Anyone on this network can run shell commands. Localhost only.";
            body.appendChild(warning);

            // xterm container
            var xtermContainer = document.createElement("div");
            Object.assign(xtermContainer.style, {
                flex: "1",
                background: (THEMES[self.settings.theme] || THEMES.dark).background,
                padding: "4px", overflow: "hidden", boxSizing: "border-box",
            });
            body.appendChild(xtermContainer);
            self.xtermContainer = xtermContainer;
            self.contentEl = body;

            // Status bar
            var statusBar = document.createElement("div");
            Object.assign(statusBar.style, {
                padding: "4px 8px", background: "#2a2a2e", color: "#888",
                fontSize: "11px", display: "flex", gap: "8px", alignItems: "center",
                borderTop: "1px solid #444",
            });
            var statusText = document.createElement("span");
            statusText.textContent = "Disconnected";
            statusText.id = "aih-terminal-status";
            statusBar.appendChild(statusText);
            var spacer = document.createElement("span");
            spacer.style.flex = "1";
            statusBar.appendChild(spacer);
            var connectBtn = document.createElement("button");
            connectBtn.textContent = "🔌 Connect";
            Object.assign(connectBtn.style, {
                padding: "2px 10px", background: "#6366f1", color: "white",
                border: "none", borderRadius: "3px", fontSize: "11px",
                fontWeight: "600", cursor: "pointer",
            });
            connectBtn.onmouseenter = function () { connectBtn.style.background = "#5558e8"; };
            connectBtn.onmouseleave = function () { connectBtn.style.background = "#6366f1"; };
            connectBtn.onclick = function () { self._toggleConnection(); };
            statusBar.appendChild(connectBtn);
            body.appendChild(statusBar);
            self._statusText = statusText;
            self._connectBtn = connectBtn;

            // ── Override m.close : cacher, pas détruire ────────────────────
            m.close = function () {
                panel.style.display = "none";
            };

            // ── Double-click header = fullscreen ────────────────────────────
            var header = m.header;
            header.ondblclick = function (e) {
                if (e.target.classList.contains("aih-modal-title") || e.target === header) {
                    self._toggleFullscreen();
                }
            };

            // ── Références ──────────────────────────────────────────────────
            self.panelEl = panel;
            self._m = m;

            // ── Appliquer le thème initial ──────────────────────────────────
            self._applyTheme();
        },

        // ── Fullscreen ───────────────────────────────────────────────
        _toggleFullscreen() {
            var panel = this.panelEl;
            if (!panel) return;

            this.settings.panel_is_fullscreen = !this.settings.panel_is_fullscreen;
            saveSettings({ panel_is_fullscreen: this.settings.panel_is_fullscreen });

            if (this.settings.panel_is_fullscreen) {
                panel.style.left = "0";
                panel.style.top = "0";
                panel.style.width = "100vw";
                panel.style.height = "100vh";
                panel.style.transform = "none";
                panel.style.borderRadius = "0";
                panel.style.border = "none";
            } else {
                // Reset : la v2 reprend le contrôle via ses valeurs inline
                panel.style.left = "";
                panel.style.top = "";
                panel.style.width = "";
                panel.style.height = "";
                panel.style.transform = "";
                panel.style.borderRadius = "";
                panel.style.border = "";
            }
            setTimeout(function () { this._fit(); }.bind(this), 50);
        },

        // ── xterm + WebSocket ─────────────────────────────────────────
        _setStatus(text, connected) {
            if (this._statusText) {
                this._statusText.textContent = text;
                this._statusText.style.color = connected ? "#7f7" : "#888";
            }
            if (this._connectBtn) {
                this._connectBtn.textContent = connected ? "🔌 Disconnect" : "🔌 Connect";
                this._connectBtn.style.background = connected ? "#a33" : "#6366f1";
            }
        },

        _applyTheme() {
            if (!this.terminal) return;
            const theme = THEMES[this.settings.theme] || THEMES.dark;
            this.terminal.options.theme = theme;
            if (this.xtermContainer) this.xtermContainer.style.background = theme.background;
        },

        _fit() {
            if (!this.fitAddon) return;
            try {
                this.fitAddon.fit();
            } catch {}
            // Propager la nouvelle taille au PTY serveur, sinon le shell
                // reste en 80x24 et les programmes fullscreen (mc, vim...)
                // s'affichent mal / avec des colonnes fantomes a droite.
            this._sendResizeToServer();
        },

        _sendResizeToServer() {
            if (!this.fitAddon || !this.socket || this.socket.readyState !== WebSocket.OPEN) return;
            try {
                const dims = this.fitAddon.proposeDimensions();
                if (!dims) return;
                // xterm.js renvoie {cols, rows}; le backend attend [rows, cols]
                this.socket.send(JSON.stringify({
                    resize: [dims.rows, dims.cols],
                }));
            } catch (e) {
                console.warn("[AIH Terminal] proposeDimensions failed:", e);
            }
        },

        async _connectIfNeeded() {
            // Lazy-init xterm + auto-connect
            if (!this.terminal) {
                try {
                    await ensureXtermLoaded();
                } catch (e) {
                    this._setStatus("xterm load failed", false);
                    console.error("[AIH Terminal]", e);
                    return;
                }
                this.terminal = new window.Terminal({
                    cursorBlink: true,
                    fontSize: this.settings.fontSize,
                    fontFamily: "monospace",
                    theme: THEMES[this.settings.theme] || THEMES.dark,
                    rows: 24,
                });
                this.fitAddon = new window.FitAddon.FitAddon();
                this.terminal.loadAddon(this.fitAddon);
                this.terminal.open(this.xtermContainer);

                // Refit apres que la flexbox ait calcule sa taille finale.
                // requestAnimationFrame peut ne pas suffire si la page
                // est en train de charger (fonts, CSS, layout async).
                // On force plusieurs fits sur quelques ticks.
                const refitSequence = () => {
                    [0, 50, 150, 400].forEach(delay => {
                        setTimeout(() => this._fit(), delay);
                    });
                };
                // Attendre 2 frames pour etre sur que le layout est pose
                requestAnimationFrame(() => requestAnimationFrame(refitSequence));

                this.terminal.onData(data => {
                    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
                        this.socket.send(data);
                    }
                });
                this.terminal.attachCustomKeyEventHandler(e => {
                    if (e.ctrlKey && (e.key === 'c' || e.key === 'C') && e.type === 'keydown') {
                        if (this.terminal.hasSelection()) {
                            try { navigator.clipboard.writeText(this.terminal.getSelection()); } catch {}
                            return false;
                        }
                    }
                    if (e.ctrlKey && (e.key === 'v' || e.key === 'V') && e.type === 'keydown') {
                        try {
                            navigator.clipboard.readText().then(text => {
                                if (text && this.terminal) this.terminal.paste(text);
                            });
                        } catch {}
                        return false;
                    }
                    return true;
                });

        // Resize on container resize (window, drag, fullscreen, etc.)
        // ResizeObserver fire à chaque changement de taille du xtermContainer,
        // que ce soit par resize de la fenêtre, par drag/redim du panel, ou
        // par fullscreen toggle. On debounce légèrement pour éviter les
        // appels en rafale pendant un drag rapide.
        if (window.ResizeObserver && this.xtermContainer) {
            let fitRaf = null;
            this._resizeObserver = new ResizeObserver(() => {
                if (fitRaf) return;
                fitRaf = requestAnimationFrame(() => {
                    fitRaf = null;
                    this._fit();
                });
            });
            this._resizeObserver.observe(this.xtermContainer);
        }

        // Ecouter aussi window.resize : certains changements CSS (media
        // queries, scrollbars qui apparaissent/disparaissent) modifient la
        // taille du container sans qu'il y ait un vrai resize. Le
        // ResizeObserver devrait normalement suffire, mais window.resize
        // est un filet de securite pour les cas tordus.
        if (!this._onWindowResize) {
            this._onWindowResize = () => this._fit();
            window.addEventListener("resize", this._onWindowResize);
        }

        // Open WebSocket
        this._openSocket();
            } else if (!this.isConnected) {
                this._openSocket();
            } else {
                setTimeout(() => this._fit(), 30);
            }
        },
        _openSocket() {
            if (this.socket && this.socket.readyState === WebSocket.OPEN) return;
            this._setStatus("Connecting...", false);
            const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
            const url = `${protocol}//${window.location.host}/aih/terminal`;
            this.socket = new WebSocket(url);
            this.socket.binaryType = "arraybuffer";

            this.socket.onopen = () => {
                this.isConnected = true;
                this._setStatus("Connected", true);
                requestAnimationFrame(() => {
                    this._fit();
                    if (this.terminal) this.terminal.focus();
                });
            };
            this.socket.onmessage = (event) => {
                if (!this.terminal) return;
                try {
                    if (event.data instanceof ArrayBuffer) {
                        this.terminal.write(new Uint8Array(event.data));
                    } else {
                        this.terminal.write(event.data);
                    }
                } catch (e) { console.warn("[AIH Terminal] write error:", e); }
            };
            this.socket.onclose = () => {
                this.isConnected = false;
                this._setStatus("Disconnected", false);
                if (this.terminal) {
                    try { this.terminal.writeln("\r\n--- CONNECTION CLOSED ---"); } catch {}
                }
            };
            this.socket.onerror = (e) => {
                console.error("[AIH Terminal] WebSocket error:", e);
                if (this.terminal) {
                    try { this.terminal.writeln("\r\n--- CONNECTION ERROR ---"); } catch {}
                }
            };
        },

        _disconnect() {
            try { if (this.socket) this.socket.close(); } catch {}
            this.socket = null;
            this.isConnected = false;
            this._setStatus("Disconnected", false);
        },

        _toggleConnection() {
            if (this.isConnected) this._disconnect();
            else this._openSocket();
        },
    };

    // ════════════════════════════════════════════════════════════════════
    //  Register
    // ════════════════════════════════════════════════════════════════════

    // Expose on window so the menu (aih_menu.js) can call it
    window.aihTerminal = aihTerminal;

    app.registerExtension({
        name: "AIH.Terminal.Panel",
        async setup() {
            console.log("[AIH Terminal] Panel ready. Access via AIH menu → 💻 Terminal.");
        },
    });
});
