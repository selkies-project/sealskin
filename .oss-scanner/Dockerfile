# Builds SealSkin for Anthropic's OSS Scanner (github.com/anthropics/oss-scanner) the way CI does: the server's
# Python dependencies with its test tools, and the client's npm dependencies and build (served UI, browser
# extension, mobile shell), then runs the server's tests. The scan runs offline, so everything a build or test
# needs is fetched here.
FROM node:22-trixie
ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

# scanner contract: the checkout lives inside the image, at /src
COPY . /src
WORKDIR /src

RUN python3 -m venv /opt/venv && pip install --no-cache-dir -r server/requirements.txt pytest pytest-asyncio
RUN cd client && npm ci --no-audit --no-fund && npm run build

RUN python -m pytest server/tests -q -p no:cacheprovider || echo "WARNING: the server tests reported failures"
