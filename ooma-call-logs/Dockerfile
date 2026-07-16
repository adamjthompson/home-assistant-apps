ARG BUILD_FROM
FROM $BUILD_FROM

# No Chromium/Xvfb/FlareSolverr bundled here -- this add-on is a plain
# aiohttp client that talks to a separately-running FlareSolverr instance
# (yours, or any other), configured via `flaresolverr_url`. Bundling
# FlareSolverr in this same container was tried and hit a real, unresolved
# upstream "chrome not reachable" bug (see CHANGELOG 0.2.0) -- reverted back
# to the standard ohio-aes/centerpoint-gas base-image pattern, which is all
# this add-on needs now.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY ooma-call-logs.py .
COPY run.sh .

RUN chmod +x run.sh

CMD ["/app/run.sh"]
