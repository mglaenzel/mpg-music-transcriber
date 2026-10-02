FROM python:3.11-slim

# ffmpeg: audio decoding for librosa/demucs. musescore3: PDF rendering via
# music21's MuseScore backend (registered as `mscore` on Debian).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    musescore3 \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN ln -s /usr/bin/musescore3 /usr/bin/mscore || true

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DEVICE=cpu
ENV MUSESCORE_BINARY=mscore
# MuseScore's Qt UI needs a display even for headless CLI rendering; there is
# none in this container, so force the offscreen Qt platform plugin.
ENV QT_QPA_PLATFORM=offscreen

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
