FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
RUN pip install --no-deps --no-build-isolation .
COPY fixtures ./fixtures
RUN useradd --create-home agentforge && mkdir -p /app/data && chown -R agentforge:agentforge /app
USER agentforge
ENTRYPOINT ["agentforge"]
CMD ["run"]
