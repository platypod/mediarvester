from logging import basicConfig, getLogger
from os import environ


logger = getLogger(__name__)


class Follower:
    def __init__(self):
        pass

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
