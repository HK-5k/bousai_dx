#!/bin/bash
# 香川防災DX 本番起動スクリプト
# 通常のターミナル（Terminal.app）で実行してください

cd "$(dirname "$0")"
source venv/bin/activate

# 本番: APIキーは環境変数 GEMINI_API_KEY または .env ファイルで設定
if [ -z "$GEMINI_API_KEY" ]; then
  echo "【注意】環境変数 GEMINI_API_KEY が未設定の場合、.env に GEMINI_API_KEY=キー を記述してください。"
fi

# IPアドレスを取得（Wi-Fi: en0, 有線: en1）
IP_WIFI=$(ipconfig getifaddr en0 2>/dev/null)
IP_ETH=$(ipconfig getifaddr en1 2>/dev/null)
IP=${IP_WIFI:-$IP_ETH}
if [ -z "$IP" ]; then
  IP=$(ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -1)
fi

echo "=========================================="
echo "  香川防災DX 起動中..."
echo "=========================================="
echo ""
echo "【iPhoneでアクセスする場合】"
echo "  1. iPhoneをMacと同じWi-Fiに接続"
echo "  2. Safariで以下のURLを開く:"
echo ""
echo "     http://${IP:-'IPを確認'}:8501"
echo ""
echo "【つながらない場合】"
echo "  • システム設定 → ネットワーク → Wi-Fi → 詳細 → IPアドレス で確認"
echo "  • ファイアウォール: システム設定 → セキュリティ → ファイアウォール"
echo "    → Python/Streamlit を「許可」に設定"
echo "  • Mac再起動後に再度試す"
echo ""

streamlit run app.py --server.address=0.0.0.0 --server.port=8501
