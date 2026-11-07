# MINDLOCK Journal online: dashboard + 24/7 broker sync. Data lives in /data (mount a persistent disk there).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 MINDLOCK_HOST=0.0.0.0 PORT=8765 MINDLOCK_SYNC_SECONDS=120
WORKDIR /app
COPY . /app/mindlock-journal
RUN useradd --create-home mindlock && mkdir -p /data && chown mindlock /data
USER mindlock
EXPOSE 8765
HEALTHCHECK CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8765'))"
CMD ["sh", "-c", "python mindlock-journal/sync.py --dir /data --serve \"$PORT\" --host \"$MINDLOCK_HOST\" --watch \"$MINDLOCK_SYNC_SECONDS\""]
