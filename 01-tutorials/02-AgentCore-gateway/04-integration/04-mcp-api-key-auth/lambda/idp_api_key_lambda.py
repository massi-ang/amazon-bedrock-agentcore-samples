"""
MCP OAuth Proxy Lambda - Handles OAuth metadata, callback interception, token proxying, and MCP forwarding.

This Lambda function replaces the local mcp_oauth_proxy.py script, enabling serverless deployment.
"""

import json
import os
import base64
import urllib.parse

CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")


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

    return json_response(200, {"access_token": os.environ.get("API_KEY", "MOCK")})


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
