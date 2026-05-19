#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate

echo "🚀 Запускаю Crypto Screener Pro..."
python3 screener.py &
SERVER_PID=$!
sleep 2

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Запускаю ngrok туннель..."
echo "  Скопируй URL https://xxxx.ngrok-free.app"
echo "  и вставляй в алерты TradingView как:"
echo "  https://xxxx.ngrok-free.app/webhook"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

ngrok http 5001

kill $SERVER_PID 2>/dev/null
