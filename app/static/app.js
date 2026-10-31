"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { token: "", epoch: 0, reports: [], total: 0, loaded: false, selected: null, listRequest: 0, detailRequest: 0, controllers: new Set() };
  const text = (value, fallback = "Not provided") => value === null || value === undefined || value === "" ? fallback : typeof value === "object" ? JSON.stringify(value) : String(value);
  const array = (value) => Array.isArray(value) ? value : [];
  const node = (tag, className, value) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined) element.textContent = text(value);
    return element;
  };
  const date = (value) => {
    if (!value) return "Time not provided";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? "Invalid timestamp" : parsed.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  };
  const severityGroup = (value) => {
    const severity = text(value, "").toLowerCase();
    if (["critical", "high", "sev0", "sev1"].includes(severity)) return "high";
    if (["warning", "medium", "sev2"].includes(severity)) return "warning";
    if (["low", "info", "informational", "sev3", "sev4"].includes(severity)) return "low";
    return "other";
  };
  const badge = (value) => node("span", `badge badge-${severityGroup(value)}`, value);
  function message(id, value = "", error = false) {
    const target = $(id);
    target.textContent = value;
    target.classList.toggle("error", error);
    target.hidden = !value;
  }
  function sessionUI() {
    const connected = Boolean(state.token);
    $("connection-status").textContent = connected ? "Connected" : "Disconnected";
    $("connection-status").classList.toggle("connected", connected);
    $("connect-button").hidden = connected;
    $("disconnect-button").hidden = !connected;
    $("new-event-button").disabled = !connected;
    $("refresh-button").disabled = !connected;
  }
  function disconnect(reason = "Disconnected. Your token and loaded reports have been cleared.") {
    state.token = "";
    state.epoch += 1;
    state.listRequest += 1;
    state.detailRequest += 1;
    for (const controller of state.controllers) controller.abort();
    state.controllers.clear();
    state.reports = [];
    state.total = 0;
    state.loaded = false;
    state.selected = null;
    $("token").value = "";
    $("event-form").reset();
    $("event-dialog").close();
    $("detail-content").replaceChildren();
    $("detail-content").hidden = true;
    $("detail-placeholder").hidden = false;
    $("detail-panel").setAttribute("aria-busy", "false");
    $("incident-list").setAttribute("aria-busy", "false");
    $("refresh-button").textContent = "↻ Refresh";
    message("event-message");
    sessionUI();
    renderList();
    metrics();
    message("workspace-message", reason);
  }
  class ApiError extends Error {
    constructor(messageText, status) { super(messageText); this.status = status; }
  }
  async function request(path, { method = "GET", body, publicRequest = false } = {}) {
    if (!publicRequest && !state.token) throw new ApiError("Connect with a bearer token first.", 401);
    const epoch = state.epoch;
    const controller = new AbortController();
    state.controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const headers = { Accept: "application/json" };
      if (!publicRequest) headers.Authorization = `Bearer ${state.token}`;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal, cache: "no-store", credentials: "omit", redirect: "error" });
      if (!publicRequest && epoch !== state.epoch) throw new ApiError("Session changed. Please try again.", 0);
      if (!publicRequest && response.status === 401) {
        disconnect("Your token was rejected or expired. Reconnect to continue.");
        message("auth-message", "Authentication failed. Check your bearer token.", true);
        if (!$("auth-dialog").open) $("auth-dialog").showModal();
        throw new ApiError("Authentication failed. Check your bearer token.", 401);
      }
      let data;
      try { data = await response.json(); } catch {
        if (response.ok) throw new ApiError("The service returned an invalid JSON response.", response.status);
      }
      if (!response.ok) {
        let detail = "";
        if (typeof data?.detail === "string") detail = data.detail;
        else if (Array.isArray(data?.detail)) detail = data.detail.map((item) => `${array(item.loc).join(".")}: ${text(item.msg, "Invalid value")}`).join("; ");
        const fallback = response.status === 404 ? "This endpoint or report is unavailable. Reports may have expired from temporary storage." : response.status === 403 ? "Access denied for this token." : "The service could not complete the request.";
        throw new ApiError(`${detail || fallback} (HTTP ${response.status})`, response.status);
      }
      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(error.name === "AbortError" ? "Request cancelled or timed out. Try refreshing." : "Unable to reach the service. Check your connection and try again.", 0);
    } finally {
      clearTimeout(timer);
      state.controllers.delete(controller);
    }
  }
  async function readiness() {
    try {
      const data = await request("/ready", { publicRequest: true });
      $("ready-status").textContent = text(data?.status, "Unknown");
      $("ready-status").closest(".metric").classList.toggle("is-ready", data?.status === "ready" || data?.status === "ok");
      $("storage-status").textContent = `Storage: ${text(data?.storage, "not reported")}`;
      $("ready-status").closest(".metric").title = text(data?.note, "Process readiness does not verify cloud connectivity.");
    } catch (error) {
      $("ready-status").textContent = "Unavailable";
      $("ready-status").closest(".metric").classList.remove("is-ready");
      $("storage-status").textContent = error.message;
    }
  }
  function metrics() {
    $("total-count").textContent = state.loaded ? state.total.toLocaleString() : "—";
    $("high-count").textContent = state.loaded ? state.reports.filter((report) => severityGroup(report.severity) === "high").length : "—";
    $("gap-count").textContent = state.loaded ? state.reports.filter((report) => array(report.evidence_gaps).length > 0).length : "—";
  }
  function renderList() {
    const query = $("search").value.trim().toLowerCase();
    const provider = $("provider-filter").value;
    const severity = $("severity-filter").value;
    const filtered = state.reports.filter((report) => (!provider || report.provider === provider) && (!severity || severityGroup(report.severity) === severity) && (!query || [report.summary, report.resource_id, report.event_type, report.incident_id].some((value) => text(value, "").toLowerCase().includes(query))));
    const fragment = document.createDocumentFragment();
    for (const report of filtered) {
      const row = node("button", "incident-row");
      row.type = "button";
      row.setAttribute("aria-pressed", String(state.selected === report.incident_id));
      const top = node("span", "row-top");
      top.append(node("span", "provider-tag", text(report.provider, "unknown").toUpperCase()), badge(report.severity));
      const bottom = node("span", "row-bottom");
      bottom.append(node("span", "", date(report.incident_time || report.detected_at)), node("span", "", `${array(report.evidence_gaps).length} evidence gaps`));
      row.append(top, node("span", "row-title", report.summary), node("span", "row-resource", report.resource_id), bottom);
      row.addEventListener("click", () => selectReport(report.incident_id));
      fragment.append(row);
    }
    $("incident-list").replaceChildren(fragment);
    $("list-empty").hidden = filtered.length > 0;
    $("visible-count").textContent = state.loaded ? `${filtered.length} shown · ${state.reports.length} loaded` : "No reports loaded";
    $("list-description").textContent = state.loaded ? `Latest ${state.reports.length} of ${state.total} reports · filters apply to loaded reports` : "Connect to load the latest 50 reports.";
    if (!state.token) {
      $("empty-title").textContent = "Your investigations start here";
      $("empty-description").textContent = "Connect with a bearer token to explore reports or submit a normalized event.";
    } else if (!state.loaded) {
      $("empty-title").textContent = "No reports loaded yet";
      $("empty-description").textContent = "Refresh to load reports from the service.";
    } else if (!state.reports.length) {
      $("empty-title").textContent = "No incidents in the cache";
      $("empty-description").textContent = "Submit a normalized event to create a preliminary report. Older reports may have expired.";
    } else {
      $("empty-title").textContent = "No matching incidents";
      $("empty-description").textContent = "Try another search or choose all clouds and severities.";
    }
  }
  async function loadReports({ propagate = false, quiet = false } = {}) {
    const sequence = ++state.listRequest;
    const epoch = state.epoch;
    $("refresh-button").disabled = true;
    $("refresh-button").textContent = "Refreshing…";
    $("incident-list").setAttribute("aria-busy", "true");
    if (!quiet) message("workspace-message", "Loading recent reports…");
    try {
      const data = await request("/api/v1/reports?limit=50");
      if (sequence !== state.listRequest || epoch !== state.epoch) return false;
      if (!data || !Array.isArray(data.items) || !Number.isInteger(data.total) || data.total < 0 || data.items.some((item) => !item || typeof item !== "object" || typeof item.incident_id !== "string" || !item.incident_id)) throw new ApiError("The report list response did not match the expected format.", 0);
      state.reports = data.items.slice(0, 50);
      state.total = data.total;
      state.loaded = true;
      metrics();
      renderList();
      if (!quiet) message("workspace-message", `Updated ${state.reports.length} recent reports at ${new Date().toLocaleTimeString()}.`);
      return true;
    } catch (error) {
      if (sequence === state.listRequest && epoch === state.epoch) message("workspace-message", `${error.message}${state.loaded ? " Previously loaded reports are still displayed." : ""}`, true);
      if (propagate) throw error;
      return false;
    } finally {
      if (sequence === state.listRequest && epoch === state.epoch) {
        $("refresh-button").disabled = !state.token;
        $("refresh-button").textContent = "↻ Refresh";
        $("incident-list").setAttribute("aria-busy", "false");
      }
    }
  }
  function section(parent, title, values, empty, className = "") {
    const items = array(values);
    const heading = node("div", "section-heading");
    heading.append(node("h3", "", title), node("span", "section-count", items.length));
    parent.append(heading);
    if (!items.length) { parent.append(node("p", "section-empty", empty)); return; }
    const list = node("ul", `text-list ${className}`);
    for (const value of items) list.append(node("li", "", value));
    parent.append(list);
  }
  function renderDetail(report, focus = false) {
    const body = node("div", "detail-body");
    const tags = node("div", "tag-line");
    tags.append(node("span", "provider-tag", text(report.provider, "unknown").toUpperCase()), badge(report.severity), node("span", "badge", `Status: ${text(report.status)}`));
    const title = node("h3", "detail-title", report.summary);
    title.tabIndex = -1;
    body.append(tags, title);
    const facts = node("dl", "detail-facts");
    for (const [label, value, className] of [["Resource", report.resource_id, "wide mono"], ["Resource type", report.resource_type, ""], ["Event type", report.event_type, ""], ["Incident time", date(report.incident_time), ""], ["Detected at", date(report.detected_at), ""], ["Report ID", report.incident_id, "wide mono"], ["Confidence (reported)", report.confidence, ""], ["Rule pack", report.rule_pack_version, ""]]) {
      const entry = node("div", className);
      entry.append(node("dt", "", label), node("dd", "", value));
      facts.append(entry);
    }
    body.append(facts);
    const assessment = node("div", "assessment");
    assessment.append(node("h3", "", "Preliminary cause assessment · unverified"), node("p", "", report.probable_root_cause), node("h3", "", "Impact assessment"), node("p", "", report.impact_assessment));
    body.append(assessment);
    const findings = array(report.findings);
    const heading = node("div", "section-heading");
    heading.append(node("h3", "", "Findings & evidence"), node("span", "section-count", findings.length));
    body.append(heading);
    if (!findings.length) body.append(node("p", "section-empty", "No findings were included in this report."));
    for (const finding of findings) {
      if (!finding || typeof finding !== "object") continue;
      const card = node("article", "finding");
      const line = node("div", "tag-line");
      line.append(badge(finding.severity), node("span", "badge", `Confidence: ${text(finding.confidence)}`), node("span", "finding-code mono", finding.code));
      card.append(line, node("h4", "", finding.title), node("p", "", finding.explanation));
      const evidence = array(finding.evidence);
      if (!evidence.length) card.append(node("p", "evidence", "No supporting evidence was included for this finding."));
      for (const item of evidence) {
        if (!item || typeof item !== "object") continue;
        const entry = node("div", "evidence");
        entry.append(node("strong", "", item.source), node("p", "", item.description), node("p", "evidence-meta", `Observed: ${date(item.observed_at)} · Supports cause: ${item.supports_cause === true ? "yes (reported, unverified)" : item.supports_cause === false ? "no" : "not established"}`));
        if (item.query_reference) entry.append(node("p", "evidence-meta mono", `Query reference: ${text(item.query_reference)}`));
        card.append(entry);
      }
      body.append(card);
    }
    section(body, "Evidence gaps", report.evidence_gaps, "No evidence gaps were listed. This does not establish completeness or verify a root cause.", "gap-list");
    section(body, "Recommended actions · for human review", report.recommended_actions, "No recommended actions were included.");
    const actions = node("div", "detail-actions");
    const email = node("button", "button", "Email this report");
    email.type = "button";
    const emailMessage = node("div", "message");
    emailMessage.hidden = true;
    emailMessage.setAttribute("role", "status");
    emailMessage.setAttribute("aria-live", "polite");
    email.addEventListener("click", async () => {
      const epoch = state.epoch;
      email.disabled = true;
      email.textContent = "Submitting email…";
      emailMessage.hidden = true;
      try {
        const data = await request(`/api/v1/reports/${encodeURIComponent(report.incident_id)}/email`, { method: "POST" });
        if (epoch !== state.epoch) return;
        emailMessage.classList.remove("error");
        emailMessage.textContent = `Email request accepted by the service (${text(data?.status, "accepted")}). Delivery is not confirmed here.`;
      } catch (error) {
        if (epoch !== state.epoch) return;
        emailMessage.classList.add("error");
        emailMessage.textContent = `${error.message} Delivery is not confirmed; check before retrying.`;
      } finally {
        email.disabled = !state.token;
        email.textContent = "Email this report";
        if (epoch === state.epoch) emailMessage.hidden = false;
      }
    });
    actions.append(email, node("p", "", "Sends only when clicked, using the service’s configured email recipients."), emailMessage, node("p", "", report.safety_note || "Read-only investigation. No remediation was performed."));
    body.append(actions);
    $("detail-content").replaceChildren(body);
    $("detail-content").hidden = false;
    $("detail-placeholder").hidden = true;
    if (focus) title.focus();
  }
  async function selectReport(id) {
    const sequence = ++state.detailRequest;
    const epoch = state.epoch;
    state.selected = id;
    renderList();
    $("detail-panel").setAttribute("aria-busy", "true");
    $("detail-content").hidden = false;
    $("detail-placeholder").hidden = true;
    $("detail-content").replaceChildren(node("p", "empty-state", "Loading report details…"));
    try {
      const report = await request(`/api/v1/reports/${encodeURIComponent(id)}`);
      if (sequence !== state.detailRequest || epoch !== state.epoch) return;
      if (!report || report.incident_id !== id) throw new ApiError("The service returned an unexpected report.", 0);
      renderDetail(report, true);
    } catch (error) {
      if (sequence !== state.detailRequest || epoch !== state.epoch) return;
      const container = node("div", "detail-body");
      const feedback = node("p", "message error", error.message);
      feedback.setAttribute("role", "alert");
      const retry = node("button", "button", "Retry report");
      retry.type = "button";
      retry.addEventListener("click", () => selectReport(id));
      container.append(feedback, retry);
      $("detail-content").replaceChildren(container);
    } finally {
      if (sequence === state.detailRequest && epoch === state.epoch) $("detail-panel").setAttribute("aria-busy", "false");
    }
  }
  $("connect-button").addEventListener("click", () => { message("auth-message"); $("auth-dialog").showModal(); });
  $("disconnect-button").addEventListener("click", () => disconnect());
  for (const button of document.querySelectorAll("[data-close]")) button.addEventListener("click", () => $(button.dataset.close).close());
  $("auth-dialog").addEventListener("close", () => { $("token").value = ""; });
  $("auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const token = $("token").value.trim();
    if (!token || /\s/.test(token)) { message("auth-message", "Enter a token without whitespace or the Bearer prefix.", true); return; }
    state.token = token;
    state.epoch += 1;
    $("token").value = "";
    $("auth-submit").disabled = true;
    $("auth-submit").textContent = "Connecting…";
    message("auth-message", "Checking access and loading reports…");
    sessionUI();
    try {
      if (await loadReports({ propagate: true })) $("auth-dialog").close();
    } catch (error) {
      message("auth-message", error.status === 401 ? error.message : `Reports could not be loaded: ${error.message} You can close this dialog and retry or submit an event.`, true);
    } finally {
      $("auth-submit").disabled = false;
      $("auth-submit").textContent = "Connect";
    }
  });
  $("new-event-button").addEventListener("click", () => { message("event-message"); $("event-dialog").showModal(); });
  $("event-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = $("event-form");
    for (const input of form.querySelectorAll("input, textarea")) {
      input.value = input.value.trim();
      input.setCustomValidity(input.required && !input.value ? "Enter a value; whitespace alone is not accepted." : "");
    }
    if (!form.reportValidity()) return;
    const epoch = state.epoch;
    const payload = Object.fromEntries(new FormData(form));
    payload.resource_type = payload.resource_type || "unknown";
    $("event-submit").disabled = true;
    $("event-submit").textContent = "Creating report…";
    message("event-message", "Submitting event and generating a preliminary report…");
    try {
      const report = await request("/api/v1/events", { method: "POST", body: payload });
      if (epoch !== state.epoch) return;
      if (!report || typeof report.incident_id !== "string" || !report.incident_id) throw new ApiError("The service did not return a valid report. Check recent reports before submitting again.", 0);
      state.detailRequest += 1;
      state.listRequest += 1;
      state.selected = report.incident_id;
      state.reports = [report, ...state.reports.filter((item) => item.incident_id !== report.incident_id)].slice(0, 50);
      state.total = Math.max(state.total + 1, state.reports.length);
      state.loaded = true;
      $("search").value = "";
      $("provider-filter").value = "";
      $("severity-filter").value = "";
      $("detail-panel").setAttribute("aria-busy", "false");
      $("incident-list").setAttribute("aria-busy", "false");
      sessionUI();
      $("refresh-button").textContent = "↻ Refresh";
      metrics();
      renderList();
      $("event-dialog").close();
      form.reset();
      renderDetail(report, true);
      message("workspace-message", "Event accepted. A preliminary, unverified report was created. Refresh to synchronize the report count with the service.");
    } catch (error) {
      if (epoch === state.epoch) message("event-message", `${error.message} If the request reached the service, a report may have been created. Refresh before retrying.`, true);
    } finally {
      $("event-submit").disabled = false;
      $("event-submit").textContent = "Create report";
    }
  });
  for (const input of $("event-form").querySelectorAll("input, textarea")) input.addEventListener("input", () => input.setCustomValidity(""));
  $("refresh-button").addEventListener("click", () => { readiness(); loadReports(); if (state.selected) selectReport(state.selected); });
  $("search").addEventListener("input", renderList);
  $("provider-filter").addEventListener("change", renderList);
  $("severity-filter").addEventListener("change", renderList);
  sessionUI();
  renderList();
  readiness();
})();
