#!/usr/bin/env node
import * as cdk from "aws-cdk-lib"
import { AwsSolutionsChecks } from "cdk-nag"
import { GridToolsStack } from "../lib/grid-tools-stack"
import { ConfigManager } from "../lib/utils/config-manager"

/**
 * Standalone synth entry for the grid-tools stack ONLY (design §16, task 72).
 *
 * The default app (`bin/fast-cdk.ts`) also instantiates the FAST main stack, whose Cedar-policy
 * PythonFunction needs Docker arm64 emulation that is blocked on the build host
 * (docs/plans/autopilot-state.md). This entry synthesises just the grid-tools stack — whose tool
 * Lambdas bundle locally with uv (design §3.2) — so `cdk synth` and cdk-nag can run against it
 * independently, and so the qa lane (task 73) can obtain the template via `Template.fromStack`.
 *
 * Synth with:
 *   npx cdk synth --app "npx ts-node --prefer-ts-exts bin/grid-tools-app.ts"
 */
const configManager = new ConfigManager("config.yaml")
const props = configManager.getProps()

const app = new cdk.App()
cdk.Validations.of(app).addPlugins(new AwsSolutionsChecks(undefined, { verbose: true }))

new GridToolsStack(app, `${props.stack_name_base}-grid-tools`, {
  config: props,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
})

app.synth()
