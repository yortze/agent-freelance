"""Stockage local en JSON : missions, prospects (avec leur conversation), envois en attente, alertes."""

import json
import re
import threading
import uuid
from datetime import date, datetime, timedelta

import config

VERROU = threading.RLock()  # l'interface web et la tâche de fond écrivent en même temps
STATUTS = ["à contacter", "contacté", "en discussion", "rendez-vous", "gagné", "perdu", "stop"]


def maintenant() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normaliser(prospect: dict) -> dict:
    """Complète les prospects enregistrés par la première version de l'agent."""
    if "email" not in prospect:
        trouve = re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", prospect.get("contact", ""))
        prospect["email"] = trouve.group(0).lower() if trouve else ""
        prospect["autres_contacts"] = prospect.pop("contact", "")
    for cle, defaut in (("pays", ""), ("langue", "fr"), ("statut", "à contacter"), ("relances", 0), ("fil", []), ("notes", [])):
        prospect.setdefault(cle, defaut)
    return prospect


def lire(nom: str) -> list:
    chemin = config.DONNEES / f"{nom}.json"
    elements = json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else []
    return [_normaliser(p) for p in elements] if nom == "prospects" else elements


def ecrire(nom: str, elements: list) -> None:
    config.DONNEES.mkdir(exist_ok=True)
    (config.DONNEES / f"{nom}.json").write_text(json.dumps(elements, ensure_ascii=False, indent=2), encoding="utf-8")


def ajouter(nom: str, element: dict, cle: str) -> str:
    with VERROU:
        elements = lire(nom)
        if element.get(cle) and any((e.get(cle) or "").lower() == element[cle].lower() for e in elements):
            return f"Déjà enregistré ({cle} = {element[cle]})."
        element["ajoute_le"] = maintenant()
        elements.append(element)
        ecrire(nom, elements)
        return f"Enregistré. {len(elements)} {nom} au total."


# --- Prospects et conversations ----------------------------------------------

def trouver_prospect(email: str) -> dict | None:
    email = email.strip().lower()
    return next((p for p in lire("prospects") if (p.get("email") or "").lower() == email), None)


def modifier_prospect(email: str, modification) -> dict:
    """Applique `modification(prospect)` et sauvegarde. Lève une erreur si l'adresse est inconnue."""
    with VERROU:
        prospects = lire("prospects")
        prospect = next((p for p in prospects if (p.get("email") or "").lower() == email.strip().lower()), None)
        if prospect is None:
            raise ValueError(f"Aucun prospect enregistré avec l'adresse {email}. Enregistre-le d'abord.")
        modification(prospect)
        ecrire("prospects", prospects)
        return prospect


def envois_du_jour() -> int:
    aujourd_hui = date.today().isoformat()
    return sum(
        1
        for p in lire("prospects")
        for m in p.get("fil", [])
        if m["sens"] == "envoyé" and m["type"] in ("premier_contact", "relance") and m["date"].startswith(aujourd_hui)
    )


def a_relancer() -> list[dict]:
    """Prospects contactés, sans réponse, dont le délai de relance est passé."""
    relances_max, delai = config.entier("RELANCES_MAX"), config.entier("JOURS_AVANT_RELANCE")
    resultat = []
    for p in lire("prospects"):
        if p.get("statut") != "contacté" or p.get("relances", 0) >= relances_max or not p.get("dernier_envoi"):
            continue
        attente = timedelta(days=delai * (p.get("relances", 0) + 1))
        if datetime.fromisoformat(p["dernier_envoi"]) + attente <= datetime.now():
            resultat.append(p)
    return resultat


def demande_desinscription(texte: str) -> bool:
    debut = texte.strip()[:400].lower()
    return debut.startswith("stop") or bool(re.search(
        r"d[ée]sinscri|unsubscribe|ne plus (me |nous )?(contacter|écrire|ecrire)|remove me|stop contacting", debut
    ))


# --- Envois en attente (mode validation) --------------------------------------

def mettre_en_attente(envoi: dict) -> str:
    with VERROU:
        envois = lire("envois")
        envoi.update(id=uuid.uuid4().hex[:8], cree_le=maintenant())
        envois.append(envoi)
        ecrire("envois", envois)
        return envoi["id"]


def retirer_envoi(identifiant: str) -> dict | None:
    with VERROU:
        envois = lire("envois")
        envoi = next((e for e in envois if e["id"] == identifiant), None)
        ecrire("envois", [e for e in envois if e["id"] != identifiant])
        return envoi


# --- Alertes et journal -------------------------------------------------------

def alerte(titre: str, details: str) -> None:
    with VERROU:
        alertes = lire("alertes")
        alertes.append({"titre": titre, "details": details, "date": maintenant()})
        ecrire("alertes", alertes[-200:])


def journal(texte: str) -> None:
    config.DONNEES.mkdir(exist_ok=True)
    with VERROU, open(config.DONNEES / "journal.log", "a", encoding="utf-8") as f:
        f.write(f"{maintenant()}  {texte.replace(chr(10), ' ')[:500]}\n")


def lire_journal(n: int = 80) -> list[str]:
    chemin = config.DONNEES / "journal.log"
    return chemin.read_text(encoding="utf-8").splitlines()[-n:] if chemin.exists() else []
