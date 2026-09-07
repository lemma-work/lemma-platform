# syntax=docker/dockerfile:1.7
FROM ubuntu:24.04 AS runtime

ARG TARGETARCH
ARG NERDCTL_VERSION=2.3.4
ARG GUEST_PLATFORM=macos-aarch64
ARG UBUNTU_KERNEL_RELEASE=6.8.0-138-generic

ENV DEBIAN_FRONTEND=noninteractive
# Keep Ubuntu's kernel, modules and generated initramfs together. WSL supplies
# its own kernel and does not need Linux boot packages in the distribution.
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
        runc \
        systemd \
        systemd-resolved \
        systemd-sysv \
        systemd-timesyncd \
        util-linux \
    && update-alternatives --set iptables /usr/sbin/iptables-legacy \
    && update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy \
    && rm -rf /var/lib/apt/lists/*

RUN if [ "$GUEST_PLATFORM" = macos-aarch64 ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends initramfs-tools kmod \
          "linux-image-${UBUNTU_KERNEL_RELEASE}" \
          "linux-modules-extra-${UBUNTU_KERNEL_RELEASE}" \
      && printf '%s\n' rtc-pl031 virtiofs vmw_vsock_virtio_transport \
          >> /etc/initramfs-tools/modules \
      && for module in rtc-pl031 virtiofs vmw_vsock_virtio_transport; do \
           modinfo -k "$UBUNTU_KERNEL_RELEASE" "$module" >/dev/null || exit 1; \
         done \
      && update-initramfs -u -k "$UBUNTU_KERNEL_RELEASE" \
      && mkdir -p /etc/lemma \
      && printf '%s\n' "$UBUNTU_KERNEL_RELEASE" > /etc/lemma/kernel-release \
      && rm -rf /var/lib/apt/lists/*; \
    elif [ "$GUEST_PLATFORM" != windows-x86_64 ]; then exit 1; fi

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
    && if [ "$GUEST_PLATFORM" = macos-aarch64 ]; then \
         systemctl enable lemma-guestd.service lemma-data.service lemma-host-clock.service \
           containerd.service systemd-logind.service \
         && systemctl set-default multi-user.target; \
       fi \
    && rm -f /etc/machine-id /var/lib/dbus/machine-id \
    && touch /etc/machine-id

RUN dpkg-query --show --showformat='${binary:Package}\t${Version}\n' > /etc/lemma/packages.txt

FROM scratch AS export
COPY --from=runtime / /
