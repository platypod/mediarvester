from datetime import datetime
from googleapiclient.discovery import build
from logging import basicConfig, getLogger
from os import environ
from re import pattern


logger = getLogger(__name__)


class YoutubeService:
    def __init__(
        self,
        api_key: str = None,
        followed_channels_file: str = "followed_channels.csv"
    ):
        api_key: str = api_key \
            if api_key \
            else environ['YOUTUBE__API_KEY']
        logger.debug(f"init client with API_KEY {api_key[0:3]}...{api_key[-3]}")
        self.youtube = build('youtube', 'v3', developerKey=api_key)

        followed_channels_file: str = followed_channels_file \
            if followed_channels_file \
            else environ['YOUTUBE__FOLLOWED_CHANNELS_FILE']
        logger.debug(f"load followed channels list from file {followed_channels_file}")

    def is_youtube_channel_id(channel: str):
        """
        Validates if a string is a YouTube channel ID.
        Format: Exactly 24 chars, starts with 'UC', followed by 22 chars from [A-Za-z0-9_-],
        ends with one of [AQgw] due to Base64 encoding constraints.
        """
        if not channel or len(channel) != 24:
            return False

        pattern = r'^UC[A-Za-z0-9_-]{21}[AQgw]$'
        return bool(re.match(pattern, channel))

    def get_channel_name_from_id(self, channel_id: str) -> str:
        if not self.is_youtube_channel_id(channel_id):
            error_msg: str = f"'{channel_id}' does not match youtube's channel ID standard"
            logger.error(error_msg)
            raise KeyError(error_msg)

        response = youtube.channels().list(part="snippet", id=channel_id).execute()
        if response['items']:
            channel: str = response['items'][0]['snippet']['title']
            logger.info(f"channel found: '{channel}'")
            return channel

        error_msg: str = f"channel does not exist: '{channel_id}"
        logger.error(error_msg)
        raise KeyError(error_msg)

    def get_channel_id_from_name(self, channel: str) -> str:
        # lookup exact name
        response = youtube.channels().list(part="id,snippet", forUsername=name).execute()
        if response['items']:
            channel_id: str = response['items'][0]['id']
            logger.info(f"channel found (exact match): '{channel_id}'")
            return channel_id

        # lookup in search
        search_response = youtube.search().list(part="id,snippet", q=name, type="channel", maxResults=1).execute()
        if search_response['items']:
            channel_id: str = search_response['items'][0]['id']['channelId']
            logger.info(f"channel found (from search): '{channel_id}'")
            return channel_id

        logger.error(f"no such channel found: '{channel}'")
        raise KeyError(f"no such channel found: '{channel}'")

    def follow_channel(self, channel: str) -> bool:
        value: str = channel.strip()
        logger.info(f"follow channel: '{value}'")

        if not self.is_youtube_channel_id(channel):
            logger.warning(f"channel id '{value}' not found, searching based on channel name")
            value = self.get_channel_id_from_name(channel)
            logger.info(f"follow channel: '{value}' instead")

        if os.path.exists(self.followed_channels_file):
            with open(self.followed_channels_file, 'r') as followed_channels:
                if value in followed_channels.read().splitlines():
                    logger.warning(f"tried to follow already followed channel: '{value}'")
                    return False

        with open(self.followed_channels_file, 'a') as f:
            f.write(value + '\n')
        return True

    def unfollow_channel(self, channel: str) -> bool:
        value = channel.strip()
        logger.info(f"unfollow channel: '{value}'")

        if not self.is_youtube_channel_id(channel):
            logger.warning(f"channel id '{value}' not found, searching based on channel name")
            value = self.get_channel_id_from_name(channel)
            logger.info(f"unfollow channel: '{value}' instead")

        if not os.path.exists(self.followed_channels_file):
            logger.warning(f"tried to unfollow non-followed channel (no followed channels file exists): '{value}'")
            return False

        with open(self.followed_channels_file, 'r') as f:
            lines = f.read().splitlines()

        if value not in lines:
            logger.warning(f"tried to unfollow non-followed channel: '{value}'")
            return False

        lines = [line for line in lines if line.strip() != value]
        with open(self.followed_channels_file, 'w') as f:
            f.write('\n'.join(lines) + '\n')

        logger.info(f"unfollowed channel: '{value}'")
        return True

    def list_videos_in_channel(
            self,
            channel: str,
            start_date: datetime = None,
            end_date: datetime = None
    ):
        videos = []
        next_page_token = None

        while True:
            params = {
                'part': 'id,snippet',
                'channelId': channel,
                'order': 'date',
                'maxResults': 50
            }

            if start_date:
                params['publishedAfter'] = start_date.isoformat() + 'Z'
            if end_date:
                params['publishedBefore'] = end_date.isoformat() + 'Z'

            if next_page_token:
                params['pageToken'] = next_page_token

            response = self.youtube.search().list(**params).execute()

            new_videos = [
                {
                    'id': item['id']['videoId'],
                    'title': item['snippet']['title'],
                    'url': f"https://www.youtube.com/watch?v={item['id']['videoId']}"
                }
                for item in response['items']
            ]

            videos.extend(new_videos)
            next_page_token = response.get('nextPageToken')

            if not next_page_token:
                logger.debug("no next page of results, list is complete")
                break
            logger.debug("moving on to the next page of results")

        logger.debug(f"found {len(videos)} results: {','.join([video['title'] for video in videos])}")
        return videos

    def get_playlist_name_from_id(self, playlist_id: str) -> str:
        return self.youtube.playlists().list(
            part='snippet',
            id=playlist_id
        ).execute()['items'][0]['snippet']['title']

    def list_videos_in_playlist(
            self,
            playlist_id: str,
            start_date: datetime = None,
            end_date: datetime = None
    ):
        videos = []
        next_page_token = None

        while True:
            params = {
                'part': 'id,snippet',
                'playlistId': playlist_id,
                'maxResults': 50
            }

            if start_date:
                params['publishedAfter'] = start_date.isoformat() + 'Z'
            if end_date:
                params['publishedBefore'] = end_date.isoformat() + 'Z'

            if next_page_token:
                params['pageToken'] = next_page_token

            response = self.youtube.playlistItems().list(**params).execute()

            new_videos = [
                {
                    'id': item['snippet']['resourceId']['videoId'],
                    'title': item['snippet']['title'],
                    'url': f"https://www.youtube.com/watch?v={item['snippet']['resourceId']['videoId']}"
                }
                for item in response['items']
            ]

            videos.extend(new_videos)
            next_page_token = response.get('nextPageToken')

            if not next_page_token:
                logger.debug("no next page of results, list is complete")
                break
            logger.debug("moving on to the next page of results")

        logger.debug(f"found {len(videos)} results: {','.join([v['title'] for v in videos])}")
        return videos

    def download_video(self, url: str):
        pass

    def get_download_progress(self, download_id: str):
        pass
