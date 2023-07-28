System overview 
--

AlmaLinux Build System Build Node - ALBS Node - is designed for the automated building of rpm packages. It uses docker and docker-compose for local\production deployment. 
Build Node supports several types of architectures: x86_64, aarch64, ppc64le. The support of the architectures is provided by the [packages](https://repo.almalinux.org/build_system/8/) that were built for AlmaLinux Build Node specifically.

Build Node requires this albs-* services:
- [AlmaLinux Build System Web-Server]((https://github.com/AlmaLinux/albs-web-server)) (albs-web-server) -  puts a new build into a queue for Build Node;
- AlmaLinux Build System Build Node (albs-node) - receives and performs a build task, sends the results as artifacts to PULP, informs web-server about results. 


Build Node sends a request to the Web-Server. If there is an idle task (not started), Build Node receives back a build task to build packages. After the task is completed, Build Node uploads artifacts which are build logs and rpm packages to the [Artifact Storage (PULP)](https://build.almalinux.org/pulp/content/builds/AlmaLinux-8-x86_64-22-br/). 

Mentioned tools and libraries are required for ALBS Node to run in their current state:
 
- Python 3 
- Pulp
- PostgreSQL 
- Docker 
- Docker-compose
- Plumbum
- Mock
- Pbuilder

Build Node flow 
--

Currently, it is available to build for the AlmaLinux-8 OS mentioned architectures: x86_64, aarch64, ppc64le. 
Also, you can build both from the project tag or branch: 
- projects from [git.almalinux.org](https://git.almalinux.org/)
- projects from any git-reference
- projects from the third-party link on src.rpm 

Build Node can manage multiple builds at the same time, max number of simultaneously is configurable value (default: 4). In case of a failed build, artifacts that appeared before the fail moment will be uploaded to the PULP. 

The process:

- Prepare a specially isolated environment. For the rpm package type, the [mock](https://github.com/rpm-software-management/mock) utility creates one. A Build Node receives a task from the Web-Server by requesting `/api/v1/build_node/get_task`, creates a mock-environment to build an rpm package, and sends artifacts to the Artifact Storage (PULP). 
- Preparing the project source code for building. In the case of rpm, this means creating an src-rpm using a SPEC file, pre-unpacking the archive, applying patches, and possibly other operations according to SPEC. After that, all build artifacts are transfered (src.rpm, dsc and logs) to special directories.
- The building of binary packages in a special environment based on the prepared source code from the previous stage. 
- The transfer of all build artifacts (src.rpm, dsc and logs) takes place.


Running using docker-compose
--

You can start the system using the Docker Compose tool.

Pre-requisites:
- `docker` and `docker-compose` tools are installed and set up;

To start the system, run the following command: `docker-compose up -d`.  To rebuild images after your local changes, just run `docker-compose up -d --build`.


Reporing issues 
--

All issues should be reported to the [Build System project](https://github.com/AlmaLinux/build-system).
