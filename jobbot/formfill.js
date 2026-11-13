// Reads every question on an application form and tags each control with data-jh="<qid>".
// Returns a list of questions: {qid, kind, label, required, options, multi, hint}.
(() => {
  const vis = (e) => {
    if (!e) return false;
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  };
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim();
  const strip = (t) => clean(t).replace(/\s*[*✱]\s*$/, "").replace(/\s*\(required\)\s*$/i, "").trim();
  const textOf = (e) => (e ? clean(e.innerText || e.textContent) : "");
  const byId = (id) => (id ? document.getElementById(id) : null);

  function directLabel(e) {
    if (e.id) {
      const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`);
      if (l && textOf(l)) return textOf(l);
    }
    const al = e.getAttribute("aria-labelledby");
    if (al) {
      const t = al.split(/\s+/).map((i) => textOf(byId(i))).join(" ").trim();
      if (t) return t;
    }
    const cl = e.closest("label");
    if (cl && textOf(cl)) return textOf(cl);
    const aria = e.getAttribute("aria-label");
    if (aria) return clean(aria);
    return "";
  }

  // Walk up from an element until an ancestor has text other than the given option texts.
  function contextLabel(e, exclude) {
    let a = e.parentElement;
    for (let i = 0; i < 8 && a && a !== document.body; i++, a = a.parentElement) {
      const lg = a.querySelector(":scope > legend");
      if (lg && textOf(lg)) return textOf(lg);
      let t = a.innerText || "";
      for (const x of exclude) if (x) t = t.split(x).join(" ");
      t = clean(t);
      if (t.length > 1) return t.slice(0, 500);
    }
    return "";
  }

  const isRequired = (e, label) =>
    e.required || e.getAttribute("aria-required") === "true" || /[*✱]\s*$/.test(label || "") || /\(required\)/i.test(label || "");

  const questions = [];
  let n = 0;
  const seenGroups = new Set();
  const tag = (el, qid) => el.setAttribute("data-jh", qid);

  const controls = [...document.querySelectorAll("input, textarea, select")];
  for (const e of controls) {
    const type = (e.getAttribute("type") || e.tagName).toLowerCase();
    if (["hidden", "submit", "button", "image", "reset", "search"].includes(type)) continue;
    if (e.disabled || e.readOnly && type !== "file") continue;
    if (e.closest("[aria-hidden=true]") && type !== "file") continue;

    if (type === "file") {
      const label = directLabel(e) || contextLabel(e, []);
      const qid = "q" + n++;
      tag(e, qid);
      questions.push({ qid, kind: "file", label: strip(label).slice(0, 200), required: isRequired(e, label),
                       hint: [e.id, e.name].join(" ") });
      continue;
    }

    if (type === "radio" || type === "checkbox") {
      const group = e.name
        ? [...document.querySelectorAll(`input[type=${type}][name="${CSS.escape(e.name)}"]`)]
        : [e];
      const key = type + ":" + (e.name || "solo" + n);
      if (seenGroups.has(key)) continue;
      seenGroups.add(key);
      const optLabels = group.map((g) => directLabel(g) || clean(g.value));
      const qid = "q" + n++;
      group.forEach((g, i) => { tag(g, qid); g.setAttribute("data-jh-opt", String(i)); });
      if (type === "checkbox" && group.length === 1) {
        // A lone checkbox, e.g. "I agree to the privacy policy".
        const own = optLabels[0];
        const ctx = contextLabel(e, [own]);
        const label = own && own.length > 3 ? own : ctx;
        questions.push({ qid, kind: "checkbox_single", label: strip(label), required: isRequired(e, label),
                         options: ["checked", "unchecked"] });
      } else {
        const label = contextLabel(group[0].closest("label") || group[0], optLabels);
        questions.push({ qid, kind: type === "radio" ? "radio" : "checkbox", label: strip(label),
                         required: group.some((g) => isRequired(g, label)) || /[*✱]/.test(label),
                         options: optLabels, multi: type === "checkbox" });
      }
      continue;
    }

    if (!vis(e)) continue;
    let label = directLabel(e);
    if (!label || label.length < 2) label = e.getAttribute("placeholder") || contextLabel(e, []);
    const qid = "q" + n++;
    tag(e, qid);
    if (e.tagName === "SELECT") {
      const opts = [...e.options].map((o) => clean(o.text)).filter((t) => t && !/^(select|choose|--)/i.test(t));
      questions.push({ qid, kind: "select", label: strip(label), required: isRequired(e, label), options: opts,
                       multi: e.multiple });
    } else if (e.getAttribute("role") === "combobox" || e.getAttribute("aria-autocomplete")) {
      questions.push({ qid, kind: "combobox", label: strip(label), required: isRequired(e, label),
                       multi: /\[\]$/.test(e.id || "") || !!e.closest("[class*=multi]"), hint: e.id });
    } else {
      questions.push({ qid, kind: e.tagName === "TEXTAREA" ? "textarea" : type === "text" ? "text" : type,
                       label: strip(label), required: isRequired(e, label), hint: [e.id, e.name, e.getAttribute("autocomplete")].filter(Boolean).join(" ") });
    }
  }

  // Yes/No answered with buttons (Ashby and some custom forms).
  const btns = [...document.querySelectorAll("button:not([type=submit])")].filter(vis);
  const parents = new Set(btns.filter((b) => /^(yes|no)$/i.test(textOf(b))).map((b) => b.parentElement));
  for (const p of parents) {
    const opts = [...p.querySelectorAll(":scope > button")].filter(vis);
    if (opts.length < 2 || opts.length > 4) continue;
    const optLabels = opts.map(textOf);
    const qid = "q" + n++;
    opts.forEach((b, i) => { tag(b, qid); b.setAttribute("data-jh-opt", String(i)); });
    const label = contextLabel(p, optLabels);
    questions.push({ qid, kind: "buttons", label: strip(label), required: /[*✱]/.test(label), options: optLabels });
  }
  return questions;
})();
