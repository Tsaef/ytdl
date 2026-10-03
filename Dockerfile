# ytdl : interface web pour yt-dlp (spec §14).
# ffmpeg : fusions, MP3, assemblages. deno : exigé par yt-dlp pour YouTube (spec §3).
FROM python:3.14-alpine

RUN apk add --no-cache ffmpeg deno \
 && adduser -D -u 1000 ytdl

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    YTDL_DATA=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY ytdl/ ytdl/
COPY web/ web/

# uid 1000 = ubuntu sur l'hôte : peut écrire dans ~/stack/apps/ytdl-data monté sur /data.
USER ytdl
EXPOSE 8000
CMD ["python", "-m", "ytdl.server"]
