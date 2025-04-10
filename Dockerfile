FROM almalinux:9 as albs-node

COPY buildnode.repo /etc/yum.repos.d/buildnode.repo
RUN <<EOT
  set -ex
  dnf install -y epel-release
  dnf upgrade -y
  dnf install -y \
    gcc gcc-c++ make cmake git mock mock-rpmautospec keyrings-filesystem sudo \
    libicu libicu-devel kernel-rpm-macros createrepo_c cpio e2fsprogs \
    python3-devel python3-lxml python3-createrepo_c python3-libmodulemd \
    centpkg \
    fedpkg
  dnf clean all
EOT

RUN mkdir -p \
    /srv/alternatives/castor/build_node/mock_configs \
    /root/.config/castor/build_node \
    /root/.config/cl-alternatives/

WORKDIR /build-node
COPY requirements.txt .
RUN <<EOT
  set -ex
  python3 -m ensurepip
  pip3 install -r requirements.txt --user
  rm -rf requirements.txt ~/.cache/pip
EOT

ADD --chmod=755 https://raw.githubusercontent.com/vishnubob/wait-for-it/master/wait-for-it.sh /


FROM albs-node as albs-node-tests

COPY requirements-tests.txt .
RUN <<EOT
  set -ex
  pip3 install -r requirements-tests.txt
  rm -rf requirements-tests.txt ~/.cache/pip
EOT
