# Agent Student 🎓🤖

Une équipe d'agents IA qui **cherche des jobs étudiants en Belgique, écrit un message sur mesure pour chacun,
postule, relance, trie les réponses** — et ne te dérange sur Telegram que quand il faut décider :
« Boutique Lumen te propose un entretien samedi à 10h, tu confirmes ? »

Tu parles à l'agent comme à quelqu'un, **à l'écrit ou en vocal**. Les refus ? Tu n'en entends même pas parler.

## Comment ça se passe pour toi

1. Tu ouvres l'application, tu remplis : email, mot de passe d'application, clé API, jeton Telegram.
2. Sur Telegram, l'agent te demande ton CV (PDF, même court) et ce que tu cherches :
   « vente ou événementiel, Bruxelles, pas d'horeca, dispo le samedi et le mercredi aprem, tout l'été ».
3. Il cherche en continu (Student.be et StudentJob en FR/NL pour chacune de tes villes, Indeed, Google Jobs —
   qui regroupe les agences d'intérim —, sites des enseignes, magasins à contacter spontanément),
   écrit un email + une courte lettre, les fait vérifier, puis t'envoie des cartes : **[✅ Envoyer] [👁 Lettre] [❌ Non]**.
4. Il envoie, relance après 5 jours, lit les réponses.
5. Entretien proposé → **[✅ Samedi 10h] [📆 Autre date]** → il répond à l'employeur, t'envoie le rdv
   pour ton agenda, et la veille une fiche pour préparer l'entretien.

On démarre en **mode test** 🧪 : l'agent fait tout seul et envoie chaque candidature (CV + lettre en pièces
jointes) à **l'adresse email écrite dans ton CV**, jamais aux employeurs. Rien sur Telegram. Quand tout te plaît :
« mode validation », puis « vrai tout » pour envoyer pour de vrai celles que tu as reçues en test.
Détails : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## L'application

Télécharge la dernière version dans l'onglet **Releases** de ce dépôt :
- **Windows** : `AgentStudent-Windows.exe` → double-clic. Rien à extraire.
  Si Windows dit « a protégé votre ordinateur » : *Informations complémentaires* → *Exécuter quand même*.
- **Mac** : `AgentStudent-Mac.dmg` → glisse *Agent Student* dans *Applications*. 1er lancement : clic droit → *Ouvrir*.

Au premier lancement : **adresse email**, **mot de passe d'application**, **clé API Claude**, puis le jeton
de ton bot Telegram (2 min avec @BotFather). Un QR code s'affiche : tu le scannes, c'est relié.

⚠️ Si tu utilises aussi Agent Avocat : crée un **autre bot Telegram** (un même bot dans deux apps = conflit)
et, idéalement, une **autre adresse email**. La clé API peut être la même.

## Ce qu'il ne fait pas (exprès)

- **Pas de remplissage automatique de LinkedIn / Jobat / StepStone / Student.be** : interdit par leurs conditions.
  Pour ces offres, il te donne le lien + le message prêt, tu colles, tu réponds « fait 12 ».
- **Il n'invente rien** : ni expérience, ni langue, ni **disponibilité**. Tout est vérifié contre ton profil.
- **Pas de spam** : max 25 envois/jour, jamais deux fois le même employeur en 45 jours (`config/objectif.yaml`).

## Développeur

`python -m unittest discover tests -v` — test complet du circuit en simulation (sans clé API).
`python -m agent_student --canal console` — discuter avec l'agent dans le terminal.
