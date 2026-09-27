# Wassal sur Hugging Face Spaces (SDK Docker), transcription vocale MoulSot incluse.
#
# Ce Dockerfile est le seul fichier de code du Space : il récupère Wassal depuis
# GitHub au moment du build. GitHub reste l'unique source du code ; pour mettre
# le Space à jour, il suffit de relancer un build ("Factory rebuild").
#
# Test en local :
#   docker build -t wassal-space deploy/huggingface
#   docker run -p 7860:7860 wassal-space        -> http://localhost:7860

FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Les Spaces exécutent le conteneur avec l'utilisateur 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/home/user/.cache/huggingface \
    FLASK_ENV=production \
    PRELOAD_MODEL=true \
    DEVICE=auto
WORKDIR /home/user/app

ARG WASSAL_REPO=https://github.com/oussamabartil/wassal.git
ARG WASSAL_REF=main

# 1) Dépendances d'abord : cette couche n'est reconstruite que si
#    requirements.txt change, pas à chaque commit.
#    torch CPU explicite : sinon pip installe la version CUDA (~3 Go de plus),
#    inutile sur le matériel CPU gratuit des Spaces.
ADD --chown=user https://raw.githubusercontent.com/oussamabartil/wassal/${WASSAL_REF}/requirements.txt /tmp/requirements.txt
RUN pip install --user torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --user -r /tmp/requirements.txt gunicorn==26.2.0

# 2) Le code. Le fichier ADD change dès qu'un commit arrive sur la branche,
#    ce qui force un nouveau clone (sinon Docker réutiliserait l'ancien).
ADD https://api.github.com/repos/oussamabartil/wassal/commits/${WASSAL_REF} /tmp/wassal-commit.json
RUN git clone --depth 1 --branch "${WASSAL_REF}" "${WASSAL_REPO}" . \
    && git log -1 --format="Wassal %h (%cd) %s"

EXPOSE 7860

# 1 worker : chaque worker chargerait sa propre copie du modèle (~7 Go en RAM).
# Des threads pour que l'API texte réponde pendant une transcription.
# Timeout long : une transcription sur CPU peut prendre plusieurs dizaines de secondes.
CMD ["gunicorn", "--bind", "0.0.0.0:7860", "--workers", "1", "--threads", "4", \
     "--timeout", "300", "--access-logfile", "-", "src.api:create_app()"]
