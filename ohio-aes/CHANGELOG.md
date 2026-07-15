# Changelog

## 0.1.1

- Fixed build failure: switched base image from Alpine to Debian (via build.yaml) because Playwright's pip package has no musllinux/Alpine wheel for any version, so it could never install on the Alpine base regardless of pinned version.

## 0.1.0

- Initial release: Playwright-driven login through AES Ohio's SAML/OIDC chain to Opower, CSV interval export download, daily total published to MQTT.
