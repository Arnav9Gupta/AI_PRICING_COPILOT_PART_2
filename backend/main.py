
"""
backend/main.py

Pricing Copilot - FastAPI Backend
Version 10: Financial Report Generation

Features:
- HTTP Basic authentication
- Admin-only SQL dataset access
- Admin-only legacy SQL chat
- Unified SQL + document agent
- Document upload, download and deletion
- Finance document question answering
- Role-based API protection
- Dynamic visualization API support
- Financial PDF report generation
- Admin-only report downloads

IMPORTANT:
SQL permissions must also be enforced in
data_engine.py, chatbot.py and report_generator.py.
"""

import logging
import os

from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    Query,
)

from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import (
    FileResponse,
    StreamingResponse,
)

from fastapi.security import (
    HTTPBasic,
    HTTPBasicCredentials,
)

from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field

from backend import (
    agent,
    chatbot,
    documents,
    data_engine,
    report_generator,
)

from backend.data_engine import (
    DATASETS,
    allowed_datasets,
    allowed_departments,
    authenticate,
    get_load_errors,
    record_count,
)

from backend.database import (
    DatabaseConfigurationError,
    DatabaseUnavailableError,
    QueryLimitError,
)


# ============================================================
# 1. APPLICATION CONFIGURATION
# ============================================================

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

FRONTEND_DIR = BASE_DIR / "frontend"

ASSET_DIR = FRONTEND_DIR / "assets"

app = FastAPI(
    title="AI Pricing Copilot",
    version="10.0.0",
    description=(
        "Unified Financial Intelligence Platform "
        "with SQL analytics, document intelligence, "
        "dynamic visualization and PDF reporting."
    ),
)


# ============================================================
# 2. STATIC FRONTEND
# ============================================================

if ASSET_DIR.is_dir():

    app.mount(
        "/assets",
        StaticFiles(
            directory=str(ASSET_DIR)
        ),
        name="assets",
    )


# ============================================================
# 3. CORS CONFIGURATION
# ============================================================

allowed_origins = [
    item.strip()
    for item in os.getenv(
        "FRONTEND_ORIGINS",
        (
            "http://localhost:5173,"
            "http://127.0.0.1:5173"
        ),
    ).split(",")
    if item.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=[
        "GET",
        "POST",
        "DELETE",
    ],
    allow_headers=[
        "Authorization",
        "Content-Type",
    ],
)


# ============================================================
# 4. AUTHENTICATION
# ============================================================

security = HTTPBasic()


def current_user(
    credentials: HTTPBasicCredentials = Depends(
        security
    ),
) -> dict:
    """
    Authenticate users using the existing
    data_engine authentication configuration.
    """

    user = authenticate(
        credentials.username,
        credentials.password,
    )

    if user is None:

        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password.",
            headers={
                "WWW-Authenticate": "Basic"
            },
        )

    return user


# ============================================================
# 5. ADMIN-ONLY ACCESS CONTROL
# ============================================================

def require_admin(
    user: dict = Depends(current_user),
) -> dict:
    """
    Only Admin users can access SQL datasets
    and generate financial reports.
    """

    if user.get("role") != "Admin":

        raise HTTPException(
            status_code=403,
            detail=(
                "Access denied. SQL datasets and "
                "financial reports are available "
                "only to administrators."
            ),
        )

    return user


def is_admin(user: dict) -> bool:
    """
    Return whether the authenticated user
    has the Admin role.
    """

    return user.get("role") == "Admin"


# ============================================================
# 6. REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):

    question: str = Field(
        min_length=1,
        max_length=4000,
    )

    dataset: Optional[str] = None

    last_query: Optional[
        Dict[str, Any]
    ] = None

    explain: bool = True


class DocumentChatRequest(BaseModel):

    question: str = Field(
        min_length=1,
        max_length=4000,
    )

    history: List[
        Dict[str, str]
    ] = Field(
        default_factory=list,
        max_length=20,
    )


class AgentChatRequest(BaseModel):

    dataset: Optional[str] = None

    document_id: Optional[str] = None

    question: str = Field(
        min_length=1,
        max_length=4000,
    )

    history: List[
        Dict[str, str]
    ] = Field(
        default_factory=list,
        max_length=20,
    )

    last_query: Optional[
        Dict[str, Any]
    ] = None


# ============================================================
# 7. FINANCIAL REPORT REQUEST MODEL
# ============================================================

class FinancialReportRequest(BaseModel):
    """
    Request for financial PDF generation.

    Supported datasets:
    - payroll
    - sales
    - procurement
    - transactions

    Financial totals are never accepted
    from the frontend.
    """

    dataset: str = Field(
        min_length=1,
        max_length=40,
    )

    filters: Dict[str, Any] = Field(
        default_factory=dict
    )


# ============================================================
# 8. HELPER FUNCTIONS
# ============================================================

def _checked_question(question: str) -> str:

    value = question.strip()

    if not value:

        raise HTTPException(
            status_code=422,
            detail="Enter a question.",
        )

    return value


def _safe_history(
    history: List[Dict[str, str]],
) -> List[Dict[str, str]]:

    return [
        {
            "role": item["role"],
            "content": item["content"][:1000],
        }
        for item in history[-6:]
        if item.get("role") in {
            "user",
            "assistant",
        }
        and isinstance(
            item.get("content"),
            str,
        )
        and item["content"].strip()
    ]


def _json(data: Any) -> Any:

    return jsonable_encoder(
        data,
        custom_encoder={
            Decimal: str
        },
    )


def _badge(
    status: str,
    *,
    error: Any = None,
    route: str = "sql",
) -> Dict[str, str]:

    if error or status in {
        "No answer",
        "Not verified",
        "",
    }:

        return {
            "verification_badge": "not_verified",
            "verification_label": "Not Verified",
        }

    if (
        route == "sql"
        and status == "Verified"
    ):

        return {
            "verification_badge": "source_verified",
            "verification_label": "Source Verified",
        }

    return {
        "verification_badge": "source_referenced",
        "verification_label": "Source Referenced",
    }


def _service_error(
    exc: Exception,
) -> HTTPException:

    if isinstance(exc, HTTPException):
        return exc

    if isinstance(
        exc,
        (
            DatabaseConfigurationError,
            DatabaseUnavailableError,
        ),
    ):

        return HTTPException(
            status_code=503,
            detail=str(exc),
        )

    if isinstance(
        exc,
        QueryLimitError,
    ):

        return HTTPException(
            status_code=422,
            detail=str(exc),
        )

    if isinstance(
        exc,
        PermissionError,
    ):

        return HTTPException(
            status_code=403,
            detail=str(exc),
        )

    if isinstance(
        exc,
        (
            documents.DocumentError,
            agent.AgentError,
            ValueError,
        ),
    ):

        return HTTPException(
            status_code=400,
            detail=str(exc),
        )

    logger.exception(
        "Unexpected API error"
    )

    return HTTPException(
        status_code=500,
        detail=(
            "An unexpected server error occurred."
        ),
    )


# ============================================================
# 9. HOME
# ============================================================

@app.get("/")
def home():

    index_file = (
        FRONTEND_DIR / "index.html"
    )

    if index_file.is_file():

        return FileResponse(
            str(index_file)
        )

    return {
        "message": (
            "AI Pricing Copilot API is running"
        ),
        "health": "/health",
    }


# ============================================================
# 10. HEALTH CHECK
# ============================================================

@app.get("/health")
def health():

    errors = get_load_errors()

    return {
        "status": (
            "ready"
            if not errors
            else "setup_required"
        ),
        "database_ready": not bool(errors),
        "errors": errors,
    }


# ============================================================
# 11. CURRENT USER INFORMATION
# ============================================================

@app.get("/auth/me")
def signed_in_user(
    user: dict = Depends(current_user),
):

    admin = is_admin(user)

    return {
        "username": user["username"],
        "name": user["name"],
        "role": user["role"],

        "datasets": (
            allowed_datasets(user)
            if admin
            else []
        ),

        "can_access_sql": admin,
        "can_access_documents": True,
        "can_generate_reports": admin,
        "can_view_financial_charts": admin,
    }


# ============================================================
# 12. DATASET LIST - ADMIN ONLY
# ============================================================

@app.get("/datasets")
def datasets(
    user: dict = Depends(require_admin),
):
    """
    Only Admin can list dataset names,
    schemas, and record counts.
    """

    try:

        hidden = {
            str(col).strip().lower()
            for col in user.get(
                "hidden_columns",
                [],
            )
        }

        return [
            {
                "key": key,

                "label": DATASETS[key]["label"],

                "description": DATASETS[key][
                    "description"
                ],

                "records": record_count(
                    key,
                    allowed_departments(user),
                ),

                "columns": [
                    col
                    for col in DATASETS[key]["columns"]
                    if col.strip().lower()
                    not in hidden
                ],
            }

            for key in allowed_datasets(user)
        ]

    except Exception as exc:

        raise _service_error(exc) from exc


# ============================================================
# 13. DATASET RECORDS - ADMIN ONLY
# ============================================================

@app.get(
    "/datasets/{dataset_key}/records"
)
def dataset_records(
    dataset_key: str,

    page: int = Query(
        default=1,
        ge=1,
        le=100000,
    ),

    page_size: int = Query(
        default=50,
        ge=1,
        le=100,
    ),

    user: dict = Depends(require_admin),
):
    """
    Only Admin can access SQL rows
    and dataset schemas.
    """

    if dataset_key not in DATASETS:

        raise HTTPException(
            status_code=404,
            detail="Dataset not found.",
        )

    if dataset_key not in allowed_datasets(user):

        raise HTTPException(
            status_code=403,
            detail="Dataset access denied.",
        )

    try:

        result = data_engine.paginated_records(
            dataset_key,
            user,
            page=page,
            page_size=page_size,
        )

        return _json(result)

    except Exception as exc:

        raise _service_error(exc) from exc


# ============================================================
# 14. LEGACY SQL CHAT - ADMIN ONLY
# ============================================================

@app.post("/chat")
def chat(
    request: ChatRequest,
    user: dict = Depends(require_admin),
):
    """
    Legacy SQL chat endpoint.

    Non-Admin users receive HTTP 403.
    """

    try:

        question = _checked_question(
            request.question
        )

        query = chatbot.understand_question(
            question,
            last_query=request.last_query,
            dataset=request.dataset,
            user=user,
        )

        result = chatbot.execute_query(
            query,
            user,
        )

        explanation = (
            chatbot.explain_result(
                question,
                query,
                result,
            )
            if request.explain
            else ""
        )

        suggestions = chatbot.suggest_followups(
            query,
            result,
            user,
        ) if not result.get("error") else []

        result.update(
            _badge(
                result.get(
                    "reliability_status",
                    "",
                ),
                error=result.get("error"),
                route="sql",
            )
        )

        return _json({
            "question": question,
            "dataset": result.get("dataset"),
            "query": query,
            "result": result,
            "answer": result.get(
                "message",
                "",
            ),
            "explanation": explanation,
            "suggestions": suggestions,
            "verification_badge": result[
                "verification_badge"
            ],
            "verification_label": result[
                "verification_label"
            ],
        })

    except Exception as exc:

        raise _service_error(exc) from exc


# ============================================================
# 15. UNIFIED AGENT CHAT
# ============================================================

@app.post("/agent/chat")
def agent_chat(
    request: AgentChatRequest,
    user: dict = Depends(current_user),
):
    """
    Unified SQL + Document + Advisory Chat.

    Admin:
        SQL + Documents + Combined analysis

    Non-Admin:
        Authorized document questions only

    IMPORTANT:
    Underlying data permissions remain
    enforced in data_engine and chatbot.
    """

    try:

        question = _checked_question(
            request.question
        )

        admin = is_admin(user)

        if not admin:

            if request.dataset:

                raise HTTPException(
                    status_code=403,
                    detail=(
                        "SQL dataset access is "
                        "restricted to administrators."
                    ),
                )

            if request.last_query:

                raise HTTPException(
                    status_code=403,
                    detail=(
                        "SQL conversation context is "
                        "restricted to administrators."
                    ),
                )

            # The agent's semantic scope classifier
            # is a routing hint, not authorization.
            # Data access is enforced separately.

            classification = agent.classify_scope(
                question,
                history=_safe_history(
                    request.history
                ),
            )

            if classification.get("route") in {
                "sql",
                "both",
            }:

                raise HTTPException(
                    status_code=403,
                    detail=(
                        "You do not have permission "
                        "to query financial SQL datasets. "
                        "You can still ask questions "
                        "about your permitted documents."
                    ),
                )

        response = agent.answer_question(
            question,
            user,
            history=_safe_history(
                request.history
            ),
            last_query=(
                request.last_query
                if admin
                else None
            ),
            dataset=(
                request.dataset
                if admin
                else None
            ),
            document_id=request.document_id,
        )

        if (
            not admin
            and response.get("route") in {
                "sql",
                "both",
            }
        ):

            raise HTTPException(
                status_code=403,
                detail=(
                    "SQL access is restricted "
                    "to administrators."
                ),
            )

        # Non-Admin users must not receive
        # financial chart specifications.
        if not admin:
            response.pop(
                "visualization",
                None
            )

            response.pop(
                "chart",
                None
            )

            if isinstance(
                response.get("result"),
                dict,
            ):
                response["result"].pop(
                    "visualization",
                    None
                )

        return _json(response)

    except HTTPException:
        raise

    except Exception as exc:

        raise _service_error(exc) from exc


# ============================================================
# 16. FINANCIAL PDF REPORT GENERATION - ADMIN ONLY
# ============================================================

@app.post("/reports/generate")
def generate_financial_report(
    request: FinancialReportRequest,
    user: dict = Depends(require_admin),
):
    """
    Generate downloadable financial PDF reports.

    Supported datasets:
    - HR Payroll
    - Sales Orders
    - Procurement Orders
    - Transactions

    Security:
    - Admin-only authentication
    - Dataset authorization
    - Existing financial filter validation
    - Deterministic SQL calculations
    - No client-supplied financial totals
    """

    dataset = request.dataset.strip().lower()

    # --------------------------------------------------
    # VALIDATE DATASET
    # --------------------------------------------------

    supported = {
        "payroll",
        "sales",
        "procurement",
        "transactions",
    }

    if dataset not in supported:

        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported financial dataset. "
                "Choose payroll, sales, procurement "
                "or transactions."
            ),
        )

    # --------------------------------------------------
    # ENFORCE DATASET PERMISSIONS
    # --------------------------------------------------

    if dataset not in allowed_datasets(user):

        raise HTTPException(
            status_code=403,
            detail=(
                "You are not authorized to generate "
                "a report for this dataset."
            ),
        )

    # --------------------------------------------------
    # GENERATE PDF
    # --------------------------------------------------

    try:

        pdf_bytes = (
            report_generator.generate_financial_pdf(
                user=user,
                dataset=dataset,
                filters=request.filters,
            )
        )

        if (
            not isinstance(pdf_bytes, bytes)
            or not pdf_bytes.startswith(b"%PDF-")
        ):

            raise RuntimeError(
                "Report generator returned an invalid PDF."
            )

        # --------------------------------------------------
        # RETURN DOWNLOADABLE PDF
        # --------------------------------------------------

        filename = (
            f"{dataset}_financial_report.pdf"
        )

        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{filename}"'
                ),
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    except HTTPException:
        raise

    except Exception as exc:

        raise _service_error(exc) from exc


# ============================================================
# 17. DOCUMENT UPLOAD
# ============================================================

@app.post(
    "/documents",
    status_code=201,
)
async def upload_document(
    file: UploadFile = File(...),

    department: Optional[str] = Form(
        None
    ),

    user: dict = Depends(current_user),
):
    """
    Upload an authorized PDF/DOCX document.

    Finance-domain validation is enforced
    by the document ingestion service.
    """

    try:

        content = await file.read(
            documents.MAX_DOCUMENT_BYTES + 1
        )

        return _json(
            documents.ingest_document(
                file.filename,
                content,
                user,
                department=department,
            )
        )

    except Exception as exc:

        raise _service_error(exc) from exc

    finally:

        await file.close()


# ============================================================
# 18. DOCUMENT LIBRARY
# ============================================================

@app.get("/documents")
def list_user_documents(
    user: dict = Depends(current_user),
):

    try:

        return _json(
            documents.list_documents(user)
        )

    except Exception as exc:

        raise _service_error(exc) from exc


# ============================================================
# 19. DOCUMENT DOWNLOAD
# ============================================================

@app.get(
    "/documents/{document_id}/file"
)
def download_document(
    document_id: str,
    user: dict = Depends(current_user),
):

    try:

        name, path = documents.get_document_file(
            user,
            document_id,
        )

        return FileResponse(
            str(path),
            filename=name,
        )

    except documents.DocumentError as exc:

        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except Exception as exc:

        raise _service_error(exc) from exc


# ============================================================
# 20. DOCUMENT DELETE
# ============================================================

@app.delete(
    "/documents/{document_id}"
)
def delete_document(
    document_id: str,
    user: dict = Depends(current_user),
):
    """
    Only permitted owners or administrators
    can delete documents.
    """

    try:

        return _json(
            documents.delete_document(
                user,
                document_id,
            )
        )

    except documents.DocumentError as exc:

        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except Exception as exc:

        raise _service_error(exc) from exc


# ============================================================
# 21. DOCUMENT CHAT
# ============================================================

@app.post("/documents/chat")
def document_chat(
    request: DocumentChatRequest,
    user: dict = Depends(current_user),
):
    """
    Document-only question answering.

    Analysts may use this endpoint for
    authorized finance documents.
    """

    try:

        question = _checked_question(
            request.question
        )

        result = documents.answer_document_question(
            question,
            user,
            history=_safe_history(
                request.history
            ),
        )

        result.update(
            _badge(
                result.get(
                    "reliability_status",
                    "",
                ),
                error=(
                    None
                    if result.get(
                        "reliability_status"
                    ) != "No answer"
                    else "no_answer"
                ),
                route="documents",
            )
        )

        return _json(result)

    except Exception as exc:

        raise _service_error(exc) from exc


# ============================================================
# END OF MAIN.PY VERSION 10
# ============================================================
