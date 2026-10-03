"""deploy/remote.sh contre un faux ~/stack et un faux docker : aucun accès au VPS."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HASH = "$2a$14$Qx9e1lB4rM3Hc6xTz0aS8u5mWq2y7k1oVbN3cD4eF5gH6iJ7kL8mO"

# Faux docker : journalise ses appels et imite ce dont remote.sh dépend.
FAKE_DOCKER = r"""#!/bin/sh
echo "$*" >> "$HOME/docker.log"
case "$*" in
  *"caddy validate"*) exit "${FAKE_VALIDATE_RC:-0}" ;;
  *"printenv YT_HASH"*)
    if [ -n "${FAKE_HASH:-}" ]; then echo "$FAKE_HASH"; exit 0; fi
    # Comme compose : une valeur entre apostrophes est prise telle quelle, sans elles.
    sed -n "s/^YT_HASH='\(.*\)'\$/\1/p" "$HOME/stack/caddy/auth.env" ;;
esac
exit 0
"""

COMPOSE = """name: perso

services:
  caddy:
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_config:/config
    networks: [web]

networks:
  web:
    name: web
"""
# Sans saut de ligne final : un ajout naïf collerait la ligne suivante à la dernière.
CADDYFILE = "http:// {\n\trespond 404\n}"
AUTH_ENV = "MC_UI_USER=admin\nMC_UI_HASH=x"
AUTHORIZED_KEYS = "ssh-ed25519 AAAA cle-perso"


class RemoteScriptTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.stack = self.home / "stack"
        (self.stack / "caddy").mkdir(parents=True)
        (self.stack / "docker-compose.yml").write_text(COMPOSE)
        (self.stack / "caddy" / "Caddyfile").write_text(CADDYFILE)
        (self.stack / "caddy" / "auth.env").write_text(AUTH_ENV)
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh" / "authorized_keys").write_text(AUTHORIZED_KEYS)
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(FAKE_DOCKER)
        (bin_dir / "docker").chmod(0o755)
        src = self.home / "src"
        src.mkdir()
        for name in ("compose-service.yml", "Caddyfile-block"):
            shutil.copy(ROOT / "deploy" / name, src / name)
        self.env = {**os.environ, "HOME": str(self.home), "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "YT_USER": "moi", "YT_HASH": HASH, "PUBKEY": "ssh-ed25519 BBBB gha-deploy-ytdl",
                    "YTDL_DEPLOY_SRC": str(src)}

    def run_script(self, **extra):
        return subprocess.run(["sh", str(ROOT / "deploy" / "remote.sh")], env={**self.env, **extra},
                              capture_output=True, text=True, timeout=30)

    def read(self, relative):
        return (self.home / relative).read_text()

    def docker_calls(self):
        log = self.home / "docker.log"
        return log.read_text() if log.exists() else ""

    def assert_untouched(self):
        self.assertEqual(self.read("stack/docker-compose.yml"), COMPOSE)
        self.assertEqual(self.read("stack/caddy/Caddyfile"), CADDYFILE)
        self.assertEqual(self.read("stack/caddy/auth.env"), AUTH_ENV)
        self.assertEqual(self.read(".ssh/authorized_keys"), AUTHORIZED_KEYS)
        self.assertNotIn("up -d caddy", self.docker_calls())

    def test_installs_service_site_password_and_key(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        compose = self.read("stack/docker-compose.yml")
        self.assertIn("      - caddy_config:/config\n      - ./apps/ytdl-data/jobs:/ytdl-jobs:ro\n", compose)
        self.assertLess(compose.index("\n  ytdl:\n"), compose.index("\nnetworks:\n"))
        caddyfile = self.read("stack/caddy/Caddyfile")
        self.assertTrue(caddyfile.startswith(CADDYFILE + "\n"))
        self.assertIn("\nyt.{$DOMAIN} {\n", caddyfile)
        # Le hash bcrypt est entre apostrophes : compose ne touche pas à ses « $ ».
        self.assertEqual(self.read("stack/caddy/auth.env").splitlines(),
                         ["MC_UI_USER=admin", "MC_UI_HASH=x", "YT_USER=moi", f"YT_HASH='{HASH}'"])
        keys = self.read(".ssh/authorized_keys").splitlines()
        self.assertEqual(keys[0], AUTHORIZED_KEYS)  # la clé existante reste intacte
        self.assertEqual(keys[1], f'command="{self.home}/stack/deploy-gate.sh {self.home}/stack/apps/ytdl ytdl",'
                                  'restrict ssh-ed25519 BBBB gha-deploy-ytdl')
        self.assertTrue((self.stack / "apps" / "ytdl-data" / "jobs").is_dir())
        self.assertIn("up -d caddy", self.docker_calls())

    def test_restores_everything_when_the_hash_does_not_survive_compose(self):
        result = self.run_script(FAKE_HASH="$2a$14")  # compose aurait mangé « $Qx… »
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ÉCHEC", result.stderr)
        self.assert_untouched()

    def test_restores_everything_when_caddy_rejects_the_config(self):
        result = self.run_script(FAKE_VALIDATE_RC="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_refuses_a_second_install(self):
        self.assertEqual(self.run_script().returncode, 0)
        before = {p: self.read(p) for p in ("stack/docker-compose.yml", "stack/caddy/auth.env", ".ssh/authorized_keys")}
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("déjà", result.stderr)
        self.assertEqual({p: self.read(p) for p in before}, before)


if __name__ == "__main__":
    unittest.main()
