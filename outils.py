"""Outils de l'agent : audit de site, suivi des missions / prospects, emails aux clients."""

import contextvars
import ipaddress
import json
import os
import re
import socket
import time
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

import config
import messagerie
import suivi

AUDITS = config.RACINE / "audits"
USER_AGENT = "Mozilla/5.0 (compatible; AuditFreelance/1.0)"

# Pendant une réponse ou une relance automatique, l'agent ne peut écrire qu'à ce client
DESTINATAIRE = contextvars.ContextVar("destinataire", default=None)

FIN_DE_MESSAGE = {
    "fr": "\n\n—\nSi vous ne souhaitez plus recevoir de message de ma part, répondez simplement « STOP ».",
    "en": "\n\n—\nIf you'd rather not hear from me again, just reply \"STOP\".",
}


# --- Définitions envoyées à Claude -------------------------------------------

def _schema(props: dict) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


TEXTE = {"type": "string"}
EMAIL = {"type": "string", "description": "Adresse email du prospect, telle qu'enregistrée."}

DEFINITIONS = [
    {
        "name": "auditer_site",
        "description": (
            "Analyse technique d'un site web : HTTPS, temps de réponse, poids, SEO "
            "(title, meta description, h1, sitemap…), accessibilité de base, mobile, "
            "réseaux sociaux et, si demandé, scores Lighthouse via PageSpeed Insights "
            "(plus lent, ~30 s). Renvoie des mesures brutes en JSON à interpréter."
        ),
        "input_schema": _schema({
            "url": {**TEXTE, "description": "URL complète, ex. https://exemple.fr"},
            "lighthouse": {"type": "boolean", "description": "Ajouter les scores Lighthouse mobile."},
        }),
        "strict": True,
    },
    {
        "name": "enregistrer_mission",
        "description": "Ajoute une offre de mission freelance trouvée au fichier de suivi.",
        "input_schema": _schema({
            "titre": TEXTE,
            "plateforme": {**TEXTE, "description": "Malt, Codeur.com, LinkedIn…"},
            "url": TEXTE,
            "client": {**TEXTE, "description": "Nom du client s'il est connu, sinon chaîne vide."},
            "budget": {**TEXTE, "description": "Budget ou TJM annoncé, sinon chaîne vide."},
            "resume": {**TEXTE, "description": "Deux ou trois phrases sur le besoin."},
            "pertinence": {"type": "integer", "description": "Adéquation avec le profil, de 1 à 5."},
            "pourquoi": {**TEXTE, "description": "Pourquoi cette mission correspond (ou pas)."},
        }),
        "strict": True,
    },
    {
        "name": "enregistrer_prospect",
        "description": (
            "Ajoute un client potentiel au fichier de suivi (statut « à contacter »). "
            "Obligatoire avant de lui écrire avec envoyer_email."
        ),
        "input_schema": _schema({
            "nom": TEXTE,
            "site": {**TEXTE, "description": "Site web, ou chaîne vide s'il n'en a pas."},
            "secteur": TEXTE,
            "ville": TEXTE,
            "pays": TEXTE,
            "langue": {"type": "string", "enum": ["fr", "en"], "description": "Langue à utiliser avec ce client."},
            "email": {**TEXTE, "description": "Email professionnel publié par l'entreprise, sinon chaîne vide."},
            "autres_contacts": {**TEXTE, "description": "Téléphone, WhatsApp, page Facebook… publiés, sinon chaîne vide."},
            "problemes": {**TEXTE, "description": "Problèmes constatés (site lent, absent, pas mobile…)."},
            "source": {**TEXTE, "description": "Où tu as trouvé l'entreprise et son email (URL)."},
        }),
        "strict": True,
    },
    {
        "name": "envoyer_email",
        "description": (
            "Envoie un email à un prospect enregistré : premier contact, réponse à son message, ou relance. "
            "Des garde-fous s'appliquent (limite par jour, relances max, désinscription). "
            "Pour une réponse ou une relance, le sujet est repris du fil automatiquement."
        ),
        "input_schema": _schema({
            "email": EMAIL,
            "type": {"type": "string", "enum": ["premier_contact", "reponse", "relance"]},
            "sujet": TEXTE,
            "texte": {**TEXTE, "description": "Corps du message en texte brut, signé."},
        }),
        "strict": True,
    },
    {
        "name": "lire_conversation",
        "description": "Renvoie la fiche d'un prospect et tout l'historique des emails échangés.",
        "input_schema": _schema({"email": EMAIL}),
        "strict": True,
    },
    {
        "name": "changer_statut",
        "description": "Met à jour le statut d'un prospect, avec une note.",
        "input_schema": _schema({
            "email": EMAIL,
            "statut": {"type": "string", "enum": suivi.STATUTS},
            "note": TEXTE,
        }),
        "strict": True,
    },
    {
        "name": "alerter_moi",
        "description": (
            "Prévient le freelance qu'une décision lui revient : client prêt à signer, demande de devis ferme, "
            "rendez-vous à caler, question à laquelle tu ne peux pas répondre, problème."
        ),
        "input_schema": _schema({"titre": TEXTE, "details": TEXTE}),
        "strict": True,
    },
    {
        "name": "sauver_audit",
        "description": "Enregistre un rapport d'audit rédigé (Markdown) dans le dossier audits/.",
        "input_schema": _schema({
            "url": TEXTE,
            "rapport": {**TEXTE, "description": "Rapport complet en Markdown."},
        }),
        "strict": True,
    },
    {
        "name": "lister_suivi",
        "description": "Liste ce qui est déjà enregistré, pour éviter les doublons ou faire le point.",
        "input_schema": _schema({
            "quoi": {"type": "string", "enum": ["missions", "prospects", "audits", "alertes"]},
        }),
        "strict": True,
    },
]


# --- Suivi --------------------------------------------------------------------

def enregistrer_mission(**mission) -> str:
    return suivi.ajouter("missions", mission, "url")


def enregistrer_prospect(**prospect) -> str:
    prospect.update(email=prospect["email"].strip().lower(), statut="à contacter", relances=0, fil=[], notes=[])
    return suivi.ajouter("prospects", prospect, "email" if prospect["email"] else "site")


def lister_audits() -> list:
    if not AUDITS.exists():
        return []
    return [
        {"fichier": f.name, "date": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="minutes")}
        for f in sorted(AUDITS.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    ]


def lister_suivi(quoi: str) -> str:
    if quoi == "audits":
        elements = lister_audits()
    elif quoi == "prospects":
        elements = [
            {k: p.get(k) for k in ("nom", "site", "pays", "email", "statut", "relances")} | {"messages": len(p.get("fil", []))}
            for p in suivi.lire("prospects")
        ]
    else:
        elements = suivi.lire(quoi)
    return json.dumps(elements, ensure_ascii=False) if elements else f"Aucun élément dans {quoi}."


def lire_conversation(email: str) -> str:
    prospect = suivi.trouver_prospect(email)
    if prospect is None:
        raise ValueError(f"Aucun prospect avec l'adresse {email}.")
    return json.dumps({k: v for k, v in prospect.items() if k != "message_id"}, ensure_ascii=False)


def changer_statut(email: str, statut: str, note: str) -> str:
    def maj(p):
        p["statut"] = statut
        p.setdefault("notes", []).append(f"{suivi.maintenant()} : {statut}. {note}")

    suivi.modifier_prospect(email, maj)
    suivi.journal(f"Statut de {email} → {statut} ({note})")
    return f"Statut mis à jour : {statut}."


def alerter_moi(titre: str, details: str) -> str:
    suivi.alerte(titre, details)
    suivi.journal(f"Alerte : {titre}")
    if (destinataire := config.get("EMAIL_ALERTES")) and config.email_configure():
        try:
            messagerie.envoyer(destinataire, f"[Agent] {titre}", details)
        except Exception as e:
            suivi.journal(f"Alerte non envoyée par email : {e}")
    return "Le freelance est prévenu."


def sauver_audit(url: str, rapport: str) -> str:
    AUDITS.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", urlparse(url).netloc.lower()).strip("-") or "site"
    chemin = AUDITS / f"{slug}-{date.today().isoformat()}.md"
    chemin.write_text(rapport, encoding="utf-8")
    return f"Rapport enregistré : audits/{chemin.name}"


# --- Emails aux clients -------------------------------------------------------

def verifier_envoi(prospect: dict, type_: str, valide_par_moi: bool = False) -> None:
    """Garde-fous appliqués à chaque email, quel que soit le mode. Lève une erreur si l'envoi est refusé.

    `valide_par_moi` : le freelance a relu l'email lui-même (mode validation).
    """
    email, statut = prospect["email"], prospect.get("statut")
    if not config.email_configure():
        raise ValueError("L'email n'est pas configuré (EMAIL_ADRESSE, EMAIL_MOT_DE_PASSE, EXPEDITEUR_NOM dans .env).")
    if statut in ("stop", "perdu"):
        raise ValueError(f"Envoi refusé : {email} est au statut « {statut} ».")
    if (impose := DESTINATAIRE.get()) and impose != email:
        raise ValueError(f"Envoi refusé : dans cette tâche, tu ne peux écrire qu'à {impose}.")
    if type_ == "premier_contact" and statut != "à contacter":
        raise ValueError(f"{email} a déjà été contacté (statut « {statut} »).")
    site = prospect.get("site")
    if type_ == "premier_contact" and site and not valide_par_moi \
            and time.time() - DERNIERS_AUDITS.get(_domaine(site), 0) > 24 * 3600:
        raise ValueError(
            f"Audite d'abord {site} avec auditer_site et vérifie chaque point sur la page : "
            "un premier contact ne doit citer que des problèmes vérifiés aujourd'hui."
        )
    if type_ == "relance":
        if statut != "contacté":
            raise ValueError(f"Pas de relance : {email} est au statut « {statut} ».")
        if prospect.get("relances", 0) >= config.entier("RELANCES_MAX"):
            raise ValueError(f"Nombre maximal de relances atteint pour {email}.")
    if type_ == "reponse" and not any(m["sens"] == "reçu" for m in prospect.get("fil", [])):
        raise ValueError(f"{email} ne t'a encore rien écrit : utilise premier_contact ou relance.")
    if type_ != "reponse" and suivi.envois_du_jour() >= config.entier("ENVOIS_MAX_PAR_JOUR"):
        raise ValueError("Limite d'envois du jour atteinte. Réessaie demain.")


def envoyer_email(email: str, type: str, sujet: str, texte: str) -> str:
    prospect = suivi.trouver_prospect(email)
    if prospect is None:
        raise ValueError(f"Aucun prospect avec l'adresse {email}. Enregistre-le d'abord.")
    verifier_envoi(prospect, type)
    if type != "reponse":
        texte += FIN_DE_MESSAGE.get(prospect.get("langue"), FIN_DE_MESSAGE["fr"])
    if type != "premier_contact" and prospect.get("fil"):
        sujet = "Re: " + re.sub(r"^((re|tr|fwd?)\s*:\s*)+", "", prospect["fil"][0]["sujet"], flags=re.I)

    if config.get("MODE_ENVOI") == "validation":
        identifiant = suivi.mettre_en_attente({"email": prospect["email"], "type": type, "sujet": sujet, "texte": texte})
        suivi.journal(f"Email à valider pour {email} ({type}) : {sujet}")
        return f"Email préparé et mis en attente : le freelance doit le valider (id {identifiant})."
    return expedier(prospect["email"], type, sujet, texte)


def expedier(email: str, type_: str, sujet: str, texte: str) -> str:
    fil = suivi.trouver_prospect(email).get("fil", [])
    en_reponse_a = next((m["message_id"] for m in reversed(fil) if m.get("message_id")), "")
    message_id = messagerie.envoyer(email, sujet, texte, en_reponse_a)

    def maj(p):
        p.setdefault("fil", []).append({
            "sens": "envoyé", "type": type_, "date": suivi.maintenant(),
            "sujet": sujet, "texte": texte, "message_id": message_id,
        })
        p["dernier_envoi"] = suivi.maintenant()
        if type_ == "premier_contact":
            p["statut"] = "contacté"
        elif type_ == "relance":
            p["relances"] = p.get("relances", 0) + 1

    suivi.modifier_prospect(email, maj)
    suivi.journal(f"Email envoyé à {email} ({type_}) : {sujet}")
    return "Email envoyé."


# --- Audit de site ------------------------------------------------------------

def _verifier_hote(requete: httpx.Request) -> None:
    """Refuse les adresses locales ou privées (y compris après une redirection)."""
    hote = requete.url.host
    for info in socket.getaddrinfo(hote, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"Adresse non publique refusée : {hote}")


def _texte(balise) -> str:
    return balise.get_text(" ", strip=True) if balise else ""


def _existe(client: httpx.Client, url: str) -> bool:
    try:
        return client.get(url).status_code == 200
    except httpx.HTTPError:
        return False


def _lighthouse(url: str) -> dict:
    params = [("url", url), ("strategy", "mobile")] + [
        ("category", c) for c in ("performance", "accessibility", "best-practices", "seo")
    ]
    if cle := os.environ.get("PAGESPEED_API_KEY"):
        params.append(("key", cle))
    try:
        r = httpx.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params=params, timeout=90)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return {"erreur": f"PageSpeed indisponible ({e}). Ajoute PAGESPEED_API_KEY pour éviter les limites."}
    lh = r.json()["lighthouseResult"]
    audits = lh["audits"]
    metriques = ("largest-contentful-paint", "cumulative-layout-shift", "total-blocking-time", "first-contentful-paint")
    return {
        "scores": {k: round(v["score"] * 100) for k, v in lh["categories"].items() if v["score"] is not None},
        "metriques": {m: audits[m]["displayValue"] for m in metriques if m in audits},
        "pistes": [
            a["title"] for a in audits.values()
            if a.get("details", {}).get("type") == "opportunity" and a.get("score") is not None and a["score"] < 0.9
        ][:8],
    }


CONSTRUCTEURS = {
    "Squarespace": "squarespace", "Wix": "wix.com", "Shopify": "cdn.shopify.com", "Webflow": "webflow",
    "WordPress": "wp-content", "GoDaddy": "godaddy", "Jimdo": "jimdo", "Weebly": "weebly",
}

PRECAUTIONS = [
    "Mesures automatiques sur le HTML reçu par un robot : vérifie chaque point sur la page avant de l'affirmer.",
    "Un alt vide (alt=\"\") est valide pour une image décorative, et les constructeurs de sites le génèrent "
    "souvent : ce n'est pas une preuve que les images n'ont pas de description.",
    "Un title court n'est pas un défaut si le h1 et le nom de domaine contiennent déjà le métier et la ville.",
    "Sur un constructeur (Squarespace, Wix, Shopify…), le nombre de scripts et une partie de la performance "
    "dépendent de la plateforme : ne les reproche pas au client.",
]

# Domaine → heure du dernier audit : un premier contact exige un audit récent du site
DERNIERS_AUDITS: dict[str, float] = {}


def _domaine(url: str) -> str:
    hote = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    return hote.removeprefix("www.")


def auditer_site(url: str, lighthouse: bool) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    entetes = {"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"}
    with httpx.Client(
        headers=entetes, follow_redirects=True, timeout=20, event_hooks={"request": [_verifier_hote]}
    ) as client:
        debut = time.perf_counter()
        r = client.get(url)
        duree = time.perf_counter() - debut
        final = str(r.url)
        base = f"{r.url.scheme}://{r.url.host}"
        robots, sitemap = _existe(client, base + "/robots.txt"), _existe(client, base + "/sitemap.xml")

    soup = BeautifulSoup(r.text, "html.parser")
    meta = lambda **attrs: (soup.find("meta", attrs=attrs) or {}).get("content", "")
    images = soup.find_all("img")
    scripts = soup.find_all("script", src=True)
    liens = [a.get("href", "") for a in soup.find_all("a")]
    texte = soup.get_text(" ", strip=True)
    annees = [int(a) for a in re.findall(r"(?:©|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", texte, re.I)]
    titre = _texte(soup.title)
    description = meta(name="description")
    generateur = meta(name="generator")
    html_min = r.text.lower()
    constructeur = next((nom for nom, signe in CONSTRUCTEURS.items() if signe in html_min or nom.lower() in generateur.lower()), "")

    resultat = {
        "precautions": PRECAUTIONS,
        "domaine": r.url.host,
        "url_finale": final,
        "statut_http": r.status_code,
        "https": final.startswith("https://"),
        "redirections": len(r.history),
        "temps_reponse_s": round(duree, 2),
        "poids_html_ko": round(len(r.content) / 1024, 1),
        "compression": r.headers.get("content-encoding", "aucune"),
        "hsts": "strict-transport-security" in r.headers,
        "cms_detecte": constructeur or generateur,
        "seo": {
            "title": titre,
            "title_longueur": len(titre),
            "meta_description": description,
            "meta_description_longueur": len(description),
            "nb_h1": len(soup.find_all("h1")),
            "h1": [_texte(h) for h in soup.find_all("h1")][:5],
            "nb_h2": len(soup.find_all("h2")),
            "canonical": (soup.find("link", rel="canonical") or {}).get("href", ""),
            "meta_robots": meta(name="robots"),
            "robots_txt": robots,
            "sitemap_xml": sitemap,
            "donnees_structurees": bool(soup.find("script", type="application/ld+json")),
            "nb_mots": len(texte.split()),
        },
        "mobile_accessibilite": {
            "viewport": bool(soup.find("meta", attrs={"name": "viewport"})),
            "lang": (soup.html or {}).get("lang", ""),
            "images": len(images),
            "images_sans_attribut_alt": sum(1 for i in images if not i.has_attr("alt")),
            "images_alt_vide": sum(1 for i in images if i.has_attr("alt") and not i["alt"].strip()),
            "images_sans_dimensions": sum(1 for i in images if not (i.get("width") and i.get("height"))),
            "images_lazy": sum(1 for i in images if i.get("loading") == "lazy"),
            "favicon": bool(soup.find("link", rel=lambda v: v and "icon" in v)),
        },
        "ressources": {
            "scripts_externes": len(scripts),
            "feuilles_css": len(soup.find_all("link", rel="stylesheet")),
            "contenu_mixte_http": final.startswith("https://") and any(
                (t.get("src") or "").startswith("http://") for t in soup.find_all(src=True)
            ),
        },
        "partage_social": {
            "og_title": bool(meta(property="og:title")),
            "og_image": urljoin(final, meta(property="og:image")) if meta(property="og:image") else "",
            "twitter_card": bool(meta(name="twitter:card")),
        },
        "contact": {
            "telephone": any(l.startswith("tel:") for l in liens),
            "email": sorted({l[7:].split("?")[0] for l in liens if l.startswith("mailto:")})[:3],
            "formulaire": bool(soup.find("form")),
            "reseaux": sorted({urlparse(l).netloc for l in liens if any(
                s in l for s in ("facebook.com", "instagram.com", "linkedin.com", "tiktok.com", "x.com", "twitter.com")
            )}),
        },
        "annee_copyright": max(annees) if annees else None,
    }
    if lighthouse:
        resultat["lighthouse_mobile"] = _lighthouse(final)
    DERNIERS_AUDITS[_domaine(url)] = DERNIERS_AUDITS[_domaine(final)] = time.time()
    return json.dumps(resultat, ensure_ascii=False)


# --- Exécution ----------------------------------------------------------------

FONCTIONS = {
    "auditer_site": auditer_site,
    "enregistrer_mission": enregistrer_mission,
    "enregistrer_prospect": enregistrer_prospect,
    "envoyer_email": envoyer_email,
    "lire_conversation": lire_conversation,
    "changer_statut": changer_statut,
    "alerter_moi": alerter_moi,
    "sauver_audit": sauver_audit,
    "lister_suivi": lister_suivi,
}


def executer(nom: str, entree: dict) -> tuple[str, bool]:
    """Exécute un outil et renvoie (résultat, est_une_erreur)."""
    try:
        return FONCTIONS[nom](**entree), False
    except Exception as e:  # l'erreur est renvoyée à Claude, qui peut corriger ou réessayer
        return f"Erreur : {e}", True
