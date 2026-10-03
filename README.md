# ytdl

Interface web personnelle pour [yt-dlp](https://github.com/yt-dlp/yt-dlp), en ligne sur
`https://yt.tsaef.fr` derrière un mot de passe. On colle un lien, on choisit MP4 ou MP3
(et la qualité), on récupère le fichier ; le serveur l'efface ensuite. Une playlist
donne un ZIP, ou un seul fichier où les vidéos sont mises bout à bout avec un chapitre
par vidéo.

Conception : [docs/superpowers/specs/2026-10-03-ytdl-design.md](docs/superpowers/specs/2026-10-03-ytdl-design.md).

## Cookies YouTube

YouTube refuse yt-dlp sans session connectée (« Sign in to confirm you're not a bot »).
ytdl utilise les cookies d'un **compte secondaire**, jamais ceux du compte principal.
À refaire quand la page affiche « Cookies expirés » :

1. Ouvrir une **fenêtre de navigation privée**, se connecter à YouTube avec le compte secondaire.
2. Dans le même onglet, aller sur `https://www.youtube.com/robots.txt`.
3. Exporter les cookies de `youtube.com` au format `cookies.txt` (Netscape) avec une
   extension d'export autorisée en navigation privée (par ex. « Get cookies.txt LOCALLY »).
4. **Fermer la fenêtre privée** sans se déconnecter : le navigateur n'utilise plus cette
   session, YouTube ne la fait donc pas tourner.
5. Déposer le fichier sur le VPS :

   ```bash
   scp cookies.txt vps:stack/apps/ytdl-data/cookies.txt
   ssh vps chmod 600 stack/apps/ytdl-data/cookies.txt
   ```

Pas de redémarrage : le fichier est relu à chaque tâche.

## Réglages

Variables d'environnement du conteneur (service `ytdl` de `~/stack/docker-compose.yml`) :

| Variable | Défaut | Rôle |
|---|---|---|
| `YTDL_WORKERS` | `2` | téléchargements en parallèle |
| `YTDL_DISK_FLOOR` | `10` | Go toujours laissés libres sur le VPS |
| `YTDL_TTL` | `6` | heures avant l'effacement d'une tâche terminée |
| `YTDL_REENCODE_FACTOR` | `1.0` | secondes de calcul par seconde de vidéo en 1080p (estimation affichée avant un ré-encodage) |
| `YTDL_DATA` | `/data` | `cookies.txt`, `jobs/`, `cookie-copies/` |

## Développement

```bash
python3 -m unittest discover -s tests -t .   # Mac : tests ffmpeg sautés
docker build -t ytdl:dev . && docker run --rm -v "$PWD:/src" -w /src -e YTDL_REQUIRE_FFMPEG=1 ytdl:dev python -m unittest discover -s tests -t .
python3 -m tests.devserver                   # vraie page, faux téléchargeur : http://localhost:8000
```

## Déploiement

- Push sur `main` → tests + build de l'image → **validation** (environnement
  `production`) → `rsync` vers `~/stack/apps/ytdl` puis reconstruction du service.
- Installation initiale, une seule fois : `sh deploy/install.sh` (mot de passe du site,
  clé de déploiement bridée, service compose, bloc Caddy ; sauvegardes `.bak-*`).
- **Quand YouTube casse yt-dlp** : Dependabot ouvre chaque semaine une PR qui monte
  yt-dlp ; la fusionner puis valider le déploiement. Pour forcer : modifier la version
  dans `requirements.txt` et pousser.

## Test manuel après un déploiement

1. `https://yt.tsaef.fr` demande un mot de passe.
2. Une vidéo courte en MP4 720p : progression, puis « Récupérer » donne un MP4 lisible
   sur le téléphone.
3. La même en MP3 : fichier lisible, 192 kb/s.
4. Une petite playlist (3 ou 4 vidéos) en ZIP : fichiers numérotés « 01 - … ».
5. La même en « un seul fichier » : chapitres visibles dans VLC ; si les vidéos
   diffèrent, la page propose « Ré-encoder » ou « ZIP à la place ».
6. « Supprimer » efface la tâche ; une tâche prête disparaît au bout de 6 h.

## Limites connues

- Un redémarrage du conteneur (donc un déploiement) interrompt les tâches en cours et
  efface leurs fichiers.
- Les directs ne sont pas pris en charge ; le ré-encodage d'une playlist est plafonné à 1080p.
- Si le manque de place empêche de créer un fichier unique, la playlist est livrée en ZIP.
