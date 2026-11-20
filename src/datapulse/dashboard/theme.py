"""Sistema visual del panel: una hoja CSS y unas líneas de JavaScript estático.

El CSS se apoya en los ``data-testid`` de Streamlit, que no son API pública: si una versión
nueva los cambia, el panel sigue funcionando pero pierde este acabado (ver ADR-0009).
"""

from __future__ import annotations

from typing import Final

import streamlit as st

# Colores base, compartidos con los argumentos ``--theme.*`` del arranque.
BACKGROUND: Final = "#080b12"
SURFACE: Final = "#111726"
TEXT: Final = "#e6eaf2"
PRIMARY: Final = "#5b9dff"

# El escape CSS «3c» es «<»: el saneador de st.html borra una hoja que contenga «<angle>».
CSS: Final = """
@property --dp-angle{syntax:"\\3c angle>";initial-value:0deg;inherits:false}
[data-testid="stAppViewContainer"]{
  background:
    radial-gradient(900px 500px at 12% -10%,rgba(91,157,255,.16),transparent 60%),
    radial-gradient(800px 480px at 95% 0%,rgba(47,212,160,.10),transparent 60%),
    #080b12}
[data-testid="stHeader"]{background:transparent}
[data-testid="stMainBlockContainer"]{padding-top:3rem}
h1{width:fit-content;background:linear-gradient(90deg,#e6eaf2,#8fb8ff 55%,#2fd4a0);
  -webkit-background-clip:text;background-clip:text;color:transparent!important;
  letter-spacing:-.02em}
/* Tarjetas: profundidad, brillo que sigue al puntero y borde iluminado que gira. */
[data-testid="stMetric"],.st-key-dp-map{
  position:relative;border-radius:14px!important;border:1px solid rgba(255,255,255,.08)!important;
  background:
    radial-gradient(260px circle at var(--mx,50%) var(--my,-40%),
      rgba(91,157,255,.20),transparent 65%),
    linear-gradient(180deg,#141b2c,#0d121e)!important;
  box-shadow:0 1px 0 rgba(255,255,255,.05) inset,0 18px 40px -24px rgba(0,0,0,.9);
  transition:transform .2s ease}
[data-testid="stMetric"]{padding:1rem 1.1rem!important}
[data-testid="stMetric"]:hover{transform:translateY(-2px)}
.st-key-dp-map{padding:1rem 1.2rem .4rem}
[data-testid="stMetric"]::before,.st-key-dp-map::before{
  content:"";position:absolute;inset:-1px;border-radius:inherit;padding:1px;pointer-events:none;
  background:conic-gradient(from var(--dp-angle),
    transparent 0 70%,#5b9dff 82%,#2fd4a0 90%,transparent 100%);
  -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);
  -webkit-mask-composite:xor;mask-composite:exclude;
  animation:dp-spin 6s linear infinite;opacity:.75}
[data-testid="stMetric"]:nth-child(odd)::before{animation-delay:-3s}
@keyframes dp-spin{to{--dp-angle:360deg}}
[data-testid="stMetricLabel"]{color:#9aa4b8}
[data-baseweb="tab-list"]{gap:.4rem}
[data-baseweb="tab"]{border-radius:10px 10px 0 0;padding:.4rem .9rem}
[data-testid="stVegaLiteChart"],[data-testid="stDataFrame"]{
  border:1px solid rgba(255,255,255,.07);border-radius:14px;background:rgba(17,23,38,.6);
  padding:.5rem}
.dp-pill{display:inline-flex;align-items:center;gap:.4rem;padding:.15rem .6rem;border-radius:999px;
  font-size:.8rem;border:1px solid rgba(255,255,255,.12);color:#c5cde0}
.dp-pill::before{content:"";width:.5rem;height:.5rem;border-radius:50%;background:var(--c,#6b7488);
  box-shadow:0 0 8px var(--c,#6b7488)}
.dp-pill.ok{--c:#3ddc84}.dp-pill.bad{--c:#ff5d5d}
/* A7: cifras tabulares para que el conteo no mueva el layout. */
[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums}
/* H1: entrada de los gráficos. Solo se ocultan cuando el script ya les puso la clase. */
.dp-chart-in>*{animation:dp-chart-in .65s cubic-bezier(.2,.7,.2,1) both}
@keyframes dp-chart-in{
  from{opacity:0;transform:translateY(10px);clip-path:inset(0 100% 0 0)}
  to{opacity:1;transform:none;clip-path:inset(0 0 0 0)}}
@media (prefers-reduced-motion:reduce){
  .dp-chart-in>*{animation:none}
  [data-testid="stMetric"]::before,.st-key-dp-map::before{animation:none}
  [data-testid="stMetric"]{transition:none}
  [data-testid="stMetric"]:hover{transform:none}}
"""

# Delegado en el documento: sobrevive a los redibujados de Streamlit. Texto fijo, sin datos.
# Además, la primera vez que una tarjeta o un gráfico entra en pantalla: las cifras cuentan
# hasta su valor (A7) y el gráfico entra con un barrido (H1). Sin JavaScript, o con
# prefers-reduced-motion, todo se ve directamente con su valor final.
# Sin el carácter «menor que»: el saneador de st.html lo trataría como una etiqueta.
JS: Final = """
(() => {
  const doc = window.parent && window.parent.document ? window.parent.document : document;
  if (doc.__dpGlow) return;
  doc.__dpGlow = true;
  doc.addEventListener("pointermove", (e) => {
    const card = e.target.closest && e.target.closest('[data-testid="stMetric"], .st-key-dp-map');
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty("--mx", (e.clientX - r.left) + "px");
    card.style.setProperty("--my", (e.clientY - r.top) + "px");
  }, { passive: true });

  const win = doc.defaultView || window;
  const reduce = win.matchMedia("(prefers-reduced-motion: reduce)");
  const seen = new Set();
  const done = new WeakSet();
  const NUM = /^(\\D*?)(\\d[\\d,]*(?:\\.\\d+)?)(.*)$/s;

  const countUp = (el) => {
    const walker = doc.createTreeWalker(el, 4);
    const node = walker.nextNode();
    if (!node) return;
    const final = node.nodeValue;
    const label = el.closest('[data-testid="stMetric"]');
    const id = (label ? label.innerText.split("\\n")[0] : "") + "|" + final;
    const m = final.match(NUM);
    if (seen.has(id) || !m) return;
    seen.add(id);
    const [, pre, digits, post] = m;
    const target = parseFloat(digits.replace(/,/g, ""));
    const decimals = (digits.split(".")[1] || "").length;
    const grouped = digits.includes(",");
    const fmt = (v) => v.toLocaleString("en-US", {
      minimumFractionDigits: decimals, maximumFractionDigits: decimals, useGrouping: grouped,
    });
    const start = win.performance.now();
    let shown = final;
    const end = () => { if (node.nodeValue === shown) node.nodeValue = final; };
    const safety = win.setTimeout(end, 1500);
    const step = (now) => {
      if (node.nodeValue !== shown) return; // Streamlit lo redibujó: manda su valor
      const t = Math.min(1, (now - start) / 700);
      if (t === 1) { win.clearTimeout(safety); end(); return; }
      shown = pre + fmt(target * (1 - Math.pow(1 - t, 3))) + post;
      node.nodeValue = shown;
      win.requestAnimationFrame(step);
    };
    win.requestAnimationFrame(step);
  };

  const io = new win.IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      io.unobserve(e.target);
      if (reduce.matches) continue;
      if (e.target.matches('[data-testid="stMetricValue"]')) countUp(e.target);
      else e.target.classList.add("dp-chart-in");
    }
  });
  const scan = () => {
    for (const el of doc.querySelectorAll(
      '[data-testid="stMetricValue"], [data-testid="stVegaLiteChart"]')) {
      if (done.has(el)) continue;
      done.add(el);
      io.observe(el);
    }
  };
  new win.MutationObserver(scan).observe(doc.body, { childList: true, subtree: true });
  scan();
})();
"""


def inject() -> None:
    st.html(f"<style>{CSS}</style>")
    st.html(f"<script>{JS}</script>", unsafe_allow_javascript=True)
