# Parameter Store Setup Guide

This guide explains how to securely store and manage credentials using AWS Systems Manager Parameter Store for the MCP Gateway.

## Overview

The application stores sensitive credentials (OAuth client ID/secret and provider API keys) in AWS Systems Manager Parameter Store as encrypted SecureString parameters. This provides:

- **Encryption at rest** using AWS KMS
- **Access control** via IAM policies
- **Audit trail** through CloudTrail
- **Credential rotation** without code redeployment

## Deployment Steps

### 1. Deploy the CDK Stack

The CDK stack creates placeholder parameters in Parameter Store:

```bash
cd cdk
npm install
cdk deploy
```

This creates the following SecureString parameters with placeholder values:
- `/agentcore-mcp-gateway/{stack-name}/credentials/client-id`
- `/agentcore-mcp-gateway/{stack-name}/credentials/client-secret`
- `/agentcore-mcp-gateway/{stack-name}/credentials/api-key-atlassian`
- `/agentcore-mcp-gateway/{stack-name}/credentials/api-key-coralogix`

### 2. Populate Parameter Values

After deployment, you must replace the placeholder values with actual credentials.

#### Option A: Using AWS Console

1. Navigate to **AWS Systems Manager** > **Parameter Store** in the AWS Console
2. Filter by `/agentcore-mcp-gateway/` to find your parameters
3. For each parameter:
   - Click on the parameter name
   - Click **Edit**
   - Replace `PLACEHOLDER_REPLACE_ME` with the actual credential value
   - Click **Save changes**

#### Option B: Using AWS CLI

Replace `{stack-name}` with your actual CloudFormation stack name and `{value}` with the credential:

```bash
# Set your stack name
STACK_NAME="your-stack-name"

# Update client ID
aws ssm put-parameter \
  --name "/agentcore-mcp-gateway/${STACK_NAME}/credentials/client-id" \
  --value "your-actual-client-id" \
  --type "SecureString" \
  --overwrite

# Update client secret
aws ssm put-parameter \
  --name "/agentcore-mcp-gateway/${STACK_NAME}/credentials/client-secret" \
  --value "your-actual-client-secret" \
  --type "SecureString" \
  --overwrite

# Update Atlassian API key
aws ssm put-parameter \
  --name "/agentcore-mcp-gateway/${STACK_NAME}/credentials/api-key-atlassian" \
  --value "your-actual-atlassian-key" \
  --type "SecureString" \
  --overwrite

# Update Coralogix API key
aws ssm put-parameter \
  --name "/agentcore-mcp-gateway/${STACK_NAME}/credentials/api-key-coralogix" \
  --value "your-actual-coralogix-key" \
  --type "SecureString" \
  --overwrite
```

### 3. Verify Lambda Function

After populating parameters, test the Lambda function:

```bash
# Invoke the Lambda function to trigger initialization
aws lambda invoke \
  --function-name $(aws cloudformation describe-stacks \
    --stack-name ${STACK_NAME} \
    --query 'Stacks[0].Outputs[?OutputKey==`ProxyLambdaName`].OutputValue' \
    --output text) \
  --payload '{"rawPath": "/.well-known/oauth-authorization-server"}' \
  response.json

# Check the response
cat response.json
```

If parameters are not populated, the Lambda will fail with a clear error message.

## Credential Rotation

To rotate credentials without redeploying the Lambda:

### 1. Update Parameter Value

Use AWS Console or CLI to update the parameter value (same as step 2 above).

### 2. Force Lambda Container Refresh

The Lambda caches credentials for the lifetime of the container. To apply new credentials immediately:

#### Option A: Manual Invocation (Immediate)

Invoke the Lambda multiple times to cycle through containers:

```bash
for i in {1..10}; do
  aws lambda invoke \
    --function-name <lambda-name> \
    --payload '{}' \
    /dev/null
done
```

#### Option B: Wait for Natural Recycling (15-30 minutes)

AWS automatically recycles Lambda containers every 15-30 minutes. New containers will pick up the updated credentials.

#### Option C: Redeploy Lambda (Nuclear Option)

Update the Lambda configuration to force immediate container recycling:

```bash
aws lambda update-function-configuration \
  --function-name <lambda-name> \
  --description "Force refresh - $(date)"
```

## Troubleshooting

### Lambda Initialization Error: "Missing required parameters"

**Cause:** Parameters are not created in Parameter Store or have incorrect names.

**Solution:**
1. Verify parameters exist: `aws ssm get-parameters --names "/agentcore-mcp-gateway/${STACK_NAME}/credentials/client-id"`
2. Check parameter names match the hierarchical pattern
3. Ensure you're checking the correct region and account

### Lambda Initialization Error: "Parameter contains placeholder value"

**Cause:** Parameter still has the default `PLACEHOLDER_REPLACE_ME` value.

**Solution:** Update the parameter value using Console or CLI (see step 2 above).

### Lambda Error: "AccessDeniedException"

**Cause:** Lambda execution role doesn't have permission to read Parameter Store.

**Solution:**
1. Check the IAM policy attached to the Lambda execution role
2. Verify the resource ARN matches: `arn:aws:ssm:${region}:${account}:parameter/agentcore-mcp-gateway/${stack-name}/credentials/*`
3. Ensure you're using the correct stack name in parameter paths

### Invalid Client Error in Token Request

**Cause:** Client ID or secret doesn't match the values in Parameter Store.

**Solution:**
1. Verify parameter values in AWS Console
2. Check client is sending correct credentials
3. Force Lambda container refresh if credentials were recently rotated

## Security Best Practices

1. **Use Least Privilege IAM**: Lambda execution role only has access to parameters under `/agentcore-mcp-gateway/{stack-name}/credentials/*`

2. **Enable CloudTrail**: Parameter access is automatically logged to CloudTrail for audit purposes

3. **Rotate Regularly**: Establish a schedule for credential rotation (e.g., every 90 days)

4. **Monitor Access**: Set up CloudWatch alarms for unusual Parameter Store access patterns

5. **Use KMS Customer Managed Keys** (Optional): For additional control, use a customer-managed KMS key instead of the default AWS-managed key:

```typescript
// In CDK stack
const kmsKey = new kms.Key(this, 'ParameterKey', {
  enableKeyRotation: true,
  description: 'KMS key for Parameter Store credentials'
});

new ssm.StringParameter(this, 'ClientIdParam', {
  parameterName: '/agentcore-mcp-gateway/${this.stackName}/credentials/client-id',
  stringValue: 'PLACEHOLDER_REPLACE_ME',
  type: ssm.ParameterType.SECURE_STRING,
  encryption: ssm.ParameterEncryption.KMS,
  key: kmsKey
});
```

## Adding New Provider API Keys

To add a new provider (e.g., "github"):

### 1. Create Parameter in CDK

Add to `cdk/lib/cdk-stack.ts`:

```typescript
const githubApiKeyParam = new ssm.StringParameter(this, "GithubApiKeyParam", {
  parameterName: `/agentcore-mcp-gateway/${this.stackName}/credentials/api-key-github`,
  stringValue: "PLACEHOLDER_REPLACE_ME",
  type: ssm.ParameterType.SECURE_STRING,
  description: "GitHub API Key for MCP Gateway",
});
```

### 2. Update Lambda Retrieval Logic

Add to `lambda/idp_api_key_lambda.py` in `get_parameters_from_ssm()`:

```python
param_names = [
    f"{param_prefix}client-id",
    f"{param_prefix}client-secret",
    f"{param_prefix}api-key-atlassian",
    f"{param_prefix}api-key-coralogix",
    f"{param_prefix}api-key-github",  # Add new parameter
]

# In the parameter mapping section:
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

## Cost Estimation

Parameter Store pricing (as of 2024):

- **Parameter Storage**: Free for standard parameters
- **API Requests**: $0.05 per 10,000 requests (only on Lambda cold starts)
- **KMS**: $1/month per customer-managed key (optional) + $0.03 per 10,000 encryption/decryption requests

**Estimated monthly cost for this implementation**: <$5/month (assuming 100K Lambda invocations with 1% cold start rate)
