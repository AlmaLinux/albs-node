FROM almalinux:8

COPY ./buildnode.repo /etc/yum.repos.d/buildnode.repo
RUN dnf install -y epel-release && \
    dnf upgrade -y && \
    dnf install -y --enablerepo="powertools" --enablerepo="epel" --enablerepo="buildnode" \
        python3 gcc gcc-c++ python3-devel python3-virtualenv cmake \
        python3-pycurl libicu libicu-devel python3-lxml git tree mlocate mc createrepo_c \
        python3-createrepo_c xmlsec1-openssl-devel cpio sudo \
        kernel-rpm-macros python3-libmodulemd dpkg-dev mock debootstrap pbuilder apt apt-libs \
        python3-apt keyrings-filesystem ubu-keyring debian-keyring && \
    dnf clean all

RUN curl https://raw.githubusercontent.com/vishnubob/wait-for-it/master/wait-for-it.sh -o wait_for_it.sh && chmod +x wait_for_it.sh
# A lot of rpm packages contains unit-tests which should be run as non-root user
RUN useradd -ms /bin/bash alt
RUN usermod -aG wheel alt
RUN usermod -aG mock alt
RUN echo 'alt ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
RUN echo 'wheel ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

RUN mkdir -p \
    /srv/alternatives/castor/build_node \
    /var/cache/pbuilder/aptcache/ \
    /var/cache/pbuilder/pbuilder_envs/ \
    /srv/alternatives/castor/build_node/pbuilder_envs/buster-amd64/aptcache \
    /srv/alternatives/castor/build_node/pbuilder_envs/bionic-amd64/aptcache \
    /srv/alternatives/castor/build_node/pbuilder_envs/focal-amd64/aptcache \
    /srv/alternatives/castor/build_node/pbuilder_envs/jessie-amd64/aptcache \
    /srv/alternatives/castor/build_node/pbuilder_envs/stretch-amd64/aptcache \
    /srv/alternatives/castor/build_node/pbuilder_envs/xenial-amd64/aptcache \
    /srv/alternatives/castor/build_node/pbuilder_envs/buster-arm64/aptcache \
    /srv/alternatives/castor/build_node/pbuilder_envs/buster-armhf/aptcache \
    /srv/alternatives/castor/build_node/pbuilder_envs/raspbian-armhf/aptcache \
    /srv/alternatives/castor/build_node/mock_configs \
    /root/.config/castor/build_node \
    /root/.config/cl-alternatives/

WORKDIR /build-node

COPY requirements.txt /build-node/requirements.txt

RUN python3 -m venv --system-site-packages env
RUN /build-node/env/bin/pip install --upgrade pip==21.1 && /build-node/env/bin/pip install -r requirements.txt && /build-node/env/bin/pip cache purge

COPY ./build_node /build-node/build_node
COPY almalinux_build_node.py /build-node/almalinux_build_node.py

RUN chown -R alt:alt /build-node /wait_for_it.sh /srv
USER alt

# FIXME:
# COPY ./tests /build-node/tests
# RUN /build-node/env/bin/py.test tests

CMD ["/build-node/env/bin/python", "/build-node/almalinux_build_node.py"]
