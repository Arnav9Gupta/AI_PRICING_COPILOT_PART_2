
/* ============================================================
   PRICING COPILOT - APP.JS VERSION 10

   Unified Multi-Tool Agent
   Dynamic Visualization + Financial PDF Reports

   Features:
   - Authentication and role-based access
   - Admin-only SQL datasets
   - Unified SQL and document chat
   - Document uploads and library
   - Persistent conversation history
   - Source verification badges
   - Recommendations and follow-ups
   - Interactive Chart.js visualizations
   - Financial PDF report download button
   - Report action persistence after refresh
   ============================================================ */

(() => {
  "use strict";

  const $ = id => document.getElementById(id);

  const DATASET_NAMES = {
    transactions: "Transactions",
    procurement: "Procurement Orders",
    payroll: "HR Payroll",
    sales: "Sales Orders"
  };

  const DATASET_ORDER = [
    "transactions",
    "procurement",
    "payroll",
    "sales"
  ];

  const REPORT_DATASETS = new Set([
    "transactions",
    "procurement",
    "payroll",
    "sales"
  ]);

  const HISTORY_VERSION = "v6";
  const MAX_CHART_POINTS = 24;
  const MAX_CHART_SERIES = 6;

  const state = {
    username: "",
    password: "",
    user: null,
    conversations: [],
    activeId: null,
    busy: false,
    uploading: false,
    datasets: [],
    documents: [],
    selectedDataset: null,
    selectedSource: null,
    datasetPage: 1,
    datasetPageSize: 50,
    attachedFiles: [],
    charts: new Map()
  };

  const text = value => String(value ?? "");
  const safeList = value =>
    Array.isArray(value) ? value : [];

  function el(tag, className = "", value) {
    const node = document.createElement(tag);

    if (className) {
      node.className = className;
    }

    if (value !== undefined) {
      node.textContent = text(value);
    }

    return node;
  }

  function notify(message, type = "") {
    const root = $("toastContainer");

    if (!root) return;

    const toast = el(
      "div",
      `toast ${type}`,
      message
    );

    root.appendChild(toast);

    setTimeout(() => toast.remove(), 4500);
  }

  function formatNumber(value) {
    const number = Number(value);

    return Number.isFinite(number)
      ? number.toLocaleString("en-IN", {
          maximumFractionDigits: 2
        })
      : text(value);
  }

  function isAdmin() {
    return state.user?.role === "Admin";
  }

  /* ============================================================
     1. CHART.JS LOADER
     ============================================================ */

  let chartLibraryPromise = null;

  function loadChartLibrary() {
    if (window.Chart) {
      return Promise.resolve(window.Chart);
    }

    if (chartLibraryPromise) {
      return chartLibraryPromise;
    }

    chartLibraryPromise = new Promise(
      (resolve, reject) => {
        const script = document.createElement("script");

        script.src =
          "https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.min.js";

        script.async = true;

        script.onload = () => {
          if (window.Chart) {
            resolve(window.Chart);
          } else {
            reject(
              new Error("Chart.js did not initialize.")
            );
          }
        };

        script.onerror = () => {
          reject(
            new Error("Unable to load Chart.js.")
          );
        };

        document.head.appendChild(script);
      }
    ).catch(error => {
      chartLibraryPromise = null;
      throw error;
    });

    return chartLibraryPromise;
  }

  /* ============================================================
     2. CHART VALIDATION
     ============================================================ */

  function validNumber(value) {
    if (
      value === null ||
      value === undefined ||
      typeof value === "boolean"
    ) {
      return null;
    }

    const number = Number(value);

    return Number.isFinite(number)
      ? number
      : null;
  }

  function validateVisualization(raw) {
    if (
      !raw ||
      typeof raw !== "object" ||
      Array.isArray(raw)
    ) {
      return null;
    }

    const type = text(raw.type).toLowerCase();

    if (!["bar", "line"].includes(type)) {
      return null;
    }

    const labels = safeList(raw.labels)
      .slice(0, MAX_CHART_POINTS)
      .map(value => text(value).slice(0, 100));

    if (!labels.length) {
      return null;
    }

    const datasets = safeList(raw.datasets)
      .slice(0, MAX_CHART_SERIES)
      .map(item => {
        if (
          !item ||
          typeof item !== "object"
        ) {
          return null;
        }

        const values = safeList(item.data)
          .slice(0, labels.length)
          .map(validNumber);

        if (
          values.length !== labels.length ||
          values.every(v => v === null)
        ) {
          return null;
        }

        return {
          label: text(item.label || "Value")
            .slice(0, 100),
          data: values
        };
      })
      .filter(Boolean);

    if (!datasets.length) {
      return null;
    }

    if (
      raw.source !== "sql" ||
      raw.generated_from !==
        "authorized_sql_result"
    ) {
      return null;
    }

    return {
      type,
      title: text(
        raw.title || "Financial Analysis"
      ).slice(0, 160),
      labels,
      datasets,
      currency: raw.currency === true,
      source: "sql",
      generated_from:
        "authorized_sql_result"
    };
  }

  function extractVisualization(payload) {
    if (
      !isAdmin() ||
      !payload ||
      payload.result?.error ||
      payload.route !== "sql"
    ) {
      return null;
    }

    const raw =
      payload.visualization ||
      payload.chart ||
      payload.result?.visualization ||
      null;

    return validateVisualization(raw);
  }

  /* ============================================================
     3. FINANCIAL PDF REPORT ACTIONS
     ============================================================ */

  function validateReportAction(raw) {
    if (
      !isAdmin() ||
      !raw ||
      typeof raw !== "object" ||
      Array.isArray(raw) ||
      raw.type !== "financial_pdf" ||
      !REPORT_DATASETS.has(raw.dataset)
    ) {
      return null;
    }

    const filters = raw.filters;

    if (
      filters != null &&
      (
        typeof filters !== "object" ||
        Array.isArray(filters)
      )
    ) {
      return null;
    }

    const safeFilters = {};

    for (
      const [key, value]
      of Object.entries(filters || {})
    ) {
      if (
        !/^[a-zA-Z][a-zA-Z0-9_]{0,49}$/.test(key)
      ) {
        return null;
      }

      if (
        typeof value !== "string" &&
        typeof value !== "number" &&
        typeof value !== "boolean"
      ) {
        return null;
      }

      if (String(value).length > 200) {
        return null;
      }

      safeFilters[key] = value;
    }

    return {
      type: "financial_pdf",
      dataset: raw.dataset,
      filters: safeFilters
    };
  }

  async function downloadFinancialReport(
    action,
    button
  ) {
    const report = validateReportAction(action);

    if (!report) {
      notify(
        "Only administrators can request valid financial reports.",
        "error"
      );
      return;
    }

    const originalLabel = button.textContent;

    button.disabled = true;
    button.textContent = "Generating PDF...";

    try {
      const response = await request(
        "/reports/generate",
        {
          method: "POST",
          body: JSON.stringify({
            dataset: report.dataset,
            filters: report.filters
          })
        }
      );

      const contentType =
        response.headers.get("content-type") || "";

      if (
        !contentType.toLowerCase()
          .includes("application/pdf")
      ) {
        throw new Error(
          "The server did not return a PDF."
        );
      }

      const blob = await response.blob();

      if (
        !blob.size ||
        (await blob.slice(0, 5).text()) !== "%PDF-"
      ) {
        throw new Error(
          "The PDF response is invalid."
        );
      }

      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");

      link.href = url;
      link.download =
        `${report.dataset}_financial_report.pdf`;

      document.body.appendChild(link);
      link.click();
      link.remove();

      setTimeout(
        () => URL.revokeObjectURL(url),
        3000
      );

      notify(
        "Financial PDF report downloaded.",
        "success"
      );

    } catch (error) {
      notify(
        error.message ||
          "Unable to generate the PDF.",
        "error"
      );

    } finally {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  }

  function renderReportButton(
    parent,
    rawAction
  ) {
    const action = validateReportAction(
      rawAction
    );

    if (!action) return;

    const wrap = el(
      "div",
      "financial-report-actions"
    );

    const button = el(
      "button",
      "financial-report-download-button",
      "Download Financial PDF"
    );

    button.type = "button";

    button.addEventListener(
      "click",
      () => {
        downloadFinancialReport(
          action,
          button
        );
      }
    );

    wrap.appendChild(button);
    parent.appendChild(wrap);
  }

  /* ============================================================
     4. CHART RENDERING
     ============================================================ */

  function destroyCharts() {
    state.charts.forEach(chart => {
      try {
        chart.destroy();
      } catch (error) {
        console.warn(
          "Chart cleanup failed:",
          error
        );
      }
    });

    state.charts.clear();
  }

  function chartColors(count) {
    const palette = [
      "#2563eb",
      "#0d9488",
      "#7c3aed",
      "#ea580c",
      "#64748b",
      "#db2777"
    ];

    return Array.from(
      { length: count },
      (_, i) => palette[i % palette.length]
    );
  }

  function formatChartValue(value, currency) {
    const number = Number(value);

    if (!Number.isFinite(number)) {
      return "N/A";
    }

    const formatted = number.toLocaleString(
      "en-IN",
      {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2
      }
    );

    return currency
      ? `₹${formatted}`
      : formatted;
  }

  function renderChart(parent, specification) {
    if (!isAdmin()) return;

    const chart = validateVisualization(
      specification
    );

    if (!chart) return;

    const wrapper = el(
      "section",
      "financial-chart-card"
    );

    const header = el(
      "div",
      "financial-chart-header"
    );

    header.appendChild(
      el(
        "h3",
        "financial-chart-title",
        chart.title
      )
    );

    header.appendChild(
      el(
        "p",
        "financial-chart-subtitle",
        "Interactive financial visualization"
      )
    );

    const chartArea = el(
      "div",
      "financial-chart-area"
    );

    const canvas = el("canvas");

    canvas.setAttribute("role", "img");
    canvas.setAttribute(
      "aria-label",
      chart.title
    );

    chartArea.appendChild(canvas);

    const footer = el(
      "div",
      "financial-chart-footer"
    );

    footer.appendChild(
      el(
        "span",
        "",
        "Source: Authorized SQL analysis"
      )
    );

    wrapper.append(
      header,
      chartArea,
      footer
    );

    parent.appendChild(wrapper);

    loadChartLibrary()
      .then(Chart => {
        if (
          !canvas.isConnected ||
          !isAdmin()
        ) {
          return;
        }

        const colors = chartColors(
          chart.datasets.length
        );

        const datasets = chart.datasets.map(
          (dataset, index) => ({
            label: dataset.label,
            data: dataset.data,
            backgroundColor:
              chart.type === "line"
                ? colors[index] + "22"
                : colors[index],
            borderColor: colors[index],
            borderWidth:
              chart.type === "line" ? 2.5 : 1,
            borderRadius:
              chart.type === "bar" ? 6 : 0,
            tension: 0.3,
            fill: false,
            pointRadius:
              chart.type === "line" ? 4 : 0,
            pointHoverRadius: 6,
            maxBarThickness: 65
          })
        );

        const instance = new Chart(
          canvas,
          {
            type: chart.type,

            data: {
              labels: chart.labels,
              datasets
            },

            options: {
              responsive: true,
              maintainAspectRatio: false,

              animation: {
                duration: 450
              },

              interaction: {
                intersect: false,
                mode: "index"
              },

              plugins: {
                legend: {
                  display:
                    chart.datasets.length > 1,
                  position: "bottom",
                  labels: {
                    usePointStyle: true,
                    padding: 18
                  }
                },

                tooltip: {
                  callbacks: {
                    label: context => {
                      const label =
                        context.dataset.label ||
                        "Value";

                      return (
                        `${label}: ` +
                        formatChartValue(
                          context.parsed.y,
                          chart.currency
                        )
                      );
                    }
                  }
                }
              },

              scales: {
                x: {
                  grid: {
                    display: false
                  },

                  ticks: {
                    color: "#64748b",
                    maxRotation: 35,
                    autoSkip: true
                  }
                },

                y: {
                  beginAtZero: true,

                  grid: {
                    color: "#edf0f4"
                  },

                  ticks: {
                    color: "#64748b",

                    callback: value => {
                      const number = Number(value);

                      if (!Number.isFinite(number)) {
                        return value;
                      }

                      const compact =
                        Intl.NumberFormat(
                          "en-IN",
                          {
                            notation: "compact",
                            maximumFractionDigits: 1
                          }
                        ).format(number);

                      return chart.currency
                        ? `₹${compact}`
                        : compact;
                    }
                  }
                }
              }
            }
          }
        );

        state.charts.set(canvas, instance);
      })
      .catch(error => {
        console.warn(
          "Chart rendering failed:",
          error
        );

        if (wrapper.isConnected) {
          chartArea.replaceChildren(
            el(
              "p",
              "financial-chart-error",
              "The chart could not be displayed."
            )
          );
        }
      });
  }

  /* ============================================================
     5. ROLE-BASED DATASET VISIBILITY
     ============================================================ */

  function updateDatasetVisibility() {
    const admin = isAdmin();

    document.querySelectorAll(
      '[data-tab="datasets"], ' +
      '[data-page="datasets"], ' +
      '[data-view="datasets"], ' +
      'a[href="#datasets"]'
    ).forEach(element => {
      element.hidden = !admin;
      element.style.display =
        admin ? "" : "none";
    });

    const menu = $("datasetsMenuButton");

    if (menu) {
      menu.hidden = !admin;
      menu.style.display =
        admin ? "" : "none";
    }

    const panel = $("datasets");

    if (panel && !admin) {
      panel.hidden = true;
      panel.classList.remove("active");
    }

    if (!admin) {
      destroyCharts();

      state.datasets = [];
      state.selectedDataset = null;
      state.datasetPage = 1;

      $("datasetsList")?.replaceChildren();
      $("datasetSchema")?.replaceChildren();
      $("datasetRecords")?.replaceChildren();

      if ($("datasetDetail")) {
        $("datasetDetail").hidden = true;
      }

      if (
        document.querySelector(
          "#datasets.tab-panel.active"
        )
      ) {
        setTab("chat");
      }

    } else if (panel) {
      panel.hidden = false;
    }
  }

  /* ============================================================
     6. CONVERSATION PERSISTENCE
     ============================================================ */

  function historyKey() {
    if (
      !state.user ||
      !state.username
    ) {
      return null;
    }

    return (
      "pricing-copilot:" +
      HISTORY_VERSION +
      ":" +
      state.username.toLowerCase()
    );
  }

  function saveConversations() {
    const key = historyKey();

    if (!key) return;

    try {
      const data = {
        activeId: state.activeId,

        conversations: state.conversations.map(
          conversation => ({
            id: conversation.id,
            title: conversation.title,

            entries: conversation.entries
              .filter(entry => !entry.loading)
              .map(entry => ({
                role: entry.role,
                content: entry.content,

                payload: entry.payload
                  ? {
                      route: entry.payload.route,

                      report_action:
                        validateReportAction(
                          entry.payload.report_action
                        ),

                      suggestions:
                        entry.payload.suggestions,

                      followups:
                        entry.payload.followups,

                      recommendations:
                        entry.payload.recommendations,

                      recommendations_available:
                        entry.payload
                          .recommendations_available,

                      verification_badge:
                        entry.payload
                          .verification_badge,

                      verification_label:
                        entry.payload
                          .verification_label,

                      insights_integrated:
                        entry.payload
                          .insights_integrated,

                      visualization: isAdmin()
                        ? extractVisualization(
                            entry.payload
                          )
                        : null
                    }
                  : null,

                disliked:
                  Boolean(entry.disliked),

                reaction:
                  entry.reaction || null
              })),

            history:
              conversation.history.slice(-20),

            lastQuery: isAdmin()
              ? conversation.lastQuery
              : null,

            source: conversation.source
          })
        )
      };

      localStorage.setItem(
        key,
        JSON.stringify(data)
      );

    } catch (error) {
      console.warn(
        "Conversation save failed:",
        error
      );
    }
  }

  function restoreConversations() {
    const key = historyKey();

    if (!key) return false;

    try {
      const raw = localStorage.getItem(key);

      if (!raw) return false;

      const saved = JSON.parse(raw);

      if (
        !Array.isArray(saved.conversations)
      ) {
        return false;
      }

      state.conversations =
        saved.conversations
          .filter(item =>
            item &&
            typeof item.id === "string" &&
            Array.isArray(item.entries)
          )
          .map(item => ({
            id: item.id,
            title:
              item.title || "Conversation",

            entries: item.entries.map(
              entry => ({
                ...entry,

                payload: entry.payload
                  ? {
                      ...entry.payload,

                      visualization: isAdmin()
                        ? validateVisualization(
                            entry.payload.visualization
                          )
                        : null,

                      report_action:
                        validateReportAction(
                          entry.payload.report_action
                        )
                    }
                  : null
              })
            ),

            history:
              safeList(item.history),

            lastQuery: isAdmin()
              ? item.lastQuery || null
              : null,

            source: isAdmin()
              ? item.source || null
              : (
                  item.source?.document_id
                    ? {
                        document_id:
                          item.source.document_id
                      }
                    : null
                )
          }));

      state.activeId =
        state.conversations.some(
          conversation =>
            conversation.id === saved.activeId
        )
          ? saved.activeId
          : state.conversations[0]?.id ||
            null;

      renderConversations();
      renderConversation();

      return state.conversations.length > 0;

    } catch (error) {
      console.warn(
        "Conversation restore failed:",
        error
      );

      return false;
    }
  }

  /* ============================================================
     7. AUTHENTICATION AND API
     ============================================================ */

  function openLogin() {
    const modal = $("loginModal");

    if (!modal) return;

    modal.hidden = false;
    modal.style.display = "grid";

    $("username")?.focus();
  }

  function closeLogin() {
    const modal = $("loginModal");

    if (!modal) return;

    modal.hidden = true;
    modal.style.display = "none";
  }

  function authHeaders() {
    if (
      !state.username ||
      !state.password
    ) {
      throw new Error(
        "Please sign in first."
      );
    }

    const bytes = new TextEncoder().encode(
      `${state.username}:${state.password}`
    );

    let binary = "";

    bytes.forEach(byte => {
      binary += String.fromCharCode(byte);
    });

    return {
      Authorization:
        `Basic ${btoa(binary)}`
    };
  }

  async function request(
    path,
    options = {}
  ) {
    const headers = {
      ...authHeaders(),
      ...(options.headers || {})
    };

    if (
      options.body &&
      !(options.body instanceof FormData)
    ) {
      headers["Content-Type"] =
        "application/json";
    }

    let response;

    try {
      response = await fetch(
        path,
        {
          ...options,
          headers,
          cache: "no-store"
        }
      );

    } catch {
      throw new Error(
        "Cannot connect to FastAPI. " +
        "Check that the backend is running."
      );
    }

    if (!response.ok) {
      let detail =
        `Request failed (${response.status}).`;

      try {
        const body =
          await response.json();

        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(
                body.detail || body
              );

      } catch {
        // Keep HTTP error.
      }

      throw new Error(detail);
    }

    return response;
  }

  async function api(
    path,
    options = {}
  ) {
    const response = await request(
      path,
      options
    );

    return response.status === 204
      ? null
      : response.json();
  }

  function setConnected(connected) {
    $("connectionIndicator")?.classList.toggle(
      "connected",
      connected
    );

    const values = {
      connectionText:
        connected
          ? "Connected"
          : "Not connected",

      loginButton:
        connected
          ? "Sign out"
          : "Sign in",

      accountName:
        connected
          ? state.user?.name ||
            state.username
          : "Guest User",

      accountRole:
        connected
          ? state.user?.role ||
            "User"
          : "Sign in to continue",

      userAvatar:
        connected
          ? (
              state.user?.name ||
              state.username
            ).charAt(0).toUpperCase()
          : "U"
    };

    Object.entries(values).forEach(
      ([id, value]) => {
        if ($(id)) {
          $(id).textContent = value;
        }
      }
    );
  }

  async function signIn(event) {
    event.preventDefault();

    state.username =
      $("username").value.trim();

    state.password =
      $("password").value;

    if (
      !state.username ||
      !state.password
    ) {
      return;
    }

    if ($("authStatus")) {
      $("authStatus").textContent =
        "Connecting...";
    }

    try {
      state.user =
        await api("/auth/me");

      setConnected(true);
      updateDatasetVisibility();
      closeLogin();

      if ($("password")) {
        $("password").value = "";
      }

      state.conversations = [];
      state.activeId = null;
      state.selectedSource = null;
      state.attachedFiles = [];

      if (!restoreConversations()) {
        newConversation();
      }

      await Promise.all([
        isAdmin()
          ? loadDatasets()
          : Promise.resolve(),

        loadDocuments()
      ]);

      notify(
        "Signed in successfully.",
        "success"
      );

    } catch (error) {
      state.username = "";
      state.password = "";
      state.user = null;

      setConnected(false);
      updateDatasetVisibility();

      if ($("authStatus")) {
        $("authStatus").textContent =
          error.message;
      }

      notify(
        error.message,
        "error"
      );
    }
  }

  function signOut() {
    if (
      state.busy ||
      state.uploading
    ) {
      notify(
        "Wait for the current operation to finish."
      );
      return;
    }

    saveConversations();
    destroyCharts();

    state.username = "";
    state.password = "";
    state.user = null;
    state.conversations = [];
    state.activeId = null;
    state.datasets = [];
    state.documents = [];
    state.selectedSource = null;
    state.attachedFiles = [];

    if ($("password")) {
      $("password").value = "";
    }

    if ($("username")) {
      $("username").value = "";
    }

    $("documentsList")?.replaceChildren();
    $("datasetsList")?.replaceChildren();

    setConnected(false);
    updateDatasetVisibility();
    renderAttachments();
    newConversation();

    notify("Signed out.");
  }

  /* ============================================================
     8. NAVIGATION
     ============================================================ */

  function closeMenus() {
    [
      "profileMenu",
      "conversationMenu",
      "attachMenu"
    ].forEach(id => {
      if ($(id)) {
        $(id).hidden = true;
      }
    });

    [
      "accountButton",
      "conversationMenuButton",
      "attachButton"
    ].forEach(id => {
      $(id)?.setAttribute(
        "aria-expanded",
        "false"
      );
    });
  }

  function setTab(name) {
    if (
      name === "datasets" &&
      !isAdmin()
    ) {
      name = "chat";
    }

    document.querySelectorAll(
      ".tab-button"
    ).forEach(button => {
      button.classList.toggle(
        "active",
        button.dataset.tab === name
      );
    });

    document.querySelectorAll(
      ".tab-panel"
    ).forEach(panel => {
      panel.classList.toggle(
        "active",
        panel.id === name
      );
    });

    if ($("pageTitle")) {
      $("pageTitle").textContent = {
        chat: "Chat",
        documents: "Documents",
        datasets: "Datasets"
      }[name] || "Chat";
    }

    closeMenus();

    $("appShell")?.classList.remove(
      "mobile-nav-open"
    );
  }

  /* ============================================================
     9. CONVERSATION MANAGEMENT
     ============================================================ */

  function activeConversation() {
    return state.conversations.find(
      conversation =>
        conversation.id === state.activeId
    );
  }

  function renderConversations() {
    const root = $("conversationList");

    if (!root) return;

    root.replaceChildren();

    if (!state.conversations.length) {
      root.appendChild(
        el(
          "p",
          "empty-sidebar-text",
          "Your conversations will appear here."
        )
      );

      return;
    }

    state.conversations.forEach(
      conversation => {
        const button = el(
          "button",
          `conversation-item ${
            conversation.id === state.activeId
              ? "active"
              : ""
          }`,
          conversation.title
        );

        button.type = "button";

        button.addEventListener(
          "click",
          () => {
            if (state.busy) return;

            state.activeId =
              conversation.id;

            state.selectedSource = null;

            renderConversations();
            renderConversation();
            saveConversations();
            setTab("chat");
          }
        );

        root.appendChild(button);
      }
    );
  }

  function newConversation() {
    if (state.busy) return;

    const conversation = {
      id: crypto.randomUUID(),
      title: "New conversation",
      entries: [],
      history: [],
      lastQuery: null,
      source: null
    };

    state.conversations.unshift(
      conversation
    );

    state.activeId =
      conversation.id;

    state.selectedSource = null;

    renderConversations();
    renderConversation();
    saveConversations();
    setTab("chat");

    $("questionInput")?.focus();
  }

  function clearCurrentChat() {
    if (state.busy) return;

    const conversation =
      activeConversation();

    if (!conversation) return;

    if (
      !confirm(
        "Clear this conversation?"
      )
    ) {
      return;
    }

    conversation.entries = [];
    conversation.history = [];
    conversation.lastQuery = null;
    conversation.title =
      "New conversation";
    conversation.source = null;

    state.selectedSource = null;

    renderConversations();
    renderConversation();
    saveConversations();
    closeMenus();

    notify(
      "Chat cleared.",
      "success"
    );
  }

  function renderConversation() {
    const root = $("chatMessages");
    const conversation =
      activeConversation();

    if (
      !root ||
      !conversation
    ) {
      return;
    }

    destroyCharts();
    root.replaceChildren();

    conversation.entries.forEach(
      entry => {
        renderEntry(entry);
      }
    );

    root.scrollTop =
      root.scrollHeight;
  }

  /* ============================================================
     10. SAFE ANSWER FORMATTING
     ============================================================ */

  function renderFormattedAnswer(
    container,
    answer
  ) {
    container.replaceChildren();

    const source = text(answer)
      .replace(/\r\n/g, "\n")
      .trim();

    if (!source) return;

    function appendInline(parent, value) {
      const parts = String(value).split(
        /(\*\*[^*]+\*\*)/g
      );

      parts.forEach(part => {
        if (
          part.startsWith("**") &&
          part.endsWith("**")
        ) {
          parent.appendChild(
            el(
              "strong",
              "",
              part.slice(2, -2)
            )
          );

        } else {
          parent.appendChild(
            document.createTextNode(part)
          );
        }
      });
    }

    const lines =
      source.split("\n");

    let currentList = null;
    let paragraph = [];

    function flushParagraph() {
      if (!paragraph.length) return;

      const p = el("p");

      appendInline(
        p,
        paragraph.join(" ")
      );

      container.appendChild(p);
      paragraph = [];
    }

    for (const line of lines) {
      const value = line.trim();

      if (!value) {
        flushParagraph();
        currentList = null;
        continue;
      }

      const heading = value.match(
        /^(#{1,3})\s+(.+)$/
      );

      if (heading) {
        flushParagraph();
        currentList = null;

        const level = Math.min(
          heading[1].length + 2,
          5
        );

        const h = el(
          `h${level}`
        );

        appendInline(
          h,
          heading[2]
        );

        container.appendChild(h);
        continue;
      }

      const ordered = value.match(
        /^\d+\.\s+(.+)$/
      );

      const bullet = value.match(
        /^[-*]\s+(.+)$/
      );

      if (
        ordered ||
        bullet
      ) {
        flushParagraph();

        const type =
          ordered ? "ol" : "ul";

        if (
          !currentList ||
          currentList.tagName.toLowerCase()
            !== type
        ) {
          currentList = el(type);
          container.appendChild(
            currentList
          );
        }

        const item = el("li");

        appendInline(
          item,
          (ordered || bullet)[1]
        );

        currentList.appendChild(item);
        continue;
      }

      currentList = null;
      paragraph.push(value);
    }

    flushParagraph();
  }

  /* ============================================================
     11. SOURCE VERIFICATION
     ============================================================ */

  function makeBadge(payload) {
    const status = text(
      payload?.verification_badge ||
      payload?.result?.verification_badge ||
      payload?.verification_label ||
      payload?.result?.reliability_status
    ).toLowerCase().replaceAll("_", " ");

    if (
      payload?.error ||
      payload?.result?.error ||
      status.includes("not verified") ||
      status.includes("no answer")
    ) {
      return null;
    }

    if (
      status === "source verified" ||
      status === "verified"
    ) {
      return el(
        "span",
        "verification-badge verified",
        "✓ Source Verified"
      );
    }

    if (
      status.includes("source referenced") ||
      status.includes("partially verified")
    ) {
      return el(
        "span",
        "verification-badge referenced",
        "◉ Source Referenced"
      );
    }

    return null;
  }

  /* ============================================================
     12. MESSAGE ACTIONS
     ============================================================ */

  function svgIcon(paths) {
    const svg = document.createElementNS(
      "http://www.w3.org/2000/svg",
      "svg"
    );

    svg.setAttribute(
      "viewBox",
      "0 0 24 24"
    );

    svg.setAttribute("fill", "none");
    svg.setAttribute(
      "stroke",
      "currentColor"
    );

    svg.setAttribute(
      "stroke-width",
      "1.8"
    );

    svg.setAttribute(
      "stroke-linecap",
      "round"
    );

    svg.setAttribute(
      "stroke-linejoin",
      "round"
    );

    svg.setAttribute(
      "aria-hidden",
      "true"
    );

    svg.innerHTML = paths;

    return svg;
  }

  function addAction(
    parent,
    label,
    paths,
    callback
  ) {
    const button = el("button");

    button.type = "button";
    button.title = label;

    button.setAttribute(
      "aria-label",
      label
    );

    button.appendChild(
      svgIcon(paths)
    );

    button.addEventListener(
      "click",
      callback
    );

    parent.appendChild(button);

    return button;
  }

  function renderActions(
    container,
    entry
  ) {
    const actions = el(
      "div",
      "message-actions"
    );

    addAction(
      actions,
      "Retry answer",
      '<path d="M3 11a9 9 0 1 1 2.5 6.2"/><path d="M3 4v7h7"/>',
      () => {
        if (state.busy) return;

        const conversation =
          activeConversation();

        if (!conversation) return;

        const index =
          conversation.entries.indexOf(entry);

        if (index < 1) return;

        const previous =
          conversation.entries[index - 1];

        if (
          previous.role !== "user"
        ) {
          return;
        }

        const question =
          previous.content;

        if (
          index ===
          conversation.entries.length - 1
        ) {
          conversation.entries.splice(
            index - 1,
            2
          );

          conversation.history.splice(
            -2,
            2
          );

          conversation.lastQuery = null;

          saveConversations();
        }

        sendChat(question);
      }
    );

    addAction(
      actions,
      "Copy answer",
      '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
      async () => {
        try {
          await navigator.clipboard.writeText(
            entry.content
          );

          notify(
            "Answer copied.",
            "success"
          );

        } catch {
          notify(
            "Unable to copy answer.",
            "error"
          );
        }
      }
    );

    const dislike = addAction(
      actions,
      "Dislike answer",
      '<path d="M17 14V2H9a3 3 0 0 0-2.8 2L3 12v3h6l3 7 2-1v-7z"/><path d="M17 3h4v11h-4"/>',
      () => {
        entry.disliked =
          !entry.disliked;

        dislike.classList.toggle(
          "feedback-active",
          entry.disliked
        );

        saveConversations();

        notify(
          "Feedback updated."
        );
      }
    );

    dislike.classList.toggle(
      "feedback-active",
      Boolean(entry.disliked)
    );

    const picker = el(
      "div",
      "emoji-picker"
    );

    picker.hidden = true;

    addAction(
      actions,
      "React with emoji",
      '<circle cx="12" cy="12" r="9"/><path d="M8 14s1.5 2 4 2 4-2"/><path d="M9 9h.01M15 9h.01"/>',
      () => {
        picker.hidden =
          !picker.hidden;
      }
    );

    [
      "👍",
      "❤️",
      "🎯",
      "👏",
      "💡"
    ].forEach(symbol => {
      const button = el(
        "button",
        "",
        symbol
      );

      button.type = "button";

      button.addEventListener(
        "click",
        () => {
          entry.reaction = symbol;
          picker.hidden = true;

          saveConversations();

          notify(
            "Reaction saved."
          );
        }
      );

      picker.appendChild(button);
    });

    container.append(
      actions,
      picker
    );
  }

  /* ============================================================
     13. MESSAGE RENDERING
     ============================================================ */

  function renderEntry(entry) {
    const root = $("chatMessages");

    if (!root) return;

    const container = el(
      "div",
      `message ${entry.role}`
    );

    container.appendChild(
      el(
        "span",
        "role",
        entry.role === "user"
          ? "You"
          : "Pricing Copilot"
      )
    );

    if (entry.loading) {
      const indicator = el(
        "div",
        "typing-indicator"
      );

      for (let i = 0; i < 3; i++) {
        indicator.appendChild(
          el("span")
        );
      }

      container.appendChild(
        indicator
      );

      root.appendChild(
        container
      );

      return;
    }

    const content = el(
      "div",
      "message-content"
    );

    if (
      entry.role === "assistant"
    ) {
      renderFormattedAnswer(
        content,
        entry.content || ""
      );

    } else {
      content.textContent =
        entry.content || "";
    }

    container.appendChild(content);

    const payload = entry.payload;

    if (
      entry.role === "assistant" &&
      payload
    ) {
      const badge =
        makeBadge(payload);

      if (badge) {
        container.insertBefore(
          badge,
          content
        );
      }

      // ------------------------------------------
      // DYNAMIC FINANCIAL CHART
      // ------------------------------------------

      const visualization =
        extractVisualization(payload);

      if (visualization) {
        renderChart(
          container,
          visualization
        );
      }

      // ------------------------------------------
      // FINANCIAL PDF DOWNLOAD BUTTON
      // ------------------------------------------

      renderReportButton(
        container,
        payload.report_action
      );

      // ------------------------------------------
      // CONDITIONAL RECOMMENDATIONS
      // ------------------------------------------

      const recommendations = safeList(
        payload.recommendations ||
        payload.advisory?.recommendations
      ).filter(item =>
        typeof item === "string" &&
        item.trim()
      );

      const recommendationsAvailable =
        payload.recommendations_available === true &&
        recommendations.length > 0 &&
        !payload.result?.error;

      if (recommendationsAvailable) {
        const wrap = el(
          "div",
          "recommendation-wrap"
        );

        const toggle = el(
          "button",
          "recommendation-toggle",
          "💡 Recommendations"
        );

        toggle.type = "button";

        toggle.setAttribute(
          "aria-expanded",
          "false"
        );

        const popup = el(
          "div",
          "recommendation-popup"
        );

        popup.hidden = true;

        popup.appendChild(
          el(
            "strong",
            "",
            "Suggested next steps"
          )
        );

        recommendations
          .slice(0, 3)
          .forEach(item => {
            popup.appendChild(
              el(
                "p",
                "",
                item
              )
            );
          });

        popup.appendChild(
          el(
            "small",
            "",
            "Recommendations should be reviewed against the underlying financial evidence."
          )
        );

        toggle.addEventListener(
          "click",
          () => {
            popup.hidden =
              !popup.hidden;

            toggle.setAttribute(
              "aria-expanded",
              String(!popup.hidden)
            );
          }
        );

        wrap.append(
          toggle,
          popup
        );

        container.appendChild(
          wrap
        );
      }

      // ------------------------------------------
      // FOLLOW-UP QUESTIONS
      // ------------------------------------------

      const suggestions = safeList(
        payload.suggestions ||
        payload.followups
      )
        .map(item =>
          typeof item === "string"
            ? item
            : item?.question ||
              item?.text
        )
        .filter(Boolean)
        .slice(0, 5);

      if (suggestions.length) {
        const group = el(
          "div",
          "followup-list"
        );

        suggestions.forEach(
          question => {
            const button = el(
              "button",
              "followup-button",
              question
            );

            button.type = "button";

            button.addEventListener(
              "click",
              () => {
                sendChat(question);
              }
            );

            group.appendChild(
              button
            );
          }
        );

        container.appendChild(
          group
        );
      }
    }

    if (
      entry.role === "assistant" &&
      entry.content
    ) {
      renderActions(
        container,
        entry
      );
    }

    root.appendChild(container);
  }

  /* ============================================================
     14. UNIFIED CHAT
     ============================================================ */

  function setBusy(value) {
    state.busy = value;

    if ($("sendButton")) {
      $("sendButton").disabled =
        value;
    }

    if ($("questionInput")) {
      $("questionInput").disabled =
        value;
    }
  }

  async function sendChat(
    questionOverride = null
  ) {
    if (
      state.busy ||
      state.uploading
    ) {
      return;
    }

    const question = text(
      questionOverride ??
      $("questionInput")?.value
    ).trim();

    if (!question) return;

    if (!state.user) {
      openLogin();

      notify(
        "Please sign in first.",
        "error"
      );

      return;
    }

    let conversation =
      activeConversation();

    if (!conversation) {
      newConversation();

      conversation =
        activeConversation();
    }

    const previousHistory =
      conversation.history.slice(-10);

    const previousQuery = isAdmin()
      ? conversation.lastQuery
      : null;

    if (
      !conversation.entries.length
    ) {
      conversation.title =
        question.slice(0, 45);
    }

    conversation.entries.push({
      role: "user",
      content: question
    });

    const loadingEntry = {
      role: "assistant",
      loading: true
    };

    conversation.entries.push(
      loadingEntry
    );

    if ($("questionInput")) {
      $("questionInput").value = "";
    }

    renderConversations();
    renderConversation();
    setTab("chat");
    setBusy(true);

    const conversationId =
      conversation.id;

    try {
      const uploadedIds =
        await uploadChatAttachments();

      let source =
        state.selectedSource || null;

      if (
        uploadedIds.length === 1
      ) {
        source = {
          document_id:
            uploadedIds[0]
        };
      }

      if (
        uploadedIds.length > 1
      ) {
        source = null;
      }

      if (
        !isAdmin() &&
        source?.dataset
      ) {
        source = null;
      }

      const body = {
        question,
        history: previousHistory,
        last_query: previousQuery
      };

      if (
        isAdmin() &&
        source?.dataset
      ) {
        body.dataset =
          source.dataset;
      }

      if (
        source?.document_id
      ) {
        body.document_id =
          source.document_id;
      }

      const payload = await api(
        "/agent/chat",
        {
          method: "POST",
          body: JSON.stringify(body)
        }
      );

      const answer = text(
        payload.answer ||
        payload.result?.message ||
        payload.message ||
        "No answer was returned."
      );

      // Validate backend-generated chart data.
      payload.visualization =
        extractVisualization(payload);

      // Validate PDF report action.
      payload.report_action =
        validateReportAction(
          payload.report_action
        );

      Object.assign(
        loadingEntry,
        {
          loading: false,
          content: answer,
          payload
        }
      );

      conversation.history.push(
        {
          role: "user",
          content: question
        },
        {
          role: "assistant",
          content: answer
        }
      );

      if (
        isAdmin() &&
        payload.query &&
        !payload.result?.error
      ) {
        conversation.lastQuery =
          payload.query;
      }

      conversation.source =
        source;

      state.selectedSource = null;

      saveConversations();

    } catch (error) {
      Object.assign(
        loadingEntry,
        {
          loading: false,
          content: error.message,
          payload: null
        }
      );

      saveConversations();

      notify(
        error.message,
        "error"
      );

    } finally {
      setBusy(false);

      if (
        state.activeId ===
        conversationId
      ) {
        renderConversation();
      }

      $("questionInput")?.focus();
    }
  }

  function askInChat(
    question,
    source = null
  ) {
    if (
      state.busy ||
      state.uploading
    ) {
      notify(
        "Wait for the current response to finish."
      );
      return;
    }

    if (
      source?.dataset &&
      !isAdmin()
    ) {
      notify(
        "Only administrators can access SQL datasets.",
        "error"
      );
      return;
    }

    if (source) {
      state.selectedSource =
        source;
    }

    setTab("chat");
    sendChat(question);
  }

  /* ============================================================
     15. CHAT ATTACHMENTS
     ============================================================ */

  function renderAttachments() {
    const tray =
      $("attachmentTray");

    if (!tray) return;

    tray.replaceChildren();

    tray.hidden =
      state.attachedFiles.length === 0;

    state.attachedFiles.forEach(
      (file, index) => {
        const chip = el(
          "div",
          "attachment-chip"
        );

        chip.appendChild(
          el(
            "span",
            "attachment-pdf",
            file.name.toLowerCase()
              .endsWith(".pdf")
              ? "PDF"
              : "DOC"
          )
        );

        chip.appendChild(
          el(
            "span",
            "attachment-name",
            file.name
          )
        );

        const remove = el(
          "button",
          "attachment-remove",
          "×"
        );

        remove.type = "button";

        remove.addEventListener(
          "click",
          () => {
            state.attachedFiles.splice(
              index,
              1
            );

            renderAttachments();
          }
        );

        chip.appendChild(
          remove
        );

        tray.appendChild(
          chip
        );
      }
    );
  }

  async function uploadChatAttachments() {
    if (
      !state.attachedFiles.length
    ) {
      return [];
    }

    const files = [
      ...state.attachedFiles
    ];

    const uploadedIds = [];

    state.uploading = true;

    try {
      for (const file of files) {
        if (
          !/\.(pdf|docx)$/i.test(
            file.name
          )
        ) {
          throw new Error(
            "Only PDF and DOCX documents are supported."
          );
        }

        if (
          file.size >
          20 * 1024 * 1024
        ) {
          throw new Error(
            `${file.name} exceeds the 20 MB limit.`
          );
        }

        const body =
          new FormData();

        body.append(
          "file",
          file
        );

        const result = await api(
          "/documents",
          {
            method: "POST",
            body
          }
        );

        uploadedIds.push(
          result.id
        );

        state.attachedFiles =
          state.attachedFiles.filter(
            item => item !== file
          );

        renderAttachments();
      }

      await loadDocuments();

      notify(
        `${uploadedIds.length} document(s) uploaded.`,
        "success"
      );

      return uploadedIds;

    } finally {
      state.uploading = false;
    }
  }

  /* ============================================================
     16. DOCUMENT LIBRARY
     ============================================================ */

  async function loadDocuments() {
    if (!state.user) return;

    const root =
      $("documentsList");

    if (!root) return;

    try {
      state.documents = safeList(
        await api("/documents")
      );

      root.replaceChildren();

      if (
        !state.documents.length
      ) {
        root.appendChild(
          el(
            "p",
            "empty-state",
            "No documents uploaded yet."
          )
        );

        return;
      }

      state.documents.forEach(
        doc => {
          const item = el(
            "article",
            "doc-item"
          );

          const heading = el(
            "div",
            "doc-item-header"
          );

          const info = el("div");

          info.appendChild(
            el(
              "strong",
              "",
              doc.name || "Document"
            )
          );

          info.appendChild(
            el(
              "small",
              "",
              `${doc.department || "Private"} · ${
                doc.status || "unknown"
              }`
            )
          );

          heading.appendChild(info);
          item.appendChild(heading);

          if (doc.error) {
            item.appendChild(
              el(
                "p",
                "status-text",
                doc.error
              )
            );
          }

          const actions = el(
            "div",
            "doc-actions"
          );

          const ask = el(
            "button",
            "secondary-button",
            "Ask about file"
          );

          ask.type = "button";

          ask.disabled =
            doc.status !== "ready";

          ask.addEventListener(
            "click",
            () => {
              askInChat(
                `Summarize the document "${doc.name}" in clear English. Explain its purpose, key points, important provisions and practical implications.`,
                {
                  document_id: doc.id
                }
              );
            }
          );

          const download = el(
            "button",
            "secondary-button",
            "Download"
          );

          download.type = "button";

          download.addEventListener(
            "click",
            async () => {
              try {
                const response =
                  await request(
                    `/documents/${encodeURIComponent(
                      doc.id
                    )}/file`
                  );

                const blob =
                  await response.blob();

                const url =
                  URL.createObjectURL(blob);

                const link =
                  el("a");

                link.href = url;
                link.download =
                  doc.name || "document";

                document.body.appendChild(
                  link
                );

                link.click();
                link.remove();

                setTimeout(
                  () =>
                    URL.revokeObjectURL(url),
                  2000
                );

              } catch (error) {
                notify(
                  error.message,
                  "error"
                );
              }
            }
          );

          const remove = el(
            "button",
            "secondary-button",
            "Delete"
          );

          remove.type = "button";

          remove.addEventListener(
            "click",
            async () => {
              if (
                !confirm(
                  `Delete "${doc.name}"?`
                )
              ) {
                return;
              }

              try {
                await request(
                  `/documents/${encodeURIComponent(
                    doc.id
                  )}`,
                  {
                    method: "DELETE"
                  }
                );

                notify(
                  "Document deleted.",
                  "success"
                );

                await loadDocuments();

              } catch (error) {
                notify(
                  error.message,
                  "error"
                );
              }
            }
          );

          actions.append(
            ask,
            download,
            remove
          );

          item.appendChild(actions);
          root.appendChild(item);
        }
      );

    } catch (error) {
      root.textContent =
        error.message;

      notify(
        error.message,
        "error"
      );
    }
  }

  /* ============================================================
     17. ADMIN DATASET EXPLORER
     ============================================================ */

  async function loadDatasets() {
    if (
      !state.user ||
      !isAdmin()
    ) {
      return;
    }

    try {
      state.datasets = safeList(
        await api("/datasets")
      );

      renderDatasetCards();

    } catch (error) {
      notify(
        error.message,
        "error"
      );
    }
  }

  function renderDatasetCards() {
    if (!isAdmin()) return;

    const root =
      $("datasetsList");

    const detail =
      $("datasetDetail");

    if (
      !root ||
      !detail
    ) {
      return;
    }

    root.hidden = false;
    detail.hidden = true;

    root.replaceChildren();

    DATASET_ORDER.forEach(
      key => {
        const dataset =
          state.datasets.find(
            item =>
              item.key === key
          );

        if (!dataset) return;

        const card = el(
          "button",
          "dataset-tile"
        );

        card.type = "button";

        card.appendChild(
          el(
            "span",
            "dataset-tile-icon",
            "▦"
          )
        );

        card.appendChild(
          el(
            "strong",
            "",
            DATASET_NAMES[key]
          )
        );

        card.appendChild(
          el(
            "small",
            "",
            `${formatNumber(
              dataset.records
            )} records`
          )
        );

        card.addEventListener(
          "click",
          () => {
            openDataset(key);
          }
        );

        root.appendChild(card);
      }
    );

    if (!root.children.length) {
      root.appendChild(
        el(
          "p",
          "empty-state",
          "No authorized datasets."
        )
      );
    }
  }

  function renderDatasetTable(
    rows,
    columns
  ) {
    if (!isAdmin()) return;

    const root =
      $("datasetRecords");

    if (!root) return;

    root.replaceChildren();

    if (!rows.length) {
      root.appendChild(
        el(
          "p",
          "empty-state",
          "No records found."
        )
      );

      return;
    }

    const keys = columns.length
      ? columns
      : Object.keys(rows[0]);

    const wrapper = el(
      "div",
      "result-table-wrap"
    );

    const table = el(
      "table",
      "result-table"
    );

    const thead = el("thead");
    const headerRow = el("tr");

    keys.forEach(column => {
      headerRow.appendChild(
        el(
          "th",
          "",
          column
        )
      );
    });

    thead.appendChild(
      headerRow
    );

    const tbody = el("tbody");

    rows.forEach(row => {
      const tr = el("tr");

      keys.forEach(column => {
        const value =
          row[column];

        tr.appendChild(
          el(
            "td",
            "",
            typeof value === "object" &&
            value !== null
              ? JSON.stringify(value)
              : value ?? "—"
          )
        );
      });

      tbody.appendChild(tr);
    });

    table.append(
      thead,
      tbody
    );

    wrapper.appendChild(table);
    root.appendChild(wrapper);
  }

  async function openDataset(
    key,
    page = 1
  ) {
    if (!isAdmin()) {
      notify(
        "Only administrators can access datasets.",
        "error"
      );
      return;
    }

    const dataset =
      state.datasets.find(
        item => item.key === key
      );

    if (!dataset) return;

    state.selectedDataset = key;
    state.datasetPage = page;

    $("datasetsList").hidden = true;
    $("datasetDetail").hidden = false;

    $("datasetDetailTitle").textContent =
      DATASET_NAMES[key];

    $("datasetDetailStatus").textContent =
      "Loading records...";

    const schema =
      $("datasetSchema");

    schema.replaceChildren();

    safeList(dataset.columns).forEach(
      column => {
        schema.appendChild(
          el(
            "span",
            "schema-chip",
            column
          )
        );
      }
    );

    try {
      const params =
        new URLSearchParams({
          page: String(page),
          page_size: String(
            state.datasetPageSize
          )
        });

      const data = await api(
        `/datasets/${encodeURIComponent(
          key
        )}/records?${params}`
      );

      const rows =
        safeList(data.rows);

      const total = Number(
        data.total ?? 0
      );

      const totalPages = Math.max(
        1,
        Math.ceil(
          total /
          state.datasetPageSize
        )
      );

      renderDatasetTable(
        rows,
        safeList(dataset.columns)
      );

      $("datasetDetailStatus").textContent =
        `${formatNumber(total)} total records`;

      $("datasetPageLabel").textContent =
        `Page ${page} of ${totalPages}`;

      $("datasetPrev").disabled =
        page <= 1;

      $("datasetNext").disabled =
        data.has_next === undefined
          ? page >= totalPages
          : !data.has_next;

    } catch (error) {
      $("datasetDetailStatus").textContent =
        error.message;

      $("datasetRecords").replaceChildren();

      $("datasetPrev").disabled = true;
      $("datasetNext").disabled = true;

      notify(
        error.message,
        "error"
      );
    }
  }

  function askAboutDataset() {
    if (!isAdmin()) {
      notify(
        "Only administrators can access datasets.",
        "error"
      );
      return;
    }

    const key =
      state.selectedDataset;

    if (!key) return;

    askInChat(
      `Summarize the ${DATASET_NAMES[key]} SQL dataset in clear business English. Explain its key metrics, financial performance and practical significance.`,
      {
        dataset: key
      }
    );
  }

  /* ============================================================
     18. EVENT LISTENERS
     ============================================================ */

  function init() {
    $("loginForm")?.addEventListener(
      "submit",
      signIn
    );

    $("closeLoginModal")?.addEventListener(
      "click",
      closeLogin
    );

    $("chatForm")?.addEventListener(
      "submit",
      event => {
        event.preventDefault();
        sendChat();
      }
    );

    $("questionInput")?.addEventListener(
      "keydown",
      event => {
        if (
          event.key === "Enter" &&
          !event.shiftKey &&
          !event.isComposing
        ) {
          event.preventDefault();
          sendChat();
        }
      }
    );

    $("newChatButton")?.addEventListener(
      "click",
      newConversation
    );

    document.querySelectorAll(
      ".tab-button"
    ).forEach(button => {
      button.addEventListener(
        "click",
        () => {
          setTab(
            button.dataset.tab
          );

          if (
            button.dataset.tab === "documents"
          ) {
            loadDocuments();
          }

          if (
            button.dataset.tab === "datasets" &&
            isAdmin()
          ) {
            renderDatasetCards();
          }
        }
      );
    });

    $("accountButton")?.addEventListener(
      "click",
      event => {
        event.stopPropagation();

        if (!state.user) {
          openLogin();
          return;
        }

        const menu =
          $("profileMenu");

        menu.hidden =
          !menu.hidden;

        $("accountButton").setAttribute(
          "aria-expanded",
          String(!menu.hidden)
        );
      }
    );

    $("loginButton")?.addEventListener(
      "click",
      () => {
        state.user
          ? signOut()
          : openLogin();
      }
    );

    $("conversationMenuButton")?.addEventListener(
      "click",
      event => {
        event.stopPropagation();

        const menu =
          $("conversationMenu");

        menu.hidden =
          !menu.hidden;

        $("conversationMenuButton").setAttribute(
          "aria-expanded",
          String(!menu.hidden)
        );
      }
    );

    $("clearChatButton")?.addEventListener(
      "click",
      clearCurrentChat
    );

    $("sidebarToggle")?.addEventListener(
      "click",
      () => {
        $("appShell")?.classList.add(
          "sidebar-collapsed"
        );
      }
    );

    $("reopenSidebarButton")?.addEventListener(
      "click",
      () => {
        $("appShell")?.classList.remove(
          "sidebar-collapsed"
        );
      }
    );

    $("mobileMenuButton")?.addEventListener(
      "click",
      () => {
        $("appShell")?.classList.toggle(
          "mobile-nav-open"
        );
      }
    );

    $("attachButton")?.addEventListener(
      "click",
      event => {
        event.stopPropagation();

        const menu =
          $("attachMenu");

        menu.hidden =
          !menu.hidden;

        $("attachButton").setAttribute(
          "aria-expanded",
          String(!menu.hidden)
        );
      }
    );

    $("attachDocumentOption")?.addEventListener(
      "click",
      () => {
        $("attachMenu").hidden = true;

        $("chatDocumentFile")?.click();
      }
    );

    $("chatDocumentFile")?.addEventListener(
      "change",
      event => {
        const files = Array.from(
          event.target.files || []
        );

        files.forEach(file => {
          if (
            !/\.(pdf|docx)$/i.test(
              file.name
            )
          ) {
            notify(
              "Only PDF and DOCX are supported.",
              "error"
            );
            return;
          }

          if (
            file.size >
            20 * 1024 * 1024
          ) {
            notify(
              "Maximum document size is 20 MB.",
              "error"
            );
            return;
          }

          state.attachedFiles.push(
            file
          );
        });

        event.target.value = "";

        renderAttachments();
      }
    );

    $("backToDatasets")?.addEventListener(
      "click",
      renderDatasetCards
    );

    $("datasetPrev")?.addEventListener(
      "click",
      () => {
        if (
          isAdmin() &&
          state.datasetPage > 1
        ) {
          openDataset(
            state.selectedDataset,
            state.datasetPage - 1
          );
        }
      }
    );

    $("datasetNext")?.addEventListener(
      "click",
      () => {
        if (isAdmin()) {
          openDataset(
            state.selectedDataset,
            state.datasetPage + 1
          );
        }
      }
    );

    $("datasetAskButton")?.addEventListener(
      "click",
      askAboutDataset
    );

    document.addEventListener(
      "click",
      event => {
        if (
          !event.target.closest(
            ".profile-menu-wrap"
          ) &&
          !event.target.closest(
            ".conversation-menu-wrap"
          ) &&
          !event.target.closest(
            ".attach-wrap"
          )
        ) {
          closeMenus();
        }
      }
    );

    document.addEventListener(
      "keydown",
      event => {
        if (
          event.key === "Escape"
        ) {
          closeMenus();
          closeLogin();
        }
      }
    );

    closeLogin();
    setConnected(false);
    updateDatasetVisibility();
    setTab("chat");
    newConversation();
  }

  init();

})();
