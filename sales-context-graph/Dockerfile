# 3.11, not 3.12 -- matches pyproject.toml's requires-python (see its
# comment): the version actually developed and tested against all session,
# not the previously-declared, never-tested 3.12.
FROM python:3.11-slim

WORKDIR /app

# requirements.lock.txt, not requirements.txt: verified directly during this
# deployment-readiness pass (docs/evaluation.md, 2026-08-08) that installing
# from requirements.txt's floating >= ranges resolves starlette==1.4.1 /
# fastapi==0.141.1 today -- versions this repo's test suite has never once
# run against (tested all session against 0.50.0 / 0.128.2). The lock file
# is the actually-tested closure; see its own header for how it's generated
# and regenerated. requirements.txt stays as the abstract source of intent
# pyproject.toml mirrors -- this is the only place that installs from it.
COPY requirements.lock.txt .

# torch first, from PyTorch's own CPU-only wheel index, before the rest of
# requirements.lock.txt: caught directly during this deployment-readiness
# pass (docs/evaluation.md, 2026-08-08) -- a plain `pip install torch` on
# Linux pulls the default CUDA build, which drags in several GB of unused
# nvidia-cu12 packages (cublas alone is ~600MB) neither needed nor usable
# on fly.toml's `shared-cpu-1x` VM (no GPU there at all). Installing the
# exact pinned version from the CPU index first means the later
# requirements.lock.txt install finds it already satisfied and never
# reaches for the GPU build. sentence-transformers (src/embedding/, entity
# resolution's default local embedder) is what actually pulls torch in.
RUN pip install --no-cache-dir torch==2.10.0 \
      --extra-index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY . .
ENV PYTHONPATH=/app

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
