// DataPulse · landing. JS puro, sin dependencias. Solo anima transform y opacity.
// Sin JS o con prefers-reduced-motion, la página queda estática y con todo el contenido visible.
(() => {
  'use strict';
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const smooth = (t) => t * t * (3 - 2 * t);

  // ── A1 / A2: apariciones al entrar en pantalla ───────────────────────────────
  document.querySelectorAll('.stagger').forEach((list) => {
    [...list.children].forEach((el, i) => el.style.setProperty('--i', String(i)));
  });
  const onVisible = (els, fn, threshold = 0.2) => {
    if (!('IntersectionObserver' in window)) { els.forEach(fn); return; }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) { io.unobserve(e.target); fn(e.target); } });
    }, { threshold });
    els.forEach((el) => io.observe(el));
  };
  onVisible(document.querySelectorAll('.reveal, .stagger'), (el) => el.classList.add('in'), 0.12);

  // ── B1: titular palabra a palabra ────────────────────────────────────────────
  const title = document.querySelector('.split');
  if (title && !reduced) {
    const words = title.textContent.trim().split(/\s+/);
    title.setAttribute('aria-label', title.textContent.trim());
    title.textContent = '';
    words.forEach((w, i) => {
      const s = document.createElement('span');
      s.className = 'w';
      s.setAttribute('aria-hidden', 'true');
      s.style.setProperty('--i', String(i));
      s.textContent = w;
      title.append(s, i < words.length - 1 ? ' ' : '');
    });
    title.classList.add('ready');
  }

  // ── A7: cifras que cuentan ───────────────────────────────────────────────────
  const countUp = (el) => {
    const end = Number(el.dataset.count);
    const suffix = el.dataset.suffix || '';
    if (reduced || !Number.isFinite(end)) return;
    const t0 = performance.now();
    const dur = 1400;
    const tick = (now) => {
      const t = clamp((now - t0) / dur);
      el.textContent = Math.round(end * (1 - Math.pow(1 - t, 3))) + suffix;
      if (t < 1) requestAnimationFrame(tick);
    };
    el.textContent = '0' + suffix;
    requestAnimationFrame(tick);
  };
  onVisible(document.querySelectorAll('[data-count]'), countUp, 0.6);

  // ── H3: terminal que teclea ──────────────────────────────────────────────────
  const term = document.querySelector('.term-body');
  if (term && !reduced) {
    const lines = [...term.children];
    const texts = lines.map((l) => l.textContent);
    // El hueco se reserva: las líneas pendientes ocupan su sitio, invisibles.
    lines.forEach((l) => l.classList.add('pending'));
    const run = async () => {
      const wait = (ms) => new Promise((r) => setTimeout(r, ms));
      for (let i = 0; i < lines.length; i++) {
        const l = lines[i];
        l.classList.remove('pending');
        if (l.classList.contains('cmd')) {
          l.textContent = '';
          l.classList.add('caret');
          for (const ch of texts[i]) { l.textContent += ch; await wait(28); }
          await wait(380);
          l.classList.remove('caret');
        } else {
          await wait(l.classList.contains('dim') ? 520 : 140);
        }
      }
    };
    onVisible([term], () => { run(); }, 0.5);
  }

  // ── C3 + C13: recorrido atado al scroll ──────────────────────────────────────
  const wrap = document.querySelector('.scrub-wrap');
  if (!wrap || reduced) return;

  // Fotogramas por fila: [progreso, x, y, escala, opacidad]; x e y en fracción de la caja.
  // Etapas: 0–.2 fuentes · .2–.4 validación · .4–.6 raw · .6–.8 staging · .8–1 marts.
  const Q = 0.78; // escala de lo que sale del flujo (columna derecha)
  const K = {
    c3:   [[0, .02, .02, 1, 1], [.22, .02, .04, 1, 1], [.30, 0, .06, 1, 1], [.82, 0, .06, 1, 1], [.90, 0, .04, 1, 1]],
    c9:   [[0, .14, .17, 1, 1], [.22, .14, .17, 1, 1], [.32, .58, .08, Q, 1]],
    o231: [[0, .05, .33, 1, 1], [.24, .05, .33, 1, 1], [.34, .58, .26, Q, 1]],
    o65a: [[0, .16, .49, 1, 1], [.22, .16, .49, 1, 1], [.30, 0, .24, 1, 1], [.64, 0, .24, 1, 1], [.72, 0, .42, 1, 0]],
    o65b: [[0, .07, .65, 1, 1], [.22, .07, .65, 1, 1], [.30, 0, .42, 1, 1], [.64, 0, .42, 1, 1], [.72, 0, .26, 1, 1], [.90, 0, .30, 1, 1]],
    rs:   [[0, .18, .81, 1, 1], [.22, .18, .81, 1, 1], [.30, 0, .60, 1, 1], [.42, 0, .60, 1, 1], [.52, .58, .44, Q, .7]],
    daily:[[0, 0, .58, 1, 1], [.84, .04, .58, 1, 1], [.92, 0, .56, 1, 1]],
  };
  const sample = (frames, p) => {
    if (p <= frames[0][0]) return frames[0];
    for (let i = 1; i < frames.length; i++) {
      const a = frames[i - 1], b = frames[i];
      if (p <= b[0]) {
        const t = smooth((p - a[0]) / (b[0] - a[0]));
        return a.map((v, j) => v + (b[j] - v) * t);
      }
    }
    return frames[frames.length - 1];
  };

  // data-show="a [b]": aparece entre a y a+F, desaparece entre b y b+F.
  const F = 0.05;
  const shows = [...wrap.querySelectorAll('[data-show]')].map((el) => {
    const [a, b] = el.dataset.show.split(/\s+/).map(Number);
    return { el, a, b };
  });
  const showOpacity = ({ a, b }, p) =>
    clamp((p - a) / F) * (Number.isFinite(b) ? 1 - clamp((p - b) / F) : 1);

  const rows = [...wrap.querySelectorAll('.row[data-row]')].map((el) => ({ el, k: K[el.dataset.row] }));
  const rail = [...wrap.querySelectorAll('.rail li')];
  const stage = wrap.querySelector('.stage');
  let W = 0, H = 0, ticking = false, lastStage = -1;

  wrap.classList.add('scrub');

  const measure = () => { W = stage.clientWidth; H = stage.clientHeight; };
  const render = () => {
    ticking = false;
    const r = wrap.getBoundingClientRect();
    const p = clamp(-r.top / Math.max(1, r.height - window.innerHeight));

    for (const s of shows) {
      if (s.el.classList.contains('row')) continue; // la opacidad de las filas la decide su fotograma
      s.el.style.opacity = String(showOpacity(s, p));
    }
    for (const { el, k } of rows) {
      if (!k) continue;
      const [, x, y, sc, o] = sample(k, p);
      const extra = el.dataset.show ? showOpacity(shows.find((s) => s.el === el), p) : 1;
      el.style.transform = `translate3d(${(x * W).toFixed(1)}px, ${(y * H).toFixed(1)}px, 0) scale(${sc.toFixed(3)})`;
      el.style.opacity = String(o * extra);
    }
    const st = Math.min(4, Math.floor(p * 5));
    if (st !== lastStage) {
      rail.forEach((li, i) => li.classList.toggle('on', i === st));
      lastStage = st;
    }
  };
  const request = () => { if (!ticking) { ticking = true; requestAnimationFrame(render); } };

  measure();
  render();
  window.addEventListener('scroll', request, { passive: true });
  window.addEventListener('resize', () => { measure(); request(); });
})();
