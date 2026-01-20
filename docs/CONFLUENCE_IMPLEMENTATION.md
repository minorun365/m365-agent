# Confluence機能 実装ガイド

**ステータス: ✅ 実装完了（2026-01-15）**

## 概要

Outlook AgentアプリにConfluence Cloud APIを使った読み書き機能を追加する。

## 技術選定

| 項目 | 選定 |
|------|------|
| APIクライアント | `atlassian-python-api` ライブラリ |
| 認証方式 | Basic認証（email + API Token） |
| 認証情報管理 | 環境変数 |

## 環境変数

```bash
CONFLUENCE_URL=https://your-domain.atlassian.net
CONFLUENCE_EMAIL=user@example.com
CONFLUENCE_API_TOKEN=xxxxxxxxxx
CONFLUENCE_DEFAULT_SPACE_KEY=~xxxxxxxxxxxxxxxxxxxxxxxx  # ページ作成時のデフォルトスペース
```

### ローカル開発時（sandbox）

`.env.local` に設定し、sandbox起動時に環境変数を読み込む：

```bash
# .env.local を読み込んでからsandbox起動
export $(cat .env.local | grep CONFLUENCE | xargs) && npx ampx sandbox
```

### 本番デプロイ時（Amplify Hosting）

Amplify コンソールで環境変数を設定：
1. Amplify コンソール → アプリ → 環境変数
2. 以下を追加：
   - `CONFLUENCE_URL`
   - `CONFLUENCE_EMAIL`
   - `CONFLUENCE_API_TOKEN`
   - `CONFLUENCE_DEFAULT_SPACE_KEY`

CDK（`resource.ts`）が `process.env` から自動的に読み込み、AgentCore Runtimeに渡します。

## 実装するツール

| ツール名 | 機能 | 主要パラメータ |
|----------|------|----------------|
| `get_confluence_page` | ページ内容を取得 | `page_id` |
| `search_confluence` | コンテンツを検索 | `query`, `space_key`(任意), `limit`(任意) |
| `create_confluence_page` | 新規ページ作成 | `space_key`, `title`, `body`, `parent_id`(任意) |
| `update_confluence_page` | ページ更新 | `page_id`, `title`, `body` |

## 実装タスク

- [x] `requirements.txt` に `atlassian-python-api` を追加
- [x] `create_confluence_tools()` 関数を実装
- [x] `invoke_agent()` にConfluenceツールを統合
- [x] システムプロンプトを更新
- [x] `resource.ts` にCDK環境変数設定を追加

## コード設計

### create_confluence_tools() 関数

```python
import os
from atlassian import Confluence

def create_confluence_tools():
    """
    Confluence APIツールを生成
    環境変数から認証情報を取得し、クロージャで隠蔽
    """
    confluence_url = os.environ.get("CONFLUENCE_URL")
    confluence_email = os.environ.get("CONFLUENCE_EMAIL")
    confluence_token = os.environ.get("CONFLUENCE_API_TOKEN")

    # 環境変数が設定されていない場合は空リストを返す
    if not all([confluence_url, confluence_email, confluence_token]):
        return []

    confluence = Confluence(
        url=confluence_url,
        username=confluence_email,
        password=confluence_token,
        cloud=True
    )

    @tool
    def get_confluence_page(page_id: str) -> str:
        """Confluenceページの内容を取得します"""
        ...

    @tool
    def search_confluence(query: str, space_key: str = None, limit: int = 10) -> str:
        """Confluenceでコンテンツを検索します"""
        ...

    @tool
    def create_confluence_page(space_key: str, title: str, body: str, parent_id: str = None) -> str:
        """Confluenceに新しいページを作成します"""
        ...

    @tool
    def update_confluence_page(page_id: str, title: str, body: str) -> str:
        """既存のConfluenceページを更新します"""
        ...

    return [get_confluence_page, search_confluence, create_confluence_page, update_confluence_page]
```

### invoke_agent() での統合

```python
# Graph ツールを生成
graph_tools = create_graph_tools(ms_graph_token, user_timezone)

# Confluence ツールを生成（環境変数から認証情報取得）
confluence_tools = create_confluence_tools()

# 全ツールを結合
all_tools = graph_tools + confluence_tools
```

### システムプロンプト更新

```python
system_prompt = f"""あなたは秘書AIです。ユーザーの Outlook カレンダーとConfluenceを操作できます。

## 利用可能な機能

### Outlook
- 予定参照: 指定期間の予定一覧を取得
- 会議作成: 新しい会議を作成し、参加者に招待を送信
- 現在時刻取得: get_current_datetime ツールで正確な日時と曜日を確認

### Confluence
- ページ取得: get_confluence_page でページ内容を取得（page_id必須）
- 検索: search_confluence でコンテンツを検索
- ページ作成: create_confluence_page で新規ページを作成
- ページ更新: update_confluence_page で既存ページを更新

## タイムゾーン
{user_timezone}

## 注意事項
- 日時は必ず ISO8601 形式で指定
- 「今日」「明日」などの相対表現は get_current_datetime で確認してから処理
- Confluenceのページ本文はHTML形式で記述
"""
```

## 重要な注意事項

### APIキーとAPIトークンの違い

Atlassian には2種類の認証情報があるので注意：

| 種類 | 用途 | 発行場所 |
|------|------|----------|
| **API Key** | 組織管理用（管理者向け） | admin.atlassian.com |
| **API Token** | REST API認証用（ユーザー向け） | id.atlassian.com/manage-profile/security/api-tokens |

**Confluence REST APIには「API Token」を使用する。**

APIキーを使うと以下のエラーが発生する：
```
Current user not permitted to use Confluence
```

### APIトークンの発行手順

1. https://id.atlassian.com/manage-profile/security/api-tokens にアクセス
2. 「Create API token」をクリック
3. ラベルを入力（例: `outlook-agent`）
4. 生成されたトークンをコピーして環境変数に設定

## 参考リンク

- [atlassian-python-api Documentation](https://atlassian-python-api.readthedocs.io/confluence.html)
- [Confluence Cloud REST API v2](https://developer.atlassian.com/cloud/confluence/rest/v2/)
- [Strands Agents - Creating Custom Tools](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/tools/custom-tools/index.md)
- [Atlassian API Token 発行ページ](https://id.atlassian.com/manage-profile/security/api-tokens)
