"""
main.py - FastAPI backend for AI Pricing Copilot.

Run from the project folder (the one that contains the 'backend' and 'Data'
folders):

    uvicorn main:app --reload

Every call to /chat needs HTTP Basic credentials (same users as the app), so
dataset, department and column permissions are enforced for API users too.
"""

from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from backend.chatbot import understand_question, execute_query, explain_result
from backend.data_engine import (
    authenticate,
    allowed_datasets,
    get_data,
    DATASETS,
    LOAD_ERRORS,
)

app = FastAPI(title="AI Pricing Copilot")

security = HTTPBasic()


def current_user(credentials: HTTPBasicCredentials = Depends(security)):

    user = authenticate(credentials.username, credentials.password)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )

    return user


class ChatRequest(BaseModel):
    question: str
    # "transactions", "procurement", "payroll" or "sales".
    # Leave empty to let the question decide.
    dataset: Optional[str] = None
    # Send back the "query" from the previous reply to ask a follow-up.
    last_query: Optional[Dict[str, Any]] = None
    # Set to false to skip the extra language-model call for the explanation.
    explain: bool = True


@app.get("/")
def home():
    return {
        "message": "AI Pricing Copilot backend is running",
        "datasets_loaded": list(DATASETS),
        "datasets_failed": LOAD_ERRORS,
    }


@app.get("/datasets")
def datasets(user: dict = Depends(current_user)):
    """The datasets the signed-in user may use."""

    return [
        {
            "key": key,
            "label": DATASETS[key]["label"],
            "description": DATASETS[key]["description"],
            "records": len(get_data(key)),
            "columns": [
                c for c in DATASETS[key]["columns"]
                if c.lower() not in {h.lower() for h in user["hidden_columns"]}
            ],
        }
        for key in allowed_datasets(user)
    ]


@app.post("/chat")
def chat(request: ChatRequest, user: dict = Depends(current_user)):

    query = understand_question(
        request.question,
        last_query=request.last_query,
        dataset=request.dataset,
        user=user,
    )

    result = execute_query(query, user)

    explanation = (
        explain_result(request.question, query, result)
        if request.explain else ""
    )

    return {
        "question": request.question,
        "dataset": result.get("dataset"),
        "query": query,
        "result": result,
        "explanation": explanation,
    }
