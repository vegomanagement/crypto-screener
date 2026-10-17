"""Flask-only launcher for the review-fixes worktree on 127.0.0.1:5095 (no Telegram/scheduler)."""
import os
import sys

sys.path.insert(0, '/Users/maxbelov/.hermes/cache/scratch')
sys.path.insert(0, '/Users/maxbelov/Projects/wt/review-fixes')
os.chdir('/Users/maxbelov/Projects/wt/review-fixes')
os.environ.setdefault('PORT', '5095')
os.environ.setdefault('DB_PATH', '/Users/maxbelov/.hermes/cache/scratch/review-fixes-signals.db')
if os.environ.get('ANALYST_NO_HERMES') != '1':
    import hermes_env  # noqa: E402
    hermes_env.load()   # sets HERMES_API_URL/KEY in-process, prints nothing

import screener  # noqa: E402

if __name__ == '__main__':
    screener.app.run(host='127.0.0.1', port=5095, debug=False, use_reloader=False, threaded=True)
