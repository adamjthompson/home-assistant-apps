ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.11-alpine3.18
FROM $BUILD_FROM

# Playwright drives a real Chromium to walk AES Ohio's SAML/OIDC login chain.
# Alpine has no glibc, so we can't use Playwright's own downloaded browser build --
# instead we install Chromium from apk and point Playwright at it at runtime.
RUN apk add --no-cache \
    chromium \
    nss \
    freetype \
    freetype-dev \
    harfbuzz \
    ca-certificates \
    ttf-freefont

ENV CHROMIUM_PATH=/usr/bin/chromium-browser

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY ohio-aes.py .
COPY run.sh .

RUN chmod +x run.sh

CMD ["/app/run.sh"]
