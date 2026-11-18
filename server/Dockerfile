FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=8080
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
# Run as an unprivileged user, not root.
RUN useradd --system --no-create-home app
USER app
EXPOSE 8080
CMD ["python", "server.py"]
