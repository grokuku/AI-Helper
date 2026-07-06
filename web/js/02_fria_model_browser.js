/**
 * FR.IA Model Browser — Parcourir, uploader et télécharger des modèles.
 *
 * Dépend : 01_fria_modal_v2.js (friaOpenModalV2)
 *           fria_elements_widget.js (esc, getApiUrl, getApiKey, apiHeaders)
 *
 * Fonctions exportées sur window :
 *   - openModelBrowser()    → ouvre la fenêtre Model Browser
 */

(function () {
    "use strict";

    // ─── Injection CSS (une seule fois) ──────────────────────────────────────────
    var _cssInjected = false;
    function _mbInjectCSS() {
        if (_cssInjected) return;
        _cssInjected = true;
        var style = document.createElement("style");
        style.textContent = [
            /* Filtres */
            ".mb-filters {",
            "  display: flex;",
            "  flex-wrap: wrap;",
            "  gap: 6px;",
            "  padding: 8px 0;",
            "  align-items: center;",
            "  flex-shrink: 0;",
            "}",
            ".mb-filters .mb-filter-checkbox {",
            "  display: inline-flex;",
            "  align-items: center;",
            "  gap: 4px;",
            "  font-size: 11px;",
            "  cursor: pointer;",
            "  padding: 2px 8px;",
            "  border-radius: 4px;",
            "  border: 1px solid #444;",
            "  background: #1e1e22;",
            "  color: #ccc;",
            "  user-select: none;",
            "  transition: background 0.15s, border-color 0.15s;",
            "}",
            ".mb-filters .mb-filter-checkbox:hover {",
            "  background: #2a2a2e;",
            "  border-color: #666;",
            "}",
            ".mb-filters .mb-filter-checkbox.active {",
            "  border-color: var(--mb-color, #6366f1);",
            "  background: var(--mb-color, #6366f1);",
            "  color: #fff;",
            "}",
            ".mb-filters .mb-filter-checkbox input {",
            "  display: none;",
            "}",
            ".mb-filters .mb-filter-search {",
            "  display: flex;",
            "  align-items: center;",
            "  gap: 4px;",
            "  margin-left: auto;",
            "}",
            ".mb-filters .mb-filter-search input {",
            "  padding: 4px 8px;",
            "  border-radius: 4px;",
            "  border: 1px solid #444;",
            "  background: #1e1e22;",
            "  color: #ccc;",
            "  font-size: 11px;",
            "  width: 120px;",
            "  outline: none;",
            "}",
            ".mb-filters .mb-filter-search input:focus {",
            "  border-color: #6366f1;",
            "}",
            /* Panneaux */
            ".mb-panels {",
            "  display: flex;",
            "  flex: 1;",
            "  gap: 8px;",
            "  min-height: 0;",
            "}",
            ".mb-panel {",
            "  flex: 1;",
            "  display: flex;",
            "  flex-direction: column;",
            "  min-width: 0;",
            "}",
            ".mb-panel-header {",
            "  font-size: 11px;",
            "  color: #888;",
            "  padding: 4px 0;",
            "  font-weight: 600;",
            "  flex-shrink: 0;",
            "}",
            ".mb-panel-list {",
            "  flex: 1;",
            "  overflow-y: auto;",
            "  border: 1px solid #444;",
            "  border-radius: 6px;",
            "  background: #1e1e22;",
            "}",
            ".mb-panel-list .mb-empty {",
            "  padding: 20px;",
            "  text-align: center;",
            "  color: #666;",
            "  font-size: 12px;",
            "}",
            ".mb-panel-list .mb-loading {",
            "  padding: 20px;",
            "  text-align: center;",
            "  color: #888;",
            "  font-size: 12px;",
            "}",
            /* Items */
            ".mb-item {",
            "  padding: 6px 8px;",
            "  cursor: pointer;",
            "  font-size: 12px;",
            "  color: #ccc;",
            "  border-bottom: 1px solid #333;",
            "  display: flex;",
            "  align-items: center;",
            "  gap: 6px;",
            "  position: relative;",
            "}",
            ".mb-item:hover {",
            "  background: #2a2a2e;",
            "}",
            ".mb-item.selected {",
            "  background: #2a2a4e;",
            "  border-left: 3px solid #6366f1;",
            "}",
            ".mb-item .mb-badge {",
            "  font-size: 9px;",
            "  padding: 1px 4px;",
            "  border-radius: 3px;",
            "  font-weight: 600;",
            "  flex-shrink: 0;",
            "  color: #fff;",
            "}",
            ".mb-item .mb-name {",
            "  flex: 1;",
            "  overflow: hidden;",
            "  text-overflow: ellipsis;",
            "  white-space: nowrap;",
            "}",
            ".mb-item .mb-size {",
            "  font-size: 10px;",
            "  color: #666;",
            "  flex-shrink: 0;",
            "}",
            ".mb-item .mb-check {",
            "  font-size: 12px;",
            "  flex-shrink: 0;",
            "}",
            ".mb-item .mb-action-btn {",
            "  display: none;",
            "  padding: 2px 8px;",
            "  border-radius: 4px;",
            "  border: none;",
            "  cursor: pointer;",
            "  font-size: 10px;",
            "  font-weight: 600;",
            "  flex-shrink: 0;",
            "}",
            ".mb-item.selected .mb-action-btn {",
            "  display: inline-block;",
            "}",
            ".mb-item .mb-action-btn.mb-upload-btn {",
            "  background: #6366f1;",
            "  color: #fff;",
            "}",
            ".mb-item .mb-action-btn.mb-upload-btn:hover {",
            "  background: #4f46e5;",
            "}",
            ".mb-item .mb-action-btn.mb-download-btn {",
            "  background: #22c55e;",
            "  color: #fff;",
            "}",
            ".mb-item .mb-action-btn.mb-download-btn:hover {",
            "  background: #16a34a;",
            "}",
            ".mb-item .mb-extra {",
            "  font-size: 10px;",
            "  color: #666;",
            "  flex-shrink: 0;",
            "  margin-left: 4px;",
            "}",
            /* Divider vertical */
            ".mb-divider {",
            "  width: 1px;",
            "  background: #444;",
            "  flex-shrink: 0;",
            "}",
            /* Progress */
            ".mb-progress {",
            "  border-top: 1px solid #444;",
            "  padding: 8px 0;",
            "  max-height: 120px;",
            "  overflow-y: auto;",
            "  flex-shrink: 0;",
            "}",
            ".mb-progress-row {",
            "  display: flex;",
            "  align-items: center;",
            "  gap: 8px;",
            "  padding: 4px 0;",
            "  font-size: 11px;",
            "  color: #aaa;",
            "}",
            ".mb-progress-name {",
            "  flex: 1;",
            "  overflow: hidden;",
            "  text-overflow: ellipsis;",
            "  white-space: nowrap;",
            "  min-width: 0;",
            "}",
            ".mb-progress-bar {",
            "  width: 120px;",
            "  height: 8px;",
            "  background: #333;",
            "  border-radius: 4px;",
            "  overflow: hidden;",
            "  flex-shrink: 0;",
            "}",
            ".mb-progress-fill {",
            "  height: 100%;",
            "  width: 0%;",
            "  background: linear-gradient(90deg, #6366f1, #22c55e);",
            "  border-radius: 4px;",
            "  transition: width 0.3s;",
            "}",
            ".mb-progress-pct {",
            "  width: 40px;",
            "  text-align: right;",
            "  color: #888;",
            "  flex-shrink: 0;",
            "}",
            ".mb-loading-spinner {",
            "  display: inline-block;",
            "  width: 14px;",
            "  height: 14px;",
            "  border: 2px solid #444;",
            "  border-top-color: #6366f1;",
            "  border-radius: 50%;",
            "  animation: mb-spin 0.8s linear infinite;",
            "  vertical-align: middle;",
            "}",
            "@keyframes mb-spin {",
            "  to { transform: rotate(360deg); }",
            "}",
        ].join("\n");
        document.head.appendChild(style);
    }

    // ─── Helper d'échappement HTML local ────────────────────────────────────────
    function _esc(str) {
        if (typeof str !== 'string') return String(str || '');
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ─── Types de modèles ────────────────────────────────────────────────────────
    var MODEL_TYPES = [
        { key: 'unet', label: 'UNET', color: '#60a5fa' },
        { key: 'unet_gguf', label: 'GGUF', color: '#a78bfa' },
        { key: 'checkpoint', label: 'Checkpoints', color: '#34d399' },
        { key: 'lora', label: 'LoRAs', color: '#f472b6' },
        { key: 'vae', label: 'VAE', color: '#fbbf24' },
        { key: 'clip', label: 'CLIP', color: '#f87171' },
        { key: 'controlnet', label: 'ControlNet', color: '#38bdf8' },
        { key: 'upscale', label: 'Upscale', color: '#fb923c' },
        { key: 'text_encoder', label: 'Text Enc.', color: '#e879f9' },
        { key: 'style_model', label: 'Style', color: '#2dd4bf' },
        { key: 'diffusion_model', label: 'Diffusion', color: '#f0abfc' },
        { key: 'other', label: 'Autres', color: '#888' },
    ];

    // ─── Helpers ─────────────────────────────────────────────────────────────────
    function formatSize(bytes) {
        if (!bytes && bytes !== 0) return "";
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
        if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + " MB";
        return (bytes / 1073741824).toFixed(2) + " GB";
    }

    function formatDate(d) {
        if (!d) return "";
        var date = new Date(d);
        if (isNaN(date.getTime())) return d;
        return date.toLocaleDateString("fr-FR", {
            day: "numeric", month: "short", year: "numeric",
        });
    }

    function getActiveTypeFilters() {
        var checked = document.querySelectorAll('.mb-filter-checkbox.active');
        var types = [];
        checked.forEach(function (el) {
            types.push(el.dataset.type);
        });
        return types.length ? types : null;
    }

    function getSearchQuery() {
        var input = document.getElementById('mb-search-local');
        var val = input ? input.value.trim() : "";
        return val || null;
    }

    function getRemoteSearchQuery() {
        var input = document.getElementById('mb-search-remote');
        var val = input ? input.value.trim() : "";
        return val || null;
    }

    // ─── openModelBrowser ────────────────────────────────────────────────────────
    window.openModelBrowser = function () {
        _mbInjectCSS();

        var m = friaOpenModalV2({
            title: "📦 Model Browser",
            width: "920px",
            height: "620px",
            minWidth: "700px",
            minHeight: "400px",
            storageKey: "fria-modal-model-browser",
            persistSize: true,
            persistPos: true,
            className: "fria-model-browser",
        });
        renderModelBrowser(m);

        // Nettoyer le timer à la fermeture
        m.modal.addEventListener('fria-modal-close', function () {
            if (m._refreshTimer) {
                clearInterval(m._refreshTimer);
                m._refreshTimer = null;
            }
        });
    };

    // ─── renderModelBrowser ──────────────────────────────────────────────────────
    function renderModelBrowser(m) {
        m.body.style.display = "flex";
        m.body.style.flexDirection = "column";
        m.body.style.padding = "8px 12px";

        m.body.innerHTML = "" +
            '<div class="mb-filters" id="mb-filters"></div>' +
            '<div class="mb-panels" id="mb-panels">' +
            '  <div class="mb-panel mb-panel-local">' +
            '    <div class="mb-panel-header">📁 Local (ComfyUI)</div>' +
            '    <div class="mb-panel-list" id="mb-local-list"></div>' +
            '  </div>' +
            '  <div class="mb-divider"></div>' +
            '  <div class="mb-panel mb-panel-remote">' +
            '    <div class="mb-panel-header">🌐 Remote (FR.IA)</div>' +
            '    <div class="mb-panel-list" id="mb-remote-list"></div>' +
            '  </div>' +
            '</div>' +
            '<div class="mb-progress" id="mb-progress" style="display:none;"></div>';

        // Références utiles
        m._localList = m.modal.querySelector('#mb-local-list');
        m._remoteList = m.modal.querySelector('#mb-remote-list');
        m._progressContainer = m.modal.querySelector('#mb-progress');
        m._localLoading = false;
        m._remoteLoading = false;
        m._localPage = 1;
        m._remotePage = 1;
        m._remoteHasMore = true;

        // Filtres
        renderFilters(m);

        // Chargement initial
        loadLocalModels(m);
        loadRemoteModels(m);

        // Infinite scroll sur le panneau distant
        m._remoteList.addEventListener('scroll', function () {
            if (m._remoteLoading || !m._remoteHasMore) return;
            var el = m._remoteList;
            if (el.scrollTop + el.clientHeight >= el.scrollHeight - 60) {
                m._remotePage++;
                loadRemoteModels(m);
            }
        });

        // Rafraîchissement périodique toutes les 30s
        m._refreshTimer = setInterval(function () {
            m._remotePage = 1;
            m._remoteHasMore = true;
            loadLocalModels(m);
            loadRemoteModels(m);
        }, 30000);
    }

    // ─── renderFilters ──────────────────────────────────────────────────────────
    function renderFilters(m) {
        var container = m.modal.querySelector('#mb-filters');
        container.innerHTML = "";

        // Checkboxes de type
        MODEL_TYPES.forEach(function (t) {
            var label = document.createElement('label');
            label.className = 'mb-filter-checkbox active';
            label.style.setProperty('--mb-color', t.color);
            label.dataset.type = t.key;

            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = true;
            label.appendChild(cb);

            var dot = document.createElement('span');
            dot.textContent = '●';
            dot.style.color = t.color;
            dot.style.marginRight = '2px';
            label.appendChild(dot);

            label.appendChild(document.createTextNode(t.label));

            label.addEventListener('click', function (e) {
                e.preventDefault();
                var isActive = label.classList.toggle('active');
                cb.checked = isActive;
                // Re-filtrer les deux listes
                m._remotePage = 1;
                m._remoteHasMore = true;
                loadLocalModels(m);
                loadRemoteModels(m);
            });

            container.appendChild(label);
        });

        // Champs de recherche
        var searchGroup = document.createElement('div');
        searchGroup.className = 'mb-filter-search';

        var s1 = document.createElement('input');
        s1.type = 'text';
        s1.id = 'mb-search-local';
        s1.placeholder = '🔍 Search local...';

        var s2 = document.createElement('input');
        s2.type = 'text';
        s2.id = 'mb-search-remote';
        s2.placeholder = '🔍 Search remote...';

        var debounceTimer = null;
        function onSearchInput() {
            if (debounceTimer) clearTimeout(debounceTimer);
            debounceTimer = setTimeout(function () {
                m._remotePage = 1;
                m._remoteHasMore = true;
                loadLocalModels(m);
                loadRemoteModels(m);
            }, 300);
        }
        s1.addEventListener('input', onSearchInput);
        s2.addEventListener('input', onSearchInput);

        searchGroup.appendChild(s1);
        searchGroup.appendChild(s2);
        container.appendChild(searchGroup);
    }

    // ─── loadLocalModels ───────────────────────────────────────────────────────
    function loadLocalModels(m) {
        var types = getActiveTypeFilters();
        var search = getSearchQuery();

        var url = '/api/fria/models/local';
        var params = [];
        if (types && types.length) params.push('type=' + encodeURIComponent(types.join(',')));
        if (search) params.push('search=' + encodeURIComponent(search));
        if (params.length) url += '?' + params.join('&');

        m._localList.innerHTML = '<div class="mb-loading"><span class="mb-loading-spinner"></span> Chargement...</div>';

        fetch(url, { headers: apiHeaders() })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (data) {
                // data.items est un dictionnaire { catégorie: [modèles] }
                // On l'aplatit en tableau en déduisant le type depuis la catégorie
                var itemsObj = data.items || {};
                var flatItems = [];
                Object.keys(itemsObj).forEach(function (category) {
                    (itemsObj[category] || []).forEach(function (item) {
                        item.type = item.type || category;
                        flatItems.push(item);
                    });
                });
                m._localItems = flatItems;  // pour isLocalByFingerprint
                renderLocalPanel(m, flatItems);
            })
            .catch(function (err) {
                m._localLoading = false;
                m._localList.innerHTML = '<div class="mb-empty">❌ Erreur: ' + _esc(err.message || 'Requête échouée') + '</div>';
            });
    }

    // ─── loadRemoteModels ──────────────────────────────────────────────────────
    function loadRemoteModels(m) {
        if (m._remoteLoading) return;
        m._remoteLoading = true;

        var types = getActiveTypeFilters();
        var search = getRemoteSearchQuery();
        var page = m._remotePage || 1;

        var url = '/api/fria/models/remote?page=' + page + '&limit=50';
        if (types && types.length) url += '&type=' + encodeURIComponent(types.join(','));
        if (search) url += '&search=' + encodeURIComponent(search);

        if (page === 1) {
            m._remoteList.innerHTML = '<div class="mb-loading"><span class="mb-loading-spinner"></span> Chargement...</div>';
        }

        fetch(url, { headers: apiHeaders() })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (data) {
                renderRemotePanel(m, data);
                m._remoteLoading = false;
            })
            .catch(function (err) {
                m._remoteLoading = false;  // TOUJOURS réinitialiser
                if (page === 1) {
                    m._remoteList.innerHTML = '<div class="mb-empty">❌ Erreur: ' + _esc(err.message || 'Requête échouée') + '</div>';
                }
            });
    }

    // ─── renderLocalPanel ──────────────────────────────────────────────────────
    function renderLocalPanel(m, items) {
        var list = m._localList;
        list.innerHTML = "";

        if (!items || items.length === 0) {
            list.innerHTML = '<div class="mb-empty">Aucun modèle local trouvé</div>';
            return;
        }

        // Index des modèles distants pour vérifier les fingerprints
        var remoteIndex = {};
        var remoteItems = m._remoteItems || [];
        for (var i = 0; i < remoteItems.length; i++) {
            var ri = remoteItems[i];
            if (ri.fingerprint) {
                remoteIndex[ri.fingerprint] = ri;
            }
        }

        items.forEach(function (item) {
            var div = document.createElement('div');
            div.className = 'mb-item';

            // Badge type
            var typeInfo = getTypeInfo(item.type || item.model_type || 'other');
            var badge = document.createElement('span');
            badge.className = 'mb-badge';
            badge.textContent = typeInfo.label;
            badge.style.background = typeInfo.color;
            div.appendChild(badge);

            // Nom
            var nameSpan = document.createElement('span');
            nameSpan.className = 'mb-name';
            nameSpan.textContent = item.name || item.filename || '?';
            nameSpan.title = item.name || item.filename || '';
            div.appendChild(nameSpan);

            // Taille
            var sizeSpan = document.createElement('span');
            sizeSpan.className = 'mb-size';
            sizeSpan.textContent = formatSize(item.size || item.file_size);
            div.appendChild(sizeSpan);

            // Icône ✅ si déjà sur le serveur
            if (item.fingerprint && remoteIndex[item.fingerprint]) {
                var check = document.createElement('span');
                check.className = 'mb-check';
                check.textContent = '✅';
                check.title = 'Déjà présent sur le serveur';
                div.appendChild(check);
            }

            // Bouton upload
            var uploadBtn = document.createElement('button');
            uploadBtn.className = 'mb-action-btn mb-upload-btn';
            uploadBtn.textContent = '↗ Upload';
            uploadBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                uploadLocalModel(m, item.path || item.filepath, item.type || item.model_type, item.name || item.filename);
            });
            div.appendChild(uploadBtn);

            // Clic simple → sélection
            div.addEventListener('click', function () {
                // Deselect all others
                var allItems = list.querySelectorAll('.mb-item');
                allItems.forEach(function (el) { el.classList.remove('selected'); });
                div.classList.add('selected');
            });

            // Double-clic → upload direct
            div.addEventListener('dblclick', function () {
                uploadLocalModel(m, item.path || item.filepath, item.type || item.model_type, item.name || item.filename);
            });

            list.appendChild(div);
        });
    }

    // ─── renderRemotePanel ─────────────────────────────────────────────────────
    function renderRemotePanel(m, data) {
        var list = m._remoteList;
        var raw = data.items || data.uploads || [];
        var items = Array.isArray(raw) ? raw : [];
        var page = m._remotePage || 1;

        // Si data a une structure paginée
        if (data.items && Array.isArray(data.items)) {
            items = data.items;
            m._remoteHasMore = items.length >= (data.limit || 50);
            if (data.total !== undefined) {
                m._remoteHasMore = (page * (data.limit || 50)) < data.total;
            }
        } else if (Array.isArray(data)) {
            items = data;
            m._remoteHasMore = false;
        }

        if (page === 1) {
            list.innerHTML = "";
        }

        // Stocker les items distants pour le matching local
        if (page === 1) {
            m._remoteItems = items;
        } else {
            m._remoteItems = (m._remoteItems || []).concat(items);
        }

        if (items.length === 0 && page === 1) {
            list.innerHTML = '<div class="mb-empty">Aucun modèle distant trouvé</div>';
            return;
        }

        items.forEach(function (item) {
            var div = document.createElement('div');
            div.className = 'mb-item';

            // Badge type
            var typeInfo = getTypeInfo(item.type || item.model_type || 'other');
            var badge = document.createElement('span');
            badge.className = 'mb-badge';
            badge.textContent = typeInfo.label;
            badge.style.background = typeInfo.color;
            div.appendChild(badge);

            // Nom
            var nameSpan = document.createElement('span');
            nameSpan.className = 'mb-name';
            var displayName = item.name || item.filename || item.original_name || '?';
            nameSpan.textContent = displayName;
            nameSpan.title = displayName;
            div.appendChild(nameSpan);

            // Extra info (uploader + date)
            var extraSpan = document.createElement('span');
            extraSpan.className = 'mb-extra';
            var uploader = item.uploader || item.uploader_name || item.owner_name || '';
            var dateStr = formatDate(item.created_at || item.uploaded_at || item.date);
            var downloadCount = item.downloads || item.download_count || 0;
            var extraParts = [];
            if (uploader) extraParts.push(uploader);
            if (dateStr) extraParts.push(dateStr);
            extraSpan.textContent = extraParts.join(' · ');
            div.appendChild(extraSpan);

            // Taille + downloads
            var sizeSpan = document.createElement('span');
            sizeSpan.className = 'mb-size';
            var sizeTxt = formatSize(item.size || item.file_size || item.original_size);
            var dlTxt = downloadCount > 0 ? ' ⬇' + downloadCount : '';
            sizeSpan.textContent = sizeTxt + dlTxt;
            div.appendChild(sizeSpan);

            // Icône ✅ si déjà en local
            if (item.fingerprint && isLocalByFingerprint(m, item.fingerprint)) {
                var check = document.createElement('span');
                check.className = 'mb-check';
                check.textContent = '✅';
                check.title = 'Déjà présent localement';
                div.appendChild(check);
            }

            // Bouton download
            var dlBtn = document.createElement('button');
            dlBtn.className = 'mb-action-btn mb-download-btn';
            dlBtn.textContent = '↙ Download';
            dlBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                var uploadId = item.id || item.upload_id || item._id;
                var destSubdir = item.type || item.model_type || '';
                downloadRemoteModel(m, uploadId, displayName, item.type || item.model_type, destSubdir);
            });
            div.appendChild(dlBtn);

            // Clic simple → sélection
            div.addEventListener('click', function () {
                var allItems = list.querySelectorAll('.mb-item');
                allItems.forEach(function (el) { el.classList.remove('selected'); });
                div.classList.add('selected');
            });

            // Double-clic → download direct
            div.addEventListener('dblclick', function () {
                var uploadId = item.id || item.upload_id || item._id;
                var destSubdir = item.type || item.model_type || '';
                downloadRemoteModel(m, uploadId, displayName, item.type || item.model_type, destSubdir);
            });

            list.appendChild(div);
        });

        // Indicateur de chargement pour la page suivante
        if (m._remoteHasMore) {
            var loadMore = document.createElement('div');
            loadMore.className = 'mb-loading';
            loadMore.style.padding = '12px';
            loadMore.style.borderBottom = 'none';
            loadMore.innerHTML = '<span class="mb-loading-spinner"></span> Scrollez pour charger plus...';
            list.appendChild(loadMore);
        }
    }

    // ─── getTypeInfo ────────────────────────────────────────────────────────────
    function getTypeInfo(typeKey) {
        if (!typeKey) return { key: 'other', label: 'Autres', color: '#888' };
        var key = typeKey.toLowerCase().replace(/[^a-z0-9_]/g, '');
        for (var i = 0; i < MODEL_TYPES.length; i++) {
            if (MODEL_TYPES[i].key === key) return MODEL_TYPES[i];
        }
        return { key: 'other', label: 'Autres', color: '#888' };
    }

    // ─── isLocalByFingerprint ──────────────────────────────────────────────────
    function isLocalByFingerprint(m, fingerprint) {
        if (!fingerprint) return false;
        // Vérifie dans les items locaux déjà chargés
        var localItems = m._localItems || [];
        for (var i = 0; i < localItems.length; i++) {
            if (localItems[i].fingerprint === fingerprint) return true;
        }
        return false;
    }

    // ─── uploadLocalModel ──────────────────────────────────────────────────────
    function uploadLocalModel(m, filepath, fileType, filename) {
        if (!filepath) {
            friaShowAlert("Erreur", "Chemin du fichier manquant", "error");
            return;
        }

        var progressEl = showProgress(m, filename || filepath);

        fetch(getApiUrl() + '/fria/models/upload', {
            method: 'POST',
            headers: apiHeaders(),
            body: JSON.stringify({
                path: filepath,
                type: fileType || 'other',
            }),
        })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (d) {
                    throw new Error(d.error || d.message || 'HTTP ' + r.status);
                });
                return r.json();
            })
            .then(function (data) {
                if (data.status === 'ok' || data.success) {
                    updateProgress(progressEl, 100, '✅ Upload terminé');
                    // Rafraîchir les deux listes
                    m._remotePage = 1;
                    m._remoteHasMore = true;
                    loadRemoteModels(m);
                    loadLocalModels(m);
                } else {
                    updateProgress(progressEl, 0, '❌ Erreur: ' + (data.error || data.message || 'inconnue'));
                }
            })
            .catch(function (err) {
                updateProgress(progressEl, 0, '❌ Erreur: ' + err.message);
            });
    }

    // ─── downloadRemoteModel ───────────────────────────────────────────────────
    function downloadRemoteModel(m, uploadId, filename, fileType, destSubdir) {
        if (!uploadId) {
            friaShowAlert("Erreur", "ID du modèle distant manquant", "error");
            return;
        }

        var progressEl = showProgress(m, filename);

        fetch(getApiUrl() + '/fria/models/download', {
            method: 'POST',
            headers: apiHeaders(),
            body: JSON.stringify({
                upload_id: uploadId,
                filename: filename,
                type: fileType || 'other',
                dest_path: destSubdir || fileType || '',
            }),
        })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (d) {
                    throw new Error(d.error || d.message || 'HTTP ' + r.status);
                });
                return r.json();
            })
            .then(function (data) {
                if (data.status === 'ok' || data.success) {
                    if (data.conflict) {
                        // Conflit détecté par le serveur
                        updateProgress(progressEl, 50, '⚠️ Conflit, résolution...');
                        return handleDownloadConflict(m, uploadId, filename, fileType, destSubdir, data, progressEl);
                    }
                    updateProgress(progressEl, 100, '✅ Download terminé');
                    m._remotePage = 1;
                    m._remoteHasMore = true;
                    loadLocalModels(m);
                    loadRemoteModels(m);
                } else {
                    updateProgress(progressEl, 0, '❌ Erreur: ' + (data.error || data.message || 'inconnue'));
                }
            })
            .catch(function (err) {
                updateProgress(progressEl, 0, '❌ Erreur: ' + err.message);
            });
    }

    // ─── handleDownloadConflict ────────────────────────────────────────────────
    function handleDownloadConflict(m, uploadId, filename, fileType, destSubdir, conflictData, progressEl) {
        // Si showConflictModal est disponible globalement, l'utiliser
        if (typeof window.showConflictModal === 'function') {
            window.showConflictModal(filename, conflictData.local, conflictData.remote)
                .then(function (result) {
                    if (result.action === 'overwrite') {
                        return retryDownload(m, uploadId, filename, fileType, destSubdir, 'overwrite', progressEl);
                    } else if (result.action === 'suffix') {
                        return retryDownload(m, uploadId, result.newName, fileType, destSubdir, 'suffix', progressEl);
                    } else {
                        updateProgress(progressEl, 0, '⏹️ Download annulé (conflit)');
                    }
                });
        } else {
            // Fallback : friaShowConfirm simple
            friaShowConfirm(
                "⚠️ Conflit",
                "Le fichier \"" + _esc(filename) + "\" existe déjà localement avec un contenu différent.\n\nVoulez-vous écraser le fichier local ?"
            ).then(function (ok) {
                if (ok) {
                    retryDownload(m, uploadId, filename, fileType, destSubdir, 'overwrite', progressEl);
                } else {
                    updateProgress(progressEl, 0, '⏹️ Download annulé (conflit)');
                }
            });
        }
    }

    // ─── retryDownload ─────────────────────────────────────────────────────────
    function retryDownload(m, uploadId, filename, fileType, destSubdir, resolution, progressEl) {
        var body = {
            upload_id: uploadId,
            filename: filename,
            type: fileType || 'other',
            dest_path: destSubdir || fileType || '',
            conflict_resolution: resolution,
        };

        fetch(getApiUrl() + '/fria/models/download', {
            method: 'POST',
            headers: apiHeaders(),
            body: JSON.stringify(body),
        })
            .then(function (r) {
                if (!r.ok) return r.json().then(function (d) {
                    throw new Error(d.error || d.message || 'HTTP ' + r.status);
                });
                return r.json();
            })
            .then(function (data) {
                if (data.status === 'ok' || data.success) {
                    updateProgress(progressEl, 100, '✅ Download terminé');
                    m._remotePage = 1;
                    m._remoteHasMore = true;
                    loadLocalModels(m);
                    loadRemoteModels(m);
                } else {
                    updateProgress(progressEl, 0, '❌ Erreur: ' + (data.error || data.message || 'inconnue'));
                }
            })
            .catch(function (err) {
                updateProgress(progressEl, 0, '❌ Erreur: ' + err.message);
            });
    }

    // ─── showProgress / updateProgress ─────────────────────────────────────────
    function showProgress(m, filename) {
        var container = m.modal.querySelector('#mb-progress');
        if (!container) return null;
        container.style.display = 'block';

        var row = document.createElement('div');
        row.className = 'mb-progress-row';

        var nameSpan = document.createElement('span');
        nameSpan.className = 'mb-progress-name';
        nameSpan.textContent = filename || 'fichier';
        row.appendChild(nameSpan);

        var barWrap = document.createElement('div');
        barWrap.className = 'mb-progress-bar';
        var fill = document.createElement('div');
        fill.className = 'mb-progress-fill';
        fill.style.width = '0%';
        barWrap.appendChild(fill);
        row.appendChild(barWrap);

        var pctSpan = document.createElement('span');
        pctSpan.className = 'mb-progress-pct';
        pctSpan.textContent = '0%';
        row.appendChild(pctSpan);

        container.appendChild(row);

        // Scroll en bas pour voir la progression
        container.scrollTop = container.scrollHeight;

        return {
            row: row,
            fill: fill,
            pctSpan: pctSpan,
            nameSpan: nameSpan,
        };
    }

    function updateProgress(progressEl, pct, text) {
        if (!progressEl) return;
        progressEl.fill.style.width = pct + '%';
        progressEl.pctSpan.textContent = (typeof pct === 'number' ? Math.round(pct) : pct) + '%';
        if (text) {
            progressEl.nameSpan.textContent = text;
        }
    }

})();
