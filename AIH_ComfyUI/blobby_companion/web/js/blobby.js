import { app } from "../../../scripts/app.js";

// ─── Blobby ― Version Gooey ───
// Physique de particules inspirée de Gemini + rendu avec filtre liquide
// Yeux expressifs, cerveau palpitant, découpe à la souris
//
// Activation contrôlée depuis le menu AIH (aih_menu.js).
// Sauvegarde l'état dans localStorage.AIH_config.blobbyActive.

const AIH_CONFIG_KEY = "AIH_config";

function _getAIHConfig() {
    try { return JSON.parse(localStorage.getItem(AIH_CONFIG_KEY)) || {}; }
    catch { return {}; }
}

function _setAIHConfig(cfg) {
    localStorage.setItem(AIH_CONFIG_KEY, JSON.stringify(cfg));
}

const Blobby = {
    // ---- État ----
    x: 400,
    y: 300,
    vx: 0,
    vy: 0,
    scale: 1,

    // ── Particules ──
    bodyParticles: [],
    _fxParticles: [],
    NUM_PARTICLES: 60,
    GRAVITY: 0.003,
    DAMPING: 0.94,
    REPULSION: 0.4,
    COHESION: 0.04,
    CUT_RADIUS: 50,

    // Humeur / Expression
    mood: "happy",
    moodTimer: 0,
    mouthOpen: 0,
    targetMouthOpen: 0,

    // Yeux / Cerveau (suivent le centre de masse)
    organX: 400,
    organY: 300,
    organVx: 0,
    organVy: 0,

    // Souris
    mouseX: 0,
    mouseY: 0,
    mousePX: 0,
    mousePY: 0,
    mouseActive: false,

    // Clignement
    blinkTimer: 0,
    isBlinking: false,
    blinkFrame: 0,
    BLINK_DURATION: 8,

    followMouse: false,
    isDragging: false,
    dragPrevX: 0, dragPrevY: 0,
    throwVx: 0, throwVy: 0,
    dragDistance: 0,
    time: 0,
    nodeBounceCooldown: 0,

    // Zone visible actuelle (gérée en coordonnées graphiques)
    visibleArea: null,

    // Offscreen canvas pour le gooey (créé une seule fois)
    _offscreen: null,
    _octx: null,

    // ── Contrôle d'activation ──
    _active: false,
    _canvas: null,
    _animFrameId: null,
    _origDraw: null,
    _origMD: null,
    _origMM: null,
    _origMU: null,

    // ─── Initialisation ───
    init(canvas) {
        this._canvas = canvas;
        this.x = 400;
        this.y = 300;
        this.organX = this.x;
        this.organY = this.y;
        this.blinkTimer = 60 + Math.random() * 120;
        this.initParticles();

        // Offscreen canvas pour le rendu gooey
        this._offscreen = document.createElement('canvas');
        this._octx = this._offscreen.getContext('2d');

        // Sauvegarder les handlers canvas originaux
        this._origDraw = canvas.onDrawForeground;
        this._origMD = canvas.processMouseDown;
        this._origMM = canvas.processMouseMove;
        this._origMU = canvas.processMouseUp;
    },

    initParticles() {
        this.bodyParticles = [];
        const count = this.NUM_PARTICLES;
        for (let i = 0; i < count; i++) {
            const angle = Math.random() * Math.PI * 2;
            const r = Math.random() * 30;
            this.bodyParticles.push({
                x: this.x + Math.cos(angle) * r,
                y: this.y + Math.sin(angle) * r,
                vx: (Math.random() - 0.5) * 2,
                vy: (Math.random() - 0.5) * 2,
                radius: 16 + (Math.random() - 0.5) * 4
            });
        }
    },

    // ─── Activation / Désactivation (appelé depuis aih_menu.js) ───

    activate() {
        if (this._active) return;
        this._active = true;

        const canvas = this._canvas;
        if (!canvas) return;

        // Hook onDrawForeground
        canvas.onDrawForeground = (ctx, visibleArea) => {
            if (this._origDraw) {
                try { this._origDraw.apply(canvas, arguments); }
                catch (e) { console.error("[Blobby] Erreur origDraw:", e); }
            }
            try {
                let mouseGraph = null;
                if (canvas.graph_mouse) {
                    mouseGraph = [canvas.graph_mouse[0], canvas.graph_mouse[1]];
                }
                const currentVisibleArea = canvas.visible_area || visibleArea;
                this.update(1 / 60, app.graph?.nodes, mouseGraph, currentVisibleArea);
                this.draw(ctx);
            } catch (e) {
                console.error("[Blobby] Erreur draw:", e);
            }
        };

        // Hook mouse events
        canvas.processMouseDown = (e) => {
            if (this._origMD) this._origMD.apply(canvas, arguments);
            const gm = this._getGM(canvas);
            if (gm) this.onMouseDown(gm);
        };

        canvas.processMouseMove = (e) => {
            if (this._origMM) this._origMM.apply(canvas, arguments);
            if (this.isDragging) {
                const gm = this._getGM(canvas);
                if (gm) this.onMouseMove(gm);
            }
        };

        canvas.processMouseUp = (e) => {
            if (this._origMU) this._origMU.apply(canvas, arguments);
            const gm = this._getGM(canvas);
            if (gm) this.onMouseUp(gm);
        };

        // Pas de boucle rAF separee : Blobby est dessine via onDrawForeground
        // qui est deja appele par ComfyUI a chaque interaction utilisateur.
        this._animInterval = setInterval(function() {
            if (!Blobby._active) return;
            if (canvas.setDirty) canvas.setDirty(false, true);
        }, 33);

        console.log("%c🧡 Blobby activé !", "font-size: 16px; color: #FF8F00; font-weight: bold;");
    },

    deactivate() {
        if (!this._active) return;
        this._active = false;

        const canvas = this._canvas;
        if (!canvas) return;

        // Restaurer les handlers canvas originaux
        canvas.onDrawForeground = this._origDraw || null;
        canvas.processMouseDown = this._origMD || null;
        canvas.processMouseMove = this._origMM || null;
        canvas.processMouseUp = this._origMU || null;

        // Arrêter la boucle d'animation
        if (this._animFrameId) {
            cancelAnimationFrame(this._animFrameId);
            this._animFrameId = null;
        }

        // Remettre le canvas à jour pour effacer Blobby
        if (canvas.setDirty) canvas.setDirty(true, true);

        console.log("%c🧡 Blobby désactivé.", "font-size: 14px; color: #888;");
    },

    isActive() {
        return this._active;
    },

    _getGM(canvas) {
        return canvas.graph_mouse ? [canvas.graph_mouse[0], canvas.graph_mouse[1]] : null;
    },

    // ─── Mise à jour ───
    update(deltaTime, nodes, mousePosGraph, visibleArea) {
        this.time += deltaTime;
        this.moodTimer += deltaTime;
        this.visibleArea = visibleArea;

        // Humeur périodique
        if (this.moodTimer > 5 + Math.random() * 8) {
            const moods = ["happy", "happy", "happy", "surprised", "sleepy", "happy"];
            this.mood = moods[Math.floor(Math.random() * moods.length)];
            this.moodTimer = 0;
        }

        // Suivi souris
        if (mousePosGraph) {
            this.mousePX = this.mouseX;
            this.mousePY = this.mouseY;
            this.mouseX = mousePosGraph[0];
            this.mouseY = mousePosGraph[1];
            this.mouseActive = true;
        } else {
            this.mouseActive = false;
        }

        // Visible area (centre d'attraction)
        if (visibleArea) {
            const [vx, vy, vw, vh] = visibleArea;
            const cvx = vx + vw / 2, cvy = vy + vh / 2;
            const dx = cvx - this.x, dy = cvy - this.y;
            const dist = Math.hypot(dx, dy);
            const maxDist = Math.min(vw, vh) * 0.35;
            if (dist > maxDist && dist > 0.1) {
                const pull = (dist - maxDist) / dist * 0.03;
                this.x += dx * pull;
                this.y += dy * pull;
            }
        }

        // Physique des particules
        this.updateParticlePhysics();

        // Collision solide avec les nœuds
        this.updateNodeCollision(nodes);

        // Mise à jour du centre de masse (pour les organes)
        this.updateOrganPosition();

        // Drag (Déplacement)
        if (this.isDragging && mousePosGraph) {
            const dx = mousePosGraph[0] - this.x;
            const dy = mousePosGraph[1] - this.y;
            this.x = mousePosGraph[0];
            this.y = mousePosGraph[1];
            for (const p of this.bodyParticles) {
                p.x += dx;
                p.y += dy;
            }
            this.throwVx = dx / deltaTime;
            this.throwVy = dy / deltaTime;
            this.dragPrevX = this.x;
            this.dragPrevY = this.y;
            this.dragDistance += Math.hypot(dx, dy);
        }

        // Mouvement du centre (wandering)
        if (!this.isDragging) {
            this.x += this.vx;
            this.y += this.vy;
            this.vx *= 0.995;
            this.vy *= 0.995;
            if (Math.random() < 0.015) {
                this.vx += (Math.random() - 0.5) * 1.2;
                this.vy += (Math.random() - 0.5) * 1.2;
            }
            const speed = Math.hypot(this.vx, this.vy);
            if (speed > 5) { this.vx = (this.vx / speed) * 5; this.vy = (this.vy / speed) * 5; }
            if (this.mood === "sleepy") { this.vx *= 0.97; this.vy *= 0.97; }

            // Timide
            if (this.mouseActive) {
                const dx = this.mouseX - this.x;
                const dy = this.mouseY - this.y;
                const dist = Math.hypot(dx, dy);
                if (dist < 80 && dist > 0) {
                    this.vx -= (dx / dist) * 0.3 * (1 - dist / 80);
                    this.vy -= (dy / dist) * 0.3 * (1 - dist / 80);
                    if (this.mood !== "sleepy") this.mood = "surprised";
                }
                if (this.followMouse && dist > 10) {
                    this.vx += (dx / dist) * 0.3;
                    this.vy += (dy / dist) * 0.3;
                }
            }
        }

        // Bouche
        this.targetMouthOpen = this.mood === "surprised" ? 0.8 :
            this.mood === "sleepy" ? 0.1 :
                this.mood === "happy" ? 0.3 + Math.sin(this.time * 2) * 0.15 : 0.3;
        this.mouthOpen += (this.targetMouthOpen - this.mouthOpen) * 0.1;

        // Clignement
        this.blinkTimer += deltaTime * 60;
        if (!this.isBlinking && this.blinkTimer > 120 + Math.random() * 180) {
            this.isBlinking = true;
            this.blinkFrame = 0;
        }
        if (this.isBlinking) {
            this.blinkFrame++;
            if (this.blinkFrame >= this.BLINK_DURATION) {
                this.isBlinking = false;
                this.blinkTimer = 0;
            }
        }

        // Effets visuels
        this.updateParticles(deltaTime);
        if (this.mood === "sleepy" && Math.random() < 0.03) {
            this.addParticle(this.x - 15, this.y - 25, "zzz");
        }
    },

    // ─── Physique des particules ───
    updateParticlePhysics() {
        const cx = this.x;
        const cy = this.y;
        const numP = this.bodyParticles.length;

        // 1. Gravité vers le centre
        for (const p of this.bodyParticles) {
            const dx = cx - p.x;
            const dy = cy - p.y;
            const dist = Math.hypot(dx, dy);
            if (dist > 0.1) {
                const force = dist * this.GRAVITY;
                p.vx += (dx / dist) * force;
                p.vy += (dy / dist) * force;
            }
            p.vx *= this.DAMPING;
            p.vy *= this.DAMPING;
        }

        // 2. Interactions entre particules (répulsion + cohésion)
        for (let i = 0; i < numP; i++) {
            const p1 = this.bodyParticles[i];
            for (let j = i + 1; j < numP; j++) {
                const p2 = this.bodyParticles[j];
                const dx = p2.x - p1.x;
                const dy = p2.y - p1.y;
                const dist = Math.hypot(dx, dy);
                const minDist = p1.radius + p2.radius - 8;

                if (dist > 0.1) {
                    if (dist < minDist) {
                        const force = (minDist - dist) / minDist * this.REPULSION;
                        const fx = (dx / dist) * force;
                        const fy = (dy / dist) * force;
                        p1.vx -= fx; p1.vy -= fy;
                        p2.vx += fx; p2.vy += fy;
                    } else if (dist < minDist * 2.2) {
                        const force = (dist - minDist) / minDist * this.COHESION;
                        const fx = (dx / dist) * force;
                        const fy = (dy / dist) * force;
                        p1.vx += fx; p1.vy += fy;
                        p2.vx -= fx; p2.vy -= fy;
                    }
                }
            }
        }

        // 3. Découpe par la souris
        if (this.mouseActive) {
            const sdx = this.mouseX - this.mousePX;
            const sdy = this.mouseY - this.mousePY;
            const segLen = Math.hypot(sdx, sdy);

            if (segLen > 3) {
                const nx = -sdy / segLen;
                const ny = sdx / segLen;

                for (const p of this.bodyParticles) {
                    let t = ((p.x - this.mousePX) * sdx + (p.y - this.mousePY) * sdy) / (segLen * segLen);
                    t = Math.max(0, Math.min(1, t));
                    const projX = this.mousePX + t * sdx;
                    const projY = this.mousePY + t * sdy;
                    const distToSeg = Math.hypot(p.x - projX, p.y - projY);

                    if (distToSeg < this.CUT_RADIUS) {
                        const cross = sdx * (p.y - this.mousePY) - sdy * (p.x - this.mousePX);
                        const side = cross >= 0 ? 1 : -1;
                        const intensity = (1 - distToSeg / this.CUT_RADIUS);
                        const push = Math.min(segLen * 0.16, 12) * intensity;
                        p.vx += nx * side * push;
                        p.vy += ny * side * push;
                    }
                }
            }
        }

        // 4. Limites basées sur la zone visible
        let minX = -10000, maxX = 10000, minY = -10000, maxY = 10000;
        if (this.visibleArea && this.visibleArea.length === 4) {
            const [vx, vy, vw, vh] = this.visibleArea;
            if (vw > 100 && vh > 100) {
                minX = vx;
                maxX = vx + vw;
                minY = vy;
                maxY = vy + vh;
            }
        }

        for (const p of this.bodyParticles) {
            if (isNaN(p.x) || isNaN(p.y) || isNaN(p.vx) || isNaN(p.vy) || !isFinite(p.x) || !isFinite(p.y)) {
                p.x = cx + (Math.random() - 0.5) * 10;
                p.y = cy + (Math.random() - 0.5) * 10;
                p.vx = 0;
                p.vy = 0;
                continue;
            }

            p.x += p.vx;
            p.y += p.vy;
            const pr = p.radius;
            if (p.x < minX + pr) { p.x = minX + pr; p.vx *= -0.5; }
            if (p.x > maxX - pr) { p.x = maxX - pr; p.vx *= -0.5; }
            if (p.y < minY + pr) { p.y = minY + pr; p.vy *= -0.5; }
            if (p.y > maxY - pr) { p.y = maxY - pr; p.vy *= -0.5; }
        }
    },

    // ─── Collision avec les nœuds ───
    updateNodeCollision(nodes) {
        if (!nodes) return;

        if (!this.isDragging) {
            for (const node of nodes) {
                const nx = node.pos[0], ny = node.pos[1];
                const nw = node.size[0], nh = node.size[1];

                const isInside = (this.x >= nx && this.x <= nx + nw && this.y >= ny && this.y <= ny + nh);
                if (isInside) {
                    const distLeft = this.x - nx;
                    const distRight = (nx + nw) - this.x;
                    const distTop = this.y - ny;
                    const distBottom = (ny + nh) - this.y;
                    const minDist = Math.min(distLeft, distRight, distTop, distBottom);

                    if (minDist === distLeft) {
                        this.x = nx - 30; this.vx = -1.5;
                    } else if (minDist === distRight) {
                        this.x = nx + nw + 30; this.vx = 1.5;
                    } else if (minDist === distTop) {
                        this.y = ny - 30; this.vy = -1.5;
                    } else {
                        this.y = ny + nh + 30; this.vy = 1.5;
                    }
                }
            }
        }

        for (const p of this.bodyParticles) {
            if (isNaN(p.x) || isNaN(p.y)) continue;

            for (const node of nodes) {
                const nx = node.pos[0], ny = node.pos[1];
                const nw = node.size[0], nh = node.size[1];
                const pr = p.radius;

                const isInside = (p.x >= nx && p.x <= nx + nw && p.y >= ny && p.y <= ny + nh);

                if (isInside) {
                    const distLeft = p.x - nx;
                    const distRight = (nx + nw) - p.x;
                    const distTop = p.y - ny;
                    const distBottom = (ny + nh) - p.y;
                    const minDist = Math.min(distLeft, distRight, distTop, distBottom);

                    if (minDist === distLeft) {
                        p.x = nx - pr;
                        p.vx = -Math.abs(p.vx) * 0.6 - 1.5;
                    } else if (minDist === distRight) {
                        p.x = nx + nw + pr;
                        p.vx = Math.abs(p.vx) * 0.6 + 1.5;
                    } else if (minDist === distTop) {
                        p.y = ny - pr;
                        p.vy = -Math.abs(p.vy) * 0.6 - 1.5;
                    } else {
                        p.y = ny + nh + pr;
                        p.vy = Math.abs(p.vy) * 0.6 + 1.5;
                    }
                } else {
                    const closestX = Math.max(nx, Math.min(p.x, nx + nw));
                    const closestY = Math.max(ny, Math.min(p.y, ny + nh));
                    const dx = p.x - closestX;
                    const dy = p.y - closestY;
                    const dist = Math.hypot(dx, dy);

                    if (dist < pr && dist > 0.1) {
                        const force = (pr - dist) / pr * 2.5;
                        p.x += (dx / dist) * force;
                        p.y += (dy / dist) * force;
                        p.vx += (dx / dist) * force * 1.5;
                        p.vy += (dy / dist) * force * 1.5;
                    }
                }
            }
        }

        // Réaction KSampler
        if (this.nodeBounceCooldown <= 0) {
            for (const p of this.bodyParticles) {
                for (const node of nodes) {
                    const nx = node.pos[0], ny = node.pos[1];
                    const nw = node.size[0], nh = node.size[1];
                    if (p.x >= nx && p.x <= nx + nw && p.y >= ny && p.y <= ny + nh) {
                        if (node.comfyClass?.includes("KSampler")) {
                            this.mood = "surprised";
                            this.moodTimer = 0;
                        }
                        this.nodeBounceCooldown = 0.3;
                        break;
                    }
                }
            }
        }
        this.nodeBounceCooldown = Math.max(0, this.nodeBounceCooldown - 0.016);
    },

    // ─── Centre de masse ───
    updateOrganPosition() {
        let sx = 0, sy = 0;
        let validCount = 0;
        for (const p of this.bodyParticles) {
            if (!isNaN(p.x) && !isNaN(p.y)) {
                sx += p.x; sy += p.y;
                validCount++;
            }
        }
        if (validCount === 0) return;

        const comX = sx / validCount;
        const comY = sy / validCount;

        const dx = comX - this.organX;
        const dy = comY - this.organY;
        this.organVx += dx * 0.08;
        this.organVy += dy * 0.08;
        this.organVx *= 0.78;
        this.organVy *= 0.78;
        this.organX += this.organVx;
        this.organY += this.organVy;
    },

    // ─── Dessin ───
    draw(ctx) {
        const zoom = Math.max(0.01, this._canvas?.ds_scale || 1);

        ctx.save();
        ctx.translate(this.x, this.y);
        ctx.scale(1 / zoom, 1 / zoom);

        const s = this.scale || 1;
        const ox = this.organX - this.x;
        const oy = this.organY - this.y;

        this.drawGooeyBody(ctx, s);
        this.drawBrain(ctx, ox, oy, s);
        this.drawEyes(ctx, ox, oy, s);
        this.drawMouth(ctx, ox, oy, s);
        this.drawParticles(ctx);

        ctx.restore();
    },

    // ─── Rendu gooey ───
    drawGooeyBody(ctx, s) {
        const particles = this.bodyParticles;
        if (particles.length === 0) return;

        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const p of particles) {
            if (isNaN(p.x) || isNaN(p.y)) continue;
            const lx = p.x - this.x;
            const ly = p.y - this.y;
            const r = p.radius;
            if (lx - r < minX) minX = lx - r;
            if (ly - r < minY) minY = ly - r;
            if (lx + r > maxX) maxX = lx + r;
            if (ly + r > maxY) maxY = ly + r;
        }

        const pad = 30;
        let bw = Math.ceil(maxX - minX + pad * 2);
        let bh = Math.ceil(maxY - minY + pad * 2);

        const MAX_SIZE = 800;
        if (bw > MAX_SIZE) bw = MAX_SIZE;
        if (bh > MAX_SIZE) bh = MAX_SIZE;

        if (bw < 2 || bh < 2 || !isFinite(bw) || !isFinite(bh)) {
            this.drawFallbackBody(ctx, s);
            return;
        }

        try {
            const oc = this._offscreen;
            const octx = this._octx;
            oc.width = bw;
            oc.height = bh;

            octx.clearRect(0, 0, bw, bh);
            for (const p of particles) {
                if (isNaN(p.x) || isNaN(p.y)) continue;
                const lx = p.x - this.x - minX + pad;
                const ly = p.y - this.y - minY + pad;
                const grad = octx.createRadialGradient(lx, ly, 0, lx, ly, p.radius);
                grad.addColorStop(0, "rgba(255,255,255,1)");
                grad.addColorStop(0.5, "rgba(255,255,255,0.9)");
                grad.addColorStop(0.85, "rgba(255,255,255,0.3)");
                grad.addColorStop(1, "rgba(255,255,255,0)");
                octx.fillStyle = grad;
                octx.beginPath();
                octx.arc(lx, ly, p.radius, 0, Math.PI * 2);
                octx.fill();
            }

            octx.filter = "blur(6px)";
            octx.drawImage(oc, 0, 0);
            octx.filter = "none";

            const bodyColor = this.getBodyColor();
            octx.save();
            octx.globalCompositeOperation = "source-in";
            octx.fillStyle = bodyColor;
            octx.fillRect(0, 0, bw, bh);
            octx.restore();

            const dx = minX - pad;
            const dy = minY - pad;
            ctx.drawImage(oc, dx, dy);

        } catch (e) {
            this.drawFallbackBody(ctx, s);
        }
    },

    drawFallbackBody(ctx, s) {
        const bodyColor = this.getBodyColor();
        ctx.save();
        ctx.beginPath();
        ctx.arc(0, 0, 32 * s, 0, Math.PI * 2);
        ctx.fillStyle = bodyColor;
        ctx.globalAlpha = 0.6;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = "rgba(0,0,0,0.15)";
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.restore();
    },

    getBodyColor() {
        switch (this.mood) {
            case "surprised": return "#E65100";
            case "sleepy": return "#43A047";
            default: return "#FF8F00";
        }
    },

    drawBrain(ctx, ox, oy, s) {
        ctx.save();
        const pulse = 1 + Math.sin(this.time * 4) * 0.03;
        ctx.translate(ox, oy - 18 * s);
        ctx.scale(pulse, pulse);

        ctx.fillStyle = "#ff4da6";
        ctx.beginPath();
        ctx.arc(-7 * s, -4 * s, 10 * s, 0, Math.PI * 2);
        ctx.arc(-3 * s, -11 * s, 8 * s, 0, Math.PI * 2);
        ctx.arc(-8 * s, 4 * s, 7 * s, 0, Math.PI * 2);
        ctx.arc(7 * s, -4 * s, 10 * s, 0, Math.PI * 2);
        ctx.arc(3 * s, -11 * s, 8 * s, 0, Math.PI * 2);
        ctx.arc(8 * s, 4 * s, 7 * s, 0, Math.PI * 2);
        ctx.arc(0, 6 * s, 6 * s, 0, Math.PI * 2);
        ctx.fill();

        ctx.strokeStyle = "#9d004b";
        ctx.lineWidth = 2;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(0, -18 * s);
        ctx.lineTo(0, 6 * s);
        ctx.arc(-5 * s, -5 * s, 4 * s, Math.PI, Math.PI * 2.5);
        ctx.moveTo(-9 * s, 3 * s);
        ctx.quadraticCurveTo(-5 * s, 5 * s, -1 * s, 2 * s);
        ctx.arc(5 * s, -5 * s, 4 * s, 0, Math.PI * 1.5);
        ctx.moveTo(9 * s, 3 * s);
        ctx.quadraticCurveTo(5 * s, 5 * s, 1 * s, 2 * s);
        ctx.stroke();

        ctx.restore();
    },

    drawEyes(ctx, ox, oy, s) {
        const expr = this.mood;

        ctx.strokeStyle = "#0f172a";
        ctx.lineWidth = 2.5;
        ctx.lineCap = "round";

        const leftEyeX = ox - 15 * s;
        const rightEyeX = ox + 15 * s;
        const browY = oy - 6 * s;

        ctx.beginPath();
        if (expr === "surprised") {
            ctx.moveTo(leftEyeX - 8 * s, browY - 3 * s);
            ctx.quadraticCurveTo(leftEyeX, browY - 8 * s, leftEyeX + 6 * s, browY - 5 * s);
            ctx.moveTo(rightEyeX + 8 * s, browY - 3 * s);
            ctx.quadraticCurveTo(rightEyeX, browY - 8 * s, rightEyeX - 6 * s, browY - 5 * s);
        } else if (expr === "sleepy") {
            ctx.moveTo(leftEyeX - 7 * s, browY + 2 * s);
            ctx.lineTo(leftEyeX + 7 * s, browY + 2 * s);
            ctx.moveTo(rightEyeX - 7 * s, browY + 2 * s);
            ctx.lineTo(rightEyeX + 7 * s, browY + 2 * s);
        } else {
            ctx.arc(leftEyeX, browY + 2 * s, 7 * s, Math.PI * 1.15, Math.PI * 1.85);
            ctx.moveTo(rightEyeX - 7 * s, browY + 2 * s);
            ctx.arc(rightEyeX, browY + 2 * s, 7 * s, Math.PI * 1.15, Math.PI * 1.85);
        }
        ctx.stroke();

        const drawOneEye = (ex, isLeft) => {
            const eyeX = ox + ex;
            const eyeY = oy + 6 * s;

            const angle = Math.atan2(this.mouseY - (this.y + eyeY), this.mouseX - (this.x + eyeX));
            const lookDist = Math.min(5 * s, Math.hypot(this.mouseX - (this.x + eyeX), this.mouseY - (this.y + eyeY)) * 0.02);
            const pupilX = eyeX + Math.cos(angle) * lookDist;
            const pupilY = eyeY + Math.sin(angle) * lookDist;

            if (this.isBlinking) {
                ctx.strokeStyle = "#0f172a";
                ctx.lineWidth = 3;
                ctx.beginPath();
                ctx.moveTo(eyeX - 9 * s, eyeY);
                ctx.quadraticCurveTo(eyeX, eyeY + 3 * s, eyeX + 9 * s, eyeY);
                ctx.stroke();
                return;
            }

            const scleraR = expr === "surprised" ? 11 * s : 9 * s;
            ctx.fillStyle = "#ffffff";
            ctx.beginPath();
            ctx.arc(eyeX, eyeY, scleraR, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = "rgba(0,0,0,0.2)";
            ctx.lineWidth = 1;
            ctx.stroke();

            const pupilR = expr === "surprised" ? 3 * s : 4.5 * s;
            ctx.fillStyle = "#0f172a";
            ctx.beginPath();
            ctx.arc(pupilX, pupilY, pupilR, 0, Math.PI * 2);
            ctx.fill();

            ctx.fillStyle = "#ffffff";
            ctx.beginPath();
            ctx.arc(pupilX - pupilR * 0.3, pupilY - pupilR * 0.3, pupilR * 0.35, 0, Math.PI * 2);
            ctx.fill();
        };

        drawOneEye(-15 * s, true);
        drawOneEye(15 * s, false);
    },

    drawMouth(ctx, ox, oy, s) {
        const mouthY = oy + 22 * s;
        const mouthW = (10 + this.mouthOpen * 6) * s;

        if (this.mood === "surprised" && this.mouthOpen > 0.3) {
            ctx.beginPath();
            ctx.ellipse(ox, mouthY, 6 * s, (3 + this.mouthOpen * 6) * s, 0, 0, Math.PI * 2);
            ctx.fillStyle = "#5D4037";
            ctx.fill();
            ctx.beginPath();
            ctx.ellipse(ox, mouthY + s, 4 * s, (1 + this.mouthOpen * 4) * s, 0, 0, Math.PI * 2);
            ctx.fillStyle = "#D32F2F";
            ctx.fill();
        } else if (this.mood === "sleepy") {
            ctx.beginPath();
            ctx.ellipse(ox, mouthY, 2 * s, 1.5 * s, 0, 0, Math.PI * 2);
            ctx.fillStyle = "#5D4037";
            ctx.fill();
        } else {
            ctx.beginPath();
            ctx.arc(ox, mouthY - 2 * s, mouthW, 0.1, Math.PI - 0.1);
            ctx.strokeStyle = "#5D4037";
            ctx.lineWidth = 2 * s;
            ctx.lineCap = "round";
            ctx.stroke();
        }
    },

    addParticle(x, y, type) {
        this._fxParticles = this._fxParticles || [];
        this._fxParticles.push({
            x, y,
            vx: (Math.random() - 0.5) * 0.5,
            vy: -0.3 - Math.random() * 0.5,
            life: 1,
            decay: 0.008 + Math.random() * 0.005,
            size: 3 + Math.random() * 4,
            type: type || "bubble",
            hue: 30 + Math.random() * 20
        });
    },

    updateParticles(dt) {
        const arr = this._fxParticles || [];
        for (let i = arr.length - 1; i >= 0; i--) {
            const p = arr[i];
            p.x += p.vx;
            p.y += p.vy;
            p.life -= p.decay;
            if (p.life <= 0) arr.splice(i, 1);
        }
    },

    drawParticles(ctx) {
        const arr = this._fxParticles || [];
        for (const p of arr) {
            const lx = p.x - this.x;
            const ly = p.y - this.y;
            if (p.type === "zzz") {
                ctx.save();
                ctx.font = `${12 + (1 - p.life) * 8}px serif`;
                ctx.fillStyle = `rgba(100, 200, 100, ${p.life * 0.6})`;
                ctx.fillText("z", lx, ly);
                ctx.restore();
            } else {
                ctx.beginPath();
                ctx.arc(lx, ly, p.size * p.life, 0, Math.PI * 2);
                ctx.fillStyle = `hsla(${p.hue}, 80%, 70%, ${p.life * 0.5})`;
                ctx.fill();
            }
        }
    },

    hitTest(mx, my) {
        for (const p of this.bodyParticles) {
            const dx = mx - p.x;
            const dy = my - p.y;
            if (dx * dx + dy * dy < (p.radius * 2.5) ** 2) {
                return true;
            }
        }
        return false;
    },

    onMouseDown(pos) {
        if (this.hitTest(pos[0], pos[1])) {
            this.isDragging = true;
            this.dragPrevX = pos[0];
            this.dragPrevY = pos[1];
            this.throwVx = 0;
            this.throwVy = 0;
            this.dragDistance = 0;
            this.x = pos[0];
            this.y = pos[1];
            return true;
        }
        return false;
    },

    onMouseMove(pos) { },

    onMouseUp() {
        if (this.isDragging) {
            this.isDragging = false;
            if (this.dragDistance < 8) {
                this.followMouse = !this.followMouse;
                if (this.followMouse) {
                    this.mood = "happy";
                    this.moodTimer = 0;
                    for (let i = 0; i < 5; i++) {
                        this.addParticle(this.x + (Math.random() - 0.5) * 30, this.y + (Math.random() - 0.5) * 30, "sparkle");
                    }
                }
            } else {
                const spd = Math.hypot(this.throwVx, this.throwVy);
                if (spd > 0.5) {
                    const maxT = 12;
                    const sc = Math.min(maxT / spd, 1);
                    this.vx = this.throwVx * sc;
                    this.vy = this.throwVy * sc;
                    this.mood = "surprised";
                    this.moodTimer = 0;
                } else {
                    this.vx = 0;
                    this.vy = 0;
                }
            }
            return true;
        }
        return false;
    }
};

// ─── Exposition publique pour le menu AIH ───
window.BlobbyCompanion = {
    activate: () => Blobby.activate(),
    deactivate: () => Blobby.deactivate(),
    isActive: () => Blobby.isActive(),
    toggle: () => {
        if (Blobby.isActive()) {
            Blobby.deactivate();
            const cfg = _getAIHConfig();
            cfg.blobbyActive = false;
            _setAIHConfig(cfg);
            return false;
        } else {
            Blobby.activate();
            const cfg = _getAIHConfig();
            cfg.blobbyActive = true;
            _setAIHConfig(cfg);
            return true;
        }
    }
};

// ─── Enregistrement ComfyUI ───
app.registerExtension({
    name: "Blobby.Companion",
    async setup() {
        const canvas = app.canvas;
        Blobby.init(canvas);

        // Si la config dit "activé", démarrer automatiquement
        const cfg = _getAIHConfig();
        if (cfg.blobbyActive) {
            Blobby.activate();
        }

        console.log("%c🧡 Blobby Companion chargé. Menu AIH pour activer/désactiver.", "font-size: 14px; color: #FF8F00;");
    }
});
