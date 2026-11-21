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

  // ── Demo en vivo: ¿está DataPulse en marcha en este equipo? ──────────────────
  // Puertos por defecto de Docker Compose. Para otro stack local: ?api=8107&panel=8108
  // (solo números de puerto: los enlaces nunca salen del propio equipo).
  const live = document.querySelector('[data-live]');
  if (live) {
    const q = new URLSearchParams(location.search);
    const port = (name, fallback) => {
      const v = q.get(name);
      return v && /^\d{4,5}$/.test(v) && +v >= 1024 && +v <= 65535 ? v : fallback;
    };
    const host = q.has('api') || q.has('panel') ? '127.0.0.1' : 'localhost';
    const api = `http://${host}:${port('api', '8000')}`;
    const panel = `http://${host}:${port('panel', '8501')}`;
    document.querySelectorAll('[data-href]').forEach((a) => {
      a.href = a.dataset.href === 'panel' ? panel : `${api}/docs`;
    });
    const text = live.querySelector('.live-text');
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 2500);
    fetch(`${api}/health`, { signal: ctrl.signal, cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => {
        if (d.status !== 'ok') throw new Error('no ok');
        live.classList.add('on');
        text.textContent = 'DataPulse está en marcha en este equipo: los enlaces abren tu panel y tu API.';
      })
      .catch(() => {
        live.classList.add('off');
        text.textContent = 'La demo no está en marcha en este equipo: lánzala con el comando de arriba y recarga.';
      })
      .finally(() => clearTimeout(timer));
  }

  // ── C3 + C13: recorrido atado al scroll ──────────────────────────────────────
  const wrap = document.querySelector('.scrub-wrap');
  if (!wrap || reduced) return;

  // Carril y hueco de cada ficha en cada uno de los 7 pasos.
  // Carriles: 0 fuentes · 1 validación · 2 raw · 3 staging · 4 marts.
  const P = {
    c3:    [[0, 0], [0, 0], [2, 0], [2, 0], [3, 0], [4, 0], [4, 0]],
    c9:    [[0, 1], [1, 0], [1, 0], [1, 0], [1, 0], [1, 0], [1, 0]],
    o231:  [[0, 2], [1, 1], [1, 1], [1, 1], [1, 1], [1, 1], [1, 1]],
    o65a:  [[0, 3], [0, 1], [2, 1], [2, 1], [2, 1], [2, 1], [2, 1]],
    o65b:  [[0, 4], [0, 2], [2, 2], [2, 2], [3, 1], [4, 1], [4, 1]],
    rs:    [[0, 5], [0, 3], [0, 0], [1, 2], [1, 2], [1, 2], [1, 2]],
    daily: [[4, 2], [4, 2], [4, 2], [4, 2], [4, 2], [4, 2], [4, 2]],
  };
  const N = 7;

  // data-steps="a" o "a-b": visible en esos pasos.
  const stepped = [...wrap.querySelectorAll('[data-steps]')].map((el) => {
    const [a, b = a] = el.dataset.steps.split('-').map(Number);
    return { el, a, b };
  });
  const visibleAt = (el, i) => {
    const s = el.dataset.steps;
    if (!s) return true;
    const [a, b = a] = s.split('-').map(Number);
    return i >= a && i <= b;
  };

  const tickets = [...wrap.querySelectorAll('.tk[data-tk]')].map((el) => ({ el, path: P[el.dataset.tk] }));
  const lanes = [...wrap.querySelectorAll('.lane')];
  // Contador acumulado: registros que han llegado a cada capa hasta el paso i (raw solo añade).
  const counts = Array.from({ length: N }, (_, i) => lanes.map((_, l) =>
    tickets.filter(({ el, path }) => path.some(([lane], j) => j <= i && lane === l && visibleAt(el, j))).length));

  const board = wrap.querySelector('.board');
  const stepN = wrap.querySelector('.step-n');
  const bar = wrap.querySelector('.meter-bar i');
  let W = 0, H = 0, compact = false, tw = 0, th = 0, ticking = false, last = -1;
  const GAP = 8;

  const measure = () => {
    W = board.clientWidth; H = board.clientHeight; compact = W < 560;
    if (compact) {
      tw = (W - 2 * GAP) / 3;
      th = Math.max(30, (H / 5 - 20 - GAP) / 2);
    } else {
      tw = W / 5 - 12;
      th = Math.min(100, (H - 30) / 6 - GAP);
    }
    board.style.setProperty('--tw', `${tw.toFixed(1)}px`);
    board.style.setProperty('--th', `${th.toFixed(1)}px`);
  };
  const place = ([l, s]) => compact
    ? [(s % 3) * (tw + GAP), l * (H / 5) + 20 + Math.floor(s / 3) * (th + GAP)]
    : [l * (W / 5) + 6, 30 + s * (th + GAP)];

  const setStep = (i) => {
    for (const { el, a, b } of stepped) el.classList.toggle('on', i >= a && i <= b);
    lanes.forEach((lane, l) => {
      lane.querySelector('b').textContent = String(counts[i][l]);
      lane.classList.toggle('on', tickets.some(({ path }) => path[i][0] === l && path[Math.max(0, i - 1)][0] !== l));
    });
    stepN.textContent = String(i + 1);
  };

  const render = () => {
    ticking = false;
    const r = wrap.getBoundingClientRect();
    const p = clamp(-r.top / Math.max(1, r.height - window.innerHeight));
    const local = p * N;
    const i = Math.min(N - 1, Math.floor(local));
    // La ficha viaja en la primera mitad del paso y reposa en la segunda.
    const k = smooth(clamp((local - i) / 0.5));
    if (i !== last) { setStep(i); last = i; }
    bar.style.transform = `scaleX(${p.toFixed(4)})`;
    for (const { el, path } of tickets) {
      const [x0, y0] = place(path[Math.max(0, i - 1)]);
      const [x1, y1] = place(path[i]);
      el.style.transform = `translate3d(${(x0 + (x1 - x0) * k).toFixed(1)}px, ${(y0 + (y1 - y0) * k).toFixed(1)}px, 0)`;
    }
  };
  const request = () => { if (!ticking) { ticking = true; requestAnimationFrame(render); } };

  wrap.classList.add('scrub');
  measure();
  render();
  window.addEventListener('scroll', request, { passive: true });
  window.addEventListener('resize', () => { measure(); request(); });
})();
