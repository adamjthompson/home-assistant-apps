# Changelog

## 0.1.2

- Fixed post-login navigation: the real AES Ohio dashboard has no "Your Energy Use" link. "My Usage" is a hover-revealed dropdown trigger, and the actual Opower link is "PowerView", which opens in a new tab -- updated the automation to hover the trigger and capture the popup page instead of waiting on the original page to navigate.

## 0.1.1

- Fixed build failure: switched base image from Alpine to Debian (via build.yaml) because Playwright's pip package has no musllinux/Alpine wheel for any version, so it could never install on the Alpine base regardless of pinned version.

## 0.1.0

- Initial release: Playwright-driven login through AES Ohio's SAML/OIDC chain to Opower, CSV interval export download, daily total published to MQTT.
