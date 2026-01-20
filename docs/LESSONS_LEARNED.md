# 開発で学んだ教訓

## 1. リージョンの一貫性が超重要

### 問題
- AgentCore Runtime は `us-east-1` にデプロイ
- しかし Strands Agent がデフォルトで `ap-northeast-1` の Bedrock を呼び出していた
- `us.` プレフィックスのモデルは米国リージョンでしか使えない

### 解決策
```python
from strands.models import BedrockModel

bedrock_model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    region_name="us-east-1"  # 明示的に指定
)
agent = Agent(model=bedrock_model, ...)
```

### フロントエンド側も注意
```typescript
// AgentCore のリージョンと一致させる
const url = `https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/...`;
```

---

## 2. MSAL (Microsoft 認証) の実装ポイント

### 初期化を確実に
```typescript
let initialized = false;
export async function ensureMsalInitialized() {
  if (!initialized) {
    await msalInstance.initialize();
    initialized = true;
  }
}
```

### トークン更新の順序
1. `acquireTokenSilent` を試す（バックグラウンドで更新）
2. 失敗したら `acquireTokenPopup` でユーザーに再ログインを促す

### セキュリティ
- `sessionStorage` を使う（`localStorage` より安全）
- トークンをログに出力しない

---

## 3. Graph API トークンの扱い

### LLM に見せない
トークンを tool の引数にすると LLM が見える場所に置かれてしまう。
closure で保持して HTTP ヘッダーにだけ付ける。

```python
def create_graph_tools(access_token: str, user_timezone: str):
    """closure でトークンを保持"""

    @tool
    def get_schedule(start_iso: str, end_iso: str) -> str:
        headers = {"Authorization": f"Bearer {access_token}"}  # ここで使う
        # ...

    return [get_schedule, ...]
```

---

## 4. Amplify Sandbox 開発フロー

### amplify_outputs.json がないとエラー
- `npx ampx sandbox` 実行前はファイルが存在しない
- デザイン確認だけしたい場合はダミーファイルを作成

```json
{
  "version": "1",
  "custom": {
    "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/dummy"
  }
}
```

### 認証スキップでデザイン確認
```typescript
const SKIP_AUTH = true; // 開発時のみ

{SKIP_AUTH ? <App /> : <Authenticator><App /></Authenticator>}
```

### AWS 認証切れに注意
- Sandbox 実行中に AWS 認証が切れるとデプロイが失敗する
- `aws login` で再認証後、Sandbox を再起動

---

## 5. デバッグ手法

### バックエンド（AgentCore）
```bash
# ロググループを探す
aws logs describe-log-groups --region us-east-1 \
  --log-group-name-prefix "/aws/bedrock-agentcore"

# 最新ログを確認
aws logs get-log-events --region us-east-1 \
  --log-group-name "/aws/bedrock-agentcore/runtimes/..." \
  --log-stream-name "..." \
  --limit 50
```

### フロントエンド
- ブラウザの DevTools（F12）→ Console でエラー確認
- Network タブで API リクエスト/レスポンスを確認

---

## 6. AgentCore Runtime 名の制約

### 問題
Amplify Hosting デプロイ時に以下のエラー：
```
Runtime name must start with a letter and contain only letters, numbers, and underscores
```

### 原因
- `stack.stackName.split('-')[2]` で環境識別子を取得していた
- Sandbox では `undefined` になり、本番では `branch` のようにハイフンを含む値になった

### 解決策
```python
# 英数字のみを抽出し、小文字に統一
stackNameParts = stack.stackName.split('-')
rawEnvId = stackNameParts[3] if len(stackNameParts) >= 4 else stack.stackName[-10:]
envId = re.sub(r'[^a-zA-Z0-9]', '', rawEnvId).lower()

runtimeName = f"outlook_agent_{envId}"
```

---

## 7. LLM は曜日計算が苦手

### 問題
「今週の予定は？」と聞くと、曜日がずれた回答が返ってきた
- 例: 1/15 を「水曜日」と誤認（実際は木曜日）

### 原因
- システムプロンプトに ISO 8601 形式の日時を渡していた
- LLM は日付から曜日を計算するのが苦手

### 解決策
曜日情報を含むカスタムツールを追加：
```python
@tool
def get_current_datetime() -> str:
    """現在の日時と曜日を取得します。"""
    WEEKDAY_JP = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]
    tz = ZoneInfo(user_timezone)
    now = datetime.now(tz)
    weekday = WEEKDAY_JP[now.weekday()]
    return f"現在日時: {now.strftime('%Y年%m月%d日')}（{weekday}）..."
```

システムプロンプトにも指示を追加：
```
- 「今日」「明日」「今週」などの相対表現を使う場合は、
  必ず get_current_datetime ツールで現在日時を確認してから処理してください
- 曜日を計算で求めず、必ず get_current_datetime ツールで確認してください
```

---

## 8. Redirect URI の末尾スラッシュ問題

### 問題
Amplify Hosting で認証エラー：
```
invalid_request: The provided value for the input parameter 'redirect_uri' is not valid.
```

### 原因
- コード: `window.location.origin` → `https://main.xxx.amplifyapp.com`（スラッシュなし）
- Entra 登録: `https://main.xxx.amplifyapp.com/`（スラッシュあり）

### 解決策
Entra の Redirect URI を**末尾スラッシュなし**で登録：
```
https://main.d2o4kewmx47y6o.amplifyapp.com
```

---

## 9. MSAL ログアウト実装

### 実装方法
```typescript
// msal.ts
export async function logout(): Promise<void> {
  const account = pickAccount();
  if (account) {
    await msalInstance.logoutPopup({ account });
  }
}

// App.tsx - トグル式ボタン
const handleToggleOutlook = async () => {
  if (msConnected) {
    await logout();
    setMsGraphAccessToken(null);
    setMsConnected(false);
  } else {
    const res = await msalInstance.loginPopup({ scopes: graphScopes });
    setMsGraphAccessToken(res.accessToken);
    setMsConnected(true);
  }
};
```

---

## 10. 今後の改善候補

1. **リージョンを環境変数化**
   - フロント・バックで共通のリージョン設定を使う

2. **エラーハンドリング強化**
   - Graph API のエラーをユーザーにわかりやすく表示
   - トークン期限切れ時の自動再取得

3. **本番化に向けて**
   - Graph Token をフロントから渡すのをやめる（AgentCore Identity の Outbound Auth を使う）
   - 環境変数を Amplify コンソールで管理

---

---

## 11. Confluence API 認証の落とし穴

### 問題
Confluence APIを叩くと以下のエラー：
```
Current user not permitted to use Confluence
```

### 原因
**APIキー**と**APIトークン**を混同していた。

| 種類 | 用途 | 発行場所 |
|------|------|----------|
| API Key | 組織管理用 | admin.atlassian.com |
| API Token | REST API認証用 | id.atlassian.com |

### 解決策
1. https://id.atlassian.com/manage-profile/security/api-tokens にアクセス
2. 「Create API token」で新規発行
3. 環境変数 `CONFLUENCE_API_TOKEN` に設定

### 教訓
- Atlassian の「API Key」は組織管理者向けの機能
- REST API には必ず「API Token」を使う
- エラーメッセージが「permission denied」的なものだが、実際は認証情報の種類が違う

---

## 12. CDK で AgentCore Runtime に環境変数を渡す

### 方法
`@aws-cdk/aws-bedrock-agentcore-alpha` の `Runtime` コンストラクトには `environmentVariables` プロパティがある：

```typescript
const runtime = new agentcore.Runtime(stack, 'MyRuntime', {
  runtimeName: 'my_agent',
  // ...
  environmentVariables: {
    CONFLUENCE_URL: process.env.CONFLUENCE_URL || '',
    CONFLUENCE_EMAIL: process.env.CONFLUENCE_EMAIL || '',
    CONFLUENCE_API_TOKEN: process.env.CONFLUENCE_API_TOKEN || '',
  },
});
```

### ローカル開発時
```bash
export $(cat .env.local | grep CONFLUENCE | xargs) && npx ampx sandbox
```

### 本番デプロイ時
Amplify コンソール → アプリ → 環境変数 で設定

---

## 13. Amplify 環境変数の CLI 更新は上書きに注意

### 問題
`aws amplify update-app --environment-variables` で環境変数を追加したら、既存の変数が消えた。

```bash
# これをやると既存の VITE_MS_CLIENT_ID などが消える！
aws amplify update-app --app-id xxx \
  --environment-variables CONFLUENCE_URL=xxx,CONFLUENCE_EMAIL=xxx
```

### 原因
`--environment-variables` は**追加**ではなく**置換**される。

### 解決策
既存の環境変数も含めて、すべて一度に指定する：

```bash
aws amplify update-app --app-id d2o4kewmx47y6o --region us-east-1 \
  --environment-variables \
    VITE_MS_CLIENT_ID=xxx,\
    VITE_MS_AUTHORITY=xxx,\
    CONFLUENCE_URL=xxx,\
    CONFLUENCE_EMAIL=xxx,\
    CONFLUENCE_API_TOKEN=xxx
```

### 教訓
- 環境変数を追加する前に `aws amplify get-app` で現在の値を確認
- 更新時は必ず全変数を含める
- 消えた場合は再ビルドが必要（`aws amplify start-job`）

---

## 環境情報（参考）

- **Entra App Client ID**: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
- **Entra Tenant ID**: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
- **Bedrock Model**: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- **AgentCore Region**: `us-east-1`
- **Confluence URL**: `https://your-domain.atlassian.net`
