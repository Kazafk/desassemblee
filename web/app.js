/* ============================================================
   DÉSASSEMBLÉE — Visualisation D3.js v7
   ============================================================ */

const DATA_URL = "data/scores.json";

const MARGIN = { top: 30, right: 60, bottom: 56, left: 55 };
const ASPECT  = 0.48; // hauteur / largeur

let activeParties = new Set();   // slugs visibles
let highlightedParty = null;     // slug mis en valeur par hover légende

async function main() {
  let data;
  try {
    data = await d3.json(DATA_URL);
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

  // ── Labels axes Y (négatif = gauche, positif = droite)
  g.append("text")
    .attr("class", "axis-label")
    .attr("x", -MARGIN.left + 4)
    .attr("y", yScale(-10) + 16)
    .attr("text-anchor", "start")
    .text("◀ Gauche");

  g.append("text")
    .attr("class", "axis-label")
    .attr("x", -MARGIN.left + 4)
    .attr("y", yScale(10) - 8)
    .attr("text-anchor", "start")
    .text("Droite ▶");

  // ── Générateur de ligne courbe
  const lineGen = d3.line()
    .x(d => xScale(d.year))
    .y(d => yScale(d.score))
    .curve(d3.curveMonotoneX)
    .defined(d => d.score != null);

  const tooltip = document.getElementById("tooltip");

  // ── Rendu des courbes par parti
  data.parties.forEach(party => {
    const sorted = [...party.scores].sort((a, b) => a.year - b.year);
    const visible = activeParties.has(party.slug);

    // Ligne principale
    const path = g.append("path")
      .datum(sorted)
      .attr("class", `party-line party-${party.slug}`)
      .attr("stroke", party.color)
      .attr("opacity", visible ? 1 : 0.08)
      .attr("d", lineGen);

    // Animation d'entrée stroke-dashoffset
    animateLine(path);

    // Points de données
    sorted.forEach(pt => {
      const symbol = pt.source === "ches_anchor" ? "◆" : "●";
      const radius = pt.source === "ches_anchor" ? 5 : 3.5;

      const dot = g.append("circle")
        .attr("class", `party-dot party-dot-${party.slug}`)
        .attr("cx", xScale(pt.year))
        .attr("cy", yScale(pt.score))
        .attr("r", radius)
        .attr("fill", party.color)
        .attr("opacity", visible ? 1 : 0.08)
        .attr("stroke", pt.source === "ches_anchor" ? "white" : "none")
        .attr("stroke-width", 1.5);

      // Tooltip
      dot
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
  applyVisibility(data);
}

function applyVisibility(data) {
  data.parties.forEach(party => {
    const visible = activeParties.has(party.slug);
    d3.selectAll(`.party-${party.slug}`).attr("opacity", visible ? 1 : 0.08);
    d3.selectAll(`.party-dot-${party.slug}`).attr("opacity", visible ? 1 : 0.08);

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
    applyVisibility(data);
  });
}

// ── Redimensionnement responsive ─────────────────────────────────

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(async () => {
    const data = await d3.json(DATA_URL).catch(() => null);
    if (data) {
      renderChart(data);
      renderLegend(data);
      setupToggleAll(data);
    }
  }, 200);
});

main();
