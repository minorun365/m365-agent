# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

M365 Agent は、チャットで依頼すると AIエージェントが Outlook カレンダー・Microsoft To Do・Confluence を操作する秘書アプリである。

## 開発コマンド

```bash
# 依存インストール
npm install

# フロントエンド開発サーバー（Vite）
npm run dev

# ビルド（TypeScriptコンパイル + Viteビルド）
npm run build

# Amplify Sandbox 起動（AWSリソースのデプロイ、Confluence環境変数を読み込んで起動）
export $(cat .env.local | grep CONFLUENCE | xargs) && npx ampx sandbox

# 本番デプロイ: GitHubにpushするとAmplify Hostingが自動デプロイ
```

ローカル開発時はターミナルが2つ必要で、AWS側の Sandbox とフロント側の Vite を並行起動する。

## アーキテクチャ

### 2層構成: フロントエンド + AgentCoreバックエンド

フロントエンド（`src/`）は React + Vite で構成し、Amplify Gen2 Hosting でデプロイする。
- `main.tsx` はエントリーポイントで、Amplify を初期化して Cognito認証（`<Authenticator>`）でラップする
- `App.tsx` はチャットUIを担当し、SSEストリーミングで AgentCore Runtime を呼び出してテキストとツール使用イベントをリアルタイムに表示する
- `msal.ts` では Microsoft Entra ID (MSAL) を設定し、Graph APIアクセストークンを取得・管理する

バックエンドは `amplify/agent/` にあり、Bedrock AgentCore Runtime（Docker on microVM）で動作する。
- `app.py` は Strands Agents ベースのAIエージェントで、`BedrockAgentCoreApp` の entrypoint として SSEストリーミング応答を返す
- `Dockerfile` は uv + Python 3.13 を使い、OTel計装付きの ARM64ビルドを行う
- `requirements.txt` には strands-agents, bedrock-agentcore, httpx, atlassian-python-api を記載している

IaC（`amplify/`）には Amplify Gen2 と AWS CDK を使う。
- `backend.ts` は Amplifyバックエンドを定義し、認証（Cognito）と AgentCoreスタックを作成する
- `agent/resource.ts` は `@aws-cdk/aws-bedrock-agentcore-alpha` の L2コンストラクトを使って CDK で AgentCore Runtime リソースを定義し、Cognito JWT認証、Bedrockモデル呼び出し権限、Confluence環境変数の注入を行う
- `auth/resource.ts` はメールアドレスログインによる Cognito認証を設定する

### 認証フロー（二重認証）
1. Cognito認証は Amplify Authenticator を使い、アプリ利用の認証を行う
2. Microsoft Entra ID (MSAL) ではポップアップ認証を行い、Outlook/To Do操作用の Graph APIアクセストークンを取得する

フロントから AgentCore へのリクエストでは、次のように認証情報を渡す。
- `Authorization: Bearer {Cognito Access Token}` で AgentCore の Inbound JWT認証を行う
- body に `msGraphAccessToken` を同梱し、Graph API用として渡す（PoCのためフロントから渡す方式）

### ツール設計パターン
Graph APIトークンは closure で保持し、LLM に見せない設計にしている。`create_graph_tools(access_token)` の内部にある `@tool` 関数が、closure 経由でトークンを参照する。

### セッション管理
- フロントでは `crypto.randomUUID()` を使ってセッションIDを生成し、リロード時に新規発行する
- AgentCore側では `_agent_cache` に Agent インスタンスをキャッシュし、同じセッションIDであれば会話履歴を保持する

## 環境変数

`.gitignore` 済みの `.env.local` に設定する。
- `VITE_MS_CLIENT_ID` には Microsoft Entra ID のアプリ Client ID を設定する（必須）
- `VITE_MS_AUTHORITY` は認証エンドポイントで、デフォルトは `https://login.microsoftonline.com/common`
- `CONFLUENCE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN`, `CONFLUENCE_DEFAULT_SPACE_KEY` は Confluence連携用の任意項目で、未設定時は無効化される

## リージョン

AgentCore Runtime とフロントの呼び出し先は us-east-1 に統一している。Bedrockモデルも `us-east-1` で、`us.` プレフィックス付きのモデルIDを使用する。

## 既知の注意点

- `amplify_outputs.json` は gitignore 済みで、Sandbox実行時に自動生成される。Sandbox前にフロントだけ動かしたい場合はダミーファイルを作成する
- MSAL の Redirect URI は Entra 側の登録と完全に一致させる必要があり、末尾スラッシュの有無にも注意する
- Amplify CLIの `--environment-variables` は追加ではなく**置換**されるので、更新時は全変数を含める
- Confluence の認証には「API Key」ではなく、id.atlassian.com で発行する「API Token」を使う
