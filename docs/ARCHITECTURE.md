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
| Note de 8 offres | ~0,002 € |
| Lettre + Juge | ~0,04 € (0,07 € si une réécriture) |
| Offre écartée par tes règles | 0 € |
| Recherche web | ~0,10 € (2-3 fois/jour) |
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

1. **Banc d'essai** — `python -m agent_student --banc-essai` : 5 offres types → `donnees/banc_essai.md`
   avec chaque lettre, sa note et les remarques du Juge. Coût ≈ 0,20 €. Tu juges la qualité à froid.
2. **Essai d'une vraie offre** — sur Telegram : `essai https://…` → toute la chaîne sur cette offre,
   résultat et lettre PDF, **rien n'est envoyé**.
3. **Mode répétition** (par défaut) — tout tourne pour de vrai, mais chaque email arrive dans **ta**
   boîte avec l'objet `[RÉPÉTITION → adresse de l'employeur]`, pièces jointes comprises. Bouton
   « 🚀 L'envoyer pour de vrai » sur celles qui te plaisent.
4. **Mode validation** — `mode validation` : réel, rien ne part sans ton ✅.
5. **Mode auto** — `mode auto` : il envoie seul les lettres notées ≥ 8/10 par le Juge.

Tes remarques (« trop long », « parle de mon brevet d'animateur ») vont dans `profil/style.md` et s'appliquent à toutes
les lettres suivantes ; les lettres que tu valides servent de modèle de ton.

## Budget

`BUDGET_JOUR_EUR` (2 € par défaut). Les tâches de fond (recherche, prospection, enquête) s'arrêtent à 70 %
pour garder de quoi écrire les lettres et te répondre. Au-delà de 100 %, seules les commandes sans IA marchent
jusqu'au lendemain. `stats` affiche la dépense.

## Tester le code

`python -m unittest discover tests -v` fait tourner tout le circuit en simulation (sans IA ni email réels).
