# Crypto screener on the Contabo VPS (private)

Single compose service, UI/API only. **No Telegram bot, no scheduler, no alert sender**:
`run.py` imports `screener` and calls `screener.app.run` (same as the local launcher);
`screener.py`'s `__main__` block (Telegram polling, schedule thread, startup message) never runs,
and `run.py` removes `TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID` from the environment.

| what | value |
|---|---|
| host | `173.249.40.103` (override: `CS_HOST`) |
| directory | `/opt/crypto-screener` (`src/` = shipped tree, `src.prev/` = previous, `data/`, `.env`) |
| container | `crypto-screener-app-1` (compose project `crypto-screener`, network `crypto-screener_default`) |
| port | `127.0.0.1:5087` on the VPS only — no nginx vhost, no ufw rule, no public exposure |
| image | `crypto-screener-app:<git sha>`, base `python:3.11.17-slim-bookworm` (CI version), ~660 MB |
| user | uid 10001, `cap_drop: ALL`, `no-new-privileges`, `mem_limit 1536m`, logs 3×10 MB |
| health | Docker healthcheck on `GET /health` |
| backups | `/opt/backups/crypto-screener/<date>/*.gz`, cron `/etc/cron.d/crypto-screener-backup` 03:40, 14 days |

## Data (never in `src/`, never overwritten by a deploy)

| host path | container | files |
|---|---|---|
| `/opt/crypto-screener/data` | `/data` | `signals.db` (DB_PATH), `liquidations.db`, `paper_journal.db`, `whales.db` (+ `-wal`/`-shm`/`.lock`) |
| `/opt/crypto-screener/data/app-data` | `/app/.data` | `liquidity.sqlite3`, `agent_analyses.sqlite3` (code uses `app.root_path/.data`) |

`deploy.sh` runs `chown -R 10001:10001 data/` on every deploy (ownership only), so files copied in
as root become writable by the app.

## Commands (from the repo root on the Mac)

```bash
./deploy/contabo/deploy.sh                  # ship committed HEAD (git archive, no docs/tests), build if new, up -d
DEPLOY_REF=<sha> ./deploy/contabo/deploy.sh # deploy / roll back to a specific commit
FORCE_BUILD=1 ./deploy/contabo/deploy.sh    # rebuild an already built revision
./deploy/contabo/deploy.sh --status         # compose ps, revision, /health, data listing
./deploy/contabo/deploy.sh --logs [N]
./deploy/contabo/deploy.sh --stop
./deploy/contabo/deploy.sh --backup         # run backup.sh now
./deploy/contabo/deploy.sh --install-cron   # writes /etc/cron.d/crypto-screener-backup only if absent
```

Re-running the same revision is a no-op for the container (no rebuild, `up -d` → "Running");
`.env` changes are applied by `up -d` (container recreated). Uncommitted changes are never shipped.

Access from the Mac: `ssh -N -L 5187:127.0.0.1:5087 173.249.40.103` → http://127.0.0.1:5187/ui
(or the persistent tunnel below on 5087 once the local server is retired).

## Workers (OFF on first deploy)

In `/opt/crypto-screener/.env` (chmod 600, created once from `.env.example`):

```
PAPER_JOURNAL=1
WHALE_RECORDER=1
```

then `./deploy/contabo/deploy.sh`. Each worker takes a flock on its DB, so exactly one process per
file. Run the journal on ONE machine only — the Mac and the VPS have separate files and would
record diverging journals.

## Hermes analyst

There is no Hermes API on the VPS: `HERMES_API_URL`/`HERMES_API_KEY` stay empty.
`POST /api/agent/analysis` then returns 200 with `status: "hermes_unavailable"`, `model: null`,
`source: "baseline"` and the warning "Hermes gateway not configured (HERMES_API_URL); baseline shown"
— no 500. Never put a key into the repo; it would go only into the server `.env`.

## Cutover (Mac :5087 → VPS)

1. Snapshot the Mac DBs consistently (works while running; stopping the local server first is cleaner):
   `for f in paper_journal.db whales.db liquidations.db; do sqlite3 $f ".backup '/tmp/cs-cut/$f'"; done`
   and the same for `.data/agent_analyses.sqlite3` (and `.data/liquidity.sqlite3` if wanted; `signals.db` if it exists).
   Never copy `*.lock`, `-wal`, `-shm` by hand.
2. Stop the local server (it must not keep running the journal/recorder on its own copies).
3. `./deploy/contabo/deploy.sh --stop`
4. On the VPS move the empty first-boot DBs aside (do not delete):
   `cd /opt/crypto-screener/data && mkdir -p ../data-empty-firstboot && mv *.db *.db-wal *.db-shm app-data/*.sqlite3* ../data-empty-firstboot/ 2>/dev/null`
5. `scp /tmp/cs-cut/{paper_journal,whales,liquidations}.db 173.249.40.103:/opt/crypto-screener/data/`
   `scp /tmp/cs-cut/agent_analyses.sqlite3 173.249.40.103:/opt/crypto-screener/data/app-data/`
6. Set `PAPER_JOURNAL=1`, `WHALE_RECORDER=1` in `/opt/crypto-screener/.env`.
7. `./deploy/contabo/deploy.sh` (chown + `up -d`), then check `--status`,
   `curl 127.0.0.1:5087/api/paper/strategies` (`worker.running: true`) and `/api/whales/recorder`.
8. On the Mac install the persistent tunnel (local 5087 must be free):
   `cp deploy/contabo/com.maxbelov.crypto-screener-tunnel.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.maxbelov.crypto-screener-tunnel.plist`

## Rollback

- Code: `DEPLOY_REF=<previous sha> ./deploy/contabo/deploy.sh` (`src.prev/` holds the previous tree).
- Back to the Mac: `launchctl bootout gui/$(id -u)/com.maxbelov.crypto-screener-tunnel`,
  `./deploy/contabo/deploy.sh --stop`, copy the DBs back with `sqlite3 .backup`, start the local launcher.
- Data: `gunzip -c /opt/backups/crypto-screener/<date>/<file>.gz > <file>` with the container stopped.
- Remove completely: `docker compose -p crypto-screener down` (keeps `data/`), `rm /etc/cron.d/crypto-screener-backup`.

## Not done (deliberately)

- No public domain, TLS, nginx vhost or auth — SSH tunnel only.
- No Hermes LLM on the VPS (analyst shows the baseline, labelled unavailable).
- Flask development server (same as the local launcher; single process is required by the
  workers' flocks). Fine behind a loopback tunnel for one user, not for public traffic.
- No off-host backup copy (backups live on the same disk).
