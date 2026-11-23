import hashlib
from datetime import date

from fastapi.templating import Jinja2Templates

import books_db
import hardcover

templates = Jinja2Templates(directory="templates")

# Cache-busts the favicon/stylesheet links so a redeploy doesn't stay stuck behind browser caching -
# same approach as devops-feed-curator.
templates.env.globals.update(
    favicon_version=hashlib.sha256(open("static/favicon.png", "rb").read()).hexdigest()[:10],
    css_version=hashlib.sha256(open("static/style.css", "rb").read()).hexdigest()[:10],
    js_version=hashlib.sha256(open("static/reading-map.js", "rb").read()).hexdigest()[:10],
    hardcover_enabled=hardcover.enabled(),
    STATUSES=books_db.STATUSES,
    # Called from templates: the notification panel and the Follow buttons.
    new_releases=lambda: books_db.active_releases(date.today().isoformat()),
    followed_names=books_db.followed_name_map,
    norm_name=books_db.norm_name,
)
