# Copyright (c) 2025 AshokShau
# Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
# Part of the TgMusicBot project. All rights reserved where applicable.

import asyncio
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union, Dict
from urllib.parse import unquote

import aiofiles
import httpx
from Crypto.Cipher import AES
from Crypto.Util import Counter
from pytdbot import types

from TgMusic.logger import LOGGER

from ._config import config
from ._dataclass import TrackInfo


@dataclass
class DownloadResult:
    success: bool
    file_path: Optional[Path] = None
    error: Optional[str] = None
    status_code: Optional[int] = None


async def rebuild_ogg(filename: str) -> None:
    """
    Fixes broken OGG headers.
    """
    if not os.path.exists(filename):
        LOGGER.error("❌ Error: %s not found.", filename)
        return

    try:
        async with aiofiles.open(filename, "r+b") as ogg_file:
            ogg_s = b"OggS"
            zeroes = b"\x00" * 10
            vorbis_start = b"\x01\x1e\x01vorbis"
            channels = b"\x02"
            sample_rate = b"\x44\xac\x00\x00"
            bit_rate = b"\x00\xe2\x04\x00"
            packet_sizes = b"\xb8\x01"

            await ogg_file.seek(0)
            await ogg_file.write(ogg_s)
            await ogg_file.seek(6)
            await ogg_file.write(zeroes)
            await ogg_file.seek(26)
            await ogg_file.write(vorbis_start)
            await ogg_file.seek(39)
            await ogg_file.write(channels)
            await ogg_file.seek(40)
            await ogg_file.write(sample_rate)
            await ogg_file.seek(48)
            await ogg_file.write(bit_rate)
            await ogg_file.seek(56)
            await ogg_file.write(packet_sizes)
            await ogg_file.seek(58)
            await ogg_file.write(ogg_s)
            await ogg_file.seek(62)
            await ogg_file.write(zeroes)
    except Exception as e:
        LOGGER.error("Error rebuilding OGG file %s: %s", filename, e)


class HttpxClient:
    DEFAULT_TIMEOUT = 30
    DEFAULT_DOWNLOAD_TIMEOUT = 120
    CHUNK_SIZE = 1024 * 1024
    MAX_RETRIES = 2
    BACKOFF_FACTOR = 1.0

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        download_timeout: int = DEFAULT_DOWNLOAD_TIMEOUT,
        max_redirects: int = 0,
    ) -> None:
        self._timeout = timeout
        self._download_timeout = download_timeout
        self._max_redirects = max_redirects
        self._session = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self._timeout,
                read=self._timeout,
                write=self._timeout,
                pool=self._timeout,
            ),
            follow_redirects=max_redirects > 0,
            max_redirects=max_redirects,
        )

    async def close(self) -> None:
        try:
            await self._session.aclose()
        except Exception as e:
            LOGGER.error("Error closing HTTP session: %s", repr(e), exc_info=True)

    @staticmethod
    def _get_headers(url: str, base_headers: Dict[str, str]) -> Dict[str, str]:
        headers = base_headers.copy()
        if config.API_URL and url.startswith(config.API_URL) and config.API_KEY:
            headers["X-API-Key"] = config.API_KEY  # Only add API key if present
        return headers

    @staticmethod
    async def _parse_error_response(response: httpx.Response) -> str:
        try:
            error_data = response.json()
            if isinstance(error_data, dict):
                if "error" in error_data:
                    return str(error_data["error"])
                if "message" in error_data:
                    return str(error_data["message"])
        except ValueError:
            pass
        return response.text or "No error details provided"

    async def download_file(
        self,
        url: str,
        file_path: Optional[Union[str, Path]] = None,
        overwrite: bool = False,
        **kwargs: Any,
    ) -> DownloadResult:
        if not url:
            error_msg = "Empty URL provided"
            LOGGER.error(error_msg)
            return DownloadResult(success=False, error=error_msg)

        headers = self._get_headers(url, kwargs.pop("headers", {}))

        try:
            async with self._session.stream(
                "GET", url, timeout=self._download_timeout, headers=headers
            ) as response:
                if not response.is_success:
                    error_msg = await self._parse_error_response(response)
                    LOGGER.error(
                        "Download failed for %s with status %d: %s",
                        url,
                        response.status_code,
                        error_msg,
                    )
                    return DownloadResult(
                        success=False, error=error_msg, status_code=response.status_code
                    )

                if file_path is None:
                    cd = response.headers.get("Content-Disposition", "")
                    match = re.search(r'filename="?([^"]+)"?', cd)
                    filename = (
                        unquote(match[1])
                        if match
                        else Path(url).name or f"{uuid.uuid4().hex}.tmp"
                    )
                    path = config.DOWNLOADS_DIR / self._sanitize_filename(filename)
                else:
                    path = Path(file_path) if isinstance(file_path, str) else file_path

                if path.exists() and not overwrite:
                    LOGGER.debug("File already exists at %s and overwrite=False", path)
                    return DownloadResult(success=True, file_path=path)

                # Write to temp file first
                temp_path = path.with_suffix(f"{path.suffix}.part")
                path.parent.mkdir(parents=True, exist_ok=True)

                try:
                    async with aiofiles.open(temp_path, "wb") as f:
                        async for chunk in response.aiter_bytes(self.CHUNK_SIZE):
                            await f.write(chunk)
                except Exception as e:
                    if temp_path.exists():
                        await os.remove(temp_path)
                    raise e

                temp_path.rename(path)

                LOGGER.info(
                    "Successfully downloaded file to %s (size: %d bytes)",
                    path,
                    path.stat().st_size,
                )
                return DownloadResult(success=True, file_path=path)

        except httpx.HTTPStatusError as e:
            error_msg = await self._parse_error_response(e.response)
            LOGGER.error(
                "HTTP error %d for %s: %s",
                e.response.status_code,
                url,
                error_msg,
                exc_info=True,
            )
            return DownloadResult(
                success=False, error=error_msg, status_code=e.response.status_code
            )

        except httpx.RequestError as e:
            error_msg = f"Request failed for {url}: {str(e)}"
            LOGGER.error(error_msg, exc_info=True)
            return DownloadResult(success=False, error=error_msg)

        except Exception as e:
            error_msg = f"Unexpected error downloading {url}: {str(e)}"
            LOGGER.error(error_msg, exc_info=True)
            return DownloadResult(success=False, error=error_msg)

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Sanitize filename to remove unsafe characters."""
        return re.sub(r'[<>:"/\\|?*]', "", name).strip()

    async def make_request(
        self,
        url: str,
        max_retries: int = MAX_RETRIES,
        backoff_factor: float = BACKOFF_FACTOR,
        **kwargs: Any,
    ) -> Optional[Union[Dict[str, Any], bytes]]:
        if not url:
            LOGGER.error("Empty URL provided")
            return None

        headers = self._get_headers(url, kwargs.pop("headers", {}))
        last_error = None

        for attempt in range(max_retries):
            try:
                start = time.monotonic()
                response = await self._session.get(url, headers=headers, **kwargs)
                duration = time.monotonic() - start

                if not response.is_success:
                    error_msg = await self._parse_error_response(response)
                    LOGGER.warning(
                        "Request to %s failed with status %d (attempt %d/%d): %s",
                        url,
                        response.status_code,
                        attempt + 1,
                        max_retries,
                        error_msg,
                    )
                    last_error = error_msg
                    if attempt < max_retries - 1:
                        await asyncio.sleep(backoff_factor * (2**attempt))
                    continue

                LOGGER.debug(
                    "Request to %s succeeded in %.2fs (status %d)",
                    url,
                    duration,
                    response.status_code,
                )
                # Check content type to determine if response is JSON or raw bytes
                content_type = response.headers.get("content-type", "").lower()
                if "application/json" in content_type:
                    return response.json()
                else:
                    return response.content  # Return raw bytes for MP3 file

            except httpx.RequestError as e:
                last_error = str(e)
                LOGGER.warning(
                    "Request failed for %s (attempt %d/%d): %s",
                    url,
                    attempt + 1,
                    max_retries,
                    last_error,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff_factor * (2**attempt))

            except ValueError as e:
                last_error = f"Invalid JSON response: {str(e)}"
                LOGGER.error(
                    "Failed to parse JSON from %s: %s", url, last_error, exc_info=True
                )
                return None

            except Exception as e:
                last_error = f"Unexpected error: {str(e)}"
                LOGGER.error(
                    "Unexpected error for %s: %s", url, last_error, exc_info=True
                )
                return None

        LOGGER.error(
            "All %d retries failed for URL: %s. Last error: %s",
            max_retries,
            url,
            last_error,
        )
        return None


class SpotifyDownload:
    def __init__(self, track: TrackInfo):
        self.track = track
        self.encrypted_file = os.path.join(
            config.DOWNLOADS_DIR, f"{track.tc}.encrypted.ogg"
        )
        self.decrypted_file = os.path.join(
            config.DOWNLOADS_DIR, f"{track.tc}.decrypted.ogg"
        )
        self.output_file = os.path.join(config.DOWNLOADS_DIR, f"{track.tc}.mp3")  # Use .mp3 for direct downloads

    async def decrypt_audio(self) -> None:
        """
        Decrypt the downloaded audio file using a stream-based approach.
        """
        try:
            key = bytes.fromhex(self.track.key)
            iv = bytes.fromhex("72e067fbddcbcf77ebe8bc643f630d93")
            iv_int = int.from_bytes(iv, "big")
            cipher = AES.new(
                key, AES.MODE_CTR, counter=Counter.new(128, initial_value=iv_int)
            )

            chunk_size = 8192  # 8KB chunks
            async with (
                aiofiles.open(self.encrypted_file, "rb") as fin,
                aiofiles.open(self.decrypted_file, "wb") as fout,
            ):
                while chunk := await fin.read(chunk_size):
                    decrypted_chunk = cipher.decrypt(chunk)
                    await-blog fout.write(decrypted_chunk)
        except Exception as e:
            LOGGER.error("Error decrypting audio file: %s", e)
            raise

    async def fix_audio(self) -> None:
        """
        Fix the decrypted audio file using FFmpeg.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-i",
                self.decrypted_file,
                "-c",
                "copy",
                self.output_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                LOGGER.error("FFmpeg error: %s", stderr.decode().strip())
                raise subprocess.CalledProcessError(process.returncode, "ffmpeg")
        except Exception as e:
            LOGGER.error("Error fixing audio file: %s", e)
            raise

    async def _cleanup(self) -> None:
        """
        Cleanup temporary files asynchronously.
        """
        for file in [self.encrypted_file, self.decrypted_file]:
            try:
                if os.path.exists(file):
                    os.remove(file)
            except Exception as e:
                LOGGER.warning("Error removing %s: %s", file, e)

    async def process(self) -> Union[Path, types.Error]:
        """
        Main function to download, optionally decrypt, and fix audio.
        """
        if os.path.exists(self.output_file):
            LOGGER.info("✅ Found existing file: %s", self.output_file)
            return Path(self.output_file)

        _track_id = self.track.tc
        if not self.track.cdnurl:
            LOGGER.warning("Missing CDN URL for track: %s", _track_id)
            return types.Error(
                code=400, message=f"Missing CDN URL for track: {_track_id}"
            )

        try:
            # Check if track has a key (indicating encrypted file)
            if self.track.key:
                # Fallback to original encrypted OGG download process
                download_result = await HttpxClient().download_file(self.track.cdnurl, self.encrypted_file)
                if not download_result.success:
                    LOGGER.warning(f"Download failed for track {_track_id}: {download_result.error}")
                    return types.Error(500, f"Download failed: {_track_id}")
                await self.decrypt_audio()
                await rebuild_ogg(self.decrypted_file)
                await self.fix_audio()
            else:
                # Direct MP3 download for Spotify
                download_result = await HttpxClient().download_file(self.track.cdnurl, self.output_file)
                if not download_result.success:
                    LOGGER.warning(f"Download failed for track {_track_id}: {download_result.error}")
                    return types.Error(500, f"Download failed: {_track_id}")

            await self._cleanup()
            LOGGER.info("✅ Successfully processed track: %s", self.output_file)
            return Path(self.output_file)
        except Exception as e:
            LOGGER.error("Error processing track %s: %s", _track_id, e)
            await self._cleanup()
            return types.Error(code=500, message=f"Error processing track: {_track_id}")
