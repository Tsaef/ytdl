# ytdl — interface web personnelle pour yt-dlp

Date : 2026-10-03 · Statut : design validé section par section, spec à relire

## 1. Objectif

Un outil **personnel** (un seul utilisateur), **en ligne** sur le VPS (`yt.tsaef.fr`),
protégé par mot de passe. On colle un lien depuis n'importe quel appareil (téléphone
compris), on choisit le format, le serveur télécharge avec yt-dlp, on récupère le
fichier sur son appareil, puis le serveur l'efface.

Critères de réussite :

- une vidéo de 4 h en 1080p se télécharge et se récupère sur téléphone, avec reprise
  si le transfert est interrompu ;
- une playlist donne un ZIP, ou un seul fichier où les vidéos sont mises bout à bout
  avec un chapitre par vidéo ;
- quand les cookies YouTube expirent, l'interface le dit clairement ;
- le reste du VPS (Minecraft, sites, bases) n'est jamais privé de disque ni de CPU.

## 2. Périmètre

**Inclus** : vidéo MP4, audio MP3, choix de la qualité, playlists (ZIP ou un seul
fichier avec chapitres), 2 téléchargements en parallèle, file d'attente,
progression, annulation, effacement automatique, tous les sites que yt-dlp connaît.

**Exclu** (à ajouter plus tard si le besoin apparaît) : historique des
téléchargements, comptes multiples, notifications, partage direct depuis l'app
YouTube, directs (live), sous-titres, envoi de `cookies.txt` depuis l'interface,
reprise des tâches après un redémarrage, ré-encodage au-delà de 1080p.

## 3. Contraintes constatées

- **YouTube exige une session connectée.** Test du 2026-10-03 : yt-dlp 2026.08.19
  reçoit « Sign in to confirm you're not a bot » depuis le VPS **et** depuis une IP
  résidentielle, même avec deno. Il faut donc les cookies d'un **compte YouTube
  secondaire**, exportés par l'utilisateur.
- **deno** est nécessaire à yt-dlp pour YouTube (sinon des formats manquent).
- VPS : 4 cœurs, ~2 Go de RAM disponibles, 52 Go de disque libre (Minecraft occupe
  ~4,4 Go de RAM).
- Ordre de grandeur des tailles en 1080p H.264 : ~1,4 Go/h (30 i/s) à ~2,5 Go/h
  (60 i/s). La fusion audio + vidéo demande jusqu'à **2 × la taille finale** sur le
  disque.

## 4. Architecture

```
navigateur ──HTTPS──▶ Caddy  yt.tsaef.fr  (basic_auth)
                        ├─ /files/*  → file_server (lecture seule, reprise native)
                        └─ le reste  → reverse_proxy ytdl:8000
                                         │
               conteneur ytdl : python:3.14-alpine + ffmpeg + deno + yt-dlp
                 server.py    HTTP (stdlib ThreadingHTTPServer) : page + API JSON
                 jobs.py      file, workers, états, progression, annulation, expiration
                 downloader.py  analyse et téléchargement (options yt-dlp)
                 assemble.py  ZIP, mise bout à bout, chapitres (ffmpeg / ffprobe)
                 errors.py    classement des erreurs yt-dlp → messages français
                 web/         index.html, app.js, style.css (sans build)
                                         │
               ~/stack/apps/ytdl-data  (monté sur /data, hors git)
                 cookies.txt   compte secondaire
                 jobs/<id>/    fichiers temporaires d'une tâche
```

- Un seul conteneur, sans base de données : les tâches vivent en mémoire.
- Le mot de passe est géré par Caddy (même mécanisme que le panneau Minecraft) ; le
  code Python ne gère aucune authentification.
- Les fichiers terminés sont servis par Caddy, pas par Python : `file_server` gère
  les requêtes `Range`, donc la reprise d'un transfert de 10 Go interrompu.
- yt-dlp est utilisé comme **bibliothèque** (`yt_dlp.YoutubeDL`), ffmpeg et ffprobe
  comme sous-processus.

## 5. API

Toutes les réponses sont en JSON. Les identifiants de tâche sont des `uuid4`.

| Méthode | Chemin | Rôle |
|---|---|---|
| `GET` | `/` | la page |
| `GET` | `/api/status` | `{cookies: "ok"\|"absent"\|"expired"\|"unknown", disk_free_bytes}` |
| `POST` | `/api/analyze` | `{url}` → infos sans téléchargement (§7) |
| `POST` | `/api/jobs` | `{url, format: "mp4"\|"mp3", height: int\|null, playlist_mode: "zip"\|"concat"\|null}` → la tâche créée |
| `GET` | `/api/jobs` | toutes les tâches, les plus récentes d'abord |
| `GET` | `/api/jobs/<id>` | une tâche |
| `POST` | `/api/jobs/<id>/decision` | `{choice: "reencode"\|"zip"}`, seulement en état `awaiting_decision` |
| `DELETE` | `/api/jobs/<id>` | annule une tâche en cours, ou efface une tâche terminée et ses fichiers |

`height: null` signifie « meilleure qualité disponible ». `playlist_mode` est ignoré
pour une vidéo seule ; pour une playlist, `null` vaut `zip`.

**Tâche** : `id, url, title, format, height, playlist_mode, state, progress
{percent, speed_bps, eta_s, item, items}, error {kind, message, details},
decision {reencode_estimate_s}, file {name, size, url}, skipped [titres],
created_at, finished_at, expires_at`.

**États** : `queued → downloading → processing → ready`, avec en parallèle
`awaiting_decision` (§8), `error` et `cancelled`.

## 6. Interface

Une page unique, mobile d'abord, en français, interrogeant `/api/jobs` toutes les 2 s.

1. Champ « lien » + **Analyser** : titre, durée et miniature (vidéo seule), ou nombre
   de vidéos et liste des titres (playlist).
2. Choix : **Format** (Vidéo MP4 / Audio MP3) ; **Qualité** (vidéo seulement) parmi
   les hauteurs réellement disponibles, chacune avec sa taille estimée ; pour une
   playlist, la qualité est un plafond ; **Playlist** : « ZIP, un fichier par vidéo »
   ou « Un seul fichier, tout bout à bout ».
3. **Télécharger** ajoute la tâche à la file. On peut fermer l'onglet : à la
   réouverture, la liste est rechargée depuis le serveur.
4. Liste des tâches : barre de progression, vitesse, temps restant, « vidéo 5/12 » ;
   boutons **Annuler** (en cours), **Récupérer** et **Supprimer** (prête), compte à
   rebours avant expiration ; message d'erreur avec un volet « Détails ».
5. Bandeau en haut de page si les cookies sont absents ou expirés.

## 7. Téléchargement et formats

**Analyse** : `extract_info(url, download=False)`. Pour une playlist,
`extract_flat="in_playlist"` (rapide : titres seulement). Les directs (`is_live`)
sont refusés (« Les directs ne sont pas pris en charge »). Taille estimée par
hauteur : `filesize` ou à défaut `filesize_approx` du format vidéo retenu plus la
meilleure piste audio.

**Vidéo MP4**, plafond `H` :
`bv*[height<=H][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=H]+ba/b[height<=H]`,
`merge_output_format: "mp4"`. Le H.264 + AAC est préféré pour la compatibilité
(téléphones) et pour maximiser les assemblages par copie (§8). Sans plafond, on
retire le filtre `[height<=H]`.

**Audio MP3** : `ba/b` puis conversion en MP3 **CBR 192 kb/s, 44,1 kHz, stéréo**.
Des réglages identiques pour toutes les pistes permettent l'assemblage par copie.

**Noms de fichiers** : `%(title)s.%(ext)s`, avec `windowsfilenames: True` pour
qu'ils soient valides sur tous les appareils. Dans un ZIP :
`NN - Titre.ext`, où `NN` est complété par des zéros selon le nombre de vidéos.

**Vidéo ou playlist** : `noplaylist: True` toujours. Un lien
`watch?v=…&list=…` (une vidéo ouverte depuis une playlist) donne donc la vidéo seule ;
un lien `playlist?list=…` donne la playlist.

**Options communes** : `cookiefile` (copie propre à la tâche, §11),
`allowed_extractors: ["default", "-generic"]` (§13), `noplaylist: True`,
`playlistend: 200`, et un `progress_hook` qui met à jour la progression,
vérifie l'annulation et surveille l'espace disque (§9).

## 8. Playlists

Les vidéos sont téléchargées **une par une**, dans l'ordre de la playlist. Une vidéo
indisponible (privée, supprimée, bloquée par région) est sautée et ajoutée à
`skipped` ; le résultat l'indique (« 11/12 vidéos, 1 indisponible : Titre »). Si
aucune vidéo n'aboutit, la tâche passe en `error`.

**ZIP** : `zipfile` de la stdlib, `ZIP_STORED` (pas de recompression), Zip64 actif.
Chaque fichier est ajouté puis effacé aussitôt : le disque ne contient jamais deux
copies. Nom : `<titre de la playlist>.zip`.

**Un seul fichier (`concat`)** :

- **MP3** : les pistes ont des réglages identiques (§7), donc elles sont assemblées
  par le démultiplexeur `concat` de ffmpeg avec `-c copy`.
- **MP4** : après les téléchargements, `ffprobe` compare les vidéos (codec vidéo,
  largeur, hauteur, images/s, codec audio, fréquence et canaux audio).
  - Toutes identiques : assemblage `concat` avec `-c copy` (rapide, sans perte).
  - Sinon, la tâche passe en **`awaiting_decision`** avec une estimation du temps de
    ré-encodage (`durée totale × facteur`, facteur réglable, 1,0 par défaut en
    1080p). L'utilisateur choisit **Ré-encoder** ou **ZIP à la place**. Sans
    décision, la tâche expire avec les autres (§12).
  - Ré-encodage : H.264 `libx264 -preset veryfast -crf 23`, hauteur cible =
    min(plafond choisi, plus grande hauteur des vidéos, 1080), image ajustée dans un
    cadre 16:9 avec bandes noires, 30 i/s, AAC 160 kb/s 48 kHz stéréo,
    `-threads 2`.
- **Chapitres** : un chapitre par vidéo, titré avec son titre d'origine, écrit via un
  fichier `ffmetadata` pendant l'assemblage. Vérifié par test pour MP4 et MP3 ; si
  le muxer MP3 n'écrit pas les chapitres, ils restent réservés au MP4 et le README
  le dit.
- Avant l'assemblage, l'espace nécessaire (≈ somme des tailles) doit respecter la
  marge (§9) ; sinon : « Espace disque insuffisant pour l'assemblage : choisis le ZIP ».
- Nom : `<titre de la playlist>.mp4|mp3`.

## 9. Concurrence et ressources

- `YTDL_WORKERS` (défaut **2**) tâches en parallèle ; les suivantes restent en
  `queued`.
- **Marge disque** : `YTDL_DISK_FLOOR` (défaut **10 Go**) doit toujours rester libre.
  - Avant de démarrer une vidéo : on réserve `2 × taille estimée`. Si
    `espace libre − réservations en cours − réservation < marge`, la tâche (ou la
    vidéo de la playlist) est refusée : « Espace disque insuffisant (X Go
    nécessaires) ». Taille inconnue : aucune réservation, seule la surveillance
    s'applique.
  - Pendant le téléchargement, le `progress_hook` vérifie `shutil.disk_usage` au
    plus une fois par seconde ; sous la marge, la tâche est arrêtée et ses fichiers
    effacés.
- Conteneur : `mem_limit: 768m`, `cpus: 2` ; ffmpeg avec `-threads 2`.

## 10. Erreurs

`errors.py` classe le message de yt-dlp (ou de ffmpeg) en un `kind` :

| `kind` | Reconnu par | Message |
|---|---|---|
| `cookies` | « Sign in to confirm », « cookies are no longer valid », cookies absents | « Cookies expirés : à renouveler » + bandeau |
| `unavailable` | « Video unavailable », « Private video », « not available in your country » | « Vidéo indisponible » |
| `unsupported` | « Unsupported URL » | « Lien non pris en charge » |
| `disk` | marge disque (§9) | « Espace disque insuffisant (X Go nécessaires) » |
| `network` | erreurs HTTP 5xx, délais dépassés, connexion refusée | « Erreur réseau, réessaie » |
| `live` | `is_live` | « Les directs ne sont pas pris en charge » |
| `other` | tout le reste | « Échec du téléchargement » |

La dernière ligne d'erreur d'origine va dans `details`.

## 11. Cookies

- Fichier `/data/cookies.txt` (format Netscape), hors dépôt, relu à chaque tâche : le
  renouveler ne demande aucun redémarrage.
- Chaque tâche travaille sur **sa propre copie** du fichier. Au démarrage, la copie
  est faite sous un verrou. À la fin, la copie (mise à jour par yt-dlp, ce qui
  prolonge la session) est recopiée sur l'original sous le même verrou ; la dernière
  écriture l'emporte.
- État exposé par `/api/status` : `absent` si le fichier manque ; `expired` après
  une erreur `cookies` ; `ok` après un téléchargement YouTube réussi ; `unknown`
  au démarrage.
- Le README décrit le renouvellement : export depuis une fenêtre privée du compte
  secondaire, puis `scp cookies.txt vps:stack/apps/ytdl-data/cookies.txt`.

## 12. Cycle de vie et nettoyage

- `expires_at` = moment de l'entrée en `ready` ou en `awaiting_decision` + 6 h
  (`YTDL_TTL`, défaut 6 h).
- Un thread de nettoyage passe toutes les 5 minutes : il efface les tâches expirées
  et leurs fichiers. Les fichiers partiels d'une tâche annulée ou échouée sont
  effacés immédiatement.
- **Supprimer** efface tout de suite.
- Au démarrage, `jobs/` est vidé. Un redémarrage (donc un déploiement) interrompt
  les tâches en cours ; le déploiement étant validé à la main, on choisit le moment.

## 13. Sécurité

- Le conteneur n'expose aucun port : seul Caddy l'atteint, derrière `basic_auth`.
- **Extracteur générique désactivé** (`-generic`) : sans lui, yt-dlp ne peut pas
  être dirigé vers une URL arbitraire, comme une adresse interne du VPS (Netdata,
  backend du panneau Minecraft sur `172.18.0.1`). Seuls les sites connus de yt-dlp
  sont acceptés. Seules les URL `http(s)://` sont acceptées.
- Dossiers de tâches nommés par `uuid4`. Caddy sert `/files/*` avec
  `Content-Disposition: attachment`.
- Le conteneur tourne sous un utilisateur non root d'**uid 1000**, le même que
  `ubuntu` sur l'hôte, pour pouvoir écrire dans `~/stack/apps/ytdl-data` monté sur
  `/data`.

## 14. Déploiement

**Dépôt** : `Tsaef/ytdl`, public (la validation avant déploiement l'exige sur un
compte gratuit). Dossier local : `~/Practice/ytdl`.

**Image** : `python:3.14-alpine` + `apk add ffmpeg deno` + `requirements.txt`
(`yt-dlp[default]` figé sur la dernière version publiée au moment de
l'implémentation ; 2026.08.19 lors du test du 2026-10-03). **Dependabot** (écosystème `pip`,
hebdomadaire) ouvre une PR pour chaque nouvelle version de yt-dlp.

**Serveur** :

- `~/stack/apps/ytdl` : le code, synchronisé par la CI (`rsync --delete`).
- `~/stack/apps/ytdl-data` : `cookies.txt` et `jobs/`, jamais touchés par la CI.
- Service compose `ytdl` : `build: ./apps/ytdl`, volume
  `./apps/ytdl-data:/data`, `YTDL_WORKERS=2`, `mem_limit: 768m`, `cpus: 2`,
  healthcheck sur `GET /api/status`, réseau `web`, sans `ports:`.
- Caddy : volume supplémentaire `./apps/ytdl-data/jobs:/ytdl-jobs:ro` et bloc
  `yt.{$DOMAIN}` (`basic_auth` avec `YT_USER` / `YT_HASH` dans `caddy/auth.env`,
  `handle_path /files/*` → `file_server` sur `/ytdl-jobs` avec l'en-tête
  `Content-Disposition attachment`, le reste → `reverse_proxy ytdl:8000`). Le
  conteneur Caddy est recréé une fois, ce qui coupe tous les sites quelques secondes.
- **Installation initiale** par un script que l'utilisateur relit et lance (le
  classifieur interdit à Claude de modifier la configuration de la prod) : ajouts au
  compose et au Caddyfile (avec sauvegardes), dossiers `ytdl` et `ytdl-data/jobs`,
  mot de passe (`caddy hash-password`, saisi par l'utilisateur), clé de déploiement
  bridée (`deploy-gate.sh /home/ubuntu/stack/apps/ytdl ytdl`), recréation de Caddy.
  `cookies.txt` est déposé par l'utilisateur.

**CI/CD** (`.github/workflows/deploy.yml`, même modèle que pathfinder) : sur push vers
`main`, job `test` (Python 3.14, `apt install ffmpeg`, `unittest`, `docker build`) ;
puis job `deploy` derrière l'environnement `production` (validation manuelle, branche
`main` seulement, secret `SSH_KEY`) : `rsync` du code puis `ssh … rebuild`.

## 15. Tests

`unittest` de la stdlib, **aucun appel réseau**.

- **Logique pure** : options yt-dlp par format, qualité et mode ; classement des
  erreurs (§10) ; calcul de réservation et de marge disque ; choix entre copie et
  ré-encodage à partir de métadonnées `ffprobe` ; génération du fichier de
  chapitres ; estimation du temps de ré-encodage.
- **File de tâches** avec un faux téléchargeur : 2 tâches en parallèle et la 3ᵉ en
  attente ; annulation ; expiration après le TTL ; vidage de `jobs/` au démarrage ;
  passage en `awaiting_decision` puis les deux choix.
- **ffmpeg réel** sur des clips générés par ffmpeg (`lavfi` : mire + sinusoïde) :
  ZIP ; assemblage par copie ; ré-encodage de deux résolutions différentes ; MP3
  assemblé ; durée totale et chapitres vérifiés avec `ffprobe`. Ces tests sont
  sautés si ffmpeg est absent, sauf avec `YTDL_REQUIRE_FFMPEG=1`, que la CI
  active.
- **API** : `server.py` démarré sur un port libre avec un faux téléchargeur ; création,
  lecture, décision, suppression ; refus d'une URL non `http(s)`.
- **Test manuel** (README), après le premier déploiement avec les cookies : une
  vidéo courte en MP4 et en MP3, une petite playlist en ZIP et en un seul fichier.
