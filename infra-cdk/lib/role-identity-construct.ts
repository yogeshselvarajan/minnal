import * as cdk from "aws-cdk-lib"
import * as cognito from "aws-cdk-lib/aws-cognito"
import * as iam from "aws-cdk-lib/aws-iam"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as logs from "aws-cdk-lib/aws-logs"
import * as ssm from "aws-cdk-lib/aws-ssm"
import * as path from "path"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"

/**
 * Per-role machine identities for the agent team (design §19.3, R24.2).
 *
 * Creates one Cognito user pool on the Essentials plan (required for access-token
 * customisation on the Client Credentials flow), five app clients — one per ICS role —
 * each restricted to the client-credentials flow, the pre-token Lambda registered at
 * trigger version V3_0, and one SSM parameter per role holding its client id. The
 * pre-token Lambda maps `callerContext.clientId` to a role via an SSM-sourced map and
 * returns the `minnal_role` claim; it holds no secret and no business logic (§19.3).
 */
export interface RoleIdentityConstructProps {
  config: AppConfig
}

export class RoleIdentityConstruct extends Construct {
  public readonly userPool: cognito.UserPool
  public readonly preTokenFunction: lambda.Function
  /** ARNs of the five role client secrets, for the runtime's least-privilege secret grant. */
  public readonly roleSecretArns: string[]
  private readonly clientIdByRole: Record<string, string> = {}

  constructor(scope: Construct, id: string, props: RoleIdentityConstructProps) {
    super(scope, id)

    const stack = cdk.Stack.of(this)
    const stackName = props.config.stack_name_base
    const atr = props.config.agent_team_runtime
    const roles = atr.roles

    // The dedicated machine-identity pool. Essentials plan is required for pre-token
    // access-token customisation on client_credentials grants (design §19.3).
    this.userPool = new cognito.UserPool(this, "RolePool", {
      userPoolName: `minnal-${atr.env}-agent-roles`,
      selfSignUpEnabled: false,
      featurePlan: cognito.FeaturePlan.ESSENTIALS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })

    // A resource server + scope is required so a client_credentials client has a scope
    // to request. One Gateway scope covers the agent-team runtime access.
    const gatewayScope = new cognito.ResourceServerScope({
      scopeName: "invoke",
      scopeDescription: "Invoke the Minnal agent-team Gateway",
    })
    const resourceServer = this.userPool.addResourceServer("GatewayResourceServer", {
      identifier: `minnal-${atr.env}-gateway`,
      scopes: [gatewayScope],
    })

    // The pre-token Lambda: reads the client-id -> role map from SSM, injects minnal_role.
    this.preTokenFunction = new lambda.Function(this, "PreTokenRoleLambda", {
      functionName: `minnal-${atr.env}-pretoken-role`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "index.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "lambdas", "minnal-pretoken-role")), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
      timeout: cdk.Duration.seconds(10),
      description: "Maps a Cognito app client to an ICS role and injects the minnal_role claim",
      logGroup: new logs.LogGroup(this, "PreTokenRoleLogGroup", {
        logGroupName: `/aws/lambda/minnal-${atr.env}-pretoken-role`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })

    // Create the five role clients with client credentials only, and collect their ids.
    for (const role of roles) {
      const client = this.userPool.addClient(`MinnalRole-${role}`, {
        userPoolClientName: `minnal-${atr.env}-${role}`,
        generateSecret: true,
        authFlows: {}, // no interactive auth flows; machine identity only
        oAuth: {
          flows: { clientCredentials: true },
          scopes: [cognito.OAuthScope.resourceServer(resourceServer, gatewayScope)],
        },
      })
      this.clientIdByRole[role] = client.userPoolClientId

      // One SSM parameter per role client id, at the stack-scoped path (§19.3).
      new ssm.StringParameter(this, `RoleClientId-${role}`, {
        parameterName: `/${stackName}/roles/${role}/client_id`,
        stringValue: client.userPoolClientId,
        description: `Cognito app client id for the ${role} agent role`,
      })
    }

    // The client-id -> role map the pre-token Lambda reads. Written as one JSON SSM
    // parameter so the Lambda carries no business logic and no secret (§19.3). The
    // parameter name is a fixed string so the Lambda's env var and read grant do not
    // create a resource dependency on the parameter (which itself depends on the client
    // ids, which depend on the pool the Lambda is triggered by): a static name breaks
    // that would-be cycle while keeping the grant scoped to this one parameter.
    const roleMapParamName = `/${stackName}/roles/client_role_map`
    const roleMap: Record<string, string> = {}
    for (const role of roles) {
      roleMap[this.clientIdByRole[role]] = role
    }
    new ssm.StringParameter(this, "RoleClientMap", {
      parameterName: roleMapParamName,
      stringValue: JSON.stringify(roleMap),
      description: "client_id -> ICS role map read by the pre-token Lambda",
    })
    this.preTokenFunction.addEnvironment("ROLE_MAP_PARAM", roleMapParamName)
    this.preTokenFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "ReadRoleClientMap",
        effect: iam.Effect.ALLOW,
        actions: ["ssm:GetParameter"],
        resources: [
          `arn:${stack.partition}:ssm:${stack.region}:${stack.account}:parameter${roleMapParamName}`,
        ],
      })
    )

    // Let Cognito invoke the pre-token Lambda.
    this.preTokenFunction.addPermission("CognitoInvoke", {
      principal: new iam.ServicePrincipal("cognito-idp.amazonaws.com"),
      sourceArn: this.userPool.userPoolArn,
    })

    // Register the trigger at V3_0. The L2 UserPool only supports V1_0/V2_0, so the
    // CloudFormation property is set directly (matches FAST's cognito-construct pattern).
    const cfnUserPool = this.userPool.node.defaultChild as cognito.CfnUserPool
    cfnUserPool.addPropertyOverride("LambdaConfig.PreTokenGenerationConfig", {
      LambdaArn: this.preTokenFunction.functionArn,
      LambdaVersion: "V3_0",
    })

    // The five client-secret ARNs Cognito manages, for the runtime's secret grant (§19.5).
    // Cognito app-client secrets live under this Secrets Manager path.
    this.roleSecretArns = roles.map(
      role =>
        `arn:${stack.partition}:secretsmanager:${stack.region}:${stack.account}:secret:` +
        `/${stackName}/roles/${role}/client_secret*`
    )
  }

  /** The Cognito app client id for a role, resolved at synth time. */
  public clientIdFor(role: string): string {
    return this.clientIdByRole[role]
  }
}
