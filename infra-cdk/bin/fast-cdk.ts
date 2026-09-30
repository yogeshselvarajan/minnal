#!/usr/bin/env node
import * as cdk from "aws-cdk-lib"
import { FastMainStack } from "../lib/fast-main-stack"
import { MinnalAgentTeamStack } from "../lib/minnal-agent-team-stack"
import { ConfigManager } from "../lib/utils/config-manager"

// Load configuration using ConfigManager
const configManager = new ConfigManager("config.yaml")

// Initial props consist of configuration parameters
const props = configManager.getProps()

const app = new cdk.App()

// Deploy the new Amplify-based stack that solves the circular dependency
const amplifyStack = new FastMainStack(app, props.stack_name_base, {
  config: props,
  env: { 
    account: process.env.CDK_DEFAULT_ACCOUNT, 
    region: process.env.CDK_DEFAULT_REGION 
  },
})
void amplifyStack

// The agent-team-runtime infrastructure stack (agent-team-runtime spec §19). Synth-only:
// `cdk synth` + cdk-nag, never `cdk deploy`. It is synthesised environment-agnostic so every
// ARN uses CloudFormation pseudo-parameters and no account id or Region is baked in (R24.8).
// cdk-nag AwsSolutionsChecks is applied and asserted zero-findings in the verification test
// (test/agent-team-verify.test.ts), matching the repo's existing nag pattern
// (test/bedrock-model-allowlist.test.ts); it is not registered here because the pinned
// cdk-nag 3.0.2 validation plugin errors while hashing directory Lambda assets during CLI
// synth. See docs/adr/0008-cdk-nag-suppressions-agent-team.md.
new MinnalAgentTeamStack(app, `${props.stack_name_base}-agent-team`, {
  config: props,
})

app.synth()
