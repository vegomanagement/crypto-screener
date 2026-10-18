"""Safe local launcher for the indicator-groups worktree: Flask dev server only (no Telegram/scheduler)."""
import os
import sys

ROOT = '/Users/maxbelov/Projects/wt/indicator-groups'
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('PORT', '5091')

import screener  # noqa: E402

if __name__ == '__main__':
    screener.app.run(host='127.0.0.1', port=5091, debug=False, use_reloader=False, threaded=True)
