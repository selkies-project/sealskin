# Building the server image

Starting with 0.3.0 the server serves the web UI from `client/dist/ui`. That
directory is a build output and is not in the source tarball, so the image
build must either build it or download it.

As of the next release after 0.3.0, every release and pre-release also
publishes a **`sealskin_server-<VERSION>-py3-none-any.whl`** wheel that
bundles the built UI, the Caddyfile template, the VERSION file and all
Python dependencies. This is the recommended way to build the Docker image.

## Recommended: install the wheel

```dockerfile
FROM python:3.13-slim AS base
RUN pip install --no-cache-dir \
      "https://github.com/selkies-project/sealskin/releases/download/${SEALSKIN_VERSION}/sealskin_server-${SEALSKIN_VERSION}-py3-none-any.whl"

FROM ghcr.io/linuxserver/baseimage-alpine:3.23
# ... install python, caddy ...
COPY --from=base /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=base /usr/local/bin/sealskin-server /usr/local/bin/sealskin-server
```

The wheel installs the `sealskin-server` console script and the `app` Python
package with the UI baked in. No Node build step is needed.

If you prefer not to use a multi-stage build you can also `pip install` the
wheel directly in a single stage:

```dockerfile
RUN pip install --no-cache-dir \
      "https://github.com/selkies-project/sealskin/releases/download/${SEALSKIN_VERSION}/sealskin_server-${SEALSKIN_VERSION}-py3-none-any.whl"
```

## Alternative: build stage

If you need to build from source instead (for example when modifying the
client), the build stage approach still works:

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

`server/app/settings.py` resolves the UI directory from the installed package
first, falling back to `<repo>/client/dist/ui` when running from source.

## Alternative: release asset tarball

Every release (and pre-release) still publishes
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
  elsewhere. When using the wheel, this is rarely needed because the UI
  is inside the installed package.
* `/internal/*` is now denied on the public Caddy listener. Nothing in the
  image needs to change for that; the forward_auth sub-requests go to the API
  port on localhost.
* New Python dependency set: `python-jose` is replaced by `PyJWT`, and
  `watchfiles` is added for the YAML reload watcher. The wheel pins all
  dependencies; `pip install -r server/requirements.txt` also works for
  source installs.
