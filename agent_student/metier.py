"""Ce qui est propre au MÉTIER de cet agent. Le reste du code (le moteur) est identique d'un agent à l'autre :
pour créer l'agent d'un nouveau métier, on copie le dépôt et on adapte ce fichier, les prompts (prompts/*.md),
les synonymes de la Garde (agents/garde.py) et l'objectif d'exemple (objectif_exemple.yaml)."""

NOM = "Agent Student"
POSTE_PAR_DEFAUT = "job étudiant"
# Sources spécialisées, en plus des sources communes (EURES, Forem, recruteurs, Indeed, Google Jobs, alertes, web)
SOURCES = ("listes_etudiantes",)
# Sites déjà lus gratuitement : la recherche web (payante) n'y perd jamais une recherche
DEJA_LUS = ["student.be", "studentjob.be"]
