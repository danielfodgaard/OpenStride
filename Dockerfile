# TODO: run as a non-root user; a bind-mounted ./data needs matching ownership first.
FROM python:3.13-slim
# git: the workspace is a repository. bubblewrap and socat: the OS sandbox for the agents' shells.
RUN apt-get update && apt-get install -y --no-install-recommends git bubblewrap socat \
    && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv
WORKDIR /app
COPY . .
# The analyst extra is what the analyst computes with: pandas, numpy, scipy, matplotlib.
RUN uv sync --locked --no-dev --extra analyst
ENV PATH=/app/.venv/bin:$PATH
EXPOSE 8000
CMD ["openstride"]
