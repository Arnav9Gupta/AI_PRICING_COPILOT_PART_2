from fastapi import FastAPI
from pydantic import BaseModel

from chatbot import understand_question, execute_query

app = FastAPI(title="AI Pricing Copilot")


class ChatRequest(BaseModel):
    question: str


@app.get("/")
def home():
    return {
        "message": "AI Pricing Copilot backend is running"
    }


@app.post("/chat")
def chat(request: ChatRequest):

    query = understand_question(request.question)

    result = execute_query(query)

    return {
        "question": request.question,
        "result": result
    }