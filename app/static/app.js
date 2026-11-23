"use strict";
/* Interface du moteur de programmation du bloc opératoire (flux tiré). */

const JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
const JOURS_COURTS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."];
const etat = {
  init: null, planning: null, grille: null, analyse: null, resultats: null,
  semaine: 0, vueGrille: "actuelle", posCycle: 0, graphes: {}, derniereInfos: null,
};

/* ---------------- utilitaires ---------------- */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (x, d = 1) => (x === null || x === undefined || Number.isNaN(x)) ? "—" :
  Number(x).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (x, d = 0) => (x === null || x === undefined) ? "—" : nf(100 * x, d) + " %";
const hm = (m) => `${Math.floor(m / 60)}h${String(Math.round(m % 60)).padStart(2, "0")}`;
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
function dateCourte(iso) {
  const d = new Date(iso + "T12:00:00");
  return `${JOURS_COURTS[(d.getDay() + 6) % 7]} ${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
}
function dateLongue(iso) {
  const d = new Date(iso + "T12:00:00");
  return `${JOURS[(d.getDay() + 6) % 7]} ${d.toLocaleDateString("fr-FR")}`;
}
function ajouterJours(iso, n) {
  const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}
const aujourdhui = () => new Date().toISOString().slice(0, 10);

async function api(url, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, ...options };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  const r = await fetch(url, opts);
  const j = await r.json();
  if (!r.ok || j.erreur) throw new Error(j.erreur || r.statusText);
  return j;
}
let minuteurToast;
function toast(msg, erreur = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (erreur ? " erreur" : ""); t.hidden = false;
  clearTimeout(minuteurToast); minuteurToast = setTimeout(() => (t.hidden = true), erreur ? 7000 : 3500);
}

/* ---------------- graphiques (Chart.js) ---------------- */
function couleurs() {
  return {
    s1: css("--s1"), s2: css("--s2"), s3: css("--s3"), s4: css("--s4"), s5: css("--s5"), s6: css("--s6"), s7: css("--s7"),
    texte: css("--texte"), texte2: css("--texte-2"), muet: css("--muet"), grille: css("--grille"),
    axe: css("--axe"), critique: css("--critique"), alerte: css("--alerte"), surface: css("--surface"),
    voile: css("--s1-voile"),
  };
}
function appliquerDefautsGraphes() {
  const c = couleurs();
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = c.texte2;
  Chart.defaults.borderColor = c.grille;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.boxHeight = 12;
  // légende et info-bulle dans l'ordre des séries (et non dans l'ordre de dessin)
  Chart.defaults.plugins.legend.labels.sort = (a, b) => a.datasetIndex - b.datasetIndex;
  Chart.defaults.plugins.tooltip.itemSort = (a, b) => a.datasetIndex - b.datasetIndex;
  Chart.defaults.plugins.tooltip.backgroundColor = c.texte;
  Chart.defaults.plugins.tooltip.titleColor = c.surface;
  Chart.defaults.plugins.tooltip.bodyColor = c.surface;
  Chart.defaults.elements.line.borderWidth = 2;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 5;
  Chart.defaults.maintainAspectRatio = false;
}
function graphe(id, config) {
  if (etat.graphes[id]) etat.graphes[id].destroy();
  const el = document.getElementById(id);
  if (!el) return null;
  etat.graphes[id] = new Chart(el, config);
  return etat.graphes[id];
}
const echelle = (titre, extra = {}) => ({
  grid: { color: couleurs().grille }, border: { color: couleurs().axe },
  title: titre ? { display: true, text: titre, color: couleurs().texte2 } : undefined, ...extra,
});

/* ---------------- navigation et thème ---------------- */
function ouvrirOnglet(nom) {
  $$(".onglet").forEach((b) => b.classList.toggle("actif", b.dataset.onglet === nom));
  $$(".page").forEach((p) => p.classList.toggle("actif", p.id === nom));
  $("#btn-presentation").classList.toggle("actif", nom === "presentation");
  if (nom === "planning") chargerPlanning();
  if (nom === "initiaux") chargerInitiaux();
  if (nom === "grille") chargerGrille();
  if (nom === "technique") chargerTechnique();
  if (nom === "simulation") chargerSimulation();
  if (nom === "reglages") afficherReglagesMedecin();
  history.replaceState(null, "", "#" + nom);
}
/* Mode Médecin (interface simplifiée : 4 onglets) / Technicien (tout le site). Le choix est gardé dans le navigateur ;
   style.css masque .tech-seul en mode Médecin et .medecin-seul en mode Technicien. */
const ONGLETS_MODE = { medecin: ["reglages", "initiaux", "planning", "attribution"],
  technicien: ["attribution", "planning", "simulation", "grille", "initiaux", "technique"] };
function appliquerMode(mode, changerOnglet = true) {
  etat.mode = mode === "medecin" ? "medecin" : "technicien";
  document.body.classList.toggle("mode-medecin", etat.mode === "medecin");
  $$("#choix-mode button").forEach((b) => b.classList.toggle("actif", b.dataset.mode === etat.mode));
  try { localStorage.setItem("mode", etat.mode); } catch (e) { /* stockage indisponible */ }
  const actif = $(".page.actif");
  // la page de présentation existe dans les deux modes : on y reste
  if (changerOnglet && etat.init && (!actif || (actif.id !== "presentation" && !ONGLETS_MODE[etat.mode].includes(actif.id)))) {
    ouvrirOnglet(ONGLETS_MODE[etat.mode][0]);
  }
}
function initMode() {
  let m = "technicien";
  try { m = localStorage.getItem("mode") || m; } catch (e) { /* stockage indisponible */ }
  appliquerMode(m, false);
  $$("#choix-mode button").forEach((b) => b.addEventListener("click", () => appliquerMode(b.dataset.mode)));
}
function initTheme() {
  let t = null;
  try { t = localStorage.getItem("theme"); } catch (e) { /* stockage indisponible */ }
  if (t) document.documentElement.dataset.theme = t;
  $("#theme").addEventListener("click", () => {
    const sombre = document.documentElement.dataset.theme === "dark" ||
      (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = sombre ? "light" : "dark";
    try { localStorage.setItem("theme", document.documentElement.dataset.theme); } catch (e) { /* rien */ }
    appliquerDefautsGraphes();
    rafraichirGraphes();
  });
}
function rafraichirGraphes() {
  const actif = $(".page.actif").id;
  if (actif === "planning" && etat.planning) afficherPlanning();
  if (actif === "initiaux" && etat.initiaux) afficherInitiaux();
  if (actif === "grille" && etat.grille) afficherGrille();
  if (actif === "technique") { if (etat.analyse) afficherAnalyse(); if (etat.resultats) afficherResultats(etat.resultats); }
}

/* =====================================================================
   1. ATTRIBUTION D'UN PATIENT
   ===================================================================== */
function remplirFormulaire() {
  const I = etat.init;
  const sel = $("#f-praticien");
  sel.innerHTML = I.chirurgiens.map((c) =>
    `<option value="${esc(c.code)}">${esc(c.code)} — ${esc(c.nom)}${c.dans_grille ? "" : " (sans vacation)"}</option>`).join("");
  $("#f-anesth").innerHTML += I.anesth_types.map((a) => `<option>${esc(a)}</option>`).join("");
  $("#f-locoreg").innerHTML += I.anesth_locoreg.map((a) => `<option>${esc(a)}</option>`).join("");
  $("#ini-praticien").innerHTML = sel.innerHTML;
  $("#ini-anesth").innerHTML += I.anesth_types.map((a) => `<option>${esc(a)}</option>`).join("");
  $("#ini-date").value = I.params.date_ouverture;
  $("#liste-cim").innerHTML = I.cim.map((c) => `<option value="${esc(c.code)}">${esc(c.libelle || "")}</option>`).join("");
  const ouverture = I.params.date_ouverture;
  const base = aujourdhui() > ouverture ? aujourdhui() : ouverture;
  $("#f-souhait").value = ajouterJours(base, 14);
  $("#f-demande").value = aujourdhui();
  $("#f-tol").value = I.params.tolerance;
  sel.addEventListener("change", majChirurgien);
  majChirurgien();
}
function majChirurgien() {
  const c = etat.init.chirurgiens.find((x) => x.code === $("#f-praticien").value);
  if (!c) return;
  const autres = etat.init.types.filter((t) => !c.types.includes(t));
  $("#liste-types").innerHTML = [...c.types, ...autres].map((t) => `<option value="${esc(t)}">`).join("");
  if (c.types.length && !$("#f-type").value) $("#f-type").placeholder = "ex. " + c.types[0];
}
function lireFormulaire() {
  const f = new FormData($("#form-patient"));
  const o = Object.fromEntries(f.entries());
  o.age = Number(o.age); o.sexe = Number(o.sexe);
  return o;
}
async function proposer(ev) {
  ev.preventDefault();
  const infos = lireFormulaire();
  etat.derniereInfos = infos;
  $("#zone-propositions").innerHTML = `<div class="vide">Calcul des créneaux…</div>`;
  try {
    const r = await api("/api/proposer", { method: "POST", body: infos });
    afficherPrediction(r);
    afficherPropositions(r);
  } catch (e) { toast(e.message, true); $("#zone-propositions").innerHTML = ""; }
}
// fenêtre « Comment les dates sont proposées » (mode Technicien) : réglages en vigueur, lus à l'ouverture
function afficherExplicationsAttribution() {
  const P = etat.init?.params;
  if (!P) return;
  $$("#explications-attribution [data-param]").forEach((el) => (el.textContent = nfCourt(P[el.dataset.param])));
}
function afficherPrediction(r) {
  const p = r.prediction, pat = r.patient;
  const heberg = pat.ambu === 1
    ? `<div class="valeur">Ambulatoire</div><div class="detail">probabilité ${pct(p.p_ambu)}</div>`
    : `<div class="valeur">${pat.nuits} <small>nuit${pat.nuits > 1 ? "s" : ""}</small></div><div class="detail">hospitalisé · P(ambulatoire) ${pct(p.p_ambu)}</div>`;
  $("#zone-prediction").innerHTML = `
    <div class="carte prediction">
      <h2 style="margin-bottom:2px">Prédiction</h2>
      <p class="discret tech-seul" style="margin:0 0 10px">(${p.methode === "ml" ? "machine learning" : "étude statistique"}, ${etat.init.params.exclure_covid ? "mois COVID exclus" : "mois COVID inclus"})</p>
      <p class="discret medecin-seul" style="margin:0 0 10px">d'après les patients semblables opérés de 2019 à 2022</p>
      <div class="tuiles">
        <div class="tuile"><div class="etiquette">Durée opératoire</div><div class="valeur">${nf(p.duree, 0)} <small>min ± ${nf(p.sigma, 0)}</small></div></div>
        <div class="tuile"><div class="etiquette">Hébergement</div>${heberg}</div>
        <div class="tuile"><div class="etiquette"><span class="tech-seul">Poids du retard w<sub>i</sub></span><span class="medecin-seul">Priorité médicale</span></div><div class="valeur">${nf(pat.poids_retard, 1)}</div>
          <div class="detail">diagnostic ${esc(pat.cim || "non renseigné")}</div></div>
        <div class="tuile"><div class="etiquette">Fenêtre proposée</div><div class="valeur" style="font-size:15px">${dateCourte(pat.date_min)} → ${dateCourte(pat.date_max)}</div>
          <div class="detail">tolérance jusqu'au ${dateCourte(ajouterJours(pat.date_souhait, pat.tolerance))} · limite médicale : ${dateCourte(pat.limite)}</div></div>
      </div>
    </div>`;
}
function barreRemplissage(avant, apres) {
  const a = Math.min(1, avant), b = Math.min(1, apres);
  const trop = apres > 1;
  return `<div class="remplissage" title="Remplissage avant → après : ${pct(avant)} → ${pct(apres)}">
    <div class="avant" style="width:${100 * a}%"></div>
    <div class="ajout ${trop ? "trop" : ""}" style="left:${100 * a}%;width:${100 * Math.max(0, b - a)}%"></div></div>`;
}
function miniNuits(nuits) {
  if (!nuits || !nuits.length) return "";
  const max = Math.max(...nuits.map((n) => Math.max(n.lits, n.cible, 1)), 1);
  return `<div class="mini-nuits" aria-label="Lits occupés les nuits du séjour">` +
    nuits.map((n) => `<div class="n" style="height:100%" title="${dateCourte(n.date)} : ${nf(n.lits, 0)} lits (cible ${nf(n.cible, 1)}, capacité ${n.cap})">
      <div class="v" style="height:${100 * n.lits / max}%;${n.week_end ? "opacity:.6" : ""}"></div>
      <div class="c" style="bottom:${100 * n.cible / max}%"></div></div>`).join("") +
    `</div><div class="mini-legende">Barres : lits occupés chaque nuit du séjour · trait : cible (week-end en clair)</div>`;
}
function carteProposition(c, optimale) {
  const statut = c.faisable
    ? `<span class="badge ok">✓ Capacités respectées</span>`
    : `<span class="badge ko">⚠ ${!c.marge_ok ? "Marge de salle dépassée" : "Capacité d'hébergement dépassée"}</span>`;
  return `<div class="prop ${optimale ? "optimale" : ""}">
    <div class="rang"><span class="lettre">Date ${c.rang} · ${optimale ? "optimale" : "alternative"}</span>${statut}</div>
    <div class="date">${esc(c.libelle)}</div>
    <div class="ou">Salle ${c.salle} · ${c.debut} – ${c.fin} · ${esc(c.chir)}</div>
    ${barreRemplissage(c.remplissage_avant, c.remplissage_apres)}
    ${miniNuits(c.nuits)}
    <ul>${c.explication.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>
    <button class="bouton ${optimale ? "principal" : ""}" data-choix='${esc(JSON.stringify({ date: c.date, salle: c.salle, debut_min: c.debut_min }))}'>Choisir cette date</button>
  </div>`;
}
function afficherPropositions(r) {
  const z = $("#zone-propositions");
  if (r.erreur) { z.innerHTML = `<div class="alerte">${esc(r.erreur)}</div>`; return; }
  if (!r.propositions.length) { z.innerHTML = `<div class="alerte">Aucun créneau trouvé.</div>`; return; }
  let html = `<div class="titre-carte"><h2>Dates proposées</h2><span class="discret">${r.nb_candidats} vacations évaluées, ${r.nb_faisables} sans dépassement de capacité</span></div>`;
  if (!r.nb_faisables) html += `<div class="alerte">Aucune vacation ne respecte toutes les capacités dans la fenêtre : les dates ci-dessous sont les moins mauvaises. Élargissez la tolérance ou la fenêtre.</div>`;
  html += `<div class="propositions">` + r.propositions.map((c, k) => carteProposition(c, k === 0)).join("");
  z.innerHTML = html + `</div>`;
  $$("[data-choix]", z).forEach((b) => b.addEventListener("click", () => choisir(JSON.parse(b.dataset.choix))));
}
async function choisir(choix) {
  try {
    const p = await api("/api/valider", { method: "POST", body: { infos: etat.derniereInfos, choix } });
    const hp = p.horaire;
    toast(`Patient ${p.id} programmé le ${dateLongue(p.vacation.date)}, salle ${p.vacation.salle}` +
      (hp ? ` : convocation à ${hp.convocation}, SAS à ${hp.sas}, entrée en salle à ${hp.entree}.` : "."));
    $("#zone-propositions").innerHTML = `<div class="vide">Patient programmé. Saisissez le patient suivant.</div>`;
    $("#zone-prediction").innerHTML = "";
    chargerDerniers();
  } catch (e) { toast(e.message, true); }
}
async function chargerDerniers() {
  const v = await api("/api/planning");
  etat.planning = v;
  $("#compte-planning").textContent = `${v.nb_patients} patients dans le planning`;
  const L = v.derniers;
  if (!L.length) { $("#derniers").innerHTML = `<p class="discret">Aucun patient saisi par le médecin pour l'instant.
    <span class="tech-seul">Le planning initial se construit dans l'onglet Technique (comparaison des métaheuristiques, puis « Adopter »).</span>
    <span class="medecin-seul">Le planning d'ouverture se calcule dans l'onglet « Ouverture de l'hôpital ».</span></p>`; return; }
  $("#derniers").innerHTML = `<table><thead><tr><th>Patient</th><th>Chirurgien</th><th>Intervention</th><th>Diagnostic</th><th>Anesthésie</th><th>Locorégionale</th><th>Date</th><th>Salle</th><th>Convocation</th><th>SAS</th><th>Entrée en salle</th><th class="num">Durée</th><th>Hébergement</th><th></th></tr></thead><tbody>` +
    L.map((p) => `<tr><td>${esc(p.nom || p.id)}</td><td>${esc(p.chir)}</td><td>${esc(p.type_interv)}</td><td>${esc(p.cim)}</td>
      <td>${esc(p.anesth_type || "Non renseignée")}</td><td>${esc(p.anesth_locoreg || "Aucune")}</td>
      <td>${dateLongue(p.vacation.date)}</td><td>${p.vacation.salle}</td>
      <td>${p.horaire ? p.horaire.convocation : "—"}</td><td>${p.horaire ? p.horaire.sas : "—"}</td>
      <td>${p.horaire ? `${p.horaire.entree} <span class="discret">(${p.horaire.rang}/${p.horaire.nb})</span>` : "—"}</td><td class="num">${nf(p.duree, 0)} min</td>
      <td>${p.ambu ? "ambulatoire" : p.nuits + " nuit(s)"}</td><td><button class="lien" data-suppr="${esc(p.id)}">retirer</button></td></tr>`).join("") + `</tbody></table>`;
  $$("[data-suppr]").forEach((b) => b.addEventListener("click", async () => {
    await api("/api/patient/" + b.dataset.suppr, { method: "DELETE" });
    toast("Patient retiré du planning."); chargerDerniers();
  }));
}

/* ----- charge du planning par rapport à l'activité réelle de la base (onglets Patients initiaux et Simulation) -----
   Activité réelle sur S semaines : rythme de la base (patients programmés 2019-2022 / jours ouvrés) × 5 jours × S.
   Nouveaux patients du flux qui veulent une date avant la fin du planning (même règle que src/flux.py : date souhaitée
   dans les S semaines qui suivent la demande) : en moyenne (S − 1) / 2 semaines de demandes, soit rythme × 5 × (S − 1) / 2.
   connus : patients déjà connus le jour de l'ouverture en temps normal, au rythme réel (rythme × 5 × (S + 1) / 2). */
function chargeActivite(x, S, rythme) {
  const FH = etat.init.flux_historique;
  const normal = FH.par_jour * 5 * S, flux = rythme * 5 * (S - 1) / 2;
  return { normal, flux, depart: x / normal, avecFlux: (x + flux) / normal, connus: FH.par_jour * 5 * (S + 1) / 2 };
}
// liste de départ : celle de l'onglet Patients initiaux si elle est chargée (patients ajoutés à la main compris), sinon les réglages
function listeDepart() {
  return etat.initiaux ? { x: etat.initiaux.resume.n, S: etat.initiaux.parametres.semaines }
    : { x: etat.init.params.initial_nb_patients, S: etat.init.params.instance_semaines };
}
// rythme des nouveaux patients réglé dans l'onglet Simulation (valeur proposée tant que l'onglet n'a pas été ouvert)
const rythmeFlux = () => (etat.simPrete ? Number($("#flux-par-jour").value) || 0 : etat.init.flux_historique.par_jour_defaut);

/* =====================================================================
   1 bis. PATIENTS À PROGRAMMER À L'OUVERTURE
   ===================================================================== */
async function chargerInitiaux() {
  try { etat.initiaux = await api("/api/initiaux"); afficherInitiaux(); } catch (e) { toast(e.message, true); }
}
function afficherInitiaux() {
  const L = etat.initiaux, R = L.resume, P = L.parametres;
  $("#ini-x").value = P.x; $("#ini-sem").value = P.semaines; $("#ini-graine").value = P.graine;
  $("#ini-sous-titre").textContent = `ouverture le ${dateLongue(P.date_ouverture)} · ${P.grille === "nouvelle" ? "grille proposée" : "ancienne grille"}`;
  $("#ini-kpi").innerHTML = [
    tuile("Patients dans la liste", R.n, `${R.generes} générés · ${R.ajoutes} ajoutés à la main`),
    tuile("Ambulatoires prédits", pct(R.part_ambu), `${R.nuits} nuits d'hospitalisation prévues`),
    tuile("Temps opératoire prévu", `${nf(R.minutes_demandees / 60, 0)} <small>h</small>`,
      `pour ${nf(R.minutes_offertes / 60, 0)} h de vacations (${pct(R.minutes_offertes ? R.minutes_demandees / R.minutes_offertes : null)})`),
    tuile("Chirurgiens", R.chirurgiens, `âge moyen ${nf(R.age_moyen, 0)} ans`),
  ].join("");
  $("#ini-activite").innerHTML = texteActivite(R.n, P.semaines);
  const c = couleurs();
  const barres = (id, lignes, lib) => graphe(id, {
    type: "bar",
    data: { labels: lignes.map(lib), datasets: [
      { label: "Liste générée", data: lignes.map((x) => x.part_liste), backgroundColor: c.s1, borderRadius: 4, barPercentage: 0.85, categoryPercentage: 0.8 },
      { label: "Base (historique)", data: lignes.map((x) => x.part_base), backgroundColor: c.axe, borderRadius: 4, barPercentage: 0.85, categoryPercentage: 0.8 },
    ] },
    options: { indexAxis: "y", interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (x) => `${x.dataset.label} : ${pct(x.parsed.x, 1)}` } } },
      scales: { x: echelle("part des patients", { beginAtZero: true, ticks: { callback: (v) => pct(v) } }), y: echelle(null, { grid: { display: false } }) } },
  });
  barres("g-ini-diag", L.diagnostics, (x) => x.libelle ? `${x.code} ${x.libelle.slice(0, 26)}` : x.code);
  barres("g-ini-chir", L.chirurgiens, (x) => x.code);
  tableInitiaux();
}
// part de l'activité réelle : la liste seule, puis avec les nouveaux patients de la simulation
function texteActivite(x, S) {
  if (!etat.init || !etat.init.flux_historique) return "";
  const r = rythmeFlux(), C = chargeActivite(x, S, r);
  return `<div>Part de l'activité réelle : <b>${pct(C.depart)}</b> (${nf(x, 0)} patients pour ${nf(C.normal, 0)} en ${S} semaines
      au rythme de la base de la BDD).</div>
    <div>Avec le flux de la simulation : <b>≈ ${pct(C.avecFlux)}</b> (environ ${nf(C.flux, 0)} nouveaux patients de plus dans le planning,
      au rythme réglé dans l'onglet Simulation : ${nf(r, 1)} nouveaux patients par jour ouvré).</div>`;
}
function tableInitiaux() {
  const f = ($("#ini-filtre").value || "").toLowerCase();
  const L = etat.initiaux.patients.filter((p) => !f ||
    [p.id, p.chir, p.type_interv, p.cim, p.date_souhait, p.nom].join(" ").toLowerCase().includes(f));
  $("#ini-compte").textContent = `${L.length} patient(s) affiché(s) sur ${etat.initiaux.patients.length}`;
  const lib = etat.init.libelles_cim3 || {};
  $("#ini-table").innerHTML = `<table><thead><tr><th>ID</th><th>Chirurgien</th><th>Intervention</th><th>Diagnostic</th><th class="num">Âge</th><th>Sexe</th>
    <th>Anesthésie</th><th>Date souhaitée</th><th class="num">Durée prévue</th><th>Hébergement prévu</th><th class="num tech-seul">w<sub>i</sub></th><th></th></tr></thead><tbody>` +
    L.map((p) => `<tr><td>${esc(p.id)}${p.source === "ajouté" ? ` <span class="badge info">ajouté</span>` : ""}</td><td><b>${esc(p.chir)}</b></td>
      <td>${esc(p.type_interv)}</td><td title="${esc(lib[(p.cim || "").slice(0, 3)] || "")}">${esc(p.cim)}</td><td class="num">${nf(p.age, 0)}</td>
      <td>${p.sexe === 2 ? "F" : "H"}</td><td>${esc(p.anesth_type)}</td><td>${dateCourte(p.date_souhait)}</td>
      <td class="num">${nf(p.duree, 0)} ± ${nf(p.sigma, 0)} min</td><td>${p.ambu ? "ambulatoire" : p.nuits + " nuit(s)"} <span class="discret tech-seul">(P(ambu) ${pct(p.p_ambu)})</span></td>
      <td class="num tech-seul">${nf(p.poids_retard, 1)}</td><td><button class="lien" data-suppr-ini="${esc(p.id)}">supprimer</button></td></tr>`).join("") +
    `</tbody></table>`;
}
async function genererInitiaux() {
  const x = Number($("#ini-x").value), semaines = Number($("#ini-sem").value), graine = Number($("#ini-graine").value);
  if (etat.initiaux && etat.initiaux.resume.ajoutes && !confirm("Les patients ajoutés à la main seront remplacés. Continuer ?")) return;
  $("#btn-generer").disabled = true;
  try {
    const r = await api("/api/initiaux/generer", { method: "POST", body: { x, semaines, graine } });
    toast(`${r.n} patients générés.`);
    etat.init.params.instance_semaines = semaines;
    etat.init.params.initial_nb_patients = x;
    await chargerInitiaux();
  } catch (e) { toast(e.message, true); }
  $("#btn-generer").disabled = false;
}
async function ajouterInitial(ev) {
  ev.preventDefault();
  const o = Object.fromEntries(new FormData($("#form-ini")).entries());
  o.age = Number(o.age); o.sexe = Number(o.sexe);
  try {
    const p = await api("/api/initiaux/ajouter", { method: "POST", body: o });
    toast(`Patient ${p.id} ajouté : ${nf(p.duree, 0)} min prévues, ${p.ambu ? "ambulatoire" : p.nuits + " nuit(s)"}.`);
    await chargerInitiaux();
  } catch (e) { toast(e.message, true); }
}

/* =====================================================================
   2. PLANNING
   ===================================================================== */
async function chargerPlanning() {
  try { etat.planning = await api("/api/planning"); afficherPlanning(); } catch (e) { toast(e.message, true); }
}
function tuile(etiquette, valeur, detail = "", classe = "") {
  return `<div class="tuile ${classe}"><div class="etiquette">${etiquette}</div><div class="valeur">${valeur}</div><div class="detail">${detail}</div></div>`;
}
function afficherPlanning() {
  const v = etat.planning, I = v.indicateurs;
  $("#alerte-planning").innerHTML = v.nb_patients === 0
    ? `<div class="alerte tech-seul">Le planning est vide. Construisez le planning initial dans l'onglet <b>Technique</b> (comparaison des métaheuristiques puis « Adopter »), ou programmez des patients depuis la page Attribution.</div>
       <div class="alerte medecin-seul">Le planning est vide. Calculez le planning d'ouverture dans l'onglet <b>Ouverture de l'hôpital</b>, ou programmez des patients avec <b>Attribution patient</b>.</div>`
    : (v.non_places.length ? `<div class="alerte">${v.non_places.length} patient(s) n'ont plus de vacation (grille changée) : cliquez sur « Replacer ».</div>` : "");
  // patients de la liste d'ouverture écartés au calcul du planning : aucune vacation possible (src/scenario.py)
  // « Ignorer » masque l'alerte dans ce navigateur, tant que la liste des patients écartés ne change pas
  const E = v.ecartes || [], cleEcartes = E.map((e) => e.id).sort().join(",");
  let ignores = "";
  try { ignores = localStorage.getItem("ecartes-ignores") || ""; } catch (e) { /* stockage indisponible */ }
  if (v.nb_patients && E.length && ignores !== cleEcartes) {
    $("#alerte-planning").innerHTML += `<div class="alerte" id="alerte-ecartes">${E.length} patient(s) de la liste d'ouverture
    ne sont pas dans le planning, faute de vacation possible. Vous pouvez essayer de les programmer avec « Attribution patient ».
    <ul>${E.map((e) => `<li>${esc(e.id)} : ${esc(e.chir)}, ${esc(e.type_interv)}, souhaité le ${dateCourte(e.date_souhait)} (${esc(e.motif)})</li>`).join("")}</ul>
    <button class="bouton" id="btn-ignorer-ecartes">Ignorer</button></div>`;
    $("#btn-ignorer-ecartes").addEventListener("click", () => {
      try { localStorage.setItem("ecartes-ignores", cleEcartes); } catch (e) { /* stockage indisponible */ }
      $("#alerte-ecartes").remove();
    });
  }
  // nombre de patients + pourcentage (sur les patients placés) pour la case « Écart à la date souhaitée »
  const nEcart = (I.patients_dans_tolerance ?? 0) + (I.patients_avances ?? 0) + (I.patients_reportes ?? 0);
  const avecPart = (n) => (n === undefined || n === null) ? "—"
    : `${n} <span class="part">(${pct(nEcart ? n / nEcart : null)})</span>`;
  $("#kpi-planning").innerHTML = [
    tuile("Patients programmés", v.nb_patients, `${v.grille === "nouvelle" ? "grille proposée" : "ancienne grille"}`),
    // trois indicateurs de même taille : remplissage, vacations libérées (aucun patient), remplissage sans elles
    tuile("Remplissage des salles", `<div class="trio">
        <div><span>${pct(I.taux_remplissage_moyen)}</span><small>toutes les vacations</small></div>
        <div><span>${I.vacations_vides ?? "—"}</span><small>vacations libérées</small><small class="sur">sur ${v.vacations_grille ?? v.vacations.filter((x) => x.type === "chirurgie").length} vacations</small></div>
        <div><span>${pct(I.remplissage_vacations_utilisees)}</span><small>sans les vacations libérées</small></div></div>`,
      `<span class="tech-seul">${I.rotation_min > 0 ? `rotations de ${nf(I.rotation_min, 0)} min comprises (${pct(I.taux_remplissage_sans_rotation)} sans)`
        : I.rotation_dans_mu > 0 ? `rotations comprises : ${nf(I.rotation_dans_mu, 0)} min ajoutées à chaque durée µ` : "hors rotations"}<br>${I.vacations_depassees} vacation(s) au-delà de la marge</span>
        <span class="medecin-seul">${I.vacations_depassees} vacation(s) risquent de finir en retard</span>`, "tuile-trio"),
    tuile("Écart à la date souhaitée", `${nf(I.ecart_moyen_souhait, 1)} <small>j en moyenne</small>`,
      `<b>dans la tolérance :</b> ${avecPart(I.patients_dans_tolerance)}<br><b>avancés :</b> ${avecPart(I.patients_avances)}<br><b>reportés :</b> ${avecPart(I.patients_reportes)}`, "une-ligne"),
    // vacations hors marge (terme W5 de F) : charge prévue + z_β écarts-types au-delà de la durée ; seules les vacations
    // utilisées peuvent l'être (modele.py : vacations_depassees)
    tuile("Vacations hors marge", `${I.vacations_depassees ?? "—"} <small>sur ${(v.vacations_grille ?? v.vacations.filter((x) => x.type === "chirurgie").length) - (I.vacations_vides ?? 0)} utilisées</small>`,
      `<span class="tech-seul">durée prévue des opérations + marge de sécurité (z<sub>β</sub> écarts-types) au-delà de la durée de la vacation</span>
       <span class="medecin-seul">les opérations prévues et la marge de sécurité dépassent la durée de la vacation : risque de finir en retard</span>`),
  ].join("");
  // indicateurs d'hébergement : dans la carte du graphique correspondant
  $("#kpi-lits").innerHTML = [
    tuile("Lits occupés", `${nf(I.lits_moyens_semaine, 1)} <small>moyenne en semaine</small>`,
      `<b>max en semaine :</b> ${nf(I.lits_max_semaine, 0)}<br><b>moyenne le week-end :</b> ${nf(I.lits_moyens_week_end, 1)}<br><b>max le week-end :</b> ${nf(I.lits_max_week_end, 0)}`, "compacte"),
    // écart à la cible (terme W1 de F) : régularité et niveau du week-end à la fois
    tuile("Écart-type à la cible", nf(I.ecart_type_lits, 2), "toutes les nuits, autour de la cible fixée par le personnel"),
    tuile("Écart-type lits en semaine", nf(I.ecart_type_lits_semaine, 2), "nuits du lundi au vendredi"),
    tuile("Écart-type lits le week-end", nf(I.ecart_type_lits_week_end, 2), "nuits du samedi et du dimanche"),
  ].join("");
  $("#kpi-places").innerHTML = [
    tuile("Places ambulatoires", `${nf(I.places_moyennes, 1)} <small>moyenne</small>`, `<b>max :</b> ${nf(I.places_max, 0)}<br>jours d'ouverture de l'ambulatoire`),
    tuile("Écart-type places ambulatoires", nf(I.ecart_type_places_semaine, 2), "jours de semaine (ambulatoire ouvert)"),
  ].join("");
  $("#leg-lits").textContent = "Barres : lits occupés par les patients programmés · trait plein : cible proportionnelle au personnel · pointillés : capacité. Week-ends en clair.";
  $("#leg-places").textContent = "Barres : places occupées · trait : cible · pointillés : capacité (fermé le week-end et les jours fériés).";
  graphiqueOccupation("g-lits", v.dates, v.lits, v.cible_lits, v.cap_lits, "Lits", "Lits occupés");
  graphiqueOccupation("g-places", v.dates, v.places, v.cible_places, v.cap_ambu, "Places", "Places occupées");
  afficherSemaine();
}
function graphiqueOccupation(id, dates, valeurs, cible, cap, nom, libelle) {
  const c = couleurs();
  const we = dates.map((d) => { const j = new Date(d + "T12:00:00").getDay(); return j === 0 || j === 6; });
  graphe(id, {
    data: {
      labels: dates.map(dateCourte),
      datasets: [
        { type: "bar", label: libelle, data: valeurs, backgroundColor: we.map((w) => w ? c.voile : c.s1),
          borderColor: c.s1, borderWidth: we.map((w) => w ? 1 : 0), borderRadius: 3, categoryPercentage: 0.9, barPercentage: 0.92, order: 2 },
        { type: "line", label: "Cible", data: cible, borderColor: c.texte, borderWidth: 2, stepped: "middle", order: 1 },
        { type: "line", label: "Capacité", data: cap, borderColor: c.critique, borderWidth: 1.5, borderDash: [5, 4], stepped: "middle", order: 0 },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom" },
        tooltip: { callbacks: { label: (x) => `${x.dataset.label} : ${nf(x.parsed.y, 1)}` } } },
      scales: { x: echelle(null, { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 14 }, grid: { display: false } }),
        y: echelle(nom, { beginAtZero: true }) },
    },
  });
}
function afficherSemaine() {
  const v = etat.planning;
  const nbSem = Math.max(1, Math.ceil(v.nb_jours / 7));
  etat.semaine = Math.min(Math.max(0, etat.semaine), nbSem - 1);
  const lundi = ajouterJours(v.debut, 7 * etat.semaine);
  $("#sem-libelle").textContent = `Semaine ${etat.semaine + 1}/${nbSem} · du ${dateCourte(lundi)} au ${dateCourte(ajouterJours(lundi, 4))}`;
  if (!etat.progManuel) { $("#prog-debut").value = lundi; $("#prog-fin").value = ajouterJours(lundi, 4); }
  const vacs = v.vacations.filter((x) => x.jour >= 7 * etat.semaine && x.jour < 7 * etat.semaine + 5);
  let html = `<div class="semaine"><div></div>` +
    [0, 1, 2, 3, 4].map((j) => `<div class="entete-j">${dateLongue(ajouterJours(lundi, j))}</div>`).join("");
  for (const s of [1, 2, 3, 4, 5]) {
    html += `<div class="salle">Salle ${s}</div>`;
    for (let j = 0; j < 5; j++) {
      const L = vacs.filter((x) => x.salle === s && x.jour === 7 * etat.semaine + j);
      html += `<div class="cellule">` + L.map(carteVacation).join("") + `</div>`;
    }
  }
  $("#semaine").innerHTML = html + `</div>`;
}
function carteVacation(x) {
  const taux = x.charge / x.capacite, tauxMarge = x.charge_marge / x.capacite;
  const titre = x.type === "urgences" ? "⚑ Urgences" : x.type === "libre" ? "Libre" : x.chir;
  const etatMarge = x.patients.length === 0 ? `<span class="discret">vide</span>`
    : tauxMarge > 1 ? `<span class="badge ko">⚠ ${pct(taux)}</span>` : `<span class="badge ok">${pct(taux)}</span>`;
  return `<div class="vac ${x.type}">
    <div class="tete"><span>${esc(titre)} <span class="h">${hm(x.debut)}–${hm(x.fin)}</span></span>${etatMarge}</div>
    ${x.type === "chirurgie" || x.patients.length ? barreRemplissage(0, taux) : ""}
    ${x.patients.length ? `<details><summary>${x.patients.length} patient(s) · ${nf(x.charge, 0)} / ${x.capacite} min</summary>` +
      x.patients.map((p) => {
        const h = p.horaire || {};
        return `<div class="pat"><span><b>${h.entree || ""}</b> ${esc(p.type_interv)}</span><span>${p.duree} min · ${p.ambu ? "ambu" : p.nuits + " n"}</span></div>
          <div class="pat-h">convocation ${h.convocation || "—"} · SAS ${h.sas || "—"}${h.depasse ? ` · <span class="ko-txt">après la fin de la vacation</span>` : ""}
            <button class="lien modifier" data-modif="${esc(p.id)}" title="Déplacer ou déprogrammer ${esc(p.nom || p.id)}">modifier</button></div>`;
      }).join("") +
      `</details>` : ""}
  </div>`;
}

/* ----- déplacer ou déprogrammer un patient à la main (src/planning.py : options_deplacement, deplacer) ----- */
async function ouvrirModifPatient(pid) {
  const carte = $("#carte-modif-patient");
  carte.hidden = false;
  $("#modif-titre").textContent = `Déplacer ou déprogrammer ${pid}`;
  $("#modif-patient").innerHTML = `<div class="vide">Calcul des dates possibles…</div>`;
  carte.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    etat.modif = await api(`/api/patient/${encodeURIComponent(pid)}/deplacements`);
    afficherModifPatient(etat.modif);
  } catch (e) { $("#modif-patient").innerHTML = `<div class="alerte">${esc(e.message)}</div>`; }
}
// lits occupés les nuits du séjour (patient hospitalisé) ou place ambulatoire du jour
function celluleHebergement(c) {
  if (!c.nuits.length) return c.hebergement_ok ? "place ambulatoire disponible" : `<span class="ko-txt">places ambulatoires dépassées</span>`;
  const moy = c.nuits.reduce((s, n) => s + n.lits, 0) / c.nuits.length, max = Math.max(...c.nuits.map((n) => n.lits));
  const txt = `${c.nuits.length} nuit(s) : ${nf(moy, 1)} lits en moy., ${nf(max, 0)} au max.`;
  return c.hebergement_ok ? txt : `<span class="ko-txt">${txt} : capacité dépassée</span>`;
}
function texteEcartSouhait(c, tol) {
  const e = c.ecart_souhait;
  const txt = e === 0 ? "à la date souhaitée" : e < 0 ? `${-e} j avant` : e <= tol ? `${e} j après (dans la tolérance)` : `${e} j après`;
  return txt + (c.retard_jours > 0 ? ` · <span class="ko-txt">${c.retard_jours} j après la limite médicale</span>` : "");
}
function afficherModifPatient(R) {
  const p = R.patient, a = R.actuelle, C = R.candidats;
  const faisables = C.filter((c) => c.faisable);
  const meilleure = faisables.length ? faisables.reduce((m, c) => (c.delta_F < m.delta_F ? c : m)) : null;
  const reco = meilleure && meilleure.delta_F < 0 ? meilleure : null;   // seulement si le planning s'améliore
  const effet = (x) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${nf(Math.abs(x), 1)} <span class="discret">${x < 0 ? "(mieux)" : x > 0 ? "(moins bien)" : ""}</span>`;
  const detailF = (c) => Object.entries(c.delta_termes || {}).filter(([, x]) => Math.abs(x) >= 0.05)
    .map(([k, x]) => `${k} ${x > 0 ? "+" : ""}${nf(x, 1)}`).join(", ");
  const ligne = (c) => `<tr class="${c === reco ? "recommandee" : ""}">
      <td class="date">${dateLongue(c.date)}${c === reco ? `<br><span class="badge ok">recommandée</span>` : ""}${c.dans_fenetre ? "" : `<br><span class="badge info">hors fenêtre</span>`}</td>
      <td class="date">salle ${c.salle}<br>${c.debut}–${c.fin}</td>
      <td class="num" title="${esc(detailF(c))}">${effet(c.delta_F)}</td>
      <td class="num">${pct(c.remplissage_avant)} → ${pct(c.remplissage_apres)}</td>
      <td>${c.marge_ok ? `<span class="ok-txt">respectée</span>` : `<span class="ko-txt">dépassée</span>`}</td>
      <td>${celluleHebergement(c)}</td>
      <td>${texteEcartSouhait(c, p.tolerance || 0)}</td>
      <td>${c.horaire ? `convocation ${c.horaire.convocation}<br>entrée ${c.horaire.entree}` : "—"}</td>
      <td><button class="bouton ${c === reco ? "principal" : ""}" data-deplacer='${esc(JSON.stringify({ date: c.date, salle: c.salle, debut_min: c.debut_min }))}'>Déplacer ici</button></td></tr>`;
  const actuelle = a
    ? `<b>Date actuelle</b> : ${dateLongue(a.date)}, salle ${a.salle} (${a.debut}–${a.fin})${a.horaire ? `, convocation ${a.horaire.convocation}, entrée en salle ${a.horaire.entree}` : ""} ·
       remplissage ${pct(a.remplissage_apres)}, marge de la salle ${a.marge_ok ? "respectée" : "dépassée"}, ${texteEcartSouhait(a, p.tolerance || 0)}.`
    : `<b>Date actuelle</b> : aucune (sa vacation n'existe plus dans la grille). L'effet est compté par rapport au patient absent du planning.`;
  $("#modif-patient").innerHTML = `
    <p class="legende-texte" style="margin-top:0"><b>${esc(p.id)}</b>${p.nom ? ` (${esc(p.nom)})` : ""} : ${esc(p.chir)}, ${esc(p.type_interv)}${p.cim ? `, ${esc(p.cim)}` : ""},
      ${nf(p.duree, 0)} ± ${nf(p.sigma, 0)} min, ${p.ambu ? "ambulatoire" : `${p.nuits} nuit(s)`}. Souhaité le ${dateLongue(p.date_souhait)}
      (tolérance ${p.tolerance || 0} j) ; fenêtre de dates du ${dateCourte(p.date_min)} au ${dateCourte(p.date_max)} ; limite médicale le ${dateCourte(p.limite)}.</p>
    <p class="legende-texte">${actuelle}</p>
    ${C.length ? `<div class="defile" style="max-height:460px"><table class="table-modif"><thead><tr><th>Date</th><th>Vacation</th>
      <th class="num"><span class="tech-seul">Effet sur F</span><span class="medecin-seul">Effet sur le planning</span></th>
      <th class="num">Remplissage</th><th>Marge de la salle</th><th>Lits ou place</th><th>Date souhaitée</th><th>Horaire prévu</th><th></th></tr></thead>
      <tbody>${C.map(ligne).join("")}</tbody></table></div>`
      : `<div class="alerte">Aucune autre vacation de ${esc(p.chir)} jusqu'au ${dateCourte(R.fin_horizon)}.</div>`}
    <p class="aide">Toutes les autres vacations de ${esc(p.chir)} jusqu'au ${dateCourte(R.fin_horizon)}, par date ; les autres patients ne bougent pas.
      <span class="tech-seul">Effet sur F : différence de F entre cette vacation et la vacation actuelle (négatif = le planning s'améliore ;
      au survol, le détail par terme).</span><span class="medecin-seul">Effet sur le planning : négatif = le planning s'améliore.</span>
      « Recommandée » : la vacation sans dépassement (marge de la salle, lits, places) qui améliore le plus le planning.
      « Hors fenêtre » : en dehors de la fenêtre de dates du patient (avance maximale, tolérance et report maximal).</p>
    <div class="modif-actions"><button class="bouton danger" id="btn-deprogrammer">Déprogrammer ${esc(p.id)}</button>
      <span class="discret">retire le patient du planning ; les autres patients de sa vacation restent.</span></div>`;
  $$("[data-deplacer]", $("#modif-patient")).forEach((b) => b.addEventListener("click", () => deplacerPatient(p.id, JSON.parse(b.dataset.deplacer))));
  $("#btn-deprogrammer").addEventListener("click", () => deprogrammerPatient(p.id));
}
async function deplacerPatient(pid, choix) {
  if (!confirm(`Déplacer ${pid} au ${dateLongue(choix.date)}, salle ${choix.salle} ?`)) return;
  try {
    const p = await api(`/api/patient/${encodeURIComponent(pid)}/deplacer`, { method: "POST", body: choix });
    const hp = p.horaire;
    toast(`${pid} déplacé au ${dateLongue(p.vacation.date)}, salle ${p.vacation.salle}`
      + (hp ? ` : convocation à ${hp.convocation}, entrée en salle à ${hp.entree}.` : ".") + " Pensez à prévenir le patient.");
    $("#carte-modif-patient").hidden = true;
    chargerPlanning();
  } catch (e) { toast(e.message, true); }
}
async function deprogrammerPatient(pid) {
  if (!confirm(`Déprogrammer ${pid} ? Le patient sera retiré du planning.`)) return;
  try {
    await api("/api/patient/" + encodeURIComponent(pid), { method: "DELETE" });
    toast(`${pid} déprogrammé : il n'est plus dans le planning.`);
    $("#carte-modif-patient").hidden = true;
    chargerPlanning();
  } catch (e) { toast(e.message, true); }
}

/* ----- programme opératoire : impression ou PDF, et export Excel (src/planning.py : programme_operatoire) ----- */
function periodeProgramme() {
  const debut = $("#prog-debut").value, fin = $("#prog-fin").value;
  if (!debut || !fin) { toast("Indiquez les dates de début et de fin du programme.", true); return null; }
  if (fin < debut) { toast("La date de fin est avant la date de début.", true); return null; }
  return `debut=${debut}&fin=${fin}`;
}
function htmlProgramme(R) {
  const jours = [...new Set(R.vacations.map((v) => v.date))];
  const tableVac = (v) => `<h3>Salle ${v.salle} · ${v.type === "urgences" ? "Réserve d'urgences" : `${esc(v.chir)}${v.chir_nom ? ` - ${esc(v.chir_nom)}` : ""}`}
      · ${v.debut} – ${v.fin} · ${v.patients.length} patient(s)</h3>
    <table><thead><tr><th class="num">Rang</th><th>Convocation</th><th>SAS</th><th>Entrée en salle</th><th>Sortie prévue</th><th>Patient</th>
      <th>Intervention</th><th>Diagnostic</th><th>Anesthésie</th><th class="num">Durée</th><th>Hébergement</th></tr></thead><tbody>${
      v.patients.map((p) => { const h = p.horaire; return `<tr><td class="num">${h.rang}</td><td>${h.convocation}</td><td>${h.sas}</td>
        <td><b>${h.entree}</b></td><td>${h.sortie}${h.depasse ? " (après la fin)" : ""}</td>
        <td>${esc(p.nom || p.id)}${p.age ? `, ${nf(p.age, 0)} ans` : ""}${p.sexe ? ` (${p.sexe === 2 ? "F" : "H"})` : ""}</td>
        <td>${esc(p.type_interv)}</td><td>${esc(p.cim || "")}</td>
        <td>${esc(p.anesth_type || "Non renseignée")}${p.anesth_locoreg && p.anesth_locoreg !== "Aucune" ? ` + ${esc(p.anesth_locoreg)}` : ""}</td>
        <td class="num">${nf(p.duree, 0)} min</td><td>${p.ambu ? "ambulatoire" : `${p.nuits} nuit(s)`}</td></tr>`; }).join("")}</tbody></table>`;
  const maintenant = new Date();
  return `<h1>Programme opératoire</h1>
    <p class="sous-titre">Du ${dateLongue(R.debut)} au ${dateLongue(R.fin)} · édité le ${maintenant.toLocaleDateString("fr-FR")}
      à ${maintenant.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })} · horaires prévus, susceptibles de changer</p>` +
    jours.map((j) => `<section class="jour"><h2>${dateLongue(j)}</h2>${R.vacations.filter((v) => v.date === j).map(tableVac).join("")}</section>`).join("");
}
async function imprimerProgramme() {
  const periode = periodeProgramme();
  if (!periode) return;
  try {
    const R = await api("/api/programme?" + periode);
    if (!R.vacations.length) { toast("Aucun patient programmé sur cette période.", true); return; }
    $("#impression").innerHTML = htmlProgramme(R);
    window.print();
  } catch (e) { toast(e.message, true); }
}
function telechargerProgramme() {
  const periode = periodeProgramme();
  if (periode) window.location.href = "/api/programme.xlsx?" + periode;   // le serveur répond par un fichier joint
}

/* =====================================================================
   3. GRILLE DES VACATIONS
   ===================================================================== */
async function chargerGrille() {
  try { etat.grille = await api("/api/grille"); afficherGrille(); } catch (e) { toast(e.message, true); }
}
function positionsCycle() {
  return etat.vueGrille === "actuelle"
    ? [[0, "Semaine paire"], [1, "Impaire A"], [3, "Impaire B"]]
    : [[0, "Semaine paire"], [1, "Semaine impaire"]];
}
function afficherGrille() {
  const G = etat.grille;
  $$("#choix-grille button").forEach((b) => b.classList.toggle("actif", b.dataset.g === etat.vueGrille));
  const pos = positionsCycle();
  if (!pos.some(([k]) => k === etat.posCycle)) etat.posCycle = 0;
  $("#choix-semaine").innerHTML = pos.map(([k, l]) => `<button data-k="${k}" class="${k === etat.posCycle ? "actif" : ""}">${l}</button>`).join("");
  $$("#choix-semaine button").forEach((b) => b.addEventListener("click", () => { etat.posCycle = Number(b.dataset.k); afficherGrille(); }));
  const utilisee = G.grille_utilisee === etat.vueGrille;
  $("#btn-utiliser-grille").disabled = utilisee;
  $("#btn-utiliser-grille").textContent = utilisee ? "✓ Grille utilisée par le planning" : "Utiliser cette grille pour le planning";
  $("#grille-resume").textContent = etat.vueGrille === "actuelle"
    ? `Offre chirurgicale ${nf(G.total_actuel, 1)} h/sem · urgences ${nf(G.urgences_actuel, 1)} h/sem · cycle de 4 semaines (« 1 sem. impaire sur 2 »)`
    : `Offre chirurgicale ${nf(G.total_nouveau, 1)} h/sem (ancienne grille ${nf(G.total_actuel, 1)}) · urgences ${nf(G.urgences_nouveau, 1)} h/sem (ancienne grille ${nf(G.urgences_actuel, 1)}) · cycle de 2 semaines`;
  afficherEdt();
  $("#rot").textContent = nf(G.rotation, 0);
  $("#explications").innerHTML = G.explications.map((e) => `<li>${esc(e)}</li>`).join("");
  $("#p-marge").value = Math.round(100 * G.params.grille_marge);
  $("#p-debut").value = G.params.grille_periode[0];
  $("#p-fin").value = G.params.grille_periode[1];
  $("#p-quantile").value = G.params.quantile_urgences;
  afficherExplicationsGrille();
  graphiqueDemande();
  graphiqueReserve();
  tableReserve();
  tableDemande();
  const V = G.verification;
  $("#verif-grille").innerHTML = V.ecarts.length
    ? `<span class="ko-txt">⚠ ${V.ecarts.length} écart(s) entre la transcription et le fichier Excel.</span>`
    : `<span class="ok-txt">✓</span> Transcription de l'ancienne grille recoupée avec le fichier Excel : aucun écart de code sur les 25 cases jour × salle. TVO par salle (h/sem, moyenne du cycle) : ` +
      Object.entries(V.tvo_par_salle).map(([s, h]) => `salle ${s} ${nf(h, 2)}`).join(", ") + ".";
}
// fenêtre « comment la grille proposée a été construite » : réglages et résultat courants
function afficherExplicationsGrille() {
  const G = etat.grille, P = G.params;
  const salles = (g) => [...new Set(g.filter((v) => v.chir !== "LIBRE").map((v) => v.salle))].sort();
  $("#eg-debut").textContent = new Date(P.grille_periode[0] + "T12:00:00").toLocaleDateString("fr-FR");
  $("#eg-fin").textContent = new Date(P.grille_periode[1] + "T12:00:00").toLocaleDateString("fr-FR");
  $$(".eg-rot").forEach((el) => { el.textContent = nf(G.rotation, 0); });
  $("#eg-marge").textContent = pct(P.grille_marge);
  $("#eg-qurg").textContent = pct(P.quantile_urgences);
  const joursSur10 = nf(Math.floor(10 * P.quantile_urgences + 1e-9), 0);
  $("#eg-qurg-jours").textContent = joursSur10;
  $("#leg-qurg").textContent = pct(P.quantile_urgences);
  $("#leg-qurg-jours").textContent = joursSur10;
  // l'exclusion des mois COVID (mars à mai 2020) ne change la demande que si la période choisie les recouvre
  const covid = P.grille_periode[0] <= "2020-05-31" && P.grille_periode[1] >= "2020-03-01";
  $("#eg-covid").textContent = covid ? ", hors mars à mai 2020 (COVID-19)" : "";
  // exemple de la réserve : le lundi (chiffres réels de la base)
  const L = G.reserve.find((r) => r.jour === 0);
  if (L) {
    const autres = G.reserve.filter((r) => r.jour > 0 && r.jour < 5).map((r) => `${JOURS[r.jour].toLowerCase()} ${nf(r.reserve_h, 1)} h`).join(", ");
    $("#eg-exemple-reserve").innerHTML = `<ol class="explications" style="max-height:none;margin-top:6px">
      <li>On regarde tous les lundis de 2019 à 2022 où le bloc était ouvert, sauf mars à mai 2020 : <b>${L.n_jours} lundis</b>.</li>
      <li>Pour chacun, on compte les heures de salle prises par les urgences : 0 h un lundi calme, 1 h, 2 h 30, parfois 4 h ou plus.</li>
      <li>On classe ces lundis du plus calme au plus chargé et on prend la valeur qui en laisse ${pct(P.quantile_urgences)} en dessous :
        <b>${nf(L.quantile_h, 2)} h</b> (quantile ${pct(P.quantile_urgences)}).</li>
      <li>On arrondit à la demi-heure supérieure : la réserve du lundi est de <b>${nf(L.reserve_h, 1)} h</b>.</li>
    </ol>
    <p class="legende-texte" style="margin:6px 0 0"><b>Pourquoi pas la moyenne ?</b> Un lundi, les urgences prennent en moyenne ${nf(L.moyenne_h, 1)} h.
      Une réserve de ${nf(L.moyenne_h, 1)} h ${L.part_au_dela_moyenne !== undefined ? `aurait été dépassée ${pct(L.part_au_dela_moyenne)} des lundis` : "serait souvent dépassée"} :
      les urgences auraient alors pris la place d'opérations programmées. Avec ${nf(L.reserve_h, 1)} h, la réserve suffit au moins ${joursSur10} lundis sur 10.</p>
    <p class="aide" style="margin:6px 0 0">Même calcul pour chaque jour : ${autres}.</p>`;
  }
  const nj = G.reserve.filter((r) => r.jour < 5).map((r) => r.n_jours);
  $("#eg-njours").textContent = nj.length ? (Math.min(...nj) === Math.max(...nj) ? `${Math.min(...nj)}` : `${Math.min(...nj)} à ${Math.max(...nj)}`) : "—";
  $("#eg-resultat").innerHTML = `<b>Résultat avec ces réglages :</b> ${G.tableau.length} chirurgiens retenus ;
    offre chirurgicale ${nf(G.total_nouveau, 1)} h/sem (ancienne grille ${nf(G.total_actuel, 1)}) ;
    réserve d'urgences ${nf(G.urgences_nouveau, 1)} h/sem (ancienne grille ${nf(G.urgences_actuel, 1)}) ;
    salles utilisées : ${salles(G.nouvelle).join(", ")} (ancienne grille : ${salles(G.actuelle).join(", ")}).`;
}
function dansSemaine(v, k) {
  return v.semaines.includes(k) || (etat.vueGrille === "nouvelle" && v.semaines.includes(k + 2));
}
function afficherEdt() {
  const G = etat.grille, g = G[etat.vueGrille], k = etat.posCycle;
  const DEB = 7 * 60 + 30, FIN = 17 * 60 + 30, L = FIN - DEB;
  let html = `<div class="edt"><div></div>` + [1, 2, 3, 4, 5].map((s) => `<div class="tete-col">Salle ${s}</div>`).join("");
  for (let j = 0; j < 5; j++) {
    html += `<div class="tete-lig">${JOURS[j]}</div>`;
    for (const s of [1, 2, 3, 4, 5]) {
      const blocs = g.filter((v) => v.jour === j && v.salle === s && dansSemaine(v, k));
      html += `<div><div class="piste ${blocs.length ? "" : "fermee"}" title="${blocs.length ? "" : "Salle fermée"}">` +
        [9, 11, 13, 15, 17].map((hh) => `<div class="repere" style="left:${100 * (hh * 60 - DEB) / L}%"></div>`).join("") +
        blocs.map((v) => {
          const type = v.chir === "URGENCES" ? "urgences" : v.chir === "LIBRE" ? "libre" : "chir";
          const cls = [type, v.source.startsWith("conserv") ? "fige" : ""].join(" ");
          const nom = type === "urgences" ? "⚑ URG." : v.chir;
          return `<div class="bloc ${cls}" style="left:${100 * (v.debut - DEB) / L}%;width:${100 * (v.fin - v.debut) / L}%"
            title="${esc(v.chir)} ${hm(v.debut)}–${hm(v.fin)} · ${esc(v.source || "")}"><b>${esc(nom)}</b><span class="h">${hm(v.debut)}–${hm(v.fin)}</span></div>`;
        }).join("") + `</div></div>`;
    }
  }
  html += `<div></div>` + [1, 2, 3, 4, 5].map(() => `<div class="axe-h"><span>7h30</span><span class="milieu">12h30</span><span>17h30</span></div>`).join("");
  $("#edt").innerHTML = html + `</div>`;
}
function graphiqueDemande() {
  const c = couleurs(), T = etat.grille.tableau;
  const t = [...T].sort((a, b) => b.demande_h_sem - a.demande_h_sem);
  graphe("g-demande", {
    type: "bar",
    data: {
      labels: t.map((x) => x.chir),
      datasets: [
        { label: "Besoin (demande moyenne + marge)", data: t.map((x) => x.besoin_h_sem), backgroundColor: c.s2, borderRadius: 4, barPercentage: 0.85, categoryPercentage: 0.8 },
        { label: "Offre de l'ancienne grille", data: t.map((x) => x.offre_actuelle_h_sem), backgroundColor: c.axe, borderRadius: 4, barPercentage: 0.85, categoryPercentage: 0.8 },
        { label: "Offre proposée", data: t.map((x) => x.offre_proposee_h_sem), backgroundColor: c.s1, borderRadius: 4, barPercentage: 0.85, categoryPercentage: 0.8 },
      ],
    },
    options: {
      indexAxis: "y", interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (x) => `${x.dataset.label} : ${nf(x.parsed.x, 1)} h/sem` } } },
      scales: { x: echelle("heures par semaine", { beginAtZero: true }), y: echelle(null, { grid: { display: false } }) },
    },
  });
}
function graphiqueReserve() {
  const c = couleurs(), R = etat.grille.reserve.slice(0, 7);
  graphe("g-reserve", {
    type: "bar",
    data: {
      labels: R.map((r) => JOURS[r.jour]),
      datasets: [
        { label: "Moyenne des heures d'urgence", data: R.map((r) => r.moyenne_h), backgroundColor: c.axe, borderRadius: 4 },
        { label: "Réserve (quantile arrondi à 0,5 h)", data: R.map((r) => r.reserve_h), backgroundColor: c.critique, borderRadius: 4 },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (x) => `${x.dataset.label} : ${nf(x.parsed.y, 2)} h` } } },
      scales: { x: echelle(null, { grid: { display: false } }), y: echelle("heures", { beginAtZero: true }) },
    },
  });
}
function tableReserve() {
  const R = etat.grille.reserve;
  $("#table-reserve").innerHTML = `<table><thead><tr><th>Jour</th><th class="num">Jours observés</th><th class="num">Jours avec urgence</th><th class="num">Moyenne</th><th class="num">Quantile</th><th class="num">Réserve</th><th class="num">Heure médiane</th><th class="num">Lits / nuit</th></tr></thead><tbody>` +
    R.map((r) => `<tr><td>${JOURS[r.jour]}</td><td class="num">${r.n_jours}</td><td class="num">${pct(r.part_jours_avec_urgence)}</td><td class="num">${nf(r.moyenne_h, 2)} h</td>
      <td class="num">${nf(r.quantile_h, 2)} h</td><td class="num"><b>${nf(Math.max(0, r.reserve_h), 1)} h</b></td><td class="num">${r.heure_mediane ? hm(60 * r.heure_mediane) : "—"}</td><td class="num">${nf(r.lits_moyens, 2)}</td></tr>`).join("") +
    `</tbody></table><p class="aide">Le week-end, les urgences relèvent de l'astreinte (pas de vacation). Les urgences ne comptent pas dans la fonction objectif : elles n'interviennent que dans la simulation.</p>`;
}
function tableDemande() {
  const T = [...etat.grille.tableau].sort((a, b) => b.demande_h_sem - a.demande_h_sem);
  const taux = (x) => x === null ? "—" : `<span class="${x > 1 ? "ko-txt" : ""}">${pct(x)}</span>`;
  $("#table-demande").innerHTML = `<table><thead><tr><th>Chirurgien</th><th class="num">Patients / sem</th><th class="num">Demande moy. (h/sem)</th><th class="num">Besoin</th><th class="num">Offre ancienne grille</th><th class="num">Demande / offre ancienne grille</th><th class="num">Offre proposée</th><th class="num">Demande / offre proposée</th><th class="num">Ambulatoire</th><th class="num">Nuits moy.</th></tr></thead><tbody>` +
    T.map((r) => `<tr><td><b>${esc(r.chir)}</b> ${esc(r.nom)}</td><td class="num">${nf(r.patients_sem, 1)}</td><td class="num">${nf(r.demande_h_sem, 1)}</td><td class="num">${nf(r.besoin_h_sem, 1)}</td>
      <td class="num">${nf(r.offre_actuelle_h_sem, 1)}</td><td class="num">${taux(r.taux_actuel)}</td><td class="num">${nf(r.offre_proposee_h_sem, 1)}</td>
      <td class="num">${taux(r.taux_propose)}</td><td class="num">${pct(r.part_ambu)}</td><td class="num">${nf(r.nuits_moyennes, 1)}</td></tr>`).join("") + `</tbody></table>`;
}

/* =====================================================================
   4. TECHNIQUE
   ===================================================================== */
// Cible du lissage (termes W1 et W1′ de F, critères « lissage » de R) sur le planning adopté, recalculée avec les
// valeurs saisies dans « Hébergement et personnel », avant tout enregistrement : cible(t) = total × E_t / Σ E,
// E_t = 1 en semaine, α les nuits du samedi, du dimanche et des jours fériés ; places : jours d'ouverture (E_t = 1)
function afficherCibleHebergement() {
  const zone = $("#cible-heberg");
  if (!zone) return;
  const v = etat.vueTechnique;
  const somme = (L) => L.reduce((a, b) => a + b, 0);
  if (!v || !v.lits || !(somme(v.lits) > 0)) {
    zone.innerHTML = `<p class="aide">Cible des lits : elle se calcule sur le planning adopté. Adoptez un planning (onglet Planning) pour la voir ici.</p>`;
    return;
  }
  const lire = (cle) => { const el = $(`#p-heberg [data-cle="${cle}"]`); return el && el.value !== "" ? Number(el.value) : NaN; };
  const alpha = lire("alpha");
  if (!(alpha >= 0 && alpha <= 1)) {
    zone.innerHTML = `<p class="aide"><span class="ko-txt">α doit être compris entre 0 et 1</span> pour calculer la cible des lits.</p>`;
    return;
  }
  // nuits à personnel réduit : samedi, dimanche et jours fériés (jours fermés au bloc, E_t ≠ 1 dans le planning adopté)
  const reduite = v.lits.map((_, t) => [0, 6].includes(new Date(v.dates[t]).getUTCDay()) || v.cap_ambu[t] === 0 || v.pers[t] !== 1);
  const nuitsS = reduite.filter((r) => !r).length, nuitsR = reduite.length - nuitsS;
  const total = somme(v.lits), sE = nuitsS + alpha * nuitsR;
  const cS = sE ? total / sE : 0, cR = alpha * cS;
  const moy = (L) => (L.length ? somme(L) / L.length : 0);
  // places : jours d'ouverture seulement, tous en semaine (E_t = 1) : la cible est la même chaque jour, α n'y joue pas
  const ouverts = v.places.map((_, t) => v.cap_ambu[t] > 0);
  const placesO = v.places.filter((_, t) => ouverts[t]);
  const cP = moy(placesO);
  const capS = lire("cap_lits_semaine"), capR = lire("cap_lits_week_end"), capP = lire("cap_ambu");
  const alertes = [[cS, capS, "d'une nuit de semaine", "lits ouverts en semaine"], [cR, capR, "d'une nuit de week-end ou fériée", "lits ouverts ces nuits-là"],
    [cP, capP, "des places", "places ambulatoires"]]
    .filter(([c, cap]) => !Number.isNaN(cap) && c > cap)
    .map(([, , quoi, lim]) => `la cible ${quoi} dépasse les ${lim}`);
  const enregistre = etat.init?.params?.alpha;
  const notes = [enregistre !== undefined && Math.abs(enregistre - alpha) > 1e-9
      ? "Le planning adopté ne change pas avec α : relancez l'optimisation pour qu'il suive la nouvelle cible." : "",
    alertes.length ? `<span class="ko-txt">⚠ ${alertes.join(" ; ")} : le lissage pousse au-delà de la limite, que W<sub>4</sub> pénalise.</span>` : ""]
    .filter(Boolean);
  zone.innerHTML = `<table><caption>Cible du lissage sur le planning adopté (${nf(total, 0)} lits × nuits sur ${reduite.length} nuits), avec α = ${nf(alpha, 2)}.
      Recalculée dès que vous modifiez α, sans enregistrer.</caption>
    <thead><tr><th></th><th class="num">Nuits ou jours</th><th class="num">Cible</th></tr></thead>
    <tbody>
      <tr><td>Lits, nuits de semaine</td><td class="num">${nuitsS}</td><td class="num"><b>${nf(cS, 1)}</b></td></tr>
      <tr><td>Lits, nuits de week-end et fériées</td><td class="num">${nuitsR}</td><td class="num"><b>${nf(cR, 1)}</b></td></tr>
      <tr><td>Places ambulatoires, jours d'ouverture</td><td class="num">${placesO.length}</td><td class="num"><b>${placesO.length ? nf(cP, 1) : "—"}</b></td></tr>
    </tbody></table>${notes.length ? `<p class="aide" style="margin-top:4px">${notes.join("<br>")}</p>` : ""}`;
}

/* ---------------- notations des poids de F (fenêtre « Poids par défaut de F ») ---------------- */
const ORDRE_POIDS = ["W1", "W1b", "W2", "W3", "W4", "W5", "W6"];
const NOMS_POIDS = { W1: "W<sub>1</sub>", W1b: "W<sub>1</sub>′", W2: "W<sub>2</sub>", W3: "W<sub>3</sub>", W4: "W<sub>4</sub>",
  W5: "W<sub>5</sub>", W6: "W<sub>6</sub>" };
const NOMS_POIDS_TEXTE = { W1: "W1", W1b: "W1′", W2: "W2", W3: "W3", W4: "W4", W5: "W5", W6: "W6" };

/* ---------------- poids par défaut de F : optimisation bayésienne sous contraintes (scripts/optimisation_poids.py) ---------------- */
// résultats lus dans app/static/resultats/optimisation_poids/optimisation.json (écrit par le script)
const DOSSIER_OPTIMISATION = "/static/resultats/optimisation_poids/";
// petits poids (0,012, 0,014) : 2 chiffres significatifs plutôt que 2 décimales
const nfSig = (x, n = 2) => Number(x).toLocaleString("fr-FR", { maximumSignificantDigits: n });
// valeur d'une cible pour un jeu mesuré, et son affichage
const valeurCible = (m, c) => m.prevu[c.indicateur] ?? m.execute[c.indicateur];
function texteCible(c, x) {
  if (x === null || x === undefined) return "—";
  if (c.cle === "remplissage" || c.cle === "places_manquantes") return pct(x, c.cle === "remplissage" ? 1 : 0);
  if (c.cle.startsWith("ecart")) return nf(x, 2);
  if (c.cle === "lits_manquants") return nf(x, 2);
  return nf(x, 0);
}
const marqueCible = (ok) => ok ? `<span class="cible-ok">✓</span>` : `<span class="cible-ko">✗</span>`;

async function chargerOptimisation() {
  const zone = $("#optimisation");
  if (!zone) return;
  try {
    const r = await fetch(`${DOSSIER_OPTIMISATION}optimisation.json?v=${Date.now()}`);
    if (!r.ok) throw new Error(r.statusText);
    etat.optimisation = await r.json();
  } catch (e) {
    zone.innerHTML = `<p class="aide">L'optimisation n'a pas encore été lancée : <code>.venv\\Scripts\\python scripts\\optimisation_poids.py</code>
      (environ 8 min), puis rechargez la page.</p>`;
    return;
  }
  etat.choixOptimisation = etat.choixOptimisation || { poids: etat.optimisation.protocole.variables[0] };
  afficherOptimisation();
}

function afficherOptimisation() {
  const O = etat.optimisation, P = O.protocole, ch = etat.choixOptimisation;
  const E = O.essais, ref = E.find((e) => e.phase === "référence"), ret = O.retenu;
  const refV = O.validation.find((v) => v.cle === "reference");
  // poids par défaut : ceux en vigueur s'ils restent les meilleurs en validation (O.defaut.source = "en_vigueur"),
  // sinon les poids retenus arrondis à 2 chiffres et revalidés
  const parDefautEnVigueur = O.defaut?.source === "en_vigueur";
  const def = parDefautEnVigueur ? O.en_vigueur : (O.retenu_arrondi || ret);
  const ecartR = (v) => `${v.ecart_R >= 0 ? "+" : "−"}${nf(Math.abs(v.ecart_R), 1)} ± ${nf(2 * v.erreur_type, 1)}`;
  const date = new Date(O.date.replace(" ", "T")).toLocaleString("fr-FR", { dateStyle: "long", timeStyle: "short" });
  const tousPoids = ORDRE_POIDS.filter((k) => k in O.reference);
  const nomCible = (cle) => O.cibles.find((c) => c.cle === cle).libelle.toLowerCase();
  // poids : barème initial et poids retenus
  const lignesPoids = tousPoids.map((k) => {
    const cherche = P.variables.includes(k), r0 = O.reference[k], r1 = def ? def.poids[k] : null;
    return `<tr><td>${NOMS_POIDS[k]} · ${esc(O.libelles[k])}</td><td class="num">${nfSig(r0)}</td>
      <td class="num"><b>${r1 === null ? "—" : nfSig(r1)}</b></td>
      <td class="num">${!cherche ? `<span class="discret">fixe</span>` : r1 === null ? "—" : "×" + nfSig(r1 / r0)}</td></tr>`;
  }).join("");
  // cibles : barème initial et poids retenus, mesurés sur la validation (autres graines et tirages)
  const lignesCibles = O.cibles.map((c) => {
    const cel = (m) => m ? `${texteCible(c, valeurCible(m, c))} ${marqueCible(m.respecte[c.cle])}` : "—";
    const ret = def;
    return `<tr><td>${esc(c.libelle)}</td><td>${c.mesure}</td><td>${esc(c.cible)}</td>
      <td class="num">${cel(refV)}</td><td class="num">${cel(ret)}</td></tr>`;
  }).join("") + `<tr class="reference"><td>Score de robustesse R</td><td>exécuté</td><td>le plus haut possible</td>
      <td class="num">${refV ? nf(refV.R, 1) : "—"}</td>
      <td class="num">${def ? `${nf(def.R, 1)} <span class="discret">(${ecartR(def)})</span>` : "—"}</td></tr>
    <tr><td>Écart moyen à la date souhaitée <span class="discret">(pas une cible)</span></td><td>prévu</td><td>—</td>
      <td class="num">${refV ? nf(refV.prevu.ecart_moyen_souhait, 1) + " j" : "—"}</td>
      <td class="num">${def ? nf(def.prevu.ecart_moyen_souhait, 1) + " j" : "—"}</td></tr>`;
  // influence : longueurs de corrélation du modèle de R (petite = poids influent)
  const ls = O.longueurs.R;
  const ordreInfluence = P.variables.slice().sort((a, b) => ls[a] - ls[b]);
  const lignesInfluence = ordreInfluence.map((k) => `<tr><td>${NOMS_POIDS[k]} · ${esc(O.libelles[k])}</td><td class="num">${nf(ls[k], 2)}</td></tr>`).join("");
  // validation
  const autresValides = [O.retenu_arrondi, O.en_vigueur && O.en_vigueur.R_tirages ? O.en_vigueur : null].filter(Boolean);
  const lignesValidation = O.validation.concat(autresValides).map((v) => {
    const n = O.cibles.filter((c) => v.respecte[c.cle]).length;
    const marque = def && v.cle === def.cle && v.cle !== "en_vigueur" ? " <b>(par défaut)</b>"
      : !parDefautEnVigueur && ret && v.cle === ret.cle ? " <b>(retenu)</b>" : "";
    const nomRet = ret && O.validation.find((x) => x.cle === ret.cle)?.nom;
    const nom = v.cle === "reference" ? "barème initial" : v.cle === "arrondi" && nomRet ? `${esc(nomRet)} arrondi à 2 chiffres`
      : v.cle === "en_vigueur" ? `<b>${esc(v.nom)}</b>` : esc(v.nom);
    return `<tr${v.cle === "reference" ? ` class="reference"` : ""}><td>${nom}${marque}</td>
      ${P.variables.map((k) => `<td class="num">${nfSig(v.poids[k])}</td>`).join("")}
      <td class="num">${nf(v.R, 1)}</td><td class="num">${v.cle === "reference" ? "—" : ecartR(v)}</td>
      <td class="num">${n} / ${O.cibles.length}</td></tr>`;
  }).join("");
  // ce qu'on en retient (calculé à partir des résultats)
  const manque = Object.entries(O.manques).sort((a, b) => b[1] - a[1]);
  const retient = [];
  retient.push(`<li><b>${O.nb_faisables} jeux sur ${E.length}</b> respectent toutes les cibles${ref.faisable ? ", dont le barème initial"
    : ` ; le barème initial n'en fait pas partie (il manque : ${O.cibles.filter((c) => !ref.respecte[c.cle]).map((c) => c.libelle.toLowerCase()).join(", ")})`}.</li>`);
  if (parDefautEnVigueur && def.faisable) {
    retient.push(`<li><b>Poids par défaut</b> : ${P.variables.map((k) => `${NOMS_POIDS[k]} = ${nfSig(def.poids[k])}`).join(", ")},
      avec W<sub>1</sub> = 1, W<sub>3</sub> = ${nfCourt(O.reference.W3)}, W<sub>4</sub> = ${nfCourt(O.reference.W4)}.
      En validation, ils respectent toutes les cibles ; R = ${nf(def.R, 1)} contre ${nf(refV.R, 1)} pour le barème initial
      (écart ${ecartR(def)}${Math.abs(def.ecart_R) > 2 * def.erreur_type ? ", significatif" : ", dans le bruit des tirages"}).
      C'est le meilleur R en validation parmi les jeux qui respectent toutes les cibles (tableau ci-dessus).</li>`);
  }
  if (ret) {
    const nomRet = O.validation.find((v) => v.cle === ret.cle)?.nom || "";
    if (!parDefautEnVigueur) retient.push(`<li><b>Poids retenus</b> (${esc(nomRet)}, le meilleur R en validation parmi les jeux qui y respectent toutes les cibles), arrondis à
      2 chiffres et devenus les poids par défaut : ${P.variables.map((k) => `${NOMS_POIDS[k]} = ${nfSig(def.poids[k])}`).join(", ")},
      avec W<sub>1</sub> = 1, W<sub>3</sub> = ${nfCourt(O.reference.W3)}, W<sub>4</sub> = ${nfCourt(O.reference.W4)}.
      ${def.faisable ? "Revalidés, ils respectent toutes les cibles" : "Revalidés, ils ne respectent pas toutes les cibles"} ;
      R = ${nf(def.R, 1)} contre ${nf(refV.R, 1)} pour le barème initial
      (écart ${ecartR(def)}${Math.abs(def.ecart_R) > 2 * def.erreur_type ? ", significatif" : ", dans le bruit des tirages"}).</li>`);
    // prix payé : la date souhaitée n'est ni une cible ni dans R
    retient.push(`<li><b>Ce que ces poids sacrifient</b> : la date souhaitée. L'écart moyen passe de ${nf(refV.prevu.ecart_moyen_souhait, 1)} à
      ${nf(def.prevu.ecart_moyen_souhait, 1)} jours par patient : aucune cible ne le protège et R ne le mesure pas, donc la recherche baisse
      W<sub>6</sub> autant qu'elle le peut.</li>`);
    const bord = P.variables.filter((k) => Math.abs(Math.log10(def.poids[k] / O.reference[k])) > P.decades - 0.3);
    if (bord.length) retient.push(`<li><b>À la limite de la plage explorée</b> : ${bord.map((k) => `${NOMS_POIDS[k]} (${"×" + nfSig(def.poids[k] / O.reference[k])}
      le barème)`).join(", ")}. La recherche irait plus loin si on la laissait faire : c'est le signe qu'aucune cible ne retient ce poids.</li>`);
  } else {
    retient.push(`<li><b>Aucun jeu ne respecte toutes les cibles en validation</b> : les poids par défaut restent ceux du barème initial.
      Cible la plus souvent manquée pendant la recherche : ${nomCible(manque[0][0])} (${manque[0][1]} essais sur ${E.length}).</li>`);
  }
  retient.push(`<li><b>Poids les plus influents sur R</b> (longueur de corrélation la plus courte) : ${ordreInfluence.slice(0, 2).map((k) => `${NOMS_POIDS[k]} (${esc(O.libelles[k])})`).join(" puis ")}.</li>`);
  retient.push(`<li><b>Cibles les plus difficiles</b> pendant la recherche : ${manque.filter(([, n]) => n > 0).slice(0, 3).map(([k, n]) => `${nomCible(k)} (manquée ${n} fois)`).join(", ") || "aucune"}.</li>`);
  // tableau complet
  const complet = E.map((e) => `<tr${e.phase === "référence" ? ` class="reference"` : ""}><td class="num">${e.n}</td><td>${e.phase}</td>
      ${P.variables.map((k) => `<td class="num">${nfSig(e.poids[k])}</td>`).join("")}
      <td class="num">${nf(e.R, 1)}</td><td>${e.faisable ? marqueCible(true) : `<span class="discret">${O.cibles.filter((c) => !e.respecte[c.cle]).map((c) => esc(c.libelle.toLowerCase())).join(", ")}</span>`}</td></tr>`).join("");
  const figures = (O.fichiers.figures || []).map((f) => `<a href="${DOSSIER_OPTIMISATION}${f}" target="_blank" rel="noopener">${f}</a>`).join(" · ");
  $("#optimisation").innerHTML = `
    <p class="aide" style="margin:0 0 8px">Calcul du ${date} (${nf(O.duree_min, 0)} min) : ${E.length} jeux de poids évalués, ${P.patients} patients,
      ${P.semaines} semaines, grille ${P.grille === "nouvelle" ? "proposée" : "ancienne"}, ${nf(P.nouveaux_patients, 0)} nouveaux patients par simulation en moyenne.</p>
    <h4>Les poids</h4>
    <div class="table-norm"><table>
      <thead><tr><th>Poids</th><th class="num">Barème initial</th><th class="num">Poids par défaut</th><th class="num">Rapport</th></tr></thead>
      <tbody>${lignesPoids}</tbody></table></div>
    <h4>Les cibles, en validation (graines et tirages jamais vus pendant la recherche)</h4>
    <div class="table-norm"><table>
      <thead><tr><th>Cible</th><th>Mesurée sur</th><th>Valeur à respecter</th><th class="num">Barème initial</th><th class="num">Poids par défaut</th></tr></thead>
      <tbody>${lignesCibles}</tbody></table></div>
    <p class="aide" style="margin:2px 0 0">Écart de R : moyenne, tirage par tirage, de la différence avec le barème initial ± 2 erreurs-types.</p>
    <h4>Convergence</h4>
    <div class="graphe moyen"><canvas id="g-optimisation"></canvas></div>
    <p class="aide">Un point par jeu de poids évalué, dans l'ordre. Bleu : respecte toutes les cibles ; gris : en manque au moins une.
      La courbe orange suit le meilleur R parmi les jeux qui respectent tout ; le trait pointillé est le barème initial.</p>
    <h4>Les essais selon chaque poids</h4>
    <div class="balayage-outils"><div class="segments" id="optimisation-poids">${P.variables.map((k) =>
      `<button data-k="${k}" class="${k === ch.poids ? "actif" : ""}">${NOMS_POIDS[k]} · ${esc(O.libelles[k])}</button>`).join("")}</div></div>
    <div class="graphe moyen"><canvas id="g-optimisation-poids"></canvas></div>
    <p class="aide">Chaque essai placé selon la valeur du poids choisi (échelle logarithmique) ; les trois autres poids varient en même temps.
      Losange : barème initial ; étoile : poids par défaut (mesurés sur les mêmes tirages que les essais).</p>
    <h4>Influence de chaque poids sur R</h4>
    <div class="table-norm"><table>
      <thead><tr><th>Poids</th><th class="num">Longueur de corrélation ℓ (sur u ∈ [0 ; 1])</th></tr></thead>
      <tbody>${lignesInfluence}</tbody></table></div>
    <p class="aide" style="margin:2px 0 0">Lue sur le modèle final de R. Plus ℓ est petite, plus R change vite quand ce poids bouge ;
      ℓ ≥ 1 signifie que R ne dépend presque pas de ce poids sur toute la plage explorée (×0,01 à ×100).</p>
    <h4>Validation</h4>
    <div class="table-norm"><table>
      <thead><tr><th>Jeu de poids</th>${P.variables.map((k) => `<th class="num">${NOMS_POIDS[k]}</th>`).join("")}<th class="num">R</th>
        <th class="num">Écart avec le barème</th><th class="num">Cibles respectées</th></tr></thead>
      <tbody>${lignesValidation}</tbody></table></div>
    <h4>Ce qu'on en retient</h4>
    <ul class="explications" style="max-height:none">${retient.join("")}</ul>
    <details class="replie" style="margin-top:12px"><summary>Tableau complet des ${E.length} essais</summary>
      <div class="table-norm"><table>
        <thead><tr><th class="num">Essai</th><th>Phase</th>${P.variables.map((k) => `<th class="num">${NOMS_POIDS[k]}</th>`).join("")}
          <th class="num">R</th><th>Cibles manquées</th></tr></thead>
        <tbody>${complet}</tbody></table></div></details>
    <h4>Fichiers</h4>
    <ul class="explications liens-fichiers" style="max-height:none">
      <li>Dossier du site : <code>app/static/resultats/optimisation_poids/</code> ·
        <a href="${DOSSIER_OPTIMISATION}${O.fichiers.csv}" download>${O.fichiers.csv}</a> (tableur, séparateur « ; ») ·
        <a href="${DOSSIER_OPTIMISATION}optimisation.json" target="_blank" rel="noopener">optimisation.json</a></li>
      ${figures ? `<li>Figures pour la présentation : ${figures}</li>` : ""}
      <li>Relancer : <code>.venv\\Scripts\\python scripts\\optimisation_poids.py${P.nb_total !== 101 ? ` --essais ${P.nb_total}` : ""}</code> (environ ${nf(Math.max(1, O.duree_min), 0)} min), puis recharger la page.</li>
    </ul>`;
  $$("#optimisation-poids button").forEach((b) => b.addEventListener("click", () => { ch.poids = b.dataset.k; afficherOptimisation(); }));
  grapheConvergence();
  grapheOptimisationPoids();
}

function grapheConvergence() {
  const O = etat.optimisation, E = O.essais, c = couleurs();
  let best = null;
  const courbe = E.map((e) => { if (e.faisable) best = best === null ? e.R : Math.max(best, e.R); return { x: e.n, y: best }; });
  const ref = E.find((e) => e.phase === "référence");
  const nuage = (ok, coul, label) => ({ label, data: E.filter((e) => e.faisable === ok).map((e) => ({ x: e.n, y: e.R })),
    backgroundColor: coul, borderColor: c.surface, borderWidth: 1, pointRadius: ok ? 5 : 4, pointHoverRadius: 7 });
  graphe("g-optimisation", {
    type: "scatter",
    data: { datasets: [
      nuage(false, c.muet, "ne respecte pas toutes les cibles"),
      nuage(true, c.s1, "respecte toutes les cibles"),
      { type: "line", label: "meilleur R respectant les cibles", data: courbe, stepped: "after", borderColor: c.s2, pointRadius: 0, spanGaps: false },
      { type: "line", label: "barème initial", data: [{ x: 1, y: ref.R }, { x: E.length, y: ref.R }], borderColor: c.texte2,
        borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0 },
    ] },
    options: {
      plugins: { legend: { position: "bottom" },
        tooltip: { callbacks: { label: (x) => `${x.dataset.label} : essai ${x.parsed.x}, R ${nf(x.parsed.y, 1)}` } } },
      scales: { x: echelle("essai (jeu de poids évalué)", { min: 0, max: E.length + 1, grid: { display: false } }),
                y: echelle("score de robustesse R") },
    },
  });
}

function grapheOptimisationPoids() {
  const O = etat.optimisation, E = O.essais, k = etat.choixOptimisation.poids, c = couleurs();
  const nuage = (ok, coul, label) => ({ label, data: E.filter((e) => e.faisable === ok).map((e) => ({ x: e.poids[k], y: e.R, n: e.n })),
    backgroundColor: coul, borderColor: c.surface, borderWidth: 1, pointRadius: ok ? 5 : 4, pointHoverRadius: 7 });
  const ref = E.find((e) => e.phase === "référence");
  const datasets = [nuage(false, c.muet, "ne respecte pas toutes les cibles"), nuage(true, c.s1, "respecte toutes les cibles"),
    { label: "barème initial", data: [{ x: ref.poids[k], y: ref.R, n: ref.n }], backgroundColor: c.texte, borderColor: c.surface,
      borderWidth: 2, pointStyle: "rectRot", pointRadius: 9, pointHoverRadius: 11 }];
  // étoile : poids par défaut ; ceux en vigueur sont mesurés sur les mêmes tirages que la recherche (O.en_vigueur.recherche)
  const enVigueur = O.defaut?.source === "en_vigueur" && O.en_vigueur?.recherche;
  const essaiRetenu = !enVigueur && O.retenu && E.find((e) => e.cle === O.retenu.cle);
  const etoile = enVigueur ? { x: O.en_vigueur.poids[k], y: O.en_vigueur.recherche.R }
    : essaiRetenu ? { x: essaiRetenu.poids[k], y: essaiRetenu.R, n: essaiRetenu.n } : null;
  // l'étoile de Chart.js n'est dessinée que par son contour : contour de la couleur de l'étoile
  if (etoile) datasets.push({ label: enVigueur ? "poids par défaut" : "poids retenus", data: [etoile],
    backgroundColor: c.s2, borderColor: c.s2, borderWidth: 3, pointStyle: "star", pointRadius: 12, pointHoverRadius: 14 });
  graphe("g-optimisation-poids", {
    type: "scatter", data: { datasets },
    options: {
      plugins: { legend: { position: "bottom" },
        tooltip: { callbacks: { label: (x) => `${x.dataset.label} : ${x.raw.n ? `essai ${x.raw.n}, ` : ""}${NOMS_POIDS_TEXTE[k]} = ${nfSig(x.parsed.x)}, R ${nf(x.parsed.y, 1)}` } } },
      scales: { x: echelle(`${NOMS_POIDS_TEXTE[k]} · ${O.libelles[k]} (échelle logarithmique)`, { type: "logarithmic",
                  ticks: { callback: (v) => { const l = Math.log10(v); return Math.abs(l - Math.round(l)) < 1e-6 ? nfSig(v) : ""; } } }),
                y: echelle("score de robustesse R") },
    },
  });
}

const CHAMPS = {
  // grille de 4 colonnes : W1, W1′, W2, W3 ; puis W4, W5, W6 ; puis guidage et marge d'aléa (nouvelle_ligne : 1re colonne)
  "p-poids": [["poids.W1", "W1 · lits"], ["poids.W1b", "W1′ · places"], ["poids.W2", "W2 · regroupement (vacations vides)"], ["poids.W3", "W3 · retard"],
    ["poids.W4", "W4 · dépassement hébergement"], ["poids.W5", "W5 · marge de salle"], ["poids.W6", "W6 · écart à la date souhaitée"],
    ["epsilon_salles", `ε · guidage de W2<span class="discret" style="display:block;font-weight:400;font-size:11.5px;line-height:1.3">plus haut : pousse davantage à regrouper les patients pour vider des vacations</span>`, null, { nouvelle_ligne: true }],
    ["z_beta", `z<sub>β</sub> (marge d'aléa)<span class="discret" style="display:block;font-weight:400;font-size:11.5px;line-height:1.3">plus haut : moins de dépassements, salles moins remplies</span>`],
    ["penaliser_capacites", `Pénaliser les capacités<span class="discret" style="display:block;font-weight:400;font-size:11.5px;line-height:1.3">cochée : F compte les termes W4 (lits et places ambulatoires au-delà des capacités) et W5 (vacations qui risquent de déborder, marge z<sub>β</sub>) ; décochée : ces deux termes valent 0 et seules les autres pénalités guident le planning</span>`, "case", { nouvelle_ligne: true }]],
  "p-heberg": [["alpha", "α : part du personnel infirmier le week-end (0 à 1)"], ["cap_lits_semaine", "Lits (semaine)"], ["cap_lits_week_end", "Lits (week-end et jours fériés)"],
    ["cap_ambu", "Places ambulatoires"],
    ["rotation_dans_mu", `Rotation ajoutée à µ (min)<span class="discret" style="display:block;font-weight:400;font-size:11.5px;line-height:1.3">seul réglage de la rotation entre deux patients ; à 0, la rotation mesurée (12 min) est ajoutée entre deux patients</span>`]],
  "p-delais": [["delai_medical", "Délai médical maximal (j après la demande)"], ["tolerance", `Tolérance (j)<span class="discret" style="display:block;font-weight:400;font-size:11.5px;line-height:1.3">jours après la date souhaitée sans pénalité</span>`],
    ["avance_max", "Avance max sur la date souhaitée (j)"],
    ["retard_max", "Report max après la tolérance (j)"],
    ["delai_preparation", "Délai de préparation<br>(j après la demande)"], ["semaines_horizon", "Horizon (semaines)"], ["date_ouverture", "Ouverture du bloc", "date"]],
};
const lireCle = (o, cle) => cle.split(".").reduce((a, k) => a?.[k], o);
function afficherParametres() {
  const P = etat.init.params;
  for (const [zone, champs] of Object.entries(CHAMPS)) {
    $("#" + zone).innerHTML = champs.map(([cle, lib, type, o]) => {
      const ligne = o?.nouvelle_ligne ? " nouvelle-ligne" : "";
      return type === "case"
        ? `<label class="case${ligne}"><input type="checkbox" data-cle="${cle}" ${lireCle(P, cle) ? "checked" : ""}> <span>${lib}</span></label>`
        : `<label${ligne ? ` class="${ligne.trim()}"` : ""}><span>${lib}</span><input data-cle="${cle}" type="${type || "number"}" step="any" value="${esc(lireCle(P, cle))}"></label>`;
    }).join("");
  }
  const chap = etat.init.chapitres;
  $("#t-chapitres").innerHTML = `<table><thead><tr><th>Chap.</th><th>Libellé</th><th class="num">Séjours</th><th>w</th></tr></thead><tbody>` +
    chap.map((c) => `<tr><td><b>${esc(c.code)}</b></td><td>${esc(c.libelle)}</td><td class="num">${c.n}</td>
      <td><input type="number" step="0.1" min="0" data-chap="${esc(c.code)}" value="${P.poids_chapitres[c.code] ?? 1}"></td></tr>`).join("") + `</tbody></table>`;
  $("#t-codes").innerHTML = `<table><thead><tr><th>Code</th><th>Libellé</th><th class="num">Séjours</th><th>w</th></tr></thead><tbody>` +
    etat.init.cim.slice(0, 60).map((c) => `<tr><td><b>${esc(c.code)}</b></td><td>${esc(c.libelle)}</td><td class="num">${c.n}</td>
      <td><input type="number" step="0.1" min="0" data-code="${esc(c.code)}" placeholder="${P.poids_chapitres[c.code[0]] ?? 1}" value="${P.poids_codes[c.code] ?? ""}"></td></tr>`).join("") + `</tbody></table>`;
  afficherCibleHebergement();
  $("#o-grille").value = P.grille;
  majAvertissementGrille();
  $("#o-instance").textContent = `la liste des patients initiaux (${P.initial_nb_patients} générés sur ${P.instance_semaines} semaines, modifiable)`;
}
// avertissement rouge sous le choix de la grille (Technique) : les métaheuristiques tourneraient sur l'ancienne grille
function majAvertissementGrille() {
  $("#avert-grille").hidden = $("#o-grille").value !== "actuelle";
}
async function sauverParametres(ev) {
  const msg = ev?.currentTarget?.closest(".actions-bas")?.querySelector(".msg-params") || $("#msg-params");
  const corps = {};
  $$("[data-cle]").forEach((el) => {
    const v = el.type === "checkbox" ? el.checked : el.type === "date" ? el.value : Number(el.value);
    const [a, b] = el.dataset.cle.split(".");
    if (b) { corps[a] = corps[a] || {}; corps[a][b] = v; } else corps[a] = v;
  });
  try {
    etat.init.params = await api("/api/parametres", { method: "POST", body: corps });
    $$(".msg-params").forEach((el) => (el.textContent = ""));
    msg.textContent = "Enregistré. Relancez une comparaison pour voir l'effet.";
    toast("Paramètres enregistrés.");
  } catch (e) { toast(e.message, true); }
}
// « Valeurs par défaut » d'une carte de l'onglet Technique : seuls les champs de ses zones (CHAMPS) reprennent leur valeur
// par défaut et sont enregistrés ; les autres réglages, y compris ceux en cours de saisie dans l'autre carte, ne changent pas
async function defautSection(zones, question, ev) {
  const msg = ev?.currentTarget?.closest(".actions-bas")?.querySelector(".msg-params");
  if (!confirm(question)) return;
  const D = etat.init.params_defaut, corps = {};
  for (const z of zones) for (const [cle] of CHAMPS[z]) {
    const [a, b] = cle.split(".");
    if (b) { corps[a] = corps[a] || {}; corps[a][b] = D[a][b]; } else corps[a] = D[a];
  }
  try {
    etat.init.params = await api("/api/parametres", { method: "POST", body: corps });
    for (const z of zones) for (const [cle] of CHAMPS[z]) {
      const el = $(`#${z} [data-cle="${cle}"]`), v = lireCle(etat.init.params, cle);
      if (!el) continue;
      if (el.type === "checkbox") el.checked = !!v; else el.value = v;
    }
    afficherCibleHebergement();
    $$(".msg-params").forEach((el) => (el.textContent = ""));
    if (msg) msg.textContent = "Valeurs par défaut rétablies et enregistrées.";
    toast("Valeurs par défaut rétablies.");
  } catch (e) { toast(e.message, true); }
}
async function sauverPoidsDiagnostic() {
  const chap = {}, codes = {};
  $$("[data-chap]").forEach((el) => (chap[el.dataset.chap] = Number(el.value)));
  $$("[data-code]").forEach((el) => { if (el.value !== "") codes[el.dataset.code] = Number(el.value); });
  // les codes non listés gardent leur réglage
  for (const [k, v] of Object.entries(etat.init.params.poids_codes)) if (!$(`[data-code="${k}"]`)) codes[k] = v;
  try {
    etat.init.params = await api("/api/parametres", { method: "POST", body: { poids_chapitres: chap, poids_codes: codes, remplacer: ["poids_codes"] } });
    $("#msg-wi").textContent = "Poids w_i enregistrés (appliqués aussi aux patients déjà programmés).";
    toast("Poids de retard enregistrés.");
  } catch (e) { toast(e.message, true); }
}
// vue du planning adopté (lits et places par nuit, personnel E_t) pour la cible du lissage (« Hébergement et personnel »)
async function chargerVueTechnique() {
  try { etat.vueTechnique = await api("/api/planning"); } catch (e) { etat.vueTechnique = null; }
  afficherCibleHebergement();
}
async function chargerTechnique() {
  afficherParametres();
  chargerVueTechnique();
  afficherHoraires();
  chargerOptimisation();
  if (!etat.analyse) {
    try { etat.analyse = await api("/api/analyse"); } catch (e) { toast(e.message, true); return; }
  }
  afficherAnalyse();
}
function afficherAnalyse() {
  const A = etat.analyse;
  const lignes = [["mae_duree", "Durée opératoire (MAE, min)", 1], ["mae_nuits", "Nombre de nuits (MAE)", 2], ["precision_ambu", "Ambulatoire (bonnes réponses)", -1]];
  const meth = ["Moyenne globale", "Étude statistique", "Étude statistique sans COVID", "Machine learning", "Machine learning sans COVID"];
  const f = (x, d) => d < 0 ? pct(x, 1) : nf(x, d);
  const meilleur = (k, d) => { const v = meth.slice(1).map((m) => A.ml[m][k]); return d < 0 ? Math.max(...v) : Math.min(...v); };
  const n = A.ml._apprentissage || {};
  $("#mois-covid").textContent = A.mois_covid;
  $("#table-ml").innerHTML = `<div style="overflow-x:auto"><table><thead><tr><th>Erreur sur 2022</th>${meth.map((m) => `<th class="num">${m}</th>`).join("")}</tr></thead><tbody>` +
    lignes.map(([k, lib, d]) => `<tr><td>${lib}</td>${meth.map((m) => {
      const x = A.ml[m][k];
      return `<td class="num">${m !== "Moyenne globale" && x === meilleur(k, d) ? `<b>${f(x, d)}</b>` : f(x, d)}</td>`;
    }).join("")}</tr>`).join("") +
    `</tbody></table></div><p class="aide">En gras : la meilleure méthode pour chaque erreur. Apprentissage : ${(n["avec COVID"] || 0).toLocaleString("fr-FR")} séjours
    avec les mois COVID, ${(n["sans COVID"] || 0).toLocaleString("fr-FR")} sans ; test : ${(n["test 2022"] || 0).toLocaleString("fr-FR")} séjours de 2022.
    Les nuits sont prédites en deux temps : ambulatoire ou non, puis nombre de nuits des patients hospitalisés.</p>`;
  afficherModeleActif();
  const U = A.urgences;
  const repartition = Object.entries(U.distribution_score).map(([k, n]) => [Number(k), n]).sort((a, b) => a[0] - b[0]);
  // exemples vérifiés sur la base (ligamentoplasties S83 : 11 sur 13 programmées ; réparations de tendon S66 : 4 sur 4 urgences)
  const exemples = [
    ["Fracture de jambe (S82) opérée par ostéosynthèse, un mardi à 10 h", "2 + 2 = 4", true],
    ["Lésion d'un tendon de la main (S66), réparation du tendon", "2 + 1 = 3", true],
    ["Ostéosynthèse sans diagnostic traumatique, un lundi à 17 h", "2 + 1 = 3", true],
    ["Rupture du ligament croisé (S83), ligamentoplastie, un mercredi matin", "2", false],
    ["Acte de la main sans diagnostic traumatique, un samedi", "1 + 1 = 2", false],
    ["Hernie discale (M51) opérée un samedi", "1", false],
  ];
  $("#tri-urgences").innerHTML = `<p class="legende-texte">La base ne dit pas quels séjours étaient des urgences : on les reconstitue avec un score.
    Le <b>diagnostic</b> et l'<b>acte</b> sont les critères essentiels, le <b>contexte</b> ne fait que confirmer.</p>
    <div class="tuiles">
      ${tuile("Urgences reconstituées", U.n_urgences, pct(U.part_urgences, 1) + " des séjours")}
      ${tuile("Heure médiane d'entrée", hm(60 * U.heure_mediane_urgences), "programmés : " + hm(60 * U.heure_mediane_programmes))}
      ${tuile("Urgences hospitalisées", pct(U.part_hospitalises))}</div>
    <h3>Score d'un séjour</h3>
    <div class="tri-table"><table>
      <thead><tr><th>Critère</th><th>Indice</th><th class="num">Points</th></tr></thead><tbody>
      <tr><td class="critere">Diagnostic</td><td>CIM-10 en S (lésion traumatique) ou T00 à T14 (traumatisme de siège non précisé)</td><td class="num">2</td></tr>
      <tr><td class="critere" rowspan="2">Acte</td><td>Traumatologie : ostéosynthèse, réduction, fracture, luxation, embrochage, plaie (sauf ablation de matériel)</td><td class="num">2</td></tr>
      <tr><td>Main, tendon, doigt, si ce n'est pas déjà un acte de traumatologie (souvent programmé : canal carpien, Dupuytren…)</td><td class="num">1</td></tr>
      <tr><td class="critere">Contexte</td><td>Intervention le week-end ou entrée en salle à 16 h ou plus (un seul point si les deux sont vrais)</td><td class="num">1 au plus</td></tr>
      <tr class="seuil"><td colspan="2">Urgence si le total atteint</td><td class="num">3</td></tr>
    </tbody></table></div>
    <p class="aide">Le contexte seul (1 point) ne suffit donc jamais, ni un acte de la main sans diagnostic traumatique (2 points au plus).
    Avant la revue avec le chirurgien, le week-end valait 3 points et suffisait à lui seul.
    « Admission le jour même » n'est pas retenu : 82 % des hospitalisés programmés entrent aussi le jour même.</p>
    <details class="fenetre" id="fenetre-tri-exemples">
      <summary><span class="quand-ferme">Exemples et résultats sur la base</span><span class="quand-ouvert">Masquer</span>
        <span class="discret">— exemples de séjours, répartition des scores, indices relevés chez les urgences</span></summary>
      <div class="contenu">
    <h3>Exemples</h3>
    <div class="tri-table"><table>
      <thead><tr><th>Séjour</th><th class="num">Points</th><th>Classé</th></tr></thead><tbody>
      ${exemples.map(([sej, pts, urg]) => `<tr><td>${sej}</td><td class="num">${pts}</td>
        <td>${urg ? `<span class="ko-txt">urgence</span>` : `<span class="discret">programmé</span>`}</td></tr>`).join("")}
    </tbody></table></div>
    <h3>Répartition des scores dans la base</h3>
    <div class="tri-table"><table class="tri-repartition">
      <thead><tr><th>Score</th>${repartition.map(([k]) => `<th class="num${k >= 3 ? " urg" : ""}">${k}</th>`).join("")}</tr></thead>
      <tbody><tr><td>Séjours</td>${repartition.map(([k, n]) => `<td class="num${k >= 3 ? " urg" : ""}">${nf(n, 0)}</td>`).join("")}</tr></tbody>
    </table></div>
    <p class="aide">Colonnes colorées : score de 3 ou plus, soit ${nf(U.n_urgences, 0)} urgences sur ${nf(repartition.reduce((a, [, n]) => a + n, 0), 0)} séjours.</p>
    <h3>Indices relevés chez les urgences</h3>
    <table><thead><tr><th>Indices les plus fréquents</th><th class="num">Séjours</th></tr></thead><tbody>` +
    Object.entries(U.motifs_frequents).map(([m, n]) => `<tr><td>${esc(m)}</td><td class="num">${n}</td></tr>`).join("") + `</tbody></table>
      </div>
    </details>`;
}

const CHAMPS_HORAIRES = [
  ["poids_ambu", "p<sub>ambu</sub> : ambulatoires tôt"], ["poids_alternance", "p<sub>alternance</sub> : alternance lourde / légère"],
  ["seuil_lourde", "Seuil d'une opération lourde (min)"],
  ["sas_avec_bloc", "Temps de SAS avec bloc locorégional (min)"], ["sas_sans_bloc", "Temps de SAS sans bloc locorégional (min)"],
  ["accueil_ambu", "Accueil avant le SAS, ambulatoire (min)"], ["accueil_hospit", "Accueil avant le SAS, hospitalisé (min)"],
  ["convocation_min", "Convocation au plus tôt", "time"],
];
function afficherHoraires() {
  const H = etat.init.params.horaires || {};
  $("#p-horaires").innerHTML = CHAMPS_HORAIRES.map(([cle, lib, type]) =>
    `<label><span>${lib}</span><input data-horaire="${cle}" type="${type || "number"}" step="any" value="${esc(H[cle] ?? "")}"></label>`).join("");
  $("#h-reordonner").checked = H.reordonner !== false;
  chargerExempleHoraires();
}
async function chargerExempleHoraires() {
  try {
    const { vacation: v, composition: c } = await api("/api/horaires/exemple");
    if (!v) { $("#exemple-horaires").innerHTML = `<p class="discret">Le planning est vide : calculez d'abord le planning d'ouverture.</p>`; return; }
    const s = (n) => (n > 1 ? "s" : "");   // accord au pluriel
    const compo = `${c.ambulatoires} ambulatoire${s(c.ambulatoires)}, ${c.hospitalises} hospitalisé${s(c.hospitalises)} · ` +
      `${c.lourdes} opération${s(c.lourdes)} lourde${s(c.lourdes)}, ${c.legeres} légère${s(c.legeres)}`;
    $("#exemple-horaires").innerHTML = `<p class="legende-texte">${dateLongue(v.date)}, salle ${v.salle}, ${esc(v.chir)} (${hm(v.debut)} – ${hm(v.fin)}) : ${compo}.
      ${c.complet ? "" : `<br><span class="discret">Aucune vacation du planning ne mélange encore ambulatoires et hospitalisés, opérations lourdes et légères : voici la plus variée.</span>`}</p>
      <div style="overflow-x:auto"><table><thead><tr><th class="num">Rang</th><th>Convocation</th><th>SAS</th><th>Entrée en salle</th><th>Sortie</th>
      <th>Intervention</th><th class="num">Durée</th><th>Hébergement</th><th>Catégorie</th></tr></thead><tbody>` +
      v.patients.map((p) => { const h = p.horaire; return `<tr><td class="num">${h.rang}</td><td>${h.convocation}</td><td>${h.sas}</td><td><b>${h.entree}</b></td>
        <td>${h.sortie}${h.depasse ? ` <span class="ko-txt">⚠</span>` : ""}</td><td>${esc(p.type_interv)}</td><td class="num">${p.duree} min</td>
        <td>${p.ambu ? "ambulatoire" : p.nuits + " nuit(s)"}</td><td>${h.categorie}</td></tr>`; }).join("") + `</tbody></table></div>`;
  } catch (e) { $("#exemple-horaires").innerHTML = `<p class="ko-txt">${esc(e.message)}</p>`; }
}
async function sauverHoraires(defaut = false) {
  const h = {};
  $$("[data-horaire]").forEach((el) => (h[el.dataset.horaire] = el.type === "time" ? el.value : Number(el.value)));
  h.reordonner = $("#h-reordonner").checked;
  try {
    etat.init.params = await api("/api/parametres", { method: "POST", body: defaut ? { horaires_defaut: true } : { horaires: h } });
    afficherHoraires();
    $("#msg-horaires").textContent = defaut ? "Valeurs par défaut rétablies."
      : h.reordonner ? "Enregistré : les horaires du planning sont recalculés."
      : "Enregistré : les horaires déjà attribués sont figés, les nouveaux patients passent après eux.";
  } catch (e) { toast(e.message, true); }
}

function afficherModeleActif() {
  const P = etat.init.params;
  $$("#choix-methode button").forEach((b) => b.classList.toggle("actif", b.dataset.m === P.methode_prediction));
  $$("#choix-covid button").forEach((b) => b.classList.toggle("actif", Number(b.dataset.c) === (P.exclure_covid ? 1 : 0)));
  $("#modele-actif").textContent = `Modèle actif : ${P.methode_prediction === "ml" ? "machine learning" : "étude statistique"}, ` +
    `appris sur 2019-2022 ${P.exclure_covid ? "sans les mois de " + (etat.analyse ? etat.analyse.mois_covid : "COVID") : "en gardant les mois COVID"}.`;
}
async function choisirModele(corps) {
  try {
    etat.init.params = await api("/api/parametres", { method: "POST", body: corps });
    afficherModeleActif();
    etat.initiaux = null;
    toast($("#modele-actif").textContent);
  } catch (e) { toast(e.message, true); }
}
function formuleEnLatex() {
  if (typeof katex === "undefined") return;   // hors ligne : on garde le texte brut
  $$(".formule-tex").forEach((el) => {
    try { katex.render(el.dataset.tex, el, { displayMode: true, throwOnError: false }); } catch (e) { el.textContent = el.dataset.tex; }
  });
}

/* ----- comparaison des métaheuristiques et de leurs hybrides ----- */
function libelleCapacites(s) {
  if (s.capacites_respectees) return `<span class="marque-ok">oui</span>`;
  // les deux ressources sont toujours citées, même à 0, pour qu'on sache laquelle déborde
  const lits = s.nuits_surcapacite ? `${s.nuits_surcapacite} nuit(s) au-delà` : "respectés";
  const ambu = s.jours_ambu_surcapacite ? `${s.jours_ambu_surcapacite} jour(s) au-delà` : "respectées";
  return `<span class="marque-ko">non</span><br><span class="discret">lits : ${lits}<br>places ambu. : ${ambu}</span>`;
}
const METHODES_OPTIM = ["recuit", "tabou", "genetique", "relais_recuit_tabou", "relais_genetique_recuit",
  "relais_genetique_tabou", "memetique", "recuit_liste_tabou"];
function lireParamsAlgos() {
  const part = (id) => Number($(id).value) / 100;
  return {
    recuit: { acceptation_initiale: Number($("#rs-acc").value), ratio_T_finale: Number($("#rs-ratio").value), p_echange: Number($("#rs-ech").value) },
    tabou: { taille_voisinage: Number($("#tb-k").value), duree_tabou: [Number($("#tb-min").value), Number($("#tb-max").value)] },
    genetique: { taille_population: Number($("#ag-pop").value), p_croisement: Number($("#ag-pc").value), taille_tournoi: Number($("#ag-tournoi").value), croisement: $("#ag-crois").value },
    relais_recuit_tabou: { part: part("#hy-rt") },
    relais_genetique_recuit: { part: part("#hy-gr") },
    relais_genetique_tabou: { part: part("#hy-gt") },
    memetique: { taille_population: Number($("#hy-pop").value), budget_local: Number($("#hy-local").value) },
  };
}
async function lancerOptimisation() {
  const algos = METHODES_OPTIM.filter((a) => $("#a-" + a).checked);
  if (!algos.length) { toast("Cochez au moins une méthode.", true); return; }
  const corps = {
    algos, budget: Number($("#o-budget").value), nb_graines: Number($("#o-graines").value), graine: Number($("#o-graine").value),
    depart: $("#o-depart").value,
    instance: { grille: $("#o-grille").value },
    params_algos: lireParamsAlgos(),
  };
  $("#btn-optimiser").disabled = true;
  $("#progression").hidden = false;
  try {
    const { job } = await api("/api/optimiser", { method: "POST", body: corps });
    while (true) {
      await new Promise((r) => setTimeout(r, 600));
      const j = await api("/api/job/" + job);
      $("#barre-prog").style.width = (100 * (j.progression || 0)) + "%";
      $("#msg-prog").textContent = j.message || "";
      if (j.etat === "termine") { etat.resultats = j.resultat; afficherResultats(j.resultat); break; }
      if (j.etat === "erreur") throw new Error(j.message);
    }
  } catch (e) { toast(e.message, true); }
  $("#btn-optimiser").disabled = false;
  setTimeout(() => ($("#progression").hidden = true), 1200);
}
// La couleur suit la méthode (jamais son rang). Hybrides en relais : tirets ; hybrides intégrés :
// pointillés dans la couleur de la méthode qui les accueille (recuit, génétique).
const STYLE_ALGO = {
  recuit: { c: "s1" }, tabou: { c: "s2" }, genetique: { c: "s3" },
  relais_recuit_tabou: { c: "s5", trait: "tirets" }, relais_genetique_recuit: { c: "s6", trait: "tirets" },
  relais_genetique_tabou: { c: "s7", trait: "tirets" },
  memetique: { c: "s3", trait: "points" }, recuit_liste_tabou: { c: "s1", trait: "points" },
  glouton: { c: "s4" }, aleatoire: { c: "muet" },
};
const TRAITS = { tirets: [6, 4], points: [2, 3] };
function styleAlgo(a, c) {
  const s = STYLE_ALGO[a] || { c: "muet" };
  // fond transparent : la case de légende montre alors le trait (tirets, pointillés)
  return { borderColor: c[s.c], backgroundColor: s.trait ? "transparent" : c[s.c], borderDash: s.trait ? TRAITS[s.trait] : [] };
}
function pastilleAlgo(a, c) {
  const s = STYLE_ALGO[a] || { c: "muet" };
  return `<i class="pastille ${s.trait || ""}" style="border-color:${c[s.c]};${s.trait ? "" : "background:" + c[s.c]}"></i>`;
}
const FAMILLES_OPTIM = [["seule", "Méthodes seules"], ["relais", "Hybrides en relais"], ["integre", "Hybrides intégrés"],
  ["reference", "Références (sans métaheuristique)"]];
function detailMethode(s) {
  const p = s.parametres || {};
  if (s.famille === "relais" && p.etapes) return p.etapes.join(" → ");
  if (s.algo === "memetique") return `population ${p.taille_population} · tabou de ${nf(p.budget_local, 0)} évaluations par enfant`;
  if (s.algo === "recuit_liste_tabou" && p.duree_tabou) return `retour interdit ${p.duree_tabou[0]} à ${p.duree_tabou[1]} mouvements`;
  return "";
}
function bilanComparaison(S, nbGraines) {
  const tri = S.filter((s) => s.famille !== "reference").sort((a, b) => a.F_moyen - b.F_moyen);
  const seules = tri.filter((s) => s.famille === "seule");
  const hybrides = tri.filter((s) => s.famille === "relais" || s.famille === "integre");
  let txt = `<b>Meilleure méthode en moyenne : ${esc(tri[0].nom)}</b> (F moyen ${nf(tri[0].F_moyen, 0)}).`;
  if (seules.length && hybrides.length) {
    const h = hybrides[0], m = seules[0];
    const ecart = (m.F_moyen - h.F_moyen) / m.F_moyen;      // > 0 : l'hybride fait mieux
    const bruit = Math.max(h.F_ecart_type, m.F_ecart_type);
    txt += ` Meilleur hybride : <b>${esc(h.nom)}</b> (${nf(h.F_moyen, 0)}) ; meilleure méthode seule : <b>${esc(m.nom)}</b> (${nf(m.F_moyen, 0)}).
      L'hybride fait <b>${pct(Math.abs(ecart), 1)} ${ecart >= 0 ? "mieux" : "moins bien"}</b>`;
    if (nbGraines < 2) txt += " (une seule graine : lancez-en plusieurs pour savoir si l'écart est significatif).";
    else if (Math.abs(m.F_moyen - h.F_moyen) < bruit) txt += `, mais l'écart est plus petit que la variation entre graines (± ${nf(bruit, 0)}) : il n'est pas significatif.`;
    else txt += `, un écart plus grand que la variation entre graines (± ${nf(bruit, 0)}).`;
  }
  return txt;
}
function afficherResultats(R) {
  const c = couleurs(), I = R.instance;
  const S = R.synthese;
  const meilleur = R.meilleur.algo;
  const rang = {};
  S.filter((s) => s.famille !== "reference").sort((a, b) => a.F_moyen - b.F_moyen).forEach((s, k) => (rang[s.algo] = k + 1));
  const ligne = (s) => {
    const methode = s.famille !== "reference";
    const detail = detailMethode(s);
    return `<tr class="${s.algo === meilleur ? "meilleur" : ""}">
      <td class="num">${rang[s.algo] || "—"}</td>
      <td>${pastilleAlgo(s.algo, c)}<b>${esc(s.nom)}</b>${detail ? `<div class="discret">${esc(detail)}</div>` : ""}</td>
      <td class="num">${nf(s.F_moyen, 0)}${s.nb_graines > 1 ? ` <span class="discret">± ${nf(s.F_ecart_type, 0)}</span>` : ""}</td>
      <td class="num">${nf(s.F_min, 0)}</td>
      <td class="num">${s.temps_moyen === null ? "—" : nf(s.temps_moyen, 2) + " s"}</td>
      <td class="num">${nf(s.ecart_type_lits_semaine, 2)}</td><td class="num">${nf(s.lits_max, 0)}</td>
      <td class="num">${nf(s.ecart_type_places, 2)}</td><td class="num">${pct(s.taux_remplissage_moyen)}</td><td class="num">${s.vacations_vides}</td><td class="num">${pct(s.remplissage_vacations_utilisees)}</td>
      <td class="num">${libelleCapacites(s)}</td>
      <td class="num">${s.vacations_depassees}</td>
      <td class="num">${pct(s.part_dans_tolerance, 1)}</td>
      <td>${methode ? `<button class="bouton" data-adopter="${s.algo}">Adopter</button>` : ""}</td></tr>`;
  };
  const lignes = FAMILLES_OPTIM.map(([f, titre]) => {
    const L = S.filter((s) => s.famille === f);
    return L.length ? `<tr class="groupe"><th colspan="15">${titre}</th></tr>` + L.map(ligne).join("") : "";
  }).join("");
  const grand = S.length > 7 ? "haut" : "moyen";
  $("#resultats-optim").innerHTML = `
    <p class="legende-texte">Instance : ${I.N} patients de la liste initiale (sur ${I.taille_liste}), ${I.V} vacations, ${I.T} jours (${I.grille === "nouvelle" ? "grille proposée" : "ancienne grille"}, ouverture le ${dateLongue(I.date_ouverture)}) ·
      ${nf(I.minutes_demandees / 60, 0)} h demandées pour ${nf(I.minutes_offertes / 60, 0)} h offertes · ${pct(I.part_ambu)} d'ambulatoire prédit (${esc(I.modele || "")}) ·
      ${nf(I.admissibles_moyen, 1)} vacations admissibles par patient · budget ${I.budget.toLocaleString("fr-FR")} évaluations × ${I.graines.length} graine(s), départ ${I.depart === "aleatoire" ? "aléatoire" : "glouton"}.
      ${I.exclus ? I.exclus + " patient(s) sans vacation possible écartés." : ""} ${I.vacations_retirees ? I.vacations_retirees + " vacation(s) sans patient possible retirées de d." : ""}</p>
    <div class="bilan-optim">${bilanComparaison(S, I.graines.length)}</div>
    <div class="defile" style="max-height:none"><table class="table-optim"><thead><tr><th class="num">Rang</th><th>Méthode</th><th class="num">F moyen</th><th class="num">F min</th><th class="num">Temps</th>
      <th class="num">σ lits semaine</th><th class="num">Lits max</th><th class="num">σ places</th><th class="num">Remplissage</th><th class="num">Vacations libérées</th><th class="num">Remplissage sans les vacations vides</th>
      <th class="num">Capacités respectées (lits, places)</th><th class="num">Vac. hors marge</th><th class="num">À la date souhaitée ou dans la tolérance</th><th></th></tr></thead><tbody>${lignes}</tbody></table></div>
    <p class="aide">Rang : classement par F moyen sur les graines (« ± » : écart-type entre graines). Ligne surlignée : meilleure solution trouvée, toutes graines confondues.
      « Remplissage » : minutes prévues / durée de toutes les vacations, fixé par la liste de patients (le même pour toutes les méthodes) ;
      « Vacations libérées » : vacations sans aucun patient ; « Remplissage sans les vacations vides » : même calcul sur les seules vacations utilisées.
      Glouton = insertion un par un sans métaheuristique (la règle du planning en ligne) ; aléatoire = point de départ.
      « Capacités respectées » : aucune nuit au-delà des lits disponibles, aucun jour au-delà des places ambulatoires (contraintes d'hébergement) ;
      la marge des salles est une contrainte pénalisée (W5), comptée dans « Vac. hors marge ».</p>
    <p class="aide"><button class="lien-onglet" id="vers-robustesse">Tester la robustesse de ces plannings face aux aléas →</button></p>
    <div class="grille2" style="margin-top:12px">
      <div><h3>Convergence : meilleure valeur de F</h3><div class="graphe ${grand}"><canvas id="g-conv"></canvas></div></div>
      <div><h3>Décomposition de F (meilleure graine)</h3><div class="graphe ${grand}"><canvas id="g-termes"></canvas></div></div>
    </div>
    <h3>Lits occupés chaque nuit selon la méthode</h3>
    <p class="aide" style="margin:0 0 6px">Seule la méthode de la ligne surlignée est tracée : cliquez sur une méthode de la légende pour l'ajouter ou la retirer.</p>
    <div class="graphe moyen"><canvas id="g-occ-algos"></canvas></div>`;
  $$("[data-adopter]").forEach((b) => b.addEventListener("click", () => adopter(b.dataset.adopter)));
  $("#vers-robustesse").addEventListener("click", () => {
    ouvrirOnglet("simulation");
    $("#btn-robustesse").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  const algos = Object.keys(R.courbes);
  const nom = (a) => S.find((s) => s.algo === a).nom;
  graphe("g-conv", {
    type: "line",
    data: { datasets: algos.map((a) => ({ label: nom(a), data: R.courbes[a].map(([x, y]) => ({ x, y })), ...styleAlgo(a, c) })) },
    options: {
      interaction: { mode: "nearest", intersect: false },
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (x) => `${x.dataset.label} : F = ${nf(x.parsed.y, 0)} après ${x.parsed.x.toLocaleString("fr-FR")} évaluations` } } },
      scales: { x: echelle("évaluations de F", { type: "linear" }), y: echelle("F (échelle log)", { type: "logarithmic" }) },
    },
  });
  const termes = ["lits", "places", "salles", "retard", "ecart_souhait", "capacite_hebergement", "marge_salles"];
  const libT = { lits: "Lits", places: "Places ambu.", salles: "Remplissage salles", retard: "Retard", capacite_hebergement: "Capacité hébergement", marge_salles: "Marge de salle", ecart_souhait: "Écart date souhaitée" };
  const couleursT = [c.s1, c.s2, c.s3, c.s4, c.s7, c.s5, c.s6];
  const ordre = S.map((s) => s.algo).filter((a) => a !== "aleatoire");
  graphe("g-termes", {
    type: "bar",
    data: {
      labels: ordre.map(nom),
      datasets: termes.map((t, k) => ({ label: libT[t], data: ordre.map((a) => R.termes[a][t]), backgroundColor: couleursT[k], borderColor: c.surface, borderWidth: 1 })),
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (x) => `${x.dataset.label} : ${nf(x.parsed.x, 0)}` } } },
      scales: { x: echelle("contribution à F", { stacked: true, beginAtZero: true }), y: echelle(null, { stacked: true, grid: { display: false } }) },
    },
  });
  const dates = R.dates;
  const best = R.occupation[meilleur];
  graphe("g-occ-algos", {
    type: "line",
    data: {
      labels: dates.map(dateCourte),
      datasets: [
        ...algos.map((a) => ({ label: nom(a), data: R.occupation[a].lits, ...styleAlgo(a, c), hidden: a !== meilleur })),
        { label: "Glouton", data: R.occupation.glouton.lits, borderColor: c.s4, borderDash: [2, 3], backgroundColor: c.s4 },
        { label: "Cible (méthode surlignée)", data: best.cible_lits, borderColor: c.texte, borderWidth: 1.5, borderDash: [6, 4], stepped: "middle" },
        { label: "Capacité", data: R.cap_lits, borderColor: c.critique, borderWidth: 1, borderDash: [3, 3], stepped: "middle" },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (x) => `${x.dataset.label} : ${nf(x.parsed.y, 1)}` } } },
      scales: { x: echelle(null, { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 16 }, grid: { display: false } }), y: echelle("lits", { beginAtZero: true }) },
    },
  });
}
async function adopter(algo) {
  if (!confirm("Remplacer le planning actuel par la solution de cette méthode ?")) return;
  try {
    const r = await api("/api/adopter", { method: "POST", body: { algo } });
    toast(`Planning initial adopté (${r.patients} patients). Voir l'onglet Planning.`
      + (r.ecartes ? ` ${r.ecartes} patient(s) de la liste sans vacation possible : détail dans l'onglet Planning.` : ""));
    etat.init.params = await api("/api/parametres");
  } catch (e) { toast(e.message, true); }
}

/* =====================================================================
   Simulation et aléas : système multi-agents, robustesse, scénarios
   ===================================================================== */
const CHAMPS_ALEAS = [
  ["intensite_urgences", "Urgences (× les urgences réelles)", { step: 0.1, min: 0 }],
  ["taux_annulation", "Annulations (% des patients)", { pct: true, min: 0, max: 100 }],
  ["preavis_annulation_max", "Annulation annoncée jusqu'à (jours avant)", { min: 1 }],
  ["lits_fermes", "Lits fermés", { min: 0 }],
  ["fermeture_debut", "Fermeture à partir du jour (0 = ouverture)", { min: 0 }],
  ["fermeture_duree", "Durée de la fermeture (nuits)", { min: 0 }],
  ["preavis_fermeture", "Fermeture annoncée (jours avant)", { min: 0 }],
  ["part_prolongations", "Séjours prolongés d'une nuit (%)", { pct: true, min: 0, max: 100 }],
  ["retard_bloc", "Opérations plus ou moins longues que prévu", { case: true }],
  ["tolerance_depassement", "Dépassement accepté en fin de vacation (min)", { min: 0 }],
  ["delai_prevenance", "Patient convoqué (ne bouge plus) : jours avant l'opération", { min: 0 }],
  ["marge_lits", "Lits gardés libres par l'agent Lits", { min: 0 }],
  ["tolerance_comblement", "Agents : hausse de F tolérée pour combler une annulation", { step: 0.5, min: 0 }],
  ["cout_deplacement", "Réoptimisation centralisée : coût d'un patient déplacé", { min: 0 }],
  ["graine", "Graine du hasard (même graine = mêmes imprévus)", { min: 0 }],
];
// refus d'une nouvelle date par le patient (src/sma.py, patient_accepte) : section dédiée de l'étape 1
const CHAMPS_REFUS = [
  ["refus_avance", "Refus d'une date plus tôt (%)", { pct: true, min: 0, max: 100 }],
  ["refus_report", "Refus d'une date plus tard (%)", { pct: true, min: 0, max: 100 }],
];
const CLES_ALEAS = ["intensite_urgences", "taux_annulation", "preavis_annulation_max", "lits_fermes", "fermeture_debut",
  "fermeture_duree", "preavis_fermeture", "part_prolongations"];
const STYLE_STRATEGIE = { statique: "s2", sma: "s1", centralise: "s7" };
const NOMS_STRATEGIES_COURTS = { statique: "Sans coordination", sma: "Avec les agents", centralise: "Réoptimisation centralisée" };
const NOMS_METHODES = { recuit: "Recuit simulé", tabou: "Recherche tabou", genetique: "Algorithme génétique",
  relais_recuit_tabou: "Recuit → Tabou", relais_genetique_recuit: "Génétique → Recuit", relais_genetique_tabou: "Génétique → Tabou",
  memetique: "Algorithme mémétique", recuit_liste_tabou: "Recuit à liste tabou" };

function afficherFormAleas(valeurs) {
  const champs = (liste) => liste.map(([cle, lib, o]) => o.case
    ? `<label class="case"><input type="checkbox" data-alea="${cle}" ${valeurs[cle] ? "checked" : ""}> ${lib}</label>`
    : `<label><span>${lib}</span><input type="number" data-alea="${cle}" step="${o.step ?? 1}"
        ${o.min !== undefined ? `min="${o.min}"` : ""} ${o.max !== undefined ? `max="${o.max}"` : ""}
        value="${o.pct ? Math.round(1000 * valeurs[cle]) / 10 : valeurs[cle]}"></label>`).join("");
  $("#p-aleas").innerHTML = champs(CHAMPS_ALEAS);
  $("#p-refus").innerHTML = champs(CHAMPS_REFUS);
}
function lireAleas() {
  const p = {};
  for (const [cle, , o] of [...CHAMPS_ALEAS, ...CHAMPS_REFUS]) {
    const el = $(`[data-alea="${cle}"]`);
    if (!el) continue;
    p[cle] = o.case ? el.checked : Number(el.value) / (o.pct ? 100 : 1);
  }
  return p;
}
function choisirSituation(nom) {
  const I = etat.init, actuel = lireAleas();
  const base = Object.fromEntries(CLES_ALEAS.map((k) => [k, I.aleas_defaut[k]]));
  const extra = nom === "ordinaires" ? I.aleas_ordinaires : nom === "crise" ? I.aleas_crise : {};
  afficherFormAleas({ ...I.aleas_defaut, ...actuel, ...base, ...extra });
  $$("[data-situation]").forEach((b) => b.classList.toggle("choisie", b.dataset.situation === nom));
  etat.reglagesPerso = false;
  marquerNonEnregistree();
}
// situation enregistrée à l'étape 1 : c'est elle que rejouent les étapes 2, 3 et 4
// nouveaux patients en flux : option de la situation, appliquée aux étapes 2, 3 et 4
function lireFlux() {
  return { actif: $("#flux-actif").checked, par_jour: Number($("#flux-par-jour").value) };
}
// vacations libérées (sans patient au départ) : « utilisables », « dernier_recours » ou « fermees » (src/sma.py),
// envoyé avec les aléas de la situation
const lireLiberees = () => $('input[name="liberees"]:checked')?.value || "dernier_recours";
const NOMS_LIBEREES = { utilisables: "vacations libérées utilisables", dernier_recours: "vacations libérées en dernier recours",
  fermees: "vacations libérées maintenues vides" };
function enregistrerSituation() {
  const titre = $(".situation.choisie .sit-titre"), flux = lireFlux();
  const nom = (titre ? titre.textContent : "Situation personnalisée") + (etat.reglagesPerso ? " (réglages modifiés)" : "")
    + (flux.actif && flux.par_jour > 0 ? `, avec ${nf(flux.par_jour, 1)} nouveaux patients par jour ouvré` : "")
    + `, ${NOMS_LIBEREES[lireLiberees()]}`;
  const A = lireAleas();
  const refus = A.refus_avance || A.refus_report
    ? `, refus des patients : ${pct(A.refus_avance)} des dates plus tôt et ${pct(A.refus_report)} des dates plus tard`
    : ", sans refus des patients";
  etat.situation = { nom: nom + refus, aleas: { ...A, vacations_liberees: lireLiberees() }, flux };
  $("#msg-situation").innerHTML = `<span class="badge ok">Situation enregistrée</span> <span class="discret">${esc(etat.situation.nom)}</span>`;
  $("#msg-sim").textContent = `Situation enregistrée : ${etat.situation.nom}.`;
}
function marquerNonEnregistree() {
  if (!etat.situation) return;
  $("#msg-situation").innerHTML = `<span class="badge alerte">Modifications non enregistrées</span>
    <span class="discret">les étapes 2 à 4 utilisent encore « ${esc(etat.situation.nom)} »</span>`;
}
// + seuils et poids du score de robustesse s'ils sont modifiés (reglages_score, src/aleas.parametres)
const aleasSituation = () => ({ ...(etat.situation ? etat.situation.aleas : { ...lireAleas(), vacations_liberees: lireLiberees() }),
  ...reglagesScoreRequete() });
const fluxSituation = () => (etat.situation ? etat.situation.flux : lireFlux());
// texte de la fenêtre « Ajout de nouveaux patients en flux » (liste de départ actuelle, rythme réel de la base)
function afficherTexteFlux() {
  const FH = etat.init.flux_historique, D = etat.init.defauts_liste;
  const { x, S } = listeDepart(), C = chargeActivite(x, S, FH.par_jour);   // au rythme réel
  const environ = (n) => nf(Math.round(n / 10) * 10, 0);
  const parDefaut = x === D.initial_nb_patients && S === D.instance_semaines;
  const sature = C.avecFlux > 1.05;   // au rythme réel, le planning dépasserait l'activité normale
  $("#flux-aide").innerHTML = `<p class="titre-flux">Explication du flux</p>
    <p>Rythme moyen de la base : ${nf(FH.patients, 0)} patients programmés de ${FH.debut} à ${FH.fin}
    sur ${nf(FH.jours_ouvres, 0)} jours ouvrés (lundi au vendredi), soit ${nf(FH.par_jour, 1)} par jour (les urgences n'y sont pas, elles sont déjà un imprévu).</p>
    <p>Chaque jour ouvré, on tire un nombre de nouvelles demandes au hasard autour du rythme choisi avec une loi de Poisson. Les patients sont inventés comme
    les patients initiaux ; leur date souhaitée tombe dans les semaines qui suivent la demande, autant de semaines que le planning
    (hypothèse : la base ne donne pas la date de la demande). Seuls ceux qui veulent une date avant la fin du planning sont ajoutés.</p>
    <p class="titre-flux attention">Attention : modélisation de la simulation</p>
    <p>Par défaut, la liste de départ compte ${nf(D.initial_nb_patients, 0)} patients
    sur ${D.instance_semaines} semaines (deux réglages de l'onglet Patients initiaux), et le rythme proposé est de ${nf(FH.par_jour_defaut, 1)} nouveaux
    patients par jour, soit ${pct(FH.fraction)} du rythme réel. Cette valeur est arbitraire : elle représente des demandes supplémentaires.</p>
    <p>Réglages actuels : ${nf(x, 0)} patients sur ${S} semaines${parDefaut ? " (valeurs par défaut)" : ""}. Ces patients sont toutes les demandes
    des ${S} premières semaines, connues dès l'ouverture, soit environ ${pct(C.depart)} de l'activité normale de la base
    (${nf(FH.par_jour, 1)} patients × 5 jours × ${S} semaines = ${nf(C.normal, 0)} patients). Au rythme réel de ${nf(FH.par_jour, 1)} par jour,
    le flux ajouterait environ ${environ(C.flux)} patients dans le planning (ceux qui veulent une date avant sa fin), soit
    ${pct(C.avecFlux)} de l'activité normale${sature ? `, et ${C.avecFlux > 1.3 ? "une grande partie" : "une partie"} ne trouverait pas de place`
      : " : le planning resterait à peu près à son niveau normal, le rythme réel peut donc être utilisé"}.</p>
    <p><b>Pour une simulation réaliste</b>, il faudrait garder le rythme réel de ${nf(FH.par_jour, 1)} par jour et partir d'environ ${environ(C.connus)}
    patients sur ${S} semaines, ceux qui seraient déjà connus le jour de l'ouverture en temps normal.</p>`;
}
// sous la case « Ajouter de nouveaux patients » : part de l'activité réelle, sans et avec le rythme réglé
function afficherChargeFlux() {
  const { x, S } = listeDepart(), r = rythmeFlux(), C = chargeActivite(x, S, r);
  $("#flux-charge").innerHTML = `Patients de départ : ${nf(x, 0)} sur ${S} semaines, soit <b>${pct(C.depart)}</b> de l'activité réelle
    (${nf(C.normal, 0)} patients). Avec ${nf(r, 1)} nouveaux patients par jour ouvré : environ ${nf(C.flux, 0)} de plus dans le planning,
    soit <b>≈ ${pct(C.avecFlux)}</b> de l'activité réelle${$("#flux-actif").checked ? "" : " (si la case est cochée)"}.`;
}
function chargerSimulation() {
  if (!etat.init) return;
  if (etat.simPrete) {   // la liste de départ a pu changer depuis la dernière ouverture de l'onglet
    afficherTexteFlux();
    afficherChargeFlux();
    return;
  }
  etat.simPrete = true;
  afficherDefinitionRobustesse();
  afficherFormAleas(etat.init.aleas_defaut);
  const libDefaut = $(`input[name="liberees"][value="${etat.init.aleas_defaut.vacations_liberees}"]`);
  if (libDefaut) libDefaut.checked = true;
  $("#flux-par-jour").value = etat.init.flux_historique.par_jour_defaut;   // fraction ronde du rythme réel (src/flux.py, FRACTION_DEFAUT)
  afficherTexteFlux();
  afficherChargeFlux();
  enregistrerSituation();   // situation par défaut, enregistrée d'office
  const defaut = ["recuit", "relais_recuit_tabou", "relais_genetique_recuit"];
  // sous chaque scénario : patients, rythme et imprévus appliqués (campagne.py : imprévus ordinaires, forts pour « perturbations »)
  const A = etat.init;
  const imprevus = (a) => [a.intensite_urgences === 1 ? "urgences réelles" : `${nf(a.intensite_urgences, 0)} fois plus d'urgences`,
    `${pct(a.taux_annulation)} d'annulations`, `${pct(a.part_prolongations)} de séjours prolongés`,
    a.lits_fermes ? `${a.lits_fermes} lits fermés pendant ${nf(a.fermeture_duree / 7, 0)} semaines` : "aucun lit fermé"].join(", ");
  $("#sc-choix").innerHTML = `<span class="etiquette">Scénarios</span>` + etat.init.scenarios.map((s) => {
    const forts = s.cle === "perturbations";
    const a = { ...A.aleas_defaut, ...A.aleas_ordinaires, ...(forts ? A.aleas_crise : {}) };
    return `<label class="case case-scenario"><input type="checkbox" data-scenario="${s.cle}" checked>
      <span>${esc(s.nom)}<small>${nf(s.x, 0)} patients sur ${s.semaines} semaines, soit environ ${nf(s.x / s.semaines, 0)} par semaine.<br>Imprévus ${forts ? "forts" : "ordinaires"} : ${imprevus(a)}.</small></span></label>`;
  }).join("");
  $("#sc-methodes").innerHTML = `<span class="etiquette">Méthodes</span>` + Object.entries(NOMS_METHODES).map(([k, n]) =>
    `<label class="case"><input type="checkbox" data-sc-methode="${k}" ${defaut.includes(k) ? "checked" : ""}> ${esc(n)}</label>`).join("");
}
async function suivreJob(job, sel) {
  const zone = $(sel);
  zone.hidden = false;
  try {
    while (true) {
      await new Promise((r) => setTimeout(r, 600));
      const j = await api("/api/job/" + job);
      zone.querySelector(".barre > div").style.width = (100 * (j.progression || 0)) + "%";
      zone.querySelector("span").textContent = j.message || "";
      if (j.etat === "termine") return j.resultat;
      if (j.etat === "erreur") throw new Error(j.message);
    }
  } finally {
    setTimeout(() => (zone.hidden = true), 1200);
  }
}
function libJour(dates, t) {
  return dates && dates[t] ? dateCourte(dates[t]) : `jour ${t}`;
}
const pastilleStrategie = (s, c) => `<i class="pastille" style="border-color:${c[STYLE_STRATEGIE[s]]};background:${c[STYLE_STRATEGIE[s]]}"></i>`;

/* ----- définition du score de robustesse (critères, poids et seuils : src/robustesse.py) ----- */
const CLASSE_NIVEAU = { robuste: "ok", acceptable: "info", fragile: "fragile", critique: "ko" };
const nfCourt = (x) => Number(x).toLocaleString("fr-FR", { maximumFractionDigits: 2 });
function niveauRobustesse(score) {
  const N = etat.init.robustesse?.niveaux || [];
  const lib = (N.find(([seuil]) => score >= seuil) || [0, ""])[1];
  return lib ? `<span class="badge ${CLASSE_NIVEAU[lib]}">${lib}</span>` : "";
}
// Réglages modifiables (case « Modifier ») : seuil s_k et points perdus par incident w_k / s_k, gardés en mémoire
// (etat.reglagesScore, null = valeurs par défaut) ; le poids w_k = s_k × points par incident est recalculé.
const echellePrix = (c) => c.echelle_prix || 1;   // 100 : points affichés pour 100 unités (minutes)
const unitePrix = (c) => (echellePrix(c) === 100 ? `pour 100 ${c.unite}s` : `par ${c.unite}`);
const reglageScore = (c) => etat.reglagesScore?.[c.cle] || { seuil: c.seuil, prix: c.poids / c.seuil };
const poidsScore = (c) => reglageScore(c).seuil * reglageScore(c).prix;
function reglagesScoreRequete() {
  const C = etat.init?.robustesse?.criteres;
  if (!etat.reglagesScore || !C) return {};
  return { reglages_score: Object.fromEntries(C.map((c) => [c.cle, { seuil: reglageScore(c).seuil, poids: poidsScore(c) }])) };
}
// total des poids : 100 par défaut ; sinon le score n'est plus compris entre 0 et 100
function alerteTotalScore(total) {
  if (Math.abs(total - 100) < 0.005) return "";
  return `<span class="alerte-score">Le total n'est plus 100 : le score ${total > 100
    ? `peut descendre jusqu'à −${nfCourt(total - 100)}` : `ne peut plus descendre sous ${nfCourt(100 - total)}`}.</span>`;
}
function majPoidsScore() {
  const C = etat.init.robustesse.criteres;
  C.forEach((c) => ($(`[data-poids="${c.cle}"]`).textContent = nfCourt(poidsScore(c))));
  $$("[data-total-famille]").forEach((el) => (el.textContent = nfCourt(C.filter((c) => c.famille === el.dataset.totalFamille)
    .reduce((s, c) => s + poidsScore(c), 0))));
  const total = C.reduce((s, c) => s + poidsScore(c), 0);
  $("#score-total").textContent = nfCourt(total);
  $("#score-alerte").innerHTML = alerteTotalScore(total);
}
function afficherDefinitionRobustesse() {
  const R = etat.init.robustesse;
  if (!R) {
    $("#def-robustesse").innerHTML = `<p class="aide">Redémarrez le serveur pour afficher les critères du score.</p>`;
    return;
  }
  const modif = !!etat.reglagesScore;
  // « pour 100 patients » est dans le titre des colonnes du seuil et des points perdus ; seule l'exception est précisée sur la ligne
  const base = (c) => (c.mesure ? `${c.mesure} · ` : "");
  const nombre = (x) => String(Math.round(x * 1e6) / 1e6);   // valeur d'un champ (point décimal)
  const champ = (attr, c, x) => `<input type="number" min="0" step="any" ${attr}="${c.cle}" value="${nombre(x)}">`;
  const familles = [...new Set(R.criteres.map((c) => c.famille))];
  const lignes = familles.map((f) => {
    const C = R.criteres.filter((c) => c.famille === f);
    return `<tr class="groupe"><th colspan="4">${esc(f)} - <span data-total-famille="${esc(f)}">${nfCourt(C.reduce((s, c) => s + poidsScore(c), 0))}</span> points</th></tr>`
      + C.map((c) => {
        const r = reglageScore(c), e = echellePrix(c);
        return `<tr><td><b>${esc(c.libelle)}</b><span class="discret">${esc(c.compte)}</span>${
          c.precision ? `<span class="discret"><i>${esc(c.precision)}</i></span>` : ""}</td>
        <td class="num"><b data-poids="${c.cle}">${nfCourt(poidsScore(c))}</b></td>
        <td class="num">${modif ? champ("data-score-seuil", c, r.seuil) : nfCourt(r.seuil)}<span class="discret">${base(c)}${
          modif ? `par défaut : ${nfCourt(c.seuil)} · ` : ""}${esc(c.repere)}</span></td>
        <td class="num">${modif ? `${champ("data-score-prix", c, r.prix * e)}<span class="discret">${unitePrix(c)} · par défaut : ${nfCourt(c.poids / c.seuil * e)}</span>`
          : `${nfCourt(r.prix * e)} ${unitePrix(c)}`}</td></tr>`;
      }).join("");
  }).join("");
  const total = R.criteres.reduce((s, c) => s + poidsScore(c), 0);
  const N = R.niveaux;
  const niveaux = N.map(([s, lib], k) => `${niveauRobustesse(s)} ${k === 0 ? `${s} et plus` : s > 0 ? `${s} à ${N[k - 1][0]}` : `moins de ${N[k - 1][0]}`}`);
  const zone = $("#def-robustesse");
  zone.innerHTML = `
    <div class="modif-score"><label class="case"><input type="checkbox" id="score-modif" ${modif ? "checked" : ""}>
      Modifier les seuils et les points perdus par incident</label>
      ${modif ? `<span class="discret">Le poids est recalculé : w<sub>k</sub> = s<sub>k</sub> × points perdus par incident. Ces réglages s'appliquent
        aux prochaines simulations (étapes 2 à 4) ; décocher la case revient aux valeurs par défaut.</span>` : ""}</div>
    <div class="defile" style="max-height:none"><table class="table-sim table-score${modif ? " en-modification" : ""}"><thead><tr><th>Critère</th>
      <th class="num">Poids w<sub>k</sub></th><th class="num">Seuil critique s<sub>k</sub> pour 100 patients</th>
      <th class="num">Points perdus par incident, pour 100 patients</th></tr></thead>
      <tbody>${lignes}
        <tr class="total-score"><td><b>Total</b></td><td class="num"><b id="score-total">${nfCourt(total)}</b></td>
          <td colspan="2" id="score-alerte">${alerteTotalScore(total)}</td></tr></tbody></table></div>
    <div class="niveaux-robustesse"><span>Lecture du score :</span>${niveaux.join("<span>·</span>")}</div>`;
  // case cochée : on part des valeurs par défaut ; décochée : retour aux valeurs par défaut
  zone.onchange = (e) => {
    if (e.target.id !== "score-modif") return;
    etat.reglagesScore = e.target.checked
      ? Object.fromEntries(R.criteres.map((c) => [c.cle, { seuil: c.seuil, prix: c.poids / c.seuil }])) : null;
    afficherDefinitionRobustesse();
  };
  // saisie : seuil > 0, points par incident >= 0 ; une valeur invalide est signalée et n'est pas prise en compte
  zone.oninput = (e) => {
    const el = e.target, cle = el.dataset.scoreSeuil || el.dataset.scorePrix;
    if (!cle || !etat.reglagesScore) return;
    const c = R.criteres.find((k) => k.cle === cle), v = Number(el.value), seuil = !!el.dataset.scoreSeuil;
    const ok = el.value.trim() !== "" && Number.isFinite(v) && (seuil ? v > 0 : v >= 0);
    el.classList.toggle("invalide", !ok);
    if (!ok) return;
    if (seuil) etat.reglagesScore[cle].seuil = v;
    else etat.reglagesScore[cle].prix = v / echellePrix(c);
    majPoidsScore();
  };
}
// étape 2 : score de chaque façon de réagir, avec les deux critères qui coûtent le plus
function scoreSimulation(R, c) {
  const strat = R.strategies, C = etat.init.robustesse?.criteres;
  if (!C || strat.some((s) => s.indicateurs.score_robustesse === undefined))
    return `<p class="aide">Redémarrez le serveur pour calculer le score de robustesse.</p>`;
  const perte = (s, k) => s.indicateurs["perte_" + k.cle] ?? 0;
  const tuiles = strat.map((s) => {
    const pires = C.filter((k) => perte(s, k) >= 0.05).sort((a, b) => perte(s, b) - perte(s, a)).slice(0, 2);
    return `<div class="tuile"><div class="etiquette">${pastilleStrategie(s.strategie, c)}${esc(NOMS_STRATEGIES_COURTS[s.strategie])}</div>
      <div class="valeur">${nf(s.indicateurs.score_robustesse, 0)} <small>/ 100</small></div>
      <div class="detail">${niveauRobustesse(s.indicateurs.score_robustesse)}
        ${pires.length ? ` perd surtout : ${pires.map((k) => `${k.libelle.toLowerCase()} (−${nf(perte(s, k), 1)})`).join(", ")}` : " aucun incident"}</div></div>`;
  }).join("");
  const lignes = C.map((k) => `<tr><td>${esc(k.libelle)}</td>${strat.map((s) =>
    `<td class="num">${perte(s, k) > 0 ? `−${nf(perte(s, k), 1)}` : "0"}</td>`).join("")}</tr>`).join("");
  const modifie = R.parametres?.reglages_score ? ", avec des seuils et des poids modifiés" : "";
  return `<h3 style="margin-top:4px">Score de robustesse <span class="discret">- sur 100, défini plus haut (« Définir ce qu'est un planning robuste »)${modifie}</span></h3>
    <div class="tuiles tuiles-score">${tuiles}</div>
    <details class="replie"><summary>Détail du score : points perdus par critère</summary>
      <div class="defile" style="max-height:none"><table class="table-sim"><thead><tr><th>Critère</th>${strat.map((s) =>
        `<th class="num">${pastilleStrategie(s.strategie, c)}${esc(NOMS_STRATEGIES_COURTS[s.strategie])}</th>`).join("")}</tr></thead>
        <tbody>${lignes}<tr><td><b>Score</b></td>${strat.map((s) =>
          `<td class="num"><b>${nf(s.indicateurs.score_robustesse, 1)}</b></td>`).join("")}</tr></tbody></table></div>
    </details>`;
}

/* ----- 1-2. simulation du planning adopté ----- */
async function simulerPlanning() {
  $("#btn-simuler").disabled = true;
  $("#msg-sim").textContent = "Simulation en cours…";
  try {
    const R = await api("/api/simulation", { method: "POST", body: { aleas: aleasSituation(), flux: fluxSituation() } });
    etat.simulation = R;
    afficherSimulation(R);
  } catch (e) { toast(e.message, true); }
  $("#msg-sim").textContent = etat.situation ? `Situation enregistrée : ${etat.situation.nom}.` : "";
  $("#btn-simuler").disabled = false;
}
// détail complet (zone repliée) : tous les indicateurs, les trois façons de réagir
const INDIC_SIM = [
  ["Score de robustesse", [["score_robustesse", "Score de robustesse (sur 100)", 1]]],
  ["Fonction objectif", [["F_apres", "F après la simulation (planning réellement exécuté)", 0, true]]],
  ["Lits", [["nuits_surcapacite", "Nuits où il manque des lits", 0, true], ["lits_nuits_manquants", "Lits manquants au total", 0, true],
    ["lits_max", "Lits occupés au maximum", 0], ["taux_occupation_lits", "Occupation moyenne des lits", "pct"],
    ["ecart_type_lits_semaine", "Écart-type des lits en semaine (lits)", 2, true],
    ["ecart_type_lits_week_end", "Écart-type des lits le week-end (lits)", 2, true]]],
  ["Ambulatoire", [["jours_ambu_satures", "Jours où il manque des places", 0, true],
    ["places_manquantes", "Places manquantes les jours d'ouverture (places × jours)", 1, true],
    ["ecart_type_places_semaine", "Écart-type des places ambulatoires (places)", 2, true]]],
  ["Bloc", [["taux_occupation_bloc", "Occupation réelle du bloc", "pct"],
    ["taux_occupation_bloc_utilisees", "… sans les vacations libérées (vacations où un patient est opéré)", "pct"], ["vacations_debordantes", "Vacations qui finissent en retard", 0, true],
    ["vacations_operees", "Vacations où au moins un patient est opéré", 0],
    ["minutes_debordement", "Minutes de retard au total", 0, true], ["patients_reportes", "Patients non opérés le jour prévu", 0, true],
    ["minutes_vides_annulation", "Minutes de bloc laissées vides après une annulation", 0, true],
    ["vacations_trou_annulation", "Vacations avec un trou laissé par une annulation", 0, true],
    ["liberees_rouvertes", "Vacations libérées rouvertes (patients concernés)", "liberees", true]]],
  ["Patients", [["patients_operes", "Patients opérés", 0], ["annulations", "Annulations", 0],
    ["creneaux_recombles", "Créneaux libérés réutilisés", 0], ["patients_dates_changees", "Patients dont la date a changé", 0, true],
    ["patients_avances", "… dont opérés plus tôt", 0], ["patients_retardes", "… dont opérés plus tard", 0, true],
    ["jours_attente_gagnes", "Jours d'attente gagnés (patients avancés)", 0],
    ["ecart_moyen_jours", "Décalage moyen (jours)", 1], ["patients_sans_creneau", "Patients sans nouvelle date", 0, true],
    ["conflits_non_resolus", "Placés quand même (conflit)", 0, true],
    ["conflits_marge_salle", "… dont faute de vacation avec la marge de la salle", 0, true],
    ["refus_patients", "Nouvelles dates refusées par les patients", 0], ["refus_avances", "… dates plus tôt", 0],
    ["refus_reports", "… dates plus tard", 0], ["patients_refus", "Patients qui ont refusé au moins une date", 0]]],
  ["Qualité du planning exécuté (patients programmés ; planning prévu pour comparaison)", [
    ["ecart_cible_lits_prevu", "Écart à la cible des lits, planning prévu (lits)", 2],
    ["ecart_cible_lits_execute", "… planning exécuté (lits)", 2, true],
    ["hausse_ecart_lits", "Hausse de l'écart à la cible des lits (%)", 1, true],
    ["ecart_cible_places_prevu", "Écart à la cible des places ambulatoires, planning prévu (places)", 2],
    ["ecart_cible_places_execute", "… planning exécuté (places)", 2, true],
    ["hausse_ecart_places", "Hausse de l'écart à la cible des places (%)", 1, true],
    ["remplissage_prevu", "Remplissage des vacations utilisées, planning prévu", "pct"],
    ["taux_occupation_bloc_utilisees", "… planning exécuté", "pct"],
    ["baisse_remplissage", "Baisse du remplissage (points)", 1, true],
    ["manque_remplissage", "Remplissage exécuté sous 85 % (points, score de robustesse)", 1, true]]],
  ["Calcul", [["messages", "Messages échangés", 0], ["reoptimisations", "Recalculs du planning", 0], ["temps_s", "Temps de calcul (s)", 2]]],
];
// groupe ajouté quand des nouveaux patients arrivent pendant la simulation (flux)
const INDIC_FLUX = ["Nouveaux patients (flux)", [["nouveaux_patients", "Nouveaux patients ajoutés", 0],
  ["nouveaux_operes", "… dont opérés pendant la simulation", 0],
  ["delai_moyen_nouveaux", "Délai moyen entre la demande et l'opération (jours)", 1, true],
  ["ecart_souhait_nouveaux", "Écart moyen à la date souhaitée (jours)", 1, true],
  ["nouveaux_conflits", "… placés quand même (conflit)", 0, true],
  ["nouveaux_sans_creneau", "… sans place avant la fin du planning", 0, true]]];
// fmt (facultatif) : texte de la valeur à partir des indicateurs de la stratégie (et de son nom)
function tuileComparee(lib, cle, S, note, avecEcart = true, d = 0, fmt = null) {
  const sans = S.statique[cle], avec = S.sma[cle];
  const ecart = avecEcart && sans > 0 ? (avec - sans) / sans : null;
  const c = couleurs();
  const texte = (s) => (fmt ? fmt(S[s], s) : nf(S[s][cle], d));
  return `<div class="tuile tuile-comparee"><div class="etiquette">${lib}</div>
    <div class="ligne-strat">${pastilleStrategie("statique", c)}<span>Sans coordination</span><b>${texte("statique")}</b></div>
    <div class="ligne-strat">${pastilleStrategie("sma", c)}<span>Avec les agents</span><b>${texte("sma")}</b></div>
    <div class="detail">${ecart !== null && ecart !== 0 ? `${ecart < 0 ? "−" : "+"}${pct(Math.abs(ecart))} avec les agents · ` : ""}${note}</div></div>`;
}
function afficherSimulation(R) {
  const c = couleurs(), A = R.aleas, f = R.fermeture, P = R.parametres;
  const S = Object.fromEntries(R.strategies.map((s) => [s.strategie, s.indicateurs]));
  const texteLitsManquants = (I) => {
    const n = I.lits_nuits_manquants;
    return `${nf(I.nuits_surcapacite, 0)} <span class="discret">(${nf(n, 0)} lit${n > 1 ? "s" : ""})</span>`;
  };
  // sous un titre de tuile : « sur N … », ou les deux valeurs si les deux façons de réagir diffèrent
  const sousEtiquette = (v, suite) => v.some((x) => x === undefined || x === null) ? ""
    : `<span class="sous-etiquette">sur ${v[0] === v[1] ? nf(v[0], 0)
      : `${nf(v[0], 0)} (sans coordination) et ${nf(v[1], 0)} (avec les agents)`} ${suite}</span>`;
  // lits occupés au total (lits × nuits, urgences comprises, lits manquants compris), d'après l'occupation simulée
  const totalLits = ["statique", "sma"].map((s) => {
    const lits = R.strategies.find((x) => x.strategie === s)?.series?.lits;
    return lits ? Math.round(lits.reduce((a, b) => a + b, 0)) : undefined;
  });
  const sousEtiquetteLits = sousEtiquette(totalLits, "lits occupés au total (lits × nuits, urgences comprises)");
  // nombre de vacations où au moins un patient est opéré, sous le titre de la tuile des retards
  const vo = [S.statique.vacations_operees, S.sma.vacations_operees];
  const sousEtiquetteVacations = sousEtiquette(vo, "vacations où au moins un patient est opéré");
  const imprevus = [
    f.lits > 0 && f.fin > f.debut ? `${f.lits} lits fermés du ${libJour(R.dates, f.debut)} au ${libJour(R.dates, f.fin - 1)}` : null,
    `${A.urgences_hospitalisees} urgence(s) hospitalisée(s)`,
    `${A.annulations} annulation(s)`,
    `${A.prolongations} séjour(s) prolongé(s) d'une nuit`,
    P.retard_bloc ? `des opérations qui durent en moyenne ${nf(A.ecart_moyen_duree_min, 0)} min de plus ou de moins que prévu` : null,
    P.refus_avance || P.refus_report ? `des patients qui refusent ${pct(P.refus_avance)} des dates plus tôt et ${pct(P.refus_report)} des dates plus tard` : null,
  ].filter(Boolean);
  const X = R.flux;   // nouveaux patients en flux (null si l'option n'est pas cochée)
  const pluriel = (n, mot) => `${nf(n, 0)} ${mot}${n > 1 ? "s" : ""}`;
  const texteFlux = X ? `<p class="legende-texte"><b>Nouveaux patients</b> : ${pluriel(X.arrivees, "demande")} en ${R.dates.length} jours
      (rythme réglé : ${nf(X.par_jour, 1)} par jour ouvré). Patients ajoutés au planning (date souhaitée avant sa fin) : ${nf(X.ajoutes, 0)},
      soit ${nf(X.minutes / 60, 0)} h d'opérations, dont ${pluriel(X.ambulatoires, "ambulatoire")}. Demandes pour une date après la fin du planning,
      non simulées : ${nf(X.apres_horizon, 0)}${X.sans_vacation ? ` ; sans vacation de leur chirurgien : ${X.sans_vacation}` : ""}.</p>` : "";
  $("#resultats-sim").innerHTML = `
    <p class="legende-texte"><b>Imprévus tirés</b> sur ${R.dates.length} jours (${R.nb_patients} patients${X ? " au départ" : ""}) : ${imprevus.join(" · ")}.</p>
    ${texteFlux}
    ${scoreSimulation(R, c)}
    <h3>Principaux indicateurs</h3>
    <div class="tuiles tuiles-4">
      ${tuileComparee(`Nuits où il manque des lits${sousEtiquetteLits}`, "nuits_surcapacite", S,
        "sur tout le planning ; entre parenthèses, les lits manquants au total (un lit qui manque 2 nuits compte 2)",
        true, 0, texteLitsManquants)}
      ${tuileComparee(`Vacations qui finissent en retard${sousEtiquetteVacations}`, "vacations_debordantes", S, "opérations plus longues que prévu", false)}
      ${tuileComparee(`Patients dont la date change<span class="sous-etiquette">sur ${nf(R.nb_patients + (X ? X.ajoutes : 0), 0)} patients
        programmés${X ? ` (${nf(R.nb_patients, 0)} au départ et ${nf(X.ajoutes, 0)} nouveaux)` : ""}</span>`, "patients_dates_changees", S,
        "toujours prévenus à l'avance ; entre parenthèses, les nouvelles dates refusées par les patients", false, 0,
        (I) => `${nf(I.patients_dates_changees, 0)} <span class="discret">(${nf(I.refus_patients ?? 0, 0)} refus)</span>`)}
      ${tuileComparee("F après la simulation", "F_apres", S, X
        ? `nouveaux patients compris ; planning prévu, sans eux ni imprévu : ${nf(R.F_prevu, 0)}`
        : `planning prévu, sans imprévu : ${nf(R.F_prevu, 0)}`)}
    </div>
    <div class="tuiles tuiles-4" style="margin-top:12px">
      ${tuileComparee("Écart-type des lits en semaine", "ecart_type_lits_semaine", S,
        "nuits du lundi au vendredi ; entre parenthèses, les nuits du week-end", true, 2,
        (I) => `${nf(I.ecart_type_lits_semaine, 2)} <span class="discret">(week-end ${nf(I.ecart_type_lits_week_end, 2)})</span>`)}
      ${tuileComparee("Écart-type des places ambulatoires", "ecart_type_places_semaine", S, "jours d'ouverture de l'ambulatoire", true, 2)}
      ${tuileComparee(`Vacations libérées rouvertes<span class="sous-etiquette">sur ${nf(S.sma.liberees_depart ?? 0, 0)} vacations
        sans patient au départ</span>`, "liberees_rouvertes", S, "entre parenthèses, les patients placés dedans", false, 0,
        (I) => texteLiberees(I.liberees_rouvertes ?? 0, I.patients_liberees ?? 0, 0))}
      ${tuileComparee("Remplissage sans les vacations libérées", "taux_occupation_bloc_utilisees", S,
        "minutes opérées (durées réelles) / durée des vacations où au moins un patient est opéré", false, 0,
        (I) => (I.taux_occupation_bloc_utilisees === undefined ? "—" : pct(I.taux_occupation_bloc_utilisees, 1)))}
    </div>
    <p class="aide" style="margin-top:6px">F après la simulation : la fonction objectif appliquée au planning réellement exécuté
      (urgences réellement arrivées, lits réellement ouverts, séjours prolongés, patients annulés retirés) ; plus petit = mieux.
      Une urgence ambulatoire arrivée un jour où l'ambulatoire est fermé compte comme un dépassement de capacité : cette part est la même pour les trois façons de réagir.
      Écarts-types calculés sur l'occupation simulée, urgences comprises : plus ils sont petits, plus l'occupation est régulière.</p>
    <h3>Lits occupés chaque nuit</h3><div class="graphe moyen"><canvas id="g-sim-lits"></canvas></div>
    <details class="fenetre" id="fenetre-detail-sim" style="margin-top:18px">
      <summary>Tous les indicateurs, avec une troisième façon de réagir : la réoptimisation centralisée</summary>
      <div class="contenu"><div id="detail-sim"></div></div>
    </details>`;
  const labels = R.dates.map(dateCourte);
  const ref = R.strategies.find((s) => s.strategie === "sma") || R.strategies[0];
  graphe("g-sim-lits", {
    type: "line",
    data: { labels, datasets: [
      ...["statique", "sma", "centralise"].map((k) => R.strategies.find((s) => s.strategie === k)).filter(Boolean).map((s) => ({
        label: NOMS_STRATEGIES_COURTS[s.strategie], data: s.series.lits, borderColor: c[STYLE_STRATEGIE[s.strategie]],
        backgroundColor: c[STYLE_STRATEGIE[s.strategie]], stepped: "middle" })),
      { label: "Lits disponibles", data: ref.series.cap_lits, borderColor: c.critique, borderWidth: 1.5, borderDash: [5, 4], stepped: "middle", backgroundColor: c.critique }] },
    options: {
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (x) => `${x.dataset.label} : ${nf(x.parsed.y, 0)}` } } },
      scales: { x: echelle(null, { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 }, grid: { display: false } }), y: echelle("lits", { beginAtZero: true }) },
    },
  });
  afficherDetailSimulation(R);
}
// vacations libérées rouvertes, et entre parenthèses les patients placés dedans
const texteLiberees = (n, patients, d) => `${nf(n, d)} <span class="discret">(${nf(patients, d)} patient${patients > 1 ? "s" : ""})</span>`;
function afficherDetailSimulation(R) {
  const c = couleurs(), strat = R.strategies;
  const groupes = R.flux ? [...INDIC_SIM.slice(0, -1), INDIC_FLUX, INDIC_SIM[INDIC_SIM.length - 1]] : INDIC_SIM;
  const lignes = groupes.map(([groupe, L]) => `<tr class="groupe"><th colspan="${strat.length + 1}">${groupe}</th></tr>` + L.map(([cle, lib, d, moinsMieux]) => {
    const vals = strat.map((s) => s.indicateurs[cle]);
    const best = moinsMieux ? Math.min(...vals) : null;
    const distinct = new Set(vals).size > 1;
    return `<tr><td>${lib}</td>` + vals.map((x, k) => {
      const txt = d === "pct" ? pct(x, 1) : d === "liberees" ? texteLiberees(x, strat[k].indicateurs.patients_liberees, 0) : nf(x, d);
      return `<td class="num">${moinsMieux && distinct && x === best ? `<b>${txt}</b>` : txt}</td>`;
    }).join("") + `</tr>`;
  }).join("")).join("");
  $("#detail-sim").innerHTML = `
    <p class="aide" style="margin:4px 0 8px">Réoptimisation centralisée : à chaque imprévu, tout le planning (patients pas encore convoqués) est recalculé
      en déplaçant le moins de patients possible. En gras : la meilleure valeur quand les façons de réagir diffèrent.</p>
    <div class="grille2">
      <div><div class="defile" style="max-height:none"><table class="table-sim"><thead><tr><th>Indicateur</th>${strat.map((s) =>
        `<th class="num">${pastilleStrategie(s.strategie, c)}${esc(NOMS_STRATEGIES_COURTS[s.strategie])}</th>`).join("")}</tr></thead>
        <tbody>${lignes}</tbody></table></div></div>
      <div><h3 style="margin-top:0">Places ambulatoires occupées chaque jour</h3><div class="graphe"><canvas id="g-sim-places"></canvas></div></div>
    </div>
    <div class="journal-entete"><h3>Journal complet</h3>${strat.map((s) =>
      `<button class="bouton ${s.strategie === "sma" ? "principal" : ""}" data-journal="${s.strategie}">${esc(NOMS_STRATEGIES_COURTS[s.strategie])}</button>`).join("")}
      <span class="discret" id="journal-info"></span></div>
    <div class="journal" id="journal"></div>`;
  const ref = strat.find((s) => s.strategie === "sma") || strat[0];
  graphe("g-sim-places", {
    type: "line",
    data: { labels: R.dates.map(dateCourte), datasets: [
      ...strat.map((s) => ({ label: NOMS_STRATEGIES_COURTS[s.strategie], data: s.series.places, borderColor: c[STYLE_STRATEGIE[s.strategie]],
        backgroundColor: c[STYLE_STRATEGIE[s.strategie]], stepped: "middle" })),
      { label: "Places ouvertes", data: ref.series.cap_places, borderColor: c.critique, borderWidth: 1.5, borderDash: [5, 4], stepped: "middle", backgroundColor: c.critique }] },
    options: {
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (x) => `${x.dataset.label} : ${nf(x.parsed.y, 0)}` } } },
      scales: { x: echelle(null, { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 }, grid: { display: false } }), y: echelle("places", { beginAtZero: true }) },
    },
  });
  $$("[data-journal]").forEach((b) => b.addEventListener("click", () => afficherJournal(b.dataset.journal)));
  afficherJournal(ref.strategie);
}
function afficherJournal(strategie) {
  const s = etat.simulation.strategies.find((x) => x.strategie === strategie);
  $$("[data-journal]").forEach((b) => b.classList.toggle("principal", b.dataset.journal === strategie));
  $("#journal-info").textContent = `${s.journal.length} message(s) affiché(s) sur ${s.indicateurs.messages}`;
  $("#journal").innerHTML = s.journal.length ? `<table><tbody>${s.journal.map((m) =>
    `<tr><td>${esc(m.jour)}</td><td class="acteurs">${esc(m.de)}${m.a && m.a !== "—" ? " → " + esc(m.a) : ""}</td><td>${esc(m.texte)}</td></tr>`).join("")}</tbody></table>`
    : `<p class="discret" style="padding:8px">Aucun message.</p>`;
}

/* ----- 3. robustesse des plannings alternatifs ----- */
async function lancerRobustesse() {
  $("#btn-robustesse").disabled = true;
  try {
    const { job } = await api("/api/robustesse", { method: "POST", body: {
      aleas: aleasSituation(), flux: fluxSituation(), nb_tirages: Number($("#rob-nb").value) } });
    etat.robustesse = await suivreJob(job, "#prog-rob");
    afficherRobustesse(etat.robustesse);
  } catch (e) { toast(e.message, true); }
  $("#btn-robustesse").disabled = false;
}
// critères de robustesse affichés (moyennes sur les simulations) : [clé, libellé, décimales, meilleur = "min" | "max"]
const CRITERES_ROBUSTESSE = [
  ["Lits", [["lits_nuits_manquants", "Lits manquants au total", 1, "min"], ["nuits_surcapacite", "Nuits où il manque des lits", 1, "min"],
    ["part_sans_surcapacite", "Simulations sans aucun lit manquant", "pct", "max"]]],
  ["Ambulatoire", [["jours_ambu_satures", "Jours où il manque des places", 1, "min"],
    ["places_manquantes", "Places manquantes les jours d'ouverture (places × jours)", 1, "min"]]],
  ["Bloc", [["vacations_debordantes", "Vacations qui finissent en retard", 1, "min"], ["minutes_debordement", "Minutes de retard au total", 0, "min"],
    ["patients_reportes", "Patients non opérés le jour prévu", 1, "min"],
    ["minutes_vides_annulation", "Minutes de bloc laissées vides après une annulation", 0, "min"],
    ["liberees_rouvertes", "Vacations libérées rouvertes (patients concernés)", "liberees", "min"]]],
  ["Patients", [["patients_dates_changees", "Patients dont la date change", 1, "min"],
    ["jours_attente_gagnes", "Jours d'attente gagnés (patients avancés)", 1, "max"], ["patients_sans_creneau", "Patients sans nouvelle date", 1, "min"],
    ["conflits_non_resolus", "Patients placés quand même (conflit)", 1, "min"],
    ["refus_patients", "Nouvelles dates refusées par les patients", 1, null]]],
  ["Qualité du planning exécuté", [["ecart_cible_lits_execute", "Écart à la cible des lits, planning exécuté (lits)", 2, "min"],
    ["ecart_cible_places_execute", "Écart à la cible des places ambulatoires, planning exécuté (places)", 2, "min"],
    ["taux_occupation_bloc_utilisees", "Remplissage des vacations utilisées, planning exécuté", "pct", "max"]]],
];
const CRITERES_FLUX = ["Nouveaux patients (flux)", [["nouveaux_patients", "Nouveaux patients ajoutés", 1, null],
  ["nouveaux_sans_creneau", "… sans place avant la fin du planning", 1, "min"],
  ["delai_moyen_nouveaux", "Délai moyen entre la demande et l'opération (jours)", 1, "min"],
  ["nouveaux_conflits", "… placés quand même (conflit)", 1, "min"]]];
function afficherRobustesse(R) {
  const c = couleurs();
  // colonnes : du moins au plus de lits manquants sans coordination (à égalité, le plus petit F)
  const P = [...R.plans].sort((a, b) => a.statique.lits_nuits_manquants - b.statique.lits_nuits_manquants || a.F_min - b.F_min);
  const rangF = {}, ordinal = (k) => (k === 1 ? "1er" : `${k}e`);
  [...R.plans].sort((a, b) => a.F_min - b.F_min).forEach((p, k) => (rangF[p.algo] = k + 1));
  // valeur et rang de chaque planning (rang 1 en gras) ; plusGrand : le plus grand est le meilleur
  const ligneRang = (libelle, val, plusGrand, d) => {
    const rang = {};
    [...P].sort((a, b) => (plusGrand ? val(b) - val(a) : val(a) - val(b))).forEach((p, k) => (rang[p.algo] = k + 1));
    return `<tr><td>${libelle}</td>${P.map((p) => {
      const txt = `${nf(val(p), d)} <span class="discret">(${ordinal(rang[p.algo])})</span>`;
      return `<td class="num">${rang[p.algo] === 1 ? `<b>${txt}</b>` : txt}</td>`;
    }).join("")}</tr>`;
  };
  // une ligne : valeurs des plannings, la meilleure en gras quand elles diffèrent
  const ligne = (libelle, vals, d, meilleur, patients, attributs = "") => {
    const txt = vals.map((x, k) => (d === "pct" ? pct(x) : d === "liberees" ? texteLiberees(x, patients[k], 1) : nf(x, d)));
    const distincts = new Set(txt).size > 1;
    const best = meilleur === "max" ? Math.max(...vals) : meilleur === "min" ? Math.min(...vals) : null;   // null : pas de meilleur
    return `<tr ${attributs}><td>${libelle}</td>${vals.map((x, k) =>
      `<td class="num">${distincts && x === best ? `<b>${txt[k]}</b>` : txt[k]}</td>`).join("")}</tr>`;
  };
  const strat = [["statique", "sans coordination"], ["sma", "avec les agents"]];
  const criteres = R.flux ? [...CRITERES_ROBUSTESSE, CRITERES_FLUX] : CRITERES_ROBUSTESSE;
  // catégories d'indicateurs repliées par défaut : un clic sur la ligne de la catégorie les affiche ou les masque
  const corps = criteres.map(([groupe, L], g) =>
    `<tr class="groupe groupe-repliable" data-groupe="${g}" tabindex="0" role="button" aria-expanded="false">
      <th colspan="${P.length + 1}"><span class="fleche">▸</span>${groupe}
        <span class="discret">${L.length * strat.length} indicateurs, cliquer pour afficher</span></th></tr>` + L.map(([cle, lib, d, meilleur]) =>
      strat.map(([s, nom]) => ligne(`${lib}<div class="discret">${pastilleStrategie(s, c)}${nom}</div>`,
        P.map((p) => p[s][cle]), d, meilleur, P.map((p) => p[s].patients_liberees), `data-du-groupe="${g}" hidden`)).join("")).join("")).join("");
  $("#resultats-rob").innerHTML = `
    <div class="defile" style="max-height:none"><table class="table-sim table-robustesse"><thead>
      <tr><th>Critère (moyenne sur ${R.nb_tirages} simulations)</th>${P.map((p) =>
        `<th class="num">${pastilleAlgo(p.algo, c)}${esc(p.nom)}</th>`).join("")}</tr></thead>
      <tbody>
        <tr><td>F avant simulation <span class="discret">(rang)</span></td>${P.map((p) =>
          `<td class="num">${rangF[p.algo] === 1 ? "<b>" : ""}${nf(p.F_min, 0)}${rangF[p.algo] === 1 ? "</b>" : ""}
            <span class="discret">(${ordinal(rangF[p.algo])})</span></td>`).join("")}</tr>
        ${strat.map(([s, nom]) => ligneRang(`Score de robustesse, sur 100 <span class="discret">(rang)</span>
          <div class="discret">${pastilleStrategie(s, c)}${nom}</div>`, (p) => p[s].score_robustesse, true, 1)).join("")}
        ${corps}
      </tbody></table></div>
    ${R.flux ? `<p class="aide">Avec de nouveaux patients : ${nf(R.flux.par_jour, 1)} par jour ouvré, ${nf(R.flux.ajoutes, 0)} ajoutés au planning
      en moyenne par simulation, les mêmes pour tous les plannings.</p>` : ""}
    ${R.parametres?.reglages_score ? `<p class="aide">Score de robustesse calculé avec des seuils et des poids modifiés.</p>` : ""}`;
  $$("#resultats-rob .groupe-repliable").forEach((tr) => {
    const basculer = () => {
      const ouvert = tr.getAttribute("aria-expanded") !== "true";
      tr.setAttribute("aria-expanded", ouvert);
      tr.querySelector(".fleche").textContent = ouvert ? "▾" : "▸";
      tr.querySelector(".discret").textContent = tr.querySelector(".discret").textContent
        .replace(ouvert ? "afficher" : "masquer", ouvert ? "masquer" : "afficher");
      $$(`#resultats-rob [data-du-groupe="${tr.dataset.groupe}"]`).forEach((l) => (l.hidden = !ouvert));
    };
    tr.addEventListener("click", basculer);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); basculer(); } });
  });
}

/* ----- 4. scénarios ----- */
async function lancerScenarios() {
  const scenarios = $$("[data-scenario]").filter((x) => x.checked).map((x) => x.dataset.scenario);
  const algos = $$("[data-sc-methode]").filter((x) => x.checked).map((x) => x.dataset.scMethode);
  if (!scenarios.length || !algos.length) { toast("Cochez au moins un scénario et une méthode.", true); return; }
  const aleas = aleasSituation();
  $("#btn-scenarios").disabled = true;
  try {
    const { job } = await api("/api/scenarios", { method: "POST", body: {
      scenarios, algos, budget: Number($("#sc-budget").value), nb_tirages: Number($("#sc-nb").value),
      aleas, flux: fluxSituation(), graine: aleas.graine, params_algos: lireParamsAlgos() } });
    etat.scenarios = await suivreJob(job, "#prog-sc");
    etat.scenarios.score_modifie = !!aleas.reglages_score;
    afficherScenarios(etat.scenarios);
  } catch (e) { toast(e.message, true); }
  $("#btn-scenarios").disabled = false;
}
function afficherScenarios(R) {
  const c = couleurs();
  // détail par méthode, en deux groupes de colonnes : le planning prévu (avant imprévus), puis sa robustesse (après imprévus)
  const blocs = R.scenarios.map((sc) => {
    const lignes = sc.methodes.map((m) => {
      const st = m.robustesse.statique, sm = m.robustesse.sma;
      return `<tr class="${m.algo === sc.meilleure_F ? "meilleur" : ""}"><td>${pastilleAlgo(m.algo, c)}<b>${esc(m.nom)}</b></td>
        <td class="num debut-groupe">${nf(m.F, 0)}</td><td class="num">${pct(m.remplissage)}</td><td class="num">${nf(m.lits_max, 0)}</td>
        <td class="num">${nf(m.ecart_type_lits_semaine, 2)}</td><td class="num">${nf(m.ecart_type_lits_week_end, 2)}</td>
        <td class="num">${nf(m.ecart_type_places_semaine, 2)}</td><td class="num">${libelleCapacites(m)}</td>
        <td class="num debut-groupe">${nf(st.score_robustesse, 1)}</td><td class="num">${nf(sm.score_robustesse, 1)}</td>
        <td class="num">${nf(st.lits_nuits_manquants, 1)}</td><td class="num">${nf(sm.lits_nuits_manquants, 1)}</td>
        <td class="num">${nf(st.places_manquantes, 1)}</td><td class="num">${nf(sm.places_manquantes, 1)}</td>
        <td class="num">${nf(st.F_apres, 0)}</td><td class="num">${nf(sm.F_apres, 0)}</td>
        <td class="num">${nf(sm.patients_dates_changees, 1)}</td>
        <td class="num">${texteLiberees(st.liberees_rouvertes, st.patients_liberees, 1)}</td><td class="num">${texteLiberees(sm.liberees_rouvertes, sm.patients_liberees, 1)}</td>
        <td class="num debut-groupe">${nf(m.temps, 1)} s</td></tr>`;
    }).join("");
    const strat = (k) => `${pastilleStrategie(k, c)}${k === "statique" ? "sans coord." : "agents"}`;
    const flux = sc.flux ? `<p class="aide" style="margin:-4px 0 6px">Nouveaux patients pendant les simulations : ${nf(sc.flux.par_jour, 1)}
      par jour ouvré, ${nf(sc.flux.ajoutes, 0)} ajoutés au planning en moyenne (en plus des ${nf(sc.N, 0)} du départ).</p>` : "";
    return `<div class="bloc-scenario"><h3>${esc(sc.nom)}</h3>${flux}
      <div class="defile" style="max-height:none"><table class="table-optim table-groupes"><thead>
        <tr class="groupes-colonnes"><th rowspan="2">Méthode</th>
          <th colspan="7" class="debut-groupe">Planning prévu <span class="discret">— avant imprévus</span></th>
          <th colspan="11" class="debut-groupe">Robustesse <span class="discret">— après imprévus, moyenne sur ${R.nb_tirages} simulations</span></th>
          <th rowspan="2" class="num debut-groupe">Temps</th></tr>
        <tr><th class="num debut-groupe">F avant simulation</th><th class="num">Salles remplies</th><th class="num">Lits max</th>
          <th class="num">σ lits semaine</th><th class="num">σ lits week-end</th><th class="num">σ places ambu.</th><th class="num">Capacités respectées</th>
          <th class="num debut-groupe">Score de robustesse, ${strat("statique")}</th><th class="num">Score de robustesse, ${strat("sma")}</th>
          <th class="num">Lits manquants, ${strat("statique")}</th><th class="num">Lits manquants, ${strat("sma")}</th>
          <th class="num">Places ambu. manquantes, ${strat("statique")}</th><th class="num">Places ambu. manquantes, ${strat("sma")}</th>
          <th class="num">F après, ${strat("statique")}</th><th class="num">F après, ${strat("sma")}</th>
          <th class="num">Dates changées (agents)</th>
          <th class="num">Vacations libérées rouvertes, ${strat("statique")}</th><th class="num">Vacations libérées rouvertes, ${strat("sma")}</th></tr></thead>
        <tbody>${lignes}</tbody></table></div></div>`;
  }).join("");
  $("#resultats-sc").innerHTML = `
    <h3>Détail par méthode</h3>${blocs}
      <p class="aide"><b>Planning prévu</b> : le planning calculé par la méthode, avant tout imprévu (sans les urgences).
        σ = écart-type de l'occupation : lits les nuits du lundi au vendredi, lits les nuits du week-end, places ambulatoires les jours d'ouverture ; plus il est petit, plus l'occupation est régulière.
        Capacités respectées : aucune nuit au-delà des lits, aucun jour au-delà des places ambulatoires.<br>
        <b>Robustesse</b> : le même planning rejoué ${R.nb_tirages} fois avec des imprévus, sans coordination et avec les agents (moyennes).
        Score de robustesse : sur 100 (défini en haut de l'onglet). Lits manquants : lits × nuits au-delà des lits disponibles.
        Places ambu. manquantes : places × jours au-delà des places ambulatoires, les jours d'ouverture seulement. F après : la fonction objectif du planning réellement exécuté.
        Vacations libérées rouvertes : vacations sans patient au départ qui en ont à la fin (entre parenthèses, les patients placés dedans).<br>
        Budget ${R.budget.toLocaleString("fr-FR")} évaluations par méthode, graine ${R.graine}. Ligne surlignée : plus petit F du scénario.${R.score_modifie
        ? " Score de robustesse calculé avec des seuils et des poids modifiés." : ""}</p>`;
}

/* =====================================================================
   Mode Médecin : réglages simplifiés et planning d'ouverture en un clic
   ===================================================================== */
// [clé des paramètres, libellé, indice, type] ; type « pct » : α saisi en % du personnel d'un jour de semaine
const CHAMPS_MEDECIN = {
  "med-hopital": [["date_ouverture", "Ouverture du bloc", "premier jour du planning, un lundi", "date"],
    ["cap_lits_semaine", "Lits en semaine", "nuits du lundi au vendredi, hors jours fériés"],
    ["cap_lits_week_end", "Lits le week-end", "nuits du samedi, du dimanche et des jours fériés"],
    ["cap_ambu", "Places ambulatoires", "par jour d'ouverture (fermé le week-end et les jours fériés)"],
    ["alpha", "Infirmiers le week-end (%)", "par rapport à un jour de semaine. Règle la cible de lits occupés visée en semaine et le week-end : on vise moins de lits le week-end, car il y a moins d'infirmiers pour s'en occuper", "pct"]],
  "med-delais": [["delai_medical", "Délai médical maximal (jours)", "après la demande ; au-delà, le patient est en retard"],
    ["tolerance", "Tolérance (jours)", "jours acceptés après la date souhaitée (valeur proposée pour chaque patient)"],
    ["delai_preparation", "Délai de préparation (jours)", "minimum entre la demande et l'opération"]],
  "med-fenetre": [["avance_max", "Avance maximale (jours)", "on peut opérer jusqu'à ce nombre de jours avant la date souhaitée"],
    ["retard_max", "Report maximal (jours)", "après la tolérance ; aucune date n'est proposée au-delà"]],
  // programme horaire (src/horaires.py) ; la rotation n'en fait pas partie (seul réglage : rotation_dans_mu)
  "med-horaires": [["horaires.convocation_min", "Convocation au plus tôt", "heure à laquelle on peut convoquer le premier patient", "heure"],
    ["horaires.seuil_lourde", "Seuil d'une opération lourde (min)", "à partir de cette durée prévue ; on alterne opérations lourdes et légères"],
    ["horaires.sas_avec_bloc", "Temps en SAS avec bloc locorégional (min)", "le bloc est posé dans le SAS (médiane mesurée : 37 min)"],
    ["horaires.accueil_ambu", "Accueil avant le SAS, ambulatoire (min)", "entre l'arrivée du patient et son passage en SAS"],
    ["horaires.accueil_hospit", "Accueil avant le SAS, hospitalisé (min)", "entre l'arrivée du patient et son passage en SAS"],
    ["horaires.sas_sans_bloc", "Temps en SAS sans bloc locorégional (min)", "médiane mesurée : 20 min"]],
};
// priorités = poids de F, en niveaux : facteur appliqué au poids par défaut (même facteur pour W1 et W1′)
const NIVEAUX_PRIORITE = [["faible", "Faible", 0.2], ["normale", "Normale", 1], ["forte", "Forte", 5]];
const PRIORITES_MEDECIN = [
  { cles: ["W3"], quoi: "Opérer avant le délai médical maximal",
    pourquoi: "chaque jour de retard au-delà du délai médical, multiplié par la priorité du diagnostic" },
  { cles: ["W6"], quoi: "Respecter la date souhaitée", pourquoi: "chaque jour d'avance, ou de report au-delà de la tolérance" },
  { cles: ["W1", "W1b"], quoi: "Équilibrer l'occupation des lits et des places",
    pourquoi: "éviter les pics d'occupation, et occuper moins de lits quand il y a moins d'infirmiers" },
  { cles: ["W2"], quoi: "Regrouper les patients pour libérer des vacations",
    pourquoi: "remplir certaines vacations pour en laisser d'autres entièrement vides" },
];
// hybride Génétique → Recuit (choix de l'utilisateur, 2026-10-20) : meilleur score de robustesse moyen (ordinaire, fermeture,
// crise, avec 3,2 nouveaux patients par jour ouvré), F à égalité avec Recuit → Tabou à la variation entre graines près
const METHODE_MEDECIN = "relais_genetique_recuit";
// niveau correspondant aux poids P (null : réglage personnalisé, fait dans le mode Technicien)
function niveauPriorite(prio, P) {
  const D = etat.init.params_defaut.poids;
  const n = NIVEAUX_PRIORITE.find(([, , f]) => prio.cles.every((k) => Math.abs(P.poids[k] - f * D[k]) <= 1e-9 * Math.max(1, Math.abs(D[k]))));
  return n ? n[0] : null;
}
function afficherReglagesMedecin(P = etat.init && etat.init.params) {
  if (!P || !etat.init.params_defaut) return;
  for (const [zone, champs] of Object.entries(CHAMPS_MEDECIN)) {
    $("#" + zone).innerHTML = champs.map(([cle, lib, indice, type]) => {
      const v = type === "pct" ? Math.round(100 * lireCle(P, cle)) : lireCle(P, cle);
      const texte = type === "date" || type === "heure";
      return `<label><span>${lib}</span><input data-med-cle="${cle}" data-type="${type || "nombre"}"
        type="${type === "date" ? "date" : type === "heure" ? "time" : "number"}"
        ${texte ? "" : `min="0" step="${type === "pct" ? 5 : 1}"`} value="${esc(v ?? "")}">
        <span class="indice">${typeof indice === "function" ? indice(P) : indice}</span></label>`;
    }).join("");
  }
  etat.medNiveaux = PRIORITES_MEDECIN.map((p) => niveauPriorite(p, P));
  $("#med-priorites").innerHTML = PRIORITES_MEDECIN.map((p, k) => `<div class="priorite">
      <div class="quoi">${p.quoi}</div><div class="pourquoi">${p.pourquoi}</div>
      <div class="segments">${NIVEAUX_PRIORITE.map(([n, lib]) =>
        `<button data-prio="${k}" data-niveau="${n}" class="${etat.medNiveaux[k] === n ? "actif" : ""}">${lib}</button>`).join("")}</div>
      ${etat.medNiveaux[k] ? "" : `<div class="perso">Réglage personnalisé dans le mode Technicien (${p.cles.map((c) => `${c} = ${Number(P.poids[c]).toLocaleString("fr-FR", { maximumFractionDigits: 4 })}`).join(", ")}) :
        il est gardé tant qu'aucun niveau n'est choisi.</div>`}</div>`).join("");
  $$("#med-priorites [data-prio]").forEach((b) => b.addEventListener("click", () => {
    etat.medNiveaux[Number(b.dataset.prio)] = b.dataset.niveau;
    $$(`#med-priorites [data-prio="${b.dataset.prio}"]`).forEach((x) => x.classList.toggle("actif", x === b));
  }));
  $("#med-chapitres").innerHTML = `<table><thead><tr><th>Chapitre CIM-10</th><th>Diagnostics</th><th class="num">Séjours (2019-2022)</th><th>Priorité</th></tr></thead><tbody>` +
    etat.init.chapitres.map((c) => `<tr><td><b>${esc(c.code)}</b></td><td>${esc(c.libelle)}</td><td class="num">${c.n.toLocaleString("fr-FR")}</td>
      <td><input type="number" step="0.5" min="0" data-med-chap="${esc(c.code)}" value="${P.poids_chapitres[c.code] ?? 1}"></td></tr>`).join("") + `</tbody></table>`;
  const codes = Object.entries(P.poids_codes || {});
  $("#med-codes").textContent = codes.length ? "Certains diagnostics précis gardent leur propre priorité : " +
    codes.map(([c, w]) => `${c} ${etat.init.libelles_cim3[c] || ""} (${nf(w, 1)})`).join(", ") + "." : "";
}
async function sauverReglagesMedecin() {
  const corps = { poids: {} };
  for (const el of $$("[data-med-cle]")) {
    const t = el.dataset.type, texte = t === "date" || t === "heure", v = texte ? el.value : Number(el.value);
    if (texte ? !v : !(el.value !== "" && v >= 0)) { toast(`Valeur invalide : ${el.closest("label").firstChild.textContent}.`, true); return; }
    const [a, b] = el.dataset.medCle.split(".");
    if (b) { corps[a] = corps[a] || {}; corps[a][b] = v; } else corps[a] = t === "pct" ? v / 100 : v;
  }
  if (new Date(corps.date_ouverture + "T12:00:00").getDay() !== 1) { toast("L'ouverture du bloc doit tomber un lundi.", true); return; }
  const D = etat.init.params_defaut.poids;
  PRIORITES_MEDECIN.forEach((p, k) => {
    const n = NIVEAUX_PRIORITE.find(([cle]) => cle === etat.medNiveaux[k]);
    if (n) p.cles.forEach((c) => (corps.poids[c] = n[2] * D[c]));
  });
  corps.poids_chapitres = {};
  $$("[data-med-chap]").forEach((el) => (corps.poids_chapitres[el.dataset.medChap] = Math.max(0, Number(el.value) || 0)));
  try {
    etat.init.params = await api("/api/parametres", { method: "POST", body: corps });
    $("#f-tol").value = etat.init.params.tolerance;
    afficherReglagesMedecin();
    $("#msg-med").textContent = "Réglages enregistrés : ils s'appliquent au prochain calcul du planning et aux prochains patients.";
    toast("Réglages enregistrés.");
  } catch (e) { toast(e.message, true); }
}
// remplit le formulaire avec les valeurs par défaut, sans enregistrer
function reglagesMedecinParDefaut() {
  const D = etat.init.params_defaut, P = etat.init.params;
  const champs = Object.values(CHAMPS_MEDECIN).flat().map(([cle]) => cle);
  const horaires = { ...P.horaires };
  champs.filter((c) => c.startsWith("horaires.")).forEach((c) => { const k = c.split(".")[1]; horaires[k] = D.horaires[k]; });
  afficherReglagesMedecin({ ...P, ...Object.fromEntries(champs.filter((c) => !c.includes(".")).map((c) => [c, D[c]])),
    horaires, poids: { ...P.poids, ...D.poids }, poids_chapitres: { ...P.poids_chapitres, ...D.poids_chapitres } });
  $("#msg-med").textContent = "Valeurs par défaut affichées : cliquez sur « Enregistrer les réglages » pour les appliquer.";
}
// planning d'ouverture : la méthode recommandée, 3 graines en parallèle, puis adoption de la meilleure solution
async function calculerPlanningMedecin() {
  const n = etat.initiaux ? etat.initiaux.resume.n : 0;
  if (!n) { toast("La liste est vide : générez d'abord les patients.", true); return; }
  try { etat.planning = await api("/api/planning"); } catch (e) { toast(e.message, true); return; }
  if (etat.planning.nb_patients && !confirm(`Le planning actuel (${etat.planning.nb_patients} patients, y compris ceux programmés avec « Attribution patient ») `
    + `sera remplacé par le planning d'ouverture de ces ${n} patients. Continuer ?`)) return;
  const bouton = $("#btn-med-calculer"), zone = $("#prog-med"), t0 = Date.now();
  bouton.disabled = true; zone.hidden = false;
  try {
    const { job } = await api("/api/optimiser", { method: "POST", body: {
      algos: [METHODE_MEDECIN], nb_graines: 3, graine: 0, depart: "aleatoire", instance: { grille: etat.init.params.grille } } });
    let j;
    do {
      await new Promise((r) => setTimeout(r, 600));
      j = await api("/api/job/" + job);
      if (j.etat === "erreur") throw new Error(j.message);
      zone.querySelector(".barre > div").style.width = (100 * (j.progression || 0)) + "%";
      zone.querySelector("span").textContent = `Calcul du planning… ${Math.round((Date.now() - t0) / 1000)} s`;
    } while (j.etat !== "termine");
    etat.resultats = j.resultat;   // visible aussi dans l'onglet Technique
    const r = await api("/api/adopter", { method: "POST", body: { algo: METHODE_MEDECIN } });
    etat.init.params = await api("/api/parametres");
    toast(`Planning d'ouverture calculé : ${r.patients} patients programmés.`
      + (r.ecartes ? ` ${r.ecartes} patient(s) sans vacation possible : détail dans l'onglet Planning.` : ""));
    ouvrirOnglet("planning");
  } catch (e) { toast(e.message, true); }
  bouton.disabled = false; zone.hidden = true;
}

/* =====================================================================
   Démarrage
   ===================================================================== */
async function demarrer() {
  initTheme();
  initMode();
  appliquerDefautsGraphes();
  $$(".onglet").forEach((b) => b.addEventListener("click", () => ouvrirOnglet(b.dataset.onglet)));
  // page de présentation : bouton « ? » de l'en-tête ou clic sur le titre ; ses boutons ouvrent la 1re étape du mode choisi
  $("#btn-presentation").addEventListener("click", () => ouvrirOnglet("presentation"));
  $(".entete .titre").addEventListener("click", () => ouvrirOnglet("presentation"));
  $$("[data-aller-mode]").forEach((b) => b.addEventListener("click", () => {
    appliquerMode(b.dataset.allerMode, false);
    ouvrirOnglet(ONGLETS_MODE[etat.mode][0]);
    window.scrollTo(0, 0);
  }));
  // liens « Ouvrir l'onglet » de la présentation : onglets du mode Technicien
  $$("[data-aller-onglet]").forEach((b) => b.addEventListener("click", () => {
    appliquerMode("technicien", false);
    ouvrirOnglet(b.dataset.allerOnglet);
    window.scrollTo(0, 0);
  }));
  $("#btn-med-sauver").addEventListener("click", sauverReglagesMedecin);
  $("#btn-med-defaut").addEventListener("click", reglagesMedecinParDefaut);
  $("#btn-med-suite").addEventListener("click", () => ouvrirOnglet("initiaux"));
  $("#btn-vers-technicien").addEventListener("click", () => appliquerMode("technicien"));
  $("#btn-med-calculer").addEventListener("click", calculerPlanningMedecin);
  $("#form-patient").addEventListener("submit", proposer);
  $("#explications-attribution").addEventListener("toggle", afficherExplicationsAttribution);
  $("#sem-prec").addEventListener("click", () => { etat.semaine--; afficherSemaine(); });
  $("#sem-suiv").addEventListener("click", () => { etat.semaine++; afficherSemaine(); });
  $("#semaine").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-modif]");
    if (b) { ev.preventDefault(); ouvrirModifPatient(b.dataset.modif); }
  });
  $("#btn-fermer-modif").addEventListener("click", () => ($("#carte-modif-patient").hidden = true));
  ["#prog-debut", "#prog-fin"].forEach((s) => $(s).addEventListener("change", () => (etat.progManuel = true)));
  $("#btn-prog-imprimer").addEventListener("click", imprimerProgramme);
  $("#btn-prog-excel").addEventListener("click", telechargerProgramme);
  $("#btn-vider").addEventListener("click", async () => {
    if (!confirm("Vider tout le planning ?")) return;
    await api("/api/planning/vider", { method: "POST" }); toast("Planning vidé."); chargerPlanning();
  });
  $("#btn-replacer").addEventListener("click", async () => {
    const r = await api("/api/planning/replacer", { method: "POST" }); toast(`${r.replaces} patient(s) replacé(s).`); chargerPlanning();
  });
  $$("#choix-grille button").forEach((b) => b.addEventListener("click", () => { etat.vueGrille = b.dataset.g; afficherGrille(); }));
  $("#btn-utiliser-grille").addEventListener("click", async () => {
    try {
      const r = await api("/api/grille/utiliser", { method: "POST", body: { nom: etat.vueGrille } });
      toast(`${etat.vueGrille === "nouvelle" ? "Grille proposée" : "Ancienne grille"} utilisée par le planning. ${r.replaces} patient(s) replacé(s).`);
      etat.init.params.grille = etat.vueGrille;
      chargerGrille();
    } catch (e) { toast(e.message, true); }
  });
  $("#btn-reconstruire").addEventListener("click", async () => {
    try {
      await api("/api/grille/construire", { method: "POST", body: {
        grille_marge: Number($("#p-marge").value) / 100, grille_periode: [$("#p-debut").value, $("#p-fin").value],
        quantile_urgences: Number($("#p-quantile").value) } });
      etat.vueGrille = "nouvelle"; toast("Grille reconstruite."); chargerGrille();
    } catch (e) { toast(e.message, true); }
  });
  $("#btn-sauver-params").addEventListener("click", sauverParametres);
  $("#btn-sauver-capacites").addEventListener("click", sauverParametres);
  $("#p-heberg").addEventListener("input", afficherCibleHebergement);
  // renvoi vers la fenêtre « Poids par défaut de F » (bas de l'onglet Technique) : l'ouvre et y descend
  $("#lien-poids-defaut")?.addEventListener("click", (e) => {
    e.preventDefault();
    const f = $("#fenetre-optimisation");
    if (!f) return;
    f.open = true;
    $("#carte-poids-defaut").scrollIntoView({ block: "start" });
  });
  $("#btn-defaut-params").addEventListener("click", (ev) => defautSection(["p-poids"],
    "Revenir aux valeurs par défaut de la fonction objectif (poids w_k, ε, z_β, « Pénaliser les capacités ») ?\n"
    + "Les capacités, les délais et les poids w_i ne changent pas.", ev));
  $("#btn-defaut-capacites").addEventListener("click", (ev) => defautSection(["p-heberg", "p-delais"],
    "Revenir aux valeurs par défaut des capacités et des délais (α, lits, places, rotation, délais, horizon, ouverture du bloc) ?\n"
    + "Les poids de la fonction objectif et les poids w_i ne changent pas.", ev));
  $("#btn-sauver-wi").addEventListener("click", sauverPoidsDiagnostic);
  $("#btn-optimiser").addEventListener("click", lancerOptimisation);
  $("#o-grille").addEventListener("change", majAvertissementGrille);
  $("#btn-enregistrer-situation").addEventListener("click", enregistrerSituation);
  $("#p-aleas").addEventListener("input", () => { etat.reglagesPerso = true; marquerNonEnregistree(); });
  ["#flux-actif", "#flux-par-jour"].forEach((sel) => $(sel).addEventListener("input", () => { marquerNonEnregistree(); afficherChargeFlux(); }));
  $$('input[name="liberees"]').forEach((el) => el.addEventListener("change", marquerNonEnregistree));
  $("#p-refus").addEventListener("input", marquerNonEnregistree);
  $("#btn-simuler").addEventListener("click", simulerPlanning);
  $("#btn-robustesse").addEventListener("click", lancerRobustesse);
  $("#btn-scenarios").addEventListener("click", lancerScenarios);
  $$("[data-situation]").forEach((b) => b.addEventListener("click", () => choisirSituation(b.dataset.situation)));
  $$("#choix-methode button").forEach((b) => b.addEventListener("click", () => choisirModele({ methode_prediction: b.dataset.m })));
  $$("#choix-covid button").forEach((b) => b.addEventListener("click", () => choisirModele({ exclure_covid: b.dataset.c === "1" })));
  formuleEnLatex();
  $("#btn-sauver-horaires").addEventListener("click", () => sauverHoraires(false));
  $("#btn-defaut-horaires").addEventListener("click", () => sauverHoraires(true));
  $("#h-reordonner").addEventListener("change", () => sauverHoraires(false));   // prise en compte immédiate
  $("#btn-generer").addEventListener("click", genererInitiaux);
  $("#form-ini").addEventListener("submit", ajouterInitial);
  $("#ini-filtre").addEventListener("input", tableInitiaux);
  $("#ini-table").addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-suppr-ini]");
    if (!b) return;
    try {
      await api("/api/initiaux/" + b.dataset.supprIni, { method: "DELETE" });
      await chargerInitiaux();
    } catch (e) { toast(e.message, true); }
  });
  $("#btn-envoyer").addEventListener("click", () => {
    ouvrirOnglet("technique");
    $("#btn-optimiser").scrollIntoView({ behavior: "smooth", block: "center" });
    lancerOptimisation();
  });
  try {
    etat.init = await api("/api/init");
    remplirFormulaire();
    chargerDerniers();
  } catch (e) { toast("Serveur injoignable : " + e.message, true); }
  const h = location.hash.slice(1);
  if (h === "presentation" || (h !== "attribution" && ONGLETS_MODE[etat.mode].includes(h))) ouvrirOnglet(h);
  else if (etat.mode === "medecin" && h !== "attribution" && etat.init) ouvrirOnglet("reglages");
}
document.addEventListener("DOMContentLoaded", demarrer);
