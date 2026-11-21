// Shared Plotly helpers so every tab's charts look the same.
//
//   Charts.plot("my-chart", () => ({ data: [...], layout: {...} }));
//
// The builder is a function so charts redraw with the right colors when the
// system switches between light and dark mode. Colors come from the tokens in
// style.css: use Charts.color("series-1") etc., in order, for one color per series.

const Charts = (() => {
  const registry = new Map();

  // Charts sit inside .card boxes, which set their own colors (see style.css),
  // so read the tokens from a card when the page has one.
  function color(name) {
    const scope = document.querySelector(".card") || document.documentElement;
    return getComputedStyle(scope).getPropertyValue(`--${name}`).trim();
  }

  function series(i) {
    return color(`series-${(i % 8) + 1}`);
  }

  function axis(extra = {}) {
    return {
      gridcolor: color("grid"),
      linecolor: color("axis"),
      tickcolor: color("axis"),
      zeroline: false,
      showline: true,
      automargin: true,
      tickfont: { color: color("muted"), size: 12 },
      title: { font: { color: color("text-2"), size: 12 } },
      ...extra,
    };
  }

  function layout(extra = {}) {
    const { xaxis = {}, yaxis = {}, ...rest } = extra;
    return {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: getComputedStyle(document.body).fontFamily, color: color("text-2"), size: 12 },
      margin: { l: 48, r: 16, t: 8, b: 40 },
      showlegend: false,
      legend: { orientation: "h", x: 0, y: 1.12, font: { color: color("text-2") } },
      // Hover tooltips: white box with black text
      hoverlabel: {
        bgcolor: "#ffffff",
        bordercolor: color("axis"),
        font: { color: "#0b0b0b", size: 13 },
      },
      bargap: 0.25,
      barcornerradius: 4,
      xaxis: axis({ showgrid: false, ...xaxis }),
      yaxis: axis({ showline: false, ...yaxis }),
      ...rest,
    };
  }

  const config = { displayModeBar: false, responsive: true };

  // ---- Rounded HTML tooltips ----
  // Plotly draws its own tooltips as square SVG boxes. A chart that passes a
  // `tip` function gets this HTML tooltip instead (rounded, styled in style.css).
  // tip(points) returns the tooltip's HTML; escape any names you put in it.
  let tipEl = null;
  function tipBox() {
    if (!tipEl) {
      tipEl = document.createElement("div");
      tipEl.setAttribute("role", "tooltip");
      document.body.appendChild(tipEl);
    }
    return tipEl;
  }
  function hideTip() {
    if (tipEl) tipEl.style.display = "none";
  }
  // theme: "light" (white box) or "dark" (black box)
  function tooltip(el, tip, theme = "light") {
    el.on("plotly_hover", (ev) => {
      const html = tip(ev.points);
      if (!html || !ev.event) return hideTip();
      const t = tipBox();
      t.className = `chart-tip ${theme}`;
      t.innerHTML = html;
      t.style.display = "block";
      // Next to the mouse, flipped to the other side near the window's edge
      const { clientX: x, clientY: y } = ev.event;
      const r = t.getBoundingClientRect();
      const left = x + 16 + r.width > window.innerWidth - 8 ? x - r.width - 16 : x + 16;
      const top = y + 16 + r.height > window.innerHeight - 8 ? y - r.height - 16 : y + 16;
      t.style.left = `${Math.max(8, left)}px`;
      t.style.top = `${Math.max(8, top)}px`;
    });
    el.on("plotly_unhover", hideTip);
  }
  // Turn off Plotly's own tooltip on every hoverable trace (hover events still fire)
  function withoutPlotlyTips(data) {
    return data.map((tr) => {
      if (tr.hoverinfo === "skip") return tr;
      const { hovertemplate, ...rest } = tr;
      return { ...rest, hoverinfo: "none" };
    });
  }

  const tips = new Map();  // chart id -> tip function

  function draw(id) {
    const { data, layout: lay } = registry.get(id)();
    Plotly.react(id, tips.has(id) ? withoutPlotlyTips(data) : data, layout(lay));
  }

  // options.tip: optional function(points) -> HTML for a rounded tooltip
  function plot(id, builder, options = {}) {
    registry.set(id, builder);
    const el = document.getElementById(id);
    if (!el) return;
    const { data, layout: lay } = builder();
    if (options.tip) tips.set(id, options.tip);
    Plotly.newPlot(el, options.tip ? withoutPlotlyTips(data) : data, layout(lay), config).then(() => {
      if (options.tip) tooltip(el, options.tip);
    });
  }

  // Redraw every chart with the current colors (after a light/dark switch)
  function redrawAll() {
    for (const id of registry.keys()) draw(id);
  }
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redrawAll);

  // A collapsible "Show data" table, so every value is readable without hovering.
  // columns: [{ key, label, num? }]
  function dataTable(containerId, columns, rows) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
    const head = columns.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
    const body = rows
      .map((r) => `<tr>${columns.map((c) => `<td class="${c.num ? "num" : ""}">${esc(r[c.key])}</td>`).join("")}</tr>`)
      .join("");
    el.innerHTML = `<details class="data-table"><summary>Show data</summary>
      <div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></details>`;
  }

  return { plot, redrawAll, tooltip, withoutPlotlyTips, color, series, dataTable, config };
})();
