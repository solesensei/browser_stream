# Browser Media Streamer

![version](https://img.shields.io/badge/version-v0.1.0-blue.svg)

This tool assists in preparing local media and configuring Nginx/Plex for HTTP streaming.
It generates direct URLs for media files that can be used to watch directly in web browsers.
You can use these URLs in https://app.getmetastream.com which is a great project for watching media together.

## Why not using Plex directly?

[Plex](https://plex.tv) has a "watch together" feature (give it a try!), but it works very unstable for Raspberry Pi 4 setup. The Raspberry Pi 4 is not powerful enough to transcode media files on the fly. As a result, everyone in the party must set up their own players each time to choose original quality, which is not always available. Additionally, it is not scalable for many people, as you must invite each person to the party.

This tool is designed to work with original quality (without server transcoding) and provides direct URLs secured with a token, allowing you to watch media files directly in your browser without any additional setup. You can also use it with Nginx without needing to install and configuring Plex Server. See [Prerequisites](#prerequisites) for more details.

## Features

1. Convert media files to MP4 format.
2. Convert subtitles to VTT format, compatible with most browsers.
3. Generate Nginx configurations for HTTP media streaming.
4. Support Plex media server direct urls.

## Prerequisites

1. Python 3.10 or higher (use [pyenv](https://github.com/pyenv/pyenv) if needed).
2. Nginx (if using Nginx) or configured Plex Media Server (if using Plex)
3. FFmpeg (for media encoding):
    ```bash
    sudo apt update && sudo apt install ffmpeg -y
    ```
4. Python packages:
    ```bash
    # Create a virtual environment and install dependencies
    python -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    ```
5. Static IPv4 or IPv6 address for your server.

## Usage

> [!NOTE]
> **Tool can work with Nginx or Plex. You can setup one of them or both on different ports.**

### Nginx

> [!TIP]
> **HTTPS is highly recommended for security reasons. See [Nginx HTTPS with Router Domain](#nginx-https-with-router-domain) for more details.**

```bash
# Install Nginx
sudo apt update && sudo apt install nginx -y
# Configure Nginx over HTTP (no SSL)
python main.py nginx --media-dir /path/to/media --ipv6 --port 32000 
# Get stream URL
python main.py stream --media-file /path/to/media/file.mp4 --audio-lang jp --subtitle-lang en --with-nginx
```

### Plex

> [!IMPORTANT]
> **Plex direct url would expose plex token. Use it only in a secure environments. To change the token, you need to change the plex account password and with logout from all devices checkmark.**

Setup [Plex Media Server](https://plex.tv)

```bash
# Configure Plex
python main.py plex --media-dir /path/to/media --x-token your-plex-token --server-id your-plex-server-id
# (alternative) Configure Plex with download url (can be gotten from plex web player)
python main.py plex --media-dir /path/to/media --download-url https://ip-address.plex.direct:32400/library/parts/your-part-id/file.mp4?X-Plex-Token=your-plex-token
# Get stream URL
python main.py stream --media-file /path/to/media/file.mp4 --audio-lang jp --subtitle-lang en --with-plex
```

## Nginx HTTPS with Router Domain or Dynamic DNS

If your router provides a domain name for local network devices, you can use it to stream media over HTTPS.
Alternatively, you can use a dynamic DNS (like noip.com) service to get a public domain name for your server and configre port forwarding in your router settings.

1. Set up device sharing in your router settings with a public domain name.
2. Install [Certbot](https://certbot.eff.org/) and obtain a certificate for your domain:
    ```bash
    sudo apt update && sudo apt install certbot python3-certbot-nginx -y
    sudo systemctl stop nginx
    sudo certbot certonly --standalone -d your-domain.com
    ```
3. Configure Nginx to use the certificate:
    ```bash
    python main.py nginx --media-dir /path/to/media --ipv6 --port 32000 --domain your-domain.com --ssl
    ```
4. Test the configuration and start Nginx:
    ```bash
    sudo nginx -t
    sudo systemctl start nginx
    ```
5. Open your browser and navigate to `https://your-domain.com`.
6. Renew the certificate using:
    ```bash
    sudo certbot renew
    ```
