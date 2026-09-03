# Building the server image for 0.2

Starting with 0.3.0 the server serves the web UI from `client/dist/ui`. That
directory is a build output and is not in the source tarball, so the image
build must either build it or download it.

## Option A: build stage (recommended)

```dockerfile
FROM node:22-alpine AS ui
ARG SEALSKIN_VERSION
RUN apk add --no-cache curl tar && mkdir /src && \
    curl -L "https://github.com/selkies-project/sealskin/archive/${SEALSKIN_VERSION}.tar.gz" \
      | tar xz -C /src --strip-components=1 && \
    cd /src/client && npm install && npm run build

FROM ghcr.io/linuxserver/baseimage-alpine:3.23
# ... existing install steps that unpack the source tarball into /opt/sealskin ...
COPY --from=ui /src/client/dist /opt/sealskin/client/dist
```

`server/main.py` resolves the UI directory as `<repo>/client/dist/ui` by
default, so copying `client/dist` next to `server/` needs no extra setting.

## Option B: release asset

Every release (and pre-release) now also publishes
`sealskin-ui-v<VERSION>.tar.gz` containing the `ui/` directory. The image can
download it instead of building:

```dockerfile
RUN curl -o /tmp/ui.tar.gz -L \
      "https://github.com/selkies-project/sealskin/releases/download/${SEALSKIN_VERSION}/sealskin-ui-v${SEALSKIN_VERSION}.tar.gz" && \
    mkdir -p /opt/sealskin/client/dist && \
    tar xf /tmp/ui.tar.gz -C /opt/sealskin/client/dist
```

## Other image changes

* `SEALSKIN_UI_PATH` overrides the served UI directory if you place it
  elsewhere.
* `/internal/*` is now denied on the public Caddy listener. Nothing in the
  image needs to change for that; the forward_auth sub-requests go to the API
  port on localhost.
* New Python dependency set: `python-jose` is replaced by `PyJWT`, and
  `watchfiles` is added for the YAML reload watcher. `pip install -r
  server/requirements.txt` picks both up.
