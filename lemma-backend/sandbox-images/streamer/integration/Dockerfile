# A workspace image plus the streamer, for the integration check (run.sh).
# Build context: this directory, with the two binaries copied in as bin/.
ARG BASE=lemma-workspace:e2e-96ed089a7bdf
FROM ${BASE}
USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends xclip xdotool \
    && rm -rf /var/lib/apt/lists/*
COPY bin/lemma-streamer bin/streamcheck /usr/local/bin/
COPY --chmod=0755 start-streamer.sh /usr/local/bin/start-streamer
COPY probe.html sandbox.sh mint.py /it/
USER 10001
ENTRYPOINT ["bash", "/it/sandbox.sh"]
