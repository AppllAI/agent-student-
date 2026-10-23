"""Couche IA unique pour tous les agents — pensée pour dépenser le moins de tokens possible.

Principes (tirés des guides d'Anthropic et des projets publics qui marchent) :
  1. Deux niveaux de modèle : "rapide" (Haiku, pas cher) pour trier/noter/classer,
     "qualite" (Sonnet) uniquement pour écrire ce que les recruteurs liront.
  2. Sortie TOUJOURS structurée via un outil forcé (`repondre`) → zéro JSON cassé, zéro bavardage.
  3. Prompt système mis en cache (prompt caching) → les gros blocs fixes coûtent ~10 % au 2e appel.
  4. Budget journalier en euros : au-delà, les agents IA s'arrêtent, le reste continue.
  5. Mode SIMULATION : tout le circuit tourne sans clé API (réponses factices réalistes).
"""
from __future__ import annotations

import time
from datetime import date

from . import config, db

MODELES = {
    "rapide": lambda: config.env("MODELE_RAPIDE", "claude-haiku-5-5"),
    "qualite": lambda: config.env("MODELE_QUALITE", "claude-sonnet-5-5"),
}
# Prix approximatifs en € par million de tokens (entrée, sortie) — ajustables dans .env
PRIX = {
    "rapide": tuple(float(x) for x in config.env("PRIX_RAPIDE", "0.92,4.6").split(",")),
    "qualite": tuple(float(x) for x in config.env("PRIX_QUALITE", "1.85,9.2").split(",")),
}


class BudgetDepasse(RuntimeError):
    pass


class IAIndisponible(BudgetDepasse):
    """Crédit Anthropic épuisé ou clé refusée : comme un budget atteint, les agents IA s'arrêtent proprement
    (tout le reste continue) et on réessaie tout seul plus tard."""


def _alerte(cle: str, texte: str, heures: float = 6):
    """Un message important, mais au plus une fois toutes les `heures` (pas de rafale)."""
    from . import messagerie
    if time.time() - float(db.kv_get(f"alerte_{cle}", 0) or 0) > heures * 3600:
        db.kv_set(f"alerte_{cle}", time.time())
        messagerie.dire(texte)


def _verifier_disponible():
    bloque = float(db.kv_get("ia_bloquee_jusqu", 0) or 0)
    if time.time() < bloque:
        raise IAIndisponible(db.kv_get("ia_bloquee_raison") or "IA indisponible pour le moment")


def _bloquer(raison: str, minutes: int = 30):
    db.kv_set("ia_bloquee_jusqu", time.time() + minutes * 60)
    db.kv_set("ia_bloquee_raison", raison)
    raise IAIndisponible(raison)


def _budget_atteint(plafond: float, priorite: str):
    if priorite != "fond":      # la limite « fond » (70 %) est normale ; la limite totale mérite un mot
        _alerte(f"budget_{date.today().isoformat()}",
                f"Budget IA du jour atteint ({plafond:.2f} €). Je reprends demain matin ; les commandes courtes "
                "(stats, ok 12, pause…) marchent toujours. Pour l'augmenter : « budget 5 ».", heures=24)
    raise BudgetDepasse(f"Budget du jour atteint ({plafond:.2f} € pour les tâches {priorite})")


def depense_du_jour() -> float:
    r = db.q1("SELECT COALESCE(SUM(eur),0) AS s FROM couts WHERE date(le)=?", date.today().isoformat())
    return float(r["s"])


def prompt(nom: str) -> str:
    return (config.PROMPTS / f"{nom}.md").read_text(encoding="utf-8")


_client = None


def _api():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(api_key=config.env("ANTHROPIC_API_KEY"), max_retries=3)
    return _client


# Part du budget que les tâches de FOND (recherche, prospection, enquête) peuvent consommer.
# Le reste est réservé à l'essentiel (écrire les lettres, te répondre, gérer les rendez-vous).
PART_FOND = 0.7

# Certains modèles (ex. Sonnet avec réflexion intégrée) refusent qu'on FORCE l'outil `repondre`.
# On le détecte automatiquement et, pour eux, on demande poliment au lieu de forcer.
_SANS_FORCAGE: set[str] = set()
CONSIGNE_OUTIL = "\n\nTermine TOUJOURS en appelant l'outil `repondre` avec ta réponse complète."


def demander(agent: str, tier: str, systeme: str, contenu: str, schema: dict,
             web: bool = False, recherches_max: int = 5, max_tokens: int = 1500,
             priorite: str = "essentiel", pdf: bytes | None = None, reflexion: bool = True) -> dict:
    """Pose une question à l'IA et renvoie un dict conforme à `schema`.
    priorite="fond" : s'arrête plus tôt dans la journée pour garder du budget pour l'essentiel.
    pdf : un document PDF que l'IA lit directement (ex. le CV, même scanné).
    reflexion=False : le modèle répond sans « réfléchir » d'abord (moins cher, plus rapide), si le modèle le permet."""
    if config.simulation():
        from . import simulation

        return simulation.repondre(agent, contenu, schema)

    budget = float(config.env("BUDGET_JOUR_EUR", "3"))
    plafond = budget * (PART_FOND if priorite == "fond" else 1)
    if depense_du_jour() >= plafond:
        _budget_atteint(plafond, priorite)
    _verifier_disponible()

    outil = {"name": "repondre", "description": "Rends ta réponse finale structurée.", "input_schema": schema}
    tools = [outil]
    choix = {"type": "tool", "name": "repondre"}
    if web:
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": recherches_max,
                  "user_location": {"type": "approximate", "country": "BE", "timezone": "Europe/Brussels"}},
                 outil]
        choix = {"type": "auto"}

    if pdf:
        import base64
        premier = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                    "data": base64.b64encode(pdf).decode()}},
                   {"type": "text", "text": contenu}]
    else:
        premier = contenu
    messages = [{"role": "user", "content": premier}]
    modele = MODELES[tier]()
    # Les modèles récents « réfléchissent » avant de répondre : cette réflexion compte dans max_tokens.
    max_tokens = max(max_tokens, 8000 if tier == "qualite" else 3000)
    deja_relance = False
    for tour in range(6):
        if modele in _SANS_FORCAGE:
            choix = {"type": "auto"}
        r = _appel(modele, systeme + CONSIGNE_OUTIL, messages, tools, choix, max_tokens, reflexion)
        _compter(agent, tier, modele, r.usage)
        if r.stop_reason == "max_tokens" and not any(b.type == "tool_use" and b.name == "repondre" for b in r.content):
            # Coupé avant la fin : on recommence avec plus de place, sans garder le morceau tronqué
            max_tokens = min(max_tokens * 2, 20000)   # au-delà, le SDK exige le streaming
            db.journal(agent, f"réponse coupée, nouvel essai avec {max_tokens} jetons")
            continue
        assistant = {"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in r.content]}
        appel = next((b for b in r.content if b.type == "tool_use" and b.name == "repondre"), None)
        if appel is not None:
            rep = dict(appel.input)
            manque = [k for k in schema.get("required", []) if k not in rep]
            if not manque or deja_relance:
                return completer(rep, schema)
            # Réponse incomplète : on la renvoie une fois à l'IA en lui disant ce qui manque
            deja_relance = True
            messages += [assistant, {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": appel.id, "is_error": True,
                "content": f"Réponse incomplète : il manque {', '.join(manque)}. "
                           "Rappelle `repondre` avec TOUS les champs."}]}]
            continue
        # Recherche web en cours (pause) ou réponse sans l'outil : on relance en continuant
        messages.append(assistant)
        if r.stop_reason != "pause_turn":
            messages.append({"role": "user", "content": "Rends maintenant ta réponse avec l'outil `repondre`."})
            choix = {"type": "tool", "name": "repondre"}
    raise RuntimeError(f"{agent} : pas de réponse structurée")


_DEFAUTS = {"array": list, "string": str, "boolean": lambda: False, "object": dict}


def completer(rep: dict, schema: dict) -> dict:
    """Champs obligatoires encore absents : valeur vide du bon type (une liste vide plutôt qu'un plantage)."""
    for k in schema.get("required", []):
        if k not in rep:
            t = (schema.get("properties", {}).get(k, {}) or {}).get("type")
            t = t[0] if isinstance(t, list) else t
            if t in _DEFAUTS:
                rep[k] = _DEFAUTS[t]()
            elif t in ("integer", "number") or (isinstance(t, list) and "null" in t):
                rep[k] = None
    return rep


_SANS_REGLAGE_REFLEXION: set[str] = set()


def _appel(modele, systeme, messages, tools, choix, max_tokens, reflexion=True):
    import anthropic

    for essai in range(5):
        extra = {}
        if not reflexion and modele not in _SANS_REGLAGE_REFLEXION:
            extra["thinking"] = {"type": "between_tools"}     # pas de réflexion préalable
        try:
            return _api().messages.create(
                model=modele, max_tokens=max_tokens,
                system=[{"type": "text", "text": systeme, "cache_control": {"type": "ephemeral"}}],
                messages=messages, tools=tools, tool_choice=choix, **extra,
            )
        except anthropic.AuthenticationError:
            _alerte("cle", "Ta clé API Claude est refusée (supprimée ou mal copiée). Crée-en une sur "
                           "console.anthropic.com → API Keys et colle-la dans ⚙ Réglages. Le reste continue.")
            _bloquer("clé API refusée")
        except anthropic.BadRequestError as e:
            if "credit balance" in str(e).lower() or "billing" in str(e).lower():
                _alerte("credit", "⚠️ Ton crédit Claude est épuisé : plus de lettres ni de réponses intelligentes "
                                  "pour l'instant. Recharge sur console.anthropic.com → Billing ; je reprends tout "
                                  "seul dans les 30 minutes qui suivent. Rien n'est perdu.")
                _bloquer("crédit Anthropic épuisé")
            if "thinking" in str(e) and extra:
                _SANS_REGLAGE_REFLEXION.add(modele)   # ce modèle ne se règle pas : on n'insiste pas
                continue
            if "tool_choice" in str(e) and choix.get("type") != "auto":
                _SANS_FORCAGE.add(modele)          # ce modèle ne veut pas d'outil forcé : on passe en « auto »
                choix = {"type": "auto"}
                continue
            raise
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError):
            if essai >= 3:
                raise
            time.sleep(5 * 2 ** essai)


def _compter(agent, tier, modele, u):
    cache_lu = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_ecrit = getattr(u, "cache_creation_input_tokens", 0) or 0
    p_in, p_out = PRIX[tier]
    eur = (u.input_tokens * p_in + cache_ecrit * p_in * 1.25 + cache_lu * p_in * 0.1
           + u.output_tokens * p_out) / 1_000_000
    stu = getattr(u, "server_tool_use", None)
    recherches = (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
    eur += recherches * 0.01          # recherche web : ~10 $ les 1000
    with db.tx() as c:
        c.execute("INSERT INTO couts (agent, modele, entree, sortie, cache, eur) VALUES (?,?,?,?,?,?)",
                  (agent, modele, u.input_tokens + cache_ecrit, u.output_tokens, cache_lu, eur))


def agir(agent: str, tier: str, systeme: str, messages: list, outils: list, executer, max_tours: int = 8,
         max_tokens: int = 2500) -> str:
    """Boucle « agent » : l'IA appelle autant d'outils que nécessaire (chercher, envoyer, changer un réglage…),
    voit leurs résultats, puis rend sa réponse finale en texte. `executer(nom, args) -> str`."""
    budget = float(config.env("BUDGET_JOUR_EUR", "3"))
    if depense_du_jour() >= budget:
        _budget_atteint(budget, "essentiel")
    _verifier_disponible()
    modele = MODELES[tier]()
    for _ in range(max_tours):
        r = _appel(modele, systeme, messages, outils, {"type": "auto"}, max_tokens, reflexion=False)
        _compter(agent, tier, modele, r.usage)
        messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in r.content]})
        appels = [b for b in r.content if b.type == "tool_use"]
        if not appels:
            return "\n".join(b.text for b in r.content if b.type == "text").strip()
        resultats = []
        for b in appels:
            try:
                sortie = executer(b.name, dict(b.input or {}))
                erreur = False
            except Exception as e:
                sortie, erreur = f"ÉCHEC : {type(e).__name__}: {e}", True
            resultats.append({"type": "tool_result", "tool_use_id": b.id, "content": str(sortie)[:6000] or "ok",
                              "is_error": erreur})
        messages.append({"role": "user", "content": resultats})
    return ""
