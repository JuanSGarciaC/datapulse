// Registros que fluyen por el mapa del pipeline (canvas 2D, sin dependencias).
//
// El SVG del mapa se dibuja en el servidor y se lee sin JavaScript; esta capa solo añade
// partículas encima. Cada partícula es un paquete de registros: nace en una fuente con un
// ritmo proporcional a sus filas leídas, y en «Validación» las rechazadas se desvían en rojo
// a la cuarentena. En dbt → marts, las filas con aviso salen en ámbar.
//
// Reglas: con prefers-reduced-motion no arranca; se pausa fuera de la vista o con la pestaña
// oculta; en móvil usa menos partículas y sin estelas; si algo falla, se retira y el SVG
// vuelve a su animación CSS. Los datos son números del servidor (sin texto de la API).

const BLUE = [91, 157, 255];
const AQUA = [47, 212, 160];
const RED = [255, 93, 93];
const AMBER = [255, 194, 71];
const SPEED = 150; // unidades del viewBox por segundo: igual en aristas cortas y largas
const BASE_RATE = 1.6; // partículas/s de una fuente al 100 %

function length(c) {
  // Media entre la cuerda y el polígono de control: suficiente para 7 curvas suaves.
  const d = (a, b) => Math.hypot(c[b] - c[a], c[b + 1] - c[a + 1]);
  return (d(0, 6) + d(0, 2) + d(2, 4) + d(4, 6)) / 2;
}

function bezier(c, t, out) {
  const u = 1 - t;
  const a = u * u * u, b = 3 * u * u * t, d = 3 * u * t * t, e = t * t * t;
  out[0] = a * c[0] + b * c[2] + d * c[4] + e * c[6];
  out[1] = a * c[1] + b * c[3] + d * c[5] + e * c[7];
  return out;
}

class Flow {
  constructor() {
    this.model = null;
    this.parts = [];
    this.rings = [];
    this.acc = new Map();
    this.last = 0;
    this.raf = 0;
    this.visible = true;
    this.started = false;
    this.failed = false;
    this.lastFrame = 0;
    this.pt = [0, 0];
    this.reduce = matchMedia("(prefers-reduced-motion: reduce)");
    this.small = matchMedia("(max-width: 600px), (pointer: coarse)");
    this.onPref = () => this.reduce.matches ? this.stop(true) : this.kick();
    this.reduce.addEventListener("change", this.onPref);
    this.onVis = () => this.kick();
    document.addEventListener("visibilitychange", this.onVis);
    this.canvas = document.createElement("canvas");
    this.canvas.className = "dp-flow-canvas";
    this.canvas.setAttribute("aria-hidden", "true");
    Object.assign(this.canvas.style, {
      position: "absolute", left: "0", top: "0", pointerEvents: "none", zIndex: "1",
    });
    this.ctx = this.canvas.getContext("2d");
    this.io = new IntersectionObserver((entries) => {
      this.visible = entries.some((e) => e.isIntersecting);
      this.kick();
    });
    // Perro guardián: si el bucle dejó de pintar estando visible, se retira.
    this.watch = setInterval(() => {
      if (this.raf && this.started && performance.now() - this.lastFrame > 1500) this.fail();
    }, 1000);
    this.frame = (t) => this.tick(t);
  }

  update(model) {
    this.model = model;
    this.acc = new Map(); // los ritmos van por arista y las aristas son nuevas
    for (const e of model.edges) e.len = length(e.curve);
    model.quarantine.len = length(model.quarantine.curve);
    this.byFrom = new Map();
    for (const e of model.edges) {
      if (!this.byFrom.has(e.from)) this.byFrom.set(e.from, []);
      this.byFrom.get(e.from).push(e);
    }
    const incoming = new Set(model.edges.filter((e) => e.flowing).map((e) => e.to));
    // Nacen partículas en las aristas que fluyen y a las que no llega ninguna otra.
    this.heads = model.edges.filter((e) => e.flowing && !incoming.has(e.from));
    this.kick();
  }

  host() {
    const box = document.querySelector(".st-key-dp-map");
    const svg = box && box.querySelector("svg.dp-map");
    return svg ? [box, svg] : [null, null];
  }

  kick(fromTick) {
    const runnable = this.model && !this.failed && !this.reduce.matches && !document.hidden
      && this.visible;
    if (runnable && !this.raf) {
      if (!fromTick) this.last = 0; // tras una pausa no se recupera el tiempo perdido
      this.raf = requestAnimationFrame(this.frame);
    } else if (!runnable && this.raf) {
      cancelAnimationFrame(this.raf);
      this.raf = 0;
    }
    if (this.reduce.matches || this.failed) this.stop(this.reduce.matches);
  }

  stop(clear) {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.started = false;
    const [box] = this.host();
    if (box) box.classList.remove("dp-flow-on");
    if (clear) {
      this.parts.length = 0;
      this.rings.length = 0;
      this.canvas.remove();
    }
  }

  fail() {
    this.failed = true;
    this.stop(true);
  }

  layout(box, svg) {
    if (this.canvas.parentNode !== box) {
      box.appendChild(this.canvas);
      this.io.disconnect();
      this.io.observe(box);
    }
    const b = box.getBoundingClientRect();
    const r = svg.getBoundingClientRect();
    const dpr = Math.min(this.small.matches ? 1 : 2, window.devicePixelRatio || 1);
    const w = Math.round(r.width), h = Math.round(r.height);
    if (this.cw !== w || this.ch !== h || this.dpr !== dpr) {
      this.cw = w; this.ch = h; this.dpr = dpr;
      this.canvas.width = w * dpr;
      this.canvas.height = h * dpr;
      this.canvas.style.width = w + "px";
      this.canvas.style.height = h + "px";
    }
    // Solo transform: la capa sigue al SVG sin provocar layout.
    this.canvas.style.transform =
      `translate(${r.left - b.left - box.clientLeft}px,${r.top - b.top - box.clientTop}px)`;
    this.scale = w / this.model.view[0];
  }

  spawn(edge, color, quarantine) {
    const max = this.small.matches ? 60 : 160;
    if (this.parts.length >= max) return;
    this.parts.push({ edge, t: 0, color, q: quarantine, life: 1 });
  }

  next(p) {
    // Una partícula llega al final de su arista: decide adónde sigue.
    const m = this.model;
    if (p.edge === "q") {
      this.rings.push({ t: 0 });
      return false;
    }
    if (p.edge.to === "validation") {
      if (p.q) {
        p.edge = "q"; p.t = 0; p.color = RED;
        return true;
      }
    }
    const outs = (this.byFrom.get(p.edge.to) || []).filter((e) => e.flowing);
    if (!outs.length) return false; // se queda en la etapa: sin aguas abajo, se apaga
    const e = outs[0];
    p.edge = e; p.t = 0;
    if (e.to === "marts" && Math.random() < m.warn_share) p.color = AMBER;
    else if (e.from === "raw" || e.from === "dbt") p.color = p.color === AMBER ? AMBER : AQUA;
    return true;
  }

  tick(now) {
    this.raf = 0;
    try {
      const [box, svg] = this.host();
      if (!box) { this.kick(); return; }
      this.layout(box, svg);
      const dt = this.last ? Math.min(0.05, (now - this.last) / 1000) : 0;
      this.last = now;
      const m = this.model;
      const small = this.small.matches;
      // Nacimientos (acumulador por arista, sin azar en el ritmo).
      for (const e of this.heads) {
        const rate = BASE_RATE * Math.max(0.25, e.rate) * (e.running ? 2.5 : 1)
          * (small ? 0.5 : 1);
        const a = (this.acc.get(e) || 0) + rate * dt;
        const n = Math.floor(a);
        this.acc.set(e, a - n);
        const share = m.quarantine.share[e.from] || 0;
        for (let i = 0; i !== n; i++) this.spawn(e, BLUE, Math.random() < share);
      }
      const ctx = this.ctx;
      const k = this.scale * this.dpr;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      ctx.setTransform(k, 0, 0, k, 0, 0);
      ctx.globalCompositeOperation = "lighter";
      const qc = m.quarantine.curve;
      const pt = this.pt;
      let alive = 0;
      for (const p of this.parts) {
        const len = p.edge === "q" ? m.quarantine.len : p.edge.len;
        p.t += (dt * SPEED * (p.edge.running ? 1.3 : 1)) / len;
        if (p.t >= 1 && !this.next(p)) continue;
        const cc = p.edge === "q" ? qc : p.edge.curve;
        const [r, g, b] = p.color;
        if (!small) {
          for (let j = 3; j !== 0; j--) {
            const tt = p.t - (j * 7) / (p.edge === "q" ? m.quarantine.len : p.edge.len);
            if (tt > 0) {
              bezier(cc, tt, pt);
              ctx.fillStyle = `rgba(${r},${g},${b},${0.18 * (4 - j) / 3})`;
              ctx.beginPath(); ctx.arc(pt[0], pt[1], 2.6, 0, 6.2832); ctx.fill();
            }
          }
        }
        bezier(cc, p.t, pt);
        ctx.fillStyle = `rgba(${r},${g},${b},.95)`;
        ctx.beginPath(); ctx.arc(pt[0], pt[1], 3.2, 0, 6.2832); ctx.fill();
        this.parts[alive++] = p;
      }
      this.parts.length = alive;
      // Ondas en la bandeja de cuarentena al llegar cada paquete rechazado.
      const bx = qc[6], by = qc[7] + 20;
      let ra = 0;
      for (const ring of this.rings) {
        ring.t += dt / 0.7;
        if (ring.t >= 1) continue;
        ctx.strokeStyle = `rgba(255,93,93,${0.6 * (1 - ring.t)})`;
        ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(bx, by, 8 + ring.t * 30, 0, 6.2832); ctx.stroke();
        this.rings[ra++] = ring;
      }
      this.rings.length = ra;
      this.lastFrame = performance.now();
      if (!this.started) {
        this.started = true;
        box.classList.add("dp-flow-on"); // solo ahora se apagan los guiones CSS
      }
    } catch (err) {
      console.warn("dp-flow desactivado:", err);
      this.fail();
      return;
    }
    this.kick(true);
  }
}

export default function (component) {
  const flow = (window.__dpFlow = window.__dpFlow || new Flow());
  clearTimeout(flow.unmount);
  flow.update(component.data);
  // Streamlit vuelve a llamar a esta función con datos nuevos; solo se para si no vuelve.
  return () => {
    flow.unmount = setTimeout(() => flow.stop(true), 500);
  };
}
