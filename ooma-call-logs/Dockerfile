ARG BUILD_FROM
FROM $BUILD_FROM

# Unlike ohio-aes/centerpoint-gas, this add-on is NOT based on an HA base
# image -- it's based on FlareSolverr's own published image directly (safer
# than reinstalling its patched-Chromium/undetected-chromedriver setup
# ourselves; their image already has that tuned correctly: chromium,
# chromium-driver, xvfb, dumb-init, on a python:3.11-slim-bookworm base).
# That means no bashio/s6-overlay here -- config is read directly from
# /data/options.json (the same file bashio itself just wraps), and
# SUPERVISOR_TOKEN is injected by Supervisor into any container regardless
# of base image, so homeassistant_api: true still works fine.
#
# FlareSolverr's own Dockerfile sets `ENTRYPOINT ["/usr/bin/dumb-init", "--"]`
# (PID 1, reaps zombie Chromium subprocesses) -- deliberately NOT overridden
# here, so it keeps applying to our replacement CMD below.
#
# NOTE: bundling FlareSolverr and this add-on's own script as two processes
# in one container (rather than FlareSolverr's usual one-process-per-
# container setup) is a novel combination for this project -- verify it
# actually starts and stays healthy when first built; see start.sh and the
# README's "First-run debugging" section.

USER root

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ooma-call-logs.py .
COPY start.sh .
RUN chmod +x start.sh

CMD ["/app/start.sh"]
