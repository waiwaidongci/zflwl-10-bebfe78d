FROM alpine:3.22.6@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8

RUN apk add --no-cache \
    bash=5.2.37-r0 \
    coreutils=9.7-r1 \
    python3=3.12.15-r0 \
    py3-pytest=8.3.5-r0

WORKDIR /app
RUN mkdir -p /logs/verifier
COPY app /app
