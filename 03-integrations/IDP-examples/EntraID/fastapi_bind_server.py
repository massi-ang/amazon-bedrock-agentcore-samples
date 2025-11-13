from fastapi import FastAPI, Query
import uvicorn
from bedrock_agentcore.services.identity import IdentityClient, UserIdIdentifier
import boto3
import json

app = FastAPI()

ic = IdentityClient(region=boto3.Session().region_name)
with open(".agentcore.json") as f:
    conf = json.load(f)
print(conf)

ic.update_workload_identity(
    conf["workload_identity_name"], ["http://localhost:8000/bind"]
)


@app.get("/bind")
async def bind_session(
    session_id: str = Query(..., description="Session ID parameter")
):
    """
    GET endpoint that extracts session_id from query parameters and prints it.

    Example: GET /bind?session_id=abc123
    """
    print(f"Session ID: {session_id}")
    try:
        ic.complete_resource_token_auth(
            session_uri=session_id,
            user_identifier=UserIdIdentifier(user_id=conf["user_id"]),
        )
        return {"user_id": conf["user_id"], "session_id": session_id}
    except Exception as e:
        print(e)

        return {"error": str(e), "user_id": conf["user_id"], "session_id": session_id}


if __name__ == "__main__":
    # Run the server on port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)
