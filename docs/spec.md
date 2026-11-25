# M365 Agent 詳細設計書

## 1. システム全体構成

```
┌─────────────────────────────────────────────────────────────────┐
│  ブラウザ（React + Vite）                                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────────────┐   │
│  │ Cognito  │  │  MSAL    │  │  App.tsx（チャットUI）         │   │
│  │ 認証     │  │  認証    │  │  SSEストリーミング処理         │   │
│  └────┬─────┘  └────┬─────┘  └──────────────┬───────────────┘   │
│       │             │                        │                   │
└───────┼─────────────┼────────────────────────┼───────────────────┘
        │             │                        │
        │ Cognito     │ Graph                  │ POST (SSE)
        │ Access      │ Access                 │ Authorization: Bearer {Cognito Token}
        │ Token       │ Token                  │ Body: { prompt, msGraphAccessToken, ... }
        │             │                        │
        │             │          ┌─────────────▼──────────────────┐
        │             │          │  Bedrock AgentCore Runtime      │
        │             │          │  (us-east-1, microVM, ARM64)    │
        │             │          │                                 │
        │             │          │  ┌───────────────────────────┐  │
        │             │          │  │ BedrockAgentCoreApp       │  │
        │             │          │  │  └─ invoke_agent()        │  │
        │             │          │  │      └─ Strands Agent     │  │
        │             │          │  │          ├─ Graph Tools    │──┼──→ Microsoft Graph API
        │             │          │  │          ├─ ToDo Tools     │──┼──→ Microsoft Graph API
        │             │          │  │          └─ Confluence     │──┼──→ Atlassian Cloud API
        │             │          │  │             Tools          │  │
        │             │          │  └───────────────────────────┘  │
        │             │          │              │                   │
        │             │          └──────────────┼───────────────────┘
        │             │                         │
        │             │                         ▼
        │             │          ┌──────────────────────────────┐
        │             │          │  Amazon Bedrock (us-east-1)  │
        │             │          │  Claude Sonnet 4.5           │
        │             │          └──────────────────────────────┘
        │             │
        ▼             ▼
┌──────────────┐  ┌──────────────────────┐
│ Amazon       │  │ Microsoft Entra ID   │
│ Cognito      │  │ (OAuth 2.0 / MSAL)   │
└──────────────┘  └──────────────────────┘
```

## 2. フロントエンド設計

### 2.1 コンポーネント構成

| ファイル | 役割 |
|----------|------|
| `index.html` | SPAのエントリーポイント（`<div id="root">`） |
| `src/main.tsx` | Amplifyの初期化、Cognito認証ラッパー、日本語化、Reactのレンダリング |
| `src/App.tsx` | チャット画面全体のメインUIコンポーネント |
| `src/msal.ts` | MSALの設定・初期化・トークン管理のユーティリティ |
| `src/App.css` | チャットUI用のスタイル |
| `src/index.css` | リセットやフォントなどのグローバルスタイル |

### 2.2 画面構成

```
┌─────────────────────────────────────────────────┐
│  ヘッダー                                        │
│  [左余白] [タイトル + サブタイトル] [Entra IDボタン] │
├─────────────────────────────────────────────────┤
│                                                 │
│  メッセージエリア（スクロール可能）                  │
│                                                 │
│  [ユーザーバブル]                      ← 右寄せ   │
│  [AIバブル]                           ← 左寄せ   │
│  [ツール使用中ステータス]              ← 左寄せ   │
│  [AIバブル（ツール結果を含む応答）]     ← 左寄せ   │
│                                                 │
├─────────────────────────────────────────────────┤
│  入力フォーム                                     │
│  [テキスト入力] [送信ボタン]                       │
└─────────────────────────────────────────────────┘
```

### 2.3 状態管理

`App.tsx` では `useState` を使って次の状態を管理する。

| 状態変数 | 型 | 初期値 | 用途 |
|----------|------|--------|------|
| `messages` | `Message[]` | `[]` | チャット履歴 |
| `input` | `string` | `''` | 入力フィールドの値 |
| `loading` | `boolean` | `false` | 二重送信を防ぐための送信中フラグ |
| `msConnected` | `boolean` | `false` | Entra ID接続状態 |
| `msGraphAccessToken` | `string \| null` | `null` | Graph APIアクセストークン |

`Message` 型は次のように定義する。

```typescript
interface Message {
  id: string;            // crypto.randomUUID()
  role: 'user' | 'assistant';
  content: string;       // メッセージ本文（Markdown対応）
  isToolUsing?: boolean;   // ツール使用中フラグ
  toolCompleted?: boolean; // ツール完了フラグ
  toolName?: string;       // 使用中のツール名
}
```

セッションIDは `useRef` で管理する。
- `sessionIdRef` は画面ロード時に `crypto.randomUUID()` で1回生成する
- リロードすると新しいセッションになり、AgentCore側の会話履歴はリセットされる

### 2.4 認証フロー

#### Cognito認証（アプリ利用）
- `main.tsx` で `<Authenticator>` コンポーネントを使い、アプリ全体をラップする
- メールアドレスとパスワードでサインアップ・サインインする
- `I18n` で日本語化している

#### Microsoft Entra ID認証（M365連携）

```
[初回ログイン]
1. ユーザーが「Entra IDに接続」ボタンをクリック
2. msalInstance.loginPopup({ scopes: graphScopes })
3. ポップアップでMicrosoftアカウントにサインイン
4. accessToken を state に保存、msConnected = true

[アプリ再読み込み時]
1. useEffect で ensureMsalInitialized()
2. pickAccount() で既存アカウントを確認
3. acquireTokenSilent() でトークン取得を試行
4. 成功: msConnected = true / 失敗: msConnected = false

[メッセージ送信時のトークン更新]
1. acquireTokenSilent() を試行
2. 失敗した場合: acquireTokenPopup() でユーザーに再認証を促す
```

Graph APIには次のスコープを要求する。
- `User.Read`: ユーザー情報の読み取り
- `Calendars.ReadWrite`: カレンダーの読み書き
- `Tasks.ReadWrite`: To Doタスクの読み書き
- `offline_access`: リフレッシュトークンの取得

MSALは次のように設定している。
- キャッシュには `sessionStorage` を使い、ブラウザを閉じると消える
- リダイレクトURIには `window.location.origin` を指定しており、Entra側の登録と完全に一致させる必要がある

### 2.5 メッセージ送信・SSEストリーミング処理

#### リクエスト

```
POST https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/{AGENT_ARN}/invocations?qualifier=DEFAULT

Headers:
  Authorization: Bearer {Cognito Access Token}
  Content-Type: application/json
  X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: {sessionId}

Body:
  {
    "prompt": "今日の予定を教えて",
    "msGraphAccessToken": "{Graph API Access Token}",
    "userTimeZone": "Asia/Tokyo",
    "clientNowIso": "2026-01-15T10:00:00"
  }
```

- `AGENT_ARN` は `amplify_outputs.json` の `custom.agentRuntimeArn` から取得する
- `clientNowIso` は `sv-SE` ロケールでフォーマットし、ISO 8601互換の `YYYY-MM-DDTHH:mm:ss` 形式にする

#### レスポンス（SSEストリーミング）

```
data: {"type": "text", "data": "今日の"}
data: {"type": "text", "data": "予定を"}
data: {"type": "tool_use", "tool_name": "get_current_datetime"}
data: {"type": "text", "data": "確認しました。"}
data: [DONE]
```

#### ストリーミング処理フロー

```
ReadableStream から chunk を読み取り
  → 改行で分割
  → "data: " プレフィックスを除去
  → JSONパース
  → イベントタイプに応じて処理:

[text イベント]
  ツール使用中でない場合:
    → buffer に追加
    → messages の末尾メッセージを更新（リアルタイム表示）
  ツール使用後の最初のテキスト:
    → ツールメッセージを完了状態に更新
    → 新しいメッセージを追加

[tool_use イベント]
  → isInToolUse = true
  → 既にバッファにテキストがあれば別メッセージとして確定
  → ツール使用中メッセージを追加（⏳ ツール名 ツールを利用中...）
```

## 3. バックエンド設計

### 3.1 エントリーポイント (`app.py`)

`BedrockAgentCoreApp` を使い、`@app.entrypoint` デコレータでエントリーポイントを定義する。

```python
@app.entrypoint
async def invoke_agent(payload, context):
    # payload: フロントからのJSON
    # context: AgentCoreコンテキスト（session_id等）
    # → async generator として SSE イベントを yield
```

### 3.2 セッション管理

```python
# モジュールレベルのキャッシュ（microVM内で保持）
_agent_cache: dict[str, Agent] = {}
```

- セッションIDをキーにして `Agent` インスタンスをキャッシュする
- AgentCoreの仕組みにより、同じセッションIDで呼ばれると同じmicroVMが再利用される
- Agentインスタンスを再利用することで、会話履歴である `agent.messages` が保持される
- トークンが変わる可能性があるため、再利用時もツールは毎回再生成する

### 3.3 Strands Agent 構成

```python
Agent(
    model=BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        region_name="us-east-1"
    ),
    system_prompt=system_prompt,
    tools=all_tools  # Graph + ToDo + Confluence ツールの結合リスト
)
```

ストリーミング実行は次のように行う。
```python
async for event in agent.stream_async(prompt):
    converted = convert_event(event)
    if converted:
        yield converted
```

### 3.4 ツール一覧

ツールはすべてクロージャパターンで生成する。トークンや認証情報はHTTPヘッダーだけに使い、LLMには見せない。

#### Graph API ツール（`create_graph_tools()`）

| ツール名 | 引数 | 機能 |
|----------|------|------|
| `get_current_datetime` | なし | ユーザーのタイムゾーンに準拠した現在の日時と曜日を取得 |
| `get_schedule` | `start_iso`, `end_iso` | 指定期間の予定一覧を取得（`GET /me/calendarView`） |
| `create_meeting` | `subject`, `start_iso`, `end_iso`, `attendees`, `body?` | 会議を作成し参加者に招待を送信（`POST /me/events`） |

#### To Do ツール（`create_todo_tools()`）

| ツール名 | 引数 | 機能 |
|----------|------|------|
| `get_task_lists` | なし | タスクリスト一覧を取得 |
| `get_tasks` | `list_id`, `include_completed?` | 指定リスト内のタスク一覧を取得 |
| `create_task` | `list_id`, `title`, `due_date?`, `importance?`, `body?`, `reminder_datetime?` | 新しいタスクを作成 |
| `update_task` | `list_id`, `task_id`, `title?`, `due_date?`, `importance?`, `body?` | 既存のタスクを更新 |
| `complete_task` | `list_id`, `task_id` | タスクを完了状態にする |

#### Confluence ツール（`create_confluence_tools()`）

| ツール名 | 引数 | 機能 |
|----------|------|------|
| `get_confluence_page` | `page_id` | ページの内容を取得 |
| `search_confluence` | `query`, `space_key?`, `limit?` | CQLでコンテンツを検索 |
| `create_confluence_page` | `title`, `body`, `space_key?`, `parent_id?` | HTML形式で新しいページを作成 |
| `update_confluence_page` | `page_id`, `title`, `body` | 既存のページを更新 |

Confluence ツールは、環境変数 `CONFLUENCE_URL`、`CONFLUENCE_EMAIL`、`CONFLUENCE_API_TOKEN` が未設定の場合、空リストを返して機能を無効化する。実装には `atlassian-python-api` ライブラリを使う。

### 3.5 システムプロンプト

```
あなたは秘書AIエージェントです。
ユーザーの Outlook カレンダー、Microsoft To Do、Confluence を操作できます。

# タイムゾーン
{user_timezone}

# 注意事項
- 日時は必ず ISO8601 形式で指定
- 相対表現（今日/明日/今週）は必ず get_current_datetime で現在日時を確認してから処理
- 曜日を計算で求めず、必ず get_current_datetime で確認
- To Do 操作には必ず list_id が必要。まず get_task_lists でリストIDを取得
```

### 3.6 イベント変換 (`convert_event()`)

Strands Agentの内部イベントをフロント向けJSONに変換する。

| Strandsイベント | 変換後 | 説明 |
|----------------|--------|------|
| `contentBlockDelta.delta.text` | `{"type": "text", "data": "..."}` | テキスト差分 |
| `contentBlockStart.start.toolUse` | `{"type": "tool_use", "tool_name": "..."}` | ツール使用開始 |
| その他 | `None`（無視） | 不要なイベント |

### 3.7 Dockerコンテナ

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
# ARM64ビルド（CDKのContainerImageBuildで指定）
# uv でパッケージ管理
# OpenTelemetry計装付きで起動
CMD ["opentelemetry-instrument", "python", "-m", "app"]
```

次のポートを使用する。
- `8080`: AgentCore Runtimeのメインポート
- `8000`: 予備

ヘルスチェックには `curl -f http://localhost:8080/ping` を使用する。

## 4. IaC設計

### 4.1 スタック構成

```
Amplify Backend
├── AuthStack（自動生成）
│   ├── Cognito User Pool
│   └── Cognito User Pool Client
└── AgentCoreStack（カスタム）
    ├── ContainerImageBuild（CodeBuildでARM64イメージをビルド）
    └── AgentCore Runtime
        ├── Inbound認証: Cognito JWT
        ├── ネットワーク: パブリック
        ├── 環境変数: Confluence設定
        └── IAMポリシー: Bedrock InvokeModel
```

### 4.2 AgentCore Runtime リソース (`agent/resource.ts`)

- イメージビルドでは、`deploy-time-build` の `ContainerImageBuild` を使ってCodeBuild上でARM64のDockerイメージをビルドする
- ランタイム名は `outlook_agent_{envId}` とし、スタック名から抽出した英数字のみの環境識別子を入れる
- 認証は `RuntimeAuthorizerConfiguration.usingCognito()` でCognito User PoolとClientを紐付ける
- ネットワークは `RuntimeNetworkConfiguration.usingPublicNetwork()` でパブリックアクセスにする
- IAM権限は全モデル・推論プロファイルを対象に、`bedrock:InvokeModel` と `bedrock:InvokeModelWithResponseStream` を付与する

### 4.3 出力値

`backend.addOutput()` で `agentRuntimeArn` をフロントに公開する。
```json
{
  "custom": {
    "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/outlook_agent_xxx"
  }
}
```
この値は `amplify_outputs.json` に書き出され、フロントで参照される。

## 5. デプロイ

### 5.1 ローカル開発（Sandbox）

```bash
# ターミナル1: AWSリソースをデプロイ（Confluence環境変数を含む）
export $(cat .env.local | grep CONFLUENCE | xargs) && npx ampx sandbox

# ターミナル2: フロントエンド開発サーバー
npm run dev
```

Sandboxを実行すると `amplify_outputs.json` が自動生成される。

### 5.2 本番デプロイ（Amplify Hosting）

デプロイ手順は `amplify.yml` で定義している。
1. バックエンドは `npm ci` のあと `npx ampx pipeline-deploy` を実行する
2. フロントエンドは `npm run build` を実行し、`dist/` を配信する

環境変数はAmplifyコンソールで設定する。
- `VITE_MS_CLIENT_ID`, `VITE_MS_AUTHORITY` はビルド時にフロントへ埋め込む
- `CONFLUENCE_*` はデプロイ時にAgentCoreランタイムへ注入する

GitHubへpushすると自動デプロイが実行される。

## 6. 外部API依存

| API | 認証方式 | 用途 |
|-----|----------|------|
| Microsoft Graph API (`graph.microsoft.com/v1.0`) | Bearer Token（MSAL / Delegated） | カレンダー、To Do |
| Atlassian Confluence API | Basic Auth（Email + API Token） | ページCRUD、検索 |
| Amazon Bedrock (`us-east-1`) | IAMロール（AgentCoreランタイムロール） | Claude Sonnet 4.5 呼び出し |
| Bedrock AgentCore Runtime API | Bearer Token（Cognito） | エージェント呼び出し |
