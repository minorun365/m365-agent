# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

M365 Agent - チャットで依頼すると、AIエージェントが Outlook カレンダー・Microsoft To Do・Confluence を操作する秘書アプリ。

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

ローカル開発時は**ターミナル2つ**必要: Sandbox（AWS側）とVite（フロント側）を並行起動する。

## アーキテクチャ

### 2層構成: フロントエンド + AgentCoreバックエンド

**フロントエンド** (`src/`): React + Vite、Amplify Gen2 Hosting でデプロイ
- `main.tsx` - エントリーポイント。Amplify初期化、Cognito認証（`<Authenticator>`）でラップ
- `App.tsx` - チャットUI。SSEストリーミングでAgentCore Runtimeを呼び出し、テキストとツール使用イベントをリアルタイム表示
- `msal.ts` - Microsoft Entra ID (MSAL) の設定。Graph APIアクセストークンの取得・管理

**バックエンド** (`amplify/agent/`): Bedrock AgentCore Runtime（Docker on microVM）
- `app.py` - Strands AgentsベースのAIエージェント。`BedrockAgentCoreApp`のentrypointとしてSSEストリーミング応答を返す
- `Dockerfile` - uv + Python 3.13、ARM64ビルド、OTel計装付き
- `requirements.txt` - strands-agents, bedrock-agentcore, httpx, atlassian-python-api

**IaC** (`amplify/`): Amplify Gen2 + AWS CDK
- `backend.ts` - Amplifyバックエンド定義。認証（Cognito）+ AgentCoreスタックを作成
- `agent/resource.ts` - CDKでAgentCore Runtimeリソースを定義（`@aws-cdk/aws-bedrock-agentcore-alpha` L2コンストラクト使用）。Cognito JWT認証、Bedrockモデル呼び出し権限、Confluence環境変数の注入
- `auth/resource.ts` - Cognito認証設定（メールアドレスログイン）

### 認証フロー（二重認証）
1. **Cognito認証**: アプリ利用の認証（Amplify Authenticator）
2. **Microsoft Entra ID (MSAL)**: Outlook/To Do操作用のGraph APIアクセストークン取得（ポップアップ認証）

フロントからAgentCoreへのリクエスト:
- `Authorization: Bearer {Cognito Access Token}` - AgentCoreのInbound JWT認証
- bodyに `msGraphAccessToken` を同梱 - Graph API用（PoCのためフロントから渡す方式）

### ツール設計パターン
Graph APIトークンはclosureで保持し、LLMに見せない（`create_graph_tools(access_token)` → 内部の`@tool`関数がclosure経由でトークンを参照）。

### セッション管理
- フロントで`crypto.randomUUID()`でセッションID生成（リロードで新規）
- AgentCore側で`_agent_cache`にAgent インスタンスをキャッシュ、同じセッションIDなら会話履歴を保持

## 環境変数

`.env.local` に設定（`.gitignore`済み）:
- `VITE_MS_CLIENT_ID` - Microsoft Entra IDのアプリClient ID（必須）
- `VITE_MS_AUTHORITY` - 認証エンドポイント（デフォルト: `https://login.microsoftonline.com/common`）
- `CONFLUENCE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN`, `CONFLUENCE_DEFAULT_SPACE_KEY` - Confluence連携（任意、未設定時は無効化）

## リージョン

AgentCore Runtimeとフロントの呼び出し先は **us-east-1** に統一。Bedrockモデルも`us-east-1`で`us.`プレフィックス付きモデルIDを使用。

## 既知の注意点

- `amplify_outputs.json` はSandbox実行で自動生成される（gitignore済み）。Sandbox前にフロントだけ動かしたい場合はダミーファイルを作成する
- MSAL の Redirect URI は Entra 側の登録と完全一致が必要（末尾スラッシュの有無に注意）
- Amplify CLIの `--environment-variables` は追加ではなく**置換**されるので、更新時は全変数を含める
- Confluence の認証は「API Key」ではなく「API Token」（id.atlassian.com で発行）を使う
