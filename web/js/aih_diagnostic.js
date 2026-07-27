/**
 * AIH Diagnostic Node — Debug onExecuted et DOM widget textarea.
 *
 * But : reproduire au minimum le pattern du Elements Picker pour isoler
 * le bug de mise à jour du textarea après exécution.
 *
 * Logs console : cherche les messages [DIAG] dans le F12 → Console.
 */
(function waitForApp() {
    const app = window.app || window.comfyAPI?.app?.app;
    if (!app) { setTimeout(waitForApp, 100); return; }

    app.registerExtension({
        name: "AIH.Diagnostic",
        async beforeRegisterNodeDef(nodeType, nodeData) {
            if (nodeData.name !== "AIHDiagnosticNode") return;

            console.log("[DIAG] beforeRegisterNodeDef OK —", nodeData.name);

            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                const r = onNodeCreated?.apply(this, arguments);
                const node = this;
                console.log("[DIAG] onNodeCreated — node.id:", node.id,
                    "type:", node.type);

                // Rendre le widget _diag_json invisible
                const hideWidget = (n, name) => {
                    const w = n.widgets?.find(x => x.name === name);
                    if (w) { w.hidden = true; w.computeSize = () => [0, -4]; }
                };
                hideWidget(node, "_diag_json");

                // ---- Container ----
                const container = document.createElement("div");
                Object.assign(container.style, {
                    width: "100%", padding: "8px",
                    background: "#2a2a2e", borderRadius: "8px",
                    boxSizing: "border-box", fontSize: "12px", color: "#ccc",
                    display: "flex", flexDirection: "column", gap: "6px",
                });

                // ---- Label ----
                const label = document.createElement("div");
                label.textContent = "🔬 Diagnostic";
                Object.assign(label.style, {
                    fontSize: "13px", fontWeight: "bold", color: "#fbbf24",
                });
                container.appendChild(label);

                // ---- Mode selector (via _diag_json) ----
                const modeSelect = document.createElement("select");
                ["hello", "short", "medium", "long", "special"].forEach(m => {
                    const opt = document.createElement("option");
                    opt.value = m; opt.textContent = m;
                    modeSelect.appendChild(opt);
                });
                Object.assign(modeSelect.style, {
                    padding: "3px 6px", borderRadius: "4px",
                    border: "1px solid #555", background: "#3a3a3e",
                    color: "#ccc", fontSize: "11px", cursor: "pointer", width: "100%",
                });
                modeSelect.onchange = () => {
                    const w = node.widgets?.find(x => x.name === "_diag_json");
                    if (w) w.value = JSON.stringify({ mode: modeSelect.value });
                };
                container.appendChild(modeSelect);

                // ---- Zone de résultat ----
                const result = document.createElement("textarea");
                Object.assign(result.style, {
                    width: "100%", height: "60px", minHeight: "60px",
                    borderRadius: "4px", border: "1px solid #555",
                    padding: "4px", background: "#1a1a1e", color: "#fff",
                    fontSize: "11px", resize: "none", boxSizing: "border-box",
                });
                result.placeholder = "Résultat...";
                result.readOnly = true;
                result.id = "diag-result-" + node.id;
                container.appendChild(result);

                // ---- Info zone ----
                const info = document.createElement("div");
                Object.assign(info.style, {
                    fontSize: "10px", color: "#888", whiteSpace: "pre-wrap",
                    fontFamily: "monospace",
                });
                info.id = "diag-info-" + node.id;
                info.textContent = "⏳ En attente d'exécution...";
                container.appendChild(info);

                // ---- Bouton "Log State" ----
                const btn = document.createElement("button");
                btn.textContent = "🔍 Log State";
                Object.assign(btn.style, {
                    padding: "4px 8px", borderRadius: "4px",
                    border: "1px solid #fbbf24", background: "transparent",
                    color: "#fbbf24", cursor: "pointer", fontSize: "11px",
                });
                btn.onclick = () => {
                    console.log("[DIAG] === STATE DUMP ===");
                    console.log("[DIAG] node._resultArea exists:", !!node._resultArea);
                    console.log("[DIAG] node._resultArea === result:", node._resultArea === result);
                    console.log("[DIAG] node.onExecuted type:", typeof node.onExecuted);
                    console.log("[DIAG] result.parentNode:", result.parentNode ? "OK" : "NULL");
                    console.log("[DIAG] result.value:", JSON.stringify(result.value).substring(0, 100));
                    const w = node.widgets?.find(x => x.name === "_diag_json");
                    console.log("[DIAG] _diag_json value:", w?.value);
                    // Check if the original was called
                    info.textContent = "🔍 Log envoyé (voir F12 → Console)";
                };
                container.appendChild(btn);

                // ---- Intégration DOM Widget ----
                const domWidget = node.addDOMWidget("diag_ui", "custom", container, {
                    getValue: () => "",
                    setValue: (v) => {},
                });
                domWidget.options = domWidget.options || {};
                domWidget.options.height = 200;

                // Stocker les refs
                node._resultArea = result;
                node._diagInfo = info;
                node._domWidget = domWidget;

                // ---- onExecuted SUR L'INSTANCE ----
                console.log("[DIAG] Instance onExecuted BEFORE — type:",
                    typeof node.onExecuted, "value:", node.onExecuted);

                const origExec = node.onExecuted; // null (mis par LiteGraph)
                node.onExecuted = function (output) {
                    console.log("[DIAG] ✅ onExecuted APPELÉ !");
                    console.log("[DIAG] JSON output:",
                        JSON.stringify(output).substring(0, 500));

                    if (origExec) origExec.call(this, output);

                    // HolafToText pattern: output = ui dict
                    // ex: {"diagnostic": ["Hello from AIH!"]}
                    const arr = output?.diagnostic;
                    if (Array.isArray(arr) && arr.length > 0) {
                        const text = String(arr[0]);
                        console.log("[DIAG] Setting result.value =",
                            JSON.stringify(text.substring(0, 100)));
                        if (node._resultArea) {
                            const oldVal = node._resultArea.value;
                            node._resultArea.value = text;
                            console.log("[DIAG] result.value updated:",
                                JSON.stringify(oldVal.substring(0, 50)),
                                "→", JSON.stringify(text.substring(0, 50)));
                            node._diagInfo.textContent =
                                "✅ " + text.substring(0, 60);
                        }
                    } else {
                        console.log("[DIAG] ❌ Format inattendu:",
                            JSON.stringify(output));
                        node._diagInfo.textContent =
                            "❌ Format inattendu: " +
                            JSON.stringify(output).substring(0, 80);
                    }
                };

                console.log("[DIAG] Instance onExecuted AFTER — type:",
                    typeof node.onExecuted, "!= null:", node.onExecuted !== null);

                // Sync initial
                const w = node.widgets?.find(x => x.name === "_diag_json");
                if (w) w.value = JSON.stringify({ mode: "hello" });

                return r;
            };
        },
    });
})();
