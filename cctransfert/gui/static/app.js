"use strict";
// CCTransfert — interface locale. Aucune dépendance, aucun appel vers l'extérieur.
// Tout texte venant des sessions est inséré avec textContent (jamais en HTML).

// -- jeton d'accès (transmis dans le fragment #t=… de l'URL) -------------------------
// Fichier à ouvrir directement (CCTransfert gui fichier.CCTransfert, ou double-clic).
const OPEN_PATH = new URLSearchParams(location.hash.slice(1)).get("open");
// Identifiant de cette fenêtre : le serveur s'arrête quand la dernière fenêtre se ferme.
const PAGE_ID = Math.random().toString(36).slice(2) + Date.now().toString(36);

const TOKEN = (() => {
  const m = location.hash.match(/t=([\w-]+)/);
  if (m) {
    try { sessionStorage.setItem("cct-token", m[1]); } catch (e) { /* stockage indisponible */ }
    history.replaceState(null, "", location.pathname);
    return m[1];
  }
  try { return sessionStorage.getItem("cct-token"); } catch (e) { return null; }
})();

const state = {
  info: null,
  sessions: [],
  selected: null,
  details: {},
  project: null,
  search: "",
  forceRunning: false,
  bundle: null,
  imp: null,
  doctor: null,
};

const KIND = {
  transcript: ["transcript", "transcripts"], subagent: ["sous-agent", "sous-agents"],
  "session-dir": ["fichier de session", "fichiers de session"], "file-history": ["sauvegarde du rewind", "sauvegardes du rewind"],
  plan: ["plan", "plans"], task: ["tâche", "tâches"], todo: ["todo", "todos"],
  memory: ["fichier de mémoire", "fichiers de mémoire"], history: ["historique des prompts", "historique des prompts"],
};
const kindLabel = (k, n) => (KIND[k] ? KIND[k][n > 1 ? 1 : 0] : k);

// -- outils DOM -----------------------------------------------------------------
const $ = (s, root = document) => root.querySelector(s);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (["value", "checked", "disabled", "title", "type", "placeholder", "spellcheck"].includes(k)) el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

function fmtSize(n) {
  if (n == null) return "—";
  const u = ["o", "Ko", "Mo", "Go"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? String(n) : n.toFixed(1).replace(".", ",")) + " " + u[i];
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" });
}

function relTime(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "à l'instant";
  if (s < 3600) return `il y a ${Math.floor(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.floor(s / 3600)} h`;
  if (s < 172800) return "hier";
  if (s < 30 * 86400) return `il y a ${Math.floor(s / 86400)} j`;
  return new Date(iso).toLocaleDateString("fr-FR");
}

function basename(p) {
  if (!p) return "";
  const parts = p.split(/[\\/]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : p;
}

function tilde(p) {
  const home = state.info && state.info.home;
  if (p && home && (p === home || p.startsWith(home + state.info.sep))) return "~" + p.slice(home.length);
  return p || "";
}

function toast(msg, error = false) {
  const t = h("div", { class: "toast" + (error ? " error" : "") }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), error ? 7000 : 3200);
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copié dans le presse-papiers");
  } catch (e) {
    toast("Copie impossible : sélectionnez le texte à la main", true);
  }
}

function copyBtn(text, label = "copier") {
  return h("button", { class: "copy", type: "button", title: "Copier", onclick: () => copy(text) }, label);
}

async function busy(btn, fn) {
  const old = Array.from(btn.childNodes);
  btn.disabled = true;
  clear(btn).append(h("span", { class: "spinner" }), " ", old.map((n) => n.textContent).join(""));
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    clear(btn).append(...old);
  }
}

// -- API ---------------------------------------------------------------------------
async function api(path, { method = "GET", body, raw, headers = {} } = {}) {
  const opt = { method, headers: Object.assign({ "X-CCT-Token": TOKEN }, headers) };
  if (raw !== undefined) opt.body = raw;
  else if (body !== undefined) {
    opt.body = JSON.stringify(body);
    opt.headers["Content-Type"] = "application/json";
  }
  let r;
  try {
    r = await fetch(path, opt);
  } catch (e) {
    $("#offline").hidden = false;
    throw new Error("serveur injoignable");
  }
  let data = {};
  try { data = await r.json(); } catch (e) { /* réponse vide */ }
  if (!r.ok) {
    const err = new Error(data.error || r.statusText);
    err.code = data.code;
    err.status = r.status;
    err.data = data;
    throw err;
  }
  return data;
}

// -- fenêtres modales -------------------------------------------------------------
function confirmModal({ title, body, ok = "Confirmer", cancel = "Annuler", danger = false }) {
  const dlg = $("#confirm");
  $("#confirm-title").textContent = title;
  const b = clear($("#confirm-body"));
  b.append(...(Array.isArray(body) ? body : [body]).map((x) => (x instanceof Node ? x : h("p", {}, x))));
  const yes = $("#confirm-yes");
  yes.textContent = ok;
  yes.className = "btn " + (danger ? "danger" : "primary");
  $("#confirm-no").textContent = cancel;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "yes"), { once: true });
  });
}

const picker = { mode: "dir", path: "", resolve: null };

function pickPath({ mode = "dir", start = "", title } = {}) {
  picker.mode = mode;
  $("#picker-title").textContent = title || (mode === "dir" ? "Choisir un dossier" : "Choisir un fichier .CCTransfert");
  $("#picker-ok").hidden = mode !== "dir";
  $("#picker-hint").textContent = mode === "dir"
    ? "Vous pouvez aussi taper un chemin qui n'existe pas encore."
    : "Cliquez sur un fichier .CCTransfert.";
  const dlg = $("#picker");
  dlg.returnValue = "";
  dlg.showModal();
  browse(start);
  return new Promise((resolve) => {
    picker.resolve = resolve;
    dlg.addEventListener("close", () => {
      if (picker.resolve) { picker.resolve(null); picker.resolve = null; }
    }, { once: true });
  });
}

function pickerDone(value) {
  const r = picker.resolve;
  picker.resolve = null;
  $("#picker").close();
  if (r) r(value);
}

async function browse(path) {
  let data;
  try {
    data = await api("/api/fs?" + new URLSearchParams({ path: path || "", files: picker.mode === "file" ? "1" : "0" }));
  } catch (e) {
    toast(e.message, true);
    return;
  }
  picker.path = data.path;
  $("#picker-path").value = data.path;
  $("#picker-up").disabled = !data.parent;
  $("#picker-up").onclick = () => browse(data.parent);
  const sc = clear($("#picker-shortcuts"));
  for (const s of data.shortcuts) sc.append(h("button", { type: "button", onclick: () => browse(s.path), title: s.path }, s.label));
  const ul = clear($("#picker-entries"));
  if (!data.entries.length) ul.append(h("li", { class: "empty" }, picker.mode === "dir" ? "Aucun sous-dossier" : "Aucun dossier ni fichier .CCTransfert"));
  for (const e of data.entries) {
    if (e.dir) {
      ul.append(h("li", {}, h("button", { type: "button", ondblclick: () => browse(e.path), onclick: () => browse(e.path) },
        h("span", { class: "ico" }, "▸"), e.name)));
    } else {
      ul.append(h("li", {}, h("button", { type: "button", class: "file", onclick: () => pickerDone(e.path) },
        h("span", { class: "ico" }, "◆"), e.name, h("span", { class: "sz" }, fmtSize(e.size)))));
    }
  }
}

// -- onglets ------------------------------------------------------------------------
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  if (name === "history") loadJournals();
  if (name === "doctor" && !state.doctor) loadDoctor();
  if (name === "settings") loadSettings();
}

// ===================================================================================
// Exporter
// ===================================================================================
async function loadSessions() {
  try {
    const d = await api("/api/sessions");
    state.sessions = d.sessions;
  } catch (e) {
    clear($("#session-list")).append(h("div", { class: "empty" }, "Erreur : " + e.message));
    return;
  }
  if (state.project === null) {
    // Par défaut : le projet du dossier où l'interface a été lancée, s'il a des sessions.
    const cwd = state.info && state.info.cwd;
    state.project = cwd && state.sessions.some((s) => s.root === cwd) ? cwd : "";
  }
  renderProjectSelect();
  renderList();
  if (state.selected && !state.sessions.some((s) => s.id === state.selected)) {
    state.selected = null;
    renderEmptyDetail();
  }
}

function projects() {
  const map = new Map();
  for (const s of state.sessions) {
    const k = s.root || "?";
    const p = map.get(k) || { root: k, count: 0, last: s.last };
    p.count++;
    if (s.last > p.last) p.last = s.last;
    map.set(k, p);
  }
  return Array.from(map.values()).sort((a, b) => (a.last < b.last ? 1 : -1));
}

function renderProjectSelect() {
  const sel = clear($("#project"));
  sel.append(h("option", { value: "" }, `Tous les projets (${state.sessions.length})`));
  for (const p of projects()) {
    sel.append(h("option", { value: p.root, title: p.root }, `${basename(p.root) || p.root} — ${p.count}`));
  }
  sel.value = state.project || "";
}

function filtered() {
  const q = state.search.trim().toLowerCase();
  return state.sessions.filter((s) => {
    if (state.project && s.root !== state.project) return false;
    if (!q) return true;
    return [s.title, s.first_prompt, s.id, s.root].some((x) => x && x.toLowerCase().includes(q));
  });
}

function sessionRow(s) {
  const badges = [];
  if (s.running) badges.push(h("span", { class: "badge run", title: "Session en cours d'exécution (PID " + s.pid + ")" }, "en cours"));
  if (s.sdk) badges.push(h("span", { class: "badge", title: "Créée par claude -p ou le SDK : absente du picker claude -r" }, "-p"));
  const meta = [relTime(s.last), fmtSize(s.size), s.short];
  if (!state.project) meta.unshift(basename(s.root) || "?");
  return h("button", {
    class: "session-row" + (s.id === state.selected ? " selected" : ""),
    type: "button",
    dataset: { id: s.id },
    onclick: () => selectSession(s.id),
    title: s.first_prompt || s.title,
  },
  h("div", { class: "t" }, h("span", { class: "title" }, s.title), badges),
  h("div", { class: "m" }, meta.join(" · ")));
}

function renderList() {
  const list = clear($("#session-list"));
  const items = filtered();
  if (!items.length) {
    list.append(h("div", { class: "empty" }, state.sessions.length ? "Aucune session ne correspond." : "Aucune session sur cette machine."));
  } else if (!state.project && !state.search) {
    // Regroupement par projet, du plus récemment actif au plus ancien.
    for (const p of projects()) {
      const group = items.filter((s) => (s.root || "?") === p.root);
      if (!group.length) continue;
      list.append(h("div", { class: "group-head" }, h("span", {}, basename(p.root) || "?"), h("span", { class: "path" }, tilde(p.root))));
      group.forEach((s) => list.append(sessionRow(s)));
    }
  } else {
    items.forEach((s) => list.append(sessionRow(s)));
  }
  const foot = clear($("#list-foot"));
  foot.append(`${items.length} session${items.length > 1 ? "s" : ""}`, " · ",
    h("button", { class: "copy", type: "button", onclick: () => loadSessions().then(() => toast("Liste actualisée")) }, "actualiser"));
}

function renderEmptyDetail() {
  clear($("#detail")).append(h("div", { class: "empty big" },
    h("div", { class: "empty-icon" }, "⇢"),
    h("p", {}, "Choisissez une session à exporter."),
    h("p", { class: "muted" }, "Elle sera enregistrée dans un fichier .CCTransfert à copier sur l'autre machine.")));
}

async function selectSession(id) {
  state.selected = id;
  state.forceRunning = false;
  document.querySelectorAll(".session-row").forEach((r) => r.classList.toggle("selected", r.dataset.id === id));
  const s = state.sessions.find((x) => x.id === id);
  if (!s) return;
  renderDetail(s, state.details[id]);
  if (!state.details[id]) {
    try {
      state.details[id] = await api("/api/session?" + new URLSearchParams({ id }));
    } catch (e) {
      toast(e.message, true);
      return;
    }
    if (state.selected === id) renderDetail(s, state.details[id]);
  }
}

function kindChips(kinds) {
  if (!kinds) return h("span", { class: "muted" }, "analyse…");
  return h("div", { class: "chips" }, Object.entries(kinds).map(([k, v]) =>
    h("span", { class: "chip" }, h("b", {}, v.count), " ", kindLabel(k, v.count), " · ", fmtSize(v.size))));
}

function renderDetail(s, det) {
  const pane = clear($("#detail"));
  const head = h("div", { class: "detail-head" }, h("h2", {}, s.title),
    s.running ? h("span", { class: "badge run" }, "en cours") : null,
    s.sdk ? h("span", { class: "badge" }, "claude -p") : null);
  pane.append(head);
  if (s.has_title && s.first_prompt) pane.append(h("div", { class: "prompt-quote" }, s.first_prompt));

  const git = det && det.git;
  const branch = (git && git.branch) || (s.branch && s.branch !== "HEAD" ? s.branch : null);
  pane.append(h("dl", { class: "kv" },
    h("dt", {}, "ID"), h("dd", {}, h("span", { class: "mono" }, s.id), " ", copyBtn(s.id)),
    h("dt", {}, "Projet"), h("dd", {}, h("span", { class: "mono" }, s.root || "?")),
    h("dt", {}, "Dernière activité"), h("dd", { class: "text" }, fmtDate(s.last), " ", h("span", { class: "muted" }, "(" + relTime(s.last) + ")")),
    h("dt", {}, "Créée"), h("dd", { class: "text" }, fmtDate((det && det.created) || s.created)),
    h("dt", {}, "Messages"), h("dd", { class: "text" }, det ? `${det.messages} (dont ${det.user_prompts} prompts)` : "…"),
    branch ? [h("dt", {}, "Branche git"), h("dd", { class: "text" }, branch,
      git && git.head ? h("span", { class: "muted mono" }, " " + git.head.slice(0, 10)) : null,
      git && git.dirty ? h("span", { class: "badge run" }, " modifs non commitées") : null)] : null,
    h("dt", {}, "Contenu"), h("dd", { class: "text" }, kindChips(det && det.kinds)),
  ));

  if (s.running) {
    pane.append(h("div", { class: "note warn" },
      h("strong", {}, "Session en cours (PID " + s.pid + "). "),
      "Faites /exit dedans avant d'exporter, sinon la fin de la conversation manquera.",
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: state.forceRunning,
        onchange: (e) => { state.forceRunning = e.target.checked; } }), h("span", {}, "Exporter quand même"))));
  }
  if (s.sdk) {
    pane.append(h("div", { class: "note info" }, "Session créée par claude -p ou le SDK : Claude Code ne l'affiche pas dans le picker claude -r. Elle se reprend avec claude --resume <id>."));
  }

  const k = (det && det.kinds) || {};
  const fh = k["file-history"], mem = k.memory;
  const hist = det ? det.history : null;
  const opts = {
    fh: h("input", { type: "checkbox", checked: !det || !!fh, disabled: det ? !fh : false }),
    mem: h("input", { type: "checkbox", checked: false, disabled: det ? !mem : false }),
    hist: h("input", { type: "checkbox", checked: false, disabled: det ? !hist : false }),
  };
  pane.append(h("fieldset", {}, h("legend", {}, "Inclure"),
    h("label", { class: "check" }, opts.fh, h("span", {}, h("span", { class: "lbl" }, "Sauvegardes du rewind"),
      h("span", { class: "hint" }, det ? (fh ? `${fh.count} fichier(s), ${fmtSize(fh.size)} — pour pouvoir revenir en arrière sur l'autre machine` : "aucune pour cette session") : "…"))),
    h("label", { class: "check" }, opts.mem, h("span", {}, h("span", { class: "lbl" }, "Mémoire du projet"),
      h("span", { class: "hint" }, det ? (mem ? `${mem.count} fichier(s) — fusionnés sans rien écraser à l'import` : "pas de mémoire pour ce projet") : "…"))),
    h("label", { class: "check" }, opts.hist, h("span", {}, h("span", { class: "lbl" }, "Historique des prompts"),
      h("span", { class: "hint" }, det ? (hist ? `${hist} entrée(s) — retrouvables avec la flèche ↑` : "aucune entrée pour cette session") : "…")))));

  const dest = h("input", { type: "text", class: "path", value: (state.info && state.info.export_dir) || "", spellcheck: false });
  pane.append(h("div", {}, h("div", { class: "lbl" }, h("strong", {}, "Enregistrer dans")),
    h("div", { class: "dest" }, dest, h("button", { class: "btn", type: "button", onclick: async () => {
      const p = await pickPath({ mode: "dir", start: dest.value, title: "Dossier où enregistrer le fichier" });
      if (p) dest.value = p;
    } }, "Parcourir…"))));

  const result = h("div", {});
  const exportBtn = h("button", { class: "btn primary big", type: "button" }, "Exporter");
  exportBtn.onclick = () => busy(exportBtn, async () => {
    if (s.running && !state.forceRunning) {
      toast("Session en cours : faites /exit, ou cochez « Exporter quand même ».", true);
      return;
    }
    try {
      const r = await api("/api/export", { method: "POST", body: {
        id: s.id, out_dir: dest.value, file_history: opts.fh.checked, with_memory: opts.mem.checked,
        with_history: opts.hist.checked, force_running: state.forceRunning,
      } });
      state.info.export_dir = dest.value;
      clear(result).append(h("div", { class: "note ok" },
        h("strong", {}, "Fichier créé : "), h("span", { class: "mono" }, r.name), " (", fmtSize(r.size), ")",
        h("div", { class: "mono small mt4 break" }, r.path),
        h("div", { class: "row mt8" },
          h("button", { class: "btn small", type: "button", onclick: () => api("/api/open", { method: "POST", body: { path: r.path } }).catch((e) => toast(e.message, true)) }, "Ouvrir le dossier"),
          h("button", { class: "btn small", type: "button", onclick: () => copy(r.path) }, "Copier le chemin")),
        h("p", { class: "small mt10 mb0" }, "Copiez ce fichier sur l'autre machine, puis ouvrez-y CCTransfert → onglet Importer.")));
    } catch (e) {
      clear(result).append(h("div", { class: "note danger" }, e.message));
    }
  });
  const dupBtn = h("button", { class: "btn", type: "button", title: "Créer une copie indépendante de cette session sur cette machine" }, "Dupliquer…");
  dupBtn.onclick = () => duplicateDialog(s);
  const moveBtn = h("button", { class: "btn ghost", type: "button", title: "Rattacher cette session à un autre dossier de cette machine" }, "Déplacer vers un autre dossier…");
  moveBtn.onclick = () => relocate(s, moveBtn);
  pane.append(h("div", { class: "actions" }, exportBtn, dupBtn, moveBtn));
  pane.append(h("p", { class: "muted small mt10" },
    "Le transcript contient tout ce qui est passé dans la session (sorties de commandes, fichiers lus…), y compris d'éventuels secrets : transférez-le par un canal de confiance."));
  pane.append(result);
}

function defaultCopyTitle(t) {
  return (t.length > 60 ? t.slice(0, 59) + "…" : t) + " (copie)";
}

function duplicateDialog(s) {
  const dlg = $("#dup");
  $("#dup-source").textContent = `Copie de « ${s.title} ». L'original n'est pas modifié ; la copie reçoit un nouvel ID et garde l'historique et le rewind.`;
  $("#dup-title").value = defaultCopyTitle(s.title);
  $("#dup-same").textContent = s.root || "?";
  const target = $("#dup-target"), browseBtn = $("#dup-browse"), note = clear($("#dup-note"));
  target.value = s.root ? s.root + "-copie" : "";
  const radios = dlg.querySelectorAll("input[name=dup-where]");
  radios[0].checked = true;
  const sync = () => {
    const other = radios[1].checked;
    target.disabled = browseBtn.disabled = !other;
    if (other) target.focus();
  };
  radios.forEach((r) => { r.onchange = sync; });
  sync();
  browseBtn.onclick = async () => {
    const p = await pickPath({ mode: "dir", start: target.value || s.root, title: "Dossier du projet pour la copie" });
    if (p) target.value = p;
  };
  if (s.running) note.append(h("div", { class: "note warn" }, "Session en cours : la copie s'arrêtera au dernier message enregistré."));
  const ok = $("#dup-ok");
  ok.onclick = () => busy(ok, async () => {
    const title = $("#dup-title").value.trim();
    if (!title) { clear(note).append(h("div", { class: "note danger" }, "Donnez un intitulé à la copie.")); return; }
    const body = { id: s.id, title, target: radios[1].checked ? target.value.trim() : "" };
    if (radios[1].checked && !body.target) { clear(note).append(h("div", { class: "note danger" }, "Indiquez le dossier de la copie.")); return; }
    try {
      const r = await api("/api/duplicate", { method: "POST", body });
      dlg.close();
      toast(`Copie créée : « ${title} »`);
      state.details = {};
      await loadSessions();
      state.project = r.target_root;
      renderProjectSelect();
      renderList();
      selectSession(r.new_id);
    } catch (e) {
      clear(note).append(h("div", { class: "note danger" }, e.message));
    }
  });
  dlg.showModal();
  $("#dup-title").select();
}

async function relocate(s, btn) {
  if (s.running) { toast("Session en cours : quittez-la (/exit) avant de la déplacer.", true); return; }
  const target = await pickPath({ mode: "dir", start: s.root, title: "Nouveau dossier du projet pour cette session" });
  if (!target) return;
  await busy(btn, async () => {
    let pre;
    try {
      pre = await api("/api/relocate", { method: "POST", body: { id: s.id, target, dry_run: true } });
    } catch (e) { toast(e.message, true); return; }
    const counts = pre.report.counts.map(([k, v]) => `${k} ${v}`).join(", ") || "aucun";
    const ok = await confirmModal({
      title: "Déplacer la session ?",
      body: [
        h("p", {}, "« ", s.title, " »"),
        h("dl", { class: "kv" }, h("dt", {}, "De"), h("dd", { class: "mono" }, s.root),
          h("dt", {}, "Vers"), h("dd", { class: "mono" }, pre.target_root),
          h("dt", {}, "Remplacements"), h("dd", { class: "text" }, counts)),
        h("p", { class: "muted small" }, "L'ancienne copie est sauvegardée puis retirée (un seul exemplaire de l'ID sur la machine). Annulable dans l'onglet Historique."),
      ],
      ok: "Déplacer",
    });
    if (!ok) return;
    try {
      const r = await api("/api/relocate", { method: "POST", body: { id: s.id, target } });
      toast("Session déplacée vers " + r.target_root);
      state.details = {};
      await loadSessions();
      state.project = r.target_root;
      renderProjectSelect();
      renderList();
      selectSession(s.id);
    } catch (e) { toast(e.message, true); }
  });
}

// ===================================================================================
// Importer
// ===================================================================================
function initImport() {
  const dz = $("#dropzone");
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "dragend"].forEach((ev) => dz.addEventListener(ev, () => dz.classList.remove("over")));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("over");
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) uploadFile(f);
  });
  // Déposer un fichier n'importe où dans la fenêtre ouvre l'onglet Importer.
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { showTab("import"); uploadFile(f); }
  });
  $("#pick-upload").onclick = () => $("#file-input").click();
  $("#file-input").onchange = (e) => { const f = e.target.files[0]; if (f) uploadFile(f); e.target.value = ""; };
  $("#pick-disk").onclick = async () => {
    const p = await pickPath({ mode: "file", start: (state.info && state.info.export_dir) || "" });
    if (p) openBundle(p);
  };
}

function setLoading(msg) {
  clear($("#import-flow")).append(h("div", { class: "panel card" }, h("div", { class: "row" }, h("span", { class: "spinner" }), msg)));
}

async function uploadFile(f) {
  setLoading(`Lecture de ${f.name} (${fmtSize(f.size)})…`);
  try {
    const b = await api("/api/upload", { method: "POST", raw: f, headers: { "X-Filename": encodeURIComponent(f.name) } });
    setBundle(b);
  } catch (e) {
    clear($("#import-flow")).append(h("div", { class: "note danger" }, "Fichier refusé : " + e.message));
  }
}

async function openBundle(path) {
  setLoading("Vérification de " + basename(path) + "…");
  try {
    setBundle(await api("/api/bundle", { method: "POST", body: { path } }));
  } catch (e) {
    clear($("#import-flow")).append(h("div", { class: "note danger" }, "Fichier refusé : " + e.message));
  }
}

function setBundle(b) {
  state.bundle = b;
  state.imp = {
    target: (b.suggestions[0] && b.suggestions[0].path) || "",
    new_id: false, replace: false, keep_mtime: false, maps: [],
  };
  $("#dropzone").classList.add("compact");
  $(".dz-title").textContent = "Déposez un autre fichier .CCTransfert pour le remplacer";
  renderImport();
}

function renderImport() {
  const b = state.bundle, m = b.manifest, s = m.session || {}, src = m.source || {}, imp = state.imp;
  const flow = clear($("#import-flow"));

  // 1. Fichier
  const g = s.git || {};
  flow.append(h("div", { class: "panel card" },
    h("div", { class: "card-head" }, h("span", { class: "step" }, "1"), h("h3", {}, s.title || s.first_prompt || "Session " + (s.id || "").slice(0, 8)),
      b.verified ? h("span", { class: "badge ok", title: "Tous les sha256 correspondent" }, "intègre ✓") : h("span", { class: "badge danger" }, "non vérifié")),
    s.title && s.first_prompt ? h("div", { class: "prompt-quote" }, s.first_prompt) : null,
    h("dl", { class: "kv" },
      h("dt", {}, "Fichier"), h("dd", {}, h("span", { class: "mono" }, b.name), h("span", { class: "muted" }, " · " + fmtSize(b.size))),
      h("dt", {}, "Machine source"), h("dd", { class: "text" }, `${src.user}@${src.hostname} (${src.os}${src.wsl ? ", WSL" : ""})`,
        src.claude_version || s.claude_version ? h("span", { class: "muted" }, " · Claude Code " + (src.claude_version || s.claude_version)) : null),
      h("dt", {}, "Projet source"), h("dd", {}, h("span", { class: "mono" }, src.project_root || "?")),
      h("dt", {}, "Activité"), h("dd", { class: "text" }, `${fmtDate(s.created)} → ${fmtDate(s.last_activity)}`, h("span", { class: "muted" }, ` · ${s.messages} messages`)),
      (g.branch || (s.git_branch && s.git_branch !== "HEAD")) ? [h("dt", {}, "Git"), h("dd", { class: "text" }, "branche ", g.branch || s.git_branch,
        g.head ? h("span", { class: "mono muted" }, " " + g.head.slice(0, 10)) : null,
        g.remote ? h("span", { class: "muted" }, " · " + g.remote) : null)] : null,
      h("dt", {}, "Contenu"), h("dd", { class: "text" }, kindChips(b.kinds)),
    ),
    s.entrypoint && s.entrypoint.startsWith("sdk") ? h("div", { class: "note info" }, "Session créée par claude -p : elle n'apparaîtra pas dans le picker claude -r, reprenez-la par son ID.") : null,
  ));

  // 2. Destination
  const status = h("div", { class: "target-status muted" });
  const input = h("input", { type: "text", class: "path grow", value: imp.target, spellcheck: false,
    placeholder: src.style === "windows" || (state.info && state.info.style === "windows") ? "C:\\Users\\…\\Projets\\" + basename(src.project_root) : "/home/…/Projets/" + basename(src.project_root) });
  let t = null;
  const check = async () => {
    imp.target = input.value.trim();
    if (!imp.target) { status.textContent = "Indiquez le dossier où se trouve (ou se trouvera) le code du projet."; return; }
    try {
      const st = await api("/api/stat?" + new URLSearchParams({ path: imp.target }));
      clear(status);
      if (st.is_dir) status.append(h("span", { class: "badge ok" }, "dossier existant"));
      else if (st.exists) status.append(h("span", { class: "badge danger" }, "ce n'est pas un dossier"));
      else status.append(h("span", { class: "badge run" }, "n'existe pas"), " il sera créé à l'import");
    } catch (e) { /* ignoré */ }
  };
  input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(check, 250); invalidatePreview(); });
  const sugg = h("div", { class: "suggestions" }, b.suggestions.map((sg) =>
    h("button", { class: "suggestion", type: "button", title: sg.path, onclick: () => { input.value = sg.path; check(); invalidatePreview(); } },
      h("span", { class: "lbl" }, sg.label), h("span", { class: "p" }, tilde(sg.path)))));
  flow.append(h("div", { class: "panel card" },
    h("div", { class: "card-head" }, h("span", { class: "step" }, "2"), h("h3", {}, "Dossier du projet sur cette machine")),
    h("div", { class: "row" }, input, h("button", { class: "btn", type: "button", onclick: async () => {
      const p = await pickPath({ mode: "dir", start: input.value || (state.info && state.info.home), title: "Dossier du projet (là où est le code)" });
      if (p) { input.value = p; check(); invalidatePreview(); }
    } }, "Parcourir…")),
    b.suggestions.length ? sugg : null,
    status,
    h("p", { class: "muted small mt8 mb0" }, "Le code n'est pas dans le fichier : synchronisez-le à part (git) dans ce dossier.")));
  check();

  // 3. Options
  const opt = (key, label, hint) => h("label", { class: "check" },
    h("input", { type: "checkbox", checked: imp[key], onchange: (e) => { imp[key] = e.target.checked; invalidatePreview(); } }),
    h("span", {}, h("span", { class: "lbl" }, label), h("span", { class: "hint" }, hint)));
  const mapsBox = h("div", {});
  const renderMaps = () => {
    clear(mapsBox);
    imp.maps.forEach((pair, i) => {
      const a = h("input", { type: "text", class: "path", value: pair[0], placeholder: "chemin sur la machine source", oninput: (e) => { pair[0] = e.target.value; invalidatePreview(); } });
      const c = h("input", { type: "text", class: "path", value: pair[1], placeholder: "chemin sur cette machine", oninput: (e) => { pair[1] = e.target.value; invalidatePreview(); } });
      mapsBox.append(h("div", { class: "maps-row" }, a, h("span", { class: "muted" }, "→"), c,
        h("button", { class: "icon-btn", type: "button", title: "Retirer", onclick: () => { imp.maps.splice(i, 1); renderMaps(); invalidatePreview(); } }, "✕")));
    });
    mapsBox.append(h("button", { class: "btn small", type: "button", onclick: () => { imp.maps.push(["", ""]); renderMaps(); } }, "+ Ajouter une correspondance"));
  };
  renderMaps();
  flow.append(h("div", { class: "panel card" },
    h("div", { class: "card-head" }, h("span", { class: "step" }, "3"), h("h3", {}, "Options")),
    opt("new_id", "Copie indépendante (nouvel ID)", "À cocher pour importer sur une machine où la session existe déjà, sans toucher à l'original."),
    opt("replace", "Remplacer si elle existe déjà", "Aller-retour A → B → A : l'ancienne version est sauvegardée (annulable)."),
    opt("keep_mtime", "Garder les dates d'origine", "Par défaut la date du jour, pour échapper au nettoyage automatique des sessions de plus de 30 jours."),
    h("label", { class: "field" }, h("span", { class: "lbl" }, "Nouvel intitulé (facultatif)"),
      h("input", { type: "text", value: imp.title || "", placeholder: s.title || "garder l'intitulé d'origine",
        oninput: (e) => { imp.title = e.target.value; invalidatePreview(); } })),
    h("details", { open: imp.maps.length > 0 }, h("summary", {}, "Correspondances de chemins supplémentaires"),
      h("p", { class: "muted small" }, "Pour un autre dépôt cité dans la session, par exemple ~/Projets/MonProjet2 → ~/Travail/MonProjet2."), mapsBox)));

  // 4. Aperçu / import
  const previewBox = h("div", { id: "preview" });
  const prevBtn = h("button", { class: "btn big", type: "button" }, "Aperçu");
  const impBtn = h("button", { class: "btn primary big", type: "button" }, "Importer");
  prevBtn.onclick = () => busy(prevBtn, () => runImport(true, previewBox));
  impBtn.onclick = () => busy(impBtn, () => runImport(false, previewBox));
  flow.append(h("div", { class: "actions" }, prevBtn, impBtn, h("span", { class: "muted small" }, "L'aperçu montre tout ce qui serait écrit, sans rien toucher.")));
  flow.append(previewBox);
}

function invalidatePreview() {
  if (state.imp) delete state.imp.preview_sid;
  const p = $("#preview");
  if (p && p.firstChild && !p.dataset.result) clear(p);
}

function importBody() {
  const imp = state.imp;
  return { path: state.bundle.path, target: imp.target, new_id: imp.new_id, replace: imp.replace,
    keep_mtime: imp.keep_mtime, maps: imp.maps.filter((p) => p[0].trim() && p[1].trim()),
    title: (imp.title || "").trim() || undefined,
    new_sid: imp.new_id ? imp.preview_sid : undefined };
}

async function runImport(dry, box) {
  if (!state.imp.target) { toast("Indiquez le dossier du projet sur cette machine.", true); return; }
  delete box.dataset.result;
  try {
    const r = await api(dry ? "/api/preview" : "/api/import", { method: "POST", body: importBody() });
    if (dry) renderPreview(r, box);
    else renderResult(r, box);
  } catch (e) {
    renderImportError(e, box, dry);
  }
  // Le résultat apparaît sous le formulaire : on l'amène à l'écran.
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderImportError(e, box, dry) {
  const actions = [];
  const redo = (patch) => { Object.assign(state.imp, patch); renderImport(); runImport(dry, $("#preview")); };
  if (e.code === "conflict_same") {
    actions.push(h("button", { class: "btn primary small", type: "button", onclick: () => redo({ replace: true }) }, "Remplacer (avec sauvegarde)"));
    actions.push(h("button", { class: "btn small", type: "button", onclick: () => redo({ new_id: true }) }, "Importer une copie (nouvel ID)"));
  } else if (e.code === "conflict_other") {
    actions.push(h("button", { class: "btn primary small", type: "button", onclick: () => redo({ replace: true }) }, "Déplacer ici (avec sauvegarde)"));
    actions.push(h("button", { class: "btn small", type: "button", onclick: () => redo({ new_id: true }) }, "Importer une copie (nouvel ID)"));
  }
  const titles = {
    conflict_same: "Cette session existe déjà dans ce dossier.",
    conflict_other: "Cette session existe déjà ailleurs sur cette machine.",
    running: "Session en cours.",
  };
  const texts = {
    conflict_same: "C'est le cas d'un aller-retour : remplacez-la (l'ancienne version est sauvegardée), ou importez une copie à part.",
    conflict_other: "Deux copies du même ID rendraient « claude --resume » inutilisable : déplacez-la ici (l'ancienne est sauvegardée puis retirée), ou importez une copie indépendante.",
  };
  clear(box).append(h("div", { class: "note " + (e.code && e.code.startsWith("conflict") ? "warn" : "danger") },
    h("strong", {}, titles[e.code] || "Import impossible."), " ", texts[e.code] || e.message,
    actions.length ? h("div", { class: "row mt10" }, actions) : null));
}

function reportBlocks(r) {
  const rep = r.report;
  const out = [];
  out.push(h("dl", { class: "kv" },
    h("dt", {}, "Dossier projet"), h("dd", {}, h("span", { class: "mono" }, r.target_root),
      r.target_exists === false ? h("span", { class: "badge run" }, " sera créé") : null),
    h("dt", {}, "Dossier Claude"), h("dd", {}, h("span", { class: "mono" }, "…/projects/" + r.slug), h("span", { class: "muted" }, " (" + r.slug_reason + ")")),
    h("dt", {}, "ID de session"), h("dd", {}, h("span", { class: "mono" }, r.new_id), r.new_id !== r.old_id ? h("span", { class: "badge accent" }, " nouvel ID") : null),
    h("dt", {}, "Lignes"), h("dd", { class: "text" }, `${rep.lines_total} lues, ${rep.lines_changed} réécrites`, rep.lines_bad ? `, ${rep.lines_bad} illisible(s) recopiée(s) telles quelles` : ""),
  ));
  if (rep.counts.length) {
    out.push(h("h4", {}, "Remplacements"));
    out.push(h("div", { class: "chips" }, rep.counts.map(([k, v]) => h("span", { class: "chip" }, k, " ", h("b", {}, v)))));
  }
  if (r.rules.length) {
    out.push(h("h4", {}, "Correspondances"));
    out.push(h("div", { class: "rules" }, r.rules.map((x) => h("div", {}, h("span", { class: "n" }, x.name), x.src, "  →  ", x.dst))));
  }
  if (rep.examples.length) {
    out.push(h("h4", {}, "Exemples avant / après"));
    for (const ex of rep.examples) {
      for (const [b, a] of ex.pairs.slice(0, 2)) {
        out.push(h("div", { class: "ex" }, h("div", { class: "rule" }, ex.rule), h("div", { class: "b" }, b), h("div", { class: "a" }, a)));
      }
    }
  }
  if (rep.unmapped.length) {
    out.push(h("details", {}, h("summary", {}, `Chemins absolus restés tels quels (${rep.unmapped.length}, pour information)`),
      h("ul", { class: "list" }, rep.unmapped.map(([p, n]) => h("li", {}, h("span", { class: "op" }, n + " ×"), h("span", { class: "p" }, p)))),
      h("p", { class: "muted small" }, "Si l'un d'eux doit changer, ajoutez une correspondance dans les options.")));
  }
  for (const w of rep.warnings.slice(0, 6).concat(r.warnings)) out.push(h("div", { class: "note warn" }, w));
  return out;
}

function renderPreview(r, box) {
  // L'import réel réutilisera le même nouvel ID que celui affiché ici.
  if (r.new_id !== r.old_id) state.imp.preview_sid = r.new_id;
  const files = r.actions;
  clear(box).append(h("div", { class: "panel card preview" },
    h("div", { class: "card-head" }, h("span", { class: "step" }, "✓"), h("h3", {}, "Aperçu"), h("span", { class: "badge" }, "rien n'a été écrit")),
    reportBlocks(r),
    h("h4", {}, `Fichiers (${files.length})`),
    h(files.length > 8 ? "details" : "div", {}, files.length > 8 ? h("summary", {}, "Afficher la liste") : null,
      h("ul", { class: "list" }, files.map((a) => h("li", {}, h("span", { class: "op" }, a.op), h("span", { class: "p" }, a.path))))),
    h("div", { class: "actions" }, h("span", { class: "muted small" }, "Tout est correct ? Cliquez sur Importer."))));
}

function renderResult(r, box) {
  box.dataset.result = "1";
  const cmds = [r.commands.resume, r.commands.by_id];
  clear(box).append(h("div", { class: "panel card" },
    h("div", { class: "success" },
      h("div", { class: "big-ok" }, "✓"),
      h("h2", {}, "Session importée"),
      h("p", { class: "muted" }, "dans ", h("span", { class: "mono" }, r.target_root)),
      cmds.map((c) => h("div", { class: "cmd" }, h("code", {}, c), copyBtn(c))),
      r.sdk ? h("div", { class: "note info" }, "Session créée par claude -p : elle n'apparaît pas dans le picker, utilisez la commande --resume.") : null,
      r.git_warnings.map((w) => h("div", { class: "note warn" }, w)),
      h("ul", { class: "reminders muted" },
        h("li", {}, "Le code n'est pas transféré : synchronisez-le à part (git)."),
        h("li", {}, "Au premier lancement, Claude Code demandera de faire confiance au dossier."),
        h("li", {}, "Les serveurs MCP, settings et identifiants ne sont pas transférés.")),
      h("div", { class: "row center mt14" },
        h("button", { class: "btn", type: "button", onclick: () => api("/api/open", { method: "POST", body: { path: r.target_root } }).catch((e) => toast(e.message, true)) }, "Ouvrir le dossier"),
        h("button", { class: "btn danger", type: "button", onclick: () => undoLast() }, "Annuler cet import"))),
    h("details", {}, h("summary", {}, "Détails de la réécriture"), reportBlocks(r))));
  state.details = {};
  loadSessions();
}

// ===================================================================================
// Historique
// ===================================================================================
async function loadJournals() {
  const box = clear($("#journals"));
  let d;
  try { d = await api("/api/journals"); } catch (e) { box.append(h("div", { class: "empty" }, e.message)); return; }
  if (!d.journals.length) {
    box.append(h("div", { class: "empty" }, "Aucun import pour l'instant."));
    $("#undo-last").disabled = true;
    return;
  }
  const lastActive = d.journals.find((j) => !j.undone);
  $("#undo-last").disabled = !lastActive;
  box.append(h("table", {},
    h("thead", {}, h("tr", {}, ["Date", "Session", "Dossier du projet", "Contenu", "État"].map((x) => h("th", {}, x)))),
    h("tbody", {}, d.journals.map((j) => {
      const ids = j.session_ids;
      const st = j.aborted ? h("span", { class: "badge danger" }, "interrompu") : j.undone
        ? h("span", { class: "badge" }, "annulé") : h("span", { class: "badge ok" }, j === lastActive ? "actif · annulable" : "actif");
      return h("tr", {},
        h("td", {}, fmtDate(j.created_at)),
        h("td", {}, h("span", { class: "mono nowrap" }, ids.length > 1 ? `${ids[0].slice(0, 8)} → ${ids[1].slice(0, 8)}` : ids[0].slice(0, 8))),
        h("td", {}, h("span", { class: "mono" }, tilde(j.target_root))),
        h("td", {}, `${j.created} fichier(s)`, j.backups ? `, ${j.backups} sauvegarde(s)` : "", j.history ? `, ${j.history} ligne(s) d'historique` : ""),
        h("td", {}, st));
    }))));
}

async function undoLast(force = false) {
  if (!force) {
    const ok = await confirmModal({
      title: "Annuler le dernier import ?",
      body: ["Les fichiers créés par l'import seront supprimés et ceux qu'il avait remplacés seront restaurés depuis la sauvegarde."],
      ok: "Annuler l'import", cancel: "Garder", danger: true,
    });
    if (!ok) return;
  }
  try {
    const r = await api("/api/undo", { method: "POST", body: { force } });
    toast(r.errors.length ? "Annulation terminée avec des erreurs" : "Import annulé", r.errors.length > 0);
    state.details = {};
    loadJournals();
    loadSessions();
    const p = $("#preview");
    if (p && p.dataset.result) clear(p).append(h("div", { class: "note info" }, "Cet import a été annulé."));
  } catch (e) {
    if (e.code === "modified") {
      const ok = await confirmModal({
        title: "La session a été modifiée depuis l'import",
        body: [e.message, h("ul", { class: "list" }, (e.data.files || []).slice(0, 5).map((f) => h("li", {}, h("span", { class: "p" }, f))))],
        ok: "Annuler quand même", cancel: "Garder", danger: true,
      });
      if (ok) undoLast(true);
    } else toast(e.message, true);
  }
}

// ===================================================================================
// Diagnostic
// ===================================================================================
async function loadDoctor() {
  const box = clear($("#doctor"));
  box.append(h("div", { class: "empty" }, h("span", { class: "spinner" }), " Analyse de la machine…"));
  let d;
  try { d = await api("/api/doctor"); } catch (e) { clear(box).append(h("div", { class: "note danger" }, e.message)); return; }
  state.doctor = d;
  const kv = (pairs) => h("dl", { class: "kv" }, pairs.filter(Boolean).map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
  const mono = (s) => h("span", { class: "mono" }, s == null ? "—" : String(s));
  const c = d.lettre_lecteur || {};
  clear(box).append(
    h("div", { class: "section-head" }, h("div", {}, h("h2", {}, "Diagnostic de la machine"),
      h("p", { class: "muted" }, "À lancer sur chaque machine. Sous Windows, envoyez le JSON : il sert à valider le format.")),
    h("button", { class: "btn", type: "button", onclick: () => copy(JSON.stringify(d, null, 2)) }, "Copier le diagnostic (JSON)"),
    h("button", { class: "btn ghost", type: "button", onclick: () => { state.doctor = null; loadDoctor(); } }, "Actualiser")),
    d.conseils.map((x) => h("div", { class: "note warn" }, x)),
    h("div", { class: "doc-grid" },
      h("div", { class: "panel" }, h("h3", {}, "Machine"), kv([
        ["Système", d.os + (d.wsl ? " (WSL)" : "")], ["Plateforme", d.plateforme], ["Hôte", d.hote], ["Utilisateur", d.utilisateur],
        ["Home", mono(d.home)], ["Python", d.python + " · " + d.python_exe]])),
      h("div", { class: "panel" }, h("h3", {}, "Claude Code"), kv([
        ["Version", h("span", {}, d.claude_version || "introuvable",
          d.claude_version_source === "sessions" ? h("span", { class: "muted" }, " (vue dans les sessions récentes)") : null)],
        ["Exécutable", h("span", {}, mono(d.claude_exe),
          d.claude_exe && d.claude_exe_source !== "PATH" ? h("span", { class: "badge" }, " hors du PATH") : null)],
        ["Config", h("span", {}, mono(d.config_dir), d.config_existe ? "" : h("span", { class: "badge danger" }, " introuvable"))],
        ["Temp", mono(d.temp_root)], ["Dossiers temp", (d.temp_dossiers_claude || []).join(", ") || "aucun"],
        ["Nettoyage", (d.cleanup_period_days != null ? d.cleanup_period_days : 30) + " jours"]])),
      h("div", { class: "panel" }, h("h3", {}, "Projets et slugs"), kv([
        ["Dossiers", d.projets], ["Sessions", `${d.sessions} (dont ${d.sessions_en_cours} en cours)`],
        ["Slugs", h("span", {}, h("span", { class: "badge ok" }, d.slugs_conformes + " conformes"), " ",
          d.slugs_differents.length ? h("span", { class: "badge danger" }, d.slugs_differents.length + " différents") : null)],
        (c.upper || c.lower) ? ["Lettre lecteur", `${c.upper} en C--, ${c.lower} en c--`] : null]),
      d.slugs_differents.length ? h("ul", { class: "list" }, d.slugs_differents.map((x) => h("li", {}, h("span", { class: "p" }, x.dossier + " ≠ " + x.calcule)))) : null),
      h("div", { class: "panel" }, h("h3", {}, "Dossier de lancement"), kv([
        ["Dossier", mono(d.cwd_realpath)], ["Slug calculé", mono(d.slug_calcule)],
        ["Dossier projet", d.slug_existe ? "existe" : "pas encore créé"]])),
    ));
}

// ===================================================================================
// Réglages : raccourcis et mises à jour
// ===================================================================================
async function loadSettings() {
  const box = $("#settings");
  let sc, upd;
  try {
    [sc, upd] = await Promise.all([api("/api/shortcuts"), api("/api/update")]);
  } catch (e) { clear(box).append(h("div", { class: "note danger" }, e.message)); return; }
  clear(box).append(h("h2", {}, "Réglages"), h("p", { class: "muted" }, "Raccourcis pour lancer CCTransfert, et mises à jour."));

  // Raccourcis
  const card = h("div", { class: "panel settings-card" }, h("h3", {}, "Raccourcis"));
  if (!sc.supported) {
    card.append(h("p", { class: "muted" }, "Non pris en charge sur ce système : lancez « CCTransfert gui »."));
  } else {
    const toggle = (key, label, hint, disabled) => h("label", { class: "check" },
      h("input", { type: "checkbox", checked: sc[key], disabled, onchange: async (e) => {
        e.target.disabled = true;
        try {
          const r = await api("/api/shortcuts", { method: "POST", body: { [key]: e.target.checked } });
          toast(e.target.checked ? "Raccourci ajouté" : "Raccourci retiré");
          Object.assign(sc, r);
        } catch (err) { toast(err.message, true); e.target.checked = !e.target.checked; }
        e.target.disabled = false;
      } }),
      h("span", {}, h("span", { class: "lbl" }, label), h("span", { class: "hint" }, hint)));
    card.append(
      toggle("desktop", "Icône sur le bureau", sc.desktop_available ? "Double-clic pour ouvrir CCTransfert." : "Dossier du bureau introuvable.", !sc.desktop_available),
      toggle("menu", "Raccourci dans le " + sc.menu_label, "Retrouvez CCTransfert en tapant son nom dans le " + sc.menu_label + ".", false));
    if (sc.frozen) card.append(h("p", { class: "muted small" }, "CCTransfert est copié dans ", h("span", { class: "mono" }, sc.install_dir), " pour que les raccourcis restent valides même si le fichier téléchargé est supprimé."));
  }
  box.append(card);

  // Mises à jour
  const status = h("div", {});
  const showStatus = (r) => {
    clear(status);
    if (r.error) status.append(h("div", { class: "note warn" }, r.error));
    else if (r.available) status.append(updateNotice(r, true));
    else if (r.latest) status.append(h("div", { class: "note ok" }, "✓ CCTransfert est à jour (dernière version : " + r.latest.version + ")."));
  };
  const checkBtn = h("button", { class: "btn", type: "button" }, "Vérifier maintenant");
  checkBtn.onclick = () => busy(checkBtn, async () => {
    try { showStatus(await api("/api/update?force=1")); } catch (e) { toast(e.message, true); }
  });
  box.append(h("div", { class: "panel settings-card" }, h("h3", {}, "Mises à jour"),
    h("p", {}, "Version installée : ", h("strong", {}, upd.current), h("span", { class: "muted" }, " · " + upd.kind_label)),
    h("label", { class: "check" },
      h("input", { type: "checkbox", checked: upd.auto_check, onchange: (e) =>
        api("/api/update/settings", { method: "POST", body: { auto_check: e.target.checked } }).catch((err) => toast(err.message, true)) }),
      h("span", {}, h("span", { class: "lbl" }, "Vérifier automatiquement au démarrage"),
        h("span", { class: "hint" }, "Au plus une fois par jour, auprès de GitHub ; rien d'autre n'est envoyé."))),
    h("div", { class: "actions" }, checkBtn), status));
  if (upd.latest) showStatus(upd);
}

function notesBlock(latest) {
  return latest.notes ? h("div", { class: "notes" }, latest.notes) : null;
}

function updateNotice(r, withNotes) {
  const v = r.latest.version;
  const box = h("div", { class: "note info" }, h("strong", {}, "Version " + v + " disponible"), " (vous avez " + r.current + ").");
  if (r.can_auto) {
    box.append(h("div", { class: "row mt8" }, h("button", { class: "btn primary small", type: "button", onclick: () => startUpdate(r) }, "Mettre à jour")));
  } else if (r.instructions) {
    box.append(h("div", { class: "cmd" }, h("code", {}, r.instructions), copyBtn(r.instructions)));
  }
  if (withNotes && r.latest.notes) box.append(h("details", {}, h("summary", {}, "Nouveautés"), notesBlock(r.latest)));
  return box;
}

async function autoCheckUpdate() {
  let r;
  try { r = await api("/api/update"); } catch (e) { return; }
  if (!r.available || r.skipped) return;
  const b = clear($("#update-banner"));
  const v = r.latest.version;
  b.append(h("span", { class: "grow" }, h("strong", {}, "Une nouvelle version de CCTransfert est disponible : " + v), " (vous avez " + r.current + ")."),
    h("button", { class: "btn small ghost", type: "button", onclick: () => confirmModal({
      title: "Nouveautés de la version " + v, body: [notesBlock(r.latest) || "Aucune note de version."], ok: "Fermer", cancel: "Fermer" }) }, "Nouveautés"),
    r.can_auto
      ? h("button", { class: "btn small primary", type: "button", onclick: () => startUpdate(r) }, "Mettre à jour")
      : h("button", { class: "btn small", type: "button", onclick: () => { showTab("settings"); } }, "Comment mettre à jour"),
    h("button", { class: "btn small ghost", type: "button", onclick: () => { b.hidden = true; } }, "Plus tard"),
    h("button", { class: "btn small ghost", type: "button", onclick: () => {
      api("/api/update/skip", { method: "POST", body: { version: v } }).catch(() => {});
      b.hidden = true;
    } }, "Ignorer cette version"));
  b.hidden = false;
}

async function startUpdate(r) {
  const v = r.latest.version;
  const ok = await confirmModal({
    title: "Mettre à jour vers la version " + v + " ?",
    body: ["CCTransfert va télécharger la version " + v + ", vérifier son empreinte (sha256), puis redémarrer. Vos sessions et vos réglages ne sont pas touchés.", notesBlock(r.latest)].filter(Boolean),
    ok: "Mettre à jour",
  });
  if (!ok) return;
  const b = clear($("#update-banner"));
  b.append(h("span", { class: "spinner" }), " Téléchargement et installation de la version " + v + "…");
  b.hidden = false;
  try {
    await api("/api/update/apply", { method: "POST" });
    document.body.textContent = "";
    document.body.append(h("div", { class: "empty big" }, h("div", { class: "empty-icon" }, "✓"),
      h("p", {}, "Version " + v + " installée."), h("p", { class: "muted" }, "CCTransfert redémarre dans une nouvelle fenêtre ; celle-ci va se fermer.")));
    setTimeout(() => window.close(), 5000);
  } catch (e) {
    clear(b).append(h("span", { class: "grow" }, "Mise à jour impossible : " + e.message),
      h("button", { class: "btn small ghost", type: "button", onclick: () => { b.hidden = true; } }, "Fermer"));
  }
}

// ===================================================================================
// Démarrage
// ===================================================================================
function heartbeat() {
  api("/api/ping", { method: "POST", body: { page: PAGE_ID } }).catch(() => {});
}

async function init() {
  if (!TOKEN) {
    document.body.textContent = "Jeton manquant : ouvrez l'interface avec la commande CCTransfert gui.";
    return;
  }
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
  $("#search").addEventListener("input", (e) => { state.search = e.target.value; renderList(); });
  $("#project").addEventListener("change", (e) => { state.project = e.target.value; renderList(); });
  $("#picker-ok").onclick = () => pickerDone($("#picker-path").value.trim() || picker.path);
  $("#picker-path").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); browse(e.target.value); } });
  $("#undo-last").onclick = () => undoLast();
  $("#quit").onclick = async () => {
    try { await api("/api/quit", { method: "POST" }); } catch (e) { /* déjà arrêté */ }
    document.body.textContent = "";
    document.body.append(h("div", { class: "empty big" }, h("div", { class: "empty-icon" }, "✓"), h("p", {}, "CCTransfert est arrêté."), h("p", { class: "muted" }, "Vous pouvez fermer cette fenêtre.")));
    window.close();
  };
  initImport();

  heartbeat();
  setInterval(heartbeat, 20000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) heartbeat(); });
  window.addEventListener("pagehide", () => navigator.sendBeacon(
    "/api/bye?" + new URLSearchParams({ t: TOKEN, page: PAGE_ID })));

  try {
    state.info = await api("/api/state");
  } catch (e) {
    toast(e.message, true);
    return;
  }
  const i = state.info;
  $("#ver").textContent = "v" + i.version;
  $("#machine").textContent = `${i.user}@${i.hostname} · ${i.os}${i.wsl ? " (WSL)" : ""} · Claude Code ${i.claude_version || "?"}`;
  $("#machine").title = "Config : " + i.config_dir;
  await loadSessions();
  autoCheckUpdate();
  const toOpen = OPEN_PATH || i.preload;
  if (toOpen) {
    showTab("import");
    openBundle(toOpen);
  }
}

init();
