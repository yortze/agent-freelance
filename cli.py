"""Discuter avec l'agent dans le terminal : python cli.py"""

import os
import sys

import anthropic

from agent import Agent

GRIS, CYAN, FIN = "\033[90m", "\033[36m", "\033[0m"


def afficher(type_: str, contenu: str) -> None:
    if type_ == "outil":
        print(f"{GRIS}  → {contenu}{FIN}")
    else:
        print(f"\n{contenu}\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Définis d'abord ta clé API : $env:ANTHROPIC_API_KEY = \"sk-ant-...\"")
    agent = Agent()
    print(f"{CYAN}Agent freelance prêt.{FIN} Tape ta demande ('/nouveau' pour repartir de zéro, '/quitter' pour sortir).")
    print(f"{GRIS}Exemples : « trouve-moi 5 missions React en remote », « trouve 3 salons de tatouage à Lyon "
          f"avec un site à refaire », « fais un audit complet de https://exemple.fr »{FIN}\n")
    while True:
        try:
            demande = input(f"{CYAN}toi ›{FIN} ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if demande in ("/quitter", "/q"):
            break
        if demande == "/nouveau":
            agent.reinitialiser()
            print(f"{GRIS}Nouvelle conversation.{FIN}")
            continue
        if not demande:
            continue
        try:
            agent.repondre(demande, afficher)
        except anthropic.AuthenticationError:
            print("Clé API invalide ou absente : définis ANTHROPIC_API_KEY.")
        except anthropic.RateLimitError:
            print("Limite de requêtes atteinte, réessaie dans un instant.")
        except anthropic.APIConnectionError:
            print("Impossible de joindre l'API Anthropic (connexion).")
        except anthropic.APIStatusError as e:
            print(f"Erreur de l'API ({e.status_code}) : {e.message}")
        except Exception as e:
            if "authentication" in str(e):
                e = "aucune clé API trouvée. Définis ANTHROPIC_API_KEY puis relance."
            print(f"Erreur : {e}")


if __name__ == "__main__":
    main()
