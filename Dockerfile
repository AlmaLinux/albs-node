FROM almalinux:9

COPY buildnode.repo /etc/yum.repos.d/buildnode.repo
RUN <<EOT
  set -ex
  dnf install -y epel-release
  dnf upgrade -y
  dnf install -y \
    gcc gcc-c++ make cmake git mock keyrings-filesystem sudo \
    libicu libicu-devel kernel-rpm-macros createrepo_c cpio \
    python3-devel python3-lxml python3-createrepo_c python3-libmodulemd
  dnf clean all
EOT

RUN mkdir -p \
    /srv/alternatives/castor/build_node/mock_configs \
    /root/.config/castor/build_node \
    /root/.config/cl-alternatives/

WORKDIR /build-node
COPY requirements.* .
RUN <<EOT
  set -ex
  python3 -m ensurepip
  pip3 install -r requirements.devel.txt
  rm requirements.*
EOT

ADD --chmod=755 https://raw.githubusercontent.com/vishnubob/wait-for-it/master/wait-for-it.sh /
