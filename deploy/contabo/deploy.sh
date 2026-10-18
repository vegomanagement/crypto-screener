#!/usr/bin/env bash
# Deploy the crypto screener to the shared Contabo VPS as a private (127.0.0.1) compose stack.
#
#   ./deploy/contabo/deploy.sh                # deploy the committed HEAD (idempotent)
#   DEPLOY_REF=<sha> ./deploy/contabo/deploy.sh   # deploy / roll back to a given commit
#   ./deploy/contabo/deploy.sh --status | --logs [N] | --stop | --backup | --install-cron
#
# Env overrides: CS_HOST (ssh target, default 173.249.40.103), CS_ROOT (/opt/crypto-screener),
# DEPLOY_REF (default HEAD), FORCE_BUILD=1 (rebuild an existing revision). Only the committed tree is shipped (git archive), never local DBs.
# Data under $CS_ROOT/data and $CS_ROOT/.env are created once and never overwritten.
set -euo pipefail

CS_HOST="${CS_HOST:-173.249.40.103}"
CS_ROOT="${CS_ROOT:-/opt/crypto-screener}"
DEPLOY_REF="${DEPLOY_REF:-HEAD}"
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
COMPOSE="docker compose -p crypto-screener -f $CS_ROOT/src/deploy/contabo/docker-compose.yml"

remote() { ssh -o BatchMode=yes "$CS_HOST" "CS_ROOT=$CS_ROOT; $*"; }
compose_env() { echo "CS_ROOT=$CS_ROOT CS_REVISION=\$(cat $CS_ROOT/src/REVISION 2>/dev/null || echo local)"; }

case "${1:-deploy}" in
  --status)
    remote "$(compose_env) $COMPOSE ps; echo; cat $CS_ROOT/src/REVISION 2>/dev/null;
            curl -fsS -m 5 http://127.0.0.1:5087/health; echo;
            curl -fsS -m 5 http://127.0.0.1:5087/api/paper/strategies -o /dev/null -w 'paper api %{http_code}\n' || true;
            ls -la $CS_ROOT/data $CS_ROOT/data/app-data"
    exit 0 ;;
  --logs)
    remote "$(compose_env) $COMPOSE logs --tail ${2:-200} app"
    exit 0 ;;
  --stop)
    remote "$(compose_env) $COMPOSE stop app"
    exit 0 ;;
  --backup)
    remote "$CS_ROOT/src/deploy/contabo/backup.sh"
    exit 0 ;;
  --install-cron)
    # New file only; refuses to overwrite a different existing one.
    remote "f=/etc/cron.d/crypto-screener-backup; new='SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
40 3 * * * root $CS_ROOT/src/deploy/contabo/backup.sh >>/var/log/crypto-screener-backup.log 2>&1'
      if [ -e \$f ]; then
        if [ \"\$(cat \$f)\" = \"\$new\" ]; then echo \"cron: \$f already installed\"; else echo \"cron: \$f exists and differs — not touched\" >&2; exit 1; fi
      else printf '%s\n' \"\$new\" > \$f && chmod 644 \$f && echo \"cron: installed \$f\"; fi"
    exit 0 ;;
  deploy) ;;
  *) echo "usage: $0 [--status|--logs [N]|--stop|--backup|--install-cron]" >&2; exit 2 ;;
esac

REV="$(git -C "$REPO" rev-parse --short=12 "$DEPLOY_REF")"
if [ "$DEPLOY_REF" = HEAD ] && [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then
  echo "note: uncommitted changes are NOT deployed (shipping committed $REV)" >&2
fi
echo "deploying $REV to $CS_HOST:$CS_ROOT"

# 1. Directories (data is created once, owned by the container uid 10001) and .env (once).
# chown -R only changes ownership (DBs copied in as root during cutover must be writable by the app).
remote "set -e; mkdir -p \$CS_ROOT/data/app-data; chown -R 10001:10001 \$CS_ROOT/data"

# 2. Ship the committed tree into a fresh staging dir, then swap (previous kept as src.prev).
git -C "$REPO" archive --format=tar "$REV" -- . ':(exclude)docs' ':(exclude)tests' \
  | remote "set -e; rm -rf \$CS_ROOT/src.new; mkdir -p \$CS_ROOT/src.new; tar -x -C \$CS_ROOT/src.new;
            echo $REV > \$CS_ROOT/src.new/REVISION;
            if [ ! -e \$CS_ROOT/.env ]; then install -m 600 \$CS_ROOT/src.new/deploy/contabo/.env.example \$CS_ROOT/.env; echo 'created .env from template'; fi;
            rm -rf \$CS_ROOT/src.prev; if [ -d \$CS_ROOT/src ]; then mv \$CS_ROOT/src \$CS_ROOT/src.prev; fi;
            mv \$CS_ROOT/src.new \$CS_ROOT/src"

# 3. Build and (re)start only this project's service.
remote "set -e; cd \$CS_ROOT; export CS_ROOT=\$CS_ROOT CS_REVISION=$REV;
        # Build only when this revision has no image yet (or FORCE_BUILD=1): a rebuild yields a new
        # image id and would recreate the container. up -d still applies .env changes.
        if [ \"${FORCE_BUILD:-0}\" = 1 ] || ! docker image inspect crypto-screener-app:$REV >/dev/null 2>&1; then
          $COMPOSE build app; else echo \"image crypto-screener-app:$REV exists — build skipped\"; fi
        $COMPOSE up -d app;
        for i in \$(seq 1 30); do
          s=\$(docker inspect -f '{{.State.Health.Status}}' crypto-screener-app-1 2>/dev/null || echo missing)
          [ \"\$s\" = healthy ] && break; sleep 3; done
        echo \"health: \$s\"; $COMPOSE ps app
        # Old images of this project only (other stacks untouched).
        docker images crypto-screener-app --format '{{.Tag}}' | grep -vx $REV | sed 's/^/crypto-screener-app:/' | xargs -r docker rmi >/dev/null 2>&1 || true
        [ \"\$s\" = healthy ]"
