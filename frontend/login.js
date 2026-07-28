"use strict";

(() => {
  const form = document.querySelector("#login-form");
  const password = document.querySelector("#master-password");
  const button = document.querySelector("#login-button");
  const message = document.querySelector("#form-message");
  const loginUrl = new URL("./api/session/login", document.baseURI);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    message.textContent = "";
    button.disabled = true;
    button.textContent = "Signing in…";
    try {
      const response = await fetch(loginUrl, {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Dev-Stack-Request": "credentials",
        },
        body: JSON.stringify({ password: password.value }),
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = {};
      }
      if (!response.ok) {
        throw new Error(payload.message || `Sign in failed with HTTP ${response.status}.`);
      }
      password.value = "";
      window.location.replace("./");
    } catch (error) {
      password.select();
      message.textContent = error.message;
      button.disabled = false;
      button.textContent = "Sign in";
    }
  });
})();
