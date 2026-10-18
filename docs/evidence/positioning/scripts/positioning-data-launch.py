"""Positioning worktree launcher: Flask dev server only on :5092 (no Telegram/scheduler)."""
import os
import sys

ROOT = '/Users/maxbelov/Projects/wt/positioning-data'
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ['PORT'] = '5092'

import screener  # noqa: E402

if __name__ == '__main__':
    screener.app.run(host='127.0.0.1', port=5092, debug=False, use_reloader=False, threaded=True)
