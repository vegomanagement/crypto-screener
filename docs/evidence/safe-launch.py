"""Audit launcher: local web UI only, no polling or schedulers."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path('/Users/maxbelov/Projects/crypto-screener-audit')
sys.path.insert(0, str(ROOT))
os.environ.update(TELEGRAM_TOKEN='AUDIT_DISABLED', TELEGRAM_CHAT_ID='0',
                  ANTHROPIC_API_KEY='AUDIT_DISABLED',
                  DB_PATH='/Users/maxbelov/.hermes/cache/scratch/crypto-screener-audit.db')
import screener

def disabled(*args, **kwargs):
    raise RuntimeError('Audit mode: external messaging/model calls disabled')

for name in ('tg_send', 'tg_send_photo', 'tg_send_chunked', 'tg_send_document',
             'telegram_polling', 'start_scheduler', '_register_bot_commands'):
    if hasattr(screener, name):
        setattr(screener, name, disabled)
screener.ai.messages.create = disabled
screener.db_init()
screener.app.run(host='127.0.0.1', port=5087, debug=False, use_reloader=False)
