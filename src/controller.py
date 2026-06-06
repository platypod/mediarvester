from logging import getLogger, Logger


logger: Logger = getLogger(__name__)


class Controller:
    def __init__(self):
        self.router = APIRouter(prefix="/users", tags=["users"])
        self._setup_routes()

    def _setup_routes(self):
        @self.router.get("")
        async def get_users():
            return [{"id": 1, "name": "John", "email": "john@example.com"}]