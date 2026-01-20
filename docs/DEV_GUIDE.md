# 秘書AIエージェント PoC開発ガイド

## ゴール

Web アプリにログインしたユーザーがチャットで依頼すると、AI エージェントが **Outlook（Microsoft Graph）** と **Confluence** を操作して次を実行する。

### Outlook 機能
* ✅ 予定参照（指定期間の予定一覧）
* ✅ 会議作成（Outlook カレンダーにイベント作成 + 参加者招待）

### Confluence 機能
* ✅ ページ取得（指定IDのページ内容を取得）
* ✅ 検索（キーワードでコンテンツを検索）
* ✅ ページ作成（新規ページを作成）
* ✅ ページ更新（既存ページを更新）

**採用スタック（固定）**

* フロント：React + Vite（Amplify Gen2）
* 認証：Amplify Auth（Cognito / メール認証）
* バックエンド：Bedrock AgentCore Runtime（Inbound JWT 認証）
* AI：Strands Agents
* IaC：AWS CDK（できるだけ L2）
* M365：個人で用意（最短は M365 Personal、Teams URL が必要なら別案）
* Confluence：Atlassian Cloud + atlassian-python-api

---

## 重要な前提（Personal でできる / できない）

### M365 Personal（個人 Microsoft アカウント）でできる

* `Calendars.Read` / `Calendars.ReadWrite` を Delegated で取得して、

  * 予定参照（`/me/calendarView`）
  * 予定作成（`/me/events`）
    ができる

### M365 Personal で厳しい / 非対応になりがち

* **Teams のオンライン会議 URL 自動生成**（`/me/onlineMeetings` など）は “personal Microsoft account は非対応” になりやすい
  → Teams URL まで PoC 要件なら、後述の「M365 Developer Program（E5 Sandbox）」か「Business Starter」を推奨

---

## 全体構成（PoC 最短）

* Cognito ログイン：アプリ利用の認証
* Microsoft ログイン（MSAL）：Outlook 操作用の Graph Access Token を取得
* フロントは AgentCore Runtime にリクエストする際、

  * `Authorization: Bearer {Cognito Access Token}`（AgentCore の Inbound JWT 用）
  * body に `msGraphAccessToken` を同梱（Graph 用）
    を送る
* AgentCore Runtime 側の Strands tool が Graph を呼ぶ

> 本番では「Graph Token をフロントから送る」のは避けたいが、PoC 最短はこれが爆速です。
> 本番寄りにする場合は後半の「次のステップ（AgentCore Identity / Outbound Auth）」へ。

---

# 0. 事前準備チェックリスト

## あなたがやる

* [ ] AWS アカウント準備（バージニア北部を使う）
* [ ] Bedrock で Claude などモデル有効化
* [ ] M365 アカウント準備（Personal でOK）
* [ ] Entra（アプリ登録）用のテナント準備（Personal でも作成可能）
* [ ] Qiita ベース（cdk-agent）リポジトリを用意（fork/clone）

## Claude Code にやらせる

* [ ] フロント（MSAL 追加、Connect Outlook ボタン、payload 拡張）
* [ ] バック（Graph ツール追加、requirements 追加、system prompt 調整）
* [ ] 動作確認用のプロンプト例やテストケース整備

---

# 1. Microsoft 側の環境準備（Outlook/Graph を触る “最短手順”）

## 1-1. 方式選定（最短 → 本格）

### A. 最短（おすすめ）：M365 Personal + Entra で “アプリ登録だけ” 作る

* 予定参照・予定作成はこの構成で十分
* Teams URL 自動生成は割り切り（やらない）

---

## 1-2. Entra テナントを用意する（M365 Personal の場合）

M365 Personal には「組織テナント管理（M365 管理センター）」が標準で付くわけではないので、**アプリ登録のための Entra テナント**を作ります。

### 手順（概要）

1. Azure / Entra にサインイン（個人 Microsoft アカウントでOK）
2. Microsoft Entra ID から Tenant を作成（Workforce）

> 画面の導線は変わりやすいので、詰まったら「Entra create tenant」で検索して同名画面を探すのが最短です。

---

## 1-3. App Registration を作る（Graph 用）

### あなたがやる（Entra 管理センター）

1. **App registrations** → **New registration**
2. Name：`outlook-agent`
3. Supported account types（おすすめ）

   * PoC を Personal でも組織でも動かしたい：
     **Accounts in any organizational directory and personal Microsoft accounts**
   * Personal だけでいい：
     **Personal Microsoft accounts**
4. Redirect URI（SPA）

   * ローカル（Vite）：`http://localhost:5173/`
   * 本番（Amplify）：`https://<Amplifyのドメイン>/`

> Redirect URI は “完全一致” なので、末尾スラッシュあり/なしをコードと一致させること。

5. 作成後、Overview から控える

   * **Application (client) ID**（= clientId）: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
   * （必要なら）Directory (tenant) ID: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

---

## 1-4. Authentication 設定（SPA）

App registrations → Authentication で以下を確認：

* Platform: **Single-page application**
* Redirect URI が入っていること

---

## 1-5. API Permissions（Microsoft Graph / Delegated）

App registrations → API permissions → Add a permission → Microsoft Graph → Delegated permissions

最小セット：

* `User.Read`
* `Calendars.ReadWrite`
* （推奨）`offline_access`（サイレント更新が安定しやすい）

> 組織テナントの場合、環境によっては "Grant admin consent" が必要です。

---

## 1-6. Confluence Cloud 設定

### API Token の発行

1. https://id.atlassian.com/manage-profile/security/api-tokens にアクセス
2. 「Create API token」をクリック
3. ラベルを入力（例: `outlook-agent`）
4. 生成されたトークンをコピー

> **注意**: 「API Key」（admin.atlassian.com で発行）ではなく「API Token」を使用すること

### 環境変数の設定

`.env.local` に追加：

```bash
CONFLUENCE_URL=https://your-domain.atlassian.net
CONFLUENCE_EMAIL=your-email@example.com
CONFLUENCE_API_TOKEN=発行したトークン
CONFLUENCE_DEFAULT_SPACE_KEY=~xxxxxxxxxxxxxxxxxxxxxxxx  # スペースキー（URLから確認）
```

> **スペースキーの確認方法**: ConfluenceのページURLに含まれる `spaces/【ここ】/pages/...` の部分がスペースキーです。個人スペースは `~` で始まります。

### sandbox 起動時

```bash
# Confluence 環境変数を読み込んでから起動
export $(cat .env.local | grep CONFLUENCE | xargs) && npx ampx sandbox
```

### 本番デプロイ時

Amplify コンソール → 環境変数 に以下を設定：
- `CONFLUENCE_URL`
- `CONFLUENCE_EMAIL`
- `CONFLUENCE_API_TOKEN`
- `CONFLUENCE_DEFAULT_SPACE_KEY`

---

# 2. AWS 側（Qiita ベース）準備

## 2-1. ベースを用意

あなたの Qiita ベースを流用（この URL の手順/構成を前提）：

```text
https://qiita.com/minorun365/items/0b4a980f2f4bb073a9e0
```

### このベースに最初からあるもの（重要）

* Amplify Gen2 + Cognito（メール認証）
* AgentCore Runtime（JWT authorizer が Cognito）
* Strands Agent（`agent.stream_async()` を SSE でフロントへ）

---

# 3. 実装（フロント）：MSAL 追加 & Graph Token を payload に載せる

## 3-1. あなたがやる（環境変数を入れる）

### ローカル用（Vite）

`/.env.local` を作成：

```bash
VITE_MS_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
# Personal だけなら consumers、両対応なら common
VITE_MS_AUTHORITY=https://login.microsoftonline.com/common
```

> URL を consumers にする場合：

```text
https://login.microsoftonline.com/consumers
```

### Amplify 本番用

Amplify コンソールの **Environment variables（ビルド時）** に同じ `VITE_MS_CLIENT_ID` / `VITE_MS_AUTHORITY` を設定して再デプロイ。

---

## 3-2. Claude Code にやらせる（フロント修正）

### 依存追加

```bash
npm i @azure/msal-browser
```

### `src/msal.ts` を新規作成（MSAL 初期化を確実に）

```ts
import {
  PublicClientApplication,
  type Configuration,
  type AccountInfo,
} from "@azure/msal-browser";

const clientId = import.meta.env.VITE_MS_CLIENT_ID as string | undefined;
if (!clientId) {
  throw new Error("VITE_MS_CLIENT_ID is not set");
}

const authority =
  (import.meta.env.VITE_MS_AUTHORITY as string | undefined) ??
  "https://login.microsoftonline.com/common";

const msalConfig: Configuration = {
  auth: {
    clientId,
    authority,
    redirectUri: window.location.origin, // ← Entra 側の Redirect URI と一致させる
  },
  cache: {
    // PoC: sessionStorage 推奨（localStorage より “残りにくい”）
    cacheLocation: "sessionStorage",
  },
};

export const msalInstance = new PublicClientApplication(msalConfig);

let initialized = false;
export async function ensureMsalInitialized() {
  if (!initialized) {
    await msalInstance.initialize();
    initialized = true;
  }
}

export const graphScopes = ["User.Read", "Calendars.ReadWrite", "offline_access"];

export function pickAccount(): AccountInfo | null {
  const accounts = msalInstance.getAllAccounts();
  return accounts.length > 0 ? accounts[0] : null;
}
```

### `src/App.tsx` の変更方針

* 既存の Cognito ログインはそのまま
* 画面上部に **Connect Outlook** ボタンを追加
* `handleSubmit` の payload に以下を追加

  * `msGraphAccessToken`
  * `userTimeZone`（例：`Asia/Tokyo`）
  * `clientNowIso`（例：`new Date().toISOString()`）

#### 追加する状態（例）

* `msGraphAccessToken: string | null`
* `msConnected: boolean`（UI表示用）

#### Token の取り方（例）

* `loginPopup` で初回連携
* 送信直前は `acquireTokenSilent` → ダメなら `acquireTokenPopup`

---

# 4. 実装（バック）：Strands Tool で Microsoft Graph を叩く

## 4-1. Claude Code にやらせる（バック修正）

### `amplify/agent/requirements.txt` に追加

```txt
httpx
```

### `amplify/agent/app.py` の変更方針

* payload から `msGraphAccessToken`, `userTimeZone`, `clientNowIso` を受ける
* `msGraphAccessToken` が無ければ「Connect Outlook を促す」だけ返す
* Graph 操作は Strands tools（Python 関数）に閉じ込める

  * 重要：**トークンを tool の引数にしない**（LLM に見せない）
  * closure / ローカル変数として保持して、http header にだけ付ける

### 用意するツール（最小）

* `get_schedule(start_iso, end_iso)`
  → `GET /me/calendarView`
* `create_meeting(subject, start_iso, end_iso, attendees, body="")`
  → `POST /me/events`

> PoC では「参加者は email を要求する」で割り切ると実装が一気に簡単です（名前解決・連絡先検索を後回しにできる）。

---

## 4-2. Graph API の呼び方（実装メモ）

### 予定参照（calendarView）

* Base：

```text
https://graph.microsoft.com/v1.0
```

* Endpoint：

```text
GET /me/calendarView?startDateTime=...&endDateTime=...
```

実務ポイント：

* `Prefer: outlook.timezone="Asia/Tokyo"` を付けると見やすい
* `startDateTime/endDateTime` にタイムゾーン無しを渡すと UTC 解釈されやすい
  → PoC は ISO に `+09:00` を付けるか、UTC（Z）に揃えるのが安全

### 会議作成（イベント作成）

```text
POST /me/events
```

* attendees を含めると Outlook 側で招待メールが飛ぶ挙動になりがち（抑制しにくい）
  → PoC は「招待が飛ぶ」前提でOK

---

# 5. ローカル開発（Amplify Sandbox 推奨）

## あなたがやる

```bash
npm install
npx ampx sandbox
# 別ターミナルで
npm run dev
```

* `amplify_outputs.json` が生成される
* フロントはそれを読んで AgentCore Runtime ARN を参照する

---

# 6. デプロイ（Amplify Hosting）

## あなたがやる

* [ ] CDK bootstrap（未実施の場合）

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
npx cdk bootstrap aws://$ACCOUNT_ID/ap-northeast-1
```

* [ ] GitHub に push（Amplify が自動デプロイ）
* [ ] Amplify コンソールに `VITE_MS_CLIENT_ID` / `VITE_MS_AUTHORITY` を設定
* [ ] 再デプロイ

---

# 7. 動作確認シナリオ（コピペ用）

## 7-1. Outlook 連携（初回）

1. Web アプリに Cognito ログイン
2. 「Connect Outlook」→ Microsoft ログイン
3. 成功表示になったらチャットへ

## 7-2. 予定参照（例）

* 「今日の予定を 9:00〜18:00 で一覧にして」
* 「明日の午前の予定ある？」

## 7-3. 会議作成（例）

* 「2026-01-15 10:00〜10:30 で "定例" を入れて。参加者は [a@example.com](mailto:a@example.com) と [b@example.com](mailto:b@example.com)」

> PoC の成功判定：Outlook カレンダーにイベントが作成され、参加者に招待が飛ぶ（またはカレンダーに反映される）

## 7-4. Confluence 操作（例）

* 「Confluenceで "議事録" を検索して」
* 「ConfluenceのページID 123456789 を取得して」
* 「Confluenceの DEV スペースに "本日の議事録" というページを作成して」

---

# 8. よくある詰まりポイント（対処）

## AADSTS50011: redirect URI mismatch

* Entra の Redirect URI と、コードの `redirectUri` が一致していない
* 末尾 `/` の有無も一致させる

## Graph が 401 / 403

* `msGraphAccessToken` が期限切れ
  → `acquireTokenSilent` → `acquireTokenPopup` の順で再取得
* Permission が足りない
  → `Calendars.ReadWrite` が Delegated で入っているか確認

## 「Teams 会議リンク作って」が失敗する

* M365 Personal だとオンライン会議 API が使えない場合がある
  → Teams URL が必要なら “work/school テナント” に切り替える（Developer Program / Business）

---

# 9. セキュリティ注意（PoC でも最低限）

* Graph Access Token を localStorage に保存しない（PoC は sessionStorage 推奨）
* Agent 側で payload の全文をログ出力しない（トークンが漏れる）
* tool の引数に token を渡さない（LLM が見える場所に置かない）

---

# 10. 次のステップ（PoC → 準本番）

* Graph Token をフロントから渡すのをやめる

  * AgentCore Identity の **Outbound Auth / Credential Provider** で 3LO を扱う
* 予定の更新/キャンセル、参加者の応答取得
* work account で Teams オンライン会議（URL自動生成）
* AgentCore Memory を入れて「文脈記憶」対応
* 連絡先検索（People / Contacts API）で「名前指定 → email 解決」

---

# 付録：Claude Code に投げる指示テンプレ（そのまま貼る用）

## フロント

* `@azure/msal-browser` を追加
* `src/msal.ts` を新規作成（initialize を含める）
* `src/App.tsx` に Connect Outlook ボタン
* `handleSubmit` の payload に `msGraphAccessToken/userTimeZone/clientNowIso` を追加

## バック

* `amplify/agent/requirements.txt` に `httpx` を追加
* `amplify/agent/app.py` に Graph 用の Strands tools を追加
* token は closure で保持し、LLM に見せない
* token が無い場合は Connect を促す応答にする

---

必要なら、このガイドのまま **「実際に `src/App.tsx` と `amplify/agent/app.py` の“差分パッチ（diff）”」**も作って貼ります（Qiitaベースのコードにそのまま当てられる形）。
