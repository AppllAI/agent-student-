# Notes pour Claude

- Projet en français (code, commentaires, messages). Python 3.11+, dépendances minimales.
- Le flux passe par la machine à états de `agent_student/db.py` : toute avancée d'offre via `db.changer_statut`.
- Tout appel IA passe par `llm.demander` (sortie forcée par outil, cache, budget). Tiers : `rapide` / `qualite`.
- Chaque nouvel agent IA doit avoir sa réponse factice dans `simulation.py`, sinon les tests cassent.
- Les prompts sont dans `agent_student/prompts/*.md`.
- Avant de livrer : `python -m unittest discover tests -v` doit passer.
- Ne jamais ajouter d'automatisation de LinkedIn/Jobat/StepStone (CGU) ni de code sous licence AGPL.
