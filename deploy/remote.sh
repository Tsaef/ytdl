#!/bin/sh
# Partie serveur de deploy/install.sh (ne pas lancer à la main). Sauvegarde, modifie,
# valide, et restaure tout en cas d'échec : Caddy ne redémarre qu'avec une
# configuration validée, sinon tous les sites du VPS tomberaient.
set -eu
cd "$HOME/stack"
STAMP=$(date +%Y%m%d-%H%M%S)
FILES="docker-compose.yml caddy/Caddyfile caddy/auth.env"

if grep -q '^  ytdl:' docker-compose.yml; then
  echo "ytdl est déjà dans docker-compose.yml : rien à faire." >&2
  exit 1
fi
for f in $FILES; do cp "$f" "$f.bak-$STAMP"; done
restore() {
  for f in $FILES; do cp "$f.bak-$STAMP" "$f"; done
  echo "ÉCHEC : fichiers restaurés depuis les sauvegardes .bak-$STAMP" >&2
  exit 1
}

mkdir -p apps/ytdl apps/ytdl-data/jobs

python3 - <<'PY' || restore
from pathlib import Path
compose = Path("docker-compose.yml")
text = compose.read_text()
mount = "      - caddy_config:/config\n"
anchor = "\nnetworks:\n"
assert text.count(mount) == 1, "montage caddy_config introuvable"
assert text.count(anchor) == 1, "section networks: introuvable"
text = text.replace(mount, mount + "      - ./apps/ytdl-data/jobs:/ytdl-jobs:ro\n")
text = text.replace(anchor, "\n" + Path("/tmp/compose-service.yml").read_text() + anchor)
compose.write_text(text)
PY

cat /tmp/Caddyfile-block >> caddy/Caddyfile
printf 'YT_USER=%s\nYT_HASH=%s\n' "$YT_USER" "$YT_HASH" >> caddy/auth.env

docker compose config -q || restore
# Validation dans un conteneur jetable avec la configuration du service : le Caddy
# en service n'est pas touché tant que tout n'est pas valide.
docker compose run --rm --no-deps --name "caddy-validate-$STAMP" caddy \
  caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 || restore
docker compose up -d caddy

cp ~/.ssh/authorized_keys ~/.ssh/authorized_keys.bak-$STAMP
echo "command=\"$HOME/stack/deploy-gate.sh $HOME/stack/apps/ytdl ytdl\",restrict $PUBKEY" >> ~/.ssh/authorized_keys

rm -f /tmp/compose-service.yml /tmp/Caddyfile-block /tmp/remote.sh
echo "VPS prêt : Caddy recréé, clé de déploiement ajoutée (sauvegardes .bak-$STAMP)."
