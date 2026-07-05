/**
 * FR.IA Modal v2 — Système de fenêtres flottantes pour extensions ComfyUI.
 *
 * Préfixé 01_ pour garantir le chargement avant les autres fria_*.js.
 * Remplace l'ancien 00_fria_modal.js (supprimé).
 * et avant les autres fria_*.js (qui peuvent utiliser friaOpenModalV2).
 *
 * Migration depuis friaOpenModal (v1) :
 *   var m = friaOpenModal("Titre", "<p>HTML</p>", "440px");
 *   → var m = friaOpenModalV2({ title: "Titre", content: "<p>HTML</p>", width: "440px" });
 *
 * Fonctions exportées sur window :
 *   - friaOpenModalV2(options)   → { modal, body, header, close, setTitle, setBody, bringToFront }
 *   - friaShowAlert(title, msg, type)    → Promise<void>
 *   - friaShowConfirm(title, msg)        → Promise<boolean>
 *   - friaShowPrompt(title, msg, ph)     → Promise<string|null>
 *
 * Dépendances : aucune (standalone). CSS auto-injecté au premier chargement.
 */
(function () {
    "use strict";

    // ─── Injection CSS (une seule fois) ──────────────────────────────────────────
    var _cssInjected = false;
    function _friaModalInjectCSS() {
        if (_cssInjected) return;
        _cssInjected = true;
        var style = document.createElement("style");
        style.textContent = [
            /* Conteneur racine */
            ".fria-modal {",
            "  position: fixed;",
            "  display: flex;",
            "  flex-direction: column;",
            "  background: #2a2a2e;",
            "  border: 1px solid #444;",
            "  border-radius: 12px;",
            "  box-shadow: 0 16px 48px rgba(0,0,0,0.6);",
            "  overflow: hidden;",
            "  min-width: 280px;",
            "  min-height: 120px;",
            "}",
            /* Modale active (premier plan) */
            ".fria-modal.active {",
            "  border-color: #6366f1;",
            "  box-shadow: 0 16px 48px rgba(99,102,241,0.15);",
            "}",
            ".fria-modal.active .fria-modal-header {",
            "  background: #38383d;",
            "}",
            /* Header */
            ".fria-modal-header {",
            "  display: flex;",
            "  align-items: center;",
            "  padding: 10px 16px;",
            "  cursor: grab;",
            "  user-select: none;",
            "  border-bottom: 1px solid #444;",
            "  background: #2a2a2e;",
            "  flex-shrink: 0;",
            "}",
            ".fria-modal-header:active {",
            "  cursor: grabbing;",
            "}",
            /* Titre */
            ".fria-modal-title {",
            "  flex: 1;",
            "  font-size: 14px;",
            "  font-weight: 600;",
            "  color: #fff;",
            "  overflow: hidden;",
            "  text-overflow: ellipsis;",
            "  white-space: nowrap;",
            "}",
            /* Zone droite du header (boutons supplémentaires) */
            ".fria-modal-header-right {",
            "  display: flex;",
            "  align-items: center;",
            "  gap: 4px;",
            "  margin-right: 8px;",
            "}",
            /* Bouton fermer */
            ".fria-modal-close {",
            "  background: none;",
            "  border: none;",
            "  color: #999;",
            "  cursor: pointer;",
            "  font-size: 16px;",
            "  padding: 0 4px;",
            "  line-height: 1;",
            "  flex-shrink: 0;",
            "}",
            ".fria-modal-close:hover {",
            "  color: #f87171;",
            "}",
            /* Body */
            ".fria-modal-body {",
            "  flex: 1;",
            "  padding: 16px;",
            "  overflow-y: auto;",
            "  overflow-x: hidden;",
            "  color: #fff;",
            "  font-size: 13px;",
            "  line-height: 1.5;",
            "}",
            /* Poignée de redimensionnement */
            ".fria-modal-resize-handle {",
            "  position: absolute;",
            "  right: 0;",
            "  bottom: 0;",
            "  width: 14px;",
            "  height: 14px;",
            "  cursor: nwse-resize;",
            "  background: transparent;",
            "  z-index: 1;",
            "}",
            /* Style pour les alertes / confirms */
            ".fria-modal-actions {",
            "  display: flex;",
            "  justify-content: flex-end;",
            "  gap: 8px;",
            "  margin-top: 16px;",
            "}",
            ".fria-modal-btn {",
            "  padding: 6px 16px;",
            "  border-radius: 6px;",
            "  border: 1px solid #555;",
            "  background: #3a3a3e;",
            "  color: #fff;",
            "  cursor: pointer;",
            "  font-size: 13px;",
            "  transition: background 0.15s, border-color 0.15s;",
            "}",
            ".fria-modal-btn:hover {",
            "  background: #4a4a4e;",
            "  border-color: #777;",
            "}",
            ".fria-modal-btn-primary {",
            "  background: #3b82f6;",
            "  border-color: #3b82f6;",
            "  color: #fff;",
            "}",
            ".fria-modal-btn-primary:hover {",
            "  background: #2563eb;",
            "  border-color: #2563eb;",
            "}",
            ".fria-modal-btn-danger {",
            "  background: #ef4444;",
            "  border-color: #ef4444;",
            "  color: #fff;",
            "}",
            ".fria-modal-btn-danger:hover {",
            "  background: #dc2626;",
            "  border-color: #dc2626;",
            "}",
            /* Alert types */
            ".fria-modal-icon {",
            "  font-size: 24px;",
            "  margin-bottom: 8px;",
            "  text-align: center;",
            "}",
            ".fria-modal-icon-info { color: #3b82f6; }",
            ".fria-modal-icon-success { color: #22c55e; }",
            ".fria-modal-icon-error { color: #ef4444; }",
            ".fria-modal-icon-warning { color: #f59e0b; }",
            /* Input dans prompt */
            ".fria-modal-input {",
            "  width: 100%;",
            "  padding: 8px 10px;",
            "  border-radius: 6px;",
            "  border: 1px solid #555;",
            "  background: #1e1e22;",
            "  color: #fff;",
            "  font-size: 13px;",
            "  outline: none;",
            "  box-sizing: border-box;",
            "  margin-top: 8px;",
            "}",
            ".fria-modal-input:focus {",
            "  border-color: #3b82f6;",
            "}",
            /* Message text */
            ".fria-modal-message {",
            "  color: #ccc;",
            "  font-size: 13px;",
            "  line-height: 1.5;",
            "}",
        ].join("\n");
        document.head.appendChild(style);
    }

    // ─── Z-index Stacking ────────────────────────────────────────────────────────
    var _BASE_Z = 90000;
    if (typeof window._friaModalZCounter === "undefined") {
        window._friaModalZCounter = 0;
    }

    function _friaModalNextZ() {
        window._friaModalZCounter += 1;
        if (window._friaModalZCounter > 50000) {
            // Reset : rescanner toutes les modales existantes
            var allModals = document.querySelectorAll(".fria-modal");
            window._friaModalZCounter = 0;
            for (var i = 0; i < allModals.length; i++) {
                window._friaModalZCounter += 1;
                allModals[i].style.zIndex = _BASE_Z + window._friaModalZCounter;
            }
        }
        return _BASE_Z + window._friaModalZCounter;
    }

    // ─── Persistance localStorage ────────────────────────────────────────────────
    var _STORAGE_KEY = "fria_modal_rects";
    var _saveTimeout = null;

    function _friaModalGetStore() {
        try {
            var raw = localStorage.getItem(_STORAGE_KEY);
            return raw ? JSON.parse(raw) : {};
        } catch (e) {
            return {};
        }
    }

    function _friaModalWriteStore(store) {
        try {
            localStorage.setItem(_STORAGE_KEY, JSON.stringify(store));
        } catch (e) {
            // localStorage plein ou désactivé — silencieux
        }
    }

    function _friaModalSaveRect(key, rect) {
        if (!key) return;
        if (_saveTimeout) clearTimeout(_saveTimeout);
        _saveTimeout = setTimeout(function () {
            _saveTimeout = null;
            var store = _friaModalGetStore();
            store[key] = { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
            _friaModalWriteStore(store);
        }, 300);
    }

    function _friaModalLoadRect(key) {
        if (!key) return null;
        var store = _friaModalGetStore();
        return store[key] || null;
    }

    function _friaModalRemoveRect(key) {
        if (!key) return;
        var store = _friaModalGetStore();
        if (store[key]) {
            delete store[key];
            _friaModalWriteStore(store);
        }
    }

    // ─── Helpers ─────────────────────────────────────────────────────────────────
    function _parseCSSLength(val, viewportDim) {
        if (typeof val !== 'string') return parseInt(val) || 0;
        val = val.trim();
        if (val.endsWith('px')) return parseFloat(val);
        if (val.endsWith('vw')) return (parseFloat(val) / 100) * window.innerWidth;
        if (val.endsWith('vh')) return (parseFloat(val) / 100) * window.innerHeight;
        if (val.endsWith('%')) return (parseFloat(val) / 100) * viewportDim;
        return parseFloat(val) || 0;
    }

    function _isElement(obj) {
        return obj && typeof obj === "object" && obj.nodeType === 1;
    }

    function _clamp(val, min, max) {
        return Math.max(min, Math.min(max, val));
    }

    function _ensureInViewport(rect, modalWidth, modalHeight) {
        var vw = window.innerWidth;
        var vh = window.innerHeight;
        var margin = 20;
        // Ensure visible within viewport
        var left = _clamp(rect.left, margin, vw - modalWidth - margin);
        var top = _clamp(rect.top, margin, vh - modalHeight - margin);
        // If modal is larger than viewport, just center it
        if (modalWidth > vw - margin * 2) {
            left = margin;
        }
        if (modalHeight > vh - margin * 2) {
            top = margin;
        }
        return { left: left, top: top };
    }

    // ─── Fonction principale ─────────────────────────────────────────────────────
    window.friaOpenModalV2 = function (options) {
        if (!options) options = {};
        _friaModalInjectCSS();

        var title = options.title || "";
        var content = options.content || "";
        var width = options.width || "400px";
        var height = options.height || "auto";
        var minWidth = options.minWidth || "280px";
        var minHeight = options.minHeight || "120px";
        var maxWidth = options.maxWidth || "90vw";
        var maxHeight = options.maxHeight || "85vh";
        var storageKey = options.storageKey || null;
        var persistSize = !!options.persistSize;
        var persistPos = !!options.persistPos;
        var resizable = options.resizable !== false;
        var draggable = options.draggable !== false;
        var closeOnEscape = options.closeOnEscape !== false;
        var bringToFrontOnClick = options.bringToFrontOnClick !== false;
        var onClose = options.onClose || null;
        var onOpen = options.onOpen || null;
        var onResize = options.onResize || null;
        var className = options.className || "";

        // ── Construire le DOM ──────────────────────────────────────────────────
        var modal = document.createElement("div");
        modal.className = "fria-modal" + (className ? " " + className : "");
        modal.style.width = width;
        modal.style.height = height;
        modal.style.minWidth = minWidth;
        modal.style.minHeight = minHeight;
        modal.style.maxWidth = maxWidth;
        modal.style.maxHeight = maxHeight;
        modal.style.zIndex = _friaModalNextZ();
        // Appliquer la classe active (premier plan)
        modal.classList.add("active");

        // Header
        var header = document.createElement("div");
        header.className = "fria-modal-header";

        var titleEl = document.createElement("span");
        titleEl.className = "fria-modal-title";
        titleEl.textContent = title;

        var headerRight = document.createElement("span");
        headerRight.className = "fria-modal-header-right";

        var closeBtn = document.createElement("button");
        closeBtn.className = "fria-modal-close";
        closeBtn.textContent = "✕";

        header.appendChild(titleEl);
        header.appendChild(headerRight);
        header.appendChild(closeBtn);

        // Body
        var body = document.createElement("div");
        body.className = "fria-modal-body";

        if (typeof content === "string") {
            body.innerHTML = content;
        } else if (_isElement(content)) {
            body.appendChild(content);
        }

        // Resize handle
        var resizeHandle = document.createElement("div");
        resizeHandle.className = "fria-modal-resize-handle";

        modal.appendChild(header);
        modal.appendChild(body);
        modal.appendChild(resizeHandle);
        document.body.appendChild(modal);

        // ── Restauration de la position/taille ────────────────────────────────
        var restored = false;
        if (storageKey) {
            var saved = _friaModalLoadRect(storageKey);
            if (saved) {
                var modalW = saved.width || parseInt(width);
                var modalH = saved.height || parseInt(height);
                if (persistSize) {
                    modal.style.width = modalW + "px";
                    modal.style.height = modalH + "px";
                }
                if (persistPos) {
                    var pos = _ensureInViewport(
                        { left: saved.left, top: saved.top },
                        (persistSize ? modalW : modal.offsetWidth),
                        (persistSize ? modalH : modal.offsetHeight)
                    );
                    modal.style.left = pos.left + "px";
                    modal.style.top = pos.top + "px";
                    restored = true;
                }
            }
        }

        if (!restored) {
            // Centrer par défaut
            var parsedW = parseInt(modal.style.width) || 400;
            var parsedH = parseInt(modal.style.height) || 300;
            modal.style.left = Math.max(20, (window.innerWidth - parsedW) / 2) + "px";
            modal.style.top = Math.max(20, (window.innerHeight - parsedH) / 3) + "px";
        }

        // ── Bring to front ────────────────────────────────────────────────────
        function bringToFront() {
            var newZ = _friaModalNextZ();
            modal.style.zIndex = newZ;
            // Retirer .active de toutes les modales
            var allModals = document.querySelectorAll(".fria-modal");
            for (var i = 0; i < allModals.length; i++) {
                allModals[i].classList.remove("active");
            }
            modal.classList.add("active");
        }

        if (bringToFrontOnClick) {
            modal.addEventListener("mousedown", bringToFront);
        }

        // ── Close ─────────────────────────────────────────────────────────────
        var _closed = false;

        function closeModal() {
            if (_closed) return;
            _closed = true;
            if (typeof onClose === "function") {
                try { onClose(); } catch (e) { /* silencieux */ }
            }
            // Sauvegarder la position finale avant fermeture
            if (storageKey && (persistSize || persistPos)) {
                var rect = modal.getBoundingClientRect();
                if (persistSize || persistPos) {
                    _friaModalSaveRect(storageKey, {
                        left: rect.left,
                        top: rect.top,
                        width: modal.offsetWidth,
                        height: modal.offsetHeight,
                    });
                }
            }
            modal.remove();
        }

        if (closeOnEscape) {
            function onKeydown(e) {
                if (e.key === "Escape" && !_closed) {
                    closeModal();
                }
            }
            document.addEventListener("keydown", onKeydown);
            // Nettoyage : retirer l'écouteur quand la modale est fermée
            var origRemove = closeModal;
            closeModal = function () {
                document.removeEventListener("keydown", onKeydown);
                origRemove();
            };
        }

        closeBtn.addEventListener("click", closeModal);

        // ── Drag ──────────────────────────────────────────────────────────────
        if (draggable) {
            var drag = { active: false, startX: 0, startY: 0, origLeft: 0, origTop: 0 };

            header.addEventListener("mousedown", function (e) {
                if (e.target === closeBtn || e.target === headerRight || headerRight.contains(e.target)) {
                    return;
                }
                drag.active = true;
                drag.startX = e.clientX;
                drag.startY = e.clientY;
                drag.origLeft = modal.offsetLeft;
                drag.origTop = modal.offsetTop;
                header.style.cursor = "grabbing";
                e.preventDefault();
            });

            document.addEventListener("mousemove", function (e) {
                if (!drag.active) return;
                var newLeft = drag.origLeft + (e.clientX - drag.startX);
                var newTop = drag.origTop + (e.clientY - drag.startY);
                modal.style.left = newLeft + "px";
                modal.style.top = newTop + "px";
            });

            document.addEventListener("mouseup", function () {
                if (drag.active) {
                    drag.active = false;
                    header.style.cursor = "grab";
                    // Sauvegarder position après drag
                    if (storageKey && persistPos) {
                        _friaModalSaveRect(storageKey, {
                            left: modal.offsetLeft,
                            top: modal.offsetTop,
                            width: modal.offsetWidth,
                            height: modal.offsetHeight,
                        });
                    }
                }
            });
        }

        // ── Resize ────────────────────────────────────────────────────────────
        if (resizable) {
            var resize = { active: false, startX: 0, startY: 0, origW: 0, origH: 0 };

            resizeHandle.addEventListener("mousedown", function (e) {
                resize.active = true;
                resize.startX = e.clientX;
                resize.startY = e.clientY;
                resize.origW = modal.offsetWidth;
                resize.origH = modal.offsetHeight;
                e.stopPropagation();
                e.preventDefault();
            });

            document.addEventListener("mousemove", function (e) {
                if (!resize.active) return;
                var newW = resize.origW + (e.clientX - resize.startX);
                var newH = resize.origH + (e.clientY - resize.startY);
                // Appliquer les contraintes de min/max
                var minW = _parseCSSLength(modal.style.minWidth, window.innerWidth) || 280;
                var minH = _parseCSSLength(modal.style.minHeight, window.innerHeight) || 120;
                var maxW = _parseCSSLength(modal.style.maxWidth, window.innerWidth) || Math.round(window.innerWidth * 0.9);
                var maxH = _parseCSSLength(modal.style.maxHeight, window.innerHeight) || Math.round(window.innerHeight * 0.85);
                newW = _clamp(newW, minW, maxW);
                newH = _clamp(newH, minH, maxH);
                modal.style.width = newW + "px";
                modal.style.height = newH + "px";

                if (typeof onResize === "function") {
                    try { onResize(newW, newH); } catch (e) { /* silencieux */ }
                }
            });

            document.addEventListener("mouseup", function () {
                if (resize.active) {
                    resize.active = false;
                    // Sauvegarder taille après resize
                    if (storageKey && (persistSize || persistPos)) {
                        _friaModalSaveRect(storageKey, {
                            left: modal.offsetLeft,
                            top: modal.offsetTop,
                            width: modal.offsetWidth,
                            height: modal.offsetHeight,
                        });
                    }
                }
            });
        }

        // ── setTitle / setBody ───────────────────────────────────────────────
        function setTitle(str) {
            titleEl.textContent = str;
        }

        function setBody(html) {
            body.innerHTML = "";
            if (typeof html === "string") {
                body.innerHTML = html;
            } else if (_isElement(html)) {
                body.appendChild(html);
            }
        }

        // ── Callback onOpen ────────────────────────────────────────────────────
        if (typeof onOpen === "function") {
            try { onOpen(); } catch (e) { /* silencieux */ }
        }

        // ── Return API ─────────────────────────────────────────────────────────
        return {
            modal: modal,
            body: body,
            header: header,
            close: function () {
                closeModal();
            },
            setTitle: setTitle,
            setBody: setBody,
            bringToFront: bringToFront,
        };
    };

    // ─── friaShowAlert ───────────────────────────────────────────────────────────
    window.friaShowAlert = function (title, message, type) {
        type = type || "info";
        var icons = {
            info: "ℹ️",
            success: "✅",
            error: "❌",
            warning: "⚠️",
        };
        return new Promise(function (resolve) {
            var m = window.friaOpenModalV2({
                title: title || "",
                width: "320px",
                minWidth: "280px",
                minHeight: "100px",
                resizable: false,
                storageKey: null,
                closeOnEscape: true,
                content: [
                    '<div class="fria-modal-icon fria-modal-icon-' + type + '">' + (icons[type] || "ℹ️") + '</div>',
                    '<div class="fria-modal-message">' + (message || "") + '</div>',
                    '<div class="fria-modal-actions">',
                    '<button class="fria-modal-btn fria-modal-btn-primary" id="fria-alert-ok">OK</button>',
                    '</div>',
                ].join(""),
                onClose: function () {
                    resolve();
                },
            });
            document.getElementById("fria-alert-ok").addEventListener("click", function () {
                m.close();
            });
        });
    };

    // ─── friaShowConfirm ─────────────────────────────────────────────────────────
    window.friaShowConfirm = function (title, message) {
        return new Promise(function (resolve) {
            var m = window.friaOpenModalV2({
                title: title || "",
                width: "360px",
                minWidth: "300px",
                minHeight: "100px",
                resizable: false,
                storageKey: null,
                closeOnEscape: true,
                content: [
                    '<div class="fria-modal-message">' + (message || "") + '</div>',
                    '<div class="fria-modal-actions">',
                    '<button class="fria-modal-btn" id="fria-confirm-cancel">Annuler</button>',
                    '<button class="fria-modal-btn fria-modal-btn-primary" id="fria-confirm-ok">Confirmer</button>',
                    '</div>',
                ].join(""),
                onClose: function () {
                    resolve(false);
                },
            });
            document.getElementById("fria-confirm-cancel").addEventListener("click", function () {
                resolve(false);
                m.close();
            });
            document.getElementById("fria-confirm-ok").addEventListener("click", function () {
                resolve(true);
                m.close();
            });
        });
    };

    // ─── friaShowPrompt ─────────────────────────────────────────────────────────
    window.friaShowPrompt = function (title, message, placeholder) {
        placeholder = placeholder || "";
        return new Promise(function (resolve) {
            var uid = "_fria_prompt_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8);
            var m = window.friaOpenModalV2({
                title: title || "",
                width: "360px",
                minWidth: "300px",
                minHeight: "100px",
                resizable: false,
                storageKey: null,
                closeOnEscape: true,
                content: [
                    '<div class="fria-modal-message">' + (message || "") + '</div>',
                    '<input class="fria-modal-input" id="' + uid + '" type="text" placeholder="' + placeholder + '" />',
                    '<div class="fria-modal-actions">',
                    '<button class="fria-modal-btn" id="' + uid + '-cancel">Annuler</button>',
                    '<button class="fria-modal-btn fria-modal-btn-primary" id="' + uid + '-ok">OK</button>',
                    '</div>',
                ].join(""),
                onClose: function () {
                    resolve(null);
                },
            });
            var input = document.getElementById(uid);

            function submit() {
                var val = input.value.trim();
                resolve(val || null);
                m.close();
            }

            document.getElementById(uid + "-cancel").addEventListener("click", function () {
                resolve(null);
                m.close();
            });
            document.getElementById(uid + "-ok").addEventListener("click", submit);

            // Enter dans l'input → submit
            input.addEventListener("keydown", function (e) {
                if (e.key === "Enter") {
                    submit();
                }
            });

            // Focus automatique sur l'input
            setTimeout(function () { input.focus(); }, 50);
        });
    };

})();
