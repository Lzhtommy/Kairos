FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Core deps first (better layer caching)
COPY requirements.txt requirements-data.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Live-data (AkShare) + Claude extras. Opt in for production:
#   docker build --build-arg WITH_DATA=true ...
# Default off → lean image that runs on the built-in seed / rule providers.
ARG WITH_DATA=false
RUN if [ "$WITH_DATA" = "true" ]; then pip install --no-cache-dir -r requirements-data.txt; fi

COPY . .

RUN chmod +x entrypoint.sh
EXPOSE 8000
CMD ["./entrypoint.sh"]
