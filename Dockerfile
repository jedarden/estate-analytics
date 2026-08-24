FROM python:3.12-slim
ARG VERSION=dev
ENV APP_VERSION=$VERSION PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/
ENV PYTHONPATH=/app/src
RUN useradd -u 1000 -m appuser
USER 1000
CMD ["python", "-m", "estate_analytics.main"]
