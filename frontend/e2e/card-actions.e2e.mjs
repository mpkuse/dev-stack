import assert from "node:assert/strict";
import { createReadStream } from "node:fs";
import { access } from "node:fs/promises";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { chromium } from "playwright";

const frontendDirectory = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const chromeExecutable = process.env.PLAYWRIGHT_CHROME_PATH || "/usr/bin/google-chrome";
const staticFiles = new Map([
  ["/dev-stack/", ["index.html", "text/html; charset=utf-8"]],
  ["/dev-stack/index.html", ["index.html", "text/html; charset=utf-8"]],
  ["/dev-stack/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/dev-stack/qrcode.js", ["qrcode.js", "text/javascript; charset=utf-8"]],
  ["/dev-stack/styles.css", ["styles.css", "text/css; charset=utf-8"]],
  ["/dev-stack/filebrowser-logo.svg", ["filebrowser-logo.svg", "image/svg+xml"]],
]);

let statusPayload = makeStatusPayload();
const operationRequests = [];
const credentialRequests = [];

function makeStatusPayload() {
  return {
    schema_version: 1,
    generated_at: "2026-07-28T10:45:00+02:00",
    host: {
      hostname: "playwright-host",
      display_name: "Playwright host",
      username: "tester",
      interfaces: [],
    },
    tailnet: {
      connected: true,
      dns_name: "playwright-host.example.ts.net",
      ipv4: "100.64.0.10",
      dashboard_url: "https://playwright-host.example.ts.net/dev-stack/",
    },
    services: [
      {
        id: "code-server",
        name: "Code Server",
        description: "Browser-based development workspaces",
        status: "running",
        summary: "One workspace is running and one is stopped.",
        health: { state: "healthy", message: "1 running · 1 stopped" },
        instances: [
          {
            name: "bin",
            status: "running",
            workspace: "/home/tester/.bin",
            port: 9341,
            uptime_seconds: 120,
            url: "https://playwright-host.example.ts.net/code-server-manager/bin/",
            credential: { available: true, username: null },
          },
          {
            name: "archive",
            status: "stopped",
            workspace: "/home/tester/archive",
            port: 9342,
            uptime_seconds: null,
            url: null,
            credential: { available: true, username: null },
          },
        ],
        profiles: [
          {
            name: "saved-work",
            workspace: "/home/tester/work",
          },
        ],
      },
      singletonService({
        id: "filebrowser",
        name: "File Browser",
        port: 9900,
        url: "https://playwright-host.example.ts.net/filebrowser/",
        routePath: "/filebrowser",
        routeEnabled: true,
        routePresent: true,
        username: "admin",
      }),
      singletonService({
        id: "porterminal",
        name: "Porterminal",
        port: 9444,
        url: null,
        routePath: "/",
        routeEnabled: false,
        routePresent: false,
        username: null,
      }),
      singletonService({
        id: "go2rtc",
        name: "go2rtc",
        port: 1984,
        url: "https://playwright-host.example.ts.net/go2rtc/",
        routePath: "/go2rtc",
        routeEnabled: true,
        routePresent: true,
        username: "admin",
      }),
    ],
  };
}

function singletonService({
  id,
  name,
  port,
  url,
  routePath,
  routeEnabled,
  routePresent,
  username,
}) {
  return {
    id,
    name,
    description: `${name} test service`,
    status: "running",
    summary: `${name} is running.`,
    bind_host: "127.0.0.1",
    port,
    uptime_seconds: 60,
    url,
    route: {
      enabled: routeEnabled,
      present: routePresent,
      path: routePath,
    },
    credential: {
      available: true,
      username,
    },
    health: {
      state: "healthy",
      message: "Process, listener, health check, and route agree",
    },
  };
}

function jsonResponse(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    "Cache-Control": "no-store",
    "Content-Length": Buffer.byteLength(body),
    "Content-Type": "application/json; charset=utf-8",
  });
  response.end(body);
}

async function readJsonBody(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf-8") || "{}");
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  if (request.method === "GET" && url.pathname === "/dev-stack/api/status") {
    jsonResponse(response, 200, statusPayload);
    return;
  }
  if (request.method === "POST" && url.pathname === "/dev-stack/api/credentials/reveal") {
    const body = await readJsonBody(request);
    credentialRequests.push(body);
    jsonResponse(response, 200, {
      ...body,
      username: body.service === "filebrowser" || body.service === "go2rtc"
        ? "admin"
        : null,
      password: `${body.service}-${body.instance || "singleton"}-password`,
      clear_after_seconds: 30,
    });
    return;
  }
  const actionMatch = url.pathname.match(
    /^\/dev-stack\/api\/services\/(code-server|filebrowser|porterminal|go2rtc)\/actions$/,
  );
  if (request.method === "POST" && actionMatch) {
    const body = await readJsonBody(request);
    operationRequests.push({ service: actionMatch[1], ...body });
    if (actionMatch[1] === "porterminal" && ["start", "restart"].includes(body.action)) {
      const porterminal = statusPayload.services.find((service) => service.id === "porterminal");
      porterminal.status = "running";
      porterminal.url = "https://playwright-host.example.ts.net/";
      porterminal.uptime_seconds = 1;
      porterminal.route.enabled = true;
      porterminal.route.present = true;
    }
    jsonResponse(response, 200, {
      service: actionMatch[1],
      action: body.action,
      status: "completed",
    });
    return;
  }
  if (request.method === "POST" && url.pathname === "/dev-stack/api/session/logout") {
    jsonResponse(response, 200, { status: "logged_out" });
    return;
  }
  const staticEntry = staticFiles.get(url.pathname);
  if (request.method === "GET" && staticEntry) {
    const [relativePath, contentType] = staticEntry;
    const filePath = path.join(frontendDirectory, relativePath);
    const statFailure = await access(filePath).then(() => null, (error) => error);
    if (statFailure) {
      response.writeHead(404);
      response.end("not found");
      return;
    }
    response.writeHead(200, {
      "Cache-Control": "no-store",
      "Content-Type": contentType,
    });
    createReadStream(filePath).pipe(response);
    return;
  }
  response.writeHead(404);
  response.end("not found");
});

await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(0, "127.0.0.1", resolve);
});

const address = server.address();
const baseUrl = `http://127.0.0.1:${address.port}/dev-stack/`;
await access(chromeExecutable);
const browser = await chromium.launch({
  executablePath: chromeExecutable,
  headless: true,
  args: ["--no-sandbox"],
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
page.setDefaultTimeout(10_000);
page.on("dialog", (dialog) => dialog.accept());

function resetRequests() {
  operationRequests.length = 0;
  credentialRequests.length = 0;
}

async function loadDashboard(payload = makeStatusPayload()) {
  statusPayload = structuredClone(payload);
  resetRequests();
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-service-id="porterminal"]');
}

function card(service) {
  return page.locator(`[data-service-id="${service}"]`);
}

async function openSingletonMenu(service) {
  const serviceCard = card(service);
  await serviceCard.locator("summary.service-actions-menu__trigger").click();
  return serviceCard;
}

async function clickAndExpectOperation(button, expected) {
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && response.url().includes("/api/services/")
  ));
  await button.click();
  const response = await responsePromise;
  const request = response.request();
  assert.deepEqual(request.postDataJSON(), {
    action: expected.action,
    ...(expected.instance ? { instance: expected.instance } : {}),
    ...(expected.workspace ? { workspace: expected.workspace } : {}),
  });
  assert.deepEqual(operationRequests.at(-1), expected);
  await page.waitForSelector("#service-grid[aria-busy=false]");
}

async function singletonAction(service, action) {
  const serviceCard = await openSingletonMenu(service);
  const button = serviceCard.locator(`[data-service-action="${action}"]`);
  await button.waitFor({ state: "visible" });
  await clickAndExpectOperation(button, { service, action });
}

async function revealSingletonCredential(service) {
  const serviceCard = await openSingletonMenu(service);
  const requestPromise = page.waitForRequest((request) => (
    request.method() === "POST"
    && request.url().endsWith("/api/credentials/reveal")
  ));
  await serviceCard.locator(`[data-credential-service="${service}"]`).click();
  await requestPromise;
  await page.locator("#credential-dialog[open]").waitFor();
  await page.waitForFunction(() => (
    document.querySelector("#credential-password")?.value.length > 0
  ));
  assert.equal(
    await page.locator("#credential-password").inputValue(),
    `${service}-singleton-password`,
  );
  assert.deepEqual(credentialRequests.at(-1), {
    service,
    instance: null,
  });
  await page.locator("#credential-hide").click();
}

async function testSingletonCard(service, supportsRouteAction) {
  const runningPayload = makeStatusPayload();
  await loadDashboard(runningPayload);
  const runningService = runningPayload.services.find((item) => item.id === service);
  const openLink = card(service).locator("a.open-link");
  if (runningService.url) {
    assert.equal(await openLink.getAttribute("href"), runningService.url);
  } else {
    await card(service).getByText("No active URL", { exact: true }).waitFor();
  }
  if (service === "porterminal") {
    assert.equal(
      await card(service).locator(
        '[data-service-action="publish"], [data-service-action="unpublish"]',
      ).count(),
      0,
    );
  }

  await revealSingletonCredential(service);
  await singletonAction(service, "restart");
  await singletonAction(service, "stop");

  if (supportsRouteAction) {
    const routeAction = runningService.route.enabled && runningService.route.present
      ? "unpublish"
      : "publish";
    await singletonAction(service, routeAction);
  }

  const stoppedPayload = makeStatusPayload();
  const stoppedService = stoppedPayload.services.find((item) => item.id === service);
  stoppedService.status = "stopped";
  stoppedService.url = null;
  stoppedService.uptime_seconds = null;
  stoppedService.route.present = false;
  await loadDashboard(stoppedPayload);
  await singletonAction(service, "start");
  if (service === "porterminal") {
    assert.equal(
      await card(service).locator("a.open-link").getAttribute("href"),
      "https://playwright-host.example.ts.net/",
    );
    await card(service).getByText("Open service", { exact: true }).waitFor();
  }
  await loadDashboard(stoppedPayload);
  await singletonAction(service, "clean");
}

async function codeServerInstanceAction(instance, action) {
  const instanceRow = card("code-server")
    .locator("li.instance-row")
    .filter({ has: page.locator(".instance-row__name", { hasText: instance }) });
  await instanceRow
    .locator(`[aria-label="Actions for Code Server instance ${instance}"]`)
    .click();
  const button = instanceRow.locator(`[data-service-action="${action}"]`);
  await button.waitFor({ state: "visible" });
  await clickAndExpectOperation(button, {
    service: "code-server",
    action,
    instance,
  });
}

async function testCodeServerCard() {
  await loadDashboard();
  const runningRow = card("code-server")
    .locator("li.instance-row")
    .filter({ hasText: "bin" });
  assert.equal(
    await runningRow.locator("a.open-link").getAttribute("href"),
    "https://playwright-host.example.ts.net/code-server-manager/bin/",
  );

  await runningRow.locator('[aria-label="Actions for Code Server instance bin"]').click();
  const credentialRequest = page.waitForRequest((request) => (
    request.method() === "POST"
    && request.url().endsWith("/api/credentials/reveal")
  ));
  await runningRow.locator('[data-credential-service="code-server"]').click();
  await credentialRequest;
  await page.locator("#credential-dialog[open]").waitFor();
  await page.waitForFunction(() => (
    document.querySelector("#credential-password")?.value.length > 0
  ));
  assert.equal(
    await page.locator("#credential-password").inputValue(),
    "code-server-bin-password",
  );
  await page.locator("#credential-hide").click();

  await codeServerInstanceAction("bin", "stop");
  await codeServerInstanceAction("bin", "restart");
  await codeServerInstanceAction("bin", "profile-save");
  await codeServerInstanceAction("archive", "start");

  const serviceActions = card("code-server")
    .locator('summary[aria-label="Code Server service actions"]');
  await serviceActions.click();
  await clickAndExpectOperation(
    card("code-server").locator('[data-service-action="clean"]'),
    { service: "code-server", action: "clean" },
  );

  await serviceActions.click();
  await card("code-server").locator("[data-open-workspace-dialog]").click();
  await page.locator("#workspace-instance").fill("new-workspace");
  await page.locator("#workspace-path").fill("/home/tester/new-workspace");
  await clickAndExpectOperation(
    page.locator('#workspace-form button[type="submit"]'),
    {
      service: "code-server",
      action: "start-workspace",
      instance: "new-workspace",
      workspace: "/home/tester/new-workspace",
    },
  );

  const profiles = card("code-server").locator("details.saved-profiles");
  await profiles.locator("summary").click();
  await clickAndExpectOperation(
    profiles.locator('[data-service-action="profile-start"]'),
    {
      service: "code-server",
      action: "profile-start",
      instance: "saved-work",
    },
  );
  await profiles.locator("summary").click();
  await clickAndExpectOperation(
    profiles.locator('[data-service-action="profile-delete"]'),
    {
      service: "code-server",
      action: "profile-delete",
      instance: "saved-work",
    },
  );
}

const tests = [
  ["Code Server card actions", testCodeServerCard],
  ["File Browser card actions", () => testSingletonCard("filebrowser", false)],
  ["Porterminal card actions", () => testSingletonCard("porterminal", false)],
  ["go2rtc card actions", () => testSingletonCard("go2rtc", true)],
];

let failed = false;
try {
  for (const [name, test] of tests) {
    try {
      await test();
      process.stdout.write(`✓ ${name}\n`);
    } catch (error) {
      failed = true;
      process.stderr.write(`✗ ${name}\n${error.stack || error}\n`);
      break;
    }
  }
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}

if (failed) {
  process.exitCode = 1;
} else {
  process.stdout.write(`All ${tests.length} Playwright card-action tests passed.\n`);
}
