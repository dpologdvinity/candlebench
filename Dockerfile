# The interactive dashboard as a read-only public demo.
#
#   docker build -t candlebench-demo .
#   docker run --rm -p 8765:8765 candlebench-demo
#
# On start it generates the synthetic market, runs every pattern, and serves
# the dashboard read-only: visitors can browse the run, its trades and its
# session charts, and cannot start runs or fetches. No market data, API keys
# or volumes are involved.
#
# Built in two stages so the C++ trade walk is compiled without leaving a
# compiler in the served image. The second stage imports it, so an image whose
# extension failed to build fails here rather than serving the slow walk.
FROM python:3.12-slim AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml setup.py README.md LICENSE ./
COPY candlebench ./candlebench
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.12-slim

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels \
    && python -c "import candlebench._enginecore" \
    && useradd --create-home candlebench

USER candlebench
WORKDIR /home/candlebench
EXPOSE 8765
CMD ["candlebench", "demo", "--read-only", "--host", "0.0.0.0", "--port", "8765", \
     "--no-browser", "--cache-dir", "/home/candlebench/demo"]
