# Comment ça marche

## Ce sur quoi on s'appuie

| Source | Ce qu'on en tire |
|---|---|
| **Talent Works** (1 600 candidatures) | Postuler dans les **4 premiers jours** d'une offre multiplie jusqu'à ×8 les chances d'entretien ; après ~10 jours, ~1,5 %. Le **matin** (6h-10h) donne le plus de réponses. → veille toutes les 30 min, offres fraîches traitées en premier, envois à partir de 8h. |
| **ResumeGo** (7 287 candidatures, 236 recruteurs) | Une lettre **personnalisée** : **+53 %** de rappels par rapport à pas de lettre. → Évaluateur qui trouve les liens « pourquoi toi », Enquêteur qui trouve des faits sur l'employeur, Juge qui refuse les lettres banales. |
| **Terrain (jobs étudiants)** | Le gérant lit sur son téléphone, en quelques secondes ; les **disponibilités** décident presque tout ; beaucoup de magasins et restos engagent sans publier. → email court (70-120 mots) avec les dispos exactes, lettre de 150-230 mots, Prospecteur quotidien, relance à J+5. |
| **Anthropic** (*Building effective agents*, *multi-agent research system*, *when to use multi-agent*) | Workflows simples codés plutôt qu'IA qui discutent entre elles (3 à 15× plus de tokens) ; découpe par contexte ; routeur ; boucle écrire → noter → corriger ; vérificateur au regard neuf. |
| **AIHawk** (~30 k ⭐), **ApplyPilot** | Profil = seule source de vérité ; enrichissement JSON-LD → regex → IA ; modèle rapide pour noter, qualité pour écrire ; anti-invention ; jamais deux fois le même employeur. Idées seulement (licence AGPL). |
| **Student.be, StudentJob** | Pages publiques de jobs étudiants par ville → lues sans IA (liens d'annonces), l'Extracteur complète. |

## Le circuit (qui parle à qui)

Les agents ne bavardent pas entre eux : chacun lit les offres qui sont à **son** étape dans la base,
fait son travail et les passe à l'étape suivante (champ `statut`). Seul le **Concierge** te parle.

```
 ┌── TROUVER ───────────────────────────────────────────────┐
 │ Student.be · StudentJob · Indeed · pages (toutes les 30 min)│
 │ Éclaireur web (IA, toutes les heures)                      │──► offre « découverte »
 │ Prospecteur : magasins, restos… à contacter spontanément   │
 └───────────────────────────────────────────────────────────┘
 ┌── TRIER ──────────────────────────────────────────────────┐
 │ Extracteur : complète l'offre (horaires, salaire, email)   │
 │ Garde : TES règles strictes, sans IA (« pas d'horeca », 12 €/h)│──► écartée, ou
 │ Évaluateur : adéquation /10 + 2-3 arguments « pourquoi toi »│──► « qualifiée »
 └───────────────────────────────────────────────────────────┘
 ┌── ÉCRIRE ─────────────────────────────────────────────────┐
 │ Enquêteur : faits sur l'employeur + email de recrutement  │
 │ Rédacteur ⇄ Juge : lettre + email, notés /10, 3 essais max │──► carte sur Telegram
 └───────────────────────────────────────────────────────────┘      [✅ Envoyer] [👁 Lettre] [❌ Non]
 ┌── ENVOYER ────────────────────────────────────────────────┐
 │ Contrôleur : mauvais employeur ? texte à trous ? PJ ?      │
 │ Envoyeur : répétition → TA boîte ; sinon → l'employeur     │
 │ Relanceur : une relance polie à J+5                        │
 └───────────────────────────────────────────────────────────┘
 ┌── RÉPONSES ───────────────────────────────────────────────┐
 │ Trieur : refus → silence · question → toi · entretien →    │
 │ Secrétaire : [✅ Jeudi 16h30] [📆 Autre date] → répond au   │
 │             employeur + .ics pour ton agenda               │
 │ Préparateur : la veille, fiche d'entretien                 │
 └───────────────────────────────────────────────────────────┘
 Concierge ⇄ toi (Telegram) · Coach (ton objectif) · Profileur (ton CV) · Transcripteur (vocaux)
 Rapporteur (stats, bilan du soir) · Essayeur (tests sans envoi)
```

**23 agents**, dont 9 sans IA. L'IA rapide (Haiku) trie, note et comprend tes messages ;
l'IA qualité (Sonnet) écrit et juge ce que les recruteurs liront, et prépare tes entretiens.

## Pièces jointes : toujours des PDF

Rien n'est collé dans le corps d'un email : l'email reste court (un gérant le lit en 10 secondes) et tout le reste
voyage en **PDF joint** (`agent_student/pdf.py`) :

| Email | Pièces jointes |
|---|---|
| Candidature | `CV_Prenom_Nom.pdf` + `Lettre_de_motivation_Prenom_Nom.pdf` (mise en page soignée, une page) |
| Mode test | les mêmes + `Rapport_test_12_Prenom_Nom.pdf` (offre, adéquation, avis du Juge) |
| Relance | CV + lettre à nouveau, dans le même fil |
| Sur ta messagerie | lettre, fiche d'entretien, rapport du banc d'essai : en PDF |

Le Rédacteur rend la lettre en **blocs** (expéditeur, destinataire, lieu et date, objet, paragraphes, politesse,
signature) : le code la met en page lui-même. Sans `fpdf2`, un petit générateur intégré produit quand même un PDF.

## Les bonnes infos sur les bons sites

- **Offres** : Student.be, StudentJob, Indeed, Google Jobs gratuitement ; la recherche web (payante) ne perd jamais
  une recherche sur ces sites déjà lus, ni sur LinkedIn, Facebook, Glassdoor et les agrégateurs. Elle vise les pages
  carrières des enseignes et les agences d'intérim étudiant.
- **Annonce retirée** (page 404) : écartée tout de suite, sans IA.
- **Faits sur l'employeur** : quand on connaît son site (email pro, annonce publiée chez lui), le code lit
  gratuitement l'accueil et les pages contact / jobs, et l'IA rapide en tire les faits **sans recherche web**.
  Sinon : recherche web limitée à son site, ou hors sites d'emploi.
- **Adresses email** : jamais devinées. Le code vérifie chacune : lue sur une page du site, ou du domaine officiel ;
  une adresse inventée est remplacée par celle que le site publie vraiment (jobs@, rh@…), ou abandonnée.

## Zéro invention : 4 filets de sécurité

1. **Rédacteur** (Sonnet, avec réflexion) : consigne stricte — uniquement les faits du profil, accords au genre
   indiqué (jamais deviné d'après le prénom), prudence sur les faits trouvés sur le web.
2. **Juge** (Sonnet, sans réflexion) : liste chaque fait sur la personne avec la **citation exacte** du profil qui le prouve.
3. **Le code vérifie les citations** : une « preuve » introuvable dans le profil = invention → réécriture.
   (Une IA ne peut pas valider une invention en inventant aussi la preuve.)
4. **Détecteur de chiffres** (sans IA) : chaque durée, chiffre, horaire et niveau de langue (B1/B2…) doit exister dans le profil
   ou l'offre — vérifié à l'écriture ET juste avant l'envoi.

Tes **disponibilités** font partie du profil vérifié : « libre en semaine » alors que tu as dit « samedis » = invention bloquée.
Même mécanique que dans Agent Avocat, testée contre la vraie API : 4 inventions piégées sur 4 attrapées, aucune fausse alerte.

## Coûts mesurés (API réelle, octobre 2026)

| Étape | Coût |
|---|---|
| Lecture du CV (une fois) | ~0,02 € |
| Note de 8 offres | < 0,001 € (Haiku 5.5) |
| Lettre + Juge | ~0,04 € (0,07 € si une réécriture) |
| Offre écartée par tes règles | 0 € |
| Recherche web | ~0,03 € par requête (≈ 0,01 € par recherche) |
| Faits sur un employeur (site officiel lu par le code) | < 0,001 € |
| Student.be / StudentJob / Indeed | 0 € |

## Ce qui se passe quand tu lances l'application

1. Elle lit tes réglages (email, clé API, Telegram) et démarre les agents en arrière-plan.
2. Premier lancement : le Coach t'écrit sur Telegram, te demande ton CV et ce que tu cherches.
3. Ensuite, sans rien faire de ta part : veille toutes les 30 min, recherche web quelques fois par jour,
   prospection chaque matin, boîte mail toutes les 5 min, envois du lundi au samedi, 8h-19h.
4. Tu reçois une notification seulement quand il y a une décision : une candidature prête, un entretien,
   une question d'un recruteur, une offre à postuler via un site. Le soir, un petit bilan.

## Telegram : réactivité

- Bouton touché ou commande (« ok 12 », « stats ») → réponse **immédiate**, sans IA.
- Phrase libre → « … écrit » s'affiche tout de suite, réponse en 2-5 secondes.
- Vocal → transcrit (« 🎙️ « … » ») puis traité comme un texte.
- Travail long (envoyer, chercher, essai, lire le CV) → « ⏳ » tout de suite, il travaille en
  arrière-plan, tu reçois une **notification** quand c'est fini. Tu peux fermer Telegram entre-temps.

## La phase de test (avant d'écrire au moindre employeur)

1. **Banc d'essai** — `python -m agent_student --banc-essai` : 5 offres types → `donnees/banc_essai.md` (+ `.pdf`)
   avec chaque lettre, sa note et les remarques du Juge. Coût ≈ 0,20 €. Tu juges la qualité à froid.
2. **Essai d'une vraie offre** — sur Telegram : `essai https://…` → toute la chaîne sur cette offre,
   résultat et lettre PDF, **rien n'est envoyé**.
3. **Mode répétition** (par défaut) — tout tourne pour de vrai, mais chaque email arrive dans **ta**
   boîte avec l'objet `[RÉPÉTITION → adresse de l'employeur]`, pièces jointes comprises. Bouton
   « 🚀 L'envoyer pour de vrai » sur celles qui te plaisent.
4. **Mode validation** — `mode validation` : réel, rien ne part sans ton ✅.
5. **Mode auto** — `mode auto` : il envoie seul les lettres notées ≥ 8/10 par le Juge.

## Mémoire : il se souvient de tout, pour presque rien (`memoire.py`)

| Étage | Ce que c'est | Coût |
|---|---|---|
| Fil récent | les 14 derniers messages, mot pour mot, datés | 0 € |
| Carnet de bord | chaque journée écoulée résumée UNE fois (demandes, décisions, n° d'offres, employeurs) ; les 14 derniers jours toujours sous les yeux du Manager | ≈ 0,001 € / jour |
| Recherche | « le magasin dont je t'ai parlé il y a 5 jours » → l'outil `se_souvenir` fouille tout l'historique (messages, offres, emails, rdv, carnet) sans IA et rend les passages datés | 0 € |
| Souvenirs durables | préférences, décisions (« plus jamais chez X »), faits, remarques de style — appliqués à toutes les candidatures | 0 € |

Le bloc mémoire du Manager ne change qu'une fois par jour : il reste dans le cache de prompt (~10 % du prix).
Les lettres que tu valides servent aussi de modèle de ton. « oublie que… » efface un souvenir ;
« tout effacer » remet l'agent à zéro (CV, profil, offres, mémoire), après confirmation.

## Budget

`BUDGET_JOUR_EUR` (2 € par défaut). Les tâches de fond (recherche, prospection, enquête) s'arrêtent à 70 %
pour garder de quoi écrire les lettres et te répondre. Au-delà de 100 %, seules les commandes sans IA marchent
jusqu'au lendemain. `stats` affiche la dépense.

## Tester le code

`python -m unittest discover tests -v` fait tourner tout le circuit en simulation (sans IA ni email réels).

## Un socle pour tous les métiers

Ces deux agents (Student, Avocat) partagent le même moteur. Pour un nouveau métier, seuls changent :

| Propre au métier | Commun (à ne pas dupliquer) |
|---|---|
| `prompts/*.md` (vocabulaire, sites, usages) | `llm.py` (IA, budget, recherche web ciblée) |
| `agents/sources.py` : sources gratuites du métier | `db.py` (machine à états), `orchestrateur.py` |
| `agents/garde.py` : synonymes des secteurs | `pdf.py`, `envoyeur.py`, `relance.py`, `trieur.py`, `secretaire.py` |
| `objectif_exemple.yaml`, `banc_essai.json`, `simulation.py` | `outils.py` (site officiel, vérification des emails) |
