# Agent Freelance

Un agent IA (Claude Opus 5.5) qui travaille pour toi en autonomie :

- **cherche des missions** freelance sur le web et les plateformes ;
- **trouve des clients** en Afrique francophone et anglophone, en Europe et en Amérique du Nord,
  surtout des entreprises sans site ou avec un site à refaire ;
- **leur écrit, répond à leurs messages et les relance** par email, dans leur langue ;
- **te prévient** dès qu'une décision te revient (devis, contrat, rendez-vous, client chaud) ;
- **fait des audits** de sites (SEO, performance, mobile, Lighthouse) et rédige le rapport.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

1. Copie `.env.example` en `.env` et remplis-le : clé API Anthropic, adresse email et
   mot de passe d'application, ton nom.
2. Complète `profil.md`, surtout tes **prix** : l'agent annonce ces fourchettes aux clients.

## Utilisation

Interface web (http://localhost:8000), avec la tournée automatique en fond :

```bash
python web.py
```

Terminal :

```bash
python cli.py
```

Une tournée unique (lecture des réponses, relances, prospection), pratique avec le
Planificateur de tâches Windows si tu ne laisses pas le serveur tourner :

```bash
python automate.py
```

## Parler à l'assistant (Chrome ou Edge)

- **Micro** (ou `Ctrl + M`, ou un clic sur l'orbe) : dicte ta demande. La réponse est lue à voix haute.
- **Mains libres** : l'assistant écoute en continu. Dis « Jarvis, trouve-moi des missions… »,
  ou juste « Jarvis », attends le bip, puis parle.
- `Échap` ou « stop » le fait taire.
- Commandes traitées sur place, sans coût API : « point du jour » (résumé du tableau de bord),
  « lance la tournée », « stop ».
- Le bouton à curseurs règle la voix, la vitesse, le mot d'éveil, et quand il te répond à voix haute.
  Dans Edge, les voix « Natural » (Henri, Denise) sont les plus naturelles.

La reconnaissance vocale passe par le service du navigateur (Google pour Chrome, Microsoft pour
Edge) et ne fonctionne pas dans Firefox.

## Comment il travaille seul

Quand le serveur web tourne et que `TOURNEE_AUTO=oui` :

- **toutes les 15 minutes**, il lit les nouveaux emails **de tes prospects uniquement** (les
  autres emails de ta boîte ne sont pas touchés) et leur répond ;
- **une fois par jour**, il relance ceux qui n'ont pas répondu (après 4 puis 8 jours) et
  contacte de nouveaux prospects.

Le bouton « Envoi auto / Je valide » de l'interface bascule entre l'envoi direct et la
validation manuelle de chaque email.

## Garde-fous (toujours actifs)

- 20 premiers contacts et relances par jour au maximum, 2 relances par client.
- Chaque premier contact et chaque relance se termine par « répondez STOP » ; un client qui
  répond STOP passe au statut « stop » et ne reçoit plus rien.
- L'agent n'écrit qu'aux adresses professionnelles publiées par les entreprises.
- Pendant une réponse automatique, l'agent ne peut écrire qu'au client concerné.
- Il ne s'engage jamais à ta place (prix ferme, contrat, paiement, date) : il t'alerte.

## Fichiers

| Fichier | Rôle |
|---|---|
| `agent.py` | Boucle de l'agent et consignes |
| `outils.py` | Outils : audit, prospects, envoi d'emails et garde-fous |
| `automate.py` | Tournées automatiques (réponses, relances, prospection) |
| `messagerie.py` | Envoi SMTP et lecture IMAP |
| `suivi.py` | Stockage des missions, prospects, conversations, alertes |
| `config.py` | Lecture du `.env` et des réglages |
| `profil.md` | Ton profil, tes offres et tes prix |
| `donnees/` | Fichiers JSON du suivi et journal |
| `audits/` | Rapports d'audit |
