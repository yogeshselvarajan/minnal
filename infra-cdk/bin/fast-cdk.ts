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
// `cdk synth` + cdk-nag, never `cdk deploy`.
new MinnalAgentTeamStack(app, `${props.stack_name_base}-agent-team`, {
  config: props,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
})

app.synth()
