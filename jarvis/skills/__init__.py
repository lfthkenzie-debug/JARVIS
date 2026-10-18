# JARVIS skills — each module self-registers commands on import.
# Import order = regex routing precedence (earlier modules win ambiguous phrases).
from jarvis.skills import (  # noqa: F401
    system,
    timer,
    files,
    data,
    image,
    ml,
    timeseries,
    scraping,
    nlp,
    screen,
    web,
    browser,
    youtube,
    chat,
)
