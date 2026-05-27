/**
 * MazdaAuthClient.js
 *
 * Handles authentication with Mazda's new Azure AD B2C OAuth2 system.
 * Replaces the broken auth in node-mymazda 1.1.1 which used the now-defunct
 * ptznwbh8.mazda.com usher endpoint.
 *
 * Flow:
 * 1. GET /authorize  → extract CSRF token + state + tx params
 * 2. POST to SelfAsserted with email + password + CSRF token
 * 3. GET /confirmed  → follow redirect to extract auth code
 * 4. POST to /token  → exchange code + code_verifier for tokens
 * 5. Cache tokens; use refresh_token to silently renew every 2 hours
 */

const crypto = require("crypto");
const https = require("https");
const http = require("http");
const url = require("url");

// ---------------------------------------------------------------------------
// Azure AD B2C constants (captured from MyMazda iOS app via Charles Proxy)
// ---------------------------------------------------------------------------
const TENANT_ID   = "47801034-62d1-49f6-831b-ffdcf04f13fc";
const CLIENT_ID   = "2daf581c-65c1-4fdb-b46a-efa98c6ba5b7";
const POLICY       = "b2c_1a_signin";    // used in authorize URL
const POLICY_UPPER = "B2C_1A_signin";    // used in SelfAsserted and confirmed URLs
const REDIRECT_URI = "msauth.com.mazdausa.mazdaiphone://auth";
const SCOPE       = "https://pduspb2c01.onmicrosoft.com/0728deea-be48-4382-9ef1-d4ff6d679ffa/cv openid profile offline_access";
const AUTH_BASE   = `https://na.id.mazda.com/${TENANT_ID}/${POLICY}`;
const TOKEN_URL   = `${AUTH_BASE}/oauth2/v2.0/token`;
const AUTHORIZE_URL = `${AUTH_BASE}/oauth2/v2.0/authorize`;

// App headers observed in Charles traffic
const APP_HEADERS = {
  "x-app-name":    "MyMazda",
  "x-app-ver":     "9.1.1",
  "x-client-OS":   "26.4.2",
  "x-client-DM":   "iPhone",
  "x-client-SKU":  "MSAL.iOS",
  "x-client-Ver":  "1.6.3",
  "User-Agent":    "MyMazda/1 CFNetwork/3860.500.112 Darwin/25.4.0",
  "Accept-Language": "en-US,en;q=0.9",
};

// ---------------------------------------------------------------------------
// PKCE helpers
// ---------------------------------------------------------------------------
function generateCodeVerifier() {
  return crypto.randomBytes(32)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");
}

function generateCodeChallenge(verifier) {
  return crypto.createHash("sha256")
    .update(verifier)
    .digest("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");
}

function generateState() {
  return crypto.randomBytes(24).toString("base64")
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------
function parseSetCookies(headers) {
  const cookies = {};
  const raw = headers["set-cookie"] || [];
  for (const c of raw) {
    const [pair] = c.split(";");
    const [name, ...rest] = pair.split("=");
    cookies[name.trim()] = rest.join("=").trim();
  }
  return cookies;
}

function serializeCookies(cookieObj) {
  return Object.entries(cookieObj)
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
}

function httpsGet(reqUrl, headers = {}, followRedirects = true, cookieJar = {}) {
  return new Promise((resolve, reject) => {
    const parsed = new url.URL(reqUrl);
    const options = {
      hostname: parsed.hostname,
      path: parsed.pathname + parsed.search,
      method: "GET",
      headers: {
        ...APP_HEADERS,
        ...headers,
        "Cookie": serializeCookies(cookieJar),
      },
    };

    const req = https.request(options, (res) => {
      // Merge new cookies into jar
      const newCookies = parseSetCookies(res.headers);
      Object.assign(cookieJar, newCookies);

      let body = "";
      res.on("data", chunk => body += chunk);
      res.on("end", () => {
        if (followRedirects && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          const location = res.headers.location;
  
          // Custom app scheme redirect — this contains our auth code
          if (location.startsWith("msauth.")) {
            resolve({ statusCode: res.statusCode, body, headers: res.headers, redirectUrl: location, cookieJar });
            return;
          }
        
          const next = location.startsWith("http")
            ? location
            : `https://${parsed.hostname}${location}`;
          
          httpsGet(next, headers, true, cookieJar).then(resolve).catch(reject);
        } else {
          resolve({ statusCode: res.statusCode, body, headers: res.headers, cookieJar });
        }
      });
    });

    req.on("error", reject);
    req.end();
  });
}

function httpsPost(reqUrl, postData, headers = {}, cookieJar = {}) {
  return new Promise((resolve, reject) => {
    const parsed = new url.URL(reqUrl);
    const body = typeof postData === "string" ? postData : new url.URLSearchParams(postData).toString();

    const options = {
      hostname: parsed.hostname,
      path: parsed.pathname + parsed.search,
      method: "POST",
      headers: {
        ...APP_HEADERS,
        ...headers,
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": Buffer.byteLength(body),
        "Cookie": serializeCookies(cookieJar),
      },
    };

    const req = https.request(options, (res) => {
      const newCookies = parseSetCookies(res.headers);
      Object.assign(cookieJar, newCookies);

      let respBody = "";
      res.on("data", chunk => respBody += chunk);
      res.on("end", () => {
        resolve({ statusCode: res.statusCode, body: respBody, headers: res.headers, cookieJar });
      });
    });

    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Auth flow
// ---------------------------------------------------------------------------
async function extractAuthParams(htmlBody) {
  // Extract CSRF token, tx (StateProperties), and settings from Azure B2C page
  const csrfMatch = htmlBody.match(/"csrf":\s*"([^"]+)"/);
  const txMatch   = htmlBody.match(/StateProperties=([A-Za-z0-9_\-]+)/);
  const settingsMatch = htmlBody.match(/var SETTINGS\s*=\s*({.+?});/s) ||
                        htmlBody.match(/var\s+SETTINGS\s*=\s*(\{[\s\S]*?\});/);

  if (!csrfMatch) throw new Error("Could not extract CSRF token from auth page");
  if (!txMatch)   throw new Error("Could not extract tx/StateProperties from auth page");

  return {
    csrf: csrfMatch[1],
    tx:   `StateProperties=${txMatch[1]}`,
  };
}

async function performLogin(email, password) {
  console.log("[Auth] Starting Azure B2C login flow...");
  const cookieJar = {};

  // Step 1 — GET /authorize to initiate flow and get CSRF token
  const codeVerifier  = generateCodeVerifier();
  const codeChallenge = generateCodeChallenge(codeVerifier);
  const state         = generateState();

  const authorizeParams = new url.URLSearchParams({
    "x-app-name":               "MyMazda",
    "login_hint":               email,
    "x-client-Ver":             "1.6.3",
    "ui_locales":               "en-US",
    "country":                  "US",
    "phone_number_min_length":  "10",
    "response_type":            "code",
    "code_challenge_method":    "S256",
    "x-app-ver":                "9.1.1",
    "redirect_uri":             REDIRECT_URI,
    "x-client-CPU":             "64",
    "haschrome":                "1",
    "state":                    state,
    "return-client-request-id": "true",
    "international_phone_code_list": "mnao",
    "scope":                    SCOPE,
    "x-client-SKU":             "MSAL.iOS",
    "email_domain_restrict":    "none",
    "client_id":                CLIENT_ID,
    "x-client-OS":              "26.4.2",
    "client_info":              "1",
    "x-client-DM":              "iPhone",
    "default_international_phone_code": "US",
    "code_challenge":           codeChallenge,
    "email_verify_flg":         "true",
    "phone_number_max_length":  "10",
  });

  const authorizeFullUrl = `${AUTHORIZE_URL}?${authorizeParams.toString()}`;
  console.log("[Auth] Step 1: GET /authorize");
  const authorizeResp = await httpsGet(authorizeFullUrl, {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
  }, true, cookieJar);

  const { csrf, tx } = await extractAuthParams(authorizeResp.body);
  console.log("[Auth] Step 1 complete, CSRF token obtained");

  // Step 2 — POST credentials to SelfAsserted
  const selfAssertedUrl = `${AUTH_BASE}/api/CombinedSigninAndSignup/unified?` +
    `tx=${encodeURIComponent(tx)}&p=${POLICY}`;

  console.log("[Auth] Step 2: POST credentials to SelfAsserted");
  const credResp = await httpsPost(
    `https://na.id.mazda.com/${TENANT_ID}/${POLICY_UPPER}/SelfAsserted?tx=${encodeURIComponent(tx)}&p=${POLICY}`,
    {
      request_type: "RESPONSE",
      signInName:   email,
      password:     password,
    },
    {
      "x-csrf-token": csrf,
      "Referer":      authorizeFullUrl,
      "Accept":       "application/json, text/javascript, */*; q=0.01",
      "X-Requested-With": "XMLHttpRequest",
    },
    cookieJar
  );

  if (credResp.statusCode !== 200) {
    throw new Error(`Credential submission failed with status ${credResp.statusCode}: ${credResp.body}`);
  }

  // Check for error in response (wrong password etc.)
  try {
    const credJson = JSON.parse(credResp.body);
    if (credJson.status !== "200") {
      throw new Error(`Login rejected: ${credJson.message || JSON.stringify(credJson)}`);
    }
  } catch (e) {
    if (e.message.startsWith("Login rejected")) throw e;
    // Non-JSON response is OK here
  }
  console.log("[Auth] Step 2 complete, credentials accepted");

  // Step 3 — GET /confirmed to get the auth code via redirect
  const confirmedUrl = `https://na.id.mazda.com/${TENANT_ID}/B2C_1A_signin/api/CombinedSigninAndSignup/confirmed` +
    `?rememberMe=true&csrf_token=${encodeURIComponent(csrf)}&tx=${encodeURIComponent(tx)}&p=${POLICY}`;

  console.log("[Auth] Step 3: GET /confirmed to obtain auth code");
  const confirmedResp = await httpsGet(confirmedUrl, {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": authorizeFullUrl,
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
  }, true, cookieJar);

  // The redirect will be to msauth://... containing the code
  const redirectUrl = confirmedResp.redirectUrl || confirmedResp.headers?.location || "";
  const codeMatch = redirectUrl.match(/[?&]code=([^&]+)/);
  if (!codeMatch) {
    throw new Error(`Could not extract auth code from redirect: ${redirectUrl}`);
  }
  const authCode = decodeURIComponent(codeMatch[1]);
  console.log("[Auth] Step 3 complete, auth code obtained");

  // Step 4 — Exchange auth code for tokens
  console.log("[Auth] Step 4: POST to /token endpoint");
  const tokenResp = await httpsPost(
    `${TOKEN_URL}?country=US&email_verify_flg=true&phone_number_min_length=10&ui_locales=en-US&login_hint=${encodeURIComponent(email)}`,
    {
      client_info:    "1",
      scope:          SCOPE,
      code:           authCode,
      grant_type:     "authorization_code",
      code_verifier:  codeVerifier,
      redirect_uri:   REDIRECT_URI,
      client_id:      CLIENT_ID,
    },
    {
      "Accept": "application/json",
      "Referer": authorizeFullUrl,
    },
    cookieJar
  );

  if (tokenResp.statusCode !== 200) {
    throw new Error(`Token exchange failed with status ${tokenResp.statusCode}: ${tokenResp.body}`);
  }

  const tokens = JSON.parse(tokenResp.body);
  console.log("[Auth] Step 4 complete — authenticated successfully!");
  console.log(`[Auth] Access token expires in ${tokens.expires_in}s, refresh token expires in ${tokens.refresh_token_expires_in}s`);

  return tokens; // { access_token, refresh_token, expires_in, ... }
}

async function refreshAccessToken(refreshToken) {
  console.log("[Auth] Refreshing access token using refresh token...");
  const tokenResp = await httpsPost(TOKEN_URL, {
    grant_type:    "refresh_token",
    refresh_token: refreshToken,
    client_id:     CLIENT_ID,
    scope:         SCOPE,
  }, {
    "Accept": "application/json",
  }, {});

  if (tokenResp.statusCode !== 200) {
    throw new Error(`Token refresh failed with status ${tokenResp.statusCode}: ${tokenResp.body}`);
  }

  const tokens = JSON.parse(tokenResp.body);
  console.log(`[Auth] Token refreshed successfully, expires in ${tokens.expires_in}s`);
  return tokens;
}

// ---------------------------------------------------------------------------
// Exported class — drop-in token manager for server.js
// ---------------------------------------------------------------------------
class MazdaAuthClient {
  constructor(email, password) {
    this.email    = email;
    this.password = password;
    this.accessToken   = null;
    this.refreshToken  = null;
    this.tokenExpiry   = null;
    this.refreshTimer  = null;
  }

  async getAccessToken() {
    if (this.accessToken && this.tokenExpiry && Date.now() < this.tokenExpiry - 60000) {
      return this.accessToken;
    }
    if (this.refreshToken) {
      try {
        const tokens = await refreshAccessToken(this.refreshToken);
        this._storeTokens(tokens);
        return this.accessToken;
      } catch (err) {
        console.warn("[Auth] Refresh token failed, re-doing full login:", err.message);
      }
    }
    const tokens = await performLogin(this.email, this.password);
    this._storeTokens(tokens);
    return this.accessToken;
  }

  _storeTokens(tokens) {
    this.accessToken  = tokens.access_token;
    this.refreshToken = tokens.refresh_token || this.refreshToken;
    // Schedule refresh 5 minutes before expiry
    const expiresInMs = (tokens.expires_in - 300) * 1000;
    this.tokenExpiry  = Date.now() + expiresInMs;

    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(async () => {
      try {
        console.log("[Auth] Proactive token refresh triggered");
        const newTokens = await refreshAccessToken(this.refreshToken);
        this._storeTokens(newTokens);
      } catch (err) {
        console.error("[Auth] Proactive refresh failed:", err.message);
        // Will fall back to full login on next getAccessToken() call
        this.accessToken = null;
      }
    }, expiresInMs);
  }
}

module.exports = MazdaAuthClient;