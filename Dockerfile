# The interactive dashboard as a read-only public demo.
#
#   docker build -t candlebench-demo .
#   docker run --rm -p 8765:8765 candlebench-demo
#
# On start it generates the synthetic market, runs every pattern, and serves
# the dashboard read-only: visitors can browse the run, its trades and its
# session charts, and cannot start runs or fetches. No market data, API keys
# or volumes are involved.
FROM python:3.12-slim

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY candlebench ./candlebench
RUN pip install --no-cache-dir . && useradd --create-home candlebench

USER candlebench
WORKDIR /home/candlebench
EXPOSE 8765
CMD ["candlebench", "demo", "--read-only", "--host", "0.0.0.0", "--port", "8765", \
     "--no-browser", "--cache-dir", "/home/candlebench/demo"]
