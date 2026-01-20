import { defineAuth } from '@aws-amplify/backend';

// 認証設定（メールアドレスでログイン）
export const auth = defineAuth({
  loginWith: {
    email: true,
  },
});
