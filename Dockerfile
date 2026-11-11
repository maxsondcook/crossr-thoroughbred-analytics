FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
# No credentials in the image: DATABASE_URL and the rest come from Railway variables.
# The API only reads and writes Postgres, so it runs as an unprivileged user.
RUN useradd --create-home --uid 10001 crossr && chown -R crossr /app
USER crossr
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8000'), timeout=8)" || exit 1
# Railway sets $PORT. Not "::": asyncio makes that IPv6-only, and Railway's
# healthcheck connects over IPv4. Private networking reaches IPv4 too.
CMD ["sh", "-c", "uvicorn crossr_api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
