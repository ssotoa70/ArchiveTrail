# ArchiveTrail DataEngine Function Container
#
# Build:  docker build -t archive-trail:latest .
# Push:   docker tag archive-trail:latest <registry>/archive-trail:latest
#         docker push <registry>/archive-trail:latest
#
# The DataEngine will pull this image and run the handler functions
# specified in the pipeline configuration.

FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir . \
    && rm -rf /root/.cache/pip

# DataEngine invokes functions via the handler entry point.
# The specific function (discover, offload, verify_purge) is
# configured in the pipeline's function deployment settings.
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "archive_trail"]
