FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    POETRY_VERSION=2.1.1

# 安装poetry
RUN pip install "poetry==$POETRY_VERSION"

# 配置poetry不创建虚拟环境
RUN poetry config virtualenvs.create false

# 复制项目文件
COPY pyproject.toml poetry.lock* /app/
RUN poetry install --no-root --no-interaction

# 复制应用代码
COPY . /app/
RUN poetry install --only-root --no-interaction

# 暴露需要的端口（如果有Web服务的话）
EXPOSE 8000

# Credential-free reproducible baseline. Live Slack mode is started explicitly.
CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
