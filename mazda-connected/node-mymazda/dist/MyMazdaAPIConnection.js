"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const got_1 = __importDefault(require("got"));
const log4js_1 = __importDefault(require("log4js"));
const CryptoUtils_1 = __importDefault(require("./CryptoUtils"));
const SensorDataBuilder_1 = __importDefault(require("./sensordata/SensorDataBuilder"));

const REGION_CONFIG = {
    "MNAO": {
        appCode: "202007270941270111799",
        baseUrl: "https://0cxo7m58.mazda.com/prod/",
        remoteUrl: "https://hgs2ivna.mazda.com/",
        usherUrl: "https://ptznwbh8.mazda.com/appapi/v1/"
    },
    "MME": {
        appCode: "202008100250281064816",
        baseUrl: "https://e9stj7g7.mazda.com/prod/",
        remoteUrl: "https://e9stj7g7.mazda.com/prod/",
        usherUrl: "https://rz97suam.mazda.com/appapi/v1/"
    },
    "MJO": {
        appCode: "202009170613074283422",
        baseUrl: "https://wcs9p6wj.mazda.com/prod/",
        remoteUrl: "https://wcs9p6wj.mazda.com/prod/",
        usherUrl: "https://c5ulfwxr.mazda.com/appapi/v1/"
    }
};

const IV = "0102030405060708";
const SIGNATURE_MD5 = "C383D8C4D279B78130AD52DC71D95CAA";
const APP_PACKAGE_ID = "com.interrait.mymazda";
const USER_AGENT_BASE_API = "MyMazda-Android/9.1.1";
const USER_AGENT_USHER_API = "MyMazda/9.1.1 (Google Pixel 3a; Android 11)";
const APP_OS = "Android";
const APP_VERSION = "9.1.1";
const USHER_SDK_VERSION = "11.2.0400.001";
const MAX_RETRIES = 4;
const logger = log4js_1.default.getLogger();

function isSuccessfulEncryptedAPIResponse(responseBody) {
    return (responseBody?.state === "S");
}
function isErrorEncryptedAPIResponse(responseBody) {
    return (responseBody?.state === "F" && typeof responseBody?.errorCode === "number" && responseBody?.errorCode !== 0);
}
function searchParamsToString(searchParams) {
    let str = "";
    for (let key in searchParams) {
        if (typeof searchParams[key] === "undefined" || searchParams[key] === null)
            continue;
        if (str.length > 0)
            str += "&";
        str += key;
        str += "=";
        str += searchParams[key];
    }
    return str;
}
function isURLSearchParams(obj) {
    return obj instanceof URLSearchParams;
}
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

class MyMazdaAPIConnection {
    constructor(email, password, region) {
        this.email = email;
        this.password = password;
        if (region in REGION_CONFIG) {
            let regionConfig = REGION_CONFIG[region];
            this.appCode = regionConfig.appCode;
            this.baseUrl = regionConfig.baseUrl;
            this.remoteUrl = regionConfig.remoteUrl;
            this.usherUrl = regionConfig.usherUrl;
        }
        else {
            throw new Error("Invalid region");
        }
        this.baseAPIDeviceID = CryptoUtils_1.default.generateUuidFromSeed(email);
        this.usherAPIDeviceID = CryptoUtils_1.default.generateUsherDeviceIDFromSeed(email);
        this.sensorDataBuilder = new SensorDataBuilder_1.default();
        this.remoteEncKey = undefined;
        this.remoteSignKey = undefined; 
        this.encKey = undefined;
        this.signKey = undefined;
        
        // Removed global prefixUrl to allow dynamic cross-domain routing
        this.gotClient = got_1.default.extend({
            headers: {
                "device-id": this.baseAPIDeviceID,
                "app-code": this.appCode,
                "app-os": APP_OS,
                "user-agent": USER_AGENT_BASE_API,
                "app-version": APP_VERSION,
                "app-unique-id": APP_PACKAGE_ID,
                "access-token": "",
                "region": region === "MNAO" ? "us" : region === "MME" ? "eu" : "jp",
                "locale": "en-US",
                "language": "en"
            },
            responseType: "json",
            timeout: 10000,
            hooks: {
                init: [
                    options => {
                        if (typeof options.searchParams === "object" && !isURLSearchParams(options.searchParams)) {
                            if (typeof options.context === "undefined")
                                options.context = {};
                            let originalPayload = searchParamsToString(options.searchParams);
                            options.context.originalPayload = originalPayload;
                            options.searchParams = new URLSearchParams();
                            options.searchParams.append("params", this.encryptPayloadUsingKey(originalPayload));
                        }
                    }
                ],
                beforeRequest: [
                    options => {
                        let timestamp = this.getTimestampStrMs();
                        options.headers["req-id"] = `req_${timestamp}`;
                        options.headers["timestamp"] = timestamp;
                        options.headers["X-acf-sensor-data"] = this.sensorDataBuilder.generateSensorData();
                        if (options.url.href.includes("checkVersion")) {
                            options.headers["sign"] = this.getSignFromTimestamp(timestamp);
                        }
                        else if (options.method === "GET") {
                            let payload = "";
                            if (options.context.originalPayload)
                                payload = options.context.originalPayload;
                            options.headers["sign"] = this.getSignFromPayloadAndTimestamp(payload, timestamp);
                        }
                        else if (options.method === "POST") {
                            let payload = "";
                            if (typeof options.body !== "undefined") {
                                payload = options.body.toString();
                            }
                            else if ("json" in options) {
                                payload = JSON.stringify(options.json);
                            }
                            options.body = this.encryptPayloadUsingKey(payload);
                            options.headers["content-length"] = options.body.length.toString();
                            options.headers["sign"] = this.getSignFromPayloadAndTimestamp(payload, timestamp);
                        }
                        else {
                            throw new Error("Unimplemented method");
                        }
                    }
                ],
                afterResponse: [
                    (response, retryWithMergedOptions) => {
                        if (isSuccessfulEncryptedAPIResponse(response.body)) {
                            let responsePayload = response.body.payload;
                            if (response.request.options.url.href.includes("checkVersion")) {
                                response.body = this.decryptPayloadUsingAppCode(responsePayload);
                            }
                            else {
                                response.body = this.decryptPayloadUsingKey(responsePayload);
                            }
                            return response;
                        }
                        else if (isErrorEncryptedAPIResponse(response.body) && response.body.errorCode === 600001) {
                            throw new Error("API_ENCRYPTION_ERROR: Server rejected encrypted request");
                        }
                        else if (isErrorEncryptedAPIResponse(response.body) && response.body.errorCode === 600002) {
                            throw new Error("ACCESS_TOKEN_EXPIRED_ERROR: Access token expired");
                        }
                        else if (isErrorEncryptedAPIResponse(response.body) && response.body.errorCode === 920000 && response.body.extraCode === "400S01") {
                            throw new Error("REQUEST_IN_PROGRESS_ERROR: Request already in progress, please wait and try again");
                        }
                        else if (isErrorEncryptedAPIResponse(response.body) && response.body.errorCode === 920000 && response.body.extraCode === "400S11") {
                            throw new Error("The engine can only be remotely started 2 consecutive times. Please drive the vehicle to reset the counter.");
                        }
                        else if (isErrorEncryptedAPIResponse(response.body) && response.body.errorCode === 900500) {
                            throw new Error("RATE_LIMITING_ERROR: Rate limited; please wait and try again");
                        }
                        else if (isErrorEncryptedAPIResponse(response.body) && "error" in response.body) {
                            throw new Error("Request failed: " + response.body.error);
                        }
                        else {
                            throw new Error("Request failed due to an unknown error");
                        }
                    }
                ]
            },
        });
    }

    getTimestampStrMs() {
        return Date.now().toString();
    }
    getTimestampStr() {
        return Math.round(Date.now() / 1000).toString();
    }
    getDecryptionKeyFromAppCode() {
        let val = CryptoUtils_1.default.md5(CryptoUtils_1.default.md5(this.appCode + APP_PACKAGE_ID).toUpperCase() + SIGNATURE_MD5).toLowerCase();
        return val.substring(4, 20);
    }
    getTemporarySignKeyFromAppCode() {
        let val = CryptoUtils_1.default.md5(CryptoUtils_1.default.md5(this.appCode + APP_PACKAGE_ID).toUpperCase() + SIGNATURE_MD5).toLowerCase();
        return val.substring(20, 32) + val.substring(0, 10) + val.substring(4, 6);
    }
    getSignFromTimestamp(timestamp) {
        if (typeof timestamp !== "string" || timestamp === "")
            return "";
        let timestampExtended = (timestamp + timestamp.substring(6) + timestamp.substring(3)).toUpperCase();
        let temporarySignKey = this.getTemporarySignKeyFromAppCode();
        return this.getPayloadSign(timestampExtended, temporarySignKey);
    }
    getSignFromPayloadAndTimestamp(payload, timestamp) {
        if (timestamp.length === 0)
            return "";
        if (typeof this.signKey === "undefined")
            throw new Error("signKey must be set first");
        return this.getPayloadSign(this.encryptPayloadUsingKey(payload) + timestamp + timestamp.substring(6) + timestamp.substring(3), this.signKey);
    }
    getPayloadSign(encryptedPayloadAndTimestamp, signKey) {
        return CryptoUtils_1.default.sha256(encryptedPayloadAndTimestamp + signKey).toUpperCase();
    }
    encryptPayloadUsingKey(payload) {
        if (typeof this.encKey === "undefined")
            throw new Error("encKey must be set first");
        if (payload.length === 0)
            return "";
        let payloadBuffer = Buffer.from(payload);
        return CryptoUtils_1.default.encryptAES128CBCBufferToBase64String(payloadBuffer, this.encKey, IV);
    }
    encryptPasswordWithPublicKey(password, publicKey) {
        let timestamp = this.getTimestampStr();
        let encryptedBuffer = CryptoUtils_1.default.encryptRSAECBPKCS1Padding(Buffer.from(`${password}:${timestamp}`, "utf8"), publicKey);
        return encryptedBuffer.toString("base64");
    }
    decryptPayloadUsingAppCode(payload) {
        let payloadBuffer = Buffer.from(payload, "base64");
        let key = this.getDecryptionKeyFromAppCode();
        let decrypted = CryptoUtils_1.default.decryptAES128CBCBufferToString(payloadBuffer, key, IV);
        let result = JSON.parse(decrypted);
        return result;
    }
    decryptPayloadUsingKey(payload) {
        if (typeof this.encKey === "undefined")
            throw new Error("encKey must be set first");
        let payloadBuffer = Buffer.from(payload, "base64");
        let decrypted = CryptoUtils_1.default.decryptAES128CBCBufferToString(payloadBuffer, this.encKey, IV);
        let result = JSON.parse(decrypted);
        return result;
    }

    async apiRequest(needsKeys, needsAuth, gotOptions) {
        return await this.apiRequestRetry(needsKeys, needsAuth, gotOptions, 0);
    }

    async apiRequestRetry(needsKeys, needsAuth, gotOptions, numRetries) {
        if (numRetries > MAX_RETRIES) throw new Error("Max retries reached");
        
        let urlStr = gotOptions.url;
        let isRemoteService = typeof urlStr === "string" && urlStr.includes("remoteServices");
        
        if (needsKeys) await this.ensureKeysPresent(isRemoteService);
        if (needsAuth) await this.ensureTokenIsValid();

        // Assign keys based on domain
        this.encKey = isRemoteService ? this.remoteEncKey : this.encKey;
        this.signKey = isRemoteService ? this.remoteSignKey : this.signKey;

        let targetBaseUrl = isRemoteService ? this.remoteUrl : this.baseUrl;
        let absoluteUrl = targetBaseUrl + urlStr;

        let gotOptionsWithToken = { 
            ...gotOptions, 
            url: absoluteUrl,
            headers: { 
                ...gotOptions.headers, 
                "access-token": needsAuth ? this.accessToken : undefined 
            } 
        };

        if (isRemoteService) {
            gotOptionsWithToken.headers["app-code"] = this.remoteAppCode;
            gotOptionsWithToken.headers["app-os"] = "IOS";
            gotOptionsWithToken.headers["app-unique-id"] = "com.mazdausa.mazdaiphone";
            gotOptionsWithToken.headers["user-agent"] = "MyMazda-ios/9.1.0";
            if (needsAuth) {
                gotOptionsWithToken.headers["Authorization"] = `Bearer ${this.accessToken}`;
            }
        } else {
            gotOptionsWithToken.headers["app-code"] = this.appCode;
        }

        try {
            let response = await this.gotClient(gotOptionsWithToken);
            return response.body;
        }
        catch (err) {
            console.error(`[API Debug] Error on ${gotOptions.url}: ${err.message}`);
            
            if (!isRemoteService && typeof err.message === "string" && err.message.includes("API_ENCRYPTION_ERROR")) {
                logger.debug("Server reports request was not encrypted properly. Retrieving new encryption keys.");
                await this.retrieveKeys();
                return await this.apiRequestRetry(needsKeys, needsAuth, gotOptions, numRetries + 1);
            }
            else if (!isRemoteService && typeof err.message === "string" && err.message.includes("ACCESS_TOKEN_EXPIRED_ERROR")) {
                await this.login();
                return await this.apiRequestRetry(needsKeys, needsAuth, gotOptions, numRetries + 1);
            }
            else {
                throw err;
            }
        }
    }

    async ensureKeysPresent() {
        if (typeof this.encKey === "undefined" || typeof this.signKey === "undefined") {
            await this.retrieveKeys();
        }
    }
    async ensureTokenIsValid() {
        if (typeof this.accessToken === "undefined" || this.accessToken.length === 0 || typeof this.accessTokenExpirationTs === "undefined")
            logger.debug("No access token present. Logging in.");
        if (typeof this.accessTokenExpirationTs !== "undefined" && this.accessTokenExpirationTs <= (new Date().getTime() / 1000))
            logger.debug("Access token is expired. Fetching a new one.");
        if (typeof this.accessToken === "undefined" || this.accessToken.length === 0 || typeof this.accessTokenExpirationTs === "undefined" || this.accessTokenExpirationTs <= (new Date().getTime() / 1000)) {
            await this.login();
        }
    }
    async ensureKeysPresent(isRemote) {
        if (isRemote) {
            if (typeof this.remoteEncKey === "undefined") await this.retrieveRemoteKeys();
        } else {
            if (typeof this.encKey === "undefined") await this.retrieveKeys();
        }
    }

    async retrieveKeys() {
        logger.debug("Retrieving standard encryption keys");
        // We force this request to use the base domain, avoiding the isRemote logic
        let responseObj = await this.gotClient({
            url: this.baseUrl + "service/checkVersion",
            method: "POST",
            headers: { "app-code": this.appCode }
        }).json();
        
        // Ensure we got the keys back
        if (!responseObj.encKey || !responseObj.signKey) {
            throw new Error("Failed to retrieve encryption keys from Mazda API");
        }
        
        this.encKey = responseObj.encKey;
        this.signKey = responseObj.signKey;
        logger.debug("Successfully retrieved encryption keys");
    }

    async apiRequestRetry(needsKeys, needsAuth, gotOptions, numRetries) {
        if (numRetries > MAX_RETRIES) throw new Error("Max retries reached");
        
        // 1. Ensure keys exist before doing anything
        if (needsKeys && !this.encKey) await this.retrieveKeys();
        if (needsAuth) await this.ensureTokenIsValid();

        let urlStr = gotOptions.url;
        let isRemoteService = typeof urlStr === "string" && urlStr.includes("remoteServices");
        
        // 2. Route domain and set Headers based on destination
        let targetBaseUrl = isRemoteService ? this.remoteUrl : this.baseUrl;
        let absoluteUrl = targetBaseUrl + urlStr;

        let gotOptionsWithToken = { 
            ...gotOptions, 
            url: absoluteUrl,
            headers: { 
                ...gotOptions.headers, 
                "access-token": needsAuth ? this.accessToken : undefined 
            } 
        };

        if (isRemoteService) {
            gotOptionsWithToken.headers["app-code"] = this.remoteAppCode;
            gotOptionsWithToken.headers["app-os"] = "IOS";
            gotOptionsWithToken.headers["app-unique-id"] = "com.mazdausa.mazdaiphone";
            gotOptionsWithToken.headers["user-agent"] = "MyMazda-ios/9.1.0";
            if (needsAuth) {
                gotOptionsWithToken.headers["Authorization"] = `Bearer ${this.accessToken}`;
            }
        } else {
            gotOptionsWithToken.headers["app-code"] = this.appCode;
        }

        try {
            let response = await this.gotClient(gotOptionsWithToken);
            return response.body;
        }
        catch (err) {
            console.error(`[API Debug] Error on ${gotOptions.url}: ${err.message}`);
            
            // Only retry if it's a generic failure, not if it's a hard encryption error
            if (typeof err.message === "string" && err.message.includes("API_ENCRYPTION_ERROR")) {
                throw err; // Fail fast so we don't loop
            }
            throw err;
        }
    }

    async login() {
        logger.debug(`Logging in as ${this.email}`);
        logger.debug("Retrieving public key to encrypt password");
        let gotClientUsher = got_1.default.extend({
            prefixUrl: this.usherUrl,
            responseType: "json",
            headers: {
                "User-Agent": USER_AGENT_USHER_API
            }
        });
        let encryptionKeyResponse = await gotClientUsher({
            url: "system/encryptionKey",
            searchParams: {
                "appId": "MazdaApp",
                "locale": "en-US",
                "deviceId": this.usherAPIDeviceID,
                "sdkVersion": USHER_SDK_VERSION
            }
        });
        let publicKey = encryptionKeyResponse.body.data.publicKey;
        let encryptedPassword = this.encryptPasswordWithPublicKey(this.password, publicKey);
        let versionPrefix = encryptionKeyResponse.body.data.versionPrefix;
        logger.debug("Sending login request");
        let loginResponse = await gotClientUsher({
            url: "user/login",
            method: "POST",
            json: {
                "appId": "MazdaApp",
                "deviceId": this.usherAPIDeviceID,
                "locale": "en-US",
                "password": `${versionPrefix}${encryptedPassword}`,
                "sdkVersion": USHER_SDK_VERSION,
                "userId": this.email,
                "userIdType": "email"
            },
            throwHttpErrors: false
        });
        if (loginResponse.body.status === "INVALID_CREDENTIAL") {
            logger.debug("Login failed due to invalid email or password");
            throw new Error("Invalid email or password");
        }
        if (loginResponse.body.status === "USER_LOCKED") {
            logger.debug("Login failed to account being locked");
            throw new Error("Account has been locked");
        }
        if (loginResponse.body.status !== "OK") {
            let errStr = "Login failed" + (("status" in loginResponse.body) ? (": " + loginResponse.body.status) : "");
            logger.debug(errStr);
            throw new Error("LOGIN_ERROR " + errStr);
        }
        logger.debug(`Successfully logged in as ${this.email}`);
        this.accessToken = loginResponse.body.data.accessToken;
        this.accessTokenExpirationTs = loginResponse.body.data.accessTokenExpirationTs;
    }
}
exports.default = MyMazdaAPIConnection;