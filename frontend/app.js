"use strict";

(() => {
  const REFRESH_INTERVAL_MS = 15_000;
  const FETCH_TIMEOUT_MS = 5_000;
  const STALE_AFTER_MS = 45_000;
  const SUPPORTED_SCHEMA_VERSION = 1;
  const STOPPED_STATES = new Set(["stopped", "not_found"]);
  const MANAGED_SINGLETON_SERVICES = new Set(["filebrowser", "porterminal", "go2rtc"]);
  const STATUS_LABELS = {
    not_found: "Not found",
  };
  const SERVICE_ICONS = {
    "code-server": "</>",
    filebrowser: "FB",
    porterminal: ">_",
    go2rtc: "RTC",
    www: "WWW",
  };
  const OPERATION_PROGRESS_LABELS = {
    start: "Starting…",
    stop: "Stopping…",
    restart: "Restarting…",
    clean: "Cleaning…",
    publish: "Publishing…",
    unpublish: "Unpublishing…",
    "profile-save": "Saving…",
    "profile-start": "Starting…",
    "profile-delete": "Deleting…",
    "start-workspace": "Starting…",
  };

  const elements = {
    attentionCount: document.querySelector("#attention-count"),
    connectionPill: document.querySelector("#connection-pill"),
    connectionLabel: document.querySelector("#connection-label"),
    credentialClose: document.querySelector("#credential-close"),
    credentialContent: document.querySelector("#credential-content"),
    credentialCopy: document.querySelector("#credential-copy"),
    credentialDialog: document.querySelector("#credential-dialog"),
    credentialExpiry: document.querySelector("#credential-expiry"),
    credentialHide: document.querySelector("#credential-hide"),
    credentialLoading: document.querySelector("#credential-loading"),
    credentialPassword: document.querySelector("#credential-password"),
    credentialTarget: document.querySelector("#credential-target"),
    credentialUsername: document.querySelector("#credential-username"),
    credentialUsernameRow: document.querySelector("#credential-username-row"),
    detailHostname: document.querySelector("#detail-hostname"),
    detailMagicDns: document.querySelector("#detail-magicdns"),
    detailTailnetIp: document.querySelector("#detail-tailnet-ip"),
    detailUsername: document.querySelector("#detail-username"),
    endpointLabel: document.querySelector("#endpoint-label"),
    generatedAt: document.querySelector("#generated-at"),
    hostInterfaceList: document.querySelector("#host-interface-list"),
    hostMenu: document.querySelector("#host-menu"),
    hostMenuPanel: document.querySelector("#host-menu-panel"),
    hostName: document.querySelector("#host-name"),
    hostQr: document.querySelector("#host-qr"),
    hostQrLink: document.querySelector("#host-qr-link"),
    hostQrUrl: document.querySelector("#host-qr-url"),
    interfaceCount: document.querySelector("#interface-count"),
    logoutButton: document.querySelector("#logout-button"),
    noticeRegion: document.querySelector("#notice-region"),
    refreshButton: document.querySelector("#refresh-button"),
    refreshStatus: document.querySelector("#refresh-status"),
    runningCount: document.querySelector("#running-count"),
    serviceGrid: document.querySelector("#service-grid"),
    stoppedCount: document.querySelector("#stopped-count"),
    sshCommand: document.querySelector("#ssh-command"),
    sshCopyButton: document.querySelector("#ssh-copy-button"),
    tailnetAddress: document.querySelector("#tailnet-address"),
    workspaceCancel: document.querySelector("#workspace-cancel"),
    workspaceClose: document.querySelector("#workspace-close"),
    workspaceDialog: document.querySelector("#workspace-dialog"),
    workspaceForm: document.querySelector("#workspace-form"),
    workspaceInstance: document.querySelector("#workspace-instance"),
    workspacePath: document.querySelector("#workspace-path"),
  };

  const query = new URLSearchParams(window.location.search);
  const demoMode = query.get("demo") === "1";
  const statusUrl = new URL(demoMode ? "./status.sample.json" : "./api/status", document.baseURI);
  const credentialUrl = new URL("./api/credentials/reveal", document.baseURI);
  const actionUrls = {
    "code-server": new URL("./api/services/code-server/actions", document.baseURI),
    filebrowser: new URL("./api/services/filebrowser/actions", document.baseURI),
    porterminal: new URL("./api/services/porterminal/actions", document.baseURI),
    go2rtc: new URL("./api/services/go2rtc/actions", document.baseURI),
  };
  const logoutUrl = new URL("./api/session/logout", document.baseURI);
  const loginUrl = new URL("./login", document.baseURI);
  const runtime = {
    controller: null,
    hasData: false,
    inFlight: false,
    lastCompletedAt: null,
    credentialClearTimer: null,
    credentialTarget: null,
    operationInFlight: false,
    operation: null,
  };

  elements.endpointLabel.textContent = "";
  const endpointPrefix = document.createTextNode(demoMode ? "Preview: " : "Endpoint: ");
  const endpointCode = document.createElement("code");
  endpointCode.textContent = demoMode ? "./status.sample.json" : "./api/status";
  elements.endpointLabel.append(endpointPrefix, endpointCode);

  function clearNode(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function createElement(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function normalizedStatus(value) {
    if (typeof value !== "string" || value.trim() === "") {
      return "unknown";
    }
    return value
      .trim()
      .toLowerCase()
      .replaceAll("-", "_")
      .replace(/[^a-z0-9_]/g, "_")
      .replace(/_+/g, "_");
  }

  function statusLabel(status) {
    const normalized = normalizedStatus(status);
    return STATUS_LABELS[normalized] || normalized.replaceAll("_", " ");
  }

  function makeStatusBadge(status) {
    const normalized = normalizedStatus(status);
    const badge = createElement(
      "span",
      `status-badge status-badge--${normalized}`,
      statusLabel(normalized),
    );
    badge.setAttribute("aria-label", `Status: ${statusLabel(normalized)}`);
    return badge;
  }

  function safeUrl(value) {
    if (typeof value !== "string" || value.trim() === "") {
      return null;
    }

    try {
      const url = new URL(value, document.baseURI);
      if (url.protocol !== "http:" && url.protocol !== "https:") {
        return null;
      }
      return url.href;
    } catch (_error) {
      return null;
    }
  }

  function formatTimestamp(value) {
    const timestamp = Date.parse(value);
    if (!Number.isFinite(timestamp)) {
      return "Unavailable";
    }

    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "medium",
    }).format(new Date(timestamp));
  }

  function formatShortTime(value) {
    const timestamp = Date.parse(value);
    if (!Number.isFinite(timestamp)) {
      return "unknown time";
    }

    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date(timestamp));
  }

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || seconds === "") {
      return null;
    }

    const value = Number(seconds);
    if (!Number.isFinite(value) || value < 0) {
      return null;
    }

    if (value < 60) {
      return `${Math.round(value)}s`;
    }

    const minutes = Math.floor(value / 60);
    if (minutes < 60) {
      return `${minutes}m`;
    }

    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;
    if (hours < 24) {
      return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
    }

    const days = Math.floor(hours / 24);
    const remainingHours = hours % 24;
    return remainingHours > 0 ? `${days}d ${remainingHours}h` : `${days}d`;
  }

  function validatePayload(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("Status response is not a JSON object.");
    }
    if (payload.schema_version !== SUPPORTED_SCHEMA_VERSION) {
      throw new Error(
        `Unsupported status schema ${String(payload.schema_version)}; expected ${SUPPORTED_SCHEMA_VERSION}.`,
      );
    }
    if (!payload.host || typeof payload.host !== "object") {
      throw new Error("Status response is missing host information.");
    }
    if (!Array.isArray(payload.services)) {
      throw new Error("Status response is missing its services array.");
    }
    return payload;
  }

  function setConnectionState(tailnet, error = false) {
    elements.connectionPill.className = "topbar-host";

    if (error) {
      elements.connectionPill.classList.add("connection-pill--error");
      elements.connectionLabel.textContent = "Status unavailable";
      return;
    }

    if (tailnet?.connected === true) {
      elements.connectionPill.classList.add("connection-pill--connected");
      elements.connectionLabel.textContent = tailnet.dns_name || "Tailnet connected";
      return;
    }

    if (tailnet?.connected === false) {
      elements.connectionPill.classList.add("connection-pill--disconnected");
      elements.connectionLabel.textContent = "Tailnet disconnected";
      return;
    }

    elements.connectionPill.classList.add("connection-pill--checking");
    elements.connectionLabel.textContent = "Tailnet unknown";
  }

  function setHostMenuOpen(open) {
    elements.connectionPill.setAttribute("aria-expanded", String(open));
    elements.hostMenuPanel.hidden = !open;
  }

  function addressValues(candidates) {
    const addresses = [];

    for (const candidate of Array.isArray(candidates) ? candidates : [candidates]) {
      const value = typeof candidate === "string"
        ? candidate
        : candidate?.family === "inet" || candidate?.family === "ipv4"
          ? candidate.address
          : null;
      if (typeof value !== "string" || value.trim() === "") {
        continue;
      }
      const address = value.trim();
      if (!address.startsWith("127.") && address !== "::1" && !addresses.includes(address)) {
        addresses.push(address);
      }
    }

    return addresses;
  }

  function interfaceAddressEntries(networkInterface) {
    const active = addressValues(
      networkInterface.addresses ?? networkInterface.ipv4 ?? [],
    );
    const activeKeys = new Set(active.map((address) => address.split("/")[0]));
    const configured = addressValues(networkInterface.configured_addresses ?? [])
      .filter((address) => !activeKeys.has(address.split("/")[0]));

    if (active.length === 0 && configured.length === 0) {
      return [{ address: "No address", configured: false, unavailable: true }];
    }

    return [
      ...active.map((address) => ({ address, configured: false, unavailable: false })),
      ...configured.map((address) => ({ address, configured: true, unavailable: false })),
    ];
  }

  function isPhysicalNetworkInterface(networkInterface) {
    if (!networkInterface || typeof networkInterface !== "object") {
      return false;
    }

    const name = String(networkInterface.name || "").trim();
    const type = String(networkInterface.type || networkInterface.kind || "")
      .trim()
      .toLowerCase();
    const virtualName = /^(br-|bridge|cni|docker|dummy|lo$|podman|tailscale|tap|tun|veth|virbr|vmnet|wg)/i;
    const virtualType = /^(bridge|container|docker|loopback|tunnel|virtual|vpn)$/;

    if (
      name === ""
      || networkInterface.virtual === true
      || networkInterface.physical === false
      || virtualName.test(name)
      || virtualType.test(type)
    ) {
      return false;
    }

    return ["ethernet", "wifi", "wireless", "wired"].includes(type)
      || /^(en|eth|wl)/i.test(name);
  }

  function dashboardUrl(payload) {
    const explicitUrl = safeUrl(payload.tailnet?.dashboard_url);
    if (explicitUrl) {
      return explicitUrl;
    }

    const dnsName = typeof payload.tailnet?.dns_name === "string"
      ? payload.tailnet.dns_name.trim().replace(/\.$/, "")
      : "";
    if (dnsName === "") {
      return null;
    }

    return `https://${dnsName}/dev-stack/${demoMode ? "?demo=1" : ""}`;
  }

  function renderQrCode(url) {
    clearNode(elements.hostQr);
    if (!url || !window.DevStackQRCode || !window.DevStackQRErrorCorrectLevel) {
      elements.hostQr.append(createElement("span", "host-qr__unavailable", "QR unavailable"));
      elements.hostQr.setAttribute("aria-label", "Dashboard QR code unavailable");
      return;
    }

    const qr = new window.DevStackQRCode(-1, window.DevStackQRErrorCorrectLevel.M);
    qr.addData(url);
    qr.make();

    const quietZone = 4;
    const moduleCount = qr.getModuleCount();
    const size = moduleCount + quietZone * 2;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
    svg.setAttribute("shape-rendering", "crispEdges");
    svg.setAttribute("aria-hidden", "true");

    const background = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    background.setAttribute("width", String(size));
    background.setAttribute("height", String(size));
    background.setAttribute("fill", "#fff");
    svg.append(background);

    let pathData = "";
    for (let row = 0; row < moduleCount; row += 1) {
      for (let column = 0; column < moduleCount; column += 1) {
        if (qr.isDark(row, column)) {
          pathData += `M${column + quietZone} ${row + quietZone}h1v1h-1z`;
        }
      }
    }
    const modules = document.createElementNS("http://www.w3.org/2000/svg", "path");
    modules.setAttribute("d", pathData);
    modules.setAttribute("fill", "#101612");
    svg.append(modules);
    elements.hostQr.append(svg);
    elements.hostQr.setAttribute("aria-label", `QR code for ${url}`);
  }

  function renderHostNetwork(payload) {
    const hostname = payload.host?.hostname || "Unavailable";
    const username = payload.host?.username || "Unavailable";
    const rawMagicDns = payload.tailnet?.dns_name || "";
    const magicDns = rawMagicDns || "Unavailable";
    const tailnetIp = payload.tailnet?.ipv4 || "Unavailable";
    const sshHost = rawMagicDns.trim().replace(/\.$/, "") || (hostname !== "Unavailable" ? hostname : "");
    const sshCommand = username !== "Unavailable" && sshHost ? `ssh ${username}@${sshHost}` : "";
    elements.detailUsername.textContent = username;
    elements.detailHostname.textContent = hostname;
    elements.detailMagicDns.textContent = magicDns;
    elements.detailTailnetIp.textContent = tailnetIp;
    elements.sshCommand.textContent = sshCommand || "Unavailable";
    elements.sshCopyButton.disabled = !sshCommand;
    elements.sshCopyButton.dataset.command = sshCommand;

    const interfaces = Array.isArray(payload.host?.interfaces)
      ? payload.host.interfaces.filter(isPhysicalNetworkInterface)
      : [];
    elements.interfaceCount.textContent = `${interfaces.length} detected`;
    clearNode(elements.hostInterfaceList);

    if (interfaces.length === 0) {
      elements.hostInterfaceList.append(
        createElement("p", "host-interface-list__empty", "No physical interfaces reported."),
      );
    } else {
      for (const networkInterface of interfaces) {
        const state = normalizedStatus(networkInterface.state || "unknown");
        const type = String(networkInterface.type || networkInterface.kind || "network");
        const card = createElement("div", "host-interface-card");
        card.dataset.state = state;

        const identity = createElement("div", "host-interface-card__identity");
        identity.append(
          createElement("span", "host-interface-card__dot"),
          createElement(
            "span",
            "host-interface-card__name",
            networkInterface.name || "Unnamed interface",
          ),
          createElement("span", "host-interface-card__meta", `${type} · ${statusLabel(state)}`),
        );

        const addresses = createElement("div", "host-interface-card__addresses");
        for (const entry of interfaceAddressEntries(networkInterface)) {
          const address = createElement(
            "span",
            `host-interface-address${entry.unavailable ? " is-muted" : ""}`,
          );
          address.append(createElement("span", "", entry.address));
          if (entry.configured) {
            address.append(createElement("small", "", "configured"));
          }
          addresses.append(address);
        }
        card.append(identity, addresses);
        elements.hostInterfaceList.append(card);
      }
    }

    const url = dashboardUrl(payload);
    if (url) {
      elements.hostQrLink.href = url;
      elements.hostQrLink.removeAttribute("aria-disabled");
      elements.hostQrUrl.textContent = "Open private dashboard";
      elements.hostQrLink.title = url;
    } else {
      elements.hostQrLink.removeAttribute("href");
      elements.hostQrLink.setAttribute("aria-disabled", "true");
      elements.hostQrUrl.textContent = "URL unavailable";
      elements.hostQrLink.removeAttribute("title");
    }
    renderQrCode(url);
  }

  function appendNotice(kind, title, message) {
    const notice = createElement("div", `notice notice--${kind}`);
    const icon = createElement("span", "notice__icon", kind === "error" ? "!" : "i");
    icon.setAttribute("aria-hidden", "true");
    const content = createElement("div", "notice__content");
    content.append(
      createElement("p", "notice__title", title),
      createElement("p", "notice__message", message),
    );
    notice.append(icon, content);
    elements.noticeRegion.append(notice);
  }

  function renderNotices(payload, refreshError = null) {
    clearNode(elements.noticeRegion);

    if (demoMode) {
      appendNotice(
        "info",
        "Preview data",
        "This is the frontend demo fixture, not live dev_stack status. Remove ?demo=1 when the API is available.",
      );
    }

    if (refreshError) {
      appendNotice(
        "error",
        "Latest refresh failed",
        `The last successful status remains visible. ${refreshError.message}`,
      );
    }

    if (Array.isArray(payload?.warnings)) {
      const warnings = payload.warnings.filter(
        (warning) => typeof warning === "string" && warning.trim() !== "",
      );
      if (warnings.length > 0) {
        appendNotice(
          "warning",
          "Some status details are unavailable",
          warnings.join(" · "),
        );
      }
    }

    if (!demoMode) {
      const generatedAt = Date.parse(payload?.generated_at);
      if (Number.isFinite(generatedAt) && Date.now() - generatedAt > STALE_AFTER_MS) {
        appendNotice(
          "warning",
          "Status may be stale",
          `The backend generated this snapshot at ${formatTimestamp(payload.generated_at)}.`,
        );
      }
    }
  }

  function summarizeServices(services) {
    const summary = {
      attention: 0,
      running: 0,
      stopped: 0,
      total: services.length,
    };

    for (const service of services) {
      const status = normalizedStatus(service.status);
      if (status === "running" || status === "healthy") {
        summary.running += 1;
      } else if (STOPPED_STATES.has(status)) {
        summary.stopped += 1;
      } else {
        summary.attention += 1;
      }
    }

    return summary;
  }

  function renderSummary(services) {
    const summary = summarizeServices(services);
    elements.runningCount.textContent = String(summary.running);
    elements.attentionCount.textContent = String(summary.attention);
    elements.stoppedCount.textContent = String(summary.stopped);
  }

  function serviceSummary(service) {
    if (typeof service.summary === "string" && service.summary.trim() !== "") {
      return service.summary;
    }

    const status = normalizedStatus(service.status);
    if (Array.isArray(service.instances)) {
      const running = service.instances.filter(
        (instance) => normalizedStatus(instance.status) === "running",
      ).length;
      return `${running} of ${service.instances.length} instances running.`;
    }
    return `Service is ${statusLabel(status)}.`;
  }

  function makeOpenLink(url, label, ariaLabel) {
    const href = safeUrl(url);
    if (!href) {
      return null;
    }

    const link = createElement("a", "open-link", label || "Open");
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", ariaLabel || `${label || "Open"} in a new tab`);
    return link;
  }

  function makeCredentialTrigger(service, instance, credential, label) {
    if (demoMode || !credential || typeof credential !== "object") {
      return null;
    }
    if (credential.available !== true) {
      return createElement("span", "credential-unavailable", "Password not saved");
    }
    const button = createElement("button", "credential-trigger", label || "Reveal password");
    button.type = "button";
    button.dataset.credentialService = service;
    if (instance) {
      button.dataset.credentialInstance = instance;
    }
    button.setAttribute(
      "aria-label",
      `Reveal password for ${instance || service}`,
    );
    return button;
  }

  function makeLifecycleButton(service, serviceName, action, instance = null) {
    const labels = {
      start: "Start",
      stop: "Stop",
      restart: "Restart",
      clean: "Clean",
      publish: "Publish",
      unpublish: "Unpublish",
      "profile-save": "Save profile",
      "profile-start": "Start",
      "profile-delete": "Delete",
    };
    const button = createElement(
      "button",
      `lifecycle-button lifecycle-button--${action}`,
      labels[action],
    );
    button.type = "button";
    button.dataset.serviceAction = action;
    button.dataset.serviceId = service;
    if (instance) {
      button.dataset.serviceInstance = instance;
    }
    button.dataset.idleLabel = labels[action];
    button.disabled = runtime.operationInFlight;
    button.setAttribute("aria-label", `${labels[action]} ${serviceName}`);
    return button;
  }

  function makeSingletonLifecycleActions(service, serviceName, status) {
    if (demoMode) {
      return [];
    }
    if (status === "not_found") {
      return [makeLifecycleButton(service, serviceName, "start")];
    }
    if (status === "stopped") {
      return [
        makeLifecycleButton(service, serviceName, "start"),
        makeLifecycleButton(service, serviceName, "clean"),
      ];
    }
    return [
      makeLifecycleButton(service, serviceName, "stop"),
      makeLifecycleButton(service, serviceName, "restart"),
    ];
  }

  function makeSingletonActionsMenu(service, serviceName, status, credentialTrigger, route = null) {
    const details = createElement("details", "service-actions-menu");
    const summary = createElement("summary", "service-actions-menu__trigger", "Actions");
    summary.setAttribute("aria-label", `${serviceName} actions`);
    const panel = createElement("div", "service-actions-menu__panel");

    if (credentialTrigger) {
      credentialTrigger.classList.add("service-actions-menu__item");
      panel.append(credentialTrigger);
    }
    for (const button of makeSingletonLifecycleActions(service, serviceName, status)) {
      button.classList.add("service-actions-menu__item");
      panel.append(button);
    }
    if (service === "go2rtc" && !STOPPED_STATES.has(status)) {
      const routeAction = route?.enabled === true && route?.present === true
        ? "unpublish"
        : "publish";
      const routeButton = makeLifecycleButton(service, serviceName, routeAction);
      routeButton.textContent = routeAction === "publish"
        ? "Publish with Tailscale"
        : "Unpublish from Tailscale";
      routeButton.dataset.idleLabel = routeButton.textContent;
      routeButton.classList.add("service-actions-menu__item");
      panel.append(routeButton);
    }
    details.append(summary, panel);
    return details;
  }

  function makeCodeServerActionsMenu(instance, status, credentialTrigger) {
    const menu = createElement("div", "service-actions-menu service-actions-menu--popover");
    const trigger = createElement("button", "service-actions-menu__trigger", "Actions");
    trigger.type = "button";
    trigger.dataset.actionsPopoverTrigger = "true";
    trigger.setAttribute("aria-label", `Actions for Code Server instance ${instance}`);
    trigger.setAttribute("aria-expanded", "false");
    const panel = createElement("div", "service-actions-menu__panel");
    panel.classList.add("service-actions-menu__panel--popover");
    panel.setAttribute("popover", "auto");

    if (credentialTrigger) {
      credentialTrigger.classList.add("service-actions-menu__item");
      panel.append(credentialTrigger);
    }
    const actions = status === "stopped" || status === "not_found"
      ? ["start"]
      : ["stop", "restart"];
    for (const action of actions) {
      const button = makeLifecycleButton("code-server", "Code Server", action, instance);
      button.classList.add("service-actions-menu__item");
      panel.append(button);
    }
    if (status === "running") {
      const saveButton = makeLifecycleButton("code-server", "Code Server profile", "profile-save", instance);
      saveButton.classList.add("service-actions-menu__item");
      panel.append(saveButton);
    }
    panel.addEventListener("toggle", () => {
      trigger.setAttribute("aria-expanded", panel.matches(":popover-open") ? "true" : "false");
    });
    menu.append(trigger, panel);
    return menu;
  }

  function positionActionsPopover(trigger, panel) {
    const viewportMargin = 12;
    const gap = 7;
    const triggerRect = trigger.getBoundingClientRect();
    const phoneLayout = window.matchMedia("(max-width: 470px)").matches;
    const panelWidth = phoneLayout
      ? Math.max(0, window.innerWidth - viewportMargin * 2)
      : Math.min(240, window.innerWidth - viewportMargin * 2);
    panel.style.width = `${panelWidth}px`;

    const panelHeight = panel.offsetHeight;
    if (phoneLayout) {
      panel.style.left = `${viewportMargin}px`;
      panel.style.top = `${Math.max(viewportMargin, window.innerHeight - panelHeight - viewportMargin)}px`;
      return;
    }

    const left = Math.min(
      window.innerWidth - panelWidth - viewportMargin,
      Math.max(viewportMargin, triggerRect.right - panelWidth),
    );
    const below = triggerRect.bottom + gap;
    const top = below + panelHeight <= window.innerHeight - viewportMargin
      ? below
      : Math.max(viewportMargin, triggerRect.top - panelHeight - gap);
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  }

  function makeCodeServerServiceActions(instances) {
    const stoppedCount = instances.filter(
      (instance) => normalizedStatus(instance.status) === "stopped",
    ).length;
    const details = createElement("details", "service-actions-menu");
    const summary = createElement("summary", "service-actions-menu__trigger", "Actions");
    summary.setAttribute("aria-label", "Code Server service actions");
    const panel = createElement("div", "service-actions-menu__panel");
    const startWorkspaceButton = createElement(
      "button",
      "lifecycle-button lifecycle-button--start service-actions-menu__item",
      "Start workspace",
    );
    startWorkspaceButton.type = "button";
    startWorkspaceButton.dataset.openWorkspaceDialog = "true";
    startWorkspaceButton.disabled = runtime.operationInFlight;
    panel.append(startWorkspaceButton);
    const cleanButton = makeLifecycleButton("code-server", "Code Server", "clean");
    cleanButton.textContent = `Clean stopped instances (${stoppedCount})`;
    cleanButton.dataset.idleLabel = cleanButton.textContent;
    cleanButton.dataset.stoppedCount = String(stoppedCount);
    cleanButton.disabled = runtime.operationInFlight || stoppedCount === 0;
    if (stoppedCount === 0) {
      cleanButton.dataset.permanentlyDisabled = "true";
    }
    cleanButton.classList.add("service-actions-menu__item");
    panel.append(cleanButton);
    details.append(summary, panel);
    return details;
  }

  function makeInstanceRow(instance) {
    const status = normalizedStatus(instance.status);
    const row = createElement("li", "instance-row");

    const primary = createElement("div", "instance-row__primary");
    const nameLine = createElement("div", "instance-row__name-line");
    nameLine.append(
      createElement("span", "instance-row__name", instance.name || "Unnamed instance"),
      makeStatusBadge(status),
    );
    primary.append(nameLine);

    const facts = [];
    if (Number.isInteger(instance.port)) {
      facts.push(`Port ${instance.port}`);
    }
    const uptime = formatDuration(instance.uptime_seconds);
    if (uptime) {
      facts.push(`Up ${uptime}`);
    }
    if (facts.length > 0) {
      primary.append(createElement("span", "instance-row__meta", facts.join(" · ")));
    }

    const context = createElement("div", "instance-row__context");
    context.append(
      createElement(
        "span",
        "instance-row__workspace",
        instance.workspace || "Workspace unavailable",
      ),
    );

    const actions = createElement("div", "instance-row__actions");
    const credentialTrigger = makeCredentialTrigger(
      "code-server",
      instance.name,
      instance.credential,
      "Show password",
    );
    const openLink = status === "running"
      ? makeOpenLink(
        instance.url,
        "Open",
        `Open ${instance.name || "code-server instance"} in a new tab`,
      )
      : null;
    actions.append(openLink || createElement("span", "unavailable-label", "Unavailable"));
    if (!demoMode) {
      actions.append(makeCodeServerActionsMenu(instance.name, status, credentialTrigger));
    }

    row.append(primary, context, actions);
    return row;
  }

  function makeInstanceList(instances) {
    const body = createElement("div", "service-card__body");
    if (instances.length === 0) {
      const empty = createElement("div", "empty-state");
      empty.append(
        createElement("span", "empty-state__mark", "0"),
        createElement("h3", "", "No managed instances"),
        createElement("p", "", "Code Server has no recorded instances yet."),
      );
      body.append(empty);
      return body;
    }

    const list = createElement("ul", "instance-list");
    for (const instance of instances) {
      list.append(makeInstanceRow(instance));
    }
    body.append(list);
    return body;
  }

  function makeSavedProfiles(profiles) {
    const section = createElement("details", "saved-profiles");
    const heading = createElement("summary", "saved-profiles__heading");
    heading.append(
      createElement("h4", "", "Saved profiles"),
      createElement("span", "saved-profiles__count", String(profiles.length)),
    );
    section.append(heading);
    if (profiles.length === 0) {
      section.append(createElement("p", "saved-profiles__empty", "Save a running instance to start it again later."));
      return section;
    }
    const tools = createElement("div", "saved-profiles__tools");
    const searchLabel = createElement("label", "saved-profiles__search");
    searchLabel.append(createElement("span", "visually-hidden", "Filter saved profiles"));
    const search = createElement("input", "");
    search.type = "search";
    search.placeholder = "Filter profiles…";
    search.autocomplete = "off";
    search.spellcheck = false;
    searchLabel.append(search);
    tools.append(searchLabel);
    section.append(tools);
    const list = createElement("ul", "saved-profile-list");
    for (const profile of profiles) {
      const row = createElement("li", "saved-profile-row");
      row.dataset.profileSearch = `${profile.name || ""} ${profile.workspace || ""}`.toLowerCase();
      const identity = createElement("div", "saved-profile-row__identity");
      identity.append(
        createElement("strong", "", profile.name || "Unnamed profile"),
        createElement("span", "", profile.workspace || "Workspace unavailable"),
      );
      const actions = createElement("div", "saved-profile-row__actions");
      actions.append(
        makeLifecycleButton("code-server", "saved Code Server profile", "profile-start", profile.name),
        makeLifecycleButton("code-server", "saved Code Server profile", "profile-delete", profile.name),
      );
      row.append(identity, actions);
      list.append(row);
    }
    const noResults = createElement("p", "saved-profiles__empty", "No saved profiles match that filter.");
    noResults.hidden = true;
    search.addEventListener("input", () => {
      const query = search.value.trim().toLowerCase();
      let visibleCount = 0;
      for (const row of list.children) {
        const matches = !query || row.dataset.profileSearch.includes(query);
        row.hidden = !matches;
        if (matches) {
          visibleCount += 1;
        }
      }
      noResults.hidden = visibleCount !== 0;
    });
    section.append(list, noResults);
    return section;
  }

  function makeCodeServerBody(instances, profiles) {
    const body = makeInstanceList(instances);
    body.append(makeSavedProfiles(profiles));
    return body;
  }

  function detailItem(label, value, mono = false) {
    const wrapper = createElement("div", "service-details__item");
    wrapper.append(
      createElement("dt", "", label),
      createElement("dd", mono ? "is-mono" : "", value || "—"),
    );
    return wrapper;
  }

  function makeServiceDetails(service) {
    const body = createElement("div", "service-card__body");
    const details = createElement("dl", "service-details");
    const bind = service.bind_host && Number.isInteger(service.port)
      ? `${service.bind_host}:${service.port}`
      : service.bind_host || (Number.isInteger(service.port) ? `Port ${service.port}` : "—");
    const routePath = service.route?.enabled === false
      ? "Disabled"
      : service.route?.path || "—";
    const routeState = service.route?.enabled === false
      ? "Off"
      : service.route?.present === true
        ? "Published"
        : service.route?.present === false
          ? "Not published"
          : "Unknown";
    const uptime = formatDuration(service.uptime_seconds);

    details.append(
      detailItem("Bind", bind, true),
      detailItem("Route", routePath, true),
      detailItem("Exposure", routeState),
      detailItem("Uptime", uptime ? `Up ${uptime}` : "—"),
    );
    body.append(details);
    return body;
  }

  function makeServiceCard(service) {
    const status = normalizedStatus(service.status);
    const hasInstances = Array.isArray(service.instances);
    const card = createElement(
      "article",
      `service-card${hasInstances ? " service-card--wide" : ""}`,
    );
    card.dataset.status = status;
    card.dataset.serviceId = service.id;

    const header = createElement("header", "service-card__header");
    const identity = createElement("div", "service-card__identity");
    const icon = createElement(
      "span",
      "service-icon",
      service.id === "filebrowser" ? null : SERVICE_ICONS[service.id] || "●",
    );
    icon.setAttribute("aria-hidden", "true");
    if (service.id === "filebrowser") {
      const image = createElement("img", "service-icon__image");
      image.src = "./filebrowser-logo.svg";
      image.alt = "";
      icon.append(image);
    }
    const titleGroup = createElement("div", "service-card__title-group");
    const heading = createElement("h3", "service-card__title", service.name || service.id || "Service");
    titleGroup.append(
      heading,
      createElement(
        "p",
        "service-card__description",
        service.description || "Managed by dev_stack",
      ),
    );
    identity.append(icon, titleGroup);

    const statusGroup = createElement("div", "service-card__status-group");
    const operationIndicator = createElement("div", "service-card__operation");
    operationIndicator.dataset.operationIndicator = "true";
    operationIndicator.setAttribute("role", "status");
    operationIndicator.setAttribute("aria-live", "polite");
    operationIndicator.hidden = true;
    const operationIcon = createElement("span", "service-card__operation-icon", "⏳");
    operationIcon.setAttribute("aria-hidden", "true");
    operationIndicator.append(
      operationIcon,
      createElement("span", "service-card__operation-label", "Working…"),
    );
    statusGroup.append(operationIndicator, makeStatusBadge(status));
    header.append(identity, statusGroup);

    const summary = createElement("p", "service-card__summary", serviceSummary(service));
    const body = hasInstances
      ? makeCodeServerBody(service.instances, Array.isArray(service.profiles) ? service.profiles : [])
      : makeServiceDetails(service);

    const footer = createElement("footer", "service-card__footer");
    const healthText = service.health?.message
      || (service.health?.state ? `Health: ${statusLabel(service.health.state)}` : "Health details unavailable");
    footer.append(createElement("span", "service-card__health", healthText));

    if (service.id === "code-server" && !demoMode) {
      footer.append(makeCodeServerServiceActions(service.instances));
    }

    if (!hasInstances) {
      const footerActions = createElement("div", "service-card__actions");
      const credentialTrigger = makeCredentialTrigger(
        service.id,
        null,
        service.credential,
        MANAGED_SINGLETON_SERVICES.has(service.id) ? "Show password" : "Password",
      );
      const openLink = status === "running"
        ? makeOpenLink(service.url, "Open service", `Open ${service.name || "service"} in a new tab`)
        : null;
      if (MANAGED_SINGLETON_SERVICES.has(service.id)) {
        footerActions.append(openLink || createElement("span", "unavailable-label", "No active URL"));
        if (!demoMode) {
          footerActions.append(
            makeSingletonActionsMenu(
              service.id,
              service.name,
              status,
              credentialTrigger,
              service.route,
            ),
          );
        }
      } else {
        if (credentialTrigger) {
          footerActions.append(credentialTrigger);
        }
        footerActions.append(openLink || createElement("span", "unavailable-label", "No active URL"));
      }
      footer.append(footerActions);
    }

    card.append(header, summary, body, footer);
    syncOperationCard(card);
    return card;
  }

  function renderServices(services) {
    clearNode(elements.serviceGrid);
    elements.serviceGrid.setAttribute("aria-busy", "false");

    if (services.length === 0) {
      const empty = createElement("div", "empty-state");
      empty.append(
        createElement("span", "empty-state__mark", "—"),
        createElement("h3", "", "No services reported"),
        createElement("p", "", "The status endpoint returned an empty service list."),
      );
      elements.serviceGrid.append(empty);
      return;
    }

    for (const service of services) {
      elements.serviceGrid.append(makeServiceCard(service));
    }
    syncOperationUi();
  }

  function renderHostContext(payload) {
    const hostname = payload.host.hostname || "Unknown host";
    const generatedAt = payload.generated_at;

    elements.hostName.textContent = hostname;
    elements.tailnetAddress.textContent = payload.tailnet?.ipv4
      || payload.tailnet?.dns_name
      || (payload.tailnet?.connected === false ? "Disconnected" : "Unavailable");
    elements.generatedAt.textContent = Number.isFinite(Date.parse(generatedAt))
      ? formatShortTime(generatedAt)
      : "unavailable";
    if (Number.isFinite(Date.parse(generatedAt))) {
      elements.generatedAt.dateTime = new Date(generatedAt).toISOString();
    } else {
      elements.generatedAt.removeAttribute("datetime");
    }
    renderHostNetwork(payload);
    setConnectionState(payload.tailnet);
  }

  function renderPayload(payload, refreshError = null) {
    renderHostContext(payload);
    renderSummary(payload.services);
    renderServices(payload.services);
    renderNotices(payload, refreshError);
    runtime.hasData = true;
    document.title = `Dev Stack Status · ${payload.host.hostname || "Unknown host"}`;
  }

  function renderInitialError(error) {
    setConnectionState(null, true);
    clearNode(elements.noticeRegion);
    appendNotice(
      "error",
      "Status endpoint unavailable",
      `${error.message} Use ?demo=1 to preview the frontend without a backend.`,
    );

    elements.hostName.textContent = "Unavailable";
    elements.tailnetAddress.textContent = "Unavailable";
    renderHostNetwork({ host: { interfaces: [] }, tailnet: null });
    elements.generatedAt.textContent = "—";
    elements.generatedAt.removeAttribute("datetime");
    elements.runningCount.textContent = "—";
    elements.attentionCount.textContent = "—";
    elements.stoppedCount.textContent = "—";

    clearNode(elements.serviceGrid);
    elements.serviceGrid.setAttribute("aria-busy", "false");
    const errorState = createElement("div", "error-state");
    errorState.append(
      createElement("span", "error-state__mark", "!"),
      createElement("h3", "", "Could not load service status"),
      createElement("p", "", "Check the status endpoint or open this page with ?demo=1 for a UI preview."),
    );
    elements.serviceGrid.append(errorState);
  }

  function credentialTargetLabel(target) {
    if (target.service === "code-server") {
      return `Code Server · ${target.instance}`;
    }
    if (target.service === "filebrowser") {
      return "File Browser · admin";
    }
    return target.service === "go2rtc" ? "go2rtc · admin" : "Porterminal";
  }

  function clearCredential({ close = false } = {}) {
    if (runtime.credentialClearTimer) {
      window.clearTimeout(runtime.credentialClearTimer);
      runtime.credentialClearTimer = null;
    }
    elements.credentialPassword.value = "";
    elements.credentialUsername.textContent = "";
    runtime.credentialTarget = null;
    if (close && elements.credentialDialog.open) {
      elements.credentialDialog.close();
    }
  }

  function showCredentialStage(stage) {
    elements.credentialContent.hidden = stage !== "credential";
    elements.credentialLoading.hidden = stage !== "loading";
  }

  function ensureCredentialDialog() {
    if (!elements.credentialDialog.open) {
      elements.credentialDialog.showModal();
    }
  }

  async function postCredentialJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Dev-Stack-Request": "credentials",
      },
      body: JSON.stringify(body),
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      payload = {};
    }
    if (!response.ok) {
      if (response.status === 401 || payload.error === "authentication_required") {
        clearCredential({ close: true });
        window.location.replace(loginUrl);
        throw new Error("Dashboard session expired.");
      }
      const error = new Error(payload.message || `Credential request failed with HTTP ${response.status}.`);
      error.code = payload.error || "request_failed";
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function postOperation(service, action, instance = null, workspace = null) {
    const body = { action };
    if (service === "code-server" && instance) {
      body.instance = instance;
    }
    if (service === "code-server" && action === "start-workspace" && workspace) {
      body.workspace = workspace;
    }
    const response = await fetch(actionUrls[service], {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Dev-Stack-Request": "operation",
      },
      body: JSON.stringify(body),
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      payload = {};
    }
    if (!response.ok) {
      if (response.status === 401 || payload.error === "authentication_required") {
        window.location.replace(loginUrl);
        throw new Error("Dashboard session expired.");
      }
      throw new Error(payload.message || `Service operation failed with HTTP ${response.status}.`);
    }
    return payload;
  }

  function operationProgressLabel(operation) {
    const progress = OPERATION_PROGRESS_LABELS[operation.action] || "Working…";
    return operation.instance ? `${progress} ${operation.instance}` : progress;
  }

  function syncOperationCard(card) {
    const operation = runtime.operation;
    const busy = runtime.operationInFlight
      && operation
      && card.dataset.serviceId === operation.service;
    card.classList.toggle("is-operation-busy", Boolean(busy));
    card.setAttribute("aria-busy", busy ? "true" : "false");
    const indicator = card.querySelector("[data-operation-indicator]");
    if (!indicator) {
      return;
    }
    indicator.hidden = !busy;
    const label = indicator.querySelector(".service-card__operation-label");
    if (busy && label) {
      label.textContent = operationProgressLabel(operation);
    }
  }

  function syncOperationUi() {
    for (const card of elements.serviceGrid.querySelectorAll("[data-service-id]")) {
      syncOperationCard(card);
    }
    for (const button of elements.serviceGrid.querySelectorAll("[data-service-action]")) {
      const isCurrent = runtime.operationInFlight
        && runtime.operation
        && button.dataset.serviceId === runtime.operation.service
        && button.dataset.serviceAction === runtime.operation.action
        && (button.dataset.serviceInstance || null) === runtime.operation.instance;
      button.disabled = runtime.operationInFlight
        || button.dataset.permanentlyDisabled === "true";
      button.textContent = isCurrent
        ? OPERATION_PROGRESS_LABELS[runtime.operation.action] || "Working…"
        : button.dataset.idleLabel || button.textContent;
    }
    for (const button of elements.serviceGrid.querySelectorAll("[data-open-workspace-dialog]")) {
      button.disabled = runtime.operationInFlight;
    }
    elements.refreshButton.disabled = runtime.operationInFlight || runtime.inFlight;
  }

  function setOperationBusy(service, action, instance, busy) {
    runtime.operationInFlight = busy;
    runtime.operation = busy ? { service, action, instance } : null;
    syncOperationUi();
  }

  async function runServiceAction(
    service,
    action,
    instance = null,
    stoppedCount = null,
    workspace = null,
  ) {
    if (runtime.operationInFlight) {
      return;
    }
    const serviceName = service === "filebrowser"
      ? "File Browser"
      : service === "porterminal"
        ? "Porterminal"
        : service === "go2rtc"
          ? "go2rtc"
          : "Code Server";
    const targetName = instance ? `${serviceName} instance “${instance}”` : serviceName;
    const confirmations = {
      stop: `Stop ${targetName}?`,
      restart: `Restart ${targetName}?`,
      publish: `Publish ${targetName} on this machine's private Tailscale network?`,
      unpublish: `Remove ${targetName} from this machine's private Tailscale network?`,
      clean: service === "filebrowser"
        ? "Clean the stopped File Browser manager state? The saved password and database will be kept."
        : service === "porterminal"
          ? "Clean the stopped Porterminal manager state? The saved password, configuration, and snippets will be kept."
          : service === "go2rtc"
            ? "Clean the stopped go2rtc container? Its configuration, image, and saved password will be kept."
            : `Clean ${stoppedCount} stopped Code Server ${stoppedCount === 1 ? "instance" : "instances"}? Their saved configuration and passwords will be removed.`,
      "profile-delete": `Delete saved Code Server profile “${instance}”? This will not stop a running instance.`,
    };
    if (confirmations[action] && !window.confirm(confirmations[action])) {
      return;
    }

    setOperationBusy(service, action, instance, true);
    elements.refreshStatus.textContent = `${OPERATION_PROGRESS_LABELS[action].replace("…", "")} ${targetName}…`;
    try {
      await postOperation(service, action, instance, workspace);
      await refreshStatus({ manual: true, allowDuringOperation: true });
    } catch (error) {
      await refreshStatus({ manual: true, allowDuringOperation: true });
      appendNotice("error", `Could not ${action} ${targetName}`, error.message);
      elements.refreshStatus.textContent = `${targetName} ${action} failed`;
    } finally {
      setOperationBusy(service, action, instance, false);
    }
  }

  function displayCredential(payload) {
    elements.credentialPassword.value = payload.password || "";
    if (payload.username) {
      elements.credentialUsername.textContent = payload.username;
      elements.credentialUsernameRow.hidden = false;
    } else {
      elements.credentialUsername.textContent = "";
      elements.credentialUsernameRow.hidden = true;
    }
    const clearAfter = Number(payload.clear_after_seconds) || 30;
    elements.credentialExpiry.textContent = `Clears automatically in ${clearAfter} seconds.`;
    showCredentialStage("credential");
    runtime.credentialClearTimer = window.setTimeout(() => clearCredential({ close: true }), clearAfter * 1000);
  }

  async function revealCredential(target) {
    runtime.credentialTarget = target;
    ensureCredentialDialog();
    elements.credentialTarget.textContent = credentialTargetLabel(target);
    elements.credentialLoading.textContent = "Requesting protected credential…";
    showCredentialStage("loading");
    try {
      const payload = await postCredentialJson(credentialUrl, target);
      displayCredential(payload);
    } catch (error) {
      elements.credentialLoading.textContent = error.message;
      showCredentialStage("loading");
    }
  }

  async function fetchStatus() {
    runtime.controller?.abort();
    runtime.controller = new AbortController();
    const timeout = window.setTimeout(() => runtime.controller.abort(), FETCH_TIMEOUT_MS);

    try {
      const response = await fetch(statusUrl, {
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: runtime.controller.signal,
      });
      if (response.status === 401) {
        window.location.replace(loginUrl);
        throw new Error("Dashboard session expired.");
      }
      if (!response.ok) {
        throw new Error(`Status request failed with HTTP ${response.status}.`);
      }
      const payload = validatePayload(await response.json());
      if (demoMode) {
        payload.generated_at = new Date().toISOString();
      }
      return payload;
    } catch (error) {
      if (error.name === "AbortError") {
        throw new Error(`Status request timed out after ${FETCH_TIMEOUT_MS / 1000} seconds.`);
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async function refreshStatus({ manual = false, allowDuringOperation = false } = {}) {
    if (runtime.inFlight || (runtime.operationInFlight && !allowDuringOperation)) {
      return;
    }

    runtime.inFlight = true;
    elements.refreshButton.disabled = true;
    elements.refreshButton.classList.add("is-loading");
    elements.refreshStatus.textContent = manual ? "Refreshing now…" : "Updating status…";

    try {
      const payload = await fetchStatus();
      renderPayload(payload);
      runtime.lastCompletedAt = Date.now();
      elements.refreshStatus.textContent = demoMode
        ? "Preview refreshed just now"
        : `Updated at ${formatShortTime(new Date().toISOString())}`;
    } catch (error) {
      if (runtime.hasData) {
        renderNotices(null, error);
        elements.refreshStatus.textContent = "Refresh failed; showing previous status";
      } else {
        renderInitialError(error);
        elements.refreshStatus.textContent = "Status unavailable";
      }
    } finally {
      runtime.inFlight = false;
      elements.refreshButton.disabled = runtime.operationInFlight;
      elements.refreshButton.classList.remove("is-loading");
    }
  }

  elements.refreshButton.addEventListener("click", () => {
    void refreshStatus({ manual: true });
  });

  elements.sshCopyButton.addEventListener("click", async () => {
    const command = elements.sshCopyButton.dataset.command || "";
    if (!command) {
      return;
    }
    try {
      await navigator.clipboard.writeText(command);
    } catch (_error) {
      const fallback = document.createElement("textarea");
      fallback.value = command;
      fallback.setAttribute("readonly", "");
      fallback.style.position = "fixed";
      fallback.style.opacity = "0";
      document.body.append(fallback);
      fallback.select();
      document.execCommand("copy");
      fallback.remove();
    }
    elements.sshCopyButton.classList.add("is-copied");
    elements.sshCopyButton.setAttribute("aria-label", "SSH command copied");
    elements.sshCopyButton.title = "Copied";
    window.setTimeout(() => {
      elements.sshCopyButton.classList.remove("is-copied");
      elements.sshCopyButton.setAttribute("aria-label", "Copy SSH command");
      elements.sshCopyButton.title = "Copy SSH command";
    }, 1600);
  });

  elements.serviceGrid.addEventListener("click", (event) => {
    const popoverTrigger = event.target.closest("[data-actions-popover-trigger]");
    if (popoverTrigger) {
      const panel = popoverTrigger.nextElementSibling;
      if (panel?.matches("[popover]")) {
        if (panel.matches(":popover-open")) {
          panel.hidePopover();
        } else {
          panel.showPopover();
          positionActionsPopover(popoverTrigger, panel);
        }
      }
      return;
    }
    const menuTrigger = event.target.closest(".service-actions-menu__trigger");
    if (menuTrigger) {
      for (const menu of elements.serviceGrid.querySelectorAll(".service-actions-menu[open]")) {
        if (menu !== menuTrigger.parentElement) {
          menu.removeAttribute("open");
        }
      }
    }
    const workspaceTrigger = event.target.closest("[data-open-workspace-dialog]");
    if (workspaceTrigger) {
      workspaceTrigger.closest("details")?.removeAttribute("open");
      elements.workspaceForm.reset();
      elements.workspaceDialog.showModal();
      elements.workspaceInstance.focus();
      return;
    }
    const lifecycleTrigger = event.target.closest("[data-service-action]");
    if (lifecycleTrigger) {
      lifecycleTrigger.closest("details")?.removeAttribute("open");
      lifecycleTrigger.closest("[popover]")?.hidePopover();
      void runServiceAction(
        lifecycleTrigger.dataset.serviceId,
        lifecycleTrigger.dataset.serviceAction,
        lifecycleTrigger.dataset.serviceInstance || null,
        Number(lifecycleTrigger.dataset.stoppedCount) || null,
      );
      return;
    }
    const trigger = event.target.closest("[data-credential-service]");
    if (!trigger) {
      return;
    }
    trigger.closest("details")?.removeAttribute("open");
    trigger.closest("[popover]")?.hidePopover();
    void revealCredential({
      service: trigger.dataset.credentialService,
      instance: trigger.dataset.credentialInstance || null,
    });
  });

  elements.credentialClose.addEventListener("click", () => clearCredential({ close: true }));
  elements.credentialHide.addEventListener("click", () => clearCredential({ close: true }));
  elements.credentialDialog.addEventListener("close", () => clearCredential());
  elements.credentialDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    clearCredential({ close: true });
  });
  elements.credentialCopy.addEventListener("click", async () => {
    if (!elements.credentialPassword.value) {
      return;
    }
    try {
      await navigator.clipboard.writeText(elements.credentialPassword.value);
      elements.credentialCopy.textContent = "Copied";
      window.setTimeout(() => {
        elements.credentialCopy.textContent = "Copy password";
      }, 1600);
    } catch (_error) {
      elements.credentialExpiry.textContent = "Clipboard access failed; select and copy the password manually.";
      elements.credentialPassword.select();
    }
  });
  function closeWorkspaceDialog() {
    if (elements.workspaceDialog.open) {
      elements.workspaceDialog.close();
    }
  }

  elements.workspaceClose.addEventListener("click", closeWorkspaceDialog);
  elements.workspaceCancel.addEventListener("click", closeWorkspaceDialog);
  elements.workspaceDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeWorkspaceDialog();
  });
  elements.workspaceForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const instance = elements.workspaceInstance.value.trim().toLowerCase();
    const workspace = elements.workspacePath.value.trim();
    if (!/^[a-z0-9-]+$/.test(instance)) {
      elements.workspaceInstance.setCustomValidity("Use lowercase letters, numbers, and hyphens only.");
      elements.workspaceInstance.reportValidity();
      return;
    }
    elements.workspaceInstance.setCustomValidity("");
    if (!workspace.startsWith("/")) {
      elements.workspacePath.setCustomValidity("Enter a full absolute path beginning with /.");
      elements.workspacePath.reportValidity();
      return;
    }
    elements.workspacePath.setCustomValidity("");
    closeWorkspaceDialog();
    void runServiceAction("code-server", "start-workspace", instance, null, workspace);
  });
  elements.workspaceInstance.addEventListener("input", () => elements.workspaceInstance.setCustomValidity(""));
  elements.workspacePath.addEventListener("input", () => elements.workspacePath.setCustomValidity(""));
  elements.logoutButton.addEventListener("click", async () => {
    elements.logoutButton.disabled = true;
    clearCredential({ close: true });
    try {
      await postCredentialJson(logoutUrl, {});
    } catch (_error) {
      // Navigating to login is the safe outcome even if the session already expired.
    } finally {
      window.location.replace(loginUrl);
    }
  });

  elements.connectionPill.addEventListener("click", () => {
    setHostMenuOpen(elements.connectionPill.getAttribute("aria-expanded") !== "true");
  });

  document.addEventListener("pointerdown", (event) => {
    for (const panel of document.querySelectorAll(
      ".service-actions-menu__panel--popover:popover-open",
    )) {
      if (!panel.closest(".service-actions-menu")?.contains(event.target)) {
        panel.hidePopover();
      }
    }
    if (!event.target.closest(".service-actions-menu")) {
      for (const menu of elements.serviceGrid.querySelectorAll(".service-actions-menu[open]")) {
        menu.removeAttribute("open");
      }
    }
    if (
      elements.connectionPill.getAttribute("aria-expanded") === "true"
      && !elements.hostMenu.contains(event.target)
    ) {
      setHostMenuOpen(false);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && elements.connectionPill.getAttribute("aria-expanded") === "true") {
      setHostMenuOpen(false);
      elements.connectionPill.focus();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" && elements.credentialDialog.open) {
      clearCredential({ close: true });
    }
    if (
      document.visibilityState === "visible"
      && runtime.lastCompletedAt
      && Date.now() - runtime.lastCompletedAt > REFRESH_INTERVAL_MS
    ) {
      void refreshStatus();
    }
  });

  window.setInterval(() => {
    if (document.visibilityState === "visible") {
      void refreshStatus();
    }
  }, REFRESH_INTERVAL_MS);

  void refreshStatus();
})();
