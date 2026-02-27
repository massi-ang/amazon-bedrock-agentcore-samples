"""
MCP OAuth Proxy Lambda - Handles OAuth metadata, callback interception, token proxying, and MCP forwarding.

This Lambda function replaces the local mcp_oauth_proxy.py script, enabling serverless deployment.
"""

import json
import os
import base64
import urllib.parse
import boto3
from botocore.exceptions import ClientError

# Initialize SSM client at module level
ssm = boto3.client("ssm")

# Module-level cache for credentials
CACHED_PARAMS = {}


def get_parameters_from_ssm():
    """
    Retrieve all credentials from Parameter Store.
    Called once during Lambda initialization to cache credentials.
    """
    stack_name = os.environ.get("STACK_NAME", "")
    if not stack_name:
        raise ValueError("STACK_NAME environment variable is required")

    # Construct parameter names using hierarchical pattern
    param_prefix = f"/agentcore-mcp-gateway/{stack_name}/credentials/"
    param_names = [
        f"{param_prefix}client-id",
        f"{param_prefix}client-secret",
        f"{param_prefix}api-key-atlassian",
        f"{param_prefix}api-key-coralogix",
    ]

    try:
        # Use batch API to retrieve all parameters in single call
        print(f"Retrieving parameters from SSM: {param_names}")
        response = ssm.get_parameters(Names=param_names, WithDecryption=True)

        # Check for missing parameters
        if len(response["Parameters"]) != len(param_names):
            retrieved_names = {p["Name"] for p in response["Parameters"]}
            missing = set(param_names) - retrieved_names
            raise ValueError(
                f"Missing required parameters: {missing}. "
                f"Please ensure all parameters are created in Parameter Store."
            )

        # Extract parameter values and store in dictionary
        params = {}
        for param in response["Parameters"]:
            name = param["Name"]
            value = param["Value"]

            # Validate parameters are not placeholder values
            if value == "PLACEHOLDER_REPLACE_ME":
                raise ValueError(
                    f"Parameter {name} contains placeholder value. "
                    f"Please update with actual credential in Parameter Store."
                )

            # Map parameter names to simpler keys
            if name.endswith("client-id"):
                params["client_id"] = value
            elif name.endswith("client-secret"):
                params["client_secret"] = value
            elif name.endswith("api-key-atlassian"):
                params["api_key_atlassian"] = value
            elif name.endswith("api-key-coralogix"):
                params["api_key_coralogix"] = value

        print(f"Successfully retrieved {len(params)} parameters from SSM")
        return params

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))
        print(f"SSM API error: {error_code} - {error_message}")

        if error_code == "AccessDeniedException":
            raise ValueError(
                f"Lambda does not have permission to access Parameter Store. "
                f"Error: {error_message}"
            ) from e
        elif error_code == "ThrottlingException":
            raise ValueError(
                f"Parameter Store API throttled. Please retry. Error: {error_message}"
            ) from e
        else:
            raise ValueError(
                f"Failed to retrieve parameters from SSM: {error_code} - {error_message}"
            ) from e
    except Exception as e:
        print(f"Unexpected error retrieving parameters: {str(e)}")
        raise ValueError(
            f"Failed to initialize credentials from Parameter Store: {str(e)}"
        ) from e


# Initialize credentials at module level (before handler)
# This ensures parameters are retrieved once per Lambda container lifecycle
try:
    CACHED_PARAMS = get_parameters_from_ssm()
except Exception as e:
    # Fail fast on initialization if credentials unavailable
    print(f"FATAL: Failed to initialize Lambda - {str(e)}")
    raise

# Extract credentials from cache for convenience
CLIENT_ID = CACHED_PARAMS["client_id"]
CLIENT_SECRET = CACHED_PARAMS["client_secret"]


def lambda_handler(event, context):
    """Main Lambda handler - routes requests based on path."""
    path = event.get("rawPath", event.get("path", "/"))
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    print(event)
    if method == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": {"Allow": "OPTIONS, GET, POST"},
        }
    # Route to appropriate handler
    if path.startswith("/.well-known/oauth-authorization-server"):
        return handle_oauth_metadata(event)
    elif path == "/token" and method == "POST":
        return handle_token(event)
    else:
        return {
            "statusCode": 404,
        }


def handle_oauth_metadata(event):
    """Serve OAuth Authorization Server Metadata (RFC 8414)."""
    api_url = get_api_url(event)

    metadata = {
        "issuer": api_url,
        "token_endpoint": f"{api_url}/token",
        "scopes_supported": ["openid", "profile", "email"],
        "grant_types_supported": ["client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
    }

    return json_response(200, metadata)


def handle_token(event):
    """Proxy token requests to Cognito with redirect_uri rewriting."""
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()

    params = dict(urllib.parse.parse_qsl(body))
    if params["client_id"] != CLIENT_ID or params["client_secret"] != CLIENT_SECRET:
        return {
            "statusCode": 401,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "invalid_client"}),
        }
    provider = params["provider"]
    # Retrieve provider-specific API key from cached parameters
    api_key_name = f"api_key_{provider.lower()}"
    api_key = CACHED_PARAMS.get(api_key_name, "MOCK")
    return json_response(200, {"access_token": api_key})


def get_api_url(event):
    """Extract API Gateway URL from event."""
    ctx = event.get("requestContext", {})
    domain = ctx.get("domainName", "")
    stage = ctx.get("stage", "")
    if domain and stage and stage != "$default":
        return f"https://{domain}/{stage}"
    elif domain:
        return f"https://{domain}"
    return "http://localhost"


def json_response(status_code, body):
    """Create JSON response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
