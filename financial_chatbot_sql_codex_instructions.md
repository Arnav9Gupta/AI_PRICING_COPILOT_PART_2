# Codex Instructions: Convert the Existing Chatbot into a SQL-Backed Financial Chatbot

## Architecture roadmap from the PowerPoint

The architecture is organized into three stages that reuse a shared
foundation:

1.  **Stage 1 --- Structured Data Chatbot:** query structured financial
    data, calculate values in code/SQL, and enforce role-based access.
2.  **Stage 2 --- Unstructured Document Chatbot:** answer from permitted
    PDF/Word documents with page citations.
3.  **Stage 3 --- Unified Multi-Tool Agent Chatbot:** route each
    question to the SQL tool, document tool, both tools, a clarification
    path, or a no-answer path.

Shared foundation: web chat, API, login/roles/limits, chat context,
configurable LLM gateway, answer verification/reliability status,
follow-up questions, and appropriate logging/monitoring.

The PowerPoint describes a web app but does not explicitly name React.
React is addressed below as the preferred frontend only when it is
already used or the project owner wants it introduced. Preserve the
existing UI unless a React migration is explicitly intended.

## 1. Objective

Update the **existing chatbot project** into a financial chatbot that
answers questions from structured financial data stored in SQL tables.

The current project already has a working chatbot. **Extend and adapt
the existing implementation; do not rebuild the application from
scratch.** First inspect the existing code and preserve its current
working behavior wherever possible.

### Core requirements

-   Store structured financial data in a relational SQL database.
-   Use **PostgreSQL** as the target database, consistent with the
    project architecture.
-   Do not use Excel or CSV files as the runtime data source.
-   Do not pass complete datasets, table dumps, or raw database records
    into the LLM.
-   The application code must retrieve permitted data from SQL,
    calculate results using deterministic Python/SQL logic, and send
    only the minimum necessary query result or summary to the LLM when
    natural-language explanation is needed.
-   Keep the existing web app, backend structure, chat history behavior,
    and follow-up-question experience where practical.
-   Do not add document-chatbot/RAG functionality, deployment stages, or
    unrelated modules. Focus only on the SQL-backed financial chatbot.

## 2. Existing project structure

The current project appears to contain the following files and folders.
Verify the actual structure before editing.

``` text
AI_Pricing_CoPilot_Part_2/
├── __pycache__/
├── backend/
│   ├── __pycache__/
│   ├── chatbot.py
│   ├── data_engine.py
│   └── main.py
├── Data/
│   └── query_history.json
├── venv/
├── .env
├── .gitignore
├── app.py
└── requirement.txt
```

The screenshot also shows existing Excel files such as
`hr_payroll_540.xlsx`, `procurement_orders_700.xlsx`,
`sales_orders_1000.xlsx`, and `synthetic_transactions_1000.xlsx`. These
are not to remain the chatbot's runtime data source. Do not read them
during normal chatbot operation.

### Responsibilities to preserve or clarify

-   `app.py`: existing user interface; adapt only as needed to display
    financial answers, source/query details, reliability status, and
    follow-up suggestions.
-   `backend/main.py`: API/application entry point and request
    coordination.
-   `backend/chatbot.py`: question understanding, SQL-query workflow
    coordination, answer wording, and follow-up suggestions.
-   `backend/data_engine.py`: database access, schema-aware query
    planning/validation, SQL execution, and deterministic calculations.
-   `Data/query_history.json`: preserve if the current app depends on
    it, but do not use it as a financial dataset. Prefer storing
    chat/query audit history in SQL if that can be added without
    breaking the existing app.
-   `requirement.txt`: add only dependencies that the implementation
    actually needs. Preserve the project's existing dependency
    conventions.

Do not assume these files currently have the responsibilities listed
above. Read their contents first and fit the implementation to the real
code.

## 3. Target architecture

Implement the following flow:

``` text
User
  |
  v
Existing UI (app.py)
  |
  v
Backend API (backend/main.py)
  |
  v
Chat workflow (backend/chatbot.py)
  |
  v
Question-to-query planner
  |
  v
Access control + SQL validation
  |
  v
PostgreSQL database
  |
  v
Deterministic SQL/Python aggregation and calculations
  |
  v
Small, relevant result set
  |
  v
LLM explanation (only when useful; never the source of financial facts)
  |
  v
Answer verification + reliability status + follow-up questions
  |
  v
Existing UI
```

The LLM must not connect directly to the database. The LLM must not
receive database credentials, unrestricted SQL execution capability, or
full tables.

## 4. SQL database requirements

### Database configuration

-   Read the PostgreSQL connection string from an environment variable
    such as `DATABASE_URL`.
-   Keep secrets in `.env` locally and ensure `.env` is excluded from
    version control.
-   Never hard-code database passwords or API keys.
-   Validate required configuration at startup and provide a clear, safe
    error if it is missing.
-   Use parameterized SQL queries for all user-derived values.
-   Use a connection pool or safe connection lifecycle appropriate to
    the current framework.
-   Use transactions for writes and database setup operations where
    needed.

### Schema discovery

The chatbot must know which tables and columns are available without
sending table contents to the LLM.

-   Maintain an explicit allowlist of tables and columns the chatbot may
    query.
-   Use schema metadata or a controlled schema description to understand
    available fields.
-   Do not expose internal, sensitive, or unauthorized columns to the
    question planner.
-   Do not invent table names or columns. If a requested metric cannot
    be supported by the actual schema, explain what is missing or ask a
    clarifying question.

### Initial financial data model

Inspect the existing chatbot and intended finance use cases before
finalizing the schema. Create only the tables required by the supported
questions. A possible starting design is:

-   `financial_transactions`: transaction ID, transaction date,
    account/category IDs, description, transaction type, amount,
    currency, status, and any required organization/tenant ID.
-   `accounts`: account ID, account name, account type, and
    organization/tenant ID.
-   `categories`: category ID, category name, and organization/tenant
    ID.
-   `budgets`: budget ID, period, category/account ID, planned amount,
    currency, and organization/tenant ID.
-   `users` or role/permission tables only if the current application
    does not already provide authentication and access control.
-   `chat_history` or `query_audit_log` only if useful and compatible
    with the existing implementation.

These are proposed logical tables, not confirmed existing schema. Adapt
them to the actual financial dataset and requirements. Avoid creating
duplicate tables or unrelated HR/procurement/sales modules unless those
are explicitly part of the financial chatbot's scope.

### Data loading

-   Financial records must be inserted into SQL tables using a
    controlled database initialization or ingestion script.
-   Do not load Excel or CSV data on every user question.
-   Do not build runtime logic that searches a `Data/` directory for
    spreadsheets.
-   If sample data is needed for development, generate synthetic records
    directly through SQL seed scripts or Python objects inserted into
    PostgreSQL.
-   Keep schema creation/migrations and optional seed data separate from
    chatbot request handling.
-   If an existing Excel/CSV dataset is the only source of financial
    records, do not silently depend on it. Ask for or create an
    explicitly approved SQL ingestion process, then use SQL tables at
    runtime.
-   Do not fabricate business facts or present synthetic sample records
    as real company data.

## 5. Natural-language-to-SQL workflow

For each user question:

1.  Read the current question and relevant conversation context.
2.  Identify the intent, requested metrics, time period, filters,
    grouping, and comparison.
3.  Check whether the user has supplied enough information. Ask one
    concise clarification if a material detail is missing.
4.  Select only allowlisted tables and columns.
5.  Build a structured query plan before generating SQL. The plan should
    identify the metric, dimensions, filters, date range, aggregation,
    and required permissions.
6.  Validate the plan against the known schema and the user's
    permissions.
7.  Generate or construct a read-only SQL query using an approved
    strategy.
8.  Validate the query before execution.
9.  Execute it with parameterized values and bounded resource usage.
10. Calculate financial metrics deterministically in SQL or Python, not
    by asking the LLM to do arithmetic.
11. Return only the small result needed to answer the question.
12. Produce the answer from that result, with the relevant date range,
    currency, units, filters, and calculation definition.
13. Verify that all numerical claims match the query result.
14. Return a reliability status and useful follow-up questions.

### Example questions the system may support when the schema contains the required data

-   "What were total expenses last month?"
-   "Compare revenue and expenses for the last three months."
-   "Which expense category had the highest spending this quarter?"
-   "How much did we spend on travel in September?"
-   "How does actual spending compare with budget?"
-   "Show the month-over-month change in expenses."
-   "What were the top five expense categories by amount?"

Do not claim these questions are supported until the required tables and
columns actually exist.

## 6. SQL safety and query controls

Treat user input and LLM-generated plans/SQL as untrusted.

-   Use a database account with read-only permissions for chatbot
    analytics.
-   Allow only approved `SELECT` queries for normal chat requests.
-   Reject `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`,
    permission changes, multi-statement SQL, and other
    write/administrative operations in the query path.
-   Prefer SQLAlchemy Core or another controlled query builder if it
    fits the existing codebase. If raw SQL is used, validate it and
    parameterize all values.
-   Do not rely only on a prompt telling the LLM to generate safe SQL.
-   Enforce table/column allowlists in application code.
-   Apply row limits, query timeouts, and reasonable limits on date
    ranges and result sizes.
-   Prevent access to system catalogs or tables outside the allowlist.
-   Avoid returning unnecessary personal or confidential financial
    details.
-   Record query metadata for audit/debugging without logging secrets or
    unnecessarily copying sensitive result rows.
-   Return a safe, understandable error when a query is rejected or
    fails. Do not expose database credentials, stack traces, or internal
    SQL details to end users.

## 7. Keep financial data out of the LLM

This is a strict requirement.

-   Do not put entire SQL tables, database exports, all transaction
    records, or large result sets into a prompt.
-   The LLM must not be the calculation engine.
-   Perform sums, averages, counts, ratios, rankings, period
    comparisons, and percentage changes in SQL/Python.
-   For simple requests, the backend should be able to return a
    deterministic answer without calling an LLM.
-   If an LLM is needed to interpret the question or explain the result,
    send only the minimum required information---for example, an
    approved schema summary, a validated query plan, and a small
    aggregate result.
-   Never send database credentials or connection strings to the LLM.
-   Do not use embeddings or vector search for structured financial
    tables.
-   Do not use the LLM's prior knowledge to fill missing financial
    values.
-   If no rows match, clearly say that no matching records were found.
    Do not invent a value.
-   Clearly distinguish zero from missing, unavailable, or null values.

## 8. Financial calculation and answer quality

-   Define metrics consistently. For example, revenue, expenses, profit,
    cash flow, and budget variance must have explicit definitions based
    on available fields.
-   Do not assume that every positive transaction is revenue or every
    negative transaction is an expense unless the actual data model
    establishes that rule.
-   Preserve decimal precision for financial calculations. Avoid binary
    floating-point errors for currency where possible; use PostgreSQL
    `NUMERIC` and Python `Decimal` when appropriate.
-   Keep currency codes with amounts. Do not combine currencies unless a
    valid conversion rule and exchange-rate source exist.
-   Make date boundaries and time zones explicit where relevant.
-   For percentage change, handle a zero or missing baseline safely.
-   For budget variance, document whether positive variance means over
    budget or under budget.
-   Show the period and filters used so the user can understand what the
    number represents.
-   Never state that a result is verified unless the underlying query
    succeeded and the displayed numbers match its result.

## 9. Access control and multi-tenancy

If the current application has users, roles, departments, or tenants:

-   Enforce authorization before executing any query.
-   Apply row-level scope such as `tenant_id` or permitted account scope
    in the backend, not through prompt instructions alone.
-   Never trust a user-provided tenant or role value without validating
    it against the authenticated session.
-   Do not allow a user to bypass restrictions by changing the wording
    of a question.
-   Add tests proving that one user cannot access another user's
    financial records.

If authentication is not implemented yet, do not pretend role-based
access is active. Document the current limitation and implement a
simple, secure boundary appropriate to the existing application before
exposing sensitive financial data.

## 10. Reliability status and follow-up questions

Retain or adapt the existing architecture's reliability indicator and
follow-up behavior if already implemented.

Suggested statuses:

-   **Verified**: the answer is supported by a successful, authorized
    SQL result, and its numerical claims match the result.
-   **Partially verified**: the answer is supported in part, but a
    clearly stated limitation or assumption remains.
-   **Not verified**: the system cannot establish adequate support for
    the answer.
-   **No answer**: the request is outside the supported scope,
    permissions deny access, or the necessary data is unavailable.

Do not mark an answer verified just because the LLM sounds confident.

Follow-up suggestions must be grounded in available schema and permitted
data. Examples: "Break this down by category" or "Compare with the
previous month." Do not suggest a follow-up that the current schema
cannot answer.

## 11. Conversation context

-   Preserve the existing follow-up conversation flow.
-   Resolve references such as "compare it with last month" using the
    prior question and answer context.
-   Do not reuse a previous query's filters if the user has changed
    them.
-   Ask a clarification when a reference cannot be resolved reliably.
-   Store only the context needed for continuity and avoid storing
    sensitive financial records in the chat history.
-   If query history is persisted, store relevant metadata such as
    timestamp, user/session ID, normalized question, query plan ID,
    status, and execution outcome. Avoid saving secrets or unnecessary
    raw data.

## 12. Error handling and observability

Handle these cases explicitly:

-   Database unavailable or misconfigured.
-   Table or column missing.
-   Unsupported question or metric.
-   Ambiguous date range, currency, or metric definition.
-   Permission denied.
-   Invalid or unsafe query.
-   Query timeout or result limit reached.
-   Empty result.
-   LLM unavailable.
-   LLM response includes a number not supported by the result.
-   Invalid or malformed follow-up suggestion.

The application should still return a deterministic result when the LLM
is unavailable, whenever the question can be answered directly from SQL.

## 13. Tests and acceptance criteria

Add or update tests for the existing project. Use a test database or
isolated synthetic records.

### Required acceptance criteria

-   [ ] Existing UI and API still start and work after the changes.
-   [ ] Financial facts are retrieved from SQL tables, not Excel or CSV
    files.
-   [ ] Normal chatbot requests do not read spreadsheets from the
    `Data/` folder.
-   [ ] SQL credentials are read from environment configuration and are
    never sent to the LLM.
-   [ ] The LLM does not receive full tables or raw database dumps.
-   [ ] Aggregations and arithmetic are performed deterministically in
    SQL/Python.
-   [ ] SQL execution is restricted to approved tables and read-only
    operations.
-   [ ] User values are passed as parameters rather than interpolated
    into SQL.
-   [ ] Empty results are handled without fabricating data.
-   [ ] Answers include relevant time periods, units, and currency when
    available.
-   [ ] Unsupported or ambiguous questions trigger a clear clarification
    or limitation.
-   [ ] Numeric answers are checked against the SQL result before being
    marked verified.
-   [ ] Existing follow-up questions and query-history behavior remain
    functional or are migrated safely.
-   [ ] Access-control tests cover unauthorized records if users/tenants
    exist.
-   [ ] Setup instructions explain how to configure PostgreSQL and
    initialize the schema.

### Suggested test scenarios

1.  Ask for a total that can be checked against known synthetic SQL
    records.
2.  Ask for a monthly comparison and verify the arithmetic.
3.  Ask for a category breakdown.
4.  Ask a question requiring a column that does not exist.
5.  Try a prompt that requests all records or attempts SQL modification.
6.  Try an unauthorized account or tenant filter.
7.  Ask a question that returns no rows.
8.  Simulate a database connection failure.
9.  Simulate an LLM outage and verify that SQL-answerable questions
    still work.
10. Check that the answer's numerical values exactly match the executed
    result.

## 14. Implementation sequence for Codex

Work in this order and inspect the existing code before making changes:

1.  Inspect all current project files, dependencies, environment
    configuration, and existing request flow.
2.  Summarize the current implementation and identify the smallest
    changes required.
3.  Identify the current financial data source and the exact metrics the
    current chatbot supports.
4.  Define the PostgreSQL schema based on the real requirements and data
    fields; avoid inventing business fields.
5.  Add database configuration, schema/migration setup, and optional
    synthetic seed data.
6.  Refactor `backend/data_engine.py` to retrieve and calculate
    structured results from SQL.
7.  Refactor `backend/chatbot.py` to use the safe query workflow and
    keep the LLM away from full datasets.
8.  Update `backend/main.py` and `app.py` only where required to connect
    the existing interface to the new workflow.
9.  Preserve existing query history and follow-up behavior, migrating it
    only if necessary.
10. Add tests for SQL safety, calculations, empty results, permissions,
    and error cases.
11. Update `requirement.txt` and setup documentation.
12. Run the available tests and report the actual results. Do not claim
    tests passed if they were not run.

## 15. Important boundaries

-   Do not rebuild the chatbot from scratch.
-   Do not introduce Excel/CSV as the runtime data layer.
-   Do not pass the dataset to the LLM.
-   Do not add document ingestion, PDF/Word RAG, vector databases, or a
    multi-tool agent as part of this task.
-   Do not add deployment stages or unrelated product features.
-   Do not remove existing working functionality without explaining why
    and getting approval.
-   Do not assume the current `.env`, database, tables, or source code
    are configured a particular way; inspect them first.
-   Prefer small, maintainable changes and explain each significant
    change.

## 16. Frontend and React

The architecture requires a web app with chat, sources, reliability
status, and follow-up questions. The PowerPoint describes a web app but
does not explicitly mandate React. However, React is the preferred
frontend if a React frontend is already present or the project owner
explicitly wants the frontend in React.

-   Inspect the current UI implementation before changing it. The
    screenshot shows an `app.py` file, which may be a Python UI
    framework; do not assume it is React.
-   If the existing frontend is React, preserve its structure and extend
    it.
-   If the current frontend is Python-based and the user wants React,
    create a separate React frontend (for example, under `frontend/`)
    that calls the existing backend API. Do not silently replace a
    working UI without explaining the migration.
-   Keep backend business logic, SQL access, permissions, calculations,
    and LLM calls on the backend. Never connect the React browser
    directly to PostgreSQL or expose database credentials/API keys to
    the browser.
-   The React chat interface should support:
    -   chat messages and conversation context;
    -   loading, empty, error, and retry states;
    -   source/evidence details;
    -   a reliability badge/status (Verified, Partially verified, Not
        verified, or No answer);
    -   suggested follow-up question chips;
    -   a clarification question when the query is ambiguous;
    -   tabular results and optional chart/export actions only when
        supported by the existing application.
-   Use the project's existing styling and component conventions where
    possible. Avoid adding a large UI framework without need.
-   Document the frontend start command and backend API configuration if
    a separate React app is introduced.

## 17. Stage 2: Unstructured document chatbot

Add document question-answering for permitted PDF and Word documents,
without changing the Stage 1 SQL-only data workflow.

### Document ingestion pipeline

When an authorized document is uploaded:

1.  Validate file type, size, and access permissions.
2.  Store the original document in controlled file/object storage; store
    metadata and access rules in PostgreSQL.
3.  Extract text and page information from PDFs and Word documents. Use
    OCR only when scanned pages require it.
4.  Split extracted text into sensible chunks while retaining document
    ID, page number, section/heading, and chunk metadata.
5.  Create embeddings for chunks and store them in an approved vector
    index. PostgreSQL with a vector extension may be used if suitable;
    otherwise use a separately configured vector store. Do not select or
    add infrastructure without checking the current stack.
6.  Maintain document-level and chunk-level permissions in the
    index/metadata.
7.  Track ingestion status and errors so failed documents are not
    treated as searchable.

Excel and CSV are not the runtime data source for structured financial
facts. Do not route structured tables through document RAG. Do not add
Excel/CSV document ingestion unless explicitly requested and approved.

### Document question pipeline

For a question that needs document evidence:

1.  Rewrite the question using relevant chat context without changing
    its intent.
2.  Search only documents the authenticated user is permitted to access.
3.  Use hybrid retrieval (keyword plus vector search) if supported by
    the chosen stack.
4.  Rerank retrieved passages and keep only the most relevant few (the
    architecture suggests approximately 6--8; tune this based on token
    limits and evaluation).
5.  Ask the LLM to answer from those retrieved passages only.
6.  Cite the source document and page for every material claim when page
    information is available.
7.  Verify that cited passages support the answer. If evidence is
    insufficient, say so instead of guessing.
8.  Keep the original document available for authorized source viewing.

The LLM may receive relevant retrieved document passages, unlike the SQL
workflow, but only the minimum authorized passages needed for the
question. Never send every document or the entire document library to
the LLM.

## 18. Stage 3: Unified multi-tool agent

Implement an orchestrator that decides whether a question should use the
structured SQL tool, the document-search tool, both tools, or a
clarification/no-answer path.

### Routing behavior

-   **Structured-data question:** call the SQL data tool, which uses
    allowlisted PostgreSQL tables, parameterized read-only queries, and
    deterministic calculations.
-   **Document question:** call the document retrieval tool, which
    searches permitted PDF/Word passages and returns citations.
-   **Mixed question:** call both tools in a deliberate order, then
    merge their evidence without mixing unsupported assumptions into the
    answer.
-   **Ambiguous question:** ask one concise clarification before
    executing a tool.
-   **Out-of-scope or unsupported question:** return a clear no-answer
    response rather than inventing information.

The orchestrator must not bypass the permissions or validation of either
tool. The LLM must not have direct database access. The SQL tool must
never receive full tables as prompt context. The document tool must
enforce access filters before returning passages.

### Shared services

Reuse or implement shared services for:

-   API gateway/request validation and authentication;
-   role-based access and request limits;
-   chat context/memory limited to what is needed for follow-ups;
-   LLM gateway/configuration (the architecture mentions Groq or another
    provider; keep provider configurable);
-   answer verification and reliability status;
-   follow-up question generation and validation;
-   optional chart/export utilities if supported by the current
    application;
-   audit logging, error handling, caching, job queue, and monitoring
    where justified by the current project size.

Do not add all infrastructure as separate services automatically. Prefer
maintainable components within the existing application unless scale or
requirements justify separate services.

## 19. Shared answer verification and reliability status

Run checks in order:

1.  **Permission and validity check:** verify the user is allowed to
    access the result and that the query plan/evidence is valid.
2.  **Numerical and citation check:** for SQL answers, compare every
    displayed financial number with the actual SQL result; for document
    answers, check that claims are supported by retrieved passages and
    page citations.
3.  **Confidence/evidence check:** only mark an answer Verified when the
    evidence is sufficient and no unresolved assumption materially
    affects the result.

Use these outcomes: - **Verified:** supported by authorized SQL output
and/or cited document evidence, with numerical claims checked. -
**Partially verified:** some useful evidence exists but a clearly
described caveat or assumption remains. - **Not verified:** evidence is
missing or does not sufficiently support the answer. - **No answer:**
access is denied, the request is out of scope, or required data/evidence
is unavailable.

The architecture's slide mentions a score threshold of 85 and no open
caveat for a verified outcome. Treat 85 as a configurable design target,
not a proven calibration. Do not implement a misleading numeric
confidence score unless its scoring method is documented and tested. A
high LLM confidence score alone must never establish correctness.

## 20. Follow-up questions and chat context

After an answer:

-   Generate around 3--4 useful follow-up suggestions when possible.
-   Test each suggestion against the available tools, schema,
    permissions, and evidence before displaying it.
-   A clicked suggestion should reuse its validated plan where safe,
    while rechecking authorization and data freshness at execution time.
-   Support clarification options as buttons or typed responses.
-   Preserve conversation context for references such as "compare that
    with last month."
-   Do not carry over old filters when the user's new question changes
    them.
-   Do not treat generated follow-ups as pre-authorized to access data;
    always recheck permissions.

## 21. Platform operations and monitoring

Add proportionate platform support for:

-   structured application logs and request IDs;
-   audit events for access decisions and query outcomes;
-   database connection health and query timing;
-   document ingestion status, failures, and retries;
-   LLM/provider errors and latency;
-   cache only for results where user permissions and freshness are
    handled safely;
-   background jobs for document parsing/embedding if ingestion could
    block user requests;
-   rate limits and request-size limits.

Do not log secrets, complete prompts containing sensitive data, or
unnecessary raw financial/document content. Keep operational complexity
appropriate to this project.

## 22. Expanded end-to-end acceptance criteria

-   [ ] Stage 1 answers structured financial questions from PostgreSQL,
    not Excel/CSV runtime files.
-   [ ] SQL tables and raw financial records are not dumped into LLM
    prompts.
-   [ ] Financial calculations are deterministic and checked against
    query results.
-   [ ] Stage 2 ingests approved PDF/Word documents, preserves page
    metadata, and retrieves only permitted passages.
-   [ ] Document-based claims include document/page citations where page
    information is available.
-   [ ] Stage 1 and Stage 2 enforce authorization independently.
-   [ ] Stage 3 routes structured, document, mixed, ambiguous, and
    out-of-scope questions correctly.
-   [ ] Mixed answers distinguish SQL facts from document evidence.
-   [ ] Shared verification sets the reliability status based on actual
    evidence.
-   [ ] Follow-up suggestions are tested for supported scope and
    permissions.
-   [ ] React is used if already present or explicitly requested; if
    introduced, it communicates with the backend API and never connects
    directly to PostgreSQL.
-   [ ] Existing working functionality is preserved or any necessary
    breaking change is explained.
-   [ ] Tests cover SQL safety, document access filtering, citation
    grounding, routing, empty evidence, and provider/database failure.
-   [ ] Setup documentation explains database, backend, optional
    document/vector storage, environment variables, and frontend
    commands.

## 23. Expected Codex output

When implementation is complete, provide:

1.  A short summary of the existing architecture found.
2.  The files changed and why.
3.  The final SQL schema and the purpose of each table.
4.  The exact steps to configure PostgreSQL and run the app.
5.  The exact command(s) used to run tests and their real outcomes.
6.  Any remaining gaps, assumptions, or access-control limitations.
