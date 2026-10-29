# AI Pricing Copilot

AI Pricing Copilot is a local web application with a FastAPI backend and a
non-Streamlit frontend. It answers questions using four structured financial
datasets and permission-filtered PDF/DOCX documents. The Stage 3 router chooses
the SQL workflow, the document workflow, both, clarification, or no-answer.

SQLite stores the financial data, document metadata, and embeddings in one
local database file. Financial calculations are deterministic. The LLM only
receives approved schema descriptions for planning and the minimum evidence
needed to explain an answer; it does not receive full datasets or raw financial
records.

## Database

The default database file is `Data/financial_chatbot.sqlite3`. Override its
location with `SQLITE_DATABASE_PATH` in `.env`. The database is embedded in the
app process: it does not require a database server, service, username, or
database password.

The initializer creates four allowlisted financial tables:

- `transactions`: transaction IDs/dates, department, category, type, amount,
  budget, vendor/customer, payment status, and currency.
- `procurement_orders`: purchase orders, suppliers, items, warehouses,
  quantities, costs, delivery dates/status, delay days, and currency.
- `payroll`: employee/department details, pay dates, salary components,
  gross/net pay, budget, payment status, and currency.
- `sales_orders`: sales orders, region, customer/product details, quantity,
  unit price, discounts, order/target values, status, and payment method.

Document metadata and chunks are stored in SQLite; original files are stored
under `document_storage/` by default. SQLite FTS5 supports text search, and
local FastEmbed vectors are used for semantic similarity ranking.

## Setup

1. (Optional) Copy `.env.example` to `.env`. The default database path works
   without additional configuration. Set `SQLITE_DATABASE_PATH` only if you
   want the database file in a different location. Keep `.env` private.
2. Install Python dependencies:

   ```powershell
   .\venv\Scripts\python.exe -m pip install -r requirement.txt
   ```

3. Initialize the SQLite schema:

   ```powershell
   .\venv\Scripts\python.exe -m backend.initialize_database
   ```

4. To explicitly import the workbook data once, use:

   ```powershell
   .\venv\Scripts\python.exe -m backend.initialize_database --seed-from-excel
   ```

   This validates each workbook against the approved schema and replaces the
   rows in the four financial tables in one transaction. It is an explicit
   import; workbooks are not read during normal chatbot operation. If the
   workbooks are not an approved business source, do not run this option.

## Run

Start the API and web frontend:

```powershell
.\venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

Open `http://localhost:8000/`. The API health endpoint is `/health`. The
frontend and API use the same HTTP Basic authentication and local role
configuration. Set the role passwords and `AUTH_SALT` in `.env` before
sharing the application. `GROQ_API_KEY` is optional; without it, deterministic
question routing and supported financial query fallbacks remain available.

## Document chat

The **Documents** tab supports authorized PDF and Word `.docx` uploads,
questions with source citations, and permitted original-file downloads. The
corresponding APIs are `POST /documents`, `GET /documents`,
`GET /documents/{id}/file`, and `POST /documents/chat`.

- Parsing and embeddings run locally. FastEmbed uses the ONNX
  `sentence-transformers/all-MiniLM-L6-v2` model on CPU. Model files download
  on first use and are cached locally.
- Uploads are limited to 20 MB and accepted only for PDF and DOCX. Failed
  documents are excluded from search.
- Documents are private to their uploader by default. Administrators may
  share documents with a department; searches and downloads enforce the
  signed-in user's document permissions.
- PDF page and DOCX section citations are returned with retrieved passages.
  Only relevant passages are sent to Groq when answer generation is enabled.
- Scanned PDFs without extractable text require OCR before upload; OCR is not
  configured in this project.
- `DOCUMENT_STORAGE_DIR` can be set to a restricted location for original
  files. The default `document_storage/` directory is excluded from Git.

## Unified chat

The **Unified Chat** tab and `POST /agent/chat` use the Stage 3 router:

- **SQL** runs allowlisted, parameterized queries and deterministic
  calculations on authorized financial records.
- **Documents** searches only files the signed-in user may access.
- **Both** uses verified aggregate financial metrics and cited document
  passages; it does not pass matching record dumps to the synthesis model.
- **Clarify / no answer** asks for necessary context or declines unsupported
  questions instead of guessing.

The legacy `POST /chat` endpoint remains SQL-only for compatibility. Department
access is enforced in database queries, and hidden columns are excluded from
planning and results. SQLite is a local single-file database and does not
provide PostgreSQL-style database roles; use operating-system file permissions
to protect the database and storage directories.
