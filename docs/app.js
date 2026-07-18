/* ============================================================
   DÉSASSEMBLÉE — Visualisation D3.js v7
   ============================================================ */

const DATA_URL = "data/scores.json";

const MARGIN = { top: 30, right: 60, bottom: 56, left: 55 };
const ASPECT  = 0.48; // hauteur / largeur

let activeParties = new Set();   // slugs visibles
let highlightedParty = null;     // slug mis en valeur par hover légende
let cachedData = null;           // données chargées une seule fois
let displayMode = "hybride";     // "hybride" | "votes" | "ches"
let displayScope = "tous";       // "tous" | "solennels"
let displayAxis = "lrgen";       // "lrgen" | "lrecon" | "galtan"
let firstRender = true;          // n'animer le tracé qu'au premier chargement

const MODE_DESCRIPTIONS = {
  hybride: "La courbe suit le comportement de vote ; les losanges ◆ marquent l'évaluation des experts (CHES). L'écart entre les deux mesure la distance entre discours et pratique parlementaire.",
  votes:   "Positionnement mesuré uniquement par les votes à l'Assemblée nationale (ACP calibrée). Reflète la pratique, pas la communication.",
  ches:    "Positionnement évalué par les chercheurs du Chapel Hill Expert Survey (vagues 2014, 2019, 2024). Reflète le programme et le discours.",
};

const SCOPE_NOTE = " Périmètre restreint aux scrutins solennels (textes majeurs, motions de censure) : ~2 % des votes, forte salience médiatique — l'incertitude est plus large.";

const AXIS_NOTES = {
  lrecon: " Axe économique (CHES lrecon) : redistribution, fiscalité, rôle de l'État — le RN y apparaît bien plus central que sur l'axe général.",
  galtan: " Axe sociétal (CHES galtan) : immigration, autorité, mœurs — du pôle libertaire/écologiste (bas) au pôle autoritaire/national (haut).",
};

// Labels de l'axe Y selon l'axe idéologique affiché
const AXIS_Y_LABELS = {
  lrgen:  ["◀ Gauche", "Droite ▶"],
  lrecon: ["◀ Gauche éco.", "Droite éco. ▶"],
  galtan: ["◀ Libertaire (GAL)", "Autoritaire (TAN) ▶"],
};

// Blocs politiques pour le filtre rapide (champ family de scores.json)
const FAMILY_BLOCS = {
  gauche: ["gauche_radicale", "gauche", "centre_gauche"],
  centre: ["centre", "centre_droit"],
  droite: ["droite", "droite_radicale"],
};

async function main() {
  let data;
  try {
    data = await d3.json(DATA_URL);
    cachedData = data;
  } catch (e) {
    document.getElementById("chart").innerHTML =
      `<p style="padding:2rem;font-family:monospace;color:#888">
        Données non disponibles. Lance <code>python scripts/build_data.py</code> pour générer scores.json.
      </p>`;
    return;
  }

  // Mettre à jour la date en footer
  if (data.meta?.updated) {
    const d = new Date(data.meta.updated);
    document.getElementById("updateDate").textContent =
      `Mise à jour : ${d.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" })}`;
  }

  // Initialiser tous les partis visibles
  data.parties.forEach(p => activeParties.add(p.slug));

  renderChart(data);
  renderLegend(data);
  setupToggleAll(data);
  setupModeSelector(data);
  setupAxisSelector(data);
  setupScopeSelector(data);
  setupFamilyFilter(data);
}

// ── Rendu du graphique ──────────────────────────────────────────

function renderChart(data) {
  const container = document.getElementById("chart");
  container.innerHTML = "";

  const width  = container.getBoundingClientRect().width || 900;
  const height = Math.round(width * ASPECT);
  const innerW = width  - MARGIN.left - MARGIN.right;
  const innerH = height - MARGIN.top  - MARGIN.bottom;

  // Étendues temporelles et de scores
  const allYears  = data.parties.flatMap(p => p.scores.map(s => s.year));
  const yearMin   = d3.min(allYears);
  const yearMax   = d3.max(allYears);

  const xScale = d3.scaleLinear()
    .domain([yearMin, yearMax])
    .range([0, innerW])
    .nice();

  const yScale = d3.scaleLinear()
    .domain([-10, 10])
    .range([innerH, 0]);

  const svg = d3.select(container)
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("preserveAspectRatio", "xMidYMid meet");

  const g = svg.append("g")
    .attr("transform", `translate(${MARGIN.left},${MARGIN.top})`);

  // ── Grille horizontale légère
  g.append("g")
    .attr("class", "grid")
    .call(
      d3.axisLeft(yScale)
        .tickValues([-8, -6, -4, -2, 0, 2, 4, 6, 8])
        .tickSize(-innerW)
        .tickFormat("")
    )
    .call(gg => gg.select(".domain").remove())
    .call(gg => gg.selectAll("line")
      .attr("stroke", "#E8E0D4")
      .attr("stroke-dasharray", "3 2"));

  // ── Ligne zéro (axe neutre)
  g.append("line")
    .attr("class", "zero-line")
    .attr("x1", 0).attr("x2", innerW)
    .attr("y1", yScale(0)).attr("y2", yScale(0));

  // ── Lignes verticales pour les élections
  data.elections.forEach(elec => {
    const x = xScale(elec.year);
    g.append("line")
      .attr("class", "election-line")
      .attr("x1", x).attr("x2", x)
      .attr("y1", 0).attr("y2", innerH);

    g.append("text")
      .attr("class", "election-label")
      .attr("x", x + 4)
      .attr("y", 12)
      .text(elec.year);
  });

  // ── Axe X
  g.append("g")
    .attr("class", "axis axis-x")
    .attr("transform", `translate(0,${innerH})`)
    .call(
      d3.axisBottom(xScale)
        .tickFormat(d3.format("d"))
        .ticks(yearMax - yearMin > 8 ? 8 : (yearMax - yearMin))
    )
    .call(gg => gg.select(".domain").attr("stroke", "#CCBBA0"));

  // ── Axe Y
  g.append("g")
    .attr("class", "axis axis-y")
    .call(
      d3.axisLeft(yScale)
        .tickValues([-10, -5, 0, 5, 10])
        .tickFormat(d => d > 0 ? `+${d}` : d)
    )
    .call(gg => gg.select(".domain").attr("stroke", "#CCBBA0"));

  // ── Labels axes Y (négatif = bas, positif = haut, selon l'axe affiché)
  const [labelLow, labelHigh] = AXIS_Y_LABELS[displayAxis] || AXIS_Y_LABELS.lrgen;
  g.append("text")
    .attr("class", "axis-label")
    .attr("x", -MARGIN.left + 4)
    .attr("y", yScale(-10) + 16)
    .attr("text-anchor", "start")
    .text(labelLow);

  g.append("text")
    .attr("class", "axis-label")
    .attr("x", -MARGIN.left + 4)
    .attr("y", yScale(10) - 8)
    .attr("text-anchor", "start")
    .text(labelHigh);

  // ── Générateur de ligne courbe
  const lineGen = d3.line()
    .x(d => xScale(d.year))
    .y(d => yScale(d.score))
    .curve(d3.curveMonotoneX)
    .defined(d => d.score != null);

  const tooltip = document.getElementById("tooltip");

  // ── Générateur de bande de confiance (bootstrap 95 %)
  const bandGen = d3.area()
    .x(d => xScale(d.year))
    .y0(d => yScale(d.ci[0]))
    .y1(d => yScale(d.ci[1]))
    .curve(d3.curveMonotoneX)
    .defined(d => d.ci != null && d.score != null);

  // Générateur de losange pour les ancres CHES
  const diamond = d3.symbol().type(d3.symbolDiamond).size(52);

  function attachTooltip(sel, party, pt) {
    sel
      .on("mouseenter", (event) => {
        const sourceLabel = {
          ches_anchor:         "Ancre CHES ◆",
          pca_calibrated:      "ACP calibrée ●",
          pca_session_global:  "ACP (session) ●",
        }[pt.source] || pt.source;

        tooltip.innerHTML = `
          <div class="tooltip-party">${party.name_short}</div>
          <div class="tooltip-score" style="color:${party.color}">${pt.score > 0 ? "+" : ""}${pt.score.toFixed(1)}</div>
          <div class="tooltip-meta">${pt.year} · ${sourceLabel}</div>
          ${pt.r2 != null ? `<div class="tooltip-meta">r²&nbsp;=&nbsp;${pt.r2.toFixed(2)}</div>` : ""}
          ${pt.ci ? `<div class="tooltip-meta">IC&nbsp;95&nbsp;% : [${pt.ci[0].toFixed(1)}, ${pt.ci[1].toFixed(1)}]</div>` : ""}
        `;
        positionTooltip(event);
        tooltip.classList.add("visible");
        tooltip.setAttribute("aria-hidden", "false");
      })
      .on("mousemove", positionTooltip)
      .on("mouseleave", () => {
        tooltip.classList.remove("visible");
        tooltip.setAttribute("aria-hidden", "true");
      });
  }

  // ── Rendu des courbes par parti (selon le mode d'affichage)
  data.parties.forEach(party => {
    const sorted = [...party.scores].sort((a, b) => a.year - b.year);
    const pcaSeries  = sorted.filter(d =>
      d.source.startsWith("pca") &&
      (d.scope || "tous") === displayScope &&
      (d.axis || "lrgen") === displayAxis);
    const chesSeries = sorted.filter(d =>
      d.source === "ches_anchor" && (d.axis || "lrgen") === displayAxis);
    const visible = activeParties.has(party.slug);

    // La ligne suit les votes (modes hybride/votes) ou les ancres CHES (mode experts)
    const lineData = displayMode === "ches" ? chesSeries : pcaSeries;
    const showBand     = displayMode !== "ches";
    const showPcaDots  = displayMode !== "ches";
    const showChesDots = displayMode !== "votes";

    // Bande de confiance (uniquement sur les scores de votes)
    if (showBand && pcaSeries.filter(d => d.ci).length >= 2) {
      g.append("path")
        .datum(pcaSeries)
        .attr("class", `party-band party-band-${party.slug}`)
        .attr("fill", party.color)
        .attr("opacity", visible ? 0.10 : 0.015)
        .attr("d", bandGen);
    }

    // Ligne principale (pointillée en mode experts : points épars)
    if (lineData.length >= 2) {
      const path = g.append("path")
        .datum(lineData)
        .attr("class", `party-line party-${party.slug}`)
        .attr("stroke", party.color)
        .attr("opacity", visible ? 1 : 0.08)
        .attr("d", lineGen);

      if (displayMode === "ches") {
        path.attr("stroke-dasharray", "6 4").attr("stroke-width", 1.6);
      } else if (firstRender) {
        animateLine(path);
      }
    }

    // Points ACP ●
    if (showPcaDots) {
      pcaSeries.forEach(pt => {
        const dot = g.append("circle")
          .attr("class", `party-dot party-dot-${party.slug}`)
          .attr("cx", xScale(pt.year))
          .attr("cy", yScale(pt.score))
          .attr("r", 3.5)
          .attr("fill", party.color)
          .attr("opacity", visible ? 1 : 0.08);
        attachTooltip(dot, party, pt);
      });
    }

    // Ancres CHES ◆ (losanges — flottent hors de la courbe en mode hybride)
    if (showChesDots) {
      chesSeries.forEach(pt => {
        const mark = g.append("path")
          .attr("class", `party-dot party-dot-${party.slug}`)
          .attr("d", diamond())
          .attr("transform", `translate(${xScale(pt.year)},${yScale(pt.score)})`)
          .attr("fill", party.color)
          .attr("stroke", "white")
          .attr("stroke-width", 1.2)
          .attr("opacity", visible ? 1 : 0.08);
        attachTooltip(mark, party, pt);
      });
    }
  });

  firstRender = false;
}

// ── Sélecteurs de mode et de périmètre ───────────────────────────

function updateDescription() {
  const description = document.getElementById("modeDescription");
  let text = MODE_DESCRIPTIONS[displayMode];
  if (displayAxis !== "lrgen") text += AXIS_NOTES[displayAxis] || "";
  if (displayScope === "solennels" && displayMode !== "ches") text += SCOPE_NOTE;
  description.textContent = text;

  // Le périmètre n'a pas de sens en mode Experts (données CHES, pas votes)
  // ni sur les axes thématiques (calculés sur tous les scrutins uniquement)
  const scopeSel = document.getElementById("scopeSelector");
  if (scopeSel) scopeSel.classList.toggle("disabled", displayMode === "ches" || displayAxis !== "lrgen");
}

function setupModeSelector(data) {
  const container = document.getElementById("modeSelector");
  if (!container) return;

  updateDescription();

  container.querySelectorAll(".mode-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mode === displayMode);
    btn.setAttribute("aria-selected", String(btn.dataset.mode === displayMode));
    btn.addEventListener("click", () => {
      if (btn.dataset.mode === displayMode) return;
      displayMode = btn.dataset.mode;
      container.querySelectorAll(".mode-btn").forEach(b => {
        b.classList.toggle("active", b === btn);
        b.setAttribute("aria-selected", String(b === btn));
      });
      updateDescription();
      renderChart(data);
    });
  });
}

function setupAxisSelector(data) {
  const container = document.getElementById("axisSelector");
  if (!container) return;

  container.querySelectorAll(".mode-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.axis === displayAxis);
    btn.setAttribute("aria-selected", String(btn.dataset.axis === displayAxis));
    btn.addEventListener("click", () => {
      if (btn.dataset.axis === displayAxis) return;
      displayAxis = btn.dataset.axis;
      // Les axes thématiques ne sont calculés que sur tous les scrutins
      if (displayAxis !== "lrgen" && displayScope !== "tous") {
        displayScope = "tous";
        document.querySelectorAll("#scopeSelector .mode-btn").forEach(b => {
          b.classList.toggle("active", b.dataset.scope === "tous");
          b.setAttribute("aria-selected", String(b.dataset.scope === "tous"));
        });
      }
      container.querySelectorAll(".mode-btn").forEach(b => {
        b.classList.toggle("active", b === btn);
        b.setAttribute("aria-selected", String(b === btn));
      });
      updateDescription();
      renderChart(data);
    });
  });
}

function setupScopeSelector(data) {
  const container = document.getElementById("scopeSelector");
  if (!container) return;

  container.querySelectorAll(".mode-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.scope === displayScope);
    btn.setAttribute("aria-selected", String(btn.dataset.scope === displayScope));
    btn.addEventListener("click", () => {
      if (btn.dataset.scope === displayScope) return;
      displayScope = btn.dataset.scope;
      container.querySelectorAll(".mode-btn").forEach(b => {
        b.classList.toggle("active", b === btn);
        b.setAttribute("aria-selected", String(b === btn));
      });
      updateDescription();
      renderChart(data);
    });
  });
}

// ── Filtre rapide par bloc politique ─────────────────────────────

function clearFamilyButtons() {
  document.querySelectorAll(".family-btn").forEach(b => b.classList.remove("active"));
}

function setupFamilyFilter(data) {
  const container = document.getElementById("familyFilter");
  if (!container) return;

  container.querySelectorAll(".family-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const wasActive = btn.classList.contains("active");
      clearFamilyButtons();
      if (wasActive) {
        // Re-clic sur le bloc actif : retour à l'affichage complet
        activeParties = new Set(data.parties.map(p => p.slug));
      } else {
        btn.classList.add("active");
        const families = FAMILY_BLOCS[btn.dataset.bloc] || [];
        activeParties = new Set(
          data.parties.filter(p => families.includes(p.family)).map(p => p.slug)
        );
      }
      applyVisibility(data);
    });
  });
}

function animateLine(path) {
  const length = path.node().getTotalLength();
  path
    .attr("stroke-dasharray", `${length} ${length}`)
    .attr("stroke-dashoffset", length)
    .transition()
    .duration(1200)
    .ease(d3.easeQuadInOut)
    .attr("stroke-dashoffset", 0);
  // Filet de sécurité : si les requestAnimationFrame sont throttlés
  // (onglet inactif), la transition gèle et la ligne resterait invisible
  setTimeout(() => {
    path.interrupt().attr("stroke-dashoffset", 0).attr("stroke-dasharray", null);
  }, 1600);
}

function positionTooltip(event) {
  const tooltip = document.getElementById("tooltip");
  const vw = window.innerWidth;
  const tw = tooltip.offsetWidth || 200;
  let left = event.clientX + 14;
  let top  = event.clientY - 20;
  if (left + tw > vw - 10) left = event.clientX - tw - 14;
  if (top < 0) top = 10;
  tooltip.style.left = `${left}px`;
  tooltip.style.top  = `${top}px`;
}

// ── Légende ─────────────────────────────────────────────────────

function renderLegend(data) {
  const legend = document.getElementById("legend");
  legend.innerHTML = "";

  data.parties.forEach(party => {
    const item = document.createElement("div");
    item.className = "legend-item";
    item.dataset.slug = party.slug;
    if (!activeParties.has(party.slug)) item.classList.add("dimmed");

    item.innerHTML = `
      <span class="legend-swatch" style="background:${party.color}"></span>
      <span class="legend-name">${party.name_short}</span>
    `;

    item.addEventListener("click", () => toggleParty(party.slug, data));
    legend.appendChild(item);
  });
}

function toggleParty(slug, data) {
  if (activeParties.has(slug)) {
    activeParties.delete(slug);
  } else {
    activeParties.add(slug);
  }
  clearFamilyButtons();  // la sélection manuelle invalide le filtre de bloc
  applyVisibility(data);
}

function applyVisibility(data) {
  data.parties.forEach(party => {
    const visible = activeParties.has(party.slug);
    d3.selectAll(`.party-${party.slug}`).attr("opacity", visible ? 1 : 0.08);
    d3.selectAll(`.party-dot-${party.slug}`).attr("opacity", visible ? 1 : 0.08);
    d3.selectAll(`.party-band-${party.slug}`).attr("opacity", visible ? 0.10 : 0.015);

    const item = document.querySelector(`.legend-item[data-slug="${party.slug}"]`);
    if (item) item.classList.toggle("dimmed", !visible);
  });

  const btn = document.getElementById("toggleAll");
  btn.textContent = activeParties.size === data.parties.length ? "Tout masquer" : "Tout afficher";
}

function setupToggleAll(data) {
  const btn = document.getElementById("toggleAll");
  // Synchroniser le texte avec l'état courant de activeParties
  btn.textContent = activeParties.size === data.parties.length ? "Tout masquer" : "Tout afficher";
  // Réattacher le listener (éviter les doublons après resize)
  const newBtn = btn.cloneNode(true);
  btn.parentNode.replaceChild(newBtn, btn);
  newBtn.addEventListener("click", () => {
    if (activeParties.size > 0) {
      activeParties.clear();
    } else {
      data.parties.forEach(p => activeParties.add(p.slug));
    }
    clearFamilyButtons();
    applyVisibility(data);
  });
}

// ── Redimensionnement responsive ─────────────────────────────────

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (cachedData) {
      renderChart(cachedData);
      renderLegend(cachedData);
      setupToggleAll(cachedData);
    }
  }, 200);
});

main();
