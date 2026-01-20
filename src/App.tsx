// 必要なパッケージをインポート
import { useState, useRef, useEffect, type FormEvent } from 'react';
import { fetchAuthSession } from 'aws-amplify/auth';
import ReactMarkdown from 'react-markdown';
import './App.css';
import outputs from '../amplify_outputs.json';
import { msalInstance, ensureMsalInitialized, graphScopes, pickAccount, logout } from './msal';

// Amplify outputs から設定を取得
const AGENT_ARN = outputs.custom?.agentRuntimeArn;

// チャットメッセージの型定義
interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  isToolUsing?: boolean;
  toolCompleted?: boolean;
  toolName?: string;
}

// メインのアプリケーションコンポーネント
function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // セッションID（画面リロードごとに新規生成 → 新しいセッションになる）
  const sessionIdRef = useRef<string>(crypto.randomUUID());

  // Microsoft Graph 用の状態
  const [msConnected, setMsConnected] = useState(false);
  const [msGraphAccessToken, setMsGraphAccessToken] = useState<string | null>(null);

  // MSAL 初期化（アプリ起動時に1回）
  useEffect(() => {
    ensureMsalInitialized().then(() => {
      // 既にログイン済みのアカウントがあればトークンを取得
      const account = pickAccount();
      if (account) {
        msalInstance.acquireTokenSilent({ scopes: graphScopes, account })
          .then(res => {
            setMsGraphAccessToken(res.accessToken);
            setMsConnected(true);
          })
          .catch(() => {
            // サイレント取得失敗（再ログインが必要）
            setMsConnected(false);
          });
      }
    });
  }, []);

  // Entra ID 接続/切断ボタンのハンドラ
  const handleToggleOutlook = async () => {
    await ensureMsalInitialized();
    if (msConnected) {
      // 接続済み → ログアウト
      try {
        await logout();
        setMsGraphAccessToken(null);
        setMsConnected(false);
      } catch (err) {
        console.error('Microsoft logout failed:', err);
      }
    } else {
      // 未接続 → ログイン
      try {
        const res = await msalInstance.loginPopup({ scopes: graphScopes });
        setMsGraphAccessToken(res.accessToken);
        setMsConnected(true);
      } catch (err) {
        console.error('Microsoft login failed:', err);
      }
    }
  };

  // メッセージ追加時に自動スクロール
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // フォーム送信処理
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim() || loading) return;

    // ユーザーメッセージを作成
    const userMessage: Message = { id: crypto.randomUUID(), role: 'user', content: input.trim() };

    // メッセージ配列に追加（ユーザー発言 + 空のAI応答）
    setMessages(prev => [...prev, userMessage, { id: crypto.randomUUID(), role: 'assistant', content: '' }]);
    setInput('');
    setLoading(true);

    // Cognito認証トークンを取得
    const session = await fetchAuthSession();
    const accessToken = session.tokens?.accessToken?.toString();

    // Graph トークンを最新化（送信直前に取得）
    let currentGraphToken = msGraphAccessToken;
    const account = pickAccount();
    if (account) {
      try {
        const tokenRes = await msalInstance.acquireTokenSilent({ scopes: graphScopes, account });
        currentGraphToken = tokenRes.accessToken;
        setMsGraphAccessToken(currentGraphToken);
      } catch {
        // サイレント取得失敗 → ポップアップで再取得
        try {
          const tokenRes = await msalInstance.acquireTokenPopup({ scopes: graphScopes });
          currentGraphToken = tokenRes.accessToken;
          setMsGraphAccessToken(currentGraphToken);
          setMsConnected(true);
        } catch (err) {
          console.error('Token acquisition failed:', err);
        }
      }
    }

    // AgentCore Runtime APIを呼び出し
    const url = `https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/${encodeURIComponent(AGENT_ARN)}/invocations?qualifier=DEFAULT`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
        // セッションIDを指定して会話履歴を保持（同じIDで同じmicroVMを再利用）
        'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id': sessionIdRef.current,
      },
      body: JSON.stringify({
        prompt: userMessage.content,
        msGraphAccessToken: currentGraphToken,
        userTimeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        clientNowIso: new Date().toLocaleString('sv-SE', { timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone }).replace(' ', 'T'),
      }),
    });

    // SSEストリーミングを処理
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let isInToolUse = false;
    let toolIdx = -1;

    // ストリームを読み続ける
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      // 受信データを行ごとに処理
      for (const line of decoder.decode(value, { stream: true }).split('\n')) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data === '[DONE]') continue;
        const event = JSON.parse(data);

        // ツール使用開始イベント
        if (event.type === 'tool_use') {
          isInToolUse = true;
          const savedBuffer = buffer;
          setMessages(prev => {
            const msgs = [...prev];
            if (savedBuffer) {
              msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], content: savedBuffer };
              toolIdx = msgs.length;
              msgs.push({ id: crypto.randomUUID(), role: 'assistant', content: '', isToolUsing: true, toolName: event.tool_name });
            } else {
              toolIdx = msgs.length - 1;
              msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], isToolUsing: true, toolName: event.tool_name };
            }
            return msgs;
          });
          buffer = '';
          continue;
        }

        // テキストイベント（AI応答本文）
        if (event.type === 'text' && event.data) {
          if (isInToolUse && !buffer) {
            // ツール実行後の最初のテキスト → ツールを完了状態に
            const savedIdx = toolIdx;
            setMessages(prev => {
              const msgs = [...prev];
              if (savedIdx >= 0 && savedIdx < msgs.length) msgs[savedIdx] = { ...msgs[savedIdx], toolCompleted: true };
              msgs.push({ id: crypto.randomUUID(), role: 'assistant', content: event.data });
              return msgs;
            });
            buffer = event.data;
            isInToolUse = false;
            toolIdx = -1;
          } else {
            // 通常のテキスト蓄積（ストリーミング表示）
            buffer += event.data;
            setMessages(prev => {
              const msgs = [...prev];
              msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], content: buffer, isToolUsing: false };
              return msgs;
            });
          }
        }
      }
    }
    setLoading(false);
  };

  // チャットUI（ヘッダー＋チャットエリア＋入力フォーム）
  return (
    <div className="container">
      <header className="header">
        <div className="header-left"></div>
        <div className="header-center">
          <h1 className="title">あなただけの秘書AIエージェント</h1>
          <p className="subtitle">AmplifyとAgentCoreでフルサーバーレス構築しています</p>
        </div>
        <div className="header-right">
          <button
            onClick={handleToggleOutlook}
            className={`outlook-button ${msConnected ? 'connected' : ''}`}
          >
            {msConnected ? '✓ Entra ID有効' : 'Entra IDに接続'}
          </button>
        </div>
      </header>

      <div className="message-area">
        <div className="message-container">
          {messages.map(msg => (
            <div key={msg.id} className={`message-row ${msg.role}`}>
              <div className={`bubble ${msg.role}`}>
                {msg.role === 'assistant' && !msg.content && !msg.isToolUsing && (
                  <span className="thinking">考え中…</span>
                )}
                {msg.isToolUsing && (
                  <span className={`tool-status ${msg.toolCompleted ? 'completed' : 'active'}`}>
                    {msg.toolCompleted ? '✓' : '⏳'} {msg.toolName}
                    {msg.toolCompleted ? 'ツールを利用しました' : 'ツールを利用中...'}
                  </span>
                )}
                {msg.content && !msg.isToolUsing && <ReactMarkdown>{msg.content}</ReactMarkdown>}
              </div>
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="form-wrapper">
        <form onSubmit={handleSubmit} className="form">
          <input value={input} onChange={e => setInput(e.target.value)} placeholder="メッセージを入力..." disabled={loading} className="input" />
          <button type="submit" disabled={loading || !input.trim()} className="button">
            {loading ? '⌛️' : '送信'}
          </button>
        </form>
      </div>
    </div>
  );
}

export default App;
