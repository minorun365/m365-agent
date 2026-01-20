/**
 * MSAL (Microsoft Authentication Library) の設定ファイル
 *
 * このファイルは Microsoft Entra ID (旧 Azure AD) を使った
 * OAuth 2.0 認証を行うための設定を管理します。
 *
 * 主な役割:
 * - MSAL インスタンスの初期化
 * - Microsoft Graph API にアクセスするためのトークン取得
 */

import {
  PublicClientApplication,
  type Configuration,
  type AccountInfo,
} from "@azure/msal-browser";

// =====================================
// 環境変数から設定を取得
// =====================================

// Entra で登録したアプリの Client ID（必須）
const clientId = import.meta.env.VITE_MS_CLIENT_ID as string | undefined;
if (!clientId) {
  throw new Error("VITE_MS_CLIENT_ID is not set");
}

// 認証エンドポイント
// - consumers: 個人アカウントのみ
// - common: 個人 + 組織アカウント両方
// - organizations: 組織アカウントのみ
// - <tenant-id>: 特定のテナントのみ
const authority =
  (import.meta.env.VITE_MS_AUTHORITY as string | undefined) ??
  "https://login.microsoftonline.com/common";

// =====================================
// MSAL の設定
// =====================================

const msalConfig: Configuration = {
  auth: {
    clientId,
    authority,
    // リダイレクト先 URL（Entra 側の Redirect URI と一致させる必要あり）
    redirectUri: window.location.origin,
  },
  cache: {
    // トークンの保存先
    // - sessionStorage: ブラウザを閉じると消える（より安全）
    // - localStorage: ブラウザを閉じても残る
    cacheLocation: "sessionStorage",
  },
};

// =====================================
// MSAL インスタンス（シングルトン）
// =====================================

export const msalInstance = new PublicClientApplication(msalConfig);

// =====================================
// 初期化関数
// =====================================

// MSAL は使用前に必ず initialize() を呼ぶ必要がある
// 複数回呼ばれても大丈夫なようにフラグで制御
let initialized = false;

export async function ensureMsalInitialized() {
  if (!initialized) {
    await msalInstance.initialize();
    initialized = true;
  }
}

// =====================================
// Graph API のスコープ（権限）
// =====================================

// 要求するアクセス権限の一覧
// - User.Read: ユーザー情報の読み取り
// - Calendars.ReadWrite: カレンダーの読み書き
// - offline_access: リフレッシュトークンの取得（長期間のアクセス）
export const graphScopes = ["User.Read", "Calendars.ReadWrite", "Tasks.ReadWrite", "offline_access"];

// =====================================
// ヘルパー関数
// =====================================

/**
 * ログイン中のアカウントを取得
 * 複数アカウントがある場合は最初のものを返す
 */
export function pickAccount(): AccountInfo | null {
  const accounts = msalInstance.getAllAccounts();
  return accounts.length > 0 ? accounts[0] : null;
}

/**
 * ログアウト（アカウントをクリア）
 */
export async function logout(): Promise<void> {
  const account = pickAccount();
  if (account) {
    await msalInstance.logoutPopup({ account });
  }
}
