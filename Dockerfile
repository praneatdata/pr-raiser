FROM python:3.12-slim
WORKDIR /app

# Trust the corporate TLS proxy's CA so intercepted traffic (e.g. slack.com)
# verifies inside the container, same as on the host.
COPY vmock-ca.crt /usr/local/share/ca-certificates/vmock-ca.crt
RUN update-ca-certificates

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py socket_mode.py ./
CMD ["python", "socket_mode.py"]
