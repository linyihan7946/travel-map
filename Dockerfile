FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    XDG_CACHE_HOME=/tmp/.cache \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
    PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

# Tencent Cloud hosts the deployment server, so use its nearby Debian mirror
# instead of waiting on the much slower default CDN. Static exports need a CJK
# font; fontconfig makes it discoverable by Matplotlib.
RUN sed -i 's|http://deb.debian.org|https://mirrors.cloud.tencent.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::Timeout=30 install -y --no-install-recommends fonts-wqy-microhei fontconfig \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefer-binary --only-binary=:all: --retries 5 -r requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /tmp/matplotlib /tmp/.cache \
    && chown -R appuser:appuser /tmp/matplotlib /tmp/.cache /app

USER appuser

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "180", "--max-requests", "200", "--max-requests-jitter", "20", "--access-logfile", "-", "--error-logfile", "-", "travel_map.web:app"]
