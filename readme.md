# Browser Media Streamer

![version](https://img.shields.io/badge/version-v0.2.0-blue.svg)

A command-line tool for streaming local media files directly in web browsers. It prepares media files and configures Nginx or Plex for HTTP streaming, generating secure direct URLs that work seamlessly with online watch party platforms like [Metastream](https://app.getmetastream.com).

## Why Not Use Plex Directly?

[Plex](https://plex.tv) has a "watch together" feature (give it a try!), but it works very unstably on Raspberry Pi 4 setups. The Raspberry Pi 4 is not powerful enough to transcode media files on the fly. As a result, everyone in the party must set up their own players each time to choose original quality, which is not always available. Additionally, it is not scalable for many people, as you must invite each person to the party.

This tool is designed to work **with original quality** (without server transcoding) and provides direct URLs secured with a token, allowing you to watch media files directly in your browser without any additional setup. You can also use it with Nginx without needing to install and configure Plex Server. See [Prerequisites](#prerequisites) for more details.

## Features

- **Simple media streaming**: Stream any video file with `browser-streamer stream /path/to/file.mp4`
- **Multiple server backends**: Choose between Nginx or Plex streaming servers with `--server=nginx|plex`
- **Media conversion**: Convert media files to MP4 format and subtitles to VTT format (HTML5 compatible)
- **Instant streaming**: Use `--raw` to stream files immediately without format conversion
- **Media preparation**: Convert and optimize media files with `--prepare-only` without starting streams
- **Batch TV show processing**: Automatically detect and process entire TV show directories from any starting episode
- **Subtitle embedding**: Embed subtitles directly into video streams with `--embed-subs`
- **Intelligent file scanning**: Automatically scans directories or use `--scan-external` for individual files
- **Nginx configuration**: Generate Nginx configurations for HTTP media streaming
- **Plex integration**: Support Plex Media Server direct URLs
- **Input validation**: Comprehensive error checking with clear, actionable error messages

## Prerequisites

1. **Python 3.10 or higher** (use [pyenv](https://github.com/pyenv/pyenv) if needed)
2. **Install the tool**

   <details><summary>Install using uvx</summary>

   ```bash
   uvx --from git+ssh://git@github.com/solesensei/browser_stream.git@v0.2.0 browser-streamer --help
   # or install persistently
   uv tool install git+ssh://git@github.com/solesensei/browser_stream.git@v0.2.0
   ```

   </details>

   <details><summary>Install using venv+pip</summary>

   ```bash
   # Create a virtual environment and install dependencies
   python -m venv venv && source venv/bin/activate
   pip install -I git+ssh://git@github.com/solesensei/browser_stream.git@v0.2.0
   # or
   git clone git@github.com:solesensei/browser_stream.git
   pip install browser_stream/
   ```

   </details>

3. **Nginx** (if using Nginx) or configured **Plex Media Server** (if using Plex)
4. **FFmpeg** (for media encoding):
   ```bash
   sudo apt update && sudo apt install ffmpeg -y
   ```
5. **Static IPv4 or IPv6 address** for your server

## Usage

> [!NOTE]
> This tool can work with Nginx or Plex. You can set up one of them or both on different ports.

### Quick Examples

```bash
# Basic streaming (uses Nginx by default)
browser-streamer stream /path/to/movie.mp4

# Stream with Plex server
browser-streamer stream /path/to/movie.mp4 --server=plex

# Quick streaming without conversion
browser-streamer stream /path/to/movie.mp4 --raw

# Stream with specific audio and subtitle files
browser-streamer stream /path/to/movie.mp4 --audio-file audio.aac --subtitle-file subs.srt

# Stream directory (scans for video files)
browser-streamer stream /path/to/media/directory/

# Scan for external audio/subtitle files for single movie
browser-streamer stream movie.mkv --scan-external

# Stream with embedded subtitles
browser-streamer stream /path/to/movie.mp4 --embed-subs --subtitle-file subs.srt

# Raw streaming (no conversion, for supported formats)
browser-streamer stream /path/to/movie.mp4 --raw

# Prepare media for streaming without generating URLs (useful for batch processing)
browser-streamer stream movie.mkv --prepare-only --audio-lang en --subtitle-lang en

# Batch process TV show episodes (auto-detected)
browser-streamer stream /path/to/tv-show-directory/ --prepare-only
```

### Nginx

> [!TIP]
> HTTPS is highly recommended for security reasons. See [Nginx HTTPS with Router Domain](#nginx-https-with-router-domain) for more details.

```bash
# Install Nginx
sudo apt update && sudo apt install nginx -y
# Configure Nginx over HTTP (no SSL)
browser-streamer setup nginx --media-dir /path/to/media --ipv6 --port 32000 
# Get stream URL
browser-streamer stream /path/to/media/file.mp4 --audio-lang jp --subtitle-lang en
```

### Plex

> [!IMPORTANT]
> Plex direct URLs expose your Plex token. Use only in secure environments. To change the token, you need to change your Plex account password with the 'logout from all devices' option checked.

Set up [Plex Media Server](https://plex.tv)

```bash
# Configure Plex
browser-streamer setup plex --media-dir /path/to/media --x-token your-plex-token --server-id your-plex-server-id
# (alternative) Configure Plex with download url (can be gotten from plex web player)
browser-streamer setup plex --media-dir /path/to/media --download-url https://ip-address.plex.direct:32400/library/parts/your-part-id/file.mp4?X-Plex-Token=your-plex-token
# Get stream URL
browser-streamer stream /path/to/media/file.mp4 --audio-lang jp --subtitle-lang en --server=plex
```

## Nginx HTTPS with Router Domain or Dynamic DNS

If your router provides a domain name for local network devices, you can use it to stream media over HTTPS.
Alternatively, you can use a dynamic DNS service (like noip.com) to get a public domain name for your server and configure port forwarding in your router settings.

1. Set up device sharing in your router settings with a public domain name.
2. Install [Certbot](https://certbot.eff.org/) and obtain a certificate for your domain:
    ```bash
    sudo apt update && sudo apt install certbot python3-certbot-nginx -y
    sudo systemctl stop nginx
    sudo certbot certonly --standalone -d your-domain.com  # Port 80 should be open by default and port forwarding configured
    ```
3. Configure Nginx to use the certificate:
    ```bash
    browser-streamer setup nginx --media-dir /path/to/media --ipv6 --port 32000 --domain your-domain.com --ssl
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

## Command Reference

```bash
# View all options
browser-streamer stream --help

# Setup commands
browser-streamer setup nginx --help
browser-streamer setup plex --help

# Media analysis
browser-streamer media info /path/to/file.mp4

# Configuration management
browser-streamer config              # View current config
browser-streamer config --reset      # Reset configuration
```

## References

- **[FFmpeg](https://ffmpeg.org/)** - Media processing and conversion
- **[Nginx](https://nginx.org/)** - HTTP server for media streaming
- **[Plex Media Server](https://plex.tv/)** - Media server platform
- **[Metastream](https://getmetastream.com/)** - Synchronized watch parties (highly recommended)
- **[Certbot](https://certbot.eff.org/)** - Let's Encrypt certificate automation
