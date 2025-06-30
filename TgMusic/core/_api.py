# Copyright (c) 2025 AshokShau
# Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
# Part of the TgMusicBot project. All rights reserved where applicable.

import re
from pathlib import Path
from typing import Optional, Union
from urllib.parse import quote

from pytdbot import types
from TgMusic.logger import LOGGER

from ._config import config
from ._downloader import MusicService
from ._httpx import HttpxClient, SpotifyDownload
from ._dataclass import PlatformTracks, MusicTrack, TrackInfo


class ApiData(MusicService):
    """API integration handler for multiple music streaming platforms.

    Provides functionality to:
    - Validate and process music URLs
    - Retrieve track information
    - Search across platforms
    - Download tracks
    """

    # Platform URL validation patterns
    URL_PATTERNS = {
        "apple_music": re.compile(
            r"^(https?://)?(music\.apple\.com/([a-z]{2}/)?(album|playlist|song)/[a-zA-Z0-9\-_]+/[0-9]+)(\?.*)?$",
            re.IGNORECASE,
        ),
        "spotify": re.compile(
            r"^(https?://)?(open\.spotify\.com/(track|playlist|album|artist)/[a-zA-Z0-9]+)(\?.*)?$",
            re.IGNORECASE,
        ),
        "soundcloud": re.compile(
            r"^(https?://)?(www\.)?soundcloud\.com/[a-zA-Z0-9_-]+(/(sets)?/[a-zA-Z0-9_-]+)?(\?.*)?$",
            re.IGNORECASE,
        ),
    }

    def __init__(self, query: Optional[str] = None) -> None:
        """Initialize the API handler with optional query.

        Args:
            query: URL or search term to process
        """
        self.query = self._sanitize_query(query) if query else None
        self.api_url = "https://billa-api.vercel.app"
        self.api_key = config.API_KEY if config.API_KEY else None  # API key is optional
        self.client = HttpxClient()

    @staticmethod
    def _sanitize_query(query: str) -> str:
        """Clean and standardize input queries.

        Removes:
        - URL fragments (#)
        - Query parameters (?)
        - Leading/trailing whitespace
        """
        return query.strip().split("?")[0].split("#")[0]

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Sanitize text to prevent Telegram entity parsing issues.

        Escapes HTML characters and removes invalid characters.
        """
        if not text:
            return text
        # Replace problematic characters
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Remove any remaining control characters
        text = re.sub(r"[\x00-\x1F\x7F]", "", text)
        return text

    def is_valid(self, url: Optional[str]) -> bool:
        """Validate if URL matches supported platform patterns.

        Args:
            url: The URL to validate

        Returns:
            bool: True if URL matches any platform pattern
        """
        if not url or not self.api_url:
            return False
        return any(pattern.match(url) for pattern in self.URL_PATTERNS.values())

    async def _make_api_request(
            self, endpoint: str, params: Optional[dict] = None
    ) -> Optional[Union[dict, bytes]]:
        """Make API requests to the music service, with optional authentication.

        Args:
            endpoint: API endpoint to call
            params: Query parameters for the request

        Returns:
            dict or bytes: JSON response or direct file content from API, or None if failed
        """
        if not self.api_url:
            LOGGER.warning("API URL configuration missing")
            return None

        # Construct endpoint URL with path parameter if needed
        if endpoint in ["search_track", "get_track"] and self.query:
            request_url = f"{self.api_url}/{endpoint.lstrip('/')}/{quote(self.query)}"
        elif endpoint == "get_url" and self.query:
            request_url = f"{self.api_url}/{endpoint.lstrip('/')}/{quote(self.query)}"
        else:
            request_url = f"{self.api_url}/{endpoint.lstrip('/')}"

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        try:
            response = await self.client.make_request(request_url, params=params, headers=headers)
            if response is None:
                LOGGER.warning(f"API request to {request_url} failed: No response received")
                return None
            return response
        except Exception as e:
            LOGGER.error(f"API request to {request_url} failed: {str(e)}")
            return None

    async def get_info(self) -> Union[PlatformTracks, types.Error]:
        """Retrieve track information from a valid URL.

        Returns:
            PlatformTracks: Contains track metadata
            types.Error: If URL is invalid or request fails
        """
        if not self.query or not self.is_valid(self.query):
            return types.Error(400, "Invalid or unsupported URL provided")

        response = await self._make_api_request("get_url")
        return self._parse_tracks_response(response) or types.Error(
            404, f"No track information found for URL: {self.query}"
        )

    async def search(self) -> Union[PlatformTracks, types.Error]:
        """Search for tracks across supported platforms.

        Returns:
            PlatformTracks: Contains search results
            types.Error: If query is invalid or search fails
        """
        if not self.query:
            return types.Error(400, "No search query provided")

        if self.is_valid(self.query):
            return await self.get_info()

        response = await self._make_api_request("search_track")
        return self._parse_tracks_response(response) or types.Error(
            404, f"No results found for search query: {self.query}"
        )

    async def get_track(self) -> Union[TrackInfo, types.Error]:
        """Get detailed track information or direct MP3 file.

        Returns:
            TrackInfo: Detailed track metadata with direct MP3 URL
            types.Error: If track cannot be found or request fails
        """
        if not self.query:
            return types.Error(400, "No track identifier provided")

        response = await self._make_api_request("get_track")
        if response is None:
            return types.Error(404, f"Track not found for ID: {self.query}")

        # Handle JSON response
        if isinstance(response, dict):
            try:
                return TrackInfo(
                    url=self._sanitize_text(response.get("spotify_url", f"https://open.spotify.com/track/{self.query}")),
                    cdnurl=self._sanitize_text(response.get("cdnurl", "")),
                    key=self._sanitize_text(response.get("key", "")),
                    name=self._sanitize_text(response.get("name", "Unknown Track")),
                    artist=self._sanitize_text(", ".join(response.get("artists", ["Unknown Artist"]))),
                    album=self._sanitize_text(response.get("album", "Unknown Album")),
                    tc=self._sanitize_text(response.get("tc", self.query)),
                    cover=self._sanitize_text(response.get("cover", "")),
                    lyrics=self._sanitize_text(response.get("lyrics", "")),
                    duration=response.get("duration", 0),
                    year=response.get("year", 0),
                    platform="spotify"
                )
            except Exception as e:
                LOGGER.error(f"Error parsing JSON track response for {self.query}: {str(e)}")
                return types.Error(500, "Failed to process track data")

        # Handle direct MP3 file response
        if isinstance(response, bytes):
            temp_file = config.DOWNLOADS_DIR / f"{self.query}.mp3"
            try:
                async with aiofiles.open(temp_file, "wb") as f:
                    await f.write(response)
                return TrackInfo(
                    url=f"https://open.spotify.com/track/{self.query}",
                    cdnurl=str(temp_file),
                    key="",  # No encryption key for direct MP3
                    name="Unknown Track",
                    artist="Unknown Artist",
                    album="Unknown Album",
                    tc=self.query,
                    cover="",
                    lyrics="",
                    duration=0,
                    year=0,
                    platform="spotify"
                )
            except Exception as e:
                LOGGER.error(f"Error saving MP3 file for track {self.query}: {str(e)}")
                return types.Error(500, "Failed to process MP3 file")

        LOGGER.warning(f"Unexpected response format for get_track: {type(response)}")
        return types.Error(500, "Unexpected response format from API")

    async def download_track(
            self, track: TrackInfo, video: bool = False
    ) -> Union[Path, types.Error]:
        """Download a track to local storage.

        Args:
            track: TrackInfo object containing download details
            video: Whether to download video (default: False)

        Returns:
            Path: Location of downloaded file
            types.Error: If download fails
        """
        if not track:
            return types.Error(400, "Invalid track information provided")

        if track.platform.lower() == "spotify":
            spotify_result = await SpotifyDownload(track).process()
            if isinstance(spotify_result, types.Error):
                LOGGER.error(f"Spotify download failed: {spotify_result.message}")
            return spotify_result

        if not track.cdnurl:
            error_msg = f"No download URL available for track: {track.tc}"
            LOGGER.error(error_msg)
            return types.Error(400, error_msg)

        download_path = config.DOWNLOADS_DIR / f"{track.tc}.mp3"
        download_result = await self.client.download_file(track.cdnurl, download_path)

        if not download_result.success:
            LOGGER.warning(f"Download failed for track {track.tc}: {download_result.error}")
            return types.Error(500, f"Download failed: {download_result.error or track.tc}")

        return download_result.file_path

    @staticmethod
    def _parse_tracks_response(
            response_data: Optional[dict],
    ) -> Union[PlatformTracks, types.Error]:
        """Parse and validate API response data.

        Args:
            response_data: Raw API response

        Returns:
            PlatformTracks: Validated track data
            types.Error: If response is invalid
        """
        if not response_data:
            return types.Error(404, "Invalid API response format")

        try:
            # Handle search_track single-object response
            if "id" in response_data:
                track_data = response_data
                tracks = [
                    MusicTrack(
                        url=ApiData._sanitize_text(track_data.get("spotify_url", "")),
                        name=ApiData._sanitize_text(track_data.get("name", "Unknown Track")),
                        artist=ApiData._sanitize_text(", ".join(track_data.get("artists", ["Unknown Artist"]))),
                        id=track_data.get("id", ""),
                        year=track_data.get("year", 0),
                        cover=ApiData._sanitize_text(track_data.get("album_art", "")),
                        duration=ApiData._parse_duration(track_data.get("duration", 0)),
                        platform="spotify"
                    )
                ]
            # Handle get_url results list response
            elif "results" in response_data:
                tracks = [
                    MusicTrack(
                        url=ApiData._sanitize_text(track_data.get("spotify_url", "")),
                        name=ApiData._sanitize_text(track_data.get("name", "Unknown Track")),
                        artist=ApiData._sanitize_text(track_data.get("artist", "Unknown Artist")),
                        id=track_data.get("id", ""),
                        year=track_data.get("year", 0),
                        cover=ApiData._sanitize_text(track_data.get("cover", "")),
                        duration=track_data.get("duration", 0),
                        platform="spotify"
                    )
                    for track_data in response_data["results"]
                    if isinstance(track_data, dict)
                ]
            else:
                return types.Error(404, "No valid tracks found in response")

            return PlatformTracks(tracks=tracks) if tracks else types.Error(404, "No valid tracks found")
        except Exception as parse_error:
            LOGGER.error(f"Failed to parse tracks: {parse_error}")
            return types.Error(500, "Failed to process track data")

    @staticmethod
    def _parse_duration(duration: Union[str, int]) -> int:
        """Convert duration from string (MM:SS) or integer to seconds."""
        if isinstance(duration, int):
            return duration
        if isinstance(duration, str):
            try:
                minutes, seconds = map(int, duration.split(":"))
                return minutes * 60 + seconds
            except ValueError:
                LOGGER.warning(f"Invalid duration format: {duration}")
                return 0
        return 0
