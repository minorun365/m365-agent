# Microsoft To Do ツール実装

> **ステータス: 実装完了** (2026-01-16)

## 1. 概要

Strands エージェントに Microsoft Graph To Do API を使ったタスク管理機能を追加。
既存の Outlook カレンダーツールと同様のクロージャパターンで実装。

## 2. 技術調査結果

### 2.1 Microsoft Graph To Do API

| 項目 | 内容 |
|------|------|
| API バージョン | v1.0 |
| ベース URL | `https://graph.microsoft.com/v1.0` |
| 認証 | OAuth 2.0（委任されたアクセス許可） |
| 必要スコープ | `Tasks.ReadWrite` |

### 2.2 主要リソース

| リソース | 説明 | エンドポイント |
|---------|------|---------------|
| todoTaskList | タスクリスト（コンテナ） | `/me/todo/lists` |
| todoTask | 個別のタスク | `/me/todo/lists/{listId}/tasks` |
| checklistItem | サブタスク | `/me/todo/lists/{listId}/tasks/{taskId}/checklistItems` |

### 2.3 タスクのステータス

| status 値 | 説明 |
|-----------|------|
| notStarted | 未着手 |
| inProgress | 進行中 |
| completed | 完了 |
| waitingOnOthers | 他者待ち |
| deferred | 延期 |

### 2.4 タスクの重要度

| importance 値 | 説明 |
|---------------|------|
| low | 低 |
| normal | 通常 |
| high | 高 |

## 3. 実装するツール

### 3.1 ツール一覧（5つ）

| ツール名 | 機能 | HTTPメソッド | エンドポイント |
|---------|------|-------------|---------------|
| `get_task_lists` | タスクリスト一覧取得 | GET | `/me/todo/lists` |
| `get_tasks` | タスク一覧取得 | GET | `/me/todo/lists/{listId}/tasks` |
| `create_task` | タスク作成 | POST | `/me/todo/lists/{listId}/tasks` |
| `update_task` | タスク更新 | PATCH | `/me/todo/lists/{listId}/tasks/{taskId}` |
| `complete_task` | タスク完了 | PATCH | `/me/todo/lists/{listId}/tasks/{taskId}` |

### 3.2 各ツールの詳細設計

#### get_task_lists
```python
@tool
def get_task_lists() -> str:
    """
    Microsoft To Do のタスクリスト一覧を取得します。
    タスクを操作する前に、まずこのツールでリストIDを確認してください。
    """
    # GET /me/todo/lists
    # 戻り値例: "- タスク (ID: AQMkADA...)\n- 買い物リスト (ID: AQMkADB...)"
```

#### get_tasks
```python
@tool
def get_tasks(list_id: str, include_completed: bool = False) -> str:
    """
    指定したタスクリスト内のタスク一覧を取得します。
    list_id: タスクリストID（get_task_lists で取得）
    include_completed: 完了済みタスクも含めるか（デフォルト: False）
    """
    # GET /me/todo/lists/{list_id}/tasks
    # $filter=status ne 'completed' で未完了のみ取得可能
```

#### create_task
```python
@tool
def create_task(
    list_id: str,
    title: str,
    due_date: str = None,
    importance: str = "normal",
    body: str = "",
    reminder_datetime: str = None
) -> str:
    """
    新しいタスクを作成します。
    list_id: タスクリストID（get_task_lists で取得）
    title: タスクのタイトル（必須）
    due_date: 期限日時（ISO8601形式、例: 2026-01-20T17:00:00+09:00）
    importance: 重要度（low/normal/high、デフォルト: normal）
    body: 詳細説明（省略可）
    reminder_datetime: リマインダー日時（ISO8601形式、省略可）
    """
    # POST /me/todo/lists/{list_id}/tasks
```

#### update_task
```python
@tool
def update_task(
    list_id: str,
    task_id: str,
    title: str = None,
    due_date: str = None,
    importance: str = None,
    body: str = None
) -> str:
    """
    既存のタスクを更新します。
    list_id: タスクリストID
    task_id: タスクID（get_tasks で取得）
    title: 新しいタイトル（省略時は変更なし）
    due_date: 新しい期限（省略時は変更なし）
    importance: 新しい重要度（省略時は変更なし）
    body: 新しい詳細説明（省略時は変更なし）
    """
    # PATCH /me/todo/lists/{list_id}/tasks/{task_id}
```

#### complete_task
```python
@tool
def complete_task(list_id: str, task_id: str) -> str:
    """
    タスクを完了状態にします。
    list_id: タスクリストID
    task_id: タスクID（get_tasks で取得）
    """
    # PATCH /me/todo/lists/{list_id}/tasks/{task_id}
    # body: {"status": "completed"}
```

## 4. データ構造

### 4.1 タスクリスト（todoTaskList）
```json
{
  "id": "AQMkADAwATM0MDAAMS0yMDkyLWVjMzYtM...",
  "displayName": "タスク",
  "isOwner": true,
  "isShared": false,
  "wellknownListName": "defaultList"
}
```

### 4.2 タスク（todoTask）
```json
{
  "id": "AlMKXwbQAAAJws6wcAAAA=",
  "title": "レポートを書く",
  "status": "notStarted",
  "importance": "high",
  "isReminderOn": true,
  "createdDateTime": "2026-01-15T09:00:00Z",
  "lastModifiedDateTime": "2026-01-15T10:30:00Z",
  "dueDateTime": {
    "dateTime": "2026-01-20T17:00:00.0000000",
    "timeZone": "Asia/Tokyo"
  },
  "reminderDateTime": {
    "dateTime": "2026-01-20T09:00:00.0000000",
    "timeZone": "Asia/Tokyo"
  },
  "body": {
    "content": "詳細メモをここに記載",
    "contentType": "text"
  }
}
```

## 5. 必要な変更箇所

### 5.1 Azure Portal（Entra ID アプリ設定）

1. Azure Portal → アプリの登録 → 該当アプリを選択
2. 「API のアクセス許可」→「アクセス許可の追加」
3. Microsoft Graph → 委任されたアクセス許可
4. `Tasks.ReadWrite` を追加
5. 「管理者の同意を与える」（テナント管理者の場合）

### 5.2 フロントエンド（src/msal.ts）

```diff
- export const graphScopes = ["User.Read", "Calendars.ReadWrite", "offline_access"];
+ export const graphScopes = ["User.Read", "Calendars.ReadWrite", "Tasks.ReadWrite", "offline_access"];
```

### 5.3 バックエンド（amplify/agent/app.py）

1. `create_todo_tools()` 関数を追加
2. `invoke_agent()` で `todo_tools` を `all_tools` に結合
3. システムプロンプトに To Do 機能の説明を追加

## 6. 実装ステップ

| # | タスク | ファイル | 状態 |
|---|--------|---------|------|
| 1 | Entra ID アプリに Tasks.ReadWrite スコープを追加 | Azure Portal | [x] |
| 2 | フロントエンドにスコープを追加 | src/msal.ts | [x] |
| 3 | create_todo_tools() 関数を実装 | amplify/agent/app.py | [x] |
| 4 | システムプロンプトを更新 | amplify/agent/app.py | [x] |
| 5 | ローカルテスト | - | [x] |
| 6 | Amplify 環境変数設定 | AWS CLI | [x] |
| 7 | デプロイ | git push → Amplify | [x] |

## 7. 秘書AIとの会話例

### 例1: タスク一覧の確認
```
ユーザー: 「今日やることを教えて」
秘書AI: [get_task_lists → get_tasks を呼び出し]
        「本日期限のタスクは2件あります：
         1. レポート提出（重要度: 高）
         2. メール返信」
```

### 例2: タスクの追加
```
ユーザー: 「来週金曜までに企画書を書くタスクを追加して」
秘書AI: [get_task_lists → create_task を呼び出し]
        「タスク『企画書を書く』を期限1/24（金）で作成しました。」
```

### 例3: タスクの完了
```
ユーザー: 「レポート提出のタスクを完了にして」
秘書AI: [get_task_lists → get_tasks → complete_task を呼び出し]
        「タスク『レポート提出』を完了にしました。」
```

## 8. 参考リンク

- [Microsoft To Do API Overview](https://learn.microsoft.com/en-us/graph/api/resources/todo-overview?view=graph-rest-1.0)
- [Create todoTask API](https://learn.microsoft.com/en-us/graph/api/todotasklist-post-tasks?view=graph-rest-1.0)
- [To Do API Concept](https://learn.microsoft.com/en-us/graph/todo-concept-overview)
- [Strands Agents Documentation](https://strandsagents.com/latest/)

## 9. 注意事項

- タスク操作には必ず `list_id` が必要
- デフォルトのタスクリストは `wellknownListName: "defaultList"` で識別可能
- 日時は ISO8601 形式で、タイムゾーンを明示的に指定すること
- アプリケーション権限（daemon アプリ）はサポートされていない（委任権限のみ）
