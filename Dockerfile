FROM python:3.10

RUN apt install -y curl
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH=$PATH:/root/.local/bin

COPY pyproject.toml poetry.lock poetry.toml ./
RUN pip install --upgrade pip && \
    poetry install --no-root
