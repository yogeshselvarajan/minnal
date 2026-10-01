#!/usr/bin/env node
import * as cdk from "aws-cdk-lib"
import { AwsSolutionsChecks } from "cdk-nag"
import { FastMainStack } from "../lib/fast-main-stack"
import { MinnalAgentTeamStack } from "../lib/minnal-agent-team-stack"
import { ConfigManager } from "../lib/utils/config-manager"

// Load configuration using ConfigManager
const configManager = new ConfigManager("config.yaml")

// Initial props consist of configuration parameters
const props = configManager.getProps()

const app = new cdk.App()

// cdk-nag v3 registers as a CDK validation plugin, applying AwsSolutionsChecks to every stack
// at synth time (steering `infra-cdk.md`, design §16.5). Suppressions are acknowledged
// per-resource with reviewer-grade reasoning citing the ADR that justifies each one.
cdk.Validations.of(app).addPlugins(new AwsSolutionsChecks(undefined, { verbose: true }))

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

// The grid-tools spec stack (design §16). Synthesised as its own stack so it can be
// checked (cdk-nag) and asserted (tests/infra) independently of the FAST main stack —
// which is important because the FAST main stack's PythonFunction assets need Docker
// arm64 emulation that is blocked on the build host, whereas grid-tools' tool Lambdas
// bundle locally with uv (design §3.2).
const gridToolsStack = new GridToolsStack(app, `${props.stack_name_base}-grid-tools`, {
  config: props,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
})

// Reference the stacks so they are part of the tree the validation plugin walks.
void amplifyStack
void gridToolsStack

app.synth()
