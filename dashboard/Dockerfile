FROM python:3.12-alpine
WORKDIR /app
COPY app.py storage.py index.html statistics.html charts.js averages.js statistics-bars.js theme.css ./
EXPOSE 3000
CMD ["python", "app.py"]
