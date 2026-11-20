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

  return { store, esc, variant, source, carry, stamp, post, live, emailOk, siteLine, confirmed };
})();
