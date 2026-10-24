"""Agent Essayeur — pour tester la qualité AVANT de laisser l'agent écrire aux employeurs.

  • « essai <lien d'une offre> » sur Telegram : toute la chaîne sur CETTE offre (lecture, règles, note,
    enquête, lettre, Juge) et il te montre le résultat. Rien n'est envoyé. Un bouton permet ensuite de l'ajouter.
  • python -m agent_student --banc-essai : 5 offres types (banc_essai.json) → rapport donnees/banc_essai.md
    avec chaque lettre, sa note et les remarques du Juge, et le coût. À relancer après chaque réglage."""
from __future__ import annotations

import json
from pathlib import Path

from .. import config, db, llm, messagerie, outils, pdf
from . import enqueteur, evaluateur, extracteur, garde
from .redacteur import ecrire_pdf, ecrire_une

BANC = Path(__file__).resolve().parent.parent / "banc_essai.json"


def _lire_offre(url: str) -> dict:
    o = {"url": url, "source": "essai"}
    try:
        page = outils.telecharger(url)
    except Exception as e:
        raise RuntimeError(f"je n'arrive pas à ouvrir ce lien ({type(e).__name__})") from e
    o.update({k: v for k, v in (outils.jobposting_jsonld(page) or {}).items() if v})
    texte = outils.texte_html(page)
    o["email_contact"] = o.get("email_contact") or (outils.emails_dans(texte) or [None])[0]
    r = llm.demander("extracteur", "rapide", llm.prompt("extracteur"),
                     json.dumps({"url": url, "deja_connu": o, "page": texte}, ensure_ascii=False, default=str),
                     extracteur.SCHEMA, max_tokens=900)
    for k, v in r.items():
        if v and not o.get(k):
            o[k] = v
    return o


def analyser(o: dict) -> dict:
    """Passe une offre (dict) dans toute la chaîne, sans rien écrire en base. Renvoie le rapport."""
    dom, regles = config.domaine_actif(), config.regles_envoi()
    o = {"id": 0, "publie_le": None, "pourquoi": "[]", "infos_cabinet": "{}", "source": "essai", **o}
    rapport = {"offre": o, "garde": garde.verdict(o, dom, regles)}
    systeme = llm.prompt("evaluateur") + "\n\n## Objectif\n" + json.dumps(dom, ensure_ascii=False) + \
        "\n\n## Profil\n" + config.profil_texte()
    champs = {k: o.get(k) for k in evaluateur.CHAMPS}
    n = llm.demander("evaluateur", "rapide", systeme, json.dumps([champs], ensure_ascii=False, default=str),
                     evaluateur.SCHEMA, max_tokens=600)["notes"][0]
    if n.get("regle_violee") and not n.get("regle"):      # « violation » sans règle précise : on ne la retient pas
        n["regle_violee"] = False
    rapport["evaluation"] = n
    o["score"], o["pourquoi"] = n["score"], json.dumps(n.get("pourquoi") or [], ensure_ascii=False)
    if rapport["garde"] or n.get("regle_violee") or (n.get("score") or 0) < regles["score_minimum"]:
        rapport["candidature"] = None       # offre écartée : on n'écrit pas (et on ne paie pas) de lettre
        rapport["offre"] = o
        return rapport
    if o.get("entreprise") and o["source"] != "spontanee" and not o.get("fictif"):
        try:
            infos, email = enqueteur.enquete(o["entreprise"], o.get("ville"), o.get("titre"), o.get("url"),
                                             o.get("email_contact"))
            o["infos_cabinet"] = json.dumps(infos, ensure_ascii=False)
            o["email_contact"] = o.get("email_contact") or email
        except llm.BudgetDepasse:
            raise
        except Exception as ex:
            rapport["enquete_erreur"] = str(ex)
    rapport["candidature"] = ecrire_une(o)
    rapport["offre"] = o
    return rapport


def texte_rapport(r: dict, avec_lettre: bool = True) -> str:
    o, n, c = r["offre"], r["evaluation"], r["candidature"]
    garde_txt = f"🚫 Tes règles l'excluent : {r['garde']}" if r["garde"] else "✅ Respecte tes règles"
    regle = " (règle stricte violée)" if n.get("regle_violee") else ""
    tete = (f"🧪 *Essai : {o.get('titre')}* — {o.get('entreprise') or '?'}\n{garde_txt}\n"
            f"Adéquation : {n.get('score')}/10{regle} · {n.get('raison') or ''}\n"
            + "".join(f"• {p}\n" for p in n.get("pourquoi") or []))
    if c is None:
        return tete + "➡️ Offre écartée : je n'écris pas de lettre (aucun coût)."
    envoi = f"📧 {o['email_contact']}" if o.get("email_contact") else "🌐 formulaire sur leur site"
    remarques = f"\nRemarques : {'; '.join(c['corrections'][:3])}" if c["corrections"] else ""
    return (tete + f"Lettre : {c['note']}/10 selon le Juge{remarques}\nEnvoi : {envoi}\n\n"
            f"✉️ *{c['objet']}*\n{c['email']}" + (f"\n\n— Lettre —\n{c['lettre']}" if avec_lettre else
                                                   "\n\nLa lettre est dans le PDF ci-dessous."))


def essayer(url: str):
    messagerie.dire("J'analyse cette offre (lecture, règles, note, enquête, lettre, Juge)… ~1 minute.")
    try:
        r = analyser(_lire_offre(url))
    except Exception as e:
        return messagerie.dire(f"⚠️ Essai impossible : {e}")
    dossier = config.DONNEES / "essais"
    dossier.mkdir(parents=True, exist_ok=True)
    o, c = r["offre"], r["candidature"]
    boutons = None
    if c is None:
        return messagerie.dire(texte_rapport(r)[:3900])
    if not r["garde"] and not r["evaluation"]["regle_violee"]:
        with db.tx() as cx:
            cur = cx.execute(
                """INSERT OR IGNORE INTO offres (cle, url, source, titre, entreprise, ville, secteur, contrat, langue,
                   horaires, salaire_horaire, description, email_contact, mode_candidature, score, raison, pourquoi, infos_cabinet, lettre,
                   objet_mail, corps_mail, note_lettre, statut, notifie)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'a_valider',1)""",
                (outils.cle_offre(o["url"], o.get("entreprise"), o.get("titre")), o["url"], "essai", o.get("titre"),
                 o.get("entreprise"), o.get("ville"), o.get("secteur"), o.get("contrat"), o.get("langue"),
                 o.get("horaires"), o.get("salaire_horaire"), o.get("description"), o.get("email_contact"), "email" if o.get("email_contact") else "formulaire",
                 o.get("score"), r["evaluation"]["raison"], o.get("pourquoi"), o.get("infos_cabinet"),
                 c["lettre"], c["objet"], c["email"], c["note"]))
            oid = cur.lastrowid if cur.rowcount else None
        if oid:
            (config.DONNEES / "lettres").mkdir(parents=True, exist_ok=True)
            ecrire_pdf(c.get("structure") or c["lettre"], config.DONNEES / "lettres" / f"lettre_{oid}.pdf")
            db.maj(oid, lettre_json=json.dumps(c["structure"], ensure_ascii=False) if c.get("structure") else None)
            boutons = [[("✅ L'envoyer", f"ok {oid}"), ("❌ Non merci", f"ecarte {oid}")]]
    messagerie.dire(texte_rapport(r, avec_lettre=False)[:3900], boutons)
    messagerie.fichier(ecrire_pdf(c.get("structure") or c["lettre"], dossier / "essai_lettre.pdf"), "Lettre (essai)")


def banc_essai() -> Path:
    """Rapport qualité sur 5 offres types, sans rien envoyer."""
    offres = json.loads(BANC.read_text(encoding="utf-8"))
    depart = llm.depense_du_jour()
    lignes = ["# Banc d'essai — qualité des candidatures\n",
              f"Profil : {len(config.profil_texte())} caractères · mode {config.regles_envoi()['mode']}\n"]
    notes = []

    def un(o):
        try:
            return analyser(o)
        except Exception as e:
            return e

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(3) as ex:          # 3 offres en même temps : ~3× plus rapide
        resultats = list(ex.map(un, offres))
    for i, (o, r) in enumerate(zip(offres, resultats), 1):
        if isinstance(r, Exception):
            lignes.append(f"\n## {i}. {o['titre']} — ÉCHEC : {r}\n")
            continue
        if r["candidature"]:
            notes.append(r["candidature"]["note"])
        lignes.append(f"\n---\n\n## {i}. {o['titre']} — {o['entreprise']}\n\n" + texte_rapport(r).replace("*", "**"))
    cout = llm.depense_du_jour() - depart
    lignes.insert(2, f"**Note moyenne du Juge : {sum(notes) / max(len(notes), 1):.1f}/10** · coût : {cout:.2f} €\n")
    f = config.DONNEES / "banc_essai.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(lignes), encoding="utf-8")
    pdf.document("Banc d'essai — qualité des candidatures", "\n".join(lignes[1:]).replace("**", ""),
                 f.with_suffix(".pdf"))                      # le rapport voyage en PDF
    return f
