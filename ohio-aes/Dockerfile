ARG BUILD_FROM
FROM $BUILD_FROM

# Playwright drives a real Chromium to walk AES Ohio's SAML/OIDC login chain.
# Playwright's pip package only ships manylinux (glibc) wheels -- it has never
# published a musllinux/Alpine wheel for any version -- so this add-on needs a
# glibc base (Debian), set via build.yaml. apt pulls in Chromium's full runtime
# dependency set on its own, unlike apk which needs each lib named explicitly.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    chromium \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV CHROMIUM_PATH=/usr/bin/chromium

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY ohio-aes.py .
COPY run.sh .

RUN chmod +x run.sh

CMD ["/app/run.sh"]
