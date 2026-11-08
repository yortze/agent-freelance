"""Envoi (SMTP) et lecture (IMAP) des emails."""

import email
import imaplib
import re
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from bs4 import BeautifulSoup

import config


def envoyer(a: str, sujet: str, texte: str, en_reponse_a: str = "") -> str:
    """Envoie un email texte et renvoie son Message-ID (pour garder le fil)."""
    adresse = config.get("EMAIL_ADRESSE")
    msg = EmailMessage()
    msg["From"] = formataddr((config.get("EXPEDITEUR_NOM"), adresse))
    msg["To"] = a
    msg["Subject"] = sujet
    msg["Message-ID"] = make_msgid(domain=adresse.split("@")[-1])
    if en_reponse_a:
        msg["In-Reply-To"] = en_reponse_a
        msg["References"] = en_reponse_a
    msg.set_content(texte)

    hote, port = config.get("SMTP_HOTE"), config.entier("SMTP_PORT")
    contexte = ssl.create_default_context()
    if port == 465:
        serveur = smtplib.SMTP_SSL(hote, port, context=contexte, timeout=30)
    else:
        serveur = smtplib.SMTP(hote, port, timeout=30)
        serveur.starttls(context=contexte)
    with serveur:
        serveur.login(adresse, config.get("EMAIL_MOT_DE_PASSE"))
        serveur.send_message(msg)
    return msg["Message-ID"]


def _corps(msg) -> str:
    texte, html = "", ""
    for partie in msg.walk():
        if partie.get_content_disposition() == "attachment":
            continue
        if partie.get_content_type() == "text/plain" and not texte:
            texte = partie.get_payload(decode=True).decode(partie.get_content_charset() or "utf-8", "replace")
        elif partie.get_content_type() == "text/html" and not html:
            html = partie.get_payload(decode=True).decode(partie.get_content_charset() or "utf-8", "replace")
    if not texte and html:
        texte = BeautifulSoup(html, "html.parser").get_text("\n")
    # On garde la nouvelle réponse, sans l'historique cité en dessous
    lignes = []
    for ligne in texte.splitlines():
        if re.match(r"^\s*(Le .+a écrit|On .+wrote|De ?: |From: |-{2,} ?(Original|Message d'origine))", ligne):
            break
        if not ligne.startswith(">"):
            lignes.append(ligne)
    return "\n".join(lignes).strip()[:6000]


def lire_nouveaux(expediteurs: set[str]) -> list[dict]:
    """Renvoie les emails non lus venant d'expéditeurs connus, et les marque comme lus.

    Les autres emails de la boîte ne sont ni lus ni modifiés.
    """
    nouveaux = []
    with imaplib.IMAP4_SSL(config.get("IMAP_HOTE")) as imap:
        imap.login(config.get("EMAIL_ADRESSE"), config.get("EMAIL_MOT_DE_PASSE"))
        imap.select("INBOX")
        _, resultat = imap.search(None, "UNSEEN")
        for numero in resultat[0].split():
            _, entete = imap.fetch(numero, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            expediteur = parseaddr(email.message_from_bytes(entete[0][1])["From"] or "")[1].lower()
            if expediteur not in expediteurs:
                continue
            _, donnees = imap.fetch(numero, "(RFC822)")  # marque aussi l'email comme lu
            msg = email.message_from_bytes(donnees[0][1])
            nouveaux.append({
                "de": expediteur,
                "sujet": str(make_header(decode_header(msg["Subject"] or ""))),
                "message_id": msg["Message-ID"] or "",
                "texte": _corps(msg),
            })
    return nouveaux
