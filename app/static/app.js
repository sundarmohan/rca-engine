"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { token: "", authenticated: false, epoch: 0, reports: [], total: 0, loaded: false, selected: null, listRequest: 0, detailRequest: 0, controllers: new Set(), support: null };
  const cloudProviders = { azure: "Azure VM", aws: "AWS EC2" };
  const cloud = { loaded: false, loading: false, sequence: 0, providers: {} };
  function resetCloud() {
    cloud.sequence += 1;
    cloud.loaded = false;
    cloud.loading = false;
    cloud.focusedControl = "";
    for (const item of Object.values(cloud.providers)) clearTimeout(item.timer);
    cloud.providers = Object.fromEntries(Object.keys(cloudProviders).map((provider) => [provider, { connection: null, verified: false, busy: "", resources: [], discovered: false, total: 0, expiresAt: 0, timer: null }]));
  }
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
    const connected = Boolean(state.token) && state.authenticated;
    $("connection-status").textContent = connected ? "Connected" : "Disconnected";
    $("connection-status").classList.toggle("connected", connected);
    $("connect-button").hidden = connected;
    $("disconnect-button").hidden = !connected;
    $("new-event-button").disabled = !connected;
    $("refresh-button").disabled = !connected;
    $("event-submit").disabled = !connected;
    renderCloud();
  }
  function disconnect(reason = "Disconnected. Your token and loaded reports have been cleared.") {
    clearSupport();
    state.token = "";
    state.authenticated = false;
    state.epoch += 1;
    state.listRequest += 1;
    state.detailRequest += 1;
    for (const controller of state.controllers) controller.abort();
    state.controllers.clear();
    state.reports = [];
    state.total = 0;
    state.loaded = false;
    state.selected = null;
    resetCloud();
    message("cloud-message");
    $("token").value = "";
    $("event-form").reset();
    $("event-webhook-token").disabled = false;
    $("event-submit").textContent = "Create report";
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
  async function request(path, { method = "GET", body, publicRequest = false, tokenOverride, signal } = {}) {
    if (!publicRequest && !state.token) throw new ApiError("Connect with a bearer token first.", 401);
    const webhookRequest = tokenOverride !== undefined;
    if (webhookRequest && (path !== "/api/v1/events" || method !== "POST" || publicRequest || typeof tokenOverride !== "string" || !tokenOverride || /\s/.test(tokenOverride))) throw new ApiError("A webhook token can only be used to submit an event.", 0);
    const epoch = state.epoch;
    const controller = new AbortController();
    state.controllers.add(controller);
    const abort = () => controller.abort();
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
    const supportExecute = method === "POST" && /^\/api\/v1\/support\/proposals\/[^/]+\/execute$/.test(path);
    const timeout = supportExecute ? 60000 : path.startsWith("/api/v1/cloud/") ? 35000 : 20000;
    const timer = setTimeout(abort, timeout);
    try {
      const headers = { Accept: "application/json" };
      if (!publicRequest) headers.Authorization = `Bearer ${webhookRequest ? tokenOverride : state.token}`;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal, cache: "no-store", credentials: "omit", redirect: "error" });
      if (!publicRequest && epoch !== state.epoch) throw new ApiError("Session changed. Please try again.", 0);
      if (!publicRequest && response.status === 401) {
        if (webhookRequest) throw new ApiError("The webhook token was rejected or expired. Your operator session is still connected. Enter a valid webhook token to submit this event. (HTTP 401)", 401);
        disconnect("Your token was rejected or expired. Reconnect to continue.");
        message("auth-message", "Authentication failed. Check your bearer token.", true);
        if (!$("auth-dialog").open) $("auth-dialog").showModal();
        throw new ApiError("Authentication failed. Check your bearer token.", 401);
      }
      let data;
      try { data = await response.json(); } catch {
        if (response.ok) throw new ApiError("The service returned an invalid JSON response.", response.status);
      }
      if (!publicRequest && epoch !== state.epoch) throw new ApiError("Session changed. Please try again.", 0);
      if (!publicRequest && !webhookRequest && !state.authenticated && (response.ok || response.status === 403)) {
        state.authenticated = true;
        sessionUI();
      }
      if (!response.ok) {
        let detail = "";
        if (typeof data?.detail === "string") detail = data.detail;
        else if (Array.isArray(data?.detail)) detail = data.detail.map((item) => `${array(item.loc).join(".")}: ${text(item.msg, "Invalid value")}`).join("; ");
        const fallback = response.status === 404 ? "This endpoint or report is unavailable. Reports may have expired from temporary storage." : response.status === 403 ? "Access denied for this token." : response.status === 503 ? "The service is temporarily unavailable." : "The service could not complete the request.";
        if (path.startsWith("/api/v1/cloud/") && [403, 503].includes(response.status)) detail = "";
        throw new ApiError(`${detail || fallback} (HTTP ${response.status})`, response.status);
      }
      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(error.name === "AbortError" ? "Request cancelled or timed out. Try refreshing." : "Unable to reach the service. Check your connection and try again.", 0);
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      state.controllers.delete(controller);
    }
  }
  function renderCloud() {
    const focusedControl = $("cloud-connections").contains(document.activeElement) ? document.activeElement.id : "";
    if (focusedControl) cloud.focusedControl = focusedControl;
    const busy = cloud.loading || Object.values(cloud.providers).some((item) => item.busy);
    $("cloud-panel").setAttribute("aria-busy", String(busy));
    $("cloud-refresh-button").disabled = !state.authenticated || !state.token || busy;
    $("cloud-refresh-button").textContent = cloud.loading ? "Loading connections…" : cloud.loaded ? "Refresh connections" : "Load connections";
    const fragment = document.createDocumentFragment();
    for (const [provider, label] of Object.entries(cloudProviders)) {
      const item = cloud.providers[provider];
      const enabled = item.connection?.enabled === true && item.connection.status === "configured";
      const card = node("article", "cloud-card");
      const heading = node("div", "tag-line");
      const title = node("h3", "", label);
      title.id = `cloud-${provider}-heading`;
      card.setAttribute("aria-labelledby", title.id);
      heading.append(title, node("span", "badge", item.verified ? "Identity verified" : item.connection ? enabled ? "Configured · unverified" : "Disabled" : cloud.loaded ? "Not configured" : "Configuration not loaded"));
      card.append(heading);
      if (item.connection) {
        const scope = node("dl", "cloud-scope");
        const fields = provider === "azure" ? [["Subscriptions", "subscription_ids"], ["Resource groups", "resource_groups"]] : [["Accounts", "account_ids"], ["Regions", "regions"]];
        for (const [name, key] of fields) {
          const entry = node("div");
          entry.append(node("dt", "", name), node("dd", "", array(item.connection.scope?.[key]).map((value) => text(value)).join(", ") || "Not specified by the service"));
          scope.append(entry);
        }
        card.append(scope);
        section(card, "Adapter limitations", item.connection.limitations, "No additional limitations were provided. Coverage remains limited to VM / EC2 health.");
      }
      const actions = node("div", "cloud-actions");
      for (const [action, caption] of [["verify", "Verify identity"], ["discover", "Discover resources"]]) {
        const button = node("button", "button button-small", item.busy === action ? action === "verify" ? "Verifying…" : "Discovering…" : caption);
        button.type = "button";
        button.id = `cloud-${provider}-${action}`;
        button.disabled = !state.authenticated || !state.token || busy || !enabled || (action === "discover" && !item.verified);
        button.setAttribute("aria-label", `${caption} for ${label}`);
        button.addEventListener("click", () => cloudAction(provider, action));
        actions.append(button);
      }
      card.append(actions, node("p", "field-help", !state.token ? "Connect with your workspace bearer token to begin." : !enabled ? "Load connections to check server configuration. Disabled adapters must be enabled on the server." : !item.verified ? "Verify identity before discovering resources. This does not verify a root cause." : "Identity validated. Discover resources to choose a health investigation."));
      if (item.discovered) {
        card.append(node("p", "cloud-resource-count", `${item.resources.length} shown of ${item.total} resources. Selection expires at ${new Date(item.expiresAt).toLocaleTimeString()}; discover again to refresh.`));
        if (!item.resources.length) card.append(node("p", "section-empty", "No resources were found in the configured scope."));
        const list = node("ul", "cloud-resources");
        for (const [index, resource] of item.resources.entries()) {
          const row = node("li", "cloud-resource");
          const details = node("div");
          details.append(node("strong", "", resource.name || resource.resource_id), node("p", "mono", resource.resource_id), node("p", "", `${text(resource.provider)} · ${text(resource.resource_type)} · ${text(resource.region)} · ${provider === "aws" ? "Account" : "Subscription"}: ${text(provider === "aws" ? resource.account_id : resource.subscription_id)}`));
          const investigate = node("button", "button button-small", item.busy === resource.resource_key ? "Investigating…" : "Investigate health");
          investigate.type = "button";
          investigate.id = `cloud-${provider}-investigate-${index}`;
          investigate.disabled = !state.authenticated || !state.token || busy || Date.now() >= item.expiresAt;
          investigate.setAttribute("aria-label", `Investigate health of ${text(resource.name || resource.resource_id)} in ${label}`);
          investigate.addEventListener("click", () => cloudAction(provider, "investigate", resource));
          row.append(details, investigate);
          list.append(row);
        }
        card.append(list);
      }
      fragment.append(card);
    }
    $("cloud-connections").replaceChildren(fragment);
    const restoreFocus = $(cloud.focusedControl);
    if (restoreFocus && !restoreFocus.disabled && (focusedControl || document.activeElement === document.body)) restoreFocus.focus();
    state.support?.refreshResources();
  }
  async function loadConnections() {
    if (!state.authenticated || !state.token || cloud.loading || Object.values(cloud.providers).some((item) => item.busy)) return;
    resetCloud();
    cloud.loading = true;
    const epoch = state.epoch;
    const sequence = cloud.sequence;
    renderCloud();
    message("cloud-message", "Loading server-configured cloud connections…");
    try {
      const data = await request("/api/v1/cloud/connections");
      if (epoch !== state.epoch || sequence !== cloud.sequence) return;
      if (!Array.isArray(data?.items)) throw new ApiError("The service returned invalid cloud connections.", 0);
      for (const connection of data.items) {
        if (connection && Object.hasOwn(cloudProviders, connection.provider)) cloud.providers[connection.provider].connection = connection;
      }
      cloud.loaded = true;
      message("cloud-message", "Connections loaded. Verify identity and discover resources explicitly; no health investigation has run.");
    } catch (error) {
      if (epoch === state.epoch && sequence === cloud.sequence) message("cloud-message", error.message, true);
    } finally {
      if (epoch === state.epoch && sequence === cloud.sequence) { cloud.loading = false; renderCloud(); }
    }
  }
  async function cloudAction(provider, action, resource) {
    const item = cloud.providers[provider];
    if (!state.authenticated || !state.token || cloud.loading || Object.values(cloud.providers).some((value) => value.busy) || item.connection?.enabled !== true || item.connection.status !== "configured") return;
    if (action !== "verify" && !item.verified) return;
    if (action === "investigate" && (!item.resources.includes(resource) || Date.now() >= item.expiresAt)) {
      message("cloud-message", "Resource selection expired. Discover resources again before investigating.", true);
      renderCloud();
      return;
    }
    const epoch = state.epoch;
    const sequence = cloud.sequence;
    const detailSequence = state.detailRequest;
    item.busy = action === "investigate" ? resource.resource_key : action;
    if (action === "verify" || action === "discover") {
      clearTimeout(item.timer);
      item.resources = [];
      item.discovered = false;
      if (action === "verify") item.verified = false;
    }
    renderCloud();
    message("cloud-message", action === "verify" ? `Validating ${cloudProviders[provider]} identity…` : action === "discover" ? `Discovering up to 50 ${cloudProviders[provider]} resources…` : "Creating a preliminary health report…");
    try {
      const data = action === "verify" ? await request(`/api/v1/cloud/connections/${provider}/verify`, { method: "POST" }) : action === "discover" ? await request(`/api/v1/cloud/resources?provider=${provider}&limit=50`) : await request("/api/v1/cloud/investigate", { method: "POST", body: { resource_key: resource.resource_key } });
      if (epoch !== state.epoch || sequence !== cloud.sequence) return;
      if (action === "verify") {
        if (data?.provider !== provider || data.status !== "connected" || data.identity_validated !== true) throw new ApiError("The service did not confirm cloud identity validation.", 0);
        item.verified = true;
        message("cloud-message", `${cloudProviders[provider]} identity validated. Discover resources to continue. Health and root cause have not been verified.`);
      } else if (action === "discover") {
        if (!Array.isArray(data?.items) || !Number.isInteger(data.total) || data.total < 0 || !Number.isFinite(data.expires_in_seconds) || data.expires_in_seconds <= 0 || data.items.some((value) => !value || value.provider !== provider || typeof value.resource_key !== "string" || !value.resource_key)) throw new ApiError("The service returned invalid cloud resources.", 0);
        item.resources = data.items.slice(0, 50);
        item.total = data.total;
        item.discovered = true;
        item.expiresAt = Date.now() + Math.min(data.expires_in_seconds, 600) * 1000;
        item.timer = setTimeout(() => {
          if (epoch !== state.epoch || sequence !== cloud.sequence) return;
          item.resources = [];
          item.discovered = false;
          renderCloud();
          if (!Object.values(cloud.providers).some((value) => value.busy)) message("cloud-message", `${cloudProviders[provider]} resource selection expired. Discover resources again to continue.`);
        }, Math.min(data.expires_in_seconds, 600) * 1000);
        message("cloud-message", `${cloudProviders[provider]} discovery complete. Choose a resource to investigate its health. Resource selections are temporary.`);
      } else {
        if (!data || typeof data.incident_id !== "string" || !data.incident_id) throw new ApiError("The service did not return a valid report. Refresh reports before investigating again.", 0);
        acceptReport(data, detailSequence === state.detailRequest);
        message("cloud-message", "Preliminary health report created. Full RCA, logs, and metrics are not supported yet. Refresh reports to synchronize the count.");
      }
    } catch (error) {
      if (epoch === state.epoch && sequence === cloud.sequence) message("cloud-message", `${error.message}${action === "investigate" ? " A report may have been created; refresh reports before retrying." : ""}`, true);
    } finally {
      if (epoch === state.epoch && sequence === cloud.sequence) { item.busy = ""; renderCloud(); }
    }
  }
  function acceptReport(report, show = true) {
    state.listRequest += 1;
    state.reports = [report, ...state.reports.filter((item) => item.incident_id !== report.incident_id)].slice(0, 50);
    state.total = Math.max(state.total + 1, state.reports.length);
    state.loaded = true;
    if (show) {
      state.detailRequest += 1;
      state.selected = report.incident_id;
      $("search").value = "";
      $("provider-filter").value = "";
      $("severity-filter").value = "";
      $("detail-panel").setAttribute("aria-busy", "false");
      renderDetail(report, true);
    }
    $("incident-list").setAttribute("aria-busy", "false");
    $("refresh-button").textContent = "↻ Refresh";
    sessionUI();
    metrics();
    renderList();
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
        $("refresh-button").disabled = !state.token || !state.authenticated;
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
  function clearSupport() {
    if (state.support) {
      state.support.controller.abort();
      clearTimeout(state.support.timer);
      state.support.assistance = null;
      state.support.reason = "";
      state.support.resourceKey = "";
      state.support.revisions.clear();
    }
    state.support = null;
  }
  const supportText = (value, fallback = "Not provided") => typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? String(value).slice(0, 1000) : fallback;
  function matchingResources(report) {
    const provider = report.provider;
    const item = cloud.providers[provider];
    if (!item || !item.discovered || item.busy || cloud.loading || Date.now() >= item.expiresAt || typeof report.resource_id !== "string") return [];
    return item.resources.filter((resource) => resource.provider === provider && typeof resource.resource_key === "string" && resource.resource_key && resource.resource_id === report.resource_id && resource.resource_type === report.resource_type);
  }
  function proposalId(proposal) {
    return typeof proposal?.proposal_id === "string" && proposal.proposal_id ? proposal.proposal_id : typeof proposal?.id === "string" ? proposal.id : "";
  }
  function validProposal(proposal) {
    return Boolean(proposalId(proposal) && typeof proposal.definition_hash === "string" && /^[0-9a-f]{64}$/.test(proposal.definition_hash) && typeof proposal.status === "string" && proposal.definition && typeof proposal.definition === "object" && !Array.isArray(proposal.definition) && Number.isFinite(Date.parse(proposal.definition.expires_at)));
  }
  function addSupport(body, report) {
    clearSupport();
    const panel = $("support-template").content.firstElementChild.cloneNode(true);
    const load = panel.querySelector("[data-support-load]");
    const feedback = panel.querySelector("[data-support-message]");
    const content = panel.querySelector("[data-support-content]");
    const workflow = { epoch: state.epoch, sequence: state.detailRequest, controller: new AbortController(), busy: false, loaded: false, uncertain: false, unavailable: false, assistance: null, reason: "", resourceKey: "", revisions: new Map(), timer: null, refreshResources: () => {} };
    state.support = workflow;
    const current = () => state.support === workflow && state.epoch === workflow.epoch && state.detailRequest === workflow.sequence && state.selected === report.incident_id && state.authenticated && Boolean(state.token);
    function feedbackMessage(value, error = false) {
      feedback.textContent = value;
      feedback.classList.toggle("error", error);
      feedback.hidden = !value;
    }
    function button(caption, handler, disabled = false) {
      const control = node("button", "button button-small", caption);
      control.type = "button";
      control.disabled = disabled;
      control.addEventListener("click", handler);
      return control;
    }
    function reasonField(parent, id, label, value, update) {
      const input = node("textarea");
      input.id = id;
      input.maxLength = 500;
      input.rows = 3;
      input.value = value;
      input.disabled = workflow.busy || workflow.unavailable;
      const caption = node("label", "", label);
      caption.htmlFor = id;
      input.addEventListener("input", () => update(input.value));
      parent.append(caption, input);
      return input;
    }
    function render() {
      if (!current()) return;
      const focusedId = content.contains(document.activeElement) ? document.activeElement.id : "";
      clearTimeout(workflow.timer);
      panel.setAttribute("aria-busy", String(workflow.busy));
      load.disabled = workflow.busy;
      load.textContent = workflow.busy ? "Working..." : workflow.loaded ? "Refresh assistance" : "Load assistance";
      content.replaceChildren();
      if (!workflow.loaded) return;
      const assistance = workflow.assistance;
      content.append(node("p", "field-help", `${supportText(assistance.status)} · ${supportText(assistance.gap_category)} · Report revision ${supportText(assistance.report_revision)} · Assistance expires ${date(assistance.expires_at)}`));
      const gaps = array(assistance.evidence_gaps).slice(0, 20).map((gap) => supportText(gap, "Evidence gap listed; details unavailable."));
      section(content, "Assistance gaps", gaps, "No assistance gaps were listed. This does not verify completeness or root cause.", "gap-list");
      for (const [caption, values] of [["Questions for human review", assistance.questions], ["Recommended next steps", assistance.recommended_next_steps], ["Evidence references", assistance.evidence_references], ["Executed query references", assistance.executed_queries]]) {
        section(content, caption, array(values).slice(0, 20).map((value) => supportText(value)), "None were listed.");
      }
      const form = node("form", "support-form");
      form.append(node("h4", "", "Propose current VM health follow-up"), node("p", "field-help", "Fixed follow-up: check current VM health for this report's resource. Choose only a matching resource from cached cloud discovery. Reasons are limited to 500 characters; avoid secrets and raw telemetry."));
      const label = node("label", "", "Cached resource matching this report");
      label.htmlFor = "support-resource";
      const select = node("select");
      select.id = "support-resource";
      select.addEventListener("change", () => { workflow.resourceKey = select.value; });
      const create = node("button", "button button-primary", "Propose current VM health");
      create.type = "submit";
      workflow.refreshResources = () => {
        if (!current()) return;
        const resources = matchingResources(report);
        select.replaceChildren(node("option", "", resources.length ? "Select the matching cached resource" : "No matching cached resource available"));
        select.firstElementChild.value = "";
        for (const resource of resources) {
          const option = node("option", "", `${supportText(resource.provider)}: ${supportText(resource.resource_id)}`);
          option.value = resource.resource_key;
          select.append(option);
        }
        if (!resources.some((resource) => resource.resource_key === workflow.resourceKey)) workflow.resourceKey = "";
        select.value = workflow.resourceKey;
        select.disabled = workflow.busy || workflow.unavailable || workflow.uncertain || !resources.length;
        create.disabled = select.disabled;
      };
      form.append(label, select, node("p", "field-help", "Use Cloud connections above to verify identity and discover resources again if the cache is empty or expired."));
      const reason = reasonField(form, "support-reason", "Reason (1–500 characters)", workflow.reason, (value) => { workflow.reason = value; });
      reason.required = true;
      form.append(create);
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!current() || workflow.busy || workflow.unavailable || workflow.uncertain) return;
        const resource = matchingResources(report).find((value) => value.resource_key === select.value);
        if (!resource) { workflow.refreshResources(); feedbackMessage("Select a matching, unexpired resource from cloud discovery.", true); return; }
        const value = reason.value.trim();
        if (!value || value.length > 500) { feedbackMessage("Enter a reason between 1 and 500 characters.", true); reason.focus(); return; }
        transact(`/api/v1/reports/${encodeURIComponent(report.incident_id)}/proposals`, { resource_key: resource.resource_key, reason: value }, "propose");
      });
      content.append(form);
      workflow.refreshResources();
      const proposals = array(assistance.proposals).slice(-20).reverse();
      const heading = node("div", "section-heading");
      heading.append(node("h3", "", "Proposals"), node("span", "section-count", proposals.length));
      content.append(heading);
      if (!proposals.length) content.append(node("p", "section-empty", "No proposals were returned."));
      let nextExpiry = Infinity;
      for (const [index, proposal] of proposals.entries()) {
        if (!proposal || typeof proposal !== "object") continue;
        const id = proposalId(proposal);
        const definition = proposal.definition && typeof proposal.definition === "object" ? proposal.definition : {};
        const scope = definition;
        const card = node("article", "proposal-card");
        const line = node("div", "tag-line");
        line.append(node("h4", "", "Current VM health follow-up"), node("span", "badge", supportText(proposal.status)));
        card.append(line);
        const facts = node("dl", "detail-facts");
        for (const [caption, value] of [["Proposal ID", id], ["Scope provider", scope.provider], ["Scope resource", scope.resource_id], ["Resource type", scope.resource_type], ["Region", scope.region], ["Account / subscription", scope.account_id ?? scope.subscription_id], ["Expected report revision", definition.expected_report_revision], ["Template", definition.template_id], ["Template version", definition.template_version], ["Policy", definition.policy_version], ["Risk", definition.risk], ["Reason", definition.reason], ["Definition digest", proposal.definition_hash], ["Definition expires", date(definition.expires_at)], ["Approval expires", proposal.approval ? date(proposal.approval.expires_at) : "Not approved"], ["Supersedes", proposal.supersedes], ["Superseded by", proposal.superseded_by]]) {
          const entry = node("div", "wide");
          entry.append(node("dt", "", caption), node("dd", "mono", supportText(value)));
          facts.append(entry);
        }
        card.append(facts);
        section(card, "Required permissions", array(definition.required_permissions).slice(0, 4).map((value) => supportText(value)), "No permissions were listed.");
        const parameters = definition.parameters;
        if (parameters && typeof parameters === "object") card.append(node("p", "field-help", `Observation: ${supportText(parameters.observation)} · Timeout: ${supportText(parameters.timeout_seconds)}s · Max states: ${supportText(parameters.max_states)} · Max output: ${supportText(parameters.max_output_bytes)} bytes`));
        if (proposal.outcome && typeof proposal.outcome === "object") {
          card.append(node("p", "field-help", `Health outcome: ${supportText(proposal.outcome.status)} · Observed: ${date(proposal.outcome.observed_at)}`));
          section(card, "Observed states", array(proposal.outcome.states).slice(0, 16).map((value) => supportText(value)), "No states were returned.");
          section(card, "Outcome limitations", array(proposal.outcome.limitations).slice(0, 16).map((value) => supportText(value)), "No additional limitations were returned.");
        }
        if (proposal.failure_code) card.append(node("p", "field-help", `Failure category: ${supportText(proposal.failure_code)}`));
        const definitionExpiry = Date.parse(definition.expires_at);
        const approvalExpiry = Date.parse(proposal.approval?.expires_at);
        const expiry = proposal.status === "approved" ? Math.min(definitionExpiry, approvalExpiry) : definitionExpiry;
        const expired = !Number.isFinite(expiry) || expiry <= Date.now();
        if (!expired) nextExpiry = Math.min(nextExpiry, expiry);
        const pending = proposal.status === "pending";
        const approved = proposal.status === "approved";
        const scoped = definition.incident_id === report.incident_id && definition.provider === report.provider && definition.resource_id === report.resource_id && definition.resource_type === report.resource_type && definition.template_id === "current-platform-health.v1";
        const disabled = workflow.busy || workflow.uncertain || workflow.unavailable || !validProposal(proposal) || !scoped;
        const decisionDisabled = disabled || expired;
        const staleRevision = definition.expected_report_revision !== assistance.report_revision;
        const revisable = ["pending", "approved", "rejected", "cancelled", "expired"].includes(proposal.status) && !proposal.superseded_by;
        if (expired && (pending || approved || proposal.status === "expired")) card.append(node("p", "field-help", "Definition or approval expired; execution is unavailable. A retained proposal may be revised."));
        if (!validProposal(proposal)) card.append(node("p", "field-help", "Incomplete proposal contract; actions are unavailable."));
        if (!scoped) card.append(node("p", "field-help", "This proposal does not match this report's resource and fixed health template. Actions are unavailable."));
        if (staleRevision && (pending || approved)) card.append(node("p", "field-help", "The report revision changed. Revise this proposal and approve the new definition before execution."));
        const actions = node("div", "cloud-actions");
        const perform = (action, extra = {}) => {
          if (!current() || disabled || workflow.busy || workflow.uncertain || (action !== "revise" && (!Number.isFinite(expiry) || Date.now() >= expiry)) || (["approve", "execute"].includes(action) && staleRevision)) return;
          if (action === "execute" && proposal.approval?.definition_hash !== proposal.definition_hash) return;
          transact(`/api/v1/support/proposals/${encodeURIComponent(id)}/${action}`, { definition_hash: proposal.definition_hash, ...extra }, action);
        };
        if (pending) actions.append(button("Approve", () => perform("approve"), decisionDisabled || staleRevision));
        if (pending || approved) actions.append(button("Reject", () => perform("reject"), decisionDisabled), button("Cancel proposal", () => perform("cancel"), decisionDisabled));
        if (approved) actions.append(button("Execute current VM health", () => perform("execute"), decisionDisabled || staleRevision || proposal.approval?.definition_hash !== proposal.definition_hash));
        card.append(actions);
        if (revisable) {
          const revision = reasonField(card, `support-revision-${index}`, "Revision reason (1–500 characters)", workflow.revisions.get(id) || "", (value) => workflow.revisions.set(id, value));
          revision.disabled = disabled;
          card.append(node("p", "field-help", "Revision creates a new pending proposal and cancels this proposal. Review the new definition digest and approve it before execution."));
          card.append(button("Revise reason", () => {
            const value = revision.value.trim();
            if (!value || value.length > 500) { feedbackMessage("Enter a revision reason between 1 and 500 characters.", true); revision.focus(); return; }
            perform("revise", { reason: value });
          }, disabled));
        }
        content.append(card);
      }
      const history = array(assistance.proposals).slice(-100).flatMap((proposal) => array(proposal?.history).slice(-20).map((entry) => ({ status: entry?.status, at: entry?.at, detail: entry?.detail, actor: entry?.actor, proposal_id: proposalId(proposal) }))).sort((left, right) => (Date.parse(right.at) || 0) - (Date.parse(left.at) || 0)).slice(0, 20);
      const historyHeading = node("div", "section-heading");
      historyHeading.append(node("h3", "", "History · read only"), node("span", "section-count", history.length));
      content.append(historyHeading, node("p", "field-help", "At most 20 history entries, proposals, and gaps are shown. History contains summary fields only."));
      const historyList = node("ul", "support-history");
      for (const entry of history) {
        const row = node("li");
        if (entry && typeof entry === "object") row.append(node("strong", "", `${supportText(entry.status)} · ${supportText(entry.actor)}`), node("p", "mono", supportText(entry.proposal_id)), node("p", "", date(entry.at)), node("p", "", supportText(entry.detail, "Status recorded.")));
        else row.append(node("p", "", "Activity recorded; structured summary unavailable."));
        historyList.append(row);
      }
      content.append(history.length ? historyList : node("p", "section-empty", "No history entries were returned."));
      const restoreFocus = focusedId ? $(focusedId) : null;
      if (restoreFocus && !restoreFocus.disabled) restoreFocus.focus({ preventScroll: true });
      if (Number.isFinite(nextExpiry)) workflow.timer = setTimeout(render, Math.min(Math.max(nextExpiry - Date.now(), 1), 2147483647));
    }
    async function fetchAssistance() {
      const data = await request(`/api/v1/reports/${encodeURIComponent(report.incident_id)}/support`, { signal: workflow.controller.signal });
      if (!current()) return false;
      const assistance = data?.assistance ?? data;
      if (!assistance || typeof assistance !== "object" || assistance.incident_id !== report.incident_id || assistance.provider !== report.provider || assistance.resource_id !== report.resource_id || assistance.resource_type !== report.resource_type || !Number.isInteger(assistance.report_revision) || assistance.report_revision < 1 || !Array.isArray(assistance.evidence_gaps) || !Array.isArray(assistance.proposals)) throw new ApiError("The assistance response did not match the expected report and format.", 0);
      workflow.assistance = assistance;
      workflow.loaded = true;
      workflow.uncertain = false;
      workflow.unavailable = false;
      return true;
    }
    async function refresh() {
      if (!current() || workflow.busy) return;
      workflow.busy = true;
      render();
      feedbackMessage("Loading structured assistance...");
      try {
        if (await fetchAssistance()) feedbackMessage("Assistance refreshed. Review scope, definition digest, template, and expiry before acting.");
      } catch (error) {
        if (!current()) return;
        workflow.uncertain = true;
        workflow.unavailable = error.status === 503;
        feedbackMessage(error.status === 503 ? "Assistance and proposal APIs are development only and unavailable in production (HTTP 503)." : error.message, true);
      } finally {
        if (current()) { workflow.busy = false; render(); }
      }
    }
    async function transact(path, payload, action) {
      if (!current() || workflow.busy || workflow.uncertain || workflow.unavailable) return;
      workflow.busy = true;
      render();
      feedbackMessage(action === "execute" ? "Executing the approved current VM health check..." : "Submitting proposal action...");
      try {
        const data = await request(path, { method: "POST", body: payload, signal: workflow.controller.signal });
        if (!current()) return;
        const proposal = data?.proposal ?? data;
        if (!validProposal(proposal)) throw new ApiError("The service returned an incomplete proposal. Refresh assistance before continuing.", 0);
        workflow.reason = action === "propose" ? "" : workflow.reason;
        workflow.revisions.clear();
        if (action === "execute") {
          if (!data.report || data.report.incident_id !== report.incident_id || proposal.status !== "completed") throw new ApiError("Execution returned no completed proposal and matching report. Refresh reports and assistance before continuing.", 0);
          await loadReports({ quiet: true });
          if (!current()) return;
          await selectReport(report.incident_id);
          if (state.epoch === workflow.epoch && state.selected === report.incident_id && state.support) await state.support.refresh();
          return;
        }
        workflow.uncertain = true;
        if (await fetchAssistance()) feedbackMessage(action === "revise" ? `Revision created proposal ${proposalId(proposal)} and superseded the old proposal. Review and approve the new definition before execution.` : "Proposal action accepted. Assistance refreshed; review the current definition before the next action.");
      } catch (error) {
        if (!current()) return;
        workflow.uncertain = true;
        workflow.unavailable = error.status === 503;
        feedbackMessage(error.status === 503 ? "Proposal APIs are development only and unavailable in production (HTTP 503)." : `${error.message} The action may have reached the service. Refresh assistance to reconcile its status before another action.`, true);
      } finally {
        if (current()) { workflow.busy = false; render(); }
      }
    }
    workflow.refresh = refresh;
    load.addEventListener("click", refresh);
    body.append(panel);
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
    addSupport(body, report);
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
        email.disabled = !state.token || !state.authenticated;
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
    clearSupport();
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
  $("event-dialog").addEventListener("close", () => { $("event-webhook-token").value = ""; });
  $("event-dialog").addEventListener("cancel", () => { $("event-webhook-token").value = ""; });
  $("auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const token = $("token").value.trim();
    if (!token || /\s/.test(token)) { message("auth-message", "Enter a token without whitespace or the Bearer prefix.", true); return; }
    state.token = token;
    state.authenticated = false;
    state.epoch += 1;
    const epoch = state.epoch;
    resetCloud();
    message("cloud-message");
    $("token").value = "";
    $("auth-submit").disabled = true;
    $("auth-submit").textContent = "Connecting…";
    message("auth-message", "Checking access and loading reports…");
    sessionUI();
    try {
      if (await loadReports({ propagate: true }) && epoch === state.epoch) $("auth-dialog").close();
    } catch (error) {
      if (epoch === state.epoch) message("auth-message", error.status === 401 ? error.message : `Reports could not be loaded: ${error.message} ${state.authenticated ? "You can close this dialog and retry or submit an event." : "Reconnect to retry access."}`, true);
    } finally {
      if (epoch === state.epoch || !state.token) {
        $("auth-submit").disabled = false;
        $("auth-submit").textContent = "Connect";
      }
    }
  });
  $("new-event-button").addEventListener("click", () => { message("event-message"); $("event-dialog").showModal(); });
  $("event-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = $("event-form");
    for (const input of form.querySelectorAll("input, textarea")) {
      input.value = input.value.trim();
      input.setCustomValidity(input.id === "event-webhook-token" && /\s/.test(input.value) ? "Enter the webhook token without whitespace or the Bearer prefix." : input.required && !input.value ? "Enter a value; whitespace alone is not accepted." : "");
    }
    if (!form.reportValidity()) return;
    const epoch = state.epoch;
    const payload = Object.fromEntries(new FormData(form));
    payload.resource_type = payload.resource_type || "unknown";
    $("event-submit").disabled = true;
    $("event-submit").textContent = "Creating report…";
    $("event-webhook-token").disabled = true;
    message("event-message", "Submitting event and generating a preliminary report…");
    try {
      const pendingReport = request("/api/v1/events", { method: "POST", body: payload, tokenOverride: $("event-webhook-token").value || undefined });
      $("event-webhook-token").value = "";
      const report = await pendingReport;
      if (epoch !== state.epoch) return;
      if (!report || typeof report.incident_id !== "string" || !report.incident_id) throw new ApiError("The service did not return a valid report. Check recent reports before submitting again.", 0);
      $("event-dialog").close();
      form.reset();
      acceptReport(report);
      message("workspace-message", "Event accepted. A preliminary, unverified report was created. Refresh to synchronize the report count with the service.");
    } catch (error) {
      if (epoch === state.epoch) message("event-message", error.status === 401 ? error.message : `${error.message} If the request reached the service, a report may have been created. Refresh before retrying.`, true);
    } finally {
      if (epoch === state.epoch) {
        $("event-webhook-token").value = "";
        $("event-webhook-token").disabled = false;
        $("event-submit").disabled = !state.token || !state.authenticated;
        $("event-submit").textContent = "Create report";
      }
    }
  });
  for (const input of $("event-form").querySelectorAll("input, textarea")) input.addEventListener("input", () => input.setCustomValidity(""));
  $("refresh-button").addEventListener("click", () => { readiness(); loadReports(); if (state.selected) selectReport(state.selected); });
  $("search").addEventListener("input", renderList);
  $("provider-filter").addEventListener("change", renderList);
  $("severity-filter").addEventListener("change", renderList);
  $("cloud-refresh-button").addEventListener("click", loadConnections);
  resetCloud();
  sessionUI();
  renderList();
  readiness();
})();
