# syntax=docker/dockerfile:1.7
FROM ubuntu:24.04 AS runtime

ARG TARGETARCH
ARG NERDCTL_VERSION=2.3.4

ENV DEBIAN_FRONTEND=noninteractive
# Lemma is a versioned appliance, not a general-purpose Ubuntu host. The GA
# package supplies a distro-compatible initramfs and userspace module tree; the
# macOS direct-boot kernel is replaced by the hash-pinned Kata container kernel
# in build_local_guest_runtime.sh.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        containerd \
        containernetworking-plugins \
        coreutils \
        curl \
        e2fsprogs \
        iproute2 \
        iptables \
        initramfs-tools \
        kmod \
        linux-image-virtual \
        runc \
        systemd \
        systemd-resolved \
        systemd-sysv \
        systemd-timesyncd \
        util-linux \
    && update-alternatives --set iptables /usr/sbin/iptables-legacy \
    && update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    archive="nerdctl-${NERDCTL_VERSION}-linux-${TARGETARCH}.tar.gz"; \
    base="https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}"; \
    curl -fsSLo "/tmp/${archive}" "${base}/${archive}"; \
    curl -fsSLo /tmp/SHA256SUMS "${base}/SHA256SUMS"; \
    cd /tmp; \
    grep "  ${archive}$" SHA256SUMS | sha256sum -c -; \
    tar -xzf "${archive}" -C /usr/local/bin; \
    rm -f "${archive}" SHA256SUMS

COPY lemma-guestd /usr/local/bin/lemma-guestd
COPY rootfs-overlay/ /

RUN chmod 0755 \
        /usr/local/bin/lemma-guestd \
        /usr/local/bin/lemma-mount-data \
        /usr/local/bin/lemma-set-host-time \
        /usr/local/bin/lemma-runtime-guest-service \
        /usr/local/bin/lemma-runtime-init \
    && mkdir -p \
        /etc/lemma \
        /etc/cni/net.d \
        /mnt/lemma-control \
        /opt/cni \
        /var/lib/lemma/run \
        /var/lib/lemma/home \
        /var/lib/lemma/workspaces \
        /var/log/lemma \
    && ln -s /usr/lib/cni /opt/cni/bin \
    && chmod 0700 \
        /etc/lemma \
        /var/lib/lemma \
        /var/lib/lemma/home \
        /var/lib/lemma/run \
        /var/lib/lemma/workspaces \
    && systemctl enable systemd-networkd.service systemd-resolved.service systemd-timesyncd.service \
    && rm -f /etc/machine-id /var/lib/dbus/machine-id \
    && touch /etc/machine-id

FROM scratch AS export
COPY --from=runtime / /
