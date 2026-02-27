import * as cdk from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as integrations from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { Construct } from "constructs";
import * as path from "path";
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";

export class CdkStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // =============================================================================
    // PARAMETER STORE - SECURE CREDENTIAL STORAGE
    // =============================================================================

    // Create SecureString parameters for storing credentials
    const clientIdParam = new ssm.StringParameter(this, "ClientIdParam", {
      parameterName: `/agentcore-mcp-gateway/${this.stackName}/credentials/client-id`,
      stringValue: "PLACEHOLDER_REPLACE_ME",
      type: ssm.ParameterType.SECURE_STRING,
      description: "OAuth Client ID for MCP Gateway",
    });

    const clientSecretParam = new ssm.StringParameter(
      this,
      "ClientSecretParam",
      {
        parameterName: `/agentcore-mcp-gateway/${this.stackName}/credentials/client-secret`,
        stringValue: "PLACEHOLDER_REPLACE_ME",
        type: ssm.ParameterType.SECURE_STRING,
        description: "OAuth Client Secret for MCP Gateway",
      }
    );

    const atlassianApiKeyParam = new ssm.StringParameter(
      this,
      "AtlassianApiKeyParam",
      {
        parameterName: `/agentcore-mcp-gateway/${this.stackName}/credentials/api-key-atlassian`,
        stringValue: "PLACEHOLDER_REPLACE_ME",
        type: ssm.ParameterType.SECURE_STRING,
        description: "Atlassian API Key for MCP Gateway",
      }
    );

    const coralogixApiKeyParam = new ssm.StringParameter(
      this,
      "CoralogixApiKeyParam",
      {
        parameterName: `/agentcore-mcp-gateway/${this.stackName}/credentials/api-key-coralogix`,
        stringValue: "PLACEHOLDER_REPLACE_ME",
        type: ssm.ParameterType.SECURE_STRING,
        description: "Coralogix API Key for MCP Gateway",
      }
    );

    // =============================================================================
    // LAMBDA FUNCTION
    // =============================================================================

    // MCP Proxy Lambda
    const idpLambda = new lambda.Function(this, "IdpLambda", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "lambda_function.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../lambda"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            "bash",
            "-c",
            ["cp idp_api_key_lambda.py /asset-output/lambda_function.py"].join(
              " && "
            ),
          ],
        },
      }),
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      environment: {
        STACK_NAME: this.stackName,
      },
    });

    // Grant Lambda permission to read Parameter Store parameters
    idpLambda.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["ssm:GetParameter", "ssm:GetParameters"],
        resources: [
          `arn:aws:ssm:${this.region}:${this.account}:parameter/agentcore-mcp-gateway/${this.stackName}/credentials/*`,
        ],
      })
    );

    // =============================================================================
    // API GATEWAY
    // =============================================================================

    // Create HTTP API
    const httpApi = new apigateway.HttpApi(this, "McpOAuthProxyApi", {
      apiName: `mcp-oauth-proxy`,
      corsPreflight: {
        allowOrigins: ["*"],
        allowMethods: [
          apigateway.CorsHttpMethod.GET,
          apigateway.CorsHttpMethod.POST,
          apigateway.CorsHttpMethod.OPTIONS,
        ],
        allowHeaders: ["*"],
      },
    });

    // Create Lambda integrations
    const idpIntegration = new integrations.HttpLambdaIntegration(
      "ProxyIntegration",
      idpLambda
    );

    // Add routes
    httpApi.addRoutes({
      path: "/.well-known/oauth-authorization-server",
      methods: [apigateway.HttpMethod.GET],
      integration: idpIntegration,
    });

    httpApi.addRoutes({
      path: "/token",
      methods: [apigateway.HttpMethod.POST],
      integration: idpIntegration,
    });

    // Default route
    httpApi.addRoutes({
      path: "/{proxy+}",
      methods: [apigateway.HttpMethod.ANY],
      integration: idpIntegration,
    });

    // Get API endpoint
    const apiEndpoint =
      httpApi.url ||
      `https://${httpApi.httpApiId}.execute-api.${this.region}.amazonaws.com`;

    const gatewayRole = new iam.Role(this, "GatewayRole", {
      assumedBy: iam.ServicePrincipal.fromStaticServicePrincipleName(
        "bedrock-agentcore.amazonaws.com"
      ),
      inlinePolicies: {
        getAccessToken: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: [
                "bedrock-agentcore:GetWorkloadAccess*",
                "bedrock-agentcore:GetResourceOauth2Token",
                "secretsmanager:GetSecretValue",
              ],
              resources: ["*"],
              effect: iam.Effect.ALLOW,
            }),
          ],
        }),
      },
    });

    const gateway = new agentcore.Gateway(this, "AgentCoreMcpGateway", {
      gatewayName: `${this.stackName}-agentcore-gateway`,
      description: "AgentCore Gateway for VS Code IDE integration",
      protocolConfiguration: agentcore.GatewayProtocol.mcp({
        searchType: agentcore.McpGatewaySearchType.SEMANTIC,
        supportedVersions: [
          agentcore.MCPProtocolVersion.MCP_2025_03_26,
          agentcore.MCPProtocolVersion.MCP_2025_06_18,
          "2025-11-25" as agentcore.MCPProtocolVersion,
        ],
      }),
      role: gatewayRole,
      exceptionLevel: agentcore.GatewayExceptionLevel.DEBUG,
      authorizerConfiguration: agentcore.GatewayAuthorizer.usingAwsIam(),
    });

    idpLambda.addEnvironment("GATEWAY_URL", gateway.gatewayUrl ?? "");

    // =============================================================================
    // OUTPUTS
    // =============================================================================

    new cdk.CfnOutput(this, "ApiEndpoint", {
      value: apiEndpoint,
      description: "API Gateway Endpoint",
    });

    new cdk.CfnOutput(this, "ProxyLambdaName", {
      value: idpLambda.functionName,
      description: "MCP Proxy Lambda Function Name",
    });

    new cdk.CfnOutput(this, "GatewayId", {
      value: gateway.gatewayId,
      description: "Gateway ID",
    });
  }
}
