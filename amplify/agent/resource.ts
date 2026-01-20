import { Stack } from 'aws-cdk-lib';
import * as agentcore from '@aws-cdk/aws-bedrock-agentcore-alpha';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import { ContainerImageBuild } from 'deploy-time-build';
import { IUserPool, IUserPoolClient } from 'aws-cdk-lib/aws-cognito';
import * as path from 'path';
import { fileURLToPath } from 'url';

// 指定されたCDKスタックに、AgentCoreランタイム関連のリソース一式を作成する関数
export function createAgentCoreRuntime(
  stack: Stack,
  userPool: IUserPool,
  userPoolClient: IUserPoolClient
) {
  // CodeBuildでARM64イメージをビルド（deploy-time-buildを利用）
  const agentImage = new ContainerImageBuild(stack, 'AgentImage', {
    directory: path.dirname(fileURLToPath(import.meta.url)),
    platform: Platform.LINUX_ARM64,
  });

  // スタック名から環境識別子を抽出（英数字とアンダースコアのみ許可）
  const stackNameParts = stack.stackName.split('-');
  const rawEnvId = stackNameParts.length >= 4 ? stackNameParts[3] : stack.stackName.slice(-10);
  const envId = rawEnvId.replace(/[^a-zA-Z0-9]/g, '').toLowerCase();

  // Confluence環境変数を設定（未設定の場合は空オブジェクト）
  const confluenceEnvVars: Record<string, string> = {};
  if (process.env.CONFLUENCE_URL) {
    confluenceEnvVars.CONFLUENCE_URL = process.env.CONFLUENCE_URL;
  }
  if (process.env.CONFLUENCE_EMAIL) {
    confluenceEnvVars.CONFLUENCE_EMAIL = process.env.CONFLUENCE_EMAIL;
  }
  if (process.env.CONFLUENCE_API_TOKEN) {
    confluenceEnvVars.CONFLUENCE_API_TOKEN = process.env.CONFLUENCE_API_TOKEN;
  }
  if (process.env.CONFLUENCE_DEFAULT_SPACE_KEY) {
    confluenceEnvVars.CONFLUENCE_DEFAULT_SPACE_KEY = process.env.CONFLUENCE_DEFAULT_SPACE_KEY;
  }

  // AgentCoreランタイムを作成（L2コンストラクト利用）
  const runtime = new agentcore.Runtime(stack, 'UpdateCheckerRuntime', {
    runtimeName: `outlook_agent_${envId}`,
    agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromEcrRepository(
      agentImage.repository,
      agentImage.imageTag
    ),
    // AgentCore Identityでインバウンド認証を設定
    authorizerConfiguration: agentcore.RuntimeAuthorizerConfiguration.usingCognito(
      userPool,
      [userPoolClient],
    ),
    networkConfiguration: agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
    // Confluence環境変数を追加
    environmentVariables: confluenceEnvVars,
  });

  // Bedrock APIの利用権限を追加
  runtime.addToRolePolicy(
    new iam.PolicyStatement({
      actions: [
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream'
      ],
      resources: [
        'arn:aws:bedrock:*::foundation-model/*',
        'arn:aws:bedrock:*:*:inference-profile/*',
      ],
    })
  );

  return { runtime };
}
