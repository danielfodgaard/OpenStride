# TODO(v4.1): run as a non-root user; a bind-mounted ./data needs matching ownership first.
FROM python:3.13-slim
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . .
RUN uv sync --locked --no-dev
ENV PATH=/app/.venv/bin:$PATH
EXPOSE 8000
CMD ["openstride"]
