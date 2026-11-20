/* Shared by every page: who runs the site, the message test, and sending the forms to Netlify. */

/* ---- Who runs the site. Shown in every footer and in the privacy notice. Fill in before going public. ---- */
window.SITE = {
  operator: "",   // the person (or company) that runs the site, e.g. "Ion Popescu"
  contact: "",    // a public contact email; best a dedicated address, not your personal one
};

window.AC = (() => {
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
  };
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* ---- message test: which headline brought this visitor (?m=...), and from where (?utm_source= or ?src=).
     Nothing is stored on the device for this (that would need consent): the two values travel in the links
     between our own pages instead (see carry()). ---- */
  const VARIANTS = ["geam", "somn", "smog", "copil", "condens"];
  const q = new URLSearchParams(location.search);
  const m = (q.get("m") || "").toLowerCase();
  const src = (q.get("utm_source") || q.get("src") || "").toLowerCase().replace(/[^a-z0-9_.-]/g, "").slice(0, 40);
  const variant = () => (VARIANTS.includes(m) ? m : "geam");
  const external = document.referrer && !document.referrer.startsWith(location.origin);
  const source = () => src || (external ? "ref" : "direct");
  function carry(root) {
    if (!VARIANTS.includes(m) && !src) return;
    (root || document).querySelectorAll("a[href]").forEach(a => {
      const h = a.getAttribute("href");
      const hit = /^(index|aer|metoda|confidentialitate)\.html(\?[^#]*)?(#.*)?$/.exec(h);
      if (!hit || /[?&](m|src)=/.test(h)) return;
      const p = new URLSearchParams((hit[2] || "").slice(1));
      if (VARIANTS.includes(m)) p.set("m", m);
      if (src) p.set("src", src);
      a.setAttribute("href", `${hit[1]}.html?${p}${hit[3] || ""}`);
    });
  }

  /* fill the hidden fields every form carries */
  function stamp(form, lang) {
    const set = (name, v) => { const f = form.elements[name]; if (f) f.value = v; };
    set("varianta", variant()); set("sursa", source()); set("limba", lang);
    const id = (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2)).slice(0, 36);
    set("id", id);
    return id;
  }

  /* Netlify Forms: a urlencoded POST to "/". Only on the real site (build.py marks it); elsewhere it's a preview. */
  const live = () => document.documentElement.dataset.forms === "netlify";
  function post(form) {
    if (!live()) return Promise.resolve("preview");
    const body = new URLSearchParams(new FormData(form)).toString();
    return fetch("/", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body })
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return "sent"; });
  }
  const emailOk = s => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(String(s).trim());

  /* footer line: who runs the site, contact, privacy, method */
  function siteLine(lang) {
    const s = window.SITE || {}, ro = lang !== "en";
    const who = s.operator
      ? (ro ? "Site administrat de " : "Site run by ") + esc(s.operator)
      : (ro ? "Datele administratorului site-ului se completează înainte de lansare." : "The site operator's details are added before launch.");
    const mail = s.contact && emailOk(s.contact) ? ` · <a href="mailto:${esc(s.contact)}">${esc(s.contact)}</a>` : "";
    return `${who}${mail} · <a href="confidentialitate.html">${ro ? "Confidențialitate" : "Privacy"}</a> · <a href="metoda.html">${ro ? "Cum estimăm" : "How we estimate"}</a>`;
  }

  /* after the double opt-in link: Brevo sends people to ...?confirmat=1 */
  const confirmed = q.get("confirmat") === "1";

  /* ---- CO₂ scale, the same three levels everywhere (chart, calculator, device): good under 1,000 ppm,
     stale 1,000–1,500, very stale above 1,500. Outdoor air is about 420 ppm. ---- */
  const CO2 = {
    ro: { names: ["Aer bun", "Aer închis", "Foarte închis"], ranges: ["sub 1.000", "1.000–1.500", "peste 1.500"],
          note: "CO₂ se măsoară în ppm (părți per milion). Pentru comparație, aerul de afară are în jur de 420 ppm.",
          aria: (v, n) => `Scara CO₂: aer bun sub 1.000 ppm, aer închis între 1.000 și 1.500 ppm, foarte închis peste 1.500 ppm. Valoarea de aici: ${v} ppm, ${n}.` },
    en: { names: ["Good", "Stale", "Very stale"], ranges: ["under 1,000", "1,000–1,500", "over 1,500"],
          note: "CO₂ is measured in ppm (parts per million). For comparison, outdoor air is around 420 ppm.",
          aria: (v, n) => `CO₂ scale: good under 1,000 ppm, stale from 1,000 to 1,500 ppm, very stale over 1,500 ppm. This value: ${v} ppm, ${n}.` },
  };
  const co2Level = ppm => (ppm < 1000 ? 0 : ppm <= 1500 ? 1 : 2);
  function co2Scale(box, ppm, lang) {
    if (!box) return;
    const L = CO2[lang === "en" ? "en" : "ro"], i = co2Level(ppm);
    // three equal parts; the marker sits inside its part (the top part runs to 3,000 ppm and stops there)
    const part = i === 0 ? Math.max(0, ppm - 400) / 600 : i === 1 ? (ppm - 1000) / 500 : Math.min(1, (ppm - 1500) / 1500);
    const pos = ((i + Math.min(1, part)) / 3) * 100;
    const v = Math.round(ppm).toLocaleString(lang === "en" ? "en-GB" : "ro-RO", { useGrouping: "always" });
    box.innerHTML =
      `<div class="cs-bar">${[0, 1, 2].map(k => `<span class="cs-seg cs-${k}${k === i ? " on" : ""}"></span>`).join("")}<span class="cs-mark"></span></div>` +
      `<div class="cs-legend">${[0, 1, 2].map(k => `<span class="${k === i ? "on" : ""}"><b>${L.names[k]}</b><small>${L.ranges[k]}</small></span>`).join("")}</div>` +
      `<p class="cs-note">${L.note}</p>`;
    box.querySelector(".cs-mark").style.left = pos.toFixed(1) + "%";
    box.setAttribute("role", "img");
    box.setAttribute("aria-label", L.aria(v, L.names[i].toLowerCase()));
  }

  return { store, esc, variant, source, carry, stamp, post, live, emailOk, siteLine, confirmed, co2Level, co2Scale };
})();
