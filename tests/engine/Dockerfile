# SooperLooper 1.7.9 on headless JACK — the engine the appliance runs, on a laptop or in CI.
#
# Built from the upstream v1.7.9 tag with the liblo 0.32 handler patch the Pi
# carries (docs/measurements/archive/sooperlooper-eval-2026-08-14.md), on Debian
# trixie, like the Pi. Single stage on purpose: the runtime library names on
# trixie (t64 transition) are easy to get wrong, and a fat image that is right
# beats a slim one that is a guess. Trim once it has run in CI.
FROM debian:trixie-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git g++ make autoconf automake libtool-bin autopoint gettext pkg-config \
        libjack-jackd2-dev liblo-dev libsigc++-2.0-dev libxml2-dev libsndfile1-dev \
        libsamplerate0-dev librubberband-dev libfftw3-dev libncurses-dev libicu-dev \
        jackd2 jack-example-tools \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch v1.7.9 https://github.com/essej/sooperlooper.git /src
WORKDIR /src
# liblo 0.32: lo_method_handler's fifth argument is lo_message, not void*.
RUN find src \( -name '*.cpp' -o -name '*.hpp' \) \
        -exec sed -i 's/void \*data, void \*user_data/lo_message data, void *user_data/g' {} + \
    && ./autogen.sh \
    && ./configure --without-gui \
    && make -j"$(nproc)" \
    && install -m 0755 src/sooperlooper /usr/local/bin/sooperlooper \
    && sooperlooper --version
COPY run-engine.sh /usr/local/bin/run-engine
RUN chmod 0755 /usr/local/bin/run-engine
ENTRYPOINT ["/usr/local/bin/run-engine"]
