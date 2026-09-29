#!/usr/bin/env node
import * as cdk from "aws-cdk-lib"
import { AwsSolutionsChecks } from "cdk-nag"
import { FastMainStack } from "../lib/fast-main-stack"
import { GridToolsStack } from "../lib/grid-tools-stack"
import { ConfigManager } from "../lib/utils/config-manager"

// Load configuration using ConfigManager
const configManager = new ConfigManager("config.yaml")

// Initial props consist of configuration parameters
const props = configManager.getProps()

// cdk-nag v3 registers as a CDK validation plugin on the App, applying AwsSolutionsChecks
// to every stack at synth time (steering `infra-cdk.md`, design §16.5). Suppressions are
// added per-resource with reviewer-grade reasoning citing the ADR that justifies each one.
//
// The cast bridges a minor type skew: cdk-nag 3.0.2's PolicyViolationBeta1 marks
// `resourceLogicalId` optional while this aws-cdk-lib's plugin interface requires it. The
// runtime contract is identical (cdk-nag always sets the id); only the .d.ts differ. Isolated
// to this single registration point so no construct code depends on the skew.
const nagPack = new AwsSolutionsChecks(undefined, { verbose: true })
const app = new cdk.App({
  policyValidationBeta1: [nagPack as unknown as cdk.IPolicyValidationPluginBeta1],
})

// Deploy the new Amplify-based stack that solves the circular dependency
const amplifyStack = new FastMainStack(app, props.stack_name_base, {
  config: props,
  env: { 
    account: process.env.CDK_DEFAULT_ACCOUNT, 
    region: process.env.CDK_DEFAULT_REGION 
  },
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
