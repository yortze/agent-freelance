"""Interface web de l'agent : python web.py, puis http://localhost:8000"""

import json
import queue
import threading
from datetime import date, datetime, timedelta

import anthropic
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

import automate
import config
import outils
import suivi
from agent import Agent

app = FastAPI()
sessions: dict[str, Agent] = {}


class Demande(BaseModel):
    session: str
    message: str = ""
    voix: bool = False  # demande dictée : la réponse sera lue à voix haute

CONSIGNE_VOIX = (
    "\n\n(Demande dictée à la voix : commence ta réponse par une ou deux phrases courtes, faciles à "
    "dire à voix haute, sans lien ni liste. Mets les détails ensuite si besoin.)"
)


def statistiques(prospects: list) -> dict:
    """Chiffres du tableau de bord, calculés sur place (aucun appel à l'API)."""
    depuis_24h = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    messages = [m for p in prospects for m in p.get("fil", [])]
    contactes = [p for p in prospects if any(m["sens"] == "envoyé" for m in p.get("fil", []))]
    jours = [(date.today() - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    return {
        "par_statut": {s: sum(p["statut"] == s for p in prospects) for s in suivi.STATUTS},
        "envois_du_jour": suivi.envois_du_jour(),
        "envois_max": config.entier("ENVOIS_MAX_PAR_JOUR"),
        "contactes": len(contactes),
        "ont_repondu": sum(any(m["sens"] == "reçu" for m in p["fil"]) for p in contactes),
        "recus_24h": sum(m["sens"] == "reçu" and m["date"] >= depuis_24h for m in messages),
        "a_relancer": len(suivi.a_relancer()),
        "semaine": [
            {
                "jour": j,
                "envoyes": sum(m["sens"] == "envoyé" and m["date"].startswith(j) for m in messages),
                "recus": sum(m["sens"] == "reçu" and m["date"].startswith(j) for m in messages),
            }
            for j in jours
        ],
    }


class Reglages(BaseModel):
    MODE_ENVOI: str | None = None
    TOURNEE_AUTO: str | None = None


@app.get("/")
def accueil():
    return FileResponse(config.RACINE / "static" / "index.html")


@app.post("/api/chat")
def chat(demande: Demande):
    agent = sessions.setdefault(demande.session, Agent())
    file: queue.Queue = queue.Queue()

    message = demande.message + (CONSIGNE_VOIX if demande.voix else "")

    def travailler():
        try:
            agent.repondre(message, lambda t, c: file.put({"type": t, "contenu": c}))
        except anthropic.AuthenticationError:
            file.put({"type": "erreur", "contenu": "Clé API invalide ou absente : définis ANTHROPIC_API_KEY."})
        except anthropic.APIStatusError as e:
            file.put({"type": "erreur", "contenu": f"Erreur de l'API ({e.status_code}) : {e.message}"})
        except anthropic.APIConnectionError:
            file.put({"type": "erreur", "contenu": "Impossible de joindre l'API Anthropic."})
        except Exception as e:
            if "authentication" in str(e):
                e = "aucune clé API trouvée. Définis ANTHROPIC_API_KEY puis relance le serveur."
            file.put({"type": "erreur", "contenu": f"Erreur : {e}"})
        finally:
            file.put(None)

    threading.Thread(target=travailler, daemon=True).start()

    def flux():
        while (evenement := file.get()) is not None:
            yield f"data: {json.dumps(evenement, ensure_ascii=False)}\n\n"

    return StreamingResponse(flux(), media_type="text/event-stream")


@app.post("/api/nouveau")
def nouveau(demande: Demande):
    sessions.pop(demande.session, None)
    return {"ok": True}


@app.get("/api/suivi")
def etat_suivi():
    prospects = suivi.lire("prospects")
    return {
        "missions": suivi.lire("missions"),
        "prospects": prospects,
        "stats": statistiques(prospects),
        "expediteur": config.get("EXPEDITEUR_NOM"),
        "audits": outils.lister_audits(),
        "alertes": suivi.lire("alertes"),
        "envois": suivi.lire("envois"),
        "journal": suivi.lire_journal(),
        "reglages": {
            "MODE_ENVOI": config.get("MODE_ENVOI"),
            "TOURNEE_AUTO": config.get("TOURNEE_AUTO"),
            "email_configure": config.email_configure(),
            "en_cours": automate.EN_COURS.locked(),
        },
    }


@app.post("/api/reglages")
def reglages(valeurs: Reglages):
    config.modifier(**valeurs.model_dump(exclude_none=True))
    return {"ok": True}


@app.post("/api/tournee")
def tournee():
    if not config.email_configure():
        raise HTTPException(400, "Configure d'abord l'email dans .env.")
    return {"lancee": automate.lancer()}


@app.post("/api/envois/{identifiant}/{action}")
def envoi(identifiant: str, action: str):
    envoi = suivi.retirer_envoi(identifiant)
    if envoi is None:
        raise HTTPException(404)
    if action == "supprimer":
        suivi.journal(f"Email à {envoi['email']} supprimé sans envoi.")
        return {"ok": True}
    try:
        outils.verifier_envoi(suivi.trouver_prospect(envoi["email"]), envoi["type"], valide_par_moi=True)
        outils.expedier(envoi["email"], envoi["type"], envoi["sujet"], envoi["texte"])
    except Exception as e:
        suivi.mettre_en_attente({k: envoi[k] for k in ("email", "type", "sujet", "texte")})
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/audits/{fichier}")
def audit(fichier: str):
    chemin = (outils.AUDITS / fichier).resolve()
    if chemin.parent != outils.AUDITS.resolve() or not chemin.exists():
        raise HTTPException(404)
    return PlainTextResponse(chemin.read_text(encoding="utf-8"))


if __name__ == "__main__":
    automate.demarrer_en_fond()
    uvicorn.run(app, host="127.0.0.1", port=8000)
