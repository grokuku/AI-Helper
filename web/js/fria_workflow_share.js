/**
 * FR.IA Workflow Manager — Modale unique avec 2 onglets.
 *
 * Onglet 1 : 📤 Partager — upload du workflow actif + dépendances auto-détectées
 * Onglet 2 : 🌐 Parcourir — liste paginée des workflows publics + installation
 *
 * Autonome — ne dépend plus de fria_menu.js pour la création de modales.
 */

(function () {
  "use strict";

  // ── Helpers ──

  function getApp() {
    return window.app || window.comfyAPI?.app?.app;
  }

  function getApiUrl() {
    try {
      var cfg = JSON.parse(localStorage.getItem("FRIA_config") || "{}");
      return (cfg.serverUrl || "https://kw.holaf.fr").replace(/\/+$/, "") + "/api";
    } catch { return "https://kw.holaf.fr/api"; }
  }

  function getApiKey() {
    try { return JSON.parse(localStorage.getItem("FRIA_config") || "{}").apiKey || ""; }
    catch { return ""; }
  }

  function apiHeaders() {
    var h = { "Content-Type": "application/json" };
    var key = getApiKey();
    if (key) h["Authorization"] = "Bearer " + key;
    return h;
  }

  function esc(str) {
    if (typeof str !== "string") return "";
    var d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  // ── Toast / progress ──

  var _toastContainer = null;

  function _getToastContainer() {
    if (!_toastContainer || !document.body.contains(_toastContainer)) {
      _toastContainer = document.createElement("div");
      _toastContainer.style.cssText = "position:fixed;bottom:16px;right:16px;z-index:99999;display:flex;flex-direction:column;gap:8px;pointer-events:none;";
      document.body.appendChild(_toastContainer);
    }
    return _toastContainer;
  }

  function friaToast(message, type) {
    // type: "info" | "success" | "error" | "progress"
    var el = document.createElement("div");
    var bg = type === "success" ? "#16a34a" : type === "error" ? "#dc2626" : type === "progress" ? "#1e293b" : "#334155";
    el.style.cssText = "pointer-events:auto;padding:10px 16px;border-radius:8px;color:#fff;font-size:13px;max-width:380px;box-shadow:0 4px 12px rgba(0,0,0,0.4);display:flex;align-items:center;gap:10px;border:1px solid rgba(255,255,255,0.15);";
    el.style.background = bg;
    el.innerHTML = '<span>' + esc(message) + '</span>' + (type === "progress" ? '<div style="flex:1;height:4px;background:rgba(255,255,255,0.2);border-radius:2px;overflow:hidden;"><div style="width:0%;height:100%;background:#6366f1;transition:width 0.3s;"></div></div>' : '');
    _getToastContainer().appendChild(el);
    if (type !== "progress") {
      setTimeout(function() {
        el.style.transition = "opacity 0.5s";
        el.style.opacity = "0";
        setTimeout(function() { el.remove(); }, 500);
      }, 4000);
    }
    return el;
  }

  function friaToastProgress(el, percent, message) {
    if (message) {
      var span = el.querySelector("span");
      if (span) span.textContent = message;
    }
    var bar = el.querySelector("div > div");
    if (bar) bar.style.width = Math.round(percent) + "%";
  }

  function friaToastDone(el, type, message) {
    var bg = type === "success" ? "#16a34a" : "#dc2626";
    el.style.background = bg;
    var span = el.querySelector("span");
    if (span) span.textContent = message;
    var bar = el.querySelector("div > div");
    if (bar) bar.parentElement.remove();
    setTimeout(function() {
      el.style.transition = "opacity 0.5s";
      el.style.opacity = "0";
      setTimeout(function() { el.remove(); }, 500);
    }, 3000);
  }

  // ── Types ComfyUI natifs ──
  // La distinction custom/natif se fait via /fria/custom-nodes :
  // on recupere les types declares par chaque pack custom_nodes, et
  // seuls les nodes du workflow qui matchent ces types sont des dependances.

  // Map complet des loaders -> {widgetIndex, category}
  // Couvre tous les loaders ComfyUI natifs + communautaires
  var MODEL_LOADERS = {
    // Checkpoints
    "CheckpointLoaderSimple":  { idx: 0, cat: "checkpoint" },
    "CheckpointLoader":        { idx: 0, cat: "checkpoint" },
    "unCLIPCheckpointLoader":  { idx: 0, cat: "checkpoint" },
    "CannyCheckpointLoader":   { idx: 0, cat: "checkpoint" },
    "CheckpointLoader|pysssss":{ idx: 0, cat: "checkpoint" },
    "EasyLoadCheckpoint":      { idx: 0, cat: "checkpoint" },
    "CheckpointLoaderSimple|bg2": { idx: 0, cat: "checkpoint" },
    // LoRAs
    "LoraLoader":              { idx: 0, cat: "lora" },
    "LoraLoaderModelOnly":     { idx: 0, cat: "lora" },
    "EasyLoraLoader":          { idx: 0, cat: "lora" },
    "LoraLoader|pysssss":      { idx: 0, cat: "lora" },
    // VAE
    "VAELoader":               { idx: 0, cat: "vae" },
    "VAELoaderFile":           { idx: 0, cat: "vae" },
    "EasyVAELoader":           { idx: 0, cat: "vae" },
    // CLIP
    "CLIPLoader":              { idx: 0, cat: "clip" },
    "DualCLIPLoader":          { idx: 0, cat: "clip" },
    "CLIPVisionLoader":        { idx: 0, cat: "clip_vision" },
    "CLIPLoaderGGUF":          { idx: 0, cat: "clip" },
    // UNET / Diffusion models
    "UNETLoader":              { idx: 0, cat: "unet" },
    "UnetLoaderGGUF":          { idx: 0, cat: "unet_gguf" },
    "DiffModelLoader":         { idx: 0, cat: "unet" },
    "EasyFullyLoader":         { idx: 0, cat: "unet" },
    // ControlNet
    "ControlNetLoader":        { idx: 0, cat: "controlnet" },
    "ControlNetLoaderAdvanced":{ idx: 0, cat: "controlnet" },
    "EasyControlnetLoader":    { idx: 0, cat: "controlnet" },
    // Upscale
    "UpscaleModelLoader":      { idx: 0, cat: "upscale" },
    "ImageUpscaleWithModel":   { idx: 0, cat: "upscale" },
    // GLIGEN
    "GLIGENLoader":            { idx: 0, cat: "gligen" },
    // Hypernetwork
    "HypernetworkLoader":      { idx: 0, cat: "hypernetwork" },
    // Text encoders (SD3, Flux, etc.)
    "TextEncoderLoader":       { idx: 0, cat: "text_encoder" },
    "BERTLoader":              { idx: 0, cat: "text_encoder" },
    "T5Loader":                { idx: 0, cat: "text_encoder" },
    "CLIPLoaderModelOnly":     { idx: 0, cat: "text_encoder" },
    // Style models
    "StyleModelLoader":        { idx: 0, cat: "style_model" },
    // Embeddings
    "PromptStyleLoader":       { idx: 0, cat: "embedding" },
  };

  // Extensions de fichiers models connus
  var MODEL_EXTENSIONS = [".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".bin", ".t5", ".fp16", ".fp8", ".bf16"];

  // ── Custom nodes : detection des URLs git (via endpoint ComfyUI) ──

  async function getInstalledCustomNodes() {
    try {
      var resp = await fetch('/api/fria/custom-nodes');
      if (!resp.ok) {
        console.warn('[FR.IA] /fria/custom-nodes HTTP ' + resp.status + ' ' + resp.statusText + ' — route non enregistree ou erreur serveur');
        return [];
      }
      var data = await resp.json();
      if (!data.nodes || data.nodes.length === 0) {
        console.warn('[FR.IA] /fria/custom-nodes OK mais 0 packs trouves — verifier _CUSTOM_NODES_DIR et _extract_node_types');
      } else {
        console.log('[FR.IA] /fria/custom-nodes: ' + data.nodes.length + ' packs, ' + data.nodes.map(function(n){return n.name + "("+(n.node_types||[]).length+")";}).join(', '));
      }
      return data.nodes || [];
    } catch(e) { console.error('[FR.IA] getInstalledCustomNodes error:', e); return []; }
  }

  async function detectDependencies(workflowJSON) {
    // Collecter TOUS les nodes, y compris ceux dans les subgraphs (recursif)
    function _collectAllNodes(wf) {
      var allNodes = (wf?.nodes || []).slice();
      var subgraphs = wf?.definitions?.subgraphs || [];
      for (var sgi = 0; sgi < subgraphs.length; sgi++) {
        allNodes = allNodes.concat(_collectAllNodes(subgraphs[sgi]));
      }
      return allNodes;
    }
    var nodes = _collectAllNodes(workflowJSON);
    var deps = { nodes: [], models: [], loras: [] };
    var seen = { nodes: {}, models: {}, loras: {} };

    // Recuperer les types de custom nodes installes depuis ComfyUI
    var installedPacks = await getInstalledCustomNodes();
    var customTypeToUrl = {};
    var customTypeToPack = {};
    for (var pi = 0; pi < installedPacks.length; pi++) {
      var pack = installedPacks[pi];
      var types = pack.node_types || [];
      for (var t = 0; t < types.length; t++) {
        if (pack.git_url) customTypeToUrl[types[t]] = pack.git_url;
        customTypeToPack[types[t]] = pack.name;
      }
    }

    // Helper recursif : scanne les widgets values (y compris subgraphs)
    function _scanWidgetValue(wv, nodeType) {
      if (typeof wv === "string" && wv.length > 3) {
        var lower = wv.toLowerCase();
        for (var ei = 0; ei < MODEL_EXTENSIONS.length; ei++) {
          if (lower.endsWith(MODEL_EXTENSIONS[ei]) && !seen.models[wv] && !seen.loras[wv]) {
            if (nodeType.toLowerCase().indexOf("lora") >= 0) {
              seen.loras[wv] = true;
              deps.loras.push({ name: wv, type: "lora" });
            } else {
              seen.models[wv] = true;
              deps.models.push({ name: wv, type: "model" });
            }
            break;
          }
        }
      } else if (Array.isArray(wv)) {
        // Subgraph / widgets imbriques
        for (var si = 0; si < wv.length; si++) {
          _scanWidgetValue(wv[si], nodeType);
        }
      }
    }

    for (var i = 0; i < nodes.length; i++) {
      var type = nodes[i].type || "";
      var widgets = nodes[i].widgets_values || [];

      // Custom nodes (skip UUID subgraph wrappers)
      if (customTypeToPack[type] && !seen.nodes[type] && type.indexOf('-') < 0) {
        seen.nodes[type] = true;
      }

      // Models / LoRAs via les loaders connus
      var loader = MODEL_LOADERS[type];
      if (loader) {
        var filename = widgets[loader.idx];
        if (filename && typeof filename === "string" && filename !== "None" && filename !== "none") {
          if (loader.cat === "lora") {
            if (!seen.loras[filename]) {
              seen.loras[filename] = true;
              deps.loras.push({ name: filename, type: "lora" });
            }
          } else {
            if (!seen.models[filename]) {
              seen.models[filename] = true;
              deps.models.push({ name: filename, type: loader.cat });
            }
          }
        }
        // Verifier les autres widgets (VAE, CLIP etc.)
        for (var wi = 0; wi < widgets.length; wi++) {
          if (wi === loader.idx) continue;
          _scanWidgetValue(widgets[wi], type);
        }
      } else {
        // Detection dynamique sur tous les widgets
        for (var wi = 0; wi < widgets.length; wi++) {
          _scanWidgetValue(widgets[wi], type);
        }
      }
    }

    // Grouper les custom nodes par pack (git_url)
    var packMap = {};
    for (var i = 0; i < nodes.length; i++) {
      var type = nodes[i].type || "";
      if (!customTypeToPack[type]) continue;
      var url = customTypeToUrl[type] || "";
      var packName = customTypeToPack[type];
      var key = url || "__unknown_" + packName;
      if (!packMap[key]) {
        packMap[key] = { name: packName, url: url, node_types: [] };
      }
      if (packMap[key].node_types.indexOf(type) < 0) {
        packMap[key].node_types.push(type);
      }
    }
    deps.nodes = Object.keys(packMap).map(function(k) { return packMap[k]; });

    return deps;
  }

  // ── Fingerprint (deduplication upload) ──

  async function computeFileFingerprint(file) {
    try {
      var headSize = Math.min(1024 * 1024, file.size);
      var head = await file.slice(0, headSize).arrayBuffer();
      var tail = await file.slice(file.size - headSize).arrayBuffer();
      var headHash = await crypto.subtle.digest('SHA-256', head);
      var tailHash = await crypto.subtle.digest('SHA-256', tail);
      function toHex(buf) {
        return Array.from(new Uint8Array(buf)).map(function(b) {
          return b.toString(16).padStart(2, '0');
        }).join('');
      }
      return { size: file.size, head: toHex(headHash), tail: toHex(tailHash) };
    } catch (e) {
      console.warn('[FR.IA] Fingerprint failed:', e);
      return null;
    }
  }

  // ── Local model detection (avoid unnecessary downloads) ──

  async function getLocalModelFiles() {
    // Interroge l'endpoint Python /fria/models/list qui retourne les chemins + tailles
    try {
      var resp = await fetch('/api/fria/models/list');
      if (!resp.ok) {
        console.warn('[FR.IA] /fria/models/list HTTP ' + resp.status + ' ' + resp.statusText + ' — route non enregistree ou erreur serveur');
        return {};
      }
      var data = await resp.json();
      var total = 0;
      for (var cat in data) { total += data[cat].length; }
      if (total === 0) {
        console.warn('[FR.IA] /fria/models/list OK mais 0 fichiers trouves — verifier folder_paths et MODEL_EXTENSIONS');
      } else {
        console.log('[FR.IA] /fria/models/list: ' + total + ' fichiers dans ' + Object.keys(data).length + ' categories');
      }
      return data;
    } catch(e) { console.error('[FR.IA] getLocalModelFiles error:', e); return {}; }
  }

  async function uploadModelToServer(filepath, fileType) {
    // Demande au Python d'uploader le fichier directement depuis le filesystem
    try {
      var resp = await fetch('/fria/models/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: filepath, type: fileType })
      });
      return await resp.json();
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  async function downloadModelFromServer(uploadId, filename, fileType) {
    // Demande au Python de downloader et sauvegarder dans le bon dossier
    try {
      var resp = await fetch('/fria/models/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ upload_id: uploadId, filename: filename, type: fileType })
      });
      return await resp.json();
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  async function getLocalModels() {
    try {
      var resp = await fetch('/object_info/CheckpointLoaderSimple');
      if (!resp.ok) return [];
      var data = await resp.json();
      var info = data.CheckpointLoaderSimple;
      if (info && info.inputs && info.inputs.required && info.inputs.required.ckpt_name) {
        return info.inputs.required.ckpt_name[0] || [];
      }
      return [];
    } catch { return []; }
  }

  async function getLocalLoras() {
    try {
      var resp = await fetch('/object_info/LoraLoader');
      if (!resp.ok) return [];
      var data = await resp.json();
      var info = data.LoraLoader;
      if (info && info.inputs && info.inputs.required && info.inputs.required.lora_name) {
        return info.inputs.required.lora_name[0] || [];
      }
      return [];
    } catch { return []; }
  }

  // ── Modale unique ──

  window.openWorkflowManager = function () {
    var _m = friaOpenModal("📤  Workflows", "", "680px");
    var modal = _m.modal;
    var body = _m.body;

    var currentTab = "share";
    var browseState = { page: 1, query: "", sort: "downloads" };

    function updateTabStyles() {
      var shareBtn = body.querySelector("#wf-tab-share");
      var browseBtn = body.querySelector("#wf-tab-browse");
      if (shareBtn) {
        shareBtn.style.borderBottomColor = currentTab === "share" ? "#6366f1" : "transparent";
        shareBtn.style.color = currentTab === "share" ? "#e2e8f0" : "#888";
        shareBtn.style.fontWeight = currentTab === "share" ? "600" : "400";
      }
      if (browseBtn) {
        browseBtn.style.borderBottomColor = currentTab === "browse" ? "#6366f1" : "transparent";
        browseBtn.style.color = currentTab === "browse" ? "#e2e8f0" : "#888";
        browseBtn.style.fontWeight = currentTab === "browse" ? "600" : "400";
      }
    }

    function render() {
      body.innerHTML =
        '<div style="display:flex;flex-direction:column;gap:10px;min-height:350px;">' +
        // Tab bar
        '<div style="display:flex;gap:0;border-bottom:1px solid #444;">' +
        '<button id="wf-tab-share" style="flex:1;padding:8px;border:none;border-bottom:2px solid ' +
        (currentTab === "share" ? "#6366f1" : "transparent") + ';background:transparent;color:' +
        (currentTab === "share" ? "#e2e8f0" : "#888") + ';font-size:13px;font-weight:' +
        (currentTab === "share" ? "600" : "400") + ';cursor:pointer;">📤 Partager</button>' +
        '<button id="wf-tab-browse" style="flex:1;padding:8px;border:none;border-bottom:2px solid ' +
        (currentTab === "browse" ? "#6366f1" : "transparent") + ';background:transparent;color:' +
        (currentTab === "browse" ? "#e2e8f0" : "#888") + ';font-size:13px;font-weight:' +
        (currentTab === "browse" ? "600" : "400") + ';cursor:pointer;">🌐 Parcourir</button>' +
        '</div>' +
        '<div id="wf-tab-content" style="flex:1;"></div>' +
        '</div>';

      body.querySelector("#wf-tab-share").onclick = function () { currentTab = "share"; renderTab(); updateTabStyles(); };
      body.querySelector("#wf-tab-browse").onclick = function () { currentTab = "browse"; renderTab(); updateTabStyles(); };
      renderTab();
    }

    function renderTab() {
      var container = body.querySelector("#wf-tab-content");
      if (currentTab === "share") renderShareTab(container);
      else renderBrowseTab(container);
    }

    // ═══════════════════════════════════════════════
    //  TAB 1 : PARTAGER
    // ═══════════════════════════════════════════════

    async function renderShareTab(container) {
      // Lire le workflow actif
      var workflowStr = "";
      var workflowJSON = null;
      try {
        var currentApp = getApp();
        if (currentApp && currentApp.graph) {
          workflowJSON = currentApp.graph.serialize();
          workflowStr = JSON.stringify(workflowJSON, null, 2);
        }
      } catch (e) { workflowStr = ""; }

      if (!workflowStr) {
        container.innerHTML = '<p style="color:#f87171;font-size:13px;text-align:center;padding:30px 0;">Impossible de lire le workflow actif.</p>';
        return;
      }

      var deps = await detectDependencies(workflowJSON);
      var existingId = null;

      container.innerHTML =
        '<div style="display:flex;flex-direction:column;gap:10px;">' +
        '<div><label style="font-size:11px;color:#888;display:block;margin-bottom:3px;">Nom *</label>' +
        '<input id="wf-name" type="text" value="' + esc(workflowJSON?.extra?.title || workflowJSON?.title || workflowJSON?.name || '') + '" style="width:100%;padding:6px 8px;border-radius:4px;border:1px solid #555;background:#3a3a3e;color:#ccc;font-size:13px;box-sizing:border-box;"></div>' +
        '<div><label style="font-size:11px;color:#888;display:block;margin-bottom:3px;">Description</label>' +
        '<textarea id="wf-desc" rows="2" style="width:100%;padding:6px 8px;border-radius:4px;border:1px solid #555;background:#3a3a3e;color:#ccc;font-size:12px;box-sizing:border-box;resize:vertical;"></textarea></div>' +
        '<div><label style="font-size:11px;color:#888;display:block;margin-bottom:3px;">Tags (virgules)</label>' +
        '<input id="wf-tags" type="text" style="width:100%;padding:6px 8px;border-radius:4px;border:1px solid #555;background:#3a3a3e;color:#ccc;font-size:13px;box-sizing:border-box;"></div>' +
        '<div id="wf-deps" style="font-size:12px;color:#bbb;border-top:1px solid #444;padding-top:8px;"></div>' +
        '<button id="wf-publish-btn" style="padding:8px;border:none;border-radius:6px;background:#6366f1;color:#fff;font-size:13px;font-weight:600;cursor:pointer;">📤 Publier</button>' +
        '<div id="wf-status" style="font-size:11px;color:#888;display:none;"></div>' +
        '</div>';

      // Remplir les dépendances avec checkboxes d'upload
      var depsHtml = '<p style="font-size:11px;color:#888;margin:0 0 6px 0;">🔍 Dépendances détectées :</p>';
      if (deps.nodes.length === 0 && deps.models.length === 0 && deps.loras.length === 0) {
        depsHtml += '<span style="color:#34d399;">✓ Aucune dépendance externe</span>';
      } else {
        depsHtml += '<p style="font-size:10px;color:#666;margin:0 0 6px 0;">Cochez les models/loras à uploader vers le serveur :</p>';
        if (deps.nodes.length) {
          depsHtml += '<div style="margin-bottom:4px;"><span style="color:#f59e0b;">📦 Custom nodes (' + deps.nodes.length + (deps.nodes.length > 1 ? ' packs' : ' pack') + ')</span>';
          for (var i = 0; i < deps.nodes.length; i++) {
            var pk = deps.nodes[i];
            var nodeCount = pk.node_types ? pk.node_types.length : 1;
            depsHtml += '<div style="margin-left:12px;color:#ccc;">· ' + esc(pk.name) +
              (nodeCount > 1 ? ' <span style="color:#888;font-size:10px;">(' + nodeCount + ' nodes)</span>' : '') +
              (pk.url ? ' <span style="color:#34d399;font-size:10px;">✓ ' + esc(pk.url) + '</span>' : ' <span style="color:#f87171;font-size:10px;">URL git non trouvée</span>') +
              '</div>';
          }
          depsHtml += '</div>';
        }
        if (deps.models.length) {
          depsHtml += '<div style="margin-bottom:4px;"><span style="color:#60a5fa;">🧠 Modèles</span>';
          for (var i = 0; i < deps.models.length; i++) {
            var m = deps.models[i];
            depsHtml += '<label style="display:flex;align-items:center;gap:6px;margin-left:12px;color:#ccc;cursor:pointer;font-size:11px;">' +
              '<input type="checkbox" class="wf-upload-cb" data-type="' + esc(m.type || 'model') + '" data-name="' + esc(m.name) + '" style="accent-color:#6366f1;">' +
              '<span style="flex:1;">' + esc(m.name) + '</span></label>';
          }
          depsHtml += '</div>';
        }
        if (deps.loras.length) {
          depsHtml += '<div style="margin-bottom:4px;"><span style="color:#a78bfa;">🎨 LoRAs</span>';
          for (var i = 0; i < deps.loras.length; i++) {
            var l = deps.loras[i];
            depsHtml += '<label style="display:flex;align-items:center;gap:6px;margin-left:12px;color:#ccc;cursor:pointer;font-size:11px;">' +
              '<input type="checkbox" class="wf-upload-cb" data-type="lora" data-name="' + esc(l.name) + '" style="accent-color:#6366f1;">' +
              '<span style="flex:1;">' + esc(l.name) + '</span></label>';
          }
          depsHtml += '</div>';
        }
      }
      container.querySelector("#wf-deps").innerHTML = depsHtml;

      // Vérifier si un workflow du même nom existe déjà
      function checkExisting(name) {
        fetch(getApiUrl() + "/workflows?q=" + encodeURIComponent(name) + "&limit=5", { headers: apiHeaders() })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            var items = data?.items || [];
            fetch(getApiUrl() + "/auth/me", { headers: apiHeaders() })
              .then(function (r) { return r.json(); })
              .then(function (me) {
                if (!me || typeof me.id !== 'string') return;
                for (var i = 0; i < items.length; i++) {
                  if (items[i].name.toLowerCase() === name.toLowerCase() && items[i].user_id === me.id) {
                    existingId = items[i].id;
                    var btn = container.querySelector("#wf-publish-btn");
                    btn.textContent = "🔄 Mettre à jour (v" + (items[i].version + 1) + ")";
                    btn.style.background = "#f59e0b";
                    return;
                  }
                }
                existingId = null;
                var btn = container.querySelector("#wf-publish-btn");
                btn.textContent = "📤 Publier";
                btn.style.background = "#6366f1";
              });
          });
      }

      container.querySelector("#wf-name").addEventListener("input", function () {
        checkExisting(this.value.trim());
      });
      // Check existing on load too
      var initialName = container.querySelector("#wf-name").value.trim();
      if (initialName) checkExisting(initialName);

      // Publish
      container.querySelector("#wf-publish-btn").onclick = async function () {
        var name = container.querySelector("#wf-name").value.trim();
        var desc = container.querySelector("#wf-desc").value.trim();
        var tags = container.querySelector("#wf-tags").value.trim();
        var statusEl = container.querySelector("#wf-status");
        statusEl.style.display = "block";
        statusEl.style.color = "#fbbf24";
        statusEl.textContent = "Capture de l'aperçu...";

        // 📸 Capture du canvas ComfyUI
        var thumbnail = "";
        try {
          var currentApp = getApp();
          var canvas = null;
          if (currentApp && currentApp.canvas && currentApp.canvas.canvas) {
            canvas = currentApp.canvas.canvas;
          } else if (window.canvasEl) {
            canvas = window.canvasEl;
          }
          if (canvas && canvas.toDataURL) {
            // Redimensionner pour limiter la taille (max 400px de large)
            var tmpCanvas = document.createElement("canvas");
            var maxW = 400;
            var scale = Math.min(1, maxW / canvas.width);
            tmpCanvas.width = Math.round(canvas.width * scale);
            tmpCanvas.height = Math.round(canvas.height * scale);
            var ctx = tmpCanvas.getContext("2d");
            ctx.fillStyle = "#2a2a2e";
            ctx.fillRect(0, 0, tmpCanvas.width, tmpCanvas.height);
            ctx.drawImage(canvas, 0, 0, tmpCanvas.width, tmpCanvas.height);
            thumbnail = tmpCanvas.toDataURL("image/jpeg", 0.7);
          }
        } catch (e) {
          console.warn("[FR.IA] Screenshot failed:", e);
        }

        statusEl.textContent = "Publication...";

        // Uploader les models/loras cochés vers le serveur FR.IA
        var uploadCbs = container.querySelectorAll(".wf-upload-cb:checked");
        if (uploadCbs.length > 0) {
          var localFiles = await getLocalModelFiles();
          // Construire un map global: filename → {name, path, size}
          var allFilesMap = {};
          for (var cat in localFiles) {
            var catFiles = localFiles[cat];
            for (var fi = 0; fi < catFiles.length; fi++) {
              allFilesMap[catFiles[fi].name] = catFiles[fi];
            }
          }

          for (var ui = 0; ui < uploadCbs.length; ui++) {
            var cb = uploadCbs[ui];
            var fileType = cb.dataset.type;
            var fileName = cb.dataset.name;
            var localFile = allFilesMap[fileName];
            // Fallback: le workflow peut avoir "gguf/krea2_turbo.gguf" mais la BDD juste "krea2_turbo.gguf"
            if (!localFile && fileName.indexOf('/') >= 0) {
              var base = fileName.substring(fileName.lastIndexOf('/') + 1);
              localFile = allFilesMap[base];
              if (localFile) console.log('[FR.IA] Resolved subfolder: ' + fileName + ' → ' + base);
            }

            if (!localFile) {
              friaToast("⚠️ " + fileName + " non trouvé localement, skip", "info");
              continue;
            }

            var sizeMB = (localFile.size / 1048576).toFixed(1);
            var toast = friaToast("Upload " + fileName + " (" + sizeMB + " MB)...", "progress");

            var upResult = await uploadModelToServer(localFile.path, fileType);
            if (upResult.success) {
              // Lier l'upload_id au model/lora dans deps
              var depArray = fileType === 'lora' ? deps.loras : deps.models;
              for (var di = 0; di < depArray.length; di++) {
                if (depArray[di].name === fileName) {
                  depArray[di].upload_id = upResult.upload_id;
                  depArray[di].file_path = upResult.file_path;
                  break;
                }
              }
              friaToastDone(toast, "success", "✅ " + fileName + " uploadé" + (upResult.deduplicated ? " (déjà présent)" : ""));
            } else {
              friaToastDone(toast, "error", "❌ " + fileName + ": " + (upResult.error || "échec"));
            }
          }
        }

        statusEl.textContent = "Publication...";
        var payload = {
          name: name, description: desc, tags: tags,
          workflow_json: workflowStr,
          required_nodes: deps.nodes,
          required_models: deps.models,
          required_loras: deps.loras,
          thumbnail: thumbnail,
        };
        if (existingId) payload.existing_id = existingId;

        fetch(getApiUrl() + "/workflows", {
          method: "POST", headers: apiHeaders(),
          body: JSON.stringify(payload),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.error) throw new Error(data.error);
            statusEl.style.color = "#34d399";
            statusEl.textContent = existingId ? "✅ Mis à jour !" : "✅ Publié !";
            setTimeout(function () { statusEl.textContent = ""; statusEl.style.display = "none"; }, 2000);
          })
          .catch(function (e) {
            statusEl.style.color = "#f87171";
            statusEl.textContent = "❌ " + e.message;
          });
      };
    }

    // ═══════════════════════════════════════════════
    //  TAB 2 : PARCOURIR
    // ═══════════════════════════════════════════════

    async function renderBrowseTab(container, ctx) {
      ctx = ctx || browseState;
      var q = encodeURIComponent(ctx.query);
      var s = encodeURIComponent(ctx.sort);
      var url = getApiUrl() + "/workflows?q=" + q + "&sort=" + s + "&page=" + ctx.page + "&limit=20";

      // Injecter les styles CSS pour le hover des cards
      if (!document.getElementById("wf-browse-styles")) {
        var s = document.createElement("style");
        s.id = "wf-browse-styles";
        s.textContent = '.wf-card:hover .wf-del-btn { display: block !important; }';
        document.head.appendChild(s);
      }

      container.innerHTML =
        '<div style="display:flex;flex-direction:column;gap:8px;min-height:300px;">' +
        '<div style="display:flex;gap:8px;">' +
        '<input id="wf-search" type="text" placeholder="🔍 Rechercher..." value="' + esc(ctx.query) + '" style="flex:1;padding:6px 8px;border-radius:4px;border:1px solid #555;background:#3a3a3e;color:#ccc;font-size:13px;">' +
        '<select id="wf-sort" style="padding:6px 8px;border-radius:4px;border:1px solid #555;background:#3a3a3e;color:#ccc;font-size:12px;">' +
        '<option value="downloads"' + (ctx.sort === "downloads" ? " selected" : "") + '>📥 DL</option>' +
        '<option value="likes"' + (ctx.sort === "likes" ? " selected" : "") + '>❤️ Likes</option>' +
        '<option value="created_at"' + (ctx.sort === "created_at" ? " selected" : "") + '>📅 Date</option>' +
        '</select></div>' +
        '<div id="wf-list" style="flex:1;"><p style="color:#888;font-size:13px;text-align:center;padding:30px 0;">Chargement...</p></div>' +
        '<div id="wf-pages" style="display:flex;justify-content:center;gap:6px;"></div>' +
        '</div>';

      container.querySelector("#wf-search").addEventListener("input", function () {
        clearTimeout(window._wfSearchTimer);
        window._wfSearchTimer = setTimeout(function () {
          ctx.query = container.querySelector("#wf-search").value.trim();
          ctx.page = 1;
          renderBrowseTab(container, ctx);
        }, 300);
      });

      container.querySelector("#wf-sort").addEventListener("change", function () {
        ctx.sort = container.querySelector("#wf-sort").value;
        ctx.page = 1;
        renderBrowseTab(container, ctx);
      });

      fetch(url, { headers: apiHeaders() })
        .then(function (r) { return r.json(); })
        .then(async function (data) {
          var items = data?.items || [];
          var total = data?.total || 0;
          var pages = Math.ceil(total / 20);
          var listEl = container.querySelector("#wf-list");

          if (items.length === 0) {
            listEl.innerHTML = '<p style="color:#888;font-size:13px;text-align:center;padding:30px 0;">Aucun workflow trouvé.</p>';
            return;
          }

          var html = "";
          for (var i = 0; i < items.length; i++) {
            var w = items[i];
            var author = w.author || w.user_id || "?";
            var depsCount = (w.required_nodes?.length || 0) + (w.required_models?.length || 0) + (w.required_loras?.length || 0);
            var delHtml =
              '<button class="wf-del-btn" data-wf-id="' + w.id + '" data-wf-name="' + esc(w.name) + '" onclick="event.stopPropagation();window._wfDeleteWorkflow(this)" style="position:absolute;top:4px;right:4px;width:22px;height:22px;border:1px solid #555;border-radius:4px;background:rgba(60,60,64,0.9);color:#f87171;font-size:11px;cursor:pointer;padding:0;line-height:20px;text-align:center;z-index:2;display:none;">🗑</button>';
            html +=
              '<div class="wf-card" style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid #444;border-radius:6px;margin-bottom:4px;cursor:pointer;background:#3a3a3e;position:relative;"' +

              ' onclick="window._wfOpenDetail(' + w.id + ', this)">' +
              delHtml +
              (w.thumbnail ? '<img src="' + w.thumbnail + '" style="width:48px;height:48px;border-radius:4px;object-fit:cover;flex-shrink:0;">' : '<div style="width:48px;height:48px;border-radius:4px;background:#444;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0;">📤</div>') +
              '<div style="flex:1;min-width:0;">' +
              '<div style="font-size:13px;font-weight:600;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + esc(w.name) + '</div>' +
              '<div style="font-size:11px;color:#888;">par ' + esc(author) + (depsCount > 0 ? ' · ' + depsCount + ' dép.' : '') + '</div></div>' +
              '<div style="text-align:right;font-size:11px;color:#888;white-space:nowrap;">' +
              '❤️ ' + (w.likes || 0) + ' 📥 ' + (w.downloads || 0) + ' <span style="color:#666;">v' + (w.version || 1) + '</span></div></div>';
          }
          listEl.innerHTML = html;

          // Pagination
          var pagEl = container.querySelector("#wf-pages");
          if (pages > 1) {
            var pagHtml = "";
            if (ctx.page > 1)
              pagHtml += '<button onclick="window._wfGoPage(' + (ctx.page - 1) + ')" style="padding:4px 10px;border:1px solid #555;border-radius:4px;background:#3a3a3e;color:#ccc;cursor:pointer;font-size:12px;">←</button>';
            pagHtml += '<span style="font-size:12px;color:#888;padding:4px 8px;">' + ctx.page + ' / ' + pages + '</span>';
            if (ctx.page < pages)
              pagHtml += '<button onclick="window._wfGoPage(' + (ctx.page + 1) + ')" style="padding:4px 10px;border:1px solid #555;border-radius:4px;background:#3a3a3e;color:#ccc;cursor:pointer;font-size:12px;">→</button>';
            pagEl.innerHTML = pagHtml;
          }
        })
        .catch(function () {
          container.querySelector("#wf-list").innerHTML = '<p style="color:#f87171;font-size:13px;text-align:center;padding:30px 0;">Erreur de chargement.</p>';
        });
    }

    // ── Detail / Install (global pour les onclick HTML) ──

    window._wfGoPage = function (page) {
      browseState.page = page;
      render();
    };

    window._wfDeleteWorkflow = async function(btn) {
      var id = parseInt(btn.getAttribute("data-wf-id"));
      var name = btn.getAttribute("data-wf-name") || "?";
      if (!confirm("Supprimer le workflow \x22" + name + "\x22 ?\n\nLes models associés non utilisés par d\'autres workflows seront aussi supprimés.")) return;
      btn.textContent = "⏳";
      try {
        var resp = await fetch(getApiUrl() + "/workflows/" + id, { method: "DELETE", headers: apiHeaders() });
        var data = await resp.json();
        if (data.error) throw new Error(data.error);
        var card = btn.closest('[class*="wf-card"]');
        if (card) { card.style.transition = "opacity 0.3s, transform 0.3s"; card.style.opacity = "0"; card.style.transform = "scale(0.9)"; setTimeout(function() { if (card) card.remove(); }, 300); }
        friaToast('Workflow "' + name + '" supprimé' + (data.deleted_files && data.deleted_files.length ? ' (' + data.deleted_files.length + ' fichiers orphelins supprimés)' : ''), "success");
      } catch (e) {
        friaToast("Erreur: " + e.message, "error");
      }
    };

    window.friaOpenModalClose = function(btn) {
      var modal = btn.closest('[style*="position: fixed"]');
      if (modal) modal.remove();
    };

    window._wfOpenDetail = function (workflowId) {
      var _dm = friaOpenModal("📥 Workflow", "", "580px");
      var detailModal = _dm.modal;
      var detailBody = _dm.body;
      detailBody.innerHTML = '<p style="color:#888;font-size:13px;text-align:center;padding:30px 0;">Chargement...</p>';

      fetch(getApiUrl() + "/workflows/" + workflowId, { headers: apiHeaders() })
        .then(function (r) { return r.json(); })
        .then(function (w) {
          var html =
            '<div style="margin-bottom:12px;">' +
            '<h2 style="font-size:16px;font-weight:700;color:#e2e8f0;margin:0 0 4px 0;">' + esc(w.name) + '</h2>' +
            '<p style="font-size:12px;color:#888;margin:0;">par ' + esc(w.author || w.user_id) + ' · v' + (w.version || 1) +
            ' · ❤️ ' + (w.likes || 0) + ' · 📥 ' + (w.downloads || 0) + '</p>' +
            (w.description ? '<p style="font-size:12px;color:#aaa;margin:8px 0 0 0;">' + esc(w.description) + '</p>' : '') +
            '</div>' +
            '<div id="wf-install-deps" style="margin-bottom:12px;"></div>' +
            '<div style="display:flex;gap:8px;">' +
            '<button id="wf-load-btn" style="flex:1;padding:10px;border:none;border-radius:6px;background:#6366f1;color:#fff;font-size:13px;font-weight:600;cursor:pointer;">📥 Charger le workflow</button>' +
            '<button onclick="friaOpenModalClose(this)" style="padding:10px 16px;border:1px solid #555;border-radius:6px;background:transparent;color:#999;font-size:13px;cursor:pointer;">Fermer</button></div>' +
            '<div id="wf-load-status" style="font-size:11px;color:#888;display:none;margin-top:8px;"></div>';

          detailBody.innerHTML = html;

          // Dépendances — vérifier les models/loras locaux en async
          var allDeps = {
            nodes: w.required_nodes || [],
            models: w.required_models || [],
            loras: w.required_loras || [],
          };
          var totalDeps = allDeps.nodes.length + allDeps.models.length + allDeps.loras.length;
          var depsEl = detailBody.querySelector("#wf-install-deps");

          if (totalDeps === 0) {
            depsEl.innerHTML = '<p style="font-size:12px;color:#34d399;">✓ Aucune dépendance externe</p>';
          } else {
            depsEl.innerHTML = '<p style="font-size:12px;color:#888;">Vérification des dépendances locales...</p>';
            
            // Interroger ComfyUI pour les models/loras déjà installés
            Promise.all([getLocalModels(), getLocalLoras()]).then(async function(results) {
              var localModels = results[0];
              var localLoras = results[1];
              
              var depHtml = '<p style="font-size:12px;color:#fbbf24;margin:0 0 8px 0;">⚠️ Dépendances requises :</p>';
              depHtml += '<div style="border:1px solid #444;border-radius:6px;overflow:hidden;">';

              if (allDeps.nodes.length) {
                // Check which custom node packs are already installed (by git_url)
                var installedNodes = await getInstalledCustomNodes();
                var installedUrls = {};
                for (var k = 0; k < installedNodes.length; k++) {
                  if (installedNodes[k].git_url) {
                    installedUrls[installedNodes[k].git_url] = true;
                  }
                }
                depHtml += '<div style="background:#3a3a3e;padding:6px 10px;border-bottom:1px solid #444;"><span style="font-size:11px;color:#f59e0b;font-weight:600;">📦 Custom nodes</span></div>';
                for (var i = 0; i < allDeps.nodes.length; i++) {
                  var n = allDeps.nodes[i];
                  var nodeCount = n.node_types ? n.node_types.length : 1;
                  var installed = n.url && installedUrls[n.url];
                  depHtml += '<label style="display:flex;align-items:center;gap:8px;padding:6px 10px;border-bottom:1px solid #3a3a3e;cursor:pointer;font-size:12px;color:' + (installed ? '#34d399' : '#ccc') + ';">' +
                    '<input type="checkbox" class="wf-dep-cb" ' + (installed ? 'unchecked' : 'checked') + ' data-type="node" data-name="' + esc(n.name) + '" data-url="' + esc(n.url || '') + '" style="accent-color:#6366f1;">' +
                    '<span style="flex:1;">' + esc(n.name) +
                    (nodeCount > 1 ? ' <span style="color:#888;font-size:10px;">(' + nodeCount + ' nodes)</span>' : '') +
                    (installed ? ' ✅ déjà installé' : '') + '</span>' +
                    (n.url && !installed ? '<button onclick="window._wfInstallNode(\'' + esc(n.url) + '\', \'' + esc(n.name) + '\', this)" style="padding:2px 8px;border:1px solid #555;border-radius:3px;background:#4a4a4e;color:#ccc;font-size:10px;cursor:pointer;">📥 Installer</button>' : '') +
                    (n.url ? '<a href="' + esc(n.url) + '" target="_blank" style="color:#60a5fa;text-decoration:none;font-size:11px;" onclick="event.stopPropagation();">🔗</a>' : '') +
                    '</label>';
                }
              }
              if (allDeps.models.length) {
                if (allDeps.nodes.length) depHtml += '<div style="border-top:1px solid #444;"></div>';
                depHtml += '<div style="background:#3a3a3e;padding:6px 10px;border-bottom:1px solid #444;"><span style="font-size:11px;color:#60a5fa;font-weight:600;">🧠 Modèles</span></div>';
                for (var i = 0; i < allDeps.models.length; i++) {
                  var m = allDeps.models[i];
                  var installed = localModels.indexOf(m.name) >= 0;
                  var hasFile = !!m.upload_id;
                  depHtml += '<label style="display:flex;align-items:center;gap:8px;padding:6px 10px;border-bottom:1px solid #3a3a3e;cursor:pointer;font-size:12px;color:' + (installed ? '#34d399' : '#ccc') + ';">' +
                    '<input type="checkbox" class="wf-dep-cb" ' + (installed ? 'unchecked' : 'checked') + ' data-type="model" data-name="' + esc(m.name) + '" ' + (hasFile ? 'data-upload-id="' + esc(m.upload_id) + '"' : '') + ' style="accent-color:#6366f1;">' +
                    '<span style="flex:1;">' + esc(m.name) + (installed ? ' ✅ déjà installé' : '') + '</span>';
                  if (!installed && hasFile) {
                    depHtml += '<button onclick="window._wfDownloadFile(\'' + esc(m.upload_id) + '\', \'' + esc(m.name) + '\', this)" style="padding:2px 8px;border:1px solid #555;border-radius:3px;background:#4a4a4e;color:#ccc;font-size:10px;cursor:pointer;">📥 Télécharger</button>';
                  } else if (!installed && !hasFile) {
                    depHtml += '<span style="font-size:10px;color:#666;">non uploadé</span>';
                  }
                  depHtml += '<span style="font-size:10px;color:#666;">' + (m.type || 'modèle') + '</span></label>';
                }
              }
              if (allDeps.loras.length) {
                if (allDeps.nodes.length || allDeps.models.length) depHtml += '<div style="border-top:1px solid #444;"></div>';
                depHtml += '<div style="background:#3a3a3e;padding:6px 10px;border-bottom:1px solid #444;"><span style="font-size:11px;color:#a78bfa;font-weight:600;">🎨 LoRAs</span></div>';
                for (var i = 0; i < allDeps.loras.length; i++) {
                  var l = allDeps.loras[i];
                  var installed = localLoras.indexOf(l.name) >= 0;
                  var hasFile = !!l.upload_id;
                  depHtml += '<label style="display:flex;align-items:center;gap:8px;padding:6px 10px;border-bottom:1px solid #3a3a3e;cursor:pointer;font-size:12px;color:' + (installed ? '#34d399' : '#ccc') + ';">' +
                    '<input type="checkbox" class="wf-dep-cb" ' + (installed ? 'unchecked' : 'checked') + ' data-type="lora" data-name="' + esc(l.name) + '" ' + (hasFile ? 'data-upload-id="' + esc(l.upload_id) + '"' : '') + ' style="accent-color:#6366f1;">' +
                    '<span style="flex:1;">' + esc(l.name) + (installed ? ' ✅ déjà installé' : '') + '</span>';
                  if (!installed && hasFile) {
                    depHtml += '<button onclick="window._wfDownloadFile(\'' + esc(l.upload_id) + '\', \'' + esc(l.name) + '\', this)" style="padding:2px 8px;border:1px solid #555;border-radius:3px;background:#4a4a4e;color:#ccc;font-size:10px;cursor:pointer;">📥 Télécharger</button>';
                  } else if (!installed && !hasFile) {
                    depHtml += '<span style="font-size:10px;color:#666;">non uploadé</span>';
                  }
                  depHtml += '</label>';
                }
              }
              depHtml += '</div>';
              depsEl.innerHTML = depHtml;
            });
          }

          // Download a file from server (global for onclick)
          window._wfDownloadFile = async function(uploadId, fileName, btn) {
            var fileType = btn.getAttribute('data-file-type') || 'model';
            btn.textContent = "⏳...";
            btn.disabled = true;
            var toast = friaToast("Download " + fileName + "...", "progress");
            try {
              var result = await downloadModelFromServer(uploadId, fileName, fileType);
              if (result.success) {
                btn.textContent = "✅ Installé";
                btn.style.color = "#34d399";
                btn.style.borderColor = "#34d399";
                friaToastDone(toast, "success", "✅ " + fileName + " installé");
              } else {
                btn.textContent = "❌";
                btn.style.color = "#f87171";
                friaToastDone(toast, "error", "❌ " + fileName + ": " + (result.error || "échec"));
              }
            } catch (e) {
              btn.textContent = "❌";
              btn.style.color = "#f87171";
              friaToastDone(toast, "error", "❌ " + fileName + ": " + e.message);
            }
          };

          // Install custom node (global for onclick)
          window._wfInstallNode = async function(gitUrl, nodeName, btn) {
            if (!gitUrl) { friaToast("Pas d URL git pour ce node.", "error"); return; }
            btn.textContent = "⏳ Clone...";
            btn.disabled = true;
            var toast = friaToast("Installation " + nodeName + "...", "progress");
            try {
              var resp = await fetch("/api/fria/custom-nodes/install", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({git_url: gitUrl, name: nodeName})
              });
              var data = await resp.json();
              if (data.success) {
                btn.textContent = "✅ Installé";
                btn.style.color = "#34d399";
                btn.style.borderColor = "#34d399";
                friaToastDone(toast, "success", "✅ " + nodeName + " installé");
              } else {
                btn.textContent = "❌";
                btn.style.color = "#f87171";
                friaToastDone(toast, "error", "❌ " + (data.message || "échec"));
              }
            } catch (e) {
              btn.textContent = "❌";
              btn.style.color = "#f87171";
              friaToastDone(toast, "error", "❌ " + e.message);
            }
          };

          // Load button
          detailBody.querySelector("#wf-load-btn").onclick = function () {
            var statusEl = detailBody.querySelector("#wf-load-status");
            statusEl.style.display = "block";
            statusEl.style.color = "#fbbf24";
            statusEl.textContent = "Téléchargement...";

            fetch(getApiUrl() + "/workflows/" + workflowId + "/download", { headers: apiHeaders() })
              .then(function (r) { return r.json(); })
              .then(function (data) {
                if (data.error) throw new Error(data.error);
                var wfJson = data.workflow_json;
                statusEl.textContent = "Chargement dans ComfyUI...";

                try {
                  var parsed = JSON.parse(wfJson);
                  // Renommer le workflow avec le nom qu'il a sur FR.IA
                  if (data.name) {
                    if (!parsed.extra) parsed.extra = {};
                    parsed.extra.title = data.name;
                  }
                  var currentApp = getApp();
                  if (currentApp && currentApp.loadGraphData) {
                    currentApp.loadGraphData(parsed).then(function () {
                      statusEl.style.color = "#34d399";
                      statusEl.textContent = "✅ Workflow chargé !";
                      setTimeout(function () { detailModal.remove(); }, 1500);
                    }).catch(function (err) {
                      statusEl.style.color = "#f87171";
                      statusEl.textContent = "❌ Erreur de chargement : " + err.message;
                    });
                  } else if (currentApp && currentApp.graph) {
                    currentApp.graph.clear();
                    currentApp.loadGraphData(parsed);
                    statusEl.style.color = "#34d399";
                    statusEl.textContent = "✅ Workflow chargé !";
                    setTimeout(function () { detailModal.remove(); }, 1500);
                  } else {
                    navigator.clipboard.writeText(wfJson).then(function () {
                      statusEl.style.color = "#fbbf24";
                      statusEl.textContent = "⚠️ Copié dans le presse-papier.";
                    }).catch(function () {
                      statusEl.style.color = "#f87171";
                      statusEl.textContent = "❌ Impossible de charger.";
                    });
                  }
                } catch (e) {
                  statusEl.style.color = "#f87171";
                  statusEl.textContent = "❌ Erreur : " + e.message;
                }
              })
              .catch(function (e) {
                statusEl.style.color = "#f87171";
                statusEl.textContent = "❌ " + e.message;
              });
          };
        })
        .catch(function () {
          detailBody.innerHTML = '<p style="color:#f87171;font-size:13px;text-align:center;padding:30px 0;">Erreur de chargement.</p>';
        });
    };

    render();
  };
})();
