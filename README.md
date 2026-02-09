# 香川防災DX（本番システム）

備蓄品の撮影・AI解析・登録・一覧・CSVエクスポートを行う防災備蓄管理システムです。

## 本番環境の構成

- **永続化**: SQLite（`data/stock.db`）に登録データを保存
- **APIキー**: 環境変数 `GEMINI_API_KEY` または `.env` で設定（コードに書かない）
- **エクスポート**: リストタブからCSVダウンロード可能

## 初回セットアップ（本番）

1. **APIキーを設定**
   - 方法A: 環境変数  
     `export GEMINI_API_KEY=AIzaから始まるキー`
   - 方法B: `.env` を作成  
     `cp .env.example .env` のあと、`.env` に `GEMINI_API_KEY=あなたのキー` を記述

2. **起動**
   ```bash
   cd /path/to/bousai_dx
   ./run.sh
   ```
   または  
   `source venv/bin/activate && streamlit run app.py --server.address=0.0.0.0 --server.port=8501`

3. **iPhoneからアクセス**  
   同じWi-Fiで Safari から `http://[MacのIP]:8501` を開く

## データの保存場所

- 備蓄品データ: `data/stock.db`（SQLite）
- `.env` と `data/` は `.gitignore` 済み（リポジトリに含めない想定）

## 必要なパッケージ

`requirements.txt` を参照。  
`pip install -r requirements.txt` で一括インストール可能。
