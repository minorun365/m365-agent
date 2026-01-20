# =====================================
# インポート
# =====================================

from strands import Agent, tool
from strands.models import BedrockModel
import httpx
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from atlassian import Confluence

# =====================================
# 定数
# =====================================

# AgentCore Runtime 用の API サーバーを作成
app = BedrockAgentCoreApp()

# Microsoft Graph API のベース URL
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# セッションごとの Agent インスタンスをキャッシュ（会話履歴保持用）
# 同じ microVM 内で保持されるため、同じセッションIDなら履歴が継続する
_agent_cache: dict[str, Agent] = {}


# =====================================
# Graph API ツール
# =====================================

def create_graph_tools(access_token: str, user_timezone: str):
    """
    Graph API を呼ぶツールを生成する

    なぜ関数でラップするのか？
    → closure でトークンを保持することで、LLM にトークンを見せない
    → ツールの引数にトークンを入れると、プロンプトに含まれてしまう

    Args:
        access_token: Microsoft Graph API のアクセストークン
        user_timezone: ユーザーのタイムゾーン（例: Asia/Tokyo）

    Returns:
        Strands tools のリスト
    """

    # 曜日の日本語マッピング
    WEEKDAY_JP = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]

    # ---------------------------------
    # ツール0: 現在時刻と曜日の取得
    # ---------------------------------
    @tool
    def get_current_datetime() -> str:
        """
        現在の日時と曜日を取得します。
        セッションの最初に必ず呼び出してください。
        """
        tz = ZoneInfo(user_timezone)
        now = datetime.now(tz)
        weekday = WEEKDAY_JP[now.weekday()]
        return f"現在日時: {now.strftime('%Y年%m月%d日')}（{weekday}）{now.strftime('%H:%M:%S')} ({user_timezone})"

    # ---------------------------------
    # ツール1: 予定の取得
    # ---------------------------------
    @tool
    def get_schedule(start_iso: str, end_iso: str) -> str:
        """
        指定期間の予定一覧を取得します。
        start_iso: 開始日時（ISO8601形式、例: 2026-01-15T09:00:00+09:00）
        end_iso: 終了日時（ISO8601形式、例: 2026-01-15T18:00:00+09:00）
        """
        # Graph API: カレンダービューを取得
        # https://learn.microsoft.com/ja-jp/graph/api/calendar-list-calendarview
        url = f"{GRAPH_BASE}/me/calendarView"
        headers = {
            "Authorization": f"Bearer {access_token}",
            # タイムゾーンを指定して、その時間帯で日時を返してもらう
            "Prefer": f'outlook.timezone="{user_timezone}"',
        }
        params = {"startDateTime": start_iso, "endDateTime": end_iso}

        # HTTP GET リクエスト
        with httpx.Client() as client:
            res = client.get(url, headers=headers, params=params)
            if res.status_code != 200:
                return f"エラー: {res.status_code} - {res.text}"

            data = res.json()
            events = data.get("value", [])

            if not events:
                return "指定期間に予定はありません。"

            # 予定を整形して返す
            result = []
            for ev in events:
                start = ev.get("start", {}).get("dateTime", "")
                end = ev.get("end", {}).get("dateTime", "")
                subject = ev.get("subject", "(件名なし)")
                # 表示形式: "- 2026-01-15T09:00〜10:00 会議タイトル"
                result.append(f"- {start[:16]}〜{end[11:16]} {subject}")
            return "\n".join(result)

    # ---------------------------------
    # ツール2: 会議の作成
    # ---------------------------------
    @tool
    def create_meeting(
        subject: str,
        start_iso: str,
        end_iso: str,
        attendees: list[str],
        body: str = ""
    ) -> str:
        """
        Outlook カレンダーに会議を作成し、参加者に招待を送ります。
        subject: 会議のタイトル
        start_iso: 開始日時（ISO8601形式、例: 2026-01-15T10:00:00+09:00）
        end_iso: 終了日時（ISO8601形式、例: 2026-01-15T10:30:00+09:00）
        attendees: 参加者のメールアドレスのリスト（例: ["a@example.com", "b@example.com"]）
        body: 会議の説明（省略可）
        """
        # Graph API: イベントを作成
        # https://learn.microsoft.com/ja-jp/graph/api/calendar-post-events
        url = f"{GRAPH_BASE}/me/events"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # リクエストボディを構築
        event_body = {
            "subject": subject,
            "start": {"dateTime": start_iso, "timeZone": user_timezone},
            "end": {"dateTime": end_iso, "timeZone": user_timezone},
            # 参加者を「必須出席者」として追加
            "attendees": [
                {"emailAddress": {"address": email}, "type": "required"}
                for email in attendees
            ],
        }

        # 説明文があれば追加
        if body:
            event_body["body"] = {"contentType": "text", "content": body}

        # HTTP POST リクエスト
        with httpx.Client() as client:
            res = client.post(url, headers=headers, json=event_body)
            if res.status_code not in (200, 201):
                return f"エラー: {res.status_code} - {res.text}"

            created = res.json()
            return f"会議を作成しました: {created.get('subject')} ({created.get('webLink', '')})"

    # ツールのリストを返す
    return [get_current_datetime, get_schedule, create_meeting]


# =====================================
# Microsoft To Do API ツール
# =====================================

def create_todo_tools(access_token: str, user_timezone: str):
    """
    Microsoft To Do API を呼ぶツールを生成する

    Args:
        access_token: Microsoft Graph API のアクセストークン
        user_timezone: ユーザーのタイムゾーン（例: Asia/Tokyo）

    Returns:
        Strands tools のリスト
    """

    # ---------------------------------
    # ツール1: タスクリスト一覧取得
    # ---------------------------------
    @tool
    def get_task_lists() -> str:
        """
        Microsoft To Do のタスクリスト一覧を取得します。
        タスクを操作する前に、まずこのツールでリストIDを確認してください。
        """
        url = f"{GRAPH_BASE}/me/todo/lists"
        headers = {"Authorization": f"Bearer {access_token}"}

        with httpx.Client() as client:
            res = client.get(url, headers=headers)
            if res.status_code != 200:
                return f"エラー: {res.status_code} - {res.text}"

            data = res.json()
            lists = data.get("value", [])

            if not lists:
                return "タスクリストがありません。"

            result = []
            for lst in lists:
                display_name = lst.get("displayName", "(名前なし)")
                list_id = lst.get("id", "")
                # デフォルトリストかどうかを表示
                wellknown = lst.get("wellknownListName", "")
                default_mark = " [デフォルト]" if wellknown == "defaultList" else ""
                result.append(f"- {display_name}{default_mark} (ID: {list_id})")
            return "タスクリスト一覧:\n" + "\n".join(result)

    # ---------------------------------
    # ツール2: タスク一覧取得
    # ---------------------------------
    @tool
    def get_tasks(list_id: str, include_completed: bool = False) -> str:
        """
        指定したタスクリスト内のタスク一覧を取得します。
        list_id: タスクリストID（get_task_lists で取得）
        include_completed: 完了済みタスクも含めるか（デフォルト: False）
        """
        url = f"{GRAPH_BASE}/me/todo/lists/{list_id}/tasks"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {}

        # 未完了のみ取得する場合はフィルタを追加
        if not include_completed:
            params["$filter"] = "status ne 'completed'"

        with httpx.Client() as client:
            res = client.get(url, headers=headers, params=params)
            if res.status_code != 200:
                return f"エラー: {res.status_code} - {res.text}"

            data = res.json()
            tasks = data.get("value", [])

            if not tasks:
                return "タスクがありません。"

            # 重要度の日本語マッピング
            importance_jp = {"low": "低", "normal": "通常", "high": "高"}

            result = []
            for task in tasks:
                title = task.get("title", "(タイトルなし)")
                task_id = task.get("id", "")
                status = task.get("status", "notStarted")
                importance = task.get("importance", "normal")
                importance_str = importance_jp.get(importance, importance)

                # 期限日時
                due = task.get("dueDateTime")
                due_str = ""
                if due:
                    due_dt = due.get("dateTime", "")[:10]  # YYYY-MM-DD 形式
                    due_str = f" 期限: {due_dt}"

                # ステータスアイコン
                status_icon = "✓" if status == "completed" else "○"

                result.append(f"{status_icon} {title} [重要度: {importance_str}]{due_str} (ID: {task_id})")
            return "タスク一覧:\n" + "\n".join(result)

    # ---------------------------------
    # ツール3: タスク作成
    # ---------------------------------
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
        due_date: 期限日時（ISO8601形式、例: 2026-01-20T17:00:00+09:00、省略可）
        importance: 重要度（low/normal/high、デフォルト: normal）
        body: 詳細説明（省略可）
        reminder_datetime: リマインダー日時（ISO8601形式、省略可）
        """
        url = f"{GRAPH_BASE}/me/todo/lists/{list_id}/tasks"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # リクエストボディを構築
        task_body = {
            "title": title,
            "importance": importance,
        }

        # 期限日時があれば追加
        if due_date:
            task_body["dueDateTime"] = {
                "dateTime": due_date,
                "timeZone": user_timezone,
            }

        # 詳細説明があれば追加
        if body:
            task_body["body"] = {
                "content": body,
                "contentType": "text",
            }

        # リマインダーがあれば追加
        if reminder_datetime:
            task_body["reminderDateTime"] = {
                "dateTime": reminder_datetime,
                "timeZone": user_timezone,
            }
            task_body["isReminderOn"] = True

        with httpx.Client() as client:
            res = client.post(url, headers=headers, json=task_body)
            if res.status_code not in (200, 201):
                return f"エラー: {res.status_code} - {res.text}"

            created = res.json()
            return f"タスクを作成しました: {created.get('title')} (ID: {created.get('id')})"

    # ---------------------------------
    # ツール4: タスク更新
    # ---------------------------------
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
        due_date: 新しい期限（ISO8601形式、省略時は変更なし）
        importance: 新しい重要度（low/normal/high、省略時は変更なし）
        body: 新しい詳細説明（省略時は変更なし）
        """
        url = f"{GRAPH_BASE}/me/todo/lists/{list_id}/tasks/{task_id}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # 変更するフィールドのみを含むボディを構築
        task_body = {}
        if title is not None:
            task_body["title"] = title
        if importance is not None:
            task_body["importance"] = importance
        if due_date is not None:
            task_body["dueDateTime"] = {
                "dateTime": due_date,
                "timeZone": user_timezone,
            }
        if body is not None:
            task_body["body"] = {
                "content": body,
                "contentType": "text",
            }

        if not task_body:
            return "更新する項目が指定されていません。"

        with httpx.Client() as client:
            res = client.patch(url, headers=headers, json=task_body)
            if res.status_code != 200:
                return f"エラー: {res.status_code} - {res.text}"

            updated = res.json()
            return f"タスクを更新しました: {updated.get('title')}"

    # ---------------------------------
    # ツール5: タスク完了
    # ---------------------------------
    @tool
    def complete_task(list_id: str, task_id: str) -> str:
        """
        タスクを完了状態にします。
        list_id: タスクリストID
        task_id: タスクID（get_tasks で取得）
        """
        url = f"{GRAPH_BASE}/me/todo/lists/{list_id}/tasks/{task_id}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        task_body = {"status": "completed"}

        with httpx.Client() as client:
            res = client.patch(url, headers=headers, json=task_body)
            if res.status_code != 200:
                return f"エラー: {res.status_code} - {res.text}"

            updated = res.json()
            return f"タスクを完了にしました: {updated.get('title')}"

    # ツールのリストを返す
    return [get_task_lists, get_tasks, create_task, update_task, complete_task]


# =====================================
# Confluence API ツール
# =====================================

def create_confluence_tools():
    """
    Confluence API を呼ぶツールを生成する

    環境変数から認証情報を取得し、クロージャで隠蔽する
    環境変数が未設定の場合は空リストを返す（Confluence機能は無効化）

    環境変数:
        CONFLUENCE_URL: Confluence Cloud の URL（例: https://your-domain.atlassian.net）
        CONFLUENCE_EMAIL: 認証用メールアドレス
        CONFLUENCE_API_TOKEN: API トークン

    Returns:
        Strands tools のリスト（環境変数未設定時は空リスト）
    """

    # 環境変数から認証情報を取得
    confluence_url = os.environ.get("CONFLUENCE_URL")
    confluence_email = os.environ.get("CONFLUENCE_EMAIL")
    confluence_token = os.environ.get("CONFLUENCE_API_TOKEN")
    default_space_key = os.environ.get("CONFLUENCE_DEFAULT_SPACE_KEY")

    # 環境変数が設定されていない場合は空リストを返す
    if not all([confluence_url, confluence_email, confluence_token]):
        print("[Confluence] 環境変数が未設定のため、Confluence機能は無効です")
        return []

    # Confluence クライアントを作成
    confluence = Confluence(
        url=confluence_url,
        username=confluence_email,
        password=confluence_token,
        cloud=True
    )
    print(f"[Confluence] 接続先: {confluence_url}")
    if default_space_key:
        print(f"[Confluence] デフォルトスペースキー: {default_space_key}")

    # ---------------------------------
    # ツール1: ページ取得
    # ---------------------------------
    @tool
    def get_confluence_page(page_id: str) -> str:
        """
        Confluenceページの内容を取得します。
        page_id: ページID（URLの末尾の数字、例: 123456789）
        """
        try:
            page = confluence.get_page_by_id(
                page_id,
                expand="body.storage,version"
            )
            title = page.get("title", "(タイトルなし)")
            body = page.get("body", {}).get("storage", {}).get("value", "")
            version = page.get("version", {}).get("number", "?")
            return f"# {title}\n\nバージョン: {version}\n\n{body}"
        except Exception as e:
            return f"エラー: ページの取得に失敗しました - {str(e)}"

    # ---------------------------------
    # ツール2: 検索
    # ---------------------------------
    @tool
    def search_confluence(query: str, space_key: str = None, limit: int = 10) -> str:
        """
        Confluenceでコンテンツを検索します。
        query: 検索キーワード
        space_key: スペースキーで絞り込み（省略時は全スペース）
        limit: 取得件数（デフォルト10件）
        """
        try:
            # CQL クエリを構築
            cql = f'text ~ "{query}"'
            if space_key:
                cql += f' AND space = "{space_key}"'

            results = confluence.cql(cql, limit=limit)
            items = results.get("results", [])

            if not items:
                return "検索結果が見つかりませんでした。"

            # 結果を整形
            output = []
            for item in items:
                content = item.get("content", {})
                title = content.get("title", "(タイトルなし)")
                page_id = content.get("id", "")
                space = item.get("resultGlobalContainer", {}).get("title", "")
                output.append(f"- [{title}] (ID: {page_id}, スペース: {space})")

            return f"検索結果 ({len(items)}件):\n" + "\n".join(output)
        except Exception as e:
            return f"エラー: 検索に失敗しました - {str(e)}"

    # ---------------------------------
    # ツール3: ページ作成
    # ---------------------------------
    @tool
    def create_confluence_page(
        title: str,
        body: str,
        space_key: str = None,
        parent_id: str = None
    ) -> str:
        """
        Confluenceに新しいページを作成します。
        title: ページタイトル
        body: ページ本文（HTML形式）
        space_key: スペースキー（省略時はデフォルトスペースを使用）
        parent_id: 親ページID（省略時はスペースのトップレベル）
        """
        # スペースキーが指定されていない場合はデフォルトを使用
        target_space = space_key or default_space_key
        if not target_space:
            return "エラー: スペースキーが指定されておらず、デフォルトスペースキーも設定されていません"

        try:
            page = confluence.create_page(
                space=target_space,
                title=title,
                body=body,
                parent_id=parent_id
            )
            page_id = page.get("id", "")
            page_url = f"{confluence_url}/wiki/spaces/{target_space}/pages/{page_id}"
            return f"ページを作成しました: {title} (ID: {page_id})\nURL: {page_url}"
        except Exception as e:
            return f"エラー: ページの作成に失敗しました - {str(e)}"

    # ---------------------------------
    # ツール4: ページ更新
    # ---------------------------------
    @tool
    def update_confluence_page(page_id: str, title: str, body: str) -> str:
        """
        既存のConfluenceページを更新します。
        page_id: ページID
        title: 新しいタイトル
        body: 新しい本文（HTML形式）
        """
        try:
            page = confluence.update_page(
                page_id=page_id,
                title=title,
                body=body
            )
            version = page.get("version", {}).get("number", "?")
            return f"ページを更新しました: {title} (Version: {version})"
        except Exception as e:
            return f"エラー: ページの更新に失敗しました - {str(e)}"

    # ツールのリストを返す
    return [get_confluence_page, search_confluence, create_confluence_page, update_confluence_page]


# =====================================
# ストリーミングイベント変換
# =====================================

def convert_event(event) -> dict | None:
    """
    Strands のイベントをフロントエンド向け JSON 形式に変換

    Strands Agent は様々なイベントを発行するが、
    フロントエンドで必要なのは:
    - text: AI の応答テキスト（差分）
    - tool_use: ツール使用開始の通知

    Args:
        event: Strands からのイベント（dict 形式）

    Returns:
        フロント向け JSON または None（無視するイベント）
    """
    try:
        if not hasattr(event, 'get'):
            return None

        inner_event = event.get('event')
        if not inner_event:
            return None

        # ---------------------------------
        # テキスト差分を検知
        # contentBlockDelta.delta.text に文字列が入っている
        # ---------------------------------
        content_block_delta = inner_event.get('contentBlockDelta')
        if content_block_delta:
            delta = content_block_delta.get('delta', {})
            text = delta.get('text')
            if text:
                return {'type': 'text', 'data': text}

        # ---------------------------------
        # ツール使用開始を検知
        # contentBlockStart.start.toolUse に情報が入っている
        # ---------------------------------
        content_block_start = inner_event.get('contentBlockStart')
        if content_block_start:
            start = content_block_start.get('start', {})
            tool_use = start.get('toolUse')
            if tool_use:
                tool_name = tool_use.get('name', 'unknown')
                return {'type': 'tool_use', 'tool_name': tool_name}

        return None
    except Exception:
        return None


# =====================================
# メインエントリーポイント
# =====================================

@app.entrypoint
async def invoke_agent(payload, context):
    """
    AgentCore Runtime のエントリーポイント

    フロントエンドからのリクエストを受け取り、
    AI エージェントを実行してストリーミングで応答を返す

    Args:
        payload: フロントエンドからの JSON
            - prompt: ユーザーの入力テキスト
            - msGraphAccessToken: Graph API のアクセストークン
            - userTimeZone: ユーザーのタイムゾーン
            - clientNowIso: クライアント側の現在時刻
        context: AgentCore からのコンテキスト情報（session_id など）

    Yields:
        SSE 形式のイベント（text または tool_use）
    """

    # ---------------------------------
    # セッションID確認（デバッグ用）
    # 同じセッションIDで呼ばれると、同じmicroVMを再利用して会話履歴が保持される
    # ---------------------------------
    session_id = context.session_id if context else None
    print(f"[Session] ID: {session_id}")

    # ---------------------------------
    # リクエストからパラメータを取得
    # ---------------------------------
    prompt = payload.get("prompt")
    ms_graph_token = payload.get("msGraphAccessToken")
    user_timezone = payload.get("userTimeZone", "Asia/Tokyo")
    client_now_iso = payload.get("clientNowIso", "")

    # ---------------------------------
    # トークンチェック
    # ---------------------------------
    if not ms_graph_token:
        yield {
            'type': 'text',
            'data': 'Outlook に連携されていません。画面右上の「Entra IDに接続」ボタンをクリックして連携してください。'
        }
        return

    # ---------------------------------
    # ツールを生成
    # ---------------------------------
    # closure でトークンを保持し、LLM には見せない
    graph_tools = create_graph_tools(ms_graph_token, user_timezone)
    todo_tools = create_todo_tools(ms_graph_token, user_timezone)
    confluence_tools = create_confluence_tools()

    # 全ツールを結合
    all_tools = graph_tools + todo_tools + confluence_tools

    # ---------------------------------
    # システムプロンプト
    # ---------------------------------
    # AI の役割と、利用可能な機能を定義
    system_prompt = f"""
あなたは秘書AIエージェントです。
ユーザーの Outlook カレンダー、Microsoft To Do、Confluence を操作できます。

# タイムゾーン
{user_timezone}

# 注意事項
- 日時は必ず ISO8601 形式（例: 2026-01-15T10:00:00+09:00）で指定してください
- 「今日」「明日」「今週」などの相対表現を使う場合は、必ず get_current_datetime ツールで現在日時を確認してから処理してください
- 曜日を計算で求めず、必ず get_current_datetime ツールで確認してください
- To Do のタスク操作には必ず list_id が必要です。まず get_task_lists でリストIDを取得してください
"""

    # ---------------------------------
    # AI エージェントを取得または作成
    # ---------------------------------
    # セッションIDでキャッシュを参照し、同じセッションなら既存のAgentを再利用
    # これにより会話履歴（agent.messages）が保持される
    global _agent_cache

    if session_id and session_id in _agent_cache:
        # 既存のAgentを再利用（会話履歴が保持されている）
        agent = _agent_cache[session_id]
        # ツールを更新（トークンが変わる可能性があるため）
        agent.tools = all_tools
        print(f"[Session] Reusing existing agent for session: {session_id}")
    else:
        # 新しいAgentを作成
        # Bedrock の Claude モデルを使用
        # 重要: リージョンを明示的に指定（デフォルトだと ap-northeast-1 になる）
        bedrock_model = BedrockModel(
            # model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            region_name="us-east-1"
        )
        agent = Agent(
            model=bedrock_model,
            system_prompt=system_prompt,
            tools=all_tools
        )
        # キャッシュに保存
        if session_id:
            _agent_cache[session_id] = agent
            print(f"[Session] Created new agent for session: {session_id}")

    # ---------------------------------
    # ストリーミング実行
    # ---------------------------------
    # async generator でイベントを逐次返す
    async for event in agent.stream_async(prompt):
        converted = convert_event(event)
        if converted:
            yield converted


# =====================================
# ローカル実行用
# =====================================

if __name__ == "__main__":
    # ローカルで実行する場合（デバッグ用）
    # 通常は AgentCore Runtime がこのファイルをロードする
    app.run()