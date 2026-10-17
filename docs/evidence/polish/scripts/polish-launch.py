"""Safe local launcher for the polish worktree: Flask dev server only (no Telegram/scheduler)."""
import os
import sys

ROOT = '/Users/maxbelov/Projects/wt/polish'
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('PORT', '5096')
os.environ.setdefault('LIQUIDATIONS_DB_PATH', '/Users/maxbelov/.hermes/cache/scratch/polish-liquidations.db')

import screener  # noqa: E402

if __name__ == '__main__':
    screener.app.run(host='127.0.0.1', port=5096, debug=False, use_reloader=False, threaded=True)
