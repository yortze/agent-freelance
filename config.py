"""Réglages : fichier .env, plus quelques réglages modifiables depuis l'interface."""

import json
import os
from pathlib import Path

RACINE = Path(__file__).parent
DONNEES = RACINE / "donnees"

DEFAUTS = {
    "MODE_ENVOI": "auto",            # auto : l'agent envoie seul / validation : tu valides chaque email
    "TOURNEE_AUTO": "oui",           # lecture de la boîte, relances et prospection en tâche de fond
    "ENVOIS_MAX_PAR_JOUR": "20",     # premiers contacts + relances (les réponses ne sont pas limitées)
    "RELANCES_MAX": "2",
    "JOURS_AVANT_RELANCE": "4",      # 4 jours avant la 1re relance, 8 avant la 2e
    "PROSPECTS_PAR_TOURNEE": "5",
    "MINUTES_ENTRE_LECTURES": "15",
    "SMTP_HOTE": "smtp.gmail.com",
    "SMTP_PORT": "465",
    "IMAP_HOTE": "imap.gmail.com",
}
MODIFIABLES = ("MODE_ENVOI", "TOURNEE_AUTO")


def _charger_env() -> None:
    fichier = RACINE / ".env"
    if not fichier.exists():
        return
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if ligne and not ligne.startswith("#") and "=" in ligne:
            cle, valeur = ligne.split("=", 1)
            os.environ.setdefault(cle.strip(), valeur.strip().strip('"'))


_charger_env()


def _reglages() -> dict:
    fichier = DONNEES / "reglages.json"
    return json.loads(fichier.read_text(encoding="utf-8")) if fichier.exists() else {}


def _sauver(reglages: dict) -> None:
    DONNEES.mkdir(exist_ok=True)
    (DONNEES / "reglages.json").write_text(json.dumps(reglages, ensure_ascii=False, indent=2), encoding="utf-8")


def get(cle: str) -> str:
    return _reglages().get(cle) or os.environ.get(cle) or DEFAUTS.get(cle, "")


def entier(cle: str) -> int:
    return int(get(cle))


def modifier(**valeurs) -> None:
    reglages = _reglages()
    reglages.update({k: v for k, v in valeurs.items() if k in MODIFIABLES})
    _sauver(reglages)


def etat(cle: str, valeur: str | None = None) -> str:
    """Petite mémoire interne (ex. date de la dernière tournée)."""
    reglages = _reglages()
    if valeur is not None:
        reglages[cle] = valeur
        _sauver(reglages)
    return reglages.get(cle, "")


def email_configure() -> bool:
    return bool(get("EMAIL_ADRESSE") and get("EMAIL_MOT_DE_PASSE") and get("EXPEDITEUR_NOM"))
