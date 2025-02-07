FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    software-properties-common \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY issue_parser.py /app
COPY requirements.txt /app
RUN pip3 install -r /app/requirements.txt

EXPOSE 8501
ENTRYPOINT ["streamlit", "run", "issue_parser.py", "--server.port=8501", "--server.address=0.0.0.0"]
