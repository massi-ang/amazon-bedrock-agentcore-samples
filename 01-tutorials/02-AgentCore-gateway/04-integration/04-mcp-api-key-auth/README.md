# AgentCore Gateway with API Key Authentication via OAuth Proxy

## Overview

This project demonstrates a pattern for **securely managing multiple API keys** for external services that AgentCore Gateway needs to access. It implements a **fake OAuth Identity Provider (IdP)** that returns provider-specific API keys as OAuth access tokens, allowing AgentCore Gateway to dynamically fetch credentials using a standard OAuth M2M (machine-to-machine) flow.

**Key Pattern**: Instead of hardcoding API keys in Gateway targets, this approach centralizes API key management in a serverless OAuth proxy that securely stores credentials in AWS Systems Manager Parameter Store and leverages the custom parameters information that can be provided when setting up a credential provider in AgentCore Identity.

## Architecture

```
AgentCore Gateway
    ↓
Credential Provider (OAuth M2M)
    ↓
API Gateway + Lambda (Fake OAuth IdP)
    ↓
Parameter Store (Encrypted API Keys)
```

**Flow:**

1. AgentCore Gateway needs to call an external service (e.g., MCP server with Atlassian authentication)
2. Gateway requests OAuth token from the IdP Lambda, specifying provider name in custom parameters
3. Lambda retrieves encrypted API key for that provider from Parameter Store
4. Lambda returns API key disguised as an OAuth access token
5. Gateway uses this "token" (actually an API key) to authenticate to the external service

NOTE: since OAuth2.0 mandates that the tokens are inserted in the `Authorization` header as `Bearer TOKEN` this only works for API that accept the API Key in that format.

## Why This Pattern?

### Benefits

- **API Key support for MCP targets in AgentCore Gateway**: Allow using API Keys with MCP targets
- **Dynamic Credential Fetching**: Gateway gets credentials on-demand, not hardcoded in configuration
- **Multi-Provider Support**: Single IdP serves API keys for multiple external services
- **Standard OAuth Interface**: AgentCore Gateway uses familiar OAuth credential provider configuration
- **Security**: KMS encryption at rest, IAM access control, CloudTrail audit logging
- **Rotation Without Redeployment**: Update API keys in Parameter Store without changing Gateway configuration

### Use Cases

- AgentCore Gateway connecting to MCP servers using API key as authentication mechanism

## Components

| Component               | Purpose                                                     | Technology                         |
| ----------------------- | ----------------------------------------------------------- | ---------------------------------- |
| **Parameter Store**     | Encrypted storage for API keys and OAuth credentials        | AWS Systems Manager (SecureString) |
| **IdP Lambda**          | Fake OAuth server that returns API keys as access tokens    | Python 3.12 Lambda                 |
| **API Gateway**         | Public endpoint exposing OAuth metadata and token endpoints | HTTP API                           |
| **AgentCore Gateway**   | Consumes OAuth tokens to authenticate to external services  | AWS Bedrock AgentCore              |
| **Credential Provider** | OAuth M2M provider configuration pointing to IdP Lambda     | AgentCore Credential Provider      |

## Security Features

### Parameter Store Integration

All sensitive credentials are stored in AWS Systems Manager Parameter Store with:

- **Encryption at rest** using AWS KMS
- **IAM-based access control** - Lambda can only read specific parameter paths
- **CloudTrail audit logging** - All parameter access is logged
- **Rotation without redeployment** - Update values in place, Lambda picks up on next cold start
- **No secrets in code** - CloudFormation templates and CDK code contain only placeholders

### OAuth-Style Security

- Client credentials (client_id/client_secret) validated before returning API keys
- Provider-specific API keys isolated - requesting "atlassian" returns only Atlassian key
- Failed authentication returns standard OAuth error responses

## Setup

### Prerequisites

- Python 3.10+
- Node.js 18+ (for CDK)
- AWS CLI configured with credentials
- AWS account with permissions for Lambda, API Gateway, SSM, IAM, and Bedrock AgentCore

### Step 1: Deploy the CDK Stack

The CDK stack creates:

- 4 SecureString parameters in Parameter Store (with placeholder values)
- Lambda function with SSM read permissions
- API Gateway with OAuth endpoints
- AgentCore Gateway with IAM authentication

```bash
cd cdk
npm install
cdk deploy
```

**Note**: The stack will output the API endpoint URL and Gateway ID - save these for later steps.

### Step 2: Populate Parameter Store Credentials

**Critical**: After deployment, you must replace placeholder values with actual credentials.

#### Option A: AWS CLI (Recommended)

```bash
# Set your stack name (from CDK deployment)
STACK_NAME="ApiKeyIdpStack"  # Replace with your actual stack name

# Update OAuth client credentials
aws ssm put-parameter \
  --name "/agentcore-mcp-gateway/${STACK_NAME}/credentials/client-id" \
  --value "your-oauth-client-id" \
  --type "SecureString" \
  --overwrite

aws ssm put-parameter \
  --name "/agentcore-mcp-gateway/${STACK_NAME}/credentials/client-secret" \
  --value "your-oauth-client-secret" \
  --type "SecureString" \
  --overwrite

# Update provider API keys
aws ssm put-parameter \
  --name "/agentcore-mcp-gateway/${STACK_NAME}/credentials/api-key-atlassian" \
  --value "your-atlassian-api-key" \
  --type "SecureString" \
  --overwrite

aws ssm put-parameter \
  --name "/agentcore-mcp-gateway/${STACK_NAME}/credentials/api-key-coralogix" \
  --value "your-coralogix-api-key" \
  --type "SecureString" \
  --overwrite
```

#### Option B: AWS Console

1. Navigate to **AWS Systems Manager** > **Parameter Store**
2. Filter by `/agentcore-mcp-gateway/`
3. Click each parameter and replace `PLACEHOLDER_REPLACE_ME` with actual values

**📖 For detailed instructions including credential rotation, troubleshooting, and adding new providers, see [PARAMETER_STORE_SETUP.md](PARAMETER_STORE_SETUP.md)**

### Step 3: Create Credential Provider and Test

Follow the [agentcore-gateway-apikey-mcp.ipynb](agentcore-gateway-apikey-mcp.ipynb) notebook to:

1. Create OAuth2 Credential Provider pointing to your IdP Lambda
2. Test token retrieval with different provider parameters
3. Create Gateway target using the credential provider
4. Test end-to-end authentication flow

**Key notebook steps:**

```python
# Create credential provider
client.create_oauth2_credential_provider(
    name="OauthAPIKey",
    credentialProviderVendor="CustomOauth2",
    oauth2ProviderConfigInput={
        'customOauth2ProviderConfig': {
            'clientId': 'abc',  # Must match Parameter Store value
            'clientSecret': 'abc',  # Must match Parameter Store value
            'oauthDiscovery': {
                'authorizationServerMetadata': {
                    'tokenEndpoint': f'{api_gateway_url}/token',
                    'issuer': api_gateway_url,
                }
            }
        }
    }
)

# Get token for specific provider
acr.get_resource_oauth2_token(
    workloadIdentityToken=workload_token,
    resourceCredentialProviderName='OauthAPIKey',
    scopes=['list', 'invoke', 'openid'],
    customParameters={'provider': 'atlassian'},  # Request Atlassian API key
    oauth2Flow='M2M'
)
```

## How It Works

### OAuth Endpoints Implemented

The Lambda function implements two OAuth endpoints:

#### 1. OAuth Discovery Endpoint

```
GET /.well-known/oauth-authorization-server
```

Returns OAuth server metadata (RFC 8414):

```json
{
  "issuer": "https://<api-id>.execute-api.<region>.amazonaws.com",
  "token_endpoint": "https://<api-id>.execute-api.<region>.amazonaws.com/token",
  "grant_types_supported": ["client_credentials"],
  "token_endpoint_auth_methods_supported": ["client_secret_post"]
}
```

#### 2. Token Endpoint

```
POST /token
```

Request body:

```
client_id=abc&client_secret=abc&grant_type=client_credentials&provider=atlassian
```

Response:

```json
{
  "access_token": "<atlassian-api-key-from-parameter-store>"
}
```

### Parameter Store Lookup

When the token endpoint receives a request:

1. Validates `client_id` and `client_secret` against cached values from Parameter Store
2. Extracts `provider` from custom parameters (e.g., "atlassian", "coralogix")
3. Retrieves API key from cached parameters: `api_key_atlassian` or `api_key_coralogix`
4. Returns API key as the OAuth access token

### Caching Strategy

- Lambda retrieves all parameters from Parameter Store during **initialization** (cold start)
- Parameters are cached in memory for the lifetime of the Lambda container
- Subsequent requests use cached values (no SSM API calls)
- Cold start overhead: ~50-100ms
- Warm request overhead: 0ms (pure memory lookup)

## Adding New Providers

To add support for a new service (e.g., "github"):

### 1. Update CDK Stack

Add new parameter in `cdk/lib/cdk-stack.ts`:

```typescript
const githubApiKeyParam = new ssm.StringParameter(this, "GithubApiKeyParam", {
  parameterName: `/agentcore-mcp-gateway/${this.stackName}/credentials/api-key-github`,
  stringValue: "PLACEHOLDER_REPLACE_ME",
  type: ssm.ParameterType.SECURE_STRING,
  description: "GitHub API Key for MCP Gateway",
});
```

### 2. Update Lambda Function

Add to parameter retrieval list in `lambda/idp_api_key_lambda.py`:

```python
param_names = [
    f"{param_prefix}client-id",
    f"{param_prefix}client-secret",
    f"{param_prefix}api-key-atlassian",
    f"{param_prefix}api-key-coralogix",
    f"{param_prefix}api-key-github",  # New provider
]

# In parameter mapping section:
elif name.endswith("api-key-github"):
    params["api_key_github"] = value
```

### 3. Deploy and Populate

```bash
cdk deploy

aws ssm put-parameter \
  --name "/agentcore-mcp-gateway/${STACK_NAME}/credentials/api-key-github" \
  --value "your-github-api-key" \
  --type "SecureString" \
  --overwrite
```

### 4. Use in Gateway Target

```python
acr.get_resource_oauth2_token(
    workloadIdentityToken=workload_token,
    resourceCredentialProviderName='OauthAPIKey',
    customParameters={'provider': 'github'},  # Request GitHub API key
    oauth2Flow='M2M'
)
```

## Troubleshooting

### Parameter Store Issues

#### Lambda Error: "Parameter contains placeholder value"

**Cause**: Parameters not populated after deployment.
**Solution**: Follow Step 2 to update parameter values using CLI or Console.

#### Lambda Error: "Missing required parameters"

**Cause**: Parameters not created or named incorrectly.
**Solution**:

1. Verify parameters exist: `aws ssm get-parameters --names "/agentcore-mcp-gateway/${STACK_NAME}/credentials/client-id"`
2. Check parameter names match hierarchical pattern
3. Redeploy CDK stack if parameters are missing

#### Lambda Error: "AccessDeniedException"

**Cause**: Lambda role missing SSM permissions.
**Solution**: Verify IAM policy in Lambda execution role includes:

```json
{
  "Effect": "Allow",
  "Action": ["ssm:GetParameter", "ssm:GetParameters"],
  "Resource": "arn:aws:ssm:${region}:${account}:parameter/agentcore-mcp-gateway/${stack-name}/credentials/*"
}
```

### OAuth Flow Issues

#### "invalid_client" Error

**Cause**: Client credentials don't match Parameter Store values.
**Solution**:

1. Check client_id and client_secret in Parameter Store
2. Ensure credential provider uses same values
3. Force Lambda restart if credentials were recently updated

#### Gateway Can't Retrieve Token

**Cause**: Token endpoint URL incorrect or Lambda not accessible.
**Solution**:

1. Verify API Gateway URL in credential provider configuration
2. Check Lambda CloudWatch logs for errors
3. Test token endpoint directly: `curl -X POST https://<api-url>/token -d "client_id=abc&client_secret=abc&grant_type=client_credentials&provider=atlassian"`

### Credential Rotation

When you update API keys in Parameter Store, the Lambda won't pick up changes immediately due to caching. Options:

**Option A: Force Lambda Restart (Immediate)**

```bash
# Invoke Lambda multiple times to cycle containers
for i in {1..10}; do
  aws lambda invoke --function-name <lambda-name> --payload '{}' /dev/null
done
```

**Option B: Wait for Natural Recycling (15-30 minutes)**
AWS automatically recycles Lambda containers, new ones will fetch updated credentials.

**Option C: Update Lambda Configuration (Nuclear)**

```bash
aws lambda update-function-configuration \
  --function-name <lambda-name> \
  --description "Force refresh - $(date)"
```

## Cleanup

### Using Notebook

Run the cleanup cell in [agentcore-gateway-apikey-mcp.ipynb](agentcore-gateway-apikey-mcp.ipynb) to delete:

- Gateway targets
- Credential providers

### Using CDK

```bash
cd cdk
cdk destroy
```

This removes:

- Lambda function
- API Gateway
- Parameter Store parameters
- IAM roles
- AgentCore Gateway

## Files

| File                                                                     | Description                                                                        |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| [agentcore-gateway-apikey-mcp.ipynb](agentcore-gateway-apikey-mcp.ipynb) | Setup notebook - creates credential provider, tests flows, creates Gateway targets |
| [cdk/lib/cdk-stack.ts](cdk/lib/cdk-stack.ts)                             | CDK stack - Parameter Store, Lambda, API Gateway, IAM permissions                  |
| [lambda/idp_api_key_lambda.py](lambda/idp_api_key_lambda.py)             | IdP Lambda - OAuth endpoints, Parameter Store retrieval, API key mapping           |
| [PARAMETER_STORE_SETUP.md](PARAMETER_STORE_SETUP.md)                     | Detailed guide for parameter management and credential rotation                    |
| [README.md](README.md)                                                   | This file                                                                          |

## Cost Estimation

- **Parameter Store**: $0.05 per 10,000 API requests (only on Lambda cold starts)
- **Lambda**: Compute charges based on invocations and duration (~1-5ms per request)
- **API Gateway**: HTTP API pricing (~$1 per million requests)
- **KMS**: $0 (uses AWS-managed key) or $1/month + $0.03 per 10K operations (customer-managed key)

**Estimated monthly cost**: $5-10 for moderate usage (1M Gateway token requests)

## Security Best Practices

1. **Use Unique Client Credentials**: Don't use "abc/abc" in production - generate strong random values
2. **Rotate API Keys Regularly**: Establish 90-day rotation schedule for provider API keys
3. **Monitor Access**: Set CloudWatch alarms for unusual Parameter Store access patterns
4. **Least Privilege IAM**: Lambda can only read parameters under its specific prefix
5. **Enable CloudTrail**: Parameter access automatically logged for compliance
6. **Customer-Managed KMS Keys** (optional): For additional control over encryption

## Advanced: Custom OAuth Parameters

The IdP supports custom parameters beyond the standard OAuth flow. This allows you to:

- Route to different API keys based on provider name
- Support tenant-specific credentials
- Implement custom authentication logic

Example custom parameters:

```python
customParameters={
    'provider': 'atlassian',
    'environment': 'production',
    'region': 'us-east-1'
}
```

Update Lambda to parse these parameters and return appropriate credentials.

## References

- [AWS Systems Manager Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html)
- [AgentCore Gateway Documentation](https://docs.aws.amazon.com/bedrock-agentcore/)
- [OAuth 2.0 Client Credentials Grant (RFC 6749)](https://datatracker.ietf.org/doc/html/rfc6749#section-4.4)
- [OAuth 2.0 Authorization Server Metadata (RFC 8414)](https://datatracker.ietf.org/doc/html/rfc8414)
- [AWS Lambda Best Practices](https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html)
- [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
