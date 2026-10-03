// Page unique de ytdl (spec §6) : analyse d'un lien, choix du format, suivi des tâches.
// Les titres viennent de sites tiers : toujours textContent, jamais innerHTML.
const $ = (id) => document.getElementById(id);
const GB = 2 ** 30;
const STATES = {
  queued: "En attente",
  downloading: "Téléchargement",
  processing: "Conversion",
  awaiting_decision: "Décision requise",
  ready: "Prête",
  error: "Erreur",
  cancelled: "Annulée",
};
let analysis = null;
let lastJobs = "";

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw data.error || { message: `Erreur ${response.status}` };
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((child) => child !== null && child !== undefined));
  return node;
}

const known = (value) => value !== null && value !== undefined;
const checked = (name) => document.querySelector(`input[name="${name}"]:checked`).value;
const clock = (t) => new Date(t * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });

function bytes(n) {
  if (!known(n)) return "taille inconnue";
  if (n >= GB) return `${(n / GB).toFixed(1).replace(".", ",")} Go`;
  return `${Math.max(1, Math.round(n / 2 ** 20))} Mo`;
}

function duration(seconds) {
  if (!known(seconds)) return "";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h} h ${String(m).padStart(2, "0")}`;
  return `${m} min ${String(s % 60).padStart(2, "0")}`;
}

function showError(err) {
  $("form-error").textContent = err.message || "Erreur";
  $("form-error").hidden = false;
}

$("analyze").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button");
  button.disabled = true;
  button.textContent = "Analyse…";
  $("form-error").hidden = true;
  $("options").hidden = true;
  try {
    analysis = await api("POST", "/api/analyze", { url: $("url").value.trim() });
    showOptions();
  } catch (err) {
    showError(err);
  } finally {
    button.disabled = false;
    button.textContent = "Analyser";
  }
});

function showOptions() {
  const summary = $("summary");
  summary.replaceChildren();
  if (analysis.kind === "video") {
    if (analysis.thumbnail) summary.append(el("img", { src: analysis.thumbnail, alt: "" }));
    summary.append(el("p", { class: "title" }, analysis.title), el("p", { class: "muted" }, duration(analysis.duration)));
    $("mp3-size").textContent = analysis.mp3_size ? ` · ≈ ${bytes(analysis.mp3_size)}` : "";
  } else {
    summary.append(
      el("p", { class: "title" }, `${analysis.title} · ${analysis.count} vidéos`),
      el("ol", { class: "entries" }, ...analysis.entries.map((entry) => el("li", {}, entry.title))),
    );
    $("mp3-size").textContent = "";
  }
  $("mode-row").hidden = analysis.kind !== "playlist";
  fillQuality();
  $("options").hidden = false;
}

function fillQuality() {
  $("quality-row").hidden = checked("format") === "mp3";
  const choices = analysis.kind === "video"
    ? analysis.heights.map((h) => [h.height, `${h.height}p · ≈ ${bytes(h.size)}`])
    : [["", "Meilleure"], [1080, "1080p maximum"], [720, "720p maximum"], [480, "480p maximum"], [360, "360p maximum"]];
  $("height").replaceChildren(...choices.map(([value, label]) => el("option", { value: String(value) }, label)));
}

document.querySelectorAll('input[name="format"]').forEach((input) => {
  input.addEventListener("change", () => analysis && fillQuality());
});

$("options").addEventListener("submit", async (event) => {
  event.preventDefault();
  const format = checked("format");
  const height = $("height").value;
  try {
    await api("POST", "/api/jobs", {
      url: $("url").value.trim(),
      format,
      height: format === "mp4" && height ? Number(height) : null,
      playlist_mode: analysis.kind === "playlist" ? checked("mode") : null,
    });
    $("options").hidden = true;
    $("url").value = "";
    analysis = null;
    refresh();
  } catch (err) {
    showError(err);
  }
});

async function act(method, path, body) {
  try {
    await api(method, path, body);
  } catch (err) {
    alert(err.message || "Erreur");
  }
  refresh();
}

function renderJob(job) {
  const progress = job.progress || {};
  const mode = { zip: "ZIP", concat: "un seul fichier" }[job.playlist_mode];
  const li = el("li", { class: `job ${job.state}` },
    el("p", { class: "title" }, job.title || job.url),
    el("p", { class: "muted" }, [STATES[job.state], job.format.toUpperCase() + (job.height ? ` ${job.height}p` : ""), mode]
      .filter(Boolean).join(" · ")),
  );
  if (job.state === "downloading" || job.state === "processing") {
    if (known(progress.percent)) li.append(el("progress", { max: "100", value: String(progress.percent) }));
    const bits = [];
    if (progress.items) bits.push(`vidéo ${progress.item}/${progress.items}`);
    if (known(progress.percent)) bits.push(`${Math.round(progress.percent)} %`);
    if (progress.speed_bps) bits.push(`${bytes(progress.speed_bps)}/s`);
    if (known(progress.eta_s)) bits.push(`${duration(progress.eta_s)} restantes`);
    if (bits.length) li.append(el("p", { class: "muted" }, bits.join(" · ")));
  }
  if (job.skipped.length) li.append(el("p", { class: "muted" }, `${job.skipped.length} indisponible(s) : ${job.skipped.join(", ")}`));
  if (job.note) li.append(el("p", { class: "muted" }, job.note));
  if (job.state === "error") {
    li.append(
      el("p", { class: "error" }, job.error.message),
      el("details", {}, el("summary", {}, "Détails"), el("pre", {}, job.error.details || "—")),
    );
  }
  const actions = el("div", { class: "actions" });
  if (job.state === "ready") {
    actions.append(el("a", { class: "button", href: job.file.url, download: job.file.name }, `Récupérer · ${bytes(job.file.size)}`));
    li.append(el("p", { class: "muted" }, `Effacée à ${clock(job.expires_at)}`));
  }
  if (job.state === "awaiting_decision") {
    li.append(el("p", {}, `Les vidéos n'ont pas toutes le même format : il faut tout ré-encoder (≈ ${duration(job.decision.reencode_estimate_s)} de calcul).`));
    actions.append(
      el("button", { onclick: () => act("POST", `/api/jobs/${job.id}/decision`, { choice: "reencode" }) }, "Ré-encoder"),
      el("button", { onclick: () => act("POST", `/api/jobs/${job.id}/decision`, { choice: "zip" }) }, "ZIP à la place"),
    );
  }
  const running = ["queued", "downloading", "processing"].includes(job.state);
  actions.append(el("button", { class: "secondary", onclick: () => act("DELETE", `/api/jobs/${job.id}`) }, running ? "Annuler" : "Supprimer"));
  li.append(actions);
  return li;
}

async function refresh() {
  try {
    const [jobs, status] = await Promise.all([api("GET", "/api/jobs"), api("GET", "/api/status")]);
    const snapshot = JSON.stringify(jobs);
    if (snapshot !== lastJobs) {
      // Ne redessine que si quelque chose a changé : un volet « Détails » ouvert le reste.
      lastJobs = snapshot;
      $("jobs").replaceChildren(...jobs.map(renderJob));
      $("empty").hidden = jobs.length > 0;
    }
    const banner = $("banner");
    banner.hidden = status.cookies !== "absent" && status.cookies !== "expired";
    banner.textContent = status.cookies === "absent"
      ? "Cookies absents : dépose cookies.txt sur le VPS (voir le README)."
      : "Cookies expirés : à renouveler (voir le README).";
  } catch {
    // Réseau coupé (téléphone en veille…) : on réessaie au prochain tour.
  }
}

refresh();
setInterval(refresh, 2000);
