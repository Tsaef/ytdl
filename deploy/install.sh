#!/bin/sh
# Installation initiale de ytdl sur le VPS (spec §14). À relire, puis à lancer UNE
# fois depuis le Mac :   sh deploy/install.sh
# Prérequis : alias SSH « vps », gh connecté, dépôt Tsaef/ytdl et son environnement
# « production » déjà créés.
set -eu
cd "$(dirname "$0")"

printf "Utilisateur pour yt.tsaef.fr : "
read -r YT_USER
case "$YT_USER" in
  ''|*[!A-Za-z0-9._-]*) echo "Utilisateur invalide (lettres, chiffres, . _ - uniquement)" >&2; exit 1 ;;
esac
stty -echo
printf "Mot de passe : "
read -r YT_PASS
stty echo
echo
[ -n "$YT_PASS" ] || { echo "Mot de passe vide" >&2; exit 1; }

# Le mot de passe passe par l'entrée standard : jamais dans une ligne de commande.
YT_HASH=$(printf '%s\n' "$YT_PASS" | ssh vps 'docker exec -i caddy caddy hash-password')
case "$YT_HASH" in
  '$2'*) ;;
  *) echo "Hachage inattendu : $YT_HASH" >&2; exit 1 ;;
esac

KEYDIR=$(mktemp -d)
trap 'rm -rf "$KEYDIR"' EXIT
ssh-keygen -q -t ed25519 -N "" -C gha-deploy-ytdl -f "$KEYDIR/key"

scp -q compose-service.yml Caddyfile-block remote.sh vps:/tmp/
ssh vps "YT_USER='$YT_USER' YT_HASH='$YT_HASH' PUBKEY='$(cat "$KEYDIR/key.pub")' sh /tmp/remote.sh"

# La clé privée ne vit que dans le secret GitHub ; le dossier temporaire est effacé (trap).
gh secret set SSH_KEY --env production --repo Tsaef/ytdl < "$KEYDIR/key"
echo "Terminé. Dépose maintenant cookies.txt (voir le README), puis pousse sur main."
