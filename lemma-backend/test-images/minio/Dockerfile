FROM golang:1.24.13-bookworm AS build

# The official server and client releases used by the S3 test fixture.
RUN git init /src/minio && cd /src/minio \
    && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth=1 origin 07c3a429bfed433e49018cb0f78a52145d4bedeb \
    && git checkout --detach FETCH_HEAD \
    && CGO_ENABLED=0 go build -trimpath -o /out/minio .
RUN git init /src/mc && cd /src/mc \
    && git remote add origin https://github.com/minio/mc.git \
    && git fetch --depth=1 origin 7394ce0dd2a80935aded936b09fa12cbb3cb8096 \
    && git checkout --detach FETCH_HEAD \
    && CGO_ENABLED=0 go build -trimpath -o /out/mc .

FROM debian:bookworm-slim
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/minio /out/mc /usr/local/bin/
COPY --from=build /src/minio/LICENSE /usr/share/licenses/minio/LICENSE
COPY --from=build /src/mc/LICENSE /usr/share/licenses/mc/LICENSE
ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data"]
