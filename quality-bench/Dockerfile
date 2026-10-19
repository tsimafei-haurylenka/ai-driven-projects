FROM node:24-bookworm-slim
# shell tools needed by terminal benchmark tasks
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 jq git mawk xz-utils unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent
WORKDIR /bench
# helpers land in /bench/ so INSTRUCTIONS.md's /bench/_*.py references resolve
COPY bin/_show_batch.py bin/_append_batch.py bin/_time.py bin/_todo.py ./
COPY INSTRUCTIONS.md ./
RUN mkdir -p /questions /work /tasks
CMD ["bash"]
